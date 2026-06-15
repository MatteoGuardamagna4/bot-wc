"""API-Football (api-sports.io) client — primary data source.

Free tier: 100 requests/day. Provides fixtures and pre-match odds from
dozens of bookmakers (1X2, over/under, first-half markets).
"""
from __future__ import annotations

import logging
import time
from statistics import mean

import httpx

from config import API_FOOTBALL_KEY, REQUEST_TIMEOUT

log = logging.getLogger(__name__)

BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}

WC_LEAGUE_ID = 1  # FIFA World Cup
WC_SEASON = 2026
WC_SCHEDULE_TTL = 900  # re-fetch the WC calendar every 15 min

_team_id_cache: dict[str, int] = {}
_wc_teams_cache: list[dict] | None = None
_wc_fixtures_cache: list[dict] | None = None
_wc_fixtures_ts = 0.0


def _get(path: str, params: dict) -> list:
    """GET helper. Returns the 'response' list, or [] on any failure."""
    if not API_FOOTBALL_KEY or API_FOOTBALL_KEY.startswith("INSERISCI"):
        return []
    try:
        r = httpx.get(BASE + path, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
        data = r.json()
        if data.get("errors"):
            log.warning("API-Football %s errors: %s", path, data["errors"])
        resp = data.get("response", []) or []
        log.info("API-Football GET %s %s -> %d results", path, params, len(resp))
        return resp
    except Exception as e:  # noqa: BLE001
        log.warning("API-Football %s failed: %s", path, e)
        return []


def _wc_teams() -> list[dict]:
    """The 48 national teams in the 2026 World Cup (cached, 1 request)."""
    global _wc_teams_cache
    if _wc_teams_cache is None:
        resp = _get("/teams", {"league": WC_LEAGUE_ID, "season": WC_SEASON})
        _wc_teams_cache = [item.get("team", {}) for item in resp]
    return _wc_teams_cache


# Alternate names (Italian/Portuguese/Spanish, common variants) -> the name
# API-Football uses in the WC squad list. Keeps id resolution reliable
# without an extra fuzzy /teams?search request.
_NAME_ALIASES = {
    "cabo verde": "cape verde",
    "cape verde islands": "cape verde",
    "costa d'avorio": "ivory coast",
    "cote d'ivoire": "ivory coast",
    "corea del sud": "south korea",
    "korea republic": "south korea",
    "stati uniti": "usa",
    "united states": "usa",
    "olanda": "netherlands",
    "paesi bassi": "netherlands",
    "spagna": "spain",
    "germania": "germany",
    "francia": "france",
    "inghilterra": "england",
    "marocco": "morocco",
    "arabia saudita": "saudi arabia",
    "giappone": "japan",
    "brasile": "brazil",
    "croazia": "croatia",
}


def find_team_id(name: str) -> int | None:
    key = name.casefold().strip()
    key = _NAME_ALIASES.get(key, key)
    if key in _team_id_cache:
        return _team_id_cache[key]

    # 1) Exact/partial match inside the World Cup squad list (reliable ids)
    for exact in (True, False):
        for t in _wc_teams():
            fields = [
                str(t.get(k, "")).casefold()
                for k in ("name", "code", "country")
                if t.get(k)
            ]
            if (key in fields) if exact else any(key in f for f in fields):
                _team_id_cache[key] = t["id"]
                log.info("API-Football: '%s' -> WC team id %s (%s)", name, t["id"], t.get("name"))
                return t["id"]

    # 2) Generic search: prefer exact-name national teams
    resp = _get("/teams", {"search": name})
    best = None
    for item in resp:
        team = item.get("team", {})
        nm = str(team.get("name", "")).casefold()
        if team.get("national") and nm == key:
            best = team["id"]
            break
        if best is None and team.get("national"):
            best = team["id"]
    if best is None and resp:
        best = resp[0]["team"]["id"]
    if best is not None:
        _team_id_cache[key] = best
    return best


def wc_fixtures() -> list[dict]:
    """Full FIFA World Cup 2026 calendar (1 request, cached 15 min).

    Used both to find fixtures and to validate that a requested match
    actually exists in the schedule. Returns [] if the API is unreachable
    (a stale cache, if any, is served instead).
    """
    global _wc_fixtures_cache, _wc_fixtures_ts
    if _wc_fixtures_cache is not None and time.time() - _wc_fixtures_ts < WC_SCHEDULE_TTL:
        return _wc_fixtures_cache
    resp = _get("/fixtures", {"league": WC_LEAGUE_ID, "season": WC_SEASON})
    if resp:
        _wc_fixtures_cache, _wc_fixtures_ts = resp, time.time()
        return resp
    return _wc_fixtures_cache or []


def schedule_available() -> bool:
    """True if the WC calendar could be fetched (lets callers distinguish
    'match not in schedule' from 'schedule service down')."""
    return bool(wc_fixtures())


# Fixture status groups (API-Football "short" codes)
_UPCOMING_STATUS = ("NS", "TBD")
_FINISHED_STATUS = {"FT", "AET", "PEN", "WO", "AWD"}
_LIVE_STATUS = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"}


def _fixture_dict(fx: dict) -> dict:
    return {
        "fixture_id": fx["fixture"]["id"],
        "date": fx["fixture"].get("date"),
        "league": fx.get("league", {}).get("name"),
        "home": fx["teams"]["home"]["name"],
        "away": fx["teams"]["away"]["name"],
        "home_id": fx["teams"]["home"]["id"],
        "away_id": fx["teams"]["away"]["id"],
    }


def find_fixture_ex(team1: str, team2: str) -> tuple[dict | None, str]:
    """Find the upcoming WC fixture between two teams, with a diagnosis.

    Returns (fixture_dict, "ok") on success, otherwise (None, reason) where
    reason is one of:
      - "schedule_unavailable": the WC calendar could not be fetched
      - "team_not_found":       one/both team names couldn't be resolved
      - "not_scheduled":        no match between the two teams is in the
                                calendar (e.g. different groups, or a
                                knockout pairing not drawn yet)
      - "already_played":       their match exists but has already finished
      - "live":                 their match is currently being played
      - "not_upcoming":         exists but in some other non-playable state
    """
    id1, id2 = find_team_id(team1), find_team_id(team2)
    if not id1 or not id2:
        log.warning(
            "API-Football: team id not found (%s=%s, %s=%s).",
            team1, id1, team2, id2,
        )
        return None, "team_not_found"

    fixtures = wc_fixtures()
    if not fixtures:
        return None, "schedule_unavailable"

    # All calendar fixtures featuring BOTH teams, regardless of status/time.
    matched = [
        f
        for f in fixtures
        if {id1, id2}
        <= {
            f.get("teams", {}).get("home", {}).get("id"),
            f.get("teams", {}).get("away", {}).get("id"),
        }
    ]
    if not matched:
        log.info(
            "API-Football: %s (id %s) and %s (id %s) have no fixture in the WC calendar.",
            team1, id1, team2, id2,
        )
        return None, "not_scheduled"

    now = time.time()
    upcoming = [
        f
        for f in matched
        if (f.get("fixture", {}).get("status", {}).get("short") in _UPCOMING_STATUS)
        and f.get("fixture", {}).get("timestamp", 0) >= now - 6 * 3600
    ]
    if upcoming:
        upcoming.sort(key=lambda f: f["fixture"]["timestamp"])
        return _fixture_dict(upcoming[0]), "ok"

    # A match exists but isn't pre-match anymore — say exactly why.
    statuses = [
        (f.get("fixture", {}).get("status", {}) or {}).get("short") for f in matched
    ]
    log.info(
        "API-Football: %s-%s found but not upcoming (statuses=%s).",
        team1, team2, statuses,
    )
    if any(s in _LIVE_STATUS for s in statuses):
        return None, "live"
    if any(s in _FINISHED_STATUS for s in statuses):
        return None, "already_played"
    return None, "not_upcoming"


def upcoming_fixtures(limit: int | None = None) -> list[dict]:
    """All not-yet-started WC fixtures, soonest first (for the web match list).

    Each item: {"home", "away", "home_id", "away_id", "date", "timestamp"}.
    Returns [] if the calendar is unreachable.
    """
    now = time.time()
    out: list[dict] = []
    for f in wc_fixtures():
        fix = f.get("fixture", {})
        if fix.get("status", {}).get("short") not in _UPCOMING_STATUS:
            continue
        if fix.get("timestamp", 0) < now - 6 * 3600:
            continue
        teams = f.get("teams", {})
        home, away = teams.get("home", {}), teams.get("away", {})
        if not home.get("name") or not away.get("name"):
            continue  # skip knockout placeholders with no teams yet
        out.append(
            {
                "home": home.get("name"),
                "away": away.get("name"),
                "home_id": home.get("id"),
                "away_id": away.get("id"),
                "date": fix.get("date"),
                "timestamp": fix.get("timestamp"),
            }
        )
    out.sort(key=lambda x: x["timestamp"] or 0)
    return out[:limit] if limit else out


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


def get_lineups(fixture_id: int) -> dict:
    """Official lineups, published ~20-60 min before kickoff.

    Returns {team_id: {"formation": str, "starters": [names], "subs": [names]}}
    or {} if lineups are not (yet) available.
    """
    resp = _get("/fixtures/lineups", {"fixture": fixture_id})
    out: dict[int, dict] = {}
    for item in resp:
        tid = (item.get("team") or {}).get("id")
        if not tid:
            continue

        def names(key: str) -> list[str]:
            return [
                n
                for n in (
                    ((x or {}).get("player") or {}).get("name")
                    for x in item.get(key) or []
                )
                if n
            ]

        out[tid] = {
            "formation": item.get("formation"),
            "starters": names("startXI"),
            "subs": names("substitutes"),
        }
    if out:
        log.info(
            "API-Football: official lineups available for fixture %s (%d teams)",
            fixture_id, len(out),
        )
    else:
        log.info("API-Football: no official lineups yet for fixture %s", fixture_id)
    return out


def collect_team_stats(team_id: int, team_label: str = "") -> dict:
    """Per-player stats from the team's last matches via /fixtures/events.

    Same structure as espn/sofascore.collect_team_stats:
    goals (and first-half split), yellow cards, substituted-off minutes.
    Requires a paid plan (the 'last' parameter is paid-only).
    """
    if not team_id:
        return {}
    fixtures = _get("/fixtures", {"team": team_id, "last": 5, "status": "FT"})
    if not fixtures:
        # some plans dislike combined params — retry without status filter
        fixtures = _get("/fixtures", {"team": team_id, "last": 5})
    if not fixtures:
        log.info("API-Football: no recent fixtures for team %s", team_id)
        return {}

    players: dict[str, dict] = {}
    goals_for = 0
    fh_goals = 0
    matches = 0

    def p(name: str) -> dict:
        return players.setdefault(
            name, {"goals": 0, "yellows": 0, "sub_off_minutes": []}
        )

    for f in fixtures:
        fid = f.get("fixture", {}).get("id")
        if not fid:
            continue
        events = _get("/fixtures/events", {"fixture": fid})
        if not events:
            continue
        matches += 1
        for ev in events:
            if ev.get("team", {}).get("id") != team_id:
                continue
            etype = ev.get("type")
            detail = ev.get("detail", "")
            minute = (ev.get("time") or {}).get("elapsed") or 0
            name = (ev.get("player") or {}).get("name")
            if etype == "Goal" and detail not in ("Missed Penalty", "Own Goal"):
                if name:
                    p(name)["goals"] += 1
                goals_for += 1
                if 0 < minute <= 45:
                    fh_goals += 1
            elif etype == "Card" and detail == "Yellow Card":
                if name:
                    p(name)["yellows"] += 1
            elif etype == "subst":
                # v3 convention: 'player' is the one leaving the pitch
                if name and minute:
                    p(name)["sub_off_minutes"].append(minute)

    if not matches:
        return {}
    log.info(
        "API-Football: stats for team %s (%s): %d matches, %d players",
        team_id, team_label, matches, len(players),
    )
    return {
        "source_name": "API-Football",
        "team_id": team_id,
        "matches": matches,
        "goals_for_per_match": round(goals_for / matches, 2),
        "first_half_goal_share": round(fh_goals / goals_for, 2) if fh_goals else 0.45,
        "players": players,
    }


def get_match_data(team1: str, team2: str) -> dict:
    """Orchestrate: fixture + odds.

    On success returns {"fixture": {...}, "odds": {...}}. When no playable
    fixture is found, returns {"fixture": None, "reason": <reason>} so the
    caller can show the user a specific message.
    """
    fx, reason = find_fixture_ex(team1, team2)
    if not fx:
        log.info("API-Football: no fixture for %s vs %s (%s)", team1, team2, reason)
        return {"fixture": None, "reason": reason}
    log.info(
        "API-Football: fixture %s (%s vs %s, %s)",
        fx["fixture_id"], fx["home"], fx["away"], fx.get("date"),
    )
    odds = get_odds(fx["fixture_id"])
    markets = [k for k in odds if k != "bookmakers"]
    log.info(
        "API-Football: odds from %s bookmakers, markets: %s",
        odds.get("bookmakers", 0), markets or "none",
    )
    return {"fixture": fx, "odds": odds}
