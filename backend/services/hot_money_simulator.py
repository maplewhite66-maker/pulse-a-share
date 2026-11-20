"""Rule-based research styles and isolated historical replay, with no order routing."""
from __future__ import annotations

import asyncio
import math
import re

import numpy as np

from backend.services.ground_volume_engine import iso_day, timestamp
from backend.services.paper import fees
from backend.services.strategy_inputs import finite, present_time, safe_text

STOCK = re.compile(r"(?:sh60|sz00)\d{4}\Z")
RISK_NAME = re.compile(r"^(?:\*?ST|S\*?ST|退市)", re.IGNORECASE)
STYLES = {
    "cycle": ("陈小群风格 · 情绪周期龙头", 3),
    "capacity": ("紫阳东路风格 · 容量核心", 2),
    "catalyst": ("六一中路风格 · 催化反包", 3),
    "pullback": ("作手新一风格 · 龙头分歧回踩", 3),
    "trend": ("机构游资合力趋势", 5),
}
SLIPPAGE = .001
ALLOCATION = .20
LOOKBACK = 485


def base_stock(row):
    code, price = row.get("code"), finite(row.get("close", row.get("price")))
    return (isinstance(code, str) and STOCK.fullmatch(code) and price is not None and 0 < price < 50
            and row.get("risk_warning") is False and row.get("suspended") is False
            and not RISK_NAME.search(safe_text(row.get("name"))))


def style_matches(row):
    if not base_stock(row):
        return []
    features = row.get("features")
    if not isinstance(features, dict) or features.get("high_position") is not False:
        return []
    on = lambda *names: all(features.get(name) is True for name in names)
    score = finite(row.get("amount"))
    rules = {
        "cycle": on("sector_leader", "cycle_launch", "early_limit_board", "disagreement_confirmed"),
        "capacity": score is not None and score > 1.5e9 and on("sector_leader", "institution_active"),
        "catalyst": on("catalyst_disclosed", "engulfing", "weak_to_strong"),
        "pullback": on("sector_leader", "first_bearish_pullback", "shrinking_volume", "ma5_reclaimed"),
        "trend": on("institution_buy_streak", "trend_up", "reclaimed_ma10_or_ma20", "active_flow_support"),
    }
    if features.get("cycle_retreat") is not False:
        rules["cycle"] = False
    return [key for key in STYLES if rules[key]]


def live_rows(snapshot, current):
    day = current.date().isoformat()
    if (not snapshot or snapshot.get("day") != day or not present_time(snapshot.get("as_of"), current)
            or not isinstance(snapshot.get("items"), list)):
        return []
    results, seen = [], set()
    as_of = timestamp(snapshot["as_of"])
    for row in snapshot["items"][:3000]:
        if not isinstance(row, dict) or row.get("suspended") is not False:
            continue
        code = row.get("code")
        if not isinstance(code, str) or not STOCK.fullmatch(code):
            continue
        stamp = present_time(row.get("observed_at"), current)
        features = row.get("features")
        price = finite(row.get("price"))
        if code in seen or not stamp or stamp > as_of or not price or not 0 < price < 1e6:
            continue
        seen.add(code)
        bid, ask = finite(row.get("bid")), finite(row.get("ask"))
        bid_lots, ask_lots = finite(row.get("bid_lots")), finite(row.get("ask_lots"))
        limit_up, limit_down = finite(row.get("limit_up")), finite(row.get("limit_down"))
        liquid = bool(bid and ask and 0 < bid < ask and (ask - bid) / bid <= .006)
        executable_buy = bool(liquid and ask_lots and ask_lots > 0 and ask_lots.is_integer()
                              and limit_up and ask * (1 + SLIPPAGE) < limit_up - .005)
        executable_sell = bool(liquid and bid_lots and bid_lots > 0 and bid_lots.is_integer()
                               and limit_down and bid * (1 - SLIPPAGE) > limit_down + .005)
        result = {**row, "observed_at": stamp.isoformat(), "price": price,
                  "features": features if isinstance(features, dict) else {},
                  "name": safe_text(row.get("name"), 40) or code.upper(), "bid": bid if executable_sell else None,
                  "ask": ask if executable_buy else None,
                  "buy_shares": min(10_000_000, int(ask_lots) * 100) if executable_buy else 0,
                  "sell_shares": min(10_000_000, int(bid_lots) * 100) if executable_sell else 0,
                  "styles": style_matches(row)}
        results.append(result)
    return results


def empty_metric(key):
    label, horizon = STYLES[key]
    return {"key": key, "label": label, "exit_horizon": horizon, "signals": 0, "trades": 0, "unresolved": 0,
            "win_rate": None, "annualized": None, "mean_return": None, "payoff": None,
            "max_drawdown": None, "next_day_premium": None}


def historical_features(index, prices, raw):
    """Use completed OHLCV sessions where present; preserve time-qualified external facts."""
    row = raw[index]
    configured = row.get("features")
    result = dict(configured) if isinstance(configured, dict) else {}
    prior = prices.get(index - 1)
    open_price, close = prices[index][:2]
    pct = finite(row.get("pct"))
    if prior:
        current_limit = finite(row.get("limit_up"))
        prior_limit = finite(raw[index - 1].get("limit_up"))
        at_limit = current_limit is not None and abs(row["close"] - current_limit) < .005
        prev_at_limit = prior_limit is not None and abs(raw[index - 1]["close"] - prior_limit) < .005
        earlier = raw.get(index - 2)
        earlier_limit = finite(earlier.get("limit_up")) if earlier else None
        if earlier_limit:
            result["early_limit_board"] = at_limit and not (
                prev_at_limit and abs(earlier["close"] - earlier_limit) < .005
            )
        result["engulfing"] = prior[1] < prior[0] and open_price <= prior[1] and close >= prior[0] and close > open_price
        if pct is not None:
            result["weak_to_strong"] = open_price <= prior[1] and close > prior[1] and pct > 0
    if all(i in prices for i in range(index - 19, index + 1)):
        recent = [prices[i][1] for i in range(index - 19, index + 1)]
        ma5, ma10, ma20 = (sum(recent[-n:]) / n for n in (5, 10, 20))
        result["high_position"] = result.get("high_position") is True or close > ma20 * 1.15
        result["trend_up"] = ma5 > ma10 > ma20 and close >= ma10
        raw_low, factor = finite(row.get("low")), finite(row.get("adj_factor"))
        if raw_low is not None and factor:
            low = raw_low * factor
            result["ma5_reclaimed"] = low <= ma5 * 1.005 and close >= ma5
            result["reclaimed_ma10_or_ma20"] = any(low <= ma * 1.005 and close >= ma for ma in (ma10, ma20))
            result["first_bearish_pullback"] = recent[-4] < recent[-3] < recent[-2] and close < recent[-2]
        volumes = [finite(raw[i].get("volume")) for i in range(index - 5, index + 1)]
        if all(v is not None and v > 0 for v in volumes):
            result["shrinking_volume"] = volumes[-1] <= .8 * sum(volumes[:-1]) / 5
        return result, ma5
    return result, None


def prepare_archive(payload, current):
    sessions = payload.get("sessions")
    if (not isinstance(sessions, list) or not LOOKBACK <= len(sessions) <= 540
            or not all(iso_day(day) for day in sessions) or sorted(set(sessions)) != sessions
            or not isinstance(payload.get("stocks"), list) or len(payload["stocks"]) > 6000):
        raise ValueError("bad backtest archive")
    # Outcomes must be completed; including today's partial bar would leak marks.
    cutoff = current.date().isoformat()
    sessions = [d for d in sessions if d < cutoff or (d == cutoff and current.strftime("%H:%M") >= "15:15")][-LOOKBACK:]
    if len(sessions) < LOOKBACK:
        raise ValueError("not enough completed sessions")
    indices = {day: i for i, day in enumerate(sessions)}
    matrix = {}
    events = {key: {} for key in STYLES}
    for stock in payload["stocks"]:
        if not isinstance(stock, dict):
            continue
        code, raw_bars = stock.get("code"), stock.get("bars")
        if not isinstance(code, str) or not STOCK.fullmatch(code) or not isinstance(raw_bars, list) or len(raw_bars) > 540:
            continue
        if code in matrix:
            raise ValueError("duplicate stock")
        rows, raw = {}, {}
        for row in raw_bars:
            if not isinstance(row, dict) or not isinstance(row.get("day"), str):
                continue
            day = row["day"]
            index = indices.get(day)
            at = timestamp(row.get("known_at"))
            if (index is None or not at or at > current or at.date().isoformat() != day
                    or at.strftime("%H:%M") < "15:00"):
                continue
            if index in rows:
                raise ValueError("duplicate bar")
            raw_open, raw_close, factor = (finite(row.get(k)) for k in ("open", "close", "adj_factor"))
            if not raw_open or not raw_close or not factor or min(raw_open, raw_close, factor) <= 0:
                continue
            limit_up, limit_down = finite(row.get("limit_up")), finite(row.get("limit_down"))
            tradable = row.get("suspended") is False and bool(limit_up and limit_down and limit_up > limit_down > 0)
            rows[index] = (raw_open * factor, raw_close * factor,
                           tradable and raw_open < limit_up - .005, tradable and raw_close > limit_down + .005,
                           row.get("suspended") is False, tradable and raw_open > limit_down + .005)
            raw[index] = row
        for index, row in raw.items():
            indicators, support = historical_features(index, rows, raw)
            rows[index] = (*rows[index], support)
            amount = finite(row.get("amount")) or 0
            for key in style_matches({**row, "code": code, "features": indicators}):
                events[key].setdefault(index, []).append((amount, code))
        matrix[code] = rows
    return sessions, matrix, events


def style_replay(key, matrix, events, days):
    output = empty_metric(key)
    horizon = output["exit_horizon"]
    output["signals"] = sum(map(len, events.values()))
    gains, premiums = [], []
    equity, peak, max_dd, last_exit = 1_000_000.0, 1_000_000.0, 0.0, -1
    for i, candidates in sorted(events.items()):
        buy, sell = i + 1, i + horizon
        if buy <= last_exit or sell >= days:
            continue
        _, symbol = min(candidates, key=lambda item: (-item[0], item[1]))
        prices = matrix[symbol]
        signal, exit_at_open = prices[i], key == "capacity"
        support = signal[6]
        if key == "pullback" and support:
            broken = next((n for n in range(buy + 1, sell) if prices.get(n) and prices[n][4] and prices[n][1] < support), None)
            if broken is not None:
                sell, exit_at_open = broken + 1, True
        entry = prices.get(buy)
        if not entry or not entry[2]:
            continue
        exit_ = prices.get(sell)
        if not exit_ or not exit_[5 if exit_at_open else 3]:
            # Stay invested through the first later eligible close; do not free cash
            # at a blocked exit or silently remove that trade from the position path.
            first = sell if exit_at_open else sell + 1
            available = next((n for n in range(first, days) if prices.get(n) and prices[n][3]), None)
            if available is None:
                exposure = equity * ALLOCATION
                cash = equity - exposure - fees(exposure)
                units = exposure / (entry[0] * (1 + SLIPPAGE))
                last_mark = cash + units * entry[0]
                for n in range(buy, days):
                    mark = prices.get(n)
                    if mark and mark[4]:
                        last_mark = cash + units * mark[1]
                    peak = max(peak, last_mark)
                    max_dd = max(max_dd, 1 - last_mark / peak)
                equity = last_mark
                output["unresolved"] += 1
                break
            sell, exit_at_open, exit_ = available, False, prices[available]
        purchase, sale = entry[0] * (1 + SLIPPAGE), exit_[0 if exit_at_open else 1] * (1 - SLIPPAGE)
        premium = entry[0] / signal[1] - 1
        simple = sale / purchase - 1
        if not -.8 <= simple <= 1 or not -.5 <= premium <= .5:
            continue
        exposure = equity * ALLOCATION
        entry_fee = fees(exposure)
        cash = equity - exposure - entry_fee
        units = exposure / purchase
        for at in range(buy, sell + (0 if exit_at_open else 1)):
            row = prices.get(at)
            if row and row[4]:
                marked = cash + units * row[1]
                peak = max(peak, marked)
                max_dd = max(max_dd, 1 - marked / peak)
        net = units * sale - fees(units * sale)
        equity = cash + net
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
        gains.append((net - exposure - entry_fee) / (exposure + entry_fee))
        premiums.append(premium)
        last_exit = sell
    if gains:
        values = np.array(gains)
        wins, losses = values[values > 0], values[values < 0]
        output.update(trades=len(gains), win_rate=float(100 * len(wins) / len(values)),
                      mean_return=float(100 * values.mean()), payoff=float(wins.mean() / -losses.mean())
                      if wins.size and losses.size else None,
                      next_day_premium=float(100 * np.mean(premiums)))
    if gains or output["unresolved"]:
        output.update(annualized=math.expm1(math.log(equity / 1_000_000) * 252 / days) * 100,
                      max_drawdown=max_dd * 100)
    return output


def backtest_archive(payload, current):
    sessions, matrix, events = prepare_archive(payload, current)
    if not matrix:
        raise ValueError("empty backtest stock universe")
    results = [style_replay(key, matrix, events[key], len(sessions)) for key in STYLES]
    results.sort(key=lambda row: (row["win_rate"] is None, -(row["win_rate"] or 0), row["key"]))
    return {"sessions": len(sessions), "from_day": sessions[0], "to_day": sessions[-1],
            "universe": len(matrix), "source": safe_text(payload.get("source")), "results": results}


def price_signal(row, current):
    pct, previous_turnover = finite(row.get("pct")), finite(row.get("previous_turnover"))
    return {
        "code": row["code"], "name": row["name"], "price": row["price"], "styles": row["styles"],
        "market_time": row["observed_at"], "bid": row["bid"], "ask": row["ask"],
        "pct": pct, "previous_turnover": previous_turnover,
        "buy_shares": row["buy_shares"], "sell_shares": row["sell_shares"],
        "entry_allowed": (base_stock(row) and row["features"].get("high_position") is False
                          and previous_turnover is not None and 0 <= previous_turnover <= 35
                          and pct is not None and (current.strftime("%H:%M") >= "14:45" or pct <= 4)),
        "amount": finite(row.get("amount")), "day": current.date().isoformat(),
    }


class HotMoneySimulator:
    def __init__(self, inputs):
        self.inputs = inputs
        self.lock = asyncio.Lock()
        self.previous = None
        self.cache = None
        self.cache_day = None

    async def snapshot(self, current):
        return await self.inputs.read(f"live/{current.date().isoformat()}.json", 4_000_000)

    async def report(self, current, continuous, requested=()):
        (bundle, history_error), (snapshot, live_error) = await asyncio.gather(
            self.inputs.read("backtest.json", 24_000_000), self.snapshot(current),
        )
        async with self.lock:
            as_of = (current.date().isoformat(), current.strftime("%H:%M") >= "15:15")
            if bundle is not self.previous or as_of != self.cache_day:
                self.previous, self.cache_day, self.cache = bundle, as_of, None
                if bundle:
                    try:
                        self.cache = await asyncio.to_thread(backtest_archive, bundle, current)
                    except (ValueError, TypeError, OverflowError):
                        pass
        if bundle and self.cache is None:
            history_error = "历史输入格式不符或不足 485 个完整会话"
        rows = live_rows(snapshot, current) if continuous else []
        rows.sort(key=lambda row: (row["code"] not in requested, not row["styles"],
                                   -(finite(row.get("amount")) or 0), row["code"]))
        return {"status": "available" if self.cache else "waiting", "backtest": self.cache,
                "styles": [empty_metric(key) for key in STYLES], "quotes": [price_signal(row, current) for row in rows[:150]],
                "market_open": continuous, "day": current.date().isoformat(), "generated_at": current.isoformat(),
                "history_note": history_error, "live_note": (live_error or "等待符合时效的授权报价") if not rows else None}
