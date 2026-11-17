import unittest

import httpx
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from backend.main import Market, create_app
from backend.services.cache import Cache
from backend.services.clock import SH
from backend.services.hourly import KEY, due, evaluate
from backend.services.paper import INITIAL, PaperAccount, equity, executable, fees


def candle(close, opening=100):
    return {"open": opening, "close": close, "high": max(opening, close)+1,
            "low": min(opening, close)-1, "volume": 100, "amount": 10000}


def book(at, price, lots=100):
    return {"code":"sh600519","price":price,"market_time":at,"is_stale":False,
            "book":[{"bid":price-.01,"ask":price+.01,"bid_lots":lots,"ask_lots":lots}]}


def history(win_count):
    start = date(2026, 4, 1)
    sessions = [(start + timedelta(days=i)).isoformat() for i in range(102)]
    days = {}
    for i, session in enumerate(sessions):
        days[session] = {"10:00": candle(103 if 2 <= i < 2+win_count else 101),
                         "10:30": candle(102), "11:00": candle(102), "15:00": candle(100)}
    return days, sessions


class HistoryTests(unittest.TestCase):
    def test_strictly_greater_than_80_and_never_use_unfinished_exit(self):
        rows, days = history(80)
        result = evaluate(rows, days, "10:30")
        self.assertEqual((result["wins"], result["samples"]), (80, 100))
        self.assertFalse(result["eligible"])
        rows[days[-1]]["10:00"] = candle(103)
        self.assertTrue(evaluate(rows, days, "10:30")["eligible"])
        rows["2026-12-01"] = {"10:00": candle(900)}
        self.assertEqual(evaluate(rows, days, "10:30")["wins"], 81)

    def test_entry_cap_covers_slippage_and_unresolved_exit_is_not_a_win(self):
        rows, days = history(100)
        rows[days[1]]["11:00"] = candle(102.5)
        del rows[days[3]]["10:00"]
        result = evaluate(rows, days, "10:30")
        self.assertEqual((result["samples"], result["unresolved"], result["wins"]), (98, 1, 97))
        self.assertFalse(evaluate(rows, days[-10:], "10:30")["eligible"])

    def test_trading_clock_excludes_lunch_other_hours_and_weekend(self):
        for slot in ("10:30", "11:30", "14:00", "15:00"):
            self.assertEqual(due(datetime.fromisoformat(f"2026-11-11T{slot}:40+08:00")), slot)
        self.assertIsNone(due(datetime(2026, 11, 11, 12, 30, tzinfo=SH)))
        self.assertIsNone(due(datetime(2026, 11, 14, 10, 31, tzinfo=SH)))

    def test_fill_rejects_old_locked_or_wide_book_and_charges_seller_tax(self):
        stamp = "2026-11-11T11:00:30+08:00"
        q = book("2026-11-11T11:00:25+08:00", 10, 2)
        self.assertEqual(executable(q, "ask", stamp)[1], 200)
        self.assertIsNone(executable(q, "ask", stamp, after=q["market_time"]))
        self.assertIsNone(executable(q, "ask", "2026-11-11T11:01:00+08:00"))
        q["book"][0]["bid"] = 0
        self.assertIsNone(executable(q, "ask", stamp))
        self.assertEqual((fees(1_000), fees(1_000, sell=True)), (5.01, 5.51))

    def test_local_api_initial_balance_and_demo_does_not_show_live_results(self):
        with TestClient(create_app(database=":memory:", transport=httpx.MockTransport(lambda req: httpx.Response(503))),
                        base_url="http://127.0.0.1", client=("127.0.0.1",50000)) as client:
            account = client.get("/api/paper/account").json()["data"]
            self.assertEqual(account["cash"], INITIAL)
            self.assertEqual(account["trades"], [])
            packet = client.get("/api/stocks/hourly-opportunities?mode=demo").json()
            self.assertEqual(packet["data"]["rounds"], [])
            self.assertEqual(packet["mode"], "demo")


class ScheduledTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.market = Market(Cache(":memory:"), object())

    async def asyncTearDown(self):
        await self.market.close()

    async def test_hourly_saved_once_and_holiday_skipped(self):
        scanner = self.market.hourly
        when = datetime(2026, 11, 11, 10, 31, tzinfo=SH)
        self.market.clock = AsyncMock(return_value={"phase": "holiday", "latest_day": "2026-11-10"})
        scanner.scan = AsyncMock(return_value={"status": "empty"})
        await scanner.tick(when)
        scanner.scan.assert_not_awaited()
        self.market.clock.return_value = {"phase": "morning", "latest_day": "2026-11-11"}
        await scanner.tick(when)
        await scanner.tick(when + timedelta(minutes=1))
        scanner.scan.assert_awaited_once()
        self.assertEqual(len(scanner.history()), 1)

    async def test_paper_ticks_buy_once_close_next_day_and_daily_review(self):
        a = self.market.paper
        setup = {"code": "sh600519", "name": "样本", "entry_floor": 99, "entry_cap": 102,
                 "wins": 29, "samples": 32, "win_rate": 29/32}
        self.market.cache.record(KEY, "2026-11-11T10:30:00+08:00", {
            "slot": "2026-11-11T10:30:00+08:00", "entry_at": "2026-11-11T11:00:00+08:00", "items": [setup]})
        self.market.clock = AsyncMock(return_value={"phase": "morning", "latest_day": "2026-11-11"})
        self.market.get = AsyncMock(return_value={"data": [{**candle(101), "t": "2026-11-11T11:00:00+08:00"}],
                                                  "is_stale": False})
        self.market.quotes = AsyncMock(return_value={"data":[book("2026-11-11T11:00:35+08:00", 101)]})
        at = datetime(2026, 11, 11, 11, 0, 40, tzinfo=SH)
        await a.tick(at)
        await a.tick(at + timedelta(minutes=1))
        state = a.load()
        self.assertEqual(len(state["trades"]), 1)
        trade = state["trades"][0]
        self.assertEqual(trade["entry_at"], "2026-11-11T11:00:35+08:00")
        self.assertEqual(trade["shares"] % 100, 0)
        self.assertLess(trade["cost"], INITIAL * .06)
        self.assertFalse(a.sell(state, trade, 109, "2026-11-11T14:30:00+08:00"))
        self.market.clock.return_value = {"phase": "morning", "latest_day": "2026-11-12"}
        self.market.get.return_value = {"data": [{**candle(103), "t": "2026-11-12T10:00:00+08:00"}], "is_stale": False}
        self.market.quotes.return_value = {"data":[book("2026-11-12T10:00:35+08:00", 103)]}
        await a.tick(datetime(2026, 11, 12, 10, 0, 40, tzinfo=SH))
        self.market.clock.return_value["phase"] = "closed"
        await a.tick(datetime(2026, 11, 12, 15, 0, 40, tzinfo=SH))
        restored = PaperAccount(self.market).load()
        self.assertGreater(equity(restored), INITIAL)
        self.assertEqual(restored["reviews"][0]["wins"], 1)
        self.assertEqual(restored["reviews"][0]["weekly_gap"], 4)

    async def test_drawdown_changes_next_session_and_no_forced_trade(self):
        a = self.market.paper
        account = a.load()
        account["cash"] = 965_000
        a.review(account, "2026-11-11", "2026-11-11T15:00:00+08:00", False)
        self.assertEqual(account["profile"], "paused")
        self.assertEqual(account["reviews"][0]["weekly_gap"], 5)
        result = await a.open_one(account, "2026-11-12", "11:00", "2026-11-12T11:00:00+08:00", 40, False)
        self.assertIn("风控", result)
        self.assertEqual(account["trades"], [])
