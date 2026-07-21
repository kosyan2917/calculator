from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .optimizer import BuildSolution
from .solver_catalog import SolverCatalog
from .stat_model import MechanicsConfig, add_stats, infection_report, split_infections


STRATEGY_CURRENT = "best_now"
STRATEGY_BALANCED = "balanced"
STRATEGY_UPGRADE = "upgrade"
STRATEGIES = (STRATEGY_CURRENT, STRATEGY_BALANCED, STRATEGY_UPGRADE)

STRATEGY_LOSS_LIMITS = {
    STRATEGY_CURRENT: 0.0,
    STRATEGY_BALANCED: 0.03,
    STRATEGY_UPGRADE: 0.07,
}


@dataclass(frozen=True)
class RankedBuild:
    solution: BuildSolution
    upgrade_potential: float
    current_stat_loss_percent: float
    strategy: str

    def to_dict(self) -> dict[str, Any]:
        result = self.solution.to_dict()
        result.update(
            {
                "upgrade_potential": self.upgrade_potential,
                "current_stat_loss_percent": self.current_stat_loss_percent,
                "selection_strategy": self.strategy,
            }
        )
        return result


class BuildStrategyRanker:
    """Ranks already valid builds by current strength and reuse potential."""

    def __init__(self, catalog: SolverCatalog):
        self.catalog = catalog
        self.mechanics = MechanicsConfig(**catalog.mechanics)

    def rank(
        self,
        solutions: Sequence[BuildSolution],
        strategy: str = STRATEGY_CURRENT,
        max_results: int = 10,
        excluded_container_ids: Iterable[str] = (),
    ) -> tuple[RankedBuild, ...]:
        if strategy not in STRATEGIES:
            raise ValueError(f"Unknown selection strategy: {strategy}")
        if max_results <= 0 or not solutions:
            return ()

        strongest = max(float(solution.preference_score) for solution in solutions)
        denominator = max(abs(strongest), 1.0)
        excluded = set(excluded_container_ids)
        containers = tuple(
            container
            for container in self.catalog.containers
            if container["container_id"] not in excluded
        )
        ranked = [
            RankedBuild(
                solution=solution,
                upgrade_potential=self._upgrade_potential(solution, containers),
                current_stat_loss_percent=round(
                    max(0.0, strongest - float(solution.preference_score)) / denominator * 100.0,
                    2,
                ),
                strategy=strategy,
            )
            for solution in solutions
        ]

        if strategy == STRATEGY_CURRENT:
            ranked.sort(key=self._current_key)
            return tuple(ranked[:max_results])

        loss_limit = STRATEGY_LOSS_LIMITS[strategy] * 100.0
        within_limit = [item for item in ranked if item.current_stat_loss_percent <= loss_limit + 1e-9]
        candidates = within_limit or ranked
        if strategy == STRATEGY_BALANCED:
            candidates.sort(
                key=lambda item: (
                    -(0.65 * self._strength_fraction(item, strongest, denominator)
                      + 0.35 * item.upgrade_potential / 100.0),
                    item.current_stat_loss_percent,
                    item.solution.total_price,
                    item.solution.build_id,
                )
            )
        else:
            candidates.sort(
                key=lambda item: (
                    -item.upgrade_potential,
                    item.current_stat_loss_percent,
                    item.solution.total_price,
                    item.solution.build_id,
                )
            )
        return tuple(candidates[:max_results])

    def _current_key(self, item: RankedBuild) -> tuple[float, int, str]:
        return (-float(item.solution.preference_score), item.solution.total_price, item.solution.build_id)

    def _strength_fraction(self, item: RankedBuild, strongest: float, denominator: float) -> float:
        return 1.0 - max(0.0, strongest - float(item.solution.preference_score)) / denominator

    def _upgrade_potential(
        self,
        solution: BuildSolution,
        containers: tuple[dict[str, Any], ...],
    ) -> float:
        role_score = self._artifact_role_score(solution.artifacts)
        container_score = self._compatible_container_score(solution.artifacts, containers)
        return round(100.0 * (0.55 * role_score + 0.45 * container_score), 1)

    def _artifact_role_score(self, artifacts: tuple[dict[str, Any], ...]) -> float:
        if not artifacts:
            return 0.0
        role_counts = [self._artifact_roles(artifact) for artifact in artifacts]
        per_artifact = sum(min(len(roles), 3) / 3.0 for roles in role_counts) / len(role_counts)
        covered = len(set().union(*role_counts)) / 5.0
        return min(1.0, 0.7 * per_artifact + 0.3 * covered)

    def _artifact_roles(self, artifact: dict[str, Any]) -> set[str]:
        stats = artifact.get("stats") or {}
        roles: set[str] = set()
        if float(stats.get("movement_speed", 0.0)) > 0 or float(stats.get("sprint_speed", 0.0)) > 0:
            roles.add("speed")
        if float(stats.get("bullet_resistance", 0.0)) > 0 or float(stats.get("vitality", 0.0)) > 0:
            roles.add("durability")
        if any(float(stats.get(key, 0.0)) > 0 for key in ("health_regeneration", "periodic_healing", "healing_effectiveness")):
            roles.add("regen")
        if float(stats.get("stamina", 0.0)) > 0 or float(stats.get("stamina_regeneration", 0.0)) > 0:
            roles.add("endurance")
        if float(stats.get("carry_weight", 0.0)) > 0:
            roles.add("weight")
        return roles

    def _compatible_container_score(
        self,
        artifacts: tuple[dict[str, Any], ...],
        containers: tuple[dict[str, Any], ...],
    ) -> float:
        candidates = tuple(container for container in containers if int(container["capacity"]) >= len(artifacts))
        if not candidates:
            return 0.0
        artifact_infections = add_stats(*(artifact.get("infections") or {} for artifact in artifacts))
        compatible = 0
        for container in candidates:
            _, container_infections = split_infections(container.get("stats") or {})
            report = infection_report(
                artifact_infections,
                container_infections,
                float(container["inner_protection"]),
                self.mechanics,
            )
            if report["valid"]:
                compatible += 1
        return compatible / len(candidates)
