import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.adapters.live import Unavailable, response
from backend.main import Market, create_app
from backend.services.cache import Cache
from backend.services.clock import SH
from backend.services.ideas import prior_tail, rank_candidate, shortlist, window_open
from backend.services.quotes import QuoteBatcher, quote_interval


def quote(code="sh600519", time="14:46:10", price=11.2, amount=2e8):
    return {"code":code,"name":"样本","price":price,"open":11,"pct":3.1,"vol_ratio":2.0,"amount":amount,
            "market_time":f"2026-11-10T{time}+08:00","inactive":False,"main_net":3e7}


class QuoteBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlapping_clients_share_one_upstream_and_stamp(self):
        class Provider:
            def __init__(self):
                self.calls = []

            async def quotes(self,codes):
                self.calls.append(set(codes))
                await asyncio.sleep(.015)
                return response([quote(code) for code in codes],"样本",None)

        provider=Provider()
        batcher=QuoteBatcher(provider)
        try:
            a,b,c=await asyncio.gather(batcher.read(("sh600519","sz000001"),8),
                batcher.read(("sz000001","sh000001"),8),batcher.read(("sh600519",),8))
            self.assertEqual(provider.calls,[{"sh600519","sz000001","sh000001"}])
            self.assertEqual(len({a["fetched_at"],b["fetched_at"],c["fetched_at"]}),1)
            cached=await batcher.read(("sz000001",),8)
            self.assertTrue(cached["cached"])
            self.assertEqual(cached["fetched_at"],a["fetched_at"])
        finally:
            await batcher.close()

    async def test_partial_failure_marks_only_the_affected_symbol(self):
        class Provider:
            calls=0
            async def quotes(self,codes):
                self.calls+=1
                if self.calls==2:
                    return response([quote("sz000001")],"样本",None)
                return response([quote(code) for code in codes],"样本",None)

        batcher=QuoteBatcher(Provider())
        try:
            await batcher.read(("sh600519","sz000001"),8)
            packet=await batcher.read(("sh600519","sz000001"),0)
            rows={r["code"]:r for r in packet["data"]}
            self.assertTrue(rows["sh600519"]["is_stale"])
            self.assertNotIn("is_stale",rows["sz000001"])
        finally:
            await batcher.close()

    async def test_regressing_quote_does_not_overwrite_latest(self):
        class Provider:
            async def quotes(self, codes):
                rows = [quote(code,time=self.stamp if code == "sh600519" else "14:46:10") for code in codes]
                return response(rows,"样本",None)
        provider = Provider()
        batcher = QuoteBatcher(provider)
        try:
            provider.stamp = "14:46:10"
            await batcher.read(("sh600519","sz000001"),0)
            provider.stamp = "14:46:00"
            result = await batcher.read(("sh600519","sz000001"),0)
            a = next(r for r in result["data"] if r["code"]=="sh600519")
            self.assertIn("14:46:10",a["market_time"])
            self.assertTrue(a["is_stale"])
        finally:
            await batcher.close()

    async def test_open_live_pool_is_rechecked_and_history_is_separate(self):
        opening = "2026-11-10T09:40:00+08:00"

        class Provider:
            async def idea_pool(self, session):
                row = quote(time="09:39:00")
                return response([row],"榜单样本","https://example.test/market",row["market_time"])

            async def quotes(self, codes):
                row = quote(time="09:39:54")
                return response([row],"报价样本",None,row["market_time"])

        m = Market(Cache(":memory:"), Provider())
        clock = {"time":opening,"phase":"morning","active":True,"latest_day":"2026-11-10"}
        try:
            with patch.object(m,"clock",return_value=clock):
                pending = await m.ideas("live","open")
                self.assertEqual(pending["data"]["status"],"loading")
                await asyncio.gather(*m.cache.tasks.values())
                picks = await m.ideas("live","open")
                self.assertEqual(picks["data"]["status"],"ready")
                self.assertEqual(picks["data"]["items"][0]["code"],"sh600519")
                self.assertEqual(picks["data"]["pool_source"],"榜单样本")
            with patch.object(m,"clock",return_value={**clock,"time":"2026-11-10T10:00:00+08:00"}):
                history = await m.ideas("live","open")
            self.assertEqual(history["data"]["status"],"history")
            self.assertTrue(history["is_stale"])
        finally:
            await m.close()

    async def test_closed_session_does_not_query_candidate_provider(self):
        class Provider:
            async def idea_pool(self,session):
                raise AssertionError("收盘集合竞价不扫描候选")

        m=Market(Cache(":memory:"),Provider())
        try:
            with patch.object(m,"clock",return_value={"time":"2026-11-10T14:57:00+08:00","phase":"close_auction"}):
                p=await m.ideas("live","tail")
            self.assertEqual(p["data"]["status"],"closed")
            self.assertEqual(p["data"]["items"],[])
        finally:
            await m.close()


class IdeaTests(unittest.TestCase):
    def test_window_boundaries_and_refresh_intervals(self):
        for session,clock,expected in [
            ("open",{"phase":"open_auction","time":"2026-11-10T09:20:00+08:00"},False),
            ("open",{"phase":"morning","time":"2026-11-10T09:30:00+08:00"},True),
            ("open",{"phase":"morning","time":"2026-11-10T10:00:00+08:00"},False),
            ("tail",{"phase":"afternoon","time":"2026-11-10T14:56:59+08:00"},True),
            ("tail",{"phase":"close_auction","time":"2026-11-10T14:57:00+08:00"},False),
        ]:
            self.assertEqual(window_open(session,clock),expected)
        self.assertEqual([quote_interval("live",{"active":v}) for v in (True,False)],[8,45])

    def test_open_rejects_stale_quote_and_risk_names(self):
        current="2026-11-10T09:40:00+08:00"
        q=quote(time="09:39:45")
        pick,_=rank_candidate("open",q,q,current)
        self.assertTrue(0<=pick["score"]<=100)
        old=quote(time="09:37:00")
        self.assertEqual(rank_candidate("open",old,old,current),(None,False))
        self.assertEqual(shortlist("open",[{**q,"name":"*ST样本"}],current),[])

    def test_tail_requires_same_session_reference_and_turnover_growth(self):
        q=quote()
        now=q["market_time"]
        good=quote(time="14:45:35",price=11.15,amount=1.95e8)
        too_early=quote(time="14:44:59")
        self.assertEqual(rank_candidate("tail",q,q,now),(None,True))
        history=[(too_early["market_time"],too_early),(good["market_time"],good)]
        previous=prior_tail(history,now)
        pick,waiting=rank_candidate("tail",q,q,now,previous)
        self.assertFalse(waiting)
        self.assertAlmostEqual(pick["tail_amount"],5e6)
        self.assertEqual(rank_candidate("tail",q,q,now,{**good,"amount":q["amount"]}),(None,False))

    def test_demo_ideas_and_websocket_subscription(self):
        def forbidden(_):
            raise AssertionError("演示模式不应访问外部接口")
        with TestClient(create_app(database=":memory:",transport=httpx.MockTransport(forbidden)), base_url="http://127.0.0.1", client=("127.0.0.1",50000)) as c:
            for session in ("open","tail"):
                p=c.get(f"/api/stocks/buy-candidates?mode=demo&session={session}").json()
                self.assertEqual(p["mode"],"demo")
                self.assertEqual(p["data"]["status"],"ready")
                self.assertTrue(p["data"]["items"])
            with c.websocket_connect("ws://127.0.0.1/ws/market?mode=demo&codes=600519") as ws:
                first=ws.receive_json()
                self.assertEqual(first["codes"],["sh600519"])
                ws.send_json({"type":"subscribe","codes":"000001"})
                changed=ws.receive_json()
                self.assertEqual(changed["codes"],["sz000001"])
                self.assertEqual(changed["quotes"]["data"][0]["code"],"sz000001")
                ws.send_json({"type":"refresh"})
                self.assertEqual(ws.receive_json()["clock"]["quote_interval_s"],5)

    def test_live_watch_screener_needs_only_quotes_without_fund_rules(self):
        def provider(req):
            if req.url.host=="qt.gtimg.cn":
                from test_pulse import tencent
                return httpx.Response(200,content=tencent(["sh600519"]))
            if req.url.host=="www.szse.cn":
                return httpx.Response(503)
            raise AssertionError(f"unexpected market universe call {req.url.host}")
        with TestClient(create_app(database=":memory:",transport=httpx.MockTransport(provider)), base_url="http://127.0.0.1", client=("127.0.0.1",50000)) as c, patch(
                "backend.main.now",return_value=datetime(2026,11,10,14,25,tzinfo=SH)):
            p=c.post("/api/stocks/screen",json={"scope":"watch","codes":["600519"],"ranges":[{"field":"pct","minimum":1}]}).json()
            self.assertEqual(p["data"]["matched"],1)
            self.assertIn("自选快速筛选",p["note"])
