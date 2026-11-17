"""Bound public requests and enforce host/origin rules behind Railway TLS."""
import asyncio
import os
import time
from collections import OrderedDict, deque
from urllib.parse import parse_qs

from starlette.responses import PlainTextResponse

from backend.local_access import BODY_LIMIT, WS_CLIENT_LIMIT


class CloudAccess:
    def __init__(self, app):
        self.app = app
        self.hosts = {h.strip().lower() for h in os.getenv("PULSE_ALLOWED_HOSTS", "").split(",") if h.strip()}
        self.hosts.update(filter(None, (os.getenv("RAILWAY_PUBLIC_DOMAIN"), os.getenv("RAILWAY_PRIVATE_DOMAIN"))))
        self.websockets = 0
        self.rates = OrderedDict()

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = scope["headers"]
        hosts = [v.decode("latin1").lower() for k, v in headers if k == b"host"]
        origins = [v.decode("latin1") for k, v in headers if k == b"origin"]
        host = hosts[0] if len(hosts) == 1 else ""
        path = scope["path"]
        protected = path.startswith(("/api/", "/ws/"))
        query = parse_qs(scope.get("query_string", b"").decode("ascii", "replace"))
        demo = "demo" in query.get("mode", [])
        allowed_host = host in self.hosts
        # Railway probes do not promise a public Host header. This route reveals no state.
        probe = path == "/healthz" and kind == "http"
        allowed_origin = not origins or origins == [f"https://{host}"]
        fetch_site = {v for k, v in headers if k == b"sec-fetch-site"}
        allowed = (allowed_host or probe) and (not protected or allowed_origin and fetch_site <= {b"same-origin", b"none"})
        if demo or path in {"/docs", "/openapi.json"}:
            allowed = False
        # Cloud execution does not grant access to the account through public routes.
        if path.startswith("/api/paper/"):
            if kind == "http":
                return await PlainTextResponse("公网不提供模拟账户明细", 403)(scope, receive, send)
            allowed = False
        if not allowed:
            if kind == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await PlainTextResponse("Host, origin or data mode is not allowed.", 403)(scope, receive, send)
        if kind == "websocket":
            if self.websockets >= WS_CLIENT_LIMIT:
                return await send({"type": "websocket.close", "code": 1013})
            self.websockets += 1
            try:
                return await self.app(scope, receive, send)
            finally:
                self.websockets -= 1
        if protected:
            # One shared ceiling also bounds spoofed X-Forwarded-For values.
            current = time.monotonic()
            peer = scope.get("client", ("unknown",))[0]
            for key, maximum in (("all", 900), (peer, 240)):
                queue = self.rates.setdefault(key, deque())
                while queue and queue[0] <= current - 60:
                    queue.popleft()
                if len(queue) >= maximum:
                    return await PlainTextResponse("Too many requests", 429, headers={"Retry-After": "60"})(scope, receive, send)
                queue.append(current)
                self.rates.move_to_end(key)
            while len(self.rates) > 2000:
                self.rates.popitem(last=False)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] += [
                    (b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"strict-transport-security", b"max-age=31536000"),
                    (b"content-security-policy", ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                     "img-src 'self' data: blob:; font-src 'self'; connect-src 'self' wss://" + host +
                     "; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; upgrade-insecure-requests").encode("ascii")),
                    (b"cache-control", b"no-store" if protected else b"no-cache")]
            await send(message)

        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            if scope["method"] != "POST" or path != "/api/stocks/screen":
                return await PlainTextResponse("Method not allowed", 405)(scope, receive, secured_send)
            body = bytearray()
            try:
                async with asyncio.timeout(5):
                    while True:
                        part = await receive()
                        if part["type"] == "http.disconnect":
                            return
                        body.extend(part.get("body", b""))
                        if len(body) > BODY_LIMIT:
                            return await PlainTextResponse("Request body too large", 413)(scope, receive, secured_send)
                        if not part.get("more_body"):
                            break
            except TimeoutError:
                return await PlainTextResponse("Request timeout", 408)(scope, receive, secured_send)
            delivered = False

            async def buffered():
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            return await self.app(scope, buffered, secured_send)
        return await self.app(scope, receive, secured_send)
