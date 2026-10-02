"""FastAPI BMI health-check service with end-user UI."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.bmi import calculate_bmi

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

app = FastAPI(title="BMI Health Check", version="1.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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
