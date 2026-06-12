"""Best-effort odds scraper.

Tries Betexplorer (more scraper-tolerant than Oddschecker) to find the match
and pull public odds (1X2, over/under, HT/FT when available).

Everything here is failure-tolerant: any exception results in partial or empty
data, and the AI analyzer compensates with its own football knowledge.
"""
from __future__ import annotations

import logging
import sys
import unicodedata

import httpx
from bs4 import BeautifulSoup

from config import REQUEST_TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)

BASE = "https://www.betexplorer.com"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/",
}


def _norm(s: str) -> str:
    """Lowercase, strip accents."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _get(client: httpx.Client, url: str, **kw) -> httpx.Response | None:
    try:
        r = client.get(url, timeout=REQUEST_TIMEOUT, follow_redirects=True, **kw)
        if r.status_code == 200:
            return r
        log.warning("GET %s -> %s", url, r.status_code)
    except httpx.HTTPError as e:
        log.warning("GET %s failed: %s", url, e)
    return None


def find_match_url(client: httpx.Client, team1: str, team2: str) -> str | None:
    """Find the match on Betexplorer by scanning World Cup fixture pages.

    (The /search/ endpoint returns 404 — it is JS-only — so we go straight
    to the competition pages and match team names in link texts.)
    """
    candidates: list[str] = []
    for path in (
        "/football/world/world-cup/fixtures/",
        "/football/world/world-cup/",
        "/football/world/world-cup-2026/fixtures/",
        "/football/world/world-cup-2026/",
        "/next/football/",
    ):
        r = _get(client, BASE + path)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.select("a[href*='/football/']"):
            text = _norm(a.get_text(" "))
            if _norm(team1) in text and _norm(team2) in text:
                candidates.append(a.get("href", ""))
        if candidates:
            break
    if not candidates:
        return None
    url = candidates[0]
    return url if url.startswith("http") else BASE + url


def _parse_odds_tables(html: str) -> dict[str, list[dict]]:
    """Parse all odds tables on a Betexplorer match page.

    Returns {table_caption: [{bookmaker, odds: [...]}, ...]}
    """
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, list[dict]] = {}
    for table in soup.select("table"):
        rows = []
        for tr in table.select("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.select("td, th")]
            cells = [c for c in cells if c]
            if len(cells) >= 3:
                rows.append(cells)
        if rows:
            caption = table.get("id") or table.get("class", [""])[0] or "odds"
            key = str(caption)
            out.setdefault(key, [])
            for cells in rows[:15]:  # cap rows per table
                out[key].append({"bookmaker": cells[0], "odds": cells[1:8]})
    return out


def scrape_match_odds(team1: str, team2: str) -> dict:
    """Main entry point. Returns whatever odds data could be collected.

    {
      "source": "betexplorer" | "none",
      "match_url": str | None,
      "odds_tables": {...},      # raw parsed odds
      "notes": str,
    }
    """
    result = {"source": "none", "match_url": None, "odds_tables": {}, "notes": ""}
    try:
        with httpx.Client(headers=HEADERS) as client:
            url = find_match_url(client, team1, team2)
            if not url:
                result["notes"] = "Match page not found on Betexplorer."
                return result
            result["match_url"] = url
            r = _get(client, url)
            if not r:
                result["notes"] = "Match page found but could not be fetched."
                return result
            tables = _parse_odds_tables(r.text)
            if tables:
                result["source"] = "betexplorer"
                result["odds_tables"] = tables
                result["notes"] = "Main odds (typically 1X2; check tables)."
            else:
                result["notes"] = "Match page fetched but no odds tables parsed."
    except Exception as e:  # noqa: BLE001 - scraping must never crash the bot
        log.exception("Scraping failed")
        result["notes"] = f"Scraping error: {e}"
    return result


if __name__ == "__main__":
    # Quick manual test:  python scraper.py Argentina France
    logging.basicConfig(level=logging.INFO)
    t1 = sys.argv[1] if len(sys.argv) > 1 else "Argentina"
    t2 = sys.argv[2] if len(sys.argv) > 2 else "France"
    import json

    print(json.dumps(scrape_match_odds(t1, t2), indent=2)[:4000])
