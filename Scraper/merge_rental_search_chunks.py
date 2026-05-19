"""
Merge Rental Search Chunks
==========================

Merges multiple search-stage JSON outputs into one deduplicated rental search
dataset that can feed the detail enrichment chunk pipeline.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"

if str(SCRIPT_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPT_DIR))

from rightmove_rental_search_scraper import merge_listing_data, save_outputs  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description="Merge multiple rental search JSON chunks into one deduplicated dataset.")
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Directory containing chunk search_results.json files.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory to write the merged search dataset into.",
    )
    return parser.parse_args()


def _resolve_path(path_arg):
    path = Path(path_arg)
    if not path.is_absolute():
        path = (SCRIPT_DIR.parent / path).resolve()
    return path


def _iter_search_jsons(input_dir):
    for path in sorted(Path(input_dir).rglob("search_results.json")):
        yield path


def main():
    args = parse_args()
    input_dir = _resolve_path(args.input_dir)
    output_dir = _resolve_path(args.output_dir)

    merged = {}
    chunk_summaries = []

    for json_path in _iter_search_jsons(input_dir):
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        results = payload.get("results", [])
        meta = payload.get("meta", {})
        chunk_summaries.append(
            {
                "path": str(json_path),
                "results_count": len(results),
                "collector": meta.get("collector"),
                "area_count": meta.get("area_count"),
                "area_names": meta.get("area_names", []),
            }
        )
        for item in results:
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
        "collector": "merged_adaptive_london_rental_search",
        "chunk_count": len(chunk_summaries),
        "results_count": len(merged_results),
        "dedupe_key": "listing_id_or_listing_url",
        "chunk_summaries": chunk_summaries,
    }

    merged_json, merged_csv, _ = save_outputs(output_dir, merged_results, [], metadata)
    print(merged_json)
    print(merged_csv)


if __name__ == "__main__":
    main()
