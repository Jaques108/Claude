"""
Premier League Data Display
============================
Reads the most recent collected JSON file from sports/data/
and prints a formatted summary to stdout.

Run:
    python sports/display.py
    python sports/display.py --date 2026-05-13
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
SEP = "-" * 60


def _latest_file() -> Path:
    files = sorted(DATA_DIR.glob("*.json"), reverse=True)
    if not files:
        raise FileNotFoundError(f"No data files found in {DATA_DIR}")
    return files[0]


def _load(date_str: str | None) -> dict:
    if date_str:
        path = DATA_DIR / f"{date_str}.json"
        if not path.exists():
            raise FileNotFoundError(f"No data file for {date_str} — run collector.py first")
    else:
        path = _latest_file()
    print(f"Data file : {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Section printers
# ---------------------------------------------------------------------------

def _print_standings(data: dict) -> None:
    try:
        table    = data["standings"]["standings"][0]["table"]
        matchday = data["standings"]["season"]["currentMatchday"]
    except (KeyError, IndexError, TypeError):
        print("  (standings unavailable)")
        return

    print(f"\n{'PREMIER LEAGUE STANDINGS':^60}")
    print(f"{'Matchday ' + str(matchday):^60}")
    print(SEP)
    print(f"{'Pos':<4} {'Team':<24} {'P':>3} {'W':>3} {'D':>3} {'L':>3} {'GD':>5} {'Pts':>4}")
    print(SEP)

    relegation_line = len(table) - 3  # bottom 3 go down
    for row in table:
        pos  = row["position"]
        team = row["team"]["shortName"]
        p    = row["playedGames"]
        w    = row["won"]
        d    = row["draw"]
        l    = row["lost"]
        gd   = row["goalDifference"]
        pts  = row["points"]

        marker = ""
        if pos <= 4:
            marker = "*"   # Champions League
        elif pos <= 6:
            marker = "+"   # European
        elif pos > relegation_line:
            marker = "v"   # Relegation zone

        print(f"{pos:<4} {team:<24} {p:>3} {w:>3} {d:>3} {l:>3} {gd:>+5} {pts:>4}  {marker}")

    print(f"\n  * Top 4 = Champions League  + = Europe  v = Relegation zone")


def _fmt_match(m: dict) -> str:
    home   = m["homeTeam"]["shortName"]
    away   = m["awayTeam"]["shortName"]
    ft     = m["score"]["fullTime"]
    status = m["status"]
    date   = m["utcDate"][:10]

    if status == "FINISHED":
        score = f"{ft['home']} - {ft['away']}"
    else:
        kick_off = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        local    = kick_off.astimezone()
        score    = local.strftime("%a %d %b  %H:%M")

    return f"  {home:<20} {score:^11} {away}"


def _print_recent(data: dict) -> None:
    matches = (data.get("recent_matches") or {}).get("matches", [])
    print(f"\n{'RECENT RESULTS (last 7 days)':^60}")
    print(SEP)
    if not matches:
        print("  No recent results.")
        return
    for m in matches:
        print(_fmt_match(m))


def _print_upcoming(data: dict) -> None:
    matches = (data.get("upcoming_fixtures") or {}).get("matches", [])
    print(f"\n{'UPCOMING FIXTURES (next 7 days)':^60}")
    print(SEP)
    if not matches:
        print("  No upcoming fixtures.")
        return
    for m in matches:
        print(_fmt_match(m))


def _print_scorers(data: dict) -> None:
    scorers = (data.get("top_scorers") or {}).get("scorers", [])
    print(f"\n{'TOP SCORERS':^60}")
    print(SEP)
    if not scorers:
        print("  (scorer data unavailable)")
        return
    print(f"  {'#':<4} {'Player':<22} {'Team':<20} {'G':>3} {'A':>3} {'Pen':>4}")
    print(f"  {'-'*55}")
    for i, s in enumerate(scorers, 1):
        name  = s["player"]["name"]
        team  = s["team"]["shortName"]
        goals = s.get("goals") or 0
        asst  = s.get("assists") or 0
        pens  = s.get("penalties") or 0
        print(f"  {i:<4} {name:<22} {team:<20} {goals:>3} {asst:>3} {pens:>4}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Display collected Premier League data")
    parser.add_argument("--date", help="Date to display (YYYY-MM-DD). Defaults to most recent.")
    args = parser.parse_args()

    data = _load(args.date)
    collected = data.get("collected_at", "unknown")
    print(f"Collected : {collected}")

    _print_standings(data)
    _print_recent(data)
    _print_upcoming(data)
    _print_scorers(data)
    print()


if __name__ == "__main__":
    main()
