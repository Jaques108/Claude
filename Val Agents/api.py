"""
Valorant Meta API
=================
Fetches the current agent roster from valorant-api.com (free JSON API) and
computes a weighted meta score (0–1 exclusive) for every agent.

Note on live stats: tracker.gg, blitz.gg, and gamesradar.com are all
client-side-rendered SPAs. requests+BeautifulSoup only gets the JS shell —
no actual data. Live pick/win scraping would require a headless browser
(Playwright/Selenium). Until then, stats come from built-in fallback data
(Act 2025 snapshot). The live roster call still detects newly released agents.

Factors (weights sum to 1.0):
  - pick_rate       (0.20) — popularity in ranked play
  - win_rate        (0.20) — in-match win contribution
  - tier_rank       (0.25) — community tier-list consensus (S/A/B/C/D)
  - role_demand     (0.15) — current meta preference for each role
  - versatility     (0.10) — number of viable maps / game-modes
  - crowd_sentiment (0.10) — aggregated forum/Reddit sentiment proxy

Run:
    python api.py            → JSON to stdout
    python api.py --serve    → Flask REST API on http://localhost:5000
    python api.py --fallback → skip network calls entirely
"""

import argparse
import json
import time
import random
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

VALORANT_API_URL = "https://valorant-api.com/v1/agents?isPlayableCharacter=true"

# Known agents + roles as a baseline (scraped data fills scores)
AGENT_ROSTER: dict[str, str] = {
    "Brimstone": "Controller",
    "Viper": "Controller",
    "Omen": "Controller",
    "Killjoy": "Sentinel",
    "Cypher": "Sentinel",
    "Sage": "Sentinel",
    "Phoenix": "Duelist",
    "Jett": "Duelist",
    "Reyna": "Duelist",
    "Raze": "Duelist",
    "Breach": "Initiator",
    "Sova": "Initiator",
    "Skye": "Initiator",
    "Yoru": "Duelist",
    "Astra": "Controller",
    "KAY/O": "Initiator",
    "Chamber": "Sentinel",
    "Neon": "Duelist",
    "Fade": "Initiator",
    "Harbor": "Controller",
    "Gekko": "Initiator",
    "Deadlock": "Sentinel",
    "Iso": "Duelist",
    "Clove": "Controller",
    "Vyse": "Sentinel",
    "Tejo": "Initiator",
    "Miks": "Controller",
    "Veto": "Sentinel",
    "Waylay": "Duelist",
}

# Role demand multipliers (updated to reflect current meta feel)
ROLE_DEMAND: dict[str, float] = {
    "Controller": 0.82,
    "Sentinel":   0.76,
    "Initiator":  0.88,
    "Duelist":    0.70,
}

# Tier → numeric value mapping
TIER_VALUES: dict[str, float] = {
    "S": 1.0,
    "A": 0.78,
    "B": 0.55,
    "C": 0.34,
    "D": 0.15,
    "F": 0.05,
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AgentStats:
    name: str
    role: str
    pick_rate: float        = 0.0   # 0–1
    win_rate: float         = 0.0   # 0–1
    tier_rank: str          = "C"
    versatility: float      = 0.5   # 0–1
    crowd_sentiment: float  = 0.5   # 0–1
    meta_score: float       = 0.0   # final weighted score (0–1 exclusive)
    tier_label: str         = ""    # S / A / B / C / D derived from meta_score
    sources: list[str]      = field(default_factory=list)


# ---------------------------------------------------------------------------
# Scraping helpers
# ---------------------------------------------------------------------------

def _safe_get(url: str, timeout: int = 10) -> Optional[BeautifulSoup]:
    """Fetch a URL and return BeautifulSoup, or None on failure."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        time.sleep(0.4)  # polite delay
        return BeautifulSoup(r.text, "html.parser")
    except Exception as exc:
        print(f"  [warn] Could not fetch {url}: {exc}")
        return None


def fetch_live_roster() -> dict[str, str]:
    """
    Fetch the current playable agent roster from valorant-api.com.
    Returns {agent_name: role} — useful for detecting newly released agents.
    """
    try:
        r = requests.get(VALORANT_API_URL, headers=HEADERS, timeout=10)
        r.raise_for_status()
        time.sleep(0.4)
        roster: dict[str, str] = {}
        for entry in r.json().get("data", []):
            name = entry.get("displayName", "")
            role_obj = entry.get("role") or {}
            role = role_obj.get("displayName", "Unknown")
            if name and role != "Unknown":
                roster[name] = role
        return roster
    except Exception as exc:
        print(f"  [warn] Could not fetch valorant-api.com: {exc}")
        return {}


def scrape_tierlist_site() -> dict[str, str]:
    """
    Attempt to scrape S/A/B/C/D tier rankings from a community site.
    Returns {agent_name: tier_letter}, or {} if the site is unreachable / CSR.

    Note: gamesradar.com is a React SPA — requests gets only the JS shell.
    This will return an empty dict in most cases; fallback tiers are used instead.
    """
    url = "https://www.gamesradar.com/valorant-agent-tier-list/"
    soup = _safe_get(url)
    results: dict[str, str] = {}

    if not soup:
        return results

    current_tier = "C"
    tier_map = {"s tier": "S", "a tier": "A", "b tier": "B",
                "c tier": "C", "d tier": "D", "f tier": "F"}

    for tag in soup.find_all(["h2", "h3", "p", "li"]):
        text = tag.get_text(strip=True).lower()
        for key, val in tier_map.items():
            if key in text:
                current_tier = val
                break
        for agent in AGENT_ROSTER:
            if agent.lower() in text and tag.name in ("li", "p"):
                if agent not in results:
                    results[agent] = current_tier

    return results


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------

WEIGHTS = {
    "pick_rate":       0.20,
    "win_rate":        0.20,
    "tier_rank":       0.25,
    "role_demand":     0.15,
    "versatility":     0.10,
    "crowd_sentiment": 0.10,
}

assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"


def _clamp_exclusive(val: float, lo: float = 0.02, hi: float = 0.98) -> float:
    """Ensure value is strictly inside (0, 1) — never exactly 0 or 1."""
    return max(lo, min(hi, val))


def _tier_label(score: float) -> str:
    if score >= 0.82:   return "S"
    if score >= 0.65:   return "A"
    if score >= 0.48:   return "B"
    if score >= 0.32:   return "C"
    return "D"


def compute_meta_score(agent: AgentStats) -> float:
    """
    Weighted linear combination of all factor scores → (0, 1) exclusive.
    """
    tier_val = TIER_VALUES.get(agent.tier_rank, 0.34)
    role_val = ROLE_DEMAND.get(agent.role, 0.5)

    raw = (
        WEIGHTS["pick_rate"]       * agent.pick_rate +
        WEIGHTS["win_rate"]        * agent.win_rate  +
        WEIGHTS["tier_rank"]       * tier_val        +
        WEIGHTS["role_demand"]     * role_val        +
        WEIGHTS["versatility"]     * agent.versatility +
        WEIGHTS["crowd_sentiment"] * agent.crowd_sentiment
    )
    return _clamp_exclusive(raw)


# ---------------------------------------------------------------------------
# Fallback / seeded data (used when scraping fails or is incomplete)
# ---------------------------------------------------------------------------

# Based on publicly known approximate meta standing (Act 2025)
_FALLBACK: dict[str, dict] = {
    "Jett":      {"pick_rate": 0.18, "win_rate": 0.51, "tier": "A", "versatility": 0.88, "sentiment": 0.72},
    "Omen":      {"pick_rate": 0.22, "win_rate": 0.53, "tier": "S", "versatility": 0.92, "sentiment": 0.81},
    "Killjoy":   {"pick_rate": 0.21, "win_rate": 0.54, "tier": "S", "versatility": 0.85, "sentiment": 0.83},
    "Sova":      {"pick_rate": 0.19, "win_rate": 0.52, "tier": "A", "versatility": 0.90, "sentiment": 0.76},
    "Reyna":     {"pick_rate": 0.24, "win_rate": 0.50, "tier": "B", "versatility": 0.60, "sentiment": 0.55},
    "Viper":     {"pick_rate": 0.15, "win_rate": 0.53, "tier": "A", "versatility": 0.80, "sentiment": 0.74},
    "Brimstone": {"pick_rate": 0.12, "win_rate": 0.52, "tier": "B", "versatility": 0.75, "sentiment": 0.66},
    "Sage":      {"pick_rate": 0.13, "win_rate": 0.51, "tier": "B", "versatility": 0.70, "sentiment": 0.60},
    "Raze":      {"pick_rate": 0.16, "win_rate": 0.52, "tier": "A", "versatility": 0.82, "sentiment": 0.70},
    "Breach":    {"pick_rate": 0.09, "win_rate": 0.51, "tier": "B", "versatility": 0.72, "sentiment": 0.63},
    "Skye":      {"pick_rate": 0.14, "win_rate": 0.53, "tier": "A", "versatility": 0.83, "sentiment": 0.75},
    "Cypher":    {"pick_rate": 0.10, "win_rate": 0.50, "tier": "B", "versatility": 0.68, "sentiment": 0.58},
    "Phoenix":   {"pick_rate": 0.06, "win_rate": 0.49, "tier": "C", "versatility": 0.55, "sentiment": 0.45},
    "Yoru":      {"pick_rate": 0.05, "win_rate": 0.48, "tier": "C", "versatility": 0.50, "sentiment": 0.42},
    "Astra":     {"pick_rate": 0.08, "win_rate": 0.52, "tier": "B", "versatility": 0.78, "sentiment": 0.65},
    "KAY/O":     {"pick_rate": 0.11, "win_rate": 0.52, "tier": "A", "versatility": 0.80, "sentiment": 0.71},
    "Chamber":   {"pick_rate": 0.10, "win_rate": 0.51, "tier": "B", "versatility": 0.65, "sentiment": 0.57},
    "Neon":      {"pick_rate": 0.07, "win_rate": 0.49, "tier": "C", "versatility": 0.53, "sentiment": 0.48},
    "Fade":      {"pick_rate": 0.13, "win_rate": 0.52, "tier": "A", "versatility": 0.82, "sentiment": 0.73},
    "Harbor":    {"pick_rate": 0.04, "win_rate": 0.48, "tier": "D", "versatility": 0.45, "sentiment": 0.35},
    "Gekko":     {"pick_rate": 0.12, "win_rate": 0.51, "tier": "B", "versatility": 0.76, "sentiment": 0.67},
    "Deadlock":  {"pick_rate": 0.06, "win_rate": 0.49, "tier": "C", "versatility": 0.52, "sentiment": 0.44},
    "Iso":       {"pick_rate": 0.07, "win_rate": 0.49, "tier": "C", "versatility": 0.55, "sentiment": 0.46},
    "Clove":     {"pick_rate": 0.14, "win_rate": 0.52, "tier": "A", "versatility": 0.80, "sentiment": 0.74},
    "Vyse":      {"pick_rate": 0.08, "win_rate": 0.50, "tier": "B", "versatility": 0.64, "sentiment": 0.58},
    # Released early 2025 — stats based on known meta data
    "Tejo":      {"pick_rate": 0.10, "win_rate": 0.51, "tier": "B", "versatility": 0.72, "sentiment": 0.65},
    # Released post-August 2025 — mid-tier placeholders; update when data is available
    "Miks":      {"pick_rate": 0.09, "win_rate": 0.50, "tier": "C", "versatility": 0.65, "sentiment": 0.58},
    "Veto":      {"pick_rate": 0.10, "win_rate": 0.50, "tier": "C", "versatility": 0.63, "sentiment": 0.57},
    "Waylay":    {"pick_rate": 0.10, "win_rate": 0.49, "tier": "C", "versatility": 0.60, "sentiment": 0.55},
}


def _apply_fallback(agent: AgentStats) -> AgentStats:
    fb = _FALLBACK.get(agent.name, {})
    if not fb:
        # generic mid-tier defaults
        agent.pick_rate       = round(random.uniform(0.06, 0.14), 3)
        agent.win_rate        = round(random.uniform(0.48, 0.53), 3)
        agent.tier_rank       = "C"
        agent.versatility     = round(random.uniform(0.50, 0.70), 3)
        agent.crowd_sentiment = round(random.uniform(0.45, 0.65), 3)
    else:
        agent.pick_rate       = fb["pick_rate"]
        agent.win_rate        = fb["win_rate"]
        agent.tier_rank       = fb["tier"]
        agent.versatility     = fb["versatility"]
        agent.crowd_sentiment = fb["sentiment"]
    return agent


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def build_agent_list(use_fallback_only: bool = False) -> list[AgentStats]:
    """
    Build the ranked agent list.

    When use_fallback_only=False (default):
      1. Fetches the live roster from valorant-api.com to catch newly released agents.
      2. Applies Act-2025 fallback stats (pick/win rates, tiers) for all agents.
      3. Attempts to overlay tier rankings from gamesradar.com; skips silently if
         the page is unreachable or returns no parseable data (it's a CSR React app).

    When use_fallback_only=True: skips all network calls.
    """
    agents = {name: AgentStats(name=name, role=role)
              for name, role in AGENT_ROSTER.items()}

    if not use_fallback_only:
        print("[*] Fetching live agent roster from valorant-api.com ...")
        live_roster = fetch_live_roster()
        for name, role in live_roster.items():
            if name not in agents:
                print(f"  [+] New agent detected: {name} ({role})")
                agents[name] = AgentStats(name=name, role=role)

        # Apply fallback stats — tracker.gg and blitz.gg are CSR SPAs and cannot
        # be scraped with requests alone; a headless browser would be required.
        for agent in agents.values():
            _apply_fallback(agent)
            agent.sources = ["fallback_data"]

        print("[*] Attempting tier-list scrape from gamesradar.com ...")
        tier_data = scrape_tierlist_site()
        if tier_data:
            for name, tier in tier_data.items():
                if name in agents:
                    agents[name].tier_rank = tier
                    agents[name].sources = ["gamesradar.com", "fallback_data"]
        else:
            print("  [warn] Tier scrape returned no data — using fallback tiers.")
    else:
        for agent in agents.values():
            _apply_fallback(agent)
            agent.sources = ["fallback_data"]

    for agent in agents.values():
        agent.meta_score = round(compute_meta_score(agent), 4)
        agent.tier_label = _tier_label(agent.meta_score)

    return sorted(agents.values(), key=lambda a: a.meta_score, reverse=True)


def agents_to_dict(agents: list[AgentStats]) -> list[dict]:
    result = []
    for rank, agent in enumerate(agents, 1):
        d = asdict(agent)
        d["rank"] = rank
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Valorant Meta API")
    parser.add_argument("--serve",    action="store_true", help="Run Flask REST API")
    parser.add_argument("--fallback", action="store_true", help="Skip scraping, use built-in data")
    parser.add_argument("--port",     type=int, default=5000)
    args = parser.parse_args()

    if args.serve:
        try:
            from flask import Flask, jsonify, request
        except ImportError:
            print("Flask not installed. Run: pip install flask")
            return

        app = Flask(__name__)

        @app.route("/agents", methods=["GET"])
        def get_agents():
            role   = request.args.get("role")
            tier   = request.args.get("tier")
            limit  = request.args.get("limit", type=int)
            use_fb = request.args.get("fallback", "false").lower() == "true"

            data = build_agent_list(use_fallback_only=use_fb)
            out  = agents_to_dict(data)

            if role:
                out = [a for a in out if a["role"].lower() == role.lower()]
            if tier:
                out = [a for a in out if a["tier_label"].upper() == tier.upper()]
            if limit:
                out = out[:limit]

            return jsonify({"count": len(out), "agents": out})

        @app.route("/agents/<name>", methods=["GET"])
        def get_agent(name: str):
            use_fb = request.args.get("fallback", "true").lower() == "true"
            data   = build_agent_list(use_fallback_only=use_fb)
            match  = next((a for a in data if a.name.lower() == name.lower()), None)
            if not match:
                return jsonify({"error": f"Agent '{name}' not found"}), 404
            out = asdict(match)
            out["rank"] = next(i+1 for i, a in enumerate(data) if a.name == match.name)
            return jsonify(out)

        @app.route("/meta/summary", methods=["GET"])
        def meta_summary():
            use_fb = request.args.get("fallback", "true").lower() == "true"
            data   = build_agent_list(use_fallback_only=use_fb)
            by_tier: dict[str, list] = {"S": [], "A": [], "B": [], "C": [], "D": []}
            for a in data:
                by_tier[a.tier_label].append(a.name)
            return jsonify({"meta_summary": by_tier, "total_agents": len(data)})

        print(f"[*] Starting Valorant Meta API on http://localhost:{args.port}")
        app.run(port=args.port, debug=False)

    else:
        print("[*] Building Valorant meta rankings ...")
        data = build_agent_list(use_fallback_only=args.fallback)
        print(json.dumps(agents_to_dict(data), indent=2))


if __name__ == "__main__":
    main()