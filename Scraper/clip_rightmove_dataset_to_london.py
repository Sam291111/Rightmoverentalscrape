"""
Clip Rightmove datasets to Greater London
=========================================

Filters a Rightmove JSON dataset to rows whose coordinates fall inside the
Greater London Authority boundary, then optionally tags each retained row with
its London borough.

This script is intentionally derivative-only: it never overwrites the source
dataset.
"""

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
DEFAULT_BOUNDARY_PATH = SCRIPT_DIR.parent / "London Boundary" / "gla" / "London_GLA_Boundary.shp"
DEFAULT_BOROUGHS_PATH = (
    SCRIPT_DIR.parent
    / "London Boundary"
    / "statistical-gis-boundaries-london"
    / "ESRI"
    / "London_Borough_Excluding_MHW.shp"
)


def parse_args():
    parser = argparse.ArgumentParser(description="Clip a Rightmove dataset to Greater London using listing coordinates.")
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a Rightmove JSON dataset with a top-level 'results' array.",
    )
    parser.add_argument(
        "--boundary-path",
        default=str(DEFAULT_BOUNDARY_PATH),
        help="Path to the Greater London boundary shapefile.",
    )
    parser.add_argument(
        "--boroughs-path",
        default=str(DEFAULT_BOROUGHS_PATH),
        help="Optional path to a London borough shapefile.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for clipped outputs.",
    )
    return parser.parse_args()


def _resolve_path(path_arg):
    path = Path(path_arg)
    if not path.is_absolute():
        path = (SCRIPT_DIR.parent / path).resolve()
    return path


def _load_dataset(path):
    payload = json.loads(Path(path).read_text())
    if isinstance(payload, dict) and "results" in payload:
        return payload
    if isinstance(payload, list):
        return {"meta": {}, "results": payload}
    raise ValueError("Expected a JSON dataset with a top-level 'results' array.")


def _to_numeric(series):
    return pd.to_numeric(series, errors="coerce")


def _normalise_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _truthy(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if pd.isna(value):
            return None
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


def _price_reduced_category(row):
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


def _normalised_value_or_unknown(value):
    return _normalise_text(value) or "Unknown"


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


def _add_derived_tags(rows_df):
    work = rows_df.copy()
    derived_rows = []
    for row in work.to_dict(orient="records"):
        item = dict(row)
        item["build_to_rent_category"] = _label_bool(
            item.get("build_to_rent"),
            "Build to rent",
            "Not build to rent",
        )
        item["student_category"] = _student_category(item)
        item["price_reduced_category"] = _price_reduced_category(item)
        item["deposit_category"] = _deposit_category(item)
        item["property_type_category"] = _normalised_value_or_unknown(item.get("property_type"))
        item["furnish_type_category"] = _normalised_value_or_unknown(item.get("furnish_type"))
        item["let_type_category"] = _normalised_value_or_unknown(item.get("let_type"))
        item["bedroom_category"] = _bedroom_category(item.get("bedrooms"))
        item["zero_deposit_category"] = _label_bool(
            item.get("zero_deposit"),
            "Zero deposit",
            "Not zero deposit",
        )
        item["online_viewings_category"] = _label_bool(
            item.get("online_viewings_available"),
            "Online viewing available",
            "No online viewing flag",
        )
        item["pets_category"] = _pets_category(item)
        item["bills_category"] = _bills_category(item)
        item["luxury_category"] = _luxury_category(item)
        item["investment_opportunity_category"] = _investment_opportunity_category(item)
        derived_rows.append(item)
    return pd.DataFrame(derived_rows)


def _point_gdf(rows_df):
    work = rows_df.copy()
    work["latitude"] = _to_numeric(work.get("latitude"))
    work["longitude"] = _to_numeric(work.get("longitude"))
    return gpd.GeoDataFrame(
        work,
        geometry=gpd.points_from_xy(work["longitude"], work["latitude"]),
        crs="EPSG:4326",
    )


def _clip_to_boundary(rows_df, boundary_path, boroughs_path):
    boundary = gpd.read_file(boundary_path)
    if boundary.empty:
        raise RuntimeError(f"Boundary file has no features: {boundary_path}")

    boundary_name = str(boundary.iloc[0].get("NAME") or "Greater London")
    boundary_geom = boundary.dissolve()

    coordinate_rows = rows_df[
        rows_df["latitude"].notna() & rows_df["longitude"].notna()
    ].copy()
    if coordinate_rows.empty:
        return {
            "boundary_name": boundary_name,
            "rows_with_coordinates": 0,
            "inside_rows": pd.DataFrame(columns=list(rows_df.columns)),
            "outside_rows": pd.DataFrame(columns=list(rows_df.columns)),
        }

    point_gdf = _point_gdf(coordinate_rows).to_crs(boundary_geom.crs)
    inside = gpd.sjoin(point_gdf, boundary_geom[["geometry"]], how="inner", predicate="intersects")
    inside = inside.drop(columns=["index_right"], errors="ignore").copy()
    inside["in_london_boundary"] = True
    inside["london_boundary_name"] = boundary_name
    inside["clip_method"] = "coordinate"

    outside_indexes = coordinate_rows.index.difference(inside.index)
    outside = coordinate_rows.loc[outside_indexes].copy()

    boroughs_resolved = _resolve_path(boroughs_path) if boroughs_path else None
    if boroughs_resolved and boroughs_resolved.exists() and not inside.empty:
        boroughs = gpd.read_file(boroughs_resolved).to_crs(boundary_geom.crs)
        borough_columns = [column for column in ("NAME", "GSS_CODE") if column in boroughs.columns]
        borough_join = gpd.sjoin(
            inside,
            boroughs[borough_columns + ["geometry"]],
            how="left",
            predicate="intersects",
        )
        borough_join = borough_join.drop(columns=["index_right"], errors="ignore")
        if "NAME" in borough_join.columns:
            borough_join = borough_join.rename(columns={"NAME": "london_borough"})
        if "GSS_CODE" in borough_join.columns:
            borough_join = borough_join.rename(columns={"GSS_CODE": "london_borough_gss_code"})
        inside = borough_join

    inside = inside.to_crs("EPSG:4326")
    if "geometry" in inside.columns:
        inside = inside.drop(columns=["geometry"])

    return {
        "boundary_name": boundary_name,
        "rows_with_coordinates": len(coordinate_rows),
        "inside_rows": inside,
        "outside_rows": outside,
    }


def _price_series(rows_df):
    if "price_amount" in rows_df.columns:
        return _to_numeric(rows_df["price_amount"])
    if "rent_amount" in rows_df.columns:
        return _to_numeric(rows_df["rent_amount"])
    return pd.Series(dtype="float64")


def _borough_summary(rows_df):
    if "london_borough" not in rows_df.columns:
        return []

    summary_rows = []
    for borough_name, borough_df in rows_df.groupby("london_borough", dropna=True):
        prices = _price_series(borough_df).dropna()
        summary_rows.append(
            {
                "london_borough": borough_name,
                "count": int(len(borough_df)),
                "median_price": float(prices.median()) if not prices.empty else None,
                "mean_price": float(prices.mean()) if not prices.empty else None,
                "min_price": float(prices.min()) if not prices.empty else None,
                "max_price": float(prices.max()) if not prices.empty else None,
            }
        )
    return sorted(summary_rows, key=lambda item: (-item["count"], item["london_borough"]))


def _build_summary(source_payload, source_path, clipped_rows, outside_rows, rows_with_coordinates, boundary_name):
    prices = _price_series(clipped_rows).dropna()
    rows_in = len(clipped_rows)
    rows_total = len(source_payload.get("results", []))
    rows_missing_coordinates = rows_total - rows_with_coordinates
    return {
        "generated_at": datetime.now().isoformat(),
        "source_dataset": source_path.name,
        "source_meta": source_payload.get("meta", {}),
        "boundary_name": boundary_name,
        "rows_input": rows_total,
        "rows_with_coordinates": rows_with_coordinates,
        "rows_missing_coordinates": rows_missing_coordinates,
        "rows_inside_london": rows_in,
        "rows_outside_london": int(len(outside_rows)),
        "market_counts": dict(Counter(str(value) for value in clipped_rows.get("market", pd.Series(dtype="object")).fillna(""))),
        "borough_counts": dict(
            Counter(
                str(value)
                for value in clipped_rows.get("london_borough", pd.Series(dtype="object")).fillna("")
                if str(value).strip()
            )
        ),
        "price_summary": {
            "count": int(prices.count()),
            "median": float(prices.median()) if not prices.empty else None,
            "mean": float(prices.mean()) if not prices.empty else None,
            "min": float(prices.min()) if not prices.empty else None,
            "max": float(prices.max()) if not prices.empty else None,
        },
        "borough_summary": _borough_summary(clipped_rows),
    }


def _row_to_csv_dict(row, fieldnames):
    csv_row = {}
    for field in fieldnames:
        value = row.get(field)
        if isinstance(value, list):
            csv_row[field] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, dict):
            csv_row[field] = json.dumps(value, ensure_ascii=False)
        else:
            csv_row[field] = value
    return csv_row


def save_outputs(output_dir, source_path, clipped_payload, summary):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    stem = source_path.stem
    json_path = output_path / f"{stem}_london_clipped_{timestamp}.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(clipped_payload, handle, indent=2, ensure_ascii=False)

    csv_path = output_path / f"{stem}_london_clipped_{timestamp}.csv"
    rows = clipped_payload["results"]
    fieldnames = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(_row_to_csv_dict(row, fieldnames))

    summary_path = output_path / f"{stem}_london_summary_{timestamp}.json"
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    return json_path, csv_path, summary_path


def main():
    args = parse_args()
    input_path = _resolve_path(args.input)
    boundary_path = _resolve_path(args.boundary_path)
    boroughs_path = _resolve_path(args.boroughs_path) if args.boroughs_path else None

    source_payload = _load_dataset(input_path)
    rows_df = pd.DataFrame(source_payload.get("results", []))
    if rows_df.empty:
        raise RuntimeError("Source dataset has no rows to clip.")

    clipped = _clip_to_boundary(rows_df, boundary_path, boroughs_path)
    inside_rows = _add_derived_tags(clipped["inside_rows"]).replace({pd.NA: None})
    clipped_payload = {
        "meta": {
            **source_payload.get("meta", {}),
            "london_clip": {
                "generated_at": datetime.now().isoformat(),
                "boundary_path": str(boundary_path),
                "boroughs_path": str(boroughs_path) if boroughs_path else None,
                "boundary_name": clipped["boundary_name"],
                "rows_with_coordinates": clipped["rows_with_coordinates"],
                "rows_inside_london": int(len(inside_rows)),
            },
        },
        "results": inside_rows.where(pd.notna(inside_rows), None).to_dict(orient="records"),
    }
    summary = _build_summary(
        source_payload,
        input_path,
        inside_rows,
        clipped["outside_rows"],
        clipped["rows_with_coordinates"],
        clipped["boundary_name"],
    )
    json_path, csv_path, summary_path = save_outputs(args.output_dir, input_path, clipped_payload, summary)

    print("\nSaved London-clipped outputs:")
    print(f"  JSON:    {json_path}")
    print(f"  CSV:     {csv_path}")
    print(f"  Summary: {summary_path}")
    print(f"  Rows kept: {summary['rows_inside_london']} / {summary['rows_input']}")
    print(f"  Rows missing coordinates: {summary['rows_missing_coordinates']}")


if __name__ == "__main__":
    main()
