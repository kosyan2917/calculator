from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from combat_simulator import (
    AccuracyTier,
    AmmunitionProfile,
    ShootingSimulator,
    TargetProfile,
    WeaponProfile,
)


DATABASE = ROOT / "stalzone-database" / "global" / "items"
WEAPON_CATEGORIES = (
    "assault_rifle",
    "machine_gun",
    "sniper_rifle",
    "submachine_gun",
)
DISTANCES_M = (10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 60.0, 80.0)
MINIMUM_RECOMMENDATION_EHP = 300
NEAR_BEST_SCORE_RATIO = 0.98

# The strongest regular PvP cartridge requested by the user for each caliber.
# The two calibers without AP/incendiary/SBP use their specialized ammunition.
AMMUNITION_BY_CALIBER: dict[str, tuple[str, str]] = {
    "item.wpn.display_ammo_types.9mm": ("52l0", "9 mm SBP"),
    "item.wpn.display_ammo_types.545mm": ("vdjd", "5.45 mm SBP"),
    "item.wpn.display_ammo_types.556mm": ("63oy", "5.56 mm SBP"),
    "item.wpn.display_ammo_types.762mm": ("y94o", "7.62 mm SBP"),
    "item.wpn.display_ammo_types.939mm": ("kkrv", "9x39 mm SBP"),
    "item.wpn.display_ammo_types.127mm": ("79w7", "12.7 mm specialized"),
    "item.wpn.ptrd.display_ammo_types": ("qm0k", "PTRD standard"),
}
AMMUNITION_BY_CATEGORY_CALIBER: dict[tuple[str, str], tuple[str, str]] = {
    (
        "sniper_rifle",
        "item.wpn.display_ammo_types.127mm",
    ): ("63yy", "12.7 mm sniper"),
}
RELOAD_OVERRIDES_SECONDS = {
    # The current official export omits Karbach's per-round reload field.
    "rw26v": 5.0,
}


@dataclass(frozen=True)
class LoadedWeapon:
    item_id: str
    name: str
    category: str
    caliber: str
    ammunition_name: str
    ammunition: AmmunitionProfile
    profile: WeaponProfile

    def functional_signature(self) -> tuple[Any, ...]:
        return (
            self.category,
            self.caliber,
            *asdict(self.profile).values(),
            *asdict(self.ammunition).values(),
        )


@dataclass(frozen=True)
class FunctionalWeapon:
    names: tuple[str, ...]
    item_ids: tuple[str, ...]
    category: str
    caliber: str
    ammunition_name: str
    ammunition: AmmunitionProfile
    profile: WeaponProfile


def walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def translation_key(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("key"), str):
        return value["key"]
    return None


def numeric_stats(item: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for node in walk(item):
        if node.get("type") != "numeric" or not isinstance(node.get("value"), (int, float)):
            continue
        key = translation_key(node.get("name"))
        if key:
            result[key] = float(node["value"])
    return result


def find_key_value(item: dict[str, Any], key: str) -> str:
    for node in walk(item):
        if node.get("type") != "key-value":
            continue
        if translation_key(node.get("key")) == key:
            value_key = translation_key(node.get("value"))
            if value_key:
                return value_key
    raise ValueError(f"Missing key-value field {key}")


def find_damage(item: dict[str, Any]) -> dict[str, float]:
    for node in walk(item):
        if node.get("type") == "damage":
            return {
                "startDamage": float(node["startDamage"]),
                "damageDecreaseStart": float(node["damageDecreaseStart"]),
                "endDamage": float(node["endDamage"]),
                "damageDecreaseEnd": float(node["damageDecreaseEnd"]),
            }
    raise ValueError("Missing weapon damage block")


def find_modifier(item: dict[str, Any], key: str, default: float) -> float:
    for node in walk(item):
        text = node.get("text")
        if translation_key(text) == key:
            return float(text.get("args", {}).get("modifier", default))
    return default


def load_ammunition(item_id: str) -> AmmunitionProfile:
    item = json.loads((DATABASE / "bullet" / f"{item_id}.json").read_text(encoding="utf-8"))
    stats = numeric_stats(item)
    return AmmunitionProfile(
        armor_penetration_percent=stats.get(
            "weapon.tooltip.bullet.stat_name.piercing",
            0.0,
        ),
        damage_modifier_percent=stats.get(
            "weapon.tooltip.bullet.stat_name.damage",
            0.0,
        ),
    )


def load_master_weapons() -> list[LoadedWeapon]:
    result: list[LoadedWeapon] = []
    ammo_cache: dict[str, AmmunitionProfile] = {}
    for category in WEAPON_CATEGORIES:
        category_path = DATABASE / "weapon" / category
        for source in sorted(category_path.glob("*.json")):
            item_id = source.stem
            variant = category_path / "_variants" / item_id / "15.json"
            if not variant.exists():
                continue
            item = json.loads(variant.read_text(encoding="utf-8"))
            if item.get("color") != "RANK_MASTER":
                continue

            caliber = find_key_value(item, "weapon.tooltip.weapon.info.ammo_type")
            ammo_choice = AMMUNITION_BY_CATEGORY_CALIBER.get(
                (category, caliber),
                AMMUNITION_BY_CALIBER.get(caliber),
            )
            if ammo_choice is None:
                raise ValueError(f"No ammunition configured for {caliber} ({item_id})")
            ammo_id, ammo_name = ammo_choice
            ammunition = ammo_cache.setdefault(ammo_id, load_ammunition(ammo_id))
            stats = numeric_stats(item)
            damage = find_damage(item)
            reload_seconds = stats.get(
                "weapon.tooltip.magazine.info.reload_time",
                RELOAD_OVERRIDES_SECONDS.get(item_id),
            )
            if reload_seconds is None:
                raise ValueError(f"Missing reload time for {item_id} ({item['name']['lines']['en']})")
            result.append(
                LoadedWeapon(
                    item_id=item_id,
                    name=item["name"]["lines"]["en"],
                    category=category,
                    caliber=caliber,
                    ammunition_name=ammo_name,
                    ammunition=ammunition,
                    profile=WeaponProfile(
                        close_damage=damage["startDamage"],
                        minimum_damage=damage["endDamage"],
                        damage_falloff_start_m=damage["damageDecreaseStart"],
                        damage_falloff_end_m=damage["damageDecreaseEnd"],
                        # Single-shot PTRD profiles expose only their reload
                        # time. RPM is unused for a one-round magazine.
                        rounds_per_minute=stats.get(
                            "weapon.tooltip.weapon.info.rate_of_fire",
                            60.0,
                        ),
                        magazine_capacity=round(
                            stats["weapon.tooltip.weapon.info.clip_size"]
                        ),
                        reload_seconds=reload_seconds,
                        headshot_multiplier=find_modifier(
                            item,
                            "weapon.tooltip.weapon.head_damage_modifier",
                            1.0,
                        ),
                        limb_multiplier=find_modifier(
                            item,
                            "weapon.tooltip.weapon.limbs_damage_modifier",
                            1.0,
                        ),
                    ),
                )
            )
    return result


def group_functional_weapons(weapons: Iterable[LoadedWeapon]) -> list[FunctionalWeapon]:
    groups: dict[tuple[Any, ...], list[LoadedWeapon]] = defaultdict(list)
    for weapon in weapons:
        groups[weapon.functional_signature()].append(weapon)

    result: list[FunctionalWeapon] = []
    for group in groups.values():
        first = group[0]
        result.append(
            FunctionalWeapon(
                names=tuple(sorted(weapon.name for weapon in group)),
                item_ids=tuple(sorted(weapon.item_id for weapon in group)),
                category=first.category,
                caliber=first.caliber,
                ammunition_name=first.ammunition_name,
                ammunition=first.ammunition,
                profile=first.profile,
            )
        )
    return sorted(result, key=lambda weapon: weapon.names)


def target_for_displayed_ehp(displayed_ehp: int, bullet_resistance_cap: float) -> TargetProfile:
    """Map displayed EHP to a reproducible BR-first, then vitality progression."""
    if displayed_ehp < 100:
        raise ValueError("Displayed EHP cannot be below the 100-point baseline")
    bullet_resistance = min(displayed_ehp - 100.0, bullet_resistance_cap)
    vitality = displayed_ehp / (100.0 + bullet_resistance) * 100.0 - 100.0
    return TargetProfile(
        bullet_resistance=bullet_resistance,
        vitality_percent=vitality,
    )


def calculate_hits(
    weapons: list[FunctionalWeapon],
    *,
    distance_m: float,
    displayed_ehp: int,
    bullet_resistance_cap: float,
    accuracy_tier: AccuracyTier,
) -> list[int]:
    target = target_for_displayed_ehp(displayed_ehp, bullet_resistance_cap)
    simulator = ShootingSimulator()
    return [
        simulator.calculate(
            weapon=weapon.profile,
            ammunition=weapon.ammunition,
            target=target,
            distance_m=distance_m,
            accuracy_tier=accuracy_tier,
        ).hits_to_kill
        for weapon in weapons
    ]


def candidate_metrics(
    hits: dict[int, list[int]],
    displayed_ehp: int,
    *,
    lookback: int,
    plateau: int,
) -> dict[str, float]:
    now = hits[displayed_ehp]
    before = hits[displayed_ehp - lookback]
    after = hits[displayed_ehp + plateau]
    gained = [
        index
        for index, (old, current) in enumerate(zip(before, now))
        if current > old
    ]
    stable = [
        index
        for index, (current, future) in enumerate(zip(now, after))
        if current == future
    ]
    settled = [
        index
        for index, (old, current, future) in enumerate(zip(before, now, after))
        if current > old and current == future
    ]

    next_gaps: list[int] = []
    for index, current in enumerate(now):
        next_change = next(
            (
                future_ehp
                for future_ehp in range(displayed_ehp + 1, max(hits) + 1)
                if hits[future_ehp][index] > current
            ),
            max(hits) + 1,
        )
        next_gaps.append(next_change - displayed_ehp)

    count = len(now)
    gained_share = len(gained) / count
    stable_share = len(stable) / count
    settled_share = len(settled) / count
    median_next_gap = statistics.median(next_gaps)
    # A good stopping point has just crossed many thresholds and is followed
    # by a broad plateau. The median gap rewards plateaus without allowing one
    # extreme weapon to dominate the recommendation.
    score = settled_share * median_next_gap
    return {
        "gained_share": gained_share,
        "stable_share": stable_share,
        "settled_share": settled_share,
        "median_next_gap": median_next_gap,
        "score": score,
    }


def analyze_distance(
    weapons: list[FunctionalWeapon],
    *,
    distance_m: float,
    bullet_resistance_cap: float,
    lookback: int,
    plateau: int,
    accuracy_tier: AccuracyTier,
) -> dict[str, Any]:
    hits = {
        displayed_ehp: calculate_hits(
            weapons,
            distance_m=distance_m,
            displayed_ehp=displayed_ehp,
            bullet_resistance_cap=bullet_resistance_cap,
            accuracy_tier=accuracy_tier,
        )
        for displayed_ehp in range(100, 651)
    }
    candidates = [
        (
            displayed_ehp,
            candidate_metrics(
                hits,
                displayed_ehp,
                lookback=lookback,
                plateau=plateau,
            ),
        )
        for displayed_ehp in range(MINIMUM_RECOMMENDATION_EHP, 501)
    ]
    candidates.sort(
        key=lambda candidate: (
            candidate[1]["score"],
            candidate[1]["gained_share"],
            candidate[0],
        ),
        reverse=True,
    )
    best_score = candidates[0][1]["score"]
    near_best = [
        candidate
        for candidate in candidates
        if candidate[1]["score"] >= best_score * NEAR_BEST_SCORE_RATIO
    ]
    raw_selected_ehp, _ = min(near_best, key=lambda candidate: candidate[0])
    selected_ehp = math.ceil(raw_selected_ehp / 5) * 5
    metrics = candidate_metrics(
        hits,
        selected_ehp,
        lookback=lookback,
        plateau=plateau,
    )
    target = target_for_displayed_ehp(selected_ehp, bullet_resistance_cap)

    # These profiles did not gain a bullet in the cluster that motivated the
    # recommendation, so this particular EHP breakpoint does not help them.
    selected = hits[selected_ehp]
    before = hits[selected_ehp - lookback]
    simulator = ShootingSimulator()
    unaffected: list[dict[str, Any]] = []
    for index, weapon in enumerate(weapons):
        if selected[index] != before[index]:
            continue
        result = simulator.calculate(
            weapon=weapon.profile,
            ammunition=weapon.ammunition,
            target=target,
            distance_m=distance_m,
            accuracy_tier=accuracy_tier,
        )
        next_breakpoint = next(
            (
                future_ehp
                for future_ehp in range(selected_ehp + 1, max(hits) + 1)
                if hits[future_ehp][index] > selected[index]
            ),
            None,
        )
        unaffected.append(
            {
                "names": weapon.names,
                "hits_at_recommendation": selected[index],
                "expected_bullets": result.bullets_to_kill,
                "expected_ttk_seconds": result.ttk_seconds,
                "next_hit_breakpoint": next_breakpoint,
            }
        )
    unaffected.sort(
        key=lambda weapon: (
            weapon["expected_ttk_seconds"],
            weapon["hits_at_recommendation"],
            weapon["names"],
        )
    )
    return {
        "distance_m": distance_m,
        "recommended_displayed_ehp": selected_ehp,
        "raw_recommended_displayed_ehp": raw_selected_ehp,
        "target": asdict(target),
        "metrics": metrics,
        "unaffected_count": len(unaffected),
        "fastest_unaffected": unaffected[:8],
        "top_candidates": [
            {"displayed_ehp": displayed_ehp, **candidate_metrics_}
            for displayed_ehp, candidate_metrics_ in candidates[:10]
        ],
    }


def analyze(
    bullet_resistance_cap: float,
    *,
    lookback: int = 50,
    plateau: int = 50,
    accuracy_tier: AccuracyTier = AccuracyTier.MEDIUM,
) -> dict[str, Any]:
    named_weapons = load_master_weapons()
    weapons = group_functional_weapons(named_weapons)
    return {
        "method": {
            "accuracy_tier": accuracy_tier.value,
            "weapon_level": 15,
            "weapon_categories": WEAPON_CATEGORIES,
            "named_weapon_count": len(named_weapons),
            "functional_profile_count": len(weapons),
            "bullet_resistance_cap": bullet_resistance_cap,
            "lookback_ehp": lookback,
            "plateau_ehp": plateau,
            "minimum_recommendation_ehp": MINIMUM_RECOMMENDATION_EHP,
            "near_best_score_ratio": NEAR_BEST_SCORE_RATIO,
        },
        "ammunition": {
            caliber: {
                "item_id": item_id,
                "name": name,
                **asdict(load_ammunition(item_id)),
            }
            for caliber, (item_id, name) in AMMUNITION_BY_CALIBER.items()
        },
        "category_ammunition_overrides": {
            f"{category}:{caliber}": {
                "item_id": item_id,
                "name": name,
                **asdict(load_ammunition(item_id)),
            }
            for (category, caliber), (
                item_id,
                name,
            ) in AMMUNITION_BY_CATEGORY_CALIBER.items()
        },
        "distances": [
            analyze_distance(
                weapons,
                distance_m=distance_m,
                bullet_resistance_cap=bullet_resistance_cap,
                lookback=lookback,
                plateau=plateau,
                accuracy_tier=accuracy_tier,
            )
            for distance_m in DISTANCES_M
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find aggregate EHP breakpoints for master +15 primary weapons."
    )
    parser.add_argument(
        "--bullet-resistance-cap",
        type=float,
        default=300.0,
        help="Add bullet resistance first up to this value, then add vitality.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print only the recommendation table and top candidates.",
    )
    parser.add_argument("--lookback", type=int, default=50)
    parser.add_argument("--plateau", type=int, default=50)
    parser.add_argument(
        "--accuracy-tier",
        choices=[tier.value for tier in AccuracyTier],
        default=AccuracyTier.MEDIUM.value,
    )
    args = parser.parse_args()
    report = analyze(
        args.bullet_resistance_cap,
        lookback=args.lookback,
        plateau=args.plateau,
        accuracy_tier=AccuracyTier(args.accuracy_tier),
    )
    if not args.compact:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print(json.dumps(report["method"], ensure_ascii=False))
    for result in report["distances"]:
        print(
            json.dumps(
                {
                    "distance_m": result["distance_m"],
                    "recommended_displayed_ehp": result[
                        "recommended_displayed_ehp"
                    ],
                    "target": result["target"],
                    "metrics": result["metrics"],
                    "unaffected_count": result["unaffected_count"],
                    "fastest_unaffected": result["fastest_unaffected"],
                    "top_candidates": result["top_candidates"][:5],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
