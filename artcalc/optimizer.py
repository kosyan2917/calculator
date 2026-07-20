from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import time
from typing import Any

from ortools.sat.python import cp_model

from .solver_catalog import ArtifactGroup, SolverCatalog
from .stat_model import (
    INFECTION_STATS,
    PRIMARY_STATS,
    SECONDARY_STATS,
    MechanicsConfig,
    add_stats,
    derived_stats,
    infection_report,
    split_infections,
)


METRIC_ALIASES = {
    "speed": "movement_speed",
    "run_speed": "total_sprint_speed",
    "durability": "effective_durability",
    "regen": "hp_regen_score",
    "weight": "carry_weight",
}

METRIC_SCALES = {
    "effective_durability": 100.0,
    "bullet_resistance": 100.0,
    "vitality": 10.0,
    "movement_speed": 5.0,
    "sprint_speed": 5.0,
    "total_sprint_speed": 5.0,
    "stamina": 25.0,
    "stamina_regeneration": 5.0,
    "hp_regen_score": 5.0,
    "health_regeneration": 5.0,
    "periodic_healing": 5.0,
    "healing_effectiveness": 10.0,
    "carry_weight": 10.0,
    "bleeding_output": 1.0,
    "bleeding_resistance": 10.0,
    "burn_reaction": 10.0,
    "tear_reaction": 10.0,
}


@dataclass(frozen=True)
class OptimizationRequest:
    budget: int
    preferences: dict[str, float]
    targets: dict[str, float] = field(default_factory=dict)
    armor_ids: tuple[str, ...] = ()
    container_ids: tuple[str, ...] = ()
    allowed_quality_tiers: tuple[str, ...] = ()
    excluded_artifact_ids: tuple[str, ...] = ()
    min_quality_percent: float | None = None
    min_build_price: int = 0
    max_results: int = 10


@dataclass(frozen=True)
class OptimizerConfig:
    time_limit_per_solve: float = 0.5
    max_solutions_per_container: int = 2
    num_search_workers: int = 1
    stat_scale: int = 10_000
    objective_scale: int = 10_000
    infection_safety_margin: float = 0.0001


@dataclass(frozen=True)
class BuildSolution:
    build_id: str
    objective_score: float
    total_price: int
    armor: dict[str, Any]
    container: dict[str, Any]
    artifacts: tuple[dict[str, Any], ...]
    stats: dict[str, float]
    derived: dict[str, float]
    infection: dict[str, Any]
    metrics: dict[str, float]
    solver_status: str
    solver_objective: float
    solver_best_bound: float
    solver_gap: float | None
    solve_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OptimizationResult:
    request: OptimizationRequest
    solutions: tuple[BuildSolution, ...]
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": asdict(self.request),
            "solutions": [solution.to_dict() for solution in self.solutions],
            "diagnostics": self.diagnostics,
        }


class ArtifactBuildOptimizer:
    """Query-time artifact optimizer independent of the old beam index."""

    def __init__(self, catalog: SolverCatalog, config: OptimizerConfig | None = None):
        self.catalog = catalog
        self.config = config or OptimizerConfig()
        self.mechanics = MechanicsConfig(**catalog.mechanics)
        self.groups_by_id = {group.group_id: group for group in catalog.artifact_groups}

    def search(self, request: OptimizationRequest) -> OptimizationResult:
        self._validate_request(request)
        started = time.perf_counter()
        groups = self._eligible_groups(request)
        armors = self._eligible_armors(request)
        containers = self._eligible_containers(request)
        candidates: list[BuildSolution] = []
        attempts = 0
        statuses: dict[str, int] = {}

        for container in containers:
            container_solutions, container_statuses = self._solve_container(
                request,
                container,
                groups,
                armors,
                min(request.max_results, self.config.max_solutions_per_container),
            )
            attempts += len(container_statuses)
            for status in container_statuses:
                statuses[status] = statuses.get(status, 0) + 1
            candidates.extend(container_solutions)

        candidates.sort(key=lambda item: (-item.objective_score, item.total_price, item.build_id))
        selected = self._deduplicate(candidates)[: request.max_results]
        return OptimizationResult(
            request=request,
            solutions=tuple(selected),
            diagnostics={
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "artifact_groups": len(groups),
                "armors": len(armors),
                "containers": len(containers),
                "solver_attempts": attempts,
                "solver_statuses": statuses,
                "candidate_solutions": len(candidates),
                "returned_solutions": len(selected),
            },
        )

    def _solve_container(
        self,
        request: OptimizationRequest,
        container: dict[str, Any],
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
        limit: int,
    ) -> tuple[list[BuildSolution], list[str]]:
        model, context = self._build_model(request, container, groups, armors)
        results: list[BuildSolution] = []
        statuses: list[str] = []
        for _ in range(limit):
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = self.config.time_limit_per_solve
            solver.parameters.num_search_workers = self.config.num_search_workers
            solve_started = time.perf_counter()
            status = solver.solve(model)
            elapsed = time.perf_counter() - solve_started
            status_name = solver.status_name(status)
            statuses.append(status_name)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                break
            solution = self._materialize_solution(
                request,
                container,
                groups,
                armors,
                context,
                solver,
                status_name,
                elapsed,
            )
            if solution is not None:
                results.append(solution)
            self._exclude_composition(model, context["count_vars"], solver)
        return results, statuses

    def _build_model(
        self,
        request: OptimizationRequest,
        container: dict[str, Any],
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
    ) -> tuple[cp_model.CpModel, dict[str, Any]]:
        model = cp_model.CpModel()
        capacity = int(container["capacity"])
        count_vars: list[cp_model.IntVar] = []
        quality_vars: list[cp_model.IntVar] = []
        quality_lows: list[int] = []

        for index, group in enumerate(groups):
            dynamic_low = group.quality_low
            if request.min_quality_percent is not None:
                dynamic_low = max(dynamic_low, int(math.ceil(request.min_quality_percent * 100.0 - 1e-9)))
            count = model.new_int_var(0, capacity, f"count_{index}")
            quality = model.new_int_var(0, capacity * group.quality_high, f"quality_sum_{index}")
            model.add(quality >= dynamic_low * count)
            model.add(quality <= group.quality_high * count)
            count_vars.append(count)
            quality_vars.append(quality)
            quality_lows.append(dynamic_low)

        model.add(sum(count_vars) == capacity)
        price_expression = sum(group.price * count_vars[index] for index, group in enumerate(groups))
        model.add(price_expression <= request.budget)
        if request.min_build_price > 0:
            model.add(price_expression >= request.min_build_price)

        armor_vars = [model.new_bool_var(f"armor_{index}") for index in range(len(armors))]
        model.add(sum(armor_vars) == 1)

        quality_denominator = self._quality_denominator(groups)
        stat_keys = self._stat_keys(groups, armors, container)
        stat_vars: dict[str, cp_model.IntVar] = {}
        for key in stat_keys:
            numerator = self._stat_numerator(
                key,
                groups,
                count_vars,
                quality_vars,
                armors,
                armor_vars,
                container,
                quality_denominator,
            )
            stat_var = model.new_int_var(
                -2_000 * self.config.stat_scale,
                10_000 * self.config.stat_scale,
                f"stat_{key}",
            )
            model.add_division_equality(stat_var, numerator, quality_denominator)
            stat_vars[key] = stat_var

        self._add_infection_constraints(
            model,
            groups,
            count_vars,
            quality_vars,
            container,
            quality_denominator,
        )
        metrics = self._metric_expressions(model, stat_vars)
        objective = self._objective_expression(request.preferences, metrics)
        for requested_key, target in request.targets.items():
            key = self._resolve_metric(requested_key)
            model.add(metrics[key] >= int(math.ceil(float(target) * self.config.stat_scale - 1e-9)))
        model.maximize(objective)
        return model, {
            "count_vars": count_vars,
            "quality_vars": quality_vars,
            "quality_lows": quality_lows,
            "armor_vars": armor_vars,
            "metric_expressions": metrics,
            "price_expression": price_expression,
            "objective_expression": objective,
        }

    def _stat_numerator(
        self,
        key: str,
        groups: list[ArtifactGroup],
        count_vars: list[cp_model.IntVar],
        quality_vars: list[cp_model.IntVar],
        armors: list[dict[str, Any]],
        armor_vars: list[cp_model.IntVar],
        container: dict[str, Any],
        denominator: int,
    ) -> cp_model.LinearExpr:
        scale = self.config.stat_scale
        terms: list[cp_model.LinearExpr] = []
        container_value = float((container.get("stats") or {}).get(key, 0.0))
        constant = int(round(container_value * scale)) * denominator
        effectiveness = float(container["effectiveness"]) / 100.0
        for index, group in enumerate(groups):
            low = float(group.stats_low.get(key, 0.0)) * effectiveness
            high = float(group.stats_high.get(key, 0.0)) * effectiveness
            count_coefficient, quality_coefficient = self._affine_coefficients(
                group,
                low,
                high,
                denominator,
            )
            if count_coefficient:
                terms.append(count_coefficient * count_vars[index])
            if quality_coefficient:
                terms.append(quality_coefficient * quality_vars[index])
        for index, armor in enumerate(armors):
            value = float((armor.get("stats") or {}).get(key, 0.0))
            coefficient = int(round(value * scale)) * denominator
            if coefficient:
                terms.append(coefficient * armor_vars[index])
        return cp_model.LinearExpr.sum(terms) + constant

    def _add_infection_constraints(
        self,
        model: cp_model.CpModel,
        groups: list[ArtifactGroup],
        count_vars: list[cp_model.IntVar],
        quality_vars: list[cp_model.IntVar],
        container: dict[str, Any],
        denominator: int,
    ) -> None:
        scale = self.config.stat_scale
        _, container_infections = split_infections(container.get("stats") or {})
        protection = max(0.0, min(float(container["inner_protection"]), 100.0)) / 100.0
        safety = self.config.infection_safety_margin
        for key in INFECTION_STATS:
            protected = key != "frost" or not self.mechanics.frost_ignores_inner_protection
            artifact_factor = 1.0 - protection if protected else 1.0
            terms: list[cp_model.LinearExpr] = []
            for index, group in enumerate(groups):
                low = float(group.infections_low.get(key, 0.0)) * artifact_factor
                high = float(group.infections_high.get(key, 0.0)) * artifact_factor
                count_coefficient, quality_coefficient = self._affine_coefficients(
                    group,
                    low,
                    high,
                    denominator,
                )
                if count_coefficient:
                    terms.append(count_coefficient * count_vars[index])
                if quality_coefficient:
                    terms.append(quality_coefficient * quality_vars[index])

            container_value = float(container_infections.get(key, 0.0))
            if protected and self.mechanics.container_infections_are_protected:
                container_value *= 1.0 - protection
            base_output = float(self.mechanics.base_infection_output.get(key, 0.0))
            constant = int(round((container_value + base_output + safety) * scale)) * denominator
            model.add(cp_model.LinearExpr.sum(terms) + constant <= 0)

    def _metric_expressions(
        self,
        model: cp_model.CpModel,
        stats: dict[str, cp_model.IntVar],
    ) -> dict[str, cp_model.LinearExpr]:
        scale = self.config.stat_scale
        zero = model.new_constant(0)
        bullet = stats.get("bullet_resistance", zero)
        vitality = stats.get("vitality", zero)
        movement = stats.get("movement_speed", zero)
        sprint = stats.get("sprint_speed", zero)
        regeneration = stats.get("health_regeneration", zero)
        periodic = stats.get("periodic_healing", zero)
        healing = stats.get("healing_effectiveness", zero)

        durability_product = model.new_int_var(-20_000_000_000_000_000, 20_000_000_000_000_000, "durability_product")
        model.add_multiplication_equality(
            durability_product,
            [bullet + 100 * scale, vitality + 100 * scale],
        )
        durability = model.new_int_var(-2_000 * scale, 10_000 * scale, "effective_durability")
        model.add_division_equality(durability, durability_product, 100 * scale)

        periodic_product = model.new_int_var(-20_000_000_000_000_000, 20_000_000_000_000_000, "periodic_product")
        model.add_multiplication_equality(periodic_product, [periodic, healing + 100 * scale])
        periodic_term = model.new_int_var(-2_000 * scale, 10_000 * scale, "periodic_term")
        model.add_division_equality(periodic_term, periodic_product, 100 * scale)
        regeneration_term = model.new_int_var(-2_000 * scale, 10_000 * scale, "regeneration_term")
        model.add_division_equality(regeneration_term, regeneration, 5)
        hp_regen = model.new_int_var(-2_000 * scale, 10_000 * scale, "hp_regen_score")
        model.add(hp_regen == scale // 2 + regeneration_term + periodic_term)

        result: dict[str, cp_model.LinearExpr] = dict(stats)
        result["effective_durability"] = durability
        result["total_sprint_speed"] = 100 * scale + movement + sprint
        result["hp_regen_score"] = hp_regen
        return result

    def _objective_expression(
        self,
        preferences: dict[str, float],
        metrics: dict[str, cp_model.LinearExpr],
    ) -> cp_model.LinearExpr:
        terms: list[cp_model.LinearExpr] = []
        for requested_key, weight in preferences.items():
            key = self._resolve_metric(requested_key)
            normalized_weight = max(-2.0, min(2.0, float(weight)))
            if abs(normalized_weight) <= 1e-12:
                continue
            coefficient = int(round(normalized_weight * self.config.objective_scale / METRIC_SCALES[key]))
            if coefficient:
                terms.append(coefficient * metrics[key])
        if not terms:
            raise ValueError("At least one non-zero preference is required")
        return cp_model.LinearExpr.sum(terms)

    def _materialize_solution(
        self,
        request: OptimizationRequest,
        container: dict[str, Any],
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
        context: dict[str, Any],
        solver: cp_model.CpSolver,
        status_name: str,
        elapsed: float,
    ) -> BuildSolution | None:
        armor_index = next(
            index
            for index, variable in enumerate(context["armor_vars"])
            if solver.value(variable)
        )
        armor = armors[armor_index]
        artifacts: list[dict[str, Any]] = []
        for index, group in enumerate(groups):
            count = int(solver.value(context["count_vars"][index]))
            if count <= 0:
                continue
            quality_sum = int(solver.value(context["quality_vars"][index]))
            qualities = self._distribute_quality(
                count,
                quality_sum,
                context["quality_lows"][index],
                group.quality_high,
            )
            for quality in qualities:
                artifacts.append(self._artifact_view(group, quality, container))

        stats, report = self._evaluate_exact(artifacts, armor, container)
        if not report["valid"]:
            return None
        derived = derived_stats(stats)
        if not self._passes_exact_targets(request.targets, stats, derived):
            return None
        metrics = {
            self._resolve_metric(key): self._metric_value(stats, derived, self._resolve_metric(key))
            for key in set(request.preferences) | set(request.targets)
        }
        exact_score = sum(
            max(-2.0, min(2.0, float(weight)))
            * self._metric_value(stats, derived, self._resolve_metric(key))
            / METRIC_SCALES[self._resolve_metric(key)]
            for key, weight in request.preferences.items()
        )
        objective = float(solver.objective_value)
        bound = float(solver.best_objective_bound)
        gap = abs(objective - bound) / max(1.0, abs(objective))
        build_id = f"{armor['item_id']}:{container['container_id']}:" + "|".join(
            f"{item['group_id']}@{item['quality_percent']:.2f}" for item in artifacts
        )
        return BuildSolution(
            build_id=build_id,
            objective_score=round(exact_score, 8),
            total_price=sum(int(item["price"]) for item in artifacts),
            armor=self._armor_view(armor),
            container=self._container_view(container),
            artifacts=tuple(artifacts),
            stats=stats,
            derived=derived,
            infection=report,
            metrics=metrics,
            solver_status=status_name,
            solver_objective=objective,
            solver_best_bound=bound,
            solver_gap=round(gap, 8),
            solve_seconds=round(elapsed, 6),
        )

    def _artifact_view(
        self,
        group: ArtifactGroup,
        quality: int,
        container: dict[str, Any],
    ) -> dict[str, Any]:
        quality_percent = quality / 100.0
        stats = self._interpolate_properties(group.stats_low, group.stats_high, group, quality)
        infections = self._interpolate_properties(
            group.infections_low,
            group.infections_high,
            group,
            quality,
        )
        effectiveness = float(container["effectiveness"]) / 100.0
        effective_stats = {key: value * effectiveness for key, value in stats.items()}
        return {
            "group_id": group.group_id,
            "item_id": group.item_id,
            "name": group.name,
            "quality_tier": group.quality_tier,
            "quality_percent": quality_percent,
            "upgrade_level": self.catalog.artifact_upgrade_level,
            "price": group.price,
            "price_basis": group.price_basis,
            "stats": effective_stats,
            "infections": infections,
            "liquidity_score": group.liquidity_score,
            "confidence_score": group.confidence_score,
        }

    def _evaluate_exact(
        self,
        artifacts: list[dict[str, Any]],
        armor: dict[str, Any],
        container: dict[str, Any],
    ) -> tuple[dict[str, float], dict[str, Any]]:
        container_stats, container_infections = split_infections(container.get("stats") or {})
        artifact_stats = add_stats(*(artifact["stats"] for artifact in artifacts))
        artifact_infections = add_stats(*(artifact["infections"] for artifact in artifacts))
        stats = add_stats(container_stats, artifact_stats, armor.get("stats") or {})
        report = infection_report(
            artifact_infections,
            container_infections,
            float(container["inner_protection"]),
            self.mechanics,
        )
        return stats, report

    def _eligible_groups(self, request: OptimizationRequest) -> list[ArtifactGroup]:
        tiers = set(request.allowed_quality_tiers)
        excluded = set(request.excluded_artifact_ids)
        groups = [
            group
            for group in self.catalog.artifact_groups
            if group.item_id not in excluded
            and (not tiers or group.quality_tier in tiers)
            and group.price <= request.budget
            and (
                request.min_quality_percent is None
                or group.quality_high + 1e-9 >= request.min_quality_percent * 100.0
            )
        ]
        if not groups:
            raise ValueError("No artifact groups match the request")
        return groups

    def _eligible_armors(self, request: OptimizationRequest) -> list[dict[str, Any]]:
        ids = set(request.armor_ids)
        armors = [
            armor
            for armor in self.catalog.armors
            if not ids or armor["item_id"] in ids or armor["base_id"] in ids
        ]
        if not armors:
            raise ValueError("No armors match the request")
        return armors

    def _eligible_containers(self, request: OptimizationRequest) -> list[dict[str, Any]]:
        ids = set(request.container_ids)
        containers = [
            container
            for container in self.catalog.containers
            if not ids or container["container_id"] in ids
        ]
        if not containers:
            raise ValueError("No containers match the request")
        return containers

    def _validate_request(self, request: OptimizationRequest) -> None:
        if request.budget <= 0:
            raise ValueError("budget must be positive")
        if request.max_results <= 0:
            raise ValueError("max_results must be positive")
        for key in set(request.preferences) | set(request.targets):
            resolved = self._resolve_metric(key)
            if resolved not in METRIC_SCALES:
                raise ValueError(f"Unsupported metric: {key}")

    def _resolve_metric(self, key: str) -> str:
        return METRIC_ALIASES.get(key, key)

    def _passes_exact_targets(
        self,
        targets: dict[str, float],
        stats: dict[str, float],
        derived: dict[str, float],
    ) -> bool:
        return all(
            self._metric_value(stats, derived, self._resolve_metric(key)) + 1e-7 >= float(target)
            for key, target in targets.items()
        )

    def _metric_value(
        self,
        stats: dict[str, float],
        derived: dict[str, float],
        key: str,
    ) -> float:
        if key in derived:
            return float(derived[key])
        return float(stats.get(key, 0.0))

    def _quality_denominator(self, groups: list[ArtifactGroup]) -> int:
        denominator = 1
        for group in groups:
            delta = group.quality_high - group.quality_low
            if delta > 0:
                denominator = math.lcm(denominator, delta)
        return denominator

    def _affine_coefficients(
        self,
        group: ArtifactGroup,
        low_value: float,
        high_value: float,
        denominator: int,
    ) -> tuple[int, int]:
        scale = self.config.stat_scale
        low_scaled = int(round(low_value * scale))
        high_scaled = int(round(high_value * scale))
        delta = group.quality_high - group.quality_low
        if delta <= 0:
            return low_scaled * denominator, 0
        factor = denominator // delta
        count_coefficient = (
            low_scaled * group.quality_high - high_scaled * group.quality_low
        ) * factor
        quality_coefficient = (high_scaled - low_scaled) * factor
        return count_coefficient, quality_coefficient

    def _stat_keys(
        self,
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
        container: dict[str, Any],
    ) -> set[str]:
        keys = set(PRIMARY_STATS) | set(SECONDARY_STATS)
        keys.update(container.get("stats") or {})
        for group in groups:
            keys.update(group.stats_low)
            keys.update(group.stats_high)
        for armor in armors:
            keys.update(armor.get("stats") or {})
        return keys - set(INFECTION_STATS)

    def _interpolate_properties(
        self,
        low: dict[str, float],
        high: dict[str, float],
        group: ArtifactGroup,
        quality: int,
    ) -> dict[str, float]:
        delta = group.quality_high - group.quality_low
        progress = 0.0 if delta <= 0 else (quality - group.quality_low) / delta
        return {
            key: float(low.get(key, 0.0))
            + (float(high.get(key, 0.0)) - float(low.get(key, 0.0))) * progress
            for key in set(low) | set(high)
        }

    def _distribute_quality(self, count: int, total: int, low: int, high: int) -> list[int]:
        qualities: list[int] = []
        remaining = total
        for index in range(count):
            copies_after = count - index - 1
            quality = min(high, remaining - low * copies_after)
            qualities.append(quality)
            remaining -= quality
        if remaining != 0 or any(quality < low or quality > high for quality in qualities):
            raise ValueError("Solver returned an invalid aggregate quality")
        return qualities

    def _exclude_composition(
        self,
        model: cp_model.CpModel,
        count_vars: list[cp_model.IntVar],
        solver: cp_model.CpSolver,
    ) -> None:
        equalities: list[cp_model.IntVar] = []
        for index, variable in enumerate(count_vars):
            value = int(solver.value(variable))
            if value <= 0:
                continue
            same = model.new_bool_var(f"same_{len(equalities)}_{index}_{value}")
            model.add(variable == value).only_enforce_if(same)
            model.add(variable != value).only_enforce_if(same.Not())
            equalities.append(same)
        if equalities:
            model.add(sum(equalities) <= len(equalities) - 1)

    def _deduplicate(self, candidates: list[BuildSolution]) -> list[BuildSolution]:
        selected: list[BuildSolution] = []
        seen: set[tuple[Any, ...]] = set()
        for candidate in candidates:
            signature = (
                candidate.armor["item_id"],
                candidate.container["container_id"],
                tuple(sorted((artifact["group_id"], artifact["quality_percent"]) for artifact in candidate.artifacts)),
            )
            if signature in seen:
                continue
            seen.add(signature)
            selected.append(candidate)
        return selected

    def _armor_view(self, armor: dict[str, Any]) -> dict[str, Any]:
        return {
            key: armor.get(key)
            for key in ("item_id", "base_id", "name", "rank", "category", "upgrade_level")
        }

    def _container_view(self, container: dict[str, Any]) -> dict[str, Any]:
        return {
            key: container.get(key)
            for key in (
                "container_id",
                "name",
                "rank",
                "capacity",
                "inner_protection",
                "effectiveness",
            )
        }

