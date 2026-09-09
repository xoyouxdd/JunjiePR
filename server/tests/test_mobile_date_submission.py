from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js"


def test_mobile_date_submission_explicitly_normalizes_and_sets_required_dates():
    source = APP_JS.read_text(encoding="utf-8")

    assert "function normalizeSubmitDate(raw)" in source
    assert "function bindDateSubmissionFallbacks(root=document)" in source
    assert "function requireSubmittedDate(data,input,label)" in source
    assert "form.querySelectorAll('input[type=\"date\"][name]')" in source
    assert "data.set(input.name,value)" in source


def test_all_business_date_write_forms_use_the_mobile_date_submission_guard():
    source = APP_JS.read_text(encoding="utf-8")

    assert "requireSubmittedDate(data,recognitionDate,'认可日期')" in source
    assert "requireSubmittedDate(data,pocForm.querySelector('[name=recognition_date]'),'认可日期')" in source
    assert "requireSubmittedDate(data,occurredOn,'事件日期')" in source
    assert "bindDateSubmissionFallbacks(app);" in source
