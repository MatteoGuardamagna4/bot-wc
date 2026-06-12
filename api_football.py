"""API-Football (api-sports.io) client — primary data source.

Free tier: 100 requests/day. Provides fixtures and pre-match odds from
dozens of bookmakers (1X2, over/under, first-half markets).
"""
from __future__ import annotations

import logging
from statistics import mean

import httpx

from config import API_FOOTBALL_KEY, REQUEST_TIMEOUT

log = logging.getLogger(__name__)

BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}

_team_id_cache: dict[str, int] = {}


def _get(path: str, params: dict) -> list:
    """GET helper. Returns the 'response' list, or [] on any failure."""
    if not API_FOOTBALL_KEY or API_FOOTBALL_KEY.startswith("INSERISCI"):
        return []
    try:
        r = httpx.get(BASE + path, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
        data = r.json()
        if data.get("errors"):
            log.warning("API-Football %s errors: %s", path, data["errors"])
        return data.get("response", []) or []
    except Exception as e:  # noqa: BLE001
        log.warning("API-Football %s failed: %s", path, e)
        return []


def find_team_id(name: str) -> int | None:
    key = name.lower()
    if key in _team_id_cache:
        return _team_id_cache[key]
    for item in _get("/teams", {"search": name}):
        team = item.get("team", {})
        # Prefer national teams for a World Cup bot
        if team.get("national") or team.get("name", "").lower() == key:
            _team_id_cache[key] = team["id"]
            return team["id"]
    resp = _get("/teams", {"search": name})
    if resp:
        _team_id_cache[key] = resp[0]["team"]["id"]
        return _team_id_cache[key]
    return None


def find_fixture(team1: str, team2: str) -> dict | None:
    """Find the next fixture between two teams. Returns fixture dict or None."""
    id1, id2 = find_team_id(team1), find_team_id(team2)
    if not id1 or not id2:
        return None
    resp = _get("/fixtures/headtohead", {"h2h": f"{id1}-{id2}", "next": 1})
    if not resp:
        return None
    fx = resp[0]
    return {
        "fixture_id": fx["fixture"]["id"],
        "date": fx["fixture"].get("date"),
        "league": fx.get("league", {}).get("name"),
        "home": fx["teams"]["home"]["name"],
        "away": fx["teams"]["away"]["name"],
        "home_id": fx["teams"]["home"]["id"],
        "away_id": fx["teams"]["away"]["id"],
    }


def get_odds(fixture_id: int) -> dict:
    """Fetch pre-match odds and average them across bookmakers.

    Returns e.g.:
    {
      "match_winner": {"home": 2.1, "draw": 3.2, "away": 3.5},
      "over_under_2.5": {"over": 1.9, "under": 1.95},
      "first_half_winner": {"home": ..., "draw": ..., "away": ...},
      "bookmakers": 12,
    }
    """
    resp = _get("/odds", {"fixture": fixture_id})
    if not resp:
        return {}

    buckets: dict[str, dict[str, list[float]]] = {}

    def add(market: str, key: str, odd: str) -> None:
        try:
            buckets.setdefault(market, {}).setdefault(key, []).append(float(odd))
        except (TypeError, ValueError):
            pass

    n_bookies = 0
    for item in resp:
        for bm in item.get("bookmakers", []):
            n_bookies += 1
            for bet in bm.get("bets", []):
                name = bet.get("name", "")
                for v in bet.get("values", []):
                    val, odd = str(v.get("value")), v.get("odd")
                    if name == "Match Winner":
                        add("match_winner", val.lower(), odd)
                    elif name == "Goals Over/Under" and "2.5" in val:
                        add("over_under_2.5", val.split()[0].lower(), odd)
                    elif name == "First Half Winner":
                        add("first_half_winner", val.lower(), odd)
                    elif name in ("Exact Score", "Correct Score"):
                        add("correct_score", val, odd)

    out: dict = {"bookmakers": n_bookies}
    for market, keys in buckets.items():
        out[market] = {k: round(mean(v), 3) for k, v in keys.items() if v}
    return out


def get_match_data(team1: str, team2: str) -> dict:
    """Orchestrate: fixture + odds. Returns {} if nothing found."""
    fx = find_fixture(team1, team2)
    if not fx:
        return {}
    odds = get_odds(fx["fixture_id"])
    return {"fixture": fx, "odds": odds}
