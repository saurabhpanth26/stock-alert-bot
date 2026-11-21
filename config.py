"""Configuration loaded from environment variables (or a .env file)."""
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional
    pass


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _csv(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


# Required: token from @BotFather
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# JSON file where alerts and watchlists are saved
DB_PATH = os.getenv("DATA_FILE", "data/alerts.json")

# How often to check prices (seconds)
CHECK_INTERVAL = max(1, int(os.getenv("CHECK_INTERVAL_SECONDS", "1")))

# If true, only group admins can add/delete alerts in a group
ADMIN_ONLY = _bool("ADMIN_ONLY", False)

# If set, the bot only works in these chat IDs (use /id to find a chat's ID)
ALLOWED_CHAT_IDS = {int(x) for x in _csv("ALLOWED_CHAT_IDS")}

# Max active alerts per chat
MAX_ALERTS_PER_CHAT = int(os.getenv("MAX_ALERTS_PER_CHAT", "100"))

# Check alerts outside NSE hours too (useful for testing)
CHECK_OUTSIDE_MARKET_HOURS = _bool("CHECK_OUTSIDE_MARKET_HOURS", False)

# NSE trading holidays, YYYY-MM-DD, comma separated
NSE_HOLIDAYS = set(_csv("NSE_HOLIDAYS"))

# Post a watchlist summary after market close (15:40 IST, Mon-Fri)
DAILY_SUMMARY = _bool("DAILY_SUMMARY", True)

# After an alert fires, repeat the notification every N seconds until someone acknowledges it
REMIND_INTERVAL = int(os.getenv("REMIND_INTERVAL_SECONDS", "60"))

# Safety cap on reminders per alert (0 = unlimited, keep reminding until acknowledged)
MAX_REMINDERS = int(os.getenv("MAX_REMINDERS", "0"))
