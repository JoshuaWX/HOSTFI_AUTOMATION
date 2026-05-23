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

from bot.utils.formatter import bullet, field, status_text, title
from bot.utils.keyboards import (
    campaign_cancel_keyboard,
    campaign_home_keyboard,
    campaign_raid_keyboard,
    campaign_xverify_keyboard,
)
from bot.utils.permissions import is_admin, is_admin_channel_chat, is_superadmin
from bot.utils.rate_limiter import check_rate_limit
from bot.utils.x_api import (
    XApiNotConfigured,
    fetch_post,
    is_meaningful_x_text,
    is_reply_or_quote_to,
    is_x_api_configured,
    mentions_hostfi,
    parse_x_post_url,
)
from config import COMMUNITY_GROUP_IDS, get_primary_community_group_id
from database.campaign import (
    XP_HELPFUL,
    XP_INVITE,
    XP_RAID,
    XP_X_POST,
    award_xp,
    create_raid,
    create_x_verification,
    finish_cycle,
    get_active_cycle,
    get_active_raids,
    get_campaign_leaderboard,
    get_campaign_rank,
    get_or_create_invite_link_record,
    get_pending_invite_joins,
    get_raid,
    get_x_account,
    has_daily_x_post,
    has_raid_submission,
    is_disqualified,
    mark_invite_join,
    record_invite_join,
    record_raid_submission,
    record_x_post_submission,
    start_cycle,
    verify_x_account,
)
from database.logs import log_action
from database.users import get_or_create_user

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
    """Return the current configured group, or the primary group for DM/admin flows."""
    chat_id = update.effective_chat.id if update.effective_chat else None
    return get_primary_community_group_id(chat_id)


async def _send_to_community_groups(bot, **kwargs) -> int:
    """Send one message to every configured community group."""
    sent = 0
    for chat_id in COMMUNITY_GROUP_IDS:
        await bot.send_message(chat_id=chat_id, **kwargs)
        sent += 1
    return sent


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
        + bullet(f"Telegram invites after 48h — <b>{XP_INVITE} XP</b>")
        + "\n"
        + bullet(f"Helpful contributions — <b>{XP_HELPFUL} XP</b>")
        + "\n"
        + bullet(f"HostFi X posts — <b>{XP_X_POST} XP</b> once daily")
        + "\n\nUse the buttons below to manage your campaign activity."
    )


async def _send_campaign_home(message: Message) -> None:
    """Send the campaign hub to a Telegram message target."""
    cycle = await get_active_cycle()
    if not cycle:
        await message.reply_text(_campaign_missing(), parse_mode="HTML")
        return
    await message.reply_text(
        _campaign_home_text(cycle),
        parse_mode="HTML",
        reply_markup=campaign_home_keyboard(),
    )


async def _send_xp_status(message: Message, user_id: int) -> None:
    """Send campaign XP status for a user."""
    xp, rank, total, cycle = await get_campaign_rank(user_id)
    if not cycle:
        await message.reply_text(_campaign_missing(), parse_mode="HTML")
        return

    rank_text = f"#{rank} of {total}" if rank else "Not ranked yet"
    await message.reply_text(
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


async def _send_campaign_leaderboard(message: Message) -> None:
    """Send the current campaign leaderboard."""
    top_users = await get_campaign_leaderboard(10)
    if not top_users:
        await message.reply_text(
            status_text("info", "No campaign leaderboard data yet."),
            reply_markup=campaign_home_keyboard(),
        )
        return

    lines = [title("XP Leaderboard", "🏅"), ""]
    for index, row in enumerate(top_users, 1):
        lines.append(f"{index}. <b>{_display_user(row)}</b> — {row.get('xp', 0):,} XP")
    lines.append("")
    lines.append("Ties are ranked by earliest approved XP event.")
    await message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=campaign_home_keyboard(),
    )


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
        return False
    if not is_x_api_configured():
        await update.effective_message.reply_text(_x_missing(), parse_mode="HTML")
        return False

    raid = await get_raid(raid_id)
    if not raid or raid.get("status") != "active":
        await update.effective_message.reply_text(status_text("error", "Raid not found or no longer active."))
        return False

    cycle = await get_active_cycle()
    if not cycle or int(cycle["id"]) != int(raid["cycle_id"]):
        await update.effective_message.reply_text(status_text("error", "This raid belongs to a finished campaign cycle."))
        return False
    if await is_disqualified(user_id, int(raid["cycle_id"])):
        await update.effective_message.reply_text(status_text("warning", "You are disqualified from the current campaign cycle."))
        return False

    deadline = _parse_iso(raid.get("deadline_at"))
    if deadline and _utc_now() > deadline:
        await update.effective_message.reply_text(status_text("warning", "This raid has expired."))
        return False
    if await has_raid_submission(raid_id, user_id):
        await update.effective_message.reply_text(status_text("warning", "You already submitted proof for this raid."))
        return False

    account = await get_x_account(user_id)
    if not account or account.get("status") != "verified":
        await update.effective_message.reply_text(
            status_text("warning", "Link your X account first."),
            parse_mode="HTML",
            reply_markup=campaign_home_keyboard(),
        )
        return False

    parsed = parse_x_post_url(proof_url)
    if not parsed:
        await update.effective_message.reply_text(
            status_text("error", "Send a valid X proof URL."),
            reply_markup=campaign_cancel_keyboard(),
        )
        return False

    try:
        post = await fetch_post(parsed[1], proof_url)
    except Exception as exc:
        logger.error("Raid proof fetch failed: %s", exc)
        await update.effective_message.reply_text(status_text("error", "Could not verify that X proof."))
        return False

    if post.author_id != str(account.get("x_user_id")):
        await update.effective_message.reply_text(status_text("error", "Proof must come from your linked X account."))
        return False
    if not is_reply_or_quote_to(post, str(raid["target_post_id"]), str(raid["target_url"])):
        await update.effective_message.reply_text(status_text("error", "Proof must reply to or quote the approved raid post."))
        return False
    if not is_meaningful_x_text(post.text):
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
        await update.effective_message.reply_text(status_text("error", "Could not award XP. Check that the campaign is active."))
        return False

    await update.effective_message.reply_text(
        title("Raid Approved", "✅") + f"\n\n{field('XP earned', f'<b>{XP_RAID}</b>')}",
        parse_mode="HTML",
        reply_markup=campaign_home_keyboard(),
    )
    return True


async def _submit_xpost_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> bool:
    """Submit a personal HostFi-related X post for automatic XP."""
    user_id = await _ensure_user(update)
    if not user_id or not update.effective_message:
        return False
    if not is_x_api_configured():
        await update.effective_message.reply_text(_x_missing(), parse_mode="HTML")
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
        await update.effective_message.reply_text(status_text("warning", "You already earned personal X post XP today."))
        return False

    try:
        post = await fetch_post(parsed[1], url)
    except Exception as exc:
        logger.error("X post fetch failed: %s", exc)
        await update.effective_message.reply_text(status_text("error", "Could not verify that X post."))
        return False

    if post.author_id != str(account.get("x_user_id")):
        await update.effective_message.reply_text(status_text("error", "Post must come from your linked X account."))
        return False
    if not mentions_hostfi(post.text) or not is_meaningful_x_text(post.text):
        await update.effective_message.reply_text(status_text("error", "Post must be meaningful and clearly about HostFi."))
        return False
    if post.created_at:
        cycle_start = _parse_iso(cycle.get("start_at"))
        if cycle_start and post.created_at < cycle_start:
            await update.effective_message.reply_text(status_text("error", "This post was made before the current campaign cycle."))
            return False

    submission = await record_x_post_submission(
        cycle["id"],
        user_id,
        post.post_id,
        url,
        day,
        "approved",
        {"text": post.text[:500]},
    )
    if not submission:
        await update.effective_message.reply_text(status_text("error", "Could not record this X post."))
        return False

    event = await award_xp(
        user_id,
        XP_X_POST,
        "x_post",
        "Approved personal HostFi X post",
        evidence_url=url,
        external_id=post.post_id,
        metadata={"day": day},
    )
    if not event:
        await update.effective_message.reply_text(status_text("error", "Could not award XP."))
        return False

    await update.effective_message.reply_text(
        title("X Post Approved", "✅") + f"\n\n{field('XP earned', f'<b>{XP_X_POST}</b>')}",
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
    await _send_campaign_home(update.effective_message)


async def xp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the caller's current campaign XP."""
    user_id = await _ensure_user(update)
    if not user_id or not update.effective_message:
        return

    await _send_xp_status(update.effective_message, user_id)


async def xp_router_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route /xp between user profile view and superadmin adjustment commands."""
    if context.args and context.args[0].lower() in ("add", "deduct", "disqualify"):
        await xp_admin_command(update, context)
        return
    await xp_command(update, context)


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

    target_chat_id = _target_community_group_id(update)
    if not target_chat_id:
        await update.effective_message.reply_text(status_text("error", "No community group is configured."))
        return

    existing = await get_or_create_invite_link_record(cycle["id"], user_id, chat_id=target_chat_id)
    if existing and existing.get("invite_link"):
        link = existing["invite_link"]
    else:
        try:
            invite = await context.bot.create_chat_invite_link(
                chat_id=target_chat_id,
                name=f"xp-{cycle['id']}-{user_id}",
                creates_join_request=False,
            )
        except Exception as exc:
            logger.error("Failed to create campaign invite link: %s", exc)
            await update.effective_message.reply_text(
                status_text("error", "Could not create your invite link. Make sure the bot can invite users.")
            )
            return
        record = await get_or_create_invite_link_record(
            cycle["id"],
            user_id,
            invite.invite_link,
            chat_id=target_chat_id,
        )
        link = record["invite_link"] if record else invite.invite_link

    await update.effective_message.reply_text(
        f"{title('Campaign Invite Link', '👥')}\n\n"
        f"<code>{html.escape(link)}</code>\n\n"
        f"{field('Reward', f'<b>{XP_INVITE} XP</b>')}\n"
        "XP is awarded after the invited user stays in the group for 48 hours.",
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def xlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start X account linking with a verification code."""
    if not update.effective_message:
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
        await update.effective_message.reply_text(status_text("info", "No active raids right now."))
        return

    await update.effective_message.reply_text(title("Active HostFi Raids", "🚀"), parse_mode="HTML")
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
        await update.effective_message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=campaign_raid_keyboard(raid["id"], raid.get("target_url") or ""),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )


async def raid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /raid create and /raid submit."""
    if not update.effective_message or not update.effective_user:
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n<code>/raid create X_POST_URL [deadline_hours]</code>\n"
            "<code>/raid submit RAID_ID YOUR_X_PROOF_URL</code>",
            parse_mode="HTML",
        )
        return

    subcommand = context.args[0].lower()
    if subcommand == "create":
        await _raid_create(update, context)
    elif subcommand == "submit":
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
            "Usage: <code>/raid create X_POST_URL [deadline_hours]</code>", parse_mode="HTML"
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
        deadline_hours = int(context.args[2]) if len(context.args) > 2 else 48
        if deadline_hours < 1 or deadline_hours > 336:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text(status_text("error", "Deadline must be between 1 and 336 hours."))
        return

    raid = await create_raid(
        cycle_id=cycle["id"],
        created_by=update.effective_user.id,
        target_post_id=parsed[1],
        target_url=context.args[1],
        deadline_at=_utc_now() + timedelta(hours=deadline_hours),
    )
    if not raid:
        await update.effective_message.reply_text(status_text("error", "Could not create raid."))
        return

    raid_title = title(f"New HostFi Raid #{raid['id']}", "🚀")
    text = (
        f"{raid_title}\n\n"
        f"{html.escape(context.args[1])}\n\n"
        f"{field('Reward', f'<b>{XP_RAID} XP</b>')}\n"
        f"{field('Deadline', f'<b>{deadline_hours} hours</b>')}\n\n"
        "Use the buttons below to participate."
    )
    await _send_to_community_groups(
        context.bot,
        text=text,
        parse_mode="HTML",
        reply_markup=campaign_raid_keyboard(raid["id"], context.args[1]),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    await update.effective_message.reply_text(status_text("success", f"Raid #{raid['id']} created and posted."))
    await log_action(
        "raid_created",
        update.effective_user.id,
        metadata={"raid_id": raid["id"], "target_post_id": parsed[1]},
    )


async def _raid_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Submit a raid proof URL and auto-award XP if strict checks pass."""
    if len(context.args) < 3:
        await update.effective_message.reply_text(
            "Usage: <code>/raid submit RAID_ID YOUR_X_PROOF_URL</code>", parse_mode="HTML"
        )
        return

    try:
        raid_id = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text(status_text("error", "Raid ID must be a number."))
        return
    await _submit_raid_proof_url(update, context, raid_id, context.args[2])


async def xpost_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Submit a personal HostFi-related X post for automatic XP."""
    if not update.effective_message:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: <code>/xpost YOUR_X_POST_URL</code>", parse_mode="HTML")
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
    if not is_admin_channel_chat(update.effective_chat.id if update.effective_chat else None):
        await update.effective_message.reply_text(status_text("error", "Use this in the admin channel."))
        return
    if not await is_superadmin(update.effective_user.id):
        await update.effective_message.reply_text(status_text("error", "Superadmin only."))
        return
    if len(context.args) < 3:
        await update.effective_message.reply_text(
            "Usage:\n"
            "<code>/xp add USER_ID AMOUNT reason</code>\n"
            "<code>/xp deduct USER_ID AMOUNT reason</code>\n"
            "<code>/xp disqualify USER_ID reason</code>",
            parse_mode="HTML",
        )
        return

    action = context.args[0].lower()
    try:
        target_id = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text(status_text("error", "USER_ID must be a number."))
        return
    await get_or_create_user(target_id)

    if action in ("add", "deduct"):
        try:
            amount = int(context.args[2])
        except ValueError:
            await update.effective_message.reply_text(status_text("error", "AMOUNT must be a number."))
            return
        if amount <= 0:
            await update.effective_message.reply_text(status_text("error", "AMOUNT must be positive."))
            return
        signed_amount = amount if action == "add" else -amount
        reason = " ".join(context.args[3:]) or f"Manual XP {action}"
        event_type = "manual_add" if action == "add" else "manual_deduct"
    elif action == "disqualify":
        rank_xp, _, _, _ = await get_campaign_rank(target_id)
        signed_amount = -rank_xp
        reason = " ".join(context.args[2:]) or "Disqualified by superadmin"
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
    await update.effective_message.reply_text(
        title("XP Updated", "✅")
        + f"\n\n{field('User', f'<code>{target_id}</code>')}"
        + f"\n{field('Change', f'<b>{signed_amount:+,}</b>')}"
        + f"\n{field('Visible XP', f'<b>{visible_xp:,}</b>')}",
        parse_mode="HTML",
    )
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


async def campaign_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle campaign inline button actions."""
    query = update.callback_query
    if not query or not query.data or not query.message:
        return

    await query.answer()
    data = query.data

    if data == "campaign_cancel":
        _clear_pending(context)
        await query.message.reply_text("Cancelled.", reply_markup=campaign_home_keyboard())
        return

    if data == "campaign_home":
        await _send_campaign_home(query.message)
        return

    if data == "campaign_xp":
        await get_or_create_user(query.from_user.id, query.from_user.username, query.from_user.first_name)
        await _send_xp_status(query.message, query.from_user.id)
        return

    if data == "campaign_leaderboard":
        await _send_campaign_leaderboard(query.message)
        return

    if data == "campaign_invite":
        await invite_command(update, context)
        return

    if data == "campaign_raids":
        await raids_command(update, context)
        return

    if data == "campaign_xlink_start":
        _set_pending(context, {"type": "xlink_handle", "chat_id": query.message.chat_id})
        await query.message.reply_text(
            "Send your X handle now, like <code>@yourhandle</code>.",
            parse_mode="HTML",
            reply_markup=campaign_cancel_keyboard(),
        )
        return

    if data == "campaign_xverify_start":
        _set_pending(context, {"type": "xverify", "chat_id": query.message.chat_id})
        await query.message.reply_text(
            "Send the X post URL that contains your verification code.",
            reply_markup=campaign_cancel_keyboard(),
        )
        return

    if data == "campaign_xpost_start":
        _set_pending(context, {"type": "xpost", "chat_id": query.message.chat_id})
        await query.message.reply_text(
            "Send your HostFi-related X post URL.",
            reply_markup=campaign_cancel_keyboard(),
        )
        return

    if data.startswith("campaign_raid_submit_"):
        try:
            raid_id = int(data.rsplit("_", 1)[-1])
        except ValueError:
            await query.message.reply_text(status_text("error", "Invalid raid button."))
            return
        _set_pending(context, {"type": "raid_proof", "raid_id": raid_id, "chat_id": query.message.chat_id})
        await query.message.reply_text(
            f"Send your X proof URL for <b>Raid #{raid_id}</b>.",
            parse_mode="HTML",
            reply_markup=campaign_cancel_keyboard(),
        )
        return

    if data.startswith("campaign_raid_help_"):
        raid_id = html.escape(data.rsplit("_", 1)[-1])
        await query.message.reply_text(
            f"To earn raid XP:\n"
            f"1. Open the approved X post.\n"
            f"2. Reply or quote it from your linked X account.\n"
            f"3. Tap <b>Submit Proof</b> and send your proof URL.\n\n"
            f"Fallback command:\n<code>/raid submit {raid_id} YOUR_X_PROOF_URL</code>",
            parse_mode="HTML",
        )


async def raid_submit_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show raid submission instructions for inline buttons."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    raid_id = query.data.rsplit("_", 1)[-1]
    await query.message.reply_text(
        f"Submit proof with:\n<code>/raid submit {html.escape(raid_id)} YOUR_X_PROOF_URL</code>",
        parse_mode="HTML",
    )


async def campaign_guided_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Consume the next message for a pending campaign button flow."""
    pending = context.user_data.get(PENDING_ACTION_KEY)
    if not pending:
        return
    if not update.effective_message or not update.effective_message.text:
        return
    if pending.get("chat_id") and update.effective_chat and update.effective_chat.id != pending["chat_id"]:
        return

    text = update.effective_message.text.strip()
    action_type = pending.get("type")
    success = False

    if action_type == "xlink_handle":
        success = await _start_xlink_for_handle(update, context, text)
    elif action_type == "xverify":
        success = await _verify_x_post_url(update, context, text)
    elif action_type == "xpost":
        success = await _submit_xpost_url(update, context, text)
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
    """Award invite XP for users who stayed in the group for 48 hours."""
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
            member = await bot.get_chat_member(int(join.get("chat_id") or get_primary_community_group_id()), invitee)
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
            "Invitee stayed in Telegram group for 48 hours",
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
