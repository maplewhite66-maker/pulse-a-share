"""Merge overlapping quote reads across HTTP callers and WebSocket subscribers."""
import asyncio
from dataclasses import dataclass

from backend.adapters.live import Unavailable
from backend.services.clock import now

# Sixty watchlist symbols, one hundred tracked stocks and the open chart.
QUOTE_SYMBOL_LIMIT = 161
# A fully qualified ticker such as 600519.SH has nine characters plus a separator.
QUOTE_LIST_LIMIT = QUOTE_SYMBOL_LIMIT * 10


@dataclass
class Sample:
    row: dict
    fetched_at: str
    source: str
    source_url: str | None
    at: float


class QuoteBatcher:
    def __init__(self, provider):
        self.provider = provider
        self.samples: dict[str, Sample] = {}
        self.pending: dict[str, asyncio.Future] = {}
        self.queue: list[str] = []
        self.task = None
        self.next_request = 0.0
        self.failures: dict[str, tuple[float, str]] = {}

    async def read(self, codes, ttl):
        loop = asyncio.get_running_loop()
        samples, errors, waiters = [], [], []
        for code in codes:
            sample = self.samples.get(code)
            if sample and loop.time() - sample.at < ttl:
                samples.append(sample)
                continue
            failure = self.failures.get(code)
            if failure and loop.time() < failure[0]:
                errors.append(code)
                continue
            if code not in self.pending:
                self.pending[code] = loop.create_future()
                self.queue.append(code)
            waiters.append((code, self.pending[code]))
        if self.queue and self.task is None:
            self.task = asyncio.create_task(self.flush())
        results = await asyncio.gather(*(asyncio.shield(future) for _, future in waiters))
        reason = None
        for (code, _), result in zip(waiters, results):
            if isinstance(result, Sample):
                samples.append(result)
            else:
                errors.append(code)
                reason = result
        if not samples:
            failure = next((self.failures[c][1] for c in errors if c in self.failures), None)
            raise Unavailable(reason or failure or "报价暂不可用")
        latest = max(samples, key=lambda sample: sample.fetched_at)
        rows = [{**s.row, "quote_fetched_at": s.fetched_at} for s in samples]
        rows.extend({**self.samples[code].row, "quote_fetched_at": self.samples[code].fetched_at, "is_stale": True}
                    for code in errors if code in self.samples)
        rows.sort(key=lambda row: row["code"])
        return {"data": rows, "source": latest.source, "source_url": latest.source_url,
                "fetched_at": latest.fetched_at, "market_time": max((r["market_time"] for r in rows if r["market_time"]), default=None),
                "note": "部分报价未更新 " + "、".join(errors) if errors else None, "cached": not waiters}

    async def flush(self):
        loop = asyncio.get_running_loop()
        try:
            await asyncio.sleep(.025)
            while self.queue:
                codes, self.queue = self.queue[:60], self.queue[60:]
                await asyncio.sleep(max(0, self.next_request - loop.time()))
                try:
                    packet = await self.provider.quotes(tuple(codes))
                    rows = {r["code"]: r for r in packet["data"]}
                    stamp = now().isoformat(timespec="milliseconds")
                    for code in codes:
                        if code not in rows:
                            self.fail(code, "上游缺少该标的报价", 25)
                            continue
                        previous = self.samples.get(code)
                        old_time = previous.row["market_time"] if previous else None
                        new_time = rows[code]["market_time"]
                        if old_time and (not new_time or new_time < old_time):
                            self.fail(code, "上游报价时间回退或缺失", 8)
                            continue
                        sample = Sample(rows[code], stamp, packet["source"], packet["source_url"], loop.time())
                        self.samples[code] = sample
                        self.failures.pop(code, None)
                        self.pending.pop(code).set_result(sample)
                except (Unavailable, KeyError, ValueError, TypeError, AttributeError) as exc:
                    error = str(exc) if isinstance(exc, Unavailable) else "报价响应字段异常"
                    for code in codes:
                        if code in self.pending:
                            self.fail(code, error, 8)
                finally:
                    self.next_request = loop.time() + .25
                if len(self.samples) > 1200:
                    self.samples = dict(sorted(self.samples.items(), key=lambda item: item[1].at, reverse=True)[:1200])
                if len(self.failures) > 1200:
                    self.failures = {code: v for code, v in self.failures.items() if v[0] > loop.time()}
        finally:
            self.task = None

    def fail(self, code, error, cooldown):
        self.failures[code] = (asyncio.get_running_loop().time() + cooldown, error)
        self.pending.pop(code).set_result(error)

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for future in self.pending.values():
            future.cancel()
        self.pending.clear()
        self.queue.clear()


def quote_interval(mode, clock):
    if mode == "demo":
        return 5
    return 8 if clock["active"] else 45
