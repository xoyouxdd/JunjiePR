"""HTTP request metadata used for audit records."""
from __future__ import annotations

from fastapi import Request


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
