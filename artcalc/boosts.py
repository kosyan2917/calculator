from __future__ import annotations

from dataclasses import dataclass, field
from heapq import heappush, heappushpop
from itertools import product
from pathlib import Path
from typing import Any

from .loaders import load_armor_items, read_json
from .query import QueryConfig, _secondary_score, _stat_value, _target_score
from .stat_model import PRIMARY_STATS, SECONDARY_STATS, add_stats, derived_stats


IGNORED_EFFECT_TYPES = {"Вывод", "Лечение"}


@dataclass(frozen=True)
class BoostedQueryConfig:
    precomputed_path: str = "data/precomputed_builds.json"
    armor_stats_path: str = "data/armor_stats.json"
    boosts_path: str = "data/boosts.json"
    ranks: tuple[str, ...] = ("Ветеран", "Мастер")
    armor_upgrade_level: int | None = 15
    max_results: int = 30
    base_candidate_limit: int = 800
    boosts_per_type: int = 4
    excluded_container_ids: tuple[str, ...] = ("p99d",)
    default_weights: dict[str, float] = field(default_factory=lambda: dict(QueryConfig().default_weights))


def _gap_score(row: dict[str, Any], targets: dict[str, float]) -> float:
    stats = row["stats"]
    derived = row["derived"]
    score = 0.0
    for key, target in targets.items():
        target = float(target)
        if target <= 0:
            continue
        value = _stat_value(stats, derived, key)
        gap = max(0.0, target - value)
        score += gap / max(abs(target), 1.0)
    return score


def _boost_target_score(boost: dict[str, Any], targets: dict[str, float], weights: dict[str, float]) -> float:
    stats = boost.get("stats") or {}
    derived = derived_stats(stats)
    score = 0.0
    for key in targets:
        value = _stat_value(stats, derived, key)
        if value > 0:
            score += value * float(weights.get(key, 1.0))
    duration = float(boost.get("duration_seconds") or 0.0)
    toxicity = float(boost.get("toxicity_penalty") or 0.0)
    priority = float(boost.get("priority") or 0.0)
    return score + min(duration, 1800.0) / 1800.0 + priority * 0.01 - toxicity * 0.01


class BoostedBuildQueryEngine:
    """Finds builds that pass targets only after applying one boost per effect type."""

    def __init__(self, config: BoostedQueryConfig | None = None):
        self.config = config or BoostedQueryConfig()
        self.index = read_json(Path(self.config.precomputed_path))
        self.armor_items = load_armor_items(
            Path(self.config.armor_stats_path),
            set(self.config.ranks),
            self.config.armor_upgrade_level,
        )
        self.boosts = self._load_boosts()
        self.boosts_by_effect_type = self._group_boosts()

    def query_only_with_boosts(
        self,
        budget: int | None = None,
        targets: dict[str, float] | None = None,
        weights: dict[str, float] | None = None,
        armor_ids: set[str] | None = None,
        container_ids: set[str] | None = None,
        max_results: int | None = None,
        base_candidate_limit: int | None = None,
        boosts_per_type: int | None = None,
    ) -> dict[str, Any]:
        targets = targets or {}
        merged_weights = {**self.config.default_weights, **(weights or {})}
        base_limit = base_candidate_limit or self.config.base_candidate_limit
        boost_limit = boosts_per_type or self.config.boosts_per_type
        candidate_boosts = self._candidate_boosts(targets, merged_weights, boost_limit)
        boost_combinations = self._boost_combinations(candidate_boosts)

        base_candidates: list[dict[str, Any]] = []
        scanned = 0
        skipped_already_valid = 0

        for container_entry in self.index.get("containers") or []:
            container = container_entry["container"]
            if container["container_id"] in self.config.excluded_container_ids:
                continue
            if container_ids and container["container_id"] not in container_ids:
                continue
            for build in container_entry.get("builds") or []:
                if budget is not None and build["artifact_price"] > budget:
                    continue
                for armor in self.armor_items:
                    if armor_ids and armor["base_id"] not in armor_ids and armor["item_id"] not in armor_ids:
                        continue
                    scanned += 1
                    row = self._materialize_base(build, container, armor, targets, merged_weights)
                    if not row["misses"]:
                        skipped_already_valid += 1
                        continue
                    row["_gap_score"] = _gap_score(row, targets)
                    base_candidates.append(row)

        base_candidates.sort(key=lambda row: (row["_gap_score"], -row["score"], row["total_price"]))
        base_candidates = base_candidates[:base_limit]

        result_heap: list[tuple[tuple[float, float, float, float], int, dict[str, Any]]] = []
        result_count = 0
        heap_counter = 0
        result_limit = max_results or self.config.max_results
        for row in base_candidates:
            for boosts in boost_combinations:
                boosted = self._evaluate_boosted(row, boosts, targets, merged_weights)
                if not boosted or boosted["misses"]:
                    continue
                result_count += 1
                rank = (
                    boosted["score"],
                    boosted["boost_summary"]["min_duration_seconds"],
                    -boosted["boost_summary"]["toxicity_penalty"],
                    -boosted["total_price"],
                )
                heap_counter += 1
                item = (rank, heap_counter, boosted)
                if len(result_heap) < result_limit:
                    heappush(result_heap, item)
                elif rank > result_heap[0][0]:
                    heappushpop(result_heap, item)

        selected = [
            item[2]
            for item in sorted(
                result_heap,
                key=lambda item: (
                    -item[0][0],
                    -item[0][1],
                    -item[0][2],
                    -item[0][3],
                    item[1],
                ),
            )
        ]

        return {
            "category": "only_with_boosts",
            "query": {
                "budget": budget,
                "targets": targets,
                "weights": merged_weights,
                "base_candidate_limit": base_limit,
                "boosts_per_type": boost_limit,
                "ignored_effect_types": sorted(IGNORED_EFFECT_TYPES),
            },
            "counts": {
                "scanned_base_rows": scanned,
                "skipped_already_valid_without_boosts": skipped_already_valid,
                "base_candidates_checked": len(base_candidates),
                "boost_combinations": len(boost_combinations),
                "results": result_count,
                "returned": len(selected),
            },
            "boost_options": {
                effect_type: [
                    {
                        "boost_id": boost["boost_id"],
                        "name": boost["name"],
                        "duration_seconds": boost.get("duration_seconds"),
                        "priority": boost.get("priority"),
                        "toxicity_penalty": boost.get("toxicity_penalty"),
                        "stats": boost.get("stats") or {},
                    }
                    for boost in boosts
                ]
                for effect_type, boosts in candidate_boosts.items()
            },
            "results": selected,
        }

    def _load_boosts(self) -> list[dict[str, Any]]:
        payload = read_json(Path(self.config.boosts_path))
        return [
            boost
            for boost in payload.get("boosts") or []
            if not boost.get("excluded_from_builds") and boost.get("effect_type") not in IGNORED_EFFECT_TYPES
        ]

    def _group_boosts(self) -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for boost in self.boosts:
            effect_type = str(boost.get("effect_type") or "")
            if not effect_type:
                continue
            groups.setdefault(effect_type, []).append(boost)
        return groups

    def _candidate_boosts(
        self,
        targets: dict[str, float],
        weights: dict[str, float],
        limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        selected: dict[str, list[dict[str, Any]]] = {}
        for effect_type, boosts in self.boosts_by_effect_type.items():
            ranked = sorted(boosts, key=lambda boost: -_boost_target_score(boost, targets, weights))
            selected[effect_type] = ranked[:limit]
        return selected

    def _boost_combinations(self, candidate_boosts: dict[str, list[dict[str, Any]]]) -> list[tuple[dict[str, Any], ...]]:
        groups = [[None, *candidate_boosts[effect_type]] for effect_type in sorted(candidate_boosts)]
        combinations: list[tuple[dict[str, Any], ...]] = []
        for combo in product(*groups):
            boosts = tuple(boost for boost in combo if boost is not None)
            if boosts:
                combinations.append(boosts)
        return combinations

    def _materialize_base(
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

    def _evaluate_boosted(
        self,
        row: dict[str, Any],
        boosts: tuple[dict[str, Any], ...],
        targets: dict[str, float],
        weights: dict[str, float],
    ) -> dict[str, Any] | None:
        boost_stats = add_stats(*(boost.get("stats") or {} for boost in boosts))
        stats = add_stats(row["stats"], boost_stats)
        derived = derived_stats(stats)
        target_score, misses = _target_score(stats, derived, targets, weights)
        if misses:
            return None
        secondary = _secondary_score(stats)
        price_penalty = row["total_price"] / 20_000_000.0
        toxicity = sum(float(boost.get("toxicity_penalty") or 0.0) for boost in boosts)
        min_duration = min(float(boost.get("duration_seconds") or 0.0) for boost in boosts)
        short_duration_penalty = 0.0 if min_duration >= 900.0 else (900.0 - min_duration) / 900.0
        score = (
            target_score
            + secondary
            + row.get("upgrade_potential", 0.0) * 0.01
            - price_penalty
            - toxicity * 0.005
            - short_duration_penalty * 0.25
            - len(boosts) * 0.02
        )
        output = {key: value for key, value in row.items() if not key.startswith("_")}
        output.update(
            {
                "category": "only_with_boosts",
                "score": round(score, 6),
                "target_score": round(target_score, 6),
                "secondary_score": round(secondary, 6),
                "stats_without_boosts": row["stats"],
                "derived_without_boosts": row["derived"],
                "misses_without_boosts": row["misses"],
                "stats": stats,
                "derived": derived,
                "misses": misses,
                "boosts": [
                    {
                        "boost_id": boost["boost_id"],
                        "name": boost["name"],
                        "effect_type": boost["effect_type"],
                        "price": 0,
                        "priority": boost.get("priority"),
                        "duration_seconds": boost.get("duration_seconds"),
                        "toxicity_penalty": boost.get("toxicity_penalty"),
                        "stats": boost.get("stats") or {},
                    }
                    for boost in boosts
                ],
                "boost_summary": {
                    "effect_types": sorted(boost["effect_type"] for boost in boosts),
                    "count": len(boosts),
                    "price": 0,
                    "min_duration_seconds": min_duration,
                    "toxicity_penalty": toxicity,
                    "stats": boost_stats,
                },
            }
        )
        output["comparison"] = {
            key: {
                "without_boosts": _stat_value(row["stats"], row["derived"], key),
                "with_boosts": _stat_value(stats, derived, key),
                "delta": _stat_value(stats, derived, key) - _stat_value(row["stats"], row["derived"], key),
            }
            for key in list(targets) + PRIMARY_STATS + SECONDARY_STATS
        }
        return output
