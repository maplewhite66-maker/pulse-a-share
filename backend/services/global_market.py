"""Delayed international quotes and a rule-based context for the A-share session."""
import asyncio
import re
from datetime import datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from backend.adapters.live import Unavailable, number, response
from backend.services.clock import SH

NY = ZoneInfo("America/New_York")
HOME = "https://finance.yahoo.com/markets/"
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
SINA_HQ = "https://hq.sinajs.cn/list="
SINA_CASH = {"gb_inx":"^GSPC", "gb_ixic":"^IXIC", "gb_dji":"^DJI"}
# Sina describes these continuous commodity quotes as CFDs. Keep them separate
# from exchange futures; never silently substitute a CFD for an actual contract.
SINA_CFD = {"hf_CL":("WTI原油 CFD", "USD/桶"), "hf_OIL":("布伦特原油 CFD", "USD/桶"),
            "hf_GC":("黄金 CFD", "USD/盎司"), "hf_HG":("铜 CFD", "美分/磅"),
            "hf_NG":("天然气 CFD", "USD/MMBtu"), "hf_CHA50CFD":("富时A50 CFD", "点")}
INSTRUMENTS = {
    "^GSPC": ("标普 500", "us", "点"), "^IXIC": ("纳斯达克综合", "us", "点"),
    "^DJI": ("道琼斯", "us", "点"), "^SOX": ("费城半导体", "us", "点"),
    "^VIX": ("VIX", "us", "点"), "^TNX": ("美债 10 年收益率", "macro", "%"),
    "CNH=X": ("美元／离岸人民币", "macro", "CNH"),
    "ES=F": ("标普 500 期货", "index", "点"), "NQ=F": ("纳指 100 期货", "index", "点"),
    "CL=F": ("WTI 原油", "commodity", "USD/桶"), "BZ=F": ("布伦特原油", "commodity", "USD/桶"),
    "GC=F": ("COMEX 黄金", "commodity", "USD/盎司"), "HG=F": ("COMEX 铜", "commodity", "USD/磅"),
    "NG=F": ("美国天然气", "commodity", "USD/MMBtu"), "ZC=F": ("CBOT 玉米", "commodity", "美分/蒲式耳"),
}


def asset_url(symbol):
    return f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}/"


def timestamp(value):
    try:
        return datetime.fromtimestamp(value, SH) if value else None
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def parse_chart(payload, symbol, current):
    result = payload["chart"]["result"][0]
    meta = result["meta"]
    price = number(meta.get("regularMarketPrice"))
    stamp = timestamp(meta.get("regularMarketTime"))
    if (meta.get("symbol") != symbol or price is None or price <= 0 or stamp is None
            or stamp > current + timedelta(minutes=2)):
        raise Unavailable("报价字段或时间无效")
    name, group, unit = INSTRUMENTS[symbol]
    zone = NY if group == "us" or symbol == "^TNX" else ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    local_day = stamp.astimezone(zone).date()
    series = {}
    for raw_time, value in zip(result.get("timestamp") or [], result["indicators"]["quote"][0].get("close") or []):
        at, value = timestamp(raw_time), number(value)
        if at and value is not None and value > 0:
            series[at.astimezone(zone).date()] = value
    days = sorted(day for day in series if day < local_day)
    reference = series[days[-1]] if days else None
    regular_end = timestamp(meta.get("currentTradingPeriod", {}).get("regular", {}).get("end"))
    cash = group == "us" or symbol == "^TNX"
    completed = days.copy()
    if cash and local_day in series:
        if regular_end is not None and regular_end.astimezone(zone).date() == local_day:
            complete = stamp >= regular_end and current >= regular_end + timedelta(minutes=2)
        else:
            complete = local_day < current.astimezone(zone).date() and stamp.astimezone(zone).time() >= time(16)
        if complete:
            completed.append(local_day)
    close_day = completed[-1] if completed and cash else None
    close = series[close_day] if close_day else None
    prior_close = series[completed[-2]] if close_day and len(completed) > 1 else None
    return {"symbol":symbol, "name":name, "group":group, "unit":unit, "price":price, "reference":reference,
            "pct":round((price/reference-1)*100, 3) if reference else None, "market_time":stamp.isoformat(),
            "close_date":close_day.isoformat() if close_day else None, "close":close,
            "close_pct":round((close/prior_close-1)*100, 3) if prior_close else None,
            "delay_minutes":number(meta.get("exchangeDataDelayedBy")), "url":asset_url(symbol),
            "provider":"Yahoo Finance", "instrument_type":"futures" if group in {"index","commodity"} else "index_or_macro",
            "change_basis":"上一日线收价"}


def parse_sina(raw, current):
    rows = []
    for code, text in re.findall(r'var hq_str_([^=]+)="([^"]*)"', raw):
        f = text.split(",")
        try:
            if code in SINA_CASH and len(f) >= 27:
                key = SINA_CASH[code]
                name, group, unit = INSTRUMENTS[key]
                price, reference, stated = number(f[1]), number(f[26]), number(f[2])
                stamp = datetime.strptime(f[3], "%Y-%m-%d %H:%M:%S").replace(tzinfo=SH)
                if price is None or reference is None or min(price,reference) <= 0 or stated is None:
                    continue
                pct = (price/reference-1)*100
                if abs(pct-stated) > .05:
                    continue
                local = stamp.astimezone(NY)
                completed = local.weekday() < 5 and local.time() >= time(16) and current >= stamp
                rows.append({"symbol":key,"name":name,"group":group,"unit":unit,"price":price,
                    "reference":reference,"pct":round(pct,3),"market_time":stamp.isoformat(),
                    "close_date":local.date().isoformat() if completed else None,
                    "close":price if completed else None,"close_pct":round(pct,3) if completed else None,
                    "url":SINA_HQ+code,"provider":"新浪财经", "instrument_type":"cash_index",
                    "change_basis":"源端前收价", "delay_minutes":None})
            elif code in SINA_CFD and len(f) >= 14:
                price, reference = number(f[0]), number(f[7])
                stamp = datetime.strptime(f[12]+" "+f[6], "%Y-%m-%d %H:%M:%S").replace(tzinfo=SH)
                if price is None or reference is None or min(price,reference) <= 0:
                    continue
                name, unit = SINA_CFD[code]
                rows.append({"symbol":code,"name":name,"group":"cfd","unit":unit,"price":price,
                    "reference":reference,"pct":round((price/reference-1)*100,3),"market_time":stamp.isoformat(),
                    "close_date":None,"close":None,"close_pct":None,"url":SINA_HQ+code,
                    "provider":"新浪财经", "instrument_type":"cfd", "change_basis":"源端前结算/参考价", "delay_minutes":None})
        except (ValueError,TypeError,IndexError,OverflowError):
            continue
    return [r for r in rows if datetime.fromisoformat(r["market_time"]) <= current + timedelta(minutes=2)]


def context(rows):
    def row(symbol):
        return next((r for r in rows if r["symbol"] == symbol and r["status"] == "available"), {})
    def move(symbol):
        return row(symbol).get("pct")

    es, nq, brent, copper = (move(symbol) for symbol in ("ES=F", "NQ=F", "BZ=F", "HG=F"))
    sp, nas = row("^GSPC"), row("^IXIC")
    same_close = sp.get("close_date") and sp.get("close_date") == nas.get("close_date")
    closes = (sp.get("close_pct"), nas.get("close_pct")) if same_close else (None, None)
    stance = "待确认"
    if None not in (es, nq, *closes):
        if min(es, nq) >= .4 and min(closes) > 0:
            stance = "外盘偏强"
        elif max(es, nq) <= -.4 and max(closes) < 0:
            stance = "外盘偏弱"
        else:
            stance = "外盘分化"
    notes = [{"label":"成长与科技", "text":{
        "外盘偏强":"美股已收盘指数与股指期货同向走强，核对创业板、半导体与成交广度。",
        "外盘偏弱":"美股已收盘指数与股指期货同向走弱，观察科技股能否守住分时均价。",
        "外盘分化":"美股收盘与股指期货尚未形成同向信号，先看 A 股开盘广度。",
        "待确认":"需要同一美东交易日的标普与纳指收盘数据，以及两项股指期货报价。"}[stance]},
        {"label":"能源", "text":"布伦特较前日收价上涨，核对油气板块。煤炭另看国内煤价及煤炭板块。"
            if brent is not None and brent >= 2 else "布伦特较前日收价回落至少 2%，油气动量需重新确认。"
            if brent is not None and brent <= -2 else "观察布伦特及国内能源板块是否出现同向变化。"},
        {"label":"有色与农产品", "text":"铜价波动达到 1%，关注有色板块量价。玉米用作种业的背景信息。"
            if copper is not None and abs(copper) >= 1 else "关注铜价与有色板块是否共振，并核对玉米和种业的各自走势。"}]
    return {"stance":stance, "us_close_date":sp.get("close_date") if same_close else None, "notes":notes}


class GlobalMarket:
    def __init__(self, cache, client):
        self.cache, self.client = cache, client
        self.limit = asyncio.Semaphore(2)
        self.yahoo_after = 0.0

    async def sina(self, current):
        url = SINA_HQ + ",".join((*SINA_CASH, *SINA_CFD))
        try:
            raw = await self.client.get(url, headers={"Referer":"https://finance.sina.com.cn/"})
            raw.raise_for_status()
            rows = parse_sina(raw.content.decode("gbk", "replace"), current)
        except (httpx.HTTPError, ValueError) as exc:
            raise Unavailable("新浪全球行情 · " + type(exc).__name__) from exc
        if not rows:
            raise Unavailable("新浪全球行情 · 数据格式异常或没有有效报价")
        return response(rows, "新浪财经", url, max(r["market_time"] for r in rows))

    async def quote(self, symbol, current):
        errors = []
        async with self.limit:
            if asyncio.get_running_loop().time() < self.yahoo_after:
                raise Unavailable("Yahoo Finance · 限流冷却中（15分钟）")
            for host in HOSTS:
                url = f"https://{host}/v8/finance/chart/{quote(symbol, safe='')}"
                try:
                    async with asyncio.timeout(8):
                        raw = await self.client.get(url, params={"interval":"1d", "range":"5d"})
                        raw.raise_for_status()
                        row = parse_chart(raw.json(), symbol, current)
                    return response(row, "Yahoo Finance", row["url"], row["market_time"])
                except (TimeoutError, httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, Unavailable) as exc:
                    errors.append(f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__)
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                        self.yahoo_after = asyncio.get_running_loop().time() + 900
                        break
        raise Unavailable("Yahoo Finance · " + " / ".join(dict.fromkeys(errors)))

    async def snapshot(self, current, *, wait=False):
        sina = await self.cache.get("live:global:sina-batch", 60, lambda: self.sina(current), defer=not wait, retry_delay=120)
        sina_rows = {r["symbol"]:r for r in sina["data"] or []} if not sina["is_stale"] else {}
        async def one(symbol):
            if symbol in sina_rows:
                return {**sina,"data":sina_rows[symbol]}
            return await self.cache.get(f"live:global:{symbol}", 180,
                lambda: self.quote(symbol, current), defer=not wait, retry_delay=900)
        packets = await asyncio.gather(*(one(symbol) for symbol in INSTRUMENTS))
        rows = []
        for (symbol, (name, group, unit)), packet in zip(INSTRUMENTS.items(), packets):
            row = packet["data"]
            age = (current - datetime.fromisoformat(row["market_time"])).total_seconds() if row else None
            # A recent completed US cash session remains useful overnight and over weekends.
            # Older futures can be displayed, but they never feed the directional rules.
            max_age = (100 if current.weekday() in (0, 5, 6) else 45) * 3600
            if group in {"index", "commodity"} or symbol == "CNH=X":
                max_age = 90 * 60
            status = ("loading" if packet["error"] == "后台采集中" else "missing") if not row else (
                "stale" if age > max_age or packet["is_stale"] and packet["error"] != "后台采集中" else "available")
            rows.append({**(row or {"symbol":symbol,"name":name,"group":group,"unit":unit,"url":asset_url(symbol)}),
                         "status":status,"fetched_at":packet["fetched_at"],"error":packet["error"] if status != "available" else None})
        for row in sina_rows.values():
            if row["group"] == "cfd":
                age = (current-datetime.fromisoformat(row["market_time"])).total_seconds()
                rows.append({**row,"status":"available" if age <= 7200 else "stale", "fetched_at":sina["fetched_at"],"error":None})
        return {"rows":rows, "coverage":sum(r["status"] == "available" for r in rows), "total":len(rows), **context(rows)}
