import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.services.cache import Cache
from backend.services.capital_profiler import clean_disclosures, daily_flows, pattern_scores, quote_obi, seat_label
from backend.services.hot_money_simulator import backtest_archive, live_rows, price_signal, style_matches, style_replay
from backend.services.pulse_v4_strategy import AntiChasingRiskGuard, fusion_rows
from backend.services.sector_live_flow import SectorLiveFlow, annotations, board_rows
from backend.services.strategy_inputs import StrategyInputs, present_time


def at(value="14:51:00"):
    return datetime.fromisoformat("2026-11-20T" + value + "+08:00")


def board(code="BK1000", net=20e6, stamp=None, amount=100):
    return {"code": code, "name": code, "main_net": net, "market_time": stamp or at().isoformat(),
            "amount": amount, "pct": -1.4, "super_net": 3e6, "large_net": 2e6}


def packet(rows, stale=False, delay=False):
    return {"data": {"items": rows, "total": 12}, "is_stale": stale,
            "source_url": "https://push2delay.eastmoney.com" if delay else "https://push2.eastmoney.com"}


def live_stock(stamp=None):
    stamp = stamp or at().isoformat()
    return {"code": "sh600123", "name": "合成任务股票", "price": 10., "amount": 2e9,
            "observed_at": stamp, "board": "BK1000", "risk_warning": False, "suspended": False,
            "pct": 2., "previous_turnover": 22., "bid": 9.99, "ask": 10.01, "bid_lots": 400, "ask_lots": 300,
            "limit_up": 11., "limit_down": 9., "vwap": 9.97,
            "features": {"sector_leader": True, "institution_active": True, "high_position": False,
                         "capital_verified": True, "first_bearish_pullback": True, "shrinking_volume": True,
                         "vwap_reclaimed": True, "distribution_risk": False, "fast_reversal_risk": False}}


def live_bundle(row):
    stamp = row["observed_at"]
    return {"as_of": stamp, "day": stamp[:10], "items": [row]}


class BoardTests(unittest.IsolatedAsyncioTestCase):
    def test_excludes_delay_future_old_and_normalizes_share(self):
        stamp = at().isoformat()
        fresh, stale, future = board(stamp=stamp), board("BK1001", stamp=at("14:47:00").isoformat()), board("BK1002", stamp=at("14:52:00").isoformat())
        rows, verified, total, top = board_rows([packet([fresh, stale, future]), packet([board("BK1003")], delay=True)], at())
        self.assertEqual([r["code"] for r in rows], [fresh["code"]])
        self.assertEqual(rows[0]["amount_share"], 100)
        self.assertTrue(verified)
        self.assertFalse(top)
        self.assertEqual(total, 12)
        self.assertIsNone(present_time(at("14:48:59").isoformat(), at()))
        self.assertIsNotNone(present_time(at("14:49:00").isoformat(), at()))

    def test_annotations_require_connected_windows(self):
        def point(value, net, pct):
            return {"t": at(value).isoformat(), "net": net, "pct": pct}
        compact = [point("14:50:00", 0, 1), point("14:51:00", 1e6, 2), point("14:52:00", 25e6, 2.2),
                   point("14:53:00", 13e6, 2.3)]
        self.assertEqual([m["label"] for m in annotations(compact)], ["资金加速流入", "高位资金背离"])
        self.assertEqual(annotations([compact[0], compact[1], point("15:00:00", 60e6, 3)]), [])

    async def test_cache_survives_restart_and_staleness_is_explicit(self):
        cache = Cache(":memory:")
        provider = SimpleNamespace(cache=cache, get=AsyncMock(side_effect=[packet([board()]), packet([board("BK1001", -10e6)])]))
        engine = SectorLiveFlow(provider)
        try:
            with patch("backend.services.sector_live_flow.now", return_value=at()):
                await engine.collect("industry", at())
            self.assertTrue(engine.payload("industry", at(), True)["top_verified"])
            self.assertEqual(engine.payload("industry", at("14:53:01"), True)["status"], "stale")
            again = SectorLiveFlow(provider)
            self.assertEqual(again.payload("industry", at("16:00:00"), False)["status"], "archive")
            self.assertEqual(len(again.payload("industry", at(), True)["series"]), 2)
        finally:
            await engine.close()
            await cache.close()

    async def test_authorized_complete_fallback_keeps_source_and_members(self):
        current = at()
        day, stamp = current.date().isoformat(), current.isoformat()
        cache = Cache(":memory:")
        market = SimpleNamespace(cache=cache, get=AsyncMock())
        try:
            with tempfile.TemporaryDirectory() as root:
                folder = Path(root)
                (folder / "sectors").mkdir()
                (folder / "sector-members").mkdir()
                (folder / "sectors" / (day + ".json")).write_text(json.dumps({
                    "schema_version": 1, "source": "合成授权测试", "day": day, "as_of": stamp,
                    "industry": {"total": 2, "items": [board(), board("BK1001", -1e6)]}
                }), encoding="utf-8")
                (folder / "sector-members" / "BK1000.json").write_text(json.dumps({
                    "schema_version": 1, "board": "BK1000", "as_of": stamp, "items": [
                        {"code": "sh600123", "name": "合成股票", "price": 10, "main_net": 1e6, "market_time": stamp}
                    ]
                }), encoding="utf-8")
                engine = SectorLiveFlow(market, inputs=StrategyInputs(root))
                with patch("backend.services.sector_live_flow.now", return_value=current):
                    result = await engine.collect("industry", current)
                self.assertTrue(result["top_verified"])
                self.assertEqual(result["source"], "合成授权测试")
                self.assertEqual((await engine.members("BK1000", current, True))["items"][0]["code"], "sh600123")
                market.get.assert_not_awaited()
        finally:
            await cache.close()


class ProfileTests(unittest.TestCase):
    def test_labels_never_infer_person_and_evidence_needs_more_than_one_group(self):
        kind, label = seat_label("中国银河证券股份有限公司大连黄河路证券营业部")
        self.assertEqual(kind, "active")
        self.assertNotIn("陈小群", label)
        self.assertEqual(seat_label("东方财富证券拉萨东环路")[0], "lhasa")
        seats = [{"seat": "机构专用", "buy": 10, "sell": 1, "net": 9, "kind": "institution"}]
        self.assertEqual(pattern_scores(seats, {})["dominant"], "uncertain")
        self.assertEqual(pattern_scores(seats, {"steady_trend": True})["dominant"], "institution")
        self.assertEqual(pattern_scores([], {"matched_bursts": True})["dominant"], "uncertain")

    def test_no_future_disclosure_or_unknown_turnover(self):
        daily = {"day": "2026-11-20", "scope": "daily", "published_at": at("16:10:00").isoformat(),
                 "buy": [{"seat": "机构专用", "buy": 10, "sell": 1}]}
        snapshot = {"sessions": ["2026-11-20"], "reports": [daily]}
        self.assertFalse(clean_disclosures(snapshot, at())[1])
        report = clean_disclosures(snapshot, at("16:11:00"))[1][0]
        self.assertEqual(report["buy"][0]["net"], 9)
        self.assertIsNone(report["buy"][0]["turnover_share"])
        level = {"bid_lots": 0, "ask_lots": 0}
        self.assertIsNone(quote_obi({"market_time": at().isoformat(), "book": [level] * 5}, at()))
        self.assertEqual(quote_obi({"market_time": at().isoformat(), "book": [{"bid_lots": 3, "ask_lots": 1}] * 5}, at()), 50)
        flows = daily_flows({"data": [{"t":"2026-11-20","main_net":0}]}, {}, ["2026-11-20"], at("16:11:00"))
        self.assertEqual(flows[0]["main_net"], 0)
        self.assertIsNone(flows[0]["northbound_net"])


def sessions(count=485):
    d, rows = date(2024, 1, 2), []
    while len(rows) < count:
        if d.weekday() < 5:
            rows.append(d.isoformat())
        d += timedelta(days=1)
    return rows


class StrategyTests(unittest.TestCase):
    def test_all_five_style_rules_and_retreat_override(self):
        row = live_stock()
        features = row["features"]
        for key in ("cycle_launch", "early_limit_board", "disagreement_confirmed", "catalyst_disclosed",
                    "engulfing", "weak_to_strong", "ma5_reclaimed", "institution_buy_streak", "trend_up",
                    "reclaimed_ma10_or_ma20", "active_flow_support"):
            features[key] = True
        features["cycle_retreat"] = False
        self.assertEqual(style_matches(row), ["cycle", "capacity", "catalyst", "pullback", "trend"])
        features["cycle_retreat"] = True
        self.assertNotIn("cycle", style_matches(row))
        row["name"] = "*ST合成测试"
        self.assertEqual(style_matches(row), [])

    def test_capacity_rule_limits_and_execution_requirements(self):
        row = live_stock()
        self.assertEqual(style_matches(row), ["capacity"])
        quotes = live_rows(live_bundle(row), at())
        self.assertEqual(price_signal(quotes[0], at())["buy_shares"], 30_000)
        row["ask"] = row["limit_up"]
        self.assertIsNone(live_rows(live_bundle(row), at())[0]["ask"])
        self.assertEqual(live_rows(live_bundle(row), at("14:54:01")), [])

    def test_two_year_replay_timing_censoring_and_stable_rank(self):
        dates = sessions()
        final = datetime.fromisoformat(dates[-1] + "T16:00:00+08:00")
        bars = []
        for i, day in enumerate(dates):
            row = live_stock()
            row.update(day=day, known_at=day+"T15:10:00+08:00", open=10., close=10.5 if i in (2, 6) else 10., adj_factor=1)
            row["features"] = row["features"] if i in (0, 4, 7, 8, 484) else {"high_position": False}
            bars.append(row)
        # Signal at session seven cannot enter at a limit-up open.
        bars[8]["open"] = bars[8]["limit_up"]
        bars[2]["open"] = bars[6]["open"] = 10.5
        replay = backtest_archive({"sessions": dates, "stocks": [{"code": "sh600123", "bars":bars}]}, final)
        self.assertEqual(replay["sessions"], 485)
        self.assertEqual(replay["results"][0]["key"], "capacity")
        self.assertEqual(replay["results"][0]["signals"], 5)
        self.assertEqual(replay["results"][0]["trades"], 3)
        self.assertGreater(replay["results"][0]["annualized"], 0)
        self.assertIsNone(replay["results"][1]["win_rate"])
        json.dumps(replay, allow_nan=False)

    def test_fusion_requires_proven_top_flow_fresh_price_and_safe_guardrails(self):
        row = live_stock()
        sectors = {"status": "fresh", "top_verified": True, "items": [board()]}
        ground = {"day": "2026-11-20", "items": [{"code": row["code"], "volume_percentile": 5,
                                                   "score": 82, "rank": 3, "max_position_pct": 20}]}
        qualified, issue = fusion_rows(sectors, ground, live_bundle(row), at())
        self.assertIsNone(issue)
        self.assertEqual(qualified[0]["tracks"], ["进攻", "防守"])
        self.assertEqual(qualified[0]["max_position_pct"], 20)
        self.assertEqual(qualified[0]["s1"], 10.3)
        self.assertEqual(AntiChasingRiskGuard.reasons(row, at(), 30), [])
        self.assertTrue(AntiChasingRiskGuard.reasons(row, at(), 30.01))
        sectors["top_verified"] = False
        self.assertFalse(fusion_rows(sectors, ground, live_bundle(row), at())[0])
        old = live_stock(at("14:44:00").isoformat())
        old["pct"], old["previous_turnover"] = 4.01, 35.01
        self.assertEqual(len(AntiChasingRiskGuard.reasons(old, at("14:44:00"), 20)), 2)

    def test_blocked_exit_carries_position_and_marks_unrealized_risk(self):
        rows = {0: (10, 10, True, True, True, True, None),
                1: (10, 10, True, True, True, True, None),
                2: (9, 9, False, False, True, False, None),
                3: (8, 8, False, False, True, False, None)}
        result = style_replay("capacity", {"sh600123": rows}, {0: [(1e9, "sh600123")]}, 4)
        self.assertEqual(result["trades"], 0)
        self.assertEqual(result["unresolved"], 1)
        self.assertIsNone(result["win_rate"])
        self.assertGreater(result["max_drawdown"], 4)


class InputTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_snapshot_format_read_and_missing_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "backtest.json"
            reader = StrategyInputs(root)
            self.assertIsNone((await reader.read("backtest.json", 80))[0])
            path.write_text('{"schema_version":1}', encoding="utf-8")
            self.assertEqual(await reader.read("backtest.json", 80), ({"schema_version": 1}, None))
            self.assertIsNone((await reader.read("backtest.json", 5))[0])


class AppTests(unittest.TestCase):
    def test_public_read_only_routes_and_empty_data(self):
        with patch.dict("os.environ", {"PULSE_ALLOWED_HOSTS":"testserver", "PULSE_SECTOR_FLOW":"0"}):
            app = create_app(database=":memory:", strategy_root="", access="cloud",
                             transport=httpx.MockTransport(lambda _: httpx.Response(404)))
            with TestClient(app, base_url="https://testserver") as client:
                self.assertEqual(client.get("/healthz").json()["version"], "4.0.0")
                paths = ("/api/sectors/live-flow", "/api/stocks/sh600123/capital-profile",
                         "/api/strategy/hot-money-sim", "/api/strategy/pulse-v4")
                for route in paths:
                    self.assertEqual(client.get(route).status_code, 200)
                    self.assertEqual(client.get(route, headers={"Origin":"https://example.com"}).status_code, 403)
                    self.assertEqual(client.post(route).status_code, 405)
                self.assertEqual(client.get(paths[2]).json()["data"]["status"], "waiting")
                self.assertEqual(client.get("/api/paper/account").status_code, 403)
                self.assertEqual(client.get(paths[0]+"?board=bad").status_code, 422)
