"""Roster-authorized participation, independent of HR ownership and workbench roles."""
from datetime import datetime
import json

from fastapi import HTTPException
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database.connection import Base
from app.v2_models import Attraction, UserAccount


class RosterIdentity(Base):
    __tablename__ = 'formal_rotation_roster_identities'
    upload_id: Mapped[int] = mapped_column(ForeignKey('formal_rotation_roster_uploads.id'),primary_key=True)
    employee_no: Mapped[str] = mapped_column(String(7),primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    metadata_json: Mapped[str] = mapped_column(Text)


def account_issues(db, employee):
    from app.services.employee_types import is_due
    from app.v2_services import base_role_at
    account=db.query(UserAccount).filter_by(employee_id=employee.id).first()
    issues=[]
    if employee.account_deleted_at: issues.append('已删除留档')
    if not employee.is_active: issues.append('人员非在职')
    if not account: issues.append('无登录账号')
    elif not account.enabled: issues.append('账号停用')
    if is_due(employee.employee_type,employee.internship_ends_on): issues.append('实习已到期')
    if account and not base_role_at(db,employee.id): issues.append('未配置有效角色')
    return issues,account


def roster_metadata(db,aid,day,upload_id=None):
    from app.rotation import service as S
    if not db.info.get('rotation_formal'): return {}
    upload=S.roster_upload_for(db,aid,day) if upload_id is None else None
    uid=upload_id or (upload.id if upload else None)
    if not uid:return {}
    return {r.employee_no:json.loads(r.metadata_json) for r in db.query(RosterIdentity).filter_by(upload_id=uid)}


def contexts(db,no,day=None):
    previous=db.info.get('rotation_formal')
    db.info['rotation_formal']=True
    try:return _contexts(db,no,day)
    finally:
        if previous is None:db.info.pop('rotation_formal',None)
        else:db.info['rotation_formal']=previous


def _contexts(db,no,day=None):
    """Only the effective roster (day overrides week), or a currently running board."""
    from app.rotation import service as S
    from app.rotation.namespace import FORMAL_TABLES as F
    day=day or datetime.now().date().isoformat()
    aids={r[0] for r in db.query(F.RotationRosterEntry.attraction_id).filter_by(employee_no=no,work_date=day)}
    live={}
    for row in db.query(F.RotationDay).filter_by(work_date=day,status='live'):
        state=S.day_state(row)
        if no in state.get('persons',{}):
            aids.add(row.attraction_id);live[row.attraction_id]=state.get('rosterIdentities',{}).get(no)
    out=[]
    for aid in sorted(aids):
        roster=S.roster_for(db,aid,day)
        present=bool(roster and no in roster['days'][day])
        if not present and aid not in live:continue
        circle=db.get(Attraction,aid)
        if not circle or not circle.active or not circle.employee_circle:continue
        metadata=roster_metadata(db,aid,day).get(no,{}) if present else live.get(aid) or {}
        out.append({'id':aid,'name':circle.name,'metadata':metadata})
    return out


def personal_scope(db,user,selected=None):
    choices=contexts(db,user.employee.employee_no)
    home=user.employee.attraction_id
    if not choices and not getattr(user,'rotation_temporary',False) and home:
        circle=db.get(Attraction,home)
        if circle and circle.active and circle.employee_circle:
            # Home-circle personal access predates roster-specific authorization.
            choices.append({'id':home,'name':circle.name,'metadata':{}})
    if not choices:raise HTTPException(403,'当日没有可用的本人轮岗安排')
    if selected is not None:
        choice=next((c for c in choices if c['id']==selected),None)
        if not choice:raise HTTPException(403,'该景点圈没有本人当日轮岗安排')
    else:
        choice=choices[0]
    return choice['id'],[{'id':c['id'],'name':c['name']} for c in choices]


def save_metadata(db,upload,batch):
    for no,metadata in batch['identities'].items():
        db.add(RosterIdentity(upload_id=upload.id,employee_no=no,name=metadata['name'],
                             metadata_json=json.dumps(metadata,ensure_ascii=False)))


def record_identity(db,aid,day,no,state=None):
    if not db.info.get('rotation_formal') or not no:return {}
    if state is None:
        from app.rotation import service as S
        row=S.day_row(db,aid,day)
        state=S.day_state(row) if row else {}
    return state.get('rosterIdentities',{}).get(no) or roster_metadata(db,aid,day).get(no,{})


def labels(metadata):
    out=[]
    if metadata.get('cross_circle'):out.append('跨圈支援·原属'+metadata.get('origin_name','未归属'))
    if metadata.get('temporary'):out.append('临时')
    out.extend(metadata.get('issues',[]))
    return out
