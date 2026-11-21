"""Indian stock market alert bot for Telegram groups."""
from __future__ import annotations

import functools
import html
import logging
import time
from datetime import time as dtime

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import Forbidden
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import config
import market
from storage import Storage

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
# With a 1s check interval a slow fetch can overlap the next tick; APScheduler skips it and warns — that's expected
logging.getLogger("apscheduler.scheduler").setLevel(logging.ERROR)
log = logging.getLogger("stockbot")

store = Storage(config.DB_PATH)
esc = html.escape

HELP = """<b>📈 Indian Stock Alert Bot</b>

<b>Alerts</b>
/alert RELIANCE above 2900 – alert when price crosses up
/alert TCS below 3500 – alert when price falls below
/alert INFY 1600 – direction picked automatically
/alert HDFCBANK above 1800 breakout! – add a note
/alertpct TATAMOTORS 3 – day move of ±3%
/alertpct SBIN +2 or -2 – up only / down only
/alerts – list active alerts in this chat
/delete 12 – delete alert #12
/clearalerts – delete all alerts (admins)
/history – recently triggered alerts

<b>When an alert fires 🚨</b>
It repeats every {remind}s until someone stops it:
• tap <b>✅ Acknowledge</b> on the message, or
• reply <code>stop</code> to the alert message, or
• send /ack 12 (or /stop 12) · /ackall to stop all

<b>Prices</b>
/price RELIANCE TCS – live quote(s)
/market – NIFTY, BANKNIFTY, SENSEX + market status

<b>Watchlist</b>
/watch ITC – add to chat watchlist
/unwatch ITC – remove
/watchlist – prices of all watched stocks

<b>Other</b>
/id – show this chat's ID

Symbols default to NSE (RELIANCE = RELIANCE.NS). Use <code>.BO</code> for BSE, e.g. <code>TCS.BO</code>.
Indices: NIFTY, BANKNIFTY, SENSEX, NIFTYIT, FINNIFTY.
Alerts are checked every {interval}s during market hours (Mon–Fri 9:15–15:30 IST) and fire once."""


# ---- helpers ---------------------------------------------------------------
def who(update: Update) -> str:
    u = update.effective_user
    return f"@{u.username}" if u.username else u.full_name


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        return True
    member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)


async def can_modify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return not config.ADMIN_ONLY or await is_admin(update, context)


def guarded(func):
    """Restrict commands to ALLOWED_CHAT_IDS when that setting is used."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_chat or not update.effective_user:
            return
        if config.ALLOWED_CHAT_IDS and update.effective_chat.id not in config.ALLOWED_CHAT_IDS:
            await update.effective_message.reply_text(
                f"This bot isn't enabled for this chat (ID {update.effective_chat.id})."
            )
            return
        return await func(update, context)

    return wrapper


async def reply(update: Update, text: str):
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def parse_price(s: str) -> float | None:
    try:
        v = float(s.replace(",", "").replace("₹", "").strip())
        return v if v > 0 else None
    except ValueError:
        return None


def quote_line(q: market.Quote) -> str:
    name = esc(market.display(q.ticker))
    if q.change_pct is None:
        return f"<b>{name}</b>  {market.fmt_price(q.ticker, q.price)}"
    arrow = "🟢" if q.change >= 0 else "🔴"
    return (
        f"{arrow} <b>{name}</b>  {market.fmt_price(q.ticker, q.price)}  "
        f"({q.change:+,.2f}, {q.change_pct:+.2f}%)"
    )


def quote_table(quotes: list[market.Quote]) -> str:
    rows = [f"{'SYMBOL':<14}{'PRICE':>12}{'CHG%':>9}"]
    for q in quotes:
        pct = f"{q.change_pct:+.2f}%" if q.change_pct is not None else "-"
        rows.append(f"{market.display(q.ticker)[:14]:<14}{q.price:>12,.2f}{pct:>9}")
    return "<pre>" + esc("\n".join(rows)) + "</pre>"


def alert_line(a) -> str:
    text = f"<b>#{a['id']}</b> {esc(market.display(a['ticker']))} {esc(market.describe(a['kind'], a['target'], a['ticker']))}"
    if a["note"]:
        text += f" — <i>{esc(a['note'])}</i>"
    if a["username"]:
        text += f" <i>(by {esc(a['username'])})</i>"
    return text


def market_status() -> str:
    if market.is_market_open():
        return "🟢 Market is <b>open</b>"
    if not market.is_trading_day():
        return "⚪ Market is <b>closed</b> today (weekend/holiday)"
    return "⚪ Market is <b>closed</b> (hours 9:15–15:30 IST)"


# ---- commands --------------------------------------------------------------
@guarded
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, HELP.format(interval=config.CHECK_INTERVAL, remind=config.REMIND_INTERVAL))


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, f"Chat ID: <code>{update.effective_chat.id}</code>")


@guarded
async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await reply(update, "Usage: /price RELIANCE [TCS INFY ...]")
    tickers = [market.normalize(s) for s in context.args[:10]]
    quotes = await market.get_quotes(tickers)
    lines = [quote_line(quotes[t]) if t in quotes else f"❓ {esc(market.display(t))}: not found" for t in tickers]
    await reply(update, "\n".join(lines))


@guarded
async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tickers = ["^NSEI", "^NSEBANK", "^BSESN"]
    quotes = await market.get_quotes(tickers)
    lines = [quote_line(quotes[t]) for t in tickers if t in quotes] or ["Couldn't fetch index data right now."]
    await reply(update, market_status() + "\n\n" + "\n".join(lines))


@guarded
async def cmd_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        return await reply(update, "Usage: /alert SYMBOL [above|below] PRICE [note]\nExample: /alert RELIANCE above 2900")
    if not await can_modify(update, context):
        return await reply(update, "Only group admins can add alerts here.")
    chat_id = update.effective_chat.id
    if store.count_active(chat_id) >= config.MAX_ALERTS_PER_CHAT:
        return await reply(update, f"This chat already has {config.MAX_ALERTS_PER_CHAT} alerts. Delete some first.")

    ticker = market.normalize(args[0])
    direction_words = {"above": "above", ">": "above", ">=": "above", "up": "above",
                       "below": "below", "<": "below", "<=": "below", "down": "below"}
    if args[1].lower() in direction_words:
        if len(args) < 3:
            return await reply(update, "Please give a price, e.g. /alert RELIANCE above 2900")
        kind, price_str, note_parts = direction_words[args[1].lower()], args[2], args[3:]
    else:
        kind, price_str, note_parts = None, args[1], args[2:]

    target = parse_price(price_str)
    if target is None:
        return await reply(update, f"'{esc(price_str)}' isn't a valid price.")

    q = await market.get_quote(ticker)
    if not q:
        return await reply(update, f"Couldn't find <b>{esc(market.display(ticker))}</b>. Check the symbol (NSE symbol, or add .BO for BSE).")

    if kind is None:
        kind = "above" if target > q.price else "below"
    if market.is_hit(kind, target, q):
        return await reply(
            update,
            f"{esc(market.display(ticker))} is already at {market.fmt_price(ticker, q.price)}, "
            f"so a '{kind} {market.fmt_price(ticker, target)}' alert would fire immediately. Not created.",
        )

    note = " ".join(note_parts)[:200] or None
    alert_id = store.add_alert(chat_id, update.effective_user.id, who(update), ticker, kind, target, note)
    await reply(
        update,
        f"✅ Alert <b>#{alert_id}</b> set: {esc(market.display(ticker))} {esc(market.describe(kind, target, ticker))}\n"
        f"Now: {market.fmt_price(ticker, q.price)}",
    )


@guarded
async def cmd_alertpct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        return await reply(update, "Usage: /alertpct SYMBOL PCT [note]\n3 = ±3%, +3 = up 3%, -3 = down 3%")
    if not await can_modify(update, context):
        return await reply(update, "Only group admins can add alerts here.")
    chat_id = update.effective_chat.id
    if store.count_active(chat_id) >= config.MAX_ALERTS_PER_CHAT:
        return await reply(update, f"This chat already has {config.MAX_ALERTS_PER_CHAT} alerts. Delete some first.")

    ticker = market.normalize(args[0])
    raw = args[1].rstrip("%")
    kind = "pct_up" if raw.startswith("+") else "pct_down" if raw.startswith("-") else "pct_any"
    try:
        target = abs(float(raw))
    except ValueError:
        return await reply(update, f"'{esc(args[1])}' isn't a valid percentage.")
    if target <= 0:
        return await reply(update, "Percentage must be more than 0.")

    q = await market.get_quote(ticker)
    if not q:
        return await reply(update, f"Couldn't find <b>{esc(market.display(ticker))}</b>.")
    if market.is_hit(kind, target, q):
        return await reply(update, f"{esc(market.display(ticker))} has already moved {q.change_pct:+.2f}% today. Not created.")

    note = " ".join(args[2:])[:200] or None
    alert_id = store.add_alert(chat_id, update.effective_user.id, who(update), ticker, kind, target, note)
    now = f"{q.change_pct:+.2f}%" if q.change_pct is not None else "n/a"
    await reply(
        update,
        f"✅ Alert <b>#{alert_id}</b> set: {esc(market.display(ticker))} {esc(market.describe(kind, target, ticker))}\n"
        f"Today so far: {now}",
    )


@guarded
async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    alerts = store.list_alerts(update.effective_chat.id)
    firing = store.list_firing(update.effective_chat.id)
    if not alerts and not firing:
        return await reply(update, "No active alerts. Add one with /alert RELIANCE above 2900")
    ringing = ""
    if firing:
        ringing = f"<b>🚨 Ringing – waiting for acknowledgement ({len(firing)})</b>\n" + "\n".join(
            f"{alert_line(a)}\n    fired at {market.fmt_price(a['ticker'], a['triggered_price'])} · "
            f"{a.get('reminders', 0)} reminder(s) · /ack {a['id']}"
            for a in firing
        ) + "\n\n"
    if not alerts:
        return await reply(update, ringing + "No other alerts are watching prices.")
    quotes = await market.get_quotes(sorted({a["ticker"] for a in alerts}))
    lines = []
    for a in alerts:
        line = alert_line(a)
        q = quotes.get(a["ticker"])
        if q:
            line += f"\n    now {market.fmt_price(a['ticker'], q.price)}"
            if a["kind"] in ("above", "below"):
                gap = (a["target"] - q.price) / q.price * 100
                line += f" · {gap:+.2f}% away"
        lines.append(line)
    await reply(update, ringing + f"<b>🔔 Watching ({len(alerts)})</b>\n\n" + "\n".join(lines))


@guarded
async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].lstrip("#").isdigit():
        return await reply(update, "Usage: /delete ALERT_ID (see /alerts)")
    alert_id = int(context.args[0].lstrip("#"))
    chat_id = update.effective_chat.id
    alert = store.get_alert(chat_id, alert_id)
    if not alert:
        return await reply(update, f"No active alert #{alert_id} in this chat.")
    if alert["user_id"] != update.effective_user.id and not await is_admin(update, context):
        return await reply(update, "You can only delete your own alerts (admins can delete any).")
    store.delete_alert(chat_id, alert_id)
    await reply(update, f"🗑 Deleted alert #{alert_id}.")


@guarded
async def cmd_clearalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await reply(update, "Only group admins can clear all alerts.")
    n = store.clear_alerts(update.effective_chat.id)
    await reply(update, f"🗑 Cleared {n} alert(s).")


@guarded
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = store.history(update.effective_chat.id)
    if not rows:
        return await reply(update, "No alerts have triggered yet.")
    lines = []
    for a in rows:
        status = (f"✅ acknowledged by {esc(a['acked_by'])}" if a["status"] == "done" and a.get("acked_by")
                  else "🚨 still ringing" if a["status"] == "firing" else "")
        lines.append(
            f"{alert_line(a)}\n    fired at {market.fmt_price(a['ticker'], a['triggered_price'])} · "
            f"{a['triggered_at'][:16].replace('T', ' ')} UTC · {status}"
        )
    await reply(update, "<b>📜 Recently triggered</b>\n\n" + "\n".join(lines))


@guarded
async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await reply(update, "Usage: /watch ITC [SBIN ...]")
    if not await can_modify(update, context):
        return await reply(update, "Only group admins can change the watchlist here.")
    added, bad = [], []
    for s in context.args[:10]:
        t = market.normalize(s)
        if await market.get_quote(t):
            store.add_watch(update.effective_chat.id, t, who(update))
            added.append(market.display(t))
        else:
            bad.append(s.upper())
    msg = []
    if added:
        msg.append("👀 Watching: " + esc(", ".join(added)))
    if bad:
        msg.append("❓ Not found: " + esc(", ".join(bad)))
    await reply(update, "\n".join(msg))


@guarded
async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await reply(update, "Usage: /unwatch ITC")
    if not await can_modify(update, context):
        return await reply(update, "Only group admins can change the watchlist here.")
    removed = [market.display(market.normalize(s)) for s in context.args
               if store.remove_watch(update.effective_chat.id, market.normalize(s))]
    await reply(update, ("Removed: " + esc(", ".join(removed))) if removed else "None of those were on the watchlist.")


@guarded
async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tickers = store.watchlist(update.effective_chat.id)
    if not tickers:
        return await reply(update, "Watchlist is empty. Add with /watch ITC")
    quotes = await market.get_quotes(tickers)
    await reply(update, f"<b>👀 Watchlist</b>\n{quote_table([quotes[t] for t in tickers if t in quotes])}")


# ---- background jobs -------------------------------------------------------
async def job_check_alerts(context: ContextTypes.DEFAULT_TYPE):
    if not config.CHECK_OUTSIDE_MARKET_HOURS and not market.is_market_open():
        return
    alerts = store.all_active()
    if not alerts:
        return
    quotes = await market.get_quotes(sorted({a["ticker"] for a in alerts}))
    for a in alerts:
        q = quotes.get(a["ticker"])
        if not q or not market.is_hit(a["kind"], a["target"], q):
            continue
        if not store.mark_triggered(a["id"], q.price):
            continue  # already handled
        a = store.get_alert(a["chat_id"], a["id"])
        await send_alert(context, a, q, reminder_no=0)


# ---- ringing alerts: repeat until acknowledged --------------------------------
def ack_keyboard(alert_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Acknowledge (stop)", callback_data=f"ack:{alert_id}")]])


def alert_text(a: dict, q: market.Quote | None, reminder_no: int) -> str:
    name = esc(market.display(a["ticker"]))
    if reminder_no == 0:
        text = f"🚨 <b>Alert #{a['id']} triggered</b>\n"
    else:
        text = f"🔁 <b>Reminder {reminder_no} · Alert #{a['id']} not acknowledged</b>\n"
    text += f"<b>{name}</b> {esc(market.describe(a['kind'], a['target'], a['ticker']))}\n"
    if q:
        pct = f" ({q.change_pct:+.2f}% today)" if q.change_pct is not None else ""
        text += f"Now: <b>{market.fmt_price(a['ticker'], q.price)}</b>{pct}"
    if reminder_no:
        text += f"\nFired at {market.fmt_price(a['ticker'], a['triggered_price'])}"
    if a["note"]:
        text += f"\n📝 {esc(a['note'])}"
    if a["username"]:
        text += f"\nSet by {esc(a['username'])}"
    text += f"\n\n<i>Repeats every {config.REMIND_INTERVAL}s until acknowledged — tap the button, reply \"stop\", or send /ack {a['id']}</i>"
    return text


async def send_alert(context: ContextTypes.DEFAULT_TYPE, a: dict, q: market.Quote | None, reminder_no: int):
    bot = context.bot
    # Remove the previous reminder so the chat doesn't fill up (the first alert message is kept)
    if reminder_no > 1 and a.get("last_message_id") and a["last_message_id"] != a.get("first_message_id"):
        try:
            await bot.delete_message(a["chat_id"], a["last_message_id"])
        except Exception:
            pass
    try:
        msg = await bot.send_message(a["chat_id"], alert_text(a, q, reminder_no),
                                     parse_mode=ParseMode.HTML, reply_markup=ack_keyboard(a["id"]))
        store.record_notification(a["id"], msg.message_id, is_reminder=reminder_no > 0)
    except Forbidden:
        log.warning("Bot removed from chat %s; clearing its alerts", a["chat_id"])
        store.clear_alerts(a["chat_id"])
    except Exception as exc:
        log.error("Failed to send alert %s: %s", a["id"], exc)


async def job_reminders(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    due = [a for a in store.list_firing()
           if now - (a.get("last_reminded") or 0) >= config.REMIND_INTERVAL - 2]
    if not due:
        return
    quotes = await market.get_quotes(sorted({a["ticker"] for a in due}))
    for a in due:
        if config.MAX_REMINDERS and a.get("reminders", 0) >= config.MAX_REMINDERS:
            done = store.acknowledge(a["chat_id"], a["id"], f"auto-stop after {config.MAX_REMINDERS} reminders")
            if done:
                await clear_buttons(context, done)
                try:
                    await context.bot.send_message(
                        a["chat_id"], f"⏹ Alert #{a['id']} stopped after {config.MAX_REMINDERS} reminders.")
                except Exception:
                    pass
            continue
        await send_alert(context, a, quotes.get(a["ticker"]), reminder_no=a.get("reminders", 0) + 1)


async def clear_buttons(context: ContextTypes.DEFAULT_TYPE, a: dict, skip_message_id: int | None = None):
    for mid in {a.get("first_message_id"), a.get("last_message_id")} - {None, skip_message_id}:
        try:
            await context.bot.edit_message_reply_markup(a["chat_id"], mid, reply_markup=None)
        except Exception:
            pass


async def can_ack(update: Update, context: ContextTypes.DEFAULT_TYPE, a: dict) -> bool:
    if not config.ADMIN_ONLY or a["user_id"] == update.effective_user.id:
        return True
    return await is_admin(update, context)


async def do_ack(update: Update, context: ContextTypes.DEFAULT_TYPE, alert_id: int) -> str:
    chat_id = update.effective_chat.id
    a = store.get_alert(chat_id, alert_id)
    if not a or a["status"] != "firing":
        return f"Alert #{alert_id} isn't ringing."
    if not await can_ack(update, context, a):
        return "Only the person who set this alert or an admin can acknowledge it."
    done = store.acknowledge(chat_id, alert_id, who(update))
    if not done:
        return f"Alert #{alert_id} was already acknowledged."
    await clear_buttons(context, done)
    return f"✅ Alert #{alert_id} ({esc(market.display(done['ticker']))}) acknowledged by {esc(who(update))}. Reminders stopped."


@guarded
async def cmd_ack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if context.args and context.args[0].lstrip("#").isdigit():
        return await reply(update, await do_ack(update, context, int(context.args[0].lstrip("#"))))
    firing = store.list_firing(chat_id)
    if not firing:
        return await reply(update, "No alerts are ringing right now.")
    if len(firing) == 1:
        return await reply(update, await do_ack(update, context, firing[0]["id"]))
    ids = ", ".join(f"#{a['id']} {esc(market.display(a['ticker']))}" for a in firing)
    await reply(update, f"{len(firing)} alerts are ringing: {ids}\nSend /ack ID for one, or /ackall for all.")


@guarded
async def cmd_ackall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    firing = store.list_firing(chat_id)
    if not firing:
        return await reply(update, "No alerts are ringing right now.")
    if config.ADMIN_ONLY and not await is_admin(update, context):
        return await reply(update, "Only group admins can acknowledge all alerts.")
    for a in store.acknowledge_all(chat_id, who(update)):
        await clear_buttons(context, a)
    await reply(update, f"✅ {len(firing)} alert(s) acknowledged by {esc(who(update))}. Reminders stopped.")


async def on_ack_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    alert_id = int(query.data.split(":", 1)[1])
    chat_id = update.effective_chat.id
    if config.ALLOWED_CHAT_IDS and chat_id not in config.ALLOWED_CHAT_IDS:
        return await query.answer()
    a = store.get_alert(chat_id, alert_id)
    if not a or a["status"] != "firing":
        await query.answer("Already acknowledged.")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    if not await can_ack(update, context, a):
        return await query.answer("Only the person who set this alert or an admin can acknowledge it.", show_alert=True)
    done = store.acknowledge(chat_id, alert_id, who(update))
    if not done:
        return await query.answer("Already acknowledged.")
    await query.answer("Acknowledged — reminders stopped ✅")
    try:
        await query.edit_message_text(
            query.message.text_html + f"\n\n✅ <b>Acknowledged by {esc(who(update))}</b>", parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await clear_buttons(context, done, skip_message_id=query.message.message_id)


STOP_WORDS = {"stop", "ack", "ok", "okay", "acknowledge", "acknowledged", "done", "seen"}


async def on_reply_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User replies 'stop' (or 'ack', 'ok', ...) to an alert message."""
    msg = update.effective_message
    if (msg.text or "").strip().lower().strip("!. ") not in STOP_WORDS:
        return
    if config.ALLOWED_CHAT_IDS and update.effective_chat.id not in config.ALLOWED_CHAT_IDS:
        return
    target = msg.reply_to_message
    if not target or not target.from_user or target.from_user.id != context.bot.id:
        return
    a = store.find_by_message(update.effective_chat.id, target.message_id)
    if a:
        await reply(update, await do_ack(update, context, a["id"]))


async def job_daily_summary(context: ContextTypes.DEFAULT_TYPE):
    if not market.is_trading_day():
        return
    indices = await market.get_quotes(["^NSEI", "^NSEBANK", "^BSESN"])
    header = "<b>🔔 Market closed — today's summary</b>\n" + "\n".join(quote_line(q) for q in indices.values())
    for chat_id in store.chats_with_watchlist():
        if config.ALLOWED_CHAT_IDS and chat_id not in config.ALLOWED_CHAT_IDS:
            continue
        tickers = store.watchlist(chat_id)
        quotes = await market.get_quotes(tickers)
        body = quote_table([quotes[t] for t in tickers if t in quotes])
        try:
            await context.bot.send_message(chat_id, f"{header}\n\n<b>Watchlist</b>\n{body}", parse_mode=ParseMode.HTML)
        except Exception as exc:
            log.error("Summary to %s failed: %s", chat_id, exc)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Something went wrong. Please try again.")
        except Exception:
            pass


COMMANDS = [
    ("alert", "Set price alert: /alert RELIANCE above 2900"),
    ("alertpct", "Set % move alert: /alertpct SBIN 3"),
    ("alerts", "List active alerts"),
    ("ack", "Acknowledge a ringing alert: /ack 12"),
    ("stop", "Same as /ack – stop reminders"),
    ("ackall", "Acknowledge all ringing alerts"),
    ("delete", "Delete an alert: /delete 12"),
    ("price", "Live quote: /price TCS"),
    ("market", "NIFTY / BANKNIFTY / SENSEX"),
    ("watch", "Add to watchlist"),
    ("unwatch", "Remove from watchlist"),
    ("watchlist", "Show watchlist prices"),
    ("history", "Recently triggered alerts"),
    ("clearalerts", "Delete all alerts (admins)"),
    ("help", "How to use this bot"),
]


async def post_init(app: Application):
    await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])


def main():
    if not config.BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN (get one from @BotFather).")
    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()

    handlers = {
        "start": cmd_help, "help": cmd_help, "id": cmd_id, "price": cmd_price, "market": cmd_market,
        "alert": cmd_alert, "alertpct": cmd_alertpct, "alerts": cmd_alerts, "delete": cmd_delete,
        "clearalerts": cmd_clearalerts, "history": cmd_history, "watch": cmd_watch,
        "unwatch": cmd_unwatch, "watchlist": cmd_watchlist,
        "ack": cmd_ack, "stop": cmd_ack, "ackall": cmd_ackall, "stopall": cmd_ackall,
    }
    for name, fn in handlers.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_ack_button, pattern=r"^ack:\d+$"))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, on_reply_stop))
    app.add_error_handler(on_error)

    app.job_queue.run_repeating(job_check_alerts, interval=config.CHECK_INTERVAL, first=10,
                                job_kwargs={"max_instances": 1, "coalesce": True})
    # Reminders run all the time (also after market close) until someone acknowledges
    app.job_queue.run_repeating(job_reminders, interval=min(15, config.REMIND_INTERVAL), first=15)
    if config.DAILY_SUMMARY:
        # PTB day numbering: 0=Sunday ... 6=Saturday → Mon–Fri
        app.job_queue.run_daily(job_daily_summary, time=dtime(15, 40, tzinfo=market.IST), days=(1, 2, 3, 4, 5))

    log.info("Bot started; checking alerts every %ss", config.CHECK_INTERVAL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
