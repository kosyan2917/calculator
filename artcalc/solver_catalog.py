from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .loaders import load_armor_items, load_artifact_candidates, load_containers
from .stat_model import INFECTION_STATS, MechanicsConfig


@dataclass(frozen=True)
class ArtifactGroup:
    """One artifact and rarity with affine endpoint properties."""

    group_id: str
    item_id: str
    name: str
    quality_tier: str
    quality_low: int
    quality_high: int
    price: int
    stats_low: dict[str, float]
    stats_high: dict[str, float]
    infections_low: dict[str, float]
    infections_high: dict[str, float]
    price_basis: str | None = None
    liquidity_score: float = 0.0
    confidence_score: float = 0.0
    max_count: int | None = None
    owned_instance_id: str | None = None
    market_price: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactGroup:
        return cls(**data)


@dataclass(frozen=True)
class SolverCatalog:
    generated_at: str
    artifact_upgrade_level: int
    artifact_price_upgrade_level: int
    min_quality_percent: float
    mechanics: dict[str, Any]
    artifact_groups: tuple[ArtifactGroup, ...]
    containers: tuple[dict[str, Any], ...]
    armors: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "artifact_groups": [asdict(group) for group in self.artifact_groups],
            "containers": list(self.containers),
            "armors": list(self.armors),
        }

    def write(self, path: str | Path) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def read(cls, path: str | Path) -> SolverCatalog:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            generated_at=str(data["generated_at"]),
            artifact_upgrade_level=int(data["artifact_upgrade_level"]),
            artifact_price_upgrade_level=int(data["artifact_price_upgrade_level"]),
            min_quality_percent=float(data["min_quality_percent"]),
            mechanics=dict(data["mechanics"]),
            artifact_groups=tuple(ArtifactGroup.from_dict(item) for item in data["artifact_groups"]),
            containers=tuple(data["containers"]),
            armors=tuple(data["armors"]),
        )


@dataclass(frozen=True)
class CatalogCompilerConfig:
    db_root: str = "stalzone-database"
    artifact_prices_path: str = "data/artifact_prices.json"
    artifact_additional_properties_path: str = "data/artifact_additional_properties.json"
    armor_stats_path: str = "data/armor_stats.json"
    lang: str = "ru"
    ranks: tuple[str, ...] = ("\u0412\u0435\u0442\u0435\u0440\u0430\u043d", "\u041c\u0430\u0441\u0442\u0435\u0440")
    artifact_upgrade_level: int = 15
    artifact_price_upgrade_level: int = 0
    min_quality_percent: float = 95.0
    max_artifact_price: int | None = None
    excluded_artifact_ids: tuple[str, ...] = ("9n7z",)
    excluded_container_ids: tuple[str, ...] = ("p99d",)
    included_container_ids: tuple[str, ...] = ("lny1",)
    allowed_quality_tiers: tuple[str, ...] = (
        "common",
        "uncommon",
        "special",
        "rare",
        "exclusive",
        "legendary",
    )
    validation_tolerance: float = 1e-6
    mechanics: MechanicsConfig = field(default_factory=MechanicsConfig)


class ArtifactCatalogCompiler:
    """Compiles game data into a solver-neutral, serializable catalog."""

    def __init__(self, config: CatalogCompilerConfig | None = None):
        self.config = config or CatalogCompilerConfig()

    def compile(self) -> SolverCatalog:
        config = self.config
        candidates = load_artifact_candidates(
            Path(config.db_root),
            Path(config.artifact_prices_path),
            config.lang,
            config.artifact_upgrade_level,
            config.mechanics,
            set(config.allowed_quality_tiers),
            config.artifact_price_upgrade_level,
            "grid",
            2.5,
            config.min_quality_percent,
            Path(config.artifact_additional_properties_path),
        )
        excluded_artifacts = set(config.excluded_artifact_ids)
        candidates = [candidate for candidate in candidates if candidate["item_id"] not in excluded_artifacts]
        if config.max_artifact_price is not None:
            candidates = [candidate for candidate in candidates if int(candidate["price"]) <= config.max_artifact_price]

        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for candidate in candidates:
            grouped.setdefault((candidate["item_id"], candidate["quality_tier"]), []).append(candidate)

        artifact_groups = tuple(
            self._compile_group(items)
            for _key, items in sorted(grouped.items())
        )

        containers = load_containers(
            Path(config.db_root),
            config.lang,
            set(config.ranks),
            set(config.included_container_ids),
        )
        containers = [
            container
            for container in containers
            if container["container_id"] not in set(config.excluded_container_ids)
        ]
        armors = load_armor_items(Path(config.armor_stats_path), set(config.ranks), config.artifact_upgrade_level)
        return SolverCatalog(
            generated_at=datetime.now(timezone.utc).isoformat(),
            artifact_upgrade_level=config.artifact_upgrade_level,
            artifact_price_upgrade_level=config.artifact_price_upgrade_level,
            min_quality_percent=config.min_quality_percent,
            mechanics=asdict(config.mechanics),
            artifact_groups=artifact_groups,
            containers=tuple(containers),
            armors=tuple(armors),
        )

    def _compile_group(self, items: list[dict[str, Any]]) -> ArtifactGroup:
        ordered = sorted(items, key=lambda item: float(item["quality_percent"]))
        low = ordered[0]
        high = ordered[-1]
        low_percent = float(low["quality_percent"])
        high_percent = float(high["quality_percent"])
        self._validate_affine(ordered, low, high)
        return ArtifactGroup(
            group_id=f"{low['item_id']}:{low['quality_tier']}",
            item_id=str(low["item_id"]),
            name=str(low["name"]),
            quality_tier=str(low["quality_tier"]),
            quality_low=int(round(low_percent * 100.0)),
            quality_high=int(round(high_percent * 100.0)),
            price=int(low["price"]),
            stats_low=dict(low.get("stats") or {}),
            stats_high=dict(high.get("stats") or {}),
            infections_low=dict(low.get("infections") or {}),
            infections_high=dict(high.get("infections") or {}),
            price_basis=low.get("price_basis"),
            liquidity_score=float(low.get("liquidity_score") or 0.0),
            confidence_score=float(low.get("confidence_score") or 0.0),
        )

    def _validate_affine(
        self,
        items: list[dict[str, Any]],
        low: dict[str, Any],
        high: dict[str, Any],
    ) -> None:
        low_percent = float(low["quality_percent"])
        high_percent = float(high["quality_percent"])
        if high_percent <= low_percent:
            return
        for section in ("stats", "infections"):
            keys = set(low.get(section) or {}) | set(high.get(section) or {})
            for item in items[1:-1]:
                progress = (float(item["quality_percent"]) - low_percent) / (high_percent - low_percent)
                for key in keys:
                    low_value = float((low.get(section) or {}).get(key, 0.0))
                    high_value = float((high.get(section) or {}).get(key, 0.0))
                    expected = low_value + (high_value - low_value) * progress
                    actual = float((item.get(section) or {}).get(key, 0.0))
                    if abs(expected - actual) > self.config.validation_tolerance:
                        raise ValueError(
                            f"Non-affine artifact property: {low['item_id']} {low['quality_tier']} "
                            f"{section}.{key} at {item['quality_percent']}%: {actual} != {expected}"
                        )
