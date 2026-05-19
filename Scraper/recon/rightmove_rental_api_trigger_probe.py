"""
Rightmove Rental API Trigger Probe
=================================

Final recon probe for rental search pages.

This extends the passive API probe by actively testing likely triggers that may
cause hidden first-party API requests:
  - next-page navigation
  - sort changes
  - optional manual user interactions

The goal is to confirm whether Rightmove rental search exposes a usable
listing-search API outside of plain page navigation.

Examples:
  python3 Scraper/recon/rightmove_rental_api_trigger_probe.py \
    --search-url "https://www.rightmove.co.uk/property-to-rent/find.html?searchLocation=White+City%2C+West+London&useLocationIdentifier=true&locationIdentifier=REGION%5E85399&radius=0.0&_includeLetAgreed=on"

  python3 Scraper/recon/rightmove_rental_api_trigger_probe.py \
    --search-url "https://www.rightmove.co.uk/property-to-rent/find.html?searchLocation=White+City%2C+West+London&useLocationIdentifier=true&locationIdentifier=REGION%5E85399&radius=0.0&_includeLetAgreed=on" \
    --manual-capture-seconds 20
"""

import argparse
import base64
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
    parser = argparse.ArgumentParser(description="Actively probe hidden Rightmove rental API triggers.")
    parser.add_argument(
        "--search-url",
        help="Rightmove rental search URL. If omitted, the browser opens the rent homepage for manual navigation.",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=1.5,
        help="Delay after each automatic trigger before capturing logs.",
    )
    parser.add_argument(
        "--manual-capture-seconds",
        type=float,
        default=15.0,
        help="Optional manual interaction window after automatic triggers. Use 0 to disable.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help="Directory for saved reports.",
    )
    return parser.parse_args()


def setup_browser():
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    options.set_capability("pageLoadStrategy", "eager")
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
        "then press Enter here to start the trigger probe..."
    )


def _normalise_space(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


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


def _reset_js_probe(driver):
    try:
        driver.execute_script("if (window.__rmApiProbe) { window.__rmApiProbe.requests = []; }")
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


def _capture_requests(driver):
    request_map = {}
    first_party_requests = []
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
            if classification is None:
                continue
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
            if classification is None:
                continue
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
            first_party_requests.append(current)

    deduped = []
    seen_ids = set()
    for item in first_party_requests:
        request_id = item.get("request_id")
        if request_id in seen_ids:
            continue
        seen_ids.add(request_id)
        if item.get("classification") == "listing_search_api":
            try:
                body_result = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id})
                body_text = _decode_body(body_result)
            except Exception as exc:
                body_text = None
                item["body_error"] = type(exc).__name__
            item["body_summary"] = _summarise_response_body(body_text)
        deduped.append(item)
    return deduped


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


def _collect_action_result(driver, name, started_url):
    js_requests = _read_js_requests(driver)
    network_requests = _capture_requests(driver)
    listing_requests = [item for item in network_requests if item.get("classification") == "listing_search_api"]
    return {
        "action": name,
        "started_url": started_url,
        "ended_url": driver.current_url,
        "url_changed": driver.current_url != started_url,
        "js_requests": js_requests,
        "first_party_requests_seen": network_requests,
        "listing_search_requests": listing_requests,
    }


def _run_action(driver, name, action_fn, wait_seconds):
    started_url = driver.current_url
    _clear_performance_logs(driver)
    _reset_js_probe(driver)
    error = None
    action_detail = None
    try:
        action_detail = action_fn(driver)
        time.sleep(wait_seconds)
        wait_for_cards(driver, timeout=8)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    result = _collect_action_result(driver, name, started_url)
    result["action_detail"] = action_detail
    if error:
        result["error"] = error
    return result


def _find_next_link(driver):
    script = """
const current = new URL(window.location.href);
const currentIndex = Number(current.searchParams.get('index') || '0');
const anchors = Array.from(document.querySelectorAll("a[href]"));
const scored = anchors.map((anchor) => {
  const href = anchor.href;
  let index = null;
  try {
    const url = new URL(href, window.location.href);
    index = Number(url.searchParams.get('index') || '0');
  } catch (error) {}
  const text = (anchor.innerText || anchor.textContent || '').trim();
  const rel = (anchor.getAttribute('rel') || '').toLowerCase();
  const aria = (anchor.getAttribute('aria-label') || '').toLowerCase();
  const dataTestId = (anchor.getAttribute('data-testid') || '').toLowerCase();
  let score = 0;
  if (rel.includes('next')) score += 8;
  if (aria.includes('next')) score += 7;
  if (dataTestId.includes('next')) score += 7;
  if (text.toLowerCase() === 'next') score += 7;
  if (text.includes('Next')) score += 6;
  if (index > currentIndex) score += 3;
  if (href.includes('/find.html')) score += 1;
  return { href, text, rel, aria, dataTestId, index, score };
}).filter((item) => item.score > 0 || (item.index !== null && item.index > currentIndex));
scored.sort((a, b) => b.score - a.score || (a.index || 0) - (b.index || 0));
return scored[0] || null;
"""
    return driver.execute_script(script)


def _action_next_page(driver):
    candidate = _find_next_link(driver)
    if not candidate or not candidate.get("href"):
        raise RuntimeError("No viable next-page link found.")
    driver.get(candidate["href"])
    return candidate


def _action_change_sort(driver):
    script = """
const bySelect = () => {
  const selects = Array.from(document.querySelectorAll('select'));
  for (const select of selects) {
    const label = [
      select.name || '',
      select.id || '',
      select.getAttribute('aria-label') || '',
      select.getAttribute('data-testid') || ''
    ].join(' ').toLowerCase();
    if (!label.includes('sort')) continue;
    const current = select.value;
    const options = Array.from(select.options)
      .map((option) => option.value)
      .filter((value) => value && value !== current);
    if (!options.length) continue;
    select.value = options[0];
    select.dispatchEvent(new Event('input', { bubbles: true }));
    select.dispatchEvent(new Event('change', { bubbles: true }));
    return { mode: 'select', value: options[0] };
  }
  return null;
};

const byHref = () => {
  const current = new URL(window.location.href);
  const currentSort = current.searchParams.get('sortType');
  const anchors = Array.from(document.querySelectorAll("a[href]"));
  const candidates = [];
  for (const anchor of anchors) {
    try {
      const url = new URL(anchor.href, window.location.href);
      const sortType = url.searchParams.get('sortType');
      if (!sortType || sortType === currentSort) continue;
      const samePath = url.pathname === current.pathname;
      const text = (anchor.innerText || anchor.textContent || '').trim();
      const aria = (anchor.getAttribute('aria-label') || '').toLowerCase();
      let score = 0;
      if (samePath) score += 3;
      if (text.toLowerCase().includes('sort')) score += 4;
      if (aria.includes('sort')) score += 4;
      candidates.push({ href: url.href, sortType, text, score });
    } catch (error) {}
  }
  candidates.sort((a, b) => b.score - a.score);
  return candidates[0] || null;
};

return bySelect() || byHref();
"""
    detail = driver.execute_script(script)
    if not detail:
        raise RuntimeError("No viable sort control found.")
    if detail.get("mode") == "select":
        return detail
    if detail.get("href"):
        driver.get(detail["href"])
        return detail
    raise RuntimeError("Sort trigger produced no actionable target.")


def _action_manual_window(driver, seconds):
    print(
        "\nManual trigger window started. Interact with the rental search page now "
        f"(filters, sort, pagination, map, etc.). Waiting {seconds:.0f}s..."
    )
    time.sleep(seconds)
    return {"mode": "manual_window", "seconds": seconds}


def _summarise_actions(action_results):
    summary = []
    for result in action_results:
        summary.append(
            {
                "action": result["action"],
                "url_changed": result.get("url_changed", False),
                "listing_search_request_count": len(result.get("listing_search_requests", [])),
                "first_party_request_count": len(result.get("first_party_requests_seen", [])),
                "js_request_count": len(result.get("js_requests", [])),
                "error": result.get("error"),
            }
        )
    return summary


def build_report(start_url, action_results):
    observed = []
    for result in action_results:
        for request in result.get("listing_search_requests", []):
            enriched = dict(request)
            enriched["action"] = result["action"]
            enriched["started_url"] = result["started_url"]
            enriched["ended_url"] = result["ended_url"]
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
        },
        "action_results": action_results,
        "analysis": {
            "actions": _summarise_actions(action_results),
            "request_count": len(observed),
            "successful_request_count": sum(1 for request in observed if request.get("status") == 200),
            "statuses_seen": dict(statuses),
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
    }
    report["recipe"] = build_recipe(report)
    return report


def build_recipe(report):
    lines = []
    lines.append("RIGHTMOVE RENTAL API TRIGGER PROBE")
    lines.append("=" * 50)

    analysis = report.get("analysis", {})
    lines.append(f"\nObserved listing-search requests: {analysis.get('request_count', 0)}")
    lines.append(f"Successful (200) listing-search requests: {analysis.get('successful_request_count', 0)}")
    lines.append(f"Statuses seen: {analysis.get('statuses_seen', {})}")

    lines.append("\nACTION SUMMARY:")
    for item in analysis.get("actions", []):
        line = (
            f"  • {item['action']}: listing_search={item['listing_search_request_count']} "
            f"first_party={item['first_party_request_count']} js={item['js_request_count']}"
        )
        if item.get("url_changed"):
            line += " url_changed=True"
        if item.get("error"):
            line += f" error={item['error']}"
        lines.append(line)

    urls = analysis.get("request_urls", [])
    if urls:
        lines.append("\nREQUEST URLS:")
        for url in urls[:6]:
            lines.append(f"  • {url}")

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

    if not analysis.get("successful_request_count", 0):
        lines.append("\nCONCLUSION:")
        lines.append("  No successful rental listing-search API request was observed even after active triggers.")
        lines.append("  Rentals should currently be treated as DOM/source-first unless later probing finds a trigger.")

    return "\n".join(lines)


def save_report(report, output_dir):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    json_path = output_path / f"rightmove_rental_api_trigger_probe_{timestamp}.json"
    txt_path = output_path / f"rightmove_rental_api_trigger_probe_{timestamp}.txt"

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
        if not wait_for_cards(driver):
            raise RuntimeError(f"Rental cards did not appear: {driver.current_url}")

        start_url = driver.current_url
        action_results = []

        print("\nRunning baseline capture...")
        action_results.append(_run_action(driver, "baseline_idle", lambda _driver: {"mode": "idle"}, args.wait_seconds))
        print(
            f"  listing_search={len(action_results[-1]['listing_search_requests'])} "
            f"first_party={len(action_results[-1]['first_party_requests_seen'])}"
        )

        print("\nTrying next-page trigger...")
        action_results.append(_run_action(driver, "next_page", _action_next_page, args.wait_seconds))
        print(
            f"  listing_search={len(action_results[-1]['listing_search_requests'])} "
            f"first_party={len(action_results[-1]['first_party_requests_seen'])}"
        )
        driver.get(start_url)
        wait_for_cards(driver)

        print("\nTrying sort trigger...")
        action_results.append(_run_action(driver, "sort_change", _action_change_sort, args.wait_seconds))
        print(
            f"  listing_search={len(action_results[-1]['listing_search_requests'])} "
            f"first_party={len(action_results[-1]['first_party_requests_seen'])}"
        )
        driver.get(start_url)
        wait_for_cards(driver)

        if args.manual_capture_seconds > 0:
            print("\nStarting manual trigger capture...")
            action_results.append(
                _run_action(
                    driver,
                    "manual_interaction_window",
                    lambda current_driver: _action_manual_window(current_driver, args.manual_capture_seconds),
                    0.0,
                )
            )
            print(
                f"  listing_search={len(action_results[-1]['listing_search_requests'])} "
                f"first_party={len(action_results[-1]['first_party_requests_seen'])}"
            )

        report = build_report(start_url, action_results)
        json_path, txt_path = save_report(report, args.output_dir)

        print("\nSaved rental API trigger probe:")
        print(f"  JSON: {json_path}")
        print(f"  TXT:  {txt_path}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
