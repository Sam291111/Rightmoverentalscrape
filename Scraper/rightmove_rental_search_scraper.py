"""
Rightmove Rental Search Scraper
===============================

Semi-automated rental search scraper for Rightmove.

What it does:
  - opens a real browser session so you can handle cookies/CAPTCHA yourself
  - calls the first-party rental listing API discovered by the trigger probe
  - supplements API rows with DOM card fields when useful
  - saves JSON + CSV outputs plus raw API pages

Examples:
  python3 Scraper/rightmove_rental_search_scraper.py \
    --search-url "https://www.rightmove.co.uk/property-to-rent/find.html?searchLocation=White+City%2C+West+London&useLocationIdentifier=true&locationIdentifier=REGION%5E85399&radius=0.0&_includeLetAgreed=on" \
    --pages 3
"""

import argparse
import csv
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import undetected_chromedriver as uc
from selenium.common.exceptions import NoSuchWindowException, WebDriverException


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
CARD_SELECTOR = "div[class*='PropertyCard_propertyCardContainer__']"
API_PATH = "/api/property-search/listing/search"
IMAGE_URL_RE = re.compile(r"https://media\.rightmove\.co\.uk/[^\s\"'>]*property-photo[^\s\"'>]+", re.IGNORECASE)
POSTCODE_RE = re.compile(
    r"(?i)\b(?:GIR\s?0AA|(?:[A-PR-UWYZ][A-HK-Y]?\d[A-HJKPSTUW]?|"
    r"[A-PR-UWYZ][A-HK-Y]?\d{2}|[A-PR-UWYZ][A-HK-Y]?\d[ABEHMNPRVWXY])"
    r"\s?\d[ABD-HJLNP-UW-Z]{2})\b"
)


def parse_args():
    parser = argparse.ArgumentParser(description="Scrape Rightmove rental search results into JSON/CSV.")
    parser.add_argument(
        "--search-url",
        help="Rightmove rental search URL. If omitted, the browser opens the rent homepage and you can navigate manually.",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="How many result pages to scrape, starting from the current page or supplied search URL.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=24,
        help="Search pagination step. Recon currently suggests 24 for Rightmove rentals.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        help="Optional cap on the total number of merged listings to keep.",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=2.0,
        help="Delay after each page load before scraping/fetching.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for JSON, CSV, and raw API page dumps.",
    )
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


def setup_browser(*, headless=False, user_data_dir=None):
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1600,1200")
    if headless:
        options.add_argument("--headless=new")
    if user_data_dir:
        options.add_argument(f"--user-data-dir={user_data_dir}")
    return uc.Chrome(options=options, version_main=None)


def prompt_manual_ready():
    input(
        "\nHandle cookies / CAPTCHA / login in the browser if needed, "
        "then press Enter here to start scraping rentals..."
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


def _recreate_browser(driver, landing_url, *, headless, user_data_dir, reprompt):
    _safe_quit(driver)
    driver = setup_browser(headless=headless, user_data_dir=user_data_dir)
    driver.get(landing_url)
    if reprompt:
        prompt_manual_ready()
    return driver


def _navigate_with_recovery(driver, url, *, headless, user_data_dir, reprompt):
    try:
        if not _driver_window_available(driver):
            return _recreate_browser(
                driver,
                url,
                headless=headless,
                user_data_dir=user_data_dir,
                reprompt=reprompt,
            )
        driver.get(url)
        return driver
    except NoSuchWindowException:
        return _recreate_browser(
            driver,
            url,
            headless=headless,
            user_data_dir=user_data_dir,
            reprompt=reprompt,
        )


def _current_url_with_recovery(driver, landing_url, *, headless, user_data_dir, reprompt):
    try:
        current_url = driver.current_url
    except NoSuchWindowException:
        driver = _recreate_browser(
            driver,
            landing_url,
            headless=headless,
            user_data_dir=user_data_dir,
            reprompt=reprompt,
        )
        current_url = driver.current_url
    except WebDriverException:
        driver = _recreate_browser(
            driver,
            landing_url,
            headless=headless,
            user_data_dir=user_data_dir,
            reprompt=reprompt,
        )
        current_url = driver.current_url
    return current_url, driver


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
    cleaned = _normalise_space(value).upper().replace(" ", "")
    if len(cleaned) <= 3:
        return cleaned
    return f"{cleaned[:-3]} {cleaned[-3:]}"


def _first_valid_postcode(*values):
    for value in values:
        if not value:
            continue
        match = POSTCODE_RE.search(str(value))
        if match:
            return _normalise_postcode(match.group(0))
    return None


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


def _nested_value(node, *path):
    current = node
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _collect_matching_strings(node, predicate, results=None):
    if results is None:
        results = []
    if isinstance(node, dict):
        for value in node.values():
            _collect_matching_strings(value, predicate, results)
    elif isinstance(node, list):
        for item in node:
            _collect_matching_strings(item, predicate, results)
    elif isinstance(node, str):
        if predicate(node):
            results.append(node)
    return results


def _to_int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_bool_or_none(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes"}:
            return True
        if lowered in {"false", "no"}:
            return False
    return None


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
    if any(key in node for key in ("bedrooms", "bathrooms", "propertySubType", "images")):
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
    for candidate in candidates:
        listing_id = _extract_property_id(candidate.get("id") or candidate.get("propertyId"))
        address = candidate.get("displayAddress") or candidate.get("address")
        signature = (listing_id, _normalise_space(str(address)))
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(candidate)
    return deduped


def _extract_lat_lng(node):
    location = node.get("location") if isinstance(node, dict) else None
    lat = _first_scalar_for_keys(location, ("latitude", "lat")) if isinstance(location, dict) else None
    lng = _first_scalar_for_keys(location, ("longitude", "lng", "lon")) if isinstance(location, dict) else None
    if lat is None:
        lat = _first_scalar_for_keys(node, ("latitude", "lat"))
    if lng is None:
        lng = _first_scalar_for_keys(node, ("longitude", "lng", "lon"))
    try:
        lat = float(lat) if lat is not None else None
    except (TypeError, ValueError):
        lat = None
    try:
        lng = float(lng) if lng is not None else None
    except (TypeError, ValueError):
        lng = None
    return lat, lng


def _extract_image_urls(node):
    urls = _collect_matching_strings(node, lambda value: bool(IMAGE_URL_RE.search(value)))
    clean = []
    seen = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        clean.append(url)
    return clean


def _extract_rent_prices(node):
    display_prices = []
    if isinstance(node, dict):
        price = node.get("price")
        if isinstance(price, dict):
            for item in price.get("displayPrices", []) or []:
                if isinstance(item, dict):
                    text = item.get("displayPrice")
                    if text:
                        display_prices.append(_normalise_space(str(text)))
            for key in ("displayPrice", "formattedPrice"):
                text = price.get(key)
                if text:
                    display_prices.append(_normalise_space(str(text)))

    display_prices = list(dict.fromkeys([price for price in display_prices if price]))
    amount_pcm = None
    amount_pw = None
    text_pcm = None
    text_pw = None

    for text in display_prices:
        match = re.search(r"£\s*([\d,]+)\s*(pcm|pw)\b", text, re.IGNORECASE)
        if not match:
            continue
        amount = int(match.group(1).replace(",", ""))
        frequency = match.group(2).lower()
        if frequency == "pcm" and amount_pcm is None:
            amount_pcm = amount
            text_pcm = f"£{match.group(1)} pcm"
        if frequency == "pw" and amount_pw is None:
            amount_pw = amount
            text_pw = f"£{match.group(1)} pw"

    rent_amount = amount_pcm if amount_pcm is not None else amount_pw
    rent_frequency = "pcm" if amount_pcm is not None else ("pw" if amount_pw is not None else None)
    rent_text = text_pcm if text_pcm is not None else text_pw

    if rent_text is None:
        scalar_text = _first_scalar_for_keys(node, ("displayPrice", "formattedPrice"))
        if scalar_text:
            scalar_text = _normalise_space(str(scalar_text))
            match = re.search(r"£\s*([\d,]+)\s*(pcm|pw)\b", scalar_text, re.IGNORECASE)
            if match:
                rent_amount = int(match.group(1).replace(",", ""))
                rent_frequency = match.group(2).lower()
                rent_text = f"£{match.group(1)} {rent_frequency}"

    return {
        "rent_text": rent_text,
        "rent_amount": rent_amount,
        "rent_frequency": rent_frequency,
        "rent_amount_pcm": amount_pcm,
        "rent_amount_pw": amount_pw,
        "rent_text_pcm": text_pcm,
        "rent_text_pw": text_pw,
    }


def _extract_listing_status(node):
    if not isinstance(node, dict):
        return None
    listing_update = node.get("listingUpdate")
    if isinstance(listing_update, dict):
        status = listing_update.get("listingUpdateReason") or listing_update.get("reason")
        if status:
            return _normalise_space(str(status))
    for key in ("status", "displayStatus", "availability"):
        value = node.get(key)
        if value:
            return _normalise_space(str(value))
    return None


def _extract_listing_from_api(node, page_index):
    listing_id = _extract_property_id(node.get("id") or node.get("propertyId"))
    property_url = _canonical_listing_url(
        _first_scalar_for_keys(node, ("propertyUrl", "url", "detailUrl", "propertyLink"))
    )
    if not listing_id:
        listing_id = _extract_property_id(property_url)

    lat, lng = _extract_lat_lng(node)
    image_urls = _extract_image_urls(node)
    rent_prices = _extract_rent_prices(node)
    address = _first_scalar_for_keys(node, ("displayAddress", "address", "streetAddress"))
    summary = _first_scalar_for_keys(node, ("summary", "description", "propertyDescription"))
    property_type = _first_scalar_for_keys(node, ("propertySubType", "propertyType", "displayPropertyType", "type"))
    bedrooms = _first_scalar_for_keys(node, ("bedrooms", "numberOfBedrooms", "bedroomCount"))
    bathrooms = _first_scalar_for_keys(node, ("bathrooms", "numberOfBathrooms", "bathroomCount"))
    image_count = _first_scalar_for_keys(node, ("numberOfImages", "imageCount", "imagesCount"))
    floorplan_count = _first_scalar_for_keys(node, ("numberOfFloorplans", "floorplanCount"))
    virtual_tour_count = _first_scalar_for_keys(node, ("numberOfVirtualTours", "virtualTourCount"))
    postcode = _first_valid_postcode(address, summary)

    tenure_value = _nested_value(node, "tenure", "tenureType")
    if not tenure_value:
        tenure_value = _first_scalar_for_keys(node, ("tenureType", "tenure"))

    build_to_rent = _nested_value(node, "customer", "buildToRent")
    if build_to_rent is None:
        build_to_rent = node.get("buildToRent")

    return {
        "market": "rent",
        "listing_id": listing_id,
        "listing_url": property_url,
        "location": _normalise_space(str(address)) if address else None,
        "postcode": postcode,
        "property_type": _normalise_space(str(property_type)) if property_type else None,
        "bedrooms": _to_int_or_none(bedrooms),
        "bathrooms": _to_int_or_none(bathrooms),
        "summary": _normalise_space(str(summary)) if summary else None,
        "latitude": lat,
        "longitude": lng,
        "image_url": image_urls[0] if image_urls else None,
        "image_urls": image_urls,
        "image_count": _to_int_or_none(image_count),
        "floorplan_count": _to_int_or_none(floorplan_count),
        "virtual_tour_count": _to_int_or_none(virtual_tour_count),
        "let_available_date": _first_scalar_for_keys(node, ("letAvailableDate", "availableDate")),
        "tenure": _normalise_space(str(tenure_value)) if tenure_value else None,
        "listing_status": _extract_listing_status(node),
        "featured": bool(node.get("featuredProperty") or node.get("premiumListing")),
        "students": _to_bool_or_none(_first_scalar_for_keys(node, ("students",))),
        "online_viewings_available": _to_bool_or_none(
            _first_scalar_for_keys(node, ("onlineViewingsAvailable",))
        ),
        "build_to_rent": _to_bool_or_none(build_to_rent),
        "source_page_index": page_index,
        "source": "api",
        **rent_prices,
    }


def _build_page_url(base_url, page_index, keep_zero_index):
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if page_index == 0 and not keep_zero_index:
        query.pop("index", None)
    else:
        query["index"] = [str(page_index)]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _normalise_api_query(page_url, *, default_sort=None):
    parsed = urlparse(page_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["channel"] = ["RENT"]
    query["transactionType"] = ["LETTING"]
    if default_sort is not None and "sortType" not in query:
        query["sortType"] = [str(default_sort)]
    return urlencode(query, doseq=True)


def _build_api_candidate_urls(page_url, resolved_url):
    candidates = []
    seen = set()
    for source_url in [resolved_url, page_url]:
        if not source_url:
            continue
        parsed = urlparse(source_url)
        for default_sort in (None, 6, 2):
            query = _normalise_api_query(source_url, default_sort=default_sort)
            api_url = urlunparse(parsed._replace(path=API_PATH, query=query))
            if api_url in seen:
                continue
            seen.add(api_url)
            candidates.append(api_url)
    return candidates


def wait_for_cards(driver, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        count = driver.execute_script(
            "return document.querySelectorAll(arguments[0]).length;",
            CARD_SELECTOR,
        )
        if count and count > 0:
            return True
        time.sleep(0.5)
    return False


def fetch_api_payload(driver, api_url):
    script = """
const url = arguments[0];
const done = arguments[arguments.length - 1];
fetch(url, {
  credentials: 'include',
  headers: {
    'accept': 'application/json, text/plain, */*'
  }
})
  .then(async (response) => {
    done({
      ok: response.ok,
      status: response.status,
      text: await response.text()
    });
  })
  .catch((error) => {
    done({
      ok: false,
      status: 0,
      error: String(error)
    });
  });
"""
    result = driver.execute_async_script(script, api_url)
    if not result.get("ok"):
        error = result.get("error", f"HTTP {result.get('status')}")
        raise RuntimeError(f"API request failed for {api_url}: {error}")
    try:
        return json.loads(result["text"])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"API returned non-JSON for {api_url}") from exc


def fetch_api_payload_from_candidates(driver, candidate_urls):
    attempts = []
    for api_url in candidate_urls:
        try:
            payload = fetch_api_payload(driver, api_url)
            attempts.append({"api_url": api_url, "ok": True, "payload": payload})
        except Exception as exc:
            attempts.append({"api_url": api_url, "ok": False, "error": str(exc)})
    return attempts


def _extract_rent_from_text(value):
    text = _normalise_space(value)
    if not text:
        return {"rent_text": None, "rent_amount": None, "rent_frequency": None}
    match = re.search(r"£\s*([\d,]+)\s*(pcm|pw)\b", text, re.IGNORECASE)
    if not match:
        return {"rent_text": None, "rent_amount": None, "rent_frequency": None}
    amount = int(match.group(1).replace(",", ""))
    frequency = match.group(2).lower()
    return {
        "rent_text": f"£{match.group(1)} {frequency}",
        "rent_amount": amount,
        "rent_frequency": frequency,
    }


def scrape_dom_cards(driver, page_index):
    script = """
const selector = arguments[0];
const cards = Array.from(document.querySelectorAll(selector));
return cards.map((card, position) => {
  const linkEl = card.querySelector("a[href*='/properties/']");
  const addressEl = card.querySelector("address");
  const priceEl = card.querySelector("div[class*='price']");
  const summaryEl = card.querySelector("[data-testid='property-description'], p[class*='summary']");
  const imageEls = Array.from(card.querySelectorAll("img[src*='media.rightmove.co.uk']"));
  const infoSpans = Array.from(card.querySelectorAll("[data-testid='property-information'] span"))
    .map((el) => (el.textContent || '').trim())
    .filter(Boolean);
  const imageCountLabel = card.getAttribute("aria-label")
    || (card.querySelector("[aria-label*='Property has']") || {}).getAttribute?.("aria-label")
    || (card.querySelector("[aria-label*='Viewing photo']") || {}).getAttribute?.("aria-label")
    || '';

  return {
    listing_url: linkEl ? linkEl.href.split('#')[0] : null,
    address: addressEl ? addressEl.textContent : null,
    price_text: priceEl ? priceEl.textContent : null,
    summary: summaryEl ? summaryEl.textContent : null,
    property_type: infoSpans[0] || null,
    bedrooms: infoSpans[1] || null,
    bathrooms: infoSpans[2] || null,
    image_urls: Array.from(new Set(imageEls.map((img) => img.src))),
    image_count_label: imageCountLabel,
    featured: card.textContent.includes('FEATURED'),
    position_on_page: position + 1
  };
});
"""
    cards = driver.execute_script(script, CARD_SELECTOR)
    cleaned = []
    for card in cards:
        listing_url = _canonical_listing_url(card.get("listing_url"))
        listing_id = _extract_property_id(listing_url)
        image_urls = [url for url in card.get("image_urls", []) if IMAGE_URL_RE.search(url or "")]
        rent_data = _extract_rent_from_text(card.get("price_text"))
        cleaned.append(
            {
                "market": "rent",
                "listing_id": listing_id,
                "listing_url": listing_url,
                "location": _normalise_space(card.get("address")),
                "postcode": _first_valid_postcode(card.get("address")),
                "property_type": _normalise_space(card.get("property_type")),
                "bedrooms": _to_int_or_none(card.get("bedrooms")),
                "bathrooms": _to_int_or_none(card.get("bathrooms")),
                "summary": _normalise_space(card.get("summary")),
                "image_url": image_urls[0] if image_urls else None,
                "image_urls": image_urls,
                "image_count": _extract_image_count(card.get("image_count_label")),
                "featured": bool(card.get("featured")),
                "position_on_page": card.get("position_on_page"),
                "source_page_index": page_index,
                "source": "dom",
                **rent_data,
            }
        )
    return cleaned


def _extract_image_count(label):
    if not label:
        return None
    match = re.search(r"(\d+)\s+images?", str(label))
    return int(match.group(1)) if match else None


def _choose_best_api_attempt(api_attempts, dom_cards, page_index):
    dom_ids = {card.get("listing_id") for card in dom_cards if card.get("listing_id")}
    dom_urls = {card.get("listing_url") for card in dom_cards if card.get("listing_url")}
    best = None

    for attempt in api_attempts:
        if not attempt.get("ok"):
            continue
        payload = attempt["payload"]
        api_listings = [_extract_listing_from_api(node, page_index) for node in _find_listing_dicts(payload)]
        api_ids = {item.get("listing_id") for item in api_listings if item.get("listing_id")}
        api_urls = {item.get("listing_url") for item in api_listings if item.get("listing_url")}
        overlap_ids = len(dom_ids & api_ids)
        overlap_urls = len(dom_urls & api_urls)
        overlap_score = (overlap_ids * 2) + overlap_urls
        attempt["overlap_ids"] = overlap_ids
        attempt["overlap_urls"] = overlap_urls
        attempt["api_listing_count"] = len(api_listings)
        attempt["selected_listing_ids_sample"] = sorted(api_ids)[:10]
        attempt["api_listings"] = api_listings

        candidate = (
            overlap_score,
            overlap_ids,
            overlap_urls,
            -abs(len(api_listings) - len(dom_cards)),
        )
        if best is None or candidate > best["score"]:
            best = {
                "score": candidate,
                "attempt": attempt,
            }

    if best is None:
        errors = [item for item in api_attempts if not item.get("ok")]
        raise RuntimeError(
            "Rental search API failed for all candidate URLs:\n"
            + "\n".join(f"  - {item['api_url']} => {item['error']}" for item in errors)
        )
    return best["attempt"]


def merge_listing_data(api_listing, dom_listing):
    merged = {}
    for source in (api_listing or {}, dom_listing or {}):
        for key, value in source.items():
            if value in (None, "", []):
                continue
            if key == "image_urls":
                existing = merged.get(key, [])
                merged[key] = list(dict.fromkeys(existing + value))
                continue
            merged.setdefault(key, value)

    if merged.get("image_urls") and not merged.get("image_url"):
        merged["image_url"] = merged["image_urls"][0]
    if not merged.get("listing_url") and merged.get("listing_id"):
        merged["listing_url"] = f"https://www.rightmove.co.uk/properties/{merged['listing_id']}"
    if not merged.get("postcode"):
        merged["postcode"] = _first_valid_postcode(merged.get("location"), merged.get("summary"))
    if not merged.get("rent_amount") and merged.get("rent_text"):
        match = re.search(r"£\s*([\d,]+)\s*(pcm|pw)\b", merged["rent_text"], re.IGNORECASE)
        if match:
            merged["rent_amount"] = int(match.group(1).replace(",", ""))
            merged["rent_frequency"] = match.group(2).lower()
    return merged


def save_outputs(output_dir, merged_results, raw_pages, metadata):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    raw_path = output_path / f"rental_raw_pages_{timestamp}"
    output_path.mkdir(parents=True, exist_ok=True)
    raw_path.mkdir(parents=True, exist_ok=True)

    for raw_page in raw_pages:
        page_file = raw_path / f"rightmove_rental_api_index_{raw_page['page_index']:03d}.json"
        with page_file.open("w", encoding="utf-8") as handle:
            json.dump(raw_page, handle, indent=2, ensure_ascii=False)

    dataset = {
        "meta": metadata,
        "results": merged_results,
    }

    json_path = output_path / f"rightmove_rental_search_results_{timestamp}.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(dataset, handle, indent=2, ensure_ascii=False)

    csv_path = output_path / f"rightmove_rental_search_results_{timestamp}.csv"
    fieldnames = [
        "listing_id",
        "rent_amount",
        "rent_frequency",
        "rent_text",
        "rent_amount_pcm",
        "rent_amount_pw",
        "location",
        "postcode",
        "listing_url",
        "image_url",
        "image_count",
        "floorplan_count",
        "virtual_tour_count",
        "property_type",
        "bedrooms",
        "bathrooms",
        "let_available_date",
        "listing_status",
        "tenure",
        "students",
        "online_viewings_available",
        "build_to_rent",
        "latitude",
        "longitude",
        "summary",
        "featured",
        "position_on_page",
        "source_page_index",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in merged_results:
            row = dict(item)
            writer.writerow({field: row.get(field) for field in fieldnames})

    return json_path, csv_path, raw_path


def main():
    args = parse_args()
    driver = setup_browser(headless=args.headless, user_data_dir=args.user_data_dir)
    raw_pages = []
    pages_scraped = 0
    stop_reason = "completed_requested_pages"
    last_page_index = None

    try:
        if args.search_url:
            driver = _navigate_with_recovery(
                driver,
                args.search_url,
                headless=args.headless,
                user_data_dir=args.user_data_dir,
                reprompt=False,
            )
        else:
            driver = _navigate_with_recovery(
                driver,
                "https://www.rightmove.co.uk/property-to-rent.html",
                headless=args.headless,
                user_data_dir=args.user_data_dir,
                reprompt=False,
            )

        if args.interactive:
            prompt_manual_ready()

        start_url, driver = _current_url_with_recovery(
            driver,
            args.search_url or "https://www.rightmove.co.uk/property-to-rent.html",
            headless=args.headless,
            user_data_dir=args.user_data_dir,
            reprompt=args.interactive,
        )
        parsed = urlparse(start_url)
        start_query = parse_qs(parsed.query, keep_blank_values=True)
        start_index = int(start_query.get("index", ["0"])[0])
        keep_zero_index = "index" in start_query

        all_results = {}

        for page_offset in range(args.pages):
            page_index = start_index + (page_offset * args.page_size)
            last_page_index = page_index
            page_url = _build_page_url(start_url, page_index, keep_zero_index)

            print(f"\nScraping rental page index {page_index}")
            print(f"  Page URL: {page_url}")

            driver = _navigate_with_recovery(
                driver,
                page_url,
                headless=args.headless,
                user_data_dir=args.user_data_dir,
                reprompt=args.interactive,
            )
            if not wait_for_cards(driver):
                if page_offset == 0:
                    raise RuntimeError(f"Cards did not appear for page index {page_index}")
                print(
                    "  No cards appeared on this page. Stopping pagination and keeping the "
                    "results collected so far. This usually means the requested page is past "
                    "the available result range or Rightmove stopped serving deeper pages."
                )
                stop_reason = "cards_missing_after_results_started"
                break
            time.sleep(args.wait_seconds)

            dom_cards = scrape_dom_cards(driver, page_index)
            api_candidates = _build_api_candidate_urls(page_url, driver.current_url)
            api_attempts = fetch_api_payload_from_candidates(driver, api_candidates)
            selected_attempt = _choose_best_api_attempt(api_attempts, dom_cards, page_index)
            payload = selected_attempt["payload"]
            api_url = selected_attempt["api_url"]
            api_listings = selected_attempt["api_listings"]

            raw_pages.append(
                {
                    "page_index": page_index,
                    "page_url": page_url,
                    "resolved_page_url": driver.current_url,
                    "api_url": api_url,
                    "api_attempts": [
                        {
                            key: value
                            for key, value in attempt.items()
                            if key not in {"payload", "api_listings"}
                        }
                        for attempt in api_attempts
                    ],
                    "payload": payload,
                }
            )

            by_id = {}
            for item in api_listings + dom_cards:
                listing_id = item.get("listing_id")
                listing_url = item.get("listing_url")
                key = listing_id or listing_url
                if not key:
                    continue
                by_id.setdefault(key, {})
                by_id[key] = merge_listing_data(by_id[key], item)

            for key, value in by_id.items():
                all_results[key] = merge_listing_data(all_results.get(key, {}), value)

            print(
                f"  API URL: {api_url}\n"
                f"  API/DOM overlap: ids={selected_attempt.get('overlap_ids', 0)} urls={selected_attempt.get('overlap_urls', 0)}\n"
                f"  Found {len(api_listings)} API listings, {len(dom_cards)} DOM cards, "
                f"{len(by_id)} merged listings on this page."
            )
            pages_scraped += 1

            if not by_id:
                print("  No listings were found on this page. Stopping pagination.")
                stop_reason = "empty_page"
                break

            if args.max_results and len(all_results) >= args.max_results:
                print(f"  Reached max-results cap of {args.max_results}.")
                stop_reason = "max_results_reached"
                break

        merged_results = sorted(
            all_results.values(),
            key=lambda item: (
                item.get("source_page_index", 0),
                item.get("position_on_page") or 999,
                str(item.get("listing_id") or ""),
            ),
        )
        if args.max_results:
            merged_results = merged_results[: args.max_results]

        metadata = {
            "generated_at": datetime.now().isoformat(),
            "market": "rent",
            "start_url": start_url,
            "pages_requested": args.pages,
            "page_size": args.page_size,
            "pages_scraped": pages_scraped,
            "max_results": args.max_results,
            "results_count": len(merged_results),
            "stop_reason": stop_reason,
            "last_page_index": last_page_index,
            "interactive": bool(args.interactive),
            "headless": bool(args.headless),
            "user_data_dir": args.user_data_dir,
        }

        json_path, csv_path, raw_path = save_outputs(args.output_dir, merged_results, raw_pages, metadata)

        print("\nSaved outputs:")
        print(f"  JSON dataset: {json_path}")
        print(f"  CSV dataset:  {csv_path}")
        print(f"  Raw API pages: {raw_path}")
        print(f"  Listings: {len(merged_results)}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
