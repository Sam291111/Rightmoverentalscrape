"""
London Rental Pipeline
======================

Runs the unattended Rightmove rental pipeline for a London-focused search URL,
then cleans and spatially clips the result to Greater London.
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
DEFAULT_LONDON_SEARCH_URL = (
    "https://www.rightmove.co.uk/property-to-rent/find.html"
    "?sortType=6&areaSizeUnit=sqft&channel=RENT&index=0"
    "&locationIdentifier=REGION%5E87490&transactionType=LETTING"
    "&displayLocationIdentifier=undefined&radius=10.0"
)
DEFAULT_PROFILE_DIR = SCRIPT_DIR / ".browser_profiles" / "rightmove_rent"
DEFAULT_BOUNDARY_PATH = SCRIPT_DIR.parent / "London Boundary" / "gla" / "London_GLA_Boundary.shp"
DEFAULT_BOROUGHS_PATH = (
    SCRIPT_DIR.parent
    / "London Boundary"
    / "statistical-gis-boundaries-london"
    / "ESRI"
    / "London_Borough_Excluding_MHW.shp"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the London rental scrape, clean it, then clip it to the Greater London boundary."
    )
    parser.add_argument(
        "--search-url",
        default=DEFAULT_LONDON_SEARCH_URL,
        help="Rightmove rental search URL to scrape before clipping to London.",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=0,
        help="How many search result pages to scrape. Use 0 to keep going until pagination naturally stops.",
    )
    parser.add_argument("--page-size", type=int, default=24, help="Search pagination step size.")
    parser.add_argument(
        "--max-results",
        type=int,
        default=0,
        help="Maximum search-stage listings to keep. Use 0 for no cap.",
    )
    parser.add_argument("--detail-limit", type=int, help="Optional cap on detail-stage listings.")
    parser.add_argument("--detail-run-dir", help="Optional checkpoint directory for the detail pass.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Shared output directory.")
    parser.add_argument(
        "--user-data-dir",
        default=str(DEFAULT_PROFILE_DIR),
        help="Chrome user-data directory used to persist cookies/session state between runs.",
    )
    parser.add_argument(
        "--visible-browser",
        action="store_true",
        help="Keep Chrome visible instead of running headlessly.",
    )
    parser.add_argument(
        "--boundary-path",
        default=str(DEFAULT_BOUNDARY_PATH),
        help="Path to the Greater London boundary shapefile.",
    )
    parser.add_argument(
        "--boroughs-path",
        default=str(DEFAULT_BOROUGHS_PATH),
        help="Path to the London borough shapefile used for tagging rows.",
    )
    return parser.parse_args()


def _latest_file(pattern, directory, newer_than=None):
    candidates = sorted(Path(directory).glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if newer_than is not None:
        candidates = [path for path in candidates if path.stat().st_mtime >= newer_than]
    return candidates[0] if candidates else None


def _latest_cleaned_dataset(directory, newer_than=None):
    candidates = sorted(
        Path(directory).glob("rightmove_cleaned_dataset_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    candidates = [path for path in candidates if "_qc_" not in path.name]
    if newer_than is not None:
        candidates = [path for path in candidates if path.stat().st_mtime >= newer_than]
    return candidates[0] if candidates else None


def _run_command(command):
    print(f"\nRunning: {' '.join(command)}")
    subprocess.run(command, check=True, cwd=str(SCRIPT_DIR.parent))


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now().timestamp()

    pipeline_command = [
        sys.executable,
        str(SCRIPT_DIR / "run_rightmove_pipeline.py"),
        "--market",
        "rent",
        "--search-url",
        args.search_url,
        "--pages",
        str(args.pages),
        "--page-size",
        str(args.page_size),
        "--max-results",
        str(args.max_results),
        "--output-dir",
        str(output_dir),
        "--no-interactive",
        "--user-data-dir",
        args.user_data_dir,
    ]
    if not args.visible_browser:
        pipeline_command.append("--headless")
    if args.detail_limit:
        pipeline_command.extend(["--detail-limit", str(args.detail_limit)])
    if args.detail_run_dir:
        pipeline_command.extend(["--detail-run-dir", args.detail_run_dir])
    _run_command(pipeline_command)

    enriched_path = _latest_file("rightmove_rental_enriched_results_*.json", output_dir, newer_than=started_at)
    if not enriched_path:
        raise FileNotFoundError("Could not find the enriched rental dataset produced by this run.")

    clean_started_at = datetime.now().timestamp()
    clean_command = [
        sys.executable,
        str(SCRIPT_DIR / "clean_rightmove_dataset.py"),
        "--input",
        str(enriched_path),
        "--output-dir",
        str(output_dir),
    ]
    _run_command(clean_command)

    cleaned_path = _latest_cleaned_dataset(output_dir, newer_than=clean_started_at)
    if not cleaned_path:
        raise FileNotFoundError("Could not find the cleaned dataset produced by this run.")

    clip_command = [
        sys.executable,
        str(SCRIPT_DIR / "clip_rightmove_dataset_to_london.py"),
        "--input",
        str(cleaned_path),
        "--boundary-path",
        args.boundary_path,
        "--boroughs-path",
        args.boroughs_path,
        "--output-dir",
        str(output_dir),
    ]
    _run_command(clip_command)

    history_command = [
        sys.executable,
        str(SCRIPT_DIR / "build_london_rental_history.py"),
        "--input-dir",
        str(output_dir),
        "--output-dir",
        str(output_dir / "history"),
    ]
    _run_command(history_command)


if __name__ == "__main__":
    main()
