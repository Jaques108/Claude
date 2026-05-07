# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Valorant Meta API — a Python application that scrapes agent performance data from multiple esports stat sites (tracker.gg, blitz.gg, gamesradar.com) and computes weighted meta-tier rankings. Runs as a CLI tool or Flask REST API.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r .venv\requirements.txt
```

## Running

```powershell
# Print JSON rankings to stdout
python api.py

# Use built-in fallback data only (skip live scraping)
python api.py --fallback

# Start Flask REST API
python api.py --serve
python api.py --serve --port 8080
```

## REST Endpoints (when `--serve`)

- `GET /agents` — all agents; supports `?role=`, `?tier=`, `?limit=` query params
- `GET /agents/<name>` — single agent by name
- `GET /meta/summary` — agents grouped by tier (S/A/B/C/D)

## Architecture

Everything lives in `api.py`. The pipeline is:

1. **Scraping** (`_safe_get`, `scrape_tracker_gg`, `scrape_blitz_gg`, `scrape_gamesradar`) — each scraper returns a `dict[str, AgentStats]` keyed by agent name. Scrapers extract JSON embedded in `<script>` tags (tracker/blitz) or parse HTML tier-list tables (gamesradar). A 0.4 s polite delay is applied between requests.

2. **Merging** (`build_agent_list`) — results from all sources are averaged field-by-field; any missing fields are filled from `FALLBACK_DATA`, a hardcoded Act-2025 snapshot used when scraping fails.

3. **Scoring** (`compute_meta_score`) — six weighted factors summing to 1.0:
   - `pick_rate` 20%, `win_rate` 20%, `tier_rank` 25%, `role_demand` 15%, `versatility` 10%, `crowd_sentiment` 10%
   - Output is clamped to `[0.02, 0.98]`; label buckets: S ≥ 0.75, A ≥ 0.60, B ≥ 0.45, C ≥ 0.30, D otherwise.

4. **Output** — agents sorted descending by `meta_score`, serialized to JSON via `AgentStats.to_dict()`.

The `AgentStats` dataclass (line ~95) is the central data model. `AGENT_ROSTER` (line ~44) is the authoritative list of 25 agents with their roles.
