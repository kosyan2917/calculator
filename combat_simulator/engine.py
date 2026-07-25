from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import math
from typing import Any


class AccuracyTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class WeaponProfile:
    close_damage: float
    minimum_damage: float
    damage_falloff_start_m: float
    damage_falloff_end_m: float
    rounds_per_minute: float
    magazine_capacity: int
    reload_seconds: float
    headshot_multiplier: float = 1.0
    limb_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.close_damage <= 0 or self.minimum_damage <= 0:
            raise ValueError("Weapon damage must be positive")
        if self.damage_falloff_start_m < 0:
            raise ValueError("Damage falloff start cannot be negative")
        if self.damage_falloff_end_m < self.damage_falloff_start_m:
            raise ValueError("Damage falloff end must not precede its start")
        if self.rounds_per_minute <= 0:
            raise ValueError("Rate of fire must be positive")
        if self.magazine_capacity <= 0:
            raise ValueError("Magazine capacity must be positive")
        if self.reload_seconds < 0:
            raise ValueError("Reload time cannot be negative")
        if self.headshot_multiplier < 0 or self.limb_multiplier < 0:
            raise ValueError("Hit-location multipliers cannot be negative")

    def damage_at(self, distance_m: float) -> float:
        if distance_m < 0:
            raise ValueError("Distance cannot be negative")
        if distance_m <= self.damage_falloff_start_m:
            return self.close_damage
        if distance_m >= self.damage_falloff_end_m:
            return self.minimum_damage
        falloff_range = self.damage_falloff_end_m - self.damage_falloff_start_m
        if falloff_range == 0:
            return self.minimum_damage
        progress = (distance_m - self.damage_falloff_start_m) / falloff_range
        return self.close_damage + (self.minimum_damage - self.close_damage) * progress


@dataclass(frozen=True)
class AmmunitionProfile:
    armor_penetration_percent: float = 0.0
    damage_modifier_percent: float = 0.0

    def __post_init__(self) -> None:
        if self.armor_penetration_percent < -100:
            raise ValueError("Armor penetration cannot be below -100%")
        if self.damage_modifier_percent <= -100:
            raise ValueError("Damage modifier must leave positive direct damage")


@dataclass(frozen=True)
class TargetProfile:
    bullet_resistance: float
    vitality_percent: float

    def __post_init__(self) -> None:
        if self.bullet_resistance < 0:
            raise ValueError("Bullet resistance cannot be negative")
        if self.vitality_percent <= -100:
            raise ValueError("Vitality must leave positive health")


@dataclass(frozen=True)
class ShotDistribution:
    hit_probability: float
    head_share: float
    body_share: float
    limb_share: float

    def __post_init__(self) -> None:
        if not 0 <= self.hit_probability <= 1:
            raise ValueError("Hit probability must be between zero and one")
        shares = (self.head_share, self.body_share, self.limb_share)
        if any(value < 0 for value in shares):
            raise ValueError("Hit-location shares cannot be negative")
        if not math.isclose(sum(shares), 1.0, abs_tol=1e-9):
            raise ValueError("Hit-location shares must total one")


@dataclass(frozen=True)
class CombatResult:
    ttk_seconds: float
    bullets_to_kill: int
    hits_to_kill: int
    reloads: int
    damage_at_distance: float
    effective_bullet_resistance: float
    effective_health: float
    average_damage_per_hit: float
    average_damage_per_bullet: float
    shot_distribution: ShotDistribution

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Accuracy is the chance that a fired bullet hits. Hit-location shares are
# conditional on a hit. Values between table rows are linearly interpolated.
_ACCURACY_TABLE: tuple[
    tuple[float, dict[AccuracyTier, float], tuple[float, float, float]], ...
] = (
    (10.0, {AccuracyTier.LOW: 0.86, AccuracyTier.MEDIUM: 0.90, AccuracyTier.HIGH: 0.95}, (0.60, 0.30, 0.10)),
    (20.0, {AccuracyTier.LOW: 0.55, AccuracyTier.MEDIUM: 0.64, AccuracyTier.HIGH: 0.77}, (0.50, 0.35, 0.15)),
    (30.0, {AccuracyTier.LOW: 0.37, AccuracyTier.MEDIUM: 0.46, AccuracyTier.HIGH: 0.62}, (0.40, 0.40, 0.20)),
    (40.0, {AccuracyTier.LOW: 0.27, AccuracyTier.MEDIUM: 0.35, AccuracyTier.HIGH: 0.51}, (0.33, 0.41, 0.26)),
    (50.0, {AccuracyTier.LOW: 0.20, AccuracyTier.MEDIUM: 0.27, AccuracyTier.HIGH: 0.41}, (0.28, 0.41, 0.31)),
    (60.0, {AccuracyTier.LOW: 0.15, AccuracyTier.MEDIUM: 0.21, AccuracyTier.HIGH: 0.34}, (0.24, 0.40, 0.36)),
    (80.0, {AccuracyTier.LOW: 0.09, AccuracyTier.MEDIUM: 0.13, AccuracyTier.HIGH: 0.22}, (0.20, 0.38, 0.42)),
)


def effective_bullet_resistance(
    bullet_resistance: float,
    armor_penetration_percent: float,
) -> float:
    """Apply ammunition armor penetration to the target's total bullet resistance."""
    if bullet_resistance < 0:
        raise ValueError("Bullet resistance cannot be negative")
    if armor_penetration_percent < -100:
        raise ValueError("Armor penetration cannot be below -100%")
    return max(0.0, bullet_resistance * (1.0 - armor_penetration_percent / 100.0))


def _interpolate(left: float, right: float, progress: float) -> float:
    return left + (right - left) * progress


def shot_distribution(distance_m: float, tier: AccuracyTier) -> ShotDistribution:
    if distance_m < 0:
        raise ValueError("Distance cannot be negative")

    first = _ACCURACY_TABLE[0]
    last = _ACCURACY_TABLE[-1]
    if distance_m <= first[0]:
        accuracy = first[1][tier]
        head, body, limb = first[2]
        return ShotDistribution(accuracy, head, body, limb)
    if distance_m >= last[0]:
        accuracy = last[1][tier]
        head, body, limb = last[2]
        return ShotDistribution(accuracy, head, body, limb)

    for left, right in zip(_ACCURACY_TABLE, _ACCURACY_TABLE[1:]):
        if left[0] <= distance_m <= right[0]:
            progress = (distance_m - left[0]) / (right[0] - left[0])
            accuracy = _interpolate(left[1][tier], right[1][tier], progress)
            shares = tuple(_interpolate(a, b, progress) for a, b in zip(left[2], right[2]))
            return ShotDistribution(accuracy, *shares)

    raise RuntimeError("Accuracy table does not cover the requested distance")


class ShootingSimulator:
    """Deterministic direct-damage estimate using average damage per fired bullet."""

    def calculate(
        self,
        *,
        weapon: WeaponProfile,
        ammunition: AmmunitionProfile,
        target: TargetProfile,
        distance_m: float,
        accuracy_tier: AccuracyTier,
    ) -> CombatResult:
        distribution = shot_distribution(distance_m, accuracy_tier)
        weapon_damage = weapon.damage_at(distance_m)
        ammunition_damage = weapon_damage * (1.0 + ammunition.damage_modifier_percent / 100.0)

        effective_resistance = effective_bullet_resistance(
            target.bullet_resistance,
            ammunition.armor_penetration_percent,
        )
        effective_health = (100.0 + effective_resistance) * (
            1.0 + target.vitality_percent / 100.0
        )

        average_hit_multiplier = (
            distribution.head_share * weapon.headshot_multiplier
            + distribution.body_share
            + distribution.limb_share * weapon.limb_multiplier
        )
        average_damage_per_hit = ammunition_damage * average_hit_multiplier
        average_damage_per_bullet = average_damage_per_hit * distribution.hit_probability
        if average_damage_per_bullet <= 0:
            raise ValueError("The configured attack cannot deal direct damage")

        hits_to_kill = max(1, math.ceil(effective_health / average_damage_per_hit - 1e-12))
        bullets_to_kill = max(1, math.ceil(effective_health / average_damage_per_bullet - 1e-12))
        reloads = (bullets_to_kill - 1) // weapon.magazine_capacity
        firing_intervals = bullets_to_kill - 1 - reloads
        ttk_seconds = (
            firing_intervals * 60.0 / weapon.rounds_per_minute
            + reloads * weapon.reload_seconds
        )

        return CombatResult(
            ttk_seconds=ttk_seconds,
            bullets_to_kill=bullets_to_kill,
            hits_to_kill=hits_to_kill,
            reloads=reloads,
            damage_at_distance=weapon_damage,
            effective_bullet_resistance=effective_resistance,
            effective_health=effective_health,
            average_damage_per_hit=average_damage_per_hit,
            average_damage_per_bullet=average_damage_per_bullet,
            shot_distribution=distribution,
        )
