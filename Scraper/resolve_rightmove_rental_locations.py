"""
Resolve Rightmove Rental Locations
==================================

CLI wrapper around the Rightmove rental location resolver.
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rightmove_location_resolver import resolve_location, save_resolution_report, setup_browser


def parse_args():
    parser = argparse.ArgumentParser(description="Resolve Rightmove rental area names to location identifiers.")
    parser.add_argument("--query", action="append", help="Area name to resolve. May be passed multiple times.")
    parser.add_argument("--queries-file", help="Optional text file containing one area query per line.")
    parser.add_argument("--output", help="Optional JSON file for the resolution report.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for default report outputs.")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=False, help="Run Chrome headlessly.")
    parser.add_argument("--interactive", action=argparse.BooleanOptionalAction, default=False, help="Allow manual cookie/CAPTCHA handling.")
    parser.add_argument("--user-data-dir", help="Optional Chrome user-data directory for session reuse.")
    parser.add_argument("--radius", type=float, default=0.0, help="Radius to use for direct Rightmove resolution URLs.")
    return parser.parse_args()


def _collect_queries(args):
    queries = list(args.query or [])
    if args.queries_file:
        for line in Path(args.queries_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                queries.append(line)
    return queries


def main():
    args = parse_args()
    queries = _collect_queries(args)
    if not queries:
        raise ValueError("Pass at least one --query or provide --queries-file.")

    driver = setup_browser(headless=args.headless, user_data_dir=args.user_data_dir)
    results = []
    try:
        for query in queries:
            driver, resolved = resolve_location(
                driver,
                query,
                headless=args.headless,
                user_data_dir=args.user_data_dir,
                interactive=args.interactive,
                radius=args.radius,
            )
            results.append(resolved)
            status = "OK" if resolved.get("ok") else "FAILED"
            print(f"{status}: {query} -> {resolved.get('location_identifier')} :: {resolved.get('search_url')}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    payload = {"results": results}
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path(args.output_dir) / "rightmove_rental_location_resolutions.json"
    save_resolution_report(output_path, payload)
    print(output_path)


if __name__ == "__main__":
    main()
