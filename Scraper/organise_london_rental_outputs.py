"""
Organise London Rental Outputs
==============================

Keeps Scraper/output tidy by:
  - moving timestamped pipeline outputs into archive subfolders
  - maintaining a stable current/ snapshot for the latest run artefacts
  - leaving history/ in place for derived historical outputs
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"

SEARCH_RE = re.compile(r"^rightmove_rental_search_results_(\d{8}_\d{6})\.(json|csv)$")
ENRICHED_RE = re.compile(r"^rightmove_rental_enriched_results_(\d{8}_\d{6})\.(json|csv)$")
CLEANED_RE = re.compile(r"^rightmove_cleaned_dataset_(\d{8}_\d{6})\.(json|csv)$")
CLEANED_QC_RE = re.compile(r"^rightmove_cleaned_dataset_qc_(\d{8}_\d{6})\.json$")
CLIPPED_RE = re.compile(r"^rightmove_cleaned_dataset_(\d{8}_\d{6})_london_clipped_(\d{8}_\d{6})\.(json|csv)$")
SUMMARY_RE = re.compile(r"^rightmove_cleaned_dataset_(\d{8}_\d{6})_london_summary_(\d{8}_\d{6})\.json$")


def parse_args():
    parser = argparse.ArgumentParser(description="Tidy Scraper/output after a London rental pipeline run.")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory containing London rental pipeline outputs.",
    )
    return parser.parse_args()


def _resolve_path(path_arg):
    path = Path(path_arg)
    if not path.is_absolute():
        path = (SCRIPT_DIR.parent / path).resolve()
    return path


def _archive_category(path: Path):
    name = path.name
    if SEARCH_RE.match(name):
        return "search"
    if ENRICHED_RE.match(name):
        return "enriched"
    if CLEANED_QC_RE.match(name) or CLEANED_RE.match(name):
        return "cleaned"
    if CLIPPED_RE.match(name) or SUMMARY_RE.match(name):
        return "clipped"
    return None


def _latest_file(directory: Path, pattern: str):
    files = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _latest_cleaned_json(directory: Path):
    files = sorted(directory.glob("rightmove_cleaned_dataset_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    files = [path for path in files if not CLEANED_QC_RE.match(path.name) and not CLIPPED_RE.match(path.name)]
    return files[0] if files else None


def _copy_current(latest_file: Path | None, target_path: Path):
    if not latest_file or not latest_file.exists():
        return False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(latest_file, target_path)
    return True


def _write_manifest(current_dir: Path, manifest: dict):
    manifest_path = current_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    args = parse_args()
    output_dir = _resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    archives_dir = output_dir / "archives"
    current_dir = output_dir / "current"
    history_dir = output_dir / "history"
    for subdir in [
        archives_dir / "search",
        archives_dir / "enriched",
        archives_dir / "cleaned",
        archives_dir / "clipped",
        current_dir,
        history_dir,
    ]:
        subdir.mkdir(parents=True, exist_ok=True)

    moved_files = []
    for path in sorted(output_dir.iterdir()):
        if not path.is_file():
            continue
        category = _archive_category(path)
        if not category:
            continue
        destination = archives_dir / category / path.name
        shutil.move(str(path), str(destination))
        moved_files.append(str(destination))

    for path in current_dir.glob("*"):
        if path.is_file():
            path.unlink()

    latest_search_json = _latest_file(archives_dir / "search", "rightmove_rental_search_results_*.json")
    latest_search_csv = _latest_file(archives_dir / "search", "rightmove_rental_search_results_*.csv")
    latest_enriched_json = _latest_file(archives_dir / "enriched", "rightmove_rental_enriched_results_*.json")
    latest_enriched_csv = _latest_file(archives_dir / "enriched", "rightmove_rental_enriched_results_*.csv")
    latest_cleaned_json = _latest_cleaned_json(archives_dir / "cleaned")
    latest_cleaned_csv = _latest_file(archives_dir / "cleaned", "rightmove_cleaned_dataset_*.csv")
    latest_cleaned_qc = _latest_file(archives_dir / "cleaned", "rightmove_cleaned_dataset_qc_*.json")
    latest_clipped_json = _latest_file(archives_dir / "clipped", "rightmove_cleaned_dataset_*_london_clipped_*.json")
    latest_clipped_csv = _latest_file(archives_dir / "clipped", "rightmove_cleaned_dataset_*_london_clipped_*.csv")
    latest_summary_json = _latest_file(archives_dir / "clipped", "rightmove_cleaned_dataset_*_london_summary_*.json")

    copied = {
        "search_results_json": _copy_current(latest_search_json, current_dir / "search_results.json"),
        "search_results_csv": _copy_current(latest_search_csv, current_dir / "search_results.csv"),
        "enriched_results_json": _copy_current(latest_enriched_json, current_dir / "enriched_results.json"),
        "enriched_results_csv": _copy_current(latest_enriched_csv, current_dir / "enriched_results.csv"),
        "cleaned_dataset_json": _copy_current(latest_cleaned_json, current_dir / "cleaned_dataset.json"),
        "cleaned_dataset_csv": _copy_current(latest_cleaned_csv, current_dir / "cleaned_dataset.csv"),
        "cleaned_dataset_qc_json": _copy_current(latest_cleaned_qc, current_dir / "cleaned_dataset_qc.json"),
        "london_clipped_json": _copy_current(latest_clipped_json, current_dir / "london_clipped.json"),
        "london_clipped_csv": _copy_current(latest_clipped_csv, current_dir / "london_clipped.csv"),
        "london_summary_json": _copy_current(latest_summary_json, current_dir / "london_summary.json"),
    }

    manifest = {
        "generated_at": datetime.now().isoformat(),
        "output_dir": str(output_dir),
        "archives_dir": str(archives_dir),
        "history_dir": str(history_dir),
        "moved_files": moved_files,
        "current_files": {
            name: present for name, present in copied.items()
        },
    }
    _write_manifest(current_dir, manifest)

    print("\nOrganised London rental outputs:")
    print(f"  Archives: {archives_dir}")
    print(f"  Current:  {current_dir}")
    print(f"  Moved:    {len(moved_files)}")


if __name__ == "__main__":
    main()
