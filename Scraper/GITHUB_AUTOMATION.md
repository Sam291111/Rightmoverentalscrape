# GitHub Automation

This project now includes a GitHub Actions workflow at:

- `.github/workflows/london-rental-pipeline.yml`

## What The Workflow Does

Each run:

1. installs Python dependencies from `Scraper/requirements.txt`
2. installs Chrome on the runner
3. runs `python Scraper/run_london_rental_pipeline.py`
4. uploads `Scraper/output` as a workflow artifact
5. commits changed output files back to the repository

## Triggers

The workflow supports:

- manual runs via `workflow_dispatch`
- scheduled runs twice a week

Manual runs default to a small test size:

- `pages=2`
- `max_results=40`

Scheduled runs default to a fuller scrape:

- `pages=12`
- `max_results=250`

That is deliberate: for hosted automation, a stable medium-sized recurring run is
usually more valuable than an ambitious run that fails often.

## Important Limitation

The scripts are now automation-ready, but Rightmove may still behave
differently on GitHub-hosted runners than on a local machine.

The main risk is anti-bot / session friction.

So:

- GitHub Actions is good for testing and may work well enough
- a self-hosted runner is still the more reliable long-term option if
  GitHub-hosted runs become flaky

## Outputs You Will See

The workflow preserves each London-clipped run and rebuilds the historical
summary files.

Key folders/files:

- `Scraper/output/`
- `Scraper/output/history/`

Useful files include:

- the latest cleaned run
- the latest London-clipped run
- the historical metrics JSON
- borough/category comparison CSVs

## Suggested First Use

Start with a manual run from the GitHub Actions tab using the default inputs.

That gives a lightweight check that the hosted runner can get through:

- search
- detail enrichment
- cleaning
- boundary clip
- history rebuild
