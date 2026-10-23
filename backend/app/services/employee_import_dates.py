"""Normalize only dates supplied by the employee Excel import."""
from datetime import date, datetime
import re


def normalize_import_date(value) -> str:
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return ''
    text = re.sub(r'\s*到\s*期\s*$', '', text).strip()
    # Slash dates with the year last always use month/day/year, never guess D/M/Y.
    year_first = re.fullmatch(r'(\d{4})([-/.])(\d{1,2})\2(\d{1,2})', text)
    month_first = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})', text)
    chinese = re.fullmatch(r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日', text)
    try:
        if year_first:
            year, _, month, day = year_first.groups()
        elif month_first:
            month, day, year = month_first.groups()
        elif chinese:
            year, month, day = chinese.groups()
        else:
            raise ValueError()
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError as exc:
        raise ValueError('日期无效；支持Excel日期、2026-12-04、2026/12/4、2026.12.4、2026年12月4日或12/4/2026，可带“到期”；斜杠年在后的格式按月/日/年') from exc
