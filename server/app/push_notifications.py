from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from pathlib import Path
from threading import Lock

from sqlalchemy.orm import Session

from app.v2_database import DATA_DIR, ensure_directories
from app.v2_models import AppNotification, PushSubscription


VAPID_KEY_FILE = DATA_DIR / "web_push_vapid_private.pem"
_vapid_lock = Lock()


def _vapid_key():
    """Load or generate the server's one private VAPID key without exposing it."""
    from py_vapid import Vapid

    ensure_directories()
    with _vapid_lock:
        if VAPID_KEY_FILE.is_file():
            return Vapid.from_file(private_key_file=str(VAPID_KEY_FILE))
        key = Vapid()
        key.generate_keys()
        temp_path = Path(f"{VAPID_KEY_FILE}.tmp-{os.getpid()}")
        temp_path.write_bytes(key.private_pem())
        try:
            os.replace(temp_path, VAPID_KEY_FILE)
        finally:
            temp_path.unlink(missing_ok=True)
        return key


def public_vapid_key() -> str:
    """Return only the browser-safe public VAPID key."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = _vapid_key().public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def send_notification_pushes(db: Session, notification_id: int) -> None:
    """Best-effort delivery. A provider/browser failure must never undo a review."""
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return

    notification = db.get(AppNotification, notification_id)
    if not notification:
        return
    subscriptions = (
        db.query(PushSubscription)
        .filter(PushSubscription.employee_id == notification.employee_id, PushSubscription.active.is_(True))
        .all()
    )
    if not subscriptions:
        return
    payload = json.dumps(
        {
            "notification_id": notification.id,
            "title": notification.title,
            "body": notification.body,
            "url": notification.target_path,
        },
        ensure_ascii=False,
    )
    changed = False
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=payload,
                vapid_private_key=_vapid_key(),
                vapid_claims={"sub": "mailto:notifications@localhost.invalid"},
                ttl=60 * 60 * 12,
                timeout=5,
            )
            subscription.last_success_at = datetime.now()
            subscription.failed_at = None
            changed = True
        except WebPushException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {404, 410}:
                subscription.active = False
            subscription.failed_at = datetime.now()
            changed = True
        except Exception:
            subscription.failed_at = datetime.now()
            changed = True
    if changed:
        try:
            db.commit()
        except Exception:
            db.rollback()
