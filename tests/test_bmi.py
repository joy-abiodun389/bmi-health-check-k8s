from app.bmi import calculate_bmi, categorize_bmi
from fastapi.testclient import TestClient
import pytest

from app.main import app

client = TestClient(app)


def test_normal_bmi():
    result = calculate_bmi(175, 70)
    assert result.bmi == 22.9
    assert result.category == "normal"
    assert result.needs_attention is False
    assert result.health_advice
    assert result.exercises


def test_underweight():
    result = calculate_bmi(180, 50)
    assert result.category == "underweight"
    assert result.needs_attention is True
    assert any("Strength training" in tip for tip in result.exercises)


def test_overweight():
    result = calculate_bmi(170, 80)
    assert result.category == "overweight"
    assert result.needs_attention is True
    assert any("calorie" in tip.lower() for tip in result.health_advice)


def test_obese():
    result = calculate_bmi(160, 90)
    assert result.category == "obese"
    assert result.needs_attention is True
    assert any("low-impact" in tip.lower() for tip in result.exercises)


def test_rejects_non_positive():
    with pytest.raises(ValueError):
        calculate_bmi(0, 70)
    with pytest.raises(ValueError):
        calculate_bmi(170, -1)


def test_categorize_boundaries():
    assert categorize_bmi(18.4) == "underweight"
    assert categorize_bmi(18.5) == "normal"
    assert categorize_bmi(24.9) == "normal"
    assert categorize_bmi(25.0) == "overweight"
    assert categorize_bmi(29.9) == "overweight"
    assert categorize_bmi(30.0) == "obese"


def test_ui_home():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "BMI Health Check" in response.text
    assert "/static/excelcloud-logo.jpg" in response.text


def test_brand_assets_served():
    for path in ("/static/excelcloud-logo.jpg", "/static/excelcloud-mark.png"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("image/"), path


def test_bmi_api_includes_guidance():
    response = client.post("/bmi", json={"height_cm": 175, "weight_kg": 70})
    assert response.status_code == 200
    data = response.json()
    assert data["category"] == "normal"
    assert data["summary"]
    assert len(data["health_advice"]) >= 1
    assert len(data["exercises"]) >= 1
    assert data["needs_attention"] is False
