"""Gzip for static text assets only.

CSS/JS/HTML/JSON/SVG under /static compress 4-6x. Everything else passes through
untouched: video and attachment responses rely on byte ranges, exports are already
compressed, and the rotation event stream must not be buffered.
"""
from __future__ import annotations

import re

from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

TEXT_ASSET = re.compile(r"/static/.+\.(?:css|js|mjs|html|json|svg|map)$")


class StaticTextGZipMiddleware:
    def __init__(self, app: ASGIApp, minimum_size: int = 1024) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and TEXT_ASSET.search(scope.get("path", "")):
            await self.gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)
