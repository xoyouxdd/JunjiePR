from __future__ import annotations

import os


from fastapi import Request


# Retained as an empty compatibility export: every role now follows the same password policy.
PRIVILEGED_PASSWORD_ROLE_CODES = frozenset()
DEFAULT_TRUSTED_PROXY_HOSTS = frozenset({"127.0.0.1", "::1"})

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "frame-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _trusted_proxy_hosts() -> set[str]:
    configured = os.getenv("RECOGNITION_TRUSTED_PROXY_IPS", "")
    if not configured.strip():
        return set(DEFAULT_TRUSTED_PROXY_HOSTS)
    return {item.strip() for item in configured.split(",") if item.strip()}


def request_is_https(request: Request) -> bool:
    if request.url.scheme.lower() == "https":
        return True
    client_host = request.client.host if request.client else ""
    if client_host not in _trusted_proxy_hosts():
        return False
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    return forwarded_proto.split(",", 1)[0].strip().lower() == "https"


def request_origin_root(request: Request) -> str:
    """Return the externally visible `scheme://host[:port]` of a request.

    Forwarded headers are only honoured when the direct peer is a trusted
    proxy, mirroring request_is_https, so a non-proxy client cannot forge
    the expected origin root.
    """
    client_host = request.client.host if request.client else ""
    if client_host in _trusted_proxy_hosts():
        scheme = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
        host = request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
        if scheme and host:
            return f"{scheme}://{host}".lower()
    return f"{request.url.scheme}://{request.headers.get('host', '')}".lower()


def password_policy_error(password: str, role_code: str) -> str | None:
    if len(password) > 64:
        return "新密码长度不能超过64位"
    if len(password) < 4:
        return "新密码长度不能少于4位"
    return None
