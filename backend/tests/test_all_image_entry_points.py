from __future__ import annotations

from datetime import datetime

from app.routers.v2 import is_previewable_image, preview_kind
from app.v2_models import DeductionRecord, Employee, RecognitionAttachment, RecognitionRecord, SickLeaveRecord, StoredFile
from app.record_payloads import deduction_payload, recognition_payload, sick_leave_payload
from app.services.performance_details import member_score_detail_payload


def test_material_preview_kind_follows_format_and_status() -> None:
    active_image = StoredFile(extension=".jpg", status="active")
    inactive_image = StoredFile(extension=".png", status="deleted")
    pdf = StoredFile(extension=".pdf", status="active")
    assert is_previewable_image(active_image) is True
    assert is_previewable_image(inactive_image) is False
    assert is_previewable_image(pdf) is False
    assert preview_kind(active_image) == "image"
    assert preview_kind(pdf) == "pdf"



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
