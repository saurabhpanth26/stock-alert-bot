"""Market data: symbol handling, quotes (via Yahoo Finance), market hours, alert rules."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import config

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

# Friendly names for indices -> Yahoo tickers
INDEX_ALIASES = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
    "NIFTYIT": "^CNXIT",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
}
_REVERSE_ALIASES = {}
for _name, _ticker in INDEX_ALIASES.items():
    _REVERSE_ALIASES.setdefault(_ticker, _name)


def normalize(symbol: str) -> str:
    """'reliance' -> 'RELIANCE.NS', 'tcs.bo' -> 'TCS.BO', 'nifty' -> '^NSEI'."""
    s = symbol.strip().upper().lstrip("$")
    if s in INDEX_ALIASES:
        return INDEX_ALIASES[s]
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return f"{s}.NS"  # default to NSE


def display(ticker: str) -> str:
    """'RELIANCE.NS' -> 'RELIANCE', 'TCS.BO' -> 'TCS (BSE)', '^NSEI' -> 'NIFTY'."""
    if ticker in _REVERSE_ALIASES:
        return _REVERSE_ALIASES[ticker]
    if ticker.endswith(".NS"):
        return ticker[:-3]
    if ticker.endswith(".BO"):
        return f"{ticker[:-3]} (BSE)"
    return ticker


def is_index(ticker: str) -> bool:
    return ticker in _REVERSE_ALIASES


def fmt_price(ticker: str, price: float) -> str:
    return f"{price:,.2f}" if is_index(ticker) else f"₹{price:,.2f}"


@dataclass
class Quote:
    ticker: str
    price: float
    prev_close: float | None

    @property
    def change(self) -> float | None:
        return None if not self.prev_close else self.price - self.prev_close

    @property
    def change_pct(self) -> float | None:
        return None if not self.prev_close else (self.price - self.prev_close) / self.prev_close * 100


def _valid(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x)) and x > 0


def _fetch_quote_sync(ticker: str) -> Quote | None:
    import yfinance as yf  # imported lazily so tests run without it

    try:
        t = yf.Ticker(ticker)
        price = prev = None
        try:
            fi = t.fast_info
            price = fi.last_price
            prev = fi.previous_close
        except Exception:  # fast_info can fail for some symbols
            pass
        if not _valid(price):
            hist = t.history(period="5d", interval="1d")
            if hist.empty:
                return None
            price = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else None
        return Quote(ticker, float(price), float(prev) if _valid(prev) else None)
    except Exception as exc:
        log.warning("Quote fetch failed for %s: %s", ticker, exc)
        return None


_cache: dict[str, tuple[float, Quote]] = {}
# Keep quotes fresh enough for every check (slightly under the interval), but never longer than 20s
_CACHE_TTL = min(20, config.CHECK_INTERVAL * 0.8)  # seconds
_sem = asyncio.Semaphore(5)


async def get_quote(ticker: str) -> Quote | None:
    hit = _cache.get(ticker)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    async with _sem:
        q = await asyncio.to_thread(_fetch_quote_sync, ticker)
    if q:
        _cache[ticker] = (time.monotonic(), q)
    return q


async def get_quotes(tickers: list[str]) -> dict[str, Quote]:
    results = await asyncio.gather(*(get_quote(t) for t in tickers))
    return {t: q for t, q in zip(tickers, results) if q}


def now_ist() -> datetime:
    return datetime.now(IST)


def is_trading_day(now: datetime | None = None) -> bool:
    now = now or now_ist()
    return now.weekday() < 5 and now.strftime("%Y-%m-%d") not in config.NSE_HOLIDAYS


def is_market_open(now: datetime | None = None) -> bool:
    now = now or now_ist()
    return is_trading_day(now) and MARKET_OPEN <= now.time() <= MARKET_CLOSE


# ---- Alert rules -----------------------------------------------------------
KIND_LABELS = {
    "above": "goes above",
    "below": "goes below",
    "pct_any": "moves ±{t}% on the day",
    "pct_up": "is up {t}% on the day",
    "pct_down": "is down {t}% on the day",
}


def describe(kind: str, target: float, ticker: str) -> str:
    if kind in ("above", "below"):
        return f"{KIND_LABELS[kind]} {fmt_price(ticker, target)}"
    return KIND_LABELS[kind].format(t=f"{target:g}")


def is_hit(kind: str, target: float, q: Quote) -> bool:
    if kind == "above":
        return q.price >= target
    if kind == "below":
        return q.price <= target
    pct = q.change_pct
    if pct is None:
        return False
    if kind == "pct_any":
        return abs(pct) >= target
    if kind == "pct_up":
        return pct >= target
    if kind == "pct_down":
        return pct <= -target
    return False
