import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.adapters.live import INDICES
from backend.services.cache import Cache
from backend.services.clock import SH
from backend.services.monitor import IntradayMonitor, KEY, stance
from backend.main import Market


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    def make(self, phase="open_auction", hm="09:15"):
        current=datetime.fromisoformat("2026-11-11T"+hm+":00+08:00")
        clock={"time":current.isoformat(),"phase":phase,"active":phase in {"open_auction","morning","afternoon","close_auction"},"calendar_error":None}
        row={"code":"sh600371","name":"测试样本","price":13,"open":12.8,"pct":1,"amount":1e8,
             "market_time":current.isoformat(),"is_stale":False}
        async def quotes(mode,codes,clock):
            rows=[{**row,"code":c} for c in codes]
            return {"data":rows,"source":"test fixture","market_time":row["market_time"],"error":None}
        market=SimpleNamespace(cache=Cache(":memory:"),clock=AsyncMock(return_value=clock),quotes=AsyncMock(side_effect=quotes),
             alerts=AsyncMock(return_value={"data":[]}),sectors=AsyncMock(return_value={"data":{"items":[]},"is_stale":True,"error":"not published"}))
        return market,IntradayMonitor(market,enabled=True),current

    async def test_0915_observation_never_fabricates_auction_buy_signal(self):
        market,monitor,current=self.make()
        try:
            await monitor.tick(current)
            state=monitor.prior("2026-11-11")
            self.assertEqual(state["stage"],"竞价观察")
            self.assertEqual(state["started_at"],current.isoformat(timespec="seconds"))
            self.assertEqual(state["auction"]["tradable_signals"],[])
            self.assertIsNone(state["auction"]["indicative_price"])
            market.alerts.assert_not_awaited()
        finally: await market.cache.close()

    async def test_nontrading_and_calendar_unknown_never_fetch_quotes(self):
        for phase in ("before","pause","lunch","holiday","weekend","unknown","closed"):
            market,monitor,current=self.make(phase)
            try:
                await monitor.tick(current)
                market.quotes.assert_not_awaited()
                self.assertFalse(monitor.prior("2026-11-11")["active"])
            finally: await market.cache.close()

    async def test_regular_monitor_collects_alerts_and_throttles_sectors(self):
        market,monitor,current=self.make("morning","09:31")
        try:
            await monitor.tick(current)
            self.assertEqual(monitor.prior("2026-11-11")["scans"],1)
            market.alerts.assert_awaited_once()
            market.sectors.assert_awaited_once()
            await monitor.tick(current+timedelta(seconds=4))
            self.assertEqual(market.quotes.await_count,2)
            await monitor.tick(current+timedelta(seconds=8))
            self.assertEqual(monitor.prior("2026-11-11")["scans"],2)
            market.sectors.assert_awaited_once()
        finally: await market.cache.close()

    async def test_close_auction_does_not_generate_new_entry_alerts(self):
        market,monitor,current=self.make("close_auction","14:58")
        try:
            await monitor.tick(current)
            self.assertEqual(monitor.prior("2026-11-11")["stage"],"收盘集合竞价")
            market.alerts.assert_not_awaited()
        finally: await market.cache.close()

    async def test_restart_retains_actual_start_without_inventing_0915(self):
        market,monitor,current=self.make("morning","09:35")
        try:
            await monitor.tick(current)
            restarted=IntradayMonitor(market,enabled=True)
            await restarted.tick(current+timedelta(seconds=20))
            row=restarted.prior("2026-11-11")
            self.assertEqual(row["started_at"],current.isoformat(timespec="seconds"))
            self.assertNotEqual(row["started_at"],row["planned_start"])
            self.assertEqual(row["scans"],2)
        finally: await market.cache.close()

    def test_stale_indices_do_not_create_directional_bias(self):
        rows=[{"is_stale":True,"pct":8,"open":10,"price":11} for _ in range(5)]
        self.assertEqual(stance(rows)["label"],"数据不足")
        rows=[{**q,"is_stale":False,"pct":-1,"price":9} for q in rows]
        self.assertEqual(stance(rows)["label"],"防守观察")

    async def test_browser_alert_endpoint_does_not_treat_auction_as_intraday_trading(self):
        market=Market(Cache(":memory:"),object())
        stamp="2026-11-11T09:20:00+08:00"
        market.clock=AsyncMock(return_value={"phase":"open_auction","active":True,"tail":False,"time":stamp})
        market.quotes=AsyncMock(return_value={"data":[{"code":"sh600371","name":"测试","pct":8,"turnover":10,
            "price":13,"amount":1e8,"vol_ratio":3,"market_time":stamp,"is_stale":False}],"is_stale":False})
        try:
            result=await market.alerts("live",("sh600371",),"auction-test",3,5,1,2.5,"industry")
            self.assertEqual(result["data"],[])
        finally: await market.close()

if __name__ == "__main__": unittest.main()
