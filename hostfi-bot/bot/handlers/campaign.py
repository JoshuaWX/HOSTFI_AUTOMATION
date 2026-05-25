"""
Module: campaign.py
Purpose: XP campaign commands for cycles, invites, X verification, raids, and awards
Author: HOSTFI Bot Team
"""

import html
import logging
import secrets
from datetime import datetime, timedelta, timezone

from telegram import LinkPreviewOptions, Message, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot.utils.auto_delete import (
    schedule_any_delete,
    schedule_command_delete,
    schedule_delete,
    send_dm_redirect_status,
)
from bot.utils.formatter import bullet, field, format_rules, status_text, title
from bot.utils.keyboards import (
    campaign_earn_keyboard,
    campaign_group_keyboard,
    campaign_cancel_keyboard,
    campaign_home_keyboard,
    campaign_raid_keyboard,
    campaign_xverify_keyboard,
    xpost_review_keyboard,
)
from bot.utils.permissions import is_admin, is_admin_channel_chat, is_superadmin
from bot.utils.rate_limiter import check_rate_limit
from bot.utils.x_api import (
    XApiNotConfigured,
    fetch_post,
    is_meaningful_x_text,
    is_reply_or_quote_to,
    is_x_api_configured,
    parse_x_post_url,
)
from config import (
    ADMIN_CHANNEL_ID,
    COMMUNITY_GROUP_IDS,
    INVITE_RETENTION_HOURS,
    get_invite_target_group_id,
    is_community_group_chat,
)
from database.campaign import (
    XP_HELPFUL,
    XP_INVITE,
    XP_RAID,
    award_xp,
    close_raid,
    create_raid,
    create_x_verification,
    delete_x_post_submission,
    finish_cycle,
    get_active_cycle,
    get_active_raids,
    get_campaign_leaderboard,
    get_campaign_rank,
    get_expired_active_raids,
    get_invite_stats,
    get_or_create_invite_link_record,
    get_pending_invite_joins,
    get_raid,
    get_raid_messages,
    get_x_post_submission,
    get_x_account,
    has_daily_x_post,
    has_raid_submission,
    is_disqualified,
    mark_x_post_submission_reviewed,
    mark_invite_join,
    record_invite_join,
    record_raid_message,
    record_raid_submission,
    record_x_post_submission,
    start_cycle,
    verify_x_account,
)
from database.logs import log_action
from database.users import get_or_create_user, get_user, get_user_by_username

logger = logging.getLogger(__name__)

PENDING_ACTION_KEY = "campaign_pending_action"


def _utc_now() -> datetime:
    """Return UTC now."""
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    """Parse a Supabase timestamp."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _display_user(row: dict) -> str:
    """Return a safe display name for leaderboard rows."""
    name = row.get("first_name") or row.get("username") or str(row.get("telegram_id"))
    return html.escape(str(name))


def _profile_link(telegram_id: int, username: str | None = None, first_name: str | None = None) -> str:
    """Return a clickable Telegram profile link."""
    label = f"@{username}" if username else (first_name or str(telegram_id))
    return f'<a href="tg://user?id={telegram_id}">{html.escape(str(label))}</a>'


def _campaign_missing() -> str:
    """Message used when no campaign cycle is active."""
    return (
        title("No Active Campaign", "⚠️")
        + "\n\nA superadmin can start one with <code>/cycle start</code>."
    )


def _x_missing() -> str:
    """Message used when X API is not configured."""
    return (
        title("X API Not Configured", "⚠️")
        + "\n\nSet <code>X_BEARER_TOKEN</code> before using X-based XP commands."
    )


def _target_community_group_id(update: Update) -> int:
    """Return the configured group where campaign referral links must point."""
    return get_invite_target_group_id()


def _safe_url_preview(url: str | None) -> str | None:
    """Return a short URL preview for logs without dumping full user content."""
    if not url:
        return None
    return url[:96]


def _log_raid_proof_rejection(
    reason: str,
    telegram_id: int | None,
    raid_id: int | str | None,
    *,
    proof_post_id: str | None = None,
    proof_url: str | None = None,
    level: int = logging.INFO,
) -> None:
    """Log a safe, structured raid-proof rejection record."""
    logger.log(
        level,
        "Raid proof rejected reason=%s telegram_id=%s raid_id=%s proof_post_id=%s proof_url=%s",
        reason,
        telegram_id,
        raid_id,
        proof_post_id,
        _safe_url_preview(proof_url),
    )


async def _send_to_community_groups(bot, **kwargs) -> int:
    """Send one message to every configured community group."""
    sent = 0
    for chat_id in COMMUNITY_GROUP_IDS:
        await bot.send_message(chat_id=chat_id, **kwargs)
        sent += 1
    return sent


def _retention_label() -> str:
    """Return a short label for invite XP retention copy."""
    return f"{INVITE_RETENTION_HOURS} hour" + ("" if INVITE_RETENTION_HOURS == 1 else "s")


def _is_community_group_update(update: Update) -> bool:
    """Return True when an update happened in a configured community group."""
    chat = update.effective_chat
    return bool(chat and chat.type in ("group", "supergroup") and is_community_group_chat(chat.id))


async def _send_dm_or_group_notice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    parse_mode: str | None = "HTML",
    reply_markup=None,
) -> bool:
    """Send a DM-first instruction and clean up the public group notice."""
    if not update.effective_user or not update.effective_message:
        return False
    try:
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
    except Exception:
        await send_dm_redirect_status(update, context, dm_sent=False)
        return False
    await send_dm_redirect_status(update, context, dm_sent=True)
    return True


async def _ensure_user(update: Update) -> int | None:
    """Create/touch the Telegram user and return their id."""
    user = update.effective_user
    if not user:
        return None
    await get_or_create_user(user.id, user.username, user.first_name)
    return user.id


def _campaign_home_text(cycle: dict) -> str:
    """Build the main campaign hub text."""
    end_at = _parse_iso(cycle.get("end_at"))
    ends = end_at.strftime("%Y-%m-%d %H:%M UTC") if end_at else "Manual finish"
    return (
        title(f"XP Campaign · Cycle #{cycle.get('cycle_number')}", "🏆")
        + "\n\n"
        + field("Ends", f"<b>{ends}</b>")
        + "\n\n"
        + title("Earn XP")
        + "\n"
        + bullet(f"Raids on X — <b>{XP_RAID} XP</b>")
        + "\n"
        + bullet(f"Telegram invites after {_retention_label()} — <b>{XP_INVITE} XP</b>")
        + "\n"
        + bullet(f"Helpful contributions — <b>{XP_HELPFUL} XP</b>")
        + "\n"
        + bullet("HostFi X posts — admin-reviewed, once daily")
        + "\n\nUse the buttons below to manage your campaign activity."
    )


def _user_dashboard_text() -> str:
    """Build the private user dashboard text."""
    return "\n".join(
        [
            title("HOSTFI Dashboard", "🏠"),
            "",
            "Use the buttons below to manage XP, invites, raids, X posts, and support.",
            "",
            "Most private actions happen here in DM so the group stays clean.",
        ]
    )


def _earn_xp_text() -> str:
    """Build the earning guide text."""
    return "\n".join(
        [
            title("Earn XP", "⭐"),
            "",
            bullet(f"Invite friends — <b>{XP_INVITE} XP</b> after {_retention_label()}"),
            bullet(f"Join approved raids — <b>{XP_RAID} XP</b>"),
            bullet("Submit HostFi X posts — admin-reviewed"),
            bullet(f"Helpful contributions — <b>{XP_HELPFUL} XP</b> with admin approval"),
            "",
            "Choose an earning path below.",
        ]
    )


def _campaign_keyboard_for_message(message: Message):
    """Return the right campaign keyboard for DM or group contexts."""
    if message.chat.type in ("group", "supergroup") and is_community_group_chat(message.chat_id):
        return campaign_group_keyboard()
    return campaign_home_keyboard()


async def _send_campaign_home(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> None:
    """Send the campaign hub to a Telegram message target."""
    cycle = await get_active_cycle()
    if not cycle:
        reply = await message.reply_text(_campaign_missing(), parse_mode="HTML")
        if context:
            await schedule_delete(reply, context, 60)
        return
    reply = await message.reply_text(
        _campaign_home_text(cycle),
        parse_mode="HTML",
        reply_markup=_campaign_keyboard_for_message(message),
    )
    if context:
        await schedule_delete(reply, context, 60)


async def _send_xp_status(
    message: Message,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> None:
    """Send campaign XP status for a user."""
    xp, rank, total, cycle = await get_campaign_rank(user_id)
    if not cycle:
        reply = await message.reply_text(_campaign_missing(), parse_mode="HTML")
        if context:
            await schedule_delete(reply, context, 60)
        return

    rank_text = f"#{rank} of {total}" if rank else "Not ranked yet"
    reply = await message.reply_text(
        "\n".join(
            [
                title("Your Campaign XP", "⭐"),
                "",
                field("Cycle", f"<b>#{cycle.get('cycle_number')}</b>"),
                field("XP", f"<b>{xp:,}</b>"),
                field("Rank", f"<b>{rank_text}</b>"),
            ]
        ),
        parse_mode="HTML",
        reply_markup=campaign_home_keyboard(),
    )
    if context:
        await schedule_delete(reply, context, 60)


async def _send_campaign_leaderboard(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> None:
    """Send the current campaign leaderboard."""
    top_users = await get_campaign_leaderboard(10)
    if not top_users:
        reply = await message.reply_text(
            status_text("info", "No campaign leaderboard data yet."),
            reply_markup=campaign_home_keyboard(),
        )
        if context:
            await schedule_delete(reply, context, 60)
        return

    lines = [title("XP Leaderboard", "🏅"), ""]
    for index, row in enumerate(top_users, 1):
        lines.append(f"{index}. <b>{_display_user(row)}</b> — {row.get('xp', 0):,} XP")
    lines.append("")
    lines.append("Ties are ranked by earliest approved XP event.")
    reply = await message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=campaign_home_keyboard(),
    )
    if context:
        await schedule_delete(reply, context, 60)


def _set_pending(context: ContextTypes.DEFAULT_TYPE, payload: dict) -> None:
    """Store the next guided campaign action for the user."""
    context.user_data[PENDING_ACTION_KEY] = payload


def _clear_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the user's pending guided campaign action."""
    context.user_data.pop(PENDING_ACTION_KEY, None)


async def _start_xlink_for_handle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    username: str,
) -> bool:
    """Create an X verification code for a submitted handle."""
    user_id = await _ensure_user(update)
    if not user_id or not update.effective_message:
        return False
    if not is_x_api_configured():
        await update.effective_message.reply_text(_x_missing(), parse_mode="HTML")
        return False

    clean_username = username.lstrip("@").strip()
    if not clean_username or len(clean_username) > 15:
        await update.effective_message.reply_text(
            status_text("error", "Send a valid X handle, like @hostfi_app."),
            parse_mode="HTML",
            reply_markup=campaign_cancel_keyboard(),
        )
        return False

    code = f"HOSTFI-{secrets.token_hex(3).upper()}"
    record = await create_x_verification(user_id, clean_username, code)
    if not record:
        await update.effective_message.reply_text(status_text("error", "Could not start X verification. Try again."))
        return False

    await update.effective_message.reply_text(
        "\n".join(
            [
                title("X Verification", "🔗"),
                "",
                field("Account", f"<b>@{html.escape(clean_username)}</b>"),
                field("Code", f"<code>{code}</code>"),
                "",
                "Post the code on X, then tap <b>Posted Code</b>.",
            ]
        ),
        parse_mode="HTML",
        reply_markup=campaign_xverify_keyboard(),
    )
    return True


async def _verify_x_post_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> bool:
    """Verify a pending X account with a post URL."""
    user_id = await _ensure_user(update)
    if not user_id or not update.effective_message:
        return False
    if not is_x_api_configured():
        await update.effective_message.reply_text(_x_missing(), parse_mode="HTML")
        return False

    account = await get_x_account(user_id)
    if account and account.get("status") == "verified":
        await update.effective_message.reply_text(
            status_text("info", "Your X account is already linked."),
            parse_mode="HTML",
            reply_markup=campaign_home_keyboard(),
        )
        return True
    if not account or account.get("status") != "pending":
        await update.effective_message.reply_text(
            status_text("warning", "Start by linking your X handle first."),
            parse_mode="HTML",
        )
        return False

    parsed = parse_x_post_url(url)
    if not parsed:
        await update.effective_message.reply_text(
            status_text("error", "Send a valid X verification post URL."),
            reply_markup=campaign_cancel_keyboard(),
        )
        return False

    if not await check_rate_limit(user_id, "xverify", 3, 86400):
        await update.effective_message.reply_text(
            status_text("warning", "You have reached the daily X verification limit. Try again tomorrow."),
            parse_mode="HTML",
        )
        return False

    try:
        post = await fetch_post(parsed[1], url)
    except XApiNotConfigured:
        await update.effective_message.reply_text(_x_missing(), parse_mode="HTML")
        return False
    except Exception as exc:
        logger.error("X verify fetch failed: %s", exc)
        await update.effective_message.reply_text(status_text("error", "Could not verify that X post. Try again later."))
        return False

    expected_user = str(account.get("username") or "").lower().lstrip("@")
    expected_code = str(account.get("verification_code") or "")
    if post.username != expected_user or expected_code not in post.text:
        await update.effective_message.reply_text(
            status_text("error", "Verification failed. The post must come from your linked handle and include the code."),
            reply_markup=campaign_cancel_keyboard(),
        )
        return False

    verified = await verify_x_account(user_id, post.author_id, post.username, post.post_id)
    if not verified:
        await update.effective_message.reply_text(status_text("error", "Could not save X verification. Try again."))
        return False

    await update.effective_message.reply_text(
        title("X Account Linked", "✅") + f"\n\n{field('Account', f'<b>@{html.escape(post.username)}</b>')}",
        parse_mode="HTML",
        reply_markup=campaign_home_keyboard(),
    )
    return True


async def _submit_raid_proof_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raid_id: int,
    proof_url: str,
) -> bool:
    """Submit a raid proof URL and auto-award XP if strict checks pass."""
    user_id = await _ensure_user(update)
    if not user_id or not update.effective_message:
        _log_raid_proof_rejection("missing_user_or_message", user_id, raid_id, proof_url=proof_url, level=logging.WARNING)
        return False
    if not is_x_api_configured():
        _log_raid_proof_rejection("x_api_not_configured", user_id, raid_id, proof_url=proof_url, level=logging.WARNING)
        await update.effective_message.reply_text(_x_missing(), parse_mode="HTML")
        return False

    raid = await get_raid(raid_id)
    if not raid or raid.get("status") != "active":
        _log_raid_proof_rejection("raid_inactive_or_missing", user_id, raid_id, proof_url=proof_url)
        await update.effective_message.reply_text(status_text("error", "Raid not found or no longer active."))
        return False

    cycle = await get_active_cycle()
    if not cycle or int(cycle["id"]) != int(raid["cycle_id"]):
        _log_raid_proof_rejection("cycle_mismatch", user_id, raid_id, proof_url=proof_url)
        await update.effective_message.reply_text(status_text("error", "This raid belongs to a finished campaign cycle."))
        return False
    if await is_disqualified(user_id, int(raid["cycle_id"])):
        _log_raid_proof_rejection("user_disqualified", user_id, raid_id, proof_url=proof_url)
        await update.effective_message.reply_text(status_text("warning", "You are disqualified from the current campaign cycle."))
        return False

    deadline = _parse_iso(raid.get("deadline_at"))
    if deadline and _utc_now() > deadline:
        _log_raid_proof_rejection("raid_expired", user_id, raid_id, proof_url=proof_url)
        await update.effective_message.reply_text(status_text("warning", "This raid has expired."))
        return False
    if await has_raid_submission(raid_id, user_id):
        _log_raid_proof_rejection("duplicate_submission", user_id, raid_id, proof_url=proof_url)
        await update.effective_message.reply_text(status_text("warning", "You already submitted proof for this raid."))
        return False

    account = await get_x_account(user_id)
    if not account or account.get("status") != "verified":
        _log_raid_proof_rejection("x_account_not_linked", user_id, raid_id, proof_url=proof_url)
        await update.effective_message.reply_text(
            status_text("warning", "Link your X account first, then submit raid proof again."),
            parse_mode="HTML",
            reply_markup=campaign_home_keyboard(),
        )
        return False

    parsed = parse_x_post_url(proof_url)
    if not parsed:
        _log_raid_proof_rejection("invalid_proof_url", user_id, raid_id, proof_url=proof_url)
        await update.effective_message.reply_text(
            status_text("error", "Send a valid X proof URL."),
            reply_markup=campaign_cancel_keyboard(),
        )
        return False

    try:
        post = await fetch_post(parsed[1], proof_url)
    except Exception as exc:
        _log_raid_proof_rejection("x_fetch_failed", user_id, raid_id, proof_post_id=parsed[1], proof_url=proof_url, level=logging.WARNING)
        logger.warning("Raid proof fetch failed telegram_id=%s raid_id=%s post_id=%s error=%s", user_id, raid_id, parsed[1], exc)
        await update.effective_message.reply_text(status_text("error", "Could not verify that X proof. Try again later."))
        return False

    if post.author_id != str(account.get("x_user_id")):
        _log_raid_proof_rejection("wrong_x_author", user_id, raid_id, proof_post_id=post.post_id, proof_url=proof_url)
        await update.effective_message.reply_text(status_text("error", "Proof must come from your linked X account."))
        return False
    if not is_reply_or_quote_to(post, str(raid["target_post_id"]), str(raid["target_url"])):
        _log_raid_proof_rejection("not_reply_or_quote", user_id, raid_id, proof_post_id=post.post_id, proof_url=proof_url)
        await update.effective_message.reply_text(status_text("error", "Proof must reply to or quote the approved raid post."))
        return False
    if not is_meaningful_x_text(post.text):
        _log_raid_proof_rejection("low_effort_text", user_id, raid_id, proof_post_id=post.post_id, proof_url=proof_url)
        await update.effective_message.reply_text(status_text("error", "Low-effort raid text does not qualify for XP."))
        return False

    submission = await record_raid_submission(
        raid_id,
        raid["cycle_id"],
        user_id,
        post.post_id,
        proof_url,
        "approved",
        {"text": post.text[:500]},
    )
    if not submission:
        _log_raid_proof_rejection("record_submission_failed", user_id, raid_id, proof_post_id=post.post_id, proof_url=proof_url, level=logging.WARNING)
        await update.effective_message.reply_text(status_text("error", "Could not record your raid proof."))
        return False

    event = await award_xp(
        user_id,
        XP_RAID,
        "raid",
        f"Raid #{raid_id}",
        evidence_url=proof_url,
        external_id=post.post_id,
        metadata={"raid_id": raid_id},
        cycle_id=raid["cycle_id"],
    )
    if not event:
        _log_raid_proof_rejection("award_xp_failed", user_id, raid_id, proof_post_id=post.post_id, proof_url=proof_url, level=logging.WARNING)
        await update.effective_message.reply_text(status_text("error", "Could not award XP. Check that the campaign is active."))
        return False

    await update.effective_message.reply_text(
        title("Raid Approved", "✅") + f"\n\n{field('XP earned', f'<b>{XP_RAID}</b>')}",
        parse_mode="HTML",
        reply_markup=campaign_home_keyboard(),
    )
    return True


def _xpost_review_text(
    submission: dict,
    *,
    user_link: str,
    linked_handle: str,
    url_handle: str,
    url_handle_matches: bool,
    status: str = "Pending",
    reviewed_by: str | None = None,
    xp_awarded: int | None = None,
) -> str:
    """Build the admin review card for a personal X post submission."""
    lines = [
        title("X Post Review", "📝"),
        "",
        field("Submission", f"<b>#{submission.get('id')}</b>"),
        field("Status", f"<b>{html.escape(status)}</b>"),
        field("User", user_link),
        field("Telegram ID", f"<code>{submission.get('telegram_id')}</code>"),
        field("Linked X", f"<b>@{html.escape(linked_handle)}</b>"),
        field("URL Handle", f"<b>@{html.escape(url_handle)}</b>"),
        field("URL Match", "<b>Yes</b>" if url_handle_matches else "<b>No</b>"),
        field("Cycle", f"<b>#{submission.get('cycle_number', submission.get('cycle_id'))}</b>"),
        field("Date", f"<b>{html.escape(str(submission.get('submission_date') or ''))}</b>"),
        "",
        html.escape(str(submission.get("proof_url") or "")),
        "",
        "<i>Admin must manually verify post author, quality, and HostFi relevance.</i>",
    ]
    if reviewed_by:
        lines.insert(4, field("Reviewed by", reviewed_by))
    if xp_awarded is not None:
        lines.insert(5, field("XP awarded", f"<b>{xp_awarded:,}</b>"))
    return "\n".join(lines)


async def _xpost_review_context(submission: dict) -> tuple[str, str, str, bool]:
    """Return display values needed to render an X post review card."""
    metadata = submission.get("metadata") or {}
    linked_handle = str(metadata.get("linked_x_username") or "").lower().lstrip("@")
    url_handle = str(metadata.get("submitted_url_username") or "").lower().lstrip("@")
    url_handle_matches = bool(metadata.get("url_handle_matches"))
    user_row = None
    try:
        user_row = await get_user(int(submission["telegram_id"]))
    except Exception:
        user_row = None
    user_link = _profile_link(
        int(submission["telegram_id"]),
        username=user_row.get("username") if user_row else None,
        first_name=user_row.get("first_name") if user_row else None,
    )
    return user_link, linked_handle, url_handle, url_handle_matches


async def _submit_xpost_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> bool:
    """Submit a personal HostFi-related X post for admin review without X API calls."""
    user_id = await _ensure_user(update)
    if not user_id or not update.effective_message:
        return False

    if not ADMIN_CHANNEL_ID:
        await update.effective_message.reply_text(status_text("error", "Admin review channel is not configured."))
        return False

    cycle = await get_active_cycle()
    if not cycle:
        await update.effective_message.reply_text(_campaign_missing(), parse_mode="HTML")
        return False
    if await is_disqualified(user_id, int(cycle["id"])):
        await update.effective_message.reply_text(status_text("warning", "You are disqualified from the current campaign cycle."))
        return False

    account = await get_x_account(user_id)
    if not account or account.get("status") != "verified":
        await update.effective_message.reply_text(
            status_text("warning", "Link your X account first."),
            parse_mode="HTML",
            reply_markup=campaign_home_keyboard(),
        )
        return False

    parsed = parse_x_post_url(url)
    if not parsed:
        await update.effective_message.reply_text(
            status_text("error", "Send a valid X post URL."),
            reply_markup=campaign_cancel_keyboard(),
        )
        return False

    day = _utc_now().date().isoformat()
    if await has_daily_x_post(user_id, cycle["id"], day):
        await update.effective_message.reply_text(
            status_text("warning", "You already have a pending or approved X post submission today.")
        )
        return False

    linked_handle = str(account.get("username") or "").lower().lstrip("@")
    url_handle = parsed[0].lower().lstrip("@")
    url_handle_matches = bool(linked_handle and linked_handle == url_handle)
    submission = await record_x_post_submission(
        cycle["id"],
        user_id,
        parsed[1],
        url,
        day,
        "pending",
        {
            "linked_x_username": linked_handle,
            "submitted_url_username": url_handle,
            "url_handle_matches": url_handle_matches,
            "requires_manual_review": True,
        },
    )
    if not submission:
        await update.effective_message.reply_text(
            status_text("error", "Could not record this X post. It may already have been submitted.")
        )
        return False

    user = update.effective_user
    user_link = _profile_link(user_id, user.username if user else None, user.first_name if user else None)
    review_submission = {
        **submission,
        "cycle_number": cycle.get("cycle_number"),
    }
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHANNEL_ID,
            text=_xpost_review_text(
                review_submission,
                user_link=user_link,
                linked_handle=linked_handle,
                url_handle=url_handle,
                url_handle_matches=url_handle_matches,
            ),
            parse_mode="HTML",
            reply_markup=xpost_review_keyboard(submission["id"], url),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as exc:
        logger.error("Failed to send X post review alert: %s", exc)
        await delete_x_post_submission(int(submission["id"]))
        await update.effective_message.reply_text(
            status_text("error", "Could not send this to admins for review. Please try again later.")
        )
        return False

    await update.effective_message.reply_text(
        title("X Post Submitted", "✅") + "\n\nYour post was sent to admins for review.",
        parse_mode="HTML",
        reply_markup=campaign_home_keyboard(),
    )
    return True


# ---------------------------------------------------------------------------
# User commands
# ---------------------------------------------------------------------------


async def campaign_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current campaign status and earning rules."""
    if not update.effective_message:
        return
    await _send_campaign_home(update.effective_message, context)
    await schedule_command_delete(update, context, 60)


async def xp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the caller's current campaign XP."""
    user_id = await _ensure_user(update)
    if not user_id or not update.effective_message:
        return

    await _send_xp_status(update.effective_message, user_id, context)
    await schedule_command_delete(update, context, 60)


async def xp_router_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route /xp between user profile view and superadmin adjustment commands."""
    if context.args and context.args[0].lower() in ("add", "deduct", "disqualify"):
        await xp_admin_command(update, context)
        return
    await xp_command(update, context)


async def _get_or_create_primary_invite_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    cycle: dict,
) -> tuple[str | None, str | None]:
    """Return a stable invite link for the primary campaign group."""
    target_chat_id = _target_community_group_id(update)
    if not target_chat_id:
        return None, "No community group is configured."

    existing = await get_or_create_invite_link_record(cycle["id"], user_id, chat_id=target_chat_id)
    if existing and existing.get("invite_link"):
        return existing["invite_link"], None

    try:
        invite = await context.bot.create_chat_invite_link(
            chat_id=target_chat_id,
            name=f"xp-{cycle['id']}-{user_id}",
            creates_join_request=False,
        )
    except Exception as exc:
        logger.error("Failed to create campaign invite link: %s", exc)
        return None, "Could not create your invite link. Make sure the bot can invite users."

    record = await get_or_create_invite_link_record(
        cycle["id"],
        user_id,
        invite.invite_link,
        chat_id=target_chat_id,
    )
    if not record:
        try:
            await context.bot.revoke_chat_invite_link(target_chat_id, invite.invite_link)
        except Exception as exc:
            logger.warning("Failed to revoke unsaved campaign invite link: %s", exc)
        return None, "Could not save your invite link. Please try again."

    return record["invite_link"], None


def _invite_link_text(link: str, cycle: dict) -> str:
    """Build invite-link message text."""
    cycle_number = html.escape(str(cycle.get("cycle_number") or cycle.get("id") or "Active"))
    return (
        f"{title('Campaign Invite Link', '👥')}\n\n"
        f"<code>{html.escape(link)}</code>\n\n"
        f"{field('Cycle', f'<b>#{cycle_number}</b>')}\n"
        f"{field('Reward', f'<b>{XP_INVITE} XP</b>')}\n"
        f"{field('Retention', f'<b>{_retention_label()}</b>')}\n\n"
        "XP is awarded after the invited user stays in the community for the full retention window."
    )


async def invite_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create or return the user's unique campaign invite link."""
    user_id = await _ensure_user(update)
    if not user_id or not update.effective_message:
        return

    if not await check_rate_limit(user_id, "campaign_invite", 5, 300):
        await update.effective_message.reply_text(status_text("warning", "Please wait before requesting another invite link."))
        return

    cycle = await get_active_cycle()
    if not cycle:
        await update.effective_message.reply_text(_campaign_missing(), parse_mode="HTML")
        return

    link, error = await _get_or_create_primary_invite_link(update, context, user_id, cycle)
    if error or not link:
        reply = await update.effective_message.reply_text(status_text("error", error or "Could not create your invite link."))
        await schedule_delete(reply, context, 60)
        return

    invite_text = _invite_link_text(link, cycle)
    chat = update.effective_chat
    if chat and chat.type == "private":
        await update.effective_message.reply_text(
            invite_text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=invite_text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as exc:
        logger.warning("Could not DM invite link to %s: %s", user_id, exc)
        await send_dm_redirect_status(update, context, dm_sent=False)
        return
    await send_dm_redirect_status(update, context, dm_sent=True)


async def invites_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show invite stats for the caller or, for admins, a known username."""
    user_id = await _ensure_user(update)
    if not user_id or not update.effective_message:
        return

    target_id = user_id
    target_label = "Your Invites"
    args = [] if update.callback_query else (getattr(context, "args", None) or [])
    if args:
        if not await is_admin(user_id, bot=context.bot):
            reply = await update.effective_message.reply_text(status_text("error", "Admins only."))
            await schedule_any_delete(reply, context, 30)
            return
        username = args[0]
        if not username.startswith("@"):
            reply = await update.effective_message.reply_text(status_text("error", "Use a Telegram username, like @username."))
            await schedule_any_delete(reply, context, 30)
            return
        user_row = await get_user_by_username(username)
        if not user_row:
            reply = await update.effective_message.reply_text(
                status_text("error", f"{html.escape(username)} is not known to the bot yet.")
            )
            await schedule_any_delete(reply, context, 30)
            return
        target_id = int(user_row["telegram_id"])
        target_label = f"Invites · @{html.escape(str(user_row.get('username') or username).lstrip('@'))}"

    cycle = await get_active_cycle()
    if not cycle:
        reply = await update.effective_message.reply_text(_campaign_missing(), parse_mode="HTML")
        await schedule_delete(reply, context, 60)
        return

    link, error = await _get_or_create_primary_invite_link(update, context, target_id, cycle)
    if error:
        reply = await update.effective_message.reply_text(status_text("error", error))
        await schedule_delete(reply, context, 60)
        return

    stats = await get_invite_stats(target_id, cycle["id"])
    if not stats:
        reply = await update.effective_message.reply_text(status_text("error", "Could not load invite stats."))
        await schedule_delete(reply, context, 60)
        return

    text = "\n".join(
        [
            title(target_label, "👥"),
            "",
            field("Cycle", f"<b>#{cycle.get('cycle_number')}</b>"),
            field("Pending", f"<b>{stats.get('pending', 0)}</b>"),
            field("Confirmed", f"<b>{stats.get('awarded', 0)}</b>"),
            field("Ineligible", f"<b>{stats.get('ineligible', 0)}</b>"),
            field("Invite XP", f"<b>{stats.get('invite_xp', 0):,}</b>"),
            field("Retention", f"<b>{_retention_label()}</b>"),
            "",
            f"<code>{html.escape(link or stats.get('invite_link') or '')}</code>",
        ]
    )
    if _is_community_group_update(update):
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="HTML",
                reply_markup=campaign_home_keyboard(),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except Exception:
            await send_dm_redirect_status(update, context, dm_sent=False)
            return
        await send_dm_redirect_status(update, context, dm_sent=True)
        return

    reply = await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=campaign_home_keyboard(),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    await schedule_delete(reply, context, 60)
    await schedule_command_delete(update, context, 60)


async def xlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start X account linking with a verification code."""
    if not update.effective_message:
        return
    if _is_community_group_update(update):
        handle = context.args[0] if context.args else "@yourhandle"
        await _send_dm_or_group_notice(
            update,
            context,
            f"Start X linking here with:\n<code>/xlink {html.escape(handle)}</code>",
        )
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: <code>/xlink @yourhandle</code>", parse_mode="HTML"
        )
        return
    await _start_xlink_for_handle(update, context, context.args[0])


async def xverify_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verify a user's X account by checking a verification post."""
    if not update.effective_message:
        return
    if _is_community_group_update(update):
        await _send_dm_or_group_notice(
            update,
            context,
            "Verify your X account here with:\n<code>/xverify YOUR_VERIFICATION_POST_URL</code>",
        )
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: <code>/xverify https://x.com/yourhandle/status/...</code>",
            parse_mode="HTML",
        )
        return
    await _verify_x_post_url(update, context, context.args[0])


async def raids_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List active raids."""
    if not update.effective_message:
        return

    raids = await get_active_raids()
    if not raids:
        reply = await update.effective_message.reply_text(status_text("info", "No active raids right now."))
        await schedule_delete(reply, context, 60)
        return

    header = await update.effective_message.reply_text(title("Active HostFi Raids", "🚀"), parse_mode="HTML")
    await schedule_delete(header, context, 60)
    for raid in raids:
        deadline = _parse_iso(raid.get("deadline_at"))
        deadline_text = deadline.strftime("%Y-%m-%d %H:%M UTC") if deadline else "No deadline"
        text = (
            f"<b>Raid #{raid['id']}</b>\n"
            f"{field('Reward', f'<b>{XP_RAID} XP</b>')}\n"
            f"{field('Deadline', html.escape(deadline_text))}\n"
            f"{html.escape(raid.get('target_url') or '')}\n"
            "Use the buttons below to open the post or submit proof."
        )
        reply = await update.effective_message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=campaign_raid_keyboard(raid["id"], raid.get("target_url") or ""),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        await schedule_delete(reply, context, 60)
    await schedule_command_delete(update, context, 60)


async def raid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /raid create and /raid submit."""
    if not update.effective_message or not update.effective_user:
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n<code>/raid create X_POST_URL [deadline_minutes]</code>\n"
            "Use <code>/raid submit RAID_ID YOUR_X_PROOF_URL</code> in DM.",
            parse_mode="HTML",
        )
        return

    subcommand = context.args[0].lower()
    if subcommand == "create":
        await _raid_create(update, context)
    elif subcommand == "submit":
        if _is_community_group_update(update):
            await _send_dm_or_group_notice(
                update,
                context,
                "Submit raid proof in DM with:\n<code>/raid submit RAID_ID YOUR_X_PROOF_URL</code>",
            )
            return
        await _raid_submit(update, context)
    else:
        await update.effective_message.reply_text(status_text("error", "Unknown raid command. Use create or submit."))


async def _raid_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create an approved raid target."""
    if not await is_admin(update.effective_user.id, bot=context.bot):
        await update.effective_message.reply_text(status_text("error", "Admins only."))
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage: <code>/raid create X_POST_URL [deadline_minutes]</code>", parse_mode="HTML"
        )
        return

    cycle = await get_active_cycle()
    if not cycle:
        await update.effective_message.reply_text(_campaign_missing(), parse_mode="HTML")
        return

    parsed = parse_x_post_url(context.args[1])
    if not parsed:
        await update.effective_message.reply_text(status_text("error", "Send a valid X post URL."))
        return

    try:
        deadline_minutes = int(context.args[2]) if len(context.args) > 2 else 60
        if deadline_minutes < 5 or deadline_minutes > 10080:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text(status_text("error", "Deadline must be between 5 and 10080 minutes."))
        return

    raid = await create_raid(
        cycle_id=cycle["id"],
        created_by=update.effective_user.id,
        target_post_id=parsed[1],
        target_url=context.args[1],
        deadline_at=_utc_now() + timedelta(minutes=deadline_minutes),
    )
    if not raid:
        await update.effective_message.reply_text(status_text("error", "Could not create raid."))
        return

    raid_title = title(f"New HostFi Raid #{raid['id']}", "🚀")
    text = (
        f"{raid_title}\n\n"
        f"{html.escape(context.args[1])}\n\n"
        f"{field('Reward', f'<b>{XP_RAID} XP</b>')}\n"
        f"{field('Deadline', f'<b>{deadline_minutes} minutes</b>')}\n\n"
        "Use the buttons below to participate."
    )
    posted = 0
    for chat_id in COMMUNITY_GROUP_IDS:
        try:
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=campaign_raid_keyboard(raid["id"], context.args[1]),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            posted += 1
            await record_raid_message(raid["id"], chat_id, sent.message_id)
        except Exception as exc:
            logger.warning("Could not post raid %s to %s: %s", raid["id"], chat_id, exc)
    reply = await update.effective_message.reply_text(
        status_text("success", f"Raid #{raid['id']} created and posted to {posted} group(s).")
    )
    await schedule_any_delete(reply, context, 30)
    await log_action(
        "raid_created",
        update.effective_user.id,
        metadata={"raid_id": raid["id"], "target_post_id": parsed[1], "deadline_minutes": deadline_minutes},
    )


async def _raid_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Submit a raid proof URL and auto-award XP if strict checks pass."""
    if len(context.args) < 3:
        await update.effective_message.reply_text(
            "Usage in DM: <code>/raid submit RAID_ID YOUR_X_PROOF_URL</code>", parse_mode="HTML"
        )
        return

    try:
        raid_id = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text(status_text("error", "Raid ID must be a number."))
        return
    await _submit_raid_proof_url(update, context, raid_id, context.args[2])


async def xpost_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Submit a personal HostFi-related X post for admin review."""
    if not update.effective_message:
        return
    if _is_community_group_update(update):
        await _send_dm_or_group_notice(
            update,
            context,
            "Submit your X post privately with:\n<code>/xpost YOUR_X_POST_URL</code>",
        )
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: <code>/xpost YOUR_X_POST_URL</code>\n\n"
            "Your post will be sent to admins for review.",
            parse_mode="HTML",
        )
        return
    await _submit_xpost_url(update, context, context.args[0])


# ---------------------------------------------------------------------------
# Admin / superadmin commands
# ---------------------------------------------------------------------------


async def cycle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Superadmin cycle controls."""
    if not update.effective_user or not update.effective_message:
        return
    if not is_admin_channel_chat(update.effective_chat.id if update.effective_chat else None):
        await update.effective_message.reply_text(status_text("error", "Use this in the admin channel."))
        return
    if not await is_superadmin(update.effective_user.id):
        await update.effective_message.reply_text(status_text("error", "Superadmin only."))
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: <code>/cycle start</code> or <code>/cycle finish</code>", parse_mode="HTML")
        return

    action = context.args[0].lower()
    if action == "start":
        cycle = await start_cycle(update.effective_user.id)
        if not cycle:
            await update.effective_message.reply_text(status_text("error", "Could not start cycle."))
            return
        cycle_number = cycle.get("cycle_number")
        await update.effective_message.reply_text(
            title("Campaign Cycle Started", "✅")
            + f"\n\n{field('Cycle', f'<b>#{cycle_number}</b>')}",
            parse_mode="HTML",
        )
        return

    if action == "finish":
        result = await finish_cycle(update.effective_user.id)
        if not result:
            await update.effective_message.reply_text(status_text("error", "No active cycle to finish."))
            return
        winners = result["winners"]
        lines = [
            title(f"Cycle #{result['finished_cycle'].get('cycle_number')} Finished", "🏁"),
            "",
        ]
        rewards = ["$25", "$20", "$15", "$12", "$8"]
        if not winners:
            lines.append("No winners this cycle.")
        else:
            for i, row in enumerate(winners, 1):
                lines.append(
                    f"{i}. {_display_user(row)} — <b>{row['xp']:,} XP</b> ({rewards[i - 1]})"
                )
        new_cycle = result.get("new_cycle")
        if new_cycle:
            lines.append("")
            lines.append(f"New cycle started: <b>#{new_cycle.get('cycle_number')}</b>")

        text = "\n".join(lines)
        await update.effective_message.reply_text(text, parse_mode="HTML")
        try:
            await _send_to_community_groups(context.bot, text=text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Could not announce cycle finish to community: %s", exc)
        return

    await update.effective_message.reply_text(status_text("error", "Unknown cycle action."))


async def award_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to award helpful contribution XP by replying to a message."""
    if not update.effective_user or not update.effective_message:
        return
    if not await is_admin(update.effective_user.id, bot=context.bot):
        await update.effective_message.reply_text(status_text("error", "Admins only."))
        return
    if not context.args or context.args[0].lower() != "helpful":
        await update.effective_message.reply_text(
            "Usage: reply to a helpful message with <code>/award helpful [reason]</code>",
            parse_mode="HTML",
        )
        return
    reply = update.effective_message.reply_to_message
    if not reply or not reply.from_user:
        await update.effective_message.reply_text(status_text("error", "Reply to the helpful message you want to award."))
        return

    await get_or_create_user(reply.from_user.id, reply.from_user.username, reply.from_user.first_name)
    if await is_disqualified(reply.from_user.id):
        await update.effective_message.reply_text(status_text("warning", "That user is disqualified from the current campaign cycle."))
        return
    reason = " ".join(context.args[1:]) or "Helpful community contribution"
    event = await award_xp(
        reply.from_user.id,
        XP_HELPFUL,
        "helpful_contribution",
        reason,
        metadata={"message_id": reply.message_id, "chat_id": reply.chat_id},
        actor_telegram_id=update.effective_user.id,
    )
    if not event:
        await update.effective_message.reply_text(_campaign_missing(), parse_mode="HTML")
        return

    await update.effective_message.reply_text(
        title("Helpful XP Awarded", "✅")
        + f"\n\n{field('User', html.escape(reply.from_user.first_name or str(reply.from_user.id)))}"
        + f"\n{field('XP', f'<b>{XP_HELPFUL}</b>')}",
        parse_mode="HTML",
    )


async def xp_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Superadmin manual XP add/deduct/disqualify controls."""
    if not update.effective_user or not update.effective_message:
        return
    if not await is_superadmin(update.effective_user.id):
        await update.effective_message.reply_text(status_text("error", "Superadmin only."))
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    in_admin_channel = is_admin_channel_chat(chat_id)
    in_community_group = _is_community_group_update(update)
    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Usage:\n"
            "Reply: <code>/xp add 100</code> or <code>/xp deduct 50</code>\n"
            "<code>/xp add @username AMOUNT</code>\n"
            "<code>/xp deduct @username AMOUNT</code>\n"
            "<code>/xp disqualify @username reason</code>",
            parse_mode="HTML",
        )
        return

    action = args[0].lower()
    if action in ("add", "deduct"):
        reply_target = update.effective_message.reply_to_message
        if len(args) == 2:
            if not in_community_group:
                await update.effective_message.reply_text(
                    status_text("error", "Reply-based XP shortcuts can only be used in the community group.")
                )
                return
            if not reply_target or not reply_target.from_user:
                await update.effective_message.reply_text(
                    status_text("error", "Reply to the user message you want to adjust.")
                )
                return
            if reply_target.from_user.is_bot:
                await update.effective_message.reply_text(status_text("error", "Cannot adjust XP for bot accounts."))
                return
            await get_or_create_user(
                reply_target.from_user.id,
                reply_target.from_user.username,
                reply_target.from_user.first_name,
            )
            target_id = int(reply_target.from_user.id)
            target_display = _profile_link(
                target_id,
                reply_target.from_user.username,
                reply_target.from_user.first_name,
            )
            amount_raw = args[1]
        elif len(args) == 3:
            if not in_admin_channel:
                await update.effective_message.reply_text(status_text("error", "Use direct username XP commands in the admin channel."))
                return
            username = args[1]
            if not username.startswith("@"):
                await update.effective_message.reply_text(
                    status_text("error", "Use a Telegram username, like @username.")
                )
                return
            user_row = await get_user_by_username(username)
            if not user_row:
                await update.effective_message.reply_text(
                    status_text("error", f"{username} is not known to the bot yet. The user must join or message the bot first.")
                )
                return
            target_id = int(user_row["telegram_id"])
            target_display = f"<b>@{html.escape(str(user_row.get('username') or username).lstrip('@'))}</b>"
            amount_raw = args[2]
        else:
            await update.effective_message.reply_text(
                "Usage: reply with <code>/xp add 100</code>, or use "
                "<code>/xp add @username 100</code> in the admin channel.",
                parse_mode="HTML",
            )
            return
        try:
            amount = int(amount_raw)
        except ValueError:
            await update.effective_message.reply_text(status_text("error", "AMOUNT must be a number."))
            return
        if amount <= 0:
            await update.effective_message.reply_text(status_text("error", "AMOUNT must be positive."))
            return
        signed_amount = amount if action == "add" else -amount
        reason = f"Manual XP {action} by superadmin"
        event_type = "manual_add" if action == "add" else "manual_deduct"
    elif action == "disqualify":
        if not in_admin_channel:
            await update.effective_message.reply_text(status_text("error", "Use disqualification in the admin channel."))
            return
        if len(args) < 3:
            await update.effective_message.reply_text(
                "Usage: <code>/xp disqualify @username reason</code>",
                parse_mode="HTML",
            )
            return
        username = args[1]
        if not username.startswith("@"):
            await update.effective_message.reply_text(
                status_text("error", "Use a Telegram username, like @username.")
            )
            return
        user_row = await get_user_by_username(username)
        if not user_row:
            await update.effective_message.reply_text(
                status_text("error", f"{username} is not known to the bot yet. The user must join or message the bot first.")
            )
            return
        target_id = int(user_row["telegram_id"])
        await get_or_create_user(target_id)
        target_display = f"<b>@{html.escape(str(user_row.get('username') or username).lstrip('@'))}</b>"
        rank_xp, _, _, _ = await get_campaign_rank(target_id)
        signed_amount = -rank_xp
        reason = " ".join(args[2:]) or "Disqualified by superadmin"
        event_type = "disqualification"
    else:
        await update.effective_message.reply_text(status_text("error", "Unknown XP action."))
        return

    event = await award_xp(
        target_id,
        signed_amount,
        event_type,
        reason,
        actor_telegram_id=update.effective_user.id,
    )
    if not event:
        await update.effective_message.reply_text(_campaign_missing(), parse_mode="HTML")
        return

    visible_xp = event.get("new_total", 0)
    reply = await update.effective_message.reply_text(
        title("XP Updated", "✅")
        + f"\n\n{field('User', target_display)}"
        + f"\n{field('Change', f'<b>{signed_amount:+,}</b>')}"
        + f"\n{field('Visible XP', f'<b>{visible_xp:,}</b>')}",
        parse_mode="HTML",
    )
    await schedule_any_delete(reply, context, 30)
    if in_community_group:
        await schedule_command_delete(update, context, 30)
    await log_action(
        f"xp_{event_type}",
        update.effective_user.id,
        target_telegram_id=target_id,
        reason=reason,
        metadata={"amount": signed_amount},
    )


# ---------------------------------------------------------------------------
# Callbacks and scheduler helpers
# ---------------------------------------------------------------------------


async def xpost_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin approve/reject buttons for personal X post submissions."""
    query = update.callback_query
    if not query or not query.data or not query.message:
        return

    if not await is_admin(query.from_user.id, bot=context.bot):
        await query.answer("Admins only.", show_alert=True)
        return
    if not is_admin_channel_chat(query.message.chat_id):
        await query.answer("Use this in the admin channel.", show_alert=True)
        return

    try:
        _, action, raw_id = query.data.split("_", 2)
        submission_id = int(raw_id)
    except ValueError:
        await query.answer("Invalid review action.", show_alert=True)
        return

    submission = await get_x_post_submission(submission_id)
    if not submission:
        await query.answer("Submission not found.", show_alert=True)
        return
    if submission.get("status") != "pending":
        await query.answer("This submission has already been reviewed.", show_alert=True)
        return

    if action == "approve":
        _set_pending(
            context,
            {
                "type": "xpost_award_xp",
                "submission_id": submission_id,
                "chat_id": query.message.chat_id,
                "review_message_id": query.message.message_id,
            },
        )
        await query.answer("Send the XP amount.")
        await query.message.reply_text(
            title("Approve X Post", "✅")
            + f"\n\nHow many XP should submission <b>#{submission_id}</b> receive?",
            parse_mode="HTML",
        )
        return

    if action == "reject":
        reviewed = await mark_x_post_submission_reviewed(
            submission_id,
            "rejected",
            query.from_user.id,
        )
        if not reviewed:
            await query.answer("Could not reject submission.", show_alert=True)
            return

        user_link, linked_handle, url_handle, url_handle_matches = await _xpost_review_context(reviewed)
        reviewer = _profile_link(query.from_user.id, query.from_user.username, query.from_user.first_name)
        await query.edit_message_text(
            _xpost_review_text(
                reviewed,
                user_link=user_link,
                linked_handle=linked_handle,
                url_handle=url_handle,
                url_handle_matches=url_handle_matches,
                status="Rejected",
                reviewed_by=reviewer,
            ),
            parse_mode="HTML",
            reply_markup=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        await query.answer("Submission rejected.")
        try:
            await context.bot.send_message(
                int(reviewed["telegram_id"]),
                title("X Post Not Approved", "⚠️")
                + "\n\nYour submitted X post was reviewed and not approved.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        await log_action(
            "xpost_rejected",
            query.from_user.id,
            target_telegram_id=int(reviewed["telegram_id"]),
            metadata={"submission_id": submission_id},
        )
        return

    await query.answer("Unknown review action.", show_alert=True)


async def _handle_xpost_award_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pending: dict,
    text: str,
) -> bool:
    """Award admin-selected XP for a pending X post review."""
    if not update.effective_user or not update.effective_message:
        return False
    if not await is_admin(update.effective_user.id, bot=context.bot):
        await update.effective_message.reply_text(status_text("error", "Admins only."))
        return False

    try:
        amount = int(text)
    except ValueError:
        await update.effective_message.reply_text(status_text("error", "Send a positive XP amount."))
        return False
    if amount <= 0:
        await update.effective_message.reply_text(status_text("error", "XP amount must be positive."))
        return False

    submission_id = int(pending["submission_id"])
    submission = await get_x_post_submission(submission_id)
    if not submission or submission.get("status") != "pending":
        await update.effective_message.reply_text(status_text("warning", "This X post submission is no longer pending."))
        return True

    event = await award_xp(
        int(submission["telegram_id"]),
        amount,
        "x_post",
        "Admin-approved personal HostFi X post",
        evidence_url=submission.get("proof_url"),
        external_id=submission.get("x_post_id"),
        metadata={"submission_id": submission_id, "submission_date": submission.get("submission_date")},
        actor_telegram_id=update.effective_user.id,
        cycle_id=submission["cycle_id"],
    )
    if not event:
        await update.effective_message.reply_text(status_text("error", "Could not award XP for this submission."))
        return False

    reviewed = await mark_x_post_submission_reviewed(
        submission_id,
        "approved",
        update.effective_user.id,
        xp_awarded=amount,
    )
    if not reviewed:
        await update.effective_message.reply_text(status_text("error", "XP was awarded, but review status could not be saved."))
        return True

    user_link, linked_handle, url_handle, url_handle_matches = await _xpost_review_context(reviewed)
    reviewer = _profile_link(update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    try:
        await context.bot.edit_message_text(
            chat_id=int(pending["chat_id"]),
            message_id=int(pending["review_message_id"]),
            text=_xpost_review_text(
                reviewed,
                user_link=user_link,
                linked_handle=linked_handle,
                url_handle=url_handle,
                url_handle_matches=url_handle_matches,
                status="Approved",
                reviewed_by=reviewer,
                xp_awarded=amount,
            ),
            parse_mode="HTML",
            reply_markup=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as exc:
        logger.warning("Could not update X post review card %s: %s", submission_id, exc)

    await update.effective_message.reply_text(
        status_text("success", f"Approved submission #{submission_id} for {amount:,} XP."),
        parse_mode="HTML",
    )
    try:
        await context.bot.send_message(
            int(reviewed["telegram_id"]),
            title("X Post Approved", "✅")
            + f"\n\n{field('XP earned', f'<b>{amount:,}</b>')}",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await log_action(
        "xpost_approved",
        update.effective_user.id,
        target_telegram_id=int(reviewed["telegram_id"]),
        metadata={"submission_id": submission_id, "amount": amount},
    )
    return True


async def campaign_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle campaign inline button actions."""
    query = update.callback_query
    if not query or not query.data or not query.message:
        return

    await query.answer()
    data = query.data

    if data == "campaign_cancel":
        _clear_pending(context)
        reply = await query.message.reply_text("Cancelled.", reply_markup=campaign_home_keyboard())
        await schedule_delete(reply, context, 60)
        return

    if data == "campaign_home":
        await _send_campaign_home(query.message, context)
        return

    if data == "campaign_dm_dashboard":
        try:
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text=_user_dashboard_text(),
                parse_mode="HTML",
                reply_markup=campaign_home_keyboard(),
            )
        except Exception:
            await send_dm_redirect_status(update, context, dm_sent=False)
            return
        await send_dm_redirect_status(update, context, dm_sent=True)
        return

    if data == "campaign_earn":
        reply = await query.message.reply_text(
            _earn_xp_text(),
            parse_mode="HTML",
            reply_markup=campaign_earn_keyboard(),
        )
        await schedule_delete(reply, context, 60)
        return

    if data == "campaign_rules":
        reply = await query.message.reply_text(format_rules(), parse_mode="HTML")
        await schedule_delete(reply, context, 90)
        return

    if data == "campaign_support":
        prompt = (
            f"{title('HOSTFI Support', '🎫')}\n\n"
            "Please briefly describe your issue in a single message.\n\n"
            "<i>Be specific to help our team resolve it faster.</i>\n\n"
            "Send <code>/cancel</code> to abort."
        )
        from bot.handlers.tickets import SUPPORT_PENDING_DM_KEY

        if query.message.chat.type in ("group", "supergroup"):
            try:
                await context.bot.send_message(
                    chat_id=query.from_user.id,
                    text=prompt,
                    parse_mode="HTML",
                )
                context.user_data[SUPPORT_PENDING_DM_KEY] = True
            except Exception:
                await send_dm_redirect_status(update, context, dm_sent=False)
                return
            await send_dm_redirect_status(update, context, dm_sent=True)
        else:
            context.user_data[SUPPORT_PENDING_DM_KEY] = True
            reply = await query.message.reply_text(prompt, parse_mode="HTML")
            await schedule_delete(reply, context, 60)
        return

    if data == "campaign_xp":
        await get_or_create_user(query.from_user.id, query.from_user.username, query.from_user.first_name)
        await _send_xp_status(query.message, query.from_user.id, context)
        return

    if data == "campaign_leaderboard":
        await _send_campaign_leaderboard(query.message, context)
        return

    if data == "campaign_invite":
        await invite_command(update, context)
        return

    if data == "campaign_invites":
        await invites_command(update, context)
        return

    if data == "campaign_raids":
        await raids_command(update, context)
        return

    if data == "campaign_xlink_start":
        prompt = "Send your X handle now, like <code>@yourhandle</code>."
        if query.message.chat.type in ("group", "supergroup") and is_community_group_chat(query.message.chat_id):
            _set_pending(context, {"type": "xlink_handle", "chat_id": query.from_user.id})
            try:
                await context.bot.send_message(
                    query.from_user.id,
                    prompt,
                    parse_mode="HTML",
                    reply_markup=campaign_cancel_keyboard(),
                )
            except Exception:
                _clear_pending(context)
                await send_dm_redirect_status(update, context, dm_sent=False)
                return
            await send_dm_redirect_status(update, context, dm_sent=True)
        else:
            _set_pending(context, {"type": "xlink_handle", "chat_id": query.message.chat_id})
            reply = await query.message.reply_text(
                prompt,
                parse_mode="HTML",
                reply_markup=campaign_cancel_keyboard(),
            )
            await schedule_delete(reply, context, 60)
        return

    if data == "campaign_xverify_start":
        prompt = "Send the X post URL that contains your verification code."
        if query.message.chat.type in ("group", "supergroup") and is_community_group_chat(query.message.chat_id):
            _set_pending(context, {"type": "xverify", "chat_id": query.from_user.id})
            try:
                await context.bot.send_message(
                    query.from_user.id,
                    prompt,
                    reply_markup=campaign_cancel_keyboard(),
                )
            except Exception:
                _clear_pending(context)
                await send_dm_redirect_status(update, context, dm_sent=False)
                return
            await send_dm_redirect_status(update, context, dm_sent=True)
        else:
            _set_pending(context, {"type": "xverify", "chat_id": query.message.chat_id})
            reply = await query.message.reply_text(
                prompt,
                reply_markup=campaign_cancel_keyboard(),
            )
            await schedule_delete(reply, context, 60)
        return

    if data == "campaign_xpost_start":
        prompt = "Send your HostFi-related X post URL. Admins will review it before XP is awarded."
        if query.message.chat.type in ("group", "supergroup") and is_community_group_chat(query.message.chat_id):
            _set_pending(context, {"type": "xpost", "chat_id": query.from_user.id})
            try:
                await context.bot.send_message(
                    query.from_user.id,
                    prompt,
                    reply_markup=campaign_cancel_keyboard(),
                )
            except Exception:
                _clear_pending(context)
                await send_dm_redirect_status(update, context, dm_sent=False)
                return
            await send_dm_redirect_status(update, context, dm_sent=True)
        else:
            _set_pending(context, {"type": "xpost", "chat_id": query.message.chat_id})
            reply = await query.message.reply_text(
                prompt,
                reply_markup=campaign_cancel_keyboard(),
            )
            await schedule_delete(reply, context, 60)
        return

    if data.startswith("campaign_raid_submit_"):
        try:
            raid_id = int(data.rsplit("_", 1)[-1])
        except ValueError:
            await query.message.reply_text(status_text("error", "Invalid raid button."))
            return
        prompt = f"Send your X proof URL for <b>Raid #{raid_id}</b>."
        if query.message.chat.type in ("group", "supergroup") and is_community_group_chat(query.message.chat_id):
            _set_pending(context, {"type": "raid_proof", "raid_id": raid_id, "chat_id": query.from_user.id})
            try:
                await context.bot.send_message(
                    query.from_user.id,
                    prompt,
                    parse_mode="HTML",
                    reply_markup=campaign_cancel_keyboard(),
                )
            except Exception:
                _clear_pending(context)
                await send_dm_redirect_status(update, context, dm_sent=False)
                return
            await send_dm_redirect_status(update, context, dm_sent=True)
        else:
            _set_pending(context, {"type": "raid_proof", "raid_id": raid_id, "chat_id": query.message.chat_id})
            reply = await query.message.reply_text(
                prompt,
                parse_mode="HTML",
                reply_markup=campaign_cancel_keyboard(),
            )
            await schedule_delete(reply, context, 60)
        return

    if data.startswith("campaign_raid_help_"):
        raid_id = html.escape(data.rsplit("_", 1)[-1])
        reply = await query.message.reply_text(
            f"To earn raid XP:\n"
            f"1. Open the approved X post.\n"
            f"2. Reply or quote it from your linked X account.\n"
            f"3. Tap <b>Submit Proof</b>, then send your proof URL in DM.\n\n"
            f"Fallback command in DM:\n<code>/raid submit {raid_id} YOUR_X_PROOF_URL</code>",
            parse_mode="HTML",
        )
        await schedule_delete(reply, context, 60)


async def raid_submit_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show raid submission instructions for inline buttons."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    raid_id = query.data.rsplit("_", 1)[-1]
    await query.message.reply_text(
        f"Submit proof in DM with:\n<code>/raid submit {html.escape(raid_id)} YOUR_X_PROOF_URL</code>",
        parse_mode="HTML",
    )


async def campaign_guided_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Consume the next message for a pending campaign button flow."""
    pending = context.user_data.get(PENDING_ACTION_KEY)
    if not pending:
        return
    if not update.effective_message or not update.effective_message.text:
        return

    text = update.effective_message.text.strip()
    action_type = pending.get("type")
    expected_chat_id = pending.get("chat_id")
    source_chat = update.effective_chat
    if expected_chat_id and source_chat and source_chat.id != expected_chat_id:
        if (
            action_type in {"raid_proof", "xpost", "xverify", "xlink_handle"}
            and source_chat.type in ("group", "supergroup")
            and is_community_group_chat(source_chat.id)
        ):
            user_id = update.effective_user.id if update.effective_user else None
            logger.info(
                "Campaign pending input sent in wrong chat action=%s telegram_id=%s source_chat_id=%s expected_chat_id=%s",
                action_type,
                user_id,
                source_chat.id,
                expected_chat_id,
            )
            notice = await update.effective_message.reply_text(
                "This step happens in DM. Check your private chat with the bot."
            )
            await schedule_any_delete(notice, context, 30)
            try:
                await update.effective_message.delete()
            except Exception as exc:
                logger.warning(
                    "Could not delete wrong-chat campaign input action=%s telegram_id=%s chat_id=%s: %s",
                    action_type,
                    user_id,
                    source_chat.id,
                    exc,
                )
            raise ApplicationHandlerStop
        return

    success = False

    if action_type == "xlink_handle":
        success = await _start_xlink_for_handle(update, context, text)
    elif action_type == "xverify":
        success = await _verify_x_post_url(update, context, text)
    elif action_type == "xpost":
        success = await _submit_xpost_url(update, context, text)
    elif action_type == "xpost_award_xp":
        success = await _handle_xpost_award_amount(update, context, pending, text)
    elif action_type == "raid_proof":
        success = await _submit_raid_proof_url(update, context, int(pending.get("raid_id")), text)
    else:
        await update.effective_message.reply_text(status_text("error", "Unknown campaign action. Cancelled."))
        success = True

    if success:
        _clear_pending(context)
    else:
        await update.effective_message.reply_text(
            "Send another value to retry, or tap Cancel.",
            reply_markup=campaign_cancel_keyboard(),
        )

    raise ApplicationHandlerStop


async def process_invite_awards(bot) -> int:
    """Award invite XP for users who stayed in the group for the retention window."""
    pending = await get_pending_invite_joins()
    active_cycle = await get_active_cycle()
    awarded = 0
    for join in pending:
        invitee = int(join["invitee_telegram_id"])
        inviter = int(join["inviter_telegram_id"])
        if not active_cycle or int(active_cycle["id"]) != int(join["cycle_id"]):
            await mark_invite_join(join["id"], "ineligible")
            continue
        if await is_disqualified(inviter, int(join["cycle_id"])):
            await mark_invite_join(join["id"], "ineligible")
            continue
        try:
            member = await bot.get_chat_member(int(join.get("chat_id") or get_invite_target_group_id()), invitee)
            if member.status in ("left", "kicked"):
                await mark_invite_join(join["id"], "ineligible")
                continue
        except Exception as exc:
            logger.warning("Invite retention check failed for %s: %s", invitee, exc)
            continue

        event = await award_xp(
            inviter,
            XP_INVITE,
            "telegram_invite",
            f"Invitee stayed in Telegram group for {_retention_label()}",
            external_id=str(invitee),
            metadata={"invitee_telegram_id": invitee, "join_id": join["id"]},
            cycle_id=join["cycle_id"],
        )
        if event:
            await mark_invite_join(join["id"], "awarded", awarded=True)
            awarded += 1
            try:
                await bot.send_message(
                    inviter,
                    title("Invite XP Awarded", "✅") + f"\n\n{field('XP earned', f'<b>{XP_INVITE}</b>')}",
                    parse_mode="HTML",
                )
            except Exception:
                pass
    return awarded


async def process_expired_raids(bot) -> int:
    """Close expired raids and delete their stored announcement messages."""
    expired = await get_expired_active_raids()
    closed = 0
    for raid in expired:
        raid_id = int(raid["id"])
        await close_raid(raid_id)
        closed += 1
        for message in await get_raid_messages(raid_id):
            try:
                await bot.delete_message(
                    chat_id=int(message["chat_id"]),
                    message_id=int(message["message_id"]),
                )
            except Exception as exc:
                logger.info(
                    "Could not delete expired raid message raid=%s chat=%s message=%s: %s",
                    raid_id,
                    message.get("chat_id"),
                    message.get("message_id"),
                    exc,
                )
    return closed


async def record_new_member_invite(update: Update) -> None:
    """Record campaign invite attribution from a new member service message."""
    if not update.message or not update.message.new_chat_members:
        return
    invite = getattr(update.message, "invite_link", None)
    invite_link = getattr(invite, "invite_link", None)
    if not invite_link:
        return
    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        await record_invite_join(invite_link, member.id)
