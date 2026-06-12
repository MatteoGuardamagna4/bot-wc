"""ESPN public API — backup stats source (no key, no fingerprint blocking).

Used when Sofascore is unavailable. Mines each team's recent completed
matches and builds the same per-player stats structure:
goals, yellow cards, substituted-off minutes.

Endpoints (verified June 2026):
  /apis/site/v2/sports/soccer/fifa.world/teams           -> WC team ids
  /apis/site/v2/sports/soccer/all/teams/{id}/schedule    -> recent events
  /apis/site/v2/sports/soccer/all/summary?event={id}     -> boxscore/keyEvents
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import datetime, timezone

import httpx

from config import REQUEST_TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

LAST_MATCHES = 5
DEFAULT_SUB_MINUTE = 70  # used when the boxscore lacks the exact minute

_team_cache: dict[str, int] = {}


def _get(url: str, params: dict | None = None) -> dict:
    try:
        r = httpx.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            return r.json()
        log.warning("ESPN %s -> %s", url, r.status_code)
    except Exception as e:  # noqa: BLE001
        log.warning("ESPN %s failed: %s", url, e)
    return {}


# NB: 'republic' must NOT be a stopword: 'Korea Republic' (South) would
# otherwise collide with 'North Korea' via token subset matching.
_STOPWORDS = {"and", "the", "of"}


def _norm(s: str) -> str:
    """Normalize for matching: accents off, separators -> spaces, casefold.

    Sources disagree on names: API-Football 'Bosnia & Herzegovina',
    ESPN 'Bosnia-Herzegovina' or 'Bosnia and Herzegovina', etc.
    """
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    for ch in ("&", "-", "/", "."):
        s = s.replace(ch, " ")
    return " ".join(s.casefold().split())


def _tokens(s: str) -> set[str]:
    return {t for t in _norm(s).split() if t not in _STOPWORDS}


def _names_match(query: str, candidate: str) -> bool:
    q, c = _norm(query), _norm(candidate)
    if q == c or q in c or c in q:
        return True
    tq, tc = _tokens(query), _tokens(candidate)
    # token-set equality or subset (e.g. 'bosnia herzegovina' vs
    # 'bosnia and herzegovina', or 'bosnia' vs the full name)
    return bool(tq) and bool(tc) and (tq <= tc or tc <= tq)


def find_team_id(name: str) -> int | None:
    key = _norm(name)
    if key in _team_cache:
        return _team_cache[key]
    data = _get(f"{BASE}/fifa.world/teams")
    try:
        teams = data["sports"][0]["leagues"][0]["teams"]
    except (KeyError, IndexError):
        return None
    for item in teams:
        t = item.get("team", {})
        candidates = [
            str(t.get(k, ""))
            for k in ("displayName", "name", "location", "abbreviation", "shortDisplayName")
            if t.get(k)
        ]
        if any(_names_match(name, c) for c in candidates):
            _team_cache[key] = int(t["id"])
            return _team_cache[key]
    return None


def last_event_ids(team_id: int, n: int = LAST_MATCHES) -> list[str]:
    data = _get(f"{BASE}/all/teams/{team_id}/schedule")
    now = datetime.now(timezone.utc)
    past = []
    for ev in data.get("events", []):
        try:
            d = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if d < now:
            past.append((d, str(ev["id"])))
    past.sort(reverse=True)
    # dedupe, keep order
    seen, out = set(), []
    for _, eid in past:
        if eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out[:n]


def _minute_from_clock(obj: dict) -> int:
    disp = str((obj or {}).get("displayValue", ""))
    m = re.match(r"(\d+)", disp)
    return int(m.group(1)) if m else 0


def _stats_dict(entry: dict) -> dict[str, float]:
    out = {}
    for st in entry.get("stats", []) or entry.get("statistics", []) or []:
        name = st.get("name")
        try:
            out[name] = float(st.get("value", st.get("displayValue", 0)) or 0)
        except (TypeError, ValueError):
            pass
    return out


def collect_team_stats(team_name: str) -> dict:
    """Same structure as sofascore.collect_team_stats, source_name='ESPN'."""
    team_id = find_team_id(team_name)
    if not team_id:
        log.warning("ESPN: team '%s' not found", team_name)
        return {}
    event_ids = last_event_ids(team_id)
    if not event_ids:
        log.warning("ESPN: no recent events for '%s'", team_name)
        return {}

    players: dict[str, dict] = {}
    goals_for = 0
    fh_goals = 0
    matches = 0

    def p(name: str) -> dict:
        return players.setdefault(
            name, {"goals": 0, "yellows": 0, "sub_off_minutes": []}
        )

    for eid in event_ids:
        data = _get(f"{BASE}/all/summary", {"event": eid})
        if not data:
            continue
        matches += 1

        # --- per-player boxscore (goals, yellows, subbed out) ---
        for side in data.get("rosters", []):
            if str(side.get("team", {}).get("id")) != str(team_id):
                continue
            for entry in side.get("roster", []):
                name = (entry.get("athlete") or {}).get("displayName")
                if not name:
                    continue
                st = _stats_dict(entry)
                g = int(st.get("totalGoals", 0)) - int(st.get("ownGoals", 0))
                if g > 0:
                    p(name)["goals"] += g
                    goals_for += g
                y = int(st.get("yellowCards", 0))
                if y > 0:
                    p(name)["yellows"] += y
                if entry.get("subbedOutFor"):
                    minute = _minute_from_clock(entry.get("clock") or {}) or DEFAULT_SUB_MINUTE
                    p(name)["sub_off_minutes"].append(minute)

        # --- key events: refine sub minutes and first-half goal split ---
        for kev in data.get("keyEvents", []):
            ktype = (kev.get("type") or {}).get("text", "")
            minute = _minute_from_clock(kev.get("clock") or {})
            same_team = str((kev.get("team") or {}).get("id")) == str(team_id)
            if ktype == "Goal" and same_team and 0 < minute <= 45:
                fh_goals += 1
            elif ktype == "Substitution" and same_team and minute:
                ath = kev.get("participants") or kev.get("athletesInvolved") or []
                # convention: second participant (or 'playerOut') is the one leaving
                for cand in ath:
                    nm = (cand.get("athlete") or cand or {}).get("displayName")
                    if nm and nm in players and players[nm]["sub_off_minutes"]:
                        # replace a default minute with the real one
                        mins = players[nm]["sub_off_minutes"]
                        if DEFAULT_SUB_MINUTE in mins:
                            mins[mins.index(DEFAULT_SUB_MINUTE)] = minute
        time.sleep(0.4)

    if not matches:
        return {}
    # If keyEvents were missing/unmatched, fh_goals stays 0 even when the team
    # scored — fall back to the typical 45% first-half share instead of 0,
    # otherwise the model would always predict 0-0 first halves.
    if goals_for and fh_goals:
        fh_share = round(fh_goals / goals_for, 2)
    else:
        fh_share = 0.45
    return {
        "source_name": "ESPN",
        "team_id": team_id,
        "matches": matches,
        "goals_for_per_match": round(goals_for / matches, 2),
        "first_half_goal_share": fh_share,
        "players": players,
    }


if __name__ == "__main__":
    # Quick manual test:  python espn.py Canada
    import json
    import sys

    logging.basicConfig(level=logging.INFO)
    name = sys.argv[1] if len(sys.argv) > 1 else "Canada"
    print(json.dumps(collect_team_stats(name), indent=2)[:3000])
