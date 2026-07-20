from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import time
from typing import Any

import numpy as np
from ortools.sat.python import cp_model
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

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
    excluded_armor_ids: tuple[str, ...] = ()
    excluded_container_ids: tuple[str, ...] = ()
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
    nonlinear_iterations: int = 4


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
        if self._can_use_milp(request):
            return self._solve_container_milp(request, container, groups, armors, limit)
        return self._solve_container_cp_sat(request, container, groups, armors, limit)

    def _solve_container_cp_sat(
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

    def _solve_container_milp(
        self,
        request: OptimizationRequest,
        container: dict[str, Any],
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
        limit: int,
    ) -> tuple[list[BuildSolution], list[str]]:
        results: list[BuildSolution] = []
        statuses: list[str] = []
        excluded_compositions: list[tuple[int, ...]] = []
        for _ in range(limit):
            reference_stats = self._initial_reference_stats(container, armors, request.preferences)
            iterations = 1 if self._is_linear_request(request) else max(1, self.config.nonlinear_iterations)
            best_solution: BuildSolution | None = None
            best_counts: tuple[int, ...] | None = None
            final_status = "INFEASIBLE"
            for _iteration in range(iterations):
                objective_coefficients = self._linearized_objective_coefficients(
                    request.preferences,
                    reference_stats,
                )
                problem = self._build_milp_problem(
                    request,
                    container,
                    groups,
                    armors,
                    excluded_compositions,
                    objective_coefficients,
                )
                solve_started = time.perf_counter()
                result = milp(
                    c=problem["objective"],
                    integrality=problem["integrality"],
                    bounds=Bounds(problem["lower_bounds"], problem["upper_bounds"]),
                    constraints=LinearConstraint(
                        problem["matrix"],
                        problem["constraint_lows"],
                        problem["constraint_highs"],
                    ),
                    options={
                        "time_limit": self.config.time_limit_per_solve,
                        "mip_rel_gap": 0.001,
                        "presolve": True,
                    },
                )
                elapsed = time.perf_counter() - solve_started
                final_status = self._milp_status_name(int(result.status), result.x is not None)
                if result.x is None:
                    break
                counts = tuple(int(round(result.x[2 * index])) for index in range(len(groups)))
                quality_sums = tuple(int(round(result.x[2 * index + 1])) for index in range(len(groups)))
                armor_offset = 2 * len(groups)
                armor_index = max(range(len(armors)), key=lambda index: result.x[armor_offset + index])
                objective = -float(result.fun)
                dual_bound = -float(result.mip_dual_bound) if result.mip_dual_bound is not None else objective
                nonlinear = not self._is_linear_request(request)
                solution = self._materialize_values(
                    request,
                    container,
                    groups,
                    armors,
                    counts,
                    quality_sums,
                    tuple(problem["quality_lows"]),
                    armor_index,
                    "HEURISTIC" if nonlinear else final_status,
                    objective,
                    dual_bound,
                    None if nonlinear else (float(result.mip_gap) if result.mip_gap is not None else None),
                    elapsed,
                )
                if solution is None:
                    break
                if best_solution is None or solution.objective_score > best_solution.objective_score:
                    best_solution = solution
                    best_counts = counts
                reference_stats = solution.stats
            statuses.append("HEURISTIC" if best_solution and not self._is_linear_request(request) else final_status)
            if best_solution is None or best_counts is None:
                break
            results.append(best_solution)
            excluded_compositions.append(best_counts)
        return results, statuses

    def _build_milp_problem(
        self,
        request: OptimizationRequest,
        container: dict[str, Any],
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
        excluded_compositions: list[tuple[int, ...]],
        objective_coefficients: dict[str, float],
    ) -> dict[str, Any]:
        group_count = len(groups)
        armor_offset = 2 * group_count
        capacity = int(container["capacity"])
        exclusion_specs: list[tuple[int, int, int]] = []
        next_variable = armor_offset + len(armors)
        for exclusion_index, composition in enumerate(excluded_compositions):
            for group_index, value in enumerate(composition):
                if value > 0:
                    exclusion_specs.append((exclusion_index, group_index, value))
                    next_variable += 2
        variable_count = next_variable

        objective = np.zeros(variable_count)
        integrality = np.zeros(variable_count)
        lower_bounds = np.zeros(variable_count)
        upper_bounds = np.full(variable_count, np.inf)
        quality_lows: list[int] = []

        for index, group in enumerate(groups):
            dynamic_low = group.quality_low
            if request.min_quality_percent is not None:
                dynamic_low = max(dynamic_low, int(math.ceil(request.min_quality_percent * 100.0 - 1e-9)))
            quality_lows.append(dynamic_low)
            integrality[2 * index] = 1
            upper_bounds[2 * index] = capacity
            upper_bounds[2 * index + 1] = capacity * group.quality_high
        integrality[armor_offset : armor_offset + len(armors)] = 1
        upper_bounds[armor_offset : armor_offset + len(armors)] = 1
        if exclusion_specs:
            exclusion_offset = armor_offset + len(armors)
            integrality[exclusion_offset:] = 1
            upper_bounds[exclusion_offset:] = 1

        rows: list[list[tuple[int, float]]] = []
        constraint_lows: list[float] = []
        constraint_highs: list[float] = []

        def add_constraint(items: list[tuple[int, float]], low: float, high: float) -> None:
            rows.append(items)
            constraint_lows.append(low)
            constraint_highs.append(high)

        add_constraint([(2 * index, 1.0) for index in range(group_count)], capacity, capacity)
        add_constraint(
            [(2 * index, float(group.price)) for index, group in enumerate(groups)],
            float(request.min_build_price) if request.min_build_price > 0 else -np.inf,
            float(request.budget),
        )
        add_constraint(
            [(armor_offset + index, 1.0) for index in range(len(armors))],
            1.0,
            1.0,
        )
        for index, group in enumerate(groups):
            add_constraint([(2 * index, -quality_lows[index]), (2 * index + 1, 1.0)], 0.0, np.inf)
            add_constraint([(2 * index, -group.quality_high), (2 * index + 1, 1.0)], -np.inf, 0.0)

        stat_forms = self._milp_stat_forms(groups, armors, container, variable_count)
        infection_forms = self._milp_infection_forms(groups, container, variable_count)
        for coefficients, constant in infection_forms.values():
            items = [(index, value) for index, value in enumerate(coefficients) if abs(value) > 1e-15]
            add_constraint(items, -np.inf, -constant - self.config.infection_safety_margin)

        metric_forms = dict(stat_forms)
        movement = stat_forms.get("movement_speed", (np.zeros(variable_count), 0.0))
        sprint = stat_forms.get("sprint_speed", (np.zeros(variable_count), 0.0))
        metric_forms["total_sprint_speed"] = (movement[0] + sprint[0], 100.0 + movement[1] + sprint[1])

        for key, normalized_weight in objective_coefficients.items():
            coefficients, _constant = metric_forms[key]
            objective -= normalized_weight * coefficients
        for requested_key, target in request.targets.items():
            key = self._resolve_metric(requested_key)
            coefficients, constant = metric_forms[key]
            items = [(index, value) for index, value in enumerate(coefficients) if abs(value) > 1e-15]
            add_constraint(items, float(target) - constant, np.inf)

        exclusion_variables: dict[tuple[int, int], tuple[int, int]] = {}
        cursor = armor_offset + len(armors)
        for exclusion_index, group_index, value in exclusion_specs:
            less_index = cursor
            greater_index = cursor + 1
            cursor += 2
            exclusion_variables[(exclusion_index, group_index)] = (less_index, greater_index)
            add_constraint(
                [(2 * group_index, 1.0), (less_index, float(capacity))],
                -np.inf,
                float(value - 1 + capacity),
            )
            add_constraint(
                [(2 * group_index, 1.0), (greater_index, -float(capacity))],
                float(value + 1 - capacity),
                np.inf,
            )
            add_constraint([(less_index, 1.0), (greater_index, 1.0)], -np.inf, 1.0)
        for exclusion_index, composition in enumerate(excluded_compositions):
            flags: list[tuple[int, float]] = []
            for group_index, value in enumerate(composition):
                if value <= 0:
                    continue
                less_index, greater_index = exclusion_variables[(exclusion_index, group_index)]
                flags.extend(((less_index, 1.0), (greater_index, 1.0)))
            add_constraint(flags, 1.0, np.inf)

        matrix = lil_matrix((len(rows), variable_count), dtype=float)
        for row_index, items in enumerate(rows):
            for column_index, value in items:
                matrix[row_index, column_index] += value
        return {
            "objective": objective,
            "integrality": integrality,
            "lower_bounds": lower_bounds,
            "upper_bounds": upper_bounds,
            "matrix": matrix.tocsr(),
            "constraint_lows": np.asarray(constraint_lows),
            "constraint_highs": np.asarray(constraint_highs),
            "quality_lows": quality_lows,
        }

    def _milp_stat_forms(
        self,
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
        container: dict[str, Any],
        variable_count: int,
    ) -> dict[str, tuple[np.ndarray, float]]:
        forms: dict[str, tuple[np.ndarray, float]] = {}
        armor_offset = 2 * len(groups)
        effectiveness = float(container["effectiveness"]) / 100.0
        for key in self._stat_keys(groups, armors, container):
            coefficients = np.zeros(variable_count)
            for index, group in enumerate(groups):
                low = float(group.stats_low.get(key, 0.0)) * effectiveness
                high = float(group.stats_high.get(key, 0.0)) * effectiveness
                intercept, slope = self._float_affine(group, low, high)
                coefficients[2 * index] = intercept
                coefficients[2 * index + 1] = slope
            for index, armor in enumerate(armors):
                coefficients[armor_offset + index] = float((armor.get("stats") or {}).get(key, 0.0))
            forms[key] = (coefficients, float((container.get("stats") or {}).get(key, 0.0)))
        return forms

    def _milp_infection_forms(
        self,
        groups: list[ArtifactGroup],
        container: dict[str, Any],
        variable_count: int,
    ) -> dict[str, tuple[np.ndarray, float]]:
        forms: dict[str, tuple[np.ndarray, float]] = {}
        _, container_infections = split_infections(container.get("stats") or {})
        protection = max(0.0, min(float(container["inner_protection"]), 100.0)) / 100.0
        for key in INFECTION_STATS:
            coefficients = np.zeros(variable_count)
            protected = key != "frost" or not self.mechanics.frost_ignores_inner_protection
            artifact_factor = 1.0 - protection if protected else 1.0
            for index, group in enumerate(groups):
                low = float(group.infections_low.get(key, 0.0)) * artifact_factor
                high = float(group.infections_high.get(key, 0.0)) * artifact_factor
                intercept, slope = self._float_affine(group, low, high)
                coefficients[2 * index] = intercept
                coefficients[2 * index + 1] = slope
            container_value = float(container_infections.get(key, 0.0))
            if protected and self.mechanics.container_infections_are_protected:
                container_value *= 1.0 - protection
            forms[key] = (
                coefficients,
                container_value + float(self.mechanics.base_infection_output.get(key, 0.0)),
            )
        return forms

    def _float_affine(
        self,
        group: ArtifactGroup,
        low_value: float,
        high_value: float,
    ) -> tuple[float, float]:
        delta = group.quality_high - group.quality_low
        if delta <= 0:
            return low_value, 0.0
        slope = (high_value - low_value) / delta
        return low_value - slope * group.quality_low, slope

    def _is_linear_request(self, request: OptimizationRequest) -> bool:
        metrics = {
            self._resolve_metric(key)
            for key in set(request.preferences) | set(request.targets)
        }
        return not metrics.intersection({"effective_durability", "hp_regen_score"})

    def _can_use_milp(self, request: OptimizationRequest) -> bool:
        target_metrics = {self._resolve_metric(key) for key in request.targets}
        return not target_metrics.intersection({"effective_durability", "hp_regen_score"})

    def _linearized_objective_coefficients(
        self,
        preferences: dict[str, float],
        reference_stats: dict[str, float],
    ) -> dict[str, float]:
        coefficients: dict[str, float] = {}

        def add(key: str, value: float) -> None:
            coefficients[key] = coefficients.get(key, 0.0) + value

        for requested_key, raw_weight in preferences.items():
            key = self._resolve_metric(requested_key)
            weight = max(-2.0, min(2.0, float(raw_weight)))
            if abs(weight) <= 1e-12:
                continue
            if key == "effective_durability":
                bullet = float(reference_stats.get("bullet_resistance", 0.0))
                vitality = float(reference_stats.get("vitality", 0.0))
                base = weight / METRIC_SCALES[key]
                add("bullet_resistance", base * (vitality + 100.0) / 100.0)
                add("vitality", base * (bullet + 100.0) / 100.0)
            elif key == "hp_regen_score":
                periodic = float(reference_stats.get("periodic_healing", 0.0))
                healing = float(reference_stats.get("healing_effectiveness", 0.0))
                base = weight / METRIC_SCALES[key]
                add("health_regeneration", base / 5.0)
                add("periodic_healing", base * (1.0 + healing / 100.0))
                add("healing_effectiveness", base * periodic / 100.0)
            elif key == "total_sprint_speed":
                base = weight / METRIC_SCALES[key]
                add("movement_speed", base)
                add("sprint_speed", base)
            else:
                add(key, weight / METRIC_SCALES[key])
        return coefficients

    def _initial_reference_stats(
        self,
        container: dict[str, Any],
        armors: list[dict[str, Any]],
        preferences: dict[str, float],
    ) -> dict[str, float]:
        best_stats: dict[str, float] | None = None
        best_score = -math.inf
        container_stats, _infections = split_infections(container.get("stats") or {})
        for armor in armors:
            stats = add_stats(container_stats, armor.get("stats") or {})
            derived = derived_stats(stats)
            score = sum(
                max(-2.0, min(2.0, float(weight)))
                * self._metric_value(stats, derived, self._resolve_metric(key))
                / METRIC_SCALES[self._resolve_metric(key)]
                for key, weight in preferences.items()
            )
            if score > best_score:
                best_score = score
                best_stats = stats
        return best_stats or dict(container_stats)

    def _milp_status_name(self, status: int, has_solution: bool) -> str:
        if status == 0:
            return "OPTIMAL"
        if status == 1 and has_solution:
            return "FEASIBLE"
        return {1: "LIMIT", 2: "INFEASIBLE", 3: "UNBOUNDED"}.get(status, "ERROR")

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
        requested_metrics = {
            self._resolve_metric(key)
            for key in set(request.preferences) | set(request.targets)
        }
        metrics = self._metric_expressions(model, stat_vars, requested_metrics)
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
        requested_metrics: set[str],
    ) -> dict[str, cp_model.LinearExpr]:
        scale = self.config.stat_scale
        zero = model.new_constant(0)
        result: dict[str, cp_model.LinearExpr] = dict(stats)
        if "total_sprint_speed" in requested_metrics:
            movement = stats.get("movement_speed", zero)
            sprint = stats.get("sprint_speed", zero)
            result["total_sprint_speed"] = 100 * scale + movement + sprint

        if "effective_durability" in requested_metrics:
            bullet = stats.get("bullet_resistance", zero)
            vitality = stats.get("vitality", zero)
            durability_product = model.new_int_var(
                -20_000_000_000_000_000,
                20_000_000_000_000_000,
                "durability_product",
            )
            model.add_multiplication_equality(
                durability_product,
                [bullet + 100 * scale, vitality + 100 * scale],
            )
            durability = model.new_int_var(-2_000 * scale, 10_000 * scale, "effective_durability")
            model.add_division_equality(durability, durability_product, 100 * scale)
            result["effective_durability"] = durability

        if "hp_regen_score" in requested_metrics:
            regeneration = stats.get("health_regeneration", zero)
            periodic = stats.get("periodic_healing", zero)
            healing = stats.get("healing_effectiveness", zero)
            periodic_product = model.new_int_var(
                -20_000_000_000_000_000,
                20_000_000_000_000_000,
                "periodic_product",
            )
            model.add_multiplication_equality(periodic_product, [periodic, healing + 100 * scale])
            periodic_term = model.new_int_var(-2_000 * scale, 10_000 * scale, "periodic_term")
            model.add_division_equality(periodic_term, periodic_product, 100 * scale)
            regeneration_term = model.new_int_var(-2_000 * scale, 10_000 * scale, "regeneration_term")
            model.add_division_equality(regeneration_term, regeneration, 5)
            hp_regen = model.new_int_var(-2_000 * scale, 10_000 * scale, "hp_regen_score")
            model.add(hp_regen == scale // 2 + regeneration_term + periodic_term)
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
        counts = tuple(
            int(solver.value(variable))
            for variable in context["count_vars"]
        )
        quality_sums = tuple(
            int(solver.value(variable))
            for variable in context["quality_vars"]
        )
        objective = float(solver.objective_value)
        bound = float(solver.best_objective_bound)
        gap = abs(objective - bound) / max(1.0, abs(objective))
        return self._materialize_values(
            request,
            container,
            groups,
            armors,
            counts,
            quality_sums,
            tuple(context["quality_lows"]),
            armor_index,
            status_name,
            objective,
            bound,
            gap,
            elapsed,
        )

    def _materialize_values(
        self,
        request: OptimizationRequest,
        container: dict[str, Any],
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
        counts: tuple[int, ...],
        quality_sums: tuple[int, ...],
        quality_lows: tuple[int, ...],
        armor_index: int,
        status_name: str,
        solver_objective: float,
        solver_best_bound: float,
        solver_gap: float | None,
        elapsed: float,
    ) -> BuildSolution | None:
        armor = armors[armor_index]
        artifacts: list[dict[str, Any]] = []
        for index, group in enumerate(groups):
            count = counts[index]
            if count <= 0:
                continue
            quality_sum = quality_sums[index]
            qualities = self._distribute_quality(
                count,
                quality_sum,
                quality_lows[index],
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
            solver_objective=solver_objective,
            solver_best_bound=solver_best_bound,
            solver_gap=None if solver_gap is None else round(solver_gap, 8),
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
        excluded = set(request.excluded_armor_ids)
        armors = [
            armor
            for armor in self.catalog.armors
            if not ids or armor["item_id"] in ids or armor["base_id"] in ids
            if armor["item_id"] not in excluded and armor["base_id"] not in excluded
        ]
        if not armors:
            raise ValueError("No armors match the request")
        return armors

    def _eligible_containers(self, request: OptimizationRequest) -> list[dict[str, Any]]:
        ids = set(request.container_ids)
        excluded = set(request.excluded_container_ids)
        containers = [
            container
            for container in self.catalog.containers
            if not ids or container["container_id"] in ids
            if container["container_id"] not in excluded
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
