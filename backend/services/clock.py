from datetime import datetime
from zoneinfo import ZoneInfo

SH = ZoneInfo("Asia/Shanghai")


def now():
    return datetime.now(SH)


def phase(current, open_days):
    day = current.date().isoformat()
    hm = current.hour * 60 + current.minute
    if current.weekday() >= 5:
        return "weekend", "周末休市"
    if open_days is None:
        return "unknown", "日历待确认"
    if day not in open_days:
        return "holiday", "节假日休市"
    if hm < 555:
        return "before", "盘前"
    if hm < 565:
        return "open_auction", "开盘集合竞价"
    if hm < 570:
        return "pause", "开盘竞价结束"
    if hm < 690:
        return "morning", "上午连续竞价"
    if hm < 780:
        return "lunch", "午间休市"
    if hm < 897:
        return "afternoon", "下午连续竞价"
    if hm < 900:
        return "close_auction", "收盘集合竞价"
    return "closed", "已收盘"


ACTIVE = {"morning", "afternoon", "open_auction", "close_auction"}
