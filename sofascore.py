"""Sofascore public JSON endpoints — primary stats source (no API key).

Sofascore blocks plain requests/httpx via TLS fingerprinting (403).
Fix: curl_cffi impersonates a real Chrome TLS fingerprint.

Builds per-player empirical stats from each team's recent matches:
goals (and which half), yellow cards, substitution-off minutes.
"""
from __future__ import annotations

import logging
import time

from config import REQUEST_TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)

try:
    from curl_cffi import requests as creq

    HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover
    import httpx

    HAS_CURL_CFFI = False
    log.warning(
        "curl_cffi not installed — Sofascore will likely return 403. "
        "Run: pip install curl_cffi"
    )

HOSTS = [
    "https://api.sofascore.com/api/v1",
    "https://www.sofascore.com/api/v1",
    "https://api.sofascore.app/api/v1",
]
# IMPORTANT: with curl_cffi we must NOT set a custom User-Agent/Origin —
# impersonate=... sends a full coherent Chrome header set, and overriding
# pieces of it creates a fingerprint mismatch that gets detected (-> 403).
MINIMAL_HEADERS = {"Accept": "application/json"}
IMPERSONATE_TARGETS = ["chrome", "safari", "firefox", "edge"]

LAST_MATCHES = 6  # how many recent matches to mine per team

_session = None
_working = None  # cached (host, target) that worked
_disabled_until = 0.0  # fail-fast: after a full rotation failure, back off


def _make_session(target: str):
    s = creq.Session(impersonate=target, timeout=REQUEST_TIMEOUT)
    try:
        s.get("https://www.sofascore.com/")  # cookie warm-up
    except Exception:  # noqa: BLE001
        pass
    return s


def _get(path: str, params: dict | None = None) -> dict:
    """GET rotating hosts and browser fingerprints until one works.

    Fail-fast: if a full rotation fails (Sofascore blocking this IP),
    Sofascore is disabled for 30 minutes so the pipeline jumps straight
    to ESPN instead of wasting ~100 doomed requests per analysis.
    """
    global _session, _working, _disabled_until

    if time.time() < _disabled_until:
        return {}

    if not HAS_CURL_CFFI:
        for host in HOSTS:
            try:
                r = httpx.get(
                    host + path,
                    params=params,
                    headers={**MINIMAL_HEADERS, "User-Agent": USER_AGENT},
                    timeout=REQUEST_TIMEOUT,
                )
                if r.status_code == 200:
                    return r.json()
                log.warning("Sofascore %s -> %s", host + path, r.status_code)
            except Exception as e:  # noqa: BLE001
                log.warning("Sofascore %s failed: %s", host + path, e)
        return {}

    # Try the cached working combination first
    combos = []
    if _working:
        combos.append(_working)
    combos += [
        (h, t) for t in IMPERSONATE_TARGETS for h in HOSTS if (h, t) != _working
    ]

    for host, target in combos:
        try:
            if _session is None or _working != (host, target):
                _session = _make_session(target)
            r = _session.get(host + path, params=params, headers=MINIMAL_HEADERS)
            if r.status_code == 200:
                if _working != (host, target):
                    log.info("Sofascore OK via %s (%s)", host, target)
                _working = (host, target)
                return r.json()
            log.warning("Sofascore %s [%s] -> %s", host + path, target, r.status_code)
            _session = None
        except Exception as e:  # noqa: BLE001
            log.warning("Sofascore %s [%s] failed: %s", host + path, target, e)
            _session = None
    _working = None
    _disabled_until = time.time() + 1800
    log.warning("Sofascore blocked on all combinations — disabled for 30 min, using ESPN.")
    return {}


def find_team_id(name: str) -> int | None:
    # '&' confuses search engines; sources use 'and' (e.g. Bosnia & Herzegovina)
    data = _get("/search/all", {"q": name.replace("&", "and")})
    national = other = None
    for res in data.get("results", []):
        ent = res.get("entity", {})
        if res.get("type") != "team":
            continue
        if ent.get("sport", {}).get("name") != "Football":
            continue
        if ent.get("national") and national is None:
            national = ent.get("id")
        elif other is None:
            other = ent.get("id")
    return national or other


def last_events(team_id: int, n: int = LAST_MATCHES) -> list[dict]:
    data = _get(f"/team/{team_id}/events/last/0")
    events = data.get("events", [])
    finished = [e for e in events if e.get("status", {}).get("type") == "finished"]
    finished.sort(key=lambda e: e.get("startTimestamp", 0), reverse=True)
    return finished[:n]


def collect_team_stats(team_name: str) -> dict:
    """Mine recent matches. Returns:

    {
      "team_id": int,
      "matches": int,
      "goals_for_per_match": float,
      "first_half_goal_share": float,     # share of this team's goals in 1H
      "players": {
        "Player Name": {"goals": int, "yellows": int, "sub_off_minutes": [..]},
      },
    }
    or {} on failure.
    """
    team_id = find_team_id(team_name)
    if not team_id:
        log.warning("Sofascore: team '%s' not found", team_name)
        return {}
    events = last_events(team_id)
    if not events:
        log.warning("Sofascore: no recent events for '%s'", team_name)
        return {}

    players: dict[str, dict] = {}
    goals_for = 0
    fh_goals = 0

    def p(name: str) -> dict:
        return players.setdefault(
            name, {"goals": 0, "yellows": 0, "sub_off_minutes": []}
        )

    for ev in events:
        event_id = ev["id"]
        is_home = ev.get("homeTeam", {}).get("id") == team_id
        data = _get(f"/event/{event_id}/incidents")
        for inc in data.get("incidents", []):
            if inc.get("isHome") is not None and inc["isHome"] != is_home:
                continue  # incident belongs to the opponent
            itype = inc.get("incidentType")
            minute = inc.get("time") or 0
            if itype == "goal":
                name = (inc.get("player") or {}).get("name")
                if name:
                    p(name)["goals"] += 1
                goals_for += 1
                if 0 < minute <= 45:
                    fh_goals += 1
            elif itype == "card" and inc.get("incidentClass") in ("yellow", "yellowRed"):
                name = (inc.get("player") or {}).get("name")
                if name:
                    p(name)["yellows"] += 1
            elif itype == "substitution":
                name = (inc.get("playerOut") or {}).get("name")
                if name and minute:
                    p(name)["sub_off_minutes"].append(minute)
        time.sleep(0.6)  # be polite, avoid rate limiting

    matches = len(events)
    return {
        "source_name": "Sofascore",
        "team_id": team_id,
        "matches": matches,
        "goals_for_per_match": round(goals_for / matches, 2) if matches else 0,
        "first_half_goal_share": round(fh_goals / goals_for, 2) if goals_for else 0.45,
        "players": players,
    }


if __name__ == "__main__":
    # Quick manual test:  python sofascore.py Canada
    import json
    import sys

    logging.basicConfig(level=logging.INFO)
    name = sys.argv[1] if len(sys.argv) > 1 else "Canada"
    print(json.dumps(collect_team_stats(name), indent=2)[:3000])
