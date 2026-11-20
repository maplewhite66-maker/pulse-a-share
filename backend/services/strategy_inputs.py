"""Small, read-only input boundary for licensed PULSE research snapshots."""
from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from datetime import timedelta
from pathlib import Path

from backend.services.ground_volume_engine import timestamp

LIVE_AGE = 120


def finite(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and -1e15 < value < 1e15 else None


def present_time(value, current, *, age=LIVE_AGE):
    parsed = timestamp(value)
    return parsed if parsed and parsed.date() == current.date() and timedelta(0) <= current - parsed <= timedelta(seconds=age) else None


def safe_text(value, limit=100):
    return value.strip()[:limit] if isinstance(value, str) else ""


class StrategyInputs:
    """Only consumers choose filenames. No routes accept raw file paths or uploads."""

    def __init__(self, root):
        self.root = Path(root).resolve() if root else None
        self.lock = asyncio.Lock()
        self.cache = OrderedDict()

    async def read(self, relative, max_bytes):
        if not self.root:
            return None, "授权数据目录未配置"
        async with self.lock:
            try:
                data = await asyncio.to_thread(self._read, relative, max_bytes)
            except FileNotFoundError:
                return None, "等待授权数据快照"
            except (OSError, ValueError, TypeError, RecursionError):
                return None, "授权数据未通过格式或容量校验"
        return data, None

    def _read(self, relative, max_bytes):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("path outside research data root")
        stat = path.stat()
        if not path.is_file() or not 0 < stat.st_size <= max_bytes:
            raise ValueError("invalid snapshot size")
        stamp = (stat.st_mtime_ns, stat.st_size)
        previous = self.cache.get(path)
        if previous and previous[0] == stamp:
            self.cache.move_to_end(path)
            return previous[1]
        with path.open("rb") as file:
            raw = file.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("snapshot too large")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("unsupported research schema")
        self.cache[path] = (stamp, data)
        self.cache.move_to_end(path)
        while len(self.cache) > 20:
            self.cache.popitem(last=False)
        return data
