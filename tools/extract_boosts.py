#!/usr/bin/env python3
"""Extract timed consumable boosts from stalzone-database."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artcalc.stat_model import STAT_PREFIX, STAT_COLUMNS, derived_stats


INFO_NUMERIC_COLUMNS = {
    "core.tooltip.info.weight": "weight",
    "stalker.tooltip.medicine.info.priority": "priority",
    "stalker.tooltip.medicine.info.duration": "duration_seconds",
    "stalker.tooltip.medicine.info.cooldown": "cooldown_seconds",
    "stalker.tooltip.medicine.info.toxicity": "toxicity_penalty",
    "stalker.tooltip.medicine.info.hp_regen": "instant_healing",
}

INFO_KEY_VALUE_COLUMNS = {
    "core.tooltip.info.rank": "rank",
    "core.tooltip.info.category": "class_label",
    "stalker.tooltip.medicine.info.effect_type": "effect_type",
}

EXCLUDED_EFFECT_TYPES = {"Вывод", "Лечение"}
BOOST_ROOTS = [
    ("items/supply/medicine", "medicine"),
    ("items/supply/food", "food"),
    ("items/supply/drink", "drink"),
    ("items/medicine", "medicine"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract consumable boost stats from stalzone-database.")
    parser.add_argument("--db-root", default="stalzone-database")
    parser.add_argument("--lang", default="ru")
    parser.add_argument("--output-json", default="data/boosts.json")
    parser.add_argument("--output-csv", default="data/boosts.csv")
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def text_value(node: dict[str, Any] | None, lang: str) -> str:
    if not node:
        return ""
    if node.get("type") == "text":
        return str(node.get("text") or "")
    lines = node.get("lines") or {}
    return str(lines.get(lang) or lines.get("ru") or lines.get("en") or "")


def numeric_value(element: dict[str, Any]) -> float | None:
    value = element.get("value")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def item_paths(db_root: Path, lang: str) -> list[Path]:
    paths: list[Path] = []
    for relative_root, _category in BOOST_ROOTS:
        root = db_root / lang / relative_root
        if root.exists():
            paths.extend(sorted(root.glob("*.json")))
    return sorted(set(paths))


def extract_item(path: Path, lang: str) -> tuple[dict[str, Any] | None, dict[str, str]]:
    item = read_json(path)
    info: dict[str, Any] = {}
    stats: dict[str, float] = {}
    display_names: dict[str, str] = {}
    unmapped_stats: dict[str, float] = {}
    duplicate_stats: dict[str, list[float]] = {}

    for block in item.get("infoBlocks") or []:
        for element in block.get("elements") or []:
            element_type = element.get("type")
            name_node = element.get("name") or {}
            key_node = element.get("key") or {}
            key_id = key_node.get("key")
            stat_id = name_node.get("key") or key_id

            if element_type == "key-value" and key_id in INFO_KEY_VALUE_COLUMNS:
                info[INFO_KEY_VALUE_COLUMNS[key_id]] = text_value(element.get("value") or {}, lang)
                continue

            if element_type != "numeric":
                continue

            value = numeric_value(element)
            if value is None:
                continue

            if stat_id in INFO_NUMERIC_COLUMNS:
                info[INFO_NUMERIC_COLUMNS[stat_id]] = value
                continue

            if stat_id and stat_id.startswith(STAT_PREFIX):
                suffix = stat_id.removeprefix(STAT_PREFIX)
                column = STAT_COLUMNS.get(suffix)
                display_names[stat_id] = text_value(name_node, lang)
                if column:
                    if column in stats:
                        duplicate_stats.setdefault(column, []).append(value)
                    else:
                        stats[column] = value
                else:
                    if stat_id in unmapped_stats:
                        duplicate_stats.setdefault(stat_id, []).append(value)
                    else:
                        unmapped_stats[stat_id] = value

    has_effect_info = bool(info.get("effect_type")) or "duration_seconds" in info or "priority" in info
    if not has_effect_info and not stats:
        return None, display_names

    effect_type = str(info.get("effect_type") or "")
    excluded = not effect_type or effect_type in EXCLUDED_EFFECT_TYPES
    output = {
        "boost_id": item.get("id") or path.stem,
        "name": text_value(item.get("name") or {}, lang),
        "category": item.get("category") or "",
        "rank": info.get("rank") or "",
        "class_label": info.get("class_label") or "",
        "color": item.get("color") or "",
        "status": (item.get("status") or {}).get("state") or "",
        "effect_type": effect_type,
        "priority": info.get("priority"),
        "duration_seconds": info.get("duration_seconds"),
        "cooldown_seconds": info.get("cooldown_seconds"),
        "toxicity_penalty": info.get("toxicity_penalty"),
        "instant_healing": info.get("instant_healing"),
        "stats": stats,
        "derived": derived_stats(stats),
        "unmapped_stats": unmapped_stats,
        "duplicate_stats": duplicate_stats,
        "excluded_from_builds": excluded,
        "exclude_reason": "effect_type" if excluded else "",
        "source_path": str(path.as_posix()),
    }
    return output, display_names


def write_csv(path: Path, boosts: list[dict[str, Any]]) -> None:
    stat_fields = list(STAT_COLUMNS.values())
    derived_fields = ["effective_durability", "total_sprint_speed", "hp_regen_score"]
    fields = [
        "boost_id",
        "name",
        "category",
        "rank",
        "class_label",
        "effect_type",
        "priority",
        "duration_seconds",
        "cooldown_seconds",
        "toxicity_penalty",
        "instant_healing",
        "excluded_from_builds",
        "exclude_reason",
        *stat_fields,
        *derived_fields,
        "source_path",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for boost in boosts:
            row = {field: boost.get(field) for field in fields}
            row.update(boost.get("stats") or {})
            row.update(boost.get("derived") or {})
            writer.writerow(row)


def main() -> int:
    args = parse_args()
    db_root = Path(args.db_root)
    boosts: list[dict[str, Any]] = []
    factor_names: dict[str, str] = {}

    for path in item_paths(db_root, args.lang):
        boost, names = extract_item(path, args.lang)
        factor_names.update(names)
        if boost:
            boosts.append(boost)

    boosts.sort(key=lambda item: (str(item["effect_type"]), str(item["name"]), str(item["boost_id"])))
    counts_by_effect_type: dict[str, int] = {}
    for boost in boosts:
        key = str(boost.get("effect_type") or "")
        counts_by_effect_type[key] = counts_by_effect_type.get(key, 0) + 1

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_roots": [str((db_root / args.lang / root).as_posix()) for root, _category in BOOST_ROOTS],
        "rules": {
            "one_active_consumable_per_effect_type": True,
            "excluded_effect_types_for_builds": sorted(EXCLUDED_EFFECT_TYPES),
            "exclusion_note": "Output and healing consumables are exported but not used by boosted build queries.",
        },
        "counts": {
            "items": len(boosts),
            "usable_in_builds": sum(1 for boost in boosts if not boost["excluded_from_builds"]),
            "by_effect_type": dict(sorted(counts_by_effect_type.items())),
        },
        "stat_columns": STAT_COLUMNS,
        "factor_names_ru": dict(sorted(factor_names.items())),
        "boosts": boosts,
    }

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(Path(args.output_csv), boosts)
    print(
        f"Wrote {output_json} and {args.output_csv}: "
        f"{len(boosts)} consumables, {payload['counts']['usable_in_builds']} usable in boosted builds."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
