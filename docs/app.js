const METRICS = {
  listing_count: { label: "Listing count", format: (value) => formatNumber(value, 0) },
  median_price: { label: "Median price", format: formatCurrency },
  mean_price: { label: "Mean price", format: formatCurrency },
  median_deposit: { label: "Median deposit", format: formatCurrency },
  mean_deposit: { label: "Mean deposit", format: formatCurrency },
};

const DATASET_DIMENSIONS = {
  property_type: { label: "Property type", field: "property_type_category" },
  bedrooms: { label: "Bedrooms", field: "bedroom_category" },
  build_to_rent: { label: "Build to rent", field: "build_to_rent_category" },
  student_suitable: { label: "Student suitable", field: "student_category" },
  price_reduced: { label: "Price reduced", field: "price_reduced_category" },
  deposit: { label: "Deposit", field: "deposit_category" },
  zero_deposit: { label: "Zero deposit", field: "zero_deposit_category" },
  online_viewings: { label: "Online viewings", field: "online_viewings_category" },
  pets: { label: "Pets", field: "pets_category" },
  bills: { label: "Bills", field: "bills_category" },
  luxury: { label: "Luxury", field: "luxury_category" },
  investment_opportunity: { label: "Investment opportunity", field: "investment_opportunity_category" },
  furnish_type: { label: "Furnish type", field: "furnish_type_category" },
  let_type: { label: "Let type", field: "let_type_category" },
};

const state = {
  dashboard: null,
  listings: null,
  charts: {},
};

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const dashboardResponse = await fetch("./data/dashboard.json", { cache: "no-store" });

    if (!dashboardResponse.ok) {
      throw new Error(`Failed to load dashboard data (${dashboardResponse.status})`);
    }

    state.dashboard = await dashboardResponse.json();
    const listingsResponse = await fetch("./data/listings.json", { cache: "no-store" });
    if (listingsResponse.ok) {
      state.listings = await listingsResponse.json();
    } else if (listingsResponse.status !== 404) {
      throw new Error(`Failed to load listing data (${listingsResponse.status})`);
    }

    initialiseTabs();
    initialiseControls();
    renderAll();
  } catch (error) {
    renderError(error);
  }
});

function initialiseTabs() {
  const buttons = [...document.querySelectorAll(".tab-button")];
  for (const button of buttons) {
    button.addEventListener("click", () => {
      const targetId = button.dataset.tabTarget;
      for (const other of buttons) {
        other.classList.toggle("is-active", other === button);
      }
      for (const panel of document.querySelectorAll(".tab-panel")) {
        panel.classList.toggle("is-active", panel.id === targetId);
      }
    });
  }
}

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
      state.dashboard.filters.boroughs.map((borough) => ({ value: borough, label: borough })),
    ),
    "__all__",
  );
  fillSelect(
    document.querySelector("#segment-dimension-select"),
    state.dashboard.filters.dimensions.map((dimension) => ({
      value: dimension,
      label: prettifyDimension(dimension),
    })),
    "property_type",
  );

  fillSelect(
    document.querySelector("#dataset-dimension-select"),
    Object.entries(DATASET_DIMENSIONS).map(([value, meta]) => ({ value, label: meta.label })),
    "property_type",
  );

  document.querySelector("#segment-dimension-select").addEventListener("change", syncSegmentValuesAndRender);
  document.querySelector("#run-metric-select").addEventListener("change", renderRunTrend);
  document.querySelector("#borough-metric-select").addEventListener("change", renderLatestBoroughs);
  document.querySelector("#borough-limit-select").addEventListener("change", renderLatestBoroughs);
  document.querySelector("#segment-borough-select").addEventListener("change", renderSegmentExplorer);
  document.querySelector("#segment-value-select").addEventListener("change", renderSegmentExplorer);
  document.querySelector("#segment-scale-select").addEventListener("change", renderSegmentExplorer);
  document.querySelector("#segment-metric-select").addEventListener("change", renderSegmentExplorer);

  if (state.listings) {
    fillSelect(
      document.querySelector("#dataset-run-select"),
      [{ value: "__all__", label: "All runs" }].concat(
        state.listings.filters.runs
          .slice()
          .reverse()
          .map((run) => ({ value: run.run_timestamp, label: formatRunLabel(run.run_timestamp) })),
      ),
      "__all__",
    );
    fillSelect(
      document.querySelector("#dataset-borough-select"),
      [{ value: "__all__", label: "All boroughs" }].concat(
        state.listings.filters.boroughs.map((borough) => ({ value: borough, label: borough })),
      ),
      "__all__",
    );

    document.querySelector("#dataset-dimension-select").addEventListener("change", syncDatasetValuesAndRender);
    document.querySelector("#dataset-run-select").addEventListener("change", renderDatasetTable);
    document.querySelector("#dataset-borough-select").addEventListener("change", renderDatasetTable);
    document.querySelector("#dataset-value-select").addEventListener("change", renderDatasetTable);
    document.querySelector("#dataset-limit-select").addEventListener("change", renderDatasetTable);
    document.querySelector("#dataset-download-button").addEventListener("click", downloadDatasetCsv);
  } else {
    for (const selector of [
      "#dataset-run-select",
      "#dataset-borough-select",
      "#dataset-dimension-select",
      "#dataset-value-select",
      "#dataset-limit-select",
      "#dataset-download-button",
    ]) {
      document.querySelector(selector).disabled = true;
    }
  }

  syncSegmentValuesAndRender();
  if (state.listings) {
    syncDatasetValuesAndRender();
  }
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
  const values = state.dashboard.filters.dimension_values[dimension] || [];
  const preferredValue = pickDefaultValue(dimension, values);
  fillSelect(
    document.querySelector("#segment-value-select"),
    [{ value: "__all__", label: "All values" }].concat(values.map((value) => ({ value, label: value }))),
    preferredValue,
  );
  renderSegmentExplorer();
}

function syncDatasetValuesAndRender() {
  if (!state.listings) {
    return;
  }
  const dimension = document.querySelector("#dataset-dimension-select").value;
  const values = state.listings.filters.dimension_values[dimension] || [];
  const preferredValue = pickDefaultValue(dimension, values, "__all__");
  fillSelect(
    document.querySelector("#dataset-value-select"),
    [{ value: "__all__", label: "All values" }].concat(values.map((value) => ({ value, label: value }))),
    preferredValue,
  );
  renderDatasetTable();
}

function pickDefaultValue(dimension, values, fallback = null) {
  if (!values.length) {
    return fallback ?? "";
  }
  const priority = {
    property_type: "Flat",
    deposit: "Deposit listed",
    price_reduced: "Price reduced",
    build_to_rent: "Build to rent",
    luxury: "Luxury",
    investment_opportunity: "Investment opportunity",
  };
  if (fallback !== null) {
    return values.includes(priority[dimension]) ? priority[dimension] : fallback;
  }
  return values.includes(priority[dimension]) ? priority[dimension] : "__all__";
}

function renderAll() {
  renderSummary();
  renderRunTrend();
  renderLatestBoroughs();
  renderSegmentExplorer();
  renderDatasetTable();
}

function renderSummary() {
  const meta = state.dashboard.meta;
  const latestRun = state.dashboard.overview.latest_run;
  document.querySelector("#latest-run-label").textContent = formatRunLabel(meta.latest_run_timestamp);
  document.querySelector("#generated-at-label").textContent = formatDateTime(meta.generated_at);
  document.querySelector("#runs-captured").textContent = formatNumber(meta.run_count, 0);
  document.querySelector("#latest-listings").textContent = formatNumber(latestRun.listing_count, 0);
  document.querySelector("#latest-median-price").textContent = formatCurrency(latestRun.median_price);
  document.querySelector("#latest-mean-price").textContent = formatCurrency(latestRun.mean_price);
}

function renderRunTrend() {
  const metric = document.querySelector("#run-metric-select").value;
  const series = state.dashboard.series.runs;
  state.charts.runTrend = renderChart(state.charts.runTrend, "#run-trend-chart", {
    type: "line",
    data: {
      labels: series.map((row) => formatRunLabel(row.run_timestamp)),
      datasets: [
        {
          label: METRICS[metric].label,
          data: series.map((row) => row[metric]),
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
  const rows = [...state.dashboard.latest.borough_stats]
    .filter((row) => row.london_borough && row.london_borough !== "Unknown")
    .sort((left, right) => (right[metric] ?? -Infinity) - (left[metric] ?? -Infinity))
    .slice(0, limit);

  document.querySelector("#borough-table-metric-label").textContent = METRICS[metric].label;

  state.charts.borough = renderChart(state.charts.borough, "#borough-chart", {
    type: "scatter",
    data: {
      datasets: [
        {
          label: `Latest run · ${METRICS[metric].label}`,
          data: rows.map((row) => ({ x: row[metric], y: row.london_borough })),
          backgroundColor: rows.map((_, index) =>
            index % 2 === 0 ? "rgba(37, 95, 90, 0.95)" : "rgba(184, 79, 45, 0.95)",
          ),
          borderColor: "#fff8f3",
          borderWidth: 2,
          pointRadius: 7,
          pointHoverRadius: 9,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          ticks: {
            callback: (value) => METRICS[metric].format(value),
            color: "#665d52",
          },
          grid: { color: "rgba(78, 55, 27, 0.1)" },
        },
        y: {
          type: "category",
          labels: rows.map((row) => row.london_borough),
          ticks: { color: "#665d52" },
          grid: { display: false },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label(context) {
              return `${context.raw.y}: ${METRICS[metric].format(context.raw.x)}`;
            },
          },
        },
      },
    },
  });

  document.querySelector("#borough-table-body").innerHTML = rows
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
  const scale = document.querySelector("#segment-scale-select").value;
  const metric = document.querySelector("#segment-metric-select").value;
  const usingAllLondon = borough === "__all__";

  const trendSource = usingAllLondon
    ? state.dashboard.series.category_stats
    : state.dashboard.series.borough_category_stats;
  const latestSource = usingAllLondon
    ? state.dashboard.latest.category_stats
    : state.dashboard.latest.borough_category_stats;

  const trendRows = trendSource
    .filter((row) => row.dimension === dimension)
    .filter((row) => usingAllLondon || row.london_borough === borough);

  const selectedSeries = trendRows
    .filter((row) => value === "__all__" || row.value === value)
    .sort((left, right) => left.run_timestamp.localeCompare(right.run_timestamp));

  const latestRows = latestSource
    .filter((row) => row.dimension === dimension)
    .filter((row) => usingAllLondon || row.london_borough === borough)
    .filter((row) => value === "__all__" || row.value === value)
    .sort((left, right) => (right[metric] ?? -Infinity) - (left[metric] ?? -Infinity));

  const datasets = [];
  const groupedSeries = groupBy(selectedSeries, (row) => row.value);
  const valuesToRender = value === "__all__" ? [...groupedSeries.keys()].sort() : [value];
  for (const [index, seriesValue] of valuesToRender.entries()) {
    const seriesRows = groupedSeries.get(seriesValue) || [];
    datasets.push({
      label: `${seriesValue} · ${METRICS[metric].label}`,
      data: seriesRows.map((row) => row[metric]),
      borderColor: paletteColor(index),
      backgroundColor: paletteFill(index),
      borderWidth: 3,
      pointRadius: 4,
      pointHoverRadius: 6,
      tension: 0.25,
      fill: false,
    });
  }

  state.charts.segment = renderChart(state.charts.segment, "#segment-trend-chart", {
    type: "line",
    data: {
      labels: uniqueOrdered(selectedSeries.map((row) => formatRunLabel(row.run_timestamp))),
      datasets,
    },
    options: chartOptions(METRICS[metric].format, { yScaleType: scale }),
  });

  document.querySelector("#segment-table-metric-label").textContent = METRICS[metric].label;
  document.querySelector("#segment-summary").innerHTML = [
    makePill(`Scope: ${usingAllLondon ? "All London" : borough}`),
    makePill(`Dimension: ${prettifyDimension(dimension)}`),
    makePill(`Value: ${value === "__all__" ? "All values" : value || "None"}`),
    makePill(`Scale: ${scale === "logarithmic" ? "Logarithmic" : "Linear"}`),
    makePill(
      `Latest ${METRICS[metric].label.toLowerCase()}: ${
        latestRows.length ? METRICS[metric].format(latestRows[0][metric]) : "No data"
      }`,
    ),
  ].join("");

  document.querySelector("#segment-table-body").innerHTML = latestRows
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

function getFilteredDatasetRows() {
  if (!state.listings) {
    return [];
  }
  const runValue = document.querySelector("#dataset-run-select").value;
  const boroughValue = document.querySelector("#dataset-borough-select").value;
  const dimension = document.querySelector("#dataset-dimension-select").value;
  const selectedValue = document.querySelector("#dataset-value-select").value;
  const dimensionField = DATASET_DIMENSIONS[dimension].field;

  return state.listings.rows.filter((row) => {
    if (runValue !== "__all__" && row.run_timestamp !== runValue) {
      return false;
    }
    if (boroughValue !== "__all__" && row.london_borough !== boroughValue) {
      return false;
    }
    if (selectedValue !== "__all__" && row[dimensionField] !== selectedValue) {
      return false;
    }
    return true;
  });
}

function renderDatasetTable() {
  if (!state.listings) {
    document.querySelector("#dataset-summary").innerHTML = [
      makePill("Listing-level data will appear after the first full production run."),
      makePill("The analytics tab is already live."),
    ].join("");
    document.querySelector("#dataset-table-body").innerHTML = `
      <tr>
        <td colspan="9">No listing-level dataset has been published yet.</td>
      </tr>
    `;
    return;
  }
  const rows = getFilteredDatasetRows();
  const limit = Number(document.querySelector("#dataset-limit-select").value);
  const visibleRows = rows
    .slice()
    .sort((left, right) => right.run_timestamp.localeCompare(left.run_timestamp))
    .slice(0, limit);

  const runValue = document.querySelector("#dataset-run-select").value;
  const boroughValue = document.querySelector("#dataset-borough-select").value;
  const dimension = document.querySelector("#dataset-dimension-select").value;
  const selectedValue = document.querySelector("#dataset-value-select").value;

  document.querySelector("#dataset-summary").innerHTML = [
    makePill(`Run: ${runValue === "__all__" ? "All runs" : formatRunLabel(runValue)}`),
    makePill(`Borough: ${boroughValue === "__all__" ? "All boroughs" : boroughValue}`),
    makePill(`Filter: ${DATASET_DIMENSIONS[dimension].label}`),
    makePill(`Value: ${selectedValue === "__all__" ? "All values" : selectedValue}`),
    makePill(`Rows matched: ${formatNumber(rows.length, 0)}`),
    makePill(`Rows shown: ${formatNumber(Math.min(rows.length, limit), 0)}`),
  ].join("");

  document.querySelector("#dataset-table-body").innerHTML = visibleRows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(formatRunLabel(row.run_timestamp))}</td>
          <td>${escapeHtml(row.london_borough || "Unknown")}</td>
          <td>${escapeHtml(row.display_address || row.location || "Unknown")}</td>
          <td>${formatCurrency(row.price_amount)}</td>
          <td>${escapeHtml(row.property_type_category || row.property_type || "Unknown")}</td>
          <td>${escapeHtml(row.bedroom_category || "Unknown")}</td>
          <td>${escapeHtml(row.deposit_category || "Unknown")}</td>
          <td>${escapeHtml(buildTagSummary(row))}</td>
          <td>${row.listing_url ? `<a class="dataset-link" href="${escapeAttribute(row.listing_url)}" target="_blank" rel="noreferrer">Open</a>` : "N/A"}</td>
        </tr>
      `,
    )
    .join("");
}

function buildTagSummary(row) {
  const tags = [];
  for (const field of [
    "build_to_rent_category",
    "student_category",
    "price_reduced_category",
    "zero_deposit_category",
    "online_viewings_category",
    "luxury_category",
    "investment_opportunity_category",
  ]) {
    if (row[field] && !String(row[field]).startsWith("Not ") && row[field] !== "Unknown" && row[field] !== "No online viewing flag") {
      tags.push(row[field]);
    }
  }
  return tags.length ? tags.join(" · ") : "No highlighted tags";
}

function downloadDatasetCsv() {
  if (!state.listings) {
    return;
  }
  const rows = getFilteredDatasetRows();
  const headers = [
    "run_timestamp",
    "run_date",
    "london_borough",
    "display_address",
    "location",
    "postcode",
    "price_amount",
    "price_frequency",
    "property_type_category",
    "bedroom_category",
    "bathrooms",
    "deposit_amount",
    "deposit_category",
    "furnish_type_category",
    "let_type_category",
    "build_to_rent_category",
    "student_category",
    "price_reduced_category",
    "zero_deposit_category",
    "online_viewings_category",
    "pets_category",
    "bills_category",
    "luxury_category",
    "investment_opportunity_category",
    "listing_url",
  ];
  const lines = [headers.join(",")];
  for (const row of rows) {
    lines.push(headers.map((header) => csvEscape(row[header])).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "london-rental-filtered-data.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function renderChart(existingChart, selector, config) {
  if (existingChart) {
    existingChart.destroy();
  }
  return new Chart(document.querySelector(selector), config);
}

function chartOptions(valueFormatter, { yScaleType = "linear" } = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    scales: {
      y: {
        type: yScaleType,
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

function groupBy(rows, keyFn) {
  const grouped = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    if (!grouped.has(key)) {
      grouped.set(key, []);
    }
    grouped.get(key).push(row);
  }
  return grouped;
}

function uniqueOrdered(values) {
  return [...new Set(values)];
}

function paletteColor(index) {
  const colors = ["#255f5a", "#b84f2d", "#8f6d1f", "#556ab4", "#7c4d79", "#2e7d32", "#8a3b12", "#546e7a", "#ad1457"];
  return colors[index % colors.length];
}

function paletteFill(index) {
  const fills = [
    "rgba(37, 95, 90, 0.18)",
    "rgba(184, 79, 45, 0.18)",
    "rgba(143, 109, 31, 0.18)",
    "rgba(85, 106, 180, 0.18)",
    "rgba(124, 77, 121, 0.18)",
    "rgba(46, 125, 50, 0.18)",
    "rgba(138, 59, 18, 0.18)",
    "rgba(84, 110, 122, 0.18)",
    "rgba(173, 20, 87, 0.18)",
  ];
  return fills[index % fills.length];
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

function csvEscape(value) {
  if (value === null || value === undefined) {
    return "";
  }
  const text = String(value).replaceAll('"', '""');
  return `"${text}"`;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;");
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
