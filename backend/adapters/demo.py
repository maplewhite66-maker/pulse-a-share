from datetime import timedelta
import math

from backend.adapters.live import INDICES, response, symbol
from backend.services.clock import now

DEMO_STOCKS = {
  '600519': ('贵州茅台', 1546.23, -0.75, 1.9),
  '002594': ('比亚迪', 116.80, 1.86, 2.8),
  '300750': ('宁德时代', 288.42, 2.18, 2.1),
  '601138': ('工业富联', 52.81, 3.57, 3.7),
  '000938': ('紫光股份', 24.65, 4.12, 4.8),
  '000001': ('平安银行', 11.71, 0.67, 1.1),
  '688256': ('寒武纪', 984.60, 5.36, 3.0),
  '601318': ('中国平安', 51.22, -0.81, 1.0),
  '600036': ('招商银行', 40.84, 0.42, 0.7),
  '000858': ('五粮液', 113.61, -1.28, 1.3),
  '002230': ('科大讯飞', 53.92, 3.27, 5.8),
  '603019': ('中科曙光', 74.88, 2.54, 3.9),
}
DEMO_SECTORS = [
  ('算力租赁',5.92,38.6,'中科曙光'),('液冷服务器',5.27,34.1,'工业富联'),
  ('存储芯片',4.72,29.8,'紫光股份'),('人工智能',4.25,25.4,'科大讯飞'),
  ('机器人',3.81,22.8,'埃斯顿'),('半导体',3.57,18.7,'寒武纪'),
  ('铜缆高速连接',3.21,16.4,'沃尔核材'),('商业航天',2.99,13.2,'航天科技'),
  ('低空经济',2.66,11.7,'万丰奥威'),('消费电子',2.42,9.8,'立讯精密'),
  ('电网设备',2.25,8.5,'国电南瑞'),('工业母机',2.12,7.6,'华中数控'),
  ('电池',1.82,6.7,'宁德时代'),('医药商业',1.61,5.5,'九州通'),
  ('银行',0.74,4.8,'招商银行'),('券商',0.71,4.4,'中信证券'),
  ('汽车整车',0.62,3.9,'比亚迪'),('白酒',-0.82,-4.1,'贵州茅台'),
  ('光伏设备',-1.14,-6.4,'隆基绿能'),('房地产',-2.13,-11.2,'保利发展'),
]



def wrap(data):
    return response(data, "离线演示样本", None, now().isoformat(timespec="seconds"), "全部数值均为演示")


class Demo:
    async def quotes(self, codes):
        tick = int(now().timestamp() // 5)
        result = []
        for code in codes:
            if code in INDICES:
                i = list(INDICES).index(code)
                name, base, pct, ratio = INDICES[code], [3988.42,13280.17,3291.51,4784.63,1190.65][i], [0.67,1.26,2.51,.85,1.74][i], 1.2
            else:
                name, base, pct, ratio = DEMO_STOCKS.get(code[2:], ("演示标的 "+code[2:], 42.15, 1.73, 2.1))
            seed = int(code[2:]) % 17
            price = round(base*(1+.004*math.sin(tick/7+seed)), 2)
            result.append({"code": code, "name": name, "price": price,
                           "pct": round(pct+.12*math.sin(tick/4+seed), 2), "amount": (16+seed*2)*1e8,
                           "previous_close": round(price/(1+pct/100), 2), "open": round(price*.998, 2),
                           "turnover": round(1+seed*.38, 2), "amplitude": 2.4, "vol_ratio": ratio,
                           "volume": (2+seed)*1500000, "float_cap": (180+seed*19)*1e8, "market_cap": (350+seed*23)*1e8,
                           "market_time": now().isoformat(timespec="seconds"), "inactive": False})
        return wrap(result)

    async def kline(self, code, period, adjust, count):
        price = (await self.quotes([code]))["data"][0]["price"]
        seed = int(code[2:]) % 23
        bars = []
        minute = period.startswith("m") and period != "month"
        step = int(period[1:]) if minute else {"day":1,"week":7,"month":30}[period]
        current = now().replace(hour=15, minute=0, second=0, microsecond=0)
        for i in range(count):
            j = count-1-i
            if minute:
                slot = 239 - (j * step) % 240
                d = current - timedelta(days=(j*step)//240)
                t = (d.replace(hour=9, minute=30) + timedelta(minutes=slot+(90 if slot>=120 else 0))).isoformat()
            else:
                t = (current - timedelta(days=j*step)).date().isoformat()
            drift = (.036*math.sin(i/16+seed) + .009*math.cos(i/4) + i*.00042)
            close = round(price*(.93+drift), 2)
            op = round(close*(1+.006*math.sin(i*1.29+seed)), 2)
            bars.append({"t":t, "open":op, "close":close, "high":round(max(op,close)*1.0025,2),
                         "low":round(min(op,close)*.997,2), "volume":int(1200+2900*abs(math.sin(i/7+seed)))})
        return wrap({"items": bars, "adjust": adjust or "none", "volume_unit":"手"})

    async def sectors(self, kind, period, order):
        factor = {"today":1, "5d":3.2,"10d":5.3}[period]
        regions = ("广东","江苏","上海","浙江","北京","山东","安徽","福建","四川","河南","深圳","重庆","天津","湖南","湖北","江西","陕西","河北","辽宁","广西","山西","贵州","云南","海南","甘肃","青海","宁夏","内蒙古","吉林","黑龙江","新疆","西藏","苏州","杭州","南京","合肥","成都","武汉","郑州","厦门")
        extra = ("通信设备","电子元件","软件开发","互联网服务","汽车零部件","电机","风电设备","医疗器械","化学制药","生物制品",
                 "有色金属","工业金属","有色冶炼","石油化工","船舶制造","玻璃建材","物流","铁路公路","旅游酒店","工程机械")
        samples = DEMO_SECTORS + [(name, (-1)**i*(.35+i*.12), (20-i)*.85, "演示成分股") for i,name in enumerate(extra)]
        rows = []
        for i, (name, pct, net, leader) in enumerate(samples):
            drift = .35*math.sin(now().timestamp()/120+i)
            signed = abs(net+drift) * (1 if i<20 else -1)
            rows.append({"code":f"BK{1000+i}", "name":regions[i] if kind=="region" else name, "pct":round(pct+drift/5, 2),
                         "pct_today":round(pct+drift/5, 2), "main_net":round(signed*factor*1e8),
                         "main_pct":round(signed/4, 2), "amount":(510-i*9)*1e8,
                         "net_1":signed*1e8, "net_5":signed*3.2e8, "net_10":signed*5.3e8,
                         "up":30+i, "down":8+i*2, "leader":leader, "leader_code":None})
        rows.sort(key=lambda r:r["main_net"], reverse=order=="inflow")
        return wrap({"items":rows,"total":len(rows)})

    async def members(self, board):
        codes = ["sh600519","sz002594","sz300750","sh601138","sz000938","sz000001","sh688256","sh601318",
                 "sh600036","sz000858","sz002230","sh603019"]
        i = int(board[2:]) % len(codes)
        rows = (await self.quotes((codes[i:]+codes[:i])[:8]))["data"]
        for j,r in enumerate(rows):
            r["main_net"] = (6-j)*25e6
        return wrap(rows)

    async def universe(self, extra_codes=()):
        codes = tuple(dict.fromkeys([*(symbol(code) for code in DEMO_STOCKS), "bj920001", *extra_codes]))
        rows = (await self.quotes(codes))["data"]
        for row in rows:
            row["main_net"] = round(row["amount"] * row["pct"] / 100)
        return wrap(rows)

    async def idea_pool(self, session):
        return await self.universe()

    async def breadth(self):
        return wrap({"up":3286,"down":1832,"flat":184,"covered":5302,"total":5348,"amount":1.53e12})

    async def stock_flow(self, code, span):
        stamp = now().replace(hour=15, minute=0, second=0, microsecond=0)
        minute = span=="minute"
        rows = []
        for i in range(48):
            t = stamp - (timedelta(minutes=(47-i)*5) if minute else timedelta(days=47-i))
            if minute and 11*60+30 < t.hour*60+t.minute < 13*60:
                continue
            net = round((math.sin(i/7+int(code[2:])%9)+i/38)*2.7e7)
            rows.append({"t": t.isoformat() if minute else t.date().isoformat(), "main_net":net})
        return wrap(rows)

    async def pool(self, day, kind):
        n = {"up":79,"down":9,"broken":21,"yesterday":66}[kind]
        return wrap([{"code":f"sz{2000+i:06d}", "name":f"演示标的 {i+1:02}", "pct":10 if kind=="up" else -10 if kind=="down" else 1.76,
                      "streak":max(1,7-i), "amount":(i+1)*1e7, "industry":"演示板块"} for i in range(n)])

    async def news(self):
        return wrap([{"id":"sample-1","title":"演示快讯 · 盘中算力相关板块成交活跃度上升","time":now().isoformat(),"url":None},
                     {"id":"sample-2","title":"演示快讯 · 资金从早盘热点转向设备板块","time":now().isoformat(),"url":None}])

    async def announcements(self, code):
        return wrap([{"id":"sample-announcement-"+code,"title":"演示公告 · 关于召开股东会的通知",
                      "time":now().isoformat(),"url":None,"code":code}])
