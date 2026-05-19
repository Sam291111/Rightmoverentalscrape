# Rightmoverentalscrape

Postcode-first Rightmove rental scraping for London, with:
- automated search and detail enrichment
- Greater London clipping and borough tagging
- historical metrics generation
- a GitHub Pages dashboard built from preserved scrape runs

## Dashboard

Published site:

- [London Rental Dashboard](https://sam291111.github.io/Rightmoverentalscrape/)

The site lives in [`docs/`](/Users/samwinter/Documents/Shared/University/Geography/Year%202/Digital%20Data%20Cap/Final%20Project/docs) and reads its data from [`docs/data/dashboard.json`](/Users/samwinter/Documents/Shared/University/Geography/Year%202/Digital%20Data%20Cap/Final%20Project/docs/data/dashboard.json).

## Workflows

Main production workflow:

- `London Rental Pipeline`

This:
- runs the postcode-first search collector
- enriches listing detail pages in chunks
- merges, cleans, and clips results to London
- rebuilds historical outputs in [`Scraper/output/history`](/Users/samwinter/Documents/Shared/University/Geography/Year%202/Digital%20Data%20Cap/Final%20Project/Scraper/output/history)
- updates [`docs/data/`](/Users/samwinter/Documents/Shared/University/Geography/Year%202/Digital%20Data%20Cap/Final%20Project/docs/data)

Fast testing workflow:

- `London Rental Smoke Test`

This is a small artifact-only run for testing search, chunking, collation, detail enrichment, and downstream dashboard data generation without updating the live site.

Pages deployment workflow:

- `Deploy GitHub Pages`

This now deploys:
- when the main pipeline completes successfully
- when `docs/` changes on `main`
- when run manually from the Actions tab

## GitHub Pages Setup

In GitHub:

1. Open `Settings`
2. Open `Pages`
3. Under `Build and deployment`, set `Source` to `GitHub Actions`
4. Save

Important:

- The main scraping workflow commits `docs/data` using `GITHUB_TOKEN`
- GitHub does not trigger a new `push` workflow from that token automatically
- so Pages deployment is chained from the completion of `London Rental Pipeline`, not just from `push`

GitHub Docs:

- [Triggering a workflow](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-when-your-workflow-runs/triggering-a-workflow)
- [Events that trigger workflows: `workflow_run`](https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows)

## Repo Structure

- [`Scraper/`](/Users/samwinter/Documents/Shared/University/Geography/Year%202/Digital%20Data%20Cap/Final%20Project/Scraper): scraping, cleaning, clipping, and history scripts
- [`docs/`](/Users/samwinter/Documents/Shared/University/Geography/Year%202/Digital%20Data%20Cap/Final%20Project/docs): GitHub Pages dashboard
- [`London Boundary/`](/Users/samwinter/Documents/Shared/University/Geography/Year%202/Digital%20Data%20Cap/Final%20Project/London%20Boundary): GLA and borough boundary data

## Notes

- The collector is now postcode-first rather than borough-first.
- Borough comparison is done after scraping, using the London boundary layers.
- Empty but valid postcode searches should not fail the run.
