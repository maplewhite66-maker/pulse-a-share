"""Compose verified research signals and price-based guardrails. No orders."""
from __future__ import annotations

from backend.services.hot_money_simulator import base_stock, live_rows
from backend.services.strategy_inputs import finite, present_time


class AntiChasingRiskGuard:
    @staticmethod
    def reasons(row, current, proposed_position_pct):
        issues = []
        if not base_stock(row):
            issues.append("超出主板、价格或风险状态边界")
        position = finite(proposed_position_pct)
        if position is None or not 0 < position <= 30:
            issues.append("单票仓位必须大于 0 且不超过 30%")
        turnover, change = finite(row.get("previous_turnover")), finite(row.get("pct"))
        if turnover is None or not 0 <= turnover <= 35:
            issues.append("前一日换手超限或未核实")
        if change is None or (current.strftime("%H:%M") < "14:45" and change > 4):
            issues.append("涨跌幅未核实或 14:45 前追高超限")
        if not present_time(row.get("observed_at"), current):
            issues.append("行情时点未核实或已过期")
        return issues


def fusion_rows(sectors, ground, snapshot, current):
    if sectors.get("status") != "fresh" or not sectors.get("top_verified"):
        return [], "等待可核对的行业净流入前五"
    boards = [r for r in sectors["items"] if r["main_net"] > 0][:5]
    board_ranks = {row["code"]: (i, row["name"]) for i, row in enumerate(boards, 1)
                   if present_time(row.get("market_time"), current)}
    if len(board_ranks) != len(boards) or not board_ranks:
        return [], "板块排行中有过期记录"
    live = live_rows(snapshot, current)
    if not live:
        return [], "等待授权个股信号与可核对报价"
    defense = {}
    if ground and ground.get("day") == current.date().isoformat():
        defense = {row["code"]: row for row in ground.get("items", [])
                   if row["volume_percentile"] is not None and row["volume_percentile"] <= 10}
    rows = []
    minute = current.strftime("%H:%M")
    for row in live:
        board_id = row.get("board")
        if not isinstance(board_id, str) or board_id not in board_ranks:
            continue
        f = row["features"]
        if (f.get("capital_verified") is not True or f.get("high_position") is not False
                or f.get("fast_reversal_risk") is not False or f.get("distribution_risk") is not False):
            continue
        rank, name = board_ranks[board_id]
        d = defense.get(row["code"])
        price, vwap = row["price"], finite(row.get("vwap"))
        offense = ("14:50" <= minute < "14:57" and vwap is not None and vwap > 0
                   and 0 <= price / vwap - 1 <= .006
                   and all(f.get(k) is True for k in ("first_bearish_pullback", "shrinking_volume", "vwap_reclaimed")))
        if not offense and not d:
            continue
        tracks = (["进攻"] if offense else []) + (["防守"] if d else [])
        cap = min(20, d["max_position_pct"]) if d else 30
        if AntiChasingRiskGuard.reasons(row, current, cap):
            continue
        institutional = f.get("institution_active") is True
        concentrated = f.get("chip_concentration") is True and f.get("shrinking_volume") is True
        capital_score = 25 if institutional else 18 if concentrated else 10
        score = (6 - rank) * 6 + capital_score + (18 if offense else 0) + (d["score"] * .27 if d else 0)
        take, stop = (.03, -.025) if offense else (.023, -.018)
        rows.append({"code": row["code"], "name": row["name"], "price": price, "market_time": row["observed_at"],
                     "board": name, "board_rank": rank, "tracks": tracks, "score": round(score, 3),
                     "capital_evidence": "机构与活跃营业部同向" if institutional else "筹码集中与缩量" if concentrated else "通过资金形态排雷",
                     "ground_rank": d["rank"] if d else None, "max_position_pct": cap,
                     "s1": round(price * (1 + take), 2), "stop": round(price * (1 + stop), 2),
                     "take_pct": take * 100, "stop_pct": stop * 100})
    rows.sort(key=lambda row: (-row["score"], row["board_rank"], row["code"]))
    return [{**row, "rank": i} for i, row in enumerate(rows[:20], 1)], None


class PulseV4Strategy:
    def __init__(self, sectors, simulator, ground_volume):
        self.sectors, self.simulator, self.ground_volume = sectors, simulator, ground_volume

    async def report(self, current, continuous):
        if not continuous:
            return {"status": "closed", "items": [], "message": "休市，暂停实时信号"}
        self.sectors.request("industry", current)
        sector = self.sectors.payload("industry", current, True)
        ground = self.ground_volume.results(current, "latest")["result"]
        snapshot, error = await self.simulator.snapshot(current)
        rows, note = fusion_rows(sector, ground, snapshot, current)
        return {"status": "waiting" if note else "ready", "items": rows, "message": note or error,
                "as_of": current.isoformat(), "sector_market_time": sector.get("market_time"),
                "ground_slot": ground["slot"] if ground and ground.get("day") == current.date().isoformat() else None}
