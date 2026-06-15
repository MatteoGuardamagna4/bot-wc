"""Telegram bot entry point.

Usage:  python bot.py
Then in Telegram:  /analyze ARG vs FRA

Access control: only the owner (TELEGRAM_CHAT_ID) can use the bot.
Other users must first send  /login <ACCESS_CODE>  (code set in .env).
Authorized user ids persist in authorized.json next to this file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from analyzer import MatchNotFoundError, run_analysis
from config import ACCESS_CODE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, resolve_team

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

ANALYZE_RE = re.compile(r"^(.+?)\s+(?:vs\.?|v|-)\s+(.+)$", re.IGNORECASE)

# ----------------------------------------------------------------- auth

AUTH_FILE = Path(__file__).parent / "authorized.json"


def _load_authorized() -> set[int]:
    ids: set[int] = set()
    if TELEGRAM_CHAT_ID:
        try:
            ids.add(int(TELEGRAM_CHAT_ID))
        except ValueError:
            pass
    if AUTH_FILE.exists():
        try:
            ids.update(int(x) for x in json.loads(AUTH_FILE.read_text()))
        except (ValueError, json.JSONDecodeError):
            log.warning("authorized.json unreadable — ignoring it")
    return ids


_AUTHORIZED: set[int] = _load_authorized()


def _save_authorized() -> None:
    try:
        AUTH_FILE.write_text(json.dumps(sorted(_AUTHORIZED)))
    except OSError as e:
        log.warning("Could not persist authorized.json: %s", e)


def _is_authorized(update: Update) -> bool:
    user = update.effective_user
    return bool(user) and user.id in _AUTHORIZED


async def _deny(update: Update) -> None:
    log.info(
        "Unauthorized access attempt from id=%s (@%s)",
        update.effective_user.id if update.effective_user else "?",
        update.effective_user.username if update.effective_user else "?",
    )
    await update.message.reply_text(
        "🔒 This is a private bot.\nIf you have an access code, send:\n/login YOUR_CODE"
    )


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_authorized(update):
        await update.message.reply_text("✅ You already have access.")
        return
    code = " ".join(context.args).strip() if context.args else ""
    if not ACCESS_CODE:
        await update.message.reply_text("🔒 Access by code is disabled.")
        return
    if code == ACCESS_CODE:
        _AUTHORIZED.add(update.effective_user.id)
        _save_authorized()
        log.info("New authorized user: %s", update.effective_user.id)
        await update.message.reply_text(
            "✅ Access granted! Try:  /analyze ARG vs FRA"
        )
    else:
        await update.message.reply_text("❌ Wrong code.")


# ----------------------------------------------------------------- commands


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return
    await update.message.reply_text(
        "⚽ World Cup Prediction Bot\n\n"
        "Send:  /analyze ARG vs FRA\n"
        "I'll fetch bookmaker odds + player stats and compute predictions for:\n"
        "• Top 4 final results (with odds)\n• 1st & 2nd half scores\n"
        "• Goalscorers\n• First card (2 per team)\n• First substitution (2 per team)"
    )


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return
    args_text = " ".join(context.args) if context.args else ""
    m = ANALYZE_RE.match(args_text.strip())
    if not m:
        await update.message.reply_text(
            "Usage: /analyze TEAM1 vs TEAM2\nExample: /analyze ARG vs FRA"
        )
        return

    team1 = resolve_team(m.group(1))
    team2 = resolve_team(m.group(2))

    await update.message.reply_text(f"🔍 Analyzing {team1} vs {team2}... (~60s)")
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        # Run blocking data pipeline (APIs + model + optional AI) off the event loop
        message = await asyncio.to_thread(run_analysis, team1, team2)
        await update.message.reply_text(message)
    except MatchNotFoundError as e:
        log.info("Match not found: %s vs %s (%s)", team1, team2, e)
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:  # never leave the user without a reply
        log.exception("Analysis failed")
        await update.message.reply_text(f"❌ Analysis failed: {e}")


async def post_init(app: Application) -> None:
    me = await app.bot.get_me()
    log.info(
        "Bot @%s is running (authorized users: %d). Waiting for /analyze...",
        me.username,
        len(_AUTHORIZED),
    )


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing — check your .env file.")
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
