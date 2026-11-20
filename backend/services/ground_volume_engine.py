"""Strict point-in-time ground-volume selection and daily-event backtest."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from backend.services.clock import SH, now

HORIZONS = (1, 3, 5)
BACKTEST_SESSIONS = 485
WARMUP = 120
TOP = 20
RISK_CAP_PCT = 20
UNLOCK_LIMIT_PCT = 5
FLOW_LIMIT = .03
SLOTS = {"14:45": ("14:59", "afternoon"), "15:15": ("16:00", "closed")}
HISTORY_KEY = "live:strategy:ground-volume:v1"
MAINBOARD = re.compile(r"(?:sh60|sz00)\d{4}\Z")
RISK_NAME = re.compile(r"^(?:\*?ST|退市|[NC](?=[\u4e00-\u9fff]))", re.IGNORECASE)
SIGNALS = ("MACD", "RSI(6)", "OBV", "小幅净流入")
NUMERIC_FIELDS = (
    "open", "high", "low", "close", "adj_factor", "volume", "amount", "turnover",
    "float_cap", "listed_sessions", "pct", "limit_up", "limit_down", "chip90_pct",
    "unlock_float_pct_30d", "main_net", "dark_net",
)


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(SH) if result.tzinfo else None
    except (ValueError, OverflowError):
        return None


def number(value):
    if type(value) in (int, float) and -1e15 < value < 1e15:
        return float(value)
    return np.nan


def iso_day(value):
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def rolling(series, size, operation):
    result = np.full(len(series), np.nan)
    if len(series) >= size:
        result[size - 1:] = operation(sliding_window_view(series, size), axis=1)
    return result


def lag(series, periods=1):
    result = np.full(len(series), np.nan)
    result[periods:] = series[:-periods]
    return result


def ratio(numerator, denominator):
    result = np.full(np.shape(numerator), np.nan)
    return np.divide(numerator, denominator, out=result, where=np.isfinite(denominator) & (denominator > 0))


def smooth(series, period, alpha):
    """Seed with an SMA, then apply the EMA or Wilder recurrence across valid runs."""
    result = np.full(len(series), np.nan)
    run = 0
    for i, value in enumerate(series):
        run = run + 1 if np.isfinite(value) else 0
        if run == period:
            result[i] = np.mean(series[i - period + 1:i + 1])
        elif run > period:
            result[i] = result[i - 1] + alpha * (value - result[i - 1])
    return result


def drawdown(series, size):
    def window_drawdown(windows, axis):
        peaks = np.maximum.accumulate(windows, axis=axis)
        return np.max(1 - ratio(windows, peaks), axis=axis) * 100
    return rolling(series, size, window_drawdown)


def obv_divergence(close, low, volume):
    output = np.zeros(len(close), dtype=bool)
    if len(close) < 10:
        return output
    delta = close - lag(close)
    signed = np.where(np.isfinite(delta) & np.isfinite(volume), np.sign(delta) * volume, 0)
    obv = np.cumsum(signed)
    windows = sliding_window_view(low, 10)
    positions = np.arange(len(windows))
    left = np.argmin(np.where(np.isfinite(windows[:, :5]), windows[:, :5], np.inf), axis=1)
    right = 5 + np.argmin(np.where(np.isfinite(windows[:, 5:]), windows[:, 5:], np.inf), axis=1)
    output[9:] = (
        np.all(np.isfinite(windows), axis=1)
        & np.all(np.isfinite(sliding_window_view(volume, 10)), axis=1)
        & (windows[positions, right] < windows[positions, left])
        & (obv[positions + right] > obv[positions + left])
    )
    return output


def arrays(rows, sessions, cutoff):
    """Reindex to exchange sessions so missing/suspended days cannot shorten T+n."""
    indexed = {}
    seen = set()
    allowed = set(sessions)
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid bar")
        day = row.get("day")
        if not iso_day(day) or day not in allowed or day in seen:
            raise ValueError("bar day is not unique or outside the calendar")
        seen.add(day)
        at = timestamp(row.get("known_at"))
        close_cutoff = datetime.combine(date.fromisoformat(day), time(15, 15, 59), SH)
        deadline = cutoff if day == sessions[-1] else close_cutoff
        if at and at.date().isoformat() == day and at <= deadline and (day == sessions[-1] or at.time() >= time(15)):
            indexed[day] = row

    ordered = [indexed.get(day, {}) for day in sessions]
    result = {key: np.array([number(row.get(key)) for row in ordered]) for key in NUMERIC_FIELDS}
    for flag in ("risk_warning", "suspended"):
        result[flag + "_known"] = np.array([type(row.get(flag)) is bool for row in ordered])
        result[flag] = np.array([row.get(flag) is True for row in ordered])
    profits = np.full((len(ordered), 2), np.nan)
    for i, row in enumerate(ordered):
        values = row.get("deducted_profit")
        if isinstance(values, list) and len(values) == 2:
            profits[i] = [number(value) for value in values]
    result["profit_known"] = np.all(np.isfinite(profits), axis=1)
    result["consecutive_loss"] = np.all(profits < 0, axis=1)
    result["quote_at"] = timestamp(ordered[-1].get("quote_at"))
    result["known_at"] = timestamp(ordered[-1].get("known_at"))
    return result


def market_filters(data):
    close, high, low, open_price, factor = (data[k] for k in ("close", "high", "low", "open", "adj_factor"))
    vol, turnover, amount = (data[k] for k in ("volume", "turnover", "amount"))
    valid_price = (
        (factor > 0) & (low > 0) & (high >= np.maximum(close, open_price))
        & (low <= np.minimum(close, open_price))
    )
    limit_up, limit_down = data["limit_up"], data["limit_down"]
    limits_known = (limit_up > limit_down) & (limit_down > 0)
    locked = (np.abs(high - low) < .005) & (
        (np.abs(close - limit_up) < .005) | (np.abs(close - limit_down) < .005)
    )
    chip, unlock, listing, pct = (data[k] for k in ("chip90_pct", "unlock_float_pct_30d", "listed_sessions", "pct"))
    prev_turnover = lag(turnover)
    main = ratio(data["main_net"], amount)
    dark = ratio(data["dark_net"], amount)
    covered = (
        valid_price & (vol >= 0) & (amount > 0) & (turnover >= 0) & (prev_turnover >= 0)
        & data["risk_warning_known"] & data["suspended_known"] & data["profit_known"] & limits_known
        & np.isfinite(listing) & np.isfinite(pct) & np.isfinite(data["float_cap"])
        & (chip >= 0) & (unlock >= 0) & (np.isfinite(main) | np.isfinite(dark))
    )
    tradeable = valid_price & (vol > 0) & limits_known & data["suspended_known"] & ~data["suspended"]
    base = (
        covered & tradeable & ~data["risk_warning"] & ~data["consecutive_loss"] & ~locked
        & (unlock < UNLOCK_LIMIT_PCT) & (listing >= 60) & (close < 50)
        & (data["float_cap"] >= 3e9) & (data["float_cap"] <= 5e10) & (prev_turnover <= 25)
        & (turnover >= .8) & (turnover <= 3.5) & (pct >= -1) & (pct <= 3)
        & (chip < 15) & (close - low >= .0099)
    )
    flow = np.fmax(np.where((main > 0) & (main <= FLOW_LIMIT), main, 0),
                  np.where((dark > 0) & (dark <= FLOW_LIMIT), dark, 0))
    return covered, base, tradeable, valid_price, flow


def features(data, filters=None):
    covered, base, tradeable, valid_price, flow = market_filters(data) if filters is None else filters
    close, high, low, open_price, factor = (data[k] for k in ("close", "high", "low", "open", "adj_factor"))
    price = np.where(valid_price, close * factor, np.nan)
    lows = np.where(valid_price, low * factor, np.nan)
    highs = np.where(valid_price, high * factor, np.nan)
    opens = np.where(valid_price, open_price * factor, np.nan)
    volume = np.where(data["volume"] >= 0, data["volume"], np.nan)
    prev60 = lag(rolling(volume, 60, np.max))
    vs_peak = ratio(volume, prev60)
    volume_ratio = ratio(rolling(volume, 5, np.mean), rolling(volume, 60, np.mean))
    percentile = np.full(len(volume), np.nan)
    if len(volume) > WARMUP:
        history = sliding_window_view(volume, WARMUP + 1)
        percentile[WARMUP:] = np.where(
            np.all(np.isfinite(history), axis=1),
            np.mean(history[:, :-1] <= history[:, -1:], axis=1) * 100,
            np.nan,
        )
    low_volume = ((percentile <= 10) | (vs_peak <= .25)) & (volume_ratio <= .50)
    ma5, ma10, ma60 = (rolling(price, period, np.mean) for period in (5, 10, 60))
    ma60_bias = (ratio(price, ma60) - 1) * 100
    avg_amplitude = rolling(ratio(highs - lows, lag(price)) * 100, 5, np.mean)

    dif = smooth(price, 12, 2/13) - smooth(price, 26, 2/27)
    dea = smooth(dif, 9, 2/10)
    hist = dif - dea
    macd = (
        ((lag(hist, 3) < lag(hist, 2)) & (lag(hist, 2) < lag(hist)) & (lag(hist) < hist) & (hist < 0))
        | ((lag(hist) <= 0) & (hist > 0) & (np.maximum(np.abs(dif), np.abs(dea)) <= price * .005))
    )
    delta = price - lag(price)
    gains = smooth(np.maximum(delta, 0), 6, 1/6)
    losses = smooth(np.maximum(-delta, 0), 6, 1/6)
    rsi = 100 * ratio(gains, gains + losses)
    rsi = np.where((gains == 0) & (losses == 0), 50, rsi)
    rsi_turn = (lag(rsi) <= 30) & (lag(rsi) <= lag(rsi, 2)) & (rsi > lag(rsi))
    obv = obv_divergence(price, lows, volume)
    momentum = np.array([macd, rsi_turn, obv, flow > 0])

    max_dd = drawdown(price, 20)
    support = lag(rolling(lows, 20, np.min))
    support_touches = np.zeros(len(price), dtype=int)
    if len(price) > 20:
        prior = sliding_window_view(lows, 20)[:-1]
        support_touches[20:] = np.sum(prior <= support[20:, None] * 1.015, axis=1)
    clear_support = (ratio(support, price) >= .95) & (support < price) & (support_touches >= 2)
    history_ready = (
        covered & (np.isfinite(percentile) | np.isfinite(vs_peak))
        & np.isfinite(volume_ratio) & np.isfinite(ma60_bias) & np.isfinite(avg_amplitude)
        & np.isfinite(max_dd) & np.isfinite(support) & np.isfinite(hist) & np.isfinite(rsi)
    )
    signal = (
        base & low_volume & (avg_amplitude < 3.5) & (price > ma5) & (price > ma10)
        & (ma60_bias >= -5) & (ma60_bias <= 8) & (momentum.sum(axis=0) >= 2)
        & (max_dd < 12) & clear_support
        & (price - support >= factor * .0099)
    )
    return {
        "signal": signal, "covered": covered, "history_ready": history_ready, "percentile": percentile, "vs_peak": vs_peak,
        "volume_ratio": volume_ratio, "max_dd": max_dd, "support": ratio(support, factor),
        "momentum": momentum, "flow": flow, "close": price, "open": opens,
        "can_buy": tradeable & (open_price < data["limit_up"] - .005),
        "can_sell": tradeable & (close > data["limit_down"] + .005),
        "can_mark": tradeable,
    }


def stats(returns):
    if not len(returns):
        return {"samples": 0, "win_rate": None, "mean_return": None, "payoff_ratio": None}
    wins, losses = returns[returns > 0], returns[returns < 0]
    return {
        "samples": len(returns), "win_rate": float(len(wins) / len(returns) * 100),
        "mean_return": float(np.mean(returns) * 100),
        "payoff_ratio": float(np.mean(wins) / -np.mean(losses)) if wins.size and losses.size else None,
    }


def backtest(f):
    """Study T+1 open entry; T+1 close is mark-to-market under the sell lock."""
    length = len(f["signal"])
    start = max(WARMUP, length - 1 - BACKTEST_SESSIONS)
    triggers = np.flatnonzero(f["signal"][start:-1]) + start
    metrics = {}
    final_trades = np.array([], dtype=int)
    for horizon in HORIZONS:
        # The target date can be the last completed day, never a 14:45 partial bar.
        points = triggers[triggers + horizon < length]
        entry, exit_ = points + 1, points + horizon
        valid = f["can_buy"][entry] & f["can_mark" if horizon == 1 else "can_sell"][exit_]
        points, entry, exit_ = points[valid], entry[valid], exit_[valid]
        returns = ratio(f["close"][exit_], f["open"][entry]) - 1
        valid_returns = np.isfinite(returns) & (returns > -1)
        metrics[str(horizon)] = stats(returns[valid_returns])
        if horizon == 5:
            final_trades = points[valid_returns]
    # Drawdown from one position at a time with close marks. Missing intermediate marks
    # are carried forward, so the recorded drawdown is limited to observed marks.
    equity, peak, max_dd, next_entry, dd_trades = 0.0, 0.0, 0.0, 0, 0
    for signal in final_trades:
        entry = signal + 1
        if entry <= next_entry:
            continue
        close_marks = f["close"][entry:signal + 6]
        values = ratio(close_marks, np.full(5, f["open"][entry]))
        for j, value in enumerate(values):
            if not f["can_mark"][entry + j] or not np.isfinite(value) or value <= 0:
                continue
            mark = equity + math.log(value)
            peak = max(peak, mark)
            max_dd = max(max_dd, 1 - math.exp(mark - peak))
        equity += math.log(values[-1])
        next_entry = signal + 5
        dd_trades += 1
    return {
        "trigger_count": len(triggers), "horizons": metrics,
        "max_drawdown": max_dd * 100 if dd_trades else None, "drawdown_trades": dd_trades,
    }


def ranking_score(history, flow, chip):
    clip = lambda value: min(1.0, max(0.0, float(value)))
    five = history["horizons"]["5"]
    win = 35 * clip(five["win_rate"] / 100)
    mean = clip(five["mean_return"] / 5)
    payoff = clip(five["payoff_ratio"] / 3) if five["payoff_ratio"] is not None else 0
    reward = 35 * (.6 * mean + .4 * payoff)
    funds = 20 * (.5 * clip(flow / FLOW_LIMIT) + .5 * clip(1 - chip / 15))
    defense = 10 * clip(1 - history["max_drawdown"] / 12)
    return {"win": win, "reward": reward, "funds_chips": funds, "defense": defense}


def candidate(record, sessions, as_of, slot):
    if not isinstance(record, dict):
        raise ValueError("invalid stock")
    code, name, sector = (record.get(key) for key in ("code", "name", "sector"))
    if not isinstance(code, str) or not MAINBOARD.fullmatch(code):
        return None, "non_mainboard"
    if not isinstance(name, str) or not name.strip() or not isinstance(sector, str) or not sector.strip():
        return None, "missing_identity"
    if RISK_NAME.match(name.strip()):
        return None, "risk_name"
    bars = record.get("bars")
    if not isinstance(bars, list) or len(bars) > len(sessions):
        raise ValueError("invalid bars")
    data = arrays(bars, sessions, as_of)
    quote = data["quote_at"]
    if quote is None or quote.date() != as_of.date() or quote > data["known_at"] or (
        as_of - quote > timedelta(seconds=120) if slot == "14:45" else quote.time() < time(15)
    ):
        return None, "stale_or_missing_quote"
    filters = market_filters(data)
    covered, basic_match, *_ = filters
    if not covered[-1]:
        return None, "missing_required_fields"
    if not basic_match[-1]:
        return None, "rules_not_met"
    f = features(data, filters)
    if not f["signal"][-1]:
        return None, "rules_not_met"
    # A close review can use today's final mark for prior signals; intraday cannot.
    historical = f.copy()
    if slot == "14:45":
        for k in ("can_mark", "can_sell", "can_buy"):
            historical[k] = f[k].copy()
            historical[k][-1] = False
    history = backtest(historical)
    comparable = f["history_ready"][max(WARMUP, len(sessions) - 1 - BACKTEST_SESSIONS):-1]
    history["covered_days"] = int(comparable.sum())
    history["window_days"] = len(comparable)
    five = history["horizons"]["5"]
    if not five["samples"]:
        return None, "no_mature_t5"
    price, low, pct, turnover, chip = (
        float(data[k][-1]) for k in ("close", "low", "pct", "turnover", "chip90_pct")
    )
    parts = ranking_score(history, f["flow"][-1], chip)
    stop = round(max(low, price * .97), 2)
    entry_low = min(price, round(max(f["support"][-1], price * .99, stop + .01), 2))
    tags = [tag for tag, present in zip(SIGNALS, f["momentum"][:, -1]) if present]
    return {
        "code": code, "name": name.strip()[:50], "sector": sector.strip()[:80],
        "price": price, "change_pct": pct, "turnover": turnover, "quote_at": quote.isoformat(),
        "volume_percentile": nullable(f["percentile"][-1]), "volume_vs_60d_peak": nullable(f["vs_peak"][-1]),
        "volume_5_60_ratio": nullable(f["volume_ratio"][-1]), "live_max_drawdown_20": float(f["max_dd"][-1]),
        "chip90_pct": chip, "history": history, "win_rate": five["win_rate"],
        "mean_return": five["mean_return"], "payoff_ratio": five["payoff_ratio"],
        "max_drawdown": history["max_drawdown"], "score": round(sum(parts.values()), 8),
        "score_parts": parts, "signals": tags, "support": round(float(f["support"][-1]), 2),
        "entry": [entry_low, price], "s1": round(price * 1.03, 2), "s2": round(price * 1.06, 2),
        "stop": stop, "max_position_pct": RISK_CAP_PCT,
    }, None


def nullable(value):
    return float(value) if np.isfinite(value) else None


def rank_candidates(rows):
    ordered = sorted(rows, key=lambda row: (-row["score"], -row["win_rate"], -row["mean_return"],
                                            row["max_drawdown"], row["code"]))
    return [{**row, "rank": i} for i, row in enumerate(ordered[:TOP], 1)]


def read_snapshot(root, day, slot, current):
    """Read one local, authorized batch. The manifest is published after the NDJSON."""
    folder = Path(root) / day
    stem = slot.replace(":", "")
    with (folder / (stem + ".json")).open("rb") as file:
        raw = file.read(65537)
        if len(raw) > 65536:
            raise ValueError("manifest too large")
        manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("invalid manifest schema")
    stamp = timestamp(manifest.get("as_of"))
    expected = datetime.combine(date.fromisoformat(day), time.fromisoformat(slot), SH)
    if not stamp or not expected <= stamp < expected + timedelta(minutes=1) or stamp > current:
        raise ValueError("snapshot does not match the requested minute")
    sessions, source, universe = (manifest.get(key) for key in ("sessions", "source", "universe_size"))
    if (not isinstance(sessions, list) or not WARMUP + BACKTEST_SESSIONS + 1 <= len(sessions) <= 800
        or not all(iso_day(d) for d in sessions) or sorted(set(sessions)) != sessions or sessions[-1] != day
        or not isinstance(source, str) or not source.strip() or type(universe) is not int or not 1 <= universe <= 6000):
        raise ValueError("invalid manifest coverage")
    rows, counters, seen = [], Counter(), set()
    with (folder / (stem + ".ndjson")).open("rb") as file:
        while line := file.readline(2_000_001):
            if len(line) > 2_000_000:
                raise ValueError("snapshot row too large")
            if not line.strip():
                continue
            record = json.loads(line)
            code = record.get("code") if isinstance(record, dict) else None
            if not isinstance(code, str) or code in seen or len(seen) == universe:
                raise ValueError("invalid or duplicate stock")
            seen.add(code)
            row, reason = candidate(record, sessions, stamp, slot)
            counters[reason or "matched"] += 1
            if row:
                rows.append(row)
    if len(seen) != universe:
        raise ValueError("incomplete snapshot")
    return {
        "day": day, "slot": slot, "as_of": stamp.isoformat(), "source": source.strip()[:120],
        "items": rank_candidates(rows), "scanned": universe, "qualified": len(rows),
        "diagnostics": dict(counters), "backtest_sessions": BACKTEST_SESSIONS,
    }


class GroundVolumeEngine:
    def __init__(self, market, root=None, loader=read_snapshot):
        self.market = market
        self.root = root
        self.loader = loader
        self.running = None
        self.attempts = {}

    def results(self, current, slot):
        current = current.astimezone(SH)
        day = current.date().isoformat()
        attempts = {key[1]: error for key, (_, error) in self.attempts.items() if key[0] == day}
        requested = SLOTS if slot == "latest" else (slot,)
        cache = [entry for s in requested for entry in self.market.cache.history(f"{HISTORY_KEY}:{s}", 1)]
        item = max(cache, key=lambda entry: entry[0])[1] if cache else None
        hm = current.strftime("%H:%M")
        due_slots = [s for s in requested if s <= hm]
        due = due_slots[-1] if due_slots else None
        fresh = bool(item and item["day"] == day and item["slot"] == due)
        if fresh:
            status = "ready"
        elif not self.root:
            status = "not_configured"
        elif self.running and self.running[0] == day and self.running[1] in requested:
            status = "running"
        elif due in attempts:
            status = "input_unavailable"
        else:
            status = "waiting"
        return {"status": status, "stale": bool(item and not fresh), "result": item,
                "expected_slot": due, "scheduled_slots": list(SLOTS), "error": attempts.get(due)}

    async def tick(self, current):
        if not self.root or self.running:
            return
        current = current.astimezone(SH)
        day = current.date().isoformat()
        hm = current.strftime("%H:%M")
        slot = next((s for s, (until, _) in SLOTS.items() if s <= hm <= until), None)
        if slot is None or current.weekday() >= 5:
            return
        key = (day, slot)
        stamp = f"{day}T{slot}:00+08:00"
        history_key = f"{HISTORY_KEY}:{slot}"
        if any(at == stamp for at, _ in self.market.cache.history(history_key, 1)):
            return
        if key in self.attempts and current < self.attempts[key][0]:
            return
        self.running = key
        try:
            clock = await self.market.clock("live")
            if clock.get("latest_day") != day or clock.get("phase") != SLOTS[slot][1]:
                return
            result = await asyncio.to_thread(self.loader, self.root, day, slot, current)
            self.market.cache.record(history_key, stamp, result, keep=25)
            self.attempts.pop(key, None)
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            self.attempts = {k: v for k, v in self.attempts.items() if k[0] == day}
            message = ("等待该时点的授权数据快照" if isinstance(exc, FileNotFoundError)
                       else "该时点的快照不可用或未通过完整性校验")
            self.attempts[key] = (current + timedelta(seconds=60), message)
            logging.getLogger("pulse").warning("Ground volume %s %s: %s", day, slot, type(exc).__name__)
        finally:
            self.running = None

    async def loop(self):
        while True:
            try:
                await self.tick(now())
            except Exception:
                logging.getLogger("pulse").exception("地量严选调度失败")
            await asyncio.sleep(15)
