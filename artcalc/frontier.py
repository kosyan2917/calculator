from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import math
import time
from typing import Protocol

from .optimizer import (
    ArtifactBuildOptimizer,
    BuildSolution,
    FocusedSearchResult,
    OptimizationRequest,
    OptimizationResult,
    OptimizerConfig,
)
from .solver_catalog import SolverCatalog
from .stat_model import QUALITY_ORDER
from .search_session import current_session, search_session


class FocusedBuildSolver(Protocol):
    def solve_focus(
        self,
        request: OptimizationRequest,
        container_id: str,
        focus: str,
        limit: int = 1,
    ) -> FocusedSearchResult: ...


@dataclass(frozen=True)
class FrontierGeneratorConfig:
    sweep_points: int = 3
    solutions_per_point: int = 2
    multi_container_sweep_points: int = 1
    multi_container_solutions_per_point: int = 1
    strong_dominance_speed: float = 1.0
    strong_dominance_durability: float = 15.0
    similar_artifact_distance: float = 0.34
    time_limit: float = 19.0


class FrontierBuildGenerator:
    """Generates a diverse near-Pareto portfolio from focused optimization runs."""

    def __init__(
        self,
        catalog: SolverCatalog,
        config: FrontierGeneratorConfig | None = None,
        solver: FocusedBuildSolver | None = None,
        solver_config: OptimizerConfig | None = None,
    ):
        self.catalog = catalog
        self.config = config or FrontierGeneratorConfig()
        self.solver = solver or ArtifactBuildOptimizer(catalog, solver_config)

    def search(self, request: OptimizationRequest) -> OptimizationResult:
        if current_session.get() is None:
            with search_session(self.config.time_limit):
                return self.search(request)
        self._validate_request(request)
        session = current_session.get()
        started = time.perf_counter()
        containers = self._eligible_containers(request)
        sweep_points = (
            self.config.sweep_points
            if len(containers) == 1
            else self.config.multi_container_sweep_points
        )
        solutions_per_point = (
            self.config.solutions_per_point
            if len(containers) == 1
            else self.config.multi_container_solutions_per_point
        )

        candidates: list[BuildSolution] = []
        statuses: Counter[str] = Counter()
        points_attempted = 0
        for index, container in enumerate(containers):
            if session.remaining() <= 0:
                statuses["LIMIT"] += 1
                break
            global_deadline = session.deadline
            session.deadline = time.perf_counter() + session.remaining() / (len(containers) - index)
            generated, generated_statuses, attempted = self._generate_container_frontier(
                request,
                str(container["container_id"]),
                sweep_points,
                solutions_per_point,
            )
            candidates.extend(generated)
            statuses.update(generated_statuses)
            points_attempted += attempted
            session.deadline = global_deadline

        unique = self._deduplicate(candidates, request.budget is None)
        near_frontier = self._prune_obviously_dominated(unique, request.budget is None)
        selected = self._select_portfolio(near_frontier, request.max_results, request.budget is None)
        return OptimizationResult(
            request=request,
            solutions=tuple(selected),
            diagnostics={
                "engine": "frontier_v3",
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "artifact_groups": self._eligible_group_count(request),
                "armors": len(self._eligible_armors(request)),
                "containers": len(containers),
                "frontier_points_attempted": points_attempted,
                "solver_attempts": sum(statuses.values()),
                "solver_statuses": dict(statuses),
                "candidate_solutions": len(candidates),
                "unique_candidate_solutions": len(unique),
                "near_frontier_solutions": len(near_frontier),
                "returned_solutions": len(selected),
                "milp_calls": session.milp_calls,
                "model_reuses": session.model_reuses,
                "budget_unlimited": request.budget is None,
                "search_complete": False,
                "focused_searches_complete": not any(
                    status in {"LIMIT", "UNKNOWN", "FEASIBLE", "HEURISTIC_SEED", "EXACT_REJECTED"}
                    for status in statuses
                ),
            },
        )

    def _generate_container_frontier(
        self,
        request: OptimizationRequest,
        container_id: str,
        sweep_points: int,
        solutions_per_point: int,
    ) -> tuple[list[BuildSolution], list[str], int]:
        candidates: list[BuildSolution] = []
        statuses: list[str] = []
        attempted = 0

        def solve(point_request: OptimizationRequest, focus: str, label: str) -> None:
            nonlocal attempted
            session = current_session.get()
            if session and session.remaining() <= 0:
                statuses.append("LIMIT")
                return
            attempted += 1
            result = self.solver.solve_focus(
                point_request,
                container_id,
                focus,
                max(1, solutions_per_point),
            )
            candidates.extend(
                replace(solution, search_focus=label)
                for solution in result.solutions
            )
            statuses.extend(result.statuses)

        for focus in (("speed", "durability") if request.budget is None else ("price", "speed", "durability")):
            solve(request, focus, focus)

        anchors = self._deduplicate(candidates, request.budget is None)
        if len(anchors) < 2 or sweep_points <= 0:
            return anchors, statuses, attempted

        speed_values = [self._speed(solution) for solution in anchors]
        durability_values = [self._durability(solution) for solution in anchors]
        speed_low = max(
            min(speed_values),
            float(request.targets.get("speed", -math.inf)),
        )
        durability_low = max(
            min(durability_values),
            float(request.targets.get("durability", -math.inf)),
        )
        speed_high = max(speed_values)
        durability_high = max(durability_values)

        attempted_targets: set[tuple[str, float]] = set()
        for _ in range(2 * sweep_points):
            # Split the largest still-unexplored gap, using all newly found points.
            choices = []
            for metric, measure, low, high, focus in (
                ("speed", self._speed, speed_low, speed_high, "durability"),
                ("durability", self._durability, durability_low, durability_high, "speed"),
            ):
                if high - low < 1e-4:
                    continue
                values = sorted({low, high, *(max(low, min(high, measure(s))) for s in candidates),
                                 *(v for k, v in attempted_targets if k == metric)})
                for left, right in zip(values, values[1:]):
                    midpoint = round((left + right) / 2, 6)
                    if right - left > 1e-4 and (metric, midpoint) not in attempted_targets:
                        choices.append(((right - left) / (high - low), metric, midpoint, focus))
            if not choices:
                break
            _, metric, value, focus = max(choices)
            attempted_targets.add((metric, value))
            solve(self._with_minimum(request, metric, value), focus, "frontier")

        if speed_high - speed_low > 1e-4 and durability_high - durability_low > 1e-3:
            knee_targets = dict(request.targets)
            knee_targets["speed"] = max(
                float(knee_targets.get("speed", -math.inf)),
                speed_low + (speed_high - speed_low) * 0.35,
            )
            knee_targets["durability"] = max(
                float(knee_targets.get("durability", -math.inf)),
                durability_low + (durability_high - durability_low) * 0.35,
            )
            solve(replace(request, targets=knee_targets),
                  "speed" if request.budget is None else "price", "balanced")

        return self._deduplicate(candidates, request.budget is None), statuses, attempted

    def _with_minimum(
        self,
        request: OptimizationRequest,
        metric: str,
        value: float,
    ) -> OptimizationRequest:
        targets = dict(request.targets)
        targets[metric] = max(float(targets.get(metric, -math.inf)), value)
        return replace(request, targets=targets)

    def _prune_obviously_dominated(
        self,
        candidates: list[BuildSolution],
        ignore_price: bool = False,
    ) -> list[BuildSolution]:
        def dominates(left: BuildSolution, right: BuildSolution) -> bool:
            if not ignore_price and left.total_price > right.total_price:
                return False
            speed_gain = self._speed(left) - self._speed(right)
            durability_gain = self._durability(left) - self._durability(right)
            no_worse = speed_gain >= -1e-4 and durability_gain >= -1e-3
            strictly_better = (
                (not ignore_price and left.total_price < right.total_price)
                or speed_gain > 1e-4
                or durability_gain > 1e-3
            )
            if not no_worse or not strictly_better:
                return False
            composition_is_similar = (
                self._artifact_distance(left, right)
                <= self.config.similar_artifact_distance
            )
            strongly_better = (
                speed_gain >= self.config.strong_dominance_speed
                or durability_gain >= self.config.strong_dominance_durability
            )
            return composition_is_similar or strongly_better

        return [
            candidate
            for candidate in candidates
            if not any(
                other.build_id != candidate.build_id and dominates(other, candidate)
                for other in candidates
            )
        ]

    def _select_portfolio(
        self,
        candidates: list[BuildSolution],
        limit: int,
        ignore_price: bool = False,
    ) -> list[BuildSolution]:
        if not candidates or limit <= 0:
            return []

        selected: list[BuildSolution] = []
        seen: set[str] = set()

        def add(solution: BuildSolution, label: str) -> None:
            if solution.build_id in seen:
                index = next(
                    index
                    for index, current in enumerate(selected)
                    if current.build_id == solution.build_id
                )
                labels = selected[index].search_focus.split("+")
                if label not in labels:
                    selected[index] = replace(
                        selected[index],
                        search_focus="+".join((*labels, label)),
                    )
                return
            if len(selected) < limit:
                selected.append(replace(solution, search_focus=label))
                seen.add(solution.build_id)

        price = lambda item: 0 if ignore_price else item.total_price
        if not ignore_price:
            add(min(candidates, key=lambda item: (price(item), item.build_id)), "price")
        add(
            max(candidates, key=lambda item: (self._speed(item), self._durability(item), -price(item), item.build_id)),
            "speed",
        )
        add(
            max(
                candidates,
                key=lambda item: (self._durability(item), self._speed(item), -price(item), item.build_id),
            ),
            "durability",
        )

        speed_values = [self._speed(item) for item in candidates]
        durability_values = [self._durability(item) for item in candidates]
        price_values = [price(item) for item in candidates]
        speed_range = max(max(speed_values) - min(speed_values), 1.0)
        durability_range = max(max(durability_values) - min(durability_values), 10.0)
        price_range = max(max(price_values) - min(price_values), 1_000_000)
        balanced = max(
            candidates,
            key=lambda item: (
                (self._speed(item) - min(speed_values)) / speed_range
                + (self._durability(item) - min(durability_values)) / durability_range
                - 0.2 * (price(item) - min(price_values)) / price_range,
                -price(item),
            ),
        )
        add(balanced, "balanced")

        while len(selected) < limit:
            remaining = [item for item in candidates if item.build_id not in seen]
            if not remaining:
                break
            alternative = max(
                remaining,
                key=lambda item: (
                    min(
                        self._portfolio_distance(
                            item,
                            chosen,
                            speed_range,
                            durability_range,
                            price_range,
                            ignore_price,
                        )
                        for chosen in selected
                    ),
                    -price(item),
                ),
            )
            add(alternative, "alternative")
        return selected

    def _portfolio_distance(
        self,
        left: BuildSolution,
        right: BuildSolution,
        speed_range: float,
        durability_range: float,
        price_range: float,
        ignore_price: bool = False,
    ) -> float:
        stat_distance = (
            0.25 * abs(self._speed(left) - self._speed(right)) / speed_range
            + 0.25
            * abs(self._durability(left) - self._durability(right))
            / durability_range
            + (0 if ignore_price else 0.1 * abs(left.total_price - right.total_price) / price_range)
        )
        equipment_distance = 0.0
        if left.armor["item_id"] != right.armor["item_id"]:
            equipment_distance += 0.03
        if left.container["container_id"] != right.container["container_id"]:
            equipment_distance += 0.07
        return stat_distance + 0.3 * self._artifact_distance(left, right) + equipment_distance

    def _artifact_distance(self, left: BuildSolution, right: BuildSolution) -> float:
        left_counts = Counter(str(item["item_id"]) for item in left.artifacts)
        right_counts = Counter(str(item["item_id"]) for item in right.artifacts)
        keys = set(left_counts) | set(right_counts)
        union = sum(max(left_counts[key], right_counts[key]) for key in keys)
        if union == 0:
            return 0.0
        intersection = sum(min(left_counts[key], right_counts[key]) for key in keys)
        return 1.0 - intersection / union

    def _deduplicate(self, candidates: list[BuildSolution], ignore_price: bool = False) -> list[BuildSolution]:
        unique: dict[str, BuildSolution] = {}
        for candidate in candidates:
            existing = unique.get(candidate.build_id)
            if existing is None or candidate.total_price < existing.total_price:
                unique[candidate.build_id] = candidate
        distinct = []
        for candidate in unique.values():
            # Keep genuine tradeoffs with the same artifacts, but collapse quality noise.
            if any(candidate.armor["item_id"] == other.armor["item_id"]
                   and candidate.container["container_id"] == other.container["container_id"]
                   and self._artifact_distance(candidate, other) == 0
                   and abs(self._speed(candidate) - self._speed(other)) < 0.1
                   and abs(self._durability(candidate) - self._durability(other)) < 1.0
                   and (ignore_price or abs(candidate.total_price - other.total_price) < 100_000)
                   for other in distinct):
                continue
            distinct.append(candidate)
        return distinct

    def _eligible_containers(self, request: OptimizationRequest) -> list[dict]:
        selected = set(request.container_ids)
        excluded = set(request.excluded_container_ids)
        containers = [
            container
            for container in self.catalog.containers
            if (not selected or container["container_id"] in selected)
            and container["container_id"] not in excluded
        ]
        if not containers:
            raise ValueError("No containers match the request")
        return containers

    def _eligible_armors(self, request: OptimizationRequest) -> list[dict]:
        selected = set(request.armor_ids)
        excluded = set(request.excluded_armor_ids)
        armors = [
            armor
            for armor in self.catalog.armors
            if (
                not selected
                or armor["item_id"] in selected
                or armor["base_id"] in selected
            )
            and armor["item_id"] not in excluded
            and armor["base_id"] not in excluded
        ]
        if not armors:
            raise ValueError("No armors match the request")
        return armors

    def _eligible_group_count(self, request: OptimizationRequest) -> int:
        selected_tiers = set(request.allowed_quality_tiers)
        excluded = set(request.excluded_artifact_ids)
        return sum(
            group.item_id not in excluded
            and (not selected_tiers or group.quality_tier in selected_tiers)
            and QUALITY_ORDER[group.quality_tier] <= QUALITY_ORDER[request.max_quality_tier]
            and (request.budget is None or group.price <= request.budget)
            and (request.budget is None or group.price_estimate.get("available", True))
            for group in self.catalog.artifact_groups
        )

    def _validate_request(self, request: OptimizationRequest) -> None:
        if request.budget is not None and request.budget <= 0:
            raise ValueError("budget must be positive")
        if request.max_results <= 0:
            raise ValueError("max_results must be positive")
        if not request.targets and request.budget is not None:
            raise ValueError("At least one required stat is required")
        if request.max_quality_tier not in QUALITY_ORDER:
            raise ValueError(f"Unsupported maximum artifact quality: {request.max_quality_tier}")

    def _speed(self, solution: BuildSolution) -> float:
        return float(solution.stats.get("movement_speed", 0.0))

    def _durability(self, solution: BuildSolution) -> float:
        return float(solution.derived.get("effective_durability", 100.0))
