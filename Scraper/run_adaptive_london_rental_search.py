"""
Adaptive London Rental Search Collector
=======================================

Works around Rightmove's broad-search pagination cap by:
  1. scraping known borough seed URLs from a committed config
  2. identifying borough searches that still hit Rightmove's page cap
  3. subdividing capped boroughs into precomputed postcode outcode searches
  4. merging and deduplicating the resulting search-stage listings

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
from urllib.parse import urlencode


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "config" / "rightmove_london_adaptive_search.json"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rightmove_location_resolver import save_resolution_report  # noqa: E402
from rightmove_rental_search_scraper import merge_listing_data, save_outputs  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description="Adaptively collect London rental search results across smaller areas.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for merged outputs.")
    parser.add_argument("--run-dir", help="Directory for intermediate adaptive collector files.")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="JSON config containing borough seed URLs and precomputed outcode child searches.",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=0,
        help="Search pages per area. Use 0 to let each area search auto-stop naturally.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=0,
        help="Maximum listings per area search. Use 0 for no cap.",
    )
    parser.add_argument("--pagination-cap", type=int, default=42, help="Page count that indicates a capped Rightmove search.")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=False, help="Run search browsers headlessly.")
    parser.add_argument("--interactive", action=argparse.BooleanOptionalAction, default=False, help="Allow manual cookie/CAPTCHA handling during searches.")
    parser.add_argument("--user-data-dir", help="Optional shared Chrome user-data directory.")
    parser.add_argument("--seed-borough", action="append", help="Optional borough(s) to limit collection to.")
    parser.add_argument(
        "--subdivide-capped-boroughs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When a borough seed hits the pagination cap, try postcode outcode child searches.",
    )
    parser.add_argument(
        "--max-outcodes-per-borough",
        type=int,
        help="Optional limit on how many outcodes to use for each capped borough, starting with the most common.",
    )
    return parser.parse_args()


def _resolve_path(path_arg):
    path = Path(path_arg)
    if not path.is_absolute():
        path = (SCRIPT_DIR.parent / path).resolve()
    return path


def _default_run_dir(output_dir):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(output_dir) / f"adaptive_london_search_run_{timestamp}"


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
    boroughs = payload.get("boroughs")
    if not isinstance(boroughs, list) or not boroughs:
        raise RuntimeError(f"No borough config entries found in {path}")
    return payload


def _filter_borough_configs(config_payload, seed_boroughs=None):
    allowed = {_normalise_borough_name(value) for value in (seed_boroughs or [])}
    boroughs = []
    for item in config_payload.get("boroughs", []):
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        normalised = _normalise_borough_name(name)
        if allowed and normalised not in allowed:
            continue
        boroughs.append(item)
    return sorted(boroughs, key=lambda item: item["name"])


def _normalise_outcode_identifier(outcode_code):
    code = str(outcode_code or "").strip().upper()
    if code.startswith("5E"):
        code = code[2:]
    return code


def _build_outcode_search_url(outcode, outcode_code):
    location_code = _normalise_outcode_identifier(outcode_code)
    params = {
        "useLocationIdentifier": "true",
        "locationIdentifier": f"OUTCODE^{location_code}",
        "rent": "To rent",
        "_includeLetAgreed": "on",
        "index": "0",
        "sortType": "6",
        "channel": "RENT",
        "transactionType": "LETTING",
        "displayLocationIdentifier": f"{outcode}.html",
    }
    return "https://www.rightmove.co.uk/property-to-rent/find.html?" + urlencode(params)


def _latest_search_json(directory):
    candidates = sorted(
        Path(directory).glob("rightmove_rental_search_results_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _build_empty_search_output(output_dir, *, search_url, pages, max_results, headless, interactive, user_data_dir, stop_reason):
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
    allow_empty_page_zero=False,
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
        if allow_empty_page_zero and "Cards did not appear for page index 0" in combined_output:
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
        "collector": "adaptive_london_rental_search",
        "area_count": len(area_payloads),
        "area_names": [area["query"] for area in area_payloads],
        "results_count": len(merged_results),
        "dedupe_key": "listing_id_or_listing_url",
        "area_summaries": [
            {
                "query": area["query"],
                "depth": area["depth"],
                "source_type": area["source_type"],
                "borough_name": area["borough_name"],
                "search_url": area["search_url"],
                "location_identifier": area["payload"].get("meta", {}).get("resolved_location_identifier"),
                "reported_result_count": area["payload"].get("meta", {}).get("reported_result_count"),
                "reported_pagination_total": area["payload"].get("meta", {}).get("reported_pagination_total"),
                "results_count": area["payload"].get("meta", {}).get("results_count"),
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
    borough_configs = _filter_borough_configs(config_payload, seed_boroughs=args.seed_borough)
    if not borough_configs:
        raise RuntimeError(f"No borough config entries matched the requested borough filter in {config_path}")

    area_records = []

    def collect(query, search_url, depth, borough_name, source_type, area_config):
        area_slug = _slugify(query)
        area_dir = run_dir / "areas" / f"{depth:02d}-{area_slug}"
        area_output_dir = area_dir / "search_output"
        area_output_dir.mkdir(parents=True, exist_ok=True)

        search_json, payload = _run_search(
            search_url,
            area_output_dir,
            pages=args.pages,
            max_results=args.max_results,
            headless=args.headless,
            interactive=args.interactive,
            user_data_dir=args.user_data_dir,
            allow_empty_page_zero=(depth > 0 and source_type == "borough_outcode"),
        )

        meta = payload.get("meta", {})
        page_total = int(meta.get("reported_pagination_total") or 0)
        area_record = {
            "query": query,
            "depth": depth,
            "borough_name": borough_name,
            "source_type": source_type,
            "search_url": search_url,
            "search_json": str(search_json),
            "payload": payload,
        }

        if (
            depth == 0
            and args.subdivide_capped_boroughs
            and page_total >= args.pagination_cap
            and area_config.get("outcodes")
        ):
            child_records = []
            outcodes = list(area_config.get("outcodes", []))
            if args.max_outcodes_per_borough:
                outcodes = outcodes[: args.max_outcodes_per_borough]
            for outcode_entry in outcodes:
                outcode = str(outcode_entry.get("outcode") or "").strip().upper()
                outcode_code = outcode_entry.get("location_code") or outcode_entry.get("code")
                if not outcode or not outcode_code:
                    continue
                child_query = f"{outcode} [{borough_name}]"
                child_records.extend(
                    collect(
                        child_query,
                        _build_outcode_search_url(outcode, outcode_code),
                        depth + 1,
                        borough_name,
                        "borough_outcode",
                        area_config,
                    )
                )
            if child_records:
                area_record["subdivided"] = True
                area_records.append(area_record)
                return child_records

        area_records.append(area_record)
        return [area_record]

    leaf_area_payloads = []
    for borough_config in borough_configs:
        borough_name = borough_config["name"]
        seed_url = borough_config.get("seed_search_url")
        if not seed_url:
            print(f"Skipping {borough_name}: no seed_search_url in {config_path.name}")
            continue
        leaf_area_payloads.extend(
            collect(
                borough_name,
                seed_url,
                0,
                borough_name,
                "borough_seed_link",
                borough_config,
            )
        )

    merged_json, merged_csv, _ = _merge_search_payloads(leaf_area_payloads, output_dir)

    report = {
        "generated_at": datetime.now().isoformat(),
        "run_dir": str(run_dir),
        "config_path": str(config_path),
        "config_generated_at": config_payload.get("generated_at"),
        "borough_seed_count": len(borough_configs),
        "leaf_area_count": len(leaf_area_payloads),
        "config_sources": config_payload.get("sources", {}),
        "areas": [
            {
                "query": item["query"],
                "depth": item["depth"],
                "source_type": item["source_type"],
                "borough_name": item["borough_name"],
                "subdivided": bool(item.get("subdivided")),
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
        "leaf_areas": [item["query"] for item in leaf_area_payloads],
        "borough_outcode_counts": {
            item["name"]: len(item.get("outcodes", []))
            for item in borough_configs
        },
        "merged_search_json": str(merged_json),
        "merged_search_csv": str(merged_csv),
    }
    save_resolution_report(run_dir / "adaptive_london_search_report.json", report)

    print("\nSaved adaptive London search outputs:")
    print(f"  Merged JSON: {merged_json}")
    print(f"  Merged CSV:  {merged_csv}")
    print(f"  Report:      {run_dir / 'adaptive_london_search_report.json'}")
    print(f"  Leaf areas:  {len(leaf_area_payloads)}")


if __name__ == "__main__":
    main()
