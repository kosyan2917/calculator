from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .loaders import load_armor_items, read_json
from .stat_model import PRIMARY_STATS, SECONDARY_STATS, add_stats, derived_stats


@dataclass(frozen=True)
class QueryConfig:
    precomputed_path: str = "data/precomputed_builds.json"
    armor_stats_path: str = "data/armor_stats.json"
    ranks: tuple[str, ...] = ("Ветеран", "Мастер")
    armor_upgrade_level: int | None = 15
    max_results: int = 30
    max_near_misses: int = 10
    budget_overflow_for_near_misses: float = 0.15
    default_weights: dict[str, float] = field(
        default_factory=lambda: {
            "effective_durability": 4.0,
            "movement_speed": 3.0,
            "total_sprint_speed": 2.5,
            "stamina_regeneration": 1.5,
            "hp_regen_score": 2.0,
            "healing_effectiveness": 1.0,
            "carry_weight": 1.0,
        }
    )


def _stat_value(stats: dict[str, float], derived: dict[str, float], key: str) -> float:
    if key in derived:
        return float(derived.get(key) or 0.0)
    return float(stats.get(key) or 0.0)


def _target_score(
    stats: dict[str, float],
    derived: dict[str, float],
    targets: dict[str, float],
    weights: dict[str, float],
) -> tuple[float, list[dict[str, float]]]:
    score = 0.0
    misses: list[dict[str, float]] = []
    for key, target in targets.items():
        target = float(target)
        if target <= 0:
            continue
        value = _stat_value(stats, derived, key)
        weight = float(weights.get(key, 1.0))
        ratio = value / target
        if ratio >= 1.0:
            score += weight + min(0.25 * weight, (ratio - 1.0) * 0.05 * weight)
        else:
            gap_ratio = 1.0 - ratio
            score += weight * max(0.0, ratio)
            score -= weight * gap_ratio * gap_ratio
            misses.append({"stat": key, "target": target, "value": value, "gap": target - value})
    return score, misses


def _secondary_score(stats: dict[str, float]) -> float:
    score = 0.0
    score += -stats.get("bleeding_output", 0.0) * 0.02
    score += stats.get("bleeding_resistance", 0.0) * 0.01
    score += stats.get("burn_reaction", 0.0) * 0.01
    score += stats.get("tear_reaction", 0.0) * 0.01
    return score


class BuildQueryEngine:
    """Applies runtime filters and scoring over a precomputed build index."""

    def __init__(self, config: QueryConfig | None = None):
        self.config = config or QueryConfig()
        self.index = read_json(Path(self.config.precomputed_path))
        self.armor_items = load_armor_items(
            Path(self.config.armor_stats_path),
            set(self.config.ranks),
            self.config.armor_upgrade_level,
        )
        self.builds_by_signature = self._index_by_signature()

    def query(
        self,
        budget: int | None = None,
        targets: dict[str, float] | None = None,
        weights: dict[str, float] | None = None,
        armor_ids: set[str] | None = None,
        container_ids: set[str] | None = None,
        require_targets: bool = False,
        sort_by: str = "score",
        max_results: int | None = None,
    ) -> dict[str, Any]:
        targets = targets or {}
        merged_weights = {**self.config.default_weights, **(weights or {})}
        results: list[dict[str, Any]] = []
        near_misses: list[dict[str, Any]] = []
        max_budget = None if budget is None else budget * (1.0 + self.config.budget_overflow_for_near_misses)

        for container_entry in self.index.get("containers") or []:
            container = container_entry["container"]
            if container_ids and container["container_id"] not in container_ids:
                continue
            for build in container_entry.get("builds") or []:
                if max_budget is not None and build["artifact_price"] > max_budget:
                    continue
                for armor in self.armor_items:
                    if armor_ids and armor["base_id"] not in armor_ids and armor["item_id"] not in armor_ids:
                        continue
                    row = self._materialize_result(build, container, armor, targets, merged_weights)
                    in_budget = budget is None or row["total_price"] <= budget
                    passes_targets = not row["misses"]
                    if in_budget and (passes_targets or not require_targets):
                        results.append(row)
                    elif row["misses"] or not in_budget:
                        row["near_miss_reasons"] = []
                        if not in_budget:
                            row["near_miss_reasons"].append("over_budget")
                        if row["misses"]:
                            row["near_miss_reasons"].append("missing_targets")
                        near_misses.append(row)

        results = self._sort(results, sort_by)
        near_misses = self._sort(near_misses, "score")
        limit = max_results or self.config.max_results
        selected = results[:limit]
        for row in selected:
            row["nearby_upgrades"] = self._nearby_upgrades(row, targets, merged_weights)

        return {
            "query": {
                "budget": budget,
                "targets": targets,
                "weights": merged_weights,
                "require_targets": require_targets,
                "sort_by": sort_by,
            },
            "counts": {
                "results": len(results),
                "near_misses": len(near_misses),
                "returned": len(selected),
            },
            "results": selected,
            "near_misses": near_misses[: self.config.max_near_misses],
        }

    def _materialize_result(
        self,
        build: dict[str, Any],
        container: dict[str, Any],
        armor: dict[str, Any],
        targets: dict[str, float],
        weights: dict[str, float],
    ) -> dict[str, Any]:
        armor_stats = armor.get("stats") or {}
        stats = add_stats(build.get("stats") or {}, armor_stats)
        derived = derived_stats(stats)
        target_score, misses = _target_score(stats, derived, targets, weights)
        secondary = _secondary_score(stats)
        price_penalty = build["artifact_price"] / 20_000_000.0
        score = target_score + secondary + build.get("upgrade_potential", 0.0) * 0.01 - price_penalty
        return {
            "score": round(score, 6),
            "target_score": round(target_score, 6),
            "secondary_score": round(secondary, 6),
            "upgrade_potential": build.get("upgrade_potential", 0.0),
            "total_price": build["artifact_price"],
            "armor": {
                "item_id": armor["item_id"],
                "base_id": armor["base_id"],
                "name": armor["name"],
                "rank": armor["rank"],
                "category": armor["category"],
                "upgrade_level": armor["upgrade_level"],
            },
            "armor_stats": armor_stats,
            "container": {
                "container_id": container["container_id"],
                "name": container["name"],
                "rank": container["rank"],
                "capacity": container["capacity"],
                "inner_protection": container["inner_protection"],
                "effectiveness": container["effectiveness"],
            },
            "build_id": build["build_id"],
            "artifact_signature": build["artifact_signature"],
            "artifacts": build["artifacts"],
            "stats": stats,
            "derived": derived,
            "infection": build["infection"],
            "misses": misses,
        }

    def _nearby_upgrades(
        self,
        row: dict[str, Any],
        targets: dict[str, float],
        weights: dict[str, float],
    ) -> list[dict[str, Any]]:
        alternatives: list[dict[str, Any]] = []
        current_score = row["score"]
        for container_entry, build in self.builds_by_signature.get(row["artifact_signature"], []):
            container = container_entry["container"]
            if container["container_id"] == row["container"]["container_id"]:
                continue
            alt_armor = {**row["armor"], "stats": row["armor_stats"]}
            alt = self._materialize_result(build, container, alt_armor, targets, weights)
            score_gain = alt["score"] - current_score
            if score_gain <= 0:
                continue
            alternatives.append(
                {
                    "container": alt["container"],
                    "score_gain": round(score_gain, 6),
                    "price_delta": alt["total_price"] - row["total_price"],
                    "derived": alt["derived"],
                    "stats": {key: alt["stats"].get(key, 0.0) for key in PRIMARY_STATS + SECONDARY_STATS},
                }
            )
        alternatives.sort(key=lambda item: (-item["score_gain"], item["price_delta"]))
        return alternatives[:3]

    def _sort(self, rows: list[dict[str, Any]], sort_by: str) -> list[dict[str, Any]]:
        if sort_by == "upgrade_potential":
            return sorted(rows, key=lambda row: (-row["upgrade_potential"], -row["score"], row["total_price"]))
        if sort_by == "price":
            return sorted(rows, key=lambda row: (row["total_price"], -row["score"]))
        return sorted(rows, key=lambda row: (-row["score"], row["total_price"]))

    def _index_by_signature(self) -> dict[str, list[tuple[dict[str, Any], dict[str, Any]]]]:
        mapping: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for container_entry in self.index.get("containers") or []:
            for build in container_entry.get("builds") or []:
                mapping.setdefault(build["artifact_signature"], []).append((container_entry, build))
        return mapping
