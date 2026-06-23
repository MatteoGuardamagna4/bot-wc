"""Web dashboard for World Cup predictions.

A thin Flask layer over the same pipeline the Telegram bot uses:
  - lists upcoming World Cup fixtures (api_football.upcoming_fixtures)
  - runs analyzer.analyze() for a selected match and returns structured JSON

Access is gated by ACCESS_CODE (same code as the bot's /login), stored in a
signed session cookie. Run locally with `python web.py`; in production it is
served by gunicorn (see README).
"""
from __future__ import annotations

import functools
import logging

from flask import Flask, jsonify, request, send_from_directory, session

import api_football
from analyzer import MatchNotFoundError, analyze, format_message
from config import ACCESS_CODE, FLASK_SECRET

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("web")

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = FLASK_SECRET


# ----------------------------------------------------------------- auth


def _authed() -> bool:
    return bool(session.get("authed"))


def require_auth(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not _authed():
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)

    return wrapper


@app.post("/api/login")
def login():
    code = (request.get_json(silent=True) or {}).get("code", "").strip()
    if not ACCESS_CODE:
        return jsonify({"error": "Server has no ACCESS_CODE configured."}), 500
    if code and code == ACCESS_CODE:
        session["authed"] = True
        session.permanent = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Wrong code."}), 401


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/session")
def session_state():
    return jsonify({"authed": _authed()})


# ----------------------------------------------------------------- data


@app.get("/api/matches")
@require_auth
def matches():
    fixtures = api_football.upcoming_fixtures()
    if not fixtures and not api_football.schedule_available():
        return jsonify({"error": "Schedule provider unreachable."}), 503
    return jsonify({"matches": fixtures})


@app.get("/api/team-history")
@require_auth
def team_history():
    """A team's completed World Cup 2026 matches: formation, booked players
    and substitutions per game (real API-Football data only)."""
    team = (request.args.get("team") or "").strip()
    team_id_arg = (request.args.get("team_id") or "").strip()
    if not team and not team_id_arg:
        return jsonify({"error": "Missing team."}), 400

    team_id = None
    if team_id_arg.isdigit():
        team_id = int(team_id_arg)
    elif team:
        team_id = api_football.find_team_id(team)

    if not team_id:
        if not api_football.schedule_available():
            return jsonify({"error": "Schedule provider unreachable."}), 503
        return jsonify({"error": f"Couldn't recognize the team '{team}'."}), 404

    try:
        data = api_football.team_tournament_history(team_id, team)
    except Exception:  # never leak a stack trace to the client
        log.exception("Team history failed for %s", team or team_id)
        return jsonify({"error": "Could not load team history."}), 500
    return jsonify(data)


@app.get("/api/predict")
@require_auth
def predict():
    home = (request.args.get("home") or "").strip()
    away = (request.args.get("away") or "").strip()
    if not home or not away:
        return jsonify({"error": "Missing home/away team."}), 400
    try:
        data = analyze(home, away)
    except MatchNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception:  # never leak a stack trace to the client
        log.exception("Analysis failed for %s vs %s", home, away)
        return jsonify({"error": "Analysis failed, please try again."}), 500

    pred = data["pred"]
    sources = list(pred.get("sources", []))
    if data["ai_sections"]:
        sources.append("AI (fallback for missing markets)")

    # Official lineups, only when at least one side has a published XI.
    lineups_out = None
    lineups = data.get("lineups") or {}
    home_l = lineups.get("home") or {}
    away_l = lineups.get("away") or {}
    if home_l.get("starters") or away_l.get("starters"):
        lineups_out = {"source": "official", "home": home_l, "away": away_l}

    return jsonify(
        {
            "home": data["home"],
            "away": data["away"],
            "date": data["date"],
            "sections": pred.get("sections", {}),
            "ai_sections": data["ai_sections"],
            "sources": sources,
            "lambdas": pred.get("lambdas"),
            "lineups": lineups_out,
            # plain-text version (same as the Telegram message) for copy/share
            "text": format_message(
                data["home"], data["away"], pred, data["ai_sections"]
            ),
        }
    )


# ----------------------------------------------------------------- frontend


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    # Local dev server. Production uses gunicorn (see README).
    app.run(host="0.0.0.0", port=8000, debug=True)
