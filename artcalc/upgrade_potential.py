from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .solver_catalog import SolverCatalog
from .stat_model import MechanicsConfig, add_stats, infection_report, split_infections


ROLE_STATS = {
    "speed": ("movement_speed", "sprint_speed"),
    "durability": ("bullet_resistance", "vitality"),
    "regeneration": ("health_regeneration", "periodic_healing", "healing_effectiveness"),
    "endurance": ("stamina", "stamina_regeneration"),
    "weight": ("carry_weight",),
    "support": ("bleeding_resistance", "burn_reaction", "tear_reaction"),
}


@dataclass(frozen=True)
class ArtifactReuse:
    item_id: str
    name: str
    roles: tuple[str, ...]
    compatible_containers: int
    container_candidates: int
    score: float


@dataclass(frozen=True)
class UpgradePotential:
    score: float
    artifact_reuse_score: float
    container_upgrade_score: float
    reusable_roles: tuple[str, ...]
    compatible_container_count: int
    larger_container_count: int
    best_container_upgrade: dict[str, Any] | None
    artifacts: tuple[ArtifactReuse, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BuildUpgradePotentialAnalyzer:
    """Estimates whether artifacts can move into other builds and containers."""

    def __init__(self, catalog: SolverCatalog):
        self.catalog = catalog
        self.mechanics = MechanicsConfig(**catalog.mechanics)

    def analyze(
        self,
        build: Any,
        excluded_container_ids: Iterable[str] = (),
    ) -> UpgradePotential:
        payload = build.to_dict() if hasattr(build, "to_dict") else dict(build)
        artifacts = tuple(payload.get("artifacts") or ())
        current_container = payload.get("container") or {}
        current_container_id = str(current_container.get("container_id") or "")
        current_capacity = int(current_container.get("capacity") or len(artifacts))
        excluded = set(excluded_container_ids)
        containers = tuple(
            container
            for container in self.catalog.containers
            if container["container_id"] not in excluded
            and int(container["capacity"]) >= len(artifacts)
        )

        artifact_reuse = tuple(self._artifact_reuse(artifact, containers) for artifact in artifacts)
        roles = tuple(sorted({role for artifact in artifact_reuse for role in artifact.roles}))
        average_reuse = (
            sum(artifact.score for artifact in artifact_reuse) / len(artifact_reuse)
            if artifact_reuse
            else 0.0
        )
        role_coverage = min(1.0, len(roles) / 5.0)
        reuse_score = 0.8 * average_reuse + 20.0 * role_coverage

        alternatives = tuple(
            container for container in containers if container["container_id"] != current_container_id
        )
        compatible = tuple(
            container for container in alternatives if self._artifacts_fit(artifacts, container)
        )
        larger = tuple(
            container for container in compatible if int(container["capacity"]) > current_capacity
        )
        container_score = 100.0 * (
            0.55 * min(1.0, len(compatible) / 5.0)
            + 0.45 * min(1.0, len(larger) / 3.0)
        )
        best = max(
            larger or compatible,
            key=lambda container: (
                int(container["capacity"]),
                float(container["inner_protection"]),
                float(container["effectiveness"]),
            ),
            default=None,
        )
        best_view = None if best is None else {
            "container_id": best["container_id"],
            "name": best["name"],
            "capacity": int(best["capacity"]),
            "inner_protection": float(best["inner_protection"]),
        }
        score = 0.6 * reuse_score + 0.4 * container_score
        return UpgradePotential(
            score=round(score, 1),
            artifact_reuse_score=round(reuse_score, 1),
            container_upgrade_score=round(container_score, 1),
            reusable_roles=roles,
            compatible_container_count=len(compatible),
            larger_container_count=len(larger),
            best_container_upgrade=best_view,
            artifacts=artifact_reuse,
        )

    def artifacts_fit(
        self,
        artifacts: Iterable[dict[str, Any]],
        container: dict[str, Any],
    ) -> bool:
        return self._artifacts_fit(tuple(artifacts), container)

    def _artifact_reuse(
        self,
        artifact: dict[str, Any],
        containers: tuple[dict[str, Any], ...],
    ) -> ArtifactReuse:
        roles = self._artifact_roles(artifact)
        compatible = sum(self._artifacts_fit((artifact,), container) for container in containers)
        portability = compatible / len(containers) if containers else 0.0
        role_breadth = min(1.0, len(roles) / 3.0)
        score = 100.0 * (0.55 * role_breadth + 0.45 * portability)
        return ArtifactReuse(
            item_id=str(artifact.get("item_id") or ""),
            name=str(artifact.get("name") or ""),
            roles=roles,
            compatible_containers=compatible,
            container_candidates=len(containers),
            score=round(score, 1),
        )

    def _artifact_roles(self, artifact: dict[str, Any]) -> tuple[str, ...]:
        stats = artifact.get("stats") or {}
        roles = {
            role
            for role, keys in ROLE_STATS.items()
            if any(float(stats.get(key, 0.0)) > 0.0 for key in keys)
        }
        if float(stats.get("bleeding_output", 0.0)) < 0.0:
            roles.add("support")
        if any(float(value) < 0.0 for value in (artifact.get("infections") or {}).values()):
            roles.add("cleanse")
        return tuple(sorted(roles))

    def _artifacts_fit(
        self,
        artifacts: tuple[dict[str, Any], ...],
        container: dict[str, Any],
    ) -> bool:
        artifact_infections = add_stats(*(artifact.get("infections") or {} for artifact in artifacts))
        _, container_infections = split_infections(container.get("stats") or {})
        report = infection_report(
            artifact_infections,
            container_infections,
            float(container["inner_protection"]),
            self.mechanics,
        )
        return bool(report["valid"])
