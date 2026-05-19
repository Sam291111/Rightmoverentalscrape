"""
Adaptive London Rental Search Collector
=======================================

Works around Rightmove's broad-search pagination cap by:
  1. scraping known borough seed URLs from a text file
  2. identifying borough searches that still hit Rightmove's page cap
  3. subdividing capped boroughs into postcode outcode searches
  4. merging and deduplicating the resulting search-stage listings

This produces a merged rental search dataset that can then be passed into the
existing detail scraper and downstream London clipping pipeline.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import geopandas as gpd


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
DEFAULT_BOROUGHS_PATH = (
    SCRIPT_DIR.parent
    / "London Boundary"
    / "statistical-gis-boundaries-london"
    / "ESRI"
    / "London_Borough_Excluding_MHW.shp"
)
DEFAULT_SEED_LINKS_FILE = SCRIPT_DIR / "Borough_Links.txt"
DEFAULT_POSTCODE_CSV = SCRIPT_DIR.parent / "london_postcodes-ons-postcodes-directory-feb22.csv"
DEFAULT_OUTCODE_MAPPINGS_JSON = SCRIPT_DIR.parent / "Rightmove outcode mappings.json"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rightmove_location_resolver import save_resolution_report  # noqa: E402
from rightmove_rental_search_scraper import merge_listing_data, save_outputs  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description="Adaptively collect London rental search results across smaller areas.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for merged outputs.")
    parser.add_argument("--run-dir", help="Directory for intermediate adaptive collector files.")
    parser.add_argument("--boroughs-path", default=str(DEFAULT_BOROUGHS_PATH), help="London borough shapefile.")
    parser.add_argument(
        "--seed-links-file",
        default=str(DEFAULT_SEED_LINKS_FILE),
        help="Text file containing `Borough: URL` Rightmove borough seed links.",
    )
    parser.add_argument(
        "--postcode-csv",
        default=str(DEFAULT_POSTCODE_CSV),
        help="ONS postcode CSV used to derive borough outcodes.",
    )
    parser.add_argument(
        "--outcode-mappings-json",
        default=str(DEFAULT_OUTCODE_MAPPINGS_JSON),
        help="Optional Rightmove outcode mapping JSON for validating known outcodes.",
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


def _load_borough_codes(boroughs_path, seed_boroughs=None):
    boroughs_gdf = gpd.read_file(boroughs_path)
    allowed = {_normalise_borough_name(value) for value in (seed_boroughs or [])}
    borough_names = []
    code_map = {}
    for _, row in boroughs_gdf.iterrows():
        name = str(row["NAME"]).strip()
        if not name:
            continue
        if allowed and _normalise_borough_name(name) not in allowed:
            continue
        borough_names.append(name)
        code_map[name] = str(row["GSS_CODE"]).strip()
    return sorted(set(borough_names)), code_map


def _load_seed_links(path, seed_boroughs=None):
    allowed = {_normalise_borough_name(value) for value in (seed_boroughs or [])}
    seed_map = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        name, url = line.split(":", 1)
        borough_name = name.strip()
        url = url.strip()
        normalised = _normalise_borough_name(borough_name)
        if allowed and normalised not in allowed:
            continue
        seed_map[borough_name] = {
            "borough_name": borough_name,
            "normalised_name": normalised,
            "search_url": url,
        }
    return seed_map


def _postcode_outcode(postcode):
    parts = str(postcode or "").strip().split()
    return parts[0].upper() if parts else None


def _load_valid_rightmove_outcodes(path):
    path = Path(path)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return None
    return {str(item.get("outcode")).upper() for item in payload if item.get("outcode")}


def _load_borough_outcodes(postcode_csv_path, borough_code_map, seed_boroughs=None, valid_outcodes=None):
    allowed = {_normalise_borough_name(value) for value in (seed_boroughs or [])}
    code_to_name = {code: name for name, code in borough_code_map.items()}
    borough_outcodes = {name: {} for name in borough_code_map}

    with Path(postcode_csv_path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            borough_code = str(row.get("oslaua") or "").strip()
            borough_name = code_to_name.get(borough_code)
            if not borough_name:
                continue
            if allowed and _normalise_borough_name(borough_name) not in allowed:
                continue
            if str(row.get("doterm") or "").strip():
                continue
            outcode = _postcode_outcode(row.get("pcds") or row.get("pcd"))
            if not outcode:
                continue
            if valid_outcodes is not None and outcode not in valid_outcodes:
                continue
            counts = borough_outcodes.setdefault(borough_name, {})
            counts[outcode] = counts.get(outcode, 0) + 1

    return {
        borough: [outcode for outcode, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
        for borough, counts in borough_outcodes.items()
        if counts
    }


def _build_outcode_search_url(outcode):
    params = {
        "useLocationIdentifier": "true",
        "locationIdentifier": f"OUTCODE^{outcode}",
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
    boroughs_path = _resolve_path(args.boroughs_path)
    seed_links_path = _resolve_path(args.seed_links_file)
    postcode_csv_path = _resolve_path(args.postcode_csv)
    outcode_mappings_path = _resolve_path(args.outcode_mappings_json)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    borough_names, borough_code_map = _load_borough_codes(boroughs_path, seed_boroughs=args.seed_borough)
    seed_links = _load_seed_links(seed_links_path, seed_boroughs=args.seed_borough)
    valid_rightmove_outcodes = _load_valid_rightmove_outcodes(outcode_mappings_path)
    borough_outcodes = _load_borough_outcodes(
        postcode_csv_path,
        borough_code_map,
        seed_boroughs=args.seed_borough,
        valid_outcodes=valid_rightmove_outcodes,
    )

    if not seed_links:
        raise RuntimeError("No borough seed links were loaded.")

    area_records = []

    def collect(query, search_url, depth, borough_name, source_type):
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
            and borough_outcodes.get(borough_name)
        ):
            child_records = []
            outcodes = borough_outcodes[borough_name]
            if args.max_outcodes_per_borough:
                outcodes = outcodes[: args.max_outcodes_per_borough]
            for outcode in outcodes:
                child_query = f"{outcode} [{borough_name}]"
                child_records.extend(
                    collect(
                        child_query,
                        _build_outcode_search_url(outcode),
                        depth + 1,
                        borough_name,
                        "borough_outcode",
                    )
                )
            if child_records:
                area_record["subdivided"] = True
                area_records.append(area_record)
                return child_records

        area_records.append(area_record)
        return [area_record]

    leaf_area_payloads = []
    for borough_name in borough_names:
        seed = next((seed for seed_name, seed in seed_links.items() if _normalise_borough_name(seed_name) == _normalise_borough_name(borough_name)), None)
        if not seed:
            print(f"Skipping {borough_name}: no seed link in {seed_links_path.name}")
            continue
        leaf_area_payloads.extend(
            collect(
                borough_name,
                seed["search_url"],
                0,
                borough_name,
                "borough_seed_link",
            )
        )

    merged_json, merged_csv, _ = _merge_search_payloads(leaf_area_payloads, output_dir)

    report = {
        "generated_at": datetime.now().isoformat(),
        "run_dir": str(run_dir),
        "borough_seed_file": str(seed_links_path),
        "borough_seed_count": len(seed_links),
        "leaf_area_count": len(leaf_area_payloads),
        "postcode_outcode_source": str(postcode_csv_path),
        "rightmove_outcode_mapping_source": str(outcode_mappings_path) if outcode_mappings_path.exists() else None,
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
            }
            for item in area_records
        ],
        "leaf_areas": [item["query"] for item in leaf_area_payloads],
        "borough_outcode_counts": {name: len(values) for name, values in borough_outcodes.items()},
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
