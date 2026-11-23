"""Directory of Heat Pursuit rotation contacts."""

from datetime import date, datetime
import hashlib
import json

from pydantic import BaseModel, StrictInt

from app.rotation import service as S
from app.services.identity import base_role_at, base_roles_at, employed_on
from app.services.organization import managed_attraction_ids
from app.v2_models import Employee


class ContactOrder(BaseModel):
    revision: str
    order: list[StrictInt]
    pinned: list[StrictInt]


def can_edit(db, attraction_id, user):
    if user.rotation_temporary or not user.employee.is_active:
        return False
    today = date.today().isoformat()
    role = base_role_at(db, user.id, today)
    return bool(role and role.code == 'GSM' and attraction_id in managed_attraction_ids(db, user.id, today))


def directory(db, attraction_id, cfg, *, editable):
    today = date.today().isoformat()
    employees = db.query(Employee).filter(
        Employee.attraction_id == attraction_id,
        Employee.is_active.is_(True),
        Employee.account_deleted_at.is_(None),
    ).order_by(Employee.name, Employee.id)
    staff = {employee.id: employee.name for employee in employees if employed_on(employee, today)}
    roles = base_roles_at(db, set(staff), today)
    trainers = {eid: name for eid, name in staff.items() if roles[eid] and roles[eid].code == 'TR'}
    saved = json.loads(cfg.settings_json or '{}').get(S.ROTATION_CONTACTS_SETTING, {})
    order = [eid for eid in dict.fromkeys(saved.get('order', []) + list(trainers)) if eid in trainers]
    pinned = set(saved.get('pinned', []))
    order.sort(key=lambda eid: eid not in pinned)
    items = [{'id': eid, 'name': trainers[eid], 'pinned': eid in pinned} for eid in order]
    return {'items': items, 'can_edit': editable,
            'revision': hashlib.sha256(json.dumps(items, ensure_ascii=False).encode()).hexdigest()}


def save_order(db, attraction_id, cfg, payload, user_id):
    current = directory(db, attraction_id, cfg, editable=True)
    if payload.revision != current['revision']:
        raise S.ConfirmationConflict('联系人名单已更新，请刷新后重新排序')
    listed = {item['id'] for item in current['items']}
    ordered = set(payload.order)
    pinned = set(payload.pinned)
    if len(payload.order) != len(ordered) or ordered != listed or not pinned <= ordered:
        raise S.ActionError('请按当前培训师名单保存排序和置顶')
    settings = json.loads(cfg.settings_json or '{}')
    settings[S.ROTATION_CONTACTS_SETTING] = {
        'order': payload.order, 'pinned': [eid for eid in payload.order if eid in pinned],
    }
    cfg.settings_json = json.dumps(settings, ensure_ascii=False)
    cfg.updated_by_id = user_id
    cfg.updated_at = datetime.now()
    return directory(db, attraction_id, cfg, editable=True)
