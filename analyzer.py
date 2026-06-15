"""Analysis pipeline.

Order of preference:
  1. API-Football odds  → Poisson model → full-time result + half scores
  2. Player stats (API-Football events → ESPN → Sofascore) → scorer/card/sub
     markets, plus an expected-goals fallback when odds are missing
  3. Betexplorer scrape → raw context for the AI (only if score data missing)
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
    "final_result": "🏁 FINAL RESULT",
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


def analyze(team1: str, team2: str) -> dict:
    """Full pipeline. Returns structured prediction data:

        {"home", "away", "date", "pred", "ai_sections"}

    where ``pred`` is the model dict (sections/sources/lambdas) and
    ``ai_sections`` holds AI-filled strings for any markets the data could
    not cover. Raises MatchNotFoundError if the match is not in the upcoming
    World Cup schedule. Used by both the Telegram bot (via run_analysis) and
    the web dashboard.
    """
    # 1) API-Football: fixture + odds. The fixture MUST exist in the
    #    official WC calendar — otherwise we refuse to analyze.
    api_data = api_football.get_match_data(team1, team2)
    fx = api_data.get("fixture")
    if not fx:
        reason = api_data.get("reason")
        # A team can fail to resolve simply because the data provider is down;
        # prefer the clearer "provider unreachable" message in that case.
        if reason == "team_not_found" and not api_football.schedule_available():
            reason = "schedule_unavailable"
        messages = {
            "schedule_unavailable": (
                "I can't verify the World Cup schedule right now "
                "(data provider unreachable). Please try again later."
            ),
            "team_not_found": (
                f"I couldn't recognize one of the teams: '{team1}' / '{team2}'. "
                "Check the spelling or use the 3-letter code (e.g. ESP, CPV)."
            ),
            "not_scheduled": (
                f"{team1} vs {team2} is not a scheduled World Cup match. "
                "The two teams aren't drawn to meet in the group stage, or a "
                "knockout pairing between them hasn't been set yet."
            ),
            "already_played": (
                f"{team1} vs {team2} has already been played — pre-match "
                "predictions are no longer available for it."
            ),
            "live": (
                f"{team1} vs {team2} is being played right now, so pre-match "
                "predictions are no longer available."
            ),
        }
        raise MatchNotFoundError(
            messages.get(reason)
            or (
                f"{team1} vs {team2} is not in the upcoming World Cup schedule. "
                "Check the team names and the official calendar."
            )
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
        if any(k in missing for k in ("final_result", "first_half", "second_half")):
            scraped = scrape_match_odds(home, away)  # extra context for the AI
        ai_sections = ai_fill_sections(home, away, missing, pred, scraped, lineups)

    return {
        "home": home,
        "away": away,
        "date": fx.get("date"),
        "pred": pred,
        "ai_sections": ai_sections,
    }


def run_analysis(team1: str, team2: str) -> str:
    """Full pipeline. Returns the formatted Telegram message.

    Raises MatchNotFoundError if the match is not in the upcoming
    World Cup schedule.
    """
    r = analyze(team1, team2)
    return format_message(r["home"], r["away"], r["pred"], r["ai_sections"])


# ---------------------------------------------------------------- formatting


def _fmt_final_result(results: list) -> str:
    """Render the 4 most likely full-time scorelines with odds.

    results: [{"score","prob","odd","odd_source"}, ...]. A '*' marks an odd
    derived from the model (no bookmaker quote for that exact scoreline).
    """
    lines = []
    for i, r in enumerate(results, 1):
        odd = r.get("odd")
        if odd is None:
            odd_txt = ""
        else:
            star = "*" if r.get("odd_source") == "model" else ""
            odd_txt = f" @ {odd}{star}"
        lines.append(f"{i}. {r['score']} — {r['prob']}%{odd_txt}")
    if any(r.get("odd_source") == "model" for r in results):
        lines.append("(* fair odds from model — no bookmaker quote)")
    return "\n".join(lines)


def _fmt_scores(scores: list) -> str:
    """Render a half's most-likely scoreline(s)."""
    body = f"Most likely: {scores[0][0]} ({scores[0][1]}%)"
    if len(scores) > 1:
        body += f"\nAlt: {scores[1][0]} ({scores[1][1]}%)"
    return body


def _fmt_players(items: list, first_label: str = "") -> str:
    lines = []
    for i, (name, pct) in enumerate(items, 1):
        label = f" — {first_label} " if (i == 1 and first_label) else " — "
        lines.append(f"{i}. {name}{label}{pct}%")
    return "\n".join(lines)


def _fmt_team_players(section: dict, home: str, away: str) -> str:
    """Render per-team picks, one line per team: 'Team: Player 22%, Player 12%'."""
    lines = []
    for side, label in (("home", home), ("away", away)):
        items = section.get(side) or []
        if items:
            inner = ", ".join(f"{name} {pct}%" for name, pct in items)
            lines.append(f"{label}: {inner}")
    return "\n".join(lines)


def format_message(home: str, away: str, pred: dict, ai_sections: dict) -> str:
    s = pred["sections"]
    parts = [f"⚽ {home} vs {away} — World Cup", "━━━━━━━━━━━━━━━━━━"]

    def section(key: str, body: str | None) -> None:
        if body:
            parts.append(f"\n{SECTION_TITLES[key]}\n{body}")

    section(
        "final_result",
        _fmt_final_result(s["final_result"]["results"]) if "final_result" in s
        else ai_sections.get("final_result"),
    )
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
    section(
        "first_card",
        _fmt_team_players(s["first_card"], home, away) if "first_card" in s
        else ai_sections.get("first_card"),
    )
    section(
        "first_sub",
        _fmt_team_players(s["first_sub"], home, away) if "first_sub" in s
        else ai_sections.get("first_sub"),
    )

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
- "final_result": "1. 1-0 — 18%\\n2. 1-1 — 14%\\n3. 2-1 — 11%\\n4. 0-0 — 10%"
  (the 4 most likely FULL-TIME scorelines with probabilities; no odds)
- "first_half"/"second_half": "Most likely: 1-0 (28%)\\nAlt: 0-0 (24%)"
- "scorers": "1. Player — first scorer 18%\\n2. Player — 14%\\n3. Player — 11%"
- "first_card"/"first_sub": one line PER TEAM, using the real team names, with
  the 2 most likely players each:
  "HomeTeam: Player 22%, Player 12%\\nAwayTeam: Player 18%, Player 9%"
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
