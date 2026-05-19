"""
Rightmove Rental API Probe
==========================

Focused probe for the rental search API request shape.

It captures:
  - browser-initiated fetch/XHR requests on rental search pages
  - matching first-party Rightmove API responses from performance logs
  - exact request URLs, relevant headers, and statuses
  - response body shape when Chrome exposes it

This is intentionally narrower than the explorers. Its job is to tell us whether
the rental results page makes a usable listing-search API request and, if so,
what the successful request actually looks like.

Examples:
  python3 Scraper/recon/rightmove_rental_api_probe.py \
    --search-url "https://www.rightmove.co.uk/property-to-rent/find.html?searchLocation=White+City%2C+West+London&useLocationIdentifier=true&locationIdentifier=REGION%5E85399&radius=0.0&_includeLetAgreed=on" \
    --pages 3

  python3 Scraper/recon/rightmove_rental_api_probe.py --pages 2
"""

import argparse
import base64
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import undetected_chromedriver as uc


SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
API_PATH = "/api/property-search/listing/search"
CARD_SELECTORS = [
    "div[data-testid^='propertyCard-']",
    "div[data-testid^='propertyCard-vrt-']",
    "div[class*='PropertyCard_propertyCardContainer__']",
    "div[class*='propertyCard']",
]
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
    parser = argparse.ArgumentParser(description="Probe the Rightmove rental listing-search API request shape.")
    parser.add_argument(
        "--search-url",
        help="Rightmove rental search URL. If omitted, the browser opens the Rightmove rent homepage for manual navigation.",
    )
    parser.add_argument("--pages", type=int, default=3, help="How many result pages to probe.")
    parser.add_argument("--page-size", type=int, default=24, help="Pagination step size. Recon currently suggests 24.")
    parser.add_argument("--wait-seconds", type=float, default=2.0, help="Delay after each page load before capturing logs.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Directory for saved reports.")
    return parser.parse_args()


def setup_browser():
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    driver = uc.Chrome(options=options, version_main=None)
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
(() => {
  window.__rmApiProbe = { requests: [] };
  const push = (entry) => {
    try {
      window.__rmApiProbe.requests.push({ ...entry, ts: Date.now() });
    } catch (error) {}
  };

  const originalFetch = window.fetch;
  window.fetch = function(input, init) {
    const url = typeof input === 'string' ? input : ((input && input.url) || null);
    const method = (init && init.method) || (input && input.method) || 'GET';
    push({ kind: 'fetch', url, method });
    return originalFetch.apply(this, arguments);
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function(method, url) {
    this.__rmApiProbe = { method, url };
    return originalOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function(body) {
    if (this.__rmApiProbe) {
      push({ kind: 'xhr', url: this.__rmApiProbe.url, method: this.__rmApiProbe.method });
    }
    return originalSend.apply(this, arguments);
  };
})();
"""
        },
    )
    return driver


def prompt_manual_ready():
    input(
        "\nOpen a Rightmove rental search, handle cookies / CAPTCHA if needed, "
        "then press Enter here to start the rental API probe..."
    )


def _normalise_space(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def _build_page_url(base_url, page_index, keep_zero_index):
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if page_index == 0 and not keep_zero_index:
        query.pop("index", None)
    else:
        query["index"] = [str(page_index)]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def wait_for_cards(driver, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for selector in CARD_SELECTORS:
                count = driver.execute_script("return document.querySelectorAll(arguments[0]).length;", selector)
                if count and count > 0:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


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


def _clear_performance_logs(driver):
    try:
        driver.get_log("performance")
    except Exception:
        pass


def _read_js_requests(driver):
    try:
        requests = driver.execute_script("return (window.__rmApiProbe && window.__rmApiProbe.requests) || [];")
    except Exception:
        requests = []
    clean = []
    for item in requests:
        url = item.get("url")
        if not url:
            continue
        clean.append(
            {
                "kind": item.get("kind"),
                "url": url,
                "method": item.get("method"),
                "classification": _classify_endpoint(url),
            }
        )
    return clean


def _header_subset(headers):
    if not headers:
        return {}
    keep = {
        "accept",
        "content-type",
        "referer",
        "origin",
        "x-requested-with",
        "x-client",
        "x-csrf-token",
        "sec-fetch-mode",
        "sec-fetch-site",
        "sec-fetch-dest",
    }
    subset = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in keep:
            subset[lower] = value
    if "cookie" in {key.lower() for key in headers}:
        subset["has_cookie_header"] = True
    return subset


def _decode_body(body_result):
    body = body_result.get("body", "")
    if body_result.get("base64Encoded"):
        try:
            body = base64.b64decode(body).decode("utf-8", errors="replace")
        except Exception:
            return None
    return body


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
    return candidates


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
    for node in listing_nodes[:20]:
        for finding in _scan_rental_paths(node):
            counter[finding["path"]] += 1
            if len(samples[finding["path"]]) < 4:
                samples[finding["path"]].append(finding["value"])
    summary = []
    for path, count in counter.most_common():
        summary.append(
            {
                "path": path,
                "seen_in_listings": count,
                "samples": list(dict.fromkeys(samples[path]))[:4],
            }
        )
    return summary


def _summarise_response_body(body_text):
    if not body_text:
        return {"body_available": False}
    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError:
        return {
            "body_available": True,
            "json_parse_ok": False,
            "body_sample": body_text[:800],
        }

    listing_nodes = _find_listing_dicts(payload)
    top_level_keys = list(payload.keys()) if isinstance(payload, dict) else []
    return {
        "body_available": True,
        "json_parse_ok": True,
        "top_level_keys": top_level_keys[:20],
        "listing_dict_count": len(listing_nodes),
        "listing_sample_keys": list(listing_nodes[0].keys())[:20] if listing_nodes else [],
        "rental_field_candidates": _summarise_rental_paths(listing_nodes)[:20],
    }


def capture_page_requests(driver):
    request_map = {}
    listing_requests = []
    logs = []
    try:
        logs = driver.get_log("performance")
    except Exception:
        return []

    for entry in logs:
        try:
            msg = json.loads(entry["message"])["message"]
        except Exception:
            continue

        method = msg.get("method")
        params = msg.get("params", {})

        if method == "Network.requestWillBeSent":
            request = params.get("request", {})
            url = request.get("url", "")
            classification = _classify_endpoint(url)
            request_id = params.get("requestId")
            request_map[request_id] = {
                "request_id": request_id,
                "url": url,
                "classification": classification,
                "request_method": request.get("method"),
                "request_headers": _header_subset(request.get("headers", {})),
                "post_data_present": bool(request.get("postData")),
                "initiator_type": (params.get("initiator") or {}).get("type"),
            }

        elif method == "Network.responseReceived":
            response = params.get("response", {})
            url = response.get("url", "")
            classification = _classify_endpoint(url)
            request_id = params.get("requestId")
            current = request_map.setdefault(
                request_id,
                {
                    "request_id": request_id,
                    "url": url,
                    "classification": classification,
                },
            )
            current.update(
                {
                    "url": url,
                    "classification": classification,
                    "status": response.get("status"),
                    "mime": response.get("mimeType"),
                    "response_headers": _header_subset(response.get("headers", {})),
                }
            )
            if classification == "listing_search_api":
                listing_requests.append(current)

    deduped = []
    seen_ids = set()
    for item in listing_requests:
        request_id = item.get("request_id")
        if request_id in seen_ids:
            continue
        seen_ids.add(request_id)
        try:
            body_result = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id})
            body_text = _decode_body(body_result)
        except Exception as exc:
            body_text = None
            item["body_error"] = type(exc).__name__
        item["body_summary"] = _summarise_response_body(body_text)
        deduped.append(item)
    return deduped


def scan_page(driver, page_url, page_index, wait_seconds):
    _clear_performance_logs(driver)
    driver.get(page_url)
    if not wait_for_cards(driver):
        raise RuntimeError(f"Rental cards did not appear: {page_url}")
    time.sleep(wait_seconds)

    js_requests = _read_js_requests(driver)
    listing_requests = capture_page_requests(driver)
    first_party_requests = [
        request
        for request in listing_requests
        if request.get("classification") in {"listing_search_api", "property_search_api", "first_party_api"}
    ]

    return {
        "page_index": page_index,
        "requested_page_url": page_url,
        "resolved_page_url": driver.current_url,
        "js_requests": js_requests,
        "listing_search_requests": listing_requests,
        "first_party_requests_seen": first_party_requests,
    }


def _page_vs_api_param_diff(page_url, api_url):
    page_params = parse_qs(urlparse(page_url).query, keep_blank_values=True)
    api_params = parse_qs(urlparse(api_url).query, keep_blank_values=True)
    only_in_api = {}
    changed = {}
    for key, values in api_params.items():
        if key not in page_params:
            only_in_api[key] = values
        elif page_params[key] != values:
            changed[key] = {"page": page_params[key], "api": values}
    return {"only_in_api": only_in_api, "changed_vs_page": changed}


def build_report(scans, start_url, pages_requested):
    observed = []
    for scan in scans:
        for request in scan.get("listing_search_requests", []):
            enriched = dict(request)
            enriched["page_index"] = scan["page_index"]
            enriched["page_url"] = scan["resolved_page_url"]
            enriched["param_diff_vs_page"] = _page_vs_api_param_diff(scan["resolved_page_url"], request["url"])
            observed.append(enriched)

    statuses = Counter(request.get("status") for request in observed)
    request_urls = list(dict.fromkeys(request["url"] for request in observed if request.get("url")))

    request_header_summary = defaultdict(list)
    for request in observed:
        for key, value in request.get("request_headers", {}).items():
            if value not in request_header_summary[key]:
                request_header_summary[key].append(value)

    response_body_summaries = [request.get("body_summary", {}) for request in observed if request.get("body_summary", {}).get("body_available")]
    response_top_level_keys = Counter()
    response_listing_key_samples = []
    rental_field_candidates = defaultdict(list)

    for summary in response_body_summaries:
        for key in summary.get("top_level_keys", []):
            response_top_level_keys[key] += 1
        if summary.get("listing_sample_keys"):
            response_listing_key_samples.append(summary["listing_sample_keys"])
        for item in summary.get("rental_field_candidates", []):
            if len(rental_field_candidates[item["path"]]) < 4:
                rental_field_candidates[item["path"]].extend(item["samples"])

    report = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "start_url": start_url,
            "pages_requested": pages_requested,
            "pages_scanned": len(scans),
        },
        "observed_listing_search_requests": observed,
        "analysis": {
            "request_count": len(observed),
            "statuses_seen": dict(statuses),
            "successful_request_count": sum(1 for request in observed if request.get("status") == 200),
            "request_urls": request_urls,
            "request_header_summary": dict(request_header_summary),
            "response_top_level_keys": dict(response_top_level_keys),
            "response_listing_key_samples": response_listing_key_samples[:5],
            "response_rental_field_candidates": [
                {
                    "path": path,
                    "samples": list(dict.fromkeys(samples))[:4],
                }
                for path, samples in rental_field_candidates.items()
            ],
        },
        "pages": scans,
    }
    report["recipe"] = build_recipe(report)
    return report


def build_recipe(report):
    lines = []
    lines.append("RIGHTMOVE RENTAL API PROBE")
    lines.append("=" * 50)

    analysis = report.get("analysis", {})
    lines.append(f"\nObserved listing-search requests: {analysis.get('request_count', 0)}")
    lines.append(f"Successful (200) listing-search requests: {analysis.get('successful_request_count', 0)}")
    lines.append(f"Statuses seen: {analysis.get('statuses_seen', {})}")

    urls = analysis.get("request_urls", [])
    if urls:
        lines.append("\nREQUEST URLS:")
        for url in urls[:6]:
            lines.append(f"  • {url}")

    requests = report.get("observed_listing_search_requests", [])
    if requests:
        first = requests[0]
        lines.append("\nFIRST REQUEST:")
        lines.append(f"  Page URL: {first.get('page_url')}")
        lines.append(f"  API URL:  {first.get('url')}")
        lines.append(f"  Status:   {first.get('status')}")
        if first.get("param_diff_vs_page", {}).get("only_in_api"):
            lines.append(f"  Params only in API: {first['param_diff_vs_page']['only_in_api']}")
        if first.get("param_diff_vs_page", {}).get("changed_vs_page"):
            lines.append(f"  Params changed vs page: {first['param_diff_vs_page']['changed_vs_page']}")
        if first.get("request_headers"):
            lines.append(f"  Header hints: {first['request_headers']}")

    top_level = analysis.get("response_top_level_keys", {})
    if top_level:
        lines.append("\nRESPONSE TOP-LEVEL KEYS:")
        for key, count in list(top_level.items())[:15]:
            lines.append(f"  • {key}: seen in {count} responses")

    rental_fields = analysis.get("response_rental_field_candidates", [])
    if rental_fields:
        lines.append("\nRESPONSE RENTAL FIELD CANDIDATES:")
        for item in rental_fields[:12]:
            lines.append(f"  • {item['path']}: {item['samples']}")

    page_js = [scan for scan in report.get("pages", []) if scan.get("js_requests")]
    lines.append("\nBROWSER-INITIATED REQUESTS:")
    lines.append(f"  Pages with JS fetch/XHR activity captured: {len(page_js)} / {len(report.get('pages', []))}")
    if not analysis.get("successful_request_count", 0):
        lines.append("  No successful rental listing-search API request was observed from page navigation alone.")
        lines.append("  If this persists, the rental search may be server-rendered or require a different trigger.")

    return "\n".join(lines)


def save_report(report, output_dir):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    json_path = output_path / f"rightmove_rental_api_probe_{timestamp}.json"
    txt_path = output_path / f"rightmove_rental_api_probe_{timestamp}.txt"

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
        for page_offset in range(args.pages):
            page_index = start_index + (page_offset * args.page_size)
            page_url = _build_page_url(start_url, page_index, keep_zero_index)
            print(f"\nProbing rental API on page index {page_index}")
            scan = scan_page(driver, page_url, page_index, args.wait_seconds)
            scans.append(scan)
            print(
                f"  page={scan['resolved_page_url']}\n"
                f"  js_requests={len(scan['js_requests'])} "
                f"listing_search_requests={len(scan['listing_search_requests'])}"
            )
            for request in scan["listing_search_requests"][:3]:
                print(f"    [{request.get('status')}] {request.get('url')}")

        report = build_report(scans, start_url, args.pages)
        json_path, txt_path = save_report(report, args.output_dir)

        print("\nSaved rental API probe:")
        print(f"  JSON: {json_path}")
        print(f"  TXT:  {txt_path}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
