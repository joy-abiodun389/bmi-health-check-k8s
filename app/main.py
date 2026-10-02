"""FastAPI BMI health-check service with end-user UI."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import metrics
from app.bmi import calculate_bmi

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not metrics.metrics_enabled():
        yield
        return

    stop = asyncio.Event()
    publisher = asyncio.create_task(metrics.publish_loop(stop))
    try:
        yield
    finally:
        stop.set()
        await publisher


app = FastAPI(title="BMI Health Check", version="1.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def track_request_metrics(request: Request, call_next):
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        metrics.collector.record_request(elapsed_ms, status_code)


class BmiRequest(BaseModel):
    height_cm: float = Field(..., gt=0, description="Height in centimeters")
    weight_kg: float = Field(..., gt=0, description="Weight in kilograms")


class BmiResponse(BaseModel):
    height_cm: float
    weight_kg: float
    bmi: float
    category: str
    summary: str
    health_advice: list[str]
    exercises: list[str]
    needs_attention: bool


class HealthResponse(BaseModel):
    status: str
    pod: str | None = None
    node: str | None = None


def _to_response(result) -> BmiResponse:
    metrics.collector.record_bmi_calculation()
    return BmiResponse(
        height_cm=result.height_cm,
        weight_kg=result.weight_kg,
        bmi=result.bmi,
        category=result.category,
        summary=result.summary,
        health_advice=list(result.health_advice),
        exercises=list(result.exercises),
        needs_attention=result.needs_attention,
    )


@app.get("/")
def home() -> FileResponse:
    return FileResponse(TEMPLATES_DIR / "index.html")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness/readiness probe; reports which pod answered."""
    return HealthResponse(
        status="ok",
        pod=os.getenv("POD_NAME"),
        node=os.getenv("NODE_NAME"),
    )


@app.post("/bmi", response_model=BmiResponse)
def bmi_post(body: BmiRequest) -> BmiResponse:
    try:
        result = calculate_bmi(body.height_cm, body.weight_kg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(result)


@app.get("/bmi", response_model=BmiResponse)
def bmi_get(
    height_cm: float = Query(..., gt=0),
    weight_kg: float = Query(..., gt=0),
) -> BmiResponse:
    try:
        result = calculate_bmi(height_cm, weight_kg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(result)
