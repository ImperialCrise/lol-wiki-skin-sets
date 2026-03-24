"""
Download the League of Legends Wiki page "List of skins by set" via the
MediaWiki API, parse the table, and write JSON for CLI queries.

Usage:
  py -3 scrape_lol_skin_sets.py
  py -3 scrape_lol_skin_sets.py --query-set "Spirit Blossom"
  py -3 scrape_lol_skin_sets.py --query-champion Yasuo
  py -3 scrape_lol_skin_sets.py --no-fetch --query-champion Illaoi --with-set-champions
  py -3 scrape_lol_skin_sets.py --no-fetch --query-champion Illaoi --with-set-champion
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import certifi
import requests
from bs4 import BeautifulSoup

WIKI_API = (
    "https://wiki.leagueoflegends.com/en-us/api.php"
    "?action=parse&page=List_of_skins_by_set&prop=text&format=json"
)
OUTPUT_DIR = Path(__file__).resolve().parent / "data"
SETS_JSON = OUTPUT_DIR / "skins_by_set.json"
CHAMPIONS_JSON = OUTPUT_DIR / "champions_to_sets.json"

COLUMNS = ("available", "legacy_vault", "rare", "unavailable")


def fetch_html() -> str:
    r = requests.get(
        WIKI_API,
        timeout=60,
        verify=certifi.where(),
        headers={"User-Agent": "lol-wiki-skin-sets/1.0"},
    )
    r.raise_for_status()
    payload = r.json()
    return payload["parse"]["text"]["*"]


def parse_skins_cell(td) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for li in td.find_all("li", class_=lambda c: c and "skin-icon" in c):
        ch = li.get("data-champion")
        sk = li.get("data-skin")
        if ch and sk:
            out.append({"champion": ch, "skin": sk})
    return out


def parse_table(html: str) -> tuple[dict, dict[str, list[dict]]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_=lambda c: c and "article-table" in c.split())
    if not table:
        table = soup.find("table", class_=lambda c: c and "wikitable" in c.split())
    if not table:
        raise RuntimeError("Skin table not found (expected article-table or wikitable).")

    sets_data: dict = {}
    champion_index: dict[str, list[dict]] = {}

    def add_index(champion: str, entry: dict) -> None:
        champion_index.setdefault(champion, []).append(entry)

    for tr in table.find_all("tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 8:
            continue
        set_name = cells[0].get_text(strip=True)
        if not set_name or set_name == "Total":
            continue

        buckets = {name: parse_skins_cell(cells[i]) for i, name in enumerate(COLUMNS, start=1)}
        meta = {
            "total_skins": cells[5].get_text(strip=True),
            "latest_addition": cells[6].get_text(strip=True),
            "days_ago": cells[7].get_text(strip=True),
        }
        sets_data[set_name] = {**buckets, **meta}

        for col in COLUMNS:
            for item in buckets[col]:
                add_index(
                    item["champion"],
                    {"set": set_name, "column": col, "skin": item["skin"]},
                )

    return sets_data, champion_index


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().casefold())


def find_sets_by_query(sets_data: dict, q: str) -> list[str]:
    nq = normalize(q)
    exact = [name for name in sets_data if normalize(name) == nq]
    if exact:
        return exact
    return [name for name in sets_data if nq in normalize(name)]


def print_set_columns(block: dict, *, indent_section: str = "  ", indent_item: str = "    ") -> None:
    """Print skins grouped by wiki column (Available, Legacy Vault, …)."""
    for col in COLUMNS:
        items = block.get(col, [])
        if not items:
            continue
        lines = [f"{x['champion']} ({x['skin']})" for x in items]
        label = col.replace("_", " ").title()
        print(f"{indent_section}[{label}] ({len(lines)})")
        for line in lines:
            print(f"{indent_item}{line}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape League of Legends Wiki: champion skins grouped by set",
    )
    parser.add_argument(
        "--query-set",
        metavar="NAME",
        help="Show one set (partial name match)",
    )
    parser.add_argument(
        "--query-champion",
        metavar="NAME",
        help="List sets that include this champion",
    )
    parser.add_argument(
        "--with-set-champions",
        "--with-set-champion",
        dest="with_set_champions",
        action="store_true",
        help="With --query-champion: under each set, print the full roster (same as --query-set)",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Do not download; read existing data/skins_by_set.json (and champions index)",
    )
    args = parser.parse_args()

    if args.with_set_champions and not args.query_champion:
        print(
            "--with-set-champions/--with-set-champion only applies with --query-champion.",
            file=sys.stderr,
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.no_fetch:
        if not SETS_JSON.is_file():
            print("Missing file:", SETS_JSON, file=sys.stderr)
            return 1
        raw_sets = json.loads(SETS_JSON.read_text(encoding="utf-8"))
        sets_data = raw_sets["sets"] if isinstance(raw_sets, dict) and "sets" in raw_sets else raw_sets
        if not CHAMPIONS_JSON.is_file():
            print("Missing file:", CHAMPIONS_JSON, file=sys.stderr)
            return 1
        champion_index = json.loads(CHAMPIONS_JSON.read_text(encoding="utf-8"))
    else:
        html = fetch_html()
        sets_data, champion_index = parse_table(html)
        meta = {
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "source_url": "https://wiki.leagueoflegends.com/en-us/List_of_skins_by_set",
            "wiki_api": WIKI_API.split("&format")[0] + "&format=json",
            "sets": sets_data,
        }
        SETS_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        CHAMPIONS_JSON.write_text(json.dumps(champion_index, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Wrote:", SETS_JSON)
        print("Wrote:", CHAMPIONS_JSON)

    if args.query_champion:
        key = args.query_champion.strip()
        entries = champion_index.get(key)
        if not entries:
            lower = {k.casefold(): k for k in champion_index}
            real = lower.get(key.casefold())
            entries = champion_index.get(real, []) if real else []
        if not entries:
            print(f"No entries for champion «{args.query_champion}».")
            return 0
        by_set: dict[str, list] = {}
        for e in entries:
            by_set.setdefault(e["set"], []).append(e)
        print(f"Champion: {key} ({len(by_set)} set(s))")
        for sname in sorted(by_set):
            rows = by_set[sname]
            by_col: dict[str, list[str]] = defaultdict(list)
            for r in rows:
                by_col[r["column"]].append(r["skin"])
            parts = []
            for col in COLUMNS:
                if col in by_col:
                    parts.append(f"{col}: {', '.join(by_col[col])}")
            print(f"  • {sname} — " + "; ".join(parts))
            if args.with_set_champions:
                roster = sets_data.get(sname)
                if not roster:
                    print("      (set missing in skins_by_set.json)")
                    continue
                print("      Full set roster:")
                print_set_columns(roster, indent_section="      ", indent_item="        ")
        return 0

    if args.query_set:
        matches = find_sets_by_query(sets_data, args.query_set)
        if not matches:
            print(f"No set matches «{args.query_set}».")
            return 0
        if len(matches) > 1:
            print("Multiple matches:", ", ".join(matches))
            print("Use a more specific name. Showing the first:", matches[0], "\n")
        name = matches[0]
        block = sets_data[name]
        print(f"Set: {name}")
        print(
            f"  Total (wiki): {block.get('total_skins', '')} | "
            f"Latest: {block.get('latest_addition', '')} | "
            f"Days ago: {block.get('days_ago', '')}",
        )
        print_set_columns(block, indent_section="  ", indent_item="    - ")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
