"""Fuyao live opening auction snapshots with explicit source-time verification."""
import os
from datetime import datetime, time
from time import monotonic

import httpx

from backend.adapters.live import number
from backend.services.clock import SH

SOURCE = "同花顺扶摇"
URL = "https://fuyao.aicubes.cn/api/a-share/auction/snapshot"
DOCS = "https://fuyao.aicubes.cn/docs/api-reference/auction/"
START, END = time(9, 15), time(9, 25)


class FuyaoAuction:
    def __init__(self, client, *, cloud):
        self.client = client
        self.key = os.getenv("PULSE_FUYAO_API_KEY", "").strip()
        self.time_field = os.getenv("PULSE_FUYAO_QUOTE_TIME_FIELD", "").strip()
        self.visible = not cloud or os.getenv("PULSE_AUCTION_PUBLIC") == "1"
        self.retry_after = 0.0

    @property
    def permission(self):
        if not self.key:
            return "unconfigured"
        return "ready" if self.visible else "restricted"

    def result(self, status, *, observed_at=None, items=()):
        return {"source": SOURCE, "source_url": DOCS, "status": status,
                "provider_response_at": observed_at, "items": list(items)}

    async def snapshot(self, codes, current):
        if self.permission != "ready":
            return self.result(self.permission)
        current = current.astimezone(SH)
        if not START <= current.time() < END or not codes:
            return self.result("outside_window")
        if monotonic() < self.retry_after:
            return self.result("unavailable")
        symbols = {f"{code[2:]}.{code[:2].upper()}": code for code in codes}
        try:
            response = await self.client.get(URL, params={"thscodes": ",".join(symbols), "stage": "live"},
                headers={"X-api-key": self.key}, follow_redirects=False, timeout=4.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            self.retry_after = monotonic() + 30
            return self.result("unavailable")
        if not isinstance(payload, dict) or payload.get("code") != 0:
            self.retry_after = monotonic() + 30
            return self.result("unavailable")
        return self.parse(payload.get("data"), symbols, current)

    def parse(self, data, symbols, current):
        if not isinstance(data, dict) or data.get("data_status") != "ready" or data.get("auction_phase") != "live":
            return self.result("not_ready")
        observed = self.timestamp(data.get("timestamp"))
        received = observed.isoformat() if observed else None
        if (not observed or not START <= observed.time() < END
                or observed.date() != current.date() or abs((current-observed).total_seconds()) > 30):
            return self.result("stale", observed_at=received)
        if not self.time_field or self.time_field == "timestamp":
            return self.result("source_time_unverified", observed_at=received)
        rows = data.get("item")
        if not isinstance(rows, list):
            return self.result("unavailable", observed_at=received)
        items = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("thscode") not in symbols:
                continue
            stamp = self.timestamp(row.get(self.time_field, data.get(self.time_field)))
            if (not stamp or stamp.date() != current.date() or not START <= stamp.time() < END
                    or not -2 <= (observed - stamp).total_seconds() <= 30
                    or abs((current - stamp).total_seconds()) > 30):
                continue
            price, matched, unmatched = (number(row.get(k)) for k in
                                          ("auction_price", "auction_volume", "auction_unmatched"))
            if price is None or price <= 0 or matched is None or matched < 0 or unmatched is None:
                continue
            code = symbols[row["thscode"]]
            items[code] = {"code": code, "market_time": stamp.isoformat(), "indicative_price": price,
                           "matched_volume": matched, "unmatched_volume": unmatched}
        return self.result("verified_snapshot" if items else "source_time_unverified",
                           observed_at=received, items=items.values())

    @staticmethod
    def timestamp(value):
        try:
            if isinstance(value, (float, int)) and not isinstance(value, bool):
                return datetime.fromtimestamp(value / 1000, SH)
            if isinstance(value, str):
                stamp = datetime.fromisoformat(value)
                return stamp.astimezone(SH) if stamp.tzinfo is not None else stamp.replace(tzinfo=SH)
        except (ValueError, OverflowError, OSError):
            pass
        return None
