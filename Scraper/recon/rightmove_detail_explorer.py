"""
Rightmove Detail Explorer
=========================

Recon tool for individual Rightmove listing pages.

It samples real listing detail pages and tells you:
  - Which selectors consistently expose price, address, description and facts
  - How many real property photos are present on the page
  - Whether floorplans, brochures or EPC assets are directly linked
  - Which first-party Rightmove APIs fire on detail pages
  - What useful structured data exists in page source

Examples:
  python3 Scraper/recon/rightmove_detail_explorer.py --listing-url "https://www.rightmove.co.uk/properties/173395130"
  python3 Scraper/recon/rightmove_detail_explorer.py --results-json "Scraper/output/rightmove_search_results_20260324_155603.json" --limit 3
"""

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By


SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
PRICE_SELECTORS = [
    "[data-testid*='price']",
    "div[class*='price']",
    "span[itemprop='price']",
]

ADDRESS_SELECTORS = [
    "h1",
    "address",
    "[data-testid*='address']",
    "[class*='address']",
]

DESCRIPTION_SELECTORS = [
    "[data-testid='property-description']",
    "[data-testid*='description']",
    "[itemprop='description']",
    "div[class*='description']",
    "section[class*='description']",
]

FACTS_SELECTORS = [
    "[data-testid*='property-information']",
    "[class*='propertyInformation']",
    "dl",
    "section li",
]

FLOORPLAN_SELECTORS = [
    "a[href*='floorplan']",
    "img[src*='floorplan']",
    "a[href$='.pdf']",
]

SOURCE_PATTERNS = {
    "listing_id": r'"id"\s*:\s*(\d{6,})',
    "display_address": r'"displayAddress"\s*:\s*"([^"]+)"',
    "price_amount": r'"price":\{"amount":(\d+)',
    "property_type": r'"propertyType"\s*:\s*"([^"]+)"',
    "bedrooms": r'\b([1-9]\d?)\s*bed(?:room)?s?\b',
    "bathrooms": r'\b([1-9]\d?)\s*bath(?:room)?s?\b',
    "lat_lng": r'"latitude"\s*:\s*([-\d.]+).*?"longitude"\s*:\s*([-\d.]+)',
    "property_photo_urls": r'https://media\.rightmove\.co\.uk/[^\s"\']*property-photo[^\s"\']+',
    "property_floorplan_urls": r'https://media\.rightmove\.co\.uk/[^\s"\']*property-floorplan[^\s"\']+',
    "epc_urls": r'https://media\.rightmove\.co\.uk/[^\s"\']*epc[^\s"\']+',
}

PROPERTY_PHOTO_RE = re.compile(r"https://media\.rightmove\.co\.uk/[^\s\"'>]*property-photo[^\s\"'>]+", re.IGNORECASE)
PROPERTY_FLOORPLAN_RE = re.compile(r"https://media\.rightmove\.co\.uk/[^\s\"'>]*property-floorplan[^\s\"'>]+", re.IGNORECASE)


def parse_args():
    parser = argparse.ArgumentParser(description="Recon Rightmove property detail pages.")
    parser.add_argument("--listing-url", help="Single Rightmove property detail URL to inspect.")
    parser.add_argument(
        "--results-json",
        help="Search scraper JSON output. If omitted, the latest output/rightmove_search_results_*.json is used.",
    )
    parser.add_argument("--limit", type=int, default=3, help="How many listing URLs to inspect.")
    parser.add_argument("--wait-seconds", type=float, default=3.0, help="Delay after each page load.")
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


def _normalise_space(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def _canonical_url(url):
    if not url:
        return None
    if url.startswith("/"):
        url = f"https://www.rightmove.co.uk{url}"
    return url.split("#", 1)[0]


def _extract_property_id(url_or_text):
    if not url_or_text:
        return None
    match = re.search(r"/properties/(\d{6,})", str(url_or_text)) or re.search(r"\b(\d{6,})\b", str(url_or_text))
    return match.group(1) if match else None


def _latest_results_json():
    candidates = sorted(
        OUTPUT_DIR.glob("rightmove_search_results_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_listing_urls(args):
    if args.listing_url:
        return [_canonical_url(args.listing_url)]

    results_json = Path(args.results_json) if args.results_json else _latest_results_json()
    if not results_json or not results_json.exists():
        raise FileNotFoundError("No search results JSON found. Pass --results-json or run the search scraper first.")

    data = json.loads(results_json.read_text())
    urls = []
    for result in data.get("results", []):
        url = _canonical_url(result.get("listing_url"))
        if url and url not in urls:
            urls.append(url)
        if len(urls) >= args.limit:
            break
    if not urls:
        raise RuntimeError(f"No listing URLs found in {results_json}")
    return urls


def prompt_manual_ready():
    input(
        "\nHandle cookies / CAPTCHA / login in the browser if needed, "
        "then press Enter here to start the detail-page recon..."
    )


def wait_for_page(driver, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = driver.execute_script("return document.readyState")
        title = _normalise_space(driver.title)
        if state == "complete" and title:
            return True
        time.sleep(0.5)
    return False


def visible_elements(driver, selector):
    try:
        return [el for el in driver.find_elements(By.CSS_SELECTOR, selector) if el.is_displayed()]
    except Exception:
        return []


def probe_text_selectors(driver, selectors, max_sample_chars=220):
    hits = []
    for selector in selectors:
        elements = visible_elements(driver, selector)
        texts = []
        for element in elements[:5]:
            text = _normalise_space(element.text)
            if text:
                texts.append(text[:max_sample_chars])
        unique_texts = list(dict.fromkeys(texts))
        if unique_texts:
            hits.append({
                "selector": selector,
                "count": len(elements),
                "samples": unique_texts[:3],
            })
    return hits


def probe_floorplan_selectors(driver):
    hits = []
    for selector in FLOORPLAN_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        urls = []
        for element in elements[:10]:
            href = element.get_attribute("href") or element.get_attribute("src")
            href = _canonical_url(href)
            if href:
                urls.append(href)
        urls = list(dict.fromkeys(urls))
        if urls:
            hits.append({
                "selector": selector,
                "count": len(elements),
                "samples": urls[:5],
            })
    return hits


def scrape_dom_snapshot(driver):
    script = """
const propertyPhotoPattern = /https:\\/\\/media\\.rightmove\\.co\\.uk\\/.*property-photo/i;
const floorplanPattern = /https:\\/\\/media\\.rightmove\\.co\\.uk\\/.*property-floorplan/i;

const images = Array.from(document.querySelectorAll('img[src]'))
  .map((img) => img.src)
  .filter((src) => propertyPhotoPattern.test(src));

const floorplans = Array.from(document.querySelectorAll('a[href], img[src]'))
  .map((el) => el.href || el.src || '')
  .filter((url) => floorplanPattern.test(url) || /floorplan/i.test(url));

const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
  .map((el) => (el.textContent || '').trim())
  .filter(Boolean);

const factLists = Array.from(document.querySelectorAll('dl, section li'))
  .map((el) => (el.textContent || '').trim())
  .filter(Boolean)
  .slice(0, 20);

const text = (document.body.innerText || '').trim();

return {
  title: document.title || null,
  h1: (document.querySelector('h1') || {}).textContent || null,
  price_text: (document.querySelector("[data-testid*='price'], div[class*='price']") || {}).textContent || null,
  headings,
  property_photo_urls: Array.from(new Set(images)),
  floorplan_urls: Array.from(new Set(floorplans)),
  fact_list_samples: factLists,
  body_text_sample: text.slice(0, 1500)
};
"""
    snapshot = driver.execute_script(script)
    snapshot["title"] = _normalise_space(snapshot.get("title"))
    snapshot["h1"] = _normalise_space(snapshot.get("h1"))
    snapshot["price_text"] = _normalise_space(snapshot.get("price_text"))
    snapshot["headings"] = [_normalise_space(item) for item in snapshot.get("headings", []) if _normalise_space(item)]
    snapshot["property_photo_urls"] = list(dict.fromkeys(snapshot.get("property_photo_urls", [])))
    snapshot["floorplan_urls"] = list(dict.fromkeys(snapshot.get("floorplan_urls", [])))
    snapshot["fact_list_samples"] = [_normalise_space(item) for item in snapshot.get("fact_list_samples", []) if _normalise_space(item)]
    snapshot["body_text_sample"] = _normalise_space(snapshot.get("body_text_sample"))
    return snapshot


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
                normalised.append(str(tuple(_normalise_space(part) for part in match if _normalise_space(part))))
            else:
                normalised.append(_normalise_space(str(match)))
        filtered = [item for item in normalised if item]
        if filtered:
            results[name] = {
                "count": len(filtered),
                "samples": list(dict.fromkeys(filtered))[:8],
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
            parsed = urlparse(url)
            if parsed.netloc.lower() not in ("www.rightmove.co.uk", "rightmove.co.uk"):
                continue
            if "/api/" not in parsed.path:
                continue
            seen_urls.add(url)
            endpoints.append({
                "status": response.get("status"),
                "mime": response.get("mimeType"),
                "url": url,
            })
        except Exception:
            continue
    return endpoints


def scan_listing(driver, url, wait_seconds, seen_api_urls):
    driver.get(url)
    if not wait_for_page(driver):
        raise RuntimeError(f"Page did not finish loading: {url}")
    time.sleep(wait_seconds)

    return {
        "url": driver.current_url,
        "listing_id": _extract_property_id(driver.current_url),
        "title": driver.title,
        "dom": {
            "price": probe_text_selectors(driver, PRICE_SELECTORS),
            "address": probe_text_selectors(driver, ADDRESS_SELECTORS),
            "description": probe_text_selectors(driver, DESCRIPTION_SELECTORS),
            "facts": probe_text_selectors(driver, FACTS_SELECTORS),
            "floorplans": probe_floorplan_selectors(driver),
            "snapshot": scrape_dom_snapshot(driver),
        },
        "source_patterns": scan_page_source(driver),
        "api_endpoints": capture_network_logs(driver, seen_api_urls),
    }


def build_report(scans, urls_tested):
    report = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "urls_tested": urls_tested,
            "pages_scanned": len(scans),
        }
    }

    selector_summary = {}
    for field in ("price", "address", "description", "facts", "floorplans"):
        hits = Counter()
        sample_map = defaultdict(list)
        for scan in scans:
            for hit in scan["dom"].get(field, []):
                hits[hit["selector"]] += 1
                sample_map[hit["selector"]].extend(hit["samples"])
        selector_summary[field] = [
            {
                "selector": selector,
                "seen_in_pages": hits[selector],
                "samples": list(dict.fromkeys(sample_map[selector]))[:4],
            }
            for selector in hits
        ]
        selector_summary[field].sort(key=lambda item: (-item["seen_in_pages"], item["selector"]))
    report["selector_summary"] = selector_summary

    source_summary = {}
    for scan in scans:
        for name, data in scan["source_patterns"].items():
            entry = source_summary.setdefault(name, {"count": 0, "samples": []})
            entry["count"] += data["count"]
            entry["samples"].extend(data["samples"])
    for name, data in source_summary.items():
        source_summary[name] = {
            "total_matches": data["count"],
            "unique_samples": list(dict.fromkeys(data["samples"]))[:8],
        }
    report["source_summary"] = source_summary

    image_counts = [len(scan["dom"]["snapshot"]["property_photo_urls"]) for scan in scans]
    floorplan_counts = [len(scan["dom"]["snapshot"]["floorplan_urls"]) for scan in scans]
    report["media_summary"] = {
        "property_photo_counts": image_counts,
        "floorplan_counts": floorplan_counts,
        "max_property_photos_seen": max(image_counts) if image_counts else 0,
        "max_floorplans_seen": max(floorplan_counts) if floorplan_counts else 0,
    }

    api_urls = []
    for scan in scans:
        api_urls.extend(scan["api_endpoints"])
    deduped_apis = []
    seen = set()
    for api in api_urls:
        if api["url"] in seen:
            continue
        seen.add(api["url"])
        deduped_apis.append(api)
    report["api_endpoints"] = deduped_apis

    report["pages"] = scans
    report["recipe"] = build_recipe(report)
    return report


def build_recipe(report):
    lines = []
    lines.append("DETAIL PAGE RECON SUMMARY")
    lines.append("=" * 50)

    for field, label in (
        ("price", "PRICE"),
        ("address", "ADDRESS"),
        ("description", "DESCRIPTION"),
        ("facts", "FACTS"),
        ("floorplans", "FLOORPLANS"),
    ):
        selectors = report["selector_summary"].get(field, [])
        if selectors:
            best = selectors[0]
            lines.append(f"\n{label}:")
            lines.append(f"  Best selector: {best['selector']}")
            lines.append(f"  Seen in pages: {best['seen_in_pages']}")
            lines.append(f"  Samples: {best['samples']}")

    media = report.get("media_summary", {})
    lines.append("\nMEDIA:")
    lines.append(f"  Max property photos seen on a page: {media.get('max_property_photos_seen', 0)}")
    lines.append(f"  Max floorplans seen on a page: {media.get('max_floorplans_seen', 0)}")

    if report.get("api_endpoints"):
        lines.append(f"\nFIRST-PARTY APIs ({len(report['api_endpoints'])}):")
        for endpoint in report["api_endpoints"][:8]:
            lines.append(f"  • [{endpoint['status']}] {endpoint['url']}")

    source_summary = report.get("source_summary", {})
    if source_summary:
        lines.append("\nSOURCE PATTERNS:")
        for key in ("price_amount", "display_address", "property_type", "lat_lng", "property_photo_urls", "property_floorplan_urls"):
            if key in source_summary:
                lines.append(f"  • {key}: {source_summary[key]['unique_samples'][:3]}")

    return "\n".join(lines)


def save_report(report, output_dir):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    json_path = output_path / f"rightmove_detail_report_{timestamp}.json"
    txt_path = output_path / f"rightmove_detail_report_{timestamp}.txt"

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)

    with txt_path.open("w", encoding="utf-8") as handle:
        handle.write(report["recipe"])

    return json_path, txt_path


def main():
    args = parse_args()
    urls = load_listing_urls(args)
    driver = setup_browser()

    try:
        driver.get(urls[0])
        prompt_manual_ready()

        scans = []
        seen_api_urls = set()

        for url in urls[:args.limit]:
            print(f"\nScanning detail page: {url}")
            scan = scan_listing(driver, url, args.wait_seconds, seen_api_urls)
            scans.append(scan)
            dom_snapshot = scan["dom"]["snapshot"]
            print(
                f"  photos={len(dom_snapshot['property_photo_urls'])} "
                f"floorplans={len(dom_snapshot['floorplan_urls'])} "
                f"apis={len(scan['api_endpoints'])}"
            )

        report = build_report(scans, urls[:args.limit])
        json_path, txt_path = save_report(report, args.output_dir)

        print("\nSaved detail recon:")
        print(f"  JSON: {json_path}")
        print(f"  TXT:  {txt_path}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
