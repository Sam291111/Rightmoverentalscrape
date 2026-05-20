"""
Build London Rental History Metrics
===================================

Scans preserved London-clipped Rightmove rental runs and builds historical,
Pages-friendly summary files for trend analysis.
"""

import argparse
import csv
import json
import math
import re
from datetime import datetime
from pathlib import Path

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
DEFAULT_HISTORY_DIR = DEFAULT_OUTPUT_DIR / "history"
DEFAULT_PAGES_DATA_DIR = SCRIPT_DIR.parent / "docs" / "data"
RUN_FILE_RE = re.compile(r"rightmove_cleaned_dataset_(\d{8}_\d{6})_london_clipped_(\d{8}_\d{6})\.json$")
SNAPSHOT_FILE_RE = re.compile(r"rightmove_london_listings_(\d{8}_\d{6})\.json$")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build historical London rental metrics from preserved clipped run files."
    )
    parser.add_argument(
        "--input-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory containing London-clipped run JSON files.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_HISTORY_DIR),
        help="Directory for historical metrics outputs.",
    )
    parser.add_argument(
        "--pages-data-dir",
        default=str(DEFAULT_PAGES_DATA_DIR),
        help="Directory for the GitHub Pages dashboard data bundle.",
    )
    return parser.parse_args()


def _resolve_path(path_arg):
    path = Path(path_arg)
    if not path.is_absolute():
        path = (SCRIPT_DIR.parent / path).resolve()
    return path


def _find_run_files(input_dir):
    input_dir = Path(input_dir)
    candidates = []
    seen = set()

    for path in sorted(input_dir.glob("rightmove_cleaned_dataset_*_london_clipped_*.json")):
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            candidates.append(path)

    archive_dir = input_dir / "archives" / "clipped"
    if archive_dir.exists():
        for path in sorted(archive_dir.glob("rightmove_cleaned_dataset_*_london_clipped_*.json")):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                candidates.append(path)

    return candidates


def _find_snapshot_files(snapshot_dir):
    snapshot_dir = Path(snapshot_dir)
    if not snapshot_dir.exists():
        return []
    return sorted(snapshot_dir.glob("rightmove_london_listings_*.json"))


def _parse_run_file(path):
    match = RUN_FILE_RE.match(path.name)
    if not match:
        return None
    cleaned_ts, clipped_ts = match.groups()
    return {
        "cleaned_timestamp": cleaned_ts,
        "run_timestamp": clipped_ts,
        "run_date": clipped_ts[:8],
        "source_run_file": path.name,
    }


def _parse_snapshot_file(path):
    match = SNAPSHOT_FILE_RE.match(path.name)
    if not match:
        return None
    run_timestamp = match.group(1)
    return {
        "run_timestamp": run_timestamp,
        "run_date": run_timestamp[:8],
        "snapshot_file": path.name,
    }


def _normalise_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _truthy(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "1"}:
        return True
    if text in {"false", "no", "n", "0"}:
        return False
    return None


def _label_bool(value, true_label, false_label, unknown_label="Unknown"):
    truthy = _truthy(value)
    if truthy is True:
        return true_label
    if truthy is False:
        return false_label
    return unknown_label


def _student_category(row):
    students = _truthy(row.get("students"))
    student_friendly = _truthy(row.get("student_friendly"))
    if students is True or student_friendly is True:
        return "Student suitable"
    if students is False or student_friendly is False:
        return "Not student suitable"
    return "Unknown"


def _price_reduced(row):
    status = (_normalise_text(row.get("listing_status")) or "").lower()
    added = (_normalise_text(row.get("added_text")) or "").lower()
    return "Price reduced" if "reduced" in status or "reduced" in added else "Not reduced"


def _deposit_category(row):
    zero_deposit = _truthy(row.get("zero_deposit"))
    deposit_amount = pd.to_numeric(pd.Series([row.get("deposit_amount")]), errors="coerce").iloc[0]
    deposit_text = _normalise_text(row.get("deposit_text"))
    if zero_deposit is True:
        return "Zero deposit"
    if pd.notna(deposit_amount) and float(deposit_amount) > 0:
        return "Deposit listed"
    if deposit_text:
        return "Deposit listed"
    return "Deposit not listed"


def _bedroom_category(value):
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "Unknown"
    numeric = int(numeric)
    if numeric == 0:
        return "Studio / 0 bed"
    if numeric >= 5:
        return "5+ bed"
    return f"{numeric} bed"


def _property_type(row):
    value = _normalise_text(row.get("property_type"))
    return value or "Unknown"


def _furnish_type(row):
    value = _normalise_text(row.get("furnish_type"))
    return value or "Unknown"


def _let_type(row):
    value = _normalise_text(row.get("let_type"))
    return value or "Unknown"


def _borough(row):
    value = _normalise_text(row.get("london_borough"))
    return value or "Unknown"


def _build_to_rent(row):
    return _label_bool(row.get("build_to_rent"), "Build to rent", "Not build to rent")


def _online_viewings_category(row):
    return _label_bool(
        row.get("online_viewings_available"),
        "Online viewing available",
        "No online viewing flag",
    )


def _pets_category(row):
    text = (_normalise_text(row.get("pets_text")) or "").lower()
    if not text:
        return "Unknown"
    if "no pets" in text:
        return "No pets"
    if "pet" in text:
        return "Pets mentioned"
    return "Unknown"


def _bills_category(row):
    text = (_normalise_text(row.get("bills_text")) or "").lower()
    if not text:
        return "Unknown"
    if "included" in text:
        return "Bills included"
    if "excluding" in text:
        return "Bills excluded"
    return "Bills mentioned"


def _luxury_category(row):
    return _label_bool(row.get("luxury"), "Luxury", "Not luxury")


def _investment_opportunity_category(row):
    return _label_bool(
        row.get("investment_opportunity"),
        "Investment opportunity",
        "Not investment opportunity",
    )


def _preferred_category(row, derived_field, fallback_fn):
    value = _normalise_text(row.get(derived_field))
    return value or fallback_fn(row)


def _listing_metrics(group_df):
    prices = pd.to_numeric(group_df["price_amount"], errors="coerce").dropna()
    deposits = pd.to_numeric(group_df["deposit_amount"], errors="coerce").dropna()
    return {
        "listing_count": int(len(group_df)),
        "median_price": float(prices.median()) if not prices.empty else None,
        "mean_price": float(prices.mean()) if not prices.empty else None,
        "min_price": float(prices.min()) if not prices.empty else None,
        "max_price": float(prices.max()) if not prices.empty else None,
        "median_deposit": float(deposits.median()) if not deposits.empty else None,
        "mean_deposit": float(deposits.mean()) if not deposits.empty else None,
        "deposit_listed_count": int(deposits.count()),
    }


def _run_summary(run_df):
    metrics = _listing_metrics(run_df)
    return {
        "run_timestamp": run_df["run_timestamp"].iloc[0],
        "run_date": run_df["run_date"].iloc[0],
        **metrics,
        "borough_count": int(run_df["london_borough"].nunique(dropna=True)),
        "build_to_rent_count": int((run_df["build_to_rent_category"] == "Build to rent").sum()),
        "student_suitable_count": int((run_df["student_category"] == "Student suitable").sum()),
        "price_reduced_count": int((run_df["price_reduced_category"] == "Price reduced").sum()),
    }


def _group_rows(df, group_fields, static_fields=None):
    static_fields = static_fields or []
    rows = []
    for group_keys, group_df in df.groupby(group_fields, dropna=False):
        if not isinstance(group_keys, tuple):
            group_keys = (group_keys,)
        row = {}
        for field_name, value in zip(group_fields, group_keys):
            row[field_name] = value
        for field_name in static_fields:
            row[field_name] = group_df[field_name].iloc[0]
        row.update(_listing_metrics(group_df))
        rows.append(row)
    return rows


def _load_run_rows(path):
    parsed = _parse_run_file(path)
    if not parsed:
        return []
    payload = json.loads(path.read_text())
    results = payload.get("results", [])
    rows = []
    for row in results:
        item = dict(row)
        item["source_run_file"] = path.name
        item["run_timestamp"] = parsed["run_timestamp"]
        item["run_date"] = parsed["run_date"]
        item["london_borough"] = _borough(item)
        item["build_to_rent_category"] = _preferred_category(item, "build_to_rent_category", _build_to_rent)
        item["student_category"] = _preferred_category(item, "student_category", _student_category)
        item["price_reduced_category"] = _preferred_category(item, "price_reduced_category", _price_reduced)
        item["deposit_category"] = _preferred_category(item, "deposit_category", _deposit_category)
        item["property_type_category"] = _preferred_category(item, "property_type_category", _property_type)
        item["furnish_type_category"] = _preferred_category(item, "furnish_type_category", _furnish_type)
        item["let_type_category"] = _preferred_category(item, "let_type_category", _let_type)
        item["bedroom_category"] = _preferred_category(
            item,
            "bedroom_category",
            lambda row: _bedroom_category(row.get("bedrooms")),
        )
        item["zero_deposit_category"] = _preferred_category(
            item,
            "zero_deposit_category",
            lambda row: _label_bool(row.get("zero_deposit"), "Zero deposit", "Not zero deposit"),
        )
        item["online_viewings_category"] = _preferred_category(
            item,
            "online_viewings_category",
            _online_viewings_category,
        )
        item["pets_category"] = _preferred_category(item, "pets_category", _pets_category)
        item["bills_category"] = _preferred_category(item, "bills_category", _bills_category)
        item["luxury_category"] = _preferred_category(item, "luxury_category", _luxury_category)
        item["investment_opportunity_category"] = _preferred_category(
            item,
            "investment_opportunity_category",
            _investment_opportunity_category,
        )
        rows.append(item)
    return rows


def _empty_run_summary(run_timestamp, run_date):
    return {
        "run_timestamp": run_timestamp,
        "run_date": run_date,
        "listing_count": 0,
        "median_price": None,
        "mean_price": None,
        "min_price": None,
        "max_price": None,
        "median_deposit": None,
        "mean_deposit": None,
        "deposit_listed_count": 0,
        "borough_count": 0,
        "build_to_rent_count": 0,
        "student_suitable_count": 0,
        "price_reduced_count": 0,
    }


def _history_payload(df, snapshot_records):
    run_summaries = []
    for record in snapshot_records:
        payload = record["payload"]
        rows = payload.get("rows", [])
        meta = payload.get("meta", {})
        run_timestamp = meta.get("run_timestamp") or record["run_timestamp"]
        run_date = meta.get("run_date") or record["run_date"]
        if rows:
            run_df = pd.DataFrame(rows)
            if "run_timestamp" not in run_df.columns:
                run_df["run_timestamp"] = run_timestamp
            if "run_date" not in run_df.columns:
                run_df["run_date"] = run_date
            run_summaries.append(_run_summary(run_df))
        else:
            run_summaries.append(_empty_run_summary(run_timestamp, run_date))
    run_summaries = sorted(run_summaries, key=lambda row: row["run_timestamp"])

    borough_stats = _group_rows(df, ["run_timestamp", "run_date", "london_borough"]) if not df.empty else []

    category_specs = [
        ("build_to_rent", "build_to_rent_category"),
        ("student_suitable", "student_category"),
        ("price_reduced", "price_reduced_category"),
        ("deposit", "deposit_category"),
        ("zero_deposit", "zero_deposit_category"),
        ("online_viewings", "online_viewings_category"),
        ("pets", "pets_category"),
        ("bills", "bills_category"),
        ("luxury", "luxury_category"),
        ("investment_opportunity", "investment_opportunity_category"),
        ("property_type", "property_type_category"),
        ("furnish_type", "furnish_type_category"),
        ("let_type", "let_type_category"),
        ("bedrooms", "bedroom_category"),
    ]

    category_stats = []
    borough_category_stats = []
    for dimension_name, column_name in category_specs:
        grouped = _group_rows(df, ["run_timestamp", "run_date", column_name]) if not df.empty else []
        for row in grouped:
            row["dimension"] = dimension_name
            row["value"] = row.pop(column_name)
            category_stats.append(row)

        grouped_borough = (
            _group_rows(df, ["run_timestamp", "run_date", "london_borough", column_name]) if not df.empty else []
        )
        for row in grouped_borough:
            row["dimension"] = dimension_name
            row["value"] = row.pop(column_name)
            borough_category_stats.append(row)

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "source_run_files": [
                record["payload"].get("meta", {}).get("source_run_file") or record.get("source_run_file")
                for record in snapshot_records
                if record["payload"].get("meta", {}).get("source_run_file") or record.get("source_run_file")
            ],
            "source_snapshot_files": [record["path"].name for record in snapshot_records],
            "run_count": len(snapshot_records),
            "listing_rows": int(len(df)),
            "dimensions": [spec[0] for spec in category_specs],
        },
        "runs": run_summaries,
        "borough_stats": sorted(
            borough_stats,
            key=lambda row: (row["run_timestamp"], -row["listing_count"], row["london_borough"]),
        ),
        "category_stats": sorted(
            category_stats,
            key=lambda row: (row["run_timestamp"], row["dimension"], -row["listing_count"], str(row["value"])),
        ),
        "borough_category_stats": sorted(
            borough_category_stats,
            key=lambda row: (
                row["run_timestamp"],
                row["london_borough"],
                row["dimension"],
                -row["listing_count"],
                str(row["value"]),
            ),
        ),
    }


def _write_json(path, payload, *, indent=2):
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=indent, ensure_ascii=False, separators=None if indent else (",", ":"))


def _write_csv(path, rows):
    fieldnames = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _empty_history(source_files):
    return {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "source_run_files": [path.name for path in sorted(source_files)],
            "source_snapshot_files": [],
            "run_count": len(source_files),
            "listing_rows": 0,
            "dimensions": [
                "build_to_rent",
                "student_suitable",
                "price_reduced",
                "deposit",
                "zero_deposit",
                "online_viewings",
                "pets",
                "bills",
                "luxury",
                "investment_opportunity",
                "property_type",
                "furnish_type",
                "let_type",
                "bedrooms",
            ],
        },
        "runs": [],
        "borough_stats": [],
        "category_stats": [],
        "borough_category_stats": [],
    }


def _json_value(value):
    if pd.isna(value):
        return None
    return value


def _listings_payload(df, *, meta_extra=None):
    listing_fields = [
        "run_timestamp",
        "run_date",
        "source_run_file",
        "listing_id",
        "listing_url",
        "display_address",
        "location",
        "postcode",
        "london_borough",
        "price_amount",
        "price_frequency",
        "price_text",
        "property_type",
        "property_type_category",
        "bedrooms",
        "bedroom_category",
        "bathrooms",
        "deposit_amount",
        "deposit_text",
        "deposit_category",
        "furnish_type",
        "furnish_type_category",
        "let_type",
        "let_type_category",
        "build_to_rent_category",
        "student_category",
        "price_reduced_category",
        "zero_deposit_category",
        "online_viewings_category",
        "pets_category",
        "bills_category",
        "luxury_category",
        "investment_opportunity_category",
        "epc_rating",
        "latitude",
        "longitude",
    ]

    rows = []
    for record in df[listing_fields].to_dict(orient="records"):
        rows.append({key: _json_value(value) for key, value in record.items()})

    dimension_values = {
        "property_type": sorted({str(value) for value in df["property_type_category"].dropna().tolist() if str(value).strip()}),
        "bedrooms": sorted({str(value) for value in df["bedroom_category"].dropna().tolist() if str(value).strip()}),
        "build_to_rent": sorted({str(value) for value in df["build_to_rent_category"].dropna().tolist() if str(value).strip()}),
        "student_suitable": sorted({str(value) for value in df["student_category"].dropna().tolist() if str(value).strip()}),
        "price_reduced": sorted({str(value) for value in df["price_reduced_category"].dropna().tolist() if str(value).strip()}),
        "deposit": sorted({str(value) for value in df["deposit_category"].dropna().tolist() if str(value).strip()}),
        "zero_deposit": sorted({str(value) for value in df["zero_deposit_category"].dropna().tolist() if str(value).strip()}),
        "online_viewings": sorted({str(value) for value in df["online_viewings_category"].dropna().tolist() if str(value).strip()}),
        "pets": sorted({str(value) for value in df["pets_category"].dropna().tolist() if str(value).strip()}),
        "bills": sorted({str(value) for value in df["bills_category"].dropna().tolist() if str(value).strip()}),
        "luxury": sorted({str(value) for value in df["luxury_category"].dropna().tolist() if str(value).strip()}),
        "investment_opportunity": sorted({str(value) for value in df["investment_opportunity_category"].dropna().tolist() if str(value).strip()}),
        "furnish_type": sorted({str(value) for value in df["furnish_type_category"].dropna().tolist() if str(value).strip()}),
        "let_type": sorted({str(value) for value in df["let_type_category"].dropna().tolist() if str(value).strip()}),
    }

    meta = {
        "generated_at": datetime.now().isoformat(),
        "row_count": len(rows),
    }
    if meta_extra:
        meta.update(meta_extra)

    return {
        "meta": meta,
        "filters": {
            "runs": [
                {
                    "run_timestamp": row["run_timestamp"],
                    "label": row["run_timestamp"],
                }
                for row in sorted(df[["run_timestamp"]].drop_duplicates().to_dict(orient="records"), key=lambda item: item["run_timestamp"])
            ],
            "boroughs": sorted({str(value) for value in df["london_borough"].dropna().tolist() if str(value).strip() and str(value) != "Unknown"}),
            "dimensions": list(dimension_values.keys()),
            "dimension_values": dimension_values,
        },
        "rows": rows,
    }


def _dashboard_payload(history):
    runs = history["runs"]
    borough_stats = history["borough_stats"]
    category_stats = history["category_stats"]
    borough_category_stats = history["borough_category_stats"]
    latest_run = runs[-1] if runs else None
    latest_run_timestamp = latest_run["run_timestamp"] if latest_run else None

    latest_borough_stats = [
        row for row in borough_stats if row["run_timestamp"] == latest_run_timestamp
    ]
    latest_category_stats = [
        row for row in category_stats if row["run_timestamp"] == latest_run_timestamp
    ]
    latest_borough_category_stats = [
        row for row in borough_category_stats if row["run_timestamp"] == latest_run_timestamp
    ]

    dimension_values = {}
    for row in category_stats:
        dimension_values.setdefault(row["dimension"], set()).add(str(row["value"]))

    return {
        "meta": {
            **history["meta"],
            "latest_run_timestamp": latest_run_timestamp,
            "latest_run_date": latest_run["run_date"] if latest_run else None,
        },
        "overview": {
            "latest_run": latest_run,
            "borough_count": len({row["london_borough"] for row in latest_borough_stats}),
            "category_row_count": len(latest_category_stats),
        },
        "filters": {
            "boroughs": sorted(
                {row["london_borough"] for row in borough_stats if row["london_borough"] != "Unknown"}
            ),
            "dimensions": list(history["meta"]["dimensions"]),
            "dimension_values": {
                dimension: sorted(values) for dimension, values in dimension_values.items()
            },
        },
        "series": {
            "runs": runs,
            "borough_stats": borough_stats,
            "category_stats": category_stats,
            "borough_category_stats": borough_category_stats,
        },
        "latest": {
            "borough_stats": latest_borough_stats,
            "category_stats": latest_category_stats,
            "borough_category_stats": latest_borough_category_stats,
        },
    }


def _empty_listings_payload(*, meta_extra=None):
    meta = {
        "generated_at": datetime.now().isoformat(),
        "row_count": 0,
    }
    if meta_extra:
        meta.update(meta_extra)

    return {
        "meta": meta,
        "filters": {
            "runs": [],
            "boroughs": [],
            "dimensions": [
                "property_type",
                "bedrooms",
                "build_to_rent",
                "student_suitable",
                "price_reduced",
                "deposit",
                "zero_deposit",
                "online_viewings",
                "pets",
                "bills",
                "luxury",
                "investment_opportunity",
                "furnish_type",
                "let_type",
            ],
            "dimension_values": {},
        },
        "rows": [],
    }


def _snapshot_path(snapshot_dir, run_timestamp):
    return snapshot_dir / f"rightmove_london_listings_{run_timestamp}.json"


def _build_snapshot_payload(run_file):
    parsed = _parse_run_file(run_file)
    if not parsed:
        return None
    rows = _load_run_rows(run_file)
    meta_extra = {
        "run_timestamp": parsed["run_timestamp"],
        "run_date": parsed["run_date"],
        "source_run_file": parsed["source_run_file"],
    }
    if not rows:
        return _empty_listings_payload(meta_extra=meta_extra)
    df = pd.DataFrame(rows)
    return _listings_payload(df, meta_extra=meta_extra)


def _load_snapshot_record(path):
    payload = json.loads(path.read_text())
    parsed = _parse_snapshot_file(path) or {}
    meta = payload.get("meta", {})
    run_timestamp = meta.get("run_timestamp") or parsed.get("run_timestamp")
    run_date = meta.get("run_date") or parsed.get("run_date") or (run_timestamp[:8] if run_timestamp else None)
    source_run_file = meta.get("source_run_file")
    return {
        "path": path,
        "payload": payload,
        "run_timestamp": run_timestamp,
        "run_date": run_date,
        "source_run_file": source_run_file,
    }


def main():
    args = parse_args()
    input_dir = _resolve_path(args.input_dir)
    output_dir = _resolve_path(args.output_dir)
    pages_data_dir = _resolve_path(args.pages_data_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pages_data_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir = output_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    run_files = _find_run_files(input_dir)
    existing_snapshots = {
        record["run_timestamp"]: record
        for record in (_load_snapshot_record(path) for path in _find_snapshot_files(snapshot_dir))
        if record["run_timestamp"]
    }

    for run_file in run_files:
        parsed = _parse_run_file(run_file)
        if not parsed:
            continue
        run_timestamp = parsed["run_timestamp"]
        if run_timestamp in existing_snapshots:
            continue
        snapshot_payload = _build_snapshot_payload(run_file)
        if snapshot_payload is None:
            continue
        snapshot_path = _snapshot_path(snapshot_dir, run_timestamp)
        _write_json(snapshot_path, snapshot_payload, indent=None)
        existing_snapshots[run_timestamp] = _load_snapshot_record(snapshot_path)

    snapshot_records = sorted(existing_snapshots.values(), key=lambda record: record["run_timestamp"])
    all_rows = []
    for record in snapshot_records:
        all_rows.extend(record["payload"].get("rows", []))

    json_path = output_dir / "rightmove_london_history_metrics.json"
    runs_csv = output_dir / "rightmove_london_runs.csv"
    borough_csv = output_dir / "rightmove_london_borough_stats.csv"
    category_csv = output_dir / "rightmove_london_category_stats.csv"
    borough_category_csv = output_dir / "rightmove_london_borough_category_stats.csv"
    dashboard_json = pages_data_dir / "dashboard.json"
    listings_json = pages_data_dir / "listings.json"

    if not run_files and not snapshot_records:
        raise FileNotFoundError("No London-clipped cleaned run files or listing snapshots were found.")

    if not snapshot_records:
        history = _empty_history(run_files)
        _write_json(json_path, history)
        _write_csv(runs_csv, history["runs"])
        _write_csv(borough_csv, history["borough_stats"])
        _write_csv(category_csv, history["category_stats"])
        _write_csv(borough_category_csv, history["borough_category_stats"])
        _write_json(dashboard_json, _dashboard_payload(history))
        _write_json(listings_json, _empty_listings_payload(), indent=None)

        print("\nSaved London history outputs:")
        print(f"  JSON: {json_path}")
        print(f"  Runs CSV: {runs_csv}")
        print(f"  Borough CSV: {borough_csv}")
        print(f"  Category CSV: {category_csv}")
        print(f"  Borough+category CSV: {borough_category_csv}")
        print(f"  Pages JSON: {dashboard_json}")
        print(f"  Pages listings JSON: {listings_json}")
        print(f"  Runs: {history['meta']['run_count']}")
        print("  Listing rows: 0")
        return

    df = pd.DataFrame(all_rows)
    history = _history_payload(df, snapshot_records)
    latest_snapshot = snapshot_records[-1]["payload"]

    _write_json(json_path, history)
    _write_csv(runs_csv, history["runs"])
    _write_csv(borough_csv, history["borough_stats"])
    _write_csv(category_csv, history["category_stats"])
    _write_csv(borough_category_csv, history["borough_category_stats"])
    _write_json(dashboard_json, _dashboard_payload(history))
    _write_json(listings_json, latest_snapshot, indent=None)

    print("\nSaved London history outputs:")
    print(f"  JSON: {json_path}")
    print(f"  Runs CSV: {runs_csv}")
    print(f"  Borough CSV: {borough_csv}")
    print(f"  Category CSV: {category_csv}")
    print(f"  Borough+category CSV: {borough_category_csv}")
    print(f"  Pages JSON: {dashboard_json}")
    print(f"  Pages listings JSON: {listings_json}")
    print(f"  Snapshot dir: {snapshot_dir}")
    print(f"  Runs: {history['meta']['run_count']}")
    print(f"  Listing rows: {history['meta']['listing_rows']}")


if __name__ == "__main__":
    main()
