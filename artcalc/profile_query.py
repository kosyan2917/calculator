from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .loaders import load_armor_items, read_json
from .stat_model import add_stats, derived_stats


PROFILE_KEYS = {"speed", "durability", "regen", "carry_weight"}


@dataclass(frozen=True)
class ProfileQueryConfig:
    precomputed_path: str = "data/precomputed_builds.json"
    armor_stats_path: str = "data/armor_stats.json"
    ranks: tuple[str, ...] = ("\u0412\u0435\u0442\u0435\u0440\u0430\u043d", "\u041c\u0430\u0441\u0442\u0435\u0440")
    armor_upgrade_level: int | None = 15
    max_results: int = 10
    excluded_container_ids: tuple[str, ...] = ("p99d",)


def normalize_profile(profile: dict[str, int | float]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for key, value in profile.items():
        if key not in PROFILE_KEYS:
            raise ValueError(f"Unknown profile key: {key}")
        normalized[key] = max(-2.0, min(2.0, float(value)))
    return normalized


class ProfileQueryEngine:
    """Ranks precomputed builds by relative user preferences on the -2..2 scale."""

    def __init__(self, config: ProfileQueryConfig | None = None):
        self.config = config or ProfileQueryConfig()
        self.index = read_json(Path(self.config.precomputed_path))
        self.armor_items = load_armor_items(
            Path(self.config.armor_stats_path),
            set(self.config.ranks),
            self.config.armor_upgrade_level,
        )

    def query(
        self,
        budget: int,
        profile: dict[str, int | float],
        max_results: int | None = None,
    ) -> dict[str, Any]:
        preferences = normalize_profile(profile)
        rows = self._rows_in_budget(budget)
        self._attach_profile_metrics(rows)
        ranges = self._metric_ranges(rows)
        for row in rows:
            row["profile_score"] = round(self._profile_score(row, preferences, ranges, budget), 6)
        rows.sort(key=lambda item: (-item["profile_score"], item["total_price"]))
        limit = max_results or self.config.max_results
        return {
            "query": {
                "budget": budget,
                "profile": preferences,
            },
            "counts": {
                "candidates": len(rows),
                "returned": min(limit, len(rows)),
            },
            "results": rows[:limit],
        }

    def _rows_in_budget(self, budget: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for container_entry in self.index.get("containers") or []:
            container = container_entry["container"]
            if container["container_id"] in self.config.excluded_container_ids:
                continue
            for build in container_entry.get("builds") or []:
                if int(build["artifact_price"]) > budget:
                    continue
                for armor in self.armor_items:
                    armor_stats = armor.get("stats") or {}
                    stats = add_stats(build.get("stats") or {}, armor_stats)
                    derived = derived_stats(stats)
                    rows.append(
                        {
                            "total_price": int(build["artifact_price"]),
                            "armor": {
                                "item_id": armor["item_id"],
                                "base_id": armor["base_id"],
                                "name": armor["name"],
                                "category": armor["category"],
                            },
                            "container": {
                                "container_id": container["container_id"],
                                "name": container["name"],
                                "capacity": container["capacity"],
                                "inner_protection": container["inner_protection"],
                                "effectiveness": container["effectiveness"],
                            },
                            "build_id": build["build_id"],
                            "artifacts": build["artifacts"],
                            "stats": stats,
                            "derived": derived,
                            "infection": build["infection"],
                            "upgrade_potential": build.get("upgrade_potential", 0.0),
                        }
                    )
        return rows

    def _attach_profile_metrics(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            stats = row["stats"]
            derived = row["derived"]
            row["profile_metrics"] = {
                "speed": (
                    float(stats.get("movement_speed") or 0.0)
                    + max(0.0, float(derived.get("total_sprint_speed") or 100.0) - 100.0)
                ),
                "durability": float(derived.get("effective_durability") or 0.0),
                "regen": self._regen_metric(stats, derived),
                "carry_weight": float(stats.get("carry_weight") or 0.0),
            }

    def _regen_metric(self, stats: dict[str, float], derived: dict[str, float]) -> float:
        hp_regen = float(derived.get("hp_regen_score") or 0.0)
        bullet = max(0.0, float(stats.get("bullet_resistance") or 0.0))
        return hp_regen * (1.0 + bullet / 300.0)

    def _metric_ranges(self, rows: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
        ranges: dict[str, tuple[float, float]] = {}
        for key in PROFILE_KEYS:
            values = [float(row["profile_metrics"][key]) for row in rows]
            ranges[key] = (min(values), max(values)) if values else (0.0, 0.0)
        return ranges

    def _profile_score(
        self,
        row: dict[str, Any],
        preferences: dict[str, float],
        ranges: dict[str, tuple[float, float]],
        budget: int,
    ) -> float:
        score = 0.0
        for key, weight in preferences.items():
            if abs(weight) <= 1e-9:
                continue
            score += weight * self._normalized_metric(row["profile_metrics"][key], ranges[key])
        score += float(row.get("upgrade_potential") or 0.0) / 100.0 * 0.15
        score -= row["total_price"] / max(float(budget), 1.0) * 0.05
        return score

    def _normalized_metric(self, value: float, metric_range: tuple[float, float]) -> float:
        low, high = metric_range
        if high <= low:
            return 0.0
        return (float(value) - low) / (high - low)
