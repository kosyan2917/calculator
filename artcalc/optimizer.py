from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field, replace
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
    QUALITY_ORDER,
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

MINIMIZE_METRICS = {"bleeding_output"}
NONLINEAR_METRICS = {"effective_durability", "hp_regen_score"}


@dataclass(frozen=True)
class OptimizationRequest:
    budget: int
    targets: dict[str, float]
    max_quality_tier: str = "exclusive"
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
    max_solutions_per_container: int = 6
    num_search_workers: int = 1
    stat_scale: int = 10_000
    objective_scale: int = 10_000
    infection_safety_margin: float = 0.0001
    nonlinear_iterations: int = 4


@dataclass(frozen=True)
class BuildSolution:
    build_id: str
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
    search_focus: str = "price"

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


@dataclass(frozen=True)
class FocusedSearchResult:
    solutions: tuple[BuildSolution, ...]
    statuses: tuple[str, ...]


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

        per_container_limit = min(
            request.max_results,
            self.config.max_solutions_per_container,
            6 if len(containers) == 1 else 3,
        )

        for container in containers:
            container_solutions, container_statuses = self._solve_container(
                request,
                container,
                groups,
                armors,
                per_container_limit,
            )
            attempts += len(container_statuses)
            for status in container_statuses:
                statuses[status] = statuses.get(status, 0) + 1
            candidates.extend(container_solutions)

        unique_candidates = self._deduplicate(candidates)
        selected = self._select_diverse_portfolio(unique_candidates, request.max_results)
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
                "unique_candidate_solutions": len(unique_candidates),
                "returned_solutions": len(selected),
                "search_complete": not any(
                    status in {"LIMIT", "UNKNOWN", "FEASIBLE", "HEURISTIC_SEED"}
                    for status in statuses
                ),
            },
        )

    def solve_focus(
        self,
        request: OptimizationRequest,
        container_id: str,
        focus: str,
        limit: int = 1,
    ) -> FocusedSearchResult:
        self._validate_request(request)
        if focus not in {"price", "speed", "durability"}:
            raise ValueError(f"Unsupported search focus: {focus}")
        if limit <= 0:
            raise ValueError("limit must be positive")

        focused_request = replace(request, container_ids=(container_id,))
        groups = self._eligible_groups(focused_request)
        armors = self._eligible_armors(focused_request)
        containers = self._eligible_containers(focused_request)
        if len(containers) != 1:
            raise ValueError(f"Container is not unique: {container_id}")
        solutions, statuses = self._solve_focus(
            focused_request,
            containers[0],
            groups,
            armors,
            limit,
            focus,
        )
        return FocusedSearchResult(tuple(solutions), tuple(statuses))

    def _solve_container(
        self,
        request: OptimizationRequest,
        container: dict[str, Any],
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
        limit: int,
    ) -> tuple[list[BuildSolution], list[str]]:
        focuses = ("price",) if limit == 1 else ("price", "speed", "durability")
        quota = (
            1
            if not self._can_use_milp(request)
            else max(1, math.ceil(limit / len(focuses)))
        )
        results: list[BuildSolution] = []
        statuses: list[str] = []
        for focus in focuses:
            focused, focused_statuses = self._solve_focus(
                request, container, groups, armors, quota, focus
            )
            results.extend(replace(solution, search_focus=focus) for solution in focused)
            statuses.extend(focused_statuses)

        combined = self._deduplicate(results)
        return combined[:limit], statuses

    def _solve_focus(
        self,
        request: OptimizationRequest,
        container: dict[str, Any],
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
        limit: int,
        focus: str,
    ) -> tuple[list[BuildSolution], list[str]]:
        if self._can_use_milp(request):
            objective_mode = "cost" if focus == "price" else "targets"
            objective_targets = None if focus == "price" else {focus: 1.0}
            focused, statuses = self._solve_container_milp(
                request,
                container,
                groups,
                armors,
                limit,
                objective_mode=objective_mode,
                objective_targets=objective_targets,
            )
        else:
            focused, statuses = self._solve_nonlinear_focus(
                request,
                container,
                groups,
                armors,
                limit,
                focus,
            )
        return [replace(solution, search_focus=focus) for solution in focused], statuses

    def _solve_nonlinear_focus(
        self,
        request: OptimizationRequest,
        container: dict[str, Any],
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
        limit: int,
        focus: str,
    ) -> tuple[list[BuildSolution], list[str]]:
        seed_request = request
        objective_mode = "cost" if focus == "price" else "targets"
        seed_objectives = None if focus == "price" else {focus: 1.0}
        seeds, seed_statuses = self._solve_container_milp(
            seed_request,
            container,
            groups,
            armors,
            limit,
            objective_mode=objective_mode,
            objective_targets=seed_objectives,
        )
        valid_seeds = [
            replace(seed, solver_status="FEASIBLE_SEED", search_focus=focus)
            for seed in seeds
            if self._passes_exact_targets(request.targets, seed.stats, seed.derived)
        ]
        if len(valid_seeds) >= limit:
            valid_seeds.sort(key=lambda item: self._focus_sort_key(item, focus))
            return valid_seeds[:limit], seed_statuses
        exact, exact_statuses = self._solve_container_cp_sat(
            request,
            container,
            groups,
            armors,
            limit,
            objective_focus=focus,
            initial_solutions=valid_seeds,
            hint_solution=seeds[0] if seeds else None,
        )
        return exact, seed_statuses + exact_statuses

    def _solve_container_cp_sat(
        self,
        request: OptimizationRequest,
        container: dict[str, Any],
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
        limit: int,
        objective_focus: str = "price",
        initial_solutions: list[BuildSolution] | None = None,
        hint_solution: BuildSolution | None = None,
    ) -> tuple[list[BuildSolution], list[str]]:
        model, context = self._build_model(
            request, container, groups, armors, objective_focus=objective_focus
        )
        if hint_solution is not None:
            self._apply_solution_hint(model, context, groups, armors, hint_solution)
        fallback_results = list(initial_solutions or [])
        results: list[BuildSolution] = []
        statuses: list[str] = []
        for _ in range(max(1, limit)):
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
        results = self._deduplicate(results + fallback_results)
        results = [replace(solution, search_focus=objective_focus) for solution in results]
        results.sort(key=lambda item: self._focus_sort_key(item, objective_focus))
        return results[:limit], statuses

    def _solve_container_milp(
        self,
        request: OptimizationRequest,
        container: dict[str, Any],
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
        limit: int,
        objective_mode: str,
        objective_targets: dict[str, float] | None = None,
    ) -> tuple[list[BuildSolution], list[str]]:
        results: list[BuildSolution] = []
        statuses: list[str] = []
        excluded_compositions: list[tuple[int, ...]] = []
        for _ in range(limit):
            reference_stats = self._initial_reference_stats(
                container,
                armors,
                objective_targets or {},
            )
            iterations = (
                max(1, self.config.nonlinear_iterations)
                if self._contains_nonlinear_metrics(request.targets)
                or (
                    objective_mode == "targets"
                    and self._contains_nonlinear_metrics(objective_targets or {})
                )
                else 1
            )
            best_solution: BuildSolution | None = None
            best_counts: tuple[int, ...] | None = None
            previous_signature: tuple[Any, ...] | None = None
            final_status = "INFEASIBLE"
            for _iteration in range(iterations):
                objective_coefficients = self._linearized_objective_coefficients(
                    self._target_weights(objective_targets or {}),
                    reference_stats,
                )
                problem = self._build_milp_problem(
                    request,
                    container,
                    groups,
                    armors,
                    excluded_compositions,
                    objective_coefficients,
                    objective_mode,
                    reference_stats,
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
                objective = float(result.fun) if objective_mode == "cost" else -float(result.fun)
                dual_bound = (
                    float(result.mip_dual_bound)
                    if objective_mode == "cost" and result.mip_dual_bound is not None
                    else -float(result.mip_dual_bound)
                    if result.mip_dual_bound is not None
                    else objective
                )
                solution = self._materialize_values(
                    request,
                    container,
                    groups,
                    armors,
                    counts,
                    quality_sums,
                    tuple(problem["quality_lows"]),
                    armor_index,
                    "HEURISTIC_SEED" if objective_mode == "targets" else final_status,
                    objective,
                    dual_bound,
                    None if objective_mode == "targets" else (float(result.mip_gap) if result.mip_gap is not None else None),
                    elapsed,
                )
                if solution is None:
                    break
                if best_solution is None or (
                    objective_mode == "cost" and solution.total_price < best_solution.total_price
                ) or (
                    objective_mode == "targets"
                    and self._target_search_score(solution, objective_targets or {})
                    > self._target_search_score(best_solution, objective_targets or {})
                ):
                    best_solution = solution
                    best_counts = counts
                signature = (counts, quality_sums, armor_index)
                reference_stats = solution.stats
                if signature == previous_signature:
                    break
                previous_signature = signature
            statuses.append("HEURISTIC_SEED" if best_solution and objective_mode == "targets" else final_status)
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
        objective_mode: str,
        reference_stats: dict[str, float],
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
            max_count = capacity if group.max_count is None else min(capacity, int(group.max_count))
            upper_bounds[2 * index] = max_count
            upper_bounds[2 * index + 1] = max_count * group.quality_high
        integrality[armor_offset : armor_offset + len(armors)] = 1
        upper_bounds[armor_offset : armor_offset + len(armors)] = 1
        if exclusion_specs:
            exclusion_offset = armor_offset + len(armors)
            integrality[exclusion_offset:next_variable] = 1
            upper_bounds[exclusion_offset:next_variable] = 1

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
        bullet = stat_forms.get("bullet_resistance", (np.zeros(variable_count), 0.0))
        vitality = stat_forms.get("vitality", (np.zeros(variable_count), 0.0))
        reference_bullet = float(reference_stats.get("bullet_resistance", 0.0))
        reference_vitality = float(reference_stats.get("vitality", 0.0))
        bullet_gradient = (reference_vitality + 100.0) / 100.0
        vitality_gradient = (reference_bullet + 100.0) / 100.0
        metric_forms["effective_durability"] = (
            bullet_gradient * bullet[0] + vitality_gradient * vitality[0],
            bullet_gradient * bullet[1]
            + vitality_gradient * vitality[1]
            + 100.0
            - reference_bullet * reference_vitality / 100.0,
        )
        regeneration = stat_forms.get("health_regeneration", (np.zeros(variable_count), 0.0))
        periodic = stat_forms.get("periodic_healing", (np.zeros(variable_count), 0.0))
        healing = stat_forms.get("healing_effectiveness", (np.zeros(variable_count), 0.0))
        reference_periodic = float(reference_stats.get("periodic_healing", 0.0))
        reference_healing = float(reference_stats.get("healing_effectiveness", 0.0))
        periodic_gradient = 1.0 + reference_healing / 100.0
        healing_gradient = reference_periodic / 100.0
        metric_forms["hp_regen_score"] = (
            0.2 * regeneration[0]
            + periodic_gradient * periodic[0]
            + healing_gradient * healing[0],
            0.2 * regeneration[1]
            + periodic_gradient * periodic[1]
            + healing_gradient * healing[1]
            + 0.5
            - reference_periodic * reference_healing / 100.0,
        )

        if objective_mode == "cost":
            for index, group in enumerate(groups):
                objective[2 * index] = float(group.price)
        else:
            for key, normalized_weight in objective_coefficients.items():
                coefficients, _constant = metric_forms[key]
                objective -= normalized_weight * coefficients
        for requested_key, target in request.targets.items():
            key = self._resolve_metric(requested_key)
            coefficients, constant = metric_forms[key]
            items = [(index, value) for index, value in enumerate(coefficients) if abs(value) > 1e-15]
            internal_target = self._internal_target(requested_key, float(target))
            if self._target_direction(requested_key) == "min":
                add_constraint(items, -np.inf, internal_target - constant)
            else:
                add_constraint(items, internal_target - constant, np.inf)

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

    def _can_use_milp(self, request: OptimizationRequest) -> bool:
        target_metrics = {self._resolve_metric(key) for key in request.targets}
        return not target_metrics.intersection(NONLINEAR_METRICS)

    def _contains_nonlinear_metrics(self, metrics: dict[str, float]) -> bool:
        return bool({self._resolve_metric(key) for key in metrics}.intersection(NONLINEAR_METRICS))

    def _target_weights(self, targets: dict[str, float]) -> dict[str, float]:
        return {
            key: -1.0 if self._target_direction(key) == "min" else 1.0
            for key in targets
        }

    def _linearized_objective_coefficients(
        self,
        metric_weights: dict[str, float],
        reference_stats: dict[str, float],
    ) -> dict[str, float]:
        coefficients: dict[str, float] = {}

        def add(key: str, value: float) -> None:
            coefficients[key] = coefficients.get(key, 0.0) + value

        for requested_key, raw_weight in metric_weights.items():
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
        targets: dict[str, float],
    ) -> dict[str, float]:
        best_stats: dict[str, float] | None = None
        best_score = -math.inf
        container_stats, _infections = split_infections(container.get("stats") or {})
        for armor in armors:
            stats = add_stats(container_stats, armor.get("stats") or {})
            derived = derived_stats(stats)
            score = sum(
                self._target_weights(targets)[requested_key]
                * self._metric_value(stats, derived, self._resolve_metric(requested_key))
                / METRIC_SCALES[self._resolve_metric(requested_key)]
                for requested_key in targets
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
        objective_focus: str = "price",
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
            max_count = capacity if group.max_count is None else min(capacity, int(group.max_count))
            count = model.new_int_var(0, max_count, f"count_{index}")
            quality = model.new_int_var(0, max_count * group.quality_high, f"quality_sum_{index}")
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
            for key in request.targets
        }
        if objective_focus != "price":
            requested_metrics.add(self._resolve_metric(objective_focus))
        metrics = self._metric_expressions(model, stat_vars, requested_metrics)
        for requested_key, target in request.targets.items():
            key = self._resolve_metric(requested_key)
            target_scaled = self._scaled_target(requested_key, float(target))
            if self._target_direction(requested_key) == "min":
                model.add(metrics[key] <= math.floor(target_scaled + 1e-9))
            else:
                model.add(metrics[key] >= math.ceil(target_scaled - 1e-9))
        if objective_focus == "price":
            objective_expression = price_expression
            model.minimize(price_expression)
        else:
            objective_expression = metrics[self._resolve_metric(objective_focus)]
            model.maximize(objective_expression)
        return model, {
            "count_vars": count_vars,
            "quality_vars": quality_vars,
            "quality_lows": quality_lows,
            "armor_vars": armor_vars,
            "metric_expressions": metrics,
            "price_expression": price_expression,
            "objective_expression": objective_expression,
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

    def _apply_solution_hint(
        self,
        model: cp_model.CpModel,
        context: dict[str, Any],
        groups: list[ArtifactGroup],
        armors: list[dict[str, Any]],
        solution: BuildSolution,
    ) -> None:
        counts: dict[str, int] = {}
        quality_sums: dict[str, int] = {}
        for artifact in solution.artifacts:
            group_id = str(artifact["group_id"])
            counts[group_id] = counts.get(group_id, 0) + 1
            quality_sums[group_id] = quality_sums.get(group_id, 0) + int(
                round(float(artifact["quality_percent"]) * 100.0)
            )
        for index, group in enumerate(groups):
            model.add_hint(context["count_vars"][index], counts.get(group.group_id, 0))
            model.add_hint(context["quality_vars"][index], quality_sums.get(group.group_id, 0))
        armor_id = solution.armor["item_id"]
        for index, armor in enumerate(armors):
            model.add_hint(context["armor_vars"][index], int(armor["item_id"] == armor_id))

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
        if not report["valid"] or any(
            float(item["margin"]) + 1e-9 < self.config.infection_safety_margin
            for item in report["by_type"].values()
        ):
            return None
        derived = derived_stats(stats)
        if not self._passes_exact_targets(request.targets, stats, derived):
            return None
        metrics = {
            key: self._requested_metric_value(key, stats, derived)
            for key in request.targets
        }
        build_id = f"{armor['item_id']}:{container['container_id']}:" + "|".join(
            f"{item['group_id']}@{item['quality_percent']:.2f}" for item in artifacts
        )
        return BuildSolution(
            build_id=build_id,
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
            "market_price": group.market_price if group.market_price is not None else group.price,
            "owned_instance_id": group.owned_instance_id,
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
            and QUALITY_ORDER[group.quality_tier] <= QUALITY_ORDER[request.max_quality_tier]
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
        if not request.targets:
            raise ValueError("At least one required stat is required")
        if request.max_quality_tier not in QUALITY_ORDER:
            raise ValueError(f"Unsupported maximum artifact quality: {request.max_quality_tier}")
        for key in request.targets:
            resolved = self._resolve_metric(key)
            if resolved not in METRIC_SCALES:
                raise ValueError(f"Unsupported metric: {key}")
        for key, target in request.targets.items():
            if not math.isfinite(float(target)):
                raise ValueError(f"Required stat must be finite: {key}")

    def _resolve_metric(self, key: str) -> str:
        return METRIC_ALIASES.get(key, key)

    def _passes_exact_targets(
        self,
        targets: dict[str, float],
        stats: dict[str, float],
        derived: dict[str, float],
    ) -> bool:
        for key, target in targets.items():
            value = self._requested_metric_value(key, stats, derived)
            if self._target_direction(key) == "min":
                if value - 1e-7 > float(target):
                    return False
            elif value + 1e-7 < float(target):
                return False
        return True

    def _target_direction(self, key: str) -> str:
        return "min" if self._resolve_metric(key) in MINIMIZE_METRICS else "max"

    def _internal_target(self, key: str, target: float) -> float:
        return target

    def _scaled_target(self, key: str, target: float) -> float:
        return self._internal_target(key, target) * self.config.stat_scale

    def _requested_metric_value(
        self,
        key: str,
        stats: dict[str, float],
        derived: dict[str, float],
    ) -> float:
        resolved = self._resolve_metric(key)
        return self._metric_value(stats, derived, resolved)

    def _target_search_score(
        self,
        solution: BuildSolution,
        targets: dict[str, float],
    ) -> float:
        score = 0.0
        for key in targets:
            direction = -1.0 if self._target_direction(key) == "min" else 1.0
            score += direction * self._requested_metric_value(key, solution.stats, solution.derived) / METRIC_SCALES[
                self._resolve_metric(key)
            ]
        return score

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

    def _focus_sort_key(self, solution: BuildSolution, focus: str) -> tuple[Any, ...]:
        if focus == "speed":
            return (-float(solution.stats.get("movement_speed", 0.0)), solution.total_price, solution.build_id)
        if focus == "durability":
            return (-float(solution.derived.get("effective_durability", 0.0)), solution.total_price, solution.build_id)
        return (solution.total_price, solution.build_id)

    def _select_diverse_portfolio(
        self,
        candidates: list[BuildSolution],
        limit: int,
    ) -> list[BuildSolution]:
        if not candidates or limit <= 0:
            return []

        selected: list[BuildSolution] = []
        seen: set[str] = set()

        def add(solution: BuildSolution, focus: str) -> None:
            if solution.build_id in seen:
                index = next(
                    index
                    for index, current in enumerate(selected)
                    if current.build_id == solution.build_id
                )
                focuses = selected[index].search_focus.split("+")
                if focus not in focuses:
                    selected[index] = replace(
                        selected[index],
                        search_focus="+".join((*focuses, focus)),
                    )
                return
            if len(selected) >= limit:
                return
            selected.append(replace(solution, search_focus=focus))
            seen.add(solution.build_id)

        add(min(candidates, key=lambda item: (item.total_price, item.build_id)), "price")
        add(
            max(
                candidates,
                key=lambda item: (
                    float(item.stats.get("movement_speed", 0.0)),
                    -item.total_price,
                    item.build_id,
                ),
            ),
            "speed",
        )
        add(
            max(
                candidates,
                key=lambda item: (
                    float(item.derived.get("effective_durability", 0.0)),
                    -item.total_price,
                    item.build_id,
                ),
            ),
            "durability",
        )

        speed_values = [float(item.stats.get("movement_speed", 0.0)) for item in candidates]
        durability_values = [float(item.derived.get("effective_durability", 0.0)) for item in candidates]
        speed_range = max(max(speed_values) - min(speed_values), 1.0)
        durability_range = max(max(durability_values) - min(durability_values), 10.0)

        while len(selected) < limit:
            remaining = [item for item in candidates if item.build_id not in seen]
            if not remaining:
                break
            candidate = max(
                remaining,
                key=lambda item: (
                    min(
                        self._solution_distance(item, chosen, speed_range, durability_range)
                        for chosen in selected
                    ),
                    -item.total_price,
                    item.build_id,
                ),
            )
            add(candidate, "diverse")
        return selected

    def _solution_distance(
        self,
        left: BuildSolution,
        right: BuildSolution,
        speed_range: float,
        durability_range: float,
    ) -> float:
        left_artifacts = Counter(str(item["item_id"]) for item in left.artifacts)
        right_artifacts = Counter(str(item["item_id"]) for item in right.artifacts)
        keys = set(left_artifacts) | set(right_artifacts)
        union = sum(max(left_artifacts[key], right_artifacts[key]) for key in keys)
        intersection = sum(min(left_artifacts[key], right_artifacts[key]) for key in keys)
        artifact_distance = 1.0 - intersection / union if union else 0.0
        speed_distance = abs(
            float(left.stats.get("movement_speed", 0.0))
            - float(right.stats.get("movement_speed", 0.0))
        ) / speed_range
        durability_distance = abs(
            float(left.derived.get("effective_durability", 0.0))
            - float(right.derived.get("effective_durability", 0.0))
        ) / durability_range
        stat_distance = min(1.0, (speed_distance + durability_distance) / 2.0)
        equipment_distance = float(
            left.armor["item_id"] != right.armor["item_id"]
            or left.container["container_id"] != right.container["container_id"]
        )
        return 0.6 * artifact_distance + 0.3 * stat_distance + 0.1 * equipment_distance

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
