# OpenAI Telegram Crypto Guide Bot

This is a starter Telegram bot that uses OpenAI plus public crypto APIs to help users understand coin behavior from past to present.

## What it does

- Accepts `/analyze <coin_id>` commands (example: `/analyze bitcoin`).
- Collects market data from:
  - CoinGecko (`/coins/markets` and 7-day chart)
  - Binance (recent hourly closes for `<SYMBOL>USDT`, when available)
- Sends a structured market context to OpenAI.
- Returns a concise educational analysis:
  - trend summary
  - volatility and momentum signals
  - risk notes and missing-data caveats

> ⚠️ Important: no bot can guarantee profit. This project is educational and not financial advice.

## Setup

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather) and copy token.
2. Create/OpenAI API key.
3. Install dependencies:

```bash
pip install -r telegram_bot/requirements.txt
```

4. Set env vars:

```bash
export TELEGRAM_BOT_TOKEN="your_telegram_token"
export OPENAI_API_KEY="your_openai_api_key"
# optional
export OPENAI_MODEL="gpt-4.1-mini"
```

5. Run:

```bash
python telegram_bot/bot.py
```

## Commands

- `/start`
- `/help`
- `/analyze <coin_id>`

## Notes about “all data from every platform”

Gathering *every tiny data point from every platform* is not realistic in one bot due to API limits, data quality, and legal constraints.

A practical production approach is to add modular connectors over time, for example:
- exchanges (Binance, Coinbase, Kraken)
- on-chain metrics
- social sentiment feeds
- curated expert signal streams

This starter is intentionally small and extensible.
