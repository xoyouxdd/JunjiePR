"""Shared about-dialog credits; only the exact ADMIN login may edit them."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.routers._shared import client_ip
from app.v2_auth import V2User, current_user
from app.v2_database import get_db
from app.v2_models import AboutSystemSettings
from app.v2_services import write_audit

router = APIRouter()
DEFAULT_MAINTAINERS = "赵君杰、张森"


def can_edit(user: V2User) -> bool:
    return getattr(getattr(user, "account", None), "login_account", None) == "ADMIN"


def payload(db: Session, user: V2User) -> dict:
    row = db.get(AboutSystemSettings, 1)
    return {"maintainers": row.maintainers if row else DEFAULT_MAINTAINERS,
            "feedback": row.feedback if row else "",
            "revision": row.revision if row else 0, "can_edit": can_edit(user)}


class CreditsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    maintainers: str = Field(strict=True, max_length=200)
    feedback: str = Field(strict=True, max_length=200)
    revision: int = Field(strict=True, ge=0)


@router.get("/about-system")
def read_credits(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    return payload(db, user)


@router.put("/about-system")
def update_credits(change: CreditsChange, request: Request,
                   db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if not can_edit(user):
        raise HTTPException(403, "仅ADMIN账号可修改建设与维护、建议与反馈名单")
    if any(ord(char) < 32 and char not in "\r\n" for char in change.maintainers + change.feedback):
        raise HTTPException(422, "姓名不能包含控制字符")
    names = {field: "\n".join(line.strip() for line in getattr(change, field).splitlines() if line.strip())
             for field in ("maintainers", "feedback")}
    if not names["maintainers"]:
        raise HTTPException(422, "请填写建设与维护名单")
    before = payload(db, user)
    if change.revision == 0:
        result = db.execute(insert(AboutSystemSettings).values(id=1, contributors="", **names, revision=1)
                            .on_conflict_do_nothing(index_elements=["id"]))
    else:
        result = db.execute(update(AboutSystemSettings)
                            .where(AboutSystemSettings.id == 1, AboutSystemSettings.revision == change.revision)
                            .values(**names, revision=change.revision + 1))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "名单已更新，请关闭并重新打开关于系统后再修改")
    write_audit(db, user.employee, "修改关于系统名单", "about_system", 1,
                before={field: before[field] for field in names},
                after={**names, "login_account": user.account.login_account},
                ip_address=client_ip(request))
    db.commit()
    db.expire_all()
    return payload(db, user)
