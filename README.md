# Rightmoverentalscrape

Historical Rightmove rental scraping and London-focused trend analysis.

## What This Repo Does

- scrapes rental listings from a London-centred Rightmove search
- enriches and cleans the listing data
- clips results to Greater London using GIS boundaries
- tags listings by borough and rental categories
- preserves each run so trends can be analysed over time
- publishes a static dashboard through GitHub Pages

## Main Folders

- `Scraper/`: scrape, cleaning, clipping, and history build scripts
- `Scraper/output/`: preserved datasets and historical summaries
- `docs/`: GitHub Pages dashboard

## GitHub Pages

The dashboard is designed to be served directly from `main/docs`.

To enable it on GitHub:

1. open repo `Settings`
2. open `Pages`
3. choose `Deploy from a branch`
4. select branch `main`
5. select folder `/docs`
6. save

The scraping workflow updates both `Scraper/output/` and `docs/data/`, so each
successful automation run refreshes the published dashboard data.
