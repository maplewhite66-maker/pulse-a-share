"""Adapted from a-stock-data 3.10.4, Apache-2.0, and the Pulse prototype."""
import calendar
import math
import random
import re
from datetime import date, datetime
from html import unescape
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import asyncio
import httpx

from backend.services.clock import SH

QUOTE = "https://qt.gtimg.cn/q="
K_HOSTS = ("https://web.ifzq.gtimg.cn", "https://proxy.finance.qq.com/ifzqgtimg", "https://ifzq.gtimg.cn")
BOARD = "https://push2.eastmoney.com/api/qt/clist/get"
POOL = "https://push2ex.eastmoney.com"
FLOW = "https://push2delay.eastmoney.com/api/qt/stock/fflow/kline/get"
FLOW_DAY = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
CALENDAR = "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
NEWS = "https://api-one-wscn.awtmt.com/apiv1/content/lives"
HOURLY_BARS = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
CNINFO = "https://www.cninfo.com.cn"
INDICES = dict(zip(("sh000001", "sz399001", "sz399006", "sh000300", "sh000688"),
                   ("上证指数", "深证成指", "创业板指", "沪深300", "科创50")))
PERIODS = ("m1", "m5", "m15", "m30", "m60", "day", "week", "month")
BOARD_FS = {"industry": "m:90+t:2", "concept": "m:90+t:3", "region": "m:90+t:1"}
BOARD_PERIOD = {"today": ("f62", "f184", "f3", "f204"),
                "5d": ("f164", "f165", "f109", "f257"),
                "10d": ("f174", "f175", "f160", None)}
A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"


class Unavailable(RuntimeError):
    pass


def symbol(code):
    code = code.strip().lower()
    match = re.fullmatch(r"(?:(sh|sz|bj))?([0-9]{6})(?:\.(sh|sz|bj))?", code)
    if not match or (match[1] and match[3]):
        raise ValueError("请输入六位证券代码，可加 sh/sz/bj 前缀或 .SH/.SZ/.BJ 后缀")
    pre, number, suffix = match.groups()
    if pre or suffix:
        return (pre or suffix) + number
    if number.startswith(("92", "4", "8")):
        return "bj" + number
    return ("sh" if number.startswith(("5", "6", "9")) else "sz") + number


def number(value, multiplier=1):
    try:
        result = float(value) * multiplier if not isinstance(value, bool) else math.nan
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def market_stamp(raw):
    try:
        return datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=SH).isoformat()
    except ValueError:
        return None


def quote_rows(raw, requested):
    fields = {"price": (3, 1), "previous_close": (4, 1), "open": (5, 1), "change": (31, 1),
              "pct": (32, 1), "high": (33, 1), "low": (34, 1), "volume": (36, 100),
              "amount": (37, 10000), "turnover": (38, 1), "amplitude": (43, 1),
              "float_cap": (44, 10**8), "market_cap": (45, 10**8), "vol_ratio": (49, 1)}
    rows = {}
    for code, values in re.findall(r'v_([a-z]{2}[0-9]{6})="([^"]*)"', raw):
        f = values.split("~")
        if code not in requested or len(f) < 53 or f[2] != code[2:] or not f[1]:
            continue
        row = {"code": code, "name": f[1], "market_time": market_stamp(f[30])}
        row.update({name: number(f[i], scale) for name, (i, scale) in fields.items()})
        if row["price"] is None or row["price"] <= 0:
            continue
        row["inactive"] = row["amount"] == 0 and row["price"] == row["previous_close"]
        if code not in INDICES:
            row["book"] = [{"bid": number(f[9 + i*2]), "bid_lots": number(f[10 + i*2]),
                            "ask": number(f[19 + i*2]), "ask_lots": number(f[20 + i*2])}
                           for i in range(5)]
        rows[code] = row
    return [rows[code] for code in requested if code in rows]


def response(data, source, url, market_time=None, note=None):
    return {"data": data, "source": source, "source_url": url, "market_time": market_time, "note": note}


class Live:
    def __init__(self, client, em_interval=1.4):
        self.http = client
        self.em_interval = max(1.0, em_interval)
        self.em_lock = asyncio.Lock()
        self.em_after = 0.0
        self.em_cooldowns = {}
        self.em_routes = {}
        self.k_cooldowns = {}
        self.cninfo_orgs = {}
        self.cninfo_lock = asyncio.Lock()

    async def get(self, url, params=None, *, referer=None):
        headers = {"Referer": referer or url}
        failure = "上游不可用"
        for attempt in range(2):
            try:
                result = await self.http.get(url, params=params, headers=headers)
                result.raise_for_status()
                return result
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                failure = f"HTTP {status}"
                if status not in (500, 502, 503, 504):
                    break
            except httpx.RequestError as exc:
                failure = type(exc).__name__
            if not attempt:
                await asyncio.sleep(.5)
        raise Unavailable(f"{urlsplit(url).hostname} · {failure}")

    async def em_get(self, url, params):
        clock = asyncio.get_running_loop().time
        async with self.em_lock:
            route = self.em_routes.get(url)
            target = route[0] if route and route[1] > clock() else url
            host = urlsplit(target).hostname
            failure = "上游不可用"
            attempts, redirects = 0, 0
            while attempts < 2:
                if self.em_cooldowns.get(host, 0) > clock():
                    raise Unavailable(f"{host} · 请求冷却中")
                await asyncio.sleep(max(0, self.em_after - clock()))
                try:
                    result = await self.http.get(target, params=params, headers={"Referer": "https://quote.eastmoney.com/"})
                    if result.status_code in (301, 302, 307, 308) and redirects == 0:
                        candidate = urlsplit(urljoin(target, result.headers.get("location", "")))
                        if (candidate.scheme != "https" or candidate.netloc != "push2delay.eastmoney.com"
                                or candidate.path != urlsplit(url).path or candidate.hostname == host):
                            raise Unavailable(f"{host} · 上游跳转目标异常")
                        target = urlunsplit((candidate.scheme, candidate.netloc, candidate.path, "", ""))
                        host = candidate.hostname
                        self.em_routes[url] = (target, clock()+900)
                        redirects += 1
                        continue
                    result.raise_for_status()
                    payload = result.json()
                    if not isinstance(payload, dict) or payload.get("rc", 0) != 0:
                        raise Unavailable(f"{host} · 上游返回异常状态")
                    return {**payload, "_source_url":target}
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    failure = f"HTTP {status}"
                    if status not in (500, 502, 503, 504):
                        break
                except (httpx.RequestError, ValueError) as exc:
                    failure = type(exc).__name__
                finally:
                    self.em_after = clock() + self.em_interval + random.uniform(.1, .35)
                attempts += 1
            self.em_cooldowns[host] = clock() + (600 if failure in ("HTTP 403", "HTTP 429") else 75)
            raise Unavailable(f"{host} · {failure}")

    async def quotes(self, codes):
        if not codes:
            return response([], "腾讯财经", QUOTE)
        try:
            async with asyncio.timeout(9):
                raw = await self.get(QUOTE + ",".join(codes), referer="https://gu.qq.com/")
        except TimeoutError as exc:
            raise Unavailable("腾讯财经 · 报价请求超时") from exc
        rows = quote_rows(raw.content.decode("gbk", errors="replace"), codes)
        if not rows:
            raise Unavailable("腾讯财经 · 返回空报价或代码不存在")
        times = [r["market_time"] for r in rows if r["market_time"]]
        missing = sorted(set(codes) - {r["code"] for r in rows})
        return response(rows, "腾讯财经 §1.1", QUOTE, max(times, default=None),
                        "缺少报价 " + "、".join(missing) if missing else None)

    async def kline(self, code, period, adjust, count):
        if code.startswith("bj"):
            raise Unavailable("北交所 K 线未接入")
        minute = period.startswith("m") and period != "month"
        mode = "" if minute or code in INDICES else adjust
        path = "/appstock/app/kline/mkline" if minute else "/appstock/app/fqkline/get"
        param = f"{code},{period},,{count}" if minute else f"{code},{period},,,{count},{mode}"
        clock = asyncio.get_running_loop().time
        for host in K_HOSTS:
            if self.k_cooldowns.get(host, 0) > clock():
                continue
            url = host + path
            try:
                obj = (await self.get(url, {"param": param}, referer="https://gu.qq.com/")).json()
                node = obj["data"][code]
                items = node.get(mode + period, node.get(period))
                if not items:
                    raise ValueError("无 K 线")
                rows = {}
                for f in items:
                    t = (datetime.strptime(f[0], "%Y%m%d%H%M").replace(tzinfo=SH).isoformat()
                         if minute else date.fromisoformat(f[0]).isoformat())
                    op, close, high, low, vol = map(number, f[1:6])
                    if (None in (op, close, high, low, vol) or not 0 < low <= min(op, close) <= max(op, close) <= high
                            or vol < 0 or t in rows):
                        raise ValueError("K 线字段异常")
                    rows[t] = {"t": t, "open": op, "close": close, "high": high, "low": low, "volume": vol}
                bars = [rows[t] for t in sorted(rows)]
                unit = "股" if code.startswith(("sh688", "sh689")) else "手"
                return response({"items": bars, "adjust": mode or "none", "volume_unit": unit},
                                "腾讯 K 线 §1.2", url, bars[-1]["t"])
            except (Unavailable, TypeError, ValueError, KeyError, AttributeError):
                self.k_cooldowns[host] = clock() + 45
        raise Unavailable("腾讯 K 线各入口无可用数据。复权价异常时可试不复权")

    async def calendar(self, year, month):
        payload = (await self.get(CALENDAR, {"month": f"{year}-{month}"}, referer="https://www.szse.cn/")).json()
        raw = payload.get("data") or []
        dates = {date.fromisoformat(x["jyrq"]).isoformat(): str(x["jybz"]) for x in raw}
        expected = {date(year, month, i).isoformat() for i in range(1, calendar.monthrange(year, month)[1] + 1)}
        if set(dates) != expected or len(raw) != len(dates) or set(dates.values()) - {"0", "1"}:
            raise Unavailable("深交所 · 日历月份不符或数据不完整")
        return response([d for d in sorted(dates) if dates[d] == "1"], "深交所 §12", CALENDAR)

    async def hourly_bars(self, code):
        raw = await self.get(HOURLY_BARS, {"symbol": code, "scale": 30, "ma": "no", "datalen": 1023},
                             referer="https://finance.sina.com.cn/")
        payload = raw.json()
        if not isinstance(payload, list) or not payload:
            raise Unavailable("新浪财经 · 30 分钟历史未返回")
        rows = {}
        for source in payload:
            stamp = datetime.strptime(source["day"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=SH).isoformat()
            row = {key: number(source.get(key)) for key in ("open", "high", "low", "close", "volume", "amount")}
            if (None in row.values() or row["low"] <= 0 or row["volume"] < 0 or row["amount"] < 0
                    or not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]
                    or stamp in rows):
                raise Unavailable("新浪财经 · 30 分钟历史字段异常")
            rows[stamp] = {"t": stamp, **row}
        bars = [rows[stamp] for stamp in sorted(rows)]
        return response(bars, "新浪财经 30 分钟行情", HOURLY_BARS, bars[-1]["t"])

    async def clist(self, fs, fields, sort="f12", direction=1, page=1, size=100):
        payload = await self.em_get(BOARD, {"pn": page, "pz": size, "po": direction, "np": 1, "fltt": 2,
                                            "invt": 2, "fid": sort, "fs": fs, "fields": ",".join(fields)})
        data = payload.get("data") or {}
        rows = data.get("diff")
        if isinstance(rows, dict):
            rows = list(rows.values())
        if not isinstance(rows, list) or not rows:
            raise Unavailable("东方财富 · 板块或证券列表为空")
        return rows, int(data.get("total") or 0), payload["_source_url"]

    async def sectors(self, kind, period, order, timestamped=False):
        net, ratio, change, leader = BOARD_PERIOD[period]
        fields = ["f12", "f14", "f6", "f104", "f105", "f128", "f140", "f207", "f208", "f3", net, ratio, change,
                  "f62", "f164", "f174", "f66", "f72", "f78", "f84"]
        if leader:
            fields.append(leader)
        if timestamped:
            fields.append("f124")
        rows, total, source_url = await self.clist(BOARD_FS[kind], list(dict.fromkeys(fields)), net,
                                      1 if order == "inflow" else 0)
        out = []
        for row in rows:
            value = number(row.get(net))
            if value is None or not re.fullmatch(r"BK[0-9]{4}", str(row.get("f12", ""))):
                continue
            up, down = number(row.get("f104")), number(row.get("f105"))
            code, exchange = str(row.get("f207", "")), str(row.get("f208", ""))
            leader_code = ({"1":"sh","0":"sz"}.get(exchange, "") + code) if re.fullmatch(r"[0-9]{6}", code) else None
            item = {"code": row["f12"], "name": row.get("f14"), "pct": number(row.get(change)), "pct_today": number(row.get("f3")),
                        "main_net": value, "main_pct": number(row.get(ratio)), "amount": number(row.get("f6")),
                        "net_1": number(row.get("f62")), "net_5": number(row.get("f164")), "net_10": number(row.get("f174")),
                        "leader": (row.get(leader) or row.get("f140")) if leader else None, "leader_code": leader_code if period == "today" else None,
                        "up": up, "down": down, "super_net": number(row.get("f66")), "large_net": number(row.get("f72"))}
            if timestamped:
                item["market_time"] = self.quote_stamp(row.get("f124"))
            out.append(item)
        if not out:
            raise Unavailable("东方财富 · 主力净额字段缺失")
        out.sort(key=lambda row: row["main_net"], reverse=order == "inflow")
        stamps = [row["market_time"] for row in out if row.get("market_time")]
        return response({"items": out, "total": total}, "东方财富板块 §3.8", source_url, max(stamps, default=None),
                        ("上游延时入口。 " if "push2delay" in source_url else "") +
                        ("部分或全部行缺少上游行情时间。 " if len(stamps) != len(out) else "") + "成交额和涨跌广度为当日口径")

    async def live_board_members(self, board):
        source, _, url = await self.clist(f"b:{board}+f:!50", self.security_fields, sort="f62", size=100)
        rows = [self.security_row(raw) for raw in source]
        return response(rows, "东方财富板块成分净额", url)

    async def members(self, board):
        return await self.security_list(f"b:{board}+f:!50", max_pages=20)

    async def universe(self):
        return await self.security_list(A_SHARE_FS, max_pages=75, is_market=True)

    security_fields = ("f12", "f13", "f14", "f3", "f6", "f2", "f10", "f8", "f117", "f62", "f124")

    @staticmethod
    def quote_stamp(value):
        stamp = number(value)
        try:
            return datetime.fromtimestamp(stamp, SH).isoformat() if stamp and stamp > 0 else None
        except (ValueError, OSError, OverflowError):
            return None

    def security_row(self, raw):
        code = str(raw.get("f12", ""))
        if not re.fullmatch(r"[0-9]{6}", code):
            raise Unavailable("东方财富 · 证券列表含无效代码")
        ticker = symbol(code) if str(raw.get("f13")) != "1" else "sh" + code
        stamp = self.quote_stamp(raw.get("f124"))
        return {"code":ticker,"name":raw.get("f14"),"pct":number(raw.get("f3")),"amount":number(raw.get("f6")),
                "price":number(raw.get("f2")),"vol_ratio":number(raw.get("f10")),"turnover":number(raw.get("f8")),
                "float_cap":number(raw.get("f117")),"main_net":number(raw.get("f62")),"market_time":stamp}

    async def idea_pool(self, session):
        sort = "f6" if session == "open" else "f62"
        items, _, url = await self.clist(A_SHARE_FS, self.security_fields, sort, size=100)
        rows = [self.security_row(item) for item in items]
        stamps = [row["market_time"] for row in rows if row["market_time"]]
        return response(rows, "东方财富沪深北 A 股榜单", url, max(stamps, default=None),
                        "上游延时入口" if "push2delay" in url else None)

    async def security_list(self, fs, max_pages, is_market=False):
        rows = {}
        for page in range(1, max_pages + 1):
            items, total, source_url = await self.clist(fs, self.security_fields, direction=0, page=page)
            for item in items:
                row = self.security_row(item)
                if row["code"] in rows:
                    raise Unavailable("东方财富 · 证券分页重复，请稍后重试")
                rows[row["code"]] = row
            if total and len(rows) >= total:
                break
        else:
            raise Unavailable("东方财富 · 证券分页未完整返回")
        stamps = [r["market_time"] for r in rows.values() if r["market_time"]]
        return response(list(rows.values()), "东方财富沪深北 A 股" if is_market else "东方财富板块成分",
                        source_url, max(stamps, default=None), "上游延时入口" if "push2delay" in source_url else None)

    async def stock_flow(self, code, span):
        url = FLOW if span == "minute" else FLOW_DAY
        market = {"sh": 1, "sz": 0, "bj": 0}[code[:2]]
        params = {"secid": f"{market}.{code[2:]}", "fields1": "f1,f2,f3,f7",
                  "fields2": "f51,f52,f53,f54,f55,f56,f57"}
        params.update({"klt": 1, "lmt": 0} if span == "minute" else {"lmt": 120})
        payload = await self.em_get(url, params)
        node = payload.get("data") or {}
        if str(node.get("code")) != code[2:] or str(node.get("market")) != str(market):
            raise Unavailable("东方财富 · 资金流证券代码或市场不符")
        rows = {}
        for raw in node.get("klines") or []:
            t, *values = raw.split(",")
            if len(values) < 5:
                raise Unavailable("东方财富 · 资金流字段不全")
            parsed = datetime.fromisoformat(t)
            if span == "minute":
                hm = parsed.strftime("%H:%M")
                if not ("09:30" <= hm <= "11:30" or "13:00" <= hm <= "15:00"):
                    raise Unavailable("东方财富 · 资金流时间超出交易时段")
                t = parsed.replace(tzinfo=SH).isoformat(timespec="seconds")
            else:
                t = parsed.date().isoformat()
            rows[t] = {"t": t, **dict(zip(("main_net","small_net","mid_net","large_net","super_net"), map(number, values)))}
        rows = sorted(rows.values(), key=lambda row: row["t"])
        if span == "minute" and len({row["t"][:10] for row in rows}) > 1:
            raise Unavailable("东方财富 · 分时资金流混入多个交易日")
        if not rows or rows[-1]["main_net"] is None:
            raise Unavailable("东方财富 · 无个股主力资金数据")
        note = "当日累计净额，单位元；东方财富延时入口" if span == "minute" else "各交易日净额，单位元"
        return response(rows, "东方财富个股资金", payload["_source_url"], rows[-1]["t"], note)

    async def pool(self, day, kind):
        endpoint, sort = {"up": ("getTopicZTPool", "fbt:asc"), "down": ("getTopicDTPool", "fund:asc"),
                          "broken": ("getTopicZBPool", "fbt:asc"), "yesterday": ("getYesterdayZTPool", "zs:desc")}[kind]
        url = POOL + "/" + endpoint
        node = (await self.em_get(url, {"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
                                        "Pageindex": 0, "pagesize": 10000, "sort": sort, "date": day.replace("-", "")})).get("data")
        if not isinstance(node, dict) or not isinstance(node.get("pool"), list):
            raise Unavailable("东方财富 · 涨跌停池未发布或返回异常")
        if node.get("qdate") and str(node["qdate"]) != day.replace("-", ""):
            raise Unavailable("东方财富 · 涨跌停池日期不符")
        rows = [{"code": symbol(p["c"]), "name": p["n"], "pct": number(p.get("zdp")),
                 "streak": number(p.get("lbc")), "amount": number(p.get("amount")),
                 "seal_fund": number(p.get("fund")), "industry": p.get("hybk")} for p in node["pool"]]
        return response(rows, "东方财富涨跌停池 §8.1", url, day if node.get("qdate") else None,
                        None if node.get("qdate") else "上游未返回可验证的交易日期")

    async def news(self):
        payload = (await self.get(NEWS, {"channel":"global-channel","limit":20})).json()
        rows = []
        if payload.get("code") != 20000 or not payload["data"]["items"]:
            raise Unavailable("华尔街见闻 · 未返回有效快讯")
        for row in payload["data"]["items"]:
            url = row.get("uri") or ""
            parts = urlsplit(url)
            host = parts.hostname or ""
            if parts.scheme != "https" or not (host == "wallstreetcn.com" or host.endswith(".wallstreetcn.com")):
                url = None
            title = row.get("title") or row.get("content_text")
            if title:
                rows.append({"id": str(row["id"]), "title": title[:240], "url": url,
                             "time": datetime.fromtimestamp(int(row["display_time"]), SH).isoformat()})
        return response(rows, "华尔街见闻 §5.4", NEWS, rows[0]["time"] if rows else None)


    async def announcements(self, code):
        if code in INDICES:
            raise Unavailable("指数不适用上市公司公告")
        ticker = code[2:]
        url = CNINFO + "/new/hisAnnouncement/query"
        referer = CNINFO + "/new/disclosure"
        async with self.cninfo_lock:
            if not self.cninfo_orgs:
                result = await self.get(CNINFO + "/new/data/szse_stock.json", referer=referer)
                self.cninfo_orgs = {r["code"]: r["orgId"] for r in result.json()["stockList"]}
        org = self.cninfo_orgs.get(ticker)
        if not org:
            raise Unavailable("巨潮资讯 · 证券公告标识未接入")
        form = {"stock": f"{ticker},{org}", "tabName":"fulltext", "pageSize":"12", "pageNum":"1",
                "column":"","category":"","plate":"","seDate":"","searchkey":"","secid":"",
                "sortName":"","sortType":"","isHLtitle":"false"}
        try:
            raw = await self.http.post(url, data=form, headers={"Referer":referer,"Origin":CNINFO})
            raw.raise_for_status()
        except httpx.HTTPError as exc:
            raise Unavailable("巨潮资讯 · 公告请求失败 " + type(exc).__name__) from exc
        payload = raw.json()
        if "announcements" not in payload or ("totalAnnouncement" not in payload and payload["announcements"] is None):
            raise Unavailable("巨潮资讯 · 公告列表格式异常")
        rows = []
        for r in payload["announcements"] or []:
            if str(r.get("secCode")) != ticker:
                continue
            stamp = datetime.fromtimestamp(r["announcementTime"]/1000, SH).isoformat()
            title = unescape(re.sub("<[^>]*>", "", r["announcementTitle"]))
            link = CNINFO + "/new/disclosure/detail?" + urlencode({"annoId":r["announcementId"],"stockCode":ticker})
            rows.append({"id":str(r["announcementId"]),"title":title,"url":link,"time":stamp,"code":code})
        return response(rows,"巨潮资讯 §7.1",url,rows[0]["time"] if rows else None)
