"""Live scope checks shared by shares, attachments and group-leader approvals."""
from datetime import date, datetime
from fastapi import HTTPException
from app.v2_models import Attraction, Employee, UserAccount
from app.learning_models import LearningAccess, LearningShare
from app.services.organization import current_leader_for_employee, direct_member_ids
from app.services.identity import base_roles_at, duties_at_bulk

MANAGERS = {'SUPERVISOR', 'TA_SUPERVISOR', 'TA_GSM', 'GSM', 'AM', 'OM', 'SYSTEM_ADMIN'}
PUBLISHER_ROLES = {'TR', 'SUPERVISOR', 'GSM'}
ACTING_PUBLISHER_ROLES = {'TA_SUPERVISOR', 'TA_GSM'}


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


def direct_read(user, circle_id):
    """Permanent read scope is independent of publication/management scope."""
    return (user.has_role('SUPERVISOR', 'GSM', 'AM', 'OM', 'SYSTEM_ADMIN')
            or management(user, circle_id) or user.employee.attraction_id == circle_id)


def valid_circle(db, circle_id):
    """Shared by published reading and private draft/attachment access."""
    circle = db.get(Attraction, circle_id)
    return bool(circle and circle.active and circle.employee_circle)


def readable(db, user, circle_id):
    if not valid_circle(db, circle_id):
        return False
    if direct_read(user, circle_id):
        return True
    return bool(db.query(LearningAccess.id).filter_by(employee_id=user.id, circle_id=circle_id,
        home_circle_id=user.employee.attraction_id, status='approved').filter(LearningAccess.expires_at > datetime.now()).first())


def require_read(db, user, share):
    eligible(user)
    if not share or share.status != 'published' or not readable(db, user, share.circle_id):
        raise HTTPException(403, '资料未开放、已下架或查看权限已到期')


def publisher_identity(base_role, duties):
    return bool(base_role and (base_role.code in PUBLISHER_ROLES
                               or any(role.code in ACTING_PUBLISHER_ROLES for role in duties)))


def can_publish(user, circle_id):
    return user.employee.attraction_id == circle_id and publisher_identity(user.base_role, user.duty_roles)


def owner_can_publish(user, share):
    return share.owner_id == user.id and can_publish(user, share.circle_id)


def eligible_publishers(db, circle_id, employee_id=None):
    query = db.query(Employee).join(UserAccount, UserAccount.employee_id == Employee.id).filter(
        Employee.attraction_id == circle_id, Employee.is_active.is_(True), UserAccount.enabled.is_(True),
    )
    if employee_id is not None:
        query = query.filter(Employee.id == employee_id)
    employees = query.order_by(Employee.name, Employee.id).all()
    ids = [employee.id for employee in employees]
    today = date.today()
    base_roles = base_roles_at(db, ids, today)
    duties = duties_at_bulk(db, ids, today)
    return [employee for employee in employees
            if publisher_identity(base_roles.get(employee.id), duties.get(employee.id, []))]


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
