"""
Build Adaptive Search Chunk Manifest
===================================

Reads the committed adaptive London search config and emits a GitHub Actions-
friendly chunk manifest for splitting borough searches across multiple jobs.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "config" / "rightmove_london_adaptive_search.json"


def parse_args():
    parser = argparse.ArgumentParser(description="Build a chunk manifest for adaptive London search jobs.")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to the committed adaptive search config JSON.",
    )
    parser.add_argument(
        "--boroughs-per-chunk",
        type=int,
        default=4,
        help="How many borough seed searches to run in each search chunk job.",
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
    if args.boroughs_per_chunk <= 0:
        raise ValueError("--boroughs-per-chunk must be a positive integer.")

    config_path = _resolve_path(args.config)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    boroughs = [
        item["name"]
        for item in payload.get("boroughs", [])
        if item.get("name") and item.get("seed_search_url")
    ]

    chunk_count = max(1, math.ceil(len(boroughs) / args.boroughs_per_chunk)) if boroughs else 0
    manifest = {
        "total_boroughs": len(boroughs),
        "boroughs_per_chunk": args.boroughs_per_chunk,
        "chunk_count": chunk_count,
        "include": [],
    }

    for chunk_index in range(chunk_count):
        start_index = chunk_index * args.boroughs_per_chunk
        end_index = min(len(boroughs), start_index + args.boroughs_per_chunk)
        chunk_boroughs = boroughs[start_index:end_index]
        manifest["include"].append(
            {
                "chunk_index": chunk_index,
                "chunk_label": f"search-{chunk_index:03d}",
                "borough_names": chunk_boroughs,
                "borough_count": len(chunk_boroughs),
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
