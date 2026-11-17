"""Opening and pre-close buy candidates from observable, time-bounded rules."""
import asyncio
import logging
import math
from datetime import datetime

from backend.services.clock import now

from backend.services.screener import RISK_NAME

WINDOWS = {"open": ("09:30", "10:00"), "tail": ("14:45", "14:57")}


def window_open(session, clock):
    expected = "morning" if session == "open" else "afternoon"
    start, end = WINDOWS[session]
    return clock["phase"] == expected and start <= clock["time"][11:16] < end


def fresh(stamp, current, seconds):
    if not stamp or stamp[:10] != current[:10]:
        return False
    try:
        age = (datetime.fromisoformat(current) - datetime.fromisoformat(stamp)).total_seconds()
    except (ValueError, TypeError):
        return False
    return -10 <= age <= seconds


def shortlist(session, rows, current, demo=False):
    candidates = []
    for row in rows:
        pct, amount = row.get("pct"), row.get("amount")
        if not row.get("name") or RISK_NAME.search(row["name"].strip()):
            continue
        if not demo and not fresh(row.get("market_time"), current, 1800):
            continue
        if not demo and row["market_time"][11:16] < "09:30":
            continue
        if pct is None or not -.5 <= pct <= 8 or not amount or amount < 3e7:
            continue
        if session == "tail" and (row.get("main_net") or 0) <= 0:
            continue
        candidates.append(row)
    metric = "amount" if session == "open" else "main_net"
    return sorted(candidates, key=lambda row: (-(row.get(metric) or 0), row["code"]))[:32]


def prior_tail(rows, current):
    for stamp, row in rows:
        if stamp[:10] != current[:10] or not "14:45" <= stamp[11:16] < "14:57":
            continue
        age = (datetime.fromisoformat(current) - datetime.fromisoformat(stamp)).total_seconds()
        if 20 <= age <= 300 and (row.get("amount") or 0) > 0 and (row.get("price") or 0) > 0:
            return row
    return None


def rank_candidate(session, quote, context, current, previous=None, demo=False):
    """Return (candidate, needs_another_tail_sample)."""
    stamp = quote.get("market_time")
    if quote.get("is_stale") or not stamp:
        return None, False
    start, end = WINDOWS[session]
    if not demo and (not fresh(stamp, current, 90) or not start <= stamp[11:16] < end):
        return None, False
    pct, amount, ratio, opening, price = (quote.get(k) for k in ("pct", "amount", "vol_ratio", "open", "price"))
    floor, volume = (3e7, 1.3) if session == "open" else (1.5e8, 1.0)
    if (None in (pct, amount, ratio, opening, price) or not .8 <= pct <= 6.5
            or amount < floor or ratio < volume or opening <= 0 or price < opening
            or RISK_NAME.search(quote["name"].strip())):
        return None, False
    amount_delta = tail_move = None
    if session == "tail":
        if not previous:
            return None, True
        tail_move = (price / previous["price"] - 1) * 100
        amount_delta = amount - previous["amount"]
        if tail_move < .12 or amount_delta < 2e6:
            return None, False

    def part(label, score, weight):
        return {"label":label,"score":round(min(100, max(0, score)),1),"weight":weight}

    if session == "open":
        parts = [part("涨幅强度", pct/5*100, 30),part("量比", ratio/3*100, 30),
                 part("成交额", amount/5e8*100, 25),part("高于开盘价", (price/opening-1)*10000, 15)]
    else:
        parts = [part("尾盘价格变化", tail_move/.7*100, 40),part("尾盘成交额增量", amount_delta/3e7*100, 25),
                 part("主力净额", context["main_net"]/8e7*100, 20),part("量比", ratio/3*100, 15)]
    entry_cap = math.floor(price * 100.3 + 1e-8) / 100
    invalid_below = max(opening, previous["price"]) if session == "tail" else opening
    return {"entry_ref":price,"entry_cap":entry_cap,"invalid_below":invalid_below,"code":quote["code"], "name":quote["name"], "price":price, "pct":pct, "amount":amount,
            "vol_ratio":ratio, "open":opening, "main_net":context.get("main_net"), "tail_move":tail_move,
            "tail_amount":amount_delta, "market_time":stamp,"pool_time":context.get("market_time"),
            "baseline_time":previous.get("market_time") if previous else None,
            "score":round(sum(p["score"]*p["weight"]/100 for p in parts),1),"parts":parts}, False


class SessionJournal:
    """Keep the last valid ranking and final scan status for each trading session."""
    def __init__(self, market):
        self.market = market
        self.cache = market.cache
        self.day = None
        self.latest = {}

    def capture(self, day, session, packet):
        data = packet["data"]
        if not data or data["status"] in {"closed", "history", "calendar"}:
            return
        if self.day != day:
            self.day, self.latest = day, {}
            for kind in WINDOWS:
                previous = self.cache.history(f"live:session:{kind}", 1)
                if previous and previous[0][0] == day:
                    self.latest[kind] = previous[0][1].get("ranking")
        if data["status"] in {"ready", "empty", "sampling"} and not packet["is_stale"]:
            if data["status"] == "ready":
                self.latest[session] = {**data, "at":packet["market_time"], "source":packet["source"],
                                        "source_url":packet["source_url"]}
        record = {"day":day,"session":session,"status":data["status"],"ranking":self.latest.get(session)}
        history = self.cache.history(f"live:session:{session}", 1)
        if not history or history[0] != (day, record):
            self.cache.record(f"live:session:{session}", day, record, keep=40, replace=True)

    def history(self):
        all_rows = {}
        for session in WINDOWS:
            for day, row in self.cache.history(f"live:session:{session}", 40):
                all_rows.setdefault(day, {"day":day})[session] = row
        for day, row in self.cache.history("live:session:close", 40):
            all_rows.setdefault(day, {"day":day})["close"] = row
        return [all_rows[day] for day in sorted(all_rows, reverse=True)]

    async def tick(self, current):
        hm, day = current.strftime("%H:%M"), current.date().isoformat()
        if current.weekday() >= 5:
            return
        for session, (start, end) in WINDOWS.items():
            if start <= hm < end:
                await self.market.ideas("live", session)
                return
        if hm < "15:05" or hm >= "20:00":
            return
        previous = self.cache.history("live:session:close", 1)
        if previous and previous[0][0] == day:
            return
        clock = await self.market.clock("live")
        if clock["phase"] == "closed" and clock["latest_day"] == day:
            row = next((r for r in self.history() if r["day"] == day), {})
            statuses = {kind:row.get(kind, {}).get("status", "missed") for kind in WINDOWS}
            tail = (row.get("tail", {}).get("ranking") or {}).get("items", [])
            candidates = []
            if tail:
                packet = await self.market.quotes("live", tuple(r["code"] for r in tail), clock)
                by_code = {q["code"]:q for q in packet["data"] or []}
                for idea in tail:
                    q = by_code.get(idea["code"])
                    if not q or q.get("is_stale") or q.get("inactive") or not q["market_time"]:
                        continue
                    if not q["market_time"].startswith(day) or q["market_time"][11:16] < "15:00":
                        continue
                    if not idea.get("invalid_below") or not idea["invalid_below"] <= q["price"] <= idea["entry_cap"]:
                        continue
                    candidates.append({"code":q["code"],"name":q["name"],"price":q["price"],"pct":q["pct"],
                        "market_time":q["market_time"],"invalid_below":idea["invalid_below"],"score":idea["score"]})
                if not candidates and hm < "15:10":
                    return
            self.cache.record("live:session:close", day, {"at":current.isoformat(timespec="seconds"),
                "statuses":statuses,"checked":len(tail),"items":candidates[:5]}, keep=40)

    async def loop(self):
        while True:
            try:
                await self.tick(now())
            except Exception:
                logging.getLogger("pulse").exception("开收盘候选归档失败")
            await asyncio.sleep(10)
