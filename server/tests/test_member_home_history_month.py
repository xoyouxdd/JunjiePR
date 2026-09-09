from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_member_home_offers_current_and_previous_two_months_only() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "function memberHomeMonths()" in script
    assert "return [0,1,2].map(offset=>" in script
    assert "id=\"homeMonthSelect\"" in script
    assert "api('/api/dashboard?month='+encodeURIComponent(selectedMonth))" in script


def test_historical_member_home_is_read_only_in_the_interface() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "home-read-only" in script
    assert "recognitionCard(r,i,readOnly=false)" in script
    assert "${readOnly?'':`<button class=\"withdraw-icon\"" in script
    assert "recognitionCard(r,i,isHistoricalMonth)" in script


def test_member_home_month_styles_remain_responsive() -> None:
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")

    for marker in (".home-info-heading", ".home-month-control", ".home-read-only"):
        assert marker in css
