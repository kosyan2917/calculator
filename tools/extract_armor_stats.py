#!/usr/bin/env python3
"""Extract armor stats and upgrade variants from stalzone-database."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STAT_PREFIX = "stalker.artefact_properties.factor."

STAT_COLUMNS = {
    "bullet_dmg_factor": "bullet_resistance",
    "health_bonus": "vitality",
    "speed_modifier": "movement_speed",
    "sprint_speed_modifier": "sprint_speed",
    "stamina_bonus": "stamina",
    "stamina_regeneration_bonus": "stamina_regeneration",
    "regeneration_bonus": "health_regeneration",
    "artefakt_heal": "periodic_healing",
    "heal_efficiency": "healing_effectiveness",
    "max_weight_bonus": "carry_weight",
    "bleeding_accumulation": "bleeding_output",
    "bleeding_protection": "bleeding_resistance",
    "reaction_to_burn": "burn_reaction",
    "reaction_to_tear": "tear_reaction",
    "tear_dmg_factor": "tear_protection",
    "explosion_dmg_factor": "explosion_protection",
    "electra_dmg_factor": "electricity_protection",
    "burn_dmg_factor": "fire_protection",
    "chemical_burn_dmg_factor": "chemical_protection",
    "radiation_protection": "radiation_protection",
    "thermal_protection": "thermal_protection",
    "biological_protection": "biological_protection",
    "psycho_protection": "psycho_protection",
    "frost_protection": "frost_protection",
    "stopping_protection": "stability",
    "radiation_accumulation": "radiation",
    "psycho_accumulation": "psycho",
    "thermal_accumulation": "temperature",
    "biological_accumulation": "biological",
    "frost_accumulation": "frost",
    "combustion_accumulation": "combustion",
    "reaction_to_chemical_burn": "chemical_burn_reaction",
    "reaction_to_electroshock": "electroshock_reaction",
    "recoil_bonus": "recoil",
    "wiggle_bonus": "wiggle",
}

INFO_COLUMNS = {
    "core.tooltip.info.weight": "weight",
    "core.tooltip.info.durability": "durability",
    "core.tooltip.info.max_durability": "max_durability",
}

KEY_VALUE_COLUMNS = {
    "core.tooltip.info.rank": "rank",
    "core.tooltip.info.category": "class_label",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract armor bonuses from stalzone-database.")
    parser.add_argument("--db-root", default="stalzone-database")
    parser.add_argument("--lang", default="ru")
    parser.add_argument("--output-json", default="data/armor_stats.json")
    parser.add_argument("--output-csv", default="data/armor_stats.csv")
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


def variant_info(path: Path) -> tuple[bool, str, int]:
    parts = path.parts
    if "_variants" not in parts:
        item_id = path.stem
        return False, item_id, 0
    index = parts.index("_variants")
    base_id = parts[index + 1]
    return True, base_id, int(path.stem)


def extract_item(path: Path, lang: str) -> tuple[dict[str, Any], dict[str, str]]:
    item = read_json(path)
    is_variant, base_id, upgrade_level = variant_info(path)
    info: dict[str, Any] = {}
    stats: dict[str, float] = {}
    display_names: dict[str, str] = {}
    unmapped_stats: dict[str, float] = {}
    duplicate_stats: dict[str, list[float]] = {}

    for block in item.get("infoBlocks") or []:
        for element in block.get("elements") or []:
            name_node = element.get("name") or {}
            key_node = element.get("key") or {}
            stat_id = name_node.get("key") or key_node.get("key")

            if element.get("type") == "numeric":
                value = numeric_value(element)
                if value is None:
                    continue
                if stat_id in INFO_COLUMNS:
                    info[INFO_COLUMNS[stat_id]] = value
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
                    continue

            if element.get("type") == "key-value" and stat_id in KEY_VALUE_COLUMNS:
                info[KEY_VALUE_COLUMNS[stat_id]] = text_value(element.get("value") or {}, lang)

    bullet = stats.get("bullet_resistance", 0.0)
    vitality = stats.get("vitality", 0.0)
    movement = stats.get("movement_speed", 0.0)
    sprint = stats.get("sprint_speed", 0.0)
    regeneration = stats.get("health_regeneration", 0.0)
    periodic_healing = stats.get("periodic_healing", 0.0)
    healing_effectiveness = stats.get("healing_effectiveness", 0.0)
    derived = {
        "effective_durability": (bullet + 100.0) * (vitality + 100.0) / 100.0,
        "total_sprint_speed": 100.0 + movement + sprint,
        "hp_regen_score": 0.5 + regeneration / 5.0 + periodic_healing * (1.0 + healing_effectiveness / 100.0),
    }

    output = {
        "item_id": item.get("id") or path.stem,
        "base_id": base_id,
        "upgrade_level": upgrade_level,
        "is_variant": is_variant,
        "name": text_value(item.get("name") or {}, lang),
        "category": item.get("category") or "",
        "rank": info.get("rank") or "",
        "class_label": info.get("class_label") or "",
        "color": item.get("color") or "",
        "status": (item.get("status") or {}).get("state") or "",
        "weight": info.get("weight"),
        "durability": info.get("durability"),
        "max_durability": info.get("max_durability"),
        "stats": stats,
        "derived": derived,
        "unmapped_stats": unmapped_stats,
        "duplicate_stats": duplicate_stats,
        "source_path": str(path.as_posix()),
    }
    return output, display_names


def sort_key(item: dict[str, Any]) -> tuple[str, int]:
    return str(item["base_id"]), int(item["upgrade_level"])


def write_csv(path: Path, items: list[dict[str, Any]]) -> None:
    stat_fields = list(STAT_COLUMNS.values())
    derived_fields = ["effective_durability", "total_sprint_speed", "hp_regen_score"]
    fields = [
        "item_id",
        "base_id",
        "upgrade_level",
        "is_variant",
        "name",
        "category",
        "rank",
        "class_label",
        "color",
        "status",
        "weight",
        "durability",
        "max_durability",
        *stat_fields,
        *derived_fields,
        "source_path",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            row = {field: item.get(field) for field in fields}
            row.update(item.get("stats") or {})
            row.update(item.get("derived") or {})
            writer.writerow(row)


def main() -> int:
    args = parse_args()
    armor_root = Path(args.db_root) / args.lang / "items" / "armor"
    if not armor_root.exists():
        raise FileNotFoundError(f"Armor directory not found: {armor_root}")

    items: list[dict[str, Any]] = []
    factor_names: dict[str, str] = {}
    for path in sorted(armor_root.rglob("*.json")):
        item, names = extract_item(path, args.lang)
        items.append(item)
        factor_names.update(names)

    items.sort(key=sort_key)
    base_items = [item for item in items if not item["is_variant"]]
    variant_items = [item for item in items if item["is_variant"]]
    ranks: dict[str, int] = {}
    for item in base_items:
        ranks[item["rank"]] = ranks.get(item["rank"], 0) + 1

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(armor_root.as_posix()),
        "counts": {
            "items": len(items),
            "base_items": len(base_items),
            "variant_items": len(variant_items),
            "base_items_by_rank": dict(sorted(ranks.items())),
        },
        "stat_columns": STAT_COLUMNS,
        "factor_names_ru": dict(sorted(factor_names.items())),
        "derived_formulas": {
            "effective_durability": "(bullet_resistance + 100) * (vitality + 100) / 100",
            "total_sprint_speed": "100 + movement_speed + sprint_speed",
            "hp_regen_score": "0.5 + health_regeneration / 5 + periodic_healing * (1 + healing_effectiveness / 100)",
        },
        "items": items,
    }

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(Path(args.output_csv), items)
    print(
        f"Wrote {output_json} and {args.output_csv}: "
        f"{len(items)} armor rows ({len(base_items)} base, {len(variant_items)} variants)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
