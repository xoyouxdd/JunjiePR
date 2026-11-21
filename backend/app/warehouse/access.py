from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.employee_numbers import employee_number_aliases
from app.services.identity import base_roles_at, duties_at_bulk, identity_labels
from app.v2_auth import V2User
from app.v2_models import Employee, UserAccount
from app.warehouse.models import WarehouseOwner, warehouse_now


OPERATOR_ROLES = {"TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM", "AM", "OM", "TALEAD", "LEAD"}


def is_regular(user: V2User) -> bool:
    return not (user.rotation_temporary or user.rotation_identity_temporary)


def can_dispatch(user: V2User) -> bool:
    return is_regular(user) and any(role.active and role.code in OPERATOR_ROLES for role in (user.base_role, *user.duty_roles))


def can_assign(user: V2User) -> bool:
    return is_regular(user) and user.base_role.active and user.base_role.code == "SYSTEM_ADMIN" and "SYSTEM_ADMIN" in user.permissions


def can_access(user: V2User) -> bool:
    return can_assign(user) or can_dispatch(user)


def eligible_people(db: Session, people: list[Employee]) -> list[Employee]:
    ids = [person.id for person in people]
    roles = base_roles_at(db, ids, warehouse_now().date())
    duties = duties_at_bulk(db, ids, warehouse_now().date())
    return [
        person for person in people
        if any(role and role.active and role.code in OPERATOR_ROLES for role in (roles.get(person.id), *duties.get(person.id, [])))
    ]


def active_people(db: Session):
    return db.query(Employee).join(UserAccount, UserAccount.employee_id == Employee.id).filter(
        Employee.is_active.is_(True), UserAccount.enabled.is_(True),
    )


def manager_people(db: Session) -> list[Employee]:
    return eligible_people(db, active_people(db).join(WarehouseOwner, WarehouseOwner.employee_id == Employee.id)
                           .order_by(Employee.name, Employee.employee_no).all())


def is_manager(db: Session, user: V2User) -> bool:
    if not can_dispatch(user) or db.get(WarehouseOwner, user.id) is None:
        return False
    return bool(eligible_people(db, active_people(db).filter(Employee.id == user.id).all()))


def require_access(user: V2User) -> None:
    if not can_access(user):
        raise HTTPException(403, "当前账号无权访问物资管理")


def require_assign(user: V2User) -> None:
    if not can_assign(user):
        raise HTTPException(403, "只有最高权限 HR 可以安排仓库负责人")


def require_dispatch(user: V2User) -> None:
    if not can_dispatch(user):
        raise HTTPException(403, "当前账号无权办理物资收发")


def require_manager(db: Session, user: V2User) -> None:
    if not is_manager(db, user):
        raise HTTPException(403, "此操作由已安排的仓库负责人办理")


def person_data(db: Session, people: list[Employee]) -> list[dict]:
    labels = identity_labels(db, [person.id for person in people], warehouse_now().date())
    return [{"id": row.id, "name": row.name, "employee_no": row.employee_no, "role": labels[row.id]} for row in people]


def find_candidates(db: Session, query: str) -> list[dict]:
    query = query.strip()
    if not query:
        return []
    exact = employee_number_aliases(query)
    matches = active_people(db).filter(or_(
        Employee.name.contains(query, autoescape=True),
        Employee.employee_no.in_(exact),
        Employee.employee_no.contains(query, autoescape=True),
    )).order_by(Employee.employee_no.in_(exact).desc(), (Employee.name == query).desc(), Employee.name, Employee.employee_no).all()
    return person_data(db, eligible_people(db, matches)[:30])


def can_be_manager(db: Session, employee_id: int) -> bool:
    return bool(eligible_people(db, active_people(db).filter(Employee.id == employee_id).all()))


def active_owner_ids(db: Session) -> set[int]:
    return {person.id for person in manager_people(db)}


def eligible_manager(db: Session, employee_id: int) -> bool:
    return db.get(WarehouseOwner, employee_id) is not None and can_be_manager(db, employee_id)
