from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routers import v2
from app.version import APP_VERSION, STATIC_CACHE_VERSION
from app.v2_database import BASE_DIR, init_db
from app.v2_models import Employee, UserAccount, UserSession
from app.v2_crypto import token_hash
from app.v2_services import process_role_expirations, write_audit
from app.deduction_materials import start_deduction_material_worker
from app.v2_database import SessionLocal
from app.security import SECURITY_HEADERS, request_is_https, request_origin_root


app = FastAPI(title="认可签卡绩效登记系统 V2", version=APP_VERSION)

_STATIC_PAGE_CACHE: dict[str, tuple[float, bytes]] = {}


def render_static_page(path) -> HTMLResponse:
    """Serve an app-shell page with the shared static-cache version injected.

    Rendered bytes are cached per file and invalidated by mtime, so a
    deployment that rewrites the page is picked up without a restart. The
    HTML itself is never cached so clients always revalidate the shell.
    """
    stat = path.stat()
    cached = _STATIC_PAGE_CACHE.get(str(path))
    if not cached or cached[0] != stat.st_mtime:
        html = path.read_bytes().replace(b"__STATIC_CACHE_VERSION__", STATIC_CACHE_VERSION.encode("ascii"))
        _STATIC_PAGE_CACHE[str(path)] = (stat.st_mtime, html)
    else:
        html = cached[1]
    return HTMLResponse(content=html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


SICK_LEAVE_VALIDATION_MESSAGES = {
    "employee_id": "缺勤员工未成功提交，请重新选择员工后提交。",
    "leave_start_date": "开始日期未成功提交，请重新选择日期后提交。",
    "leave_end_date": "结束日期未成功提交，请重新选择日期后提交。",
    "leave_days": "缺勤天数未成功提交，请重新填写后提交。",
    "proof": "缺勤证明未成功上传，请重新选择文件后提交。",
}

RECOGNITION_VALIDATION_MESSAGES = {
    "image": "认可图片未成功上传，请重新选择图片后提交。",
}


def browser_family(user_agent: str) -> str:
    value = (user_agent or "").lower()
    if "ucbrowser" in value or "ucweb" in value:
        return "uc"
    if "baiduboxapp" in value or "baidu" in value:
        return "baidu"
    if "micromessenger" in value:
        return "wechat"
    if "safari" in value and "chrome" not in value:
        return "safari"
    if "chrome" in value:
        return "chrome"
    return "other"


@app.exception_handler(RequestValidationError)
async def validation_error_response(request: Request, exc: RequestValidationError):
    supported_paths = {
        "/api/sick-leaves": {
            "code": "SICK_LEAVE_VALIDATION_ERROR",
            "messages": SICK_LEAVE_VALIDATION_MESSAGES,
            "audit_action": "缺勤登记校验失败",
            "entity_type": "sick_leave_submission",
            "fallback": "提交内容不完整，请检查缺勤登记表单后重试。",
        },
        "/api/recognitions": {
            "code": "RECOGNITION_IMAGE_VALIDATION_ERROR",
            "messages": RECOGNITION_VALIDATION_MESSAGES,
            "audit_action": "认可图片接收失败",
            "entity_type": "recognition_submission",
            "fallback": "提交内容不完整，请检查认可登记表单后重试。",
        },
    }
    config = supported_paths.get(request.url.path)
    if request.method != "POST" or not config:
        return await request_validation_exception_handler(request, exc)
    client_image_ready = False
    if request.url.path == "/api/recognitions":
        try:
            form = await request.form()
            client_image_ready = str(form.get("image_client_ready", "")).strip() == "1"
        except Exception:
            client_image_ready = False
    fields = []
    for error in exc.errors():
        location = error.get("loc", ())
        field = str(location[-1]) if location else "unknown"
        message = config["messages"].get(field, config["fallback"])
        if request.url.path == "/api/recognitions" and field == "image" and client_image_ready:
            message = "当前浏览器不支持本次图片上传，请更换浏览器后重新选择图片；如需继续使用，请上报浏览器兼容问题。"
        fields.append({"field": field, "message": message})
    unique_fields = []
    for item in fields:
        if item["field"] not in {existing["field"] for existing in unique_fields}:
            unique_fields.append(item)
    db = SessionLocal()
    try:
        raw_token = request.cookies.get("rc_v2_session")
        operator = None
        if raw_token:
            session = db.query(UserSession).filter(UserSession.token_hash == token_hash(raw_token)).first()
            account = db.get(UserAccount, session.account_id) if session else None
            operator = db.get(Employee, account.employee_id) if account else None
        forwarded_for = request.headers.get("x-forwarded-for", "")
        ip_address = forwarded_for.split(",", 1)[0].strip() or (request.client.host if request.client else None)
        write_audit(
            db,
            operator,
            config["audit_action"],
            config["entity_type"],
            after={"http_status": 422, "fields": [item["field"] for item in unique_fields], "browser": browser_family(request.headers.get("user-agent", "")), "client_image_ready": client_image_ready},
            ip_address=ip_address,
        )
        db.commit()
    finally:
        db.close()
    response_code = config["code"]
    has_image_failure = any(item["field"] == "image" for item in unique_fields)
    if request.url.path == "/api/recognitions" and has_image_failure and client_image_ready:
        response_code = "RECOGNITION_IMAGE_BROWSER_INCOMPATIBLE"
    elif request.url.path == "/api/recognitions" and not has_image_failure:
        response_code = "RECOGNITION_VALIDATION_ERROR"
    return JSONResponse(status_code=422, content={"detail": {"code": response_code, "fields": unique_fields}})


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request_is_https(request):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


CSRF_PROTECTED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def reject_cross_site_state_changes(request: Request, call_next):
    """Defence-in-depth against CSRF on cookie-authenticated API writes.

    SameSite=Lax cookies already stop most cross-site posts; this additionally
    rejects any state-changing /api request whose Origin or Referer names a
    different site. Requests without either header pass through unchanged.
    """
    if request.url.path.startswith("/api/") and request.method in CSRF_PROTECTED_METHODS:
        supplied = request.headers.get("origin") or request.headers.get("referer")
        if supplied:
            expected = request_origin_root(request)
            parts = urlsplit(supplied)
            actual = f"{parts.scheme}://{parts.netloc}".lower()
            if actual != expected:
                return JSONResponse(status_code=403, content={"detail": "跨站请求已被拒绝"})
    return await call_next(request)


@app.on_event("startup")
def startup() -> None:
    init_db()
    start_deduction_material_worker()
    db = SessionLocal()
    try:
        process_role_expirations(db)
    finally:
        db.close()


app.include_router(v2.router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")


@app.get("/")
def root():
    return render_static_page(BASE_DIR / "app" / "static" / "index.html")


@app.get("/login")
def login_page():
    return render_static_page(BASE_DIR / "app" / "static" / "login.html")


@app.get("/health")
def health():
    return {"ok": True, "version": APP_VERSION}
