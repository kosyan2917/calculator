from __future__ import annotations

from fastapi import FastAPI


app = FastAPI(title="STALZONE Simulator", version="0.1.0")


@app.get("/api/simulator/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "simulator"}
