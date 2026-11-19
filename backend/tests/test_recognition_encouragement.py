from __future__ import annotations

from pathlib import Path

from app.recognition_encouragement import encouragement_message, encouragement_options


ROOT = Path(__file__).resolve().parents[1]


def test_local_encouragement_options_are_varied_and_do_not_echo_note() -> None:
    options = encouragement_options(
        record_id=81,
        recognition_type_name="礼仪",
        content="耐心引导游客入场",
        stage="submitted",
    )
    assert len(options) == 6
    assert len({item["template_id"] for item in options}) == 6
    assert all(item["message"] for item in options)
    assert all("耐心引导游客入场" not in item["message"] for item in options)
    assert all("已完成确认" not in item["message"] for item in options)


def test_confirmation_feedback_is_deterministic_and_only_claims_confirmation_then() -> None:
    kwargs = dict(record_id=82, recognition_type_name="安全", content="主动提醒安全隐患", stage="confirmed")
    first = encouragement_message(**kwargs)
    assert first == encouragement_message(**kwargs)
    assert "确认" in first
    assert "隐患" not in first
