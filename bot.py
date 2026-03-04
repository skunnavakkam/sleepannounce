from __future__ import annotations

import os
import re
import uuid
import logging
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

import pytz
from telegram import Update
from telegram.helpers import mention_html
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TZ = pytz.timezone("America/Los_Angeles")

TRIGGER_RE = re.compile(
    r"^\s*(sleep|eep|nap)\s*/\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$",
    re.IGNORECASE,
)

# cancel_id -> {"job": Job, "chat_id": int, "user_name": str, "wake_time": datetime}
pending_jobs: dict = {}


def resolve_time(hour: int, minute: int, ampm: str | None) -> datetime:
    """Resolve a user-provided time to the next valid datetime in Pacific time."""
    now = datetime.now(TZ)

    if ampm:
        ampm = ampm.lower()
        if ampm == "am" and hour == 12:
            hour = 0
        elif ampm == "pm" and hour != 12:
            hour += 12
        wake = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if wake <= now:
            wake += timedelta(days=1)
        return wake

    # No AM/PM: pick the next occurrence
    # Try the hour as-is first (could be 0-23 if <= 12 we try both interpretations)
    candidates = []
    if hour <= 12:
        # Interpret as both AM and PM
        h_am = 0 if hour == 12 else hour
        h_pm = hour if hour == 12 else hour + 12
        for h in (h_am, h_pm):
            candidate = now.replace(hour=h, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            candidates.append(candidate)
    else:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        candidates.append(candidate)

    return min(candidates)


def make_cancel_id() -> str:
    return uuid.uuid4().hex[:6]


async def wake_up_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the wake-up alert and clean up."""
    data = context.job.data
    cancel_id = data["cancel_id"]
    name = data["user_name"]
    user_id = data["user_id"]
    chat_id = data["chat_id"]

    mention = mention_html(user_id, name)
    text = (
        "\U0001f6a8 WAKE UP CALL \U0001f6a8\n\n"
        f"{mention} wanted to be woken up NOW!\n\n"
        "Wake them up!! \U0001f4e2"
    )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    pending_jobs.pop(cancel_id, None)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check every message for a sleep trigger."""
    if not update.message or not update.message.text:
        return

    match = TRIGGER_RE.match(update.message.text)
    if not match:
        return

    hour = int(match.group(2))
    minute = int(match.group(3)) if match.group(3) else 0
    ampm = match.group(4)

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return
    if ampm and hour > 12:
        return

    wake_time = resolve_time(hour, minute, ampm)
    delay = (wake_time - datetime.now(TZ)).total_seconds()
    if delay <= 0:
        return

    if delay < 3600:
        await update.message.set_reaction("\U0001f5ff")

    cancel_id = make_cancel_id()
    name = update.message.from_user.first_name
    chat_id = update.message.chat_id

    job = context.job_queue.run_once(
        wake_up_callback,
        when=delay,
        data={
            "cancel_id": cancel_id,
            "user_name": name,
            "user_id": update.message.from_user.id,
            "chat_id": chat_id,
        },
        name=cancel_id,
    )

    pending_jobs[cancel_id] = {
        "job": job,
        "chat_id": chat_id,
        "user_name": name,
        "wake_time": wake_time,
    }

    time_str = wake_time.strftime("%-I:%M %p")
    date_str = wake_time.strftime("%A, %B %-d")

    text = (
        f"\U0001f634 Sleep well, {name}!\n\n"
        f"Everypony will be alerted to wake you up at {time_str} on {date_str}.\n"
        "Sweet dreams! \U0001f319\n\n"
        f"/cancel {cancel_id}"
    )
    await update.message.reply_text(text)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel a pending wake-up reminder by its ID."""
    if not context.args:
        await update.message.reply_text("Usage: /cancel <id>")
        return

    cancel_id = context.args[0].lower()
    entry = pending_jobs.pop(cancel_id, None)

    if entry is None:
        await update.message.reply_text(f"No pending reminder with ID `{cancel_id}`.")
        return

    entry["job"].schedule_removal()
    await update.message.reply_text(
        f"Cancelled wake-up reminder for {entry['user_name']}."
    )


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN environment variable.")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()
