# Rightmove Scraper Guide

This document explains:
- what the scraper toolkit does
- how to run it
- what files it produces
- what the outputs mean
- where the main fields come from

## Overview

The toolkit has two production pipelines:
- sale
- rent

Each pipeline has two stages:
1. search-stage scraping
2. detail-stage enrichment

Use the shared wrapper to run both stages in sequence:

```bash
python3 Scraper/run_rightmove_pipeline.py --market sale --search-url "YOUR_SALES_URL"
python3 Scraper/run_rightmove_pipeline.py --market rent --search-url "YOUR_RENTAL_URL"
```

## Main scripts

Production scripts:
- `run_rightmove_pipeline.py`
- `rightmove_search_scraper.py`
- `rightmove_detail_scraper.py`
- `rightmove_rental_search_scraper.py`
- `rightmove_rental_detail_scraper.py`

Recon/probe scripts are in:
- `Scraper/recon/`

Those are optional and are not needed for normal scraping runs.

## How It Works

### Search stage

The search stage is API-first.

It:
- opens a visible Chrome session
- lets you deal with cookies/CAPTCHA manually
- loads a Rightmove search page
- calls the first-party `listing/search` API
- compares candidate API payloads against the visible DOM cards
- chooses the best-matching API result
- merges API data with DOM-only card fields where useful

This is done separately for:
- sales in `rightmove_search_scraper.py`
- rentals in `rightmove_rental_search_scraper.py`

### Detail stage

The detail stage is DOM/page-source driven.

It:
- visits each listing page
- extracts media URLs
- extracts description and key features
- extracts structured facts from visible property content
- enriches the original search-stage row
- checkpoints progress after each listing

This is done separately for:
- sales in `rightmove_detail_scraper.py`
- rentals in `rightmove_rental_detail_scraper.py`

## Recommended Commands

### Sales

Fresh run:

```bash
python3 Scraper/run_rightmove_pipeline.py \
  --market sale \
  --search-url "YOUR_RIGHTMOVE_SALES_SEARCH_URL" \
  --pages 42 \
  --max-results 1000 \
  --detail-run-dir london_sale_01
```

Resume detail:

```bash
python3 Scraper/run_rightmove_pipeline.py \
  --market sale \
  --skip-search \
  --search-results-json Scraper/output/rightmove_search_results_YYYYMMDD_HHMMSS.json \
  --detail-run-dir london_sale_01 \
  --resume-detail
```

### Rentals

Fresh run:

```bash
python3 Scraper/run_rightmove_pipeline.py \
  --market rent \
  --search-url "YOUR_RIGHTMOVE_RENTAL_SEARCH_URL" \
  --pages 42 \
  --max-results 1000 \
  --detail-run-dir london_rent_01
```

Unattended run:

```bash
python3 Scraper/run_rightmove_pipeline.py \
  --market rent \
  --search-url "YOUR_RIGHTMOVE_RENTAL_SEARCH_URL" \
  --pages 42 \
  --max-results 1000 \
  --no-interactive \
  --headless \
  --user-data-dir Scraper/.browser_profiles/rightmove_rent
```

London run with built-in clipping:

```bash
python3 Scraper/run_london_rental_pipeline.py \
  --pages 42 \
  --max-results 1000
```

Resume detail:

```bash
python3 Scraper/run_rightmove_pipeline.py \
  --market rent \
  --skip-search \
  --search-results-json Scraper/output/rightmove_rental_search_results_YYYYMMDD_HHMMSS.json \
  --detail-run-dir london_rent_01 \
  --resume-detail
```

## Output Files

### Search-stage outputs

Sales:
- `Scraper/output/rightmove_search_results_<timestamp>.json`
- `Scraper/output/rightmove_search_results_<timestamp>.csv`
- `Scraper/output/raw_pages_<timestamp>/`

Rentals:
- `Scraper/output/rightmove_rental_search_results_<timestamp>.json`
- `Scraper/output/rightmove_rental_search_results_<timestamp>.csv`
- `Scraper/output/rental_raw_pages_<timestamp>/`

What these show:
- one row per search-stage listing
- the best available structured fields from search results
- raw API page dumps for inspection/debugging

### Detail-stage outputs

Sales:
- `Scraper/output/rightmove_enriched_results_<timestamp>.json`
- `Scraper/output/rightmove_enriched_results_<timestamp>.csv`
- checkpoint dir like `Scraper/output/detail_run_*` or your chosen `--detail-run-dir`

Rentals:
- `Scraper/output/rightmove_rental_enriched_results_<timestamp>.json`
- `Scraper/output/rightmove_rental_enriched_results_<timestamp>.csv`
- checkpoint dir like `Scraper/output/rental_detail_run_*` or your chosen `--detail-run-dir`

Checkpoint dirs contain:
- `progress.json`
- raw per-listing detail JSON in `raw_pages/`

What these show:
- the original search-stage fields
- plus listing-page enrichment
- plus enough metadata to resume interrupted runs

## Where The Main Fields Come From

### Sales search fields

Usually from the search API:
- `listing_id`
- `listing_url`
- `price_amount`
- `price_text`
- `price_qualifier`
- `property_type`
- `bedrooms`
- `bathrooms`
- `tenure`
- `added_or_reduced`
- `auction`
- `new_home`
- `lozenge_types`
- `latitude`
- `longitude`
- `image_count`
- `floorplan_count`
- `virtual_tour_count`

Usually from DOM fallback or merge:
- `image_url`
- `image_urls`
- `position_on_page`
- `featured`

### Rental search fields

Usually from the search API:
- `listing_id`
- `listing_url`
- `rent_amount`
- `rent_frequency`
- `rent_text`
- `rent_amount_pcm`
- `rent_amount_pw`
- `property_type`
- `bedrooms`
- `bathrooms`
- `let_available_date`
- `listing_status`
- `students`
- `online_viewings_available`
- `build_to_rent`
- `latitude`
- `longitude`
- `image_count`
- `floorplan_count`
- `virtual_tour_count`

Usually from DOM fallback or merge:
- `image_url`
- `image_urls`
- `position_on_page`
- `featured`

### Sales detail enrichment

Usually from listing page content:
- `description`
- `key_features_text`
- `property_photo_urls`
- `floorplan_urls`
- `epc_urls`
- `property_photo_count`
- `size_text`
- `tenure`
- `council_tax`
- `parking`
- `garden`
- `accessibility`
- `epc_rating`
- `investment_opportunity`
- `investment_text`
- `luxury`
- `luxury_text`

### Rental detail enrichment

Usually from listing page content:
- `description`
- `key_features_text`
- `property_photo_urls`
- `floorplan_urls`
- `epc_urls`
- `property_photo_count`
- `deposit_text`
- `deposit_amount`
- `min_tenancy`
- `let_type`
- `furnish_type`
- `council_tax`
- `let_available_date`
- `pets_text`
- `bills_text`
- `student_friendly`
- `student_text`
- `zero_deposit`
- `epc_rating`
- `investment_opportunity`
- `investment_text`
- `luxury`
- `luxury_text`

## Interpretation Notes

- `postcode` is often missing because Rightmove often shows approximate locations rather than full property postcodes.
- Empty values do not necessarily mean the scraper failed. Often the listing simply does not expose that field.
- `investment_opportunity`, `luxury`, `student_friendly`, `pets_text`, and similar fields are marketing-language signals, not authoritative legal classifications.
- `build_to_rent` on rental search output comes from the search API metadata, not the detail page.
- `epc_urls` and `epc_rating` are different:
  - `epc_urls` means an EPC asset URL was found
  - `epc_rating` means a visible letter rating like `A` to `G` was extracted from text

## Resume / Recovery

Detail runs support:
- checkpointing after each completed listing
- resume after interruption
- recovery if the browser window disappears

That is why named run dirs are recommended for large jobs.

## Practical Best Use

For research projects:
- run the scraper
- keep the raw/enriched outputs unchanged
- do analysis from cleaned derivative datasets, not by editing scraper outputs

That is what `clean_rightmove_dataset.py` is for.
