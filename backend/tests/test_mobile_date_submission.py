from frontend_source import read_frontend_source
from pathlib import Path




def test_mobile_date_submission_explicitly_normalizes_and_sets_required_dates():
    source = read_frontend_source()

    assert "function normalizeSubmitDate(raw)" in source
    assert "function bindDateSubmissionFallbacks(root=document)" in source
    assert "function requireSubmittedDate(data,input,label)" in source
    assert "form.querySelectorAll('input[type=\"date\"][name]')" in source
    assert "data.set(input.name,value)" in source
    assert "dataset.submitDateCleared" in source
    assert "if(input?.dataset.submitDateCleared==='1')return ''" in source


def test_all_business_date_write_forms_use_the_mobile_date_submission_guard():
    source = read_frontend_source()

    assert "requireSubmittedDate(data,recognitionDate,'认可日期')" in source
    assert "requireSubmittedDate(data,pocForm.querySelector('[name=recognition_date]'),'认可日期')" in source
    assert "requireSubmittedDate(data,occurredOn,'事件日期')" in source
    assert "bindDateSubmissionFallbacks(app);" in source
