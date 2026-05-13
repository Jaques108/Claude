"""
Premier League Agent
=====================
Reads the latest collected JSON, optionally compares to the previous day's
data, then calls an LLM to produce a natural-language briefing.

Run standalone:
    python sports/agent.py
    python sports/agent.py --date 2026-05-13

Or imported by collector.py after a fresh collection.

Setup:
  1. Sign up free at https://console.groq.com — no credit card needed
  2. Create an API key, then either:
       set GROQ_API_KEY environment variable, or
       paste the key into sports/groq_api_key.txt
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from groq import Groq


BASE_DIR     = Path(__file__).parent
DATA_DIR     = BASE_DIR / "data"
BRIEFING_DIR = BASE_DIR / "briefings"
BRIEFING_DIR.mkdir(exist_ok=True)

SEP = "-" * 60


# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------

def _load_groq_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        key_file = BASE_DIR / "groq_api_key.txt"
        if key_file.exists():
            key = key_file.read_text(encoding="utf-8").strip()
    if not key:
        print(
            "ERROR: No Groq API key found.\n"
            "  1. Sign up free at https://console.groq.com (no credit card)\n"
            "  2. Set GROQ_API_KEY environment variable, or\n"
            "     paste the key into sports/groq_api_key.txt",
            file=sys.stderr,
        )
        sys.exit(1)
    return key


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _latest_two_files() -> tuple[Path, "Path | None"]:
    files = sorted(DATA_DIR.glob("*.json"), reverse=True)
    if not files:
        raise FileNotFoundError(f"No data files in {DATA_DIR}")
    latest   = files[0]
    previous = files[1] if len(files) > 1 else None
    return latest, previous


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _standings_text(data: dict) -> str:
    try:
        table    = data["standings"]["standings"][0]["table"]
        matchday = data["standings"]["season"]["currentMatchday"]
    except (KeyError, IndexError, TypeError):
        return "Standings unavailable."

    lines = [f"Matchday {matchday} standings (top 8 + bottom 3):"]
    for row in table[:8]:
        lines.append(
            f"  {row['position']}. {row['team']['shortName']}"
            f"  {row['points']} pts  GD {row['goalDifference']:+d}"
        )
    lines.append("  ...")
    for row in table[-3:]:
        lines.append(
            f"  {row['position']}. {row['team']['shortName']}"
            f"  {row['points']} pts  GD {row['goalDifference']:+d}  [RELEGATION ZONE]"
        )
    return "\n".join(lines)


def _recent_text(data: dict) -> str:
    matches = (data.get("recent_matches") or {}).get("matches", [])
    if not matches:
        return "No recent results."
    lines = ["Recent results (last 7 days):"]
    for m in matches:
        ft   = m["score"]["fullTime"]
        home = m["homeTeam"]["shortName"]
        away = m["awayTeam"]["shortName"]
        lines.append(f"  {home} {ft['home']}–{ft['away']} {away}")
    return "\n".join(lines)


def _upcoming_text(data: dict) -> str:
    matches = (data.get("upcoming_fixtures") or {}).get("matches", [])
    if not matches:
        return "No upcoming fixtures in the next 7 days."
    lines = ["Upcoming fixtures (next 7 days):"]
    for m in matches:
        home = m["homeTeam"]["shortName"]
        away = m["awayTeam"]["shortName"]
        day  = m["utcDate"][:10]
        lines.append(f"  {day}  {home} vs {away}")
    return "\n".join(lines)


def _scorers_text(data: dict) -> str:
    scorers = (data.get("top_scorers") or {}).get("scorers", [])
    if not scorers:
        return "Scorer data unavailable."
    lines = ["Top 5 scorers:"]
    for i, s in enumerate(scorers[:5], 1):
        name  = s["player"]["name"]
        team  = s["team"]["shortName"]
        goals = s.get("goals") or 0
        lines.append(f"  {i}. {name} ({team})  {goals} goals")
    return "\n".join(lines)


def _changes_text(current: dict, previous: dict | None) -> str:
    if not previous:
        return "No previous snapshot available for comparison."

    lines = []

    try:
        cur_leader  = current["standings"]["standings"][0]["table"][0]
        prev_leader = previous["standings"]["standings"][0]["table"][0]
        cur_name, prev_name = cur_leader["team"]["shortName"], prev_leader["team"]["shortName"]
        cur_pts,  prev_pts  = cur_leader["points"], prev_leader["points"]
        if cur_name != prev_name:
            lines.append(f"NEW league leader: {cur_name} (was {prev_name})")
        elif cur_pts != prev_pts:
            lines.append(f"League leader {cur_name} gained {cur_pts - prev_pts} pt(s) → {cur_pts} pts")
    except (KeyError, IndexError, TypeError):
        pass

    try:
        cur_top    = current["top_scorers"]["scorers"][0]
        prev_top   = previous["top_scorers"]["scorers"][0]
        cur_goals  = cur_top.get("goals") or 0
        prev_goals = prev_top.get("goals") or 0
        cur_name   = cur_top["player"]["name"]
        prev_name  = prev_top["player"]["name"]
        if cur_name != prev_name:
            lines.append(f"NEW top scorer: {cur_name} ({cur_goals}g) overtook {prev_name}")
        elif cur_goals != prev_goals:
            lines.append(f"{cur_name} scored {cur_goals - prev_goals} more goal(s) → {cur_goals} total")
    except (KeyError, IndexError, TypeError):
        pass

    return "\n".join(lines) if lines else "No notable changes from previous snapshot."


def _build_prompt(current: dict, previous: "dict | None", snapshot_date: str) -> str:
    return "\n".join([
        f"Premier League snapshot — {snapshot_date}",
        "",
        _standings_text(current),
        "",
        _recent_text(current),
        "",
        _upcoming_text(current),
        "",
        _scorers_text(current),
        "",
        "Changes since last snapshot:",
        _changes_text(current, previous),
    ])


# ---------------------------------------------------------------------------
# LLM call (Groq — free tier, no billing required)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a sharp, concise Premier League analyst. "
    "Given structured match data, write a punchy natural-language briefing "
    "(3–6 sentences) covering: title race, standout results, notable upcoming "
    "fixtures, and the top-scorer race. No bullet points — flowing prose, "
    "match-day energy."
)


def _run_agent(data_text: str, groq_key: str) -> str:
    client = Groq(api_key=groq_key)

    print("\nGenerating briefing", end="", flush=True)

    chunks = []
    stream = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": data_text},
        ],
        max_tokens=512,
        stream=True,
    )
    for chunk in stream:
        text = chunk.choices[0].delta.content or ""
        if text:
            print(".", end="", flush=True)
            chunks.append(text)

    print()
    return "".join(chunks)


# ---------------------------------------------------------------------------
# Public entry point (called by collector.py)
# ---------------------------------------------------------------------------

def run(date_str: "str | None" = None) -> str:
    """Produce and save a briefing. Returns the briefing text."""
    if date_str:
        latest_path = DATA_DIR / f"{date_str}.json"
        if not latest_path.exists():
            raise FileNotFoundError(f"No data file for {date_str}")
        prev_date     = (date.fromisoformat(date_str) - timedelta(days=1)).isoformat()
        previous_path = DATA_DIR / f"{prev_date}.json"
        previous_path = previous_path if previous_path.exists() else None
    else:
        latest_path, previous_path = _latest_two_files()
        date_str = latest_path.stem

    current  = _load_json(latest_path)
    previous = _load_json(previous_path) if previous_path else None

    data_text = _build_prompt(current, previous, date_str)
    key       = _load_groq_key()
    briefing  = _run_agent(data_text, key)

    out_path = BRIEFING_DIR / f"{date_str}.txt"
    out_path.write_text(briefing, encoding="utf-8")
    return briefing


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Generate Gemini briefing from collected PL data")
    parser.add_argument("--date", help="Date to analyse (YYYY-MM-DD). Defaults to most recent.")
    args = parser.parse_args()

    briefing  = run(args.date)
    stem      = args.date or sorted(DATA_DIR.glob("*.json"), reverse=True)[0].stem

    print()
    print(SEP)
    print("PREMIER LEAGUE BRIEFING")
    print(SEP)
    print(briefing)
    print(SEP)
    print(f"Saved to: {BRIEFING_DIR / stem}.txt")


if __name__ == "__main__":
    main()