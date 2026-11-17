import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from backend.adapters.auction import FuyaoAuction
from backend.adapters.live import FLOW, Live, Unavailable
from backend.main import create_app
from backend.services.clock import SH
from backend.services.fund_flow import describe_flow


DAY = "2026-11-11"
CURRENT = datetime(2026, 11, 12, 9, 18, 15, tzinfo=SH)


def times():
    for hour, minute in ((9, 31), (13, 1)):
        first = datetime(2026, 11, 11, hour, minute)
        for offset in range(120):
            yield (first + timedelta(minutes=offset)).strftime("%Y-%m-%d %H:%M")


def stock_response(code="601188", market=1):
    rows = [f"{t},{-i},0,0,{-i},0" for i, t in enumerate(times(), 1)]
    return {"data": {"code": code, "market": market, "klines": rows}}


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_requests_full_day_and_keeps_cumulative_values(self):
        def upstream(req):
            self.assertEqual(req.url.params["lmt"], "0")
            self.assertEqual(req.url.params["secid"], "1.601188")
            self.assertEqual(req.url.host, "push2delay.eastmoney.com")
            return httpx.Response(200, json=stock_response())
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            data = await Live(client).stock_flow("sh601188", "minute")
        self.assertEqual(data["source_url"], FLOW)
        self.assertEqual(len(data["data"]), 240)
        self.assertEqual(data["data"][0]["t"], DAY+"T09:31:00+08:00")
        self.assertEqual(data["data"][-1]["main_net"], -240)
        self.assertEqual(data["market_time"], DAY+"T15:00:00+08:00")

    async def test_wrong_code_market_and_mixed_days_are_rejected(self):
        mixed = stock_response()
        mixed["data"]["klines"][0] = "2026-11-10 09:31,1,0,0,1,0"
        for payload in (stock_response("600000"), stock_response(market=0), mixed):
            provider = Live(None)
            provider.em_get = AsyncMock(return_value={**payload, "_source_url": FLOW})
            with self.assertRaises(Unavailable):
                await provider.stock_flow("sh601188", "minute")

    def test_lunch_rollover_and_failed_source_use_explicit_states(self):
        def packet(day, hm, error=None):
            return {"data":[{"t":day+"T"+hm+":00+08:00", "main_net":-120}],
                    "is_stale":bool(error), "error":error, "source_url":FLOW}
        def clock(day, hm, phase, latest):
            return {"time":day+"T"+hm+":00+08:00", "phase":phase, "latest_day":latest}
        lunch = clock(DAY, "12:45", "lunch", DAY)
        following = clock("2026-11-12", "09:35", "morning", "2026-11-12")
        before = clock("2026-11-12", "08:00", "before", DAY)
        for inputs, expected, stale in (
            ((packet(DAY,"11:30"), lunch), "lunch", False),
            ((packet(DAY,"11:20"), lunch), "delayed", True),
            ((packet(DAY,"15:00"), following), "wrong_session", True),
            ((packet(DAY,"15:00"), before), "closed_session", False),
            ((packet(DAY,"11:30","upstream failed"), lunch), "cached_stale", True),
            ((packet(DAY,"14:59"), before), "incomplete_close", True),
        ):
            result = describe_flow(*inputs, "minute")
            self.assertEqual(result["flow"]["state"], expected)
            self.assertEqual(result["is_stale"], stale)
            self.assertEqual(result["data"][-1]["main_net"], -120)

    async def test_existing_endpoint_exposes_flow_metadata(self):
        def upstream(req):
            if req.url.host == "www.szse.cn":
                days = [date(2026, 11, i) for i in range(1, 31)]
                return httpx.Response(200, json={"data":[{"jyrq":d.isoformat(),"jybz":str(int(d.weekday()<5))} for d in days]})
            return httpx.Response(200, json=stock_response())
        stamp = datetime(2026, 11, 12, 8, 0, tzinfo=SH)
        with patch("backend.main.now", return_value=stamp):
            app = create_app(database=":memory:", transport=httpx.MockTransport(upstream), access="local")
            with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
                result = client.get("/api/stocks/sh601188/fund-flow?span=minute").json()
        self.assertEqual(result["flow"]["state"], "closed_session")
        self.assertTrue(result["flow"]["complete"])
        self.assertEqual(len(result["data"]), 240)


class AuctionTests(unittest.IsolatedAsyncioTestCase):
    def data(self, timestamp=CURRENT, **extra):
        row = {"thscode":"000530.SZ","auction_price":11.32,"auction_volume":6500,"auction_unmatched":-800, **extra}
        return {"timestamp":int(timestamp.timestamp()*1000), "data_status":"ready",
                "auction_phase":"live", "item":[row]}

    async def test_missing_key_public_permission_and_outside_session_skip_http(self):
        with patch.dict("os.environ", {"PULSE_FUYAO_API_KEY":"","PULSE_AUCTION_PUBLIC":"0"}):
            http = AsyncMock()
            feed = FuyaoAuction(http, cloud=True)
            self.assertEqual((await feed.snapshot(("sz000530",), CURRENT))["status"], "unconfigured")
            feed.key = "fixture"
            self.assertEqual((await feed.snapshot(("sz000530",), CURRENT))["status"], "restricted")
            feed.visible = True
            self.assertEqual((await feed.snapshot(("sz000530",), CURRENT.replace(minute=25)))["status"], "outside_window")
            http.get.assert_not_awaited()

    async def test_real_contract_request_and_source_time_gate(self):
        def upstream(req):
            self.assertEqual(req.headers["X-api-key"], "fixture")
            self.assertEqual(req.url.params["stage"], "live")
            self.assertEqual(req.url.params["thscodes"], "000530.SZ")
            return httpx.Response(200, json={"code":0,"data":self.data(quoteTime=int(CURRENT.timestamp()*1000))})
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            feed = FuyaoAuction(client, cloud=False)
            feed.key = "fixture"
            feed.time_field = ""
            unverified = await feed.snapshot(("sz000530",), CURRENT)
            self.assertEqual(unverified["status"], "source_time_unverified")
            self.assertEqual(unverified["items"], [])
            feed.time_field = "quoteTime"
            verified = await feed.snapshot(("sz000530",), CURRENT)
            self.assertEqual(verified["status"], "verified_snapshot")
            self.assertEqual(verified["items"][0]["unmatched_volume"], -800)
            self.assertEqual(verified["items"][0]["market_time"], CURRENT.isoformat())

    def test_assembly_time_final_stage_and_old_source_are_not_live(self):
        feed = FuyaoAuction(None, cloud=False)
        symbols = {"000530.SZ":"sz000530"}
        feed.time_field = "timestamp"
        self.assertEqual(feed.parse(self.data(), symbols, CURRENT)["status"], "source_time_unverified")
        feed.time_field = "quoteTime"
        bad = self.data(quoteTime=int((CURRENT-timedelta(days=1)).timestamp()*1000))
        self.assertEqual(feed.parse(bad, symbols, CURRENT)["items"], [])
        bad["auction_phase"] = "final"
        self.assertEqual(feed.parse(bad, symbols, CURRENT)["status"], "not_ready")
        self.assertEqual(feed.parse(self.data(CURRENT-timedelta(days=1)),symbols,CURRENT)["status"], "stale")
