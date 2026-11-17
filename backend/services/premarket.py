"""Persistent, idempotent 08:00 Asia/Shanghai reports built only from live packets."""
import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import zlib
from datetime import date, datetime, time, timedelta

from backend.adapters.live import INDICES, NEWS, Unavailable, response, symbol
from backend.services.clock import SH, now

LOG = logging.getLogger("pulse.report")
# A disclosed sample, not a claim of whole-market coverage or a buy recommendation.
SAMPLE = {
    "sh601138": "电子", "sz300750": "电池", "sz002594": "汽车", "sh688256": "半导体",
    "sh600519": "白酒", "sz000001": "银行", "sh601899": "有色", "sh600028": "油气",
    "sz300308": "通信", "sh600309": "化工", "sh600150": "船舶", "sh600276": "医药",
}
WORLD = re.compile("美国|美股|美联储|纳斯达克|标普|道琼斯|欧洲|欧盟|英国|日本|韩国|国际|全球|美元|原油|黄金|关税|地缘|中东|俄|乌克兰|芯片|英伟达|铜")
THEMES = (
    ("半导体", "^SOX", 1.0, "半导体、算力", "核对国内半导体量能与开盘广度"),
    ("科技成长", "NQ=F", .5, "通信、电子", "核对创业板与通信电子板块能否同向放量"),
    ("能源", "BZ=F", 2.0, "油气、航空、化工", "油价上涨关注油气，航空和下游化工核对成本压力"),
    ("有色", "HG=F", 1.0, "铜、有色", "核对国内铜价、库存和有色板块资金"),
    ("黄金", "GC=F", 1.0, "黄金", "核对黄金板块成交与金价同向性"),
)


def packet_status(packet):
    if not packet or packet.get("data") is None:
        return "missing"
    return "stale" if packet.get("is_stale") else "available"


def clean(value):
    return re.sub(r"[\r\n|]+", " ", str(value or "—")).strip()


def n(value, digits=2):
    return "—" if value is None else f"{value:,.{digits}f}"


def md_link(label, url):
    if isinstance(url, str) and url.startswith("https://") and not any(c in url for c in "\r\n<> "):
        return f"[{clean(label).replace('[', '').replace(']', '')}](<{url}>)"
    return clean(label)


FALLBACK = {"NQ=F": "^IXIC", "BZ=F": "hf_OIL", "HG=F": "hf_HG", "GC=F": "hf_GC"}

def impacts(rows):
    result = []
    for name, key, threshold, sectors, trigger in THEMES:
        row = next((r for r in rows if r["symbol"] == key and r["status"] == "available"), None)
        if row is None and key in FALLBACK:
            row = next((r for r in rows if r["symbol"] == FALLBACK[key] and r["status"] == "available"), None)
        change = row.get("close_pct") if row and row["group"] == "us" else row.get("pct") if row else None
        if change is None:
            result.append({"theme": name, "direction": "待确认", "sectors": sectors,
                           "evidence": "对应外盘数据缺失或滞后", "trigger": trigger, "source_url": row.get("url") if row else None})
        else:
            direction = "正向线索" if change >= threshold else "压力线索" if change <= -threshold else "中性观察"
            result.append({"theme": name, "direction": direction, "sectors": sectors,
                           "evidence": f'{row["name"]} {change:+.2f}% · {row["market_time"]}',
                           "trigger": trigger, "source_url": row["url"]})
    return result


def candidate(code, sector, quote, bars, prior_day, target):
    if not quote or quote.get("is_stale") or not quote.get("market_time") or quote["market_time"][:10] != prior_day:
        return None
    if "ST" in quote["name"].upper() or "退" in quote["name"] or not quote.get("amount") or quote["amount"] < 1e8:
        return None
    if packet_status(bars) != "available":
        return None
    # Completed days only; a late recovery must not leak today's bars into premarket picks.
    items = [r for r in bars["data"]["items"] if r["t"][:10] < target][-30:]
    if len(items) < 21 or items[-1]["t"][:10] != prior_day:
        return None
    closes = [r["close"] for r in items]
    ma5, ma20 = sum(closes[-5:])/5, sum(closes[-20:])/20
    close = closes[-1]
    if not close >= ma5 >= ma20 or close/ma20 > 1.15:
        return None
    ret5 = (close/closes[-6]-1)*100
    if not 0 <= ret5 <= 15:
        return None
    score = round(50 + min(20, ret5*2) + min(20, quote["amount"]/1e9) + min(10, (close/ma20-1)*100), 1)
    return {"code": code, "name": quote["name"], "sector": sector, "score": score,
            "price": quote["price"], "pct": quote.get("pct"), "amount": quote["amount"],
            "as_of": quote["market_time"], "ma5": round(ma5, 3), "ma20": round(ma20, 3), "return5": round(ret5, 2),
            "reason": "已收盘日线站上5/20日线，5日涨幅0–15%，偏离20日线不超过15%",
            "trigger": "09:35后确认板块同步、成交放大且价格站稳分时均价；未确认则留在观察池",
            "risk": "开盘跌破前日低点或板块转弱时移出观察；隔夜跳空及A股T+1限制需单独评估",
            "invalid_below": items[-1]["low"], "source_url": "https://gu.qq.com/" + code}


class Premarket:
    def __init__(self, market, global_market):
        self.market, self.global_market, self.cache = market, global_market, market.cache
        self.db = self.cache.db
        self.db.execute("CREATE TABLE IF NOT EXISTS premarket_reports (day TEXT PRIMARY KEY, generated TEXT, payload TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS premarket_revisions (day TEXT, revision INTEGER, generated TEXT, payload TEXT NOT NULL, PRIMARY KEY(day,revision))")
        self.db.commit()
        self.lock = asyncio.Lock()
        self.running = False
        self.last_error = None
        self.last_attempt = None
        self.enabled = os.getenv("PULSE_SCHEDULER", "0") == "1"

    def list(self):
        days = [d for (d,) in self.db.execute("SELECT day FROM premarket_reports ORDER BY day DESC LIMIT 60")]
        return [{k: self.get(day)[k] for k in ("day", "generated_at", "status", "late", "coverage", "title")} for day in days]

    def get(self, day=None, revision=None):
        if day is None:
            last = self.db.execute("SELECT day FROM premarket_reports ORDER BY day DESC LIMIT 1").fetchone()
            if not last:
                return None
            day = last[0]
        if revision != 1:
            row = self.db.execute("SELECT payload FROM premarket_revisions WHERE day=?" +
                (" AND revision=?" if revision else " ORDER BY revision DESC LIMIT 1"),
                (day,revision) if revision else (day,)).fetchone()
            if row:
                return json.loads(row[0])
            if revision:
                return None
        row = self.db.execute("SELECT payload FROM premarket_reports WHERE day=?", (day,)).fetchone()
        return json.loads(row[0]) if row else None

    def status(self):
        return {"enabled": self.enabled, "timezone": "Asia/Shanghai", "schedule": "A股交易日 08:00",
                "running": self.running, "last_attempt": self.last_attempt, "last_error": self.last_error,
                "latest": self.list()[:1]}

    def import_archive(self, encoded):
        """Optional administrator backfill; preserve timestamps and earlier revisions.

        This is used to synchronize a genuine externally collected report when a
        hosting IP cannot reach some upstreams. It never supplies quote APIs.
        """
        if not encoded:
            return
        if len(encoded) > 100000:
            raise ValueError("盘前回填数据过大")
        decoder = zlib.decompressobj()
        raw = decoder.decompress(base64.b64decode(encoded, validate=True), 250000)
        if not decoder.eof or decoder.unconsumed_tail:
            raise ValueError("盘前回填数据不完整或超限")
        report = json.loads(raw)
        day = date.fromisoformat(report["day"]).isoformat()
        if datetime.fromisoformat(report["generated_at"]).tzinfo is None:
            raise ValueError("回填报告缺少时区")
        if hashlib.sha256(report["markdown"].encode()).hexdigest() != report["sha256"]:
            raise ValueError("回填报告校验值不符")
        rows = self.db.execute("SELECT payload FROM premarket_reports WHERE day=? UNION ALL SELECT payload FROM premarket_revisions WHERE day=?", (day,day)).fetchall()
        if any(json.loads(p)["sha256"] == report["sha256"] for (p,) in rows):
            return
        previous = self.get(day)
        if previous and previous["coverage"]["available"] > report["coverage"]["available"]:
            return
        report["revision"] = previous.get("revision",1)+1 if previous else 1
        report["supersedes_sha256"] = previous["sha256"] if previous else None
        report["provenance"] = "开发机真实采集后同步；保留源端时间与原始生成时间"
        with self.db:
            if previous:
                self.db.execute("INSERT INTO premarket_revisions VALUES (?,?,?,?)", (day, report["revision"], report["generated_at"], json.dumps(report,ensure_ascii=False,allow_nan=False)))
            else:
                self.db.execute("INSERT INTO premarket_reports VALUES (?,?,?)", (day,report["generated_at"],json.dumps(report,ensure_ascii=False,allow_nan=False)))

    async def headlines(self, current):
        provider = self.market.providers["live"]
        rows = {}
        cursor = None
        start = datetime.combine(current.date() - timedelta(days=1), time(15), SH)
        # At most 4 serialized pages; make coverage limits visible instead of implying exhaustive news.
        for _ in range(4):
            params = {"channel": "global-channel", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            data = (await provider.get(NEWS, params)).json()
            if data.get("code") != 20000 or not isinstance(data.get("data", {}).get("items"), list):
                raise Unavailable("华尔街见闻 · 全球快讯格式异常")
            node, oldest = data["data"], current
            for item in node["items"]:
                stamp = datetime.fromtimestamp(item["display_time"], SH)
                oldest = min(oldest, stamp)
                text = item.get("title") or item.get("content_text") or ""
                text = re.sub("<[^>]+>", "", text).strip()
                if start <= stamp <= current and WORLD.search(text):
                    key = str(item["id"])
                    rows[key] = {"id": key, "title": text[:140], "time": stamp.isoformat(),
                                 "url": item.get("uri") or f"https://wallstreetcn.com/livenews/{key}",
                                 "importance": item.get("score")}
            cursor = node.get("next_cursor")
            if oldest <= start or not cursor:
                break
        selected = sorted(rows.values(), key=lambda r: r["time"], reverse=True)
        return response({"items": selected[:15], "window_start": start.isoformat(),
                         "complete_window": oldest <= start, "matched": len(selected)},
                        "华尔街见闻 §5.4", NEWS, selected[0]["time"] if selected else None,
                        "最近最多400条全球快讯中的国际及行业线索，非完整新闻检索")

    async def generate(self, current=None, *, supplement=False):
        current = (current or now()).astimezone(SH)
        day = current.date().isoformat()
        async with self.lock:
            existing = self.get(day)
            if existing and not supplement:
                return existing
            if not time(8) <= current.time() < time(9,15):
                raise Unavailable("盘前生成窗口为北京时间08:00–09:15")
            self.running = True
            self.last_attempt = current.isoformat()
            try:
                calendar = await self.market.get("live", "calendar", 21600, current.year, current.month)
                if packet_status(calendar) != "available":
                    raise Unavailable("官方交易日历未确认，盘前报告等待重试")
                if day not in calendar["data"]:
                    return None
                previous = [d for d in calendar["data"] if d < day]
                if not previous:
                    prior = current.replace(day=1) - timedelta(days=1)
                    p = await self.market.get("live", "calendar", 21600, prior.year, prior.month)
                    if packet_status(p) != "available":
                        raise Unavailable("上月交易日历未确认")
                    previous = p["data"]
                prior_day = max(previous)
                global_data, headlines, quotes, sectors = await asyncio.gather(
                    self.global_market.snapshot(current, wait=True),
                    self.cache.get("live:report:news", 240, lambda: self.headlines(current)),
                    self.market.quotes("live", tuple(SAMPLE)),
                    self.market.sectors("live", "industry", "today", "inflow"))
                quote_map = {r["code"]: r for r in quotes["data"] or []}
                picks, reviewed = [], []
                # Three at a time for Tencent only. Eastmoney remains serialized by its adapter lock.
                codes = list(SAMPLE)
                for offset in range(0, len(codes), 3):
                    group = codes[offset:offset+3]
                    packets = await asyncio.gather(*(self.market.get("live", "kline", 900, c, "day", "qfq", 40) for c in group))
                    for code, bars in zip(group, packets):
                        pick = candidate(code, SAMPLE[code], quote_map.get(code), bars, prior_day, day)
                        reviewed.append({"code": code, "status": packet_status(bars), "as_of": bars.get("market_time"), "error": bars.get("error")})
                        if pick:
                            picks.append(pick)
                picks.sort(key=lambda r: (-r["score"], r["code"]))
                news_status = packet_status(headlines)
                verified_quotes = sum(r.get("market_time", "")[:10] == prior_day and not r.get("is_stale") for r in quotes["data"] or [])
                available = global_data["coverage"] + (news_status == "available") + (packet_status(sectors) == "available") + bool(verified_quotes)
                total = global_data["total"] + 3
                sector_items = (sectors.get("data") or {}).get("items", [])[:20]
                result = {"day": day, "title": f"Pulse · {day} A股盘前报告", "generated_at": now().isoformat(timespec="seconds"),
                    "scheduled_for": day+"T08:00:00+08:00", "late": current.time() >= time(8, 5),
                    "revision": (existing.get("revision", 1)+1) if existing else 1,
                    "supersedes_sha256": existing.get("sha256") if existing else None,
                    "status": "complete" if available == total else "partial" if available else "unavailable",
                    "coverage": {"available": available, "total": total, "quote_count": verified_quotes, "sample_size": len(SAMPLE)},
                    "prior_day": prior_day, "calendar_source": calendar["source_url"], "global": global_data,
                    "news": {"status": news_status, "source": headlines.get("source"), "source_url": headlines.get("source_url"),
                             "error": headlines.get("error"), "data": headlines.get("data"), "fetched_at": headlines.get("fetched_at")},
                    "sectors": {"status": packet_status(sectors), "items": sector_items, "source_url": sectors.get("source_url"),
                                "fetched_at": sectors.get("fetched_at"),
                                "market_time": sectors.get("market_time"), "error": sectors.get("error"), "note": sectors.get("note")},
                    "impacts": impacts(global_data["rows"]), "candidates": picks[:6], "reviewed": reviewed,
                    "selection": "固定12只沪深样本，非全市场；只用最近已完成交易日价格及日线。技术评分是规则分，不是胜率。",
                    "risk": ["未通过量价与板块共振确认前只观察；缺失或滞后数据不参与方向判断。",
                             "新闻到行业的映射属于规则推断，须核对上市公司公告与实际业务。",
                             "报价与期货可能有延迟；A股T+1、跳空、涨跌停和流动性会影响执行。"],
                    "method": "规则生成，事实与行业推断分别呈现；无模拟行情。报告一经归档不覆盖。"}
                result["markdown"] = render_markdown(result)
                result["sha256"] = hashlib.sha256(result["markdown"].encode()).hexdigest()
                with self.db:
                    if existing:
                        self.db.execute("INSERT INTO premarket_revisions VALUES (?,?,?,?)",
                                        (day, result["revision"], result["generated_at"], json.dumps(result, ensure_ascii=False, allow_nan=False)))
                    else:
                        self.db.execute("INSERT OR IGNORE INTO premarket_reports VALUES (?,?,?)",
                                        (day, result["generated_at"], json.dumps(result, ensure_ascii=False, allow_nan=False)))
                self.last_error = None
                return self.get(day)
            except Exception as exc:
                self.last_error = str(exc) if isinstance(exc, Unavailable) else type(exc).__name__
                LOG.exception("Premarket generation failed: %s", self.last_error)
                raise
            finally:
                self.running = False

    async def tick(self, current=None):
        current = (current or now()).astimezone(SH)
        # Do not invent an 08:00 report by using intraday/after-close data after 09:15.
        if current.weekday() < 5 and time(8) <= current.time() < time(9, 15) and not self.get(current.date().isoformat()):
            return await self.generate(current)
        return None

    async def loop(self):
        if not self.enabled:
            return
        while True:
            try:
                await self.tick()
            except Exception:
                pass  # last_error is visible in /api/reports/status; retry while still premarket.
            current = now()
            await asyncio.sleep(max(1, 60 - current.second - current.microsecond/1e6))


def render_markdown(r):
    lines = ["# " + r["title"], "", f'生成时间（北京时间）：{r["generated_at"]}',
             f'计划时间：{r["scheduled_for"]} · 最近完成的A股交易日：{r["prior_day"]}',
             "", "补生成：此报告在08:05之后生成，数据截至实际生成时点，不代表08:00历史快照。" if r["late"] else "",
             f'数据完整度：{r["coverage"]["available"]}/{r["coverage"]["total"]} · 状态：{r["status"]}',
             "", f'报告版本：{r.get("revision",1)}（补充版本保留旧版，可按 revision 参数导出）', "", "## 隔夜美股与全球期货、相关CFD", "", "| 标的 | 最新值 | 涨跌幅 | 已完成美股交易日/涨跌 | 北京时间 | 状态 |",
             "|---|---:|---:|---|---|---|"]
    for q in r["global"]["rows"]:
        lines.append(f'| {md_link(q["name"], q["url"])} | {n(q.get("price"))} {q["unit"]} | {n(q.get("pct"))}% | {q.get("close_date") or "—"} / {n(q.get("close_pct"))}% | {q.get("market_time") or "—"} | {q["status"]} |')
    lines += ["", "Yahoo期货涨跌基于上一日线收价；新浪商品CFD涨跌基于源端前结算/参考价。CFD为相关市场参考，不是交易所期货合约。", "", "外盘规则判断：" + r["global"]["stance"], "", "## 国际新闻与事件线索", ""]
    for item in (r["news"].get("data") or {}).get("items", []):
        lines.append(f'- {item["time"]} · {md_link(item["title"], item["url"])}')
    if r["news"]["status"] != "available":
        lines.append("新闻缺失或滞后：" + str(r["news"].get("error") or r["news"]["status"]))
    lines += ["", "新闻范围：最近最多400条全球快讯中的关键词匹配，不是完整新闻检索。", "", "## 行业影响（规则推断）", ""]
    for item in r["impacts"]:
        lines += [f'- **{item["theme"]} · {item["direction"]}**：{item["sectors"]}。依据：{md_link(item["evidence"], item["source_url"])}。{item["trigger"]}。']
    lines += ["", "## A股机会观察池", "", r["selection"], "", "| 标的 | 前收快照 | 5日收益 | 规则分 | 数据时间 |", "|---|---:|---:|---:|---|"]
    for pick in r["candidates"]:
        lines.append(f'| {md_link(pick["name"]+" "+pick["code"], pick["source_url"])} | {n(pick["price"])} | {n(pick["return5"])}% | {pick["score"]} | {pick["as_of"]} |')
    if not r["candidates"]:
        lines.append("\n没有通过校验的观察标的；不为凑数补入股票。")
    for pick in r["candidates"]:
        lines += ["", f'### {pick["name"]} {pick["code"]}', pick["reason"],
                  "确认条件：" + pick["trigger"], f'失效参考：前日低点 {n(pick["invalid_below"])}；{pick["risk"]}']
    lines += ["", "## 板块资金TOP20", "", "| 板块 | 主力净额（亿元） | 当日涨跌 |", "|---|---:|---:|"]
    for item in r["sectors"]["items"]:
        lines.append(f'| {clean(item["name"])} | {n(item["main_net"]/1e8)} | {n(item.get("pct"))}% |')
    lines += ["", f'状态：{r["sectors"]["status"]}。行情时间：{r["sectors"]["market_time"] or "上游未提供，日期未确认"}。',
              str(r["sectors"].get("error") or r["sectors"].get("note") or ""),
              md_link("板块数据来源", r["sectors"].get("source_url")), "", "## 风险与执行条件", ""]
    lines += ["- " + text for text in r["risk"]]
    lines += ["", r["method"], md_link("官方交易日历", r["calendar_source"]), ""]
    return "\n".join(lines)
