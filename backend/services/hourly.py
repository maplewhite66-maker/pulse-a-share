"""Time-bounded paper-trade validation for the Pulse hourly opportunity screen."""
import asyncio
import logging
import math
from collections import Counter
from datetime import datetime, timedelta

from backend.adapters.live import HOURLY_BARS
from backend.services.clock import now
from backend.services.ideas import fresh
from backend.services.screener import RISK_NAME

# Entries are modelled at the next full 30-minute bar's close.
SLOTS = {"10:30": "11:00", "11:30": "13:30", "14:00": "14:30"}
BAR_TIMES = ("10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00")
REVIEW = "15:00"
KEY = "live:hourly:v1"
THRESHOLD = .80
MIN_TRADES = 30
LOOKBACK = 100
MAX_STOCKS = 20
SLIPPAGE = .001
FEE = .0005
ENTRY_UP = 1.005
ENTRY_DOWN = .99
MIN_COVERAGE = .90


def due(current):
    if current.weekday() < 5:
        for slot in (*SLOTS, REVIEW):
            start = current.replace(hour=int(slot[:2]), minute=int(slot[3:]), second=0, microsecond=0)
            if 15 <= (current - start).total_seconds() < 300:
                return slot
    return None


def by_day(rows):
    days = {}
    for row in rows:
        days.setdefault(row["t"][:10], {})[row["t"][11:16]] = row
    return days


def signal(day, previous, slot):
    keys = BAR_TIMES[:BAR_TIMES.index(slot) + 1]
    if not previous or "15:00" not in previous or any(key not in day for key in keys):
        return None
    bars = [day[key] for key in keys]
    volume = sum(row["volume"] for row in bars)
    if not volume or not bars[-2]["volume"]:
        return None
    last = bars[-1]
    price = last["close"]
    vwap = sum(row["amount"] for row in bars) / volume
    change = price / previous["15:00"]["close"] - 1
    if (not .003 <= change <= .05 or price <= max(bars[0]["open"], vwap, last["open"])
            or last["volume"] < bars[-2]["volume"] * .8):
        return None
    return {"signal_price": price, "signal_change": change, "vwap": vwap,
            "entry_floor": math.ceil(price * ENTRY_DOWN * 100 - 1e-8) / 100,
            "entry_cap": math.floor(price * ENTRY_UP * 100 + 1e-8) / 100}


def wilson(wins, count):
    if not count:
        return None
    z = 1.96
    p = wins / count
    return (p + z*z/(2*count) - z*math.sqrt(p*(1-p)/count + z*z/(4*count**2))) / (1 + z*z/count)


def evaluate(days, sessions, slot):
    """Use only completed pre-scan sessions and the exchange's next trading date."""
    entry_time = SLOTS[slot]
    measured = list(zip(sessions, sessions[1:], sessions[2:]))[-LOOKBACK:]
    known = signals = filled = unresolved = wins = 0
    returns = []
    for prior, day, following in measured:
        bars = days.get(day, {})
        previous = days.get(prior, {})
        expected = BAR_TIMES[:BAR_TIMES.index(entry_time)+1]
        if "15:00" not in previous or any(key not in bars for key in expected):
            continue
        known += 1
        setup = signal(bars, previous, slot)
        if setup is None:
            continue
        signals += 1
        entry = bars[entry_time]
        if (not entry["volume"] or entry["high"] == entry["low"]
                or not setup["entry_floor"] <= entry["close"]
                or round(entry["close"] * (1 + SLIPPAGE), 4) > setup["entry_cap"]):
            continue
        filled += 1
        exit_bar = days.get(following, {}).get("10:00")
        if not exit_bar or not exit_bar["volume"] or exit_bar["high"] == exit_bar["low"]:
            unresolved += 1
            continue
        entry_net = round(entry["close"] * (1 + SLIPPAGE), 4) * (1 + FEE)
        exit_net = round(exit_bar["close"] * (1 - SLIPPAGE), 4) * (1 - FEE)
        result = exit_net / entry_net - 1
        returns.append(result)
        wins += result > 0
    coverage = known / LOOKBACK
    exit_coverage = (filled - unresolved) / filled if filled else 0
    rate = wins / filled if filled else None
    return {"wins": wins, "samples": filled, "historical_signals": signals, "unresolved": unresolved,
            "win_rate": rate, "wilson_lower": wilson(wins, filled), "coverage": coverage,
            "covered_days": known, "mean_net": sum(returns) / len(returns) if returns else None,
            "eligible": (filled >= MIN_TRADES and coverage >= MIN_COVERAGE and exit_coverage >= MIN_COVERAGE
                         and wins * 5 > filled * 4)}


def allowed_stock(row):
    return (row["code"].startswith(("sh600", "sh601", "sh603", "sh605", "sz000", "sz001", "sz002", "sz003"))
            and bool(row.get("name")) and not RISK_NAME.search(row["name"].strip())
            and (row.get("amount") or 0) >= 1e8)


class HourlyScanner:
    def __init__(self, market):
        self.market = market
        self.running = None

    def history(self):
        return [row for _, row in self.market.cache.history(KEY, 28)]

    async def loop(self):
        while True:
            try:
                await self.tick(now())
            except Exception:
                logging.getLogger("pulse").exception("小时筛选失败")
            await asyncio.sleep(15)

    async def tick(self, current):
        slot = due(current)
        if not slot:
            return
        day = current.date().isoformat()
        stamp = current.replace(hour=int(slot[:2]), minute=int(slot[3:]), second=0, microsecond=0)
        key = stamp.isoformat()
        if any(row["slot"] == key for row in self.history()):
            return
        clock = await self.market.clock("live")
        phases = {"10:30": "morning", "11:30": "lunch", "14:00": "afternoon", REVIEW: "closed"}
        if clock["latest_day"] != day or clock["phase"] != phases[slot]:
            return
        self.running = key
        try:
            if slot == REVIEW:
                previous = [row for row in self.history() if row["slot"][:10] == day]
                result = {"status": "review", "rounds": len(previous), "qualified": sum(len(row["items"]) for row in previous)}
            else:
                result = await self.scan(clock, slot, stamp)
            self.market.cache.record(KEY, key, {"slot": key, "items": [], **result}, keep=28)
        finally:
            self.running = None

    async def trading_days(self, current):
        first = current.replace(day=1)
        months = [first]
        for _ in range(6):
            first = (first - timedelta(days=1)).replace(day=1)
            months.append(first)
        packets = await asyncio.gather(*(self.market.get("live", "calendar", 21600, m.year, m.month) for m in months))
        if any(not p["data"] for p in packets):
            return []
        return sorted(d for p in packets for d in p["data"] if d < current.date().isoformat())[-LOOKBACK-2:]

    async def scan(self, clock, slot, stamp):
        current = datetime.fromisoformat(clock["time"])
        start = stamp.replace(hour=int(SLOTS[slot][:2]), minute=int(SLOTS[slot][3:]))
        base = {"status": "unavailable", "pool_size": 0, "checked": 0, "entry_at": start.isoformat(),
                "history_source": HOURLY_BARS, "diagnostics": {}, "pool_source": None, "pool_time": None}
        pool, sessions = await asyncio.gather(self.market.get("live", "idea_pool", 25, "open"), self.trading_days(current))
        base.update(pool_source=pool["source_url"], pool_time=pool["market_time"], pool_size=len(pool["data"] or []))
        if not sessions or not pool["data"] or pool["is_stale"] or not fresh(pool["market_time"], clock["time"], 1800):
            return {**base, "error": pool["error"] or "榜单或历史交易日历不可用"}
        candidates = sorted((r for r in pool["data"] if allowed_stock(r) and fresh(r["market_time"], clock["time"], 1800)),
                            key=lambda r: (-r["amount"], r["code"]))[:MAX_STOCKS]
        if not candidates:
            return {**base, "error": "榜单没有带有有效时间的主板股票"}
        packet = await self.market.quotes("live", tuple(row["code"] for row in candidates), clock)
        quotes = {q["code"]: q for q in packet["data"] or []}
        counters = Counter()
        items = []
        checks = []
        for row in candidates:
            quote = quotes.get(row["code"])
            if (not quote or quote["is_stale"] or not allowed_stock(quote)
                    or not fresh(quote["market_time"], clock["time"], 180)):
                counters["quote_missing"] += 1
                continue
            history = await self.market.get("live", "hourly_bars", 45, row["code"])
            if history["is_stale"] or not history["data"]:
                counters["bars_missing"] += 1
                continue
            days = by_day(history["data"])
            today = days.get(stamp.date().isoformat(), {})
            if slot not in today:
                counters["bars_missing"] += 1
                continue
            counters["available"] += 1
            setup = signal(today, days.get(sessions[-1]), slot)
            if not setup or not setup["entry_floor"] <= quote["price"] <= setup["entry_cap"]:
                counters["no_setup"] += 1
                continue
            stats = evaluate(days, sessions, slot)
            observation = {"code": row["code"], "name": quote["name"], "quote": quote["price"],
                           "quote_time": quote["market_time"], **setup, **stats}
            if not stats["eligible"]:
                reason = ("insufficient" if stats["samples"] < MIN_TRADES or stats["coverage"] < MIN_COVERAGE
                          or stats["unresolved"] * 10 > stats["samples"] else "threshold")
                counters[reason] += 1
                checks.append({**observation, "reason": reason})
                continue
            items.append(observation)
        items.sort(key=lambda item: (-item["wilson_lower"], -item["samples"], item["code"]))
        return {**base, "items": items, "checks": checks, "checked": len(candidates), "diagnostics": dict(counters),
                "backtest_from": sessions[1], "backtest_to": sessions[-2],
                "status": "ready" if items else "empty" if counters["available"] else "unavailable",
                "error": None if counters["available"] else packet["error"] or "未取得当前时段的有效报价及分时数据"}
