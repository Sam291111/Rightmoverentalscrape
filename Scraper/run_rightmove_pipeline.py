"""
Rightmove Pipeline Wrapper
==========================

Runs the search-stage scraper followed by the detail-stage scraper.

Examples:
  python3 Scraper/run_rightmove_pipeline.py \
    --market sale \
    --search-url "https://www.rightmove.co.uk/property-for-sale/find.html?searchLocation=London&useLocationIdentifier=true&locationIdentifier=REGION%5E87490&radius=5.0&_includeSSTC=on&sortType=6&channel=BUY&transactionType=BUY&displayLocationIdentifier=London-87490.html" \
    --pages 2 \
    --detail-limit 10

  python3 Scraper/run_rightmove_pipeline.py \
    --market rent \
    --search-url "https://www.rightmove.co.uk/property-to-rent/find.html?searchLocation=White+City%2C+West+London&useLocationIdentifier=true&locationIdentifier=REGION%5E85399&radius=0.0&_includeLetAgreed=on" \
    --pages 2 \
    --detail-limit 10

  python3 Scraper/run_rightmove_pipeline.py \
    --skip-search \
    --search-results-json Scraper/output/rightmove_search_results_20260324_155603.json \
    --detail-limit 5
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
MARKET_CONFIG = {
    "sale": {
        "search_script": SCRIPT_DIR / "rightmove_search_scraper.py",
        "detail_script": SCRIPT_DIR / "rightmove_detail_scraper.py",
        "search_pattern": "rightmove_search_results_*.json",
        "enriched_pattern": "rightmove_enriched_results_*.json",
    },
    "rent": {
        "search_script": SCRIPT_DIR / "rightmove_rental_search_scraper.py",
        "detail_script": SCRIPT_DIR / "rightmove_rental_detail_scraper.py",
        "search_pattern": "rightmove_rental_search_results_*.json",
        "enriched_pattern": "rightmove_rental_enriched_results_*.json",
    },
}


def parse_args():
    parser = argparse.ArgumentParser(description="Run the Rightmove search scraper and detail scraper in sequence.")
    parser.add_argument(
        "--market",
        choices=sorted(MARKET_CONFIG.keys()),
        default="sale",
        help="Which Rightmove market to run. 'sale' keeps the existing behaviour.",
    )
    parser.add_argument("--search-url", help="Rightmove search results URL for the search-stage scraper.")
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
        help="Optional cap on how many search-stage listings to keep before enrichment. Use 0 for no cap.",
    )
    parser.add_argument(
        "--search-wait-seconds",
        type=float,
        default=2.5,
        help="Delay after each search result page load.",
    )
    parser.add_argument(
        "--detail-wait-seconds",
        type=float,
        default=0.75,
        help="Extra settle delay after each listing detail page looks ready.",
    )
    parser.add_argument(
        "--detail-page-timeout",
        type=float,
        default=12.0,
        help="How long the detail scraper waits for a listing page to become scraper-ready.",
    )
    parser.add_argument(
        "--block-detail-images",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Block image/media downloads during the detail pass to speed up scraping.",
    )
    parser.add_argument(
        "--detail-limit",
        type=int,
        help="Optional cap on how many listings the detail scraper enriches.",
    )
    parser.add_argument(
        "--resume-detail",
        action="store_true",
        help="Resume a previous detail checkpoint run instead of starting a fresh detail pass.",
    )
    parser.add_argument(
        "--detail-run-dir",
        help="Checkpoint directory for the detail pass. Useful with --resume-detail.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Shared output directory for search/detail outputs.",
    )
    parser.add_argument(
        "--search-results-json",
        help="Existing search JSON to use for the detail stage. Required with --skip-search unless a latest file exists.",
    )
    parser.add_argument(
        "--skip-search",
        action="store_true",
        help="Skip the search stage and run only the detail stage.",
    )
    parser.add_argument(
        "--interactive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pause for manual cookie/CAPTCHA handling. Disable for unattended automation runs.",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Run Chrome headlessly. Useful for unattended automation.",
    )
    parser.add_argument(
        "--user-data-dir",
        help="Optional Chrome user-data directory to reuse cookies/session state across runs.",
    )
    return parser.parse_args()


def _latest_file(pattern, directory, newer_than=None):
    candidates = sorted(Path(directory).glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if newer_than is not None:
        candidates = [path for path in candidates if path.stat().st_mtime >= newer_than]
    return candidates[0] if candidates else None


def _run_command(command):
    print(f"\nRunning: {' '.join(command)}")
    subprocess.run(command, check=True, cwd=str(SCRIPT_DIR.parent))


def _resolve_user_search_json(path_arg):
    if not path_arg:
        return None
    path = Path(path_arg)
    if not path.is_absolute():
        path = (SCRIPT_DIR.parent / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Search results JSON not found: {path}")
    return path


def _resolve_search_json(args, started_at, prefer_new_output):
    market = MARKET_CONFIG[args.market]
    latest = _latest_file(market["search_pattern"], args.output_dir, newer_than=started_at)
    if latest:
        return latest.resolve()

    user_path = _resolve_user_search_json(args.search_results_json)
    if user_path and not prefer_new_output:
        return user_path

    latest = _latest_file(market["search_pattern"], args.output_dir)
    if latest:
        return latest.resolve()

    if user_path:
        return user_path

    raise FileNotFoundError("No search results JSON available for the detail stage.")


def main():
    args = parse_args()
    market = MARKET_CONFIG[args.market]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    search_started_at = datetime.now().timestamp()
    search_json = None

    if not args.skip_search:
        search_command = [
            sys.executable,
            str(market["search_script"]),
            "--pages",
            str(args.pages),
            "--page-size",
            str(args.page_size),
            "--wait-seconds",
            str(args.search_wait_seconds),
            "--output-dir",
            str(output_dir),
        ]
        if args.max_results is not None:
            search_command.extend(["--max-results", str(args.max_results)])
        if args.search_url:
            search_command.extend(["--search-url", args.search_url])
        if not args.interactive:
            search_command.append("--no-interactive")
        if args.headless:
            search_command.append("--headless")
        if args.user_data_dir:
            search_command.extend(["--user-data-dir", args.user_data_dir])
        _run_command(search_command)
        search_json = _resolve_search_json(args, search_started_at, prefer_new_output=True)
    else:
        search_json = _resolve_search_json(args, None, prefer_new_output=False)

    print(f"\nUsing search dataset: {search_json}")

    detail_command = [
        sys.executable,
        str(market["detail_script"]),
        "--search-results-json",
        str(search_json),
        "--wait-seconds",
        str(args.detail_wait_seconds),
        "--page-timeout",
        str(args.detail_page_timeout),
        "--output-dir",
        str(output_dir),
    ]
    if args.block_detail_images:
        detail_command.append("--block-images")
    else:
        detail_command.append("--no-block-images")
    if args.resume_detail:
        detail_command.append("--resume")
    if args.detail_run_dir:
        detail_command.extend(["--run-dir", args.detail_run_dir])
    if args.detail_limit:
        detail_command.extend(["--limit", str(args.detail_limit)])
    if not args.interactive:
        detail_command.append("--no-interactive")
    if args.headless:
        detail_command.append("--headless")
    if args.user_data_dir:
        detail_command.extend(["--user-data-dir", args.user_data_dir])

    _run_command(detail_command)

    latest_enriched = _latest_file(market["enriched_pattern"], output_dir)
    if latest_enriched:
        print(f"\nLatest enriched dataset: {latest_enriched.resolve()}")


if __name__ == "__main__":
    main()
