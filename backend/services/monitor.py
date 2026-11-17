"""Server-side 09:15 auction observation and intraday monitoring, without orders."""
import asyncio
import logging
import os
from datetime import datetime

from backend.adapters.live import INDICES, symbol
from backend.adapters.auction import START, END
from backend.services.clock import SH, now
from backend.services.ideas import fresh
from backend.services.premarket import SAMPLE

KEY = "live:monitor:daily"
AUCTION_KEY = "live:monitor:auction"
REGULAR = {"morning", "afternoon"}
ACTIVE = REGULAR | {"open_auction", "close_auction"}
SCHEDULE = [
    {"time":"09:15–09:20", "stage":"竞价观察", "action":"分开记录普通报价及可核验的虚拟撮合；竞价只观察"},
    {"time":"09:20–09:25", "stage":"竞价后段", "action":"继续观察；交易所此阶段不接受撤单"},
    {"time":"09:25–09:30", "stage":"等待开盘", "action":"保留竞价观察快照，等待连续竞价量价验证"},
    {"time":"09:30–10:00", "stage":"开盘候选", "action":"全市场成交额榜初筛，最多复核32只；沿用开盘量价条件"},
    {"time":"10:00–11:30", "stage":"上午盯盘", "action":"跟踪样本与候选、指数、板块和异动；10:30运行小时筛选"},
    {"time":"11:30–13:00", "stage":"午间休市", "action":"暂停报价扫描，保留上午快照；11:30小时筛选独立归档"},
    {"time":"13:00–14:45", "stage":"下午盯盘", "action":"恢复报价与异动扫描；14:00运行小时筛选"},
    {"time":"14:45–14:57", "stage":"尾盘候选", "action":"复核主力净额与两次量价快照"},
    {"time":"14:57–15:00", "stage":"收盘集合竞价", "action":"记录报价，不新增尾盘买入候选"},
    {"time":"15:05以后", "stage":"收盘复核", "action":"归档盘中策略与已有候选；不生成真实委托"},
]


def stage(clock):
    phase, hm = clock["phase"], clock["time"][11:16]
    if phase == "open_auction":
        return "竞价后段" if hm >= "09:20" else "竞价观察"
    if phase == "morning":
        return "开盘候选" if hm < "10:00" else "上午盯盘"
    if phase == "afternoon":
        return "尾盘候选" if hm >= "14:45" else "下午盯盘"
    return {"pause":"等待开盘", "lunch":"午间休市", "close_auction":"收盘集合竞价",
            "closed":"收盘复核", "before":"等待09:15", "unknown":"日历待确认",
            "weekend":"周末休市", "holiday":"节假日休市"}.get(phase, "等待交易时段")


def stance(rows):
    valid = [q for q in rows if not q.get("is_stale") and q.get("pct") is not None]
    if len(valid) < 3:
        return {"label":"数据不足", "action":"等待至少3个有效指数；不推断市场方向", "coverage":len(valid)}
    avg = sum(q["pct"] for q in valid)/len(valid)
    above = sum(q.get("open", 0) > 0 and q["price"] >= q["open"] for q in valid)
    if avg >= .5 and above >= len(valid)*.6:
        label, action = "偏强观察", "只观察量价同向标的，等待回踩承接；涨幅或量能条件失效时移出候选"
    elif avg <= -.7 and sum(q["pct"] < 0 for q in valid) >= len(valid)*.6:
        label, action = "防守观察", "减少新增追涨观察，优先记录跌破开盘价、短时下挫与板块转弱"
    else:
        label, action = "分化观察", "逐只核对开盘价、量比与板块同步性；缺少共振时保留空候选"
    return {"label":label, "action":action, "coverage":len(valid), "index_mean_pct":round(avg,3)}


class IntradayMonitor:
    def __init__(self, market, enabled=False, auction_feed=None):
        self.market, self.cache = market, market.cache
        self.enabled = os.getenv("PULSE_MONITOR", "1" if enabled else "0") == "1"
        raw = os.getenv("PULSE_MONITOR_CODES", ",".join(SAMPLE))
        self.codes = tuple(dict.fromkeys(symbol(c) for c in raw.split(",") if c.strip()))[:60]
        self.last = None
        self.running = False
        self.next_scan = 0.0
        self.next_sectors = 0.0
        self.auction_feed = auction_feed

    def current_record(self):
        day = now().date().isoformat()
        records = self.cache.history(KEY, 1)
        return records[0][1] if records and records[0][0] == day else None

    def status(self):
        row = self.current_record()
        permission = self.auction_feed.permission if self.auction_feed else "unconfigured"
        auction = (row or {}).get("auction", {})
        feed = auction.get("feed", {}) if permission == "ready" else {}
        current = now()
        if feed.get("status") == "verified_snapshot" and (row["phase"] != "open_auction" or
                not START <= current.time() < END or
                not all(fresh(x["market_time"], current.isoformat(), 30) for x in feed["items"])):
            feed = {**feed, "status": "historical_snapshot"}
        if row and "auction" in row:
            row = {**row, "auction": {**auction, "feed": feed}}
        return {"enabled":self.enabled, "running":self.running, "timezone":"Asia/Shanghai",
                "planned_start":"交易日09:15", "quote_interval_s":8, "sector_interval_s":90,
                "scope":"固定样本 + 当日已生成的开盘/尾盘候选，最多60只；手机本地自选不会自动同步到云端",
                "configured_codes":list(self.codes), "state":row, "schedule":SCHEDULE,
                "auction_fields":{"indicative_price":None,"matched_volume":None,"unmatched_volume":None},
                "auction_permission":permission,
                "auction_note":"09:15–09:25 虚拟撮合需源端时间核验；09:25 最终撮合与普通报价分别观察",
                "execution":"观察与预警；没有真实下单接口"}

    def save(self, current, row):
        self.last = row
        self.cache.record(KEY, current.date().isoformat(), row, keep=40, replace=True)

    def prior(self, day):
        rows = self.cache.history(KEY, 1)
        return rows[0][1] if rows and rows[0][0] == day else {}

    async def tick(self, current=None):
        current = (current or now()).astimezone(SH)
        day = current.date().isoformat()
        clock = await self.market.clock("live")
        old = self.prior(day)
        phase = clock["phase"]
        base = {**old, "day":day,"phase":phase,"stage":stage(clock),"heartbeat":current.isoformat(timespec="seconds"),
                "planned_start":day+"T09:15:00+08:00", "calendar_error":clock.get("calendar_error"),
                "strategy":old.get("strategy") or {"label":"等待交易时段","action":"尚未开始有效扫描","coverage":0}}
        if phase not in ACTIVE:
            if phase == "unknown":
                base["strategy"] = {"label":"日历待确认","action":"暂停生成新信号，等待官方交易日历","coverage":0}
            self.save(current, {**base,"active":False})
            return
        if current.timestamp() < self.next_scan:
            return
        self.next_scan = current.timestamp()+8
        codes = list(self.codes)
        # Include actual candidates captured by the independent session worker.
        for kind in ("open","tail"):
            history = self.cache.history(f"live:session:{kind}", 1)
            if history and history[0][0] == day:
                codes.extend(r["code"] for r in (history[0][1].get("ranking") or {}).get("items", []))
        codes = tuple(dict.fromkeys(codes))[:60]
        jobs = [self.market.quotes("live", codes, clock), self.market.quotes("live", tuple(INDICES), clock)]
        if phase == "open_auction" and self.auction_feed:
            jobs.append(self.auction_feed.snapshot(codes, current))
        packet, indices, *auction_packets = await asyncio.gather(*jobs)
        rows = packet["data"] or []
        valid = [q for q in rows if not q.get("is_stale") and fresh(q.get("market_time"),clock["time"],120)]
        base.update({"active":True,"started_at":old.get("started_at") or current.isoformat(timespec="seconds"),
            "last_scan_at":now().isoformat(timespec="seconds"),"scans":old.get("scans",0)+1,
            "watched":len(codes),"valid_quotes":len(valid),"quotes":rows,
            "quote_source":packet.get("source"),"quote_time":packet.get("market_time"),"last_error":packet.get("error")})
        if valid:
            base["last_success_at"] = now().isoformat(timespec="seconds")
        if phase == "open_auction":
            auction = {"at":current.isoformat(timespec="seconds"),"rows":rows,"verified_quotes":len(valid),
                       "status":"observing" if valid else "waiting_data", "tradable_signals":[],
                       "indicative_price":None,"matched_volume":None,"unmatched_volume":None}
            if auction_packets:
                auction["feed"] = auction_packets[0]
            self.cache.record(AUCTION_KEY, day, auction, keep=40, replace=True)
            base["auction"] = auction
            base["strategy"] = {"label":"竞价观察", "coverage":len(valid),
                "action":"分别观察普通报价和已核验的虚拟撮合数据。09:30后进行开盘量价验证。"}
        elif phase in REGULAR:
            base["strategy"] = stance(indices["data"] or [])
            alerts = await self.market.alerts("live",codes,"cloud-monitor",3,5,1,2.5,"industry")
            base["alerts"] = [r for r in alerts["data"] or [] if r["market_time"][:10] == day][:30]
            if current.timestamp() >= self.next_sectors:
                self.next_sectors = current.timestamp()+90
                sectors = await self.market.sectors("live","industry","today","inflow")
                base["sectors"] = {"items":(sectors["data"] or {}).get("items",[])[:20],
                    "is_stale":sectors["is_stale"],"error":sectors.get("error"),
                    "fetched_at":sectors.get("fetched_at"),"market_time":sectors.get("market_time"),
                    "note":sectors.get("note")}
        else:
            base["strategy"] = {"label":"收盘集合竞价观察","coverage":len(valid),
                                "action":"保留报价观察；尾盘候选于14:57停止新增，等待收盘复核"}
        self.save(current, base)

    async def loop(self):
        if not self.enabled:
            return
        self.running = True
        try:
            while True:
                try:
                    await self.tick()
                except Exception as exc:
                    current = now()
                    row = self.prior(current.date().isoformat())
                    self.save(current,{**row,"day":current.date().isoformat(),"active":False,
                        "heartbeat":current.isoformat(timespec="seconds"),"last_error":type(exc).__name__,
                        "stage":"数据异常，等待重试"})
                    logging.getLogger("pulse.monitor").exception("云端盯盘扫描失败")
                await asyncio.sleep(8)
        finally:
            self.running = False
