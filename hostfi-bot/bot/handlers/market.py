"""
Module: market.py
Purpose: Live market data commands — /price, /rates, /market, /fear, /alert
Author: HOSTFI Bot Team
"""

import html
import logging
from typing import Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.utils.rate_limiter import check_rate_limit, get_redis
from config import ADMIN_CHANNEL_ID
from database.alerts import cancel_user_alerts, create_alert, get_user_alerts

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
FEAR_GREED_URL = "https://api.alternative.me/fng/"

# Common coin aliases → CoinGecko IDs
COIN_ALIASES: dict[str, str] = {
    "btc": "bitcoin",
    "bitcoin": "bitcoin",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "usdt": "tether",
    "tether": "tether",
    "sol": "solana",
    "solana": "solana",
    "bnb": "binancecoin",
    "binancecoin": "binancecoin",
    "usdc": "usd-coin",
    "usd-coin": "usd-coin",
    "doge": "dogecoin",
    "dogecoin": "dogecoin",
    "trx": "tron",
    "tron": "tron",
    "shib": "shiba-inu",
    "shiba-inu": "shiba-inu",
    "xlm": "stellar",
    "stellar": "stellar",
    "pol": "matic-network",
    "matic": "matic-network",
    "matic-network": "matic-network",
    "xrp": "ripple",
    "ripple": "ripple",
    "ada": "cardano",
    "cardano": "cardano",
}

# Reverse: CoinGecko ID → ticker symbol
COIN_SYMBOLS: dict[str, str] = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "tether": "USDT",
    "solana": "SOL",
    "binancecoin": "BNB",
    "usd-coin": "USDC",
    "dogecoin": "DOGE",
    "tron": "TRX",
    "shiba-inu": "SHIB",
    "stellar": "XLM",
    "matic-network": "POL",
    "ripple": "XRP",
    "cardano": "ADA",
}

RATES_COINS = "bitcoin,ethereum,tether,solana,binancecoin,usd-coin"

CACHE_TTL = 60  # seconds

FEAR_EMOJIS: dict[str, str] = {
    "Extreme Fear": "😱",
    "Fear": "😰",
    "Neutral": "😐",
    "Greed": "🤑",
    "Extreme Greed": "🤩",
}


# ---------------------------------------------------------------------------
# Helpers — CoinGecko with Redis caching
# ---------------------------------------------------------------------------


def _fmt_price(value: float | int) -> str:
    """
    Format a price with comma separators.

    Args:
        value: Numeric price

    Returns:
        Comma-formatted string (e.g. "45,123.78")
    """
    if isinstance(value, float):
        if value >= 1:
            return f"{value:,.2f}"
        # Small values: up to 8 decimal places, strip trailing zeros
        return f"{value:,.8f}".rstrip("0").rstrip(".")
    return f"{value:,}"


def _fmt_change(change: float | None) -> str:
    """
    Format a 24h percentage change with colour emoji.

    Args:
        change: Percentage change (can be None)

    Returns:
        Formatted string like "📈 +3.45%" or "📉 -1.23%"
    """
    if change is None:
        return "N/A"
    arrow = "📈" if change >= 0 else "📉"
    sign = "+" if change >= 0 else ""
    return f"{arrow} {sign}{change:.2f}%"


async def _cached_get(url: str, params: dict[str, str] | None = None) -> Any:
    """
    Perform an HTTP GET with Redis cache.

    Caches the raw JSON response under a key derived from the URL + params
    for CACHE_TTL seconds. Returns cached data on CoinGecko errors.

    Args:
        url: Request URL
        params: Optional query parameters

    Returns:
        Parsed JSON response

    Raises:
        httpx.HTTPStatusError: If request fails and no cache available
    """
    import json as _json

    redis = get_redis()
    cache_key = f"mkt:{url}:{_json.dumps(params or {}, sort_keys=True)}"

    # Try cache first
    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached is not None:
                data = cached if isinstance(cached, str) else str(cached)
                return _json.loads(data)
        except Exception as exc:
            logger.debug("Cache read miss or error: %s", exc)

    # Fetch from API
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    # Write to cache (fire-and-forget, swallow errors)
    if redis is not None:
        try:
            await redis.set(cache_key, _json.dumps(data), ex=CACHE_TTL)
        except Exception as exc:
            logger.debug("Cache write error: %s", exc)

    return data


def _resolve_coin(raw: str) -> str | None:
    """
    Resolve a user-supplied coin name/ticker to a CoinGecko ID.

    Args:
        raw: User input (e.g. "BTC", "bitcoin", "eth")

    Returns:
        CoinGecko coin ID or None if unrecognised
    """
    return COIN_ALIASES.get(raw.lower().strip())


# ---------------------------------------------------------------------------
# /price command
# ---------------------------------------------------------------------------


async def price_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /price [coin] — show live price in USD and NGN.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        user_id = update.effective_user.id

        if not await check_rate_limit(user_id, "market_cmd", limit=15, window=60):
            await update.effective_message.reply_text(
                "⏳ Too many requests. Please wait a moment."
            )
            return

        if not context.args:
            await update.effective_message.reply_text(
                "ℹ️ Usage: <code>/price BTC</code>\n\n"
                "Supports: BTC, ETH, USDT, SOL, BNB, USDC, DOGE, TRX, "
                "SHIB, XLM, POL, XRP, ADA",
                parse_mode="HTML",
            )
            return

        raw_coin = html.escape(context.args[0])
        coin_id = _resolve_coin(context.args[0])

        if not coin_id:
            await update.effective_message.reply_text(
                f"❌ Unknown coin: <code>{raw_coin}</code>\n\n"
                "Try: BTC, ETH, USDT, SOL, BNB, USDC, DOGE, TRX, SHIB, "
                "XLM, POL, XRP, ADA",
                parse_mode="HTML",
            )
            return

        symbol = COIN_SYMBOLS.get(coin_id, coin_id.upper())

        data = await _cached_get(
            f"{COINGECKO_BASE}/simple/price",
            {
                "ids": coin_id,
                "vs_currencies": "usd,ngn",
                "include_24hr_change": "true",
            },
        )

        coin_data = data.get(coin_id)
        if not coin_data:
            await update.effective_message.reply_text(
                "⚠️ Could not fetch price data. Please try again later."
            )
            return

        usd_price = coin_data.get("usd", 0)
        ngn_price = coin_data.get("ngn", 0)
        change_24h = coin_data.get("usd_24h_change")

        msg = (
            f"💰 <b>{symbol} Price</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🇺🇸 USD: <code>${_fmt_price(usd_price)}</code>\n"
            f"🇳🇬 NGN: <code>₦{_fmt_price(ngn_price)}</code>\n"
            f"📊 24h: {_fmt_change(change_24h)}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<i>Data from CoinGecko • Updated live</i>"
        )

        await update.effective_message.reply_text(msg, parse_mode="HTML")
        logger.info("Price query: user=%s coin=%s", user_id, symbol)

    except httpx.HTTPStatusError as exc:
        logger.error("CoinGecko API error in /price: %s", exc)
        await update.effective_message.reply_text(
            "⚠️ Market data is temporarily unavailable. Please try again shortly."
        )
    except Exception as exc:
        logger.error("Error in price_command: %s", exc)
        await update.effective_message.reply_text(
            "⚠️ Something went wrong. Please try again later."
        )


# ---------------------------------------------------------------------------
# /rates command
# ---------------------------------------------------------------------------


async def rates_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /rates — show exchange rates for HOSTFI-relevant coins.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        user_id = update.effective_user.id

        if not await check_rate_limit(user_id, "market_cmd", limit=15, window=60):
            await update.effective_message.reply_text(
                "⏳ Too many requests. Please wait a moment."
            )
            return

        data = await _cached_get(
            f"{COINGECKO_BASE}/simple/price",
            {
                "ids": RATES_COINS,
                "vs_currencies": "usd,ngn",
                "include_24hr_change": "true",
            },
        )

        lines: list[str] = ["📊 <b>HOSTFI Exchange Rates</b>\n━━━━━━━━━━━━━━━━━━"]

        for coin_id in RATES_COINS.split(","):
            coin_data = data.get(coin_id)
            if not coin_data:
                continue
            symbol = COIN_SYMBOLS.get(coin_id, coin_id.upper())
            usd = coin_data.get("usd", 0)
            ngn = coin_data.get("ngn", 0)
            change = coin_data.get("usd_24h_change")
            lines.append(
                f"\n<b>{symbol}</b>\n"
                f"  💵 ${_fmt_price(usd)}  •  ₦{_fmt_price(ngn)}\n"
                f"  {_fmt_change(change)}"
            )

        lines.append(
            "\n━━━━━━━━━━━━━━━━━━\n"
            "<i>Data from CoinGecko • Cached 60s</i>"
        )

        await update.effective_message.reply_text(
            "\n".join(lines), parse_mode="HTML"
        )
        logger.info("Rates query: user=%s", user_id)

    except httpx.HTTPStatusError as exc:
        logger.error("CoinGecko API error in /rates: %s", exc)
        await update.effective_message.reply_text(
            "⚠️ Market data is temporarily unavailable. Please try again shortly."
        )
    except Exception as exc:
        logger.error("Error in rates_command: %s", exc)
        await update.effective_message.reply_text(
            "⚠️ Something went wrong. Please try again later."
        )


# ---------------------------------------------------------------------------
# /market command
# ---------------------------------------------------------------------------


async def market_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /market — show top 10 cryptocurrencies by market cap.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        user_id = update.effective_user.id

        if not await check_rate_limit(user_id, "market_cmd", limit=15, window=60):
            await update.effective_message.reply_text(
                "⏳ Too many requests. Please wait a moment."
            )
            return

        data = await _cached_get(
            f"{COINGECKO_BASE}/coins/markets",
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": "10",
                "page": "1",
                "sparkline": "false",
            },
        )

        if not isinstance(data, list) or not data:
            await update.effective_message.reply_text(
                "⚠️ Could not fetch market data. Please try again later."
            )
            return

        lines: list[str] = ["🌍 <b>Top 10 Crypto by Market Cap</b>\n━━━━━━━━━━━━━━━━━━"]

        for i, coin in enumerate(data[:10], 1):
            symbol = str(coin.get("symbol", "")).upper()
            price = coin.get("current_price", 0)
            change = coin.get("price_change_percentage_24h")
            mcap = coin.get("market_cap", 0)

            # Format market cap in billions/millions
            if mcap >= 1_000_000_000:
                mcap_str = f"${mcap / 1_000_000_000:.1f}B"
            elif mcap >= 1_000_000:
                mcap_str = f"${mcap / 1_000_000:.1f}M"
            else:
                mcap_str = f"${_fmt_price(mcap)}"

            lines.append(
                f"\n<b>{i}. {symbol}</b> — ${_fmt_price(price)}\n"
                f"   {_fmt_change(change)}  •  MCap: {mcap_str}"
            )

        lines.append(
            "\n━━━━━━━━━━━━━━━━━━\n"
            "<i>Data from CoinGecko • Updated live</i>"
        )

        await update.effective_message.reply_text(
            "\n".join(lines), parse_mode="HTML"
        )
        logger.info("Market query: user=%s", user_id)

    except httpx.HTTPStatusError as exc:
        logger.error("CoinGecko API error in /market: %s", exc)
        await update.effective_message.reply_text(
            "⚠️ Market data is temporarily unavailable. Please try again shortly."
        )
    except Exception as exc:
        logger.error("Error in market_command: %s", exc)
        await update.effective_message.reply_text(
            "⚠️ Something went wrong. Please try again later."
        )


# ---------------------------------------------------------------------------
# /fear command
# ---------------------------------------------------------------------------


async def fear_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /fear — show the Crypto Fear & Greed Index.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        user_id = update.effective_user.id

        if not await check_rate_limit(user_id, "market_cmd", limit=15, window=60):
            await update.effective_message.reply_text(
                "⏳ Too many requests. Please wait a moment."
            )
            return

        data = await _cached_get(FEAR_GREED_URL)

        fng_data = data.get("data", [{}])
        if not fng_data:
            await update.effective_message.reply_text(
                "⚠️ Could not fetch Fear & Greed data."
            )
            return

        entry = fng_data[0]
        value = entry.get("value", "N/A")
        classification = entry.get("value_classification", "Unknown")
        emoji = FEAR_EMOJIS.get(classification, "📊")

        # Interpretation guide
        if int(value) <= 25:
            interpretation = "Market is very fearful. Historically, this may present buying opportunities."
        elif int(value) <= 45:
            interpretation = "Market sentiment is cautious. Investors are wary."
        elif int(value) <= 55:
            interpretation = "Market sentiment is neutral. No strong directional bias."
        elif int(value) <= 75:
            interpretation = "Market is feeling greedy. Caution may be warranted."
        else:
            interpretation = "Extreme greed in the market. Historically, corrections may follow."

        msg = (
            f"{emoji} <b>Crypto Fear & Greed Index</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 <b>Score:</b> <code>{value}/100</code>\n"
            f"🏷️ <b>Status:</b> {classification}\n\n"
            f"💡 <i>{interpretation}</i>\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<i>Source: Alternative.me</i>\n\n"
            f"⚠️ <i>This is not financial advice.</i>"
        )

        await update.effective_message.reply_text(msg, parse_mode="HTML")
        logger.info("Fear & Greed query: user=%s value=%s", user_id, value)

    except Exception as exc:
        logger.error("Error in fear_command: %s", exc)
        await update.effective_message.reply_text(
            "⚠️ Something went wrong. Please try again later."
        )


# ---------------------------------------------------------------------------
# /alert command
# ---------------------------------------------------------------------------


async def alert_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /alert — manage price alerts.

    Subcommands:
        /alert set BTC 50000  — create alert for BTC crossing $50,000
        /alert cancel          — cancel all active alerts
        /alert list            — show your active alerts

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        user_id = update.effective_user.id

        if not await check_rate_limit(user_id, "market_cmd", limit=15, window=60):
            await update.effective_message.reply_text(
                "⏳ Too many requests. Please wait a moment."
            )
            return

        if not context.args:
            await update.effective_message.reply_text(
                "ℹ️ <b>Price Alert Commands</b>\n\n"
                "<code>/alert set BTC 50000</code> — Alert when BTC crosses $50,000\n"
                "<code>/alert cancel</code> — Cancel all your alerts\n"
                "<code>/alert list</code> — View your active alerts",
                parse_mode="HTML",
            )
            return

        subcommand = context.args[0].lower()

        if subcommand == "set":
            await _alert_set(update, context)
        elif subcommand == "cancel":
            await _alert_cancel(update, context)
        elif subcommand == "list":
            await _alert_list(update, context)
        else:
            await update.effective_message.reply_text(
                "❌ Unknown subcommand. Use <code>set</code>, "
                "<code>cancel</code>, or <code>list</code>.",
                parse_mode="HTML",
            )

    except Exception as exc:
        logger.error("Error in alert_command: %s", exc)
        await update.effective_message.reply_text(
            "⚠️ Something went wrong. Please try again later."
        )


async def _alert_set(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /alert set [coin] [price] — create a new price alert.

    Determines direction (above/below) by comparing target to current price.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not update.effective_user or not update.effective_message:
        return

    args = context.args or []
    if len(args) < 3:
        await update.effective_message.reply_text(
            "ℹ️ Usage: <code>/alert set BTC 50000</code>",
            parse_mode="HTML",
        )
        return

    coin_id = _resolve_coin(args[1])
    if not coin_id:
        await update.effective_message.reply_text(
            f"❌ Unknown coin: <code>{html.escape(args[1])}</code>",
            parse_mode="HTML",
        )
        return

    try:
        target_price = float(args[2].replace(",", ""))
        if target_price <= 0:
            raise ValueError("Price must be positive")
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Invalid price. Please enter a positive number."
        )
        return

    symbol = COIN_SYMBOLS.get(coin_id, coin_id.upper())
    user_id = update.effective_user.id

    # Check max active alerts (5 per user)
    existing = await get_user_alerts(user_id)
    if len(existing) >= 5:
        await update.effective_message.reply_text(
            "⚠️ You can have a maximum of 5 active alerts.\n"
            "Use <code>/alert cancel</code> to remove existing ones.",
            parse_mode="HTML",
        )
        return

    # Get current price to determine direction
    try:
        data = await _cached_get(
            f"{COINGECKO_BASE}/simple/price",
            {"ids": coin_id, "vs_currencies": "usd"},
        )
        current_price = data.get(coin_id, {}).get("usd", 0)
    except Exception:
        current_price = 0

    if current_price > 0:
        direction = "above" if target_price > current_price else "below"
    else:
        direction = "above"

    alert = await create_alert(user_id, coin_id, target_price, direction)

    if alert:
        direction_emoji = "⬆️" if direction == "above" else "⬇️"
        await update.effective_message.reply_text(
            f"✅ <b>Alert Created!</b>\n\n"
            f"🪙 Coin: <b>{symbol}</b>\n"
            f"🎯 Target: <code>${_fmt_price(target_price)}</code>\n"
            f"{direction_emoji} Direction: {direction}\n"
            f"💰 Current: <code>${_fmt_price(current_price)}</code>\n\n"
            f"<i>You'll receive a DM when the price crosses your target.\n"
            f"Alerts are checked every 5 minutes.</i>",
            parse_mode="HTML",
        )
        logger.info(
            "Alert created: user=%s coin=%s target=%s direction=%s",
            user_id,
            symbol,
            target_price,
            direction,
        )
    else:
        await update.effective_message.reply_text(
            "⚠️ Failed to create alert. Please try again later."
        )


async def _alert_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /alert cancel — cancel all active alerts for the user.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not update.effective_user or not update.effective_message:
        return

    user_id = update.effective_user.id
    cancelled = await cancel_user_alerts(user_id)

    if cancelled > 0:
        await update.effective_message.reply_text(
            f"✅ Cancelled <b>{cancelled}</b> active alert(s).",
            parse_mode="HTML",
        )
        logger.info("Alerts cancelled: user=%s count=%s", user_id, cancelled)
    else:
        await update.effective_message.reply_text(
            "ℹ️ You have no active alerts to cancel."
        )


async def _alert_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /alert list — show all active alerts for the user.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not update.effective_user or not update.effective_message:
        return

    user_id = update.effective_user.id
    alerts = await get_user_alerts(user_id)

    if not alerts:
        await update.effective_message.reply_text(
            "ℹ️ You have no active price alerts.\n"
            "Use <code>/alert set BTC 50000</code> to create one.",
            parse_mode="HTML",
        )
        return

    lines: list[str] = ["🔔 <b>Your Active Alerts</b>\n━━━━━━━━━━━━━━━━━━"]

    for alert in alerts:
        coin_id = alert.get("coin_id", "")
        symbol = COIN_SYMBOLS.get(coin_id, coin_id.upper())
        target = alert.get("target_price", 0)
        direction = alert.get("direction", "above")
        direction_emoji = "⬆️" if direction == "above" else "⬇️"
        lines.append(
            f"\n🪙 <b>{symbol}</b> — ${_fmt_price(float(target))} "
            f"{direction_emoji} {direction}"
        )

    lines.append(
        "\n━━━━━━━━━━━━━━━━━━\n"
        "Use <code>/alert cancel</code> to remove all alerts."
    )

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML"
    )


# ---------------------------------------------------------------------------
# Daily digest builder (called by scheduler)
# ---------------------------------------------------------------------------


async def build_daily_digest() -> str:
    """
    Build the daily market digest message for the community group.

    Includes top movers, HOSTFI-relevant rates, and market sentiment.

    Returns:
        HTML-formatted digest message string
    """
    lines: list[str] = [
        "☀️ <b>Good Morning! Daily Crypto Digest</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    # Rates section
    try:
        rates_data = await _cached_get(
            f"{COINGECKO_BASE}/simple/price",
            {
                "ids": RATES_COINS,
                "vs_currencies": "usd,ngn",
                "include_24hr_change": "true",
            },
        )

        lines.append("\n📊 <b>HOSTFI Rates</b>")
        for coin_id in RATES_COINS.split(","):
            coin_data = rates_data.get(coin_id)
            if not coin_data:
                continue
            symbol = COIN_SYMBOLS.get(coin_id, coin_id.upper())
            usd = coin_data.get("usd", 0)
            change = coin_data.get("usd_24h_change")
            lines.append(
                f"  {symbol}: ${_fmt_price(usd)} {_fmt_change(change)}"
            )
    except Exception as exc:
        logger.error("Digest rates fetch failed: %s", exc)
        lines.append("\n⚠️ Rates temporarily unavailable")

    # Top movers section
    try:
        market_data = await _cached_get(
            f"{COINGECKO_BASE}/coins/markets",
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": "20",
                "page": "1",
                "sparkline": "false",
            },
        )

        if isinstance(market_data, list) and market_data:
            sorted_coins = sorted(
                market_data,
                key=lambda c: abs(c.get("price_change_percentage_24h", 0) or 0),
                reverse=True,
            )
            top_movers = sorted_coins[:3]

            lines.append("\n🚀 <b>Top Movers (24h)</b>")
            for coin in top_movers:
                symbol = str(coin.get("symbol", "")).upper()
                price = coin.get("current_price", 0)
                change = coin.get("price_change_percentage_24h")
                lines.append(
                    f"  {symbol}: ${_fmt_price(price)} {_fmt_change(change)}"
                )
    except Exception as exc:
        logger.error("Digest market fetch failed: %s", exc)

    # Fear & Greed section
    try:
        fng_data = await _cached_get(FEAR_GREED_URL)
        entry = fng_data.get("data", [{}])[0]
        value = entry.get("value", "N/A")
        classification = entry.get("value_classification", "Unknown")
        emoji = FEAR_EMOJIS.get(classification, "📊")
        lines.append(f"\n{emoji} <b>Market Sentiment:</b> {classification} ({value}/100)")
    except Exception as exc:
        logger.error("Digest FnG fetch failed: %s", exc)

    lines.append(
        "\n━━━━━━━━━━━━━━━━━━\n"
        "💡 Use /price, /market, /fear for more details\n"
        "📲 Trade now on <b>HostFi</b> — https://hostfi.io"
    )

    return "\n".join(lines)
