from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from artcalc import (
    ArtifactBuildOptimizer,
    OptimizationRequest,
    OptimizerConfig,
    SolverCatalog,
    UpgradePlanner,
    UpgradePlannerConfig,
    UpgradePlanningRequest,
)


ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = Path(os.getenv("ARTCALC_CATALOG", ROOT / "data" / "solver_catalog.json"))
FRONTEND_DIST = ROOT / "web" / "frontend" / "dist"

METRICS = (
    {"key": "durability", "label": "Приведенка", "group": "main", "direction": "max"},
    {"key": "speed", "label": "Скорость передвижения", "group": "main", "direction": "max"},
    {"key": "run_speed", "label": "Итоговая скорость бега", "group": "main", "direction": "max"},
    {"key": "stamina_regeneration", "label": "Восстановление выносливости", "group": "main", "direction": "max"},
    {"key": "regen", "label": "Лечение в секунду", "group": "main", "direction": "max"},
    {"key": "healing_effectiveness", "label": "Эффективность лечения", "group": "main", "direction": "max"},
    {"key": "weight", "label": "Переносимый вес", "group": "main", "direction": "max"},
    {"key": "stamina", "label": "Выносливость", "group": "secondary", "direction": "max"},
    {"key": "bleeding_output", "label": "Вывод кровотечения", "group": "secondary", "direction": "min"},
    {"key": "bleeding_resistance", "label": "Сопротивление кровотечению", "group": "secondary", "direction": "max"},
    {"key": "burn_reaction", "label": "Реакция на ожог", "group": "secondary", "direction": "max"},
    {"key": "tear_reaction", "label": "Реакция на разрыв", "group": "secondary", "direction": "max"},
)
METRIC_DIRECTIONS = {item["key"]: item["direction"] for item in METRICS}


class OptimizePayload(BaseModel):
    budget: int = Field(ge=1)
    armor_id: str | None = None
    container_id: str | None = None
    targets: dict[str, float] = Field(min_length=1)
    exclude_legendary_artifacts: bool = True
    excluded_armor_ids: list[str] = Field(default_factory=list, max_length=100)
    excluded_container_ids: list[str] = Field(default_factory=list, max_length=100)
    excluded_artifact_ids: list[str] = Field(default_factory=list, max_length=500)
    max_results: int = Field(default=10, ge=1, le=30)
    min_quality_percent: float = Field(default=95.0, ge=95.0, le=175.0)


class UpgradePayload(BaseModel):
    current_build: dict[str, Any]
    targets: dict[str, float] = Field(min_length=1)
    exclude_legendary_artifacts: bool = True
    excluded_container_ids: list[str] = Field(default_factory=list, max_length=100)
    excluded_artifact_ids: list[str] = Field(default_factory=list, max_length=500)
    extra_budgets: list[int] = Field(default_factory=list, max_length=6)


@lru_cache(maxsize=1)
def get_catalog() -> SolverCatalog:
    return SolverCatalog.read(CATALOG_PATH)


@lru_cache(maxsize=1)
def get_optimizer() -> ArtifactBuildOptimizer:
    return ArtifactBuildOptimizer(
        get_catalog(),
        OptimizerConfig(
            time_limit_per_solve=float(os.getenv("ARTCALC_TIME_LIMIT", "0.5")),
            max_solutions_per_container=int(os.getenv("ARTCALC_SOLUTIONS_PER_CONTAINER", "1")),
            num_search_workers=int(os.getenv("ARTCALC_CP_WORKERS", "1")),
            nonlinear_iterations=int(os.getenv("ARTCALC_NONLINEAR_ITERATIONS", "3")),
            infection_safety_margin=float(os.getenv("ARTCALC_INFECTION_SAFETY_MARGIN", "0.0001")),
        ),
    )


@lru_cache(maxsize=1)
def get_upgrade_planner() -> UpgradePlanner:
    return UpgradePlanner(
        get_catalog(),
        UpgradePlannerConfig(
            time_limit_per_solve=float(os.getenv("ARTCALC_UPGRADE_TIME_LIMIT", "0.25")),
            nonlinear_iterations=int(os.getenv("ARTCALC_UPGRADE_NONLINEAR_ITERATIONS", "2")),
        ),
    )


def validate_metrics(targets: dict[str, float]) -> None:
    unknown = set(targets) - set(METRIC_DIRECTIONS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown metrics: {', '.join(sorted(unknown))}")


app = FastAPI(title="STALZONE Artifact Builds", version="2.0.0")
allowed_hosts = [host.strip() for host in os.getenv("ALLOWED_HOSTS", "").split(",") if host.strip()]
if allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/catalog")
def catalog() -> dict:
    data = get_catalog()
    armors = sorted(
        (
            {
                "id": item["item_id"],
                "name": item["name"],
                "rank": item["rank"],
                "category": item["category"],
            }
            for item in data.armors
        ),
        key=lambda item: (item["rank"], item["name"]),
    )
    containers = sorted(
        (
            {
                "id": item["container_id"],
                "name": item["name"],
                "rank": item["rank"],
                "category": item["category"],
                "equipment_class": item.get("equipment_class", ""),
                "capacity": item["capacity"],
                "inner_protection": item["inner_protection"],
                "effectiveness": item["effectiveness"],
            }
            for item in data.containers
        ),
        key=lambda item: (-int(item["capacity"]), item["name"]),
    )
    artifact_tiers: dict[str, set[str]] = {}
    artifact_names: dict[str, str] = {}
    for group in data.artifact_groups:
        artifact_names[group.item_id] = group.name
        artifact_tiers.setdefault(group.item_id, set()).add(group.quality_tier)
    artifacts = sorted(
        (
            {
                "id": item_id,
                "name": artifact_names[item_id],
                "quality_tiers": sorted(artifact_tiers[item_id]),
            }
            for item_id in artifact_names
        ),
        key=lambda item: item["name"],
    )
    return {
        "generated_at": data.generated_at,
        "armors": armors,
        "containers": containers,
        "artifacts": artifacts,
        "metrics": METRICS,
        "limits": {
            "budget_min": 2_500_000,
            "budget_step": 2_500_000,
            "quality_min": data.min_quality_percent,
            "quality_max": 175.0,
        },
    }


@app.post("/api/optimize")
async def optimize(payload: OptimizePayload) -> dict:
    validate_metrics(payload.targets)
    request = OptimizationRequest(
        budget=payload.budget,
        targets=payload.targets,
        exclude_legendary_artifacts=payload.exclude_legendary_artifacts,
        armor_ids=(payload.armor_id,) if payload.armor_id else (),
        container_ids=(payload.container_id,) if payload.container_id else (),
        excluded_armor_ids=tuple(payload.excluded_armor_ids),
        excluded_container_ids=tuple(payload.excluded_container_ids),
        excluded_artifact_ids=tuple(payload.excluded_artifact_ids),
        min_quality_percent=payload.min_quality_percent,
        max_results=payload.max_results,
    )
    try:
        result = await run_in_threadpool(get_optimizer().search, request)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return result.to_dict()


@app.post("/api/upgrade-plans")
async def upgrade_plans(payload: UpgradePayload) -> dict:
    validate_metrics(payload.targets)
    budgets = tuple(payload.extra_budgets) if payload.extra_budgets else ()
    if any(budget < 1 or budget > 50_000_000 for budget in budgets):
        raise HTTPException(status_code=422, detail="Extra budgets must be between 1 and 50000000")

    request = UpgradePlanningRequest(
        current_build=payload.current_build,
        targets=payload.targets,
        exclude_legendary_artifacts=payload.exclude_legendary_artifacts,
        extra_budgets=budgets,
        excluded_artifact_ids=tuple(payload.excluded_artifact_ids),
        excluded_container_ids=tuple(payload.excluded_container_ids),
    )
    try:
        result = await run_in_threadpool(get_upgrade_planner().plan, request)
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return result.to_dict()


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        requested = (FRONTEND_DIST / path).resolve()
        if requested.is_file() and FRONTEND_DIST.resolve() in requested.parents:
            return FileResponse(requested)
        return FileResponse(FRONTEND_DIST / "index.html")
