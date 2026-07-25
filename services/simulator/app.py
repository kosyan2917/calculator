from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from combat_simulator import (
    AccuracyTier,
    AmmunitionProfile,
    ShootingSimulator,
    TargetProfile,
    WeaponProfile,
)


app = FastAPI(title="STALZONE Simulator", version="0.1.0")
simulator = ShootingSimulator()


class WeaponInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    close_damage: float = Field(gt=0)
    minimum_damage: float = Field(gt=0)
    damage_falloff_start_m: float = Field(ge=0)
    damage_falloff_end_m: float = Field(ge=0)
    rounds_per_minute: float = Field(gt=0)
    magazine_capacity: int = Field(gt=0)
    reload_seconds: float = Field(ge=0)
    headshot_multiplier: float = Field(default=1.0, ge=0)
    limb_multiplier: float = Field(default=1.0, ge=0)


class AmmunitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    armor_penetration_percent: float = Field(default=0.0, ge=-100)
    damage_modifier_percent: float = Field(default=0.0, gt=-100)


class TargetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bullet_resistance: float = Field(ge=0)
    vitality_percent: float = Field(gt=-100)


class ShootingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weapon: WeaponInput
    ammunition: AmmunitionInput
    target: TargetInput
    distance_m: float = Field(ge=0)
    accuracy_tier: AccuracyTier


@app.get("/api/simulator/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "simulator"}


@app.post("/api/simulator/shooting")
def calculate_shooting(request: ShootingRequest) -> dict[str, object]:
    try:
        result = simulator.calculate(
            weapon=WeaponProfile(**request.weapon.model_dump()),
            ammunition=AmmunitionProfile(**request.ammunition.model_dump()),
            target=TargetProfile(**request.target.model_dump()),
            distance_m=request.distance_m,
            accuracy_tier=request.accuracy_tier,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return result.to_dict()
