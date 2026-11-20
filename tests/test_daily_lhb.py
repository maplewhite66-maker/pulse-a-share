import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from xml.etree.ElementTree import fromstring

import httpx
from fastapi.testclient import TestClient

from backend.adapters.live import Unavailable
from backend.main import create_app
from backend.services.cache import Cache
from backend.services.daily_lhb import DailyLhb, HISTORY, REPORTS, aggregate, read_report, render_svg


DAY = "2026-11-20"


def disclosure(trade_id, cause="日涨幅偏离值达到7%", amount=100, day=DAY, name="上榜测试股"):
    return {"TRADE_DATE": day, "SECUCODE": "600123.SH", "TRADE_ID": trade_id,
            "SECURITY_NAME_ABBR": name, "EXPLANATION": cause, "BILLBOARD_DEAL_AMT": amount}


def seat(trade_id, name, buy, sell, *, code="1", net=None):
    return {"TRADE_DATE": DAY, "SECUCODE": "600123.SH", "TRADE_ID": str(trade_id),
            "OPERATEDEPT_CODE": code, "OPERATEDEPT_NAME": name, "BUY": buy, "SELL": sell, "NET": net}


def example():
    return aggregate(DAY, [disclosure(1)], [seat(1, "机构专用", 80, 10)], [seat(1, "甲证券北京营业部", 20, 60)])


class DailyLhbAggregationTests(unittest.TestCase):
    def test_multiple_listing_causes_periods_and_duplicate_seats(self):
        primary = seat(2, "机构专用", 30, 5, code="0")
        broker_buy = seat(2, "银河证券大连金马路营业部", 42, None, code="11", net=42)
        broker_sell = seat(2, "银河证券大连金马路营业部", None, 51, code="11", net=-51)
        data = aggregate(
            DAY, [disclosure(1), disclosure(2, amount=400), disclosure(3, "连续三个交易日累计偏离值")],
            [seat(1, "旧营业部", 10, 0), primary, broker_buy, seat(3, "机构专用", 108, 3)],
            [seat(1, "旧营业部", 0, 10), primary, broker_sell, seat(3, "乙营业部", 2, 100)],
        )
        self.assertEqual((data["buy_net_cents"], data["sell_net_cents"]), (2500, 900))
        self.assertEqual((data["daily_stocks"], data["multi_stocks"], data["matched_events"]), (1, 1, 3))
        self.assertEqual([p["scope"] for p in data["groups"][0]["items"]], ["daily", "three"])
        self.assertEqual(data["groups"][1]["label"], "大连金马路")

    def test_blank_opposite_amount_requires_source_net(self):
        for net in (None, 50):
            with self.subTest(net=net), self.assertRaises(ValueError):
                aggregate(DAY, [disclosure(1)], [seat(1, "甲", 10, None, net=net)], [seat(1, "乙", 0, 8)])
        with self.assertRaises(ValueError):
            aggregate(DAY, [disclosure(1, day="2026-11-19")], [], [])
        with self.assertRaises(Unavailable):
            aggregate(DAY, [disclosure(1)], [seat(1, "甲", 10, 0)], [])

    def test_svg_escapes_disclosed_names_and_accepts_zero_net(self):
        raw_name = '<script>&"测试'
        raw = aggregate(DAY, [disclosure(1, name=raw_name)],
                        [seat(1, raw_name, 20, 1)], [seat(1, "乙", 1, 20)])
        raw.update(generated_at=DAY + "T17:01:00+08:00")
        img = render_svg(raw)
        self.assertNotIn("<script>", img)
        self.assertIn("&lt;script&gt;&amp;", img)
        self.assertEqual(fromstring(img).get("width"), "1120")
        zeros = aggregate(DAY, [disclosure(1)], [seat(1, "甲", 12, 12)], [seat(1, "乙", 0, 0)])
        self.assertEqual(zeros["groups"], [])
        self.assertTrue(render_svg({**zeros, "generated_at": raw["generated_at"]}).endswith("</svg>"))


class DailyLhbSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_pagination_checks_second_page_and_changed_count(self):
        def page(size, count):
            return httpx.Response(200, json={"result": {"count": count, "pages": 2, "data": [{}] * size}})

        provider = SimpleNamespace(get=AsyncMock(side_effect=[page(200, 201), page(1, 201)]))
        self.assertEqual(len(await read_report(provider, REPORTS[1], DAY)), 201)
        self.assertEqual(provider.get.await_args.args[1]["pageNumber"], 2)
        provider.get.side_effect = [page(200, 201), page(2, 202)]
        with self.assertRaises(Unavailable):
            await read_report(provider, REPORTS[1], DAY)

    async def test_calendar_retry_and_preserve_archive_during_partial_refresh(self):
        cache = Cache(":memory:")
        self.addAsyncCleanup(cache.close)
        clock = AsyncMock(return_value={"phase": "closed", "latest_day": DAY, "calendar_error": None})
        loader = AsyncMock(side_effect=[Unavailable("rate limited"), example(), {**example(), "unmatched_events": 1}])
        reporter = DailyLhb(SimpleNamespace(cache=cache, clock=clock, providers={"live": object()}), True, loader)
        current = datetime.fromisoformat(DAY + "T17:00:00+08:00")
        with patch("backend.services.daily_lhb.now", return_value=current):
            await reporter.tick(current.replace(hour=16))
            clock.return_value = {"phase": "closed", "latest_day": "2026-11-19"}
            await reporter.tick(current)
            loader.assert_not_called()
            clock.return_value = {"phase": "closed", "latest_day": DAY}
            await reporter.tick(current.replace(minute=5))
            await reporter.tick(current.replace(minute=7))
            loader.assert_awaited_once()
            await reporter.tick(current.replace(minute=10))
            stored = reporter.get(None)
            self.assertEqual(stored["slot"], "17:00")
            self.assertEqual(reporter.list_days(), [DAY])
            await reporter.tick(current.replace(hour=20, minute=30))
            self.assertEqual(reporter.get(DAY), stored)
            await reporter.tick(current.replace(day=21))
            self.assertEqual(loader.await_count, 3)


class DailyLhbEndpointTests(unittest.TestCase):
    def test_archive_image_and_cloud_policy(self):
        with patch.dict(os.environ, {"PULSE_ALLOWED_HOSTS": "pulse.example.com",
                                     "PULSE_SCHEDULER": "0", "PULSE_LHB_SCHEDULER": "0"}):
            app = create_app(database=":memory:", transport=httpx.MockTransport(lambda _: httpx.Response(503)),
                             access="cloud")
            with TestClient(app, base_url="https://pulse.example.com") as client:
                api = "/api/reports/daily-lhb"
                image = f"{api}/{DAY}.svg"
                self.assertIsNone(client.get(api).json()["data"]["report"])
                self.assertEqual(client.get(image).status_code, 404)
                self.assertEqual(client.get(api + "?day=invalid").status_code, 422)
                self.assertEqual(client.get(api + "?mode=demo").status_code, 403)
                self.assertEqual(client.get(api, headers={"Origin": "https://outside.example.com"}).status_code, 403)
                self.assertEqual(client.get("/api/paper/account").status_code, 403)
                saved = {**example(), "slot": "17:00", "generated_at": DAY + "T17:02:00+08:00", "revision": "test"}

                async def store():
                    app.state.market.cache.record(HISTORY, DAY, saved)

                client.portal.call(store)
                self.assertEqual(client.get(api).json()["data"]["days"], [DAY])
                self.assertEqual(client.get(api + "?day=" + DAY).json()["data"]["report"], saved)
                result = client.get(image)
                self.assertEqual(result.headers["content-type"], "image/svg+xml")
                self.assertEqual(result.headers["etag"], '"test"')
                self.assertEqual(fromstring(result.content).get("role"), "img")
