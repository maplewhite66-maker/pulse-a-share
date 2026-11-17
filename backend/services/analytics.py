from collections import defaultdict
from statistics import mean
from math import sqrt
from datetime import datetime


def ema(values, span):
    result = []
    factor = 2 / (span + 1)
    for x in values:
        result.append(x if not result else result[-1] + factor * (x - result[-1]))
    return result


def indicators(rows):
    close = [r["close"] for r in rows]
    dif = [a - b for a, b in zip(ema(close, 12), ema(close, 26))]
    dea = ema(dif, 9)
    delta = [b - a for a, b in zip(close, close[1:])]
    gains = losses = None
    for i, row in enumerate(rows):
        row.update({f"ma{n}": round(mean(close[i+1-n:i+1]), 4) if i+1 >= n else None for n in (5, 20)})
        if i == 14:
            gains = mean(max(v, 0) for v in delta[:14])
            losses = mean(max(-v, 0) for v in delta[:14])
        elif i > 14:
            v = delta[i-1]
            gains = (gains*13 + max(v, 0))/14
            losses = (losses*13 + max(-v, 0))/14
        rsi = None if gains is None else 100*gains/(gains+losses) if gains+losses else 50
        row.update({"dif": round(dif[i], 4) if i >= 25 else None,
                    "dea": round(dea[i], 4) if i >= 33 else None,
                    "macd": round(2*(dif[i]-dea[i]), 4) if i >= 33 else None,
                    "rsi": round(rsi, 2) if rsi is not None else None})
    return rows


def clip(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def sector_heat(row, delta_rank):
    up, down = row.get("up"), row.get("down")
    nets = [row.get(key) for key in ("net_1", "net_5", "net_10")]
    values = {
        "当日主力净额强度": clip(50 + nets[0] / 5e7) if nets[0] is not None else None,
        "5日及10日资金同向": (sum(v > 0 for v in nets[1:]) * 50) if None not in nets[1:] else None,
        "区间涨跌幅": clip(50 + row["pct"] * 10) if row.get("pct") is not None else None,
        "当日上涨广度": up / (up+down) * 100 if None not in (up, down) and up+down else None,
        "排名变动": clip(50 + 5 * delta_rank) if delta_rank is not None else None,
    }
    return weighted(values, (30, 25, 15, 20, 10), minimum=0.6)


def weighted(values, weights, minimum=1):
    weights = dict(zip(values, weights))
    active = sum(weights[k] for k, v in values.items() if v is not None)
    coverage = active / sum(weights.values())
    score = round(sum(weights[k] * v for k, v in values.items() if v is not None) / active, 1) if coverage >= minimum and active else None
    return {"score": score, "coverage": round(coverage, 2),
            "parts": [{"label": k, "score": round(v, 1) if v is not None else None, "weight": weights[k]} for k, v in values.items()]}


def percentile(rows, field):
    grouped = defaultdict(list)
    for row in rows:
        if row.get(field) is not None:
            grouped[row[field]].append(row["code"])
    size = sum(map(len, grouped.values()))
    rank, result = 0, {}
    for value in sorted(grouped):
        group = grouped[value]
        for code in group:
            result[code] = 100 * (rank + (len(group) - 1)/2) / (size - 1) if size > 1 else 50
        rank += len(group)
    return result


def daily_returns(bars):
    return {b["t"]: (b["close"]/a["close"] - 1)*100 for a, b in zip(bars, bars[1:]) if a["close"] > 0}


def correlation(a, b):
    overlap = sorted(a.keys() & b.keys())[-20:]
    if len(overlap) < 12:
        return None
    x, y = [[d[k] for k in overlap] for d in (a, b)]
    u, v = mean(x), mean(y)
    xx, yy = sum((p-u)**2 for p in x), sum((p-v)**2 for p in y)
    return sum((p-u)*(q-v) for p, q in zip(x,y)) / sqrt(xx*yy) if xx and yy else None


def role_scores(rows, history, flows, board_pct):
    activity = percentile(rows, "amount")
    capital = percentile(rows, "float_cap")
    flow_rank = percentile(rows, "main_net")
    changes = percentile(rows, "pct")
    metrics = {}
    for code, bars in history.items():
        if len(bars) >= 21 and bars[-6]["close"] > 0:
            metrics[code] = {"return_5": (bars[-1]["close"] / bars[-6]["close"] - 1)*100,
                             "range_position": range_position(bars), "returns": daily_returns(bars)}
    by_date = defaultdict(list)
    for item in metrics.values():
        for d, pct in item["returns"].items():
            by_date[d].append(pct)
    median5 = mean(x["return_5"] for x in metrics.values()) if metrics else None
    result = {"龙头": [], "中军": [], "低位补涨观察": []}
    for row in rows:
        code = row["code"]
        stat = metrics.get(code)
        excess = row["pct"] - board_pct if board_pct is not None and row["pct"] is not None else None
        if not stat or excess is None:
            continue
        sector_returns = {d: (sum(v) - stat["returns"][d])/(len(v)-1) for d, v in by_date.items()
                          if len(v) >= 3 and d in stat["returns"]}
        corr = correlation(stat["returns"], sector_returns)
        common = {"code": code, "name": row["name"], "pct": row["pct"], "relative": round(excess, 2),
                  "amount": row["amount"], "main_net": row["main_net"], "return_5": round(stat["return_5"], 2)}
        cash, size, flow, gain = [r.get(code) for r in (activity, capital, flow_rank, changes)]
        momentum = clip(50 + stat["return_5"]*5)
        if excess > 0 and stat["return_5"] > 0:
            result["龙头"].append({**common, **weighted({"板块内涨幅分位": gain, "成交额分位": cash,
                "近5日强势度": momentum, "主力净额分位": flow}, (25, 25, 30, 20))})
        if size is not None and size >= 50 and cash is not None and cash >= 50:
            result["中军"].append({**common, **weighted({"流通市值分位": size, "成交额分位": cash,
                "近20日成分样本相关性": clip((corr+1)*50) if corr is not None else None}, (40, 35, 25))})
        net = [f.get("main_net") for f in flows.get(code, [])[-3:]]
        improvement = net[-1]-mean(net[:-1]) if len(net) == 3 and None not in net else None
        if (median5 is not None and stat["return_5"] < median5 and stat["range_position"] <= .55
                and (row.get("vol_ratio") or 0) >= 1 and improvement is not None and improvement > 0
                and corr is not None and corr >= .25):
            result["低位补涨观察"].append({**common, **weighted({"近20日低位": 100*(1-stat["range_position"]),
                "相对样本涨幅差": clip(50+(median5-stat["return_5"])*5), "量比": clip(row["vol_ratio"]*35),
                "主力净额边际改善": clip(50+improvement/1e6)}, (30, 20, 25, 25))})
    return {role: sorted([x for x in candidates if x["score"] is not None],
                         key=lambda x: x["score"], reverse=True)[:3] for role, candidates in result.items()}


def range_position(bars):
    low = min(b["low"] for b in bars[-20:])
    high = max(b["high"] for b in bars[-20:])
    return (bars[-1]["close"]-low)/(high-low) if high > low else .5


def market_mood(breadth, up, down, broken, yesterday):
    gainers, losers = breadth.get("up"), breadth.get("down")
    share = gainers / (gainers+losers)*100 if None not in (gainers, losers) and gainers+losers else None
    n_up, n_down, n_broken = (len(x) if x is not None else None for x in (up, down, broken))
    seal = n_up / (n_up+n_broken) * 100 if None not in (n_up, n_broken) and n_up+n_broken else None
    ratio = n_up / (n_up+n_down)*100 if None not in (n_up, n_down) and n_up+n_down else None
    carry = [r["pct"] for r in yesterday or [] if r["pct"] is not None]
    premium = round(mean(carry), 2) if carry else None
    return {**breadth, "limit_up": n_up, "limit_down": n_down, "broken": n_broken,
            "break_rate": round(100-seal, 1) if seal is not None else None,
            "streak": max((r["streak"] for r in up or [] if r.get("streak") is not None), default=None),
            "premium": premium, "strong": sum(r["streak"] >= 2 for r in up if r.get("streak") is not None) if up is not None else None,
            "heat": weighted({"上涨广度": share, "涨停占涨跌停比例": ratio, "封板率": seal,
                              "昨涨停今平均涨幅": clip(50+premium*10) if premium is not None else None},
                             (45, 25, 20, 10), minimum=.65)}


def elapsed_minutes(a, b):
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()/60


def quote_signals(previous, current, pct, turnover, rapid, volume, is_tail):
    rules = []
    day_move, rate = current.get("pct"), current.get("turnover")
    if day_move is not None and abs(day_move) >= pct:
        rules.append(("日内涨幅" if day_move > 0 else "日内跌幅", day_move, pct, "%"))
    if rate is not None and rate >= turnover:
        rules.append(("换手率", rate, turnover, "%"))
    ratio = current.get("vol_ratio")
    if ratio is not None and ratio >= volume:
        rules.append(("量比放大", ratio, volume, "倍"))
    if previous and 0 < elapsed_minutes(previous["market_time"], current["market_time"]) <= 3:
        move = round((current["price"] / previous["price"] - 1)*100, 2)
        if abs(move) >= rapid:
            rules.append(("短时拉升" if move > 0 else "短时下挫", move, rapid, "%"))
        if is_tail and abs(move) >= rapid*.65:
            rules.append(("尾盘价格异动", move, round(rapid*.65, 2), "%"))
    return rules
