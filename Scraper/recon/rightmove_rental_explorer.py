"""
Rightmove Rental Explorer
=========================

Recon tool for Rightmove rental search results.

It helps answer:
  - Does the rental search page use the same listing search API?
  - Which DOM selectors still work on rental cards?
  - Which rental-specific fields appear in API payloads or page source?
  - How is pagination encoded for rental searches?
  - Which fields should be scraped from search vs. detail pages?

Examples:
  python3 Scraper/recon/rightmove_rental_explorer.py \
    --search-url "https://www.rightmove.co.uk/property-to-rent/find.html?searchLocation=Manchester&useLocationIdentifier=true&locationIdentifier=REGION%5E94091&radius=0.0&index=0&propertyTypes=&includeLetAgreed=false&mustHave=&dontShow=houseShare,student&furnishTypes=&keywords="

  python3 Scraper/recon/rightmove_rental_explorer.py --pages 3
"""

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By


SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
API_PATH = "/api/property-search/listing/search"

CARD_SELECTORS = [
    "div[data-testid^='propertyCard-']",
    "div[data-testid^='propertyCard-vrt-']",
    "div[class*='PropertyCard_propertyCardContainer__']",
    "div[class*='propertyCard']",
]

PRICE_SELECTORS = [
    "[data-testid*='price']",
    "div[class*='price']",
    "span[class*='price']",
]

ADDRESS_SELECTORS = [
    "address",
    "h2[class*='title']",
    "[data-testid*='address']",
    "[data-testid*='title']",
]

LINK_SELECTORS = [
    "a[href*='/properties/']",
    "a[href*='/property-to-rent/']",
]

STATUS_SELECTORS = [
    "[data-testid*='flag']",
    "[data-testid*='label']",
    "span[class*='flag']",
    "span[class*='label']",
]

SOURCE_PATTERNS = {
    "rent_pcm": r"£\s*[\d,]+(?:\.\d{2})?\s*pcm",
    "rent_pw": r"£\s*[\d,]+(?:\.\d{2})?\s*pw",
    "deposit": r"£\s*[\d,]+(?:\.\d{2})?\s*(?:deposit|held by deposit)",
    "furnished": r"(?i)\b(part[- ]furnished|furnished|unfurnished)\b",
    "available": r"(?i)\bavailable(?:\s+from)?\s+[A-Za-z0-9, ]{3,40}",
    "let_agreed": r"(?i)\b(let agreed|reserved|student friendly)\b",
    "display_address": r'"displayAddress"\s*:\s*"([^"]+)"',
    "listing_id": r'"id"\s*:\s*(\d{6,})',
    "lat_lng": r'"latitude"\s*:\s*([-\d.]+).*?"longitude"\s*:\s*([-\d.]+)',
    "property_type": r'"propertyType"\s*:\s*"([^"]+)"',
}

PROPERTY_PHOTO_RE = re.compile(r"https://media\.rightmove\.co\.uk/[^\s\"'>]*property-photo[^\s\"'>]+", re.IGNORECASE)
UK_POSTCODE_RE = re.compile(
    r"(?i)\b(?:GIR\s?0AA|(?:[A-PR-UWYZ][A-HK-Y]?\d[A-HJKPSTUW]?|"
    r"[A-PR-UWYZ][A-HK-Y]?\d{2}|[A-PR-UWYZ][A-HK-Y]?\d[ABEHMNPRVWXY])"
    r"\s?\d[ABD-HJLNP-UW-Z]{2})\b"
)
RENT_VALUE_RE = re.compile(r"£\s*([\d,]+(?:\.\d{2})?)", re.IGNORECASE)

RENT_KEYWORDS = (
    "rent",
    "price",
    "deposit",
    "furnish",
    "avail",
    "let",
    "tenant",
    "tenancy",
    "student",
    "bills",
    "council",
    "term",
)


def parse_args():
    parser = argparse.ArgumentParser(description="Recon Rightmove rental search results.")
    parser.add_argument(
        "--search-url",
        help="Rightmove rental search URL. If omitted, the browser opens the Rightmove rent homepage for manual navigation.",
    )
    parser.add_argument("--pages", type=int, default=2, help="How many result pages to inspect.")
    parser.add_argument("--page-size", type=int, default=24, help="Pagination step size. Recon currently suggests 24.")
    parser.add_argument("--wait-seconds", type=float, default=2.0, help="Delay after each page load.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Directory for saved reports.")
    return parser.parse_args()


def setup_browser():
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    driver = uc.Chrome(options=options, version_main=None)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        pass
    return driver


def prompt_manual_ready():
    input(
        "\nOpen a Rightmove rental search, handle cookies / CAPTCHA if needed, "
        "then press Enter here to start the rental recon..."
    )


def _normalise_space(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def _canonical_url(url):
    if not url:
        return None
    if url.startswith("/"):
        url = f"https://www.rightmove.co.uk{url}"
    return url.split("#", 1)[0]


def _build_page_url(base_url, page_index, keep_zero_index):
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if page_index == 0 and not keep_zero_index:
        query.pop("index", None)
    else:
        query["index"] = [str(page_index)]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _build_api_url(page_url):
    parsed = urlparse(page_url)
    return urlunparse(parsed._replace(path=API_PATH))


def _extract_property_id(value):
    if not value:
        return None
    match = re.search(r"/properties/(\d{6,})", str(value)) or re.search(r"\b(\d{6,})\b", str(value))
    return match.group(1) if match else None


def _normalise_postcode(value):
    text = _normalise_space(value).upper().replace(" ", "")
    if len(text) <= 3:
        return text
    return f"{text[:-3]} {text[-3:]}"


def _first_valid_postcode(*values):
    for value in values:
        if not value:
            continue
        match = UK_POSTCODE_RE.search(str(value))
        if match:
            return _normalise_postcode(match.group(0))
    return None


def _extract_rent_amount(text):
    if not text:
        return None
    match = RENT_VALUE_RE.search(str(text))
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _infer_rent_frequency(text):
    text = (text or "").lower()
    if "pcm" in text or "per calendar month" in text:
        return "pcm"
    if "pw" in text or "per week" in text:
        return "pw"
    return None


def _to_int_or_none(value):
    if value in (None, ""):
        return None
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    return int(match.group(0))


def _first_scalar_for_keys(node, keys):
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if isinstance(value, (str, int, float, bool)) and value not in ("", None):
                return value
        for value in node.values():
            found = _first_scalar_for_keys(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(node, list):
        for item in node:
            found = _first_scalar_for_keys(item, keys)
            if found not in (None, ""):
                return found
    return None


def _collect_matching_strings(node, predicate, results=None):
    if results is None:
        results = []
    if isinstance(node, dict):
        for value in node.values():
            _collect_matching_strings(value, predicate, results)
    elif isinstance(node, list):
        for item in node:
            _collect_matching_strings(item, predicate, results)
    elif isinstance(node, str) and predicate(node):
        results.append(node)
    return results


def _listing_score(node):
    if not isinstance(node, dict):
        return 0
    score = 0
    if any(key in node for key in ("id", "propertyId")):
        score += 2
    if any(key in node for key in ("displayAddress", "address")):
        score += 2
    if any(key in node for key in ("price", "displayPrice", "formattedPrice")):
        score += 2
    if any(key in node for key in ("propertyUrl", "url", "detailUrl")):
        score += 1
    if any(key in node for key in ("bedrooms", "bathrooms", "propertyType")):
        score += 1
    return score


def _find_listing_dicts(payload):
    candidates = []

    def walk(node):
        if isinstance(node, dict):
            if _listing_score(node) >= 5:
                candidates.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)

    seen = set()
    deduped = []
    for node in candidates:
        listing_id = _extract_property_id(node.get("id") or node.get("propertyId"))
        address = _first_scalar_for_keys(node, ("displayAddress", "address"))
        signature = (listing_id, _normalise_space(str(address)))
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(node)
    return deduped


def _extract_image_urls(node):
    urls = _collect_matching_strings(node, lambda value: bool(PROPERTY_PHOTO_RE.search(value)))
    clean = []
    seen = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            clean.append(url)
    return clean


def _scan_rental_paths(node, prefix="", findings=None):
    if findings is None:
        findings = []
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if any(keyword in key.lower() for keyword in RENT_KEYWORDS):
                if isinstance(value, (str, int, float, bool)) and value not in ("", None):
                    findings.append({"path": path, "value": _normalise_space(str(value))})
            _scan_rental_paths(value, path, findings)
    elif isinstance(node, list):
        for index, item in enumerate(node[:5]):
            _scan_rental_paths(item, f"{prefix}[{index}]", findings)
    return findings


def _summarise_rental_paths(listing_nodes):
    counter = Counter()
    samples = defaultdict(list)
    for node in listing_nodes:
        for finding in _scan_rental_paths(node):
            counter[finding["path"]] += 1
            if finding["value"] and len(samples[finding["path"]]) < 4:
                samples[finding["path"]].append(finding["value"])
    summary = []
    for path, count in counter.most_common():
        summary.append({
            "path": path,
            "seen_in_listings": count,
            "samples": list(dict.fromkeys(samples[path]))[:4],
        })
    return summary


def _extract_listing_from_api(node, page_index):
    listing_id = _extract_property_id(node.get("id") or node.get("propertyId"))
    listing_url = _canonical_url(_first_scalar_for_keys(node, ("propertyUrl", "url", "detailUrl", "propertyLink")))
    if not listing_id:
        listing_id = _extract_property_id(listing_url)

    rent_text = _normalise_space(str(_first_scalar_for_keys(node, ("displayPrice", "formattedPrice", "priceDisplay")) or ""))
    address = _first_scalar_for_keys(node, ("displayAddress", "address", "streetAddress"))
    property_type = _first_scalar_for_keys(node, ("propertyType", "displayPropertyType", "type"))
    summary = _first_scalar_for_keys(node, ("summary", "description", "propertyDescription"))
    furnished = _first_scalar_for_keys(node, ("furnishType", "furnishing", "furnishedType"))
    available = _first_scalar_for_keys(node, ("availableFrom", "availability", "availableDate"))
    deposit = _first_scalar_for_keys(node, ("deposit", "depositAmount"))
    let_status = _first_scalar_for_keys(node, ("displayStatus", "status", "listingStatus"))
    bedrooms = _first_scalar_for_keys(node, ("bedrooms", "numberOfBedrooms"))
    bathrooms = _first_scalar_for_keys(node, ("bathrooms", "numberOfBathrooms"))
    image_urls = _extract_image_urls(node)

    return {
        "listing_id": listing_id,
        "listing_url": listing_url,
        "rent_text": rent_text or None,
        "rent_amount": _extract_rent_amount(rent_text),
        "rent_frequency": _infer_rent_frequency(rent_text),
        "location": _normalise_space(str(address)) if address else None,
        "postcode": _first_valid_postcode(address, summary),
        "property_type": _normalise_space(str(property_type)) if property_type else None,
        "bedrooms": _to_int_or_none(bedrooms),
        "bathrooms": _to_int_or_none(bathrooms),
        "summary": _normalise_space(str(summary)) if summary else None,
        "furnished": _normalise_space(str(furnished)) if furnished else None,
        "available_from": _normalise_space(str(available)) if available else None,
        "deposit_text": _normalise_space(str(deposit)) if deposit not in (None, "") else None,
        "listing_status": _normalise_space(str(let_status)) if let_status else None,
        "image_count": len(image_urls),
        "source_page_index": page_index,
    }


def wait_for_cards(driver, selectors, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for selector in selectors:
                count = driver.execute_script("return document.querySelectorAll(arguments[0]).length;", selector)
                if count and count > 0:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def fetch_api_payload(driver, api_url):
    script = """
const url = arguments[0];
const done = arguments[arguments.length - 1];
fetch(url, {
  credentials: 'include',
  headers: { 'accept': 'application/json, text/plain, */*' }
})
  .then(async (response) => {
    done({ ok: response.ok, status: response.status, text: await response.text() });
  })
  .catch((error) => {
    done({ ok: false, status: 0, error: String(error) });
  });
"""
    result = driver.execute_async_script(script, api_url)
    if not result.get("ok"):
        return {
            "ok": False,
            "status": result.get("status"),
            "error": result.get("error"),
            "url": api_url,
            "payload": None,
        }
    try:
        payload = json.loads(result["text"])
    except json.JSONDecodeError:
        return {
            "ok": False,
            "status": result.get("status"),
            "error": "non_json_response",
            "url": api_url,
            "payload": None,
        }
    return {
        "ok": True,
        "status": result.get("status"),
        "error": None,
        "url": api_url,
        "payload": payload,
    }


def visible_elements(driver, selector, context=None):
    try:
        root = context if context else driver
        return [el for el in root.find_elements(By.CSS_SELECTOR, selector) if el.is_displayed()]
    except Exception:
        return []


def _looks_like_rent(text):
    lowered = (text or "").lower()
    return bool(RENT_VALUE_RE.search(lowered)) and ("pcm" in lowered or "pw" in lowered or "per week" in lowered or "per month" in lowered)


def _looks_like_address(text):
    if not text or len(text) < 6:
        return False
    return "," in text or bool(UK_POSTCODE_RE.search(text))


def _is_listing_href(href):
    href = href or ""
    if "contactBranch" in href:
        return False
    return "/properties/" in href or "/property-to-rent/" in href


def _rank_card_selector(driver, selector):
    cards = visible_elements(driver, selector)
    if len(cards) < 3:
        return None

    unique_links = set()
    unique_ids = set()
    rent_hits = 0
    address_hits = 0
    samples = []

    for card in cards[:8]:
        card_text = _normalise_space(card.text)
        if card_text and len(samples) < 2:
            samples.append(card_text[:140])

        for link_selector in LINK_SELECTORS:
            for link in visible_elements(driver, link_selector, card):
                href = _canonical_url(link.get_attribute("href"))
                if href and _is_listing_href(href):
                    unique_links.add(href)
                    property_id = _extract_property_id(href)
                    if property_id:
                        unique_ids.add(property_id)

        for price_selector in PRICE_SELECTORS:
            for el in visible_elements(driver, price_selector, card)[:2]:
                if _looks_like_rent(el.text):
                    rent_hits += 1
                    break

        for address_selector in ADDRESS_SELECTORS:
            for el in visible_elements(driver, address_selector, card)[:2]:
                if _looks_like_address(el.text):
                    address_hits += 1
                    break

    score = len(unique_ids) * 10 + len(unique_links) * 8 + rent_hits * 3 + address_hits * 2
    if "data-testid^='propertyCard-'" in selector or "propertyCardContainer__" in selector:
        score += 4

    return {
        "selector": selector,
        "count": len(cards),
        "score": score,
        "sample_unique_listing_links": len(unique_links),
        "sample_unique_property_ids": len(unique_ids),
        "sample_rent_hits": rent_hits,
        "sample_address_hits": address_hits,
        "samples": samples,
    }


def find_best_card_selector(driver):
    ranked = [item for item in (_rank_card_selector(driver, selector) for selector in CARD_SELECTORS) if item]
    ranked.sort(key=lambda item: (-item["score"], -item["sample_unique_property_ids"], -item["sample_unique_listing_links"], item["selector"]))
    return ranked


def scrape_dom_cards(driver, card_selector):
    script = """
const selector = arguments[0];
const cards = Array.from(document.querySelectorAll(selector));
return cards.map((card, index) => {
  const linkEl = card.querySelector("a[href*='/properties/'], a[href*='/property-to-rent/']");
  const addressEl = card.querySelector("address, [data-testid*='address'], [data-testid*='title']");
  const priceEl = card.querySelector("[data-testid*='price'], div[class*='price'], span[class*='price']");
  const summaryEl = card.querySelector("[data-testid='property-description'], [data-testid*='description'], p[class*='summary']");
  const infoSpans = Array.from(card.querySelectorAll("[data-testid='property-information'] span"))
    .map((el) => (el.textContent || '').trim())
    .filter(Boolean);
  const labels = Array.from(card.querySelectorAll("[data-testid*='flag'], [data-testid*='label'], span[class*='flag'], span[class*='label']"))
    .map((el) => (el.textContent || '').trim())
    .filter(Boolean);

  return {
    listing_url: linkEl ? linkEl.href.split('#')[0] : null,
    address: addressEl ? addressEl.textContent : null,
    price_text: priceEl ? priceEl.textContent : null,
    summary: summaryEl ? summaryEl.textContent : null,
    property_type: infoSpans[0] || null,
    bedrooms: infoSpans[1] || null,
    bathrooms: infoSpans[2] || null,
    labels,
    position_on_page: index + 1,
  };
});
"""
    rows = driver.execute_script(script, card_selector)
    cleaned = []
    for row in rows:
        rent_text = _normalise_space(row.get("price_text"))
        cleaned.append({
            "listing_id": _extract_property_id(row.get("listing_url")),
            "listing_url": _canonical_url(row.get("listing_url")),
            "rent_text": rent_text or None,
            "rent_amount": _extract_rent_amount(rent_text),
            "rent_frequency": _infer_rent_frequency(rent_text),
            "location": _normalise_space(row.get("address")),
            "postcode": _first_valid_postcode(row.get("address")),
            "property_type": _normalise_space(row.get("property_type")),
            "bedrooms": _to_int_or_none(row.get("bedrooms")),
            "bathrooms": _to_int_or_none(row.get("bathrooms")),
            "summary": _normalise_space(row.get("summary")),
            "labels": list(dict.fromkeys(_normalise_space(item) for item in row.get("labels", []) if _normalise_space(item))),
            "position_on_page": row.get("position_on_page"),
        })
    return cleaned


def scan_page_source(driver):
    source = driver.page_source
    results = {}
    for name, pattern in SOURCE_PATTERNS.items():
        matches = re.findall(pattern, source)
        if not matches:
            continue
        normalised = []
        for match in matches:
            if isinstance(match, tuple):
                cleaned = tuple(_normalise_space(part) for part in match if _normalise_space(part))
                if cleaned:
                    normalised.append(str(cleaned))
            else:
                text = _normalise_space(str(match))
                if text:
                    normalised.append(text)
        if normalised:
            results[name] = {
                "count": len(normalised),
                "samples": list(dict.fromkeys(normalised))[:8],
            }
    return results


def capture_network_logs(driver, seen_urls):
    endpoints = []
    try:
        logs = driver.get_log("performance")
    except Exception:
        return endpoints

    for entry in logs:
        try:
            msg = json.loads(entry["message"])["message"]
            if msg.get("method") != "Network.responseReceived":
                continue
            response = msg["params"]["response"]
            url = response.get("url", "")
            if url in seen_urls:
                continue
            classification = _classify_endpoint(url)
            if not classification:
                continue
            seen_urls.add(url)
            endpoints.append({
                "url": url,
                "status": response.get("status"),
                "mime": response.get("mimeType"),
                "classification": classification,
            })
        except Exception:
            continue
    return endpoints


def _classify_endpoint(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path
    if host in ("www.rightmove.co.uk", "rightmove.co.uk"):
        if path == API_PATH:
            return "listing_search_api"
        if path.startswith("/api/property-search/"):
            return "property_search_api"
        if path.startswith("/api/"):
            return "first_party_api"
    return None


def _candidate_api_urls(page_url, current_url, network_endpoints):
    candidates = []
    for url in (
        _build_api_url(current_url),
        _build_api_url(page_url),
    ):
        if url and url not in candidates:
            candidates.append(url)

    for endpoint in network_endpoints:
        if endpoint.get("classification") == "listing_search_api" and endpoint.get("url") not in candidates:
            candidates.append(endpoint["url"])
    return candidates


def _extract_index_from_url(url):
    try:
        query = parse_qs(urlparse(url).query)
    except Exception:
        return None
    values = query.get("index")
    if not values:
        return None
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return None


def _analyse_pagination(url_history, api_endpoints):
    page_indices = sorted({idx for idx in (_extract_index_from_url(url) for url in url_history) if idx is not None})
    api_indices = sorted({
        idx
        for idx in (
            _extract_index_from_url(endpoint["url"])
            for endpoint in api_endpoints
            if endpoint.get("classification") == "listing_search_api"
        )
        if idx is not None
    })
    all_indices = sorted(set(page_indices + api_indices))
    step_sizes = sorted({right - left for left, right in zip(all_indices, all_indices[1:]) if right > left})
    likely_page_size = step_sizes[0] if len(step_sizes) == 1 else None
    return {
        "page_indices_seen": page_indices,
        "api_indices_seen": api_indices,
        "step_sizes_seen": step_sizes,
        "likely_page_size": likely_page_size,
    }


def scan_rental_page(driver, page_url, api_url, page_index, wait_seconds, seen_api_urls):
    driver.get(page_url)
    if not wait_for_cards(driver, CARD_SELECTORS):
        raise RuntimeError(f"Rental cards did not appear: {page_url}")
    time.sleep(wait_seconds)

    ranked_card_selectors = find_best_card_selector(driver)
    best_card_selector = ranked_card_selectors[0] if ranked_card_selectors else None
    dom_cards = scrape_dom_cards(driver, best_card_selector["selector"]) if best_card_selector else []
    network_endpoints = capture_network_logs(driver, seen_api_urls)
    resolved_page_url = driver.current_url
    api_attempts = []
    payload = None
    for candidate_url in _candidate_api_urls(page_url, resolved_page_url, network_endpoints):
        attempt = fetch_api_payload(driver, candidate_url)
        api_attempts.append({
            "url": candidate_url,
            "ok": attempt["ok"],
            "status": attempt["status"],
            "error": attempt["error"],
        })
        if attempt["ok"]:
            payload = attempt["payload"]
            api_url = candidate_url
            break

    listing_nodes = _find_listing_dicts(payload) if payload else []
    api_listings = [_extract_listing_from_api(node, page_index) for node in listing_nodes]
    source_patterns = scan_page_source(driver)

    return {
        "page_index": page_index,
        "page_url": resolved_page_url,
        "api_url": api_url,
        "api_fetch_success": payload is not None,
        "api_fetch_attempts": api_attempts,
        "best_card_selector": best_card_selector,
        "card_selector_ranking": ranked_card_selectors[:5],
        "dom_cards": dom_cards[:12],
        "api_listing_count": len(api_listings),
        "api_listing_samples": api_listings[:8],
        "rental_field_candidates": _summarise_rental_paths(listing_nodes)[:20],
        "source_patterns": source_patterns,
        "api_endpoints": network_endpoints,
    }


def build_report(scans, start_url, pages_requested):
    api_endpoints = []
    url_history = [scan["page_url"] for scan in scans]
    for scan in scans:
        api_endpoints.extend(scan["api_endpoints"])
    deduped_api_endpoints = []
    seen = set()
    for endpoint in api_endpoints:
        if endpoint["url"] in seen:
            continue
        seen.add(endpoint["url"])
        deduped_api_endpoints.append(endpoint)

    source_summary = {}
    for scan in scans:
        for name, data in scan["source_patterns"].items():
            entry = source_summary.setdefault(name, {"count": 0, "samples": []})
            entry["count"] += data["count"]
            entry["samples"].extend(data["samples"])
    for name, data in source_summary.items():
        source_summary[name] = {
            "total_matches": data["count"],
            "unique_samples": list(dict.fromkeys(data["samples"]))[:10],
        }

    selector_summary = {}
    for scan in scans:
        best = scan.get("best_card_selector")
        if not best:
            continue
        current = selector_summary.setdefault(best["selector"], {
            "seen_in_pages": 0,
            "max_score": 0,
            "max_unique_links": 0,
            "max_unique_property_ids": 0,
            "samples": [],
        })
        current["seen_in_pages"] += 1
        current["max_score"] = max(current["max_score"], best["score"])
        current["max_unique_links"] = max(current["max_unique_links"], best["sample_unique_listing_links"])
        current["max_unique_property_ids"] = max(current["max_unique_property_ids"], best["sample_unique_property_ids"])
        current["samples"].extend(best["samples"])

    card_selectors = [
        {
            "selector": selector,
            "seen_in_pages": data["seen_in_pages"],
            "max_score": data["max_score"],
            "max_unique_links": data["max_unique_links"],
            "max_unique_property_ids": data["max_unique_property_ids"],
            "samples": list(dict.fromkeys(data["samples"]))[:4],
        }
        for selector, data in selector_summary.items()
    ]
    card_selectors.sort(key=lambda item: (-item["seen_in_pages"], -item["max_score"], item["selector"]))

    rental_field_candidates = defaultdict(lambda: {"seen_in_pages": 0, "samples": []})
    for scan in scans:
        seen_paths_this_page = set()
        for item in scan.get("rental_field_candidates", []):
            path = item["path"]
            rental_field_candidates[path]["samples"].extend(item["samples"])
            if path not in seen_paths_this_page:
                rental_field_candidates[path]["seen_in_pages"] += 1
                seen_paths_this_page.add(path)

    rental_field_summary = [
        {
            "path": path,
            "seen_in_pages": data["seen_in_pages"],
            "samples": list(dict.fromkeys(data["samples"]))[:4],
        }
        for path, data in rental_field_candidates.items()
    ]
    rental_field_summary.sort(key=lambda item: (-item["seen_in_pages"], item["path"]))

    report = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "start_url": start_url,
            "pages_requested": pages_requested,
            "pages_scanned": len(scans),
        },
        "best_card_selectors": card_selectors,
        "rental_field_summary": rental_field_summary,
        "source_summary": source_summary,
        "api_endpoints": deduped_api_endpoints,
        "pagination_analysis": _analyse_pagination(url_history, deduped_api_endpoints),
        "pages": scans,
    }
    report["recipe"] = build_recipe(report)
    return report


def build_recipe(report):
    lines = []
    lines.append("RIGHTMOVE RENTAL SEARCH RECON")
    lines.append("=" * 50)

    best_cards = report.get("best_card_selectors", [])
    if best_cards:
        best = best_cards[0]
        lines.append("\nCARD SELECTOR:")
        lines.append(f"  Best selector: {best['selector']}")
        lines.append(f"  Seen in pages: {best['seen_in_pages']}")
        lines.append(f"  Max score: {best['max_score']}")
        lines.append(f"  Unique sample links: {best['max_unique_links']}")
        lines.append(f"  Samples: {best['samples']}")

    apis = report.get("api_endpoints", [])
    listing_apis = [api for api in apis if api.get("classification") == "listing_search_api"]
    lines.append("\nAPI:")
    if listing_apis:
        lines.append(f"  Listing search API seen: yes ({len(listing_apis)} unique URLs)")
        lines.append(f"  First URL: {listing_apis[0]['url']}")
    else:
        lines.append("  Listing search API seen: no")
    failed_attempts = [
        attempt
        for page in report.get("pages", [])
        for attempt in page.get("api_fetch_attempts", [])
        if not attempt.get("ok")
    ]
    if failed_attempts:
        lines.append(f"  Failed API fetch attempts captured: {len(failed_attempts)}")
        lines.append(f"  Example failure: [{failed_attempts[0].get('status')}] {failed_attempts[0].get('url')}")

    pagination = report.get("pagination_analysis", {})
    lines.append("\nPAGINATION:")
    lines.append(f"  Page URL indices: {pagination.get('page_indices_seen', [])}")
    lines.append(f"  API indices: {pagination.get('api_indices_seen', [])}")
    lines.append(f"  Step sizes: {pagination.get('step_sizes_seen', [])}")
    lines.append(f"  Likely page size: {pagination.get('likely_page_size')}")

    source = report.get("source_summary", {})
    lines.append("\nRENTAL SOURCE SIGNALS:")
    for key in ("rent_pcm", "rent_pw", "deposit", "furnished", "available", "let_agreed"):
        if key in source:
            lines.append(f"  • {key}: {source[key]['unique_samples'][:3]}")

    rental_fields = report.get("rental_field_summary", [])
    if rental_fields:
        lines.append("\nRENTAL API FIELD CANDIDATES:")
        for item in rental_fields[:12]:
            lines.append(f"  • {item['path']}: {item['samples'][:3]}")

    lines.append("\nRECOMMENDED NEXT STEP:")
    lines.append("  Build a rental search scraper only after confirming which fields are stable in listing/search.")
    lines.append("  Prioritise: rent price, frequency (pcm/pw), address/location, listing URL, beds, furnishing, deposit, available date, status.")
    lines.append("  Treat anything missing here as a candidate for the rental detail-page recon phase.")

    return "\n".join(lines)


def save_report(report, output_dir):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    json_path = output_path / f"rightmove_rental_report_{timestamp}.json"
    txt_path = output_path / f"rightmove_rental_report_{timestamp}.txt"

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)

    with txt_path.open("w", encoding="utf-8") as handle:
        handle.write(report["recipe"])

    return json_path, txt_path


def main():
    args = parse_args()
    driver = setup_browser()

    try:
        if args.search_url:
            driver.get(args.search_url)
        else:
            driver.get("https://www.rightmove.co.uk/property-to-rent.html")

        prompt_manual_ready()

        start_url = driver.current_url
        parsed = urlparse(start_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        start_index = int(query.get("index", ["0"])[0])
        keep_zero_index = "index" in query

        scans = []
        seen_api_urls = set()

        for page_offset in range(args.pages):
            page_index = start_index + (page_offset * args.page_size)
            page_url = _build_page_url(start_url, page_index, keep_zero_index)
            api_url = _build_api_url(page_url)
            print(f"\nScanning rental search page index {page_index}")
            scan = scan_rental_page(driver, page_url, api_url, page_index, args.wait_seconds, seen_api_urls)
            scans.append(scan)
            print(
                f"  selector={scan['best_card_selector']['selector'] if scan['best_card_selector'] else 'none'} "
                f"api_ok={scan['api_fetch_success']} "
                f"api_listings={scan['api_listing_count']} "
                f"rental_fields={len(scan['rental_field_candidates'])}"
            )

        report = build_report(scans, start_url, args.pages)
        json_path, txt_path = save_report(report, args.output_dir)

        print("\nSaved rental recon:")
        print(f"  JSON: {json_path}")
        print(f"  TXT:  {txt_path}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
