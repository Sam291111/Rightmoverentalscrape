"""
Build committed config for adaptive Rightmove London rental searches.

This is a maintenance script, not part of the runtime scraping path. It reads
the research source files once, derives a compact borough + outcode config,
and writes a JSON bundle that the adaptive collector can consume directly.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import geopandas as gpd


SCRIPT_DIR = Path(__file__).resolve().parent
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
DEFAULT_OUTPUT_PATH = SCRIPT_DIR / "config" / "rightmove_london_adaptive_search.json"


def parse_args():
    parser = argparse.ArgumentParser(description="Build compact config for adaptive London Rightmove rental searches.")
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
        help="Rightmove outcode mapping JSON.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path for the generated adaptive search config JSON.",
    )
    return parser.parse_args()


def _resolve_path(path_arg):
    path = Path(path_arg)
    if not path.is_absolute():
        path = (SCRIPT_DIR.parent / path).resolve()
    return path


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


def _postcode_outcode(postcode):
    parts = str(postcode or "").strip().split()
    return parts[0].upper() if parts else None


def _normalise_outcode_identifier(outcode_code):
    code = str(outcode_code or "").strip().upper()
    if code.startswith("5E"):
        code = code[2:]
    return code


def _load_borough_index(path):
    boroughs_gdf = gpd.read_file(path)
    boroughs = []
    code_to_name = {}
    for _, row in boroughs_gdf.iterrows():
        name = str(row["NAME"]).strip()
        code = str(row["GSS_CODE"]).strip()
        if not name or not code:
            continue
        boroughs.append(
            {
                "name": name,
                "normalised_name": _normalise_borough_name(name),
                "borough_code": code,
            }
        )
        code_to_name[code] = name
    boroughs.sort(key=lambda item: item["name"])
    return boroughs, code_to_name


def _load_seed_links(path):
    seed_map = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        name, url = line.split(":", 1)
        borough_name = name.strip()
        search_url = url.strip()
        seed_map[_normalise_borough_name(borough_name)] = {
            "source_name": borough_name,
            "search_url": search_url,
            "location_identifier": _extract_location_identifier(search_url),
        }
    return seed_map


def _extract_location_identifier(search_url):
    query = parse_qs(urlparse(search_url).query)
    values = query.get("locationIdentifier")
    return values[0] if values else None


def _load_rightmove_outcode_mapping(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    mapping = {}
    for item in payload:
        outcode = str(item.get("outcode") or "").strip().upper()
        code = item.get("code")
        if not outcode or code in (None, ""):
            continue
        mapping[outcode] = {
            "outcode": outcode,
            "code": str(code),
            "location_code": _normalise_outcode_identifier(code),
        }
    return mapping


def _load_borough_outcode_counts(postcode_csv_path, code_to_name, valid_outcodes):
    borough_outcodes = {name: {} for name in code_to_name.values()}
    with Path(postcode_csv_path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            borough_code = str(row.get("oslaua") or "").strip()
            borough_name = code_to_name.get(borough_code)
            if not borough_name:
                continue
            if str(row.get("doterm") or "").strip():
                continue
            outcode = _postcode_outcode(row.get("pcds") or row.get("pcd"))
            if not outcode or outcode not in valid_outcodes:
                continue
            counts = borough_outcodes[borough_name]
            counts[outcode] = counts.get(outcode, 0) + 1
    return borough_outcodes


def build_config(boroughs_path, seed_links_file, postcode_csv, outcode_mappings_json):
    boroughs, code_to_name = _load_borough_index(boroughs_path)
    seed_links = _load_seed_links(seed_links_file)
    outcode_mapping = _load_rightmove_outcode_mapping(outcode_mappings_json)
    borough_outcode_counts = _load_borough_outcode_counts(postcode_csv, code_to_name, set(outcode_mapping))

    config_boroughs = []
    for borough in boroughs:
        seed = seed_links.get(borough["normalised_name"])
        counts = borough_outcode_counts.get(borough["name"], {})
        outcodes = []
        for outcode, postcode_count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            mapping = outcode_mapping.get(outcode)
            if not mapping:
                continue
            outcodes.append(
                {
                    "outcode": outcode,
                    "code": mapping["code"],
                    "location_code": mapping["location_code"],
                    "postcode_count": postcode_count,
                }
            )
        config_boroughs.append(
            {
                "name": borough["name"],
                "normalised_name": borough["normalised_name"],
                "borough_code": borough["borough_code"],
                "seed_search_url": seed["search_url"] if seed else None,
                "seed_location_identifier": seed["location_identifier"] if seed else None,
                "seed_source_name": seed["source_name"] if seed else None,
                "outcodes": outcodes,
            }
        )

    return {
        "generated_at": datetime.now().isoformat(),
        "sources": {
            "boroughs_path": str(boroughs_path),
            "seed_links_file": str(seed_links_file),
            "postcode_csv": str(postcode_csv),
            "outcode_mappings_json": str(outcode_mappings_json),
        },
        "borough_count": len(config_boroughs),
        "boroughs_with_seed_links": sum(1 for item in config_boroughs if item.get("seed_search_url")),
        "boroughs": config_boroughs,
    }


def main():
    args = parse_args()
    boroughs_path = _resolve_path(args.boroughs_path)
    seed_links_file = _resolve_path(args.seed_links_file)
    postcode_csv = _resolve_path(args.postcode_csv)
    outcode_mappings_json = _resolve_path(args.outcode_mappings_json)
    output_path = _resolve_path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = build_config(
        boroughs_path=boroughs_path,
        seed_links_file=seed_links_file,
        postcode_csv=postcode_csv,
        outcode_mappings_json=outcode_mappings_json,
    )
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Saved adaptive config: {output_path}")
    print(f"Boroughs: {payload['borough_count']}")
    print(f"With seed links: {payload['boroughs_with_seed_links']}")


if __name__ == "__main__":
    main()
