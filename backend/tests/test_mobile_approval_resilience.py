from pathlib import Path


def test_absence_dates_are_validated_before_submission_and_explicitly_sent() -> None:
    script = (Path(__file__).parents[1] / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert 'id="sickDateError"' in script
    assert "const validateDates=" in script
    assert "start.setCustomValidity(message)" in script
    assert "data.set('leave_start_date',start.value)" in script
    assert "data.set('leave_end_date',end.value)" in script


def test_portrait_preview_prioritizes_wide_material_readability() -> None:
    script = (Path(__file__).parents[1] / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    css = (Path(__file__).parents[1] / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert "image.naturalWidth>image.naturalHeight*1.3" in script
    assert ".image-preview-stage img.is-landscape" in css
    assert "usesNativeMobilePdfViewer" in script
