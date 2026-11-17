"""Validate filters and apply them to a complete, mode-specific snapshot."""
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.adapters.live import INDICES, symbol

Metric = Literal["pct", "price", "amount", "turnover", "vol_ratio", "float_cap", "main_net"]
RISK_NAME = re.compile(r"^(?:\*?ST|S\*?ST|退市)|退$", re.IGNORECASE)


class Range(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    field: Metric
    minimum: float | None = Field(None, ge=-1e15, le=1e15)
    maximum: float | None = Field(None, ge=-1e15, le=1e15)

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.minimum is None and self.maximum is None:
            raise ValueError("至少填写一个边界")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("筛选下限不能高于上限")
        return self

    def matches(self, value):
        return value is not None and (self.minimum is None or value >= self.minimum) and (
            self.maximum is None or value <= self.maximum
        )


class ScreenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["market", "watch", "board"] = "market"
    exchange: Literal["all", "sh", "sz", "bj"] = "all"
    board: str | None = Field(None, pattern=r"^BK[0-9]{4}$")
    codes: list[Annotated[str, Field(max_length=12)]] = Field(default_factory=list, max_length=60)
    exclude_risk: bool = True
    ranges: list[Range] = Field(default_factory=list, max_length=7)
    sort_by: Metric = "pct"
    direction: Literal["desc", "asc"] = "desc"
    limit: int = Field(50, ge=1, le=100)

    @model_validator(mode="after")
    def validate_scope(self):
        if (self.scope == "board") != (self.board is not None):
            raise ValueError("板块成分范围须提供板块代码")
        if self.scope != "watch" and self.codes:
            raise ValueError("股票代码仅用于自选范围")
        if len({item.field for item in self.ranges}) != len(self.ranges):
            raise ValueError("同一指标只能设置一组边界")
        self.codes = list(dict.fromkeys(symbol(code) for code in self.codes))
        return self


def market_breadth(rows):
    changes = [r["pct"] for r in rows if r["pct"] is not None]
    if not changes:
        return None
    return {"up": sum(x > 0 for x in changes), "down": sum(x < 0 for x in changes),
            "flat": sum(x == 0 for x in changes), "covered": len(changes), "total": len(rows),
            "amount": sum(r["amount"] for r in rows if r["amount"] is not None)}


def screen(rows, query, market_time):
    watch = set(query.codes)
    snapshot_day = market_time[:10] if market_time else None
    candidates = [r for r in rows if (query.scope != "watch" or r["code"] in watch)
                  and (query.exchange == "all" or r["code"].startswith(query.exchange)) and r["code"] not in INDICES]
    eligible, matches = [], []
    missing = 0
    for row in candidates:
        name, price, pct = row.get("name"), row.get("price"), row.get("pct")
        if not name or price is None or price <= 0 or pct is None:
            continue
        if snapshot_day and row.get("market_time") and row["market_time"][:10] < snapshot_day:
            continue
        if query.exclude_risk and RISK_NAME.search(name.strip()):
            continue
        eligible.append(row)
        if any(row.get(item.field) is None for item in query.ranges):
            missing += 1
            continue
        if all(item.matches(row[item.field]) for item in query.ranges):
            matches.append(row)
    order = -1 if query.direction == "desc" else 1
    matches.sort(key=lambda r: (r.get(query.sort_by) is None, order * (r.get(query.sort_by) or 0), r["code"]))
    return {"items": matches[:query.limit], "universe": len(rows), "scanned": len(candidates),
            "eligible": len(eligible), "missing": missing, "matched": len(matches), "scope": query.scope}
