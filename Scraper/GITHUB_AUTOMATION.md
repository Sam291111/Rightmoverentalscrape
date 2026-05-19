# GitHub Automation

This project now includes a GitHub Actions workflow at:

- `.github/workflows/london-rental-pipeline.yml`

## What The Workflow Does

Each run:

1. runs the rental search stage once
2. builds a chunk manifest from the search results
3. fans out the detail enrichment stage across multiple chunk jobs
4. merges the partial enriched outputs back together
5. cleans, clips, and rebuilds London history outputs
6. rebuilds the GitHub Pages dashboard data in `docs/data`
7. commits changed output and dashboard files back to the repository

## Triggers

The workflow supports:

- manual runs via `workflow_dispatch`
- scheduled runs twice a week

Manual runs now default to auto-pagination and uncapped search-stage collection:

- `pages=0`
- `max_results=0`
- `detail_chunk_size=400`

Scheduled runs use the same defaults:

- `pages=0`
- `max_results=0`
- `detail_chunk_size=400`

In this workflow, `0` means "let the scraper keep going until the site naturally
stops returning new result pages."

## Important Limitation

The scripts are now automation-ready, but Rightmove may still behave
differently on GitHub-hosted runners than on a local machine.

The main risk is anti-bot / session friction.

So:

- GitHub Actions is now more resilient because long detail runs are chunked
- GitHub-hosted runners still have a `6 hour` job execution limit
- a self-hosted runner is still the more reliable long-term option if
  GitHub-hosted runs become flaky or too slow

## Outputs You Will See

The workflow preserves each London-clipped run and rebuilds the historical
summary files.

Key folders/files:

- `Scraper/output/`
- `Scraper/output/history/`
- `docs/`
- `docs/data/dashboard.json`

Useful files include:

- the latest cleaned run
- the latest London-clipped run
- the historical metrics JSON
- borough/category comparison CSVs
- the Pages-ready dashboard data bundle

## GitHub Pages Setup

The simplest publishing route for this repo is:

1. open `Settings` -> `Pages`
2. set `Build and deployment` to `Deploy from a branch`
3. choose branch `main`
4. choose folder `/docs`
5. save

Once that is enabled, the site will publish the static dashboard from `docs/`,
and future Actions runs will update `docs/data/dashboard.json` automatically.

## Suggested First Use

Start with a manual run from the GitHub Actions tab using the default inputs.

That gives a lightweight check that the hosted runner can get through:

- search
- detail enrichment
- cleaning
- boundary clip
- history rebuild
