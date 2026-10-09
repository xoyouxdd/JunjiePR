"""Aligned monthly trend series built from the canonical monthly statistics."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.monthly_statistics import statistics_payload

if TYPE_CHECKING:
    from app.v2_auth import V2User

TREND_DEFAULT_MONTHS = 6
TREND_MAX_MONTHS = 12
TREND_SCORE_FIELDS = ("recognition_score", "attendance_score", "deduction_score", "total_score")

def trend_month_keys(end_month: str, count: int) -> list[str]:
    """Return `count` YYYY-MM keys ending at end_month, oldest first."""
    try:
        anchor = date.fromisoformat(f"{end_month}-01")
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "月份格式应为YYYY-MM") from exc
    keys: list[str] = []
    year, index = anchor.year, anchor.month
    for _ in range(count):
        keys.append(f"{year:04d}-{index:02d}")
        index -= 1
        if index == 0:
            year, index = year - 1, 12
    keys.reverse()
    return keys



def statistics_trend_payload(db: Session, end_month: str, months: int, attraction_id: int | None, title: str | None, user: V2User | None, *, payload_loader: Callable = statistics_payload) -> dict:
    """Month-by-month scores from statistics_payload, excluding unassigned circles.

    Scope, organization basis (month snapshot first) and LOA exclusion all come
    from that one function; the trend never re-implements the scoring rules.
    """
    # An explicit out-of-range value is clamped, not silently replaced by the default.
    span = min(max(int(TREND_DEFAULT_MONTHS if months is None else months), 1), TREND_MAX_MONTHS)
    keys = trend_month_keys(end_month, span)
    overall: list[dict] = []
    circles: dict[object, dict] = {}
    for key in keys:
        payload = payload_loader(db, key, attraction_id, None, title, user, include_hierarchy=False)
        assigned_nodes = [node for node in payload["by_attraction"] if node["attraction_id"]]
        summary = {
            "employee_count": sum(node["employee_count"] for node in assigned_nodes),
            **{field: round(sum(float(node[field] or 0) for node in assigned_nodes), 2)
               for field in TREND_SCORE_FIELDS},
        }
        overall.append({"month": key, "employee_count": summary["employee_count"],
                        **{field: summary[field] for field in TREND_SCORE_FIELDS}})
        for node in assigned_nodes:
            circle = circles.setdefault(node["attraction_id"], {
                "attraction_id": node["attraction_id"],
                "attraction_name": node["attraction_name"],
                "points": {},
            })
            circle["points"][key] = {"month": key, "employee_count": node["employee_count"],
                                     **{field: node[field] for field in TREND_SCORE_FIELDS}}
    blank = {"employee_count": 0, **{field: 0.0 for field in TREND_SCORE_FIELDS}}
    series = [
        {
            "attraction_id": circle["attraction_id"],
            "attraction_name": circle["attraction_name"],
            "series": [circle["points"].get(key, {"month": key, **blank}) for key in keys],
        }
        for circle in circles.values()
    ]
    series.sort(key=lambda item: str(item["attraction_name"]))
    return {
        "months": keys,
        "end_month": keys[-1],
        "overall": overall,
        "by_attraction": series,
        "filters": {"attraction_id": attraction_id or "", "title": (title or "").strip().upper(), "months": span},
        "max_months": TREND_MAX_MONTHS,
    }
