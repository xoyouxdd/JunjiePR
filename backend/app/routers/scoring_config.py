"""Score-rule workbench; HR stays read-only, SYSTEM_ADMIN keeps the original edit boundary."""
from datetime import date
import json
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.v2_auth import V2User, require_permissions, current_user
from app.v2_database import get_db
from app.v2_models import ScoringPolicy, RecognitionType, DeductionType, DeductionLevel, Role
from app.services import scoring_policies as P
from app.services.identity import recognition_score_for_role
from app.routers._shared import parse_iso_date, client_ip
from app.v2_services import write_audit

router = APIRouter()


def policy_date(value, label):
    parsed=parse_iso_date(value,label)
    if parsed.isoformat()!=value:
        raise HTTPException(422, '日期格式必须为YYYY-MM-DD')
    return parsed


@router.get('/scoring/deduction-options')
def deduction_options(on_date: str = '', db: Session = Depends(get_db), user=Depends(current_user)):
    from app.deduction_policy import direct_only_deduction_user, DIRECT_HIDDEN_DEDUCTION_CODES, DEDUCTION_TYPE_ORDER
    day = on_date or date.today().isoformat()
    policy_date(day,'事件日期')
    direct=direct_only_deduction_user(user)
    rows=[]
    for item in db.query(DeductionType).filter_by(active=True).order_by(DeductionType.id):
        policy=P.category(db,'deduction','all',item.code,day)
        if not policy['active'] or not policy['direct' if direct else 'manager'] or direct and (item.code in DIRECT_HIDDEN_DEDUCTION_CODES or policy['mode']=='fixed'):
            continue
        rows.append(dict(id=item.id,code=item.code,name=policy.get('name',item.name),repeat_check=policy['repeat']=='upgrade',fixed_points=policy['score'] if policy['mode']=='fixed' else None))
    order={code:index for index,code in enumerate(DEDUCTION_TYPE_ORDER)}
    rows.sort(key=lambda row:(order.get(row['code'],len(order)),row['id']))
    return {'types':rows,'levels':[dict(id=l.id,code=l.code,name=l.name,points=float(P.level_score(db,l,day))) for l in db.query(DeductionLevel).filter_by(active=True).order_by(DeductionLevel.points)]}


@router.get('/scoring/recognition-options')
def recognition_options(scope: str = 'frontline', on_date: str = '', db: Session = Depends(get_db), user=Depends(current_user)):
    if scope not in ('frontline','supervisor'):
        raise HTTPException(422, '计分人群无效')
    day = on_date or date.today().isoformat()
    policy_date(day, '认可日期')
    return P.recognition_types(db, scope, day)


@router.get('/hr/scoring-config')
def read_config(on_date: str = '', db: Session = Depends(get_db), user=Depends(require_permissions('HR_MANAGE'))):
    day = on_date or date.today().isoformat()
    policy_date(day, '生效日期')
    result = {'revision': P.revision(db), 'can_edit': user.role.code == 'SYSTEM_ADMIN', 'date': day, 'recognition': {}, 'roles': {}, 'deduction': [], 'levels': []}
    for scope in ('frontline','supervisor'):
        result['recognition'][scope] = [dict(code=t.code, **{**P.category(db,'recognition',scope,t.code,day),
            'name': P.category(db,'recognition',scope,t.code,day).get('name',t.name)}, dedicated=t.code=='POC') for t in db.query(RecognitionType).filter_by(active=True).order_by(RecognitionType.id)]
        codes = ['TA_SUPERVISOR','SUPERVISOR','TA_GSM','GSM','AM','OM'] if scope=='frontline' else ['TA_GSM','GSM','AM','OM']
        result['roles'][scope] = [dict(code=code,name=(role:=db.query(Role).filter_by(code=code).one()).name,
            score=float(recognition_score_for_role(db,role.id,day,'SUPERVISOR' if scope=='supervisor' else 'CM'))) for code in codes]
    for t in db.query(DeductionType).filter_by(active=True).order_by(DeductionType.id):
        policy=P.category(db,'deduction','all',t.code,day)
        result['deduction'].append(dict(code=t.code,**{**policy,'name':policy.get('name',t.name)}))
    result['levels']=[dict(code=t.code,name=t.name,score=float(P.level_score(db,t,day))) for t in db.query(DeductionLevel).filter_by(active=True).order_by(DeductionLevel.points)]
    return result


class Change(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str
    kind: str
    scope: str
    code: str = ''
    effective_date: str
    policy: dict


@router.post('/hr/scoring-config')
def save_config(body: Change, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions('HR_MANAGE'))):
    if user.role.code != 'SYSTEM_ADMIN':
        raise HTTPException(403, '全局计分规则仅最高管理员可调整')
    policy_date(body.effective_date,'生效日期')
    if body.kind not in ('recognition','deduction','role','level'):
        raise HTTPException(422,'规则类型无效')
    db.rollback()
    db.execute(text('BEGIN IMMEDIATE'))
    if body.revision != P.revision(db):
        raise HTTPException(409,'计分规则已变化，请刷新后重新核对')
    code = write_change(body, request, db, user)
    db.commit()
    return {'ok':True,'code':code,'revision':P.revision(db)}


def write_change(body, request, db, user):
    code = body.code or 'CUSTOM_'+uuid4().hex[:20]
    policy = dict(body.policy)
    if body.kind == 'deduction' and not body.code:
        policy['group'] = [code]
    value=P.validate(db,body.kind,body.scope,code,policy)
    if body.kind in ('recognition','deduction'):
        model=RecognitionType if body.kind=='recognition' else DeductionType
        row=db.query(model).filter_by(code=code).first()
        if body.code and not row:
            raise HTTPException(404,'类别不存在')
        if db.query(model).filter(model.name==value['name'],model.code!=code).first():
            raise HTTPException(409,'类别名称已存在')
        for other in db.query(model).filter(model.code!=code):
            effective=P.category(db,body.kind,body.scope,other.code,body.effective_date)
            if effective.get('name',other.name)==value['name']:
                raise HTTPException(409,'类别名称已存在')
        if not row:
            db.add(model(code=code,name=value['name'],active=True))
    row=db.query(ScoringPolicy).filter_by(kind=body.kind,scope=body.scope,code=code,effective_date=body.effective_date).first()
    before=json.loads(row.policy_json) if row else None
    if row:
        row.policy_json=P.dump(value);row.version+=1
    else:
        db.add(ScoringPolicy(kind=body.kind,scope=body.scope,code=code,effective_date=body.effective_date,policy_json=P.dump(value)))
    write_audit(db,user.employee,'修改计分规则','scoring_policy',code,before=before,
                after=dict(kind=body.kind,scope=body.scope,effective_date=body.effective_date,policy=value),ip_address=client_ip(request))
    return code


class BatchChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str
    changes: list[Change]


@router.post('/hr/scoring-config/batch')
def save_batch(body: BatchChange, request: Request, db: Session = Depends(get_db), user=Depends(require_permissions('HR_MANAGE'))):
    if user.role.code != 'SYSTEM_ADMIN':
        raise HTTPException(403, '全局计分规则仅最高管理员可调整')
    if not 1 <= len(body.changes) <= 6 or any(item.kind not in ('role','level') for item in body.changes):
        raise HTTPException(422, '仅允许批量保存角色或等级分值')
    keys = [(item.kind,item.scope,item.code,item.effective_date) for item in body.changes]
    if len(set(keys)) != len(keys):
        raise HTTPException(422, '不能重复提交同一规则')
    for item in body.changes:
        policy_date(item.effective_date,'生效日期')
    db.rollback()
    db.execute(text('BEGIN IMMEDIATE'))
    if body.revision != P.revision(db):
        raise HTTPException(409, '计分规则已变化，请刷新后重新核对')
    codes = [write_change(item,request,db,user) for item in body.changes]
    db.commit()
    return {'ok':True,'codes':codes,'revision':P.revision(db)}
