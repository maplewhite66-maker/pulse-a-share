from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, Response

from backend.local_access import ControlRate, LocalAccess, WS_MESSAGE_LIMIT
from backend.cloud_access import CloudAccess
from backend.services.premarket import Premarket
from backend.services.monitor import IntradayMonitor

from backend.adapters import live
from backend.adapters.auction import FuyaoAuction
from backend.adapters.demo import Demo
from backend.services.analytics import clip, indicators, market_mood, quote_signals, role_scores, sector_heat, weighted
from backend.services.cache import Cache
from backend.services.clock import ACTIVE, now, phase
from backend.services.screener import ScreenRequest, market_breadth, screen
from backend.services.quotes import QuoteBatcher, quote_interval
from backend.services.ideas import SessionJournal, WINDOWS, fresh, prior_tail, rank_candidate, shortlist, window_open
from backend.services.hourly import HourlyScanner
from backend.services.paper import PaperAccount
from backend.services.global_market import GlobalMarket
from backend.services.fund_flow import describe_flow

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
Mode = Literal["live", "demo"]
Kind = Literal["industry", "concept", "region"]
Span = Literal["today", "5d", "10d"]
Direction = Literal["inflow", "outflow"]
Period = Literal["m1", "m5", "m15", "m30", "m60", "day", "week", "month"]
Pool = Literal["up", "down", "broken", "yesterday"]
Session = Literal["open", "tail"]
DEFAULT_CODES = ("sh601138", "sz300750", "sz002594", "sh688256", "sh600519", "sz000001")


class Market:
    def __init__(self, cache, provider):
        self.cache = cache
        self.providers = {"live": provider, "demo": Demo()}
        self.quote_batches = {mode: QuoteBatcher(source) for mode, source in self.providers.items()}
        self.aux_task = None
        self.aux_next = 0.0
        self.aux_cursor = 0
        self.hourly = HourlyScanner(self)
        self.paper = PaperAccount(self)
        self.sessions = SessionJournal(self)

    def schedule_observation(self, mode, codes, clock):
        """Warm one monitored stock per interval, shared across live browser tabs."""
        if mode != "live" or not clock["active"] or not codes:
            return
        timestamp = asyncio.get_running_loop().time()
        if timestamp < self.aux_next or (self.aux_task and not self.aux_task.done()):
            return
        self.aux_next = timestamp + (60 if clock["tail"] else 90)
        code = codes[self.aux_cursor % len(codes)]
        self.aux_cursor += 1

        async def scan():
            jobs = [self.get(mode, "kline", 300, code, "day", "qfq", 120),
                    self.get(mode, "stock_flow", 90, code, "minute")]
            if clock["tail"]:
                jobs.append(self.sectors(mode, "industry", "today", "inflow"))
            await asyncio.gather(*jobs)

        self.aux_task = asyncio.create_task(scan())

    async def close(self):
        if self.aux_task:
            self.aux_task.cancel()
            await asyncio.gather(self.aux_task, return_exceptions=True)
        await self.cache.close()
        await asyncio.gather(*(batch.close() for batch in self.quote_batches.values()))

    async def get(self, mode, name, ttl, *args, defer=False):
        method = getattr(self.providers[mode], name)
        key = f"{mode}:{name}:" + ":".join(",".join(a) if isinstance(a, tuple) else str(a) for a in args)
        return await self.cache.get(key, ttl, lambda: method(*args), defer=defer)

    async def clock(self, mode, *, wait=True):
        current = now()
        today = current.date().isoformat()
        if mode == "demo":
            return {"phase":"demo", "label":"离线演示", "time":current.isoformat(), "latest_day":today,
                    "active":True, "tail":True, "calendar_source":None, "calendar_error":None}
        cal = await self.get(mode, "calendar", 21600, current.year, current.month, defer=not wait)
        days = cal["data"]
        state, label = phase(current, days)
        pool_ready = state in ACTIVE - {"open_auction"} or state in {"lunch", "closed"}
        previous = [d for d in days or [] if d < today or (d == today and pool_ready)]
        if days is not None and not previous:
            p = current.replace(day=1) - timedelta(days=1)
            prior = await self.get(mode, "calendar", 21600, p.year, p.month, defer=not wait)
            previous = prior["data"] or []
        return {"phase":state, "label":label, "time":current.isoformat(), "latest_day":max(previous, default=None),
                "active":state in ACTIVE, "tail":state in {"afternoon","close_auction"} and current.hour==14 and current.minute>=45,
                "calendar_source":cal["source"], "calendar_error":cal["error"]}

    async def quotes(self, mode, codes, clock=None, *, refresh=False, show_saved=False):
        if not codes:
            return self.local(mode, [], "自选为空")
        clock = clock or await self.clock(mode, wait=False)
        ttl = 3 if refresh else quote_interval(mode, clock)
        ordered = tuple(sorted(set(codes)))
        key = f"{mode}:quotes:" + ",".join(ordered)
        previous = self.cache.lookup(key) if show_saved and not refresh else None
        defer = previous is not None and previous[0] == 0
        payload = await self.cache.get(key, 0, lambda: self.quote_batches[mode].read(ordered, ttl), defer=defer, retry_delay=8)
        if payload["data"] is None:
            return payload
        rows = []
        t = datetime.fromisoformat(clock["time"])
        for raw in payload["data"]:
            row = raw.copy()
            stamp = row["market_time"]
            stale = payload["is_stale"] or row.get("is_stale", False) or row["inactive"] or stamp is None or (
                clock["latest_day"] is not None and stamp[:10] < clock["latest_day"])
            if stamp and clock["active"] and mode == "live":
                age = (t-datetime.fromisoformat(stamp)).total_seconds()
                stale = stale or not -120 <= age <= 120
            if stamp and mode == "live" and (datetime.fromisoformat(stamp)-t).total_seconds() > 120:
                stale = True
            row["is_stale"] = stale
            if stamp and not stale and not payload["cached"] and clock["active"] and row["code"] not in live.INDICES:
                self.cache.record(f'{mode}:quote:{row["code"]}', stamp, row, keep=300)
            rows.append(row)
        return {**payload, "data":rows, "is_stale":payload["is_stale"] or all(r["is_stale"] for r in rows)}

    async def bars(self, mode, code, period, adjust, count):
        clock = await self.clock(mode)
        if period in live.PERIODS[:5] or code in live.INDICES:
            adjust = ""
        ttl = (25 if period in live.PERIODS[:5] else 45) if clock["active"] else 240
        payload = await self.get(mode, "kline", ttl, code, period, adjust, count)
        if payload["data"] is not None:
            market_time = payload["market_time"]
            day = clock["latest_day"]
            stale = mode == "live" and day is not None and market_time is not None and period not in ("week", "month") and market_time[:10] < day
            items = indicators([r.copy() for r in payload["data"]["items"]])
            return {**payload, "is_stale": payload["is_stale"] or stale, "data":{**payload["data"], "items":items}}
        return payload

    async def sectors(self, mode, kind, period, order):
        clock = await self.clock(mode)
        ttl = 60 if clock["tail"] else 90 if clock["active"] else 300
        payload = await self.get(mode, "sectors", ttl, kind, period, order)
        if payload["data"] is None:
            return payload
        key = f"{mode}:ranks:{kind}:{period}:{order}"
        ranked = [r for r in payload["data"]["items"] if r["main_net"] > 0] if order=="inflow" else [
            r for r in payload["data"]["items"] if r["main_net"] < 0]
        ranked = ranked[:20]
        hist = self.cache.history(key, 120)
        previous = next((r for t, r in hist if t < payload["fetched_at"] and t[:10] == payload["fetched_at"][:10]), [])
        by_code = {r["code"]:r for r in previous}
        rows = []
        for idx, raw in enumerate(ranked, 1):
            old = by_code.get(raw["code"])
            change = old["rank"]-idx if old else None
            rows.append({**raw, "rank":idx, "rank_delta":change, "heat":sector_heat(raw, change), "source_url":payload["source_url"]})
        if not payload["is_stale"] and not payload["cached"]:
            self.cache.record(key, payload["fetched_at"], rows)
        return {**payload, "data":{**payload["data"], "items":rows}}

    async def pool(self, mode, kind, day=None):
        day = day or (await self.clock(mode))["latest_day"]
        if not day:
            return self.cache.fallback(f"{mode}:pool", "缺少已确认的交易日")
        return await self.get(mode, "pool", 120, day, kind)

    async def market_universe(self, clock):
        packet = await self.get("live", "universe", 300 if clock["active"] else 900, defer=True)
        return self.check_snapshot_date(packet, clock)

    def check_snapshot_date(self, packet, clock):
        stamp, day = packet["market_time"], clock["latest_day"]
        if packet["data"] is None or stamp is not None and (day is None or stamp[:10] >= day):
            return packet
        note = " · ".join(filter(None, (packet.get("note"), "行情日期未确认或早于最近交易日")))
        return {**packet, "is_stale": True, "note": note}

    async def breadth(self, mode, clock):
        if mode == "demo":
            return await self.get(mode, "breadth", 300)
        packet = await self.market_universe(clock)
        if packet["data"] is None:
            return self.cache.fallback("live:breadth:", packet["error"])
        data = market_breadth(packet["data"])
        return {**packet, "data": data, "error": packet["error"] if data else "东方财富 · 涨跌幅字段为空"}

    async def screen(self, mode, query):
        if query.scope == "watch" and not query.codes:
            return self.local(mode, screen([], query, None), "自选池为空")
        if (mode == "live" and query.scope == "watch" and query.sort_by != "main_net"
                and all(item.field != "main_net" for item in query.ranges)):
            codes = tuple(c for c in query.codes if c not in live.INDICES)
            packet = await self.quotes(mode, codes)
            if packet["data"] is not None and not packet["is_stale"]:
                packet = {**packet,"data":[row for row in packet["data"] if not row["is_stale"]]}
            packet = {**packet,"note":" · ".join(filter(None,(packet.get("note"),"自选快速筛选 · 主力净额未随报价返回")))}
        elif query.scope == "board":
            packet = await self.get(mode, "members", 300, query.board, defer=mode == "live")
            if mode == "live" and packet["data"] is not None:
                packet = self.check_snapshot_date(packet, await self.clock(mode))
        elif mode == "demo":
            packet = await self.get(mode, "universe", 15, tuple(sorted(query.codes)))
        else:
            packet = await self.market_universe(await self.clock(mode))
        if packet["data"] is None:
            return packet
        return {**packet, "data": screen(packet["data"], query, packet["market_time"])}

    async def ideas(self, mode, session):
        clock = await self.clock(mode, wait=False)
        today = clock["time"][:10]
        key = f"{mode}:ideas:{session}:{today}"
        start, end = WINDOWS[session]
        base = {"session":session,"window":f"{start}–{end}","items":[],"pool_size":0,"checked":0,"waiting":0,
                "pool_source":None,"pool_url":None,"pool_time":None,"pool_note":None}
        if mode == "live" and not window_open(session, clock):
            record = self.cache.lookup(key)
            history = record[1] if record else None
            archived = self.cache.history(f"live:session:{session}", 1)
            if archived and archived[0][0] == today and archived[0][1]["ranking"]:
                ranking = archived[0][1]["ranking"]
                history = {**self.local(mode, ranking, ranking["source"]),
                           "source_url":ranking["source_url"], "market_time":ranking["at"]}
            if history and history["data"]["items"]:
                return {**history,"is_stale":True,"data":{**history["data"],"status":"history"}}
            status = "calendar" if clock["phase"] == "unknown" else "closed"
            return self.local(mode,{**base,"status":status},"Pulse 买入候选")
        pool = await self.get(mode, "idea_pool", 25, session, defer=mode=="live")
        base.update({"pool_source":pool["source"],"pool_url":pool["source_url"],"pool_time":pool["market_time"],
                     "pool_note":pool.get("note"),"pool_size":len(pool["data"] or [])})
        if not pool["data"]:
            status = "loading" if pool["error"] == "后台采集中" else "unavailable"
            result = {**pool,"data":{**base,"status":status}}
            if mode == "live":
                self.sessions.capture(today, session, result)
            return result
        if mode == "live" and (pool["is_stale"] and pool["error"] != "后台采集中"
                               or not fresh(pool["market_time"],clock["time"],1800)):
            result = {**pool,"is_stale":True,"data":{**base,"status":"stale"}}
            self.sessions.capture(today, session, result)
            return result

        async def evaluate():
            candidates = shortlist(session, pool["data"], clock["time"], mode=="demo")
            result = {**base,"checked":len(candidates)}
            packet = await self.quotes(mode, tuple(r["code"] for r in candidates), clock)
            quotes = {q["code"]:q for q in packet["data"] or []}
            items, waiting = [], 0
            for row in candidates:
                q = quotes.get(row["code"])
                if not q:
                    continue
                previous = None
                if session == "tail" and q["market_time"]:
                    if mode == "demo":
                        previous = {"price":q["price"]/1.003,"amount":q["amount"]-4e6,
                                    "market_time":(datetime.fromisoformat(q["market_time"])-timedelta(seconds=60)).isoformat()}
                    else:
                        history = self.cache.history(f'live:quote:{q["code"]}', 100)
                        previous = prior_tail(history,q["market_time"])
                pick, wait_for_sample = rank_candidate(session,q,row,clock["time"],previous,mode=="demo")
                if pick:
                    items.append(pick)
                waiting += wait_for_sample
            items.sort(key=lambda item:(-item["score"],item["code"]))
            status = "ready" if items else "sampling" if waiting else "unavailable" if packet["error"] else "empty"
            return {**live.response({**result,"status":status,"items":items[:8],"waiting":waiting},
                                    packet["source"] or "Pulse 买入候选",packet["source_url"],packet["market_time"],packet["error"]),
                    "fetched_at":packet["fetched_at"]}

        result = await self.cache.get(key, 12, evaluate)
        if mode == "live" and not window_open(session, await self.clock(mode, wait=False)):
            if not (result["data"] and result["data"]["items"]):
                return self.local(mode,{**base,"status":"closed"},"Pulse 买入候选")
            result = {**result,"is_stale":True}
        if result["is_stale"] and result["data"]:
            return {**result,"data":{**result["data"],"status":"history"}}
        if mode == "live":
            self.sessions.capture(today, session, result)
        return result

    async def sentiment(self, mode):
        clock = await self.clock(mode)
        market, *pools = await asyncio.gather(
            self.breadth(mode, clock),
            *(self.pool(mode, kind, clock["latest_day"]) for kind in ("up","down","broken","yesterday")))
        data = market_mood(market["data"] or {}, *(p["data"] for p in pools))
        return {**self.local(mode, data, "全市场涨跌与涨跌停池"),
                "market_time":market["market_time"], "is_stale":all(p["is_stale"] for p in (market, *pools)),
                "data":{**data, "sources":dict(zip(("breadth","up","down","broken","yesterday"),
                        [{k:p.get(k) for k in ("source","source_url","fetched_at","market_time","is_stale","error","note")} for p in (market,*pools)]))}}

    def local(self, mode, data, source, error=None):
        return {**live.response(data, "离线演示样本" if mode=="demo" else source, None), "mode":mode,
                "fetched_at":now().isoformat(timespec="seconds"), "schema_version":"1.0", "cached":False,
                "is_stale":bool(error), "error":error}

    async def roles(self, mode, board, kind, period, order):
        members, ranking, clock = await asyncio.gather(
            self.get(mode, "members", 300, board), self.sectors(mode,kind,period,order), self.clock(mode))
        if not members["data"]:
            return members
        rows = [r for r in members["data"] if r["pct"] is not None and r["amount"] is not None and r["amount"]>0]
        candidates = sorted(rows, key=lambda r: r["amount"], reverse=True)[:8]
        candidates += [r for r in sorted(rows, key=lambda r:r["pct"], reverse=True)[:4] if r not in candidates]
        ordered = [r["code"] for r in candidates]
        # Limit the daily-bar requests and individual fund history to a clearly disclosed sample.
        bars = {}
        for offset in range(0, len(ordered), 3):
            batch = ordered[offset:offset+3]
            out = await asyncio.gather(*(self.get(mode, "kline", 900, c, "day", "qfq", 32) for c in batch))
            for c, p in zip(batch,out):
                if p["data"] and not p["is_stale"]:
                    # Compare completed daily sessions consistently, including after the close.
                    bars[c] = [r for r in p["data"]["items"] if r["t"][:10] < clock["time"][:10]]
        flow_rows = sorted(candidates, key=lambda r: r["pct"])[:4]
        flows = await asyncio.gather(*(self.get(mode, "stock_flow", 900, r["code"], "day") for r in flow_rows))
        funds = {r["code"]:p["data"] for r,p in zip(flow_rows,flows) if p["data"] is not None and not p["is_stale"]}
        row = next((r for r in (ranking["data"] or {}).get("items",[]) if r["code"]==board), None)
        scores = role_scores(rows, bars, funds, row.get("pct_today") if row else None)
        return {**members, "note":"成交额前8与涨幅前4去重后计算日线，补涨资金只核对其中涨幅后4。相关性使用其余有效样本等权涨幅。",
                "data":{"roles":scores, "sample":len(ordered), "total":len(members["data"]), "price_coverage":sum(len(b)>=21 for b in bars.values()),
                        "flow_coverage":len(funds), "members":sorted(rows,key=lambda r:r["amount"],reverse=True)[:20],
                        "board_pct":row.get("pct_today") if row else None}}

    def sector_history(self, mode, board, kind, period, order):
        key = f"{mode}:ranks:{kind}:{period}:{order}"
        return [{"t":at, **row} for at, rows in reversed(self.cache.history(key, 240))
                for row in rows if row["code"]==board]

    async def tail(self, mode, codes):
        clock = await self.clock(mode)
        day = clock["latest_day"]
        samples = []
        for code in codes:
            series = self.tail_samples(mode, f"quote:{code}", day)
            if len(series)<2:
                continue
            start, latest = series[0][1], series[-1][1]
            move = round((latest["price"]/start["price"]-1)*100, 2)
            amount = latest["amount"]-start["amount"] if None not in (start["amount"],latest["amount"]) else None
            samples.append({"code":code, "name":latest["name"], "move":move, "amount":amount, "pct":latest["pct"],
                            "from":series[0][0], "to":series[-1][0],
                            "heat":weighted({"尾段价格变化":clip(50+move*20),
                                "尾段成交额占比":clip(100*amount/latest["amount"]*3) if amount is not None and latest["amount"] else None},
                                (65,35),minimum=.65)})
        sectors = []
        for kind in ("industry","concept","region"):
            ranks = self.tail_samples(mode, f"ranks:{kind}:today:inflow", day)
            if len(ranks)<2:
                continue
            start = {r["code"]:r for r in ranks[0][1]}
            for latest in ranks[-1][1][:3]:
                old = start.get(latest["code"])
                if old:
                    sectors.append({"name":latest["name"],"kind":kind,"change":latest["main_net"]-old["main_net"],
                                    "rank_delta":old["rank"]-latest["rank"],"leader":latest["leader"],"leader_code":latest.get("leader_code")})
        return self.local(mode, {"window":clock["tail"],"phase":clock["phase"],"day":day,"items":samples,
                                 "sectors":sectors,"auction":{"match_price":None,"matched_volume":None,"unmatched_volume":None}},
                          "尾盘报价与板块快照")

    def tail_samples(self, mode, key, day):
        return list(reversed([(stamp, row) for stamp,row in self.cache.history(f"{mode}:{key}", 220)
                              if stamp[:10]==day and (mode=="demo" or "14:45"<=stamp[11:16]<="15:00")]))

    async def alerts(self, mode, codes, client, pct, turnover, rapid, volume, kind):
        current = await self.quotes(mode, codes)
        clock = await self.clock(mode)
        alerts_key = f"{mode}:alerts:{client}"

        def emit(code, name, event, value, threshold, unit, stamp, source, source_url, prior):
            previous = next((x for _,x in self.cache.history(alerts_key,300) if x["code"]==code and x["event"]==event), None)
            if previous and (datetime.fromisoformat(stamp)-datetime.fromisoformat(previous["market_time"])).total_seconds()<300:
                return
            at = now().isoformat()
            self.cache.record(alerts_key, at, {"id":at, "code":code, "name":name, "event":event, "value":value,
                "threshold":threshold, "unit":unit, "market_time":stamp,"source":source,"source_url":source_url,
                "previous":prior, "mode":mode}, keep=300)

        if clock["phase"] in {"demo", "morning", "afternoon"} and not current["is_stale"]:
            self.schedule_observation(mode,codes,clock)
            for row in current["data"] or []:
                t = row["market_time"]
                if row["is_stale"] or not t:
                    continue
                code = row["code"]
                k = f"{mode}:quote:{code}"
                old = next((r for stamp,r in self.cache.history(k,15) if stamp < t and stamp[:10]==t[:10]), None)
                signals = quote_signals(old,row,pct,turnover,rapid,volume,clock["tail"])
                daily = self.cache.lookup(f"{mode}:kline:{code}:day:qfq:120")
                if daily and daily[0]>asyncio.get_running_loop().time() and old:
                    prior_bars = [r for r in daily[1]["data"]["items"] if r["t"][:10] < t[:10]][-20:]
                    high = max((r["high"] for r in prior_bars),default=None)
                    if len(prior_bars)==20 and old["price"] <= high < row["price"]:
                        signals.append(("突破近20日高点",row["price"],high,"元"))
                for event,value,threshold,unit in signals:
                    emit(code,row["name"],event,value,threshold,unit,t,current["source"],current["source_url"],
                         old["price"] if old and event in {"短时拉升","短时下挫","尾盘价格异动","突破近20日高点"} else None)
                flow = self.cache.lookup(f"{mode}:stock_flow:{code}:minute")
                if flow and flow[0]>asyncio.get_running_loop().time() and flow[1]["data"]:
                    series = flow[1]["data"][-4:]
                    numbers = [r.get("main_net") for r in series]
                    last = series[-1]["t"]
                    if None not in numbers and len(numbers)==4 and last[:10]==t[:10] and abs((datetime.fromisoformat(t)-datetime.fromisoformat(last)).total_seconds())<=300:
                        change = numbers[-1]-numbers[-2]
                        if abs(change)>=2e7:
                            emit(code,row["name"],"尾盘主力资金异动" if clock["tail"] else "主力资金突变",change,2e7,"元",last,flow[1]["source"],flow[1]["source_url"],numbers[-2])
                        if all(b>a for a,b in zip(numbers,numbers[1:])):
                            emit(code,row["name"],"连续3段主力净流入",numbers[-1]-numbers[0],0,"元",last,flow[1]["source"],flow[1]["source_url"],numbers[0])
            ranks = self.cache.history(f"{mode}:ranks:{kind}:today:inflow",2)
            if len(ranks)==2 and ranks[0][0][:10]==ranks[1][0][:10]==clock["time"][:10]:
                old_ranks = {r["code"]:r for r in ranks[1][1]}
                for row in ranks[0][1]:
                    old = old_ranks.get(row["code"])
                    if not old:
                        continue
                    if row["rank_delta"] is not None and row["rank_delta"]>=5:
                        emit(row["code"],row["name"],"板块资金排名跃升",row["rank_delta"],5,"位",ranks[0][0],"东方财富采集快照",row.get("source_url"),old["rank"])
                    if None not in (row["pct"],old["pct"]) and abs(row["pct"]-old["pct"])>=1.2:
                        emit(row["code"],row["name"],"板块涨幅突变",round(row["pct"]-old["pct"],2),1.2,"%",ranks[0][0],"东方财富采集快照",row.get("source_url"),old["pct"])
                    leader = next((q for q in current["data"] or [] if q["code"]==row["leader_code"]),None)
                    if leader and None not in (leader["pct"],row["pct"]) and not leader["is_stale"] and leader["pct"]-row["pct"]<=-2:
                        emit(leader["code"],leader["name"],"板块领涨标的掉队",round(leader["pct"]-row["pct"],2),2,"%",leader["market_time"],"腾讯报价与东方财富板块",live.BOARD,row["pct"])
                    cached_members = self.cache.lookup(f'{mode}:members:{row["code"]}')
                    if cached_members and cached_members[0]>asyncio.get_running_loop().time() and row["pct"] is not None:
                        membership = {r["code"] for r in cached_members[1]["data"]}
                        for q in current["data"] or []:
                            if q["code"] in membership and not q["is_stale"] and q["pct"] is not None and abs(q["pct"]-row["pct"])>=3:
                                emit(q["code"],q["name"],"偏离所属板块",round(q["pct"]-row["pct"],2),3,"%",
                                     q["market_time"],"腾讯报价与东方财富板块",live.BOARD,row["pct"])
        return self.local(mode,[r for _,r in self.cache.history(alerts_key,100)],"Pulse 预警规则")


def codes_from(value, limit=60):
    if len(value) > 900:
        raise ValueError("股票代码列表过长")
    values = [x for x in value.split(",") if x.strip()]
    if len(values)>limit:
        raise ValueError(f"最多订阅 {limit} 只")
    return tuple(dict.fromkeys(map(live.symbol,values)))


def checked(code):
    try:
        return live.symbol(code)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


def parse_codes(value, limit=60):
    try:
        return codes_from(value, limit)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


def create_app(*, database=None, transport=None, access=None):
    cloud = (access or os.getenv("PULSE_ACCESS", "local")) == "cloud"
    paper_enabled = not cloud or os.getenv("PULSE_PAPER_CLOUD") == "1"
    @asynccontextmanager
    async def lifespan(app):
        path = database or os.getenv("PULSE_DB") or ROOT / ".pulse" / "market.db"
        if str(path)!=":memory:":
            Path(path).resolve().parent.mkdir(parents=True, exist_ok=True)
        store = Cache(path)
        async with httpx.AsyncClient(timeout=httpx.Timeout(7, connect=4), transport=transport,
                                    headers={"User-Agent":"Mozilla/5.0 Chrome/125 Safari/537.36"}) as client:
            app.state.market = Market(store,live.Live(client,float(os.getenv("PULSE_EM_INTERVAL","1.4"))))
            app.state.global_market = GlobalMarket(store, client)
            app.state.premarket = Premarket(app.state.market, app.state.global_market)
            app.state.premarket.import_archive(os.getenv("PULSE_BOOTSTRAP_REPORT"))
            app.state.monitor = IntradayMonitor(app.state.market, enabled=cloud,
                                               auction_feed=FuyaoAuction(client, cloud=cloud))
            background = [asyncio.create_task(worker.loop()) for worker in (
                app.state.market.hourly, app.state.market.sessions, app.state.premarket, app.state.monitor,
                *((app.state.market.paper,) if paper_enabled else ()))]
            try:
                yield
            finally:
                for task in background:
                    task.cancel()
                await asyncio.gather(*background, return_exceptions=True)
                await app.state.market.close()

    api = FastAPI(title="Pulse A 股盯盘终端", version="3.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None if cloud else "/openapi.json")
    api.add_middleware(CloudAccess if cloud else LocalAccess)

    @api.get("/docs", include_in_schema=False)
    async def docs():
        return RedirectResponse("/openapi.json")
    def svc(req):
        return req.app.state.market

    @api.get("/healthz", include_in_schema=False)
    async def liveness():
        return {"status": "ok", "version": "3.1.0"}

    @api.get("/api/config")
    async def config(req: Request):
        paper = svc(req).paper
        return svc(req).local("live", {"allow_demo": not cloud, "cloud": cloud,
                                      "paper_runner": {"enabled": paper_enabled, "last_tick_at": paper.last_tick_at},
                                      "scheduler": req.app.state.premarket.enabled}, "Pulse")

    @api.get("/api/monitor/status")
    async def monitor_status(req: Request):
        return svc(req).local("live", req.app.state.monitor.status(), "Pulse 云端盯盘")

    @api.get("/api/monitor/events")
    async def monitor_events(req: Request):
        rows = [r for _,r in svc(req).cache.history("live:alerts:cloud-monitor",100)]
        return svc(req).local("live", rows, "Pulse 云端异动记录")

    @api.get("/api/reports/status")
    async def reports_status(req: Request):
        return svc(req).local("live", req.app.state.premarket.status(), "Pulse 盘前报告")

    @api.get("/api/reports")
    async def reports(req: Request):
        return svc(req).local("live", req.app.state.premarket.list(), "Pulse 盘前档案")

    def report_markdown(report):
        if report is None:
            raise HTTPException(404, "盘前报告尚未生成")
        return Response(report["markdown"], media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="Pulse-Premarket-'+report["day"]+'.md"',
                                 "ETag": '"'+report["sha256"]+'"'})

    @api.get("/api/reports/latest.md")
    async def latest_markdown(req: Request):
        return report_markdown(req.app.state.premarket.get())

    @api.get("/api/reports/latest")
    async def latest_report(req: Request):
        report = req.app.state.premarket.get()
        return svc(req).local("live", report, "Pulse 盘前报告", None if report else "盘前报告尚未生成，请查看调度状态")

    @api.get("/api/reports/{day}.md")
    async def dated_markdown(req: Request, day: date, revision: int | None = Query(None, ge=1, le=100)):
        return report_markdown(req.app.state.premarket.get(day.isoformat(), revision))

    @api.get("/api/reports/{day}")
    async def dated_report(req: Request, day: date, revision: int | None = Query(None, ge=1, le=100)):
        report = req.app.state.premarket.get(day.isoformat(), revision)
        if report is None:
            raise HTTPException(404, "该交易日没有归档报告")
        return svc(req).local("live", report, "Pulse 盘前档案")

    @api.get("/api/market/indices")
    async def indices(req:Request, mode:Mode="live", refresh:bool=False):
        return await svc(req).quotes(mode,tuple(live.INDICES),refresh=refresh)

    @api.get("/api/stocks/quotes")
    async def quotes(req:Request, mode:Mode="live", codes:str=Query(default=",".join(DEFAULT_CODES),max_length=900),
                     refresh:bool=False):
        return await svc(req).quotes(mode,parse_codes(codes, 85),refresh=refresh)

    @api.post("/api/stocks/screen")
    async def stock_screen(req: Request, query: ScreenRequest, mode: Mode = "live"):
        return await svc(req).screen(mode, query)

    @api.get("/api/stocks/buy-candidates")
    async def buy_candidates(req: Request, session: Session = "open", mode: Mode = "live"):
        return await svc(req).ideas(mode, session)

    @api.get("/api/stocks/hourly-opportunities")
    async def hourly_opportunities(req: Request, mode: Mode = "live"):
        market = svc(req)
        rows = market.hourly.history() if mode == "live" else []
        return market.local(mode, {"rounds": rows, "running": market.hourly.running if mode == "live" else None},
                            "Pulse 小时筛选")

    @api.get("/api/market/global")
    async def global_quotes(req: Request, mode: Mode = "live"):
        market = svc(req)
        if mode == "demo":
            return market.local(mode, None, "Yahoo Finance", "切换真实模式以读取外盘行情")
        data = await req.app.state.global_market.snapshot(now())
        return market.local(mode, data, "Yahoo Finance / 新浪财经")

    @api.get("/api/stocks/session-journal")
    async def session_journal(req: Request, mode: Mode = "live"):
        market = svc(req)
        return market.local(mode, market.sessions.history() if mode == "live" else [], "本地候选档案")

    @api.get("/api/paper/account")
    async def paper_account(req: Request):
        market = svc(req)
        return market.local("live", market.paper.status(now()), "本地模拟账户")

    @api.get("/api/stocks/{code}/kline")
    async def kline(req:Request,code:str,mode:Mode="live",period:Period="day",
                    adjust:Literal["qfq","hfq","none"]="qfq",count:int=Query(120,ge=1,le=320)):
        return await svc(req).bars(mode,checked(code),period,adjust if adjust!="none" else "",count)

    @api.get("/api/stocks/{code}/fund-flow")
    async def stock_flow(req:Request,code:str,mode:Mode="live",span:Literal["minute","day"]="minute"):
        market = svc(req)
        code = checked(code)
        job = market.get(mode, "stock_flow", 90 if span == "minute" else 600, code, span)
        if mode == "demo":
            return await job
        packet, clock = await asyncio.gather(job, market.clock(mode))
        return describe_flow(packet, clock, span)

    @api.get("/api/sectors/ranking")
    async def rankings(req:Request,mode:Mode="live",kind:Kind="industry",period:Span="today",order:Direction="inflow"):
        return await svc(req).sectors(mode,kind,period,order)

    @api.get("/api/sectors/fund-flow")
    async def sector_flow(req:Request,mode:Mode="live",kind:Kind="industry",period:Span="today",order:Direction="inflow",
                          board:str|None=Query(None,pattern=r"^BK[0-9]{4}$")):
        if board is None:
            return await svc(req).sectors(mode,kind,period,order)
        data = svc(req).sector_history(mode,board,kind,period,order)
        return svc(req).local(mode,data,"Pulse 采集快照")

    @api.get("/api/sectors/{board}/roles")
    async def roles(req:Request,board:str,mode:Mode="live",kind:Kind="industry",period:Span="today",order:Direction="inflow"):
        if len(board)!=6 or not board.startswith("BK") or not board[2:].isascii() or not board[2:].isdigit():
            raise HTTPException(422,"无效板块代码")
        return await svc(req).roles(mode,board,kind,period,order)

    @api.get("/api/market/sentiment")
    async def sentiment(req:Request,mode:Mode="live"):
        return await svc(req).sentiment(mode)

    @api.get("/api/stocks/limit-up")
    async def limit_up(req:Request,mode:Mode="live",kind:Pool="up",day:date|None=None):
        return await svc(req).pool(mode,kind,day.isoformat() if day else None)

    @api.get("/api/news")
    async def news(req:Request,mode:Mode="live"):
        return await svc(req).get(mode,"news",180)

    @api.get("/api/stocks/{code}/announcements")
    async def announcements(req:Request,code:str,mode:Mode="live"):
        return await svc(req).get(mode,"announcements",600,checked(code))

    @api.get("/api/tail")
    async def tail(req:Request,mode:Mode="live",codes:str=Query(default=",".join(DEFAULT_CODES),max_length=700)):
        return await svc(req).tail(mode,parse_codes(codes))

    @api.get("/api/alerts")
    async def alerts(req:Request,mode:Mode="live",codes:str=Query(default=",".join(DEFAULT_CODES),max_length=700),
                     client:str=Query("default",pattern=r"^[a-zA-Z0-9_-]{1,64}$"),pct:float=Query(3,ge=.1,le=50),
                     turnover:float=Query(5,ge=.1,le=100),rapid:float=Query(1,ge=.1,le=25),
                     volume:float=Query(2.5,ge=1,le=100),kind:Kind="industry"):
        return await svc(req).alerts(mode,parse_codes(codes),client,pct,turnover,rapid,volume,kind)

    @api.get("/api/system/health")
    async def health(req:Request,mode:Mode="live"):
        market=svc(req)
        clock=await market.clock(mode)
        return market.local(mode,{**clock,"skill_version":"3.10.4","push":"/ws/market","status":"running","quote_interval_s":quote_interval(mode,clock)},
                            "Pulse 行情服务",clock["calendar_error"])

    @api.websocket("/ws/market")
    async def stream(ws:WebSocket):
        mode=ws.query_params.get("mode","live")
        try:
            codes = codes_from(ws.query_params.get("codes", ",".join(DEFAULT_CODES)), 85)
            if mode not in ("demo", "live"):
                raise ValueError
        except ValueError:
            await ws.close(code=1008)
            return
        await ws.accept()
        market = ws.app.state.market
        refresh = False
        loop = asyncio.get_running_loop()
        controls = ControlRate()
        try:
            while True:
                clock = await market.clock(mode, wait=False)
                interval = quote_interval(mode, clock)
                market.schedule_observation(mode,tuple(c for c in codes if c not in live.INDICES),clock)
                indices_data,quotes_data = await asyncio.gather(
                    market.quotes(mode, tuple(live.INDICES), clock, refresh=refresh, show_saved=True),
                    market.quotes(mode, codes, clock, refresh=refresh, show_saved=True))
                await ws.send_json({"mode":mode,"clock":{**clock, "quote_interval_s":interval},"codes":codes,
                                    "indices":indices_data,"quotes":quotes_data})
                refresh = False
                pending = "后台采集中" in (clock["calendar_error"], indices_data["error"], quotes_data["error"])
                deadline = loop.time() + (1 if pending else interval)
                while (remaining := deadline - loop.time()) > 0:
                    try:
                        event = await asyncio.wait_for(ws.receive(), timeout=remaining)
                        if event["type"] == "websocket.disconnect":
                            return
                        raw = event.get("text")
                        if not controls.allow(loop.time()):
                            await ws.close(code=1008)
                            return
                        if raw is None:
                            await ws.close(code=1003)
                            return
                        if len(raw.encode("utf-8")) > WS_MESSAGE_LIMIT:
                            await ws.close(code=1009)
                            return
                        message = json.loads(raw)
                        if not isinstance(message, dict):
                            continue
                        if message.get("type") == "subscribe" and isinstance(message.get("codes"), str):
                            updated = codes_from(message["codes"], 85)
                            if updated != codes:
                                codes = updated
                                break
                        elif message.get("type") == "refresh":
                            refresh = True
                            break
                    except TimeoutError:
                        break
                    except (ValueError, RecursionError):
                        continue
        except (WebSocketDisconnect,RuntimeError):
            return

    dist = ROOT / "frontend" / "dist"
    if dist.is_dir():
        api.mount("/",StaticFiles(directory=dist,html=True),name="frontend")
    return api


app = create_app()
