"""Self-only honor presentation. No external AI calls or new performance rules."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.services.monthly_ranks import member_month_query_context, member_month_rank
from app.v2_models import Employee, EmployeeHonorMemory, EmployeeHonorStyle, RecognitionRecord

HONOR_SKINS = frozenset({"sea", "gold", "moon", "ice", "jade", "copper", "forest", "minimal"})

# Editorial hints only: no length, issuer identity, score or seniority weight.
# Match actions with objects, rather than rewarding generic praise like 工作认真.
ACTION = re.compile(r"(?:完成|核对|检查|排查|发现|反馈|协助|帮助|引导|整理|清理|处理|确认|交接|提醒|疏导|复核|讲解|补充|修复|安抚).{1,}")
CONTEXT = re.compile(r"游客|交接|点检|现场|排队|高峰|当班|设备|任务|路线|分组|同事|异常|开园|闭园|岗位")
RESULT = re.compile(r"完成|解决|恢复|核实|复核|交付|消除|归还|找回|到达")


def editorial_order(record) -> tuple:
    content = record.poc_reason or record.content or ""
    return (-bool(ACTION.search(content)), -bool(CONTEXT.search(content)),
            -bool(RESULT.search(content)), record.recognition_date, record.id)


def honor_records(db: Session, month: str, employee_id: int):
    return sorted(db.query(RecognitionRecord).filter(
        RecognitionRecord.employee_id == employee_id,
        RecognitionRecord.recognition_month == month,
        RecognitionRecord.status == "confirmed",
    ).all(), key=editorial_order)


def record_view(row) -> dict:
    return {"id": row.id, "date": row.recognition_date, "category": row.recognition_type_name,
            "content": row.content, "poc_reason": row.poc_reason or "",
            "recognizer": row.recognizer_name, "status": "confirmed",
            "cap_reason": row.monthly_cap_reason or ""}


def monthly_letter(month: str, records: list) -> dict | None:
    """Grounded prose: source quotations are exact, not invented achievements.

    A date/type introduction works for short 20-character recognition records
    as well as detailed POC records. No unobserved outcome or score comparisons.
    """
    if not records:
        return None
    number = int(month[5:])
    sources = records[:3]
    passages = [f"{number}月，值得留下的不只是一个勋章，还有认可记录中的具体文字。"]
    for row in sources:
        detail = row.poc_reason or row.content
        passages.append(f"{int(row.recognition_date[5:7])}月{int(row.recognition_date[8:10])}日，"
                        f"一条关于“{row.recognition_type_name}”的认可写下：“{detail}”")
    passages.append("岗位上的用心，常常藏在一件件具体的小事里。谢谢你在这些记录中留下的投入。"
                    "愿这页回顾替你留住这些时刻，也愿今后的日子里，认真付出的你，继续被看见。")
    return {"title": f"{number}月，值得留下的一页", "paragraphs": passages,
            "source_ids": [row.id for row in sources], "method": "source-quotes-v1"}


def self_honors(db: Session, month: str, employee: Employee) -> dict:
    rank = member_month_rank(db, month, employee)
    rows = honor_records(db, month, employee.id)
    style = db.get(EmployeeHonorStyle, employee.id)
    memory = db.get(EmployeeHonorMemory, (employee.id, month))
    valid_ids = {row.id for row in rows}
    # A removed or no longer confirmed record must not remain a selected highlight.
    selected = memory.selected_record_id if memory and memory.selected_record_id in valid_ids else (rows[0].id if rows else None)
    options = member_month_query_context(db, month, employee)["query_months"]
    # The full month selector remains available; show the 12 latest entries here.
    history = []
    for option in options[:12]:
        item = rank if option["month"] == month else member_month_rank(db, option["month"], employee)
        history.append({key: item.get(key) for key in ("month", "status", "tier", "label")})
    ready = bool(rank.get("month_closed")) and rank.get("status") not in {"reopened", "pending", "waiting_global", "unavailable"}
    return {"month": month, "skin": style.skin if style and style.skin in HONOR_SKINS else "sea",
            "selected_record_id": selected, "favorite": bool(memory and memory.favorite),
            "records": [record_view(row) for row in rows],
            "letter": monthly_letter(month, rows) if ready else None,
            "letter_state": "ready" if ready and rows else "empty" if ready else "waiting",
            "history": history}
