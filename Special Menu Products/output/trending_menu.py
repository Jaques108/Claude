"""
Tortillas Special Menu Product Finder
======================================
Crawls trending food data from social media and search platforms,
cross-references with Tortillas' existing inventory, and generates
an HTML report surfacing the top 5 LTO (Limited-Time-Offer) candidates.

Output:
    output/index.html           — top-5 overview dashboard
    output/detail_1..5.html     — per-product detail & pricing pages

Data Sources:
    Google Trends  — daily RSS feed + Autocomplete API (no API key needed)
    Reddit         — public JSON API (no auth required)

Run:
    pip install requests
    python trending_menu.py              # live Google + Reddit + HTML output
    python trending_menu.py --offline    # skip all live fetching
"""

import argparse
import json
import math
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# Get a free YouTube Data API v3 key at https://console.cloud.google.com
# Set as env var:  set YOUTUBE_API_KEY=your_key_here  (Windows)
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "")

# ---------------------------------------------------------------------------
# Tortillas — Pantry & Inventory
# ---------------------------------------------------------------------------

# cost per recipe-unit for each ingredient
PANTRY: dict[str, float] = {
    "flour tortilla (10\")":    0.25,
    "corn tortilla (6\")":      0.10,
    "ground beef (4 oz)":       1.10,
    "chicken breast (4 oz)":    0.90,
    "carnitas (4 oz)":          1.20,
    "fish fillet (3 oz)":       1.40,
    "shredded cheese (1 oz)":   0.30,
    "sour cream (1 oz)":        0.20,
    "cream cheese (1 oz)":      0.25,
    "lettuce":                  0.15,
    "diced tomato":             0.20,
    "diced onion":              0.10,
    "pickled jalapeño":         0.10,
    "fresh cilantro":           0.05,
    "lime wedge":               0.10,
    "rice (4 oz)":              0.30,
    "black beans (3 oz)":       0.25,
    "pinto beans (3 oz)":       0.25,
    "red salsa (2 oz)":         0.15,
    "verde salsa (2 oz)":       0.15,
    "guacamole (2 oz)":         0.55,
    "hot sauce":                0.05,
    "cooking oil":              0.05,
    "cumin":                    0.02,
    "chili powder":             0.02,
    "garlic powder":            0.02,
    "smoked paprika":           0.02,
    "smoky adobo sauce (1 oz)":  0.20,
    "smoked pepper mayo (1 oz)": 0.25,
    "adobo seasoning":          0.03,
    "ancho chili powder":       0.05,
    # Specialty — minor procurement needed
    "birria consommé (4 oz)":   0.55,
    "dried ancho chilis":       0.30,
    "gochujang (1 oz)":         0.40,
    "sesame oil (dash)":        0.10,
    "hot honey (0.5 oz)":       0.20,
    "elote seasoning":          0.08,
    "cotija cheese (1 oz)":     0.45,
    "roasted corn (3 oz)":      0.22,
    "tajín (pinch)":            0.03,
    "fries (6 oz)":             0.60,
    "Korean BBQ glaze (1 oz)":  0.35,
    "pickled radish (1 oz)":    0.20,
}

STANDARD_PANTRY = {
    "flour tortilla (10\")", "corn tortilla (6\")", "ground beef (4 oz)",
    "chicken breast (4 oz)", "carnitas (4 oz)", "fish fillet (3 oz)",
    "shredded cheese (1 oz)", "sour cream (1 oz)", "cream cheese (1 oz)",
    "lettuce", "diced tomato", "diced onion", "pickled jalapeño",
    "fresh cilantro", "lime wedge", "rice (4 oz)", "black beans (3 oz)",
    "pinto beans (3 oz)", "red salsa (2 oz)", "verde salsa (2 oz)",
    "guacamole (2 oz)", "hot sauce", "cooking oil", "cumin", "chili powder",
    "garlic powder", "smoked paprika", "adobo seasoning", "ancho chili powder",
}

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class Ingredient:
    name: str
    quantity: str
    in_stock: bool
    unit_cost: float


@dataclass
class LTOCandidate:
    id: int
    name: str
    tagline: str
    description: str
    icon: str
    trend_keywords: list[str]
    ingredients: list[Ingredient]
    sale_price: float
    overhead_multiplier: float = 1.30
    platform_scores: dict[str, float] = field(default_factory=dict)
    trend_score: float = 0.0
    feasibility_score: float = 0.0
    margin_score: float = 0.0
    prep_score: float = 0.0
    final_score: float = 0.0

    @property
    def food_cost(self) -> float:
        return round(sum(i.unit_cost for i in self.ingredients), 2)

    @property
    def total_cogs(self) -> float:
        return round(self.food_cost * self.overhead_multiplier, 2)

    @property
    def gross_profit(self) -> float:
        return round(self.sale_price - self.total_cogs, 2)

    @property
    def gross_margin_pct(self) -> float:
        return round((self.gross_profit / self.sale_price) * 100, 1) if self.sale_price else 0.0

    @property
    def new_ingredients(self) -> list[Ingredient]:
        return [i for i in self.ingredients if not i.in_stock]


# ---------------------------------------------------------------------------
# LTO Candidate Database
# ---------------------------------------------------------------------------

def _ing(name: str, quantity: str, multiplier: float = 1.0) -> Ingredient:
    return Ingredient(
        name=name,
        quantity=quantity,
        in_stock=name in STANDARD_PANTRY,
        unit_cost=round(PANTRY.get(name, 0.50) * multiplier, 2),
    )


CANDIDATES: list[LTOCandidate] = [
    LTOCandidate(
        id=1,
        name="Birria Smash Taco",
        tagline="The internet's obsession — now on our menu",
        description=(
            "Two smash-style beef patties soaked in rich birria consommé, crisped on the plancha "
            "and loaded into corn tortillas with melted cheese and diced onion. "
            "Served with a cup of consommé for dipping. Pure TikTok gold, and the flavour "
            "backs up every bit of the hype."
        ),
        icon="🌮",
        trend_keywords=["birria tacos", "birria smash burger", "birria taco recipe", "birria quesadilla"],
        sale_price=13.99,
        ingredients=[
            _ing("ground beef (4 oz)",      "8 oz (2 patties)", 2.0),
            _ing("corn tortilla (6\")",      "2 each",           2.0),
            _ing("birria consommé (4 oz)",   "4 oz + dipping cup"),
            _ing("dried ancho chilis",       "1 serving"),
            _ing("shredded cheese (1 oz)",   "1 oz melted"),
            _ing("diced onion",              "1 tbsp"),
            _ing("fresh cilantro",           "garnish"),
            _ing("lime wedge",               "1 each"),
            _ing("cumin",                    "pinch"),
        ],
    ),
    LTOCandidate(
        id=2,
        name="Hot Honey Crispy Chicken Taco",
        tagline="Sweet heat in every bite",
        description=(
            "Crispy golden chicken thigh drizzled with our house hot honey glaze, "
            "tucked into a warm flour tortilla with shredded lettuce, pickled jalapeño, "
            "and a smoky chipotle mayo drizzle. The sweet-heat trend that's dominating "
            "every food platform right now."
        ),
        icon="🍗",
        trend_keywords=["hot honey chicken", "spicy honey chicken sandwich", "hot honey", "crispy chicken taco"],
        sale_price=8.99,
        ingredients=[
            _ing("chicken breast (4 oz)",    "4 oz crispy"),
            _ing("flour tortilla (10\")",     "1 each"),
            _ing("hot honey (0.5 oz)",        "0.5 oz drizzle"),
            _ing("hot sauce",                 "dash"),
            _ing("smoked pepper mayo (1 oz)",      "1 oz"),
            _ing("lettuce",                   "small handful"),
            _ing("pickled jalapeño",          "3 slices"),
            _ing("lime wedge",                "1 each"),
        ],
    ),
    LTOCandidate(
        id=3,
        name="Elote Street Corn Cup",
        tagline="Mexico's favourite street snack — in a cup",
        description=(
            "Roasted corn kernels tossed in smoked pepper mayo and fresh lime juice, "
            "topped with crumbled cotija cheese, elote seasoning, and a bold "
            "sprinkle of tajín. The perfect LTO side or snack — low cost, high "
            "margin, and already viral on every platform."
        ),
        icon="🌽",
        trend_keywords=["elote cup", "mexican street corn", "elote recipe", "corn in a cup"],
        sale_price=4.99,
        ingredients=[
            _ing("roasted corn (3 oz)",       "3 oz"),
            _ing("smoked pepper mayo (1 oz)",       "1 oz"),
            _ing("cotija cheese (1 oz)",       "1 oz"),
            _ing("lime wedge",                 "1 squeeze"),
            _ing("elote seasoning",            "1 tsp"),
            _ing("tajín (pinch)",              "1 pinch"),
            _ing("fresh cilantro",             "garnish"),
        ],
    ),
    LTOCandidate(
        id=4,
        name="K-BBQ Fusion Taco",
        tagline="Seoul meets Mexico City",
        description=(
            "Gochujang-glazed grilled chicken with a Korean BBQ caramelised finish, "
            "served in corn tortillas with pickled radish, sesame-cilantro slaw, "
            "and a drizzle of verde salsa. East-meets-West fusion tacos are "
            "exploding in every major food city and trending hard on Reddit."
        ),
        icon="🥢",
        trend_keywords=["korean bbq tacos", "kbbq taco", "korean fusion taco", "gochujang chicken"],
        sale_price=9.99,
        ingredients=[
            _ing("chicken breast (4 oz)",     "4 oz"),
            _ing("corn tortilla (6\")",        "2 each", 2.0),
            _ing("gochujang (1 oz)",           "1 oz marinade"),
            _ing("Korean BBQ glaze (1 oz)",    "1 oz finish"),
            _ing("pickled radish (1 oz)",      "1 oz"),
            _ing("sesame oil (dash)",          "dash"),
            _ing("fresh cilantro",             "garnish"),
            _ing("verde salsa (2 oz)",         "1 oz"),
            _ing("lime wedge",                 "1 each"),
        ],
    ),
    LTOCandidate(
        id=5,
        name="Loaded Birria Cheese Fries",
        tagline="The cheesiest, messiest thing we've ever made",
        description=(
            "Crispy fries buried under slow-braised birria beef, smothered in melted "
            "cheese, finished with pickled jalapeños, diced onion, and fresh cilantro. "
            "A separate cup of rich birria consommé for dipping turns this into an "
            "absolute flavour bomb. Born on social media, destined for our fryer."
        ),
        icon="🍟",
        trend_keywords=["birria fries", "loaded birria fries", "cheese fries tiktok", "birria loaded fries"],
        sale_price=10.99,
        ingredients=[
            _ing("fries (6 oz)",              "6 oz"),
            _ing("ground beef (4 oz)",         "4 oz birria"),
            _ing("birria consommé (4 oz)",     "2 oz over + 2 oz dip"),
            _ing("shredded cheese (1 oz)",     "2 oz melted", 2.0),
            _ing("pickled jalapeño",           "4 slices"),
            _ing("diced onion",                "1 tbsp"),
            _ing("fresh cilantro",             "garnish"),
            _ing("sour cream (1 oz)",          "1 oz drizzle"),
        ],
    ),
    LTOCandidate(
        id=6,
        name="Viral Smash Quesadilla",
        tagline="The fold that broke the internet",
        description=(
            "The TikTok quesadilla hack viewed 2B+ times — a flour tortilla sliced "
            "and folded with smoky adobo chicken, melted cheese, and pickled jalapeño, "
            "pressed dead flat on the plancha until shatteringly crispy. "
            "100% existing inventory. Zero new procurement needed."
        ),
        icon="🫔",
        trend_keywords=["smash quesadilla", "tiktok quesadilla hack", "crispy quesadilla", "folded quesadilla"],
        sale_price=9.49,
        ingredients=[
            _ing("flour tortilla (10\")",      "1 large"),
            _ing("chicken breast (4 oz)",      "4 oz shredded"),
            _ing("shredded cheese (1 oz)",     "2 oz", 2.0),
            _ing("smoky adobo sauce (1 oz)",      "1 oz"),
            _ing("pickled jalapeño",           "3 slices"),
            _ing("verde salsa (2 oz)",         "2 oz side"),
            _ing("sour cream (1 oz)",          "1 oz side"),
        ],
    ),
    LTOCandidate(
        id=7,
        name="Crispy Carnitas Al Carbon Taco",
        tagline="Old school technique, new school hype",
        description=(
            "Slow-braised carnitas crisped up on a ripping-hot plancha, "
            "heaped into corn tortillas with roasted corn elote salsa, "
            "crumbled cotija, verde salsa, and a squeeze of lime. "
            "The al carbon trend is surging on Reddit and Google — "
            "and this one is nearly all existing inventory."
        ),
        icon="🔥",
        trend_keywords=["carnitas tacos", "al carbon taco", "crispy carnitas", "pollo al carbon"],
        sale_price=9.49,
        ingredients=[
            _ing("carnitas (4 oz)",            "4 oz crisped"),
            _ing("corn tortilla (6\")",         "2 each", 2.0),
            _ing("roasted corn (3 oz)",         "2 oz elote salsa"),
            _ing("cotija cheese (1 oz)",        "0.5 oz crumble"),
            _ing("verde salsa (2 oz)",          "1 oz"),
            _ing("lime wedge",                  "1 each"),
            _ing("fresh cilantro",              "garnish"),
            _ing("diced onion",                 "1 tbsp"),
        ],
    ),
    LTOCandidate(
        id=8,
        name="Smoky Honey Chicken Burrito",
        tagline="Sweet, smoky, completely addictive",
        description=(
            "Smoky adobo-marinated grilled chicken, rice, black beans, shredded cheese, "
            "and sour cream wrapped in a flour tortilla — finished with a drizzle of hot "
            "honey and smoky adobo sauce. The sweet-heat burrito upgrade that's been "
            "trending hard across every food platform."
        ),
        icon="🌯",
        trend_keywords=["honey chicken burrito", "hot honey burrito", "sweet spicy burrito", "smoky chicken wrap"],
        sale_price=10.99,
        ingredients=[
            _ing("flour tortilla (10\")",       "1 extra-large"),
            _ing("chicken breast (4 oz)",       "4 oz grilled"),
            _ing("rice (4 oz)",                 "4 oz"),
            _ing("black beans (3 oz)",          "3 oz"),
            _ing("shredded cheese (1 oz)",      "1 oz"),
            _ing("sour cream (1 oz)",           "1 oz"),
            _ing("smoky adobo sauce (1 oz)",       "1 oz"),
            _ing("hot honey (0.5 oz)",          "0.5 oz drizzle"),
            _ing("smoked pepper mayo (1 oz)",        "0.5 oz"),
        ],
    ),
    LTOCandidate(
        id=9,
        name="Guac Smash Beef Taco",
        tagline="Everything the internet obsesses over — in one taco",
        description=(
            "A smash-style thin crispy beef patty cooked on a ripping-hot flat-top, "
            "served in a corn tortilla with a generous scoop of house guacamole, "
            "diced tomato, and smoked pepper mayo. Combines the two hottest food trends — "
            "smash burgers and loaded tacos — with near-zero new procurement."
        ),
        icon="🥑",
        trend_keywords=["smash burger taco", "smash taco", "guac smash burger", "viral smash burger"],
        sale_price=8.49,
        ingredients=[
            _ing("ground beef (4 oz)",          "4 oz smash patty"),
            _ing("corn tortilla (6\")",          "2 each", 2.0),
            _ing("guacamole (2 oz)",             "2 oz"),
            _ing("diced tomato",                 "1 tbsp"),
            _ing("smoked pepper mayo (1 oz)",         "1 oz"),
            _ing("shredded cheese (1 oz)",       "0.5 oz"),
            _ing("fresh cilantro",               "garnish"),
            _ing("lime wedge",                   "1 each"),
        ],
    ),
    LTOCandidate(
        id=10,
        name="Birria Consommé Dip Burrito",
        tagline="French dip energy. Mexican soul.",
        description=(
            "A fat beef burrito packed with birria-braised ground beef, rice, "
            "shredded cheese, and pico de gallo — served alongside a steaming cup "
            "of spiced birria consommé for dipping. The birria-dip format is "
            "exploding on all platforms and this is the simplest way to bring "
            "it to a burrito format."
        ),
        icon="🫕",
        trend_keywords=["birria burrito", "birria dip", "birria consomme burrito", "birria dipping sauce"],
        sale_price=12.49,
        ingredients=[
            _ing("flour tortilla (10\")",        "1 extra-large"),
            _ing("ground beef (4 oz)",            "4 oz birria"),
            _ing("birria consommé (4 oz)",        "2 oz + 4 oz dip cup", 1.5),
            _ing("rice (4 oz)",                   "4 oz"),
            _ing("shredded cheese (1 oz)",        "1 oz"),
            _ing("diced tomato",                  "1 tbsp pico"),
            _ing("diced onion",                   "1 tbsp pico"),
            _ing("fresh cilantro",                "garnish"),
            _ing("dried ancho chilis",            "seasoning"),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Platform Scrapers
# ---------------------------------------------------------------------------

def _fetch_trending_rss() -> set[str]:
    """
    Pull recent UK food trend headlines from Google News RSS.
    Free, no auth, reflects what food topics are actually in the news.
    """
    queries = ["food trends UK", "trending food", "viral food UK"]
    headlines: set[str] = set()
    for q in queries:
        url = (
            f"https://news.google.com/rss/search"
            f"?q={q.replace(' ', '+')}&hl=en-GB&gl=GB&ceid=GB:en"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            r.raise_for_status()
            root = ET.fromstring(r.text)
            for item in root.iter("item"):
                title = item.findtext("title", "")
                headlines.add(title.lower())
            time.sleep(0.5)
        except Exception as exc:
            print(f"  [warn] Google News RSS ({q}): {exc}")
    return headlines


def _autocomplete_score(keyword: str, geo: str = "GB") -> float:
    """
    Score a keyword 0–1 using Google's Autocomplete API.
    Higher rank in suggestions = higher score. Falls back to 0.25 if absent.
    """
    try:
        r = requests.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "firefox", "q": keyword, "hl": "en-GB", "gl": geo},
            headers=HEADERS,
            timeout=8,
        )
        suggestions = json.loads(r.text)[1]
        kw_words = set(keyword.lower().split())
        for rank, s in enumerate(suggestions[:10]):
            overlap = len(kw_words & set(s.lower().split())) / max(len(kw_words), 1)
            if overlap >= 0.5:
                # Top suggestion = ~1.0, position 10 = ~0.50
                return round(0.50 + overlap * (1.0 - rank / 10) * 0.50, 4)
        return 0.25
    except Exception as exc:
        print(f"  [warn] Autocomplete '{keyword}': {exc}")
        return 0.50


def fetch_google_trends(keywords: list[str]) -> tuple[dict[str, float], bool]:
    """
    Scores keywords using two free, no-auth Google endpoints:
      1. Daily Trends RSS  — +0.25 bonus if keyword words appear in today's hot topics
      2. Autocomplete API  — base score from search suggestion rank (0.25–1.0)
    """
    print("  Fetching Google Trends RSS ...")
    trending = _fetch_trending_rss()
    print(f"  {len(trending)} trending topics found")

    scores: dict[str, float] = {}
    for kw in keywords:
        base = _autocomplete_score(kw)
        kw_words = set(kw.lower().split())
        bonus = 0.25 if any(any(w in t for w in kw_words) for t in trending) else 0.0
        scores[kw] = round(min(base + bonus, 1.0), 4)
        time.sleep(0.3)

    return scores, True


def fetch_reddit_trends(keywords: list[str]) -> dict[str, float]:
    subreddits = ["food", "recipes", "fastfood", "FoodPorn", "mildlyinteresting"]
    mention_counts: dict[str, int] = {kw: 0 for kw in keywords}
    max_possible = len(subreddits) * 25

    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/top.json?limit=25&t=week"
        try:
            r = requests.get(url, headers=HEADERS, timeout=8)
            r.raise_for_status()
            posts = r.json().get("data", {}).get("children", [])
            for post in posts:
                title = post.get("data", {}).get("title", "").lower()
                for kw in keywords:
                    if any(w in title for w in kw.lower().split()):
                        mention_counts[kw] += 1
            time.sleep(0.5)
        except Exception as exc:
            print(f"  [warn] Reddit r/{sub}: {exc}")

    return {
        kw: round(min(mention_counts[kw] / max(max_possible, 1) * 12, 1.0), 4)
        for kw in keywords
    }


def fetch_youtube_trends(keywords: list[str]) -> tuple[dict[str, float], bool]:
    """
    Scores keywords by total view count of the top 10 recent UK food videos
    for each keyword. Requires a free YouTube Data API v3 key.
    Score scale: log10(views) / 7  →  1M views ≈ 0.86, 10M ≈ 1.0, 10k ≈ 0.57
    """
    if not YOUTUBE_API_KEY:
        print("  [warn] YOUTUBE_API_KEY not set — skipping YouTube")
        print("         Get a free key at https://console.cloud.google.com")
        return {kw: 0.50 for kw in keywords}, False

    scores: dict[str, float] = {}
    published_after = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    for kw in keywords:
        try:
            # Search for recent videos
            search = requests.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "key": YOUTUBE_API_KEY,
                    "q": f"{kw} food recipe",
                    "type": "video",
                    "regionCode": "GB",
                    "relevanceLanguage": "en",
                    "publishedAfter": published_after,
                    "maxResults": 10,
                    "order": "viewCount",
                    "part": "id",
                },
                timeout=10,
            )
            search_data = search.json()

            if "error" in search_data:
                print(f"  [warn] YouTube API: {search_data['error']['message']}")
                return {kw: 0.50 for kw in keywords}, False

            video_ids = [i["id"]["videoId"] for i in search_data.get("items", []) if "videoId" in i.get("id", {})]
            if not video_ids:
                scores[kw] = 0.25
                continue

            # Fetch view counts
            stats = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"key": YOUTUBE_API_KEY, "id": ",".join(video_ids), "part": "statistics"},
                timeout=10,
            )
            total_views = sum(
                int(item.get("statistics", {}).get("viewCount", 0))
                for item in stats.json().get("items", [])
            )

            scores[kw] = round(min(math.log10(max(total_views, 1)) / 7.0, 1.0), 4)
            time.sleep(0.2)

        except Exception as exc:
            print(f"  [warn] YouTube '{kw}': {exc}")
            scores[kw] = 0.50

    return scores, True


def fetch_wikipedia_trends(keywords: list[str]) -> tuple[dict[str, float], bool]:
    """
    Scores keywords by English Wikipedia pageviews over the last 7 days.
    Maps each keyword to a known food article title to avoid unreliable
    search API calls. Articles not in the map get scored 0.25 (neutral).
    Score scale: log10(weekly views) / 5  →  100k views ≈ 1.0, 10k ≈ 0.8, 1k ≈ 0.6
    """
    # Keyword substring → Wikipedia article title (exact, URL-encoded spaces as _)
    ARTICLE_MAP = {
        "birria":        "Birria",
        "carnitas":      "Carnitas",
        "quesadilla":    "Quesadilla",
        "burrito":       "Burrito",
        "guacamole":     "Guacamole",
        "elote":         "Elote_(dish)",
        "korean bbq":    "Korean_barbecue",
        "kbbq":          "Korean_barbecue",
        "gochujang":     "Gochujang",
        "smash burger":  "Smash_burger",
        "taco":          "Taco",
        "hot honey":     "Hot_honey",
        "al carbon":     "Carne_asada",
        "pollo":         "Pollo_a_la_brasa",
        "street corn":   "Elote_(dish)",
        "cheese fries":  "Cheesy_chips",
    }

    now = datetime.now()
    start_str = (now - timedelta(days=7)).strftime("%Y%m%d")
    end_str = now.strftime("%Y%m%d")
    wiki_headers = {"User-Agent": "TortillasLTOFinder/1.0 (food trend analysis)"}

    def find_article(kw: str) -> Optional[str]:
        kl = kw.lower()
        for term, article in ARTICLE_MAP.items():
            if term in kl:
                return article
        return None

    def pageviews(article: str) -> int:
        url = (
            f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
            f"/en.wikipedia/all-access/all-agents/{article}/daily/{start_str}/{end_str}"
        )
        r = requests.get(url, headers=wiki_headers, timeout=8)
        if r.status_code != 200 or not r.text.strip():
            return 0
        return sum(item.get("views", 0) for item in r.json().get("items", []))

    # Deduplicate — multiple keywords can map to the same article
    article_cache: dict[str, int] = {}
    scores: dict[str, float] = {}

    for kw in keywords:
        article = find_article(kw)
        if not article:
            scores[kw] = 0.25
            continue
        try:
            if article not in article_cache:
                article_cache[article] = pageviews(article)
                time.sleep(0.5)
            total = article_cache[article]
            scores[kw] = round(min(math.log10(max(total, 1)) / 5.0, 1.0), 4)
        except Exception as exc:
            print(f"  [warn] Wikipedia '{article}': {exc}")
            scores[kw] = 0.25

    fetched = sum(1 for v in article_cache.values() if v > 0)
    print(f"  {fetched}/{len(article_cache)} Wikipedia articles fetched")
    return scores, True


# ---------------------------------------------------------------------------
# Scoring Engine
# ---------------------------------------------------------------------------

# Base weights — normalised automatically if a platform is unavailable
BASE_WEIGHTS = {"youtube": 0.35, "reddit": 0.25, "google": 0.25, "wikipedia": 0.15}
SCORE_WEIGHTS = {"trend": 0.40, "feasibility": 0.30, "margin": 0.20, "prep": 0.10}


def score_candidates(candidates: list[LTOCandidate], offline: bool = False) -> list[LTOCandidate]:
    all_kw = list({kw for c in candidates for kw in c.trend_keywords})
    neutral = {kw: 0.50 for kw in all_kw}

    if offline:
        print("[*] Offline mode — skipping live fetches")
        sources = {"reddit": neutral, "google": neutral, "youtube": neutral, "wikipedia": neutral}
    else:
        print("[*] Fetching Google signals ...")
        google, google_ok = fetch_google_trends(all_kw)
        print("[*] Fetching Reddit trends ...")
        reddit = fetch_reddit_trends(all_kw)
        print("[*] Fetching YouTube trends ...")
        youtube, youtube_ok = fetch_youtube_trends(all_kw)
        print("[*] Fetching Wikipedia pageviews ...")
        wikipedia, wiki_ok = fetch_wikipedia_trends(all_kw)

        sources = {"reddit": reddit, "google": google if google_ok else None,
                   "youtube": youtube if youtube_ok else None,
                   "wikipedia": wikipedia if wiki_ok else None}

    # Drop unavailable platforms and re-normalise weights to sum to 1.0
    active = {p: s for p, s in sources.items() if s is not None}
    raw_w = {p: BASE_WEIGHTS[p] for p in active}
    total_w = sum(raw_w.values())
    weights = {p: w / total_w for p, w in raw_w.items()}
    print(f"[*] Active platforms: {', '.join(f'{p} ({w:.0%})' for p, w in weights.items())}")

    for c in candidates:
        def avg(m: dict) -> float:
            vals = [m.get(kw, 0.0) for kw in c.trend_keywords]
            return round(sum(vals) / len(vals), 4)

        c.platform_scores = {p: avg(s) for p, s in active.items()}



        c.trend_score = round(sum(weights.get(p, 0) * s for p, s in c.platform_scores.items()), 4)

        in_stock = sum(1 for i in c.ingredients if i.in_stock)
        c.feasibility_score = round(in_stock / len(c.ingredients), 4)

        c.margin_score = round(min(max((c.gross_margin_pct - 40) / 40, 0.0), 1.0), 4)

        c.prep_score = round(max(1.0 - len(c.new_ingredients) * 0.15, 0.10), 4)

        c.final_score = round(
            SCORE_WEIGHTS["trend"]       * c.trend_score +
            SCORE_WEIGHTS["feasibility"] * c.feasibility_score +
            SCORE_WEIGHTS["margin"]      * c.margin_score +
            SCORE_WEIGHTS["prep"]        * c.prep_score,
            4,
        )

    return sorted(candidates, key=lambda c: c.final_score, reverse=True)


# ---------------------------------------------------------------------------
# HTML — shared styles
# ---------------------------------------------------------------------------

_PLATFORM_META = {
    "youtube":   ("YouTube",   "#ff0000", "#fff"),
    "reddit":    ("Reddit",    "#ff4500", "#fff"),
    "google":    ("Google",    "#4285f4", "#fff"),
    "wikipedia": ("Wikipedia", "#202122", "#fff"),
}

_CSS_RESET = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f0a05;color:#f0ede8;min-height:100vh}
a{color:inherit;text-decoration:none}
.gt{background:linear-gradient(90deg,#f59e0b,#ef4444);-webkit-background-clip:text;
    -webkit-text-fill-color:transparent;background-clip:text}
"""

_CSS_INDEX = _CSS_RESET + """
header{text-align:center;padding:48px 24px 32px;border-bottom:1px solid #2a1f0f}
header h1{font-size:2.6rem;font-weight:800;letter-spacing:-.5px}
header p{margin-top:10px;color:#a09070;font-size:1.05rem}
.badge-pill{display:inline-block;background:#1e1208;border:1px solid #3a2810;
  border-radius:999px;padding:4px 16px;font-size:.78rem;color:#f59e0b;margin-top:12px;
  letter-spacing:1px;text-transform:uppercase}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
  gap:24px;padding:40px 32px;max-width:1400px;margin:0 auto}
.card{background:#1a100a;border:1px solid #2e1c0e;border-radius:16px;padding:28px 24px 24px;
  display:flex;flex-direction:column;gap:12px;cursor:pointer;position:relative;overflow:hidden;
  transition:transform .2s,border-color .2s,box-shadow .2s}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,#f59e0b,#ef4444);opacity:0;transition:opacity .2s}
.card:hover{transform:translateY(-4px);border-color:#f59e0b44;box-shadow:0 8px 32px #f59e0b22}
.card:hover::before{opacity:1}
.rank-tag{position:absolute;top:16px;right:16px;background:#2e1c0e;color:#f59e0b;
  font-size:.72rem;font-weight:700;padding:3px 9px;border-radius:999px}
.icon{font-size:3.2rem;line-height:1;margin-bottom:4px}
.name{font-size:1.25rem;font-weight:700;line-height:1.2}
.tagline{color:#c0a878;font-size:.88rem}
.meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
.pill{background:#2e1c0e;border-radius:999px;padding:4px 11px;font-size:.78rem;color:#e0c898}
.pill.p{color:#4ade80;background:#0a2010}.pill.m{color:#60a5fa;background:#0a1530}
.pill.s{color:#fb923c;background:#1f1008}
.pbadges{display:flex;gap:6px;flex-wrap:wrap;margin-top:2px}
.pb{font-size:.68rem;font-weight:700;padding:2px 8px;border-radius:4px;
  text-transform:uppercase;letter-spacing:.5px}
.cta{display:inline-block;margin-top:auto;padding:10px 0;font-weight:700;font-size:.9rem;
  color:#f59e0b;border-top:1px solid #2e1c0e;width:100%;text-align:center;transition:color .15s}
.card:hover .cta{color:#fcd34d}
footer{text-align:center;padding:32px;color:#4a3820;font-size:.8rem;border-top:1px solid #1e1208}
"""

_CSS_DETAIL = _CSS_RESET + """
.back{display:inline-flex;align-items:center;gap:6px;margin:24px 32px;font-size:.9rem;
  color:#a09070;border:1px solid #2e1c0e;padding:8px 16px;border-radius:8px;
  transition:color .15s,border-color .15s}
.back:hover{color:#f59e0b;border-color:#f59e0b44}
.hero{max-width:900px;margin:0 auto;padding:0 32px 40px}
.hero .icon{font-size:5rem;line-height:1;margin-bottom:16px}
.hero h1{font-size:2.4rem;font-weight:800;line-height:1.1;margin-bottom:8px}
.hero .tl{font-size:1.1rem;color:#c0a878;margin-bottom:20px}
.hero .desc{color:#d0c0a8;line-height:1.7;max-width:680px}
.pbadges{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}
.pb{font-size:.75rem;font-weight:700;padding:4px 12px;border-radius:6px;
  text-transform:uppercase;letter-spacing:.5px}
.sec{max-width:900px;margin:32px auto;padding:0 32px}
.sec h2{font-size:1.1rem;font-weight:700;text-transform:uppercase;letter-spacing:1px;
  color:#f59e0b;margin-bottom:16px;padding-bottom:8px;border-bottom:1px solid #2e1c0e}
.pgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px}
.pc{background:#1a100a;border:1px solid #2e1c0e;border-radius:12px;padding:20px;text-align:center}
.pc .lbl{font-size:.75rem;color:#a09070;text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px}
.pc .val{font-size:1.6rem;font-weight:800}
.pc.sale .val{color:#4ade80}.pc.cost .val{color:#f87171}
.pc.profit .val{color:#60a5fa}.pc.margin .val{color:#fb923c}
table{width:100%;border-collapse:collapse;font-size:.9rem}
th{text-align:left;padding:10px 14px;background:#1a100a;color:#a09070;
  font-size:.75rem;text-transform:uppercase;letter-spacing:.8px;border-bottom:1px solid #2e1c0e}
td{padding:11px 14px;border-bottom:1px solid #1a100a;color:#d0c0a8}
tr:hover td{background:#1e1208}
.sy{color:#4ade80;font-size:.75rem;font-weight:700}
.sn{color:#fb923c;font-size:.75rem;font-weight:700}
.cc{color:#a0c8a0;font-family:'Courier New',monospace}
.tr td{font-weight:700;color:#f0ede8;border-top:2px solid #2e1c0e}
.note{background:#1a0e08;border:1px solid #f59e0b44;border-radius:10px;
  padding:16px 20px;margin-top:16px;font-size:.87rem;color:#c0a060}
footer{text-align:center;padding:40px 32px 32px;color:#4a3820;
  font-size:.8rem;border-top:1px solid #1e1208;margin-top:40px}
"""


def _pbadges(platform_scores: dict, scale: float = 1.0) -> str:
    parts = []
    for pid, score in sorted(platform_scores.items(), key=lambda x: -x[1]):
        label, bg, fg = _PLATFORM_META[pid]
        opacity = max(0.45, round(score, 2))
        size = f"font-size:{round(0.68 * scale, 2)}rem"
        parts.append(
            f'<span class="pb" style="background:{bg};color:{fg};opacity:{opacity};{size}">'
            f'{label} {round(score * 100)}%</span>'
        )
    return "\n".join(parts)


def _bar(score: float) -> str:
    pct = round(score * 100)
    color = "#f59e0b" if score > 0.70 else "#fb923c" if score > 0.50 else "#9ca3af"
    return (
        f'<div style="background:#2e1c0e;border-radius:4px;height:6px;width:100%;margin-top:4px">'
        f'<div style="background:{color};border-radius:4px;height:6px;width:{pct}%"></div></div>'
    )


# ---------------------------------------------------------------------------
# HTML — index page
# ---------------------------------------------------------------------------

def generate_index(top5: list[LTOCandidate], timestamp: str) -> str:
    cards = []
    for rank, c in enumerate(top5, 1):
        cards.append(f"""
        <a href="detail_{rank}.html">
          <div class="card">
            <div class="rank-tag">#{rank} Pick</div>
            <div class="icon">{c.icon}</div>
            <div class="name">{c.name}</div>
            <div class="tagline">{c.tagline}</div>
            <div class="pbadges">{_pbadges(c.platform_scores)}</div>
            <div class="meta">
              <span class="pill p">£{c.sale_price:.2f}</span>
              <span class="pill m">{c.gross_margin_pct}% margin</span>
              <span class="pill s">Score {round(c.final_score * 100)}%</span>
            </div>
            <div class="cta">View Full Details →</div>
          </div>
        </a>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Tortillas — Special Menu Picks</title>
  <style>{_CSS_INDEX}</style>
</head>
<body>
  <header>
    <h1 class="gt">Tortillas LTO Picks</h1>
    <p>Top 5 Limited-Time-Offer candidates — social media trends × menu feasibility</p>
    <div class="badge-pill">Generated {timestamp}</div>
  </header>
  <div class="grid">{"".join(cards)}</div>
  <footer>
    Sources: Google Trends · Reddit (live)<br>
    Weights: Trend 40% · Feasibility 30% · Margin 20% · Prep 10%
  </footer>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTML — detail page
# ---------------------------------------------------------------------------

def generate_detail(c: LTOCandidate, rank: int, total: int) -> str:
    # pricing cards
    pricing = f"""
    <div class="pgrid">
      <div class="pc sale"><div class="lbl">Sale Price</div><div class="val">£{c.sale_price:.2f}</div></div>
      <div class="pc cost"><div class="lbl">Total COGS</div><div class="val">£{c.total_cogs:.2f}</div></div>
      <div class="pc profit"><div class="lbl">Gross Profit</div><div class="val">£{c.gross_profit:.2f}</div></div>
      <div class="pc margin"><div class="lbl">Gross Margin</div><div class="val">{c.gross_margin_pct}%</div></div>
    </div>
    <p style="margin-top:14px;font-size:.82rem;color:#6a5038">
      * COGS = food cost (£{c.food_cost:.2f}) × {c.overhead_multiplier} overhead multiplier
    </p>"""

    # ingredients table
    rows = []
    for ing in c.ingredients:
        stock = f'<span class="sy">✓ In Stock</span>' if ing.in_stock else f'<span class="sn">⚠ Source</span>'
        rows.append(
            f"<tr><td>{ing.name}</td><td>{ing.quantity}</td>"
            f"<td>{stock}</td><td class='cc'>£{ing.unit_cost:.2f}</td></tr>"
        )
    rows.append(
        f"<tr class='tr'><td colspan='3'>Food Cost Subtotal</td>"
        f"<td class='cc'>£{c.food_cost:.2f}</td></tr>"
    )
    ing_table = f"""
    <table>
      <thead><tr><th>Ingredient</th><th>Quantity</th><th>Status</th><th>Unit Cost</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""

    # procurement notice
    procurement = ""
    if c.new_ingredients:
        items = ", ".join(f"<strong>{i.name}</strong>" for i in c.new_ingredients)
        procurement = f'<div class="note"><strong style="color:#f59e0b">New procurement needed:</strong> {items}</div>'

    # trend breakdown
    trend_rows = []
    for pid, score in sorted(c.platform_scores.items(), key=lambda x: -x[1]):
        label, bg, fg = _PLATFORM_META[pid]
        trend_rows.append(
            f"<tr><td><span class='pb' style='background:{bg};color:{fg}'>{label}</span></td>"
            f"<td style='width:60%'>{_bar(score)}</td>"
            f"<td style='color:#f59e0b;font-weight:700;font-family:monospace'>{round(score*100)}%</td></tr>"
        )
    trend_section = f"""
    <table>
      <thead><tr><th>Platform</th><th>Trend Signal</th><th>Score</th></tr></thead>
      <tbody>{"".join(trend_rows)}</tbody>
    </table>
    <p style="margin-top:12px;font-size:.82rem;color:#6a5038">
      Weighted trend score: <strong style="color:#fb923c">{round(c.trend_score*100)}%</strong>
      &nbsp;·&nbsp; Final LTO score: <strong style="color:#f59e0b">{round(c.final_score*100)}%</strong>
    </p>"""

    prev_link = f'<a href="detail_{rank-1}.html" class="back" style="float:right">← #{rank-1}</a>' if rank > 1 else ""
    next_link = f'<a href="detail_{rank+1}.html" class="back" style="float:right;margin-right:8px">#{rank+1} →</a>' if rank < total else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{c.name} — Tortillas LTO Detail</title>
  <style>{_CSS_DETAIL}</style>
</head>
<body>
  <div style="max-width:900px;margin:0 auto;display:flex;align-items:center;justify-content:space-between">
    <a href="index.html" class="back">← All picks</a>
    <div>{prev_link}{next_link}</div>
  </div>
  <div class="hero">
    <div class="icon">{c.icon}</div>
    <h1 class="gt">{c.name}</h1>
    <div class="tl">{c.tagline}</div>
    <div class="desc">{c.description}</div>
    <div class="pbadges">{_pbadges(c.platform_scores, scale=1.0)}</div>
  </div>

  <div class="sec">
    <h2>Why This Trend?</h2>
    {trend_section}
  </div>

  <div class="sec">
    <h2>Pricing &amp; Margin</h2>
    {pricing}
  </div>

  <div class="sec">
    <h2>Ingredients &amp; Quantities</h2>
    {ing_table}
    {procurement}
  </div>

  <footer>Tortillas LTO Finder · Rank #{rank} of {total} candidates scored</footer>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Tortillas Special Menu Product Finder")
    parser.add_argument("--offline", action="store_true", help="Skip live scraping")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%d %b %Y, %H:%M")

    print("[*] Scoring LTO candidates ...")
    ranked = score_candidates(CANDIDATES, offline=args.offline)
    top5 = ranked[:5]

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("[*] Writing output/index.html ...")
    with open(os.path.join(OUTPUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(generate_index(top5, timestamp))

    for rank, candidate in enumerate(top5, 1):
        fname = f"detail_{rank}.html"
        print(f"[*] Writing output/{fname} ...")
        with open(os.path.join(OUTPUT_DIR, fname), "w", encoding="utf-8") as f:
            f.write(generate_detail(candidate, rank, 5))

    print(f"\n  Done — open output/index.html in your browser\n")
    print(f"  {'Rank':<5} {'Name':<36} {'Score':>6} {'Price':>7} {'Margin':>8}")
    print("  " + "-" * 66)
    for rank, c in enumerate(top5, 1):
        print(f"  #{rank:<4} {c.name:<36} {round(c.final_score*100):>5}%  £{c.sale_price:>5.2f}  {c.gross_margin_pct:>6.1f}%")


if __name__ == "__main__":
    main()