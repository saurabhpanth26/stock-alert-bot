"""File-based storage (no database): alerts and watchlists are kept in one JSON file.

Alert lifecycle:
    active  -> watching the price
    firing  -> condition hit; reminders keep coming until someone acknowledges
    done    -> acknowledged / stopped (shown in /history)

The whole file is loaded into memory at startup and rewritten on every change.
Writes are atomic (write to a temp file, then rename), so a crash never leaves a half-written file.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

HISTORY_PER_CHAT = 50  # keep only the latest N finished alerts per chat


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: str):
        self.path = path
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        self.data = {"next_id": 1, "alerts": [], "watchlist": {}}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        for a in self.data["alerts"]:  # upgrade files from the older version
            if "status" not in a:
                a["status"] = "active" if a.pop("active", True) else "done"
            a.pop("active", None)

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def _find(self, alert_id: int, chat_id: int | None = None, statuses=("active", "firing")):
        return next((a for a in self.data["alerts"]
                     if a["id"] == alert_id and a["status"] in statuses
                     and (chat_id is None or a["chat_id"] == chat_id)), None)

    # ---- alerts ----
    def add_alert(self, chat_id, user_id, username, ticker, kind, target, note=None) -> int:
        with self._lock:
            alert_id = self.data["next_id"]
            self.data["next_id"] += 1
            self.data["alerts"].append({
                "id": alert_id, "chat_id": chat_id, "user_id": user_id, "username": username,
                "ticker": ticker, "kind": kind, "target": target, "note": note,
                "created_at": _now(), "status": "active",
                "triggered_at": None, "triggered_price": None,
                "reminders": 0, "first_message_id": None, "last_message_id": None, "last_reminded": None,
                "acked_by": None, "acked_at": None,
            })
            self._save()
            return alert_id

    def list_alerts(self, chat_id: int) -> list[dict]:
        """Alerts still watching the price."""
        rows = [a for a in self.data["alerts"] if a["chat_id"] == chat_id and a["status"] == "active"]
        return sorted(rows, key=lambda a: (a["ticker"], a["id"]))

    def list_firing(self, chat_id: int | None = None) -> list[dict]:
        """Alerts that fired and are waiting for an acknowledgement."""
        return [a for a in self.data["alerts"]
                if a["status"] == "firing" and (chat_id is None or a["chat_id"] == chat_id)]

    def all_active(self) -> list[dict]:
        return [a for a in self.data["alerts"] if a["status"] == "active"]

    def count_active(self, chat_id: int) -> int:
        return sum(1 for a in self.data["alerts"] if a["chat_id"] == chat_id and a["status"] != "done")

    def get_alert(self, chat_id: int, alert_id: int):
        return self._find(alert_id, chat_id)

    def find_by_message(self, chat_id: int, message_id: int):
        return next((a for a in self.data["alerts"] if a["chat_id"] == chat_id
                     and a["status"] == "firing"
                     and message_id in (a.get("last_message_id"), a.get("first_message_id"))), None)

    def delete_alert(self, chat_id: int, alert_id: int) -> bool:
        with self._lock:
            a = self._find(alert_id, chat_id)
            if not a:
                return False
            self.data["alerts"].remove(a)
            self._save()
            return True

    def clear_alerts(self, chat_id: int) -> int:
        with self._lock:
            before = len(self.data["alerts"])
            self.data["alerts"] = [a for a in self.data["alerts"]
                                   if not (a["chat_id"] == chat_id and a["status"] != "done")]
            removed = before - len(self.data["alerts"])
            if removed:
                self._save()
            return removed

    def mark_triggered(self, alert_id: int, price: float) -> bool:
        """active -> firing. Returns False if already handled (prevents duplicates)."""
        with self._lock:
            a = self._find(alert_id, statuses=("active",))
            if not a:
                return False
            a.update(status="firing", triggered_at=_now(), triggered_price=price,
                     reminders=0, last_reminded=time.time())
            self._save()
            return True

    def record_notification(self, alert_id: int, message_id: int, is_reminder: bool):
        with self._lock:
            a = self._find(alert_id, statuses=("firing",))
            if not a:
                return
            a["last_message_id"] = message_id
            a["last_reminded"] = time.time()
            if is_reminder:
                a["reminders"] = a.get("reminders", 0) + 1
            else:
                a["first_message_id"] = message_id
            self._save()

    def acknowledge(self, chat_id: int, alert_id: int, by: str) -> dict | None:
        """firing -> done. Returns the alert, or None if it wasn't ringing."""
        with self._lock:
            a = self._find(alert_id, chat_id, statuses=("firing",))
            if not a:
                return None
            a.update(status="done", acked_by=by, acked_at=_now())
            self._trim_history(chat_id)
            self._save()
            return a

    def acknowledge_all(self, chat_id: int, by: str) -> list[dict]:
        with self._lock:
            acked = []
            for a in self.data["alerts"]:
                if a["chat_id"] == chat_id and a["status"] == "firing":
                    a.update(status="done", acked_by=by, acked_at=_now())
                    acked.append(a)
            if acked:
                self._trim_history(chat_id)
                self._save()
            return acked

    def _trim_history(self, chat_id: int):
        done = sorted((a for a in self.data["alerts"] if a["chat_id"] == chat_id and a["status"] == "done"),
                      key=lambda a: a["triggered_at"] or "", reverse=True)
        old_ids = {a["id"] for a in done[HISTORY_PER_CHAT:]}
        if old_ids:
            self.data["alerts"] = [a for a in self.data["alerts"] if a["id"] not in old_ids]

    def history(self, chat_id: int, limit: int = 10) -> list[dict]:
        fired = [a for a in self.data["alerts"] if a["chat_id"] == chat_id and a["status"] in ("firing", "done")]
        return sorted(fired, key=lambda a: a["triggered_at"] or "", reverse=True)[:limit]

    # ---- watchlist ----
    def add_watch(self, chat_id: int, ticker: str, added_by: str | None) -> bool:
        with self._lock:
            items = self.data["watchlist"].setdefault(str(chat_id), [])
            if any(w["ticker"] == ticker for w in items):
                return False
            items.append({"ticker": ticker, "added_by": added_by, "added_at": _now()})
            self._save()
            return True

    def remove_watch(self, chat_id: int, ticker: str) -> bool:
        with self._lock:
            items = self.data["watchlist"].get(str(chat_id), [])
            kept = [w for w in items if w["ticker"] != ticker]
            if len(kept) == len(items):
                return False
            if kept:
                self.data["watchlist"][str(chat_id)] = kept
            else:
                self.data["watchlist"].pop(str(chat_id), None)
            self._save()
            return True

    def watchlist(self, chat_id: int) -> list[str]:
        return sorted(w["ticker"] for w in self.data["watchlist"].get(str(chat_id), []))

    def chats_with_watchlist(self) -> list[int]:
        return [int(c) for c, items in self.data["watchlist"].items() if items]
