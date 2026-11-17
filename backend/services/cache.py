import asyncio
import json
import logging
import sqlite3

from backend.adapters.live import Unavailable
from backend.services.clock import now


class Cache:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.execute("CREATE TABLE IF NOT EXISTS snapshots (key TEXT PRIMARY KEY, payload TEXT, saved TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS history (key TEXT, at TEXT, payload TEXT, PRIMARY KEY (key, at))")
        self.db.commit()
        self.entries = {}
        self.errors = {}
        self.tasks = {}

    def lookup(self, key):
        if key not in self.entries:
            row = self.db.execute("SELECT payload FROM snapshots WHERE key = ?", (key,)).fetchone()
            if row:
                self.entries[key] = (0, json.loads(row[0]))
        return self.entries.get(key)

    async def get(self, key, ttl, factory, *, defer=False, retry_delay=None):
        clock = asyncio.get_running_loop().time
        existing = self.lookup(key)
        failure = self.errors.get(key)
        if existing and clock() < existing[0] and not failure:
            return {**existing[1], "cached": True}
        if failure and clock() < failure[0]:
            return self.fallback(key, failure[1])
        if key not in self.tasks:
            self.tasks[key] = asyncio.create_task(self.refresh(key, ttl, factory, retry_delay))
        if defer:
            return self.fallback(key, "后台采集中")
        return await asyncio.shield(self.tasks[key])

    async def refresh(self, key, ttl, factory, retry_delay=None):
        try:
            raw = await factory()
            payload = {**raw, "fetched_at": raw.get("fetched_at") or now().isoformat(timespec="seconds"), "error": None,
                       "schema_version": "1.0", "is_stale": False, "cached": raw.get("cached", False),
                       "mode": key.split(":", 1)[0]}
            expires = asyncio.get_running_loop().time() + ttl
            previous = self.entries.get(key)
            if payload["mode"] == "live" and (previous is None or previous[1]["fetched_at"] != payload["fetched_at"]
                                              or previous[1]["data"] != payload["data"]):
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?)",
                                    (key, json.dumps(payload, ensure_ascii=False, allow_nan=False), payload["fetched_at"]))
                    self.db.execute("DELETE FROM snapshots WHERE key IN (SELECT key FROM snapshots ORDER BY saved DESC LIMIT -1 OFFSET 1200)")
            self.entries[key] = (expires, payload)
            if len(self.entries) > 1400:
                oldest = min(self.entries, key=lambda k: self.entries[k][0])
                self.entries.pop(oldest, None)
            self.errors.pop(key, None)
            return payload
        except (Unavailable, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, OSError) as exc:
            message = str(exc) if isinstance(exc, Unavailable) else "上游响应字段或格式异常"
            logging.getLogger("pulse").warning("%s · %s", key, message)
            self.errors[key] = (asyncio.get_running_loop().time() + (retry_delay if retry_delay is not None else min(90, max(25, ttl))), message)
            if len(self.errors) > 1400:
                self.errors.pop(min(self.errors, key=lambda k: self.errors[k][0]), None)
            return self.fallback(key, message)
        finally:
            self.tasks.pop(key, None)

    def fallback(self, key, error):
        existing = self.lookup(key)
        if existing:
            return {**existing[1], "error": error, "is_stale": True, "cached": True}
        return {"data": None, "source": None, "source_url": None, "fetched_at": None, "market_time": None,
                "error": error, "is_stale": True, "cached": False, "schema_version": "1.0", "mode": key.split(":", 1)[0]}

    def record(self, key, stamp, payload, keep=360, *, replace=False):
        with self.db:
            self.db.execute(("INSERT OR REPLACE" if replace else "INSERT OR IGNORE") + " INTO history VALUES (?, ?, ?)",
                            (key, stamp, json.dumps(payload, ensure_ascii=False, allow_nan=False)))
            self.db.execute("DELETE FROM history WHERE key = ? AND at IN "
                            "(SELECT at FROM history WHERE key = ? ORDER BY at DESC LIMIT -1 OFFSET ?)", (key, key, keep))

    def history(self, key, limit=120):
        return [(at, json.loads(data)) for at, data in self.db.execute(
            "SELECT at, payload FROM history WHERE key = ? ORDER BY at DESC LIMIT ?", (key, limit)).fetchall()]

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.db.close()
