# Rightmove Cleaning Guide

This document explains:
- what the cleaning script does
- how to run it
- what outputs it creates
- how the cleaned dataset is structured
- best practice for research use

## Main script

- `clean_rightmove_dataset.py`

## Purpose

The scraper outputs are rich, but they are still scraper-shaped.

The cleaner converts sale and rent enriched outputs into one standardised,
research-friendly dataset.

It does not overwrite scraper outputs.

That separation is deliberate and is best practice for research workflows.

## Recommended Command

Use the latest enriched sale and rental datasets automatically:

```bash
python3 Scraper/clean_rightmove_dataset.py
```

Use specific files:

```bash
python3 Scraper/clean_rightmove_dataset.py \
  --input Scraper/output/rightmove_enriched_results_YYYYMMDD_HHMMSS.json \
  --input Scraper/output/rightmove_rental_enriched_results_YYYYMMDD_HHMMSS.json
```

Choose a different output directory:

```bash
python3 Scraper/clean_rightmove_dataset.py \
  --output-dir Scraper/output/cleaned
```

## What It Does

The cleaner:
- loads one or more enriched JSON datasets
- standardises sale and rent into one common schema
- normalises labels and basic value formats
- preserves provenance using `source_file`
- deduplicates rows on `market + listing_id/listing_url`
- writes cleaned outputs plus a QC summary

## Output Files

It creates:
- `rightmove_cleaned_dataset_<timestamp>.json`
- `rightmove_cleaned_dataset_<timestamp>.csv`
- `rightmove_cleaned_dataset_qc_<timestamp>.json`

These are written to:
- `Scraper/output/` by default

### Cleaned JSON

Contains:
- `meta`
- `results`

This is the most complete cleaned version because list fields like `image_urls`
and `floorplan_urls` stay as real arrays.

### Cleaned CSV

Contains the same cleaned rows, but list fields are stored as JSON strings.

This is good for spreadsheets, quick inspection, and import into analysis tools.

### QC JSON

Contains:
- input files used
- row counts before and after deduplication
- duplicates removed
- market counts
- field coverage summary

This is important for documenting data quality in a research workflow.

## Cleaned Schema

Main fields include:

Identity / provenance:
- `market`
- `listing_id`
- `listing_url`
- `source_file`

Location:
- `location`
- `display_address`
- `postcode`
- `latitude`
- `longitude`

Property characteristics:
- `property_type`
- `bedrooms`
- `bathrooms`
- `tenure`

Price / rent:
- `price_amount`
- `price_frequency`
- `price_text`
- `price_qualifier`
- `deposit_amount`
- `deposit_text`

Rental-specific:
- `furnish_type`
- `let_type`
- `min_tenancy`
- `let_available_date`
- `build_to_rent`
- `students`
- `student_friendly`
- `student_text`
- `pets_text`
- `bills_text`
- `zero_deposit`

Marketing / labels:
- `listing_status`
- `added_text`
- `featured`
- `auction`
- `new_home`
- `investment_opportunity`
- `investment_text`
- `luxury`
- `luxury_text`

Energy / tax:
- `epc_rating`
- `council_tax`

Text:
- `summary`
- `description`
- `key_features_text`

Media:
- `image_url`
- `image_urls`
- `image_count`
- `floorplan_urls`
- `floorplan_count`
- `epc_urls`
- `virtual_tour_count`

Search provenance:
- `source_page_index`
- `position_on_page`

## Standardisation Rules

The cleaner currently does things like:
- infer `market` from the source dataset
- convert tenure values to cleaner display labels
- standardise booleans
- keep `price_frequency` as:
  - `total` for sale
  - `pcm` or `pw` for rent
- normalise `epc_rating` to a single letter where present
- preserve arrays for media fields in JSON output

## Best Practice For Research

This is the recommended workflow:

1. Keep scraper outputs unchanged.
2. Run the cleaner to make a derivative dataset.
3. Do analysis from the cleaned dataset.
4. Keep the QC summary with your project.
5. Document any extra manual exclusions or transformations separately.

Why this is better:
- raw acquisition stays reproducible
- cleaning decisions are transparent
- you can regenerate the cleaned dataset if scraper logic changes
- you do not lose provenance

## What Counts As Good Cleaning Practice Here

For this kind of web-collected housing data, good practice means:

- separate raw, enriched, and cleaned datasets
- never silently invent missing values
- keep original identifiers
- preserve source provenance
- document field coverage
- deduplicate explicitly
- keep sale and rent in a common schema where possible
- avoid baking analysis assumptions into the scraper itself

## Important Interpretation Notes

- Missing postcode is common and often correct.
- Missing EPC rating does not mean no EPC exists; it only means no clear text rating was extracted.
- Marketing-language flags like `investment_opportunity` or `luxury` are textual indicators, not formal property categories.
- `student_friendly`, `pets_text`, and `bills_text` only appear when the listing actually advertises those conditions.

## Suggested Next Step

If you plan to analyse or publish from this data, the next useful addition is a
small data dictionary / codebook describing each cleaned field in one sentence.
