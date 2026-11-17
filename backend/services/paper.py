"""Cash-only simulation using completed bars and subsequent order-book quotes."""
import asyncio
import json
import logging
import math
from datetime import date, datetime, timedelta

from backend.services.clock import now
from backend.services.hourly import FEE, SLIPPAGE, SLOTS, by_day

INITIAL = 1_000_000
WEEKLY_TARGET = 5
EXIT_AT = "10:00"
CLOSE_AT = "15:00"
STOP = -.02
TAKE = .03
STAMP_TAX = .0005
TRANSFER_FEE = .00001
TIME_SLOTS = (EXIT_AT, *SLOTS.values(), CLOSE_AT)
PROFILES = {
    "normal": {"total": .20, "validated": .06, "trial": .02, "positions": 3, "new_per_day": 2},
    "defensive": {"total": .10, "validated": .03, "trial": .01, "positions": 2, "new_per_day": 1},
    "paused": {"total": 0, "validated": 0, "trial": 0, "positions": 0, "new_per_day": 0},
}


def entry_bar(bars, day, slot):
    bar = by_day(bars).get(day, {}).get(slot)
    return bar if bar and bar["volume"] > 0 and bar["high"] > bar["low"] else None


def fees(value, sell=False):
    return round(max(5, value * FEE) + value * (TRANSFER_FEE + (STAMP_TAX if sell else 0)), 2)


def continuous(stamp):
    return "09:30" <= stamp[11:16] < "11:30" or "13:00" <= stamp[11:16] < "14:57"


def advanced(current, started):
    return current + timedelta(seconds=asyncio.get_running_loop().time() - started)


def quote_time(quote, current, after=None):
    if not quote or quote.get("is_stale") or quote.get("inactive"):
        return None
    stamp = quote.get("market_time")
    if (not isinstance(stamp, str) or stamp[:10] != current[:10] or not continuous(stamp)
            or not continuous(current)):
        return None
    try:
        at = datetime.fromisoformat(stamp)
        age = (datetime.fromisoformat(current) - at).total_seconds()
        if not 0 <= age <= 25 or (after is not None and at <= datetime.fromisoformat(after)):
            return None
    except (TypeError, ValueError):
        return None
    return stamp


def executable(quote, side, current, *, after=None):
    stamp = quote_time(quote, current, after)
    if stamp is None or not quote.get("book"):
        return None
    level = quote["book"][0]
    bid, ask, lots = level.get("bid"), level.get("ask"), level.get(side + "_lots")
    if (not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in (bid, ask, lots))
            or bid >= ask or (ask-bid)/bid > .006 or not float(lots).is_integer()):
        return None
    return level[side], int(lots) * 100, stamp


def week_key(day):
    week = date.fromisoformat(day).isocalendar()
    return (week.year, week.week)


def equity(account):
    return account["cash"] + sum(t["mark"] * t["shares"] for t in account["trades"] if not t["exit_at"])


def risk_after_close(account, drawdown):
    closed = sorted((t for t in account["trades"] if t["exit_at"]), key=lambda trade: trade["exit_at"])[-10:]
    rate = sum(t["net"] > 0 for t in closed) / len(closed) if closed else None
    if drawdown >= .03:
        return "paused", "收盘回撤达到 3%，暂停新开仓，继续管理已有持仓。", rate
    if drawdown >= .015 or (len(closed) == 10 and rate < .55):
        return "defensive", "回撤达到 1.5% 或最近十笔胜率低于 55%，下调持仓上限。", rate
    return "normal", "回撤和已验证的滚动胜率处于风险阈值内，使用常规仓位。", rate


class PaperAccount:
    def __init__(self, market):
        self.market = market
        self.db = market.cache.db
        self.last_tick_at = None
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS paper_account (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT)")

    def load(self):
        row = self.db.execute("SELECT state FROM paper_account WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {"cash": INITIAL, "trades": [], "reviews": [], "events": [],
                                             "profile": "normal", "peak": INITIAL, "max_drawdown": 0}

    def save(self, account):
        revision = account.get("_revision", 0)
        state = json.dumps({**account, "_revision":revision+1}, ensure_ascii=False, allow_nan=False)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT state FROM paper_account WHERE id=1").fetchone()
            persisted = json.loads(row[0]).get("_revision", 0) if row else 0
            if persisted != revision:
                return False
            self.db.execute("INSERT OR REPLACE INTO paper_account VALUES (1, ?)", (state,))
        account["_revision"] = revision + 1
        return True

    def status(self, current):
        a = self.load()
        a.pop("_revision", None)
        day = current.date().isoformat()
        trades = a["trades"]
        closed = [t for t in trades if t["exit_at"]]
        opened = [t for t in trades if week_key(t["entry_at"][:10]) == week_key(day)]
        week_closed = [t for t in closed if week_key(t["exit_at"][:10]) == week_key(day)]
        positions = [t for t in trades if not t["exit_at"]]
        value = equity(a)
        exposure = sum(t["mark"] * t["shares"] for t in positions)
        gains = sum(t["net"] for t in closed if t["net"] > 0)
        losses = -sum(t["net"] for t in closed if t["net"] < 0)
        return {**a, "initial_cash": INITIAL, "equity": round(value, 2), "week_target": WEEKLY_TARGET,
                "exposure": exposure/value if value else 0, "realized": round(gains-losses, 2),
                "unrealized": round(sum(t["mark"] * t["shares"] - t["cost"] for t in positions), 2),
                "profit_factor": gains/losses if losses else None,
                "sellable": sum(t["entry_at"][:10] < day for t in positions),
                "day_halted": a.get("halt_day") == day,
                "strategy": "2.4.1 · 盘口执行 / 风险退出",
                "weekly_opened": len(opened), "weekly_closed": len(week_closed),
                "wins": sum(t["net"] > 0 for t in closed), "closed": len(closed),
                "drawdown": max(0, 1 - value/a["peak"]), "limits": PROFILES[a["profile"]],
                "as_of": current.isoformat(timespec="seconds")}

    async def loop(self):
        logger = logging.getLogger("uvicorn.error")
        account = self.load()
        if "_revision" not in account:
            self.save(account)
        logger.info("Pulse paper strategy started: cash=%.2f, trades=%d",
                    account["cash"], len(account["trades"]))
        while True:
            try:
                current = now()
                await self.tick(current)
                self.last_tick_at = current.isoformat(timespec="seconds")
            except Exception:
                logging.getLogger("pulse").exception("模拟交易轮询失败")
            await asyncio.sleep(15)

    async def tick(self, current):
        if current.weekday() >= 5:
            return
        started = asyncio.get_running_loop().time()
        minute = current.hour * 60 + current.minute
        slot = next((k for k in TIME_SLOTS if 0 <= minute - (int(k[:2])*60 + int(k[3:])) < 5), None)
        if slot is None and 900 <= minute < 1200:
            slot = CLOSE_AT
        if slot is None:
            if continuous(current.isoformat()):
                await self.monitor(current)
            return
        stamp = current.replace(hour=int(slot[:2]), minute=int(slot[3:]), second=0, microsecond=0)
        elapsed = (current - stamp).total_seconds()
        if elapsed < 25:
            return
        day = current.date().isoformat()
        event_id = stamp.isoformat()
        a = self.load()
        if any(event["id"] == event_id for event in a["events"][-32:]):
            if slot != CLOSE_AT:
                await self.monitor(current, a)
            return
        clock = await self.market.clock("live")
        if clock["latest_day"] != day or clock["phase"] != ("closed" if slot == CLOSE_AT else
                "morning" if slot in (EXIT_AT, "11:00") else "afternoon"):
            return
        current = advanced(current, started)
        elapsed = (current - stamp).total_seconds()
        positions = [t for t in a["trades"] if not t["exit_at"]]
        pending = changes = False
        if slot == CLOSE_AT:
            packets = await asyncio.gather(*(self.market.get("live", "hourly_bars", 45, t["code"]) for t in positions))
            for trade, raw in zip(positions, packets):
                bar = by_day(raw["data"] or []).get(day, {}).get(slot) if not raw["is_stale"] else None
                if not bar or not bar["volume"] or bar["close"] <= 0:
                    pending = True
                    continue
                self.mark(trade, bar["close"], event_id)
                changes = True
        else:
            changes, pending = await self.reprice(a, current, clock, after=event_id)

        messages = []
        if pending and elapsed < 240:
            if changes:
                self.save(a)
            return
        if pending:
            messages.append("部分持仓没有可核对的分时成交，保留最后估值或等待下次退出核对。")
        if slot == CLOSE_AT:
            self.review(a, day, event_id, pending)
        elif slot in SLOTS.values():
            result = await self.open_one(a, day, slot, event_id, elapsed, pending, current, clock)
            if result is None:
                if changes:
                    self.save(a)
                return
            messages.append(result)
        else:
            messages.append("执行次交易日退出核对。")
        a["events"].append({"id": event_id, "messages": messages})
        a["events"] = a["events"][-300:]
        if self.save(a):
            logging.getLogger("uvicorn.error").info("Pulse paper window %s: %s", event_id, " ".join(messages))

    async def monitor(self, current, account=None):
        a = self.load() if account is None else account
        if not any(not t["exit_at"] for t in a["trades"]):
            return
        started = asyncio.get_running_loop().time()
        clock = await self.market.clock("live", wait=False)
        if clock["latest_day"] == current.date().isoformat() and clock["phase"] in {"morning", "afternoon"}:
            changed, _ = await self.reprice(a, advanced(current, started), clock)
            if changed:
                self.save(a)

    @staticmethod
    def mark(trade, price, stamp):
        trade["mark"], trade["mark_at"] = price, stamp
        result = price / trade["entry_price"] - 1
        trade["best_move"] = max(trade.get("best_move", 0), result)
        trade["worst_move"] = min(trade.get("worst_move", 0), result)

    async def reprice(self, a, current, clock, *, after=None):
        positions = [t for t in a["trades"] if not t["exit_at"]]
        if not positions:
            return False, False
        started = asyncio.get_running_loop().time()
        packet = await self.market.quotes("live", tuple(t["code"] for t in positions), clock)
        quotes = {q["code"]:q for q in packet["data"] or []}
        current_stamp = advanced(current, started).isoformat(timespec="seconds")
        changed = pending = False
        for trade in positions:
            q = quotes.get(trade["code"])
            at = quote_time(q, current_stamp, after)
            if at is None or at < trade["mark_at"]:
                pending = True
                continue
            if at > trade["mark_at"]:
                self.mark(trade, q["price"], at)
                changed = True
            if at[:10] <= trade["entry_at"][:10]:
                continue
            move = q["price"] / trade["entry_price"] - 1
            reason = ("盘中止损观察线触发。" if move <= STOP else "盘中止盈观察线触发。"
                      if move >= TAKE else "持仓到达次日 10:00 退出时点。" if at[11:16] >= EXIT_AT else None)
            if reason:
                fill = executable(q, "bid", current_stamp, after=after)
                if not fill or fill[1] < trade["shares"]:
                    pending = True
                    continue
                changed |= self.sell(a, trade, fill[0], fill[2], reason)
        day = current_stamp[:10]
        baseline = next((r["equity"] for r in reversed(a["reviews"]) if not r["partial"]), INITIAL)
        if not pending and equity(a) <= baseline * .99 and a.get("halt_day") != day:
            a["halt_day"] = day
            changed = True
        return changed, pending

    async def open_one(self, a, day, slot, stamp, elapsed, stale_positions, current=None, clock=None):
        started = asyncio.get_running_loop().time()
        profile = PROFILES[a["profile"]]
        opened_today = sum(t["entry_at"][:10] == day for t in a["trades"])
        positions = [t for t in a["trades"] if not t["exit_at"]]
        if stale_positions or a["profile"] == "paused":
            return "风控拦截新仓：持仓估值未确认或账户暂停。"
        if opened_today >= profile["new_per_day"] or len(positions) >= profile["positions"]:
            return "达到当日开仓或同时持仓上限。"
        value = equity(a)
        prior = next((r["equity"] for r in reversed(a["reviews"]) if not r["partial"]), INITIAL)
        if a.get("halt_day") == day or value <= prior * .99 or value <= a["peak"] * .97:
            a["halt_day"] = day
            return "触发日内损失或累计回撤上限，暂停新仓。"
        batch = next((r for r in self.market.hourly.history() if r.get("entry_at") == stamp), None)
        if batch is None:
            return None if elapsed < 240 else "未找到对应的小时扫描，跳过开仓。"
        opened_week = sum(week_key(t["entry_at"][:10]) == week_key(day) for t in a["trades"])
        validated = batch["items"]
        trials = []
        if opened_week < WEEKLY_TARGET:
            trials = sorted((r for r in batch.get("checks", []) if r["samples"] >= 5 and r["win_rate"] >= .50
                             and r["mean_net"] > 0 and r["coverage"] >= .90 and r["unresolved"] == 0),
                            key=lambda r: (-r["wilson_lower"], -r["samples"], r["code"]))
        candidates = [(r, "validated") for r in validated] + [(r, "trial") for r in trials]
        already_held = {t["code"] for t in positions}
        unique = {}
        for row, grade in candidates:
            if row["code"] not in already_held:
                unique.setdefault(row["code"], (row, grade))
        candidates = list(unique.values())[:5]
        if not candidates:
            return "无符合证据门槛的标的，保留现金。"
        remaining = max(0, value * profile["total"] - sum(t["mark"]*t["shares"] for t in positions))
        missing = False
        bars = await asyncio.gather(*(self.market.get("live", "hourly_bars", 45, row["code"]) for row, _ in candidates))
        confirmed = []
        for (row, grade), packet in zip(candidates, bars):
            bar = entry_bar(packet["data"] or [], day, slot) if not packet["is_stale"] else None
            if not bar:
                missing = True
            elif row["entry_floor"] <= bar["close"] <= row["entry_cap"]:
                confirmed.append((row, grade))
        if confirmed:
            quotes = await self.market.quotes("live", tuple(row["code"] for row, _ in confirmed), clock, refresh=True)
            by_code = {q["code"]:q for q in quotes["data"] or []}
            decision_time = advanced(current, started) if current is not None else now()
            if (decision_time - datetime.fromisoformat(stamp)).total_seconds() >= 300:
                return "入场核对窗口已结束，跳过开仓。"
            current_stamp = decision_time.isoformat(timespec="seconds")
            for row, grade in confirmed:
                quote = by_code.get(row["code"])
                fill = executable(quote, "ask", current_stamp, after=stamp)
                if fill is None:
                    missing = True
                    continue
                if self.buy(a, row, grade, fill, quote["price"], min(a["cash"], remaining, value * profile[grade]), batch["slot"]):
                    return f"已模拟开仓：{row['name']}（{'通过筛选' if grade == 'validated' else '小额试验'}）。"
        if missing and elapsed < 240:
            return None
        return "模拟入场未成交：价格越过区间、数据缺失、单价锁定或预算不足。"

    def buy(self, a, row, grade, fill, mark, budget, signal_at):
        ask, available, stamp = fill
        price = math.ceil(ask * (1 + SLIPPAGE) * 100 - 1e-8) / 100
        if not row["entry_floor"] <= price <= row["entry_cap"]:
            return False
        max_shares = min(available, (budget-5) / (price * (1 + FEE + TRANSFER_FEE)))
        shares = max(0, math.floor(max_shares / 100) * 100)
        if not shares:
            return False
        fee = fees(price * shares)
        cost = round(price * shares + fee, 2)
        if cost > budget or cost > a["cash"]:
            return False
        a["cash"] = round(a["cash"] - cost, 2)
        a["trades"].append({"id": stamp+":"+row["code"], "code": row["code"], "name": row["name"],
            "entry_at": stamp, "entry_price": price, "entry_fee": fee, "entry_bar": ask, "cost": cost,
            "shares": shares, "grade": grade, "profile": a["profile"], "signal_at": signal_at,
            "sample_wins": row["wins"], "sample_size": row["samples"], "sample_rate": row["win_rate"],
            "entry_floor": row["entry_floor"], "entry_cap": row["entry_cap"],
            "entry_reason": "完成的 30 分钟 K 线确认信号，按后续卖一盘口与可见数量模拟开仓。",
            "execution": "level1", "best_move": 0, "worst_move": 0,
            "mark": mark, "mark_at": stamp, "exit_at": None, "exit_price": None, "exit_fee": None,
            "exit_reason": None, "net": None, "net_rate": None})
        return True

    def sell(self, a, trade, bid, stamp, reason=None):
        if trade["exit_at"] or stamp[:10] <= trade["entry_at"][:10]:
            return False
        price = math.floor(bid * (1 - SLIPPAGE) * 100 + 1e-8) / 100
        if price <= 0:
            return False
        fee = fees(price * trade["shares"], sell=True)
        proceeds = round(price * trade["shares"] - fee, 2)
        net = round(proceeds - trade["cost"], 2)
        a["cash"] = round(a["cash"] + proceeds, 2)
        gross = bid / trade["entry_bar"] - 1
        finding = ("价差覆盖模拟费用和滑点。" if net > 0 else "原始价差为正，但不足以覆盖模拟费用和滑点。"
                   if gross > 0 else "原始退出价未高于入场价。")
        trade.update(exit_at=stamp, exit_price=price, exit_bar=bid, exit_fee=fee, net=net,
                     gross_rate=gross, finding=finding, net_rate=net/trade["cost"], exit_reason=reason,
                     exit_execution="level1")
        return True

    def review(self, a, day, stamp, partial):
        value = equity(a)
        previous = next((r["equity"] for r in reversed(a["reviews"]) if not r["partial"]), INITIAL)
        if not partial:
            a["peak"] = max(a["peak"], value)
        drawdown = max(0, 1 - value/a["peak"])
        if not partial:
            a["max_drawdown"] = max(a["max_drawdown"], drawdown)
        profile, reason, rate = risk_after_close(a, drawdown)
        if partial and profile == "normal":
            profile, reason = "defensive", "收盘估值不全，下次交易采用防守配置。"
        a["profile"] = profile
        opened = [t for t in a["trades"] if t["entry_at"][:10] == day]
        closed = [t for t in a["trades"] if t["exit_at"] and t["exit_at"][:10] == day]
        weekly = sum(week_key(t["entry_at"][:10]) == week_key(day) for t in a["trades"])
        a["reviews"].append({"day": day, "at": stamp, "equity": round(value, 2), "change": value/previous - 1,
            "drawdown": drawdown, "partial": partial, "opened": len(opened), "closed": len(closed),
            "wins": sum(t["net"] > 0 for t in closed), "realized": round(sum(t["net"] for t in closed), 2),
            "weekly": weekly, "weekly_gap": max(0, WEEKLY_TARGET - weekly), "profile": profile,
            "reason": reason, "rolling_win_rate": rate,
            "exposure": round(sum(t["mark"]*t["shares"] for t in a["trades"] if not t["exit_at"]) / value, 4) if value else 0,
            "costs": round(sum(t["entry_fee"] for t in opened) + sum(t["exit_fee"] for t in closed), 2),
            "halted": a.get("halt_day") == day,
            "events": [e for e in a["events"] if e["id"][:10] == day]})
