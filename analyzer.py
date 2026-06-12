"""Analysis pipeline.

Order of preference:
  1. API-Football odds  → Poisson model → half scores
  2. Sofascore stats    → player markets (scorers, cards, subs) + lambda fallback
  3. Betexplorer scrape → raw context (only if 1-2 fail)
  4. GPT-4o             → ONLY to fill sections that data could not cover

The final message is built in Python from real numbers whenever possible.
"""
from __future__ import annotations

import json
import logging

from openai import OpenAI

import api_football
import espn
import sofascore
from config import OPENAI_API_KEY, OPENAI_MODEL, SOFASCORE_ENABLED
from model import build_prediction
from scraper import scrape_match_odds

log = logging.getLogger(__name__)

SECTION_TITLES = {
    "first_half": "🕐 FIRST HALF",
    "second_half": "🕑 SECOND HALF",
    "scorers": "🎯 GOALSCORERS",
    "first_card": "🟨 FIRST CARD",
    "first_sub": "🔄 FIRST SUBSTITUTION",
}
ALL_SECTIONS = list(SECTION_TITLES)


class MatchNotFoundError(Exception):
    """The requested match is not in the upcoming World Cup schedule
    (or the schedule could not be verified). Message is user-facing."""


def run_analysis(team1: str, team2: str) -> str:
    """Full pipeline. Returns the formatted Telegram message.

    Raises MatchNotFoundError if the match is not in the upcoming
    World Cup schedule.
    """
    # 1) API-Football: fixture + odds. The fixture MUST exist in the
    #    official WC calendar — otherwise we refuse to analyze.
    api_data = api_football.get_match_data(team1, team2)
    fx = api_data.get("fixture")
    if not fx:
        if not api_football.schedule_available():
            raise MatchNotFoundError(
                "I can't verify the World Cup schedule right now "
                "(data provider unreachable). Please try again later."
            )
        raise MatchNotFoundError(
            f"{team1} vs {team2} is not in the upcoming World Cup schedule. "
            "Check the team names and the official calendar."
        )
    home = fx.get("home", team1)
    away = fx.get("away", team2)

    # 1b) Official lineups (published ~1h before kickoff). If available,
    #     player suggestions are restricted to players actually playing.
    raw_lineups = api_football.get_lineups(fx["fixture_id"])
    lineups: dict | None = None
    if raw_lineups:
        lineups = {
            "home": raw_lineups.get(fx.get("home_id")) or {},
            "away": raw_lineups.get(fx.get("away_id")) or {},
        }

    # 2) Player stats: API-Football events first (single source, uses the
    #    team ids already resolved by the fixture lookup — no name matching).
    #    ESPN as free fallback; Sofascore only if explicitly enabled
    #    (it 403-blocks many IPs; see SOFASCORE_ENABLED in .env).
    def team_stats(team_id: int | None, name: str) -> dict:
        stats = api_football.collect_team_stats(team_id, name) if team_id else {}
        stats = stats or espn.collect_team_stats(name)
        if not stats and SOFASCORE_ENABLED:
            stats = sofascore.collect_team_stats(name)
        return stats

    home_stats = team_stats(fx.get("home_id"), home)
    away_stats = team_stats(fx.get("away_id"), away)

    # 3) Statistical model (lineup-aware when official lineups are out)
    pred = build_prediction(api_data, home_stats, away_stats, lineups)
    sections = pred["sections"]
    missing = [s for s in ALL_SECTIONS if s not in sections]

    # 4) AI fallback ONLY for what's missing
    ai_sections: dict[str, str] = {}
    if missing:
        scraped = {}
        if "first_half" in missing or "second_half" in missing:
            scraped = scrape_match_odds(home, away)  # extra context for the AI
        ai_sections = ai_fill_sections(home, away, missing, pred, scraped, lineups)

    return format_message(home, away, pred, ai_sections)


# ---------------------------------------------------------------- formatting


def _fmt_scores(scores: list) -> str:
    main = f"Most likely score: {scores[0][0]} ({scores[0][1]}%)"
    if len(scores) > 1:
        main += f"\nAlt: {scores[1][0]} ({scores[1][1]}%)"
    return main


def _fmt_players(items: list, first_label: str = "") -> str:
    lines = []
    for i, (name, pct) in enumerate(items, 1):
        label = f" — {first_label} " if (i == 1 and first_label) else " — "
        lines.append(f"{i}. {name}{label}{pct}%")
    return "\n".join(lines)


def format_message(home: str, away: str, pred: dict, ai_sections: dict) -> str:
    s = pred["sections"]
    parts = [f"⚽ {home} vs {away} — World Cup", "━━━━━━━━━━━━━━━━━━"]

    def section(key: str, body: str | None) -> None:
        if body:
            parts.append(f"\n{SECTION_TITLES[key]}\n{body}")

    section(
        "first_half",
        _fmt_scores(s["first_half"]["scores"]) if "first_half" in s
        else ai_sections.get("first_half"),
    )
    section(
        "second_half",
        _fmt_scores(s["second_half"]["scores"]) if "second_half" in s
        else ai_sections.get("second_half"),
    )
    section(
        "scorers",
        _fmt_players(s["scorers"], "first scorer") if "scorers" in s
        else ai_sections.get("scorers"),
    )
    if "first_card" in s:
        body = f"{s['first_card'][0][0]} ({s['first_card'][0][1]}%)"
        if len(s["first_card"]) > 1:
            body += f"\nAlt: {s['first_card'][1][0]} ({s['first_card'][1][1]}%)"
        section("first_card", body)
    else:
        section("first_card", ai_sections.get("first_card"))
    if "first_sub" in s:
        body = f"{s['first_sub'][0][0]} ({s['first_sub'][0][1]}%)"
        if len(s["first_sub"]) > 1:
            body += f"\nAlt: {s['first_sub'][1][0]} ({s['first_sub'][1][1]}%)"
        section("first_sub", body)
    else:
        section("first_sub", ai_sections.get("first_sub"))

    sources = list(pred["sources"])
    if ai_sections:
        sources.append("AI (fallback for missing markets)")
    parts.append(f"\n📊 Source: {', '.join(sources) if sources else 'AI analysis'}")
    if pred.get("lambdas"):
        parts.append(f"xG model: {home} {pred['lambdas'][0]} — {away} {pred['lambdas'][1]}")
    return "\n".join(parts)


# ---------------------------------------------------------------- AI fallback


AI_SYSTEM = """\
You are a football analyst. You will be asked to fill ONLY specific missing
prediction sections for a World Cup match. Use realistic, calibrated
percentages. Respond ONLY with a JSON object whose keys are exactly the
requested section names. Formats:
- "first_half"/"second_half": "Most likely score: 1-0 (28%)\\nAlt: 0-0 (24%)"
- "scorers": "1. Player — first scorer 18%\\n2. Player — 14%\\n3. Player — 11%"
- "first_card": "Player (22%)\\nAlt: Player (15%)"
- "first_sub": "Player (35%)\\nAlt: Player (25%)"
If "official_lineups" is present in the context, suggest ONLY players from
those lists: starters for first_card/first_sub; starters or substitutes for
scorers. Never name a player who is not in the lineups.
"""


def ai_fill_sections(
    home: str,
    away: str,
    missing: list[str],
    pred: dict,
    scraped: dict,
    lineups: dict | None = None,
) -> dict[str, str]:
    """Ask GPT-4o to fill only the missing sections. Returns {} on failure."""
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        context = {
            # lineups first: the user-message context gets truncated at 4000
            # chars and the lineups constraint must never be cut off
            "official_lineups": lineups,
            "computed_sections": {k: v for k, v in pred["sections"].items()},
            "scraped_odds": json.dumps(scraped)[:2000] if scraped else None,
        }
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": AI_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Match: {home} vs {away} (World Cup 2026).\n"
                        f"Missing sections to fill: {missing}\n"
                        f"Available context: {json.dumps(context, default=str)[:4000]}"
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        return {k: v for k, v in data.items() if k in missing and isinstance(v, str)}
    except Exception as e:  # noqa: BLE001
        log.warning("AI fallback failed: %s", e)
        return {}
