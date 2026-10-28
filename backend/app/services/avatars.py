"""Shared static assets and one batched preference lookup per directory."""
import json
from functools import lru_cache
from pathlib import Path
from app.v2_models import EmployeeAvatarPreference


@lru_cache(maxsize=1)
def avatar_catalog():
    path = Path(__file__).resolve().parents[1] / "static/images/avatars/catalog.json"
    return {item["key"]: item for item in json.loads(path.read_text(encoding="utf-8"))["avatars"]}


def avatar_keys(db, employee_ids):
    ids = set(employee_ids)
    if not ids:
        return {}
    catalog = avatar_catalog()
    return {row.employee_id: row.avatar_key for row in db.query(EmployeeAvatarPreference).filter(
        EmployeeAvatarPreference.employee_id.in_(ids)).all() if row.avatar_key in catalog}


def own_avatar(db, employee_id):
    row = db.get(EmployeeAvatarPreference, employee_id)
    return {"avatar_key": row.avatar_key if row and row.avatar_key in avatar_catalog() else "",
            "avatar_frame": row.frame if row else "plain"}
