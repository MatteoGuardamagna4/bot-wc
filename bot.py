"""Telegram bot entry point.

Usage:  python bot.py
Then in Telegram:  /analyze ARG vs FRA
"""
from __future__ import annotations

import asyncio
import logging
import re

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from analyzer import run_analysis
from config import TELEGRAM_BOT_TOKEN, resolve_team

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

ANALYZE_RE = re.compile(r"^(.+?)\s+(?:vs\.?|v|-)\s+(.+)$", re.IGNORECASE)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚽ World Cup Prediction Bot\n\n"
        "Send:  /analyze ARG vs FRA\n"
        "I'll fetch bookmaker odds + player stats and compute predictions for:\n"
        "• 1st & 2nd half scores\n• Goalscorers\n• First card\n• First substitution"
    )


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    except Exception as e:  # never leave the user without a reply
        log.exception("Analysis failed")
        await update.message.reply_text(f"❌ Analysis failed: {e}")


async def post_init(app: Application) -> None:
    me = await app.bot.get_me()
    log.info("Bot @%s is running. Waiting for /analyze commands...", me.username)


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
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
