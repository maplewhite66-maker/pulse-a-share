"""Explain observable seat/flow patterns; never identify a beneficial owner."""
from __future__ import annotations

import asyncio
from collections import Counter

from backend.services.ground_volume_engine import iso_day, timestamp
from backend.services.strategy_inputs import finite, present_time, safe_text

PATTERNS = {
    "institution": "机构披露与趋势形态", "active": "活跃营业部接力形态",
    "concentrated": "集中筹码与低波动形态", "fast": "高频双向与脉冲形态",
}
ACTIVE_SEATS = (("银河", "大连黄河路"), ("银河", "大连金马路"),
                ("招商", "福州六一中路"), ("国泰海通", "武汉紫阳东路"),
                ("国泰君安", "武汉紫阳东路"), ("", "呼家楼"))
CHANNELS = ("沪股通专用", "深股通专用")


def seat_label(name):
    if "机构专用" in name:
        return "institution", "机构专用"
    if any(channel in name for channel in CHANNELS):
        return "connect", "互联互通交易单元"
    if any(broker in name and location in name for broker, location in ACTIVE_SEATS):
        return "active", "活跃营业部（名称匹配）"
    if "东方财富" in name and "拉萨" in name:
        return "lhasa", "拉萨营业部（名称匹配）"
    if any(broker in name for broker in ("华鑫证券上海分公司", "中金公司上海分公司", "瑞银证券", "高盛", "摩根")):
        return "observed", "关注席位（交易主体未确认）"
    return "other", "营业部 / 交易单元"


def clean_disclosures(snapshot, current):
    sessions = snapshot.get("sessions")
    if (not isinstance(sessions, list) or not 1 <= len(sessions) <= 90 or
            not all(iso_day(d) for d in sessions) or sorted(set(sessions)) != sessions):
        return [], []
    sessions = [d for d in sessions if d <= current.date().isoformat()][-60:]
    reports, seen_days = [], set()
    raw_reports = snapshot.get("reports")
    if not isinstance(raw_reports, list):
        return sessions, []
    for raw in raw_reports[:90]:
        if not isinstance(raw, dict):
            continue
        day, published = raw.get("day"), timestamp(raw.get("published_at"))
        if (day not in sessions or day in seen_days or not published or
                published.date().isoformat() < day or published > current or raw.get("scope") != "daily"):
            continue
        turnover = finite(raw.get("turnover"))
        sides = {}
        for side in ("buy", "sell"):
            rows = raw.get(side)
            sides[side] = []
            if not isinstance(rows, list):
                continue
            for rank, row in enumerate(rows[:5], 1):
                if not isinstance(row, dict):
                    continue
                name = safe_text(row.get("seat"), 120)
                buy, sell = finite(row.get("buy")), finite(row.get("sell"))
                if not name or buy is None or sell is None or min(buy, sell) < 0:
                    continue
                kind, label = seat_label(name)
                sides[side].append({"rank": rank, "seat": name, "kind": kind, "label": label,
                                   "buy": buy, "sell": sell, "net": buy - sell,
                                   "turnover_share": (buy if side == "buy" else sell) / turnover * 100
                                   if turnover and turnover >= max(buy, sell) else None})
        if sides["buy"] or sides["sell"]:
            seen_days.add(day)
            reports.append({"day": day, "published_at": published.isoformat(),
                            "reason": safe_text(raw.get("reason")), **sides})
    return sessions, sorted(reports, key=lambda r: r["day"], reverse=True)


def pattern_scores(seats, features, history_seats=()):
    seats = list({(r["seat"], r["buy"], r["sell"]): r for r in seats}.values())
    net_by_kind = Counter()
    for row in seats:
        net_by_kind[row["kind"]] += row["net"]
    observations = {
        "institution": [(net_by_kind["institution"] > 0, 38, "机构专用披露净买"),
                        (features.get("connect_streak") is True, 20, "互联互通连续净买"),
                        (features.get("steady_trend") is True, 25, "沿均线平稳推升"),
                        (features.get("moderate_turnover") is True, 17, "换手温和")],
        "active": [(net_by_kind["active"] > 0, 35, "活跃营业部披露净买"),
                   (features.get("breakout_pulse") is True, 25, "盘中突破脉冲"),
                   (features.get("follow_through") is True, 15, "延续形态"),
                   (any(row["kind"] == "active" and row["net"] > 0 for row in history_seats), 15, "历史披露有活跃营业部记录"),
                   (features.get("positive_obi") is True, 10, "五档买侧挂量偏高")],
        "concentrated": [(features.get("chip_concentration") is True, 36, "筹码区间集中"),
                         (features.get("shrinking_volume") is True, 24, "成交量收缩"),
                         (features.get("fixed_size_cluster") is True, 18, "逐笔固定手数聚集"),
                         (features.get("shareholder_decline") is True, 12, "公开股东户数下降"),
                         (features.get("local_branch_overlap") is True, 10, "披露营业部与注册地地域相同")],
        "fast": [(any(r["buy"] > 0 and r["sell"] > 0 and abs(r["net"]) <= .12 * (r["buy"] + r["sell"]) for r in seats),
                  28, "部分披露席位双向成交相近"),
                 (features.get("matched_bursts") is True, 36, "逐笔双向脉冲密集"),
                 (features.get("sharp_reversal") is True, 26, "短时脉冲后回落"),
                 (sum(r["kind"] in ("observed", "lhasa") for r in seats) >= 2, 10, "若干关注营业部同时披露")],
    }
    results = []
    for key, checks in observations.items():
        evidence = [label for matched, _, label in checks if matched]
        score = sum(weight for matched, weight, _ in checks if matched)
        results.append({"key": key, "label": PATTERNS[key], "score": score,
                        "eligible": len(evidence) >= 2 and score >= 50, "evidence": evidence})
    results.sort(key=lambda item: (-item["score"], item["key"]))
    best = results[0]
    dominant = best["key"] if best["eligible"] and best["score"] - results[1]["score"] >= 10 else "uncertain"
    return {"dominant": dominant, "patterns": results,
            "institution_active": net_by_kind["institution"] > 0 and net_by_kind["active"] > 0,
            "active_sells_lhasa_buys": net_by_kind["active"] < 0 and net_by_kind["lhasa"] > 0}


def quote_obi(quote, current):
    if not quote or quote.get("is_stale") or quote.get("inactive") or not present_time(quote.get("market_time"), current):
        return None
    levels = quote.get("book") or []
    if len(levels) != 5:
        return None
    sizes = [(finite(row.get("bid_lots")), finite(row.get("ask_lots"))) for row in levels]
    if any(bid is None or ask is None or min(bid, ask) < 0 for bid, ask in sizes):
        return None
    bid, ask = map(sum, zip(*sizes))
    return (bid - ask) / (bid + ask) * 100 if bid + ask > 0 else None


def daily_flows(packet, snapshot, sessions, current):
    by_day = {}
    for raw in packet.get("data") or []:
        day = raw.get("t")
        if iso_day(day) and day <= current.date().isoformat():
            by_day[day] = {key: finite(raw.get(key)) for key in ("main_net", "large_net", "super_net")}
    raw_flows = snapshot.get("flows")
    for raw in (raw_flows[:90] if isinstance(raw_flows, list) else []):
        if not isinstance(raw, dict):
            continue
        day, at = raw.get("day"), timestamp(raw.get("known_at"))
        if not iso_day(day) or not at or at > current or at.date().isoformat() < day:
            continue
        row = by_day.setdefault(day, {})
        for key in ("dark_net", "northbound_net"):
            row[key] = finite(raw.get(key))
    days = sessions[-5:] if sessions else sorted(by_day)[-5:]
    return [{"day": day, **{key: by_day.get(day, {}).get(key) for key in
             ("main_net", "large_net", "super_net", "dark_net", "northbound_net")}} for day in reversed(days)]


class CapitalProfiler:
    def __init__(self, market, inputs):
        self.market, self.inputs = market, inputs

    async def report(self, code, current):
        (snapshot, error), quote_packet, flow_packet = await asyncio.gather(
            self.inputs.read(f"capital/{code}.json", 512_000),
            self.market.quotes("live", (code,), show_saved=True),
            self.market.get("live", "stock_flow", 90, code, "day", defer=True),
        )
        current = max(current, timestamp(quote_packet.get("fetched_at")) or current)
        snapshot = snapshot or {}
        sessions, reports = clean_disclosures(snapshot, current)
        day = current.date().isoformat()
        today = next((report for report in reports if report["day"] == day), None)
        features = snapshot.get("features")
        if not isinstance(features, dict) or not present_time(features.get("observed_at"), current):
            features = {}
        recent = [seat for r in reports if r["day"] in sessions[-20:] for seat in r["buy"] + r["sell"]]
        historical = [seat for r in reports if r["day"] != day for seat in r["buy"] + r["sell"]]
        inputs = (today["buy"] + today["sell"]) if today else recent
        quote = next((r for r in quote_packet.get("data") or [] if r["code"] == code), None)
        obi = quote_obi(quote, current)
        turnover = finite(quote.get("turnover")) if quote and not quote.get("is_stale") and present_time(
            quote.get("market_time"), current) else None
        current_evidence = {**features, "positive_obi": obi is not None and obi >= 20}
        if turnover is not None:
            current_evidence["moderate_turnover"] = .8 <= turnover <= 7
        model = pattern_scores(inputs, current_evidence, historical)
        # A fallback without today's disclosure must combine independent current observations.
        if not today and not (features or obi is not None or turnover is not None):
            model["dominant"] = "uncertain"
        flows = daily_flows(flow_packet, snapshot, sessions, current)
        totals = {}
        for key in ("main_net", "large_net", "super_net", "dark_net", "northbound_net"):
            values = [row[key] for row in flows if row[key] is not None]
            totals[key] = {"amount": sum(values) if values else None, "days": len(values)}
        return {"code": code, "as_of": current.isoformat(), **model,
                "evidence_state": "today_disclosure" if today else "historical_inference" if recent or features else "insufficient",
                "latest_report": reports[0] if reports else None, "today_report": today,
                "disclosed_days_20": sum(r["day"] in sessions[-20:] for r in reports),
                "seats_60_days": len(reports), "obi": obi, "flows": flows, "totals": totals,
                "source": safe_text(snapshot.get("source")), "input_status": error,
                "daily_flow_stale": bool(flow_packet.get("is_stale"))}
