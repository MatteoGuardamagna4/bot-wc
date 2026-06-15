# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A private Telegram bot for World Cup 2026 match predictions. Users send
`/analyze ARG vs FRA` and get predictions for: the 4 most likely final
results (with bookmaker odds), 1st/2nd half scores, goalscorers, first card
and first substitution (the 2 most likely players **per team**). **Real data
first, AI only as fallback** — the core principle of this codebase.

## Run & test

```bash
pip install -r requirements.txt
python bot.py                        # start the Telegram bot (needs .env)
python web.py                        # start the web dashboard on :8000

# Module-level smoke tests (no Telegram needed):
python espn.py Canada                # ESPN player stats
python sofascore.py Canada           # Sofascore player stats
python scraper.py Argentina France   # Betexplorer scraping
python -c "from analyzer import run_analysis; print(run_analysis('Argentina','France'))"
```

There is no test suite or linter configured. Deployed on Railway as **two
services from the same repo**: the bot (Dockerfile default `CMD python bot.py`,
polling worker, no HTTP port) and the web dashboard (custom start command
`gunicorn web:app -c gunicorn.conf.py`; the port is read from `$PORT` inside
`gunicorn.conf.py`, not passed on the command line).

## Architecture / data pipeline

Two entry points share one pipeline:
- `bot.py` (Telegram, auth) → `analyzer.run_analysis()` → formatted message string.
- `web.py` (Flask, `ACCESS_CODE` login) → `analyzer.analyze()` → structured JSON
  rendered by `static/index.html`. `analyze()` returns `{home, away, date, pred,
  ai_sections}`; `run_analysis()` is just `analyze()` + `format_message()`.
  Match list comes from `api_football.upcoming_fixtures()`.

Order of preference inside the pipeline:

1. `api_football.py` — primary source (api-sports.io, key in `.env`, Pro plan).
   Fixture lookup restricted to the official WC 2026 calendar
   (`WC_LEAGUE_ID=1`, `WC_SEASON=2026`), bookmaker odds (1X2, O/U 2.5,
   first-half winner, correct score), per-player stats from `/fixtures/events`,
   and official lineups from `/fixtures/lineups` (published ~20–60 min before
   kickoff).
2. `espn.py` — free fallback for player stats (no key needed).
3. `sofascore.py` — second fallback, only if `SOFASCORE_ENABLED=true`
   (TLS-fingerprint blocked on many IPs; uses `curl_cffi` impersonation,
   self-disables for 30 min after a full host/fingerprint rotation failure).
4. `model.py` — pure-Python Poisson model. Odds → implied probabilities →
   grid-searched expected goals (λ) → score matrices, scorer/card/sub
   probabilities. **No AI, no I/O in this module.**
5. `analyzer.ai_fill_sections()` — GPT-4o fills ONLY sections the data could
   not cover (`scraper.py` Betexplorer scrape provides extra context).

The message footer always declares which sources were used.

## Key behaviors (do not break)

- **Match validation**: `run_analysis` raises `analyzer.MatchNotFoundError`
  when the requested game is not in the upcoming WC schedule (or the schedule
  is unreachable). `bot.py` catches it and shows the message to the user.
  Never silently analyze a non-existent fixture.
- **Official lineups**: when `/fixtures/lineups` returns data, player markets
  are filtered to players actually in the squad — starters+bench for
  goalscorers, starters only for first card / first substitution
  (`model.filter_to_lineup`). The AI fallback also receives the lineups and
  must only suggest listed players. "official lineups" is added to sources.
- **Failure tolerance**: every data module (`api_football`, `espn`,
  `sofascore`, `scraper`) returns `{}`/`[]`/`None` on any failure and logs a
  warning — they never raise. Only `analyzer` raises (MatchNotFoundError),
  and `bot.cmd_analyze` wraps everything so the user always gets a reply.
- **Auth**: only `TELEGRAM_CHAT_ID` plus users who sent `/login ACCESS_CODE`
  (persisted in `authorized.json`) may use the bot.

## Conventions & gotchas

- Team names differ between sources ("Bosnia & Herzegovina" vs
  "Bosnia-Herzegovina"); `espn._norm`/`_names_match` handle this. Player
  names also differ ("L. Messi" vs "Lionel Messi"); `model._norm_name`/
  `_name_matches` do tolerant matching (accents stripped, last name + first
  initial). Reuse these helpers instead of comparing raw strings.
- User input team codes resolve via `config.TEAM_CODES` (`resolve_team`).
- API-Football free vs paid plan matters: `last`/`next` fixture params and
  first-half odds are paid-only; code paths degrade gracefully.
- API budget: WC schedule and team list are cached in `api_football`
  (`wc_fixtures()`, `_wc_teams()`); each analysis costs ~15 requests.
- `.env` holds all secrets (Telegram token, OpenAI, API-Football, access
  code) — never commit or log it. `config.py` is the only module reading env.
- Long blocking work runs via `asyncio.to_thread` in `bot.py`; keep the
  pipeline synchronous.
- Sofascore: do NOT add custom User-Agent headers when using `curl_cffi`
  impersonation (fingerprint mismatch → 403). See comment in `sofascore.py`.
- README.md is in Italian and user-facing; keep it updated on behavior
  changes.
