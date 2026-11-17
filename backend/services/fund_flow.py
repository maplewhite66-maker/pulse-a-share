from datetime import datetime

MINUTES = {f"{minute//60:02}:{minute%60:02}" for minute in
           (*range(9*60+31, 11*60+31), *range(13*60+1, 15*60+1))}


def describe_flow(packet, clock, span):
    """Apply session freshness to provider data without changing its values."""
    current = datetime.fromisoformat(clock["time"])
    phase = clock["phase"]
    expected_day = clock["latest_day"]
    rows = packet["data"] or []
    stamp = rows[-1]["t"] if rows else None
    trade_day = stamp[:10] if stamp else None
    hm = stamp[11:16] if stamp else ""
    if not rows:
        state = "unavailable"
    elif packet["is_stale"] or packet["error"]:
        state = "cached_stale"
    elif not expected_day:
        state = "calendar_unknown"
    elif trade_day != expected_day:
        state = "wrong_session"
    elif span == "day":
        state = "daily"
    elif phase == "lunch":
        state = "lunch" if "11:27" <= hm <= "11:30" else "delayed"
    elif phase in {"morning", "afternoon", "close_auction"}:
        age = (current - datetime.fromisoformat(stamp)).total_seconds()
        state = "intraday" if -30 <= age <= 180 else "delayed"
    else:
        state = "closed_session" if hm == "15:00" else "incomplete_close"
    stale = state in {"unavailable", "cached_stale", "calendar_unknown", "wrong_session", "delayed", "incomplete_close"}
    complete = span == "minute" and MINUTES.issubset(row["t"][11:16] for row in rows)
    return {**packet, "is_stale": stale, "flow": {
        "state": state, "trade_day": trade_day, "expected_day": expected_day,
        "phase": phase, "point_count": len(rows), "complete": complete,
        "unit": "CNY", "cumulative": span == "minute",
        "delay_endpoint": "push2delay.eastmoney.com" in (packet.get("source_url") or ""),
    }}
