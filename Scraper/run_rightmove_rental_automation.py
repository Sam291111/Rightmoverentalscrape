"""
Rightmove Rental Automation Wrapper
==================================

Thin wrapper around the main pipeline that enables the unattended flags
without changing the default semi-manual workflow.

Example:
  python3 Scraper/run_rightmove_rental_automation.py \
    --search-url "https://www.rightmove.co.uk/property-to-rent/find.html?searchLocation=London&useLocationIdentifier=true&locationIdentifier=REGION%5E87490"
"""

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_PATH = SCRIPT_DIR / "run_rightmove_pipeline.py"
DEFAULT_PROFILE_DIR = SCRIPT_DIR / ".browser_profiles" / "rightmove_rent"


def parse_args():
    parser = argparse.ArgumentParser(description="Run the Rightmove rental pipeline in unattended mode.")
    parser.add_argument("--search-url", required=True, help="Rightmove rental search URL.")
    parser.add_argument("--pages", type=int, default=1, help="How many search result pages to scrape.")
    parser.add_argument("--page-size", type=int, default=24, help="Search pagination step size.")
    parser.add_argument("--max-results", type=int, help="Optional cap on search-stage listings.")
    parser.add_argument("--detail-limit", type=int, help="Optional cap on detail-stage listings.")
    parser.add_argument("--detail-run-dir", help="Optional checkpoint directory for the detail pass.")
    parser.add_argument("--output-dir", default=str(SCRIPT_DIR / "output"), help="Shared output directory.")
    parser.add_argument(
        "--user-data-dir",
        default=str(DEFAULT_PROFILE_DIR),
        help="Chrome user-data directory used to persist cookies/session state between automated runs.",
    )
    parser.add_argument(
        "--visible-browser",
        action="store_true",
        help="Keep the automation browser visible instead of using headless mode.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    command = [
        sys.executable,
        str(PIPELINE_PATH),
        "--market",
        "rent",
        "--search-url",
        args.search_url,
        "--pages",
        str(args.pages),
        "--page-size",
        str(args.page_size),
        "--output-dir",
        args.output_dir,
        "--no-interactive",
        "--user-data-dir",
        args.user_data_dir,
    ]
    if not args.visible_browser:
        command.append("--headless")
    if args.max_results:
        command.extend(["--max-results", str(args.max_results)])
    if args.detail_limit:
        command.extend(["--detail-limit", str(args.detail_limit)])
    if args.detail_run_dir:
        command.extend(["--detail-run-dir", args.detail_run_dir])

    subprocess.run(command, check=True, cwd=str(SCRIPT_DIR.parent))


if __name__ == "__main__":
    main()
