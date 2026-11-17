"""Protect the local browser interface and bound request work."""
import asyncio
import re
from collections import deque
from ipaddress import ip_address

from starlette.responses import PlainTextResponse

LOOPBACK = ("127.0.0.1", "localhost", "::1")
HOST = re.compile(r"(?:127\.0\.0\.1|localhost|\[::1\])(?::([0-9]{1,5}))?", re.ASCII)
BODY_LIMIT = 16 * 1024
WS_MESSAGE_LIMIT = 1024
WS_CLIENT_LIMIT = 12
SERVER_LIMITS = {"proxy_headers":False, "limit_concurrency":64, "timeout_keep_alive":5,
                 "ws":"websockets", "ws_max_size":4096, "ws_max_queue":8, "ws_per_message_deflate":False}


class LocalAccess:
    def __init__(self, app):
        self.app = app
        self.websockets = 0

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = scope["headers"]
        hosts = [value.decode("latin1").lower() for name, value in headers if name == b"host"]
        origins = [value.decode("latin1") for name, value in headers if name == b"origin"]
        fetch_site = {value for name, value in headers if name == b"sec-fetch-site"}
        host = hosts[0] if len(hosts) == 1 else ""
        match = HOST.fullmatch(host)
        path = scope["path"]
        protected = kind == "websocket" or path == "/api" or path.startswith("/api/")
        browser_origin = f"http://{host}"
        try:
            peer = ip_address(scope["client"][0])
            loopback = peer.is_loopback
        except (KeyError, TypeError, ValueError):
            loopback = False
        allowed = bool(loopback and match and (match[1] is None or 1 <= int(match[1]) <= 65535))
        if protected:
            allowed = (allowed and (not origins or origins == [browser_origin])
                       and fetch_site <= {b"same-origin", b"none"})
        if kind == "websocket":
            if not allowed or self.websockets >= WS_CLIENT_LIMIT:
                await send({"type":"websocket.close", "code":1008 if not allowed else 1013})
                return
            self.websockets += 1
            try:
                await self.app(scope, receive, send)
            finally:
                self.websockets -= 1
            return
        if not allowed:
            await PlainTextResponse("Pulse accepts local same-origin requests only.", status_code=403)(scope, receive, send)
            return

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                    (b"content-security-policy",
                     ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                      "img-src 'self' data: blob:; font-src 'self'; "
                      f"connect-src 'self' ws://{host}; base-uri 'none'; frame-ancestors 'none'; "
                      "form-action 'none'").encode("ascii")),
                ]
                if protected:
                    message["headers"].append((b"cache-control", b"no-store"))
                elif any(name == b"content-type" and value.startswith(b"text/html") for name, value in message["headers"]):
                    message["headers"].append((b"cache-control", b"no-cache"))
            await send(message)

        if scope["method"] == "POST" and path.rstrip("/") == "/api/stocks/screen":
            body = bytearray()
            try:
                async with asyncio.timeout(5):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > BODY_LIMIT:
                            await PlainTextResponse("Request body too large.", status_code=413)(scope, receive, secured_send)
                            return
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                await PlainTextResponse("Request body timed out.", status_code=408)(scope, receive, secured_send)
                return

            async def buffered_receive():
                nonlocal body
                if body is None:
                    return await receive()
                data, body = bytes(body), None
                return {"type":"http.request", "body":data, "more_body":False}

            await self.app(scope, buffered_receive, secured_send)
        else:
            await self.app(scope, receive, secured_send)


class ControlRate:
    def __init__(self):
        self.requests = deque()

    def allow(self, current):
        while self.requests and self.requests[0] <= current - 5:
            self.requests.popleft()
        if len(self.requests) >= 12:
            return False
        self.requests.append(current)
        return True
