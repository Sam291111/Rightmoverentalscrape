# Rightmove Toolkit

This folder is now portable as a unit. If you want to move it elsewhere, move the whole `Scraper` folder, not individual files.

## Minimum files

For sales:
- `run_rightmove_pipeline.py`
- `rightmove_search_scraper.py`
- `rightmove_detail_scraper.py`

For rentals:
- `run_rightmove_pipeline.py`
- `rightmove_rental_search_scraper.py`
- `rightmove_rental_detail_scraper.py`
- `clean_rightmove_dataset.py`

Useful but optional recon scripts:
- `recon/rightmove_sales_api_trigger_probe.py`
- `recon/rightmove_rental_api_trigger_probe.py`
- `recon/rightmove_detail_explorer.py`
- `recon/rightmove_rental_detail_explorer.py`
- `recon/rightmove_rental_explorer.py`
- `recon/rightmove_rental_api_probe.py`
- `recon/site_explorer.py`

These are not needed for normal scraping runs.

## Requirements

- Python 3
- Google Chrome installed
- Python packages:
  - `selenium`
  - `undetected-chromedriver`

Install packages with:

```bash
python3 -m pip install selenium undetected-chromedriver
```

## Output location

By default, outputs are written to:

```text
Scraper/output
```

relative to this folder.

## Recommended entry point

Use the wrapper:

```bash
python3 Scraper/run_rightmove_pipeline.py --market sale --search-url "YOUR_SALES_URL"
python3 Scraper/run_rightmove_pipeline.py --market rent --search-url "YOUR_RENTAL_URL"
```

The wrapper runs:
1. search-stage scraping
2. detail-stage enrichment

Recommended after scraping:

```bash
python3 Scraper/clean_rightmove_dataset.py
```

This creates a normalised research-friendly dataset plus a QC summary.

For a London rental workflow with boundary clipping:

```bash
python3 Scraper/run_london_rental_pipeline.py
```

This wrapper:
1. runs the rental scraper in unattended mode
2. cleans the enriched output
3. clips the cleaned dataset to Greater London
4. tags retained listings with a London borough where possible

## Common commands

Sales, fresh run:

```bash
python3 Scraper/run_rightmove_pipeline.py \
  --market sale \
  --search-url "YOUR_RIGHTMOVE_SALES_SEARCH_URL" \
  --pages 42 \
  --max-results 1000 \
  --detail-run-dir london_sale_01
```

Rentals, fresh run:

```bash
python3 Scraper/run_rightmove_pipeline.py \
  --market rent \
  --search-url "YOUR_RIGHTMOVE_RENTAL_SEARCH_URL" \
  --pages 42 \
  --max-results 1000 \
  --detail-run-dir london_rent_01

Rentals, unattended automation run:

```bash
python3 Scraper/run_rightmove_rental_automation.py \
  --search-url "YOUR_RIGHTMOVE_RENTAL_SEARCH_URL" \
  --pages 42 \
  --max-results 1000
```

London rental run with the built-in 10-mile London search URL:

```bash
python3 Scraper/run_london_rental_pipeline.py \
  --pages 42 \
  --max-results 1000
```

Or use the main wrapper with opt-in automation flags:

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
```

Resume detail only:

```bash
python3 Scraper/run_rightmove_pipeline.py \
  --market rent \
  --skip-search \
  --search-results-json Scraper/output/rightmove_rental_search_results_YYYYMMDD_HHMMSS.json \
  --detail-run-dir london_rent_01 \
  --resume-detail
```

## What each stage produces

Search stage:
- search JSON dataset
- search CSV
- raw API page dumps

Detail stage:
- enriched JSON dataset
- enriched CSV
- checkpoint run directory
- raw per-listing detail JSON

## How the scraper works

- Search stage is API-first, with DOM fallback/merge.
- Detail stage is page-source / DOM-driven.
- The browser opens visibly so you can handle cookies or CAPTCHA manually.
- Automation mode is opt-in via `--no-interactive` and optionally `--headless`.
- Detail runs support checkpointing and resume.

## Moving the folder

Safe approach:
1. move the whole `Scraper` folder
2. run commands from the parent directory of that folder

Example:

```bash
python3 Scraper/run_rightmove_pipeline.py --market sale --search-url "..."
```

If you rename the folder from `Scraper` to something else, either:
- keep running the scripts with the new path, or
- update the example commands accordingly

The scripts themselves now use folder-relative paths internally, so they do not depend on this project root anymore.
