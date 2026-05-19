library(jsonlite)
library(htmltools)

options(stringsAsFactors = FALSE)

project_root <- normalizePath(".", winslash = "/", mustWork = TRUE)
output_dir <- file.path(project_root, "Scraper", "output")

find_latest_dataset <- function(output_dir) {
  json_files <- list.files(
    output_dir,
    pattern = "^rightmove_enriched_results_.*\\.json$",
    full.names = TRUE
  )
  csv_files <- list.files(
    output_dir,
    pattern = "^rightmove_enriched_results_.*\\.csv$",
    full.names = TRUE
  )

  if (length(json_files)) {
    return(json_files[which.max(file.info(json_files)$mtime)])
  }

  if (length(csv_files)) {
    return(csv_files[which.max(file.info(csv_files)$mtime)])
  }

  sample_path <- file.path(project_root, "Sample_Results_For_Testing.json")
  if (file.exists(sample_path)) {
    return(sample_path)
  }

  stop("No enriched Rightmove dataset was found in Scraper/output.")
}

`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0) y else x
}

coalesce_text <- function(...) {
  values <- list(...)
  for (value in values) {
    if (!is.null(value) && length(value) > 0) {
      text <- as.character(value[[1]])
      if (!is.na(text) && nzchar(trimws(text))) {
        return(text)
      }
    }
  }
  ""
}

extract_url_list <- function(value) {
  if (is.null(value) || length(value) == 0) {
    return(character())
  }

  if (is.list(value)) {
    value <- unlist(value, use.names = FALSE)
  }

  if (length(value) == 1 && is.character(value)) {
    text <- trimws(value)
    if (!nzchar(text)) {
      return(character())
    }
    if (startsWith(text, "[")) {
      parsed <- tryCatch(fromJSON(text), error = function(e) character())
      value <- parsed %||% character()
    } else {
      value <- text
    }
  }

  urls <- unique(as.character(value))
  urls <- urls[grepl("^https?://", urls)]
  urls <- urls[!grepl("&quot;|javascript:", urls)]
  urls
}

postcode_area <- function(postcode) {
  if (is.null(postcode) || is.na(postcode) || !nzchar(trimws(postcode))) {
    return("Unknown postcode area")
  }

  area <- sub("^([A-Z]{1,2}).*$", "\\1", toupper(trimws(postcode)))
  if (!nzchar(area)) "Unknown postcode area" else area
}

fmt_price <- function(value) {
  paste0("£", format(round(value), big.mark = ",", scientific = FALSE, trim = TRUE))
}

make_card_title <- function(bedrooms, property_type) {
  bedroom_text <- trimws(as.character(bedrooms))
  property_text <- trimws(as.character(property_type))

  if (!nzchar(property_text)) {
    property_text <- "Property"
  }

  if (!nzchar(bedroom_text) || identical(bedroom_text, "Not listed") || is.na(bedroom_text)) {
    return(property_text)
  }

  paste0(bedroom_text, "-bed ", property_text)
}

load_properties <- function(dataset_path) {
  if (grepl("\\.json$", dataset_path, ignore.case = TRUE)) {
    raw_data <- fromJSON(dataset_path, simplifyVector = FALSE)
    rows <- raw_data$results %||% raw_data
  } else {
    csv_data <- read.csv(dataset_path, stringsAsFactors = FALSE)
    rows <- split(csv_data, seq_len(nrow(csv_data)))
    rows <- lapply(rows, function(row) as.list(row[1, , drop = FALSE]))
  }

  records <- lapply(rows, function(row) {
    price_amount <- suppressWarnings(as.numeric(coalesce_text(row$price_amount, row$price)))
    latitude <- suppressWarnings(as.numeric(coalesce_text(row$latitude)))
    longitude <- suppressWarnings(as.numeric(coalesce_text(row$longitude)))
    postcode <- coalesce_text(row$postcode, row$search_postcode)
    property_type <- coalesce_text(row$property_type, "Property")
    bedrooms <- coalesce_text(row$bedrooms, "Not listed")
    bathrooms <- coalesce_text(row$bathrooms, "Not listed")
    tenure <- coalesce_text(row$tenure, "Not listed")
    size_text <- coalesce_text(row$size_text, "Not listed")
    added_text <- coalesce_text(row$added_text, "Date not listed")
    description <- coalesce_text(row$description, row$summary, row$search_summary)

    image_urls <- extract_url_list(row$image_urls)
    if (!length(image_urls)) {
      image_urls <- extract_url_list(row$property_photo_urls)
    }
    if (!length(image_urls)) {
      image_urls <- extract_url_list(row$search_image_urls)
    }
    if (!length(image_urls)) {
      image_urls <- extract_url_list(row$image_url)
    }

    list(
      listing_id = coalesce_text(row$listing_id),
      card_title = make_card_title(bedrooms, property_type),
      price_amount = price_amount,
      location = coalesce_text(row$location, row$display_address, row$search_location),
      postcode = postcode,
      area_group = postcode_area(postcode),
      property_type = property_type,
      bedrooms = bedrooms,
      bathrooms = bathrooms,
      tenure = tenure,
      size_text = size_text,
      added_text = added_text,
      description = description,
      latitude = if (is.finite(latitude)) latitude else NULL,
      longitude = if (is.finite(longitude)) longitude else NULL,
      image_urls = head(image_urls, 3)
    )
  })

  records <- Filter(function(row) {
    is.finite(row$price_amount) && row$price_amount > 0
  }, records)

  if (!length(records)) {
    stop("The dataset loaded successfully, but no listings had a valid price.")
  }

  records
}

dataset_path <- find_latest_dataset(output_dir)
properties <- load_properties(dataset_path)

price_values <- vapply(properties, `[[`, numeric(1), "price_amount")
area_choices <- sort(unique(vapply(properties, `[[`, character(1), "area_group")))

data_summary <- list(
  dataset_name = basename(dataset_path),
  listing_count = length(properties),
  min_price = min(price_values, na.rm = TRUE),
  median_price = median(price_values, na.rm = TRUE),
  max_price = max(price_values, na.rm = TRUE),
  postcode_areas = length(area_choices)
)

payload <- list(
  dataset = data_summary,
  area_choices = area_choices,
  properties = properties
)

payload_json <- toJSON(payload, auto_unbox = TRUE, null = "null", digits = 12)
payload_json <- gsub("</", "<\\\\/", payload_json, fixed = TRUE)

game_css <- paste(
  c(
    ".hp-game {font-family: Georgia, 'Times New Roman', serif; color: #1e2430;}",
    ".hp-shell {background: linear-gradient(180deg, #f6f1e8 0%, #ece3d5 100%); border: 1px solid #d9ccb8; border-radius: 22px; padding: 22px; box-shadow: 0 10px 30px rgba(66, 45, 21, 0.08);}",
    ".hp-topline {display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 20px; flex-wrap: wrap;}",
    ".hp-title h2 {margin: 0 0 6px 0; font-size: 2rem;}",
    ".hp-title p {margin: 0; max-width: 760px; color: #5c6470;}",
    ".hp-badge {background: rgba(255, 255, 255, 0.78); border: 1px solid #d6c8b3; border-radius: 14px; padding: 12px 14px; min-width: 260px;}",
    ".hp-grid {display: grid; grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr); gap: 18px; align-items: start;}",
    ".hp-card, .hp-panel, .hp-viz {background: rgba(255, 255, 255, 0.92); border: 1px solid #ddd1c0; border-radius: 18px; padding: 18px;}",
    ".hp-card h3, .hp-panel h3, .hp-viz h3 {margin-top: 0; margin-bottom: 12px;}",
    ".hp-photo-grid {display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 14px;}",
    ".hp-photo-grid img, .hp-photo-placeholder {width: 100%; height: 220px; object-fit: cover; border-radius: 12px; border: 1px solid #d7d7d7; background: #f4f4f4;}",
    ".hp-photo-placeholder {display: flex; align-items: center; justify-content: center; color: #69707a; font-size: 0.95rem;}",
    ".hp-meta {display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-top: 14px;}",
    ".hp-meta-box {background: #f6efe4; border-radius: 12px; padding: 12px; min-height: 78px;}",
    ".hp-meta-box strong {display: block; font-size: 0.9rem; color: #725533; margin-bottom: 6px;}",
    ".hp-note {margin-top: 14px; color: #7c5a22; font-weight: 600;}",
    ".hp-side-stack {display: grid; gap: 14px;}",
    ".hp-score-line {display: flex; justify-content: space-between; gap: 10px; margin: 6px 0;}",
    ".hp-control label {display: block; font-weight: 700; margin-bottom: 6px;}",
    ".hp-control input, .hp-control select {width: 100%; padding: 10px 12px; border-radius: 12px; border: 1px solid #cbb89d; background: white; font-size: 1rem;}",
    ".hp-actions {display: grid; grid-template-columns: 1fr 1fr; gap: 10px;}",
    ".hp-actions button {border: 0; border-radius: 12px; padding: 11px 12px; font-size: 0.98rem; font-weight: 700; cursor: pointer;}",
    ".hp-submit {background: #1f4d4f; color: white;}",
    ".hp-next {background: #d7c0a1; color: #2d2418;}",
    ".hp-reveal {background: #efe5d4; border: 1px solid #d3c09e; border-radius: 14px; padding: 14px;}",
    ".hp-reveal p {margin: 6px 0;}",
    ".hp-viz-grid {display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 18px;}",
    ".hp-range {margin-top: 10px;}",
    ".hp-range-track {position: relative; height: 14px; background: linear-gradient(90deg, #d9d2c8 0%, #d1b891 100%); border-radius: 999px;}",
    ".hp-range-marker {position: absolute; top: -6px; width: 4px; height: 26px; border-radius: 999px;}",
    ".hp-range-marker.actual {background: #9f2f20;}",
    ".hp-range-marker.guess {background: #1f4d4f;}",
    ".hp-range-scale {display: flex; justify-content: space-between; color: #5f6470; font-size: 0.88rem; margin-top: 8px;}",
    ".hp-legend {display: flex; gap: 14px; margin-top: 10px; color: #4b525d; font-size: 0.92rem; flex-wrap: wrap;}",
    ".hp-dot {display: inline-block; width: 10px; height: 10px; border-radius: 999px; margin-right: 6px;}",
    ".hp-empty {color: #69707a; background: #f5f5f5; border: 1px dashed #cfcfcf; border-radius: 12px; padding: 18px;}",
    ".hp-footnote {margin-top: 16px; color: #5c6470; font-size: 0.92rem;}",
    ".hp-viz svg {width: 100%; height: 280px; display: block; background: #fbfaf8; border-radius: 12px; border: 1px solid #e2ddd5;}",
    "@media (max-width: 960px) {.hp-grid, .hp-viz-grid {grid-template-columns: 1fr;} .hp-photo-grid {grid-template-columns: 1fr;} .hp-meta {grid-template-columns: 1fr;} .hp-actions {grid-template-columns: 1fr;}}"
  ),
  collapse = "\n"
)

game_js <- paste(
  c(
    "(function () {",
    "  const dataNode = document.getElementById('house-game-data');",
    "  const mount = document.getElementById('house-game-root');",
    "  if (!dataNode || !mount) return;",
    "  const payload = JSON.parse(dataNode.textContent);",
    "  const properties = payload.properties || [];",
    "  const dataset = payload.dataset || {};",
    "  const areaChoices = payload.area_choices || [];",
    "  if (!properties.length) {",
    "    mount.innerHTML = '<div class=\"hp-empty\">No properties were loaded into the game.</div>';",
    "    return;",
    "  }",
    "",
    "  const state = {",
    "    filter: 'All areas',",
    "    available: properties.map((_, index) => index),",
    "    used: [],",
    "    current: 0,",
    "    round: 1,",
    "    totalScore: 0,",
    "    revealed: false,",
    "    lastGuess: null,",
    "    lastScore: null",
    "  };",
    "",
    "  const els = {",
    "    summary: document.getElementById('hp-dataset-summary'),",
    "    filter: document.getElementById('hp-area-filter'),",
    "    card: document.getElementById('hp-card'),",
    "    score: document.getElementById('hp-score'),",
    "    guess: document.getElementById('hp-guess'),",
    "    submit: document.getElementById('hp-submit'),",
    "    next: document.getElementById('hp-next'),",
    "    reveal: document.getElementById('hp-reveal'),",
    "    priceViz: document.getElementById('hp-price-viz'),",
    "    geoViz: document.getElementById('hp-geo-viz')",
    "  };",
    "",
    "  function fmtPrice(value) {",
    "    return new Intl.NumberFormat('en-GB', {",
    "      style: 'currency',",
    "      currency: 'GBP',",
    "      maximumFractionDigits: 0",
    "    }).format(value);",
    "  }",
    "",
    "  function scoreGuess(guess, actual) {",
    "    if (!Number.isFinite(guess) || !Number.isFinite(actual) || guess <= 0 || actual <= 0) return 0;",
    "    const error = Math.abs(Math.log(guess / actual));",
    "    return Math.round(1000 * Math.exp(-2.4 * error));",
    "  }",
    "",
    "  function median(values) {",
    "    const sorted = values.filter(Number.isFinite).slice().sort((a, b) => a - b);",
    "    if (!sorted.length) return 250000;",
    "    const middle = Math.floor(sorted.length / 2);",
    "    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;",
    "  }",
    "",
    "  function stepSize(values) {",
    "    const med = median(values);",
    "    if (med >= 1000000) return 25000;",
    "    if (med >= 500000) return 10000;",
    "    return 5000;",
    "  }",
    "",
    "  function roundToStep(value, step) {",
    "    return Math.max(step, Math.round(value / step) * step);",
    "  }",
    "",
    "  function randomChoice(values) {",
    "    return values[Math.floor(Math.random() * values.length)];",
    "  }",
    "",
    "  function escapeHtml(text) {",
    "    return String(text ?? '').replace(/[&<>\\\"']/g, function (char) {",
    "      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '\\\"': '&quot;', \"'\": '&#39;' })[char];",
    "    });",
    "  }",
    "",
    "  function currentProperty() {",
    "    return properties[state.current];",
    "  }",
    "",
    "  function poolIndices() {",
    "    if (state.filter === 'All areas') return properties.map((_, index) => index);",
    "    return properties.map((row, index) => row.area_group === state.filter ? index : -1).filter(index => index >= 0);",
    "  }",
    "",
    "  function resetForFilter() {",
    "    state.available = poolIndices();",
    "    state.used = [];",
    "    state.round = 1;",
    "    state.totalScore = 0;",
    "    state.revealed = false;",
    "    state.lastGuess = null;",
    "    state.lastScore = null;",
    "    state.current = randomChoice(state.available);",
    "    const poolPrices = state.available.map(index => properties[index].price_amount);",
    "    const step = stepSize(poolPrices);",
    "    els.guess.step = String(step);",
    "    els.guess.value = String(roundToStep(median(poolPrices), step));",
    "    renderAll();",
    "  }",
    "",
    "  function imageMarkup(url) {",
    "    if (!url) {",
    "      return '<div class=\"hp-photo-placeholder\">No image available</div>';",
    "    }",
    "    return '<img src=\"' + escapeHtml(url) + '\" alt=\"Property image\">';",
    "  }",
    "",
    "  function renderSummary() {",
    "    const poolLabel = state.filter === 'All areas' ? 'all postcode areas' : state.filter;",
    "    els.summary.innerHTML = '' +",
    "      '<strong>Dataset:</strong> ' + escapeHtml(dataset.dataset_name || 'Rightmove export') + '<br>' +",
    "      '<strong>Listings:</strong> ' + properties.length + ' total, ' + state.available.length + ' in the current pool<br>' +",
    "      '<strong>Price range:</strong> ' + fmtPrice(dataset.min_price) + ' to ' + fmtPrice(dataset.max_price) + '<br>' +",
    "      '<strong>Current filter:</strong> ' + escapeHtml(poolLabel);",
    "  }",
    "",
    "  function renderScore() {",
    "    els.score.innerHTML = '' +",
    "      '<h3>Score</h3>' +",
    "      '<div class=\"hp-score-line\"><span>Round</span><strong>' + state.round + '</strong></div>' +",
    "      '<div class=\"hp-score-line\"><span>Total score</span><strong>' + state.totalScore + '</strong></div>' +",
    "      '<div class=\"hp-score-line\"><span>Listings played</span><strong>' + state.used.length + '</strong></div>' +",
    "      '<div class=\"hp-score-line\"><span>Pool size</span><strong>' + state.available.length + '</strong></div>';",
    "  }",
    "",
    "  function renderCard() {",
    "    const row = currentProperty();",
    "    const images = (row.image_urls || []).slice(0, 3);",
    "    while (images.length < 3) images.push(null);",
    "    els.card.innerHTML = '' +",
    "      '<h3>' + escapeHtml(row.card_title) + '</h3>' +",
    "      '<div class=\"hp-photo-grid\">' + images.map(imageMarkup).join('') + '</div>' +",
    "      '<div class=\"hp-meta\">' +",
    "        '<div class=\"hp-meta-box\"><strong>Property type</strong>' + escapeHtml(row.property_type || 'Not listed') + '</div>' +",
    "        '<div class=\"hp-meta-box\"><strong>Bedrooms / bathrooms</strong>' + escapeHtml((row.bedrooms || 'Not listed') + ' bed, ' + (row.bathrooms || 'Not listed') + ' bath') + '</div>' +",
    "        '<div class=\"hp-meta-box\"><strong>Tenure</strong>' + escapeHtml(row.tenure || 'Not listed') + '</div>' +",
    "        '<div class=\"hp-meta-box\"><strong>Added</strong>' + escapeHtml(row.added_text || 'Date not listed') + '</div>' +",
    "      '</div>' +",
    "      '<p class=\"hp-note\">Street and postcode stay hidden until after you guess.</p>';" ,
    "  }",
    "",
    "  function renderReveal() {",
    "    if (!state.revealed) {",
    "      els.reveal.innerHTML = '<div class=\"hp-empty\">Submit a guess to reveal the actual price, address, and comparison visuals.</div>';",
    "      return;",
    "    }",
    "    const row = currentProperty();",
    "    const actual = row.price_amount;",
    "    const pctOff = Math.round((Math.abs(state.lastGuess - actual) / actual) * 1000) / 10;",
    "    els.reveal.innerHTML = '' +",
    "      '<div class=\"hp-reveal\">' +",
    "        '<h3>Round Result</h3>' +",
    "        '<p><strong>Actual price:</strong> ' + fmtPrice(actual) + '</p>' +",
    "        '<p><strong>Your guess:</strong> ' + fmtPrice(state.lastGuess) + '</p>' +",
    "        '<p><strong>Error:</strong> ' + pctOff + '%</p>' +",
    "        '<p><strong>Round score:</strong> ' + state.lastScore + ' / 1000</p>' +",
    "        '<hr>' +",
    "        '<p><strong>Location:</strong> ' + escapeHtml(row.location || 'Not listed') + '</p>' +",
    "        '<p><strong>Postcode:</strong> ' + escapeHtml(row.postcode || 'Not listed') + '</p>' +",
    "        '<p><strong>Postcode area:</strong> ' + escapeHtml(row.area_group || 'Unknown postcode area') + '</p>' +",
    "        '<p><strong>Description:</strong> ' + escapeHtml(row.description || 'No description available.') + '</p>' +",
    "      '</div>';",
    "  }",
    "",
    "  function renderPriceViz() {",
    "    if (!state.revealed) {",
    "      els.priceViz.innerHTML = '<div class=\"hp-empty\">Price comparison appears after you submit a guess.</div>';",
    "      return;",
    "    }",
    "    const poolPrices = state.available.map(index => properties[index].price_amount).filter(Number.isFinite);",
    "    const min = Math.min.apply(null, poolPrices);",
    "    const max = Math.max.apply(null, poolPrices);",
    "    const actual = currentProperty().price_amount;",
    "    const guess = state.lastGuess;",
    "    const span = Math.max(max - min, 1);",
    "    const actualPct = ((actual - min) / span) * 100;",
    "    const guessPct = ((guess - min) / span) * 100;",
    "    const med = median(poolPrices);",
    "    els.priceViz.innerHTML = '' +",
    "      '<h3>Price Context</h3>' +",
    "      '<p>This shows where the current listing and your guess sit within the active pool.</p>' +",
    "      '<div class=\"hp-range\">' +",
    "        '<div class=\"hp-range-track\">' +",
    "          '<span class=\"hp-range-marker actual\" style=\"left: calc(' + actualPct + '% - 2px);\"></span>' +",
    "          '<span class=\"hp-range-marker guess\" style=\"left: calc(' + guessPct + '% - 2px);\"></span>' +",
    "        '</div>' +",
    "        '<div class=\"hp-range-scale\"><span>' + fmtPrice(min) + '</span><span>Median ' + fmtPrice(med) + '</span><span>' + fmtPrice(max) + '</span></div>' +",
    "      '</div>' +",
    "      '<div class=\"hp-legend\">' +",
    "        '<span><span class=\"hp-dot\" style=\"background:#9f2f20\"></span>Actual listing</span>' +",
    "        '<span><span class=\"hp-dot\" style=\"background:#1f4d4f\"></span>Your guess</span>' +",
    "      '</div>';",
    "  }",
    "",
    "  function renderGeoViz() {",
    "    if (!state.revealed) {",
    "      els.geoViz.innerHTML = '<div class=\"hp-empty\">The location comparison appears after you submit a guess.</div>';",
    "      return;",
    "    }",
    "    const rows = state.available.map(index => properties[index]).filter(row => Number.isFinite(row.longitude) && Number.isFinite(row.latitude));",
    "    if (!rows.length) {",
    "      els.geoViz.innerHTML = '<div class=\"hp-empty\">No latitude/longitude values are available in the current pool.</div>';",
    "      return;",
    "    }",
    "    const current = currentProperty();",
    "    const longs = rows.map(row => row.longitude);",
    "    const lats = rows.map(row => row.latitude);",
    "    const minLon = Math.min.apply(null, longs);",
    "    const maxLon = Math.max.apply(null, longs);",
    "    const minLat = Math.min.apply(null, lats);",
    "    const maxLat = Math.max.apply(null, lats);",
    "    const width = 480;",
    "    const height = 280;",
    "    const pad = 28;",
    "    const xPos = lon => pad + ((lon - minLon) / Math.max(maxLon - minLon, 0.0001)) * (width - pad * 2);",
    "    const yPos = lat => height - pad - ((lat - minLat) / Math.max(maxLat - minLat, 0.0001)) * (height - pad * 2);",
    "    const points = rows.map(row => '<circle cx=\"' + xPos(row.longitude).toFixed(1) + '\" cy=\"' + yPos(row.latitude).toFixed(1) + '\" r=\"4\" fill=\"#b7bec8\"></circle>').join('');",
    "    const currentPoint = Number.isFinite(current.longitude) && Number.isFinite(current.latitude)",
    "      ? '<circle cx=\"' + xPos(current.longitude).toFixed(1) + '\" cy=\"' + yPos(current.latitude).toFixed(1) + '\" r=\"6\" fill=\"#c44536\"></circle><text x=\"' + (xPos(current.longitude) + 8).toFixed(1) + '\" y=\"' + (yPos(current.latitude) - 8).toFixed(1) + '\" font-size=\"12\" fill=\"#7b241c\">Current</text>'",
    "      : '';",
    "    els.geoViz.innerHTML = '' +",
    "      '<h3>Geographic Context</h3>' +",
    "      '<p>The red point shows the current property relative to the rest of the filtered pool.</p>' +",
    "      '<svg viewBox=\"0 0 480 280\" preserveAspectRatio=\"xMidYMid meet\">' +",
    "        '<rect x=\"0\" y=\"0\" width=\"480\" height=\"280\" fill=\"#fbfaf8\"></rect>' +",
    "        '<line x1=\"28\" y1=\"252\" x2=\"452\" y2=\"252\" stroke=\"#b8b8b8\"></line>' +",
    "        '<line x1=\"28\" y1=\"28\" x2=\"28\" y2=\"252\" stroke=\"#b8b8b8\"></line>' +",
    "        + points + currentPoint +",
    "      '</svg>';",
    "  }",
    "",
    "  function renderAll() {",
    "    renderSummary();",
    "    renderScore();",
    "    renderCard();",
    "    renderReveal();",
    "    renderPriceViz();",
    "    renderGeoViz();",
    "  }",
    "",
    "  function handleSubmit() {",
    "    if (state.revealed) return;",
    "    const guess = Number(els.guess.value);",
    "    if (!Number.isFinite(guess) || guess <= 0) return;",
    "    const actual = currentProperty().price_amount;",
    "    state.lastGuess = guess;",
    "    state.lastScore = scoreGuess(guess, actual);",
    "    state.totalScore += state.lastScore;",
    "    state.revealed = true;",
    "    if (!state.used.includes(state.current)) state.used.push(state.current);",
    "    renderAll();",
    "  }",
    "",
    "  function handleNext() {",
    "    if (!state.revealed) return;",
    "    const allUsed = state.available.every(index => state.used.includes(index));",
    "    if (allUsed) {",
    "      state.used = [];",
    "      state.round = 1;",
    "      state.totalScore = 0;",
    "    } else {",
    "      state.round += 1;",
    "    }",
    "    const remaining = state.available.filter(index => !state.used.includes(index));",
    "    state.current = randomChoice(remaining.length ? remaining : state.available);",
    "    state.revealed = false;",
    "    state.lastGuess = null;",
    "    state.lastScore = null;",
    "    const poolPrices = state.available.map(index => properties[index].price_amount);",
    "    els.guess.value = String(roundToStep(median(poolPrices), Number(els.guess.step) || 5000));",
    "    renderAll();",
    "  }",
    "",
    "  function buildFilterOptions() {",
    "    const options = ['All areas'].concat(areaChoices).map(function (label) {",
    "      return '<option value=\"' + escapeHtml(label) + '\">' + escapeHtml(label) + '</option>';",
    "    });",
    "    els.filter.innerHTML = options.join('');",
    "  }",
    "",
    "  buildFilterOptions();",
    "  els.filter.addEventListener('change', function () {",
    "    state.filter = els.filter.value;",
    "    resetForFilter();",
    "  });",
    "  els.submit.addEventListener('click', handleSubmit);",
    "  els.next.addEventListener('click', handleNext);",
    "  state.current = randomChoice(state.available);",
    "  resetForFilter();",
    "})();"
  ),
  collapse = "\n"
)

browsable(
  tagList(
    tags$style(HTML(game_css)),
    tags$div(
      class = "hp-game",
      tags$div(
        class = "hp-shell",
        tags$div(
          class = "hp-topline",
          tags$div(
            class = "hp-title",
            tags$h2("Guess The UK House Price"),
            tags$p(
              "Use the photos and property clues to estimate the asking price.",
              "After you guess, the page reveals the real price and where the home sits in the wider regional dataset."
            )
          ),
          tags$div(
            class = "hp-badge",
            tags$div(id = "hp-dataset-summary")
          )
        ),
        tags$div(
          class = "hp-grid",
          tags$div(
            class = "hp-card",
            tags$div(id = "hp-card")
          ),
          tags$div(
            class = "hp-side-stack",
            tags$div(
              class = "hp-panel",
              tags$div(id = "hp-score")
            ),
            tags$div(
              class = "hp-panel",
              tags$div(
                class = "hp-control",
                tags$label(`for` = "hp-area-filter", "Postcode area filter"),
                tags$select(id = "hp-area-filter")
              ),
              tags$div(
                class = "hp-control",
                style = "margin-top: 12px;",
                tags$label(`for` = "hp-guess", "Your price guess"),
                tags$input(
                  id = "hp-guess",
                  type = "number",
                  min = "50000",
                  step = "5000",
                  value = round(data_summary$median_price, -4)
                )
              ),
              tags$div(
                class = "hp-actions",
                style = "margin-top: 14px;",
                tags$button(id = "hp-submit", class = "hp-submit", "Submit guess"),
                tags$button(id = "hp-next", class = "hp-next", "Next property")
              )
            ),
            tags$div(
              class = "hp-panel",
              tags$div(id = "hp-reveal")
            )
          )
        ),
        tags$div(
          class = "hp-viz-grid",
          tags$div(class = "hp-viz", id = "hp-price-viz"),
          tags$div(class = "hp-viz", id = "hp-geo-viz")
        ),
        tags$p(
          class = "hp-footnote",
          sprintf(
            "Current dataset: %s. The notebook uses the newest enriched Rightmove export in Scraper/output when it is knitted.",
            data_summary$dataset_name
          )
        )
      )
    ),
    tags$script(id = "house-game-data", type = "application/json", HTML(payload_json)),
    tags$script(HTML(game_js))
  )
)
