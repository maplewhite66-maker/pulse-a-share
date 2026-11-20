"""Collect time-qualified board net flows and persist one observation per board/minute."""
from __future__ import annotations

import asyncio
import logging
import re

from backend.services.clock import now
from backend.services.ground_volume_engine import timestamp
from backend.services.strategy_inputs import finite, present_time, safe_text

BOARD = re.compile(r"BK\d{4}\Z")
HISTORY = "live:sector-flow:v1"


def trading_minute(hm):
    return "09:30" <= hm <= "11:30" or "13:00" <= hm <= "15:00"


def clean_board(row):
    code, label = safe_text(row.get("code"), 10), safe_text(row.get("name"), 50)
    amount, net = finite(row.get("amount")), finite(row.get("main_net"))
    if not BOARD.fullmatch(code) or not label or net is None:
        return None
    return {"code": code, "name": label, "main_net": net, "amount": amount if amount and amount > 0 else None,
            "pct": finite(row.get("pct_today", row.get("pct"))), "market_time": row.get("market_time"),
            "super_net": finite(row.get("super_net")), "large_net": finite(row.get("large_net")),
            "leader": safe_text(row.get("leader"), 30), "leader_code": safe_text(row.get("leader_code"), 12)}


def board_rows(packets, current):
    verified, observed, total = {}, {}, 0
    for packet in packets:
        data = packet.get("data") or {}
        source_total = data.get("total")
        total = max(total, source_total if type(source_total) is int and source_total > 0 else 0)
        for raw in data.get("items", []):
            if not isinstance(raw, dict):
                continue
            row = clean_board(raw)
            if not row:
                continue
            code = row["code"]
            stamp = timestamp(row["market_time"])
            if code not in observed or (stamp and (not observed[code]["market_time"] or row["market_time"] > observed[code]["market_time"])):
                observed[code] = row
            fresh = present_time(row["market_time"], current)
            if (fresh and trading_minute(fresh.strftime("%H:%M")) and not packet.get("is_stale")
                    and "push2delay" not in (packet.get("source_url") or "")):
                row = {**row, "market_time": fresh.isoformat()}
                if code not in verified or row["market_time"] >= verified[code]["market_time"]:
                    verified[code] = row
    rows = sorted((verified or observed).values(), key=lambda row: (-row["main_net"], row["code"]))
    amount = sum(row["amount"] or 0 for row in rows)
    for row in rows:
        row["amount_share"] = row["amount"] / amount * 100 if row["amount"] and amount else None
    first = sorted((row for row in observed.values() if row["main_net"] > 0),
                   key=lambda row: (-row["main_net"], row["code"]))[:5]
    top_verified = bool(first) and all(row["code"] in verified and row["main_net"] == verified[row["code"]]["main_net"]
                                      for row in first) and not packets[0].get("is_stale") and bool(packets[0].get("data"))
    return rows, bool(verified), total, top_verified


def shortlists(rows):
    return [r for r in rows if r["main_net"] > 0][:5] + sorted(
        (r for r in rows if r["main_net"] < 0), key=lambda r: (r["main_net"], r["code"]))[:3]


def annotations(points):
    marks = []
    for a, b, c in zip(points, points[1:], points[2:]):
        ta, tb, tc = map(lambda point: timestamp(point["t"]), (a, b, c))
        first, second = (tb - ta).total_seconds(), (tc - tb).total_seconds()
        if not (0 < first <= 180 and 0 < second <= 180):
            continue
        older = (b["net"] - a["net"]) / first * 60
        latest = (c["net"] - b["net"]) / second * 60
        if latest >= 10_000_000 and latest >= 1.8 * max(older, 0):
            marks.append({"t": c["t"], "net": c["net"], "label": "资金加速流入"})
        elif c["pct"] is not None and b["pct"] is not None and c["pct"] >= 2 and c["pct"] >= b["pct"] and latest <= -10_000_000:
            marks.append({"t": c["t"], "net": c["net"], "label": "高位资金背离"})
    return marks[-15:]


class SectorLiveFlow:
    def __init__(self, market, enabled=False, inputs=None):
        self.market = market
        self.enabled = enabled
        self.inputs = inputs
        self.running = {kind: asyncio.Lock() for kind in ("industry", "concept")}
        self.latest = {}
        self.last_minute = {}
        self.lines = {}
        self.requests = {}
        self.requested_at = {}

    def saved(self, kind):
        latest = self.latest.get(kind)
        if latest is not None:
            return latest
        cache = self.market.cache.history(f"{HISTORY}:latest:{kind}", 1)
        if cache:
            self.latest[kind] = cache[0][1]
            return self.latest[kind]
        return None

    async def collect(self, kind, current):
        async with self.running[kind]:
            authorized, source = await self.authorized_boards(kind, current)
            packets = [authorized] if authorized else await asyncio.gather(*(
                self.market.get("live", "sectors", 5, kind, "today", order, True) for order in ("inflow", "outflow")
            ))
            current = max(current, now())
            rows, verified, total, top_verified = board_rows(packets, current)
            prior = self.saved(kind)
            if not rows or (not verified and prior and prior["verified"]):
                return prior
            day = current.date().isoformat()
            if not verified:
                rows = [row for row in rows if not (timestamp(row["market_time"]) and
                        timestamp(row["market_time"]).date() != current.date())]
                if not rows:
                    return prior
            market_time = max((r["market_time"] for r in rows if r["market_time"]), default=None) if verified else None
            if prior and prior["verified"] and market_time and prior["market_time"] >= market_time:
                return prior
            data = {"day": day, "market_time": market_time, "captured_at": current.isoformat(), "verified": verified,
                    "source": source or "东方财富板块资金", "total": total, "top_verified": top_verified, "items": rows}
            self.latest[kind] = data
            if verified and (trading_minute(current.strftime("%H:%M")) or "15:00" <= current.strftime("%H:%M") <= "15:02"):
                minute = market_time[:16] + ":00+08:00"
                if self.last_minute.get(kind) != minute:
                    self.market.cache.record(f"{HISTORY}:latest:{kind}", minute, data, keep=1, replace=True)
                    compact = [[r["code"], r["market_time"][:16] + ":00+08:00", r["main_net"], r["pct"]] for r in rows]
                    self.market.cache.record(f"{HISTORY}:minutes:{kind}:{day}", minute, compact, keep=250, replace=True)
                    self.last_minute[kind] = minute
                    self.lines.pop(kind, None)
                    # Limit persisted daily partitions to the latest stored trading day.
                    with self.market.cache.db:
                        self.market.cache.db.execute("DELETE FROM history WHERE key LIKE ? AND key < ?",
                            (f"{HISTORY}:minutes:{kind}:%", f"{HISTORY}:minutes:{kind}:{day}"))
            return data

    async def authorized_boards(self, kind, current):
        if not self.inputs or not self.inputs.root:
            return None, None
        day = current.date().isoformat()
        snapshot, _ = await self.inputs.read(f"sectors/{day}.json", 512_000)
        if not snapshot or snapshot.get("day") != day:
            return None, None
        stamp = present_time(snapshot.get("as_of"), current)
        section = snapshot.get(kind)
        if not stamp or not isinstance(section, dict) or not isinstance(section.get("items"), list):
            return None, None
        rows = []
        for row in section["items"][:900]:
            if not isinstance(row, dict):
                continue
            quote = present_time(row.get("market_time"), current)
            if quote and quote <= stamp and trading_minute(quote.strftime("%H:%M")) and clean_board(row):
                rows.append(row)
        if not rows:
            return None, None
        total = section.get("total")
        if type(total) is not int or total != len(rows) or len({r.get("code") for r in rows if isinstance(r.get("code"), str)}) != total:
            # Partial licensed board lists cannot establish a whole-market top five.
            return None, None
        return {"data": {"items": rows, "total": total}, "is_stale": False}, safe_text(snapshot.get("source")) or "授权板块快照"

    def request(self, kind, current):
        existing = self.requests.get(kind)
        clock = asyncio.get_running_loop().time()
        if (existing and not existing.done()) or clock - self.requested_at.get(kind, -1e9) < 5:
            return
        self.requested_at[kind] = clock

        async def warm():
            try:
                await self.collect(kind, current)
            except Exception:
                logging.getLogger("pulse").exception("板块资金即时刷新失败")
        self.requests[kind] = asyncio.create_task(warm())

    async def close(self):
        tasks = list(self.requests.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def payload(self, kind, current, active):
        saved = self.saved(kind)
        if not saved:
            return {"status": "waiting", "items": [], "series": [], "covered": 0}
        recent = present_time(saved["market_time"], current)
        state = ("fresh" if recent and active else "archive" if saved["verified"] and not active
                 else "stale" if saved["verified"] else "unverified")
        rows = saved["items"]
        top_verified = saved.get("top_verified", False)
        if state == "fresh":
            fresh = [r for r in rows if present_time(r["market_time"], current)]
            fresh_codes = {r["code"] for r in fresh}
            top_verified = top_verified and all(r["code"] in fresh_codes for r in shortlists(rows) if r["main_net"] > 0)
            total_amount = sum(r["amount"] or 0 for r in fresh)
            rows = [{**r, "amount_share": r["amount"] / total_amount * 100 if r["amount"] and total_amount else None}
                    for r in fresh]
        selected = shortlists(rows)
        series = self.lines.get(kind)
        if series is None:
            series = {}
            for _, rows in reversed(self.market.cache.history(f'{HISTORY}:minutes:{kind}:{saved["day"]}', 250)):
                for code, t, net, pct in rows:
                    if t[:10] == saved["day"]:
                        series.setdefault(code, {})[t] = {"t": t, "net": net, "pct": pct}
            self.lines[kind] = series
        out = []
        for row in selected:
            points = sorted(series.get(row["code"], {}).values(), key=lambda r: r["t"])
            out.append({"code": row["code"], "name": row["name"], "points": points, "marks": annotations(points)})
        return {**saved, "items": rows, "top_verified": top_verified, "status": state, "covered": len(rows), "series": out}

    async def members(self, board, current, active):
        packet = None
        member_stamp = None
        if active and self.inputs and self.inputs.root:
            data, _ = await self.inputs.read(f"sector-members/{board}.json", 256_000)
            if data and data.get("board") == board and isinstance(data.get("items"), list):
                member_stamp = present_time(data.get("as_of"), current)
            if member_stamp:
                packet = {"data": data["items"][:300], "is_stale": False}
        if packet is None:
            packet = await self.market.get("live", "live_board_members", 25, board, defer=True)
        if "push2delay" in (packet.get("source_url") or "") or packet.get("is_stale"):
            return {"status": "waiting", "items": []}
        rows = []
        for r in packet["data"] or []:
            if not isinstance(r, dict) or not re.fullmatch(r"(?:sh|sz|bj)\d{6}", safe_text(r.get("code"))):
                continue
            at, net = timestamp(r.get("market_time")), finite(r.get("main_net"))
            if not at or net is None or net <= 0 or (member_stamp and at > member_stamp):
                continue
            if active and (not present_time(r["market_time"], current) or not trading_minute(at.strftime("%H:%M"))):
                continue
            if not active and (current.date() - at.date()).days not in range(8):
                continue
            rows.append({"code": r["code"], "name": safe_text(r.get("name"), 50), "price": finite(r.get("price")),
                         "pct": finite(r.get("pct")), "main_net": net, "market_time": at.isoformat()})
        if rows:
            latest_day = max(timestamp(r["market_time"]).date() for r in rows)
            rows = [r for r in rows if timestamp(r["market_time"]).date() == latest_day]
        return {"status": "fresh" if active and rows else "archive" if rows else "waiting",
                "items": sorted(rows, key=lambda r: (-r["main_net"], r["code"]))[:10]}

    async def loop(self):
        while self.enabled:
            try:
                current = now()
                minute = current.strftime("%H:%M")
                if (trading_minute(minute) or "15:00" <= minute <= "15:02") and current.weekday() < 5:
                    clock = await self.market.clock("live")
                    phase = clock["phase"]
                    if clock["latest_day"] == current.date().isoformat() and (
                        phase in {"morning", "afternoon", "close_auction"} or phase == "closed" and "15:00" <= minute <= "15:02"
                    ):
                        await asyncio.gather(*(self.collect(kind, current) for kind in self.running))
            except Exception:
                logging.getLogger("pulse").exception("板块资金采集失败")
            await asyncio.sleep(5)
