"""Native-module cache version and precise import-map CSP authorization."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

import pytest

from app.main import render_static_page, _STATIC_PAGE_CACHE
from app.security import CONTENT_SECURITY_POLICY
from app.version import STATIC_CACHE_VERSION


@pytest.fixture
def page_file():
    directory = Path(__file__).resolve().parents[2] / "output" / "refactor-validation" / "phase4" / ("csp-" + uuid4().hex)
    directory.mkdir(parents=True)
    page = directory / "page.html"
    yield page
    _STATIC_PAGE_CACHE.pop(str(page), None)
    assert directory.resolve().is_relative_to(directory.parents[3].resolve())
    shutil.rmtree(directory)


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_hashes_the_versioned_normalized_import_map_only(page_file, newline):
    content = newline + ' {"imports":{"./module.js":"./module.js?v=__STATIC_CACHE_VERSION__"}}' + newline
    page_file.write_bytes(('<script type="importmap">' + content + '</script><script>alert("blocked")</script>').encode())
    response = render_static_page(page_file)
    parsed = content.replace("__STATIC_CACHE_VERSION__", STATIC_CACHE_VERSION).replace("\r\n", "\n").replace("\r", "\n")
    digest = base64.b64encode(hashlib.sha256(parsed.encode()).digest()).decode()
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY.replace("script-src 'self'", f"script-src 'self' 'sha256-{digest}'", 1)
    assert "unsafe-inline" not in response.headers["content-security-policy"]
    assert b"__STATIC_CACHE_VERSION__" not in response.body
    assert render_static_page(page_file).headers["content-security-policy"] == response.headers["content-security-policy"]


def test_other_pages_keep_global_script_policy(page_file):
    page_file.write_text('<script src="static/js/login.js?v=__STATIC_CACHE_VERSION__"></script>', encoding="utf-8")
    response = render_static_page(page_file)
    assert "content-security-policy" not in response.headers
    assert "sha256-" not in CONTENT_SECURITY_POLICY


def test_real_shell_versions_all_module_targets():
    page = Path(__file__).resolve().parents[1] / "app/static/index.html"
    response = render_static_page(page)
    content = re.search(rb'<script type="importmap">(.*?)</script>', response.body, re.DOTALL).group(1)
    imports = json.loads(content)["imports"]
    assert imports
    assert all(target.endswith("?v=" + STATIC_CACHE_VERSION) for target in imports.values())
    assert 'type="module"' in response.body.decode()
