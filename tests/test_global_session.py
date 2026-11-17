import unittest
from datetime import datetime
from unittest.mock import AsyncMock

import httpx

from backend.main import Market
from backend.services.cache import Cache
from backend.services.global_market import GlobalMarket, context, parse_chart


def iso(t):
    return datetime.fromisoformat(t)


def chart(symbol, quote_time="2026-11-10T15:45:00-05:00"):
    def ts(value):
        return int(iso(value).timestamp())
    return {"chart":{"result":[{
        "meta":{"symbol":symbol,"regularMarketPrice":102,"regularMarketTime":ts(quote_time),
                "currentTradingPeriod":{"regular":{"end":ts("2026-11-10T16:00:00-05:00")}}},
        "timestamp":[ts("2026-11-06T09:30:00-05:00"),ts("2026-11-09T09:30:00-05:00"),ts("2026-11-10T09:30:00-05:00")],
        "indicators":{"quote":[{"close":[99,100,102]}]}
    }]}}


class GlobalTests(unittest.IsolatedAsyncioTestCase):
    def test_us_intraday_never_becomes_close_and_different_dates_disable_bias(self):
        stamp = iso("2026-11-11T04:50:00+08:00")
        row = parse_chart(chart("^GSPC"), "^GSPC", stamp)
        self.assertEqual((row["pct"],row["close_date"]), (2, "2026-11-09"))
        closing = parse_chart(chart("^GSPC","2026-11-10T16:00:00-05:00"), "^GSPC", iso("2026-11-11T05:05:00+08:00"))
        self.assertEqual((closing["close_pct"],closing["close_date"]), (2,"2026-11-10"))
        delayed = parse_chart(chart("^GSPC","2026-11-10T15:59:30-05:00"), "^GSPC", iso("2026-11-11T05:05:00+08:00"))
        self.assertEqual(delayed["close_date"],"2026-11-09")
        next_day = parse_chart(chart("^GSPC","2026-11-10T15:59:30-05:00"), "^GSPC", iso("2026-11-11T15:05:00+08:00"))
        self.assertEqual(next_day["close_date"],"2026-11-09")
        quotes = [{**r,"status":"available"} for r in (
            closing, {**row,"symbol":"^IXIC"}, {"symbol":"ES=F","pct":.8}, {"symbol":"NQ=F","pct":1})]
        self.assertEqual(context(quotes)["stance"],"待确认")

    async def test_host_fallback_shared_cache_and_stale_future(self):
        calls = []
        def upstream(req):
            calls.append((req.url.host, req.url.path))
            if req.url.host.startswith("query1"):
                return httpx.Response(503)
            return httpx.Response(200,json=chart(req.url.path.split("/")[-1]))
        cache = Cache(":memory:")
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
                feed = GlobalMarket(cache,http)
                now = iso("2026-11-11T04:50:00+08:00")
                first = await feed.snapshot(now,wait=True)
                self.assertEqual(first["coverage"],first["total"])
                self.assertEqual(sum(host != "hq.sinajs.cn" for host,_ in calls),first["total"]*2)
                second = await feed.snapshot(iso("2026-11-11T08:45:00+08:00"),wait=True)
                self.assertEqual(sum(host != "hq.sinajs.cn" for host,_ in calls),first["total"]*2)
                self.assertEqual(next(r for r in second["rows"] if r["symbol"]=="ES=F")["status"],"stale")
        finally:
            await cache.close()


class JournalTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_record_retains_last_valid_and_closing_watch_is_once(self):
        market = Market(Cache(":memory:"),object())
        market.clock = AsyncMock(return_value={"phase":"closed","latest_day":"2026-11-11"})
        idea = {"code":"sh600519","invalid_below":100,"entry_cap":102,"score":76}
        def packet(status, items, stale=False):
            return {"data":{"status":status,"items":items},"is_stale":stale,
                    "source":"样本","source_url":None,"market_time":"2026-11-11T14:56:30+08:00"}
        try:
            market.sessions.capture("2026-11-11","tail",packet("ready",[idea]))
            market.sessions.capture("2026-11-11","tail",packet("stale",[],True))
            q = {"code":"sh600519","name":"测试","price":101,"pct":1,"market_time":"2026-11-11T15:00:00+08:00"}
            market.quotes = AsyncMock(return_value={"data":[q]})
            await market.sessions.tick(iso("2026-11-11T15:05:05+08:00"))
            await market.sessions.tick(iso("2026-11-11T15:06:05+08:00"))
            market.quotes.assert_awaited_once()
            row = market.sessions.history()[0]
            self.assertEqual(row["tail"]["status"],"stale")
            self.assertEqual(row["close"]["items"][0]["price"],101)
        finally:
            await market.close()
