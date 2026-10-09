"""Submission receipts and content digests; caller owns the transaction."""
from __future__ import annotations

import hashlib
import json
from app.v2_models import SubmissionRequest
from fastapi import HTTPException
from sqlalchemy.orm import Session


def normalize_request_key(value: str | None) -> str | None:
    key = str(value or "").strip()
    if not key:
        return None
    if len(key) > 100:
        raise HTTPException(400, "重复提交标识过长")
    return key


def submission_payload_digest(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def existing_submission(
    db: Session,
    actor_id: int,
    operation: str,
    request_key: str | None,
    model,
    payload_digest: str | None = None,
):
    if not request_key:
        return None
    request_row = db.query(SubmissionRequest).filter_by(
        actor_id=actor_id,
        operation=operation,
        request_key=request_key,
    ).first()
    if not request_row:
        return None
    stored = (request_row.payload_digest or "").strip()
    if payload_digest and stored and stored != payload_digest:
        raise HTTPException(
            409,
            {
                "code": "IDEMPOTENCY_PAYLOAD_CONFLICT",
                "message": "同一提交键不能用于不同内容，请刷新页面后重新提交。",
            },
        )
    return db.get(model, request_row.entity_id)


def remember_submission(
    db: Session,
    actor_id: int,
    operation: str,
    request_key: str | None,
    entity_id: int,
    payload_digest: str | None = None,
) -> None:
    if request_key:
        db.add(
            SubmissionRequest(
                actor_id=actor_id,
                operation=operation,
                request_key=request_key,
                entity_id=entity_id,
                payload_digest=payload_digest,
            )
        )
        db.flush()
