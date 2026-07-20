from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .stat_model import (
    INFECTION_STATS,
    PRIMARY_STATS,
    QUALITY_ORDER,
    STAT_PREFIX,
    MechanicsConfig,
    apply_container_effectiveness,
    artifact_level_multiplier,
    artifact_value_at_quality_percent,
    quality_points,
    quality_value,
    split_infections,
    stat_suffix_to_column,
    to_float,
    value_at_quality,
    value_at_quality_percent,
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def text_value(node: dict[str, Any] | None, lang: str) -> str:
    if not node:
        return ""
    if node.get("type") == "text":
        return str(node.get("text") or "")
    lines = node.get("lines") or {}
    return str(lines.get(lang) or lines.get("ru") or lines.get("en") or "")


def extract_stat_values(
    item: dict[str, Any],
    lang: str,
    quality_tier: str | None,
    quality_policy: str,
    quality_percent: float | None = None,
) -> dict[str, float]:
    stats: dict[str, float] = {}
    for block in item.get("infoBlocks") or []:
        for element in block.get("elements") or []:
            name_node = element.get("name") or {}
            stat_id = name_node.get("key") or (element.get("key") or {}).get("key")
            if not stat_id or not stat_id.startswith(STAT_PREFIX):
                continue
            column = stat_suffix_to_column(stat_id)
            if not column:
                continue
            if element.get("type") == "range" and quality_tier:
                range_min = to_float(element.get("min"))
                range_max = to_float(element.get("max"))
                if range_min is not None and range_max is not None:
                    if quality_percent is None:
                        stats[column] = value_at_quality(range_min, range_max, quality_tier, quality_policy)
                    else:
                        stats[column] = artifact_value_at_quality_percent(column, range_min, range_max, quality_percent)
            elif element.get("type") == "numeric":
                value = to_float(element.get("value"))
                if value is not None and column not in stats:
                    stats[column] = value
    return stats


def extract_container(path: Path, lang: str) -> dict[str, Any]:
    item = read_json(path)
    result = {
        "container_id": item.get("id") or path.stem,
        "name": text_value(item.get("name") or {}, lang),
        "rank": "",
        "category": item.get("category") or "",
        "color": item.get("color") or "",
        "status": (item.get("status") or {}).get("state") or "",
        "weight": 0.0,
        "inner_protection": 0.0,
        "effectiveness": 100.0,
        "capacity": 0,
        "stats": {},
        "source_path": str(path.as_posix()),
    }
    stats: dict[str, float] = {}
    for block in item.get("infoBlocks") or []:
        for element in block.get("elements") or []:
            key_node = element.get("key") or {}
            name_node = element.get("name") or {}
            stat_id = name_node.get("key") or key_node.get("key")
            if element.get("type") == "key-value":
                if key_node.get("key") == "core.tooltip.info.rank":
                    result["rank"] = text_value(element.get("value") or {}, lang)
                continue
            if element.get("type") != "numeric":
                continue
            value = to_float(element.get("value"))
            if value is None:
                continue
            if stat_id == "core.tooltip.info.weight":
                result["weight"] = value
            elif stat_id == "stalker.tooltip.backpack.stat_name.inner_protection":
                result["inner_protection"] = value
            elif stat_id == "stalker.tooltip.backpack.stat_name.effectiveness":
                result["effectiveness"] = value
            elif stat_id == "stalker.tooltip.backpack.info.size":
                result["capacity"] = int(round(value))
            else:
                column = stat_suffix_to_column(stat_id or "")
                if column:
                    stats[column] = value
    result["stats"] = stats
    return result


def load_containers(db_root: Path, lang: str, ranks: set[str]) -> list[dict[str, Any]]:
    root = db_root / lang / "items" / "containers"
    containers = [extract_container(path, lang) for path in sorted(root.glob("*.json"))]
    return [container for container in containers if container["rank"] in ranks and container["capacity"] > 0]


def load_artifact_price_segments(price_path: Path, upgrade_level: int) -> dict[tuple[str, str], dict[str, Any]]:
    data = read_json(price_path)
    segments: dict[tuple[str, str], dict[str, Any]] = {}
    for segment in data.get("segments") or []:
        segment_upgrade_level = segment.get("upgrade_level")
        if segment_upgrade_level is None or int(segment_upgrade_level) != upgrade_level:
            continue
        price = segment.get("price")
        if price is None:
            continue
        tier = segment.get("quality_tier")
        item_id = segment.get("item_id")
        if item_id and tier:
            segments[(str(item_id), str(tier))] = segment
    return segments


def load_artifact_additional_properties(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None or not path.exists():
        return {}
    data = read_json(path)
    items = data.get("items") if isinstance(data, dict) else {}
    if not isinstance(items, dict):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for item_id, properties in items.items():
        if isinstance(properties, list):
            result[str(item_id)] = [property_item for property_item in properties if isinstance(property_item, dict)]
    return result


def load_artifact_candidates(
    db_root: Path,
    price_path: Path,
    lang: str,
    upgrade_level: int,
    mechanics: MechanicsConfig,
    allowed_quality_tiers: set[str] | None = None,
    price_upgrade_level: int | None = None,
    quality_strategy: str = "single",
    quality_step: float = 2.5,
    min_quality_percent: float | None = None,
    additional_properties_path: Path | None = Path("data/artifact_additional_properties.json"),
) -> list[dict[str, Any]]:
    price_level = upgrade_level if price_upgrade_level is None else price_upgrade_level
    price_segments = load_artifact_price_segments(price_path, price_level)
    additional_properties = load_artifact_additional_properties(additional_properties_path)
    artifact_root = db_root / lang / "items" / "artefact"
    candidates: list[dict[str, Any]] = []
    for variant_path in sorted(artifact_root.rglob(f"_variants/*/{upgrade_level}.json")):
        item = read_json(variant_path)
        item_id = item.get("id") or variant_path.parent.name
        for (priced_id, quality_tier), price_info in price_segments.items():
            if priced_id != item_id:
                continue
            if allowed_quality_tiers and quality_tier not in allowed_quality_tiers:
                continue
            segment_candidates = _artifact_quality_candidates(
                item,
                item_id,
                variant_path,
                price_info,
                lang,
                quality_tier,
                upgrade_level,
                price_level,
                mechanics,
                quality_strategy,
                quality_step,
                min_quality_percent,
                additional_properties.get(str(item_id), []),
            )
            candidates.extend(segment_candidates)
    candidates.sort(key=lambda item: (item["item_id"], item["quality_order"], item["quality_percent"], item["price"]))
    return candidates


def _artifact_quality_candidates(
    item: dict[str, Any],
    item_id: str,
    variant_path: Path,
    price_info: dict[str, Any],
    lang: str,
    quality_tier: str,
    upgrade_level: int,
    price_level: int,
    mechanics: MechanicsConfig,
    quality_strategy: str,
    quality_step: float,
    min_quality_percent: float | None,
    additional_properties: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if quality_strategy == "single":
        percents = [quality_value(quality_tier, mechanics.quality_policy)]
    else:
        percents = quality_points(quality_tier, quality_step)
    if min_quality_percent is not None:
        percents = [percent for percent in percents if percent >= min_quality_percent]
    if not percents:
        return []

    candidates: list[dict[str, Any]] = []
    for quality_percent in percents:
        stats = extract_stat_values(item, lang, quality_tier, mechanics.quality_policy, quality_percent)
        extra_stats = additional_property_stats(additional_properties, upgrade_level, quality_percent)
        stats = {**stats, **{key: stats.get(key, 0.0) + value for key, value in extra_stats.items()}}
        normal_stats, infections = split_infections(stats)
        candidate = {
                "artifact_key": (
                    f"{item_id}:{quality_tier}:q{quality_percent:g}:"
                    f"stats+{upgrade_level}:price+{price_level}"
                ),
                "item_id": item_id,
                "name": text_value(item.get("name") or {}, lang),
                "category": item.get("category") or "",
                "quality_tier": quality_tier,
                "quality_percent": quality_percent,
                "quality_raw": price_info.get("quality_raw"),
                "quality_order": QUALITY_ORDER.get(quality_tier, 999),
                "upgrade_level": upgrade_level,
                "stat_upgrade_level": upgrade_level,
                "price_upgrade_level": price_level,
                "price": int(price_info.get("price") or 0),
                "price_basis": price_info.get("price_basis"),
                "price_confidence": price_info.get("local_confidence"),
                "sales_7d": int(price_info.get("sales_7d") or 0),
                "sales_count": int(price_info.get("sales_count") or 0),
                "confidence_score": float(price_info.get("confidence_score") or 0.0),
                "liquidity_score": float(price_info.get("liquidity_score") or 0.0),
                "stats": normal_stats,
                "infections": infections,
                "additional_properties": additional_properties,
                "source_path": str(variant_path.as_posix()),
        }
        candidates.append(candidate)

    if quality_strategy == "adaptive_grid":
        return _quality_frontier(candidates)
    return candidates


def additional_property_stats(
    additional_properties: list[dict[str, Any]],
    upgrade_level: int,
    quality_percent: float,
) -> dict[str, float]:
    multiplier = artifact_level_multiplier(upgrade_level)
    stats: dict[str, float] = {}
    for property_item in additional_properties:
        column = str(property_item.get("column") or "")
        if not column:
            stat_id = str(property_item.get("stat_id") or "")
            column = stat_suffix_to_column(stat_id) or ""
        if not column:
            suffix = str(property_item.get("stat_suffix") or "")
            column = stat_suffix_to_column(f"{STAT_PREFIX}{suffix}") or ""
        if not column:
            continue
        range_min = to_float(property_item.get("min"))
        range_max = to_float(property_item.get("max"))
        if range_min is None or range_max is None:
            continue
        stats[column] = (
            stats.get(column, 0.0)
            + artifact_value_at_quality_percent(column, range_min, range_max, quality_percent) * multiplier
        )
    return stats


def _quality_dominates(a: dict[str, Any], b: dict[str, Any]) -> bool:
    better = False
    a_stats = a.get("stats") or {}
    b_stats = b.get("stats") or {}
    a_infections = a.get("infections") or {}
    b_infections = b.get("infections") or {}

    for key in PRIMARY_STATS:
        if a_stats.get(key, 0.0) + 1e-9 < b_stats.get(key, 0.0):
            return False
        better = better or a_stats.get(key, 0.0) > b_stats.get(key, 0.0) + 1e-9
    for key in INFECTION_STATS:
        if a_infections.get(key, 0.0) > b_infections.get(key, 0.0) + 1e-9:
            return False
        better = better or a_infections.get(key, 0.0) < b_infections.get(key, 0.0) - 1e-9
    return better


def _quality_frontier(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frontier: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: -float(item["quality_percent"])):
        if any(_quality_dominates(existing, candidate) for existing in frontier):
            continue
        frontier = [existing for existing in frontier if not _quality_dominates(candidate, existing)]
        frontier.append(candidate)
    return sorted(frontier, key=lambda item: float(item["quality_percent"]))


def effective_artifact_stats(candidate: dict[str, Any], container: dict[str, Any], mechanics: MechanicsConfig) -> tuple[dict[str, float], dict[str, float]]:
    stats = {**candidate.get("stats", {}), **candidate.get("infections", {})}
    adjusted = apply_container_effectiveness(stats, float(container["effectiveness"]), mechanics)
    return split_infections(adjusted)


def load_armor_items(armor_path: Path, ranks: set[str], upgrade_level: int | None = 15) -> list[dict[str, Any]]:
    data = read_json(armor_path)
    items = []
    for item in data.get("items") or []:
        if item.get("rank") not in ranks:
            continue
        if upgrade_level is not None and int(item.get("upgrade_level") or 0) != upgrade_level:
            continue
        items.append(item)
    return items


def infection_signature(infections: dict[str, float]) -> tuple[float, ...]:
    return tuple(round(infections.get(key, 0.0), 4) for key in INFECTION_STATS)
