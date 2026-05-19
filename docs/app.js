const METRICS = {
  listing_count: { label: "Listing count", format: (value) => formatNumber(value, 0) },
  median_price: { label: "Median price", format: formatCurrency },
  mean_price: { label: "Mean price", format: formatCurrency },
  median_deposit: { label: "Median deposit", format: formatCurrency },
  mean_deposit: { label: "Mean deposit", format: formatCurrency },
};

const state = {
  data: null,
  charts: {},
};

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const response = await fetch("./data/dashboard.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Failed to load dashboard data (${response.status})`);
    }
    state.data = await response.json();
    initialiseControls();
    renderAll();
  } catch (error) {
    renderError(error);
  }
});

function initialiseControls() {
  fillSelect(
    document.querySelector("#run-metric-select"),
    Object.entries(METRICS).map(([value, meta]) => ({ value, label: meta.label })),
    "median_price",
  );
  fillSelect(
    document.querySelector("#borough-metric-select"),
    [
      { value: "listing_count", label: METRICS.listing_count.label },
      { value: "median_price", label: METRICS.median_price.label },
      { value: "mean_price", label: METRICS.mean_price.label },
    ],
    "listing_count",
  );
  fillSelect(
    document.querySelector("#segment-metric-select"),
    [
      { value: "listing_count", label: METRICS.listing_count.label },
      { value: "median_price", label: METRICS.median_price.label },
      { value: "mean_price", label: METRICS.mean_price.label },
      { value: "median_deposit", label: METRICS.median_deposit.label },
    ],
    "median_price",
  );
  fillSelect(
    document.querySelector("#segment-borough-select"),
    [{ value: "__all__", label: "All London" }].concat(
      state.data.filters.boroughs.map((borough) => ({ value: borough, label: borough })),
    ),
    "__all__",
  );
  fillSelect(
    document.querySelector("#segment-dimension-select"),
    state.data.filters.dimensions.map((dimension) => ({
      value: dimension,
      label: prettifyDimension(dimension),
    })),
    "property_type",
  );

  document
    .querySelector("#segment-dimension-select")
    .addEventListener("change", syncSegmentValuesAndRender);
  document
    .querySelector("#run-metric-select")
    .addEventListener("change", () => renderRunTrend());
  document
    .querySelector("#borough-metric-select")
    .addEventListener("change", () => renderLatestBoroughs());
  document
    .querySelector("#borough-limit-select")
    .addEventListener("change", () => renderLatestBoroughs());
  document
    .querySelector("#segment-borough-select")
    .addEventListener("change", () => renderSegmentExplorer());
  document
    .querySelector("#segment-value-select")
    .addEventListener("change", () => renderSegmentExplorer());
  document
    .querySelector("#segment-metric-select")
    .addEventListener("change", () => renderSegmentExplorer());

  syncSegmentValuesAndRender();
}

function fillSelect(select, options, defaultValue) {
  select.innerHTML = "";
  for (const option of options) {
    const element = document.createElement("option");
    element.value = option.value;
    element.textContent = option.label;
    if (option.value === defaultValue) {
      element.selected = true;
    }
    select.appendChild(element);
  }
}

function syncSegmentValuesAndRender() {
  const dimension = document.querySelector("#segment-dimension-select").value;
  const values = state.data.filters.dimension_values[dimension] || [];
  const preferredValue = pickDefaultSegmentValue(dimension, values);
  fillSelect(
    document.querySelector("#segment-value-select"),
    values.map((value) => ({ value, label: value })),
    preferredValue,
  );
  renderSegmentExplorer();
}

function pickDefaultSegmentValue(dimension, values) {
  if (!values.length) {
    return "";
  }
  const priority = {
    property_type: "Flat",
    deposit: "Deposit listed",
    price_reduced: "Price reduced",
    build_to_rent: "Build to rent",
    luxury: "Luxury",
    investment_opportunity: "Investment opportunity",
  };
  return values.includes(priority[dimension]) ? priority[dimension] : values[0];
}

function renderAll() {
  renderSummary();
  renderRunTrend();
  renderLatestBoroughs();
  renderSegmentExplorer();
}

function renderSummary() {
  const meta = state.data.meta;
  const latestRun = state.data.overview.latest_run;
  document.querySelector("#latest-run-label").textContent = formatRunLabel(meta.latest_run_timestamp);
  document.querySelector("#generated-at-label").textContent = formatDateTime(meta.generated_at);
  document.querySelector("#runs-captured").textContent = formatNumber(meta.run_count, 0);
  document.querySelector("#latest-listings").textContent = formatNumber(latestRun.listing_count, 0);
  document.querySelector("#latest-median-price").textContent = formatCurrency(latestRun.median_price);
  document.querySelector("#latest-mean-price").textContent = formatCurrency(latestRun.mean_price);
}

function renderRunTrend() {
  const metric = document.querySelector("#run-metric-select").value;
  const series = state.data.series.runs;
  const labels = series.map((row) => formatRunLabel(row.run_timestamp));
  const data = series.map((row) => row[metric]);
  state.charts.runTrend = renderChart(state.charts.runTrend, "#run-trend-chart", {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: METRICS[metric].label,
          data,
          borderColor: "#b84f2d",
          backgroundColor: "rgba(184, 79, 45, 0.18)",
          borderWidth: 3,
          pointRadius: 4,
          pointHoverRadius: 6,
          tension: 0.25,
          fill: true,
        },
      ],
    },
    options: chartOptions(METRICS[metric].format),
  });
}

function renderLatestBoroughs() {
  const metric = document.querySelector("#borough-metric-select").value;
  const limit = Number(document.querySelector("#borough-limit-select").value);
  const rows = [...state.data.latest.borough_stats]
    .filter((row) => row.london_borough && row.london_borough !== "Unknown")
    .sort((left, right) => (right[metric] ?? -Infinity) - (left[metric] ?? -Infinity))
    .slice(0, limit);

  document.querySelector("#borough-table-metric-label").textContent = METRICS[metric].label;

  state.charts.borough = renderChart(state.charts.borough, "#borough-chart", {
    type: "bar",
    data: {
      labels: rows.map((row) => row.london_borough),
      datasets: [
        {
          label: METRICS[metric].label,
          data: rows.map((row) => row[metric]),
          backgroundColor: rows.map((_, index) =>
            index % 2 === 0 ? "rgba(37, 95, 90, 0.82)" : "rgba(184, 79, 45, 0.82)",
          ),
          borderRadius: 12,
        },
      ],
    },
    options: {
      ...chartOptions(METRICS[metric].format),
      indexAxis: "y",
      plugins: {
        legend: { display: false },
        tooltip: tooltipConfig(METRICS[metric].format),
      },
    },
  });

  const tbody = document.querySelector("#borough-table-body");
  tbody.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.london_borough)}</td>
          <td>${METRICS[metric].format(row[metric])}</td>
          <td>${formatNumber(row.listing_count, 0)}</td>
        </tr>
      `,
    )
    .join("");
}

function renderSegmentExplorer() {
  const borough = document.querySelector("#segment-borough-select").value;
  const dimension = document.querySelector("#segment-dimension-select").value;
  const value = document.querySelector("#segment-value-select").value;
  const metric = document.querySelector("#segment-metric-select").value;
  const usingAllLondon = borough === "__all__";

  const trendSource = usingAllLondon
    ? state.data.series.category_stats
    : state.data.series.borough_category_stats;
  const latestSource = usingAllLondon
    ? state.data.latest.category_stats
    : state.data.latest.borough_category_stats;

  const trendRows = trendSource
    .filter((row) => row.dimension === dimension)
    .filter((row) => usingAllLondon || row.london_borough === borough);

  const selectedSeries = trendRows
    .filter((row) => row.value === value)
    .sort((left, right) => left.run_timestamp.localeCompare(right.run_timestamp));

  const latestRows = latestSource
    .filter((row) => row.dimension === dimension)
    .filter((row) => usingAllLondon || row.london_borough === borough)
    .sort((left, right) => (right[metric] ?? -Infinity) - (left[metric] ?? -Infinity));

  state.charts.segment = renderChart(state.charts.segment, "#segment-trend-chart", {
    type: "line",
    data: {
      labels: selectedSeries.map((row) => formatRunLabel(row.run_timestamp)),
      datasets: [
        {
          label: `${value} · ${METRICS[metric].label}`,
          data: selectedSeries.map((row) => row[metric]),
          borderColor: "#255f5a",
          backgroundColor: "rgba(37, 95, 90, 0.14)",
          borderWidth: 3,
          pointRadius: 4,
          pointHoverRadius: 6,
          tension: 0.25,
          fill: true,
        },
      ],
    },
    options: chartOptions(METRICS[metric].format),
  });

  document.querySelector("#segment-table-metric-label").textContent = METRICS[metric].label;
  document.querySelector("#segment-summary").innerHTML = [
    makePill(`Scope: ${usingAllLondon ? "All London" : borough}`),
    makePill(`Dimension: ${prettifyDimension(dimension)}`),
    makePill(`Value: ${value || "None"}`),
    makePill(
      `Latest ${METRICS[metric].label.toLowerCase()}: ${
        selectedSeries.length ? METRICS[metric].format(selectedSeries[selectedSeries.length - 1][metric]) : "No data"
      }`,
    ),
  ].join("");

  const tbody = document.querySelector("#segment-table-body");
  tbody.innerHTML = latestRows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.value)}</td>
          <td>${METRICS[metric].format(row[metric])}</td>
          <td>${formatNumber(row.listing_count, 0)}</td>
        </tr>
      `,
    )
    .join("");
}

function renderChart(existingChart, selector, config) {
  if (existingChart) {
    existingChart.destroy();
  }
  const canvas = document.querySelector(selector);
  return new Chart(canvas, config);
}

function chartOptions(valueFormatter) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    scales: {
      y: {
        beginAtZero: false,
        ticks: {
          callback: (value) => valueFormatter(value),
          color: "#665d52",
        },
        grid: { color: "rgba(78, 55, 27, 0.1)" },
      },
      x: {
        ticks: { color: "#665d52" },
        grid: { display: false },
      },
    },
    plugins: {
      legend: {
        labels: {
          color: "#1b1a17",
          font: { family: "Space Grotesk" },
        },
      },
      tooltip: tooltipConfig(valueFormatter),
    },
  };
}

function tooltipConfig(valueFormatter) {
  return {
    callbacks: {
      label(context) {
        return `${context.dataset.label}: ${valueFormatter(context.parsed.y ?? context.parsed.x)}`;
      },
    },
  };
}

function formatCurrency(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "N/A";
  }
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: "GBP",
    maximumFractionDigits: 0,
  }).format(Number(value));
}

function formatNumber(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "N/A";
  }
  return new Intl.NumberFormat("en-GB", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(Number(value));
}

function formatRunLabel(timestamp) {
  if (!timestamp) {
    return "Unknown run";
  }
  const year = Number(timestamp.slice(0, 4));
  const month = Number(timestamp.slice(4, 6)) - 1;
  const day = Number(timestamp.slice(6, 8));
  const hour = Number(timestamp.slice(9, 11));
  const minute = Number(timestamp.slice(11, 13));
  const date = new Date(Date.UTC(year, month, day, hour, minute));
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/London",
  }).format(date);
}

function formatDateTime(timestamp) {
  if (!timestamp) {
    return "Unknown";
  }
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Europe/London",
  }).format(new Date(timestamp));
}

function prettifyDimension(dimension) {
  return dimension
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function makePill(text) {
  return `<span class="segment-pill">${escapeHtml(text)}</span>`;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderError(error) {
  document.body.innerHTML = `
    <main class="page-shell">
      <section class="panel">
        <p class="panel-kicker">Dashboard Error</p>
        <h1>Unable to load the London rental dashboard.</h1>
        <p>${escapeHtml(error.message)}</p>
      </section>
    </main>
  `;
}
