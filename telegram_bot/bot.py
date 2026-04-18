#!/usr/bin/env python3
"""OpenAI-based Telegram bot for crypto market guidance.

Features
- Responds to /start and /help with usage instructions.
- /analyze <coin_id> (example: /analyze bitcoin) gathers:
  - CoinGecko market data (price, market cap, 24h volume, 7d chart)
  - Binance klines (if a USDT pair exists) for short-term trend context
- Uses OpenAI to produce a concise, educational market behavior summary.

Important: This bot does NOT guarantee profit and should not be treated as
financial advice.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests
from openai import OpenAI
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=LOG_LEVEL
)
logger = logging.getLogger("crypto_guide_bot")

COINGECKO_API = "https://api.coingecko.com/api/v3"
BINANCE_API = "https://api.binance.com/api/v3"

SYSTEM_PROMPT = (
    "You are a careful crypto market education assistant. "
    "Analyze provided data from multiple sources and explain coin behavior "
    "(trend, volatility, momentum, risk factors, and uncertainty) in clear language. "
    "Never promise profit. Include a brief risk warning."
)


@dataclass
class CoinSnapshot:
    coin_id: str
    symbol: str
    name: str
    current_price_usd: float
    market_cap_usd: float
    volume_24h_usd: float
    price_change_24h_pct: float
    price_7d_start: float | None
    price_7d_end: float | None
    price_7d_change_pct: float | None
    binance_symbol: str | None
    binance_close_prices: list[float]


def _safe_get(url: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def fetch_coingecko_snapshot(coin_id: str) -> dict[str, Any]:
    url = f"{COINGECKO_API}/coins/markets"
    data = _safe_get(
        url,
        params={
            "vs_currency": "usd",
            "ids": coin_id,
            "price_change_percentage": "24h",
        },
    )
    if not data:
        raise ValueError(f"Unknown coin id '{coin_id}'. Try /analyze bitcoin")
    return data[0]


def fetch_coingecko_7d_prices(coin_id: str) -> list[float]:
    url = f"{COINGECKO_API}/coins/{coin_id}/market_chart"
    data = _safe_get(url, params={"vs_currency": "usd", "days": 7, "interval": "daily"})
    prices = data.get("prices", []) if isinstance(data, dict) else []
    return [float(p[1]) for p in prices if isinstance(p, list) and len(p) >= 2]


def find_binance_symbol(symbol: str) -> str | None:
    data = _safe_get(f"{BINANCE_API}/exchangeInfo")
    symbols = data.get("symbols", []) if isinstance(data, dict) else []
    target = f"{symbol.upper()}USDT"
    for item in symbols:
        if item.get("symbol") == target and item.get("status") == "TRADING":
            return target
    return None


def fetch_binance_recent_closes(binance_symbol: str, interval: str = "1h", limit: int = 24) -> list[float]:
    data = _safe_get(
        f"{BINANCE_API}/klines",
        params={"symbol": binance_symbol, "interval": interval, "limit": limit},
    )
    closes: list[float] = []
    if isinstance(data, list):
        for row in data:
            if isinstance(row, list) and len(row) >= 5:
                closes.append(float(row[4]))
    return closes


def build_snapshot(coin_id: str) -> CoinSnapshot:
    market = fetch_coingecko_snapshot(coin_id)
    prices_7d = fetch_coingecko_7d_prices(coin_id)

    symbol = str(market.get("symbol", "")).lower()
    binance_symbol = find_binance_symbol(symbol)
    binance_closes: list[float] = []
    if binance_symbol:
        try:
            binance_closes = fetch_binance_recent_closes(binance_symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Binance fetch failed for %s: %s", binance_symbol, exc)

    start_price = prices_7d[0] if prices_7d else None
    end_price = prices_7d[-1] if prices_7d else None
    seven_day_change = None
    if start_price and end_price and start_price > 0:
        seven_day_change = ((end_price - start_price) / start_price) * 100

    return CoinSnapshot(
        coin_id=coin_id,
        symbol=symbol,
        name=str(market.get("name", coin_id)),
        current_price_usd=float(market.get("current_price") or 0),
        market_cap_usd=float(market.get("market_cap") or 0),
        volume_24h_usd=float(market.get("total_volume") or 0),
        price_change_24h_pct=float(market.get("price_change_percentage_24h") or 0),
        price_7d_start=start_price,
        price_7d_end=end_price,
        price_7d_change_pct=seven_day_change,
        binance_symbol=binance_symbol,
        binance_close_prices=binance_closes,
    )


def render_raw_context(snapshot: CoinSnapshot) -> str:
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "coin": {
            "id": snapshot.coin_id,
            "name": snapshot.name,
            "symbol": snapshot.symbol,
            "current_price_usd": snapshot.current_price_usd,
            "market_cap_usd": snapshot.market_cap_usd,
            "volume_24h_usd": snapshot.volume_24h_usd,
            "price_change_24h_pct": snapshot.price_change_24h_pct,
            "price_7d_start": snapshot.price_7d_start,
            "price_7d_end": snapshot.price_7d_end,
            "price_7d_change_pct": snapshot.price_7d_change_pct,
        },
        "binance": {
            "symbol": snapshot.binance_symbol,
            "recent_close_prices": snapshot.binance_close_prices,
        },
    }
    return json.dumps(payload, indent=2)


def ask_openai_for_analysis(client: OpenAI, snapshot: CoinSnapshot) -> str:
    context = render_raw_context(snapshot)
    user_prompt = (
        "Analyze this crypto data from multiple sources and provide:\n"
        "1) Trend summary (past to present)\n"
        "2) Volatility and momentum observations\n"
        "3) What kind of trader profile this may suit (very briefly)\n"
        "4) Key risks and what additional data is missing\n"
        "5) A short educational conclusion, not financial advice\n\n"
        f"Data:\n{context}"
    )

    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
    )
    return response.output_text.strip()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: ARG001
    text = (
        "🤖 *OpenAI Crypto Guide Bot*\n\n"
        "I collect market signals from public digital trading sources and summarize coin behavior.\n"
        "Use: `/analyze <coin_id>`\n"
        "Example: `/analyze bitcoin`\n\n"
        "I do *not* guarantee profit and this is *not financial advice*."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: ARG001
    text = (
        "Commands:\n"
        "• `/start` – intro\n"
        "• `/help` – this help\n"
        "• `/analyze <coin_id>` – analyze a coin (CoinGecko id, e.g., bitcoin, ethereum, solana)"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /analyze <coin_id>\nExample: /analyze bitcoin")
        return

    coin_id = context.args[0].lower().strip()
    await update.message.reply_text(f"Analyzing *{coin_id}*...", parse_mode=ParseMode.MARKDOWN)

    try:
        snapshot = build_snapshot(coin_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Snapshot build failed")
        await update.message.reply_text(f"Could not fetch market data: {exc}")
        return

    try:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        analysis = ask_openai_for_analysis(client, snapshot)
    except KeyError:
        await update.message.reply_text("OPENAI_API_KEY is not set on the server.")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("OpenAI analysis failed")
        await update.message.reply_text(f"OpenAI analysis failed: {exc}")
        return

    await update.message.reply_text(analysis)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("analyze", cmd_analyze))

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
