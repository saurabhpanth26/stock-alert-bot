# 📈 Indian Stock Market Alert Bot (Telegram)

A Telegram bot for groups. Anyone (or only admins) can set price alerts on NSE/BSE stocks and indices. The bot posts in the group when an alert fires.

## Features
- **Price alerts:** `/alert RELIANCE above 2900`, `/alert TCS below 3500`, or `/alert INFY 1600` (the bot picks the direction from the current price)
- **% move alerts:** `/alertpct TATAMOTORS 3` (±3% on the day), `+3` for up only, `-3` for down only
- **Notes on alerts:** `/alert HDFCBANK above 1800 breakout watch`
- **List / delete:** `/alerts` shows every alert with its live price and its distance from the target. `/delete 12` deletes one alert. Members can delete only their own alerts; admins can delete any. `/clearalerts` (admins only) deletes all.
- **Repeats until acknowledged:** when an alert fires, the bot posts it again every `REMIND_INTERVAL_SECONDS` (default 60s) until someone stops it. To stop it, tap **✅ Acknowledge** on the message, reply `stop` to the alert message, or send `/ack 12` (or `/stop 12`). `/ackall` stops every ringing alert. Each new reminder replaces the previous one, so the chat doesn't fill up. Reminders keep coming after market close too, until acknowledged.
- **History:** `/history` shows triggered alerts and who acknowledged them
- **Quotes:** `/price RELIANCE TCS ITC`, `/market` (NIFTY, BANKNIFTY, SENSEX + open/closed status)
- **Group watchlist:** `/watch`, `/unwatch`, `/watchlist`
- **Daily close summary:** at 15:40 IST on trading days, the bot posts index moves plus the chat's watchlist
- **Safety checks:** rejects an alert that would fire immediately, validates symbols, sends each alert only once
- **Access control:** `ADMIN_ONLY` mode and an `ALLOWED_CHAT_IDS` whitelist
- Checks prices only during NSE hours (Mon–Fri 9:15–15:30 IST). You can add holidays in config.

Symbols default to NSE (`RELIANCE` → `RELIANCE.NS`). Add `.BO` for BSE. Index names: `NIFTY`, `BANKNIFTY`, `SENSEX`, `NIFTYIT`, `FINNIFTY`.

## 1. Create the bot
1. In Telegram, open **@BotFather** → `/newbot` → choose a name and username → copy the **token**.
2. Add the bot to your group. Commands work with privacy mode on, so you don't need to make the bot an admin. To use `ADMIN_ONLY`, the bot only needs to see the member list, which works by default in groups.
3. Optional: send `/id` in the group and put that number in `ALLOWED_CHAT_IDS` so nobody can use the bot elsewhere.

## 2. Run with Docker (recommended)
```bash
cp .env.example .env      # paste your token
docker compose up -d --build
docker compose logs -f
```
Alerts are saved in `./data/alerts.json` (a plain JSON file, no database), which survives restarts.

## 3. Or run directly
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # paste your token
python bot.py
```

### systemd (VPS/EC2)
```ini
# /etc/systemd/system/stockbot.service
[Unit]
Description=Telegram stock alert bot
After=network-online.target

[Service]
WorkingDirectory=/opt/stock-alert-bot
ExecStart=/opt/stock-alert-bot/venv/bin/python bot.py
Restart=always
User=ubuntu

[Install]
WantedBy=multi-user.target
```
`sudo systemctl enable --now stockbot`

## Configuration (`.env`)
| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | – | Token from BotFather (required) |
| `CHECK_INTERVAL_SECONDS` | 1 | How often prices are checked |
| `ADMIN_ONLY` | false | Only group admins can add alerts / edit the watchlist |
| `ALLOWED_CHAT_IDS` | empty | Comma-separated chat IDs the bot will respond in |
| `MAX_ALERTS_PER_CHAT` | 100 | Limit per group |
| `CHECK_OUTSIDE_MARKET_HOURS` | false | Set `true` to test on weekends |
| `NSE_HOLIDAYS` | empty | e.g. `2026-12-25,2027-01-26` |
| `DAILY_SUMMARY` | true | 15:40 IST close summary |
| `REMIND_INTERVAL_SECONDS` | 60 | How often a fired alert repeats until acknowledged |
| `MAX_REMINDERS` | 0 | Stop automatically after N reminders (0 = never, wait for acknowledgement) |

## Notes
- **Data source:** Yahoo Finance via `yfinance`. It is free and needs no API key, but it is unofficial and quotes can lag by a few minutes, so it suits swing/positional alerts. For tick-accurate alerts, replace `_fetch_quote_sync()` in `market.py` with a broker API (Zerodha Kite, Upstox, Angel One SmartAPI, Dhan). Nothing else needs to change.
- After an alert is acknowledged it moves to `/history`. Set a new alert to re-arm it. In `ADMIN_ONLY` mode, only the person who set an alert or an admin can acknowledge it; otherwise anyone in the group can.
- Each check makes one request per unique symbol. At the default 1s interval, keep it to a handful of symbols — Yahoo may rate-limit heavier use (raise the interval to 5–10s if you see failed fetches). For hundreds of symbols, increase the interval or switch to a broker feed.
- This bot gives price notifications only. It is not investment advice.
