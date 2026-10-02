(() => {
  const form = document.getElementById("bmi-form");
  const errorEl = document.getElementById("form-error");
  const results = document.getElementById("results");
  const bmiValue = document.getElementById("bmi-value");
  const categoryEl = document.getElementById("bmi-category");
  const summaryEl = document.getElementById("bmi-summary");
  const adviceList = document.getElementById("health-advice");
  const exerciseList = document.getElementById("exercises");

  function showError(message) {
    errorEl.hidden = false;
    errorEl.textContent = message;
  }

  function clearError() {
    errorEl.hidden = true;
    errorEl.textContent = "";
  }

  function fillList(ul, items) {
    ul.replaceChildren(
      ...items.map((text) => {
        const li = document.createElement("li");
        li.textContent = text;
        return li;
      })
    );
  }

  function renderResult(data) {
    bmiValue.textContent = data.bmi.toFixed(1);
    categoryEl.textContent = data.category.replaceAll("_", " ");
    categoryEl.classList.toggle("is-attention", Boolean(data.needs_attention));
    categoryEl.classList.toggle("is-ok", !data.needs_attention);
    summaryEl.textContent = data.summary;
    fillList(adviceList, data.health_advice || []);
    fillList(exerciseList, data.exercises || []);
    results.hidden = false;
    results.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError();

    const height_cm = Number(document.getElementById("height_cm").value);
    const weight_kg = Number(document.getElementById("weight_kg").value);

    if (!Number.isFinite(height_cm) || height_cm <= 0 || !Number.isFinite(weight_kg) || weight_kg <= 0) {
      showError("Enter valid height (cm) and weight (kg) greater than zero.");
      return;
    }

    try {
      const response = await fetch("/bmi", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ height_cm, weight_kg }),
      });

      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload.detail;
        const message = Array.isArray(detail)
          ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
          : detail || "Could not calculate BMI. Try again.";
        showError(message);
        results.hidden = true;
        return;
      }

      renderResult(payload);
    } catch {
      showError("Network error. Check your connection and try again.");
      results.hidden = true;
    }
  });
})();
