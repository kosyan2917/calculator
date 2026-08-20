from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import time
from typing import Any

from .optimizer import ArtifactBuildOptimizer, OptimizationRequest, OptimizerConfig
from .solver_catalog import ArtifactGroup, SolverCatalog
from .upgrade_potential import BuildUpgradePotentialAnalyzer


@dataclass(frozen=True)
class UpgradePlannerConfig:
    extra_budgets: tuple[int, ...] = (2_500_000, 5_000_000, 10_000_000)
    max_plans_per_budget: int = 2
    time_limit_per_solve: float = 0.2
    nonlinear_iterations: int = 2
    max_container_candidates: int = 4
    max_speed_loss: float = 1.0
    max_durability_loss: float = 10.0


@dataclass(frozen=True)
class UpgradePlanningRequest:
    current_build: dict[str, Any]
    targets: dict[str, float]
    exclude_legendary_artifacts: bool = True
    extra_budgets: tuple[int, ...] = ()
    excluded_artifact_ids: tuple[str, ...] = ()
    excluded_container_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class UpgradePlan:
    extra_budget: int
    purchase_cost: int
    resale_credit: int
    estimated_net_cost: int
    kept_count: int
    current_count: int
    kept_value: int
    container_changed: bool
    potential_gain: float
    upgrade_potential: dict[str, Any]
    removed_artifacts: tuple[dict[str, Any], ...]
    added_artifacts: tuple[dict[str, Any], ...]
    result_build: dict[str, Any]


@dataclass(frozen=True)
class UpgradePlanningResult:
    plans: tuple[UpgradePlan, ...]
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plans": [asdict(plan) for plan in self.plans],
            "diagnostics": self.diagnostics,
        }


class UpgradePlanner:
    """Finds concrete artifact purchases while treating owned instances as free."""

    def __init__(self, catalog: SolverCatalog, config: UpgradePlannerConfig | None = None):
        self.catalog = catalog
        self.config = config or UpgradePlannerConfig()
        self.groups_by_id = {group.group_id: group for group in catalog.artifact_groups}
        self.potential_analyzer = BuildUpgradePotentialAnalyzer(catalog)

    def plan(self, request: UpgradePlanningRequest) -> UpgradePlanningResult:
        started = time.perf_counter()
        current = request.current_build
        current_artifacts = tuple(current.get("artifacts") or ())
        if not current_artifacts:
            raise ValueError("Current build has no artifacts")

        owned_groups = self._owned_groups(current_artifacts)
        planning_catalog = SolverCatalog(
            generated_at=self.catalog.generated_at,
            artifact_upgrade_level=self.catalog.artifact_upgrade_level,
            artifact_price_upgrade_level=self.catalog.artifact_price_upgrade_level,
            min_quality_percent=self.catalog.min_quality_percent,
            mechanics=self.catalog.mechanics,
            artifact_groups=tuple(self.catalog.artifact_groups) + owned_groups,
            containers=self.catalog.containers,
            armors=self.catalog.armors,
        )
        optimizer = ArtifactBuildOptimizer(
            planning_catalog,
            OptimizerConfig(
                time_limit_per_solve=self.config.time_limit_per_solve,
                max_solutions_per_container=3,
                num_search_workers=1,
                nonlinear_iterations=self.config.nonlinear_iterations,
            ),
        )
        owned_by_id = {
            group.owned_instance_id: artifact
            for group, artifact in zip(owned_groups, current_artifacts, strict=True)
            if group.owned_instance_id is not None
        }
        budgets = request.extra_budgets or self.config.extra_budgets
        all_plans: list[UpgradePlan] = []
        searches: list[dict[str, Any]] = []
        armor_id = str((current.get("armor") or {}).get("item_id") or "")
        current_container_id = str((current.get("container") or {}).get("container_id") or "")
        current_potential = self.potential_analyzer.analyze(
            current,
            request.excluded_container_ids,
        )
        upgrade_container_ids = self._upgrade_container_ids(
            current_container_id,
            current_artifacts,
            request.excluded_container_ids,
        )
        upgrade_targets = self._upgrade_targets(current, request.targets)

        for extra_budget in sorted(set(int(value) for value in budgets if int(value) > 0)):
            result = optimizer.search(
                OptimizationRequest(
                    budget=extra_budget,
                    targets=upgrade_targets,
                    exclude_legendary_artifacts=request.exclude_legendary_artifacts,
                    armor_ids=(armor_id,),
                    container_ids=upgrade_container_ids,
                    excluded_artifact_ids=request.excluded_artifact_ids,
                    excluded_container_ids=request.excluded_container_ids,
                    min_quality_percent=self.catalog.min_quality_percent,
                    max_results=max(10, self.config.max_plans_per_budget * 4),
                )
            )
            candidates = [
                self._make_plan(
                    solution.to_dict(),
                    current_artifacts,
                    owned_by_id,
                    current_container_id,
                    extra_budget,
                    current_potential.score,
                    request.excluded_container_ids,
                )
                for solution in result.solutions
            ]
            candidates = [
                plan
                for plan in candidates
                if (plan.added_artifacts or plan.removed_artifacts or plan.container_changed)
                and self._is_actual_upgrade(plan.result_build, current)
            ]
            selected = self._select_plans(candidates, self.config.max_plans_per_budget, current)
            all_plans.extend(selected)
            searches.append(
                {
                    "extra_budget": extra_budget,
                    "candidates": len(candidates),
                    "selected": len(selected),
                    "elapsed_seconds": result.diagnostics["elapsed_seconds"],
                }
            )

        unique_plans = self._deduplicate_plans(all_plans)
        useful_plans = self._prune_dominated_plans(unique_plans)
        return UpgradePlanningResult(
            plans=tuple(useful_plans),
            diagnostics={
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "owned_artifacts": len(current_artifacts),
                "current_potential": current_potential.to_dict(),
                "eligible_containers": len(upgrade_container_ids),
                "preserved_primary_targets": upgrade_targets,
                "duplicate_plans_removed": len(all_plans) - len(unique_plans),
                "dominated_plans_removed": len(unique_plans) - len(useful_plans),
                "searches": searches,
            },
        )

    def _upgrade_container_ids(
        self,
        current_container_id: str,
        current_artifacts: tuple[dict[str, Any], ...],
        excluded_container_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        excluded = set(excluded_container_ids)
        candidates = tuple(
            container
            for container in self.catalog.containers
            if int(container["capacity"]) >= len(current_artifacts)
            and container["container_id"] not in excluded
            and self.potential_analyzer.artifacts_fit(current_artifacts, container)
        )
        if not candidates:
            raise ValueError("No containers with enough slots are available for an upgrade")
        ids = {str(container["container_id"]) for container in candidates}
        if current_container_id and current_container_id not in ids and current_container_id not in excluded:
            raise ValueError("Current container is missing from the upgrade catalog")

        current = next(
            (container for container in candidates if container["container_id"] == current_container_id),
            None,
        )
        alternatives = sorted(
            (container for container in candidates if container["container_id"] != current_container_id),
            key=lambda container: (
                -int(container["capacity"]),
                -float(container["inner_protection"]),
                -float(container["effectiveness"]),
                str(container["container_id"]),
            ),
        )
        selected = ([current] if current is not None else []) + alternatives
        return tuple(
            str(container["container_id"])
            for container in selected[: max(1, self.config.max_container_candidates)]
        )

    def _upgrade_targets(
        self,
        current_build: dict[str, Any],
        requested_targets: dict[str, float],
    ) -> dict[str, float]:
        targets = dict(requested_targets)
        current_durability, current_speed = self._primary_values(current_build)
        targets["durability"] = max(
            float(targets.get("durability", -math.inf)),
            current_durability - self.config.max_durability_loss,
        )
        targets["speed"] = max(
            float(targets.get("speed", -math.inf)),
            current_speed - self.config.max_speed_loss,
        )
        return targets

    def _deduplicate_plans(self, plans: list[UpgradePlan]) -> list[UpgradePlan]:
        selected: list[UpgradePlan] = []
        seen: set[str] = set()
        for plan in plans:
            build_id = str(plan.result_build["build_id"])
            if build_id in seen:
                continue
            seen.add(build_id)
            selected.append(plan)
        return selected

    def _prune_dominated_plans(self, plans: list[UpgradePlan]) -> list[UpgradePlan]:
        def dominates(left: UpgradePlan, right: UpgradePlan) -> bool:
            if (
                left.result_build["container"]["container_id"]
                != right.result_build["container"]["container_id"]
            ):
                return False
            left_durability, left_speed = self._primary_values(left.result_build)
            right_durability, right_speed = self._primary_values(right.result_build)
            no_worse = (
                left_durability + 1e-3 >= right_durability
                and left_speed + 1e-4 >= right_speed
                and left.kept_count >= right.kept_count
                and left.purchase_cost <= right.purchase_cost
            )
            strictly_better = (
                left_durability > right_durability + 1e-3
                or left_speed > right_speed + 1e-4
                or left.kept_count > right.kept_count
                or left.purchase_cost < right.purchase_cost
            )
            return no_worse and strictly_better

        return [
            plan
            for plan in plans
            if not any(other is not plan and dominates(other, plan) for other in plans)
        ]

    def _owned_groups(self, artifacts: tuple[dict[str, Any], ...]) -> tuple[ArtifactGroup, ...]:
        groups: list[ArtifactGroup] = []
        for index, artifact in enumerate(artifacts):
            source = self.groups_by_id.get(str(artifact.get("group_id") or ""))
            if source is None:
                raise ValueError(f"Unknown artifact group: {artifact.get('group_id')}")
            quality = int(round(float(artifact["quality_percent"]) * 100.0))
            stats = self._interpolate(source.stats_low, source.stats_high, source, quality)
            infections = self._interpolate(source.infections_low, source.infections_high, source, quality)
            groups.append(
                ArtifactGroup(
                    group_id=f"owned:{index}:{source.group_id}",
                    item_id=source.item_id,
                    name=source.name,
                    quality_tier=source.quality_tier,
                    quality_low=quality,
                    quality_high=quality,
                    price=0,
                    stats_low=stats,
                    stats_high=stats,
                    infections_low=infections,
                    infections_high=infections,
                    price_basis=source.price_basis,
                    liquidity_score=source.liquidity_score,
                    confidence_score=source.confidence_score,
                    max_count=1,
                    owned_instance_id=f"owned-{index}",
                    market_price=int(artifact.get("market_price") or artifact.get("price") or source.price),
                )
            )
        return tuple(groups)

    def _make_plan(
        self,
        result_build: dict[str, Any],
        current_artifacts: tuple[dict[str, Any], ...],
        owned_by_id: dict[str, dict[str, Any]],
        current_container_id: str,
        extra_budget: int,
        current_potential_score: float,
        excluded_container_ids: tuple[str, ...],
    ) -> UpgradePlan:
        result_artifacts = tuple(result_build.get("artifacts") or ())
        kept_ids = {
            str(artifact["owned_instance_id"])
            for artifact in result_artifacts
            if artifact.get("owned_instance_id") is not None
        }
        kept = tuple(owned_by_id[owned_id] for owned_id in sorted(kept_ids))
        removed = tuple(
            artifact
            for index, artifact in enumerate(current_artifacts)
            if f"owned-{index}" not in kept_ids
        )
        added = tuple(
            artifact
            for artifact in result_artifacts
            if artifact.get("owned_instance_id") is None
        )
        purchase_cost = sum(int(artifact.get("price") or 0) for artifact in added)
        resale_credit = sum(int(artifact.get("market_price") or artifact.get("price") or 0) for artifact in removed)
        kept_value = sum(int(artifact.get("market_price") or artifact.get("price") or 0) for artifact in kept)
        result_potential = self.potential_analyzer.analyze(
            result_build,
            excluded_container_ids,
        )
        return UpgradePlan(
            extra_budget=extra_budget,
            purchase_cost=purchase_cost,
            resale_credit=resale_credit,
            estimated_net_cost=max(0, purchase_cost - resale_credit),
            kept_count=len(kept),
            current_count=len(current_artifacts),
            kept_value=kept_value,
            container_changed=str(result_build["container"]["container_id"]) != current_container_id,
            potential_gain=round(result_potential.score - current_potential_score, 1),
            upgrade_potential=result_potential.to_dict(),
            removed_artifacts=removed,
            added_artifacts=added,
            result_build=result_build,
        )

    def _select_plans(
        self,
        plans: list[UpgradePlan],
        limit: int,
        current_build: dict[str, Any],
    ) -> list[UpgradePlan]:
        if not plans or limit <= 0:
            return []

        current_durability, current_speed = self._primary_values(current_build)

        def gains(plan: UpgradePlan) -> tuple[float, float]:
            durability, speed = self._primary_values(plan.result_build)
            return durability - current_durability, speed - current_speed

        continuity = max(
            plans,
            key=lambda plan: (
                plan.kept_count,
                gains(plan)[1],
                gains(plan)[0] / 50.0,
                -plan.purchase_cost,
            ),
        )
        speed_extreme = max(
            plans,
            key=lambda plan: (gains(plan)[1], plan.kept_count, gains(plan)[0], -plan.purchase_cost),
        )
        durability_extreme = max(
            plans,
            key=lambda plan: (gains(plan)[0], plan.kept_count, gains(plan)[1], -plan.purchase_cost),
        )

        selected = [continuity]
        anchors = (speed_extreme, durability_extreme)
        for plan in sorted(
            anchors,
            key=lambda plan: (
                abs(gains(plan)[1] - gains(continuity)[1])
                + abs(gains(plan)[0] - gains(continuity)[0]) / 50.0,
                plan.kept_count,
            ),
            reverse=True,
        ):
            if len(selected) >= limit:
                break
            if plan not in selected:
                selected.append(plan)

        for plan in sorted(
            plans,
            key=lambda plan: (-plan.kept_count, plan.purchase_cost, -plan.potential_gain),
        ):
            if len(selected) >= limit:
                break
            if plan not in selected:
                selected.append(plan)
        return selected

    def _is_actual_upgrade(
        self,
        result_build: dict[str, Any],
        current_build: dict[str, Any],
    ) -> bool:
        result_durability, result_speed = self._primary_values(result_build)
        current_durability, current_speed = self._primary_values(current_build)
        return (
            result_durability > current_durability + 1e-3
            or result_speed > current_speed + 1e-4
        )

    def _primary_values(self, build: dict[str, Any]) -> tuple[float, float]:
        derived = build.get("derived") or {}
        stats = build.get("stats") or {}
        return (
            float(derived.get("effective_durability", 100.0)),
            float(stats.get("movement_speed", 0.0)),
        )

    def _interpolate(
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
