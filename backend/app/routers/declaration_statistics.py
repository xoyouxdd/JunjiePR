"""Read-only, cross-circle statistics for registered disciplinary events."""
from __future__ import annotations

from datetime import date
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app.excel_export_utils import content_disposition, excel_row
# Keep the existing query imports available to API callers during extraction.
from app.services.declaration_statistics import (
    MATERIAL_LABELS, STATUS_LABELS, UPGRADE_LABELS, checked_month, declaration_payload,
)
from app.v2_auth import V2User, current_user, require_permissions
from app.v2_database import get_db
from app.v2_services import write_audit
from app.v2_watermark import watermark_workbook
from app.routers._shared import client_ip

router = APIRouter()


@router.get("/declaration-statistics")
def get_declaration_statistics(
    month: str | None = None, attraction_id: int | None = None, level_id: int | None = None,
    type_id: int | None = None, status: str = "", keyword: str = "",
    db: Session = Depends(get_db), user: V2User = Depends(require_permissions("DECLARATION_STATS_VIEW")),
):
    return declaration_payload(db, month or date.today().strftime("%Y-%m"), attraction_id, level_id, type_id, status, keyword)


def build_declaration_workbook(data: dict) -> Workbook:
    wb = Workbook()
    summary = wb.active
    summary.title = "景点圈与等级汇总"
    summary.append(["声明登记统计", data["month"]])
    summary.append(["总记录数", data["total_records"], "涉及人数", data["employee_count"]])
    summary.append(["景点圈", "处分等级", "记录数", "涉及人数", "已生效", "待补材料", "审核中", "已作废"])
    for circle in data["circles"]:
        rows = [row for row in data["records"] if row["attraction_id"] == circle["attraction_id"]]
        for level in circle["levels"]:
            subset = [row for row in rows if row["level_id"] == level["id"]]
            summary.append(excel_row([circle["attraction_name"], level["name"], len(subset), len({row["employee_id"] for row in subset}), *[sum(row["business_status"] == state for row in subset) for state in ("已生效", "待补材料", "审核中", "已作废")]]))
    detail = wb.create_sheet("逐条明细")
    detail.append(["员工", "工号", "原景点圈", "事件日期", "处分类型", "处分等级", "内容", "登记人", "登记时间", "材料状态", "审核或升级状态", "业务状态", "升级工单号", "材料入口"])
    for row in data["records"]:
        detail.append(excel_row([row["employee_name"], row["employee_no"], row["attraction_name"], row["occurred_on"], row["type_name"], row["level_name"], row["description"], row["submitter_name"], row["submitted_at"], row["material_status"], row["upgrade_status"], row["business_status"], row["upgrade_request_id"] or "", "系统内查看" if row["document_url"] else "无可查看材料"]))
    for ws in wb:
        ws.freeze_panes = "A4" if ws is summary else "A2"
        ws.auto_filter.ref = f"A{3 if ws is summary else 1}:{get_column_letter(ws.max_column)}{ws.max_row}"
        for cell in ws[3 if ws is summary else 1]:
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.font = Font(color="FFFFFF", bold=True)
        for col in ws.columns:
            letter = col[0].column_letter
            ws.column_dimensions[letter].width = min(45, max(13, max(len(str(cell.value or "")) for cell in col) + 2))
            for cell in col:
                cell.alignment = Alignment(vertical="center", wrap_text=True)
    return wb


@router.get("/declaration-statistics/export")
def export_declaration_statistics(
    request: Request, month: str | None = None, attraction_id: int | None = None,
    level_id: int | None = None, type_id: int | None = None, status: str = "", keyword: str = "",
    db: Session = Depends(get_db), user: V2User = Depends(current_user),
):
    if not {"DECLARATION_STATS_VIEW", "DECLARATION_STATS_EXPORT"}.issubset(user.permissions):
        raise HTTPException(403, "没有权限执行该操作")
    data = declaration_payload(db, month or date.today().strftime("%Y-%m"), attraction_id, level_id, type_id, status, keyword)
    workbook = build_declaration_workbook(data)
    watermark_workbook(workbook, user.employee.employee_no)
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    write_audit(db, user.employee, "导出声明登记统计", "declaration_statistics_export", data["month"], after={"attraction_id": attraction_id, "level_id": level_id, "type_id": type_id, "status": status, "keyword": keyword, "record_count": data["total_records"]}, ip_address=client_ip(request))
    db.commit()
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": content_disposition(f"declaration_statistics_{data['month']}.xlsx", f"声明登记统计_{data['month']}.xlsx")})
