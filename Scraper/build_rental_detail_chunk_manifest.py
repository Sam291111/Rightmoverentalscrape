"""
Build Rental Detail Chunk Manifest
=================================

Reads a rental search dataset and emits a GitHub Actions-friendly chunk manifest
for splitting the detail enrichment stage across multiple jobs.
"""

import argparse
import json
import math
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"


def parse_args():
    parser = argparse.ArgumentParser(description="Build a chunk manifest for rental detail enrichment.")
    parser.add_argument(
        "--search-results-json",
        required=True,
        help="Path to the rental search results JSON.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=400,
        help="Listings per detail chunk.",
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
    if args.chunk_size <= 0:
        raise ValueError("--chunk-size must be a positive integer.")

    search_json = _resolve_path(args.search_results_json)
    payload = json.loads(search_json.read_text())
    results = payload.get("results", [])
    total_results = len(results)
    chunk_count = max(1, math.ceil(total_results / args.chunk_size)) if total_results else 0

    manifest = {
        "total_results": total_results,
        "chunk_size": args.chunk_size,
        "chunk_count": chunk_count,
        "include": [],
    }

    for chunk_index in range(chunk_count):
        start_index = chunk_index * args.chunk_size
        end_index = min(total_results, start_index + args.chunk_size)
        manifest["include"].append(
            {
                "chunk_index": chunk_index,
                "chunk_label": f"chunk-{chunk_index:03d}",
                "start_index": start_index,
                "end_index": end_index,
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
