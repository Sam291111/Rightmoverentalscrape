"""
Rightmove Dataset Cleaner
=========================

Normalises enriched Rightmove sale/rent outputs into a research-friendly schema.

What it does:
  - loads one or more enriched JSON datasets
  - standardises field names and value formats across sale and rent
  - preserves provenance back to the source file
  - deduplicates on market + listing_id/listing_url
  - writes cleaned JSON + CSV plus a small QC summary

Examples:
  python3 Scraper/clean_rightmove_dataset.py

  python3 Scraper/clean_rightmove_dataset.py \
    --input Scraper/output/rightmove_enriched_results_20260325_031755.json \
    --input Scraper/output/rightmove_rental_enriched_results_20260325_031735.json
"""

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
POSTCODE_RE = re.compile(
    r"(?i)\b(?:GIR\s?0AA|(?:[A-PR-UWYZ][A-HK-Y]?\d[A-HJKPSTUW]?|"
    r"[A-PR-UWYZ][A-HK-Y]?\d{2}|[A-PR-UWYZ][A-HK-Y]?\d[ABEHMNPRVWXY])"
    r"\s?\d[ABD-HJLNP-UW-Z]{2})\b"
)


def parse_args():
    parser = argparse.ArgumentParser(description="Clean and normalise Rightmove enriched datasets.")
    parser.add_argument(
        "--input",
        action="append",
        help="One or more enriched JSON datasets. If omitted, the latest sale and rental enriched datasets are used if available.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for cleaned outputs.",
    )
    return parser.parse_args()


def _normalise_space(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def _normalise_postcode(value):
    text = _normalise_space(value).upper().replace(" ", "")
    if not text:
        return None
    if len(text) <= 3:
        return text
    return f"{text[:-3]} {text[-3:]}"


def _first_valid_postcode(*values):
    for value in values:
        if not value:
            continue
        match = POSTCODE_RE.search(str(value))
        if match:
            return _normalise_postcode(match.group(0))
    return None


def _to_bool_or_none(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = _normalise_space(str(value)).lower()
    if text in {"true", "yes", "y", "1"}:
        return True
    if text in {"false", "no", "n", "0"}:
        return False
    return None


def _to_int_or_none(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    match = re.search(r"-?\d+", str(value))
    return int(match.group(0)) if match else None


def _to_float_or_none(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _title_or_none(value):
    text = _normalise_space(value)
    if not text:
        return None
    return text


def _normalise_tenure(value):
    text = _normalise_space(value)
    if not text:
        return None
    mapping = {
        "FREEHOLD": "Freehold",
        "LEASEHOLD": "Leasehold",
        "SHARE OF FREEHOLD": "Share of Freehold",
        "SHARE_OF_FREEHOLD": "Share of Freehold",
        "COMMONHOLD": "Commonhold",
    }
    upper = text.upper()
    return mapping.get(upper, text)


def _normalise_epc_rating(value):
    text = _normalise_space(value)
    if not text:
        return None
    match = re.search(r"\b([A-G])\b", text.upper())
    return match.group(1) if match else None


def _as_list(value):
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    return [value]


def _latest_file(pattern):
    candidates = sorted(DEFAULT_OUTPUT_DIR.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _resolve_inputs(input_args):
    if input_args:
        paths = []
        for item in input_args:
            path = Path(item)
            if not path.is_absolute():
                path = (SCRIPT_DIR.parent / path).resolve()
            if not path.exists():
                raise FileNotFoundError(f"Input dataset not found: {path}")
            paths.append(path)
        return paths

    paths = []
    latest_sale = _latest_file("rightmove_enriched_results_*.json")
    latest_rent = _latest_file("rightmove_rental_enriched_results_*.json")
    if latest_sale:
        paths.append(latest_sale)
    if latest_rent:
        paths.append(latest_rent)
    if not paths:
        raise FileNotFoundError("No enriched JSON datasets found. Pass --input or run the detail scraper first.")
    return paths


def _market_from_row(row, source_name):
    market = _normalise_space(row.get("market"))
    if market in {"sale", "rent"}:
        return market
    if "rental" in source_name.lower():
        return "rent"
    return "sale"


def _normalise_row(row, source_path):
    market = _market_from_row(row, source_path.name)
    listing_id = str(row.get("listing_id")) if row.get("listing_id") not in (None, "") else None
    postcode = _first_valid_postcode(row.get("postcode"), row.get("location"), row.get("display_address"))
    image_urls = [url for url in _as_list(row.get("image_urls")) if _normalise_space(url)]
    floorplan_urls = [url for url in _as_list(row.get("floorplan_urls")) if _normalise_space(url)]
    epc_urls = [url for url in _as_list(row.get("epc_urls")) if _normalise_space(url)]

    price_amount = row.get("price_amount") if market == "sale" else row.get("rent_amount")
    price_frequency = "total" if market == "sale" else _normalise_space(row.get("rent_frequency"))
    price_text = row.get("price_text") if market == "sale" else row.get("rent_text")

    return {
        "market": market,
        "listing_id": listing_id,
        "listing_url": _normalise_space(row.get("listing_url")),
        "source_file": source_path.name,
        "location": _normalise_space(row.get("location")),
        "display_address": _normalise_space(row.get("display_address")),
        "postcode": postcode,
        "property_type": _title_or_none(row.get("property_type")),
        "bedrooms": _to_int_or_none(row.get("bedrooms")),
        "bathrooms": _to_int_or_none(row.get("bathrooms")),
        "price_amount": _to_int_or_none(price_amount),
        "price_frequency": price_frequency,
        "price_text": _normalise_space(price_text),
        "price_qualifier": _normalise_space(row.get("price_qualifier")),
        "deposit_amount": row.get("deposit_amount"),
        "deposit_text": _normalise_space(row.get("deposit_text")),
        "tenure": _normalise_tenure(row.get("tenure")),
        "furnish_type": _title_or_none(row.get("furnish_type")),
        "let_type": _title_or_none(row.get("let_type")),
        "min_tenancy": _normalise_space(row.get("min_tenancy")),
        "let_available_date": _normalise_space(row.get("let_available_date")),
        "listing_status": _normalise_space(row.get("listing_status") or row.get("added_or_reduced")),
        "added_text": _normalise_space(row.get("added_text")),
        "featured": _to_bool_or_none(row.get("featured")),
        "auction": _to_bool_or_none(row.get("auction")),
        "new_home": _to_bool_or_none(row.get("new_home")),
        "build_to_rent": _to_bool_or_none(row.get("build_to_rent")),
        "online_viewings_available": _to_bool_or_none(row.get("online_viewings_available")),
        "students": _to_bool_or_none(row.get("students")),
        "student_friendly": _to_bool_or_none(row.get("student_friendly")),
        "student_text": _normalise_space(row.get("student_text")),
        "pets_text": _normalise_space(row.get("pets_text")),
        "bills_text": _normalise_space(row.get("bills_text")),
        "zero_deposit": _to_bool_or_none(row.get("zero_deposit")),
        "investment_opportunity": _to_bool_or_none(row.get("investment_opportunity")),
        "investment_text": _normalise_space(row.get("investment_text")),
        "luxury": _to_bool_or_none(row.get("luxury")),
        "luxury_text": _normalise_space(row.get("luxury_text")),
        "epc_rating": _normalise_epc_rating(row.get("epc_rating")),
        "council_tax": _normalise_space(row.get("council_tax")),
        "summary": _normalise_space(row.get("summary")),
        "description": _normalise_space(row.get("description")),
        "key_features_text": _normalise_space(row.get("key_features_text")),
        "latitude": _to_float_or_none(row.get("latitude")),
        "longitude": _to_float_or_none(row.get("longitude")),
        "search_latitude": _to_float_or_none(row.get("search_latitude")),
        "search_longitude": _to_float_or_none(row.get("search_longitude")),
        "detail_latitude": _to_float_or_none(row.get("detail_latitude")),
        "detail_longitude": _to_float_or_none(row.get("detail_longitude")),
        "coordinate_source": _normalise_space(row.get("coordinate_source")),
        "image_count": _to_int_or_none(row.get("image_count") or row.get("property_photo_count")),
        "floorplan_count": _to_int_or_none(row.get("floorplan_count")),
        "virtual_tour_count": _to_int_or_none(row.get("virtual_tour_count")),
        "image_url": _normalise_space(row.get("image_url") or (image_urls[0] if image_urls else None)),
        "image_urls": image_urls,
        "floorplan_urls": floorplan_urls,
        "epc_urls": epc_urls,
        "source_page_index": _to_int_or_none(row.get("source_page_index")),
        "position_on_page": _to_int_or_none(row.get("position_on_page")),
    }


def _dedupe_rows(rows):
    kept = {}
    duplicates = 0
    for row in rows:
        key = (row.get("market"), row.get("listing_id") or row.get("listing_url"))
        if not key[1]:
            key = (row.get("market"), f"no_key_{len(kept)}")
        if key in kept:
            duplicates += 1
            existing = kept[key]
            merged = dict(existing)
            for field, value in row.items():
                if value in (None, "", []):
                    continue
                if field in {"image_urls", "floorplan_urls", "epc_urls"}:
                    merged[field] = list(dict.fromkeys(merged.get(field, []) + value))
                    continue
                if merged.get(field) in (None, "", []):
                    merged[field] = value
            kept[key] = merged
        else:
            kept[key] = row
    return list(kept.values()), duplicates


def _coverage(rows, field):
    return sum(1 for row in rows if row.get(field) not in (None, "", []))


def _build_qc_summary(raw_count, cleaned_rows, duplicates_removed, input_files):
    fields_to_check = [
        "listing_id",
        "listing_url",
        "location",
        "price_amount",
        "price_frequency",
        "property_type",
        "bedrooms",
        "bathrooms",
        "postcode",
        "latitude",
        "longitude",
        "epc_rating",
        "investment_opportunity",
        "luxury",
    ]
    return {
        "generated_at": datetime.now().isoformat(),
        "input_files": [path.name for path in input_files],
        "rows_input": raw_count,
        "rows_output": len(cleaned_rows),
        "duplicates_removed": duplicates_removed,
        "market_counts": dict(Counter(row.get("market") for row in cleaned_rows)),
        "field_coverage": {
            field: {
                "count": _coverage(cleaned_rows, field),
                "fraction": (_coverage(cleaned_rows, field) / len(cleaned_rows)) if cleaned_rows else 0.0,
            }
            for field in fields_to_check
        },
    }


def save_outputs(output_dir, cleaned_rows, qc_summary):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    dataset = {
        "meta": qc_summary,
        "results": cleaned_rows,
    }

    json_path = output_path / f"rightmove_cleaned_dataset_{timestamp}.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(dataset, handle, indent=2, ensure_ascii=False)

    csv_path = output_path / f"rightmove_cleaned_dataset_{timestamp}.csv"
    fieldnames = [
        "market",
        "listing_id",
        "listing_url",
        "source_file",
        "location",
        "display_address",
        "postcode",
        "property_type",
        "bedrooms",
        "bathrooms",
        "price_amount",
        "price_frequency",
        "price_text",
        "price_qualifier",
        "deposit_amount",
        "deposit_text",
        "tenure",
        "furnish_type",
        "let_type",
        "min_tenancy",
        "let_available_date",
        "listing_status",
        "added_text",
        "featured",
        "auction",
        "new_home",
        "build_to_rent",
        "online_viewings_available",
        "students",
        "student_friendly",
        "student_text",
        "pets_text",
        "bills_text",
        "zero_deposit",
        "investment_opportunity",
        "investment_text",
        "luxury",
        "luxury_text",
        "epc_rating",
        "council_tax",
        "summary",
        "description",
        "key_features_text",
        "latitude",
        "longitude",
        "search_latitude",
        "search_longitude",
        "detail_latitude",
        "detail_longitude",
        "coordinate_source",
        "image_count",
        "floorplan_count",
        "virtual_tour_count",
        "image_url",
        "image_urls",
        "floorplan_urls",
        "epc_urls",
        "source_page_index",
        "position_on_page",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in cleaned_rows:
            csv_row = dict(row)
            csv_row["image_urls"] = json.dumps(csv_row.get("image_urls", []), ensure_ascii=False)
            csv_row["floorplan_urls"] = json.dumps(csv_row.get("floorplan_urls", []), ensure_ascii=False)
            csv_row["epc_urls"] = json.dumps(csv_row.get("epc_urls", []), ensure_ascii=False)
            writer.writerow({field: csv_row.get(field) for field in fieldnames})

    qc_path = output_path / f"rightmove_cleaned_dataset_qc_{timestamp}.json"
    with qc_path.open("w", encoding="utf-8") as handle:
        json.dump(qc_summary, handle, indent=2, ensure_ascii=False)

    return json_path, csv_path, qc_path


def main():
    args = parse_args()
    input_files = _resolve_inputs(args.input)

    raw_count = 0
    cleaned_rows = []
    for source_path in input_files:
        data = json.loads(source_path.read_text())
        rows = data.get("results", [])
        raw_count += len(rows)
        for row in rows:
            cleaned_rows.append(_normalise_row(row, source_path))

    cleaned_rows, duplicates_removed = _dedupe_rows(cleaned_rows)
    cleaned_rows = sorted(
        cleaned_rows,
        key=lambda row: (
            row.get("market") or "",
            row.get("location") or "",
            row.get("listing_id") or "",
        ),
    )

    qc_summary = _build_qc_summary(raw_count, cleaned_rows, duplicates_removed, input_files)
    json_path, csv_path, qc_path = save_outputs(args.output_dir, cleaned_rows, qc_summary)

    print("\nSaved cleaned dataset:")
    print(f"  JSON: {json_path}")
    print(f"  CSV:  {csv_path}")
    print(f"  QC:   {qc_path}")
    print(f"  Rows input: {raw_count}")
    print(f"  Rows output: {len(cleaned_rows)}")
    print(f"  Duplicates removed: {duplicates_removed}")


if __name__ == "__main__":
    main()
