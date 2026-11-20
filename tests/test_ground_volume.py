import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import httpx
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.services.cache import Cache
from backend.services.clock import SH
from backend.services.ground_volume_engine import (
    GroundVolumeEngine, arrays, backtest, candidate, features, rank_candidates, read_snapshot, stats,
)


def exchange_days(count=629):
    day = date(2024, 1, 2)
    days = []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def fixture(days):
    prices = [10.] * 26 + [10.10 - .02 * i for i in range(10)] + [10.12]
    bars = []
    for i, day in enumerate(days):
        phase = i % len(prices)
        last = prices[phase]
        previous = prices[(i - 1) % len(prices)]
        bars.append({
            "day": day, "known_at": day + "T15:15:00+08:00", "quote_at": day + "T15:00:00+08:00",
            "open": last, "high": last + .03, "low": last - .03, "close": last,
            "adj_factor": 1, "volume": 100_000 if phase >= 32 else 1_000_000,
            "amount": 1_000_000, "turnover": 1.5, "float_cap": 4e9, "listed_sessions": i + 62,
            "pct": (last / previous - 1) * 100, "limit_up": round(previous * 1.1, 2),
            "limit_down": round(previous * .9, 2), "chip90_pct": 10, "unlock_float_pct_30d": 0,
            "main_net": 10_000, "risk_warning": False, "suspended": False, "deducted_profit": [1e6, 2e6],
        })
    return {"code": "sh600001", "name": "合成测试", "sector": "测试行业", "bars": bars}


def moment(day, at="15:15:30"):
    return datetime.fromisoformat(day + "T" + at + "+08:00")


class StrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.days = exchange_days()
        cls.as_of = moment(cls.days[-1])

    def test_complete_same_rule_history_scores_and_risk_bound(self):
        row, reason = candidate(fixture(self.days), self.days, self.as_of, "15:15")
        self.assertIsNone(reason)
        self.assertGreater(row["history"]["trigger_count"], 1)
        self.assertEqual(row["history"]["covered_days"], 485)
        for horizon in ("1", "3", "5"):
            self.assertGreater(row["history"]["horizons"][horizon]["samples"], 0)
        self.assertLessEqual(row["max_position_pct"], 20)
        self.assertLessEqual(row["stop"], row["entry"][0])
        self.assertEqual(row["stop"], 10.09)
        self.assertEqual(row["signals"], ["RSI(6)", "小幅净流入"])
        self.assertAlmostEqual(row["score"], sum(row["score_parts"].values()), places=7)
        json.dumps(row, allow_nan=False)

    def test_fails_closed_on_risk_fields_and_boundaries(self):
        rejected = {
            "suspended": True, "risk_warning": True, "listed_sessions": 59,
            "deducted_profit": [-1, -2], "unlock_float_pct_30d": 5,
            "chip90_pct": 15, "float_cap": 50_000_000_001, "turnover": 3.51,
        }
        for field, value in rejected.items():
            with self.subTest(field=field):
                stock = fixture(self.days)
                stock["bars"][-1][field] = value
                self.assertIsNone(candidate(stock, self.days, self.as_of, "15:15")[0])
        stock = fixture(self.days)
        stock["bars"][-1]["chip90_pct"] = None
        self.assertEqual(candidate(stock, self.days, self.as_of, "15:15")[1], "missing_required_fields")
        stock["bars"][-1]["known_at"] = self.days[-1] + "T15:16:00+08:00"
        self.assertEqual(candidate(stock, self.days, self.as_of, "15:15")[1], "stale_or_missing_quote")
        stock["code"] = "sh688001"
        self.assertEqual(candidate(stock, self.days, self.as_of, "15:15")[1], "non_mainboard")

    def test_historical_grid_rejects_future_records_and_keeps_missing_sessions(self):
        stock = fixture(self.days)
        stock["bars"][-4]["known_at"] = self.days[-4] + "T17:00:00+08:00"
        data = arrays(stock["bars"], self.days, self.as_of)
        self.assertTrue(np.isnan(data["close"][-4]))
        self.assertEqual(len(data["close"]), len(self.days))
        self.assertIsNotNone(data["quote_at"])
        with self.assertRaises(ValueError):
            arrays(stock["bars"] + [stock["bars"][-4]], self.days, self.as_of)

    def test_intraday_quote_cannot_become_close_confirmation(self):
        stock = fixture(self.days)
        day = self.days[-1]
        bar = stock["bars"][-1]
        bar["known_at"] = day + "T14:45:25+08:00"
        bar["quote_at"] = day + "T14:44:30+08:00"
        pre = moment(day, "14:45:30")
        self.assertIsNotNone(candidate(stock, self.days, pre, "14:45")[0])
        self.assertEqual(candidate(stock, self.days, self.as_of, "15:15")[1], "stale_or_missing_quote")
        bar["quote_at"] = day + "T14:42:59+08:00"
        self.assertEqual(candidate(stock, self.days, pre, "14:45")[1], "stale_or_missing_quote")

    def test_zero_division_and_no_pseudohistory(self):
        stock = fixture(self.days)
        for row in stock["bars"]:
            row["volume"] = 0
            row["amount"] = 0
        with np.errstate(divide="raise", invalid="raise"):
            data = features(arrays(stock["bars"], self.days, self.as_of))
            result = backtest(data)
        self.assertFalse(data["signal"].any())
        self.assertEqual(result["trigger_count"], 0)
        self.assertIsNone(result["max_drawdown"])
        self.assertIsNone(stats(np.array([.05, 0.]))["payoff_ratio"])

    def test_calendar_horizons_entry_exit_censoring_and_drawdown(self):
        size = 131
        available = np.ones(size, dtype=bool)
        f = {"signal": np.zeros(size, dtype=bool), "open": np.full(size, 10.),
             "close": np.full(size, 10.), "can_mark": available.copy(),
             "can_buy": available.copy(), "can_sell": available.copy()}
        f["signal"][[120, 123, 129, 130]] = True
        f["close"][[121, 123, 125, 128]] = [9., 11., 11., 12.]
        f["can_sell"][126] = False
        f["can_buy"][130] = False  # partial or untradeable last session
        report = backtest(f)
        self.assertEqual(report["trigger_count"], 3)
        self.assertEqual(report["horizons"]["1"]["samples"], 2)
        self.assertEqual(report["horizons"]["3"]["samples"], 1)
        self.assertEqual(report["horizons"]["5"]["samples"], 2)
        self.assertAlmostEqual(report["horizons"]["5"]["mean_return"], 15)
        self.assertAlmostEqual(report["max_drawdown"], 10)
        self.assertEqual(report["drawdown_trades"], 1)

    def test_order_and_cap_are_stable(self):
        rows = [{"code": f"sh60{i:04}", "score": 90 if i else 0, "win_rate": 60,
                 "mean_return": 1, "max_drawdown": 8} for i in reversed(range(25))]
        ranked = rank_candidates(rows)
        self.assertEqual([x["code"] for x in ranked], [f"sh60{i:04}" for i in range(1, 21)])
        self.assertEqual([x["rank"] for x in ranked], list(range(1, 21)))
        self.assertEqual(rank_candidates([]), [])

    def test_snapshot_validates_slot_and_file_completeness(self):
        day = self.days[-1]
        stock = fixture(self.days)
        manifest = {"schema_version": 1, "as_of": self.as_of.isoformat(), "source": "合成测试源",
                    "sessions": self.days, "universe_size": 1}
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / day
            folder.mkdir()
            header = folder / "1515.json"
            header.write_text(json.dumps(manifest), encoding="utf-8")
            payload = folder / "1515.ndjson"
            payload.write_text(json.dumps(stock) + "\n", encoding="utf-8")
            result = read_snapshot(root, day, "15:15", self.as_of)
            self.assertEqual(result["items"][0]["code"], stock["code"])
            self.assertEqual(result["diagnostics"], {"matched": 1})
            payload.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "incomplete"):
                read_snapshot(root, day, "15:15", self.as_of)
            with self.assertRaisesRegex(ValueError, "requested minute"):
                read_snapshot(root, day, "15:15", moment(day, "15:15:15"))


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_once_and_keeps_two_slots_separate(self):
        day = "2026-11-20"
        calls = []
        cache = Cache(":memory:")
        market = SimpleNamespace(cache=cache, clock=AsyncMock(return_value={"latest_day": day, "phase": "afternoon"}))

        def load(root, requested_day, slot, current):
            calls.append(slot)
            if len(calls) == 1:
                raise FileNotFoundError()
            return {"day": requested_day, "slot": slot, "as_of": current.isoformat(), "items": []}

        engine = GroundVolumeEngine(market, "/test-only", loader=load)
        try:
            await engine.tick(moment(day, "14:44:59"))
            self.assertEqual(calls, [])
            await engine.tick(moment(day, "14:45:02"))
            self.assertEqual(engine.results(moment(day, "14:45:02"), "latest")["status"], "input_unavailable")
            await engine.tick(moment(day, "14:45:31"))
            await engine.tick(moment(day, "14:46:02"))
            await engine.tick(moment(day, "14:46:40"))
            self.assertEqual(calls, ["14:45", "14:45"])
            self.assertEqual(engine.results(moment(day, "14:46:40"), "latest")["status"], "ready")
            self.assertTrue(engine.results(moment(day, "15:15:00"), "latest")["stale"])
            market.clock.return_value = {"latest_day": day, "phase": "closed"}
            await engine.tick(moment(day, "15:15:02"))
            restarted = GroundVolumeEngine(market, "/test-only", loader=load)
            await restarted.tick(moment(day, "15:15:20"))
            self.assertEqual(calls, ["14:45", "14:45", "15:15"])
            self.assertEqual(restarted.results(moment(day, "15:15:20"), "latest")["result"]["slot"], "15:15")
            self.assertEqual(restarted.results(moment(day, "15:15:20"), "14:45")["result"]["slot"], "14:45")
        finally:
            await cache.close()

    async def test_no_job_on_weekend_or_exchange_holiday(self):
        cache = Cache(":memory:")
        market = SimpleNamespace(cache=cache, clock=AsyncMock(return_value={"latest_day": "2026-11-19", "phase": "holiday"}))
        engine = GroundVolumeEngine(market, "/test-only", loader=lambda *_: self.fail("unexpected load"))
        try:
            await engine.tick(moment("2026-11-21", "14:45:15"))
            market.clock.assert_not_awaited()
            await engine.tick(moment("2026-11-20", "14:45:15"))
            market.clock.assert_awaited_once()
        finally:
            await cache.close()


class ApiTests(unittest.TestCase):
    def test_open_endpoint_without_data_and_isolated_demo(self):
        with patch.dict("os.environ", {"PULSE_GROUND_VOLUME_DIR": ""}):
            app = create_app(database=":memory:", access="local",
                             transport=httpx.MockTransport(lambda _req: httpx.Response(503)))
            with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
                url = "/api/strategy/ground-volume-top20"
                payload = client.get(url).json()
                self.assertEqual(payload["data"]["status"], "not_configured")
                self.assertIsNone(payload["data"]["result"])
                self.assertEqual(client.get(url+"?mode=demo").json()["data"]["status"], "live_only")
                self.assertEqual(client.get(url+"?slot=09:30").status_code, 422)

    def test_real_batch_loader_scheduler_cache_and_read_only_api(self):
        days = exchange_days()
        day = days[-1]
        at = moment(day)
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / day
            folder.mkdir()
            (folder / "1515.json").write_text(json.dumps({
                "schema_version": 1, "as_of": at.isoformat(), "source": "合成测试源",
                "sessions": days, "universe_size": 1,
            }), encoding="utf-8")
            (folder / "1515.ndjson").write_text(json.dumps(fixture(days)) + "\n", encoding="utf-8")
            app = create_app(database=":memory:", access="local", ground_volume_root=root,
                             transport=httpx.MockTransport(lambda _req: httpx.Response(503)))
            with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
                with patch.object(app.state.market, "clock", AsyncMock(return_value={"phase":"closed", "latest_day":day})):
                    client.portal.call(app.state.ground_volume.tick, at)
                url = "/api/strategy/ground-volume-top20"
                with patch("backend.main.now", return_value=at):
                    packet = client.get(url + "?slot=15:15").json()
                    self.assertTrue(packet["cached"])
                    self.assertEqual(packet["market_time"], at.isoformat())
                    self.assertEqual(packet["data"]["result"]["items"][0]["rank"], 1)
                    self.assertIsNone(client.get(url + "?slot=14:45").json()["data"]["result"])
                    self.assertIsNone(client.get(url + "?mode=demo").json()["data"]["result"])
                self.assertNotIn(client.post(url).status_code, [200, 201, 204])
