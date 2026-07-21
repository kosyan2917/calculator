from __future__ import annotations

from dataclasses import asdict, dataclass
import time
from typing import Any

from .optimizer import ArtifactBuildOptimizer, OptimizationRequest, OptimizerConfig
from .solver_catalog import ArtifactGroup, SolverCatalog


@dataclass(frozen=True)
class UpgradePlannerConfig:
    extra_budgets: tuple[int, ...] = (2_500_000, 5_000_000, 10_000_000)
    max_plans_per_budget: int = 2
    reuse_reward_per_artifact: float = 0.02
    time_limit_per_solve: float = 0.2
    nonlinear_iterations: int = 2


@dataclass(frozen=True)
class UpgradePlanningRequest:
    current_build: dict[str, Any]
    preferences: dict[str, float]
    preference_caps: dict[str, float]
    targets: dict[str, float]
    extra_budgets: tuple[int, ...] = ()
    excluded_artifact_ids: tuple[str, ...] = ()
    excluded_container_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class UpgradePlan:
    extra_budget: int
    purchase_cost: int
    resale_credit: int
    estimated_net_cost: int
    preference_gain: float
    kept_count: int
    current_count: int
    kept_value: int
    container_changed: bool
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
                max_solutions_per_container=1,
                num_search_workers=1,
                nonlinear_iterations=self.config.nonlinear_iterations,
            ),
        )
        current_score = optimizer._preference_score(
            request.preferences,
            request.preference_caps,
            dict(current.get("stats") or {}),
            dict(current.get("derived") or {}),
        )
        owned_by_id = {
            group.owned_instance_id: artifact
            for group, artifact in zip(owned_groups, current_artifacts, strict=True)
            if group.owned_instance_id is not None
        }
        rewards = {
            group.group_id: self.config.reuse_reward_per_artifact
            for group in owned_groups
        }
        budgets = request.extra_budgets or self.config.extra_budgets
        all_plans: list[UpgradePlan] = []
        searches: list[dict[str, Any]] = []
        armor_id = str((current.get("armor") or {}).get("item_id") or "")
        current_container_id = str((current.get("container") or {}).get("container_id") or "")
        upgrade_container_ids = self._upgrade_container_ids(
            current_container_id,
            len(current_artifacts),
            request.excluded_container_ids,
        )

        for extra_budget in sorted(set(int(value) for value in budgets if int(value) > 0)):
            result = optimizer.search(
                OptimizationRequest(
                    budget=extra_budget,
                    preferences=request.preferences,
                    preference_caps=request.preference_caps,
                    targets=request.targets,
                    armor_ids=(armor_id,),
                    container_ids=upgrade_container_ids,
                    excluded_artifact_ids=request.excluded_artifact_ids,
                    excluded_container_ids=request.excluded_container_ids,
                    min_quality_percent=self.catalog.min_quality_percent,
                    max_results=max(10, self.config.max_plans_per_budget * 4),
                    group_rewards=rewards,
                )
            )
            candidates = [
                self._make_plan(
                    solution.to_dict(),
                    current_artifacts,
                    owned_by_id,
                    current_container_id,
                    extra_budget,
                    current_score,
                )
                for solution in result.solutions
            ]
            candidates = [plan for plan in candidates if plan.preference_gain > 1e-7]
            selected = self._select_plans(candidates, self.config.max_plans_per_budget)
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
        return UpgradePlanningResult(
            plans=tuple(unique_plans),
            diagnostics={
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "owned_artifacts": len(current_artifacts),
                "eligible_containers": len(upgrade_container_ids),
                "duplicate_plans_removed": len(all_plans) - len(unique_plans),
                "searches": searches,
            },
        )

    def _upgrade_container_ids(
        self,
        current_container_id: str,
        current_artifact_count: int,
        excluded_container_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        excluded = set(excluded_container_ids)
        ids = tuple(
            str(container["container_id"])
            for container in self.catalog.containers
            if int(container["capacity"]) >= current_artifact_count
            and container["container_id"] not in excluded
        )
        if not ids:
            raise ValueError("No containers with enough slots are available for an upgrade")
        if current_container_id and current_container_id not in ids and current_container_id not in excluded:
            raise ValueError("Current container is missing from the upgrade catalog")
        return ids

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
        current_score: float,
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
        return UpgradePlan(
            extra_budget=extra_budget,
            purchase_cost=purchase_cost,
            resale_credit=resale_credit,
            estimated_net_cost=max(0, purchase_cost - resale_credit),
            preference_gain=round(float(result_build["preference_score"]) - current_score, 8),
            kept_count=len(kept),
            current_count=len(current_artifacts),
            kept_value=kept_value,
            container_changed=str(result_build["container"]["container_id"]) != current_container_id,
            removed_artifacts=removed,
            added_artifacts=added,
            result_build=result_build,
        )

    def _select_plans(self, plans: list[UpgradePlan], limit: int) -> list[UpgradePlan]:
        if not plans or limit <= 0:
            return []
        ordered = sorted(
            plans,
            key=lambda plan: (
                -plan.preference_gain,
                -plan.kept_count,
                plan.purchase_cost,
                plan.result_build["container"]["container_id"],
            ),
        )
        selected = [ordered[0]]
        if limit > 1:
            opposite = next(
                (plan for plan in ordered[1:] if plan.container_changed != selected[0].container_changed),
                None,
            )
            if opposite is not None:
                selected.append(opposite)
        for plan in ordered[1:]:
            if len(selected) >= limit:
                break
            if plan not in selected:
                selected.append(plan)
        return selected

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
