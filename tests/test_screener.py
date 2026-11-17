import asyncio
from datetime import date
import unittest

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.adapters.live import response
from backend.main import Market, create_app
from backend.services.cache import Cache
from backend.services.screener import ScreenRequest, screen


def row(code, pct, amount, name="样本个股", stamp="2026-11-10T15:00:00+08:00"):
    return {"code":code,"name":name,"price":12,"pct":pct,"amount":amount,"market_time":stamp}


class ScreenTests(unittest.TestCase):
    def test_combined_conditions_scope_risk_and_missing(self):
        rows = [row("sh600001",3,2e8),row("sh600002",5,3e8,"*ST样本"),
                row("sz000003",4,2e8),row("sh600004",4,None),row("sh600005",1,2e8),
                row("sh600006",5,1e8),row("sh600007",4,4e8,stamp="2026-11-09T15:00:00+08:00")]
        query = ScreenRequest(exchange="sh",ranges=[
            {"field":"pct","minimum":3,"maximum":5},{"field":"amount","minimum":2e8}],
            sort_by="pct")
        data = screen(rows,query,"2026-11-10T15:00:00+08:00")
        self.assertEqual([r["code"] for r in data["items"]],["sh600001"])
        self.assertEqual((data["scanned"],data["eligible"],data["missing"],data["matched"]),(6,4,1,1))

    def test_sort_puts_missing_last_and_counts_before_limit(self):
        rows=[row("sh600001",2,None),row("sz000001",2,2e8),row("sh600002",3,3e8)]
        for direction, first in [("desc","sh600002"),("asc","sz000001")]:
            result=screen(rows,ScreenRequest(sort_by="amount",direction=direction,limit=2),None)
            self.assertEqual(result["items"][0]["code"],first)
            self.assertTrue(all(r["amount"] is not None for r in result["items"]))
            self.assertEqual(result["matched"],3)

    def test_rejects_invalid_ranges_and_scope(self):
        for options in [{"scope":"board"},{"scope":"watch","codes":["bad"]},
                        {"ranges":[{"field":"pct","minimum":6,"maximum":4}]},
                        {"ranges":[{"field":"pct","minimum":float("nan")}]},
                        {"ranges":[{"field":"pct","minimum":0},{"field":"pct","maximum":5}]}]:
            with self.subTest(options=options), self.assertRaises(ValidationError):
                ScreenRequest(**options)

    def test_demo_api_uses_requested_scope_and_no_network(self):
        def forbidden(_):
            raise AssertionError("演示选股不可访问上游")
        with TestClient(create_app(database=":memory:",transport=httpx.MockTransport(forbidden)), base_url="http://127.0.0.1", client=("127.0.0.1",50000)) as client:
            all_stocks=client.post("/api/stocks/screen?mode=demo",json={}).json()
            self.assertEqual(all_stocks["mode"],"demo")
            self.assertGreater(all_stocks["data"]["matched"],6)
            only_watch=client.post("/api/stocks/screen?mode=demo",json={"scope":"watch","codes":["000001","sh000001"]})
            self.assertEqual([r["code"] for r in only_watch.json()["data"]["items"]],["sz000001"])
            board=client.post("/api/stocks/screen?mode=demo",json={"scope":"board","board":"BK1001"})
            self.assertEqual(board.status_code,200)
            self.assertGreater(board.json()["data"]["eligible"],0)
            self.assertEqual(client.post("/api/stocks/screen?mode=demo",json={"scope":"board","board":"invalid"}).status_code,422)


class MarketReuseTest(unittest.IsolatedAsyncioTestCase):
    async def test_live_screener_and_breadth_share_one_collection(self):
        today=date.today().isoformat()

        class Provider:
            calls=0

            async def calendar(self, year, month):
                return response([today],"日历",None)

            async def universe(self):
                self.calls+=1
                await asyncio.sleep(.01)
                stamp=today+"T15:00:00+08:00"
                return response([row("sh600001",3,2e8,stamp=stamp)],"真实快照",None,stamp)

        provider=Provider()
        service=Market(Cache(":memory:"),provider)
        try:
            clock=await service.clock("live")
            await asyncio.gather(service.breadth("live",clock),service.screen("live",ScreenRequest()))
            await asyncio.gather(*service.cache.tasks.values())
            breadth, screened=await asyncio.gather(service.breadth("live",clock),service.screen("live",ScreenRequest()))
            self.assertEqual(provider.calls,1)
            self.assertEqual(breadth["data"]["up"],1)
            self.assertEqual(screened["data"]["matched"],1)
            self.assertEqual(screened["market_time"],breadth["market_time"])
        finally:
            await service.close()
