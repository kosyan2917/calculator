from __future__ import annotations

from dataclasses import dataclass, field
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
    "radiation_dmg_factor": "radiation_resistance",
    "psycho_dmg_factor": "psycho_resistance",
    "thermal_dmg_factor": "temperature_resistance",
    "biological_dmg_factor": "biological_resistance",
    "frost_dmg_factor": "frost_resistance",
    "bleeding_dmg_factor": "bleeding_damage_resistance",
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
    "toxic_accumulation": "toxicity",
    "combustion_accumulation": "combustion",
    "reaction_to_chemical_burn": "chemical_burn_reaction",
    "reaction_to_electroshock": "electroshock_reaction",
    "recoil_bonus": "recoil",
    "wiggle_bonus": "wiggle",
    "filler_modifier": "satiety",
}

PRIMARY_STATS = [
    "bullet_resistance",
    "vitality",
    "movement_speed",
    "sprint_speed",
    "stamina_regeneration",
    "health_regeneration",
    "periodic_healing",
    "healing_effectiveness",
    "carry_weight",
]

SECONDARY_STATS = [
    "stamina",
    "bleeding_output",
    "bleeding_resistance",
    "burn_reaction",
    "tear_reaction",
]

INFECTION_STATS = ["radiation", "temperature", "biological", "psycho", "frost"]

QUALITY_BOUNDS = {
    "common": (85.0, 100.0),
    "uncommon": (100.0, 115.0),
    "special": (115.0, 130.0),
    "rare": (130.0, 145.0),
    "exclusive": (145.0, 160.0),
    "legendary": (160.0, 175.0),
}

QUALITY_ORDER = {
    "common": 0,
    "uncommon": 1,
    "special": 2,
    "rare": 3,
    "exclusive": 4,
    "legendary": 5,
}

BASE_INFECTION_OUTPUT = {
    "radiation": -0.5,
    "temperature": -0.5,
    "biological": -0.5,
    "psycho": -0.5,
    "frost": -1.0,
}


@dataclass(frozen=True)
class MechanicsConfig:
    quality_policy: str = "mid"
    apply_container_effectiveness: bool = True
    container_effectiveness_affects_infections: bool = False
    frost_ignores_inner_protection: bool = True
    container_infections_are_protected: bool = False
    base_infection_output: dict[str, float] = field(default_factory=lambda: dict(BASE_INFECTION_OUTPUT))


def add_stats(*stats_items: dict[str, float]) -> dict[str, float]:
    total: dict[str, float] = {}
    for stats in stats_items:
        for key, value in stats.items():
            total[key] = total.get(key, 0.0) + float(value)
    return {key: value for key, value in total.items() if abs(value) > 1e-12}


def derived_stats(stats: dict[str, float]) -> dict[str, float]:
    bullet = stats.get("bullet_resistance", 0.0)
    vitality = stats.get("vitality", 0.0)
    movement = stats.get("movement_speed", 0.0)
    sprint = stats.get("sprint_speed", 0.0)
    regeneration = stats.get("health_regeneration", 0.0)
    periodic_healing = stats.get("periodic_healing", 0.0)
    healing_effectiveness = stats.get("healing_effectiveness", 0.0)
    return {
        "effective_durability": (bullet + 100.0) * (vitality + 100.0),
        "total_sprint_speed": 100.0 + movement + sprint,
        "hp_regen_score": regeneration / 5.0 + periodic_healing * (100.0 + healing_effectiveness),
    }


def quality_value(quality_tier: str, policy: str) -> float:
    low, high = QUALITY_BOUNDS[quality_tier]
    if policy == "min":
        return low
    if policy == "max":
        return high
    if policy == "p75":
        return low + (high - low) * 0.75
    return (low + high) / 2.0


def quality_points(quality_tier: str, step: float) -> list[float]:
    low, high = QUALITY_BOUNDS[quality_tier]
    if step <= 0:
        return [quality_value(quality_tier, "mid")]
    points = {round(low, 4), round(high, 4)}
    current = low
    while current <= high + 1e-9:
        points.add(round(current, 4))
        current += step
    return sorted(points)


def value_at_quality_percent(range_min: float, range_max: float, quality_percent: float) -> float:
    value_at_100 = range_min if abs(range_min) >= abs(range_max) else range_max
    return value_at_100 * quality_percent / 100.0


def value_at_quality(range_min: float, range_max: float, quality_tier: str, policy: str) -> float:
    return value_at_quality_percent(range_min, range_max, quality_value(quality_tier, policy))


def split_infections(stats: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    normal: dict[str, float] = {}
    infections: dict[str, float] = {}
    for key, value in stats.items():
        if key in INFECTION_STATS:
            infections[key] = value
        else:
            normal[key] = value
    return normal, infections


def apply_container_effectiveness(stats: dict[str, float], effectiveness: float, mechanics: MechanicsConfig) -> dict[str, float]:
    if not mechanics.apply_container_effectiveness:
        return dict(stats)
    multiplier = effectiveness / 100.0
    result: dict[str, float] = {}
    for key, value in stats.items():
        if key in INFECTION_STATS and not mechanics.container_effectiveness_affects_infections:
            result[key] = value
        else:
            result[key] = value * multiplier
    return result


def infection_report(
    artifact_infections: dict[str, float],
    container_infections: dict[str, float],
    inner_protection: float,
    mechanics: MechanicsConfig,
) -> dict[str, Any]:
    protection = max(0.0, min(inner_protection, 100.0)) / 100.0
    by_type: dict[str, dict[str, float | bool]] = {}
    valid = True
    for key in INFECTION_STATS:
        raw_artifact = artifact_infections.get(key, 0.0)
        container_value = container_infections.get(key, 0.0)
        protected = key != "frost" or not mechanics.frost_ignores_inner_protection
        if protected:
            artifact_after = raw_artifact * (1.0 - protection)
            container_after = container_value * (1.0 - protection) if mechanics.container_infections_are_protected else container_value
        else:
            artifact_after = raw_artifact
            container_after = container_value
        base_output = mechanics.base_infection_output.get(key, 0.0)
        final = artifact_after + container_after + base_output
        limit = -base_output
        margin = -final
        type_valid = final <= 1e-9
        by_type[key] = {
            "raw_artifact": raw_artifact,
            "container": container_value,
            "after_inner_protection": artifact_after,
            "base_output": base_output,
            "final": final,
            "limit": limit,
            "margin": margin,
            "valid": type_valid,
        }
        valid = valid and type_valid
    return {"valid": valid, "by_type": by_type}


def stat_suffix_to_column(stat_id: str) -> str | None:
    if not stat_id.startswith(STAT_PREFIX):
        return None
    return STAT_COLUMNS.get(stat_id.removeprefix(STAT_PREFIX))


def to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None
