"""
Adaptive London Rental Search Collector
=======================================

Works around Rightmove's broad-search pagination cap by:
  1. resolving London borough names to Rightmove location identifiers
  2. scraping each borough search
  3. recursively subdividing capped boroughs into ward-level searches
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

import geopandas as gpd

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rightmove_location_resolver import resolve_location, save_resolution_report, setup_browser, _slugify
from rightmove_rental_search_scraper import merge_listing_data, save_outputs
DEFAULT_BOROUGHS_PATH = (
    SCRIPT_DIR.parent
    / "London Boundary"
    / "statistical-gis-boundaries-london"
    / "ESRI"
    / "London_Borough_Excluding_MHW.shp"
)
DEFAULT_WARDS_PATH = (
    SCRIPT_DIR.parent
    / "London Boundary"
    / "statistical-gis-boundaries-london"
    / "ESRI"
    / "London_Ward_CityMerged.shp"
)


def parse_args():
    parser = argparse.ArgumentParser(description="Adaptively collect London rental search results across smaller areas.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for merged outputs.")
    parser.add_argument("--run-dir", help="Directory for intermediate adaptive collector files.")
    parser.add_argument("--boroughs-path", default=str(DEFAULT_BOROUGHS_PATH), help="London borough shapefile.")
    parser.add_argument("--wards-path", default=str(DEFAULT_WARDS_PATH), help="London ward shapefile.")
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
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=False, help="Run resolution and search browsers headlessly.")
    parser.add_argument("--interactive", action=argparse.BooleanOptionalAction, default=False, help="Allow manual cookie/CAPTCHA handling during resolution/search.")
    parser.add_argument("--user-data-dir", help="Optional shared Chrome user-data directory.")
    parser.add_argument("--seed-borough", action="append", help="Optional borough(s) to limit collection to.")
    return parser.parse_args()


def _resolve_path(path_arg):
    path = Path(path_arg)
    if not path.is_absolute():
        path = (SCRIPT_DIR.parent / path).resolve()
    return path


def _default_run_dir(output_dir):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(output_dir) / f"adaptive_london_search_run_{timestamp}"


def _load_hierarchy(boroughs_path, wards_path, seed_boroughs=None):
    boroughs_gdf = gpd.read_file(boroughs_path)
    wards_gdf = gpd.read_file(wards_path)
    boroughs = sorted({str(name).strip() for name in boroughs_gdf["NAME"].tolist() if str(name).strip()})
    if seed_boroughs:
        allowed = {value.strip().lower() for value in seed_boroughs}
        boroughs = [name for name in boroughs if name.lower() in allowed]
    children = {}
    for borough in boroughs:
        ward_names = sorted(
            {
                f"{str(name).strip()}, {borough}"
                for name in wards_gdf.loc[wards_gdf["BOROUGH"] == borough, "NAME"].tolist()
                if str(name).strip()
            }
        )
        children[borough] = ward_names
    return boroughs, children


def _latest_search_json(directory):
    candidates = sorted(
        Path(directory).glob("rightmove_rental_search_results_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _run_search(search_url, output_dir, *, pages, max_results, headless, interactive, user_data_dir):
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
    subprocess.run(command, check=True, cwd=str(SCRIPT_DIR.parent))
    search_json = _latest_search_json(output_dir)
    if not search_json:
        raise FileNotFoundError(f"No search JSON produced in {output_dir}")
    payload = json.loads(search_json.read_text())
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
                "search_url": area["resolution"].get("search_url"),
                "location_identifier": area["resolution"].get("location_identifier"),
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
    boroughs_path = _resolve_path(args.boroughs_path)
    wards_path = _resolve_path(args.wards_path)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    boroughs, ward_children = _load_hierarchy(boroughs_path, wards_path, seed_boroughs=args.seed_borough)
    if not boroughs:
        raise RuntimeError("No London borough seeds were loaded.")

    driver = setup_browser(headless=args.headless, user_data_dir=args.user_data_dir)
    resolution_cache = {}
    area_records = []
    failed_resolutions = []

    def resolve_query(query):
        if query in resolution_cache:
            return resolution_cache[query]
        nonlocal driver
        driver, resolved = resolve_location(
            driver,
            query,
            headless=args.headless,
            user_data_dir=args.user_data_dir,
            interactive=args.interactive,
        )
        resolution_cache[query] = resolved
        return resolved

    def collect(query, depth):
        resolved = resolve_query(query)
        if not resolved.get("ok"):
            failed_resolutions.append({"query": query, "depth": depth, "resolution": resolved})
            print(f"Resolution failed for {query}")
            return []

        area_slug = _slugify(query)
        area_dir = run_dir / "areas" / f"{depth:02d}-{area_slug}"
        area_output_dir = area_dir / "search_output"
        area_output_dir.mkdir(parents=True, exist_ok=True)

        search_json, payload = _run_search(
            resolved["search_url"],
            area_output_dir,
            pages=args.pages,
            max_results=args.max_results,
            headless=args.headless,
            interactive=args.interactive,
            user_data_dir=args.user_data_dir,
        )
        meta = payload.get("meta", {})
        page_total = int(meta.get("reported_pagination_total") or 0)
        area_record = {
            "query": query,
            "depth": depth,
            "resolution": resolved,
            "search_json": str(search_json),
            "payload": payload,
        }

        if depth == 0 and page_total >= args.pagination_cap and ward_children.get(query):
            child_records = []
            for child_query in ward_children[query]:
                child_records.extend(collect(child_query, depth + 1))
            if child_records:
                area_record["subdivided"] = True
                area_records.append(area_record)
                return child_records

        area_records.append(area_record)
        return [area_record]

    try:
        leaf_area_payloads = []
        for borough in boroughs:
            leaf_area_payloads.extend(collect(borough, 0))
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    merged_json, merged_csv, _ = _merge_search_payloads(leaf_area_payloads, output_dir)

    report = {
        "generated_at": datetime.now().isoformat(),
        "run_dir": str(run_dir),
        "borough_seed_count": len(boroughs),
        "leaf_area_count": len(leaf_area_payloads),
        "failed_resolution_count": len(failed_resolutions),
        "failed_resolutions": failed_resolutions,
        "areas": [
            {
                "query": item["query"],
                "depth": item["depth"],
                "subdivided": bool(item.get("subdivided")),
                "search_json": item["search_json"],
                "location_identifier": item["resolution"].get("location_identifier"),
                "reported_result_count": item["payload"].get("meta", {}).get("reported_result_count"),
                "reported_pagination_total": item["payload"].get("meta", {}).get("reported_pagination_total"),
                "results_count": item["payload"].get("meta", {}).get("results_count"),
            }
            for item in area_records
        ],
        "leaf_areas": [item["query"] for item in leaf_area_payloads],
        "merged_search_json": str(merged_json),
        "merged_search_csv": str(merged_csv),
    }
    save_resolution_report(run_dir / "adaptive_london_search_report.json", report)
    save_resolution_report(run_dir / "location_resolution_cache.json", {"results": list(resolution_cache.values())})

    print("\nSaved adaptive London search outputs:")
    print(f"  Merged JSON: {merged_json}")
    print(f"  Merged CSV:  {merged_csv}")
    print(f"  Report:      {run_dir / 'adaptive_london_search_report.json'}")
    print(f"  Leaf areas:  {len(leaf_area_payloads)}")


if __name__ == "__main__":
    main()
