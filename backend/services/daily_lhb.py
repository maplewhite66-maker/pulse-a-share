"""Archive post-close public Dragon/Tiger disclosures and render a printable card."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape

from backend.adapters.live import Unavailable
from backend.services.capital_profiler import seat_label
from backend.services.clock import now
from backend.services.strategy_inputs import safe_text

ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SOURCE_URL = "https://data.eastmoney.com/stock/lhb.html"
SOURCE = "东方财富 · 交易公开信息"
REPORTS = ("RPT_DAILYBILLBOARD_DETAILS", "RPT_BILLBOARD_DAILYDETAILSBUY", "RPT_BILLBOARD_DAILYDETAILSSELL")
HISTORY = "live:reports:daily-lhb:v1"
SLOTS = ("17:00", "18:30", "20:30")
MULTI = re.compile(r"连续|累计|累积|[两三四五二2345]个?交易日|异常波动期间")
THREE = re.compile(r"[三3]个?交易日")
CODE = re.compile(r"((?:60|68)\d{4})\.SH|((?:00|30)\d{4})\.SZ|((?:4\d|8\d|92)\d{4})\.BJ")
ALIASES = (("银河证券", "大连黄河路"), ("银河证券", "大连金马路"), ("招商证券", "福州六一中路"),
           ("国泰海通证券", "武汉紫阳东路"), ("国泰君安证券", "武汉紫阳东路"),
           ("中信证券", "北京呼家楼"), ("招商证券", "深圳深南东路"))


def cents(value, *, signed=False):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) >= Decimal("1e15") or (amount < 0 and not signed):
            return None
        return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        return None


def ticker(raw):
    match = CODE.fullmatch(raw) if isinstance(raw, str) else None
    if not match:
        return None
    return next(exchange + group for exchange, group in zip(("sh", "sz", "bj"), match.groups()) if group)


def trade_key(row, day):
    if not isinstance(row, dict) or str(row.get("TRADE_DATE", ""))[:10] != day:
        raise ValueError("龙虎榜数据混入其他日期")
    code = ticker(row.get("SECUCODE"))
    if not code:
        return None
    trade_id = str(row.get("TRADE_ID", ""))
    if not re.fullmatch(r"[0-9]{1,24}", trade_id):
        raise ValueError("A股披露记录缺少编号")
    return code, trade_id


def group_name(name):
    kind, _ = seat_label(name)
    if kind == "institution":
        return "机构专用"
    if kind == "lhasa":
        return "东方财富拉萨营业部"
    for broker, location in ALIASES:
        if broker in name and location in name:
            return location
    return name.replace("股份有限公司", "").replace("有限责任公司", "").replace("证券营业部", "营业部")


def aggregate(day, list_rows, buy_rows, sell_rows):
    universe = {}
    for row in list_rows:
        key = trade_key(row, day)
        if not key:
            continue
        name = safe_text(row.get("SECURITY_NAME_ABBR"), 50)
        cause = safe_text(row.get("EXPLANATION"), 180)
        if not name or not cause:
            raise ValueError("A股披露记录缺少简称或上榜原因")
        scope = "three" if THREE.search(cause) else "multi" if MULTI.search(cause) else "daily"
        detail = {"key": key, "name": name, "scope": scope, "cause": cause,
                  "volume": cents(row.get("BILLBOARD_DEAL_AMT")) or 0}
        old = universe.setdefault(key, detail)
        if old != detail:
            raise ValueError("同一披露编号出现冲突")
    if not universe:
        raise Unavailable("当日公开披露尚未就绪")

    detail_rows = {"buy": defaultdict(dict), "sell": defaultdict(dict)}
    unmatched = set()
    for side, raw in (("buy", buy_rows), ("sell", sell_rows)):
        for row in raw:
            key = trade_key(row, day)
            if not key:
                continue
            if key not in universe:
                unmatched.add(key)
                continue
            name = safe_text(row.get("OPERATEDEPT_NAME"), 150)
            buy, sell = cents(row.get("BUY")), cents(row.get("SELL"))
            observed = cents(row.get("NET"), signed=True)
            listed_amount = buy if side == "buy" else sell
            # Shanghai rows can disclose a single side with a matching published
            # NET. The blank opposite column remains outside that row's scope.
            if (not name or listed_amount is None or any(row.get(field) is not None and value is None
                                                       for field, value in (("BUY", buy), ("SELL", sell)))):
                raise ValueError("席位披露缺少可核对的成交金额")
            net = (buy or 0) - (sell or 0)
            if (observed is None and (buy is None or sell is None)) or (observed is not None and abs(observed - net) > 1):
                raise ValueError("席位披露净额与成交金额不符")
            identity = (safe_text(str(row.get("OPERATEDEPT_CODE") or ""), 40), name, buy, sell)
            detail_rows[side][key][identity] = net

    by_code = defaultdict(list)
    eligible = [row for row in universe.values() if row["key"] in detail_rows["buy"] and row["key"] in detail_rows["sell"]]
    for report in eligible:
        code, _ = report["key"]
        by_code[(code, report["scope"] != "daily")].append(report)
    if not eligible:
        raise Unavailable("个股上榜名单和双边席位详情尚未对齐")
    groups, positive, negative = defaultdict(dict), 0, 0
    selected = []
    for choices in by_code.values():
        def order(item):
            key = item["key"]
            sides = min(len(detail_rows["buy"][key]), len(detail_rows["sell"][key]))
            return (-sides, -item["volume"], int(key[1]))
        report = min(choices, key=order)
        selected.append(report)
        key = report["key"]
        seats = {**detail_rows["buy"][key], **detail_rows["sell"][key]}
        # Opposite-side duplicates are the same bilateral disclosure. Anonymous
        # institution rows are deduplicated only on identical reported amounts.
        net_by_seat = defaultdict(int)
        for (seat_code, seat, buy, sell), net in seats.items():
            identity = (seat, buy, sell) if not seat_code or seat_code == "0" else (seat, seat_code)
            net_by_seat[identity] += net
        for identity, net in net_by_seat.items():
            if not net:
                continue
            seat = identity[0]
            group = groups[group_name(seat)]
            position = group.setdefault(key, {
                "code": key[0], "name": report["name"], "scope": report["scope"],
                "cause": report["cause"], "trade_id": key[1], "net_cents": 0, "seats": set(),
            })
            position["net_cents"] += net
            position["seats"].add(seat)
            if report["scope"] == "daily":
                if net > 0:
                    positive += net
                else:
                    negative -= net

    ranked = []
    for group, positions in groups.items():
        items = [{**row, "seats": sorted(row["seats"])} for row in positions.values() if row["net_cents"]]
        items.sort(key=lambda row: (row["scope"] != "daily", -abs(row["net_cents"]), row["code"]))
        if items:
            ranked.append({"label": group, "items": items,
                           "activity": sum(abs(x["net_cents"]) for x in items if x["scope"] == "daily"),
                           "cumulative_activity": sum(abs(x["net_cents"]) for x in items if x["scope"] != "daily")})
    ranked.sort(key=lambda row: (-row["activity"], -row["cumulative_activity"], row["label"]))
    return {"day": day, "source": SOURCE, "source_url": SOURCE_URL,
            "buy_net_cents": positive, "sell_net_cents": negative,
            "listed_events": len(universe), "matched_events": len(eligible), "unmatched_events": len(unmatched),
            "daily_stocks": sum(x["scope"] == "daily" for x in selected),
            "multi_stocks": sum(x["scope"] != "daily" for x in selected), "groups": ranked}


async def read_report(provider, report, day):
    page_size, rows, expected, pages = 200, [], None, 1
    # Fixed conditions only. Report names are internal constants and day is parsed as a date.
    day = date.fromisoformat(day).isoformat()
    cols = ("SECURITY_CODE,TRADE_ID" if report == REPORTS[0]
            else "SECURITY_CODE,TRADE_ID,OPERATEDEPT_CODE,BUY,SELL")
    for page in range(1, 9):
        if page > pages:
            break
        response = await provider.get(ENDPOINT, {
            "reportName": report, "columns": "ALL", "source": "WEB", "client": "WEB",
            "pageNumber": page, "pageSize": page_size, "filter": f"(TRADE_DATE='{day}')",
            "sortColumns": cols, "sortTypes": ",".join("1" for _ in cols.split(",")),
        }, referer=SOURCE_URL)
        payload = response.json()
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise Unavailable("当日龙虎榜暂未披露或上游受限")
        total, count_pages = result.get("count"), result.get("pages")
        if type(total) is not int or type(count_pages) is not int or not 0 < count_pages <= 8:
            raise Unavailable("龙虎榜分页信息不可核对")
        if expected is not None and (expected != total or pages != count_pages):
            raise Unavailable("龙虎榜在分页期间改版，等待下一轮")
        expected, pages = total, count_pages
        rows.extend(result["data"])
    if not rows or len(rows) != expected:
        raise Unavailable("龙虎榜分页尚不完整")
    return rows


async def load_market_report(provider, day):
    raw = await asyncio.gather(*(read_report(provider, report, day) for report in REPORTS))
    return aggregate(day, *raw)


def compact(cents_value, sign=False):
    amount = abs(Decimal(cents_value) / 100)
    if amount >= 100_000_000:
        number = f"{amount / Decimal(100_000_000):.2f}亿"
    elif amount >= 10_000:
        number = f"{amount / Decimal(10_000):.0f}万"
    else:
        number = f"{amount:.0f}元"
    return ("−" if cents_value < 0 else "+" if sign and cents_value else "") + number


def clipped(value, max_units):
    result, used = [], 0
    for char in value:
        if not char.isprintable():
            continue
        width = 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if used + width > max_units:
            return "".join(result[:-1]) + "…"
        used += width
        result.append(char)
    return "".join(result)


def render_svg(report):
    """Pure SVG with no external URLs, scripts, foreign objects or source HTML."""
    day = date.fromisoformat(report["day"])
    title = f"{day.month}月{day.day}日 游资龙虎榜"
    width, footer, body_start = 1120, 116, 248
    sections = []
    for group in report["groups"][:24]:
        items = group["items"][:9]
        height = max(78, 25 + 43 * max(1, (len(items) + 2) // 3))
        sections.append((group, items, height))
    height = body_start + sum(s[2] for s in sections) + footer
    font = "'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
           f'<title>{escape(title)}</title>', '<defs><linearGradient id="mast"><stop stop-color="#eaf4ee"/>'
           '<stop offset="0.52" stop-color="#fffcf8"/><stop offset="1" stop-color="#f9e9e7"/></linearGradient></defs>',
           f'<rect width="{width}" height="{height}" fill="#f7f2ea"/>',
           '<rect x="20" y="20" width="1080" height="202" rx="16" fill="url(#mast)"/>',
           f'<g font-family="{font}">']
    def label(x, y, value, size, color, *, weight=500, anchor="start"):
        # Only numeric/layout constants can affect markup.
        value = escape("".join(char for char in str(value) if char.isprintable()))
        out.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color}" text-anchor="{anchor}">{value}</text>')
    label(42, 51, f"PULSE  /  收盘复盘  /  {day.isoformat()}", 19, "#475951", weight=700)
    label(42, 119, title, 54, "#8c2d33", weight=800)
    label(44, 155, "单日公开席位净额 · 三日和其他累计披露单独标注", 21, "#515b54")
    label(44, 201, "净卖 " + compact(report["sell_net_cents"]), 31, "#126346", weight=800)
    label(1071, 201, "净买 " + compact(report["buy_net_cents"]), 31, "#a43338", weight=800, anchor="end")
    sell, buy = report["sell_net_cents"], report["buy_net_cents"]
    green = round(530 * sell / (sell + buy)) if sell + buy else 265
    out.append('<rect x="290" y="187" width="530" height="10" rx="5" fill="#eadeda"/>')
    if green:
        out.append(f'<rect x="290" y="187" width="{green}" height="10" rx="5" fill="#83ae97"/>')
    if 530 - green:
        out.append(f'<rect x="{290 + green}" y="187" width="{530 - green}" height="10" rx="5" fill="#cb8990"/>')
    y = body_start
    for index, (group, items, block) in enumerate(sections):
        out.append(f'<rect x="20" y="{y}" width="1080" height="{block - 6}" rx="9" fill="{("#fffdf8" if index%2==0 else "#f2ece1")}"/>')
        name = group["label"]
        short = clipped(name, 22)
        if short.endswith("…"):
            first = short[:-1]
            label(42, y + 25, first, 18, "#283830", weight=800)
            label(42, y + 46, clipped(name[len(first):], 22), 18, "#283830", weight=800)
        else:
            label(42, y + 32, short, 18, "#283830", weight=800)
        if len(group["items"]) > len(items):
            label(43, y + 64, f"另有 {len(group['items']) - len(items)} 条见明细", 13, "#52645a")
        for i, row in enumerate(items):
            x, top = 256 + (i % 3) * 279, y + 27 + (i // 3) * 43
            scope = " · 三日" if row["scope"] == "three" else " · 累计" if row["scope"] == "multi" else ""
            label(x, top, clipped(row["name"], 14) + scope, 20, "#27332d", weight=700)
            label(x, top + 20, compact(row["net_cents"], True), 18,
                  "#a43338" if row["net_cents"] > 0 else "#126346", weight=800)
        y += block
    coverage = (f"覆盖：单日 {report['daily_stocks']} 只／累计 {report['multi_stocks']} 只"
                f" · 匹配披露 {report['matched_events']}/{report['listed_events']}")
    if report["unmatched_events"]:
        coverage += f" · 另有 {report['unmatched_events']} 条待匹配"
    label(42, y + 21, coverage, 18, "#43544b")
    label(42, y + 46, f"来源：{report['source']} · {report['generated_at'][11:16]} 北京时间 · 海报前 {len(sections)} 组，其余见网页明细", 16, "#52645a")
    label(42, y + 74, "按已披露营业部或交易单元归类；龙虎榜席位不能确认实际操盘个人。", 16, "#52645a")
    out.append("</g></svg>")
    return "".join(out)


class DailyLhb:
    def __init__(self, market, enabled=False, loader=load_market_report):
        self.market, self.enabled, self.loader = market, enabled, loader
        self.running = False
        self.retry_at = None
        self.note = None
        self.note_day = None

    def list_days(self):
        return [row[0] for row in self.market.cache.db.execute(
            "SELECT at FROM history WHERE key = ? ORDER BY at DESC LIMIT 120", (HISTORY,))]

    def get(self, day):
        if day is None:
            found = self.market.cache.history(HISTORY, 1)
            return found[0][1] if found else None
        row = self.market.cache.db.execute("SELECT payload FROM history WHERE key = ? AND at = ?", (HISTORY, day)).fetchone()
        return json.loads(row[0]) if row else None

    async def tick(self, current):
        hm, day = current.strftime("%H:%M"), current.date().isoformat()
        if (not self.enabled or self.running or current.weekday() >= 5 or not SLOTS[0] <= hm <= "22:00"
                or self.retry_at and current < self.retry_at):
            return
        slot = max(s for s in SLOTS if s <= hm)
        prior = self.get(day)
        if prior and prior["slot"] >= slot:
            return
        self.running = True
        self.note_day = day
        try:
            clock = await self.market.clock("live")
            if clock.get("calendar_error") or clock.get("phase") != "closed" or clock.get("latest_day") != day:
                self.note = "等待交易日历确认"
                return
            async with asyncio.timeout(48):
                result = await self.loader(self.market.providers["live"], day)
            if result["day"] != day:
                raise ValueError("报告日期不符")
            incomplete = result["matched_events"] < result["listed_events"] or result["unmatched_events"] > 0
            if incomplete and hm < SLOTS[-1]:
                self.note = "公开披露仍在补齐，稍后重试"
                return
            if prior and (result["matched_events"] < prior["matched_events"] or
                          result["listed_events"] < prior["listed_events"] or
                          result["unmatched_events"] > prior["unmatched_events"]):
                self.note = "新快照覆盖下降，保留上次有效榜单"
                return
            payload = {**result, "slot": slot, "generated_at": max(current, now()).isoformat(timespec="seconds")}
            payload["revision"] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
            self.market.cache.record(HISTORY, day, payload, keep=120, replace=True)
            self.note = None
        except (Unavailable, TimeoutError, OSError, ValueError, TypeError, KeyError) as exc:
            self.note = "当前披露数据暂不可用，等待下一轮"
            logging.getLogger("pulse").warning("龙虎榜采集未完成 %s · %s: %s", day, type(exc).__name__, exc)
        finally:
            self.retry_at = current + timedelta(minutes=5)
            self.running = False

    async def loop(self):
        while self.enabled:
            try:
                await self.tick(now())
            except Exception:
                logging.getLogger("pulse").exception("每日龙虎榜任务失败")
            await asyncio.sleep(60)
