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
    # 本次升级计分口径与普通员工相关；月报条目仍按原权限过滤。
    # 2026.10.05.3 之后的版本不面向CM，普通员工看到的最新一版是 2026.10.05.2。
    assert cm[0]["version"] == "2026.10.05.2" and cm[0]["current"] is False
    assert "声明升级按考勤类别匹配并修正实际扣分" in cm_text
    assert "手机底部栏按角色放常用功能" in cm_text
    assert "新增HR月报制作与PPTX导出" not in cm_text
    assert "新增HR月报制作与PPTX导出" in admin_text
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


def test_release_announcement_shows_current_items_and_is_read_once() -> None:
    with TestClient(app) as client:
        # The current release is the rotation test entry: TR, GSM and the highest admin see it; CM does not.
        login(client, "CMTEST01")
        assert client.get("/api/changelog/announcement").json() == {"release": None, "read": True}
        client.post("/api/logout")
        login(client, "TRTEST01")
        tr = client.get("/api/changelog/announcement").json()
        assert [item["summary"] for item in tr["release"]["items"]] == ["新增「轮岗（测试）」入口（测试功能）"]
        client.post("/api/logout")
        login(client, "HR01", "HR123")
        hr = client.get("/api/changelog/announcement").json()
        assert hr["release"]["version"] == APP_VERSION
        assert [item["summary"] for item in hr["release"]["items"]] == ["新增「轮岗（测试）」入口（测试功能）"]
        client.post("/api/logout")
        login(client, "HR01", "HR123")
        first = client.get("/api/changelog/announcement").json()
        history = client.get("/api/changelog").json()["releases"]
        assert first["read"] is False
        assert first["release"]["version"] == APP_VERSION
        assert "content_version" not in first["release"]
        assert first["release"]["items"] == history[0]["items"]
        assert client.post("/api/changelog/announcement/read", json={"version": APP_VERSION}).status_code == 200
        assert client.get("/api/changelog/announcement").json()["read"] is True


def test_current_monthly_report_fix_requires_role_and_permission() -> None:
    item = next(release for release in RELEASES if release["version"] == "2026.09.29.1")["items"][0]
    for role in ("GSM", "AM", "OM", "SYSTEM_ADMIN"):
        assert item_visible(item, role, {"HR_MONTHLY_REPORT"})
        assert not item_visible(item, role, set())
    assert not item_visible(item, "CM", {"HR_MONTHLY_REPORT"})


def test_acting_duty_notes_follow_base_and_duty_roles() -> None:
    release = next(release for release in RELEASES if release["version"] == "2026.10.04.1")
    items = {item["summary"]: item for item in release["items"]}
    # A CM acting as TA主管 sees notes for either identity.
    assert item_visible(items["代理TA主管期间保留CM/TR身份"], {"CM", "TA_SUPERVISOR"}, set())
    assert item_visible(items["GSM、TA GSM可为主管加分扣分"], {"SUPERVISOR", "TA_GSM"}, set())
    assert not item_visible(items["线上申诉取消，改走线下渠道"], "GSM", set())


def test_current_sick_leave_notes_require_import_permission() -> None:
    sick_leave_release = next(release for release in RELEASES if release["version"] == "2026.10.02.1")
    for item in sick_leave_release["items"][:2]:
        for role in ("GSM", "HR_CIRCLE", "SYSTEM_ADMIN"):
            assert item_visible(item, role, {"SICK_LEAVE_IMPORT"})
            assert not item_visible(item, role, set())
        assert not item_visible(item, "CM", {"SICK_LEAVE_IMPORT"})


def test_release_announcement_is_role_filtered_and_read_once_per_account() -> None:
    # 复用旧公告（announcement_items_from）的行为：临时给当前版本加上复用设置来验证。
    # 当前版本只面向HR，借用上一版的条目让各角色都能看到当前版本。
    current = RELEASES[0]
    assert "announcement_items_from" not in current
    own_items = current["items"]
    current["items"] = next(release["items"] for release in RELEASES if release["version"] == "2026.10.05.2")
    current["announcement_items_from"] = "2026.09.25.1"
    try:
        _check_reused_announcement()
    finally:
        current.pop("announcement_items_from", None)
        current["items"] = own_items


def _check_reused_announcement() -> None:
    with TestClient(app) as client:
        # 复用的旧版本对该角色没有可见条目时，不弹公告，也不能标记已读。
        RELEASES[0]["announcement_items_from"] = "2026.09.27.2"
        login(client, "CMTEST01")
        assert client.get("/api/changelog/announcement").json() == {"release": None, "read": True}
        assert client.post("/api/changelog/announcement/read", json={"version": APP_VERSION}).status_code == 404
        client.post("/api/logout")
        RELEASES[0]["announcement_items_from"] = "2026.09.25.1"
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
