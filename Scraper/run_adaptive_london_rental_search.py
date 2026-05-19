"""
Postcode-first London Rental Search Collector
=============================================

Collects Rightmove London rental search results by postcode outcode, merges the
results, and deduplicates listings across overlapping search areas.

This produces a merged rental search dataset that can then be passed into the
existing detail scraper and downstream London clipping pipeline.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "config" / "rightmove_london_adaptive_search.json"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rightmove_location_resolver import save_resolution_report  # noqa: E402
from rightmove_rental_search_scraper import merge_listing_data, save_outputs  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description="Collect London rental search results across postcode outcodes.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for merged outputs.")
    parser.add_argument("--run-dir", help="Directory for intermediate search collector files.")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="JSON config containing postcode-first Rightmove outcode search units.",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=0,
        help="Search pages per outcode. Use 0 to let each outcode search auto-stop naturally.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=0,
        help="Maximum listings per outcode search. Use 0 for no cap.",
    )
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=False, help="Run search browsers headlessly.")
    parser.add_argument("--interactive", action=argparse.BooleanOptionalAction, default=False, help="Allow manual cookie/CAPTCHA handling during searches.")
    parser.add_argument("--user-data-dir", help="Optional shared Chrome user-data directory.")
    parser.add_argument("--seed-borough", action="append", help="Optional borough(s) to limit collection to.")
    parser.add_argument("--seed-outcode", action="append", help="Optional outcode(s) to limit collection to.")
    parser.add_argument(
        "--continue-on-search-error",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep going when an individual outcode search fails after logging the error.",
    )
    return parser.parse_args()


def _resolve_path(path_arg):
    path = Path(path_arg)
    if not path.is_absolute():
        path = (SCRIPT_DIR.parent / path).resolve()
    return path


def _default_run_dir(output_dir):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(output_dir) / f"postcode_london_search_run_{timestamp}"


def _slugify(value):
    text = str(value or "").strip().lower()
    text = "".join(ch if ch.isalnum() else "-" for ch in text)
    return "-".join(part for part in text.split("-") if part) or "location"


def _normalise_borough_name(name):
    text = str(name or "").strip().lower()
    text = text.replace("&", "and")
    text = " ".join(text.split())
    aliases = {
        "kensington chelsea": "kensington and chelsea",
        "richmond upon thames": "richmond upon thames",
        "kingston upon thames": "kingston upon thames",
        "city of london": "city of london",
    }
    return aliases.get(text, text)


def _load_config(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    search_units = payload.get("search_units")
    if not isinstance(search_units, list) or not search_units:
        raise RuntimeError(f"No search_units found in {path}")
    return payload


def _filter_search_units(config_payload, seed_boroughs=None, seed_outcodes=None):
    allowed_boroughs = {_normalise_borough_name(value) for value in (seed_boroughs or [])}
    allowed_outcodes = {str(value).strip().upper() for value in (seed_outcodes or []) if str(value).strip()}
    search_units = []
    for item in config_payload.get("search_units", []):
        borough_name = str(item.get("borough") or "").strip()
        outcode = str(item.get("outcode") or "").strip().upper()
        normalised_borough = _normalise_borough_name(borough_name)
        if allowed_boroughs and normalised_borough not in allowed_boroughs:
            continue
        if allowed_outcodes and outcode not in allowed_outcodes:
            continue
        if not borough_name or not outcode or not item.get("search_url"):
            continue
        search_units.append(item)
    return sorted(search_units, key=lambda item: (item["normalised_borough"], item["outcode"]))


def _latest_search_json(directory):
    candidates = sorted(
        Path(directory).glob("rightmove_rental_search_results_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _build_empty_search_output(output_dir, *, search_url, pages, max_results, headless, interactive, user_data_dir, stop_reason, search_unit):
    metadata = {
        "generated_at": datetime.now().isoformat(),
        "market": "rent",
        "start_url": search_url,
        "pages_requested": pages,
        "page_size": 24,
        "pages_scraped": 0,
        "max_results": max_results,
        "results_count": 0,
        "stop_reason": stop_reason,
        "last_page_index": 0,
        "interactive": bool(interactive),
        "headless": bool(headless),
        "user_data_dir": user_data_dir,
        "synthetic_empty_result": True,
        "outcode": search_unit.get("outcode"),
        "borough_hint": search_unit.get("borough"),
        "location_code": search_unit.get("location_code"),
    }
    json_path, _, _ = save_outputs(output_dir, [], [], metadata)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    return json_path, payload


def _run_search(
    search_url,
    output_dir,
    *,
    pages,
    max_results,
    headless,
    interactive,
    user_data_dir,
    search_unit,
):
    command = [
        sys.executable,
        str(SCRIPT_DIR / "rightmove_rental_search_scraper.py"),
        "--search-url",
        search_url,
        "--pages",
        str(pages),
        "--page-size",
        "24",
        "--max-results",
        str(max_results),
        "--output-dir",
        str(output_dir),
    ]
    if not interactive:
        command.append("--no-interactive")
    if headless:
        command.append("--headless")
    if user_data_dir:
        command.extend(["--user-data-dir", user_data_dir])

    print(f"\nRunning: {' '.join(command)}")
    completed = subprocess.run(
        command,
        check=False,
        cwd=str(SCRIPT_DIR.parent),
        capture_output=True,
        text=True,
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr)
    if completed.returncode != 0:
        combined_output = f"{completed.stdout}\n{completed.stderr}"
        if "Cards did not appear for page index 0" in combined_output:
            print(f"Treating page-0 no-card search as an empty result set: {search_url}")
            return _build_empty_search_output(
                output_dir,
                search_url=search_url,
                pages=pages,
                max_results=max_results,
                headless=headless,
                interactive=interactive,
                user_data_dir=user_data_dir,
                stop_reason="empty_page_0_no_cards",
                search_unit=search_unit,
            )
        raise subprocess.CalledProcessError(
            completed.returncode,
            command,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    search_json = _latest_search_json(output_dir)
    if not search_json:
        raise FileNotFoundError(f"No search JSON produced in {output_dir}")
    payload = json.loads(search_json.read_text(encoding="utf-8"))
    return search_json, payload


def _merge_search_payloads(area_payloads, output_dir):
    merged = {}
    for area in area_payloads:
        for item in area["payload"].get("results", []):
            key = item.get("listing_id") or item.get("listing_url")
            if not key:
                continue
            merged[key] = merge_listing_data(merged.get(key, {}), item)

    merged_results = sorted(
        merged.values(),
        key=lambda item: (
            item.get("source_page_index", 0),
            item.get("position_on_page") or 999,
            str(item.get("listing_id") or ""),
        ),
    )
    metadata = {
        "generated_at": datetime.now().isoformat(),
        "market": "rent",
        "collector": "postcode_london_rental_search",
        "area_count": len(area_payloads),
        "area_names": [area["query"] for area in area_payloads],
        "results_count": len(merged_results),
        "dedupe_key": "listing_id_or_listing_url",
        "area_summaries": [
            {
                "query": area["query"],
                "source_type": area["source_type"],
                "borough_name": area["borough_name"],
                "outcode": area["outcode"],
                "search_url": area["search_url"],
                "location_identifier": area["payload"].get("meta", {}).get("resolved_location_identifier"),
                "reported_result_count": area["payload"].get("meta", {}).get("reported_result_count"),
                "reported_pagination_total": area["payload"].get("meta", {}).get("reported_pagination_total"),
                "results_count": area["payload"].get("meta", {}).get("results_count"),
                "stop_reason": area["payload"].get("meta", {}).get("stop_reason"),
                "synthetic_empty_result": bool(area["payload"].get("meta", {}).get("synthetic_empty_result")),
            }
            for area in area_payloads
        ],
    }
    return save_outputs(output_dir, merged_results, [], metadata)


def main():
    args = parse_args()
    output_dir = _resolve_path(args.output_dir)
    run_dir = _resolve_path(args.run_dir) if args.run_dir else _default_run_dir(output_dir)
    config_path = _resolve_path(args.config)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    config_payload = _load_config(config_path)
    search_units = _filter_search_units(
        config_payload,
        seed_boroughs=args.seed_borough,
        seed_outcodes=args.seed_outcode,
    )
    if not search_units:
        raise RuntimeError(f"No search units matched the requested filters in {config_path}")

    area_records = []
    failed_units = []
    leaf_area_payloads = []

    for search_unit in search_units:
        outcode = search_unit["outcode"]
        borough_name = search_unit["borough"]
        query = f"{outcode} [{borough_name}]"
        area_slug = _slugify(query)
        area_dir = run_dir / "areas" / f"{area_slug}"
        area_output_dir = area_dir / "search_output"
        area_output_dir.mkdir(parents=True, exist_ok=True)

        try:
            search_json, payload = _run_search(
                search_unit["search_url"],
                area_output_dir,
                pages=args.pages,
                max_results=args.max_results,
                headless=args.headless,
                interactive=args.interactive,
                user_data_dir=args.user_data_dir,
                search_unit=search_unit,
            )
        except Exception as exc:
            failed_unit = {
                "query": query,
                "borough_name": borough_name,
                "outcode": outcode,
                "search_url": search_unit["search_url"],
                "error": str(exc),
            }
            failed_units.append(failed_unit)
            if not args.continue_on_search_error:
                raise
            print(f"Continuing after outcode search failure: {query}\n  {exc}")
            continue

        area_record = {
            "query": query,
            "source_type": "postcode_outcode",
            "borough_name": borough_name,
            "outcode": outcode,
            "search_url": search_unit["search_url"],
            "search_json": str(search_json),
            "payload": payload,
        }
        area_records.append(area_record)
        leaf_area_payloads.append(area_record)

    if not leaf_area_payloads:
        raise RuntimeError("All postcode outcode searches failed; no search-stage results were collected.")

    merged_json, merged_csv, _ = _merge_search_payloads(leaf_area_payloads, output_dir)

    report = {
        "generated_at": datetime.now().isoformat(),
        "search_mode": "postcode_first",
        "run_dir": str(run_dir),
        "config_path": str(config_path),
        "config_generated_at": config_payload.get("generated_at"),
        "search_unit_count": len(search_units),
        "successful_search_unit_count": len(leaf_area_payloads),
        "failed_search_unit_count": len(failed_units),
        "config_sources": config_payload.get("sources", {}),
        "areas": [
            {
                "query": item["query"],
                "source_type": item["source_type"],
                "borough_name": item["borough_name"],
                "outcode": item["outcode"],
                "search_json": item["search_json"],
                "search_url": item["search_url"],
                "location_identifier": item["payload"].get("meta", {}).get("resolved_location_identifier"),
                "reported_result_count": item["payload"].get("meta", {}).get("reported_result_count"),
                "reported_pagination_total": item["payload"].get("meta", {}).get("reported_pagination_total"),
                "results_count": item["payload"].get("meta", {}).get("results_count"),
                "stop_reason": item["payload"].get("meta", {}).get("stop_reason"),
                "synthetic_empty_result": bool(item["payload"].get("meta", {}).get("synthetic_empty_result")),
            }
            for item in area_records
        ],
        "failed_units": failed_units,
        "leaf_areas": [item["query"] for item in leaf_area_payloads],
        "merged_search_json": str(merged_json),
        "merged_search_csv": str(merged_csv),
    }
    save_resolution_report(run_dir / "adaptive_london_search_report.json", report)

    print("\nSaved postcode-first London search outputs:")
    print(f"  Merged JSON: {merged_json}")
    print(f"  Merged CSV:  {merged_csv}")
    print(f"  Report:      {run_dir / 'adaptive_london_search_report.json'}")
    print(f"  Successful search units: {len(leaf_area_payloads)}")
    print(f"  Failed search units:     {len(failed_units)}")


if __name__ == "__main__":
    main()
