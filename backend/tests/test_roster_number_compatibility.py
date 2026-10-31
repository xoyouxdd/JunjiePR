"""Preserve legacy roster overwrites; reject only mixed aliases for one slot."""
from io import BytesIO

import pytest
from openpyxl import Workbook
from app.rotation.roster import parse_any, parse_roster


def workbook(rows):
    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    out = BytesIO()
    wb.save(out)
    out.seek(0)
    return out


def template(rows):
    return workbook([['工号', '姓名', '日期', '班次', '备注'], *rows])


@pytest.mark.parametrize('number', ['1234567', 1234567, 1234567.0, '01234567'])
def test_same_spelling_duplicate_day_keeps_last_shift_and_name(number):
    parsed = parse_any(template([
        [number, '张三', '2026-10-31', '08:00-16:00', '早班'],
        [number, '张三 Zhang', '2026-10-31', '09:00-17:00', '修正版'],
    ]))
    assert parsed['people'] == {'1234567': {'pid': '1234567', 'name': '张三 Zhang', 'type': '', 'mark': ''}}
    assert parsed['days'] == {'2026-10-31': {'1234567': '09:00-17:00\n修正版'}}


@pytest.mark.parametrize('second_number', ['1234567', '01234567'])
def test_cross_date_name_changes_keep_both_days_and_last_name(second_number):
    parsed = parse_any(template([
        ['1234567', '张三', '2026-10-30', '08:00-16:00', ''],
        [second_number, '张三 Zhang', '2026-10-31', '09:00-17:00', ''],
    ]))
    assert parsed['people']['1234567']['name'] == '张三 Zhang'
    assert parsed['days'] == {'2026-10-30': {'1234567': '08:00-16:00'},
                              '2026-10-31': {'1234567': '09:00-17:00'}}


@pytest.mark.parametrize('numbers', [('1234567', '01234567'), ('01234567', '1234567')])
def test_mixed_aliases_on_same_day_are_still_rejected(numbers):
    with pytest.raises(ValueError, match='混用'):
        parse_any(template([[no, '张三', '2026-10-31', '08:00-16:00', ''] for no in numbers]))


def ztp(rows):
    return workbook([
        ['', '', '', '', 46000, 46001, 46002, 46003, 46004, 46005, 46006],
        ['Name', 'ID', 'Type', 'Mark', 'Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
        *rows,
    ])


@pytest.mark.parametrize('number', ['1234567', '01234567'])
def test_ztp_same_spelling_repeated_row_preserves_last_row(number):
    parsed = parse_roster(ztp([
        ['张三', number, 'CM', '旧', *(['08:00-16:00'] * 7)],
        ['张三 Zhang', number, 'TR', '新', *(['09:00-17:00'] * 7)],
    ]))
    assert parsed['people']['1234567'] == {'pid': '1234567', 'name': '张三 Zhang', 'type': 'TR', 'mark': '新'}
    assert len(parsed['days']) == 7
    assert all(day == {'1234567': '09:00-17:00'} for day in parsed['days'].values())


@pytest.mark.parametrize('numbers', [('1234567', '01234567'), ('01234567', '1234567')])
def test_ztp_mixed_alias_rows_are_still_rejected(numbers):
    with pytest.raises(ValueError, match='混用'):
        parse_roster(ztp([['张三', no, 'CM', '', *(['08:00-16:00'] * 7)] for no in numbers]))
