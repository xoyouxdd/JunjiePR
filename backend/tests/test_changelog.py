from __future__ import annotations

import os
import re
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-changelog-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402
from app.changelog import RELEASES, item_visible, visible_releases
from app.version import APP_VERSION, STATIC_CACHE_VERSION


def login(client: TestClient, account: str, password: str = "1234") -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def test_latest_changelog_version_matches_app_version() -> None:
    assert RELEASES[0]["version"] == APP_VERSION
    assert re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\.\d+", APP_VERSION)
    assert STATIC_CACHE_VERSION == APP_VERSION


def test_material_download_note_requires_declaration_view_permission() -> None:
    release = next(release for release in RELEASES if release["version"] == "2026.09.27.1")
    item = next(item for item in release["items"] if "声明材料" in item["summary"])
    assert item_visible(item, "SUPERVISOR", {"DECLARATION_STATS_VIEW"})
    assert not item_visible(item, "SUPERVISOR", set())
    assert not item_visible(item, "CM", {"DECLARATION_STATS_VIEW"})


def test_hr_monthly_note_matches_explicit_report_roles() -> None:
    release = next(release for release in RELEASES if release["version"] == "2026.09.27.2")
    item = next(item for item in release["items"] if "HR月报" in item["summary"])
    for role in ("GSM", "AM", "OM", "SYSTEM_ADMIN"):
        assert item_visible(item, role, {"HR_MONTHLY_REPORT"})
        assert not item_visible(item, role, set())
    for role in ("CM", "TR", "SUPERVISOR", "TA_GSM", "HR_ADMIN", "HR_CIRCLE"):
        assert not item_visible(item, role, {"HR_MONTHLY_REPORT"})


def test_previous_release_items_match_role_permissions() -> None:
    items = next(release["items"] for release in RELEASES if release["version"] == "2026.09.25.1")
    summaries = {item["summary"]: item for item in items}
    declaration = summaries["声明登记统计支持跨景点圈查看与导出"]
    assert item_visible(declaration, "SUPERVISOR", {"DECLARATION_STATS_VIEW", "DECLARATION_STATS_EXPORT"})
    assert item_visible(declaration, "SUPERVISOR", {"DECLARATION_STATS_VIEW"}) is False
    assert item_visible(declaration, "CM", {"DECLARATION_STATS_VIEW", "DECLARATION_STATS_EXPORT"}) is False
    monthly = summaries["月度缺勤文件覆盖当月旧登记"]
    assert item_visible(monthly, "HR_CIRCLE", {"SICK_LEAVE_IMPORT"})
    assert item_visible(monthly, "SUPERVISOR", {"SICK_LEAVE_IMPORT"}) is False


def test_home_screen_fix_announcement_is_visible_to_every_role() -> None:
    previous = next(release for release in RELEASES if release["version"] == "2026.09.26.1")
    item = next(item for item in previous["items"] if "快捷方式" in item["summary"])
    assert "快捷方式" in item["summary"]
    assert item_visible(item, "CM", set())
    assert item_visible(item, "SYSTEM_ADMIN", set())


def test_current_covered_sick_history_note_is_visible_to_every_role() -> None:
    release = next(release for release in RELEASES if release["version"] == "2026.09.26.2")
    item = next(item for item in release["items"] if "已覆盖病假历史" in item["summary"])
    assert item_visible(item, "CM", set())
    assert item_visible(item, "SYSTEM_ADMIN", set())


def test_changelog_filters_by_role() -> None:
    cm = visible_releases("CM", set())
    admin = visible_releases("SYSTEM_ADMIN", {"SYSTEM_ADMIN", "DECLARATION_STATS_VIEW", "HR_MONTHLY_REPORT"})
    gsm = visible_releases("GSM", {"POC_ISSUE", "DATA_EXPORT"})
    cm_text = " ".join(item["summary"] for release in cm for item in release["items"])
    admin_text = " ".join(item["summary"] for release in admin for item in release["items"])
    gsm_text = " ".join(item["summary"] for release in gsm for item in release["items"])
    assert "更新记录" in cm_text
    assert "版本号改成日期加当天第几版" in cm_text
    assert "修改密码放到导航栏" in cm_text
    assert "全局月结" not in cm_text
    assert "全局月结" in admin_text
    assert "POC" in gsm_text
    # A role with no changes in this release must not receive another role's note.
    assert cm[0]["current"] is False
    assert all(release["version"] != APP_VERSION for release in cm)
    assert admin[0]["current"] is True


def test_changelog_requires_matching_role_and_all_extra_permissions() -> None:
    item = {"audiences": ["GSM"], "permissions": ["DATA_EXPORT", "DATA_VIEW"]}
    assert item_visible(item, "GSM", {"DATA_EXPORT", "DATA_VIEW"}) is True
    assert item_visible(item, "GSM", {"DATA_EXPORT"}) is False
    assert item_visible(item, "CM", {"DATA_EXPORT", "DATA_VIEW"}) is False


def test_changelog_api_and_navigation_exist() -> None:
    with TestClient(app) as client:
        login(client, "CMTEST01")
        data = client.get("/api/changelog")
        assert data.status_code == 200, data.text
        body = data.json()
        assert body["app_version"] == APP_VERSION
        assert body["role_code"] == "CM"
        assert body["releases"]
        summaries = [item["summary"] for release in body["releases"] for item in release["items"]]
        assert any("更新记录" in row for row in summaries)
        assert all("全局月结" not in row for row in summaries)
    source = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "items.push(['changelog','更新记录']);" in source
    assert "changelog:renderChangelog" in source
    assert "async function renderChangelog" in source


def test_release_announcement_is_role_filtered_and_read_once_per_account() -> None:
    with TestClient(app) as client:
        login(client, "CMTEST01")
        assert client.get("/api/changelog/announcement").json() == {"release": None, "read": True}
        assert client.post("/api/changelog/announcement/read", json={"version": APP_VERSION}).status_code == 404
        client.post("/api/logout")
        login(client, "AMTEST01")
        first = client.get("/api/changelog/announcement")
        assert first.status_code == 200, first.text
        release = first.json()["release"]
        assert release["version"] == APP_VERSION
        assert release["content_version"] == "2026.09.25.1"
        assert first.json()["read"] is False
        history = client.get("/api/changelog").json()["releases"]
        assert release["items"] == next(row["items"] for row in history if row["version"] == release["content_version"])
        assert release["items"] != history[0]["items"]
        assert client.post("/api/changelog/announcement/read", json={"version": "wrong"}).status_code == 409
        assert client.get("/api/changelog/announcement").json()["read"] is False
        assert client.post("/api/changelog/announcement/read", json={"version": APP_VERSION}).status_code == 200
        assert client.post("/api/changelog/announcement/read", json={"version": APP_VERSION}).status_code == 200
        assert client.get("/api/changelog/announcement").json()["read"] is True
        client.post("/api/logout")
        login(client, "AMTEST01")
        assert client.get("/api/changelog/announcement").json()["read"] is True
        client.post("/api/logout")
        login(client, "GSMTEST01")
        gsm = client.get("/api/changelog/announcement").json()
        assert gsm["read"] is False
        assert gsm["release"]["version"] == APP_VERSION
        assert gsm["release"]["items"] == next(row["items"] for row in client.get("/api/changelog").json()["releases"] if row["version"] == "2026.09.25.1")


def test_docs_indexes_list_every_docs_file() -> None:
    repo = Path(__file__).resolve().parents[2]
    docs_dir = repo / "docs"
    files = {path.name for path in docs_dir.glob("*.md") if path.name != "README.md"}
    docs_index = (docs_dir / "README.md").read_text(encoding="utf-8")
    root_index = (repo / "README.md").read_text(encoding="utf-8")
    assert [name for name in sorted(files) if name not in docs_index] == []
    assert [name for name in sorted(files) if f"docs/{name}" not in root_index] == []
