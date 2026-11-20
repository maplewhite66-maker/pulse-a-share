import json
import tempfile
import unittest
from pathlib import Path
from contextlib import ExitStack

import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.local_access import BODY_LIMIT, WS_CLIENT_LIMIT, WS_MESSAGE_LIMIT
from backend.main import Market, create_app
from backend.services.cache import Cache
from backend.services.paper import executable

BASE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765/ws/market?mode=demo&codes=600519"


def client(peer="127.0.0.1"):
    app = create_app(database=":memory:", transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    return TestClient(app, base_url=BASE, client=(peer,50000))


class AccessTests(unittest.TestCase):
    def test_rebinding_cross_origin_and_nonlocal_peer(self):
        with client() as c:
            route = "/api/paper/account"
            self.assertEqual(c.get(route,headers={"Host":"rebind.example:8765","Origin":"http://rebind.example:8765"}).status_code,403)
            self.assertEqual(c.get(route,headers={"Host":"127.0.0.1.evil.test"}).status_code,403)
            self.assertEqual(c.get(route,headers={"Origin":"http://localhost:8765"}).status_code,403)
            self.assertEqual(c.get(route,headers={"Origin":"null"}).status_code,403)
            self.assertEqual(c.get(route,headers={"Sec-Fetch-Site":"cross-site"}).status_code,403)
            same = c.get(route,headers={"Origin":BASE,"Sec-Fetch-Site":"same-origin"})
            self.assertEqual(same.status_code,200)
            self.assertEqual(same.headers["cache-control"],"no-store")
            self.assertEqual(c.get(route,headers={"Host":"[::1]:8765","Origin":"http://[::1]:8765"}).status_code,200)
        with client("192.0.2.15") as c:
            self.assertEqual(c.get("/").status_code,403)

    def test_security_headers_no_external_api_docs_and_limited_body(self):
        with client() as c:
            page = c.get("/")
            self.assertEqual(page.status_code,200)
            csp = page.headers["content-security-policy"]
            self.assertIn("script-src 'self'",csp)
            self.assertIn("frame-ancestors 'none'",csp)
            self.assertIn("ws://127.0.0.1:8765",csp)
            self.assertEqual(c.get("/docs").json()["info"]["version"],"4.0.0")
            url = "/api/stocks/screen?mode=demo"
            self.assertEqual(c.post(url,json={"scope":"watch","codes":["600519"]}).status_code,200)
            chunks = (chunk for chunk in (b" " * (BODY_LIMIT - 1), b" " * 2))
            self.assertEqual(c.post(url,headers={"Content-Type":"application/json"},content=chunks).status_code,413)

    def test_websocket_blocks_cross_origin_binary_large_and_bursts(self):
        with client() as c:
            for url, headers in ((WS,{"Origin":"https://site.example"}),
                                 (WS.replace("127.0.0.1","rebind.example"),{"Origin":"http://rebind.example:8765"})):
                with self.assertRaises(WebSocketDisconnect) as rejected:
                    with c.websocket_connect(url,headers=headers):
                        self.fail("unsafe handshake")
                self.assertEqual(rejected.exception.code,1008)
            for mode in ("binary","oversize","burst"):
                with c.websocket_connect(WS,headers={"Origin":BASE}) as ws:
                    self.assertEqual(ws.receive_json()["codes"],["sh600519"])
                    if mode == "binary":
                        ws.send_bytes(b"{}")
                    elif mode == "oversize":
                        ws.send_text("股" * (WS_MESSAGE_LIMIT // len("股".encode("utf-8")) + 1))
                    else:
                        for _ in range(13):
                            ws.send_text("{}")
                    with self.assertRaises(WebSocketDisconnect) as closed:
                        ws.receive_json()
                    self.assertEqual(closed.exception.code,{"binary":1003,"oversize":1009,"burst":1008}[mode])


    def test_websocket_connection_limit_releases_on_disconnect(self):
        with client() as c:
            with ExitStack() as connections:
                for _ in range(WS_CLIENT_LIMIT):
                    connections.enter_context(c.websocket_connect(WS,headers={"Origin":BASE}))
                with self.assertRaises(WebSocketDisconnect) as rejected:
                    with c.websocket_connect(WS,headers={"Origin":BASE}):
                        self.fail("connection limit was bypassed")
                self.assertEqual(rejected.exception.code,1013)
            with c.websocket_connect(WS,headers={"Origin":BASE}) as ws:
                self.assertEqual(ws.receive_json()["codes"],["sh600519"])


class AccountTests(unittest.TestCase):
    def test_existing_database_and_two_workers_do_not_lose_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper.db"
            one, two = (Market(Cache(path),object()) for _ in range(2))
            try:
                a = one.paper.load()
                a["cash"] -= 200
                with one.cache.db:
                    one.cache.db.execute("INSERT INTO paper_account VALUES (1, ?)", (json.dumps(a),))
                first, outdated = one.paper.load(), two.paper.load()
                first["cash"] -= 50
                self.assertTrue(one.paper.save(first))
                outdated["cash"] -= 10
                self.assertFalse(two.paper.save(outdated))
                self.assertEqual(two.paper.load()["cash"],first["cash"])
                self.assertEqual(two.paper.load()["_revision"],1)
            finally:
                for m in (one,two):
                    m.cache.db.close()

    def test_execution_rejects_future_auction_locked_and_bad_depth(self):
        def q(stamp, bid=10, ask=10.01, lots=1):
            return {"market_time":"2026-11-11T"+stamp+"+08:00","book":[{"bid":bid,"ask":ask,"ask_lots":lots}]}
        current = "2026-11-11T11:00:30+08:00"
        self.assertEqual(executable(q("11:00:25"),"ask",current)[1],100)
        for quote in (q("11:00:31"),q("11:00:25",ask=10),q("11:00:25",lots=-2),q("11:00:25",lots=1.3)):
            self.assertIsNone(executable(quote,"ask",current))
        self.assertIsNone(executable(q("14:56:59"),"ask","2026-11-11T14:57:00+08:00"))
