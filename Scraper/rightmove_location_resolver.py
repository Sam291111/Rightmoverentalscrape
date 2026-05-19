"""
Rightmove Location Resolver
===========================

Resolves human-readable place names into Rightmove rental search URLs and
`locationIdentifier` values.

This prefers a direct server-side resolution attempt and falls back to driving
Rightmove's own search UI when needed.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse, urlunparse

import undetected_chromedriver as uc
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


RIGHTMOVE_RENT_HOME = "https://www.rightmove.co.uk/property-to-rent.html"
RIGHTMOVE_RENT_FIND = "https://www.rightmove.co.uk/property-to-rent/find.html"
LOCATION_IDENTIFIER_RE = re.compile(r"locationIdentifier=([^&]+)")
JSON_LOCATION_IDENTIFIER_RE = re.compile(r'"locationIdentifier"\s*:\s*"([^"]+)"')
JSON_RESULT_COUNT_RE = re.compile(r'"resultCount"\s*:\s*"?(?P<count>[\d,]+)"?')
JSON_PAGE_TOTAL_RE = re.compile(r'"pagination"\s*:\s*\{.*?"total"\s*:\s*(\d+)', re.DOTALL)
JSON_LOCATION_NAME_RE = re.compile(
    r'"location"\s*:\s*\{.*?"displayName"\s*:\s*"([^"]+)".*?"locationType"\s*:\s*"([^"]+)".*?"id"\s*:\s*(\d+)',
    re.DOTALL,
)

INPUT_SELECTORS = [
    "input[placeholder*='postcode' i]",
    "input[aria-label*='postcode' i]",
    "input[aria-label*='city' i]",
    "input[role='combobox']",
    "input[type='search']",
    "input",
]

SUGGESTION_SELECTORS = [
    "[role='option']",
    "[data-testid*='suggestion']",
    "li",
    "button",
    "a",
]

TO_RENT_XPATHS = [
    "//button[contains(translate(normalize-space(.), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'TO RENT')]",
    "//a[contains(translate(normalize-space(.), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'TO RENT')]",
]


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
        "then press Enter here to continue resolving Rightmove locations..."
    )


def _normalise_space(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def _slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug or "location"


def _build_direct_resolution_url(query, radius=0.0):
    params = {
        "searchLocation": query,
        "useLocationIdentifier": "true",
        "radius": str(radius),
        "index": "0",
        "sortType": "6",
        "channel": "RENT",
        "transactionType": "LETTING",
    }
    return f"{RIGHTMOVE_RENT_FIND}?{urlencode(params)}"


def _extract_resolution(current_url, page_source, query):
    parsed = urlparse(current_url or "")
    query_params = parse_qs(parsed.query)
    location_identifier = None
    if query_params.get("locationIdentifier"):
        location_identifier = query_params["locationIdentifier"][0]
    if not location_identifier:
        match = LOCATION_IDENTIFIER_RE.search(current_url or "") or JSON_LOCATION_IDENTIFIER_RE.search(page_source or "")
        if match:
            location_identifier = match.group(1)

    location_name = None
    location_type = None
    location_id = None
    match = JSON_LOCATION_NAME_RE.search(page_source or "")
    if match:
        location_name, location_type, location_id = match.group(1), match.group(2), int(match.group(3))
    elif query_params.get("searchLocation"):
        location_name = query_params["searchLocation"][0]

    result_count = None
    match = JSON_RESULT_COUNT_RE.search(page_source or "")
    if match:
        try:
            result_count = int(match.group("count").replace(",", ""))
        except ValueError:
            result_count = None

    pagination_total = None
    match = JSON_PAGE_TOTAL_RE.search(page_source or "")
    if match:
        try:
            pagination_total = int(match.group(1))
        except ValueError:
            pagination_total = None

    ok = bool(location_identifier)
    return {
        "query": query,
        "ok": ok,
        "search_url": current_url,
        "location_identifier": location_identifier,
        "location_name": location_name,
        "location_type": location_type,
        "location_id": location_id,
        "reported_result_count": result_count,
        "reported_pagination_total": pagination_total,
    }


def _wait_for_resolution(driver, query, timeout=20):
    deadline = time.time() + timeout
    last_url = ""
    last_source = ""
    while time.time() < deadline:
        try:
            last_url = driver.current_url
            last_source = driver.page_source
        except WebDriverException:
            time.sleep(0.5)
            continue
        resolved = _extract_resolution(last_url, last_source, query)
        if resolved["ok"]:
            return resolved
        time.sleep(0.5)
    return _extract_resolution(last_url, last_source, query)


def _best_input(driver):
    for selector in INPUT_SELECTORS:
        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        for element in elements:
            try:
                if element.is_displayed() and element.is_enabled():
                    return element
            except WebDriverException:
                continue
    raise NoSuchElementException("Could not find a visible Rightmove location input.")


def _try_click_suggestion(driver, query):
    tokens = [token.lower() for token in re.split(r"[\s,]+", query) if token]
    for selector in SUGGESTION_SELECTORS:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if not element.is_displayed():
                    continue
                text = _normalise_space(element.text)
                lower = text.lower()
                if text and any(token in lower for token in tokens):
                    element.click()
                    return True
            except WebDriverException:
                continue
    return False


def _click_to_rent(driver):
    for xpath in TO_RENT_XPATHS:
        try:
            button = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, xpath)))
            button.click()
            return True
        except (TimeoutException, WebDriverException):
            continue
    return False


def resolve_location(driver, query, *, headless=False, user_data_dir=None, interactive=False, radius=0.0, timeout=20):
    direct_url = _build_direct_resolution_url(query, radius=radius)
    driver.get(direct_url)
    resolved = _wait_for_resolution(driver, query, timeout=timeout)
    if resolved["ok"]:
        resolved["resolution_method"] = "direct_url"
        return driver, resolved

    driver.get(RIGHTMOVE_RENT_HOME)
    if interactive:
        prompt_manual_ready()
    search_input = _best_input(driver)
    search_input.clear()
    search_input.send_keys(query)
    time.sleep(1.5)
    clicked_suggestion = _try_click_suggestion(driver, query)
    if not clicked_suggestion:
        search_input.send_keys(Keys.ENTER)
    if "property-to-rent.html" in (driver.current_url or ""):
        _click_to_rent(driver)
    resolved = _wait_for_resolution(driver, query, timeout=timeout)
    resolved["resolution_method"] = "ui_search"
    return driver, resolved


def save_resolution_report(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


__all__ = [
    "RIGHTMOVE_RENT_HOME",
    "RIGHTMOVE_RENT_FIND",
    "resolve_location",
    "save_resolution_report",
    "setup_browser",
    "prompt_manual_ready",
    "_build_direct_resolution_url",
    "_extract_resolution",
    "_slugify",
]
