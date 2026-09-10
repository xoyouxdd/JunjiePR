from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.datastructures import FormData

os.environ["RECOGNITION_V2_DATA_DIR"] = tempfile.mkdtemp(prefix="recognition-uc-upload-test-")

from app.main import browser_family, validation_error_response
from app.v2_database import init_db


ROOT = Path(__file__).resolve().parents[1]


def test_uc_is_classified_before_chromium_user_agents() -> None:
    assert browser_family("Mozilla/5.0 UCBrowser/16.6.8.1307 Chrome/110.0 Mobile") == "uc"
    assert browser_family("Mozilla/5.0 Chrome/128.0 Mobile Safari/537.36") == "chrome"


def test_recognition_rebuilds_file_payload_and_has_browser_compatibility_fallback() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "function readableEvidenceFile(form)" in script
    assert "function evidenceSelectionAttempted(form)" in script
    assert "function recognitionSubmissionData(form,imageRequired=true)" in script
    assert "form.querySelectorAll('[name]').forEach(control=>" in script
    assert "data.append('image',file,file.name)" in script
    assert "if(imageRequired&&!file)" in script
    assert "if(file){data.append('image',file,file.name)" in script
    assert "function recognitionImageIssueMessage(form)" in script
    assert "当前浏览器不支持本次图片上传" in script
    assert "请先选择认可图片后提交。" in script
    assert "toast(recognitionImageFailureMessage(x)||x.message,true)" in script


def test_recognition_validation_returns_auditable_confirmed_compatibility_error() -> None:
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert '"/api/recognitions"' in source
    assert '"RECOGNITION_IMAGE_VALIDATION_ERROR"' in source
    assert '"认可图片接收失败"' in source
    assert 'form.get("image_client_ready", "")' in source
    assert '"client_image_ready": client_image_ready' in source


def test_confirmed_missing_image_gets_a_clear_compatibility_message() -> None:
    init_db()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/recognitions",
            "headers": [(b"user-agent", b"Mozilla/5.0 ExampleBrowser/9.0 Mobile")],
            "client": ("127.0.0.1", 12345),
        }
    )
    request._form = FormData([("image_client_ready", "1")])
    error = RequestValidationError([{"loc": ("body", "image"), "msg": "Field required", "type": "missing"}])

    response = asyncio.run(validation_error_response(request, error))
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert payload["detail"]["code"] == "RECOGNITION_IMAGE_BROWSER_INCOMPATIBLE"
    assert "当前浏览器不支持本次图片上传" in payload["detail"]["fields"][0]["message"]


def test_unconfirmed_missing_image_keeps_the_normal_error_message() -> None:
    init_db()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/recognitions",
            "headers": [(b"user-agent", b"Mozilla/5.0 ExampleBrowser/9.0 Mobile")],
            "client": ("127.0.0.1", 12346),
        }
    )
    request._form = FormData()
    error = RequestValidationError([{"loc": ("body", "image"), "msg": "Field required", "type": "missing"}])

    response = asyncio.run(validation_error_response(request, error))
    payload = json.loads(response.body)

    assert payload["detail"]["code"] == "RECOGNITION_IMAGE_VALIDATION_ERROR"
    assert payload["detail"]["fields"][0]["message"] == "认可图片未成功上传，请重新选择图片后提交。"
