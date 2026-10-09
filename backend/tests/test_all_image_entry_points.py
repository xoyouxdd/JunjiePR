from __future__ import annotations
from frontend_source import frontend_function_source, read_frontend_source

from pathlib import Path
from datetime import datetime

from app.routers.v2 import is_previewable_image, preview_kind
from app.main import SICK_LEAVE_VALIDATION_MESSAGES
from app.v2_models import DeductionRecord, Employee, RecognitionAttachment, RecognitionRecord, SickLeaveRecord, StoredFile
from app.record_payloads import deduction_payload, recognition_payload, sick_leave_payload
from app.services.performance_details import member_score_detail_payload


def test_material_preview_metadata_and_all_image_entry_points() -> None:
    active_image = StoredFile(extension=".jpg", status="active")
    inactive_image = StoredFile(extension=".png", status="deleted")
    pdf = StoredFile(extension=".pdf", status="active")
    assert is_previewable_image(active_image) is True
    assert is_previewable_image(inactive_image) is False
    assert is_previewable_image(pdf) is False
    assert preview_kind(active_image) == "image"
    assert preview_kind(pdf) == "pdf"

    script = read_frontend_source()
    router = "\n".join(_p.read_text(encoding="utf-8") for _p in sorted((Path(__file__).parents[1] / "app" / "routers").glob("*.py")))
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
    assert SICK_LEAVE_VALIDATION_MESSAGES["proof"] == "缺勤证明未成功上传，请重新选择文件后提交。"
    proof_binder = frontend_function_source("bindSick")
    assert "const proofError=" in proof_binder
    assert "const proofMessage=proofError();if(proofMessage)" in proof_binder
    assert "缺勤证明未成功读取，请重新选择文件后提交。" in proof_binder
    assert "catch(error){toast(error.message||'缺勤登记失败" in proof_binder
    # Real api error parsing/forwarding is exercised in frontend_api_errors.cjs.
    validation = frontend_function_source("validationErrorMessage")
    assert "detail.fields.map(item=>item?.message).filter(Boolean).join(' ')" in validation
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
    assert "function hrEditEmployee" in script
    assert "/api/hr/groups/'+group.id+'/leaders" in script
    assert '@router.post("/hr/groups/{group_id}/leaders")' in router
    assert "/api/accounts/update-name" in script
    assert '<button type="submit" class="primary" disabled>确认修改姓名</button>' in script
    assert "可重置范围" in script
    assert "登录账号后四位" in script
    assert "至少4位" in script
    assert "function attachmentControl(url,title,previewKind='')" in script
    assert "bindFilePreviews(document.getElementById('entryResults'))" in script
    assert "bindFilePreviews(app);" in script
    assert "bindReviewActions(showHistory,pageOffset);" in script
    assert "该材料不是可预览图片或PDF" in router
    assert "target=\"_blank\">声明PDF" not in script
    watermark = (Path(__file__).parents[1] / "app" / "v2_watermark.py").read_text(encoding="utf-8")
    assert "worksheet.protection.sheet = True" not in watermark
    assert "worksheet.protection.objects = True" not in watermark
    assert "from openpyxl.styles import Protection" not in watermark
    assert "do not enable worksheet or object" in watermark


def test_material_metadata_survives_query_service_extraction() -> None:
    """Verify response fields themselves, independent of their source-file location."""
    submitted_at = datetime(2026, 9, 15, 10)
    employee = Employee(id=1, employee_no="1000001", name="测试员工")
    image = StoredFile(id=1, extension=".jpg", status="active")
    recognition = RecognitionRecord(
        id=1, employee_id=1, recognition_date="2026-09-15", recognition_type_name="安全",
        status="confirmed", source="self", fraction=1, credited_fraction=1, submitted_at=submitted_at,
        content="测试", recognizer_name="主管", operator_name="员工",
    )
    recognition.attachments = [RecognitionAttachment(file_id=1, file=image)]
    deduction = DeductionRecord(
        id=2, employee_id=1, status="active", points=1, occurred_on="2026-09-15",
        submitted_at=submitted_at, document_file_id=2, material_status="ready",
        deduction_type_name="安全", deduction_level_name="声明", description="测试",
    )
    sick = SickLeaveRecord(
        id=3, employee_id=1, status="active", proof_file_id=1, leave_days=1, charged_days=1,
        leave_start_date="2026-09-15", leave_end_date="2026-09-15", submitted_at=submitted_at,
    )
    proof = sick_leave_payload(None, sick, employees={1: employee}, files={1: image})
    assert proof["proof_is_previewable"] is True
    assert proof["proof_preview_kind"] == "image"
    recognized = recognition_payload(recognition)
    assert recognized["image_is_previewable"] is True
    assert recognized["image_preview_kind"] == "image"
    assert deduction_payload(deduction)["document_preview_kind"] == "pdf"
    detail = member_score_detail_payload(
        None, employee, {}, [recognition], [deduction], [sick], None,
        role_name="CM", sick_leave_rows=[proof],
    )
    records = {row["record_type"]: row for row in detail["details"]["all_records"]}
    assert records["recognition"]["attachment_is_previewable"] is True
    assert records["recognition"]["attachment_preview_kind"] == "image"
    assert records["sick_leave"]["attachment_preview_kind"] == "image"
    assert records["deduction"]["attachment_preview_kind"] == "pdf"
    image.status = "deleted"
    assert recognition_payload(recognition)["image_is_previewable"] is False
    deduction.material_status = "material_processing"
    assert deduction_payload(deduction)["document_preview_kind"] == ""
