"""
Build Postcode Search Chunk Manifest
===================================

Reads the committed postcode-first London search config and emits a GitHub
Actions-friendly chunk manifest for splitting outcode searches across multiple
jobs.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "config" / "rightmove_london_adaptive_search.json"


def parse_args():
    parser = argparse.ArgumentParser(description="Build a chunk manifest for postcode-first London search jobs.")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to the committed postcode-first search config JSON.",
    )
    parser.add_argument(
        "--searches-per-chunk",
        type=int,
        default=12,
        help="How many outcode searches to run in each search chunk job.",
    )
    parser.add_argument(
        "--seed-outcode",
        action="append",
        help="Optional outcode(s) to limit the manifest to. Repeat the flag to include multiple outcodes.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the chunk manifest JSON.",
    )
    return parser.parse_args()


def _resolve_path(path_arg):
    path = Path(path_arg)
    if not path.is_absolute():
        path = (SCRIPT_DIR.parent / path).resolve()
    return path


def main():
    args = parse_args()
    if args.searches_per_chunk <= 0:
        raise ValueError("--searches-per-chunk must be a positive integer.")

    config_path = _resolve_path(args.config)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    search_units = payload.get("search_units", [])
    allowed_outcodes = {
        str(value).strip().upper()
        for value in (args.seed_outcode or [])
        if str(value).strip()
    }

    unit_refs = [
        {
            "outcode": item["outcode"],
            "borough": item["borough"],
        }
        for item in search_units
        if (
            item.get("outcode")
            and item.get("borough")
            and item.get("search_url")
            and (not allowed_outcodes or str(item["outcode"]).strip().upper() in allowed_outcodes)
        )
    ]

    chunk_count = max(1, math.ceil(len(unit_refs) / args.searches_per_chunk)) if unit_refs else 0
    manifest = {
        "total_search_units": len(unit_refs),
        "searches_per_chunk": args.searches_per_chunk,
        "chunk_count": chunk_count,
        "include": [],
    }

    for chunk_index in range(chunk_count):
        start_index = chunk_index * args.searches_per_chunk
        end_index = min(len(unit_refs), start_index + args.searches_per_chunk)
        chunk_units = unit_refs[start_index:end_index]
        manifest["include"].append(
            {
                "chunk_index": chunk_index,
                "chunk_label": f"search-{chunk_index:03d}",
                "search_units": chunk_units,
                "search_unit_count": len(chunk_units),
            }
        )

    if args.output:
        output_path = _resolve_path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(output_path)
    else:
        print(json.dumps(manifest))


if __name__ == "__main__":
    main()
