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


def test_changelog_filters_by_role() -> None:
    cm = visible_releases("CM", set())
    admin = visible_releases("SYSTEM_ADMIN", {"SYSTEM_ADMIN"})
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
    assert not any(release["current"] for release in cm)
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


def test_docs_indexes_list_every_docs_file() -> None:
    repo = Path(__file__).resolve().parents[2]
    docs_dir = repo / "docs"
    files = {path.name for path in docs_dir.glob("*.md") if path.name != "README.md"}
    docs_index = (docs_dir / "README.md").read_text(encoding="utf-8")
    root_index = (repo / "README.md").read_text(encoding="utf-8")
    assert [name for name in sorted(files) if name not in docs_index] == []
    assert [name for name in sorted(files) if f"docs/{name}" not in root_index] == []
