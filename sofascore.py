"""Sofascore public JSON endpoints — secondary data source (no API key).

Builds per-player empirical stats from each team's recent matches:
goals (and which half), yellow cards, substitution-off minutes.
Unofficial endpoints: failure-tolerant by design.
"""
from __future__ import annotations

import logging
import time

import httpx

from config import REQUEST_TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)

BASE = "https://api.sofascore.com/api/v1"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

LAST_MATCHES = 6  # how many recent matches to mine per team


def _get(path: str) -> dict:
    try:
        r = httpx.get(BASE + path, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            return r.json()
        log.warning("Sofascore %s -> %s", path, r.status_code)
    except Exception as e:  # noqa: BLE001
        log.warning("Sofascore %s failed: %s", path, e)
    return {}


def find_team_id(name: str) -> int | None:
    data = _get(f"/search/all?q={httpx.QueryParams({'q': name})['q']}")
    for res in data.get("results", []):
        ent = res.get("entity", {})
        if res.get("type") == "team" and ent.get("sport", {}).get("name") == "Football":
            # Prefer national teams
            if ent.get("national") or ent.get("teamColors"):
                return ent.get("id")
    return None


def last_event_ids(team_id: int, n: int = LAST_MATCHES) -> tuple[list[int], list[dict]]:
    data = _get(f"/team/{team_id}/events/last/0")
    events = data.get("events", [])
    finished = [e for e in events if e.get("status", {}).get("type") == "finished"]
    finished.sort(key=lambda e: e.get("startTimestamp", 0), reverse=True)
    return [e["id"] for e in finished[:n]], finished[:n]


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
        return {}
    try:
        ids, events = last_event_ids(team_id)
    except Exception:  # noqa: BLE001
        return {}
    if not ids:
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
        time.sleep(0.4)  # be polite

    matches = len(events)
    return {
        "team_id": team_id,
        "matches": matches,
        "goals_for_per_match": round(goals_for / matches, 2) if matches else 0,
        "first_half_goal_share": round(fh_goals / goals_for, 2) if goals_for else 0.45,
        "players": players,
    }
