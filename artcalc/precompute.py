from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from heapq import nlargest
import math
from pathlib import Path
import time
from typing import Any

from .loaders import effective_artifact_stats, load_artifact_candidates, load_containers
from .stat_model import (
    INFECTION_STATS,
    PRIMARY_STATS,
    SECONDARY_STATS,
    MechanicsConfig,
    add_stats,
    derived_stats,
    infection_report,
    split_infections,
)


REPRESENTATIVE_DERIVED_METRICS = ["effective_durability", "total_sprint_speed", "hp_regen_score"]
REPRESENTATIVE_PROFILES = {
    "tank": {
        "effective_durability": 2.0,
        "vitality": 0.8,
        "movement_speed": -1.0,
        "total_sprint_speed_bonus": -1.0,
    },
    "vitality_tank": {
        "vitality": 2.0,
        "effective_durability": 0.8,
        "movement_speed": -0.8,
        "total_sprint_speed_bonus": -0.8,
    },
    "speed": {
        "movement_speed": 2.0,
        "total_sprint_speed_bonus": 2.0,
        "effective_durability": -1.0,
    },
    "balanced": {
        "effective_durability": 1.2,
        "movement_speed": 1.2,
        "total_sprint_speed_bonus": 1.0,
    },
    "regen": {
        "hp_regen_score": 2.0,
        "bullet_resistance": 1.2,
    },
    "tank_regen": {
        "bullet_resistance": 1.8,
        "hp_regen_score": 1.5,
        "effective_durability": 0.6,
        "movement_speed": -0.6,
        "total_sprint_speed_bonus": -0.6,
    },
    "speed_regen": {
        "movement_speed": 1.5,
        "total_sprint_speed_bonus": 1.5,
        "hp_regen_score": 1.0,
        "effective_durability": -0.6,
    },
    "balanced_regen": {
        "effective_durability": 1.0,
        "movement_speed": 1.0,
        "total_sprint_speed_bonus": 0.8,
        "hp_regen_score": 1.0,
    },
}

PROFILE_METRIC_SCALES = {
    "effective_durability": 100.0,
    "bullet_resistance": 10.0,
    "vitality": 10.0,
    "movement_speed": 5.0,
    "sprint_speed": 5.0,
    "total_sprint_speed_bonus": 5.0,
    "stamina_regeneration": 5.0,
    "hp_regen_score": 5.0,
    "healing_effectiveness": 10.0,
    "carry_weight": 10.0,
}


@dataclass(frozen=True)
class PrecomputeConfig:
    db_root: str = "stalzone-database"
    artifact_prices_path: str = "data/artifact_prices.json"
    artifact_additional_properties_path: str = "data/artifact_additional_properties.json"
    output_path: str = "data/precomputed_builds.json"
    lang: str = "ru"
    ranks: tuple[str, ...] = ("Ветеран", "Мастер")
    artifact_upgrade_level: int = 15
    artifact_price_upgrade_level: int = 0
    quality_strategy: str = "adaptive_grid"
    quality_step: float = 2.5
    min_quality_percent: float = 95.0
    min_build_price: int = 2_500_000
    max_build_price: int | None = 150_000_000
    price_bucket_start: int = 2_500_000
    price_bucket_step: int = 2_500_000
    price_bucket_beam_size: int = 20
    max_beam_states: int = 0
    excluded_artifact_ids: tuple[str, ...] = ("9n7z",)
    excluded_container_ids: tuple[str, ...] = ("p99d",)
    allowed_quality_tiers: tuple[str, ...] = ("common", "uncommon", "special", "rare", "exclusive", "legendary")
    max_artifact_candidates: int = 1500
    beam_size: int = 800
    frontier_limit_per_container: int = 6000
    allow_duplicate_artifacts: bool = True
    progress_path: str = "data/precompute_progress.json"
    progress_interval_seconds: float = 5.0
    mechanics: MechanicsConfig = field(default_factory=MechanicsConfig)


def _state_score(state: dict[str, Any]) -> float:
    stats = state["stats"]
    infections = state["artifact_infections"]
    price = max(1.0, float(state["artifact_price"]))
    score = 0.0
    for key in PRIMARY_STATS:
        value = max(0.0, stats.get(key, 0.0))
        score += value / (value + 10.0) if value else 0.0
    for key in SECONDARY_STATS:
        value = max(0.0, stats.get(key, 0.0))
        score += (value / (value + 10.0) if value else 0.0) * 0.25
    score -= sum(max(0.0, infections.get(key, 0.0)) * 0.1 for key in INFECTION_STATS)
    score -= price / 1_000_000_000.0
    return score


def _candidate_score(candidate: dict[str, Any]) -> float:
    stats = candidate.get("stats") or {}
    infections = candidate.get("infections") or {}
    price = max(1.0, float(candidate.get("price") or 1))
    score = 0.0
    for key in PRIMARY_STATS:
        value = max(0.0, stats.get(key, 0.0))
        score += value / (value + 10.0) if value else 0.0
    score -= sum(max(0.0, infections.get(key, 0.0)) * 0.1 for key in INFECTION_STATS)
    score += min(float(candidate.get("liquidity_score") or 0.0), 100.0) / 100.0
    return score / (1.0 + price / 50_000_000.0)


def select_artifact_candidates(
    candidates: list[dict[str, Any]],
    limit: int,
    price_bucket_start: int = 2_500_000,
    price_bucket_step: int = 2_500_000,
) -> list[dict[str, Any]]:
    if limit <= 0 or len(candidates) <= limit:
        return sorted(candidates, key=_candidate_score, reverse=True)

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = (str(candidate["item_id"]), str(candidate["quality_tier"]))
        groups.setdefault(key, []).append(candidate)

    grouped = []
    for key, items in groups.items():
        ranked = _rank_quality_variants(items)
        min_price = min(int(item.get("price") or 0) for item in ranked)
        quality_order = min(int(item.get("quality_order") or 999) for item in ranked)
        grouped.append((key, ranked, min_price, quality_order))
    grouped.sort(key=lambda item: (price_bucket(item[2], price_bucket_start, price_bucket_step), item[3], item[0]))

    selected: list[dict[str, Any]] = []
    depth = 0
    while len(selected) < limit:
        added = False
        for _key, items, _min_price, _quality_order in grouped:
            if depth >= len(items):
                continue
            selected.append(items[depth])
            added = True
            if len(selected) >= limit:
                break
        if not added:
            break
        depth += 1
    return selected


def _rank_quality_variants(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    has_harmful_infection = any(
        any(value > 0 for value in (candidate.get("infections") or {}).values())
        for candidate in items
    )
    if not has_harmful_infection:
        return sorted(items, key=lambda item: -float(item["quality_percent"]))

    ordered = sorted(items, key=lambda item: float(item["quality_percent"]))
    ranked: list[dict[str, Any]] = []
    low_index = 0
    high_index = len(ordered) - 1
    while low_index <= high_index:
        ranked.append(ordered[low_index])
        if low_index != high_index:
            ranked.append(ordered[high_index])
        low_index += 1
        high_index -= 1
    return ranked


def _dedupe_key(state: dict[str, Any]) -> tuple[Any, ...]:
    stats = state["stats"]
    infections = state["artifact_infections"]
    return (
        round(state["artifact_price"] / 250_000),
        *(round(stats.get(key, 0.0), 2) for key in PRIMARY_STATS),
        *(round(infections.get(key, 0.0), 2) for key in INFECTION_STATS),
    )


def price_bucket(price: int | float, start: int, step: int) -> int:
    if step <= 0:
        return int(price)
    if price <= start:
        return start
    return int(math.ceil(float(price) / float(step)) * step)


def _dominates(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["artifact_price"] > b["artifact_price"]:
        return False
    better = a["artifact_price"] < b["artifact_price"]
    for key in PRIMARY_STATS:
        if a["stats"].get(key, 0.0) + 1e-9 < b["stats"].get(key, 0.0):
            return False
        better = better or a["stats"].get(key, 0.0) > b["stats"].get(key, 0.0) + 1e-9
    return better


def pareto_frontier(builds: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ordered = sorted(builds, key=lambda build: (-build["precompute_score"], build["artifact_price"]))
    frontier: list[dict[str, Any]] = []
    for build in ordered:
        if any(_dominates(existing, build) for existing in frontier):
            continue
        frontier = [existing for existing in frontier if not _dominates(build, existing)]
        frontier.append(build)
        if len(frontier) > limit * 2:
            frontier = sorted(frontier, key=lambda item: (-item["precompute_score"], item["artifact_price"]))[:limit]
    return sorted(frontier, key=lambda item: (-item["precompute_score"], item["artifact_price"]))[:limit]


class BuildPrecomputer:
    """Builds a reusable artifact-loadout index grouped by container."""

    def __init__(self, config: PrecomputeConfig | None = None):
        self.config = config or PrecomputeConfig()
        self._started_at = time.monotonic()
        self._last_progress_at = 0.0

    def run(self) -> dict[str, Any]:
        self._started_at = time.monotonic()
        self._last_progress_at = 0.0
        db_root = Path(self.config.db_root)
        ranks = set(self.config.ranks)
        allowed_tiers = set(self.config.allowed_quality_tiers)
        self._write_progress({"status": "loading", "message": "Loading containers and artifact candidates."}, force=True)
        containers = load_containers(db_root, self.config.lang, ranks)
        excluded_container_ids = set(self.config.excluded_container_ids)
        containers_before_exclusions = len(containers)
        containers = [container for container in containers if container["container_id"] not in excluded_container_ids]
        containers_excluded = containers_before_exclusions - len(containers)
        loaded_candidates_before_exclusions = load_artifact_candidates(
            db_root,
            Path(self.config.artifact_prices_path),
            self.config.lang,
            self.config.artifact_upgrade_level,
            self.config.mechanics,
            allowed_tiers,
            self.config.artifact_price_upgrade_level,
            self.config.quality_strategy,
            self.config.quality_step,
            self.config.min_quality_percent,
            Path(self.config.artifact_additional_properties_path),
        )
        excluded_artifact_ids = set(self.config.excluded_artifact_ids)
        loaded_candidates = [
            candidate
            for candidate in loaded_candidates_before_exclusions
            if candidate["item_id"] not in excluded_artifact_ids
        ]
        artifact_candidates_excluded = len(loaded_candidates_before_exclusions) - len(loaded_candidates)
        loaded_candidates_before_price_cap = len(loaded_candidates)
        if self.config.max_build_price is not None:
            loaded_candidates = [
                candidate
                for candidate in loaded_candidates
                if int(candidate.get("price") or 0) <= self.config.max_build_price
            ]
        artifact_candidates_excluded_by_price_cap = loaded_candidates_before_price_cap - len(loaded_candidates)
        candidates = select_artifact_candidates(
            loaded_candidates,
            self.config.max_artifact_candidates,
            self.config.price_bucket_start,
            self.config.price_bucket_step,
        )

        container_entries: list[dict[str, Any]] = []
        all_builds: list[dict[str, Any]] = []
        for container_index, container in enumerate(containers, start=1):
            self._write_progress(
                {
                    "status": "running",
                    "phase": "container",
                    "container_index": container_index,
                    "containers_total": len(containers),
                    "container": self._container_progress_view(container),
                    "artifact_candidates_loaded_before_exclusions": len(loaded_candidates_before_exclusions),
                    "artifact_candidates_excluded": artifact_candidates_excluded,
                    "artifact_candidates_excluded_by_price_cap": artifact_candidates_excluded_by_price_cap,
                    "artifact_candidates_loaded": len(loaded_candidates),
                    "artifact_candidates": len(candidates),
                    "completed_containers": len(container_entries),
                    "frontier_builds_so_far": sum(entry["frontier_builds"] for entry in container_entries),
                },
                force=True,
            )
            builds, diagnostics = self._precompute_container(container, candidates, container_index, len(containers))
            frontier = pareto_frontier(builds, self.config.frontier_limit_per_container)
            container_entry = {
                "container": container,
                "candidate_builds": len(builds),
                "frontier_builds": len(frontier),
                "diagnostics": diagnostics,
                "builds": frontier,
            }
            container_entries.append(container_entry)
            all_builds.extend(frontier)
            self._write_progress(
                {
                    "status": "running",
                    "phase": "container_done",
                    "container_index": container_index,
                    "containers_total": len(containers),
                    "container": self._container_progress_view(container),
                    "candidate_builds": len(builds),
                    "frontier_builds": len(frontier),
                    "completed_containers": len(container_entries),
                    "frontier_builds_so_far": sum(entry["frontier_builds"] for entry in container_entries),
                },
                force=True,
            )

        self._write_progress({"status": "finalizing", "message": "Attaching upgrade potential and writing output."}, force=True)
        self._attach_upgrade_potential(container_entries)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "config": {
                **asdict(self.config),
                "mechanics": asdict(self.config.mechanics),
            },
            "counts": {
                "containers": len(container_entries),
                "containers_excluded": containers_excluded,
                "artifact_candidates_loaded_before_exclusions": len(loaded_candidates_before_exclusions),
                "artifact_candidates_excluded": artifact_candidates_excluded,
                "artifact_candidates_excluded_by_price_cap": artifact_candidates_excluded_by_price_cap,
                "artifact_candidates_loaded": len(loaded_candidates),
                "artifact_candidates": len(candidates),
                "frontier_builds": sum(entry["frontier_builds"] for entry in container_entries),
            },
            "containers": container_entries,
        }
        output_path = Path(self.config.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(__import__("json").dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_progress(
            {
                "status": "done",
                "output_path": self.config.output_path,
                "counts": payload["counts"],
                "elapsed_seconds": round(time.monotonic() - self._started_at, 3),
            },
            force=True,
        )
        return payload

    def _precompute_container(
        self,
        container: dict[str, Any],
        candidates: list[dict[str, Any]],
        container_index: int,
        containers_total: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        container_stats, container_infections = split_infections(container.get("stats") or {})
        initial = {
            "last_index": 0,
            "artifact_indices": (),
            "artifact_keys": (),
            "artifact_counts": {},
            "artifact_price": 0,
            "stats": dict(container_stats),
            "artifact_infections": {},
            "container_infections": dict(container_infections),
            "artifact_details": (),
        }
        beam = [initial]
        final_states: list[dict[str, Any]] = []
        slot_diagnostics: list[dict[str, Any]] = []
        capacity = int(container["capacity"])
        for slot in range(capacity):
            expanded: list[dict[str, Any]] = []
            beam_len = len(beam)
            for state_index, state in enumerate(beam, start=1):
                start = int(state["last_index"])
                for index in range(start, len(candidates)):
                    next_state = self._extend_state(state, candidates[index], index, container)
                    if self.config.max_build_price is not None and int(next_state["artifact_price"]) > self.config.max_build_price:
                        continue
                    expanded.append(next_state)
                self._write_progress(
                    {
                        "status": "running",
                        "phase": "expanding_slot",
                        "container_index": container_index,
                        "containers_total": containers_total,
                        "container": self._container_progress_view(container),
                        "slot": slot + 1,
                        "slots_total": capacity,
                        "beam_state_index": state_index,
                        "beam_states_total": beam_len,
                        "expanded_states": len(expanded),
                        "completed_containers": container_index - 1,
                    },
                )
            if not expanded:
                break
            beam, diagnostics = self._prune_beam_by_price(expanded)
            diagnostics["slot"] = slot + 1
            slot_diagnostics.append(diagnostics)
            self._write_progress(
                {
                    "status": "running",
                    "phase": "slot_done",
                    "container_index": container_index,
                    "containers_total": containers_total,
                    "container": self._container_progress_view(container),
                    "slot": slot + 1,
                    "slots_total": capacity,
                    "completed_containers": container_index - 1,
                    "slot_diagnostics": diagnostics,
                },
                force=True,
            )
            if slot + 1 == capacity:
                final_states = beam

        builds: list[dict[str, Any]] = []
        for state in final_states:
            if int(state["artifact_price"]) < self.config.min_build_price:
                continue
            if self.config.max_build_price is not None and int(state["artifact_price"]) > self.config.max_build_price:
                continue
            report = infection_report(
                state["artifact_infections"],
                state["container_infections"],
                float(container["inner_protection"]),
                self.config.mechanics,
            )
            if not report["valid"]:
                continue
            stats = state["stats"]
            build = {
                "build_id": f"{container['container_id']}:" + ",".join(state["artifact_keys"]),
                "container_id": container["container_id"],
                "artifact_signature": "|".join(state["artifact_keys"]),
                "artifact_price": state["artifact_price"],
                "price_bucket": price_bucket(
                    state["artifact_price"],
                    self.config.price_bucket_start,
                    self.config.price_bucket_step,
                ),
                "artifact_count": len(state["artifact_keys"]),
                "artifacts": list(state["artifact_details"]),
                "stats": stats,
                "derived": derived_stats(stats),
                "infection": report,
                "precompute_score": _state_score(state),
                "secondary": {key: stats.get(key, 0.0) for key in SECONDARY_STATS},
                "upgrade_potential": 0.0,
            }
            builds.append(build)
        diagnostics = {
            "slots": slot_diagnostics,
            "final_states": len(final_states),
            "valid_builds": len(builds),
            "invalid_or_too_cheap_final_states": max(0, len(final_states) - len(builds)),
        }
        return builds, diagnostics

    def _container_progress_view(self, container: dict[str, Any]) -> dict[str, Any]:
        return {
            "container_id": container.get("container_id"),
            "name": container.get("name"),
            "capacity": container.get("capacity"),
        }

    def _write_progress(self, data: dict[str, Any], force: bool = False) -> None:
        if not self.config.progress_path:
            return
        now = time.monotonic()
        if not force and now - self._last_progress_at < self.config.progress_interval_seconds:
            return
        self._last_progress_at = now
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(now - self._started_at, 3),
            **data,
        }
        path = Path(self.config.progress_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(__import__("json").dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _prune_beam_by_price(self, expanded: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        grouped: dict[int, list[dict[str, Any]]] = {}
        for state in expanded:
            bucket = price_bucket(
                state["artifact_price"],
                self.config.price_bucket_start,
                self.config.price_bucket_step,
            )
            state["price_bucket"] = bucket
            grouped.setdefault(bucket, []).append(state)

        bucket_states: dict[int, list[dict[str, Any]]] = {}
        deduped_count = 0
        for bucket, states in grouped.items():
            deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
            for state in states:
                key = _dedupe_key(state)
                if key not in deduped or _state_score(state) > _state_score(deduped[key]):
                    deduped[key] = state
            bucket_states[bucket] = self._bucket_representatives(list(deduped.values()), self.config.price_bucket_beam_size)
            deduped_count += len(deduped)

        selected: list[dict[str, Any]] = []
        depth = 0
        buckets = sorted(bucket_states)
        global_beam_limit = self.config.max_beam_states if self.config.max_beam_states > 0 else self.config.beam_size
        while len(selected) < global_beam_limit:
            added = False
            for bucket in buckets:
                states = bucket_states[bucket]
                if depth >= len(states):
                    continue
                selected.append(states[depth])
                added = True
                if len(selected) >= global_beam_limit:
                    break
            if not added:
                break
            depth += 1

        selected.sort(key=_state_score, reverse=True)
        return selected, {
            "expanded_states": len(expanded),
            "price_buckets": len(grouped),
            "deduped_states": deduped_count,
            "kept_states": len(selected),
            "global_beam_limit": global_beam_limit,
            "min_bucket": min(buckets) if buckets else None,
            "max_bucket": max(buckets) if buckets else None,
        }

    def _bucket_representatives(self, states: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        if len(states) <= limit:
            return nlargest(len(states), states, key=_state_score)

        metrics = (
            ["cheap"]
            + PRIMARY_STATS
            + SECONDARY_STATS
            + REPRESENTATIVE_DERIVED_METRICS
            + [f"profile:{name}" for name in REPRESENTATIVE_PROFILES]
            + [f"infection_safety:{key}" for key in INFECTION_STATS]
            + ["low_positive_infection"]
        )
        score_cache: dict[int, float] = {}
        metric_winners: dict[str, tuple[float, float, float, dict[str, Any]]] = {}

        for state in states:
            state_id = id(state)
            state_score = _state_score(state)
            score_cache[state_id] = state_score
            price_tiebreak = -float(state["artifact_price"])
            for metric in metrics:
                value = self._representative_metric(state, metric)
                candidate = (value, state_score, price_tiebreak, state)
                current = metric_winners.get(metric)
                if current is None or candidate[:3] > current[:3]:
                    metric_winners[metric] = candidate

        selected_by_id: dict[int, dict[str, Any]] = {}
        for _metric, (_value, _state_score_value, _price_tiebreak, state) in metric_winners.items():
            selected_by_id[id(state)] = state

        selected = list(selected_by_id.values())
        if len(selected) < limit:
            remaining = [state for state in states if id(state) not in selected_by_id]
            selected.extend(
                nlargest(
                    limit - len(selected),
                    remaining,
                    key=lambda state: score_cache.get(id(state)) or _state_score(state),
                )
            )
        return selected[:limit]

    def _representative_metric(self, state: dict[str, Any], metric: str) -> float:
        stats = state["stats"]
        infections = state["artifact_infections"]
        if metric == "cheap":
            return -float(state["artifact_price"])
        if metric == "effective_durability":
            return derived_stats(stats)["effective_durability"]
        if metric == "total_sprint_speed":
            return derived_stats(stats)["total_sprint_speed"]
        if metric == "hp_regen_score":
            return derived_stats(stats)["hp_regen_score"]
        if metric.startswith("profile:"):
            profile_name = metric.split(":", 1)[1]
            return self._profile_score(state, REPRESENTATIVE_PROFILES[profile_name])
        if metric.startswith("infection_safety:"):
            infection_key = metric.split(":", 1)[1]
            return -float(infections.get(infection_key, 0.0))
        if metric == "low_positive_infection":
            return -sum(max(0.0, float(infections.get(key, 0.0))) for key in INFECTION_STATS)
        return float(stats.get(metric, 0.0))

    def _profile_score(self, state: dict[str, Any], profile: dict[str, float]) -> float:
        stats = state["stats"]
        derived = derived_stats(stats)
        score = 0.0
        for metric, weight in profile.items():
            value = self._profile_metric_value(stats, derived, metric)
            scale = PROFILE_METRIC_SCALES.get(metric, 10.0)
            score += float(weight) * (value / (abs(value) + scale) if abs(value) > 1e-9 else 0.0)
        score -= state["artifact_price"] / 150_000_000.0 * 0.05
        return score

    def _profile_metric_value(self, stats: dict[str, float], derived: dict[str, float], metric: str) -> float:
        if metric == "effective_durability":
            return float(derived.get("effective_durability") or 0.0)
        if metric == "total_sprint_speed_bonus":
            return float(derived.get("total_sprint_speed") or 100.0) - 100.0
        if metric == "hp_regen_score":
            return float(derived.get("hp_regen_score") or 0.0)
        return float(stats.get(metric) or 0.0)

    def _extend_state(
        self,
        state: dict[str, Any],
        candidate: dict[str, Any],
        candidate_index: int,
        container: dict[str, Any],
    ) -> dict[str, Any]:
        stats, infections = effective_artifact_stats(candidate, container, self.config.mechanics)
        next_last = candidate_index if self.config.allow_duplicate_artifacts else candidate_index + 1
        counts = dict(state["artifact_counts"])
        counts[candidate["artifact_key"]] = counts.get(candidate["artifact_key"], 0) + 1
        return {
            "last_index": next_last,
            "artifact_indices": (*state["artifact_indices"], candidate_index),
            "artifact_keys": (*state["artifact_keys"], candidate["artifact_key"]),
            "artifact_counts": counts,
            "artifact_price": state["artifact_price"] + int(candidate["price"]),
            "stats": add_stats(state["stats"], stats),
            "artifact_infections": add_stats(state["artifact_infections"], infections),
            "container_infections": state["container_infections"],
            "artifact_details": (
                *state["artifact_details"],
                {
                    "artifact_key": candidate["artifact_key"],
                    "item_id": candidate["item_id"],
                    "name": candidate["name"],
                    "quality_tier": candidate["quality_tier"],
                    "quality_percent": candidate["quality_percent"],
                    "upgrade_level": candidate["upgrade_level"],
                    "stat_upgrade_level": candidate["stat_upgrade_level"],
                    "price_upgrade_level": candidate["price_upgrade_level"],
                    "price": candidate["price"],
                    "price_basis": candidate["price_basis"],
                    "liquidity_score": candidate["liquidity_score"],
                    "confidence_score": candidate["confidence_score"],
                    "stats": stats,
                    "infections": infections,
                },
            ),
        }

    def _attach_upgrade_potential(self, container_entries: list[dict[str, Any]]) -> None:
        usage: dict[str, int] = {}
        total_builds = 0
        max_total_effectiveness = 1.0
        for entry in container_entries:
            container = entry["container"]
            max_total_effectiveness = max(
                max_total_effectiveness,
                float(container["effectiveness"]) * float(container["capacity"]),
            )
            for build in entry["builds"]:
                total_builds += 1
                for artifact in build["artifacts"]:
                    usage[artifact["artifact_key"]] = usage.get(artifact["artifact_key"], 0) + 1

        total_builds = max(total_builds, 1)
        for entry in container_entries:
            container = entry["container"]
            container_headroom = 1.0 - (
                float(container["effectiveness"]) * float(container["capacity"]) / max_total_effectiveness
            )
            for build in entry["builds"]:
                artifacts = build["artifacts"]
                avg_liquidity = sum(float(artifact.get("liquidity_score") or 0.0) for artifact in artifacts) / max(len(artifacts), 1)
                avg_confidence = sum(float(artifact.get("confidence_score") or 0.0) for artifact in artifacts) / max(len(artifacts), 1)
                avg_reuse = sum(usage.get(artifact["artifact_key"], 0) / total_builds for artifact in artifacts) / max(len(artifacts), 1)
                build["upgrade_potential"] = round(
                    min(100.0, avg_liquidity * 0.35 + avg_confidence * 0.25 + avg_reuse * 100.0 * 0.25 + container_headroom * 100.0 * 0.15),
                    3,
                )
