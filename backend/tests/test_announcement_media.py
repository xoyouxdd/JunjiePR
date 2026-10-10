from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile

import pytest
from PIL import Image
from fastapi.testclient import TestClient

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-announcement-media-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402
from app.v2_database import SessionLocal, FILE_DIR  # noqa: E402
from app.v2_models import AnnouncementMedia, StoredFile  # noqa: E402
from app.announcement_posters import ILLUSTRATED_TEMPLATES, TEMPLATE_DIR, render_poster  # noqa: E402


def login(client, account, password="1234"):
    result = client.post("/api/login", json={"employee_no": account, "password": password})
    assert result.status_code == 200, result.text


def picture(color="blue"):
    stream = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.parametrize("layout,dimensions", [("portrait", (1080, 1440)), ("landscape", (1280, 720))])
@pytest.mark.parametrize("template", ILLUSTRATED_TEMPLATES, ids=lambda x: x["id"])
def test_illustrated_library_composes_both_sizes(template, layout, dimensions):
    assert (TEMPLATE_DIR / (template["id"]+".png")).is_file()
    category = template["category"] if template["category"] != "通用" else "安全 & 合规"
    raw = render_poster(title="伙伴协作，做好每一天", summary="请查阅完整公告，并按要求完成确认。",
                        category=category, layout=layout, template_id=template["id"],
                        scope="三个景点圈", effective_date="2026-10-09")
    with Image.open(io.BytesIO(raw)) as result:
        assert result.size == dimensions
        result.load()


def test_template_catalog_and_unknown_id_rejection():
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        catalog = client.get("/api/announcement-media/options").json()["templates"]
        assert len(catalog) == 8
        for template in catalog:
            response = client.get(template["preview_url"])
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/png"
        result = client.post("/api/announcement-media/poster", json={
            "title": "公告", "summary": "重点", "category": "安全 & 合规", "template_id": "../../private"})
        assert result.status_code == 400
        assert "有效的插画模板" in result.json()["detail"]


def upload(client, key, raw=None, title="测试活动配图"):
    return client.post("/api/announcement-media/images",
                       data={"title": title, "source": "external_ai", "request_key": key,
                             "alt_text": "员工活动插画"},
                       files={"image": ("example.png", raw if raw is not None else picture(), "image/png")})


@pytest.mark.parametrize("account", ["GSMTEST01", "TAGSMTEST01", "AMTEST01"])
def test_publisher_roles_can_make_posters_without_online_generation(account):
    with TestClient(app) as client:
        login(client, account)
        options = client.get("/api/announcement-media/options")
        assert options.status_code == 200
        assert options.json()["online_generation_enabled"] is False
        result = client.post("/api/announcement-media/poster", json={
            "title": "员工活动", "summary": "伙伴协作，轻松愉快", "category": "HR & 活动"})
        assert result.status_code == 200
        assert result.headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(result.content)) as poster:
            assert poster.size == (1080, 1440)


@pytest.mark.parametrize("account,password", [
    ("CMTEST01", "1234"), ("SUPTEST01", "1234"), ("TATEST01", "1234"),
    ("OMTEST01", "1234"), ("HR01", "HR123")])
def test_nonpublishers_cannot_prepare_or_upload(account, password):
    with TestClient(app) as client:
        login(client, account, password)
        assert client.get("/api/announcement-media/options").status_code == 403
        assert client.get("/api/announcement-media/images").status_code == 403
        assert upload(client, "forbidden-" + account).status_code == 403
        assert client.post("/api/announcement-media/poster", json={
            "title": "通知", "summary": "重点", "category": "安全 & 合规"}).status_code == 403


def test_validated_upload_is_private_and_survives_new_session():
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        first = upload(client, "media-valid-001")
        assert first.status_code == 200, first.text
        item = first.json()["item"]
        assert (item["width"], item["height"]) == (64, 48)
        assert client.get(item["preview_url"]).content == picture()
        with SessionLocal() as db:
            media = db.get(AnnouncementMedia, item["id"])
            file_id = media.file_id
            assert (FILE_DIR / db.get(StoredFile, file_id).storage_key).is_file()
        login(client, "AMTEST01")
        assert client.get(item["preview_url"]).status_code == 404
        assert client.get(f"/api/files/{file_id}").status_code == 404
        assert item["id"] not in [x["id"] for x in client.get("/api/announcement-media/images").json()["items"]]
        login(client, "HR01", "HR123")
        assert client.get(f"/api/files/{file_id}").status_code == 404
        login(client, "GSMTEST01")
        assert item["id"] in [x["id"] for x in client.get("/api/announcement-media/images").json()["items"]]


def test_duplicate_replay_and_changed_content_do_not_leave_extra_files():
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        first = upload(client, "media-retry-001")
        assert first.status_code == 200, first.text
        files_before = set(FILE_DIR.iterdir())
        again = upload(client, "media-retry-001")
        assert again.status_code == 200
        assert again.json()["duplicate"] is True
        assert first.json()["item"]["id"] == again.json()["item"]["id"]
        conflict = upload(client, "media-retry-001", raw=picture("red"))
        assert conflict.status_code == 409
        assert set(FILE_DIR.iterdir()) == files_before


@pytest.mark.parametrize("raw", [b"not-an-image", b"\x89PNG\r\n\x1a\ninvalid-data", b""])
def test_corrupt_upload_does_not_create_rows_or_files(raw):
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        files_before = set(FILE_DIR.iterdir())
        with SessionLocal() as db:
            count_before = db.query(AnnouncementMedia).count()
        response = upload(client, "corrupt-test-001", raw=raw)
        assert response.status_code == 400, response.text
        with SessionLocal() as db:
            assert db.query(AnnouncementMedia).count() == count_before
        assert set(FILE_DIR.iterdir()) == files_before


def test_size_limit_is_enforced_without_leftover_file():
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        files_before = set(FILE_DIR.iterdir())
        result = upload(client, "large-file-001", raw=b"x" * (10 * 1024 * 1024 + 1))
        assert result.status_code == 400
        assert "10MB" in result.json()["detail"]
        assert set(FILE_DIR.iterdir()) == files_before


def test_poster_rejects_missing_and_blank_content():
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        assert client.post("/api/announcement-media/poster", json={}).status_code == 422
        assert client.post("/api/announcement-media/poster", json={
            "title": " ", "summary": " ", "category": "安全 & 合规"}).status_code == 400


@pytest.mark.parametrize("layout,style", [("portrait", "notice"), ("portrait", "event"),
    ("portrait", "guide"), ("landscape", "notice"), ("landscape", "event"), ("landscape", "guide")])
def test_layouts_can_render_and_save_as_template_material(layout, style):
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        result = client.post("/api/announcement-media/poster", json={
            "title": "十月员工活动", "summary": "伙伴协作，轻松愉快。欢迎参加本月员工活动。",
            "category": "HR & 活动", "style": style, "layout": layout,
            "scope": "三个景点圈", "effective_date": "2026-10-09"})
        assert result.status_code == 200, result.text[:200]
        with Image.open(io.BytesIO(result.content)) as decoded:
            assert decoded.size == ((1080, 1440) if layout == "portrait" else (1280, 720))
        saved = client.post("/api/announcement-media/images", data={
            "title": "十月员工活动", "source": "template", "request_key": f"poster-{layout}-{style}"},
            files={"image": ("poster.png", result.content, "image/png")})
        assert saved.status_code == 200
        assert saved.json()["item"]["source"] == "template"


def test_unavailable_font_is_reported_without_returning_broken_image(monkeypatch):
    monkeypatch.setenv("ANNOUNCEMENT_POSTER_FONT", str(TEST_DATA_DIR / "missing-font.ttf"))
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        result = client.post("/api/announcement-media/poster", json={
            "title": "标题", "summary": "内容", "category": "安全 & 合规"})
        assert result.status_code == 400
        assert "字体" in result.json()["detail"]


def test_overflowing_content_is_rejected_instead_of_clipped():
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        result = client.post("/api/announcement-media/poster", json={
            "title": "标题", "summary": "\n".join(["重点"] * 50), "category": "安全 & 合规",
            "layout": "landscape"})
        assert result.status_code == 400
        assert "放不下" in result.json()["detail"]
