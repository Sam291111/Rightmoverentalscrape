"""
Build committed config for postcode-first Rightmove London rental searches.

This is a maintenance script, not part of the runtime scraping path. It reads
the borough-to-prefix CSV plus Rightmove outcode codes and writes a compact JSON
bundle that the postcode-first collector can consume directly.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PREFIX_CSV = SCRIPT_DIR.parent / "Postcode_prefix_toBorough.csv"
DEFAULT_OUTCODE_MAPPINGS_JSON = SCRIPT_DIR.parent / "Rightmove outcode mappings.json"
DEFAULT_OUTPUT_PATH = SCRIPT_DIR / "config" / "rightmove_london_adaptive_search.json"

# Known valid Rightmove outcodes that are not present in the saved mapping file.
EXTRA_RIGHTMOVE_OUTCODE_CODES = {
    "E20": "6110",
    "N1C": "6147",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Build compact config for postcode-first London Rightmove rental searches.")
    parser.add_argument(
        "--prefix-csv",
        default=str(DEFAULT_PREFIX_CSV),
        help="CSV containing `prefix,borough` rows for London postcode prefixes.",
    )
    parser.add_argument(
        "--outcode-mappings-json",
        default=str(DEFAULT_OUTCODE_MAPPINGS_JSON),
        help="Rightmove outcode mapping JSON.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path for the generated postcode-first search config JSON.",
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


def _normalise_outcode_identifier(outcode_code):
    code = str(outcode_code or "").strip().upper()
    if code.startswith("5E"):
        code = code[2:]
    return code


def _build_outcode_search_url(outcode, location_code):
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
            "mapping_source": "saved_mapping_json",
        }
    for outcode, code in EXTRA_RIGHTMOVE_OUTCODE_CODES.items():
        mapping[outcode] = {
            "outcode": outcode,
            "code": str(code),
            "location_code": _normalise_outcode_identifier(code),
            "mapping_source": "manual_override",
        }
    return mapping


def _load_prefix_rows(path):
    seen_prefixes = {}
    rows = []
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            prefix = str(row.get("prefix") or "").strip().upper()
            borough = str(row.get("borough") or "").strip()
            if not prefix or not borough:
                continue
            existing = seen_prefixes.get(prefix)
            if existing and existing != borough:
                raise RuntimeError(f"Prefix {prefix} is mapped to multiple boroughs: {existing!r} and {borough!r}")
            seen_prefixes[prefix] = borough
            rows.append(
                {
                    "outcode": prefix,
                    "borough": borough,
                    "normalised_borough": _normalise_borough_name(borough),
                }
            )
    rows.sort(key=lambda item: (item["normalised_borough"], item["outcode"]))
    return rows


def build_config(prefix_csv, outcode_mappings_json):
    mapping = _load_rightmove_outcode_mapping(outcode_mappings_json)
    prefix_rows = _load_prefix_rows(prefix_csv)

    search_units = []
    skipped_prefixes = []
    for item in prefix_rows:
        outcode = item["outcode"]
        rightmove_mapping = mapping.get(outcode)
        if not rightmove_mapping:
            skipped_prefixes.append(
                {
                    "outcode": outcode,
                    "borough": item["borough"],
                    "reason": "missing_rightmove_mapping",
                }
            )
            continue
        location_code = rightmove_mapping["location_code"]
        search_units.append(
            {
                "outcode": outcode,
                "borough": item["borough"],
                "normalised_borough": item["normalised_borough"],
                "code": rightmove_mapping["code"],
                "location_code": location_code,
                "mapping_source": rightmove_mapping["mapping_source"],
                "search_url": _build_outcode_search_url(outcode, location_code),
            }
        )

    borough_counts = {}
    for unit in search_units:
        borough_counts[unit["borough"]] = borough_counts.get(unit["borough"], 0) + 1

    return {
        "generated_at": datetime.now().isoformat(),
        "search_mode": "postcode_first",
        "sources": {
            "prefix_csv": str(prefix_csv),
            "outcode_mappings_json": str(outcode_mappings_json),
            "manual_rightmove_outcode_codes": EXTRA_RIGHTMOVE_OUTCODE_CODES,
        },
        "search_unit_count": len(search_units),
        "borough_count": len(borough_counts),
        "search_units": search_units,
        "borough_outcode_counts": borough_counts,
        "skipped_prefixes": skipped_prefixes,
    }


def main():
    args = parse_args()
    prefix_csv = _resolve_path(args.prefix_csv)
    outcode_mappings_json = _resolve_path(args.outcode_mappings_json)
    output_path = _resolve_path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = build_config(
        prefix_csv=prefix_csv,
        outcode_mappings_json=outcode_mappings_json,
    )
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Saved postcode-first config: {output_path}")
    print(f"Search units: {payload['search_unit_count']}")
    print(f"Boroughs represented: {payload['borough_count']}")
    print(f"Skipped prefixes: {len(payload['skipped_prefixes'])}")


if __name__ == "__main__":
    main()
