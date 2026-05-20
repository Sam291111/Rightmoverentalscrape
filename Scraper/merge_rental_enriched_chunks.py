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
    parser.add_argument(
        "--search-results-json",
        help="Optional merged search-results JSON used to create search-only fallback rows for missing detail chunks.",
    )
    parser.add_argument(
        "--chunk-manifest-json",
        help="Optional detail chunk manifest JSON used to detect missing chunk artifacts.",
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


def _csv_fieldnames(enriched_results):
    if not enriched_results:
        return []

    fieldnames = list(_row_for_csv(enriched_results[0]).keys())
    seen = set(fieldnames)
    for item in enriched_results[1:]:
        for key in _row_for_csv(item).keys():
            if key in seen:
                continue
            fieldnames.append(key)
            seen.add(key)
    return fieldnames


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
        "detail_extraction_failed": item.get("detail_extraction_failed"),
        "detail_fallback_mode": item.get("detail_fallback_mode"),
        "detail_failure_type": item.get("detail_failure_type"),
        "detail_failure_error": item.get("detail_failure_error"),
        "detail_failure_http_status": item.get("detail_failure_http_status"),
        "detail_failure_browser_error_code": item.get("detail_failure_browser_error_code"),
        "detail_failure_page_title": item.get("detail_failure_page_title"),
        "detail_failure_current_url": item.get("detail_failure_current_url"),
        "missing_detail_chunk_label": item.get("missing_detail_chunk_label"),
        "added_text": item.get("added_text"),
        "description": item.get("description"),
        "key_features_text": item.get("key_features_text"),
        "image_urls": json.dumps(item.get("image_urls", []), ensure_ascii=False),
        "floorplan_urls": json.dumps(item.get("floorplan_urls", []), ensure_ascii=False),
        "epc_urls": json.dumps(item.get("epc_urls", []), ensure_ascii=False),
    }


def _search_only_fallback_row(search_row, *, reason, chunk_label):
    merged = dict(search_row)
    merged["search_summary"] = search_row.get("summary")
    merged["search_image_urls"] = search_row.get("image_urls", [])
    merged["search_location"] = search_row.get("location")
    merged["search_postcode"] = search_row.get("postcode")
    merged["search_latitude"] = search_row.get("latitude")
    merged["search_longitude"] = search_row.get("longitude")
    merged["detail_latitude"] = None
    merged["detail_longitude"] = None
    merged["detail_extraction_failed"] = True
    merged["detail_fallback_mode"] = "search_only_missing_chunk"
    merged["detail_failure_type"] = "detail_chunk_missing"
    merged["detail_failure_error"] = reason
    merged["detail_failure_http_status"] = None
    merged["detail_failure_browser_error_code"] = None
    merged["detail_failure_page_title"] = None
    merged["detail_failure_current_url"] = merged.get("listing_url")
    merged["missing_detail_chunk_label"] = chunk_label
    if search_row.get("latitude") not in (None, "") and search_row.get("longitude") not in (None, ""):
        merged["latitude"] = search_row.get("latitude")
        merged["longitude"] = search_row.get("longitude")
        merged["coordinate_source"] = "search_api"
    else:
        merged["latitude"] = search_row.get("latitude")
        merged["longitude"] = search_row.get("longitude")
        merged["coordinate_source"] = "missing"
    return merged


def _load_search_results(path_arg):
    if not path_arg:
        return []
    path = _resolve_path(path_arg)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("results", [])


def _load_chunk_manifest(path_arg):
    if not path_arg:
        return []
    path = _resolve_path(path_arg)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("include", [])


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
    fieldnames = _csv_fieldnames(enriched_results)
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
    search_results = _load_search_results(args.search_results_json)
    chunk_manifest = _load_chunk_manifest(args.chunk_manifest_json)

    chunk_jsons = _find_chunk_jsons(input_dir)

    merged_by_key = {}
    source_files = []
    source_search_json = None
    total_completed = 0
    completed_chunk_labels = set()

    for path in chunk_jsons:
        payload = json.loads(path.read_text())
        meta = payload.get("meta", {})
        source_files.append(path.name)
        try:
            relative_parts = path.relative_to(input_dir).parts
            if relative_parts:
                completed_chunk_labels.add(relative_parts[0].replace("detail-", "", 1))
        except ValueError:
            pass
        if not source_search_json and meta.get("source_search_json"):
            source_search_json = meta["source_search_json"]
        total_completed += int(meta.get("completed_count") or len(payload.get("results", [])))
        for item in payload.get("results", []):
            key = str(item.get("listing_id") or item.get("listing_url") or "")
            if not key:
                continue
            merged_by_key[key] = item

    missing_chunk_labels = []
    search_only_fallback_count = 0
    if chunk_manifest and search_results:
        for chunk in chunk_manifest:
            chunk_label = chunk.get("chunk_label")
            if not chunk_label or chunk_label in completed_chunk_labels:
                continue
            missing_chunk_labels.append(chunk_label)
            start_index = int(chunk.get("start_index") or 0)
            end_index = int(chunk.get("end_index") or start_index)
            for row in search_results[start_index:end_index]:
                key = str(row.get("listing_id") or row.get("listing_url") or "")
                if not key or key in merged_by_key:
                    continue
                merged_by_key[key] = _search_only_fallback_row(
                    row,
                    reason=f"detail chunk artifact missing for {chunk_label}",
                    chunk_label=chunk_label,
                )
                search_only_fallback_count += 1

    if not chunk_jsons and not search_only_fallback_count:
        if not args.search_results_json and not args.chunk_manifest_json:
            raise FileNotFoundError("No chunk enriched JSON files were found.")

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
        "expected_chunk_count": len(chunk_manifest),
        "missing_chunk_labels": missing_chunk_labels,
        "search_only_fallback_count": sum(
            1 for item in merged_results if item.get("detail_fallback_mode")
        ),
        "results_count": len(merged_results),
    }

    json_path, csv_path = save_outputs(output_dir, merged_results, metadata)
    print(json_path)
    print(csv_path)


if __name__ == "__main__":
    main()
