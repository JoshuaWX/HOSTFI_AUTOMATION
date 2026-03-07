"""
Module: tasks.py
Purpose: APScheduler job definitions — daily digest and price alert checker
Author: HOSTFI Bot Team
"""

import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telegram import LinkPreviewOptions
from telegram.ext import Application

from config import ADMIN_CHANNEL_ID, COMMUNITY_GROUP_ID

logger = logging.getLogger(__name__)

# West Africa Time = UTC+1
WAT = timezone(timedelta(hours=1))

# Module-level scheduler instance
_scheduler: AsyncIOScheduler | None = None


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------


def get_scheduler() -> AsyncIOScheduler:
    """
    Return the singleton AsyncIOScheduler instance.

    Returns:
        The APScheduler instance
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=WAT)
    return _scheduler


def setup_scheduler(application: Application) -> AsyncIOScheduler:
    """
    Create, configure, and start the APScheduler with all recurring jobs.

    Jobs registered:
    - **daily_digest**: Posts market digest to community group at 9:00 AM WAT
    - **price_alert_checker**: Checks active alerts every 5 minutes
    - **weekly_leaderboard**: Posts XP leaderboard Sundays at 12:00 PM WAT
    - **ticket_escalation**: Re-alerts unclaimed tickets every 30 minutes
    - **daily_report**: Posts admin report to admin channel at 7:00 AM WAT

    Args:
        application: The telegram.ext.Application instance (needed to
                     obtain the bot for sending messages)

    Returns:
        The started AsyncIOScheduler instance
    """
    scheduler = get_scheduler()

    # Daily digest at 9:00 AM WAT
    scheduler.add_job(
        daily_digest_job,
        trigger=CronTrigger(hour=9, minute=0, timezone=WAT),
        args=[application],
        id="daily_digest",
        name="Daily Market Digest",
        replace_existing=True,
    )

    # Price alert checker every 5 minutes
    scheduler.add_job(
        price_alert_checker_job,
        trigger=IntervalTrigger(minutes=5),
        args=[application],
        id="price_alert_checker",
        name="Price Alert Checker",
        replace_existing=True,
    )

    # Weekly XP leaderboard post — Sundays at 12:00 PM WAT
    scheduler.add_job(
        weekly_leaderboard_job,
        trigger=CronTrigger(day_of_week="sun", hour=12, minute=0, timezone=WAT),
        args=[application],
        id="weekly_leaderboard",
        name="Weekly Leaderboard Post",
        replace_existing=True,
    )

    # Ticket escalation checker every 30 minutes
    scheduler.add_job(
        ticket_escalation_job,
        trigger=IntervalTrigger(minutes=30),
        args=[application],
        id="ticket_escalation",
        name="Ticket Escalation Checker",
        replace_existing=True,
    )

    # Daily admin report at 7:00 AM WAT
    scheduler.add_job(
        daily_report_job,
        trigger=CronTrigger(hour=7, minute=0, timezone=WAT),
        args=[application],
        id="daily_report",
        name="Daily Admin Report",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "APScheduler started with %d jobs: %s",
        len(scheduler.get_jobs()),
        [j.name for j in scheduler.get_jobs()],
    )
    return scheduler


def shutdown_scheduler() -> None:
    """Gracefully shut down the scheduler if running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler shut down")
        _scheduler = None


# ---------------------------------------------------------------------------
# Job: Daily Digest (9 AM WAT → community group)
# ---------------------------------------------------------------------------


async def daily_digest_job(application: Application) -> None:
    """
    Post the daily market digest to the community group.

    Called by APScheduler at 9:00 AM WAT every day.

    Args:
        application: The telegram.ext.Application instance
    """
    try:
        from bot.handlers.market import build_daily_digest

        digest = await build_daily_digest()

        await application.bot.send_message(
            chat_id=COMMUNITY_GROUP_ID,
            text=digest,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

        logger.info("Daily digest posted to community group")

    except Exception as exc:
        logger.error("Failed to post daily digest: %s", exc)
        # Notify admin channel about the failure
        try:
            await application.bot.send_message(
                chat_id=ADMIN_CHANNEL_ID,
                text=(
                    "⚠️ <b>Scheduler Alert</b>\n\n"
                    f"Daily digest failed to post.\nError: <code>{exc}</code>"
                ),
                parse_mode="HTML",
            )
        except Exception as notify_exc:
            logger.error("Failed to notify admins about digest failure: %s", notify_exc)


# ---------------------------------------------------------------------------
# Job: Price Alert Checker (every 5 minutes)
# ---------------------------------------------------------------------------


async def price_alert_checker_job(application: Application) -> None:
    """
    Check all active price alerts against current prices and notify users.

    Fetches current prices for all coins with active alerts, compares
    against target prices, and sends a DM to the user when triggered.
    Triggered alerts are deactivated.

    Args:
        application: The telegram.ext.Application instance
    """
    try:
        from database.alerts import deactivate_alert, get_active_alerts
        from bot.handlers.market import (
            COINGECKO_BASE,
            COIN_SYMBOLS,
            _cached_get,
            _fmt_price,
        )

        alerts = await get_active_alerts()
        if not alerts:
            return

        # Collect unique coin IDs
        coin_ids: set[str] = {a["coin_id"] for a in alerts}
        coin_ids_str = ",".join(coin_ids)

        # Fetch current prices in one API call
        price_data = await _cached_get(
            f"{COINGECKO_BASE}/simple/price",
            {"ids": coin_ids_str, "vs_currencies": "usd"},
        )

        triggered_count = 0

        for alert in alerts:
            coin_id = alert.get("coin_id", "")
            target = float(alert.get("target_price", 0))
            direction = alert.get("direction", "above")
            user_id = alert.get("user_telegram_id")
            alert_id = alert.get("id")

            current_price = price_data.get(coin_id, {}).get("usd")
            if current_price is None:
                continue

            triggered = False
            if direction == "above" and current_price >= target:
                triggered = True
            elif direction == "below" and current_price <= target:
                triggered = True

            if triggered:
                symbol = COIN_SYMBOLS.get(coin_id, coin_id.upper())
                direction_emoji = "⬆️" if direction == "above" else "⬇️"

                msg = (
                    f"🔔 <b>Price Alert Triggered!</b>\n\n"
                    f"🪙 <b>{symbol}</b> has crossed your target!\n"
                    f"🎯 Target: <code>${_fmt_price(target)}</code> "
                    f"{direction_emoji} {direction}\n"
                    f"💰 Current: <code>${_fmt_price(current_price)}</code>\n\n"
                    f"📲 Trade now on <b>HostFi</b> — https://hostfi.io"
                )

                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=msg,
                        parse_mode="HTML",
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                    )
                except Exception as send_exc:
                    logger.warning(
                        "Could not DM user %s for alert %s: %s",
                        user_id,
                        alert_id,
                        send_exc,
                    )

                # Deactivate alert regardless of DM success
                await deactivate_alert(alert_id)
                triggered_count += 1

        if triggered_count > 0:
            logger.info("Price alert checker: %d alerts triggered", triggered_count)

    except Exception as exc:
        logger.error("Price alert checker failed: %s", exc)


# ---------------------------------------------------------------------------
# Job: Weekly Leaderboard (Sunday 12 PM WAT → community group)
# ---------------------------------------------------------------------------


async def weekly_leaderboard_job(application: Application) -> None:
    """
    Post the weekly XP leaderboard to the community group.

    Called by APScheduler every Sunday at 12:00 PM WAT.

    Args:
        application: The telegram.ext.Application instance
    """
    try:
        from bot.handlers.broadcast import build_leaderboard_message

        leaderboard_msg = await build_leaderboard_message()

        await application.bot.send_message(
            chat_id=COMMUNITY_GROUP_ID,
            text=leaderboard_msg,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

        logger.info("Weekly leaderboard posted to community group")

    except Exception as exc:
        logger.error("Failed to post weekly leaderboard: %s", exc)
        try:
            await application.bot.send_message(
                chat_id=ADMIN_CHANNEL_ID,
                text=(
                    "⚠️ <b>Scheduler Alert</b>\n\n"
                    f"Weekly leaderboard failed to post.\nError: <code>{exc}</code>"
                ),
                parse_mode="HTML",
            )
        except Exception as notify_exc:
            logger.error("Failed to notify admins about leaderboard failure: %s", notify_exc)


# ---------------------------------------------------------------------------
# Job: Ticket Escalation Checker (every 30 minutes)
# ---------------------------------------------------------------------------


async def ticket_escalation_job(application: Application) -> None:
    """
    Re-alert the admin channel about tickets unclaimed for 2+ hours.

    Called by APScheduler every 30 minutes.

    Args:
        application: The telegram.ext.Application instance
    """
    try:
        from bot.handlers.tickets import build_escalation_alerts

        count = await build_escalation_alerts(application.bot)
        if count:
            logger.info("Ticket escalation: %d alert(s) sent", count)

    except Exception as exc:
        logger.error("Ticket escalation check failed: %s", exc)


# ---------------------------------------------------------------------------
# Job: Daily Admin Report (7 AM WAT → admin channel)
# ---------------------------------------------------------------------------


async def daily_report_job(application: Application) -> None:
    """
    Post the daily admin report to the admin channel.

    Called by APScheduler at 7:00 AM WAT every day.

    Args:
        application: The telegram.ext.Application instance
    """
    try:
        from bot.handlers.admin import build_daily_report

        report = await build_daily_report()

        await application.bot.send_message(
            chat_id=ADMIN_CHANNEL_ID,
            text=report,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

        logger.info("Daily admin report posted to admin channel")

    except Exception as exc:
        logger.error("Failed to post daily report: %s", exc)
        try:
            await application.bot.send_message(
                chat_id=ADMIN_CHANNEL_ID,
                text=(
                    "⚠️ <b>Scheduler Alert</b>\n\n"
                    f"Daily report failed to generate.\nError: <code>{exc}</code>"
                ),
                parse_mode="HTML",
            )
        except Exception as notify_exc:
            logger.error("Failed to notify about report failure: %s", notify_exc)
