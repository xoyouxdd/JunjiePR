"""Calendar parsing and ranges shared by business entry points."""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from fastapi import HTTPException


def parse_iso_date(value: str, label: str = "日期") -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"{label}格式必须为YYYY-MM-DD") from exc


def parse_score_month(value: str) -> str:
    month = str(value or "").strip()
    if len(month) != 7:
        raise HTTPException(400, "月份格式必须为YYYY-MM")
    try:
        parsed = date.fromisoformat(f"{month}-01")
    except ValueError as exc:
        raise HTTPException(400, "月份格式必须为YYYY-MM") from exc
    normalized = parsed.strftime("%Y-%m")
    if month != normalized:
        raise HTTPException(400, "月份格式必须为YYYY-MM")
    return normalized


def subtract_calendar_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def add_calendar_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def months_between(start: date, end: date) -> list[str]:
    result = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        result.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return result
