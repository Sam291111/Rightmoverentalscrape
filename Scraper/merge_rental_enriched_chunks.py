"""
Merge Rental Enriched Chunks
============================

Combines multiple chunked rental detail JSON outputs into a single enriched
dataset that downstream cleaning and London clipping steps can consume.
"""

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"


def parse_args():
    parser = argparse.ArgumentParser(description="Merge chunked rental enriched JSON outputs.")
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Directory containing chunk subdirectories or enriched JSON files.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for the merged enriched outputs.",
    )
    return parser.parse_args()


def _resolve_path(path_arg):
    path = Path(path_arg)
    if not path.is_absolute():
        path = (SCRIPT_DIR.parent / path).resolve()
    return path


def _find_chunk_jsons(input_dir):
    input_dir = Path(input_dir)
    direct = sorted(input_dir.glob("**/rightmove_rental_enriched_results_*.json"))
    return [path for path in direct if path.is_file()]


def _row_for_csv(item):
    return {
        "listing_id": item.get("listing_id"),
        "listing_url": item.get("listing_url"),
        "rent_amount": item.get("rent_amount"),
        "rent_frequency": item.get("rent_frequency"),
        "rent_text": item.get("rent_text"),
        "rent_amount_pcm": item.get("rent_amount_pcm"),
        "rent_amount_pw": item.get("rent_amount_pw"),
        "location": item.get("location"),
        "postcode": item.get("postcode"),
        "display_address": item.get("display_address"),
        "image_url": item.get("image_url"),
        "property_photo_count": item.get("property_photo_count"),
        "image_count": item.get("image_count"),
        "floorplan_count": item.get("floorplan_count"),
        "virtual_tour_count": item.get("virtual_tour_count"),
        "property_type": item.get("property_type"),
        "bedrooms": item.get("bedrooms"),
        "bathrooms": item.get("bathrooms"),
        "let_available_date": item.get("let_available_date"),
        "listing_status": item.get("listing_status"),
        "deposit_text": item.get("deposit_text"),
        "deposit_amount": item.get("deposit_amount"),
        "min_tenancy": item.get("min_tenancy"),
        "let_type": item.get("let_type"),
        "furnish_type": item.get("furnish_type"),
        "council_tax": item.get("council_tax"),
        "parking": item.get("parking"),
        "garden": item.get("garden"),
        "accessibility": item.get("accessibility"),
        "size_text": item.get("size_text"),
        "students": item.get("students"),
        "student_friendly": item.get("student_friendly"),
        "student_text": item.get("student_text"),
        "investment_opportunity": item.get("investment_opportunity"),
        "investment_text": item.get("investment_text"),
        "luxury": item.get("luxury"),
        "luxury_text": item.get("luxury_text"),
        "online_viewings_available": item.get("online_viewings_available"),
        "build_to_rent": item.get("build_to_rent"),
        "pets_text": item.get("pets_text"),
        "bills_text": item.get("bills_text"),
        "zero_deposit": item.get("zero_deposit"),
        "epc_rating": item.get("epc_rating"),
        "latitude": item.get("latitude"),
        "longitude": item.get("longitude"),
        "search_latitude": item.get("search_latitude"),
        "search_longitude": item.get("search_longitude"),
        "detail_latitude": item.get("detail_latitude"),
        "detail_longitude": item.get("detail_longitude"),
        "coordinate_source": item.get("coordinate_source"),
        "added_text": item.get("added_text"),
        "description": item.get("description"),
        "key_features_text": item.get("key_features_text"),
        "image_urls": json.dumps(item.get("image_urls", []), ensure_ascii=False),
        "floorplan_urls": json.dumps(item.get("floorplan_urls", []), ensure_ascii=False),
        "epc_urls": json.dumps(item.get("epc_urls", []), ensure_ascii=False),
    }


def save_outputs(output_dir, enriched_results, metadata):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    dataset = {
        "meta": metadata,
        "results": enriched_results,
    }

    json_path = output_path / f"rightmove_rental_enriched_results_{timestamp}.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(dataset, handle, indent=2, ensure_ascii=False)

    csv_path = output_path / f"rightmove_rental_enriched_results_{timestamp}.csv"
    fieldnames = list(_row_for_csv(enriched_results[0]).keys()) if enriched_results else []
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in enriched_results:
            writer.writerow(_row_for_csv(item))

    return json_path, csv_path


def main():
    args = parse_args()
    input_dir = _resolve_path(args.input_dir)
    output_dir = _resolve_path(args.output_dir)

    chunk_jsons = _find_chunk_jsons(input_dir)
    if not chunk_jsons:
        raise FileNotFoundError("No chunk enriched JSON files were found.")

    merged_by_key = {}
    source_files = []
    source_search_json = None
    total_completed = 0

    for path in chunk_jsons:
        payload = json.loads(path.read_text())
        meta = payload.get("meta", {})
        source_files.append(path.name)
        if not source_search_json and meta.get("source_search_json"):
            source_search_json = meta["source_search_json"]
        total_completed += int(meta.get("completed_count") or len(payload.get("results", [])))
        for item in payload.get("results", []):
            key = str(item.get("listing_id") or item.get("listing_url") or "")
            if not key:
                continue
            merged_by_key[key] = item

    merged_results = sorted(
        merged_by_key.values(),
        key=lambda item: (
            item.get("source_page_index", 0),
            item.get("position_on_page") or 999,
            str(item.get("listing_id") or ""),
        ),
    )

    metadata = {
        "generated_at": datetime.now().isoformat(),
        "source_search_json": source_search_json,
        "merged_chunk_files": sorted(source_files),
        "chunk_file_count": len(chunk_jsons),
        "completed_count_total": total_completed,
        "results_count": len(merged_results),
    }

    json_path, csv_path = save_outputs(output_dir, merged_results, metadata)
    print(json_path)
    print(csv_path)


if __name__ == "__main__":
    main()
