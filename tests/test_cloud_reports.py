import hashlib
import base64
import json
import os
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.main import Market, create_app
from backend.services.cache import Cache
from backend.services.clock import SH
from backend.services.global_market import context
from backend.services.global_market import GlobalMarket, parse_sina
from backend.services.premarket import Premarket, SAMPLE, candidate, impacts

BASE = "https://pulse.example.com"

def packet(data, stale=False):
    return {"data": data, "source": "test fixture", "source_url": "https://source.example.com/",
            "market_time": "2026-11-10T15:00:00+08:00", "fetched_at": "2026-11-11T08:00:00+08:00",
            "is_stale": stale, "error": "upstream failed" if stale else None}

def fixture_bars():
    return packet({"items": [{"t": (datetime(2026,11,10) - timedelta(days=29-i)).date().isoformat(),
               "open": 10+i*.01, "close": 10+i*.01, "high": 11, "low": 9, "volume": 10000} for i in range(30)]})

class CloudTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"PULSE_ALLOWED_HOSTS": "pulse.example.com", "PULSE_SCHEDULER": "0", "PULSE_MONITOR": "0", "PULSE_PAPER_CLOUD": "0"})
        self.env.start()
        self.app = create_app(database=":memory:", transport=httpx.MockTransport(lambda _: httpx.Response(503)), access="cloud")
        self.client = TestClient(self.app, base_url=BASE, client=("192.0.2.15", 50000))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.env.stop()

    def test_public_same_origin_security_headers_and_probe(self):
        result = self.client.get("/api/config", headers={"Origin": BASE})
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["data"]["allow_demo"])
        self.assertIn("wss://pulse.example.com", result.headers["content-security-policy"])
        self.assertIn("max-age=31536000", result.headers["strict-transport-security"])
        self.assertEqual(self.client.get("/healthz", headers={"Host":"railway.internal"}).status_code, 200)
        self.assertEqual(self.client.get("/", headers={"Host":"evil.example"}).status_code, 403)

    def test_cloud_paper_worker_starts_without_exposing_account(self):
        with patch.dict(os.environ, {"PULSE_PAPER_CLOUD": "1"}), patch(
            "backend.services.paper.PaperAccount.tick", new_callable=AsyncMock,
        ) as tick:
            app = create_app(database=":memory:", transport=httpx.MockTransport(lambda _: httpx.Response(503)), access="cloud")
            with TestClient(app, base_url=BASE, client=("192.0.2.15", 50000)) as client:
                runner = client.get("/api/config").json()["data"]["paper_runner"]
                self.assertTrue(runner["enabled"])
                self.assertIsNotNone(runner["last_tick_at"])
                tick.assert_awaited()
                self.assertEqual(client.get("/api/paper/account").status_code, 403)

    def test_no_demo_private_account_cross_origin_or_remote_generation(self):
        for route in ("/api/market/indices?mode=demo", "/api/paper/account", "/docs", "/openapi.json"):
            self.assertEqual(self.client.get(route).status_code, 403)
        for origin in ("https://evil.example", "http://pulse.example.com", "null"):
            self.assertEqual(self.client.get("/api/config", headers={"Origin":origin}).status_code,403)
        self.assertEqual(self.client.post("/api/reports/generate").status_code,405)
        self.assertEqual(self.client.get("/api/config", headers={"Sec-Fetch-Site":"cross-site"}).status_code,403)

    def test_limits_body_and_quote_validation(self):
        self.assertEqual(self.client.post("/api/stocks/screen", content=b"x"*17000).status_code,413)
        self.assertEqual(self.client.get("/api/stocks/not-a-code/kline").status_code,422)
        self.assertEqual(self.client.get("/api/stocks/600519/kline?count=1000000").status_code,422)
        self.assertEqual(self.client.get("/api/reports/not-a-day").status_code,422)

    def test_http_request_rate_limit_returns_retry_after(self):
        for _ in range(240):
            self.assertEqual(self.client.get("/api/config").status_code,200)
        response=self.client.get("/api/config")
        self.assertEqual(response.status_code,429)
        self.assertEqual(response.headers["retry-after"],"60")

    def test_websocket_origin_and_demo_are_rejected(self):
        for suffix, origin in (("mode=demo", BASE), ("mode=live", "https://evil.example")):
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect("/ws/market?"+suffix, headers={"Origin":origin}):
                    self.fail("unsafe websocket accepted")

    def test_empty_report_and_markdown_export(self):
        self.assertIsNone(self.client.get("/api/reports/latest").json()["data"])
        self.assertEqual(self.client.get("/api/reports/latest.md").status_code,404)
        md = "# source test\n"
        report = {"day":"2026-11-11", "markdown":md, "sha256":hashlib.sha256(md.encode()).hexdigest()}
        async def insert():
            with self.app.state.market.cache.db:
                self.app.state.market.cache.db.execute("INSERT INTO premarket_reports VALUES (?,?,?)", (report["day"],"2026-11-11",json.dumps(report)))
        self.client.portal.call(insert)
        result = self.client.get("/api/reports/2026-11-11.md")
        self.assertEqual(result.text,md)
        self.assertIn("attachment",result.headers["content-disposition"])
        self.assertIn(report["sha256"],result.headers["etag"])


class ReportTests(unittest.IsolatedAsyncioTestCase):
    def prepare(self, path=":memory:", calendar=None):
        market = Market(Cache(path),object())
        async def get(mode, name, ttl, *args, **kwargs):
            return calendar or packet(["2026-11-10","2026-11-11"]) if name == "calendar" else fixture_bars()
        market.get = AsyncMock(side_effect=get)
        market.quotes = AsyncMock(return_value=packet([{"code":c,"name":"测试证券", "price":10.29,
            "market_time":"2026-11-10T15:00:00+08:00", "is_stale":False,"amount":2e9,"pct":1} for c in SAMPLE]))
        market.sectors = AsyncMock(return_value=packet({"items":[]}))
        global_market = type("World", (), {"snapshot": AsyncMock(return_value={"coverage":0,"total":15,"rows":[],"stance":"待确认"})})()
        engine = Premarket(market,global_market)
        engine.headlines = AsyncMock(return_value=packet({"items":[], "complete_window":False}))
        return market, engine

    async def test_idempotence_restart_partial_report_and_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder)/"market.db"
            market,engine = self.prepare(db)
            try:
                current = datetime(2026,11,11,8,0,tzinfo=SH)
                first,second = await __import__('asyncio').gather(engine.generate(current),engine.generate(current))
                self.assertEqual(first["sha256"],second["sha256"])
                self.assertEqual(first["status"],"partial")
                self.assertFalse(first["late"])
                self.assertEqual(first["sha256"],hashlib.sha256(first["markdown"].encode()).hexdigest())
                market.quotes.assert_awaited_once()
            finally:
                await market.close()
            market,engine = self.prepare(db)
            try:
                self.assertEqual(engine.get()["sha256"],first["sha256"])
                self.assertEqual(len(engine.list()),1)
            finally:
                await market.close()

    async def test_calendar_failure_never_creates_official_report(self):
        market,engine = self.prepare(calendar=packet(["2026-11-11"],True))
        try:
            with self.assertRaises(RuntimeError):
                await engine.generate(datetime(2026,11,11,8,0,tzinfo=SH))
            self.assertIsNone(engine.get())
            self.assertIn("交易日历",engine.last_error)
        finally:
            await market.close()

    async def test_holiday_weekend_before_schedule_and_after_auction(self):
        market,engine = self.prepare(calendar=packet(["2026-11-10"]))
        try:
            for dt in (datetime(2026,11,11,7,59,tzinfo=SH),datetime(2026,11,14,8,0,tzinfo=SH),datetime(2026,11,11,9,15,tzinfo=SH)):
                self.assertIsNone(await engine.tick(dt))
            self.assertIsNone(await engine.tick(datetime(2026,11,11,8,0,tzinfo=SH)))
            self.assertIsNone(engine.get())
        finally:
            await market.close()

    async def test_late_recovery_is_explicit(self):
        market,engine = self.prepare()
        try:
            r = await engine.tick(datetime(2026,11,11,8,12,tzinfo=SH))
            self.assertTrue(r["late"])
            self.assertIn("不代表08:00历史快照",r["markdown"])
        finally:
            await market.close()

    async def test_supplement_preserves_original_report(self):
        market,engine = self.prepare()
        try:
            original = await engine.generate(datetime(2026,11,11,8,0,tzinfo=SH))
            revised = await engine.generate(datetime(2026,11,11,8,20,tzinfo=SH),supplement=True)
            self.assertEqual(revised["revision"],2)
            self.assertEqual(engine.get("2026-11-11",1)["sha256"],original["sha256"])
            self.assertEqual(engine.get()["sha256"],revised["sha256"])
            self.assertEqual(revised["supersedes_sha256"],original["sha256"])
        finally:
            await market.close()

    async def test_verified_archive_import_is_idempotent_and_preserves_timestamps(self):
        market,engine = self.prepare()
        try:
            report = await engine.generate(datetime(2026,11,11,8,0,tzinfo=SH))
            report = {**report, "markdown":report["markdown"]+"\nSupplement\n"}
            report["sha256"]=hashlib.sha256(report["markdown"].encode()).hexdigest()
            encoded=base64.b64encode(zlib.compress(json.dumps(report).encode())).decode()
            engine.import_archive(encoded)
            engine.import_archive(encoded)
            self.assertEqual(engine.get()["revision"],2)
            self.assertEqual(engine.get()["generated_at"],report["generated_at"])
            bad={**report,"sha256":"0"*64}
            with self.assertRaises(ValueError):
                engine.import_archive(base64.b64encode(zlib.compress(json.dumps(bad).encode())).decode())
            self.assertEqual(engine.get()["revision"],2)
        finally:
            await market.close()

    async def test_utc_scheduler_uses_shanghai_0800(self):
        from datetime import timezone
        market,engine = self.prepare()
        try:
            r = await engine.tick(datetime(2026,11,11,0,0,tzinfo=timezone.utc))
            self.assertEqual(r["scheduled_for"],"2026-11-11T08:00:00+08:00")
            self.assertFalse(r["late"])
        finally:
            await market.close()

    def test_sina_cash_and_cfd_contracts_are_distinct(self):
        fields=["标普500", "7520.9302", "-0.38", "2026-11-11 06:00:45"]+[""]*22+["7549.9399"]
        raw='var hq_str_gb_inx="'+','.join(fields)+'";'
        raw+='var hq_str_hf_HG="626.835,,626.700,626.800,627.600,625.700,08:49:14,623.450,626.850,0,5,2,2026-11-11,纽约铜,0";'
        rows = parse_sina(raw,datetime(2026,11,11,8,50,tzinfo=SH))
        cash,cfd=rows
        self.assertEqual(cash["close_date"],"2026-11-10")
        self.assertAlmostEqual(cash["pct"],-.384,places=2)
        self.assertEqual(cfd["instrument_type"],"cfd")
        self.assertEqual(cfd["unit"],"美分/磅")
        self.assertEqual(cfd["price"],626.835)
        self.assertEqual(parse_sina(raw,datetime(2026,11,11,4,0,tzinfo=SH)),[])

    async def test_yahoo_429_stops_requests_until_cooldown(self):
        calls=[]
        def upstream(req):
            calls.append(req.url.host)
            return httpx.Response(429)
        cache=Cache(":memory:")
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
                feed=GlobalMarket(cache,client)
                stamp=datetime(2026,11,11,8,0,tzinfo=SH)
                for key in ("^GSPC","^IXIC"):
                    with self.assertRaises(RuntimeError):
                        await feed.quote(key,stamp)
                self.assertEqual(len(calls),1)
        finally:
            await cache.close()

    def test_future_wrong_day_and_stale_candidates_are_excluded(self):
        quote={"name":"测试", "is_stale":False, "market_time":"2026-11-10T15:00:00+08:00", "price":10.29,"amount":2e9,"pct":1}
        bars=fixture_bars()
        self.assertIsNotNone(candidate("sh601138","电子",quote,bars,"2026-11-10","2026-11-11"))
        for changed in ({**quote,"is_stale":True},{**quote,"market_time":"2026-11-11T09:31:00+08:00"},{**quote,"name":"*ST测试"}):
            self.assertIsNone(candidate("sh601138","电子",changed,bars,"2026-11-10","2026-11-11"))
        bars["data"]["items"].append({"t":"2026-11-11","close":100000,"low":1})
        r = candidate("sh601138","电子",quote,bars,"2026-11-10","2026-11-11")
        self.assertLess(r["ma20"],11)

    def test_stale_international_quotes_never_support_direction(self):
        rows = [{"symbol":"NQ=F","group":"index","name":"纳指期货","status":"stale","pct":8}]
        self.assertTrue(all(r["direction"]=="待确认" for r in impacts(rows)))
        self.assertEqual(context(rows)["stance"],"待确认")

if __name__ == "__main__":
    unittest.main()
