import asyncio
import calendar
import unittest
from datetime import date, datetime
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.adapters.live import INDICES, quote_rows, symbol
from backend.main import create_app
from backend.services.analytics import indicators, market_mood, percentile, quote_signals, weighted
from backend.services.cache import Cache
from backend.services.clock import SH, phase


def tencent(symbols):
    lines = []
    for code in symbols:
        f = [""] * 53
        for index, value in {1:INDICES.get(code,"测试证券"), 2:code[2:],3:"12.5",4:"12",5:"12.1",30:"20261110142500",
                             31:".5",32:"4.17",36:"500",37:"24",38:"3.5",43:"4",44:"16",45:"18",49:"2.1"}.items():
            f[index]=value
        lines.append(f'v_{code}="'+'~'.join(f)+'";')
    return '\n'.join(lines).encode("gbk")


class AnalyticsTests(unittest.TestCase):
    def test_symbol_and_units(self):
        self.assertEqual(symbol("000001"),"sz000001")
        self.assertEqual(symbol("000001.SH"),"sh000001")
        self.assertEqual(symbol("920001"),"bj920001")
        self.assertEqual(symbol("830001"),"bj830001")
        with self.assertRaises(ValueError):
            symbol("sz000001.sh")
        rows=quote_rows(tencent(["sh000001","sz000001"]).decode("gbk"),["sh000001","sz000001"])
        self.assertEqual([q["code"] for q in rows],["sh000001","sz000001"])
        self.assertEqual(rows[0]["amount"],240000)
        self.assertEqual(rows[0]["float_cap"],1.6e9)
        self.assertEqual(rows[0]["market_cap"],1.8e9)
        self.assertEqual(rows[0]["volume"],50000)
        self.assertEqual(rows[0]["market_time"],"2026-11-10T14:25:00+08:00")

    def test_clock_and_auction_boundaries(self):
        def state(day,h,m,days):
            return phase(datetime(2026,11,day,h,m,tzinfo=SH),days)[0]
        days=["2026-11-10"]
        for h,m,s in [(9,14,"before"),(9,15,"open_auction"),(9,25,"pause"),(9,30,"morning"),
                      (11,30,"lunch"),(13,0,"afternoon"),(14,57,"close_auction"),(15,0,"closed")]:
            self.assertEqual(state(10,h,m,days),s)
        self.assertEqual(state(11,10,0,days),"holiday")
        self.assertEqual(state(10,10,0,None),"unknown")
        self.assertEqual(state(14,10,0,[]),"weekend")

    def test_indicators_and_score_coverage(self):
        rows=indicators([{"close":float(i+1)} for i in range(40)])
        self.assertIsNone(rows[3]["ma5"])
        self.assertEqual(rows[4]["ma5"],3)
        self.assertEqual(rows[19]["ma20"],10.5)
        self.assertEqual(rows[14]["rsi"],100)
        self.assertIsNone(rows[32]["macd"])
        self.assertIsNotNone(rows[33]["macd"])
        self.assertIsNone(weighted({"a":80,"b":None},(50,50),.65)["score"])
        self.assertEqual(percentile([{"code":"a","x":1},{"code":"b","x":1},{"code":"c","x":3}],"x"),{"a":25,"b":25,"c":100})
        mood=market_mood({"up":20,"down":30},[{"streak":3}],[],[{}],[{"pct":2}])
        self.assertEqual(mood["break_rate"],50)
        self.assertEqual(mood["streak"],3)
        self.assertIsNotNone(mood["heat"]["score"])

    def test_short_term_signal_uses_observation_time(self):
        previous={"market_time":"2026-11-10T14:49:00+08:00","price":10}
        current={"market_time":"2026-11-10T14:50:00+08:00","price":10.15,"pct":3.1,"turnover":6,"vol_ratio":2.7}
        events=[r[0] for r in quote_signals(previous,current,3,5,1,2.5,True)]
        self.assertIn("短时拉升",events)
        self.assertIn("尾盘价格异动",events)
        previous["market_time"]="2026-11-10T14:40:00+08:00"
        self.assertNotIn("短时拉升",[r[0] for r in quote_signals(previous,current,3,5,1,2.5,True)])


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_flight_stale_and_mode_isolation(self):
        from backend.adapters.live import Unavailable
        cache=Cache(":memory:")
        calls=0
        async def okay():
            nonlocal calls
            calls+=1
            await asyncio.sleep(.01)
            return {"data":[1],"source":"test","source_url":None,"market_time":None}
        async def down():
            raise Unavailable("测试故障")
        try:
            result=await asyncio.gather(*(cache.get("live:key",0,okay) for _ in range(8)))
            self.assertEqual(calls,1)
            self.assertTrue(all(r["data"]==[1] for r in result))
            failed=await cache.get("live:key",0,down)
            self.assertTrue(failed["is_stale"] and failed["cached"])
            self.assertEqual(failed["data"],[1])
            self.assertEqual(failed["error"],"测试故障")
            self.assertIsNone((await cache.get("demo:key",0,down))["data"])
        finally:
            await cache.close()

    async def test_eastmoney_redirect_preserves_source_and_filters(self):
        from backend.adapters.live import BOARD, Live
        hosts=[]
        def transport(req):
            hosts.append(req.url.host)
            self.assertEqual(req.url.params["fs"], "m:90+t:2")
            if req.url.host=="push2.eastmoney.com":
                return httpx.Response(302,headers={"Location":"//push2delay.eastmoney.com/api/qt/clist/get"})
            return httpx.Response(200,json={"rc":0,"data":{"total":1,"diff":[
                {"f12":"BK1201","f14":"示例板块","f62":1e8,"f184":"-","f3":1.2,"f6":1e9}]}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            provider=Live(client)
            result=await provider.sectors("industry","today","inflow")
        self.assertEqual(hosts,["push2.eastmoney.com","push2delay.eastmoney.com"])
        self.assertEqual(result["source_url"],BOARD.replace("push2.","push2delay."))
        self.assertIsNone(result["market_time"])
        self.assertIsNone(result["data"]["items"][0]["main_pct"])
        self.assertEqual(result["data"]["items"][0]["main_net"],1e8)

    async def test_history_uses_distinct_observation_timestamps(self):
        cache=Cache(":memory:")
        try:
            cache.record("live:rank","2026-11-10T13:00:00+08:00",[1])
            cache.record("live:rank","2026-11-10T13:00:00+08:00",[2])
            cache.record("live:rank","2026-11-10T13:01:00+08:00",[3])
            self.assertEqual([x[1] for x in cache.history("live:rank")],[[3],[1]])
        finally:
            await cache.close()


class ApiTests(unittest.TestCase):
    def test_demo_offline_routes_and_websocket(self):
        def forbidden(req):
            raise AssertionError(f"Demo tried external host {req.url.host}")
        with TestClient(create_app(database=":memory:",transport=httpx.MockTransport(forbidden)), base_url="http://127.0.0.1", client=("127.0.0.1",50000)) as c:
            data=c.get("/api/market/indices?mode=demo").json()
            self.assertEqual(len(data["data"]),5)
            self.assertEqual(data["mode"],"demo")
            for period in ("m1","m5","m15","m30","m60","day","week","month"):
                candles=c.get(f"/api/stocks/600519/kline?mode=demo&period={period}").json()
                self.assertEqual(len(candles["data"]["items"]),120)
                self.assertIsNotNone(candles["data"]["items"][-1]["ma20"])
            for kind in ("industry","concept","region"):
                r=c.get(f"/api/sectors/ranking?mode=demo&kind={kind}&period=5d&order=outflow").json()
                self.assertLess(r["data"]["items"][0]["main_net"],0)
                self.assertEqual(len(r["data"]["items"]),20)
            self.assertGreater(c.get("/api/market/sentiment?mode=demo").json()["data"]["up"],1000)
            roles=c.get("/api/sectors/BK1000/roles?mode=demo").json()["data"]
            self.assertEqual(len(roles["roles"]),3)
            self.assertTrue(roles["price_coverage"])
            self.assertEqual(len(c.get("/api/stocks/limit-up?mode=demo").json()["data"]),79)
            self.assertEqual(len(c.get("/api/stocks/600519/announcements?mode=demo").json()["data"]),1)
            self.assertIsNone(c.get("/api/tail?mode=demo").json()["data"]["auction"]["matched_volume"])
            self.assertEqual(c.get("/api/stocks/invalid/kline?mode=demo").status_code,422)
            self.assertEqual(c.get("/api/stocks/600519/kline?mode=demo&period=quarter").status_code,422)
            # Use a single, stable signal so a new quote cannot add an unrelated alert.
            alert_url = (
                "/api/alerts?mode=demo&codes=600519&client=test"
                "&pct=0.1&turnover=100&rapid=25&volume=100"
            )
            a=c.get(alert_url).json()["data"]
            b=c.get(alert_url).json()["data"]
            self.assertEqual([(item["code"], item["event"]) for item in a], [("sh600519", "日内跌幅")])
            self.assertEqual([x["id"] for x in a],[x["id"] for x in b])
            with c.websocket_connect("ws://127.0.0.1/ws/market?mode=demo&codes=000001,sh000001") as ws:
                p=ws.receive_json()
                self.assertEqual(p["mode"],"demo")
                self.assertEqual(len(p["quotes"]["data"]),2)

    def test_live_contract_and_provider_error(self):
        def source(req):
            if req.url.host=="www.szse.cn":
                year,month=map(int,req.url.params["month"].split("-"))
                dates=[date(year,month,d) for d in range(1,calendar.monthrange(year,month)[1]+1)]
                return httpx.Response(200,json={"data":[{"jyrq":d.isoformat(),"jybz":str(int(d.weekday()<5))} for d in dates]})
            if req.url.host=="qt.gtimg.cn":
                return httpx.Response(200,content=tencent(req.url.path.split("=")[1].split(",")))
            if req.url.host=="web.ifzq.gtimg.cn":
                code=req.url.params["param"].split(",")[0]
                return httpx.Response(200,json={"data":{code:{"day":[["2026-11-10","12","13","13.2","11.5","200"]]}}})
            return httpx.Response(403,json={"message":"blocked"})
        with TestClient(create_app(database=":memory:",transport=httpx.MockTransport(source)), base_url="http://127.0.0.1", client=("127.0.0.1",50000)) as c, patch(
            "backend.main.now",return_value=datetime(2026,11,10,14,25,tzinfo=SH)):
            h=c.get("/api/system/health").json()
            self.assertEqual(h["data"]["latest_day"],"2026-11-10")
            self.assertEqual(h["data"]["phase"],"afternoon")
            q=c.get("/api/market/indices").json()
            self.assertFalse(q["is_stale"])
            self.assertEqual(len(q["data"]),5)
            self.assertEqual(q["mode"],"live")
            k=c.get("/api/stocks/sh000001/kline?period=day").json()
            self.assertEqual(k["data"]["adjust"],"none")
            s=c.get("/api/sectors/ranking").json()
            self.assertTrue(s["error"])
            self.assertTrue(s["is_stale"])
            self.assertIsNone(s["data"])


if __name__=="__main__":
    unittest.main()
