from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_visual_badge_reuses_existing_action_center_contract_only() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "data-action-center-badge" in script
    assert "refreshActionBadge" in script
    assert "syncAppBadge" in script
    assert "navigator.setAppBadge" in script
    assert "api('/api/action-center')" in script
    assert "api('/api/deduction-upgrades/pending')" in script
    assert "void refreshActionBadge();" in script
    # The interface layer must not introduce a separate business-data endpoint.
    assert "api('/api/action-badge" not in script


def test_visual_system_keeps_semantic_status_and_mobile_action_styles() -> None:
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")

    for marker in (
        "V2.23.67 visual system",
        ".nav-count-badge",
        ".status-chip",
        ".action-center-empty",
        ".action-card-side",
        "@media (max-width: 700px)",
    ):
        assert marker in css
