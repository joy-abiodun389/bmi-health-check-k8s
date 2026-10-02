"""BMI calculation helpers with health and exercise guidance."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BmiResult:
    bmi: float
    category: str
    height_cm: float
    weight_kg: float
    summary: str
    health_advice: tuple[str, ...]
    exercises: tuple[str, ...]
    needs_attention: bool


_GUIDANCE: dict[str, dict[str, object]] = {
    "underweight": {
        "summary": "Your BMI is below the healthy range. Focus on nourishing weight gain and strength.",
        "needs_attention": True,
        "health_advice": (
            "Eat regular, nutrient-dense meals with healthy fats, lean protein, and complex carbs.",
            "Add calorie-rich snacks such as nuts, yogurt, avocado, or peanut butter on whole-grain toast.",
            "Prioritize sleep and manage stress — both affect appetite and recovery.",
            "If weight loss is unintentional or persistent, talk with a clinician to rule out underlying causes.",
        ),
        "exercises": (
            "Strength training 3×/week (squats, rows, push-ups, resistance bands) to build muscle mass.",
            "Short walks or light cycling for circulation — avoid long high-intensity cardio that burns excess calories.",
            "Yoga or Pilates 1–2×/week for mobility, appetite regulation, and stress relief.",
            "Progressive overload: gradually increase weights or resistance as you get stronger.",
        ),
    },
    "normal": {
        "summary": "Your BMI is in the healthy range. Keep up balanced habits to stay here.",
        "needs_attention": False,
        "health_advice": (
            "Maintain a varied diet with vegetables, fruit, whole grains, and adequate protein.",
            "Stay hydrated and limit ultra-processed foods and sugary drinks.",
            "Aim for consistent sleep (7–9 hours) and routine activity most days of the week.",
            "Recheck BMI periodically if your weight or lifestyle changes.",
        ),
        "exercises": (
            "150+ minutes/week of moderate cardio (brisk walking, cycling, swimming).",
            "Strength training 2×/week covering major muscle groups.",
            "Flexibility or mobility work (stretching, yoga) 1–2×/week.",
            "Mix intensities — include some vigorous sessions if your doctor clears you for them.",
        ),
    },
    "overweight": {
        "summary": "Your BMI is above the healthy range. Small, steady changes in diet and activity can help.",
        "needs_attention": True,
        "health_advice": (
            "Create a modest calorie deficit with whole foods — favor vegetables, lean protein, and fiber.",
            "Cut back on sugary drinks and large portions; use a plate method (half veggies).",
            "Track habits weekly rather than obsessing over daily weight swings.",
            "Consider a checkup for blood pressure, blood sugar, and cholesterol.",
        ),
        "exercises": (
            "Brisk walking 30–45 minutes most days — start where you are and build up.",
            "Low-impact cardio: swimming, elliptical, or cycling 3–5×/week.",
            "Strength training 2–3×/week to preserve muscle while losing fat.",
            "Optional short interval sessions (e.g. 1 min faster / 2 min easy) once you have a base.",
        ),
    },
    "obese": {
        "summary": "Your BMI is in a higher-risk range. Focus on safe, low-impact progress and professional support.",
        "needs_attention": True,
        "health_advice": (
            "Work with a healthcare professional before aggressive diet or exercise changes.",
            "Emphasize sustainable eating: regular meals, high fiber, lean protein, fewer ultra-processed foods.",
            "Set process goals (steps, meals cooked at home) instead of only scale goals.",
            "Screen for related risks (blood pressure, glucose, sleep apnea) with your clinician.",
        ),
        "exercises": (
            "Start with low-impact movement: walking, water aerobics, or seated marches 10–20 minutes.",
            "Swim or water walking to reduce joint stress while building endurance.",
            "Beginner strength work 2×/week (bodyweight or light bands) with attention to form.",
            "Increase duration by ~10% per week; stop and seek care for chest pain, dizziness, or severe shortness of breath.",
        ),
    },
}


def categorize_bmi(bmi: float) -> str:
    """Return WHO-style adult BMI category."""
    if bmi < 18.5:
        return "underweight"
    if bmi < 25.0:
        return "normal"
    if bmi < 30.0:
        return "overweight"
    return "obese"


def calculate_bmi(height_cm: float, weight_kg: float) -> BmiResult:
    if height_cm <= 0 or weight_kg <= 0:
        raise ValueError("height_cm and weight_kg must be positive numbers")

    height_m = height_cm / 100.0
    bmi = weight_kg / (height_m * height_m)
    bmi_rounded = round(bmi, 1)
    category = categorize_bmi(bmi_rounded)
    guide = _GUIDANCE[category]

    return BmiResult(
        bmi=bmi_rounded,
        category=category,
        height_cm=height_cm,
        weight_kg=weight_kg,
        summary=str(guide["summary"]),
        health_advice=tuple(guide["health_advice"]),  # type: ignore[arg-type]
        exercises=tuple(guide["exercises"]),  # type: ignore[arg-type]
        needs_attention=bool(guide["needs_attention"]),
    )
