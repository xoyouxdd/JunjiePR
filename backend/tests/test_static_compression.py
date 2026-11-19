"""Exercise static gzip negotiation and caching without starting application workers."""
from pathlib import Path

import pytest
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from app.main import app
from app.static_compression import StaticTextGZipMiddleware


STYLE_URL = "/static/css/style.css"
STYLE_SOURCE = Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "style.css"


@pytest.mark.parametrize("encoding", ["identity", "gzip;q=0, identity", "gzip;q=0, *;q=1"])
def test_unacceptable_gzip_returns_original_static_file(encoding):
    response = TestClient(app).get(STYLE_URL, headers={"Accept-Encoding": encoding})

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.headers["vary"].lower() == "accept-encoding"
    assert response.content == STYLE_SOURCE.read_bytes()


@pytest.mark.parametrize("encoding", ["gzip", "GZip; q=0.8, identity", "br, *;q=0.5"])
def test_static_gzip_preserves_conditional_get(encoding):
    client = TestClient(app)
    original = client.get(STYLE_URL, headers={"Accept-Encoding": "identity"})
    compressed = client.get(STYLE_URL, headers={"Accept-Encoding": encoding})

    assert compressed.status_code == 200
    assert compressed.headers["content-encoding"] == "gzip"
    assert compressed.headers["etag"] == f'W/{original.headers["etag"]}'
    assert compressed.content == original.content == STYLE_SOURCE.read_bytes()

    conditional = client.get(STYLE_URL, headers={
        "Accept-Encoding": encoding, "If-None-Match": compressed.headers["etag"],
    })
    assert conditional.status_code == 304
    assert conditional.headers["etag"] == compressed.headers["etag"]
    assert conditional.headers["vary"].lower() == "accept-encoding"


def test_range_remains_in_original_byte_representation():
    client = TestClient(app)
    original = client.get(STYLE_URL, headers={"Accept-Encoding": "identity"})
    response = client.get(STYLE_URL, headers={
        "Accept-Encoding": "gzip", "Range": "bytes=0-2047", "If-Range": original.headers["etag"],
    })

    assert response.status_code == 206
    assert "content-encoding" not in response.headers
    assert response.headers["etag"] == original.headers["etag"]
    assert response.headers["content-range"] == f"bytes 0-2047/{len(original.content)}"
    assert response.headers["content-length"] == "2048"
    assert response.content == original.content[:2048]


def test_small_and_out_of_scope_responses_remain_uncompressed():
    async def endpoint(scope, receive, send):
        response = PlainTextResponse("small" if scope["path"].endswith(".css") else "outside " * 250)
        await response(scope, receive, send)

    client = TestClient(StaticTextGZipMiddleware(endpoint))
    for path in ("/static/small.css", "/static/large.png", "/api/records.json", "/api/rotation/stream"):
        response = client.get(path, headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200
        assert "content-encoding" not in response.headers
