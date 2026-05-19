"""
Rightmove Rental Detail Scraper
===============================

Enriches the rental search-stage dataset by visiting each listing page and extracting:
  - full property image URLs
  - floorplan URLs
  - EPC URLs
  - description text
  - key features text
  - rental facts such as deposit / furnishing / min tenancy / council tax / let availability
  - lat/lng from page source

Example:
  python3 Scraper/rightmove_rental_detail_scraper.py --limit 10
  python3 Scraper/rightmove_rental_detail_scraper.py --search-results-json Scraper/output/rightmove_rental_search_results_20260325_023210.json
"""

import argparse
import csv
import json
import re
import time
from datetime import datetime
from pathlib import Path

import undetected_chromedriver as uc
from selenium.common.exceptions import NoSuchWindowException, WebDriverException


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
PHOTO_URL_RE = re.compile(r"https://media\.rightmove\.co\.uk/[^\s\"'>]*property-photo[^\s\"'>]+", re.IGNORECASE)
FLOORPLAN_URL_RE = re.compile(r"https://media\.rightmove\.co\.uk/[^\s\"'>]*property-floorplan[^\s\"'>]+", re.IGNORECASE)
EPC_URL_RE = re.compile(r"https://media\.rightmove\.co\.uk/[^\s\"'>]*property-epc[^\s\"'>]+", re.IGNORECASE)
POSTCODE_RE = re.compile(
    r"(?i)\b(?:GIR\s?0AA|(?:[A-PR-UWYZ][A-HK-Y]?\d[A-HJKPSTUW]?|"
    r"[A-PR-UWYZ][A-HK-Y]?\d{2}|[A-PR-UWYZ][A-HK-Y]?\d[ABEHMNPRVWXY])"
    r"\s?\d[ABD-HJLNP-UW-Z]{2})\b"
)

SOURCE_PATTERNS = {
    "display_address": r'"displayAddress"\s*:\s*"([^"]+)"',
    "lat_lng": r'"latitude"\s*:\s*([-\d.]+).*?"longitude"\s*:\s*([-\d.]+)',
    "property_photo_urls": r'https://media\.rightmove\.co\.uk/[^\s"\']*property-photo[^\s"\']+',
    "property_floorplan_urls": r'https://media\.rightmove\.co\.uk/[^\s"\']*property-floorplan[^\s"\']+',
    "epc_urls": r'https://media\.rightmove\.co\.uk/[^\s"\']*property-epc[^\s"\']+',
}
MEDIA_SOURCE_KEYS = {"property_photo_urls", "property_floorplan_urls", "epc_urls"}

DESCRIPTION_STOP_MARKERS = [
    "About the agent",
    "Affordability",
    "Letting information",
    "Request details",
    "Email agent",
    "Staying secure when looking for a property",
]

FEATURES_STOP_MARKERS = [
    "Description",
    "About the agent",
    "Affordability",
    "Letting information",
]

AGENT_TEXT_MARKERS = [
    "estate agents",
    "estate agent",
    "sales and lettings",
    "sales & lettings",
    "lettings",
    "branch",
    "office",
    "call agent",
    "email agent",
    "contact branch",
    "limited",
    "ltd",
]
PETS_PHRASES = [
    "pets considered",
    "pets allowed",
    "pet friendly",
    "no pets",
]
BILLS_PHRASES = [
    "bills included",
    "excluding bills",
    "council tax included",
    "all bills included",
    "some bills included",
]
STUDENT_PHRASES = [
    "student friendly",
    "student welcome",
    "students welcome",
    "students can enquire",
    "students considered",
    "ideal for students",
]
ZERO_DEPOSIT_PHRASES = [
    "zero deposit",
    "no deposit option",
]
INVESTMENT_PHRASES = [
    "investment opportunity",
    "ideal investment",
    "great investment",
    "buy-to-let",
    "buy to let",
    "investor",
    "investment purchase",
    "rental investment",
]
LUXURY_PHRASES = [
    "luxury",
    "luxurious",
    "premium",
    "high-end",
    "high end",
    "exclusive",
    "prestigious",
]


def parse_args():
    parser = argparse.ArgumentParser(description="Enrich Rightmove rental search results with detail-page data.")
    parser.add_argument(
        "--search-results-json",
        help="Rental search scraper JSON output. If omitted, the latest output/rightmove_rental_search_results_*.json is used.",
    )
    parser.add_argument("--limit", type=int, help="Optional limit for test runs.")
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=0.75,
        help="Extra settle delay after the detail page looks ready.",
    )
    parser.add_argument(
        "--page-timeout",
        type=float,
        default=12.0,
        help="How long to wait for a detail page to become scraper-ready.",
    )
    parser.add_argument(
        "--block-images",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Block image/media downloads in the detail browser to speed up navigation.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for enriched output files.")
    parser.add_argument("--run-dir", help="Checkpoint directory for this detail run.")
    parser.add_argument("--resume", action="store_true", help="Resume a previous detail run from its checkpoint data.")
    parser.add_argument(
        "--interactive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pause for manual cookie/CAPTCHA handling before scraping. Disable for automation runs.",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Run Chrome headlessly. Useful for unattended automation.",
    )
    parser.add_argument(
        "--user-data-dir",
        help="Optional Chrome user-data directory to reuse cookies/session state across automated runs.",
    )
    return parser.parse_args()


def setup_browser(block_images=True, *, headless=False, user_data_dir=None):
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1600,1200")
    if headless:
        options.add_argument("--headless=new")
    if user_data_dir:
        options.add_argument(f"--user-data-dir={user_data_dir}")
    options.page_load_strategy = "eager"
    driver = uc.Chrome(options=options, version_main=None)
    if block_images:
        _enable_network_blocking(driver)
    return driver


def prompt_manual_ready():
    input(
        "\nHandle cookies / CAPTCHA / login in the browser if needed, "
        "then press Enter here to start the rental detail scraping pass..."
    )


def _safe_quit(driver):
    if not driver:
        return
    try:
        driver.quit()
    except Exception:
        pass


def _driver_window_available(driver):
    if not driver:
        return False
    try:
        return bool(driver.window_handles)
    except WebDriverException:
        return False


def _enable_network_blocking(driver):
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd(
            "Network.setBlockedURLs",
            {
                "urls": [
                    "*.jpg",
                    "*.jpeg",
                    "*.png",
                    "*.webp",
                    "*.gif",
                    "*.avif",
                    "*.svg",
                ]
            },
        )
    except Exception:
        pass


def _recreate_browser(driver, landing_url, reprompt, block_images, headless, user_data_dir):
    _safe_quit(driver)
    driver = setup_browser(block_images=block_images, headless=headless, user_data_dir=user_data_dir)
    driver.get(landing_url)
    if reprompt:
        prompt_manual_ready()
    return driver


def _navigate_with_recovery(driver, url, block_images, reprompt, headless, user_data_dir):
    try:
        if not _driver_window_available(driver):
            return _recreate_browser(
                driver,
                url,
                reprompt=reprompt,
                block_images=block_images,
                headless=headless,
                user_data_dir=user_data_dir,
            )
        driver.get(url)
        return driver
    except NoSuchWindowException:
        return _recreate_browser(
            driver,
            url,
            reprompt=reprompt,
            block_images=block_images,
            headless=headless,
            user_data_dir=user_data_dir,
        )


def _latest_detail_run_dir(output_dir):
    candidates = sorted(
        Path(output_dir).glob("rental_detail_run_*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _resolve_run_dir(output_dir, run_dir_arg, resume):
    if run_dir_arg:
        run_dir = Path(run_dir_arg)
        if not run_dir.is_absolute():
            run_dir = Path(output_dir) / run_dir
        return run_dir
    if resume:
        latest = _latest_detail_run_dir(output_dir)
        if latest:
            return latest
        raise FileNotFoundError("No previous rental detail run directory found to resume.")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(output_dir) / f"rental_detail_run_{timestamp}"


def _progress_file(run_dir):
    return Path(run_dir) / "progress.json"


def _raw_pages_dir(run_dir):
    return Path(run_dir) / "raw_pages"


def _load_progress(run_dir):
    progress_path = _progress_file(run_dir)
    if not progress_path.exists():
        return {"results": [], "completed_listing_ids": [], "meta": {}}
    return json.loads(progress_path.read_text())


def _write_progress(run_dir, enriched_results, completed_listing_ids, metadata):
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    progress_path = _progress_file(run_dir)
    payload = {
        "meta": metadata,
        "completed_listing_ids": sorted(completed_listing_ids),
        "results": enriched_results,
    }
    with progress_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def _write_raw_page(run_dir, raw_page):
    raw_dir = _raw_pages_dir(run_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    file_path = raw_dir / f"rightmove_rental_detail_{raw_page['listing_id'] or 'unknown'}.json"
    with file_path.open("w", encoding="utf-8") as handle:
        json.dump(raw_page, handle, indent=2, ensure_ascii=False)


def _normalise_space(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def _canonical_listing_url(url):
    if not url:
        return None
    if url.startswith("/"):
        url = f"https://www.rightmove.co.uk{url}"
    return url.split("#", 1)[0]


def _extract_property_id(value):
    if value is None:
        return None
    match = re.search(r"/properties/(\d{6,})", str(value)) or re.search(r"\b(\d{6,})\b", str(value))
    return match.group(1) if match else None


def _normalise_postcode(value):
    if not value:
        return None
    compact = _normalise_space(value).upper().replace(" ", "")
    if len(compact) <= 3:
        return compact
    return f"{compact[:-3]} {compact[-3:]}"


def _first_valid_postcode(*values):
    for value in values:
        if not value:
            continue
        match = POSTCODE_RE.search(str(value))
        if match:
            return _normalise_postcode(match.group(0))
    return None


def _latest_search_results_json():
    candidates = sorted(
        DEFAULT_OUTPUT_DIR.glob("rightmove_rental_search_results_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_search_results(path_arg, limit=None):
    search_json = Path(path_arg) if path_arg else _latest_search_results_json()
    if not search_json or not search_json.exists():
        raise FileNotFoundError(
            "No rental search results JSON found. Pass --search-results-json or run the rental search scraper first."
        )

    data = json.loads(search_json.read_text())
    results = data.get("results", [])
    if limit:
        results = results[:limit]
    return search_json, results


def wait_for_page(driver, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            state = driver.execute_script("return document.readyState")
            title = _normalise_space(driver.title)
        except WebDriverException:
            time.sleep(0.25)
            continue
        if state in ("interactive", "complete") and title:
            return True
        time.sleep(0.25)
    return False


def wait_for_detail_content(driver, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ready = driver.execute_script(
                """
const state = document.readyState;
const h1 = (document.querySelector('h1') || {}).textContent || '';
const bodyText = document.body ? (document.body.innerText || '') : '';
const sourceHint = document.documentElement ? document.documentElement.innerHTML : '';
return {
  state,
  hasTitle: Boolean((document.title || '').trim()),
  hasH1: Boolean(h1.trim()),
  hasDescription: bodyText.includes('Description'),
  hasFacts: document.querySelectorAll('dl dt').length > 0,
  hasAddressHint: sourceHint.includes('displayAddress'),
  hasPhotoHint: sourceHint.includes('property-photo')
};
"""
            )
        except WebDriverException:
            time.sleep(0.25)
            continue

        if (
            ready.get("hasTitle")
            and ready.get("state") in ("interactive", "complete")
            and (
                ready.get("hasH1")
                or ready.get("hasFacts")
                or ready.get("hasDescription")
                or ready.get("hasAddressHint")
                or ready.get("hasPhotoHint")
            )
        ):
            return True
        time.sleep(0.25)
    return False


def _canonical_photo_url(url):
    url = _normalise_space(url)
    if not url or "property-photo" not in url.lower():
        return None
    if "/dir/property-photo/" in url:
        url = url.replace("/dir/property-photo/", "/property-photo/")
    url = re.sub(r"_max_\d+x\d+(?=\.\w+$)", "", url)
    return url


def _canonical_floorplan_url(url):
    url = _normalise_space(url)
    if not url or "property-floorplan" not in url.lower():
        return None
    if "/dir/property-floorplan/" in url:
        url = url.replace("/dir/property-floorplan/", "/property-floorplan/")
    url = re.sub(r"_max_\d+x\d+(?=\.\w+$)", "", url)
    return url


def _dedupe_urls(urls, normaliser):
    clean = []
    seen = set()
    for url in urls:
        canonical = normaliser(url)
        if canonical and canonical not in seen:
            seen.add(canonical)
            clean.append(canonical)
    return clean


def _extract_section(text, heading, stop_markers):
    if not text:
        return None
    start = text.find(heading)
    if start == -1:
        return None
    remainder = text[start + len(heading):].strip()
    end_positions = [remainder.find(marker) for marker in stop_markers if remainder.find(marker) != -1]
    end = min(end_positions) if end_positions else len(remainder)
    section = _normalise_space(remainder[:end])
    return section or None


def _extract_label_value(text, label, stop_labels):
    if not text:
        return None
    stop_pattern = "|".join(re.escape(item) for item in stop_labels)
    pattern = re.compile(
        rf"{re.escape(label)}\s*:\s*(.+?)(?=\s+(?:{stop_pattern})\s*:|$)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None
    return _normalise_space(match.group(1))


def _extract_added_text(text):
    if not text:
        return None
    match = re.search(r"\b(Added today|Added yesterday|Reduced on [A-Za-z0-9 ,]+)\b", text)
    return _normalise_space(match.group(1)) if match else None


def _looks_like_agent_text(value):
    text = _normalise_space(value)
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in AGENT_TEXT_MARKERS)


def _normalise_location_candidate(value):
    text = _normalise_space(value)
    if not text or _looks_like_agent_text(text):
        return None
    return text


def _choose_property_location(search_location, detail_location, display_address):
    search_location = _normalise_location_candidate(search_location)
    detail_location = _normalise_location_candidate(detail_location)
    display_address = _normalise_location_candidate(display_address)

    if search_location:
        if detail_location:
            search_parts = [token.strip().lower() for token in search_location.split(",") if token.strip()]
            detail_parts = [token.strip().lower() for token in detail_location.split(",") if token.strip()]
            if search_parts and detail_parts and len(search_parts) == len(detail_parts):
                comparable_parts = all(
                    search_part == detail_part
                    or search_part in detail_part
                    or detail_part in search_part
                    for search_part, detail_part in zip(search_parts, detail_parts)
                )
            else:
                comparable_parts = False
            if comparable_parts:
                return detail_location
        return search_location

    return detail_location or display_address


def _extract_lat_lng_from_source(source_patterns):
    samples = source_patterns.get("lat_lng", {}).get("samples", [])
    if not samples:
        return None, None
    sample = samples[0]
    match = re.search(r"\('([-\d.]+)', '([-\d.]+)'\)", sample)
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def _source_values(source_patterns, key):
    block = source_patterns.get(key, {})
    return block.get("urls") or block.get("samples", [])


def _scan_page_source(driver):
    source = driver.page_source
    results = {}
    for name, pattern in SOURCE_PATTERNS.items():
        matches = re.findall(pattern, source)
        if not matches:
            continue
        normalised = []
        for match in matches:
            if isinstance(match, tuple):
                normalised.append(str(tuple(_normalise_space(part) for part in match if _normalise_space(part))))
            else:
                normalised.append(_normalise_space(str(match)))
        filtered = [item for item in normalised if item]
        if filtered:
            unique = list(dict.fromkeys(filtered))
            block = {
                "count": len(filtered),
                "samples": unique[:20],
            }
            if name in MEDIA_SOURCE_KEYS:
                block["urls"] = unique
            results[name] = block
    return results


def _scrape_detail_snapshot(driver):
    script = r"""
const bodyText = (document.body.innerText || '').trim();
const mediaHost = 'https://media.rightmove.co.uk/';

const collectMediaUrls = (value) => {
  if (!value) return [];
  const text = String(value);
  const urls = [];
  let start = 0;

  while (start < text.length) {
    const index = text.indexOf(mediaHost, start);
    if (index === -1) break;
    let end = index + mediaHost.length;
    while (end < text.length && !['"', "'", ' ', '\n', '\r', '\t', ')'].includes(text[end])) {
      end += 1;
    }
    urls.push(text.slice(index, end));
    start = end;
  }

  return urls;
};

const collectCandidateUrls = () => {
  const urls = [];
  const pushValue = (value) => {
    for (const match of collectMediaUrls(value)) {
      urls.push(match);
    }
  };

  for (const el of Array.from(document.querySelectorAll('img, source, a, [style]'))) {
    pushValue(el.getAttribute('src'));
    pushValue(el.getAttribute('href'));
    pushValue(el.getAttribute('data-src'));
    pushValue(el.getAttribute('data-lazy-src'));
    pushValue(el.getAttribute('data-test'));
    pushValue(el.getAttribute('style'));
    const srcset = el.getAttribute('srcset') || '';
    for (const part of srcset.split(',')) {
      pushValue(part.trim().split(/\s+/)[0]);
    }
  }

  return Array.from(new Set(urls));
};

const candidateUrls = collectCandidateUrls();
const photoUrls = candidateUrls.filter((url) => url.toLowerCase().includes('property-photo'));
const floorplanUrls = candidateUrls.filter((url) => url.toLowerCase().includes('property-floorplan'));
const epcUrls = candidateUrls.filter((url) => url.toLowerCase().includes('property-epc'));

const factPairs = Array.from(document.querySelectorAll('dl')).flatMap((dl) => {
  const pairs = [];
  const children = Array.from(dl.children);
  for (let index = 0; index < children.length; index += 1) {
    const child = children[index];
    if (child.tagName !== 'DT') continue;
    const label = (child.textContent || '').trim();
    let value = '';
    let next = child.nextElementSibling;
    while (next && next.tagName !== 'DT') {
      value += ' ' + (next.textContent || '').trim();
      next = next.nextElementSibling;
    }
    value = value.trim();
    if (label && value) {
      pairs.push({ label, value });
    }
  }
  return pairs;
});

return {
  title: document.title || null,
  h1: (document.querySelector('h1') || {}).textContent || null,
  body_text: bodyText,
  photo_urls: Array.from(new Set(photoUrls)),
  floorplan_urls: Array.from(new Set(floorplanUrls)),
  epc_urls: Array.from(new Set(epcUrls)),
  fact_pairs: factPairs
};
"""
    snapshot = driver.execute_script(script)
    snapshot["title"] = _normalise_space(snapshot.get("title"))
    snapshot["h1"] = _normalise_space(snapshot.get("h1"))
    snapshot["body_text"] = _normalise_space(snapshot.get("body_text"))
    snapshot["photo_urls"] = _dedupe_urls(snapshot.get("photo_urls", []), _canonical_photo_url)
    snapshot["floorplan_urls"] = _dedupe_urls(snapshot.get("floorplan_urls", []), _canonical_floorplan_url)
    snapshot["epc_urls"] = list(
        dict.fromkeys(_normalise_space(url) for url in snapshot.get("epc_urls", []) if _normalise_space(url))
    )
    snapshot["fact_pairs"] = [
        {
            "label": _normalise_space(item.get("label")),
            "value": _normalise_space(item.get("value")),
        }
        for item in snapshot.get("fact_pairs", [])
        if _normalise_space(item.get("label")) and _normalise_space(item.get("value"))
    ]
    return snapshot


def _fact_map(fact_pairs):
    mapping = {}
    for item in fact_pairs:
        key = item["label"].lower()
        mapping[key] = item["value"]
    return mapping


def _extract_rent_fields(*values):
    for value in values:
        text = _normalise_space(value)
        if not text:
            continue
        match = re.search(r"£\s*([\d,]+)\s*(pcm|pw)\b", text, re.IGNORECASE)
        if match:
            amount = int(match.group(1).replace(",", ""))
            frequency = match.group(2).lower()
            return {
                "rent_text": f"£{match.group(1)} {frequency}",
                "rent_amount": amount,
                "rent_frequency": frequency,
            }
    return {"rent_text": None, "rent_amount": None, "rent_frequency": None}


def _extract_money_amount(value):
    text = _normalise_space(value)
    if not text:
        return None
    match = re.search(r"£\s*([\d,]+(?:\.\d{2})?)", text)
    if not match:
        return None
    amount_text = match.group(1).replace(",", "")
    if "." in amount_text:
        return float(amount_text)
    return int(amount_text)


def _extract_body_rental_details(body_text):
    block = _extract_section(
        body_text,
        "Letting details",
        ["PROPERTY TYPE", "BEDROOMS", "BATHROOMS", "SIZE", "Key features", "Description", "Read full description"],
    )
    source = block or body_text
    labels = [
        "Let available date",
        "Deposit",
        "Min. Tenancy",
        "Minimum tenancy",
        "Let type",
        "Furnish type",
        "Council Tax",
    ]
    extracted = {}
    for label in labels:
        extracted[label.lower()] = _extract_label_value(source, label, labels)

    if not extracted.get("council tax"):
        for label in ["COUNCIL TAX", "PARKING", "GARDEN", "ACCESSIBILITY"]:
            extracted[label.lower()] = _extract_label_value(body_text, label, ["COUNCIL TAX", "PARKING", "GARDEN", "ACCESSIBILITY"])
    return extracted


def _normalise_boolean_phrase(value):
    text = _normalise_space(value)
    return text or None


def _find_phrase(text, phrases):
    lowered = _normalise_space(text).lower()
    if not lowered:
        return None
    for phrase in phrases:
        if phrase in lowered:
            return phrase
    return None


def _extract_epc_rating(*values):
    patterns = [
        re.compile(r"\bEPC(?:\s+Rating)?\s*[:\-]?\s*([A-G])\b", re.IGNORECASE),
        re.compile(r"\bEnergy(?:\s+Efficiency)?(?:\s+Rating)?\s*[:\-]?\s*([A-G])\b", re.IGNORECASE),
        re.compile(r"\bEPC-([A-G])\b", re.IGNORECASE),
    ]
    for value in values:
        text = _normalise_space(value)
        if not text:
            continue
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return match.group(1).upper()
    return None


def _content_text(*values):
    return _normalise_space(" ".join(_normalise_space(value) for value in values if _normalise_space(value)))


def _extract_structured_detail(snapshot, source_patterns):
    facts = _fact_map(snapshot["fact_pairs"])
    body_facts = _extract_body_rental_details(snapshot["body_text"])
    description = _extract_section(snapshot["body_text"], "Description", DESCRIPTION_STOP_MARKERS)
    key_features_text = _extract_section(snapshot["body_text"], "Key features", FEATURES_STOP_MARKERS)
    lat, lng = _extract_lat_lng_from_source(source_patterns)

    source_photos = _source_values(source_patterns, "property_photo_urls")
    source_floorplans = _source_values(source_patterns, "property_floorplan_urls")
    source_epcs = _source_values(source_patterns, "epc_urls")

    photo_urls = _dedupe_urls(snapshot["photo_urls"] + source_photos, _canonical_photo_url)
    floorplan_urls = _dedupe_urls(snapshot["floorplan_urls"] + source_floorplans, _canonical_floorplan_url)
    epc_urls = list(dict.fromkeys(snapshot["epc_urls"] + source_epcs))

    display_address = None
    address_samples = source_patterns.get("display_address", {}).get("samples", [])
    if address_samples:
        display_address = _normalise_space(address_samples[0].replace("\\r\\n", ", ").replace("\\n", ", "))

    clean_location = _normalise_location_candidate(snapshot["h1"])
    clean_display_address = _normalise_location_candidate(display_address)

    deposit_text = facts.get("deposit") or body_facts.get("deposit")
    min_tenancy = (
        facts.get("min. tenancy")
        or facts.get("minimum tenancy")
        or body_facts.get("min. tenancy")
        or body_facts.get("minimum tenancy")
    )
    furnish_type = facts.get("furnish type") or facts.get("furnishing") or body_facts.get("furnish type")
    let_type = facts.get("let type") or body_facts.get("let type")
    council_tax = facts.get("council tax") or body_facts.get("council tax")
    let_available_date = (
        facts.get("let available date")
        or facts.get("available from")
        or body_facts.get("let available date")
    )
    property_type = facts.get("property type")
    bedrooms = _to_int_or_none(facts.get("bedrooms"))
    bathrooms = _to_int_or_none(facts.get("bathrooms"))
    parking = facts.get("parking") or body_facts.get("parking")
    garden = facts.get("garden") or body_facts.get("garden")
    accessibility = facts.get("accessibility") or body_facts.get("accessibility")
    size_text = facts.get("size")
    pets = facts.get("pets allowed") or facts.get("pets")

    body_lower = snapshot["body_text"].lower()
    content_text = _content_text(snapshot["title"], snapshot["h1"], key_features_text, description)
    zero_deposit_phrase = _find_phrase(snapshot["body_text"], ZERO_DEPOSIT_PHRASES)
    student_phrase = _find_phrase(snapshot["body_text"], STUDENT_PHRASES)
    pets_phrase = _find_phrase(snapshot["body_text"], PETS_PHRASES)
    bills_phrase = _find_phrase(snapshot["body_text"], BILLS_PHRASES)
    investment_phrase = _find_phrase(content_text, INVESTMENT_PHRASES)
    luxury_phrase = _find_phrase(content_text, LUXURY_PHRASES)
    epc_rating = _extract_epc_rating(
        content_text,
        snapshot["body_text"],
        key_features_text,
        description,
        snapshot["title"],
        snapshot["h1"],
    )

    return {
        "detail_title": snapshot["h1"] or snapshot["title"],
        "added_text": _extract_added_text(snapshot["body_text"]),
        "description": description,
        "key_features_text": key_features_text,
        "location": clean_location or clean_display_address,
        "display_address": clean_display_address,
        "postcode": _first_valid_postcode(clean_location, clean_display_address),
        "property_type": property_type,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "size_text": size_text,
        "council_tax": council_tax,
        "parking": parking,
        "garden": garden,
        "accessibility": accessibility,
        "deposit_text": deposit_text,
        "deposit_amount": _extract_money_amount(deposit_text),
        "min_tenancy": min_tenancy,
        "furnish_type": furnish_type,
        "let_type": let_type,
        "let_available_date": let_available_date,
        "pets_text": _normalise_boolean_phrase(pets) or _normalise_boolean_phrase(pets_phrase),
        "bills_text": _normalise_boolean_phrase(bills_phrase),
        "zero_deposit": True if zero_deposit_phrase else None,
        "student_friendly": True if student_phrase else None,
        "student_text": _normalise_boolean_phrase(student_phrase),
        "investment_opportunity": True if investment_phrase else None,
        "investment_text": _normalise_boolean_phrase(investment_phrase),
        "luxury": True if luxury_phrase else None,
        "luxury_text": _normalise_boolean_phrase(luxury_phrase),
        "epc_rating": epc_rating,
        "latitude": lat,
        "longitude": lng,
        "property_photo_urls": photo_urls,
        "image_url": photo_urls[0] if photo_urls else None,
        "property_photo_count": len(photo_urls),
        "floorplan_urls": floorplan_urls,
        "epc_urls": epc_urls,
        "fact_pairs": snapshot["fact_pairs"],
        **_extract_rent_fields(snapshot["body_text"], snapshot["title"], snapshot["h1"]),
    }


def _to_int_or_none(value):
    if value in (None, ""):
        return None
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    return int(match.group(0))


def _merge_search_and_detail(search_row, detail_row):
    merged = dict(search_row)
    merged["search_summary"] = search_row.get("summary")
    merged["search_image_urls"] = search_row.get("image_urls", [])
    merged["search_location"] = search_row.get("location")
    merged["search_postcode"] = search_row.get("postcode")
    merged["search_latitude"] = search_row.get("latitude")
    merged["search_longitude"] = search_row.get("longitude")
    merged["detail_latitude"] = detail_row.get("latitude")
    merged["detail_longitude"] = detail_row.get("longitude")

    for key, value in detail_row.items():
        if value in (None, "", []):
            continue
        merged[key] = value

    if merged.get("property_photo_urls"):
        merged["image_urls"] = merged["property_photo_urls"]
        merged["image_url"] = merged["property_photo_urls"][0]
    if merged.get("detail_title"):
        merged["summary"] = merged.get("description") or merged.get("search_summary")
    chosen_location = _choose_property_location(
        search_row.get("location"),
        detail_row.get("location"),
        detail_row.get("display_address"),
    )
    if chosen_location:
        merged["location"] = chosen_location
    if search_row.get("postcode"):
        merged["postcode"] = search_row.get("postcode")
    if not merged.get("postcode"):
        merged["postcode"] = _first_valid_postcode(
            merged.get("location"),
            merged.get("display_address"),
        )
    if not merged.get("rent_amount") and merged.get("rent_text"):
        match = re.search(r"£\s*([\d,]+)\s*(pcm|pw)\b", merged["rent_text"], re.IGNORECASE)
        if match:
            merged["rent_amount"] = int(match.group(1).replace(",", ""))
            merged["rent_frequency"] = match.group(2).lower()

    # Trust search/API coordinates first; detail-page coordinates can be noisy.
    if search_row.get("latitude") not in (None, "") and search_row.get("longitude") not in (None, ""):
        merged["latitude"] = search_row.get("latitude")
        merged["longitude"] = search_row.get("longitude")
        merged["coordinate_source"] = "search_api"
    elif detail_row.get("latitude") not in (None, "") and detail_row.get("longitude") not in (None, ""):
        merged["latitude"] = detail_row.get("latitude")
        merged["longitude"] = detail_row.get("longitude")
        merged["coordinate_source"] = "detail_page"
    else:
        merged["latitude"] = search_row.get("latitude")
        merged["longitude"] = search_row.get("longitude")
        merged["coordinate_source"] = "missing"
    return merged


def _row_for_csv(item):
    return {
        "listing_id": item.get("listing_id"),
        "rent_amount": item.get("rent_amount"),
        "rent_frequency": item.get("rent_frequency"),
        "rent_text": item.get("rent_text"),
        "location": item.get("location"),
        "postcode": item.get("postcode"),
        "listing_url": item.get("listing_url"),
        "image_url": item.get("image_url"),
        "property_photo_count": item.get("property_photo_count"),
        "image_count": item.get("image_count"),
        "floorplan_count": item.get("floorplan_count"),
        "virtual_tour_count": item.get("virtual_tour_count"),
        "property_type": item.get("property_type"),
        "bedrooms": item.get("bedrooms"),
        "bathrooms": item.get("bathrooms"),
        "let_available_date": item.get("let_available_date"),
        "listing_status": item.get("listing_status"),
        "deposit_text": item.get("deposit_text"),
        "deposit_amount": item.get("deposit_amount"),
        "min_tenancy": item.get("min_tenancy"),
        "let_type": item.get("let_type"),
        "furnish_type": item.get("furnish_type"),
        "council_tax": item.get("council_tax"),
        "parking": item.get("parking"),
        "garden": item.get("garden"),
        "accessibility": item.get("accessibility"),
        "size_text": item.get("size_text"),
        "students": item.get("students"),
        "student_friendly": item.get("student_friendly"),
        "student_text": item.get("student_text"),
        "investment_opportunity": item.get("investment_opportunity"),
        "investment_text": item.get("investment_text"),
        "luxury": item.get("luxury"),
        "luxury_text": item.get("luxury_text"),
        "online_viewings_available": item.get("online_viewings_available"),
        "build_to_rent": item.get("build_to_rent"),
        "pets_text": item.get("pets_text"),
        "bills_text": item.get("bills_text"),
        "zero_deposit": item.get("zero_deposit"),
        "epc_rating": item.get("epc_rating"),
        "latitude": item.get("latitude"),
        "longitude": item.get("longitude"),
        "search_latitude": item.get("search_latitude"),
        "search_longitude": item.get("search_longitude"),
        "detail_latitude": item.get("detail_latitude"),
        "detail_longitude": item.get("detail_longitude"),
        "coordinate_source": item.get("coordinate_source"),
        "added_text": item.get("added_text"),
        "description": item.get("description"),
        "key_features_text": item.get("key_features_text"),
        "image_urls": json.dumps(item.get("image_urls", []), ensure_ascii=False),
        "floorplan_urls": json.dumps(item.get("floorplan_urls", []), ensure_ascii=False),
        "epc_urls": json.dumps(item.get("epc_urls", []), ensure_ascii=False),
    }


def save_outputs(output_dir, enriched_results, raw_path, metadata):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    dataset = {
        "meta": metadata,
        "results": enriched_results,
    }

    json_path = output_path / f"rightmove_rental_enriched_results_{timestamp}.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(dataset, handle, indent=2, ensure_ascii=False)

    csv_path = output_path / f"rightmove_rental_enriched_results_{timestamp}.csv"
    fieldnames = list(_row_for_csv(enriched_results[0]).keys()) if enriched_results else []
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in enriched_results:
            writer.writerow(_row_for_csv(item))

    return json_path, csv_path, raw_path


def main():
    args = parse_args()
    search_json, search_results = load_search_results(args.search_results_json, args.limit)
    run_dir = _resolve_run_dir(args.output_dir, args.run_dir, args.resume)
    if not args.resume and _progress_file(run_dir).exists():
        raise RuntimeError(
            f"Checkpoint directory already exists: {run_dir}. "
            "Use --resume to continue it or choose a different --run-dir."
        )
    progress = _load_progress(run_dir) if args.resume else {"results": [], "completed_listing_ids": [], "meta": {}}
    completed_listing_ids = {str(item) for item in progress.get("completed_listing_ids", []) if item}
    enriched_results = list(progress.get("results", []))
    driver = setup_browser(
        block_images=args.block_images,
        headless=args.headless,
        user_data_dir=args.user_data_dir,
    )

    try:
        if not search_results:
            raise RuntimeError("No rental search results to enrich.")

        resume_source = progress.get("meta", {}).get("source_search_json")
        if args.resume and resume_source and str(search_json) != str(resume_source):
            raise RuntimeError(
                f"Resume run was created from a different search dataset: {resume_source}"
            )

        print(f"Rental detail run directory: {run_dir}")
        if completed_listing_ids:
            print(f"Resuming with {len(completed_listing_ids)} listings already completed.")
        print(
            f"Rental detail speed settings: block_images={args.block_images} "
            f"page_timeout={args.page_timeout}s settle={args.wait_seconds}s"
        )

        driver = _navigate_with_recovery(
            driver,
            search_results[0]["listing_url"],
            block_images=args.block_images,
            reprompt=False,
            headless=args.headless,
            user_data_dir=args.user_data_dir,
        )
        if args.interactive:
            prompt_manual_ready()

        for index, row in enumerate(search_results, start=1):
            listing_url = _canonical_listing_url(row.get("listing_url"))
            if not listing_url:
                continue
            listing_id = row.get("listing_id") or _extract_property_id(listing_url)
            listing_key = str(listing_id) if listing_id else None
            if listing_key and listing_key in completed_listing_ids:
                print(f"\n[{index}/{len(search_results)}] Skipping completed listing {listing_key}")
                continue

            print(f"\n[{index}/{len(search_results)}] {listing_url}")
            driver = _navigate_with_recovery(
                driver,
                listing_url,
                block_images=args.block_images,
                reprompt=args.interactive,
                headless=args.headless,
                user_data_dir=args.user_data_dir,
            )
            if not wait_for_page(driver, timeout=args.page_timeout):
                raise RuntimeError(f"Page did not finish loading: {listing_url}")
            if not wait_for_detail_content(driver, timeout=args.page_timeout):
                raise RuntimeError(f"Property content did not appear: {listing_url}")
            time.sleep(args.wait_seconds)

            snapshot = _scrape_detail_snapshot(driver)
            source_patterns = _scan_page_source(driver)
            detail_row = _extract_structured_detail(snapshot, source_patterns)
            detail_row["listing_id"] = listing_id
            detail_row["listing_url"] = listing_url
            merged = _merge_search_and_detail(row, detail_row)
            enriched_results.append(merged)
            raw_page = {
                "listing_id": merged.get("listing_id"),
                "listing_url": listing_url,
                "snapshot": snapshot,
                "source_patterns": source_patterns,
                "detail_row": detail_row,
            }
            _write_raw_page(run_dir, raw_page)
            if listing_key:
                completed_listing_ids.add(listing_key)

            progress_metadata = {
                "generated_at": datetime.now().isoformat(),
                "source_search_json": str(search_json),
                "run_dir": str(run_dir),
                "results_count": len(enriched_results),
                "completed_count": len(completed_listing_ids),
                "resume_enabled": bool(args.resume),
                "block_images": bool(args.block_images),
                "interactive": bool(args.interactive),
                "headless": bool(args.headless),
                "user_data_dir": args.user_data_dir,
                "page_timeout": args.page_timeout,
                "settle_seconds": args.wait_seconds,
            }
            _write_progress(run_dir, enriched_results, completed_listing_ids, progress_metadata)

            print(
                f"  photos={merged.get('property_photo_count', 0)} "
                f"floorplans={len(merged.get('floorplan_urls', []))} "
                f"epcs={len(merged.get('epc_urls', []))}"
            )

        metadata = {
            "generated_at": datetime.now().isoformat(),
            "source_search_json": str(search_json),
            "run_dir": str(run_dir),
            "results_count": len(enriched_results),
            "completed_count": len(completed_listing_ids),
            "block_images": bool(args.block_images),
            "interactive": bool(args.interactive),
            "headless": bool(args.headless),
            "user_data_dir": args.user_data_dir,
            "page_timeout": args.page_timeout,
            "settle_seconds": args.wait_seconds,
        }
        _write_progress(run_dir, enriched_results, completed_listing_ids, metadata)
        json_path, csv_path, raw_path = save_outputs(
            args.output_dir,
            enriched_results,
            _raw_pages_dir(run_dir),
            metadata,
        )

        print("\nSaved enriched rental outputs:")
        print(f"  JSON dataset: {json_path}")
        print(f"  CSV dataset:  {csv_path}")
        print(f"  Raw detail pages: {raw_path}")
        print(f"  Checkpoint dir: {run_dir}")

    finally:
        _safe_quit(driver)


if __name__ == "__main__":
    main()
