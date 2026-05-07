#!/usr/bin/env python3
"""
check_stock.py
Scrapes Tortilla's live menu, determines which LTO ingredients they
already stock, then updates the In Stock / Source flags in all detail
HTML files accordingly.
"""

import re
import sys
import requests
from bs4 import BeautifulSoup
from pathlib import Path

MENU_URLS = [
    "https://www.tortilla.co.uk/menu",
    "https://www.tortilla.co.uk/",
]

OUTPUT_DIR = Path(__file__).parent / "output"
DETAIL_FILES = sorted(OUTPUT_DIR.glob("detail_*.html"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

# Ground-truth map derived from Tortilla's known menu.
# Keys are lowercase substrings to match against ingredient cell text.
# True = they have it, False = needs sourcing.
STOCK_MAP = {
    # ── Confirmed on Tortilla menu ──────────────────────────────────────────
    "flour tortilla":       True,
    "corn tortilla":        True,
    "rice":                 True,
    "black beans":          True,
    "pinto beans":          True,
    "chicken":              True,   # marinated chicken asado
    "ground beef":          True,   # barbacoa beef base
    "beef":                 True,
    "shredded cheese":      True,   # Monterey Jack
    "monterey jack":        True,
    "sour cream":           True,
    "guacamole":            True,
    "guac":                 True,
    "avocado":              True,
    "jalapeño":             True,
    "pickled jalapeño":     True,
    "salsa verde":          True,
    "verde salsa":          True,
    "salsa roja":           True,
    "pico de gallo":        True,
    "diced tomato":         True,
    "tomato":               True,
    "fresh cilantro":       True,   # used in guacamole
    "cilantro":             True,
    "lime":                 True,
    "lettuce":              True,
    "onion":                True,
    "diced onion":          True,
    "pickled red onion":    True,
    "chipotle":             True,
    "hot sauce":            True,
    "cumin":                True,   # standard spice
    # ── NOT on Tortilla menu — needs sourcing ───────────────────────────────
    "smoked pepper mayo":   False,
    "smoky adobo":          False,
    "adobo sauce":          False,
    "hot honey":            False,
    "birria":               False,
    "consommé":             False,
    "ancho chili":          False,
    "ancho chilis":         False,
    "dried ancho":          False,
}


def scrape_menu_text() -> str:
    """Return lowercased visible text from Tortilla's menu pages."""
    combined = ""
    for url in MENU_URLS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            combined += " " + soup.get_text(" ", strip=True).lower()
            print(f"  Fetched {url} ({len(r.text):,} bytes)")
        except Exception as exc:
            print(f"  Could not fetch {url}: {exc}")
    return combined


def ingredient_in_stock(name: str, menu_text: str) -> bool:
    """
    Decide whether an ingredient is stocked at Tortilla.
    1. Walk STOCK_MAP — first substring match wins.
    2. Fall back to looking for the core word in scraped menu text.
    3. Default to False (needs sourcing) when uncertain.
    """
    key = name.lower()

    for pattern, stocked in STOCK_MAP.items():
        if pattern in key:
            return stocked

    # Fallback: strip parenthetical quantities, look for the longest
    # meaningful word in the scraped menu text.
    clean = re.sub(r"\([^)]*\)", "", key).strip()
    words = [w for w in clean.split() if len(w) > 3]
    if words and menu_text:
        core = max(words, key=len)
        if core in menu_text:
            return True

    return False  # unknown → assume needs sourcing


def update_file(path: Path, menu_text: str) -> list[tuple[str, str, str]]:
    """
    Re-evaluate every ingredient row in one detail HTML file and patch
    the status <span> in-place using regex (preserves minified formatting).
    Returns a list of (ingredient, old_status, new_status) for changed rows.
    """
    html = path.read_text(encoding="utf-8")
    changes = []

    # Each ingredient row looks like:
    #   <tr><td>flour tortilla (10")</td><td>…</td>
    #       <td><span class="sy">✓ In Stock</span></td><td …>…</td></tr>
    row_pattern = re.compile(
        r'(<tr><td>)((?:(?!<tr>).)+?)(</td>(?:<td>[^<]*</td>))'
        r'(<td>)(<span class=["\']s[ny]["\']>[^<]+</span>)(</td>)',
        re.DOTALL,
    )

    def replacer(m: re.Match) -> str:
        ingredient = BeautifulSoup(m.group(2), "html.parser").get_text(strip=True)

        # Skip the subtotal row
        if "subtotal" in ingredient.lower():
            return m.group(0)

        in_stock = ingredient_in_stock(ingredient, menu_text)

        old_span = m.group(5)
        old_text = BeautifulSoup(old_span, "html.parser").get_text(strip=True)
        was_stocked = "In Stock" in old_text

        if in_stock == was_stocked:
            return m.group(0)  # no change needed

        if in_stock:
            new_span = '<span class="sy">✓ In Stock</span>'
            new_text = "✓ In Stock"
        else:
            new_span = '<span class="sn">⚠ Source</span>'
            new_text = "⚠ Source"

        changes.append((ingredient, old_text, new_text))
        return m.group(1) + m.group(2) + m.group(3) + m.group(4) + new_span + m.group(6)

    updated_html = row_pattern.sub(replacer, html)
    path.write_text(updated_html, encoding="utf-8")
    return changes


def main():
    print("=" * 50)
    print("  Tortilla Stock Checker")
    print("=" * 50)

    print("\nFetching live menu...")
    menu_text = scrape_menu_text()
    if not menu_text.strip():
        print("  No menu text retrieved — running on fallback map only.")

    if not DETAIL_FILES:
        print(f"\nNo detail_*.html files found in {OUTPUT_DIR}")
        sys.exit(1)

    print(f"\nChecking {len(DETAIL_FILES)} detail file(s)...\n")
    total_changes = 0

    for path in DETAIL_FILES:
        changes = update_file(path, menu_text)
        total_changes += len(changes)
        if changes:
            print(f"  {path.name} - {len(changes)} update(s):")
            for ingredient, old, new in changes:
                tag = "[IN STOCK]" if "In Stock" in new else "[SOURCE]"
                old_safe = old.encode("ascii", "replace").decode()
                new_safe = new.encode("ascii", "replace").decode()
                print(f"    {tag}  {ingredient!r:40s}  {old_safe!r} -> {new_safe!r}")
        else:
            print(f"  {path.name} — no changes")

    print(f"\nDone. {total_changes} total update(s) across all files.")
    if total_changes:
        print("Reload index.html in your browser to see the changes.")


if __name__ == "__main__":
    main()