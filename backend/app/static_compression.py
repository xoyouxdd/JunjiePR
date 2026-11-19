"""Gzip for static text assets only.

CSS/JS/HTML/JSON/SVG under /static compress 4-6x. Everything else passes through
untouched: video and attachment responses rely on byte ranges, exports are already
compressed, and the rotation event stream must not be buffered.
"""
from __future__ import annotations

import re

from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TEXT_ASSET = re.compile(r"/static/.+\.(?:css|js|mjs|html|json|svg|map)$")


def accepts_gzip(headers: Headers) -> bool:
    """An explicit gzip offer, even at quality zero, overrides a wildcard."""
    qualities: dict[str, float] = {}
    for field in headers.getlist("accept-encoding"):
        for offer in field.split(","):
            coding, *parameters = offer.split(";")
            coding = coding.strip().lower()
            if coding not in {"gzip", "*"}:
                continue
            quality = 1.0
            for parameter in parameters:
                name, _, value = parameter.partition("=")
                if name.strip().lower() == "q":
                    try:
                        quality = float(value.strip())
                    except ValueError:
                        quality = 0.0
                    break
            qualities[coding] = quality
    return 0 < qualities.get("gzip", qualities.get("*", 0.0)) <= 1


class StaticTextGZipMiddleware:
    def __init__(self, app: ASGIApp, minimum_size: int = 1024) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not TEXT_ASSET.search(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        request = Headers(scope=scope)
        use_gzip = scope["method"] == "GET" and "range" not in request and accepts_gzip(request)

        async def send_static_headers(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] in {200, 206, 304}:
                headers = MutableHeaders(scope=message)
                vary = {name.strip().lower() for name in headers.get("vary", "").split(",")}
                if not vary.intersection({"*", "accept-encoding"}):
                    headers.add_vary_header("Accept-Encoding")
                # The file's strong validator describes its original bytes.
                # A weak tag still lets StaticFiles revalidate the same content.
                etag = headers.get("etag")
                gzip_representation = headers.get("content-encoding") == "gzip" or (
                    use_gzip and message["status"] == 304
                )
                if gzip_representation and etag and not etag.startswith("W/"):
                    headers["etag"] = f"W/{etag}"
            await send(message)

        if not use_gzip:
            await self.app(scope, receive, send_static_headers)
            return

        # Starlette's middleware checks for a literal "gzip" substring. Pass
        # the negotiated coding so case and wildcard offers work consistently.
        negotiated = [(name, value) for name, value in request.raw if name != b"accept-encoding"]
        negotiated.append((b"accept-encoding", b"gzip"))
        await self.gzip({**scope, "headers": negotiated}, receive, send_static_headers)
