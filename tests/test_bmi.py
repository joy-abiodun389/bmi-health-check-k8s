from app.bmi import calculate_bmi, categorize_bmi
from fastapi.testclient import TestClient
import pytest

from app import metrics
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


def test_metrics_disabled_by_default(monkeypatch):
    monkeypatch.delenv("METRICS_ENABLED", raising=False)
    assert metrics.metrics_enabled() is False


def test_region_falls_back_to_aws_region(monkeypatch):
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    assert metrics.resolve_region() == "us-east-2"

    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    assert metrics.resolve_region() == "us-west-2"

    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    assert metrics.resolve_region() is None


def test_metrics_collector_aggregates():
    collector = metrics.MetricsCollector()
    collector.record_request(12.5, 200)
    collector.record_request(30.0, 500)
    collector.record_bmi_calculation()

    window = collector.drain()
    assert window.requests == 2
    assert window.errors == 1
    assert window.bmi_calculations == 1

    data = {item["MetricName"]: item for item in metrics.build_metric_data(window)}
    assert data["RequestCount"]["Value"] == 2
    assert data["ErrorCount"]["Value"] == 1
    assert data["LatencyMs"]["StatisticValues"]["SampleCount"] == 2
    assert data["LatencyMs"]["StatisticValues"]["Maximum"] == 30.0

    # Draining resets the window so counts are never double-reported.
    assert collector.drain().is_empty()


def test_requests_are_recorded_by_middleware():
    before = metrics.collector.drain()
    assert before.is_empty() or before.requests >= 0

    client.get("/health")
    window = metrics.collector.drain()
    assert window.requests >= 1


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
