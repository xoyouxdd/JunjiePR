"""Live scope checks shared by shares, attachments and group-leader approvals."""
from datetime import datetime
from fastapi import HTTPException
from app.v2_models import Attraction, Employee, UserAccount
from app.learning_models import LearningAccess, LearningShare
from app.services.organization import current_leader_for_employee, direct_member_ids
from app.services.identity import base_role_at

MANAGERS = {'SUPERVISOR', 'TA_SUPERVISOR', 'TA_GSM', 'GSM', 'AM', 'OM', 'SYSTEM_ADMIN'}


def eligible(user):
    if getattr(user, 'rotation_temporary', False) or user.account is None or user.role.code == 'ROTATION':
        raise HTTPException(403, '请使用本人正式账号')


def circles(db):
    return db.query(Attraction).filter_by(active=True, employee_circle=True).order_by(Attraction.id).all()


def management(user, circle_id):
    if not user.has_role(*MANAGERS):
        return False
    return user.has_role('AM', 'OM', 'SYSTEM_ADMIN') or user.employee.attraction_id == circle_id


def editing(user, circle_id):
    return management(user, circle_id) or (user.has_role('TR') and user.employee.attraction_id == circle_id)


def readable(db, user, circle_id):
    if management(user, circle_id) or user.employee.attraction_id == circle_id:
        return True
    return bool(db.query(LearningAccess.id).filter_by(employee_id=user.id, circle_id=circle_id,
        home_circle_id=user.employee.attraction_id, status='approved').filter(LearningAccess.expires_at > datetime.now()).first())


def require_read(db, user, share):
    eligible(user)
    if not share or share.status != 'published' or not readable(db, user, share.circle_id):
        raise HTTPException(403, '资料未开放、已下架或查看权限已到期')


def owner_can_publish(db, user, share):
    return share.owner_id == user.id and user.employee.attraction_id == share.circle_id and user.has_role('TR')


def active_tr(db, employee_id, circle_id):
    e = db.get(Employee, employee_id)
    r = base_role_at(db, employee_id) if e else None
    return bool(e and e.is_active and e.attraction_id == circle_id and r and r.code == 'TR'
                and db.query(UserAccount.id).filter_by(employee_id=employee_id, enabled=True).first())


def approvable_ids(db, user):
    if not user.has_role(*MANAGERS):
        return set()
    return direct_member_ids(db, user.id) - {user.id}


def approval_rows(db, user):
    ids = approvable_ids(db, user)
    if not ids:
        return []
    return db.query(LearningAccess).join(Employee, Employee.id == LearningAccess.employee_id).filter(
        LearningAccess.employee_id.in_(ids), LearningAccess.status == 'pending', Employee.is_active.is_(True),
        Employee.attraction_id == LearningAccess.home_circle_id).order_by(LearningAccess.requested_at).all()


def validate_approver(db, user, access):
    employee = db.get(Employee, access.employee_id)
    leader = current_leader_for_employee(db, access.employee_id)
    if not employee or not employee.is_active or employee.attraction_id != access.home_circle_id or not leader or leader.id != user.id or user.id == access.employee_id or not user.has_role(*MANAGERS):
        raise HTTPException(403, '仅申请人当前本组组长可处理，请核对分组关系')
