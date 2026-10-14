from frontend_source import read_frontend_source
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_member_home_groups_own_history_in_existing_month_selector() -> None:
    script = read_frontend_source()

    assert "function memberHomeMonths()" in script
    assert "return [0,1,2].map(offset=>" in script
    assert "id=\"homeMonthSelect\"" in script
    assert "api('/api/dashboard?month='+encodeURIComponent(selectedMonth))" in script
    assert "d.query_months" in script
    assert '<optgroup label=' in script
    assert 'homeQueryRetry' in script
    assert 'rank.month===selectedMonth' in script


def test_historical_member_home_is_read_only_in_the_interface() -> None:
    script = read_frontend_source()

    assert "home-read-only" in script
    assert "recognitionCard(r,i,readOnly=false)" in script
    assert "${readOnly?'':`<button class=\"withdraw-icon\"" in script
    assert "const homeReadOnly=isHistoricalMonth||Boolean(d.month_closed)" in script
    assert "recognitionCard(r,i,homeReadOnly)" in script


def test_member_home_month_styles_remain_responsive() -> None:
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")

    for marker in (".home-info-heading", ".home-month-control", ".home-read-only"):
        assert marker in css
