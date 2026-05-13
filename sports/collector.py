"""
Premier League Data Collector
==============================
Runs on every computer startup (via Windows Task Scheduler).
Only collects data on Monday / Tuesday / Wednesday — skips all other days.

Data source: football-data.org free tier
  Endpoints used:
    /v4/competitions/PL/standings  — live league table
    /v4/competitions/PL/matches    — recent results + upcoming fixtures
    /v4/competitions/PL/scorers    — top scorers

Setup (one-time):
  1. Register at https://www.football-data.org/client/register (free)
  2. Set your key via one of:
       $env:FOOTBALL_API_KEY = "your_key_here"   (session)
       [System.Environment]::SetEnvironmentVariable("FOOTBALL_API_KEY","your_key","User")  (permanent)
       Or paste the key into  sports/api_key.txt
  3. Register the startup task:
       powershell -ExecutionPolicy Bypass -File sports/setup_task.ps1

Output: sports/data/YYYY-MM-DD.json
Logs:   sports/logs/collector.log
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR  = BASE_DIR / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Logging — file + stdout
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "collector.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_BASE      = "https://api.football-data.org/v4"
COMPETITION   = "PL"
COLLECT_DAYS  = {0, 1, 2}   # Mon=0, Tue=1, Wed=2
DAY_NAMES     = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _load_api_key() -> str:
    key = os.environ.get("FOOTBALL_API_KEY", "").strip()
    if not key:
        key_file = BASE_DIR / "api_key.txt"
        if key_file.exists():
            key = key_file.read_text(encoding="utf-8").strip()
    if not key:
        log.error(
            "No API key found. Set the FOOTBALL_API_KEY environment variable "
            "or create sports/api_key.txt containing just the key."
        )
        sys.exit(1)
    return key


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(path: str, api_key: str, params: dict | None = None) -> dict | None:
    url = f"{API_BASE}/{path}"
    try:
        r = requests.get(
            url,
            headers={"X-Auth-Token": api_key},
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as exc:
        log.warning("HTTP %s for %s — %s", exc.response.status_code, url, exc)
        return None
    except Exception as exc:
        log.warning("Request failed for %s — %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def collect(api_key: str) -> dict:
    today = datetime.now(timezone.utc).date()

    log.info("Fetching standings ...")
    standings = _get(f"competitions/{COMPETITION}/standings", api_key)

    week_ago   = (today - timedelta(days=7)).isoformat()
    week_ahead = (today + timedelta(days=7)).isoformat()
    today_iso  = today.isoformat()

    log.info("Fetching recent results (past 7 days) ...")
    recent = _get(
        f"competitions/{COMPETITION}/matches",
        api_key,
        {"dateFrom": week_ago, "dateTo": today_iso, "status": "FINISHED"},
    )

    log.info("Fetching upcoming fixtures (next 7 days) ...")
    upcoming = _get(
        f"competitions/{COMPETITION}/matches",
        api_key,
        {"dateFrom": today_iso, "dateTo": week_ahead},
    )

    log.info("Fetching top scorers ...")
    scorers = _get(f"competitions/{COMPETITION}/scorers", api_key)

    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "competition": COMPETITION,
        "standings": standings,
        "recent_matches": recent,
        "upcoming_fixtures": upcoming,
        "top_scorers": scorers,
    }


# ---------------------------------------------------------------------------
# Post-collection summary
# ---------------------------------------------------------------------------

def _log_summary(data: dict) -> None:
    if data["standings"]:
        try:
            leader = data["standings"]["standings"][0]["table"][0]
            log.info(
                "League leader: %s  (%d pts)",
                leader["team"]["name"],
                leader["points"],
            )
        except (KeyError, IndexError, TypeError):
            pass

    for key, label in [("recent_matches", "recent"), ("upcoming_fixtures", "upcoming")]:
        if data[key]:
            count = len(data[key].get("matches", []))
            log.info("%s fixtures: %d", label.capitalize(), count)

    if data["top_scorers"]:
        try:
            top = data["top_scorers"]["scorers"][0]
            log.info(
                "Top scorer: %s — %d goals",
                top["player"]["name"],
                top["goals"],
            )
        except (KeyError, IndexError, TypeError):
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Premier League data collector")
    parser.add_argument(
        "--no-agent", action="store_true",
        help="Skip the Claude briefing step after collection",
    )
    args = parser.parse_args()

    now     = datetime.now(timezone.utc)
    weekday = now.weekday()

    log.info("=== Premier League Collector started — %s ===", DAY_NAMES[weekday])

    if weekday not in COLLECT_DAYS:
        log.info("Collection only runs Mon/Tue/Wed. Today is %s — exiting.", DAY_NAMES[weekday])
        sys.exit(0)

    api_key = _load_api_key()
    data    = collect(api_key)

    out_path = DATA_DIR / f"{now.date().isoformat()}.json"
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log.info("Saved → %s", out_path)

    _log_summary(data)
    log.info("=== Collection complete ===")

    if not args.no_agent:
        try:
            from sports import agent  # noqa: PLC0415
        except ModuleNotFoundError:
            import importlib.util, pathlib
            spec = importlib.util.spec_from_file_location(
                "agent", pathlib.Path(__file__).parent / "agent.py"
            )
            agent = importlib.util.module_from_spec(spec)  # type: ignore[assignment]
            spec.loader.exec_module(agent)  # type: ignore[union-attr]

        log.info("=== Running Claude agent briefing ===")
        try:
            briefing = agent.run()
            log.info("Briefing saved.")
            print("\n" + "=" * 60)
            print(briefing)
            print("=" * 60)
        except SystemExit:
            log.warning("Agent skipped — ANTHROPIC_API_KEY not set.")
        except Exception as exc:
            log.warning("Agent failed: %s", exc)


if __name__ == "__main__":
    main()
