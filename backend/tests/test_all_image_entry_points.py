from __future__ import annotations

from pathlib import Path

from app.routers.v2 import is_previewable_image, preview_kind
from app.v2_models import StoredFile


def test_material_preview_metadata_and_all_image_entry_points() -> None:
    active_image = StoredFile(extension=".jpg", status="active")
    inactive_image = StoredFile(extension=".png", status="deleted")
    pdf = StoredFile(extension=".pdf", status="active")
    assert is_previewable_image(active_image) is True
    assert is_previewable_image(inactive_image) is False
    assert is_previewable_image(pdf) is False
    assert preview_kind(active_image) == "image"
    assert preview_kind(pdf) == "pdf"

    script = (Path(__file__).parents[1] / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    router = (Path(__file__).parents[1] / "app" / "routers" / "v2.py").read_text(encoding="utf-8")
    assert "function openPdfPreview" in script
    assert "function usesNativeMobilePdfViewer" in script
    assert "window.location.assign(previewUrl)" in script
    assert "frame-src 'self' blob:" in (Path(__file__).parents[1] / "app" / "security.py").read_text(encoding="utf-8")
    index_html = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'v=__STATIC_CACHE_VERSION__' in index_html  # runtime injects app.version.STATIC_CACHE_VERSION
    assert "__STATIC_CACHE_VERSION__" in (Path(__file__).parents[1] / "app" / "static" / "login.html").read_text(encoding="utf-8")
    assert "缺勤证明未上传，请重新选择图片或PDF文件" in router
    assert "请先选择缺勤证明（图片或PDF）。" in script
    assert "单个文件不超过100MB" in script
    assert "最多100MB、6页" in script
    assert "单张25MB、合计100MB" in script
    assert "function validationErrorMessage" in script
    assert "缺勤证明未成功上传，请重新选择文件后提交。" in script
    assert "proofPayloadError" in script
    assert "data.set('proof',file,file.name)" in script
    assert "SICK_LEAVE_VALIDATION_ERROR" in (Path(__file__).parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    stylesheet = (Path(__file__).parents[1] / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert "@media (min-width: 761px)" in stylesheet
    assert "aspect-ratio: 210 / 297" in stylesheet
    assert "width: min(1200px, calc(100vw - 12px))" in stylesheet
    assert "height: min(calc(100vh - 76px), 1120px)" in stylesheet
    assert "password-rule-box" in stylesheet
    assert "function passwordRuleState" in script
    assert "function bindPasswordForm" in script
    assert "function passwordResetScopeHint" in script
    assert "async function renderAccountReset" in script
    assert "hrBatchLeaderBar" in script
    assert "/api/hr/employees/batch-leaders" in script
    assert '@router.post("/hr/employees/batch-leaders")' in router
    assert "/api/accounts/update-name" in script
    assert '<button type="submit" class="primary" disabled>确认修改姓名</button>' in script
    assert "可重置范围" in script
    assert "登录账号后四位" in script
    assert "至少4位" in script
    assert "function attachmentControl(url,title,previewKind='')" in script
    assert "bindFilePreviews(document.getElementById('entryResults'))" in script
    assert "bindFilePreviews(app);" in script
    assert "bindReviewActions(showHistory,pageOffset);" in script
    assert "proof_is_previewable" in router
    assert "image_is_previewable" in router
    assert "attachment_is_previewable" in router
    assert "document_preview_kind" in router
    assert "proof_preview_kind" in router
    assert "attachment_preview_kind" in router
    assert "该材料不是可预览图片或PDF" in router
    assert "target=\"_blank\">声明PDF" not in script
    watermark = (Path(__file__).parents[1] / "app" / "v2_watermark.py").read_text(encoding="utf-8")
    assert "worksheet.protection.sheet = True" not in watermark
    assert "worksheet.protection.objects = True" not in watermark
    assert "from openpyxl.styles import Protection" not in watermark
    assert "do not enable worksheet or object" in watermark
