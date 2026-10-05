"""轮岗测试时钟接口（仅测试阶段使用，正式版与 app/rotation/test_clock.py 一起删除）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.rotation import service, test_clock
from app.rotation.access import RotationActor, require_manager, rotation_attraction_id
from app.v2_database import get_db


router = APIRouter(prefix="/rotation", tags=["rotation"])


@router.post("/test-clock")
def rotation_test_clock(payload: dict, db: Session = Depends(get_db), actor: RotationActor = Depends(require_manager)):
    try:
        return test_clock.control(db, rotation_attraction_id(db), payload)
    except service.ActionError as exc:
        raise HTTPException(400, str(exc)) from exc
