"""Independent learning library; no signatures, notifications or publication todos."""
from datetime import datetime, timedelta
from io import BytesIO
import json
from pathlib import Path
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app import learning as domain
from app.learning_models import LearningShare as Share, LearningRevision as Revision, LearningReaction as Reaction, LearningAccess as Access, LearningAsset as Asset, LearningDraft as Draft, LearningFeedback as Feedback
from app.v2_auth import current_user, V2User
from app.v2_database import get_db, FILE_DIR
from app.v2_models import Employee
from app.v2_services import write_audit

router = APIRouter(prefix='/learning', tags=['learning'])
CATEGORIES = ('未分类', '开场准备', '岗位操作', '异常处理', '经验技巧')
Category = Literal['未分类', '开场准备', '岗位操作', '异常处理', '经验技巧']


def member(response: Response, user=Depends(current_user)):
    domain.eligible(user)
    response.headers.update({'Cache-Control':'private, no-store','Vary':'Cookie'})
    return user


class Content(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(default='', max_length=20000)
    assets: list[int] = Field(default_factory=list, max_length=8)
    summary: str = Field(default='', max_length=500)
    expected_version: int = Field(default=1, ge=1)
    category: Category | None = None


class Decision(BaseModel):
    action: str
    reason: str = Field(default='', max_length=500)
    expected_version: int = Field(ge=1)


class AccessInput(BaseModel):
    circle_id: int
    reason: str = Field(min_length=1, max_length=500)


class ReactionInput(BaseModel):
    liked: bool
    starred: bool


class TransferInput(BaseModel):
    employee_id: int
    reason: str = Field(min_length=1, max_length=500)
    expected_version: int = Field(ge=1)


def circle_or_fail(db, id):
    if id not in {c.id for c in domain.circles(db)}:
        raise HTTPException(400, '请选择有效景点圈')


def assets_valid(db, user, values, share=None):
    existing = set(json.loads(share.assets)) if share else set()
    for id in set(values):
        row = db.get(Asset, id)
        if not row or row.circle_id != (share.circle_id if share else user.employee.attraction_id) or (row.owner_id != user.id and id not in existing):
            raise HTTPException(403, '附件未上传成功或不属于当前文档')


def content_valid(db, user, data, share=None):
    if not data.title.strip() or not (data.body.strip() or data.assets):
        raise HTTPException(400, '请填写标题和内容或上传材料')
    assets_valid(db, user, data.assets, share)


def claim(db, share, version, **values):
    count = db.query(Share).filter_by(id=share.id, owner_id=share.owner_id, version=version, status='published').update(
        {**values, 'version': version+1, 'updated_at': datetime.now()}, synchronize_session=False)
    if not count:
        raise HTTPException(409, '文档已经变化，请刷新后核对再操作')


@router.get('/options')
def options(db: Session = Depends(get_db), user: V2User = Depends(member)):
    grants = {r.circle_id:r for r in db.query(Access).filter_by(employee_id=user.id)}
    leader = domain.current_leader_for_employee(db, user.id)
    pending = db.query(Revision).join(Share, Share.id == Revision.share_id).filter(
        Share.owner_id == user.id, Share.circle_id == user.employee.attraction_id,
        Share.status == 'published', Revision.status == 'pending').count()
    return {'circles':[{'id':c.id,'name':c.name,'readable':domain.readable(db,user,c.id),
        'own':c.id==user.employee.attraction_id,'can_edit':domain.editing(user,c.id),
        'can_manage':domain.management(user,c.id),
        'access_status':grants[c.id].status if c.id in grants and grants[c.id].home_circle_id==user.employee.attraction_id else None,
        'expires_at':grants[c.id].expires_at if c.id in grants and grants[c.id].home_circle_id == user.employee.attraction_id else None} for c in domain.circles(db)],
        'can_publish':user.has_role('TR') and user.employee.attraction_id in {c.id for c in domain.circles(db)},
        'can_draft':domain.editing(user,user.employee.attraction_id), 'categories':CATEGORIES,
        'pending_revisions':pending,'approvals':len(domain.approval_rows(db,user)),
        'leader':{'id':leader.id,'name':leader.name} if leader else None,
        'viewer':{'name':user.name,'employee_no':user.employee.employee_no}}


@router.get('')
def listing(circle_id: int, view: str = 'circle', page: int = Query(1, ge=1), keyword: str = Query('', max_length=100), category: Category | None = None, db: Session = Depends(get_db), user: V2User = Depends(member)):
    circle_or_fail(db,circle_id)
    q = db.query(Share).filter_by(circle_id=circle_id, status='published')
    if view == 'star':
        q=q.join(Reaction, Reaction.share_id==Share.id).filter(Reaction.employee_id==user.id,Reaction.starred.is_(True))
    elif view == 'mine':
        q=q.filter(Share.owner_id==user.id)
    elif view != 'circle':
        raise HTTPException(400,'栏目不正确')
    if keyword.strip():q=q.filter(Share.title.contains(keyword.strip(),autoescape=True))
    if category:q=q.filter(Share.category==category)
    total=q.count()
    rows=q.order_by(Share.updated_at.desc(),Share.id.desc()).offset((page-1)*20).limit(20).all()
    ids=[r.id for r in rows]
    owners={e.id:e.name for e in db.query(Employee).filter(Employee.id.in_({r.owner_id for r in rows}))}
    counts=dict(db.query(Reaction.share_id,func.count(Reaction.id)).filter(Reaction.share_id.in_(ids),Reaction.liked.is_(True)).group_by(Reaction.share_id).all())
    reactions={r.share_id:r for r in db.query(Reaction).filter(Reaction.share_id.in_(ids),Reaction.employee_id==user.id)}
    pending=dict(db.query(Revision.share_id,func.count(Revision.id)).filter(Revision.share_id.in_(ids),Revision.status=='pending').group_by(Revision.share_id).all())
    allowed=domain.readable(db,user,circle_id)
    return {'items':[{'id':r.id,'title':r.title,'category':r.category,'version':r.version,'owner_id':r.owner_id,'owner':owners.get(r.owner_id,''),
        'updated_at':r.updated_at,'locked':not allowed,'likes':counts.get(r.id,0),
        'liked':bool(reactions.get(r.id) and reactions[r.id].liked),'starred':bool(reactions.get(r.id) and reactions[r.id].starred),
        'pending_revisions':pending.get(r.id,0) if r.owner_id==user.id else 0} for r in rows],
        'total':total,'page':page,'pages':max(1,(total+19)//20)}


@router.post('')
def publish(data: Content, db: Session = Depends(get_db), user: V2User = Depends(member)):
    result = create_share(db,user,data)
    db.commit()
    return result


def create_share(db, user, data):
    circle_or_fail(db,user.employee.attraction_id)
    if not user.has_role('TR'):
        raise HTTPException(403,'仅本圈TR可以直接发布攻略')
    content_valid(db,user,data)
    row=Share(circle_id=user.employee.attraction_id,owner_id=user.id,publisher_id=user.id,title=data.title.strip(),body=data.body,category=data.category or '未分类',assets=json.dumps(data.assets))
    db.add(row);db.flush()
    db.add(Revision(share_id=row.id,editor_id=user.id,base_version=1,title=row.title,body=row.body,assets=row.assets,
        category=row.category,summary='首次发布',status='published',reviewed_by=user.id,reviewed_at=datetime.now()))
    write_audit(db,user.employee,'发布学习攻略','learning_share',row.id,after={'title':row.title,'circle_id':row.circle_id})
    return {'id':row.id,'version':row.version}


@router.get('/requests')
def requests(db: Session = Depends(get_db), user: V2User = Depends(member)):
    rows=domain.approval_rows(db,user)
    employees={e.id:e for e in db.query(Employee).filter(Employee.id.in_({r.employee_id for r in rows}))}
    names={c.id:c.name for c in domain.circles(db)}
    return {'items':[{'id':r.id,'employee_name':employees[r.employee_id].name,'employee_no':employees[r.employee_id].employee_no,
        'circle':names.get(r.circle_id,''),'reason':r.reason,'requested_at':r.requested_at,'generation':r.generation} for r in rows]}


@router.post('/requests')
def apply(data: AccessInput, db: Session = Depends(get_db), user: V2User = Depends(member)):
    circle_or_fail(db,data.circle_id)
    if not data.reason.strip():raise HTTPException(400,'请填写申请原因')
    if domain.readable(db,user,data.circle_id):raise HTTPException(409,'已有查看权限，无需重复申请')
    leader=domain.current_leader_for_employee(db,user.id)
    if not leader or leader.id==user.id:raise HTTPException(400,'未找到本组组长，请先核对分组关系')
    row=db.query(Access).filter_by(employee_id=user.id,circle_id=data.circle_id).first()
    if row and row.status=='pending' and row.home_circle_id==user.employee.attraction_id:
        return {'id':row.id,'status':'pending'}
    values={'home_circle_id':user.employee.attraction_id,'reason':data.reason.strip(),'status':'pending',
        'requested_at':datetime.now(),'reviewed_by':None,'reviewed_at':None,'expires_at':None,'review_note':''}
    stmt=insert(Access).values(employee_id=user.id,circle_id=data.circle_id,**values)
    db.execute(stmt.on_conflict_do_update(index_elements=['employee_id','circle_id'],set_={**values,'generation':Access.generation+1}))
    write_audit(db,user.employee,'申请跨圈学习资料','learning_access',after={'circle_id':data.circle_id,'reason':data.reason})
    db.commit()
    return {'status':'pending'}


@router.post('/requests/{id}/decision')
def access_decision(id: int, data: Decision, db: Session = Depends(get_db), user: V2User = Depends(member)):
    row=db.get(Access,id)
    if not row:raise HTTPException(404,'申请不存在')
    domain.validate_approver(db,user,row)
    if data.action not in {'approve','reject','revoke'}:raise HTTPException(400,'操作不正确')
    if data.action=='reject' and not data.reason.strip():raise HTTPException(400,'请填写退回原因')
    now=datetime.now();expires=now+timedelta(days=7) if data.action=='approve' else None
    status={'approve':'approved','reject':'rejected','revoke':'revoked'}[data.action]
    count=db.query(Access).filter_by(id=id,generation=data.expected_version,status='approved' if data.action=='revoke' else 'pending').update(
        {'status':status,'reviewed_by':user.id,'reviewed_at':now,'expires_at':expires,'review_note':data.reason,'generation':Access.generation+1},synchronize_session=False)
    if not count:raise HTTPException(409,'申请状态已变化，请刷新')
    write_audit(db,user.employee,'审批跨圈学习资料','learning_access',id,after={'action':data.action,'expires_at':str(expires),'reason':data.reason})
    db.commit()
    return {'status':status,'expires_at':expires}


@router.get('/grants')
def grants(db: Session = Depends(get_db), user: V2User = Depends(member)):
    ids=domain.approvable_ids(db,user)
    rows=db.query(Access).filter(Access.employee_id.in_(ids),Access.status=='approved',Access.expires_at>datetime.now()).all()
    names={c.id:c.name for c in domain.circles(db)}
    people={e.id:e for e in db.query(Employee).filter(Employee.id.in_({r.employee_id for r in rows}))}
    return {'items':[{'id':r.id,'employee_name':people[r.employee_id].name,'circle':names.get(r.circle_id,''),
                     'expires_at':r.expires_at,'generation':r.generation} for r in rows if people[r.employee_id].attraction_id==r.home_circle_id]}


@router.get('/owners')
def owners(circle_id: int, db: Session = Depends(get_db), user: V2User = Depends(member)):
    if not domain.management(user,circle_id):raise HTTPException(403,'仅本圈主管及以上可以交接负责人')
    return {'items':[{'id':e.id,'name':e.name,'employee_no':e.employee_no} for e in db.query(Employee).filter_by(attraction_id=circle_id,is_active=True)
                     if domain.active_tr(db,e.id,circle_id)]}


@router.post('/assets')
async def upload(file: UploadFile = File(...), circle_id: int = Form(...), db: Session = Depends(get_db), user: V2User = Depends(member)):
    if not domain.editing(user,circle_id):raise HTTPException(403,'没有上传权限')
    circle_or_fail(db,circle_id)
    # Read one bounded upload. Validate without rendering, watermarking or copying it per viewer.
    raw=await file.read(10*1024*1024+1)
    if not raw or len(raw)>10*1024*1024:raise HTTPException(400,'单个附件请控制在10MB以内')
    suffix=Path(file.filename or '').suffix.lower()
    try:
        if suffix=='.pdf':
            import fitz
            with fitz.open(stream=raw,filetype='pdf') as pdf:
                if pdf.needs_pass or not pdf.page_count:raise ValueError('PDF不可预览')
            media='application/pdf'
        elif suffix in {'.jpg','.jpeg','.png','.webp'}:
            from PIL import Image
            with Image.open(BytesIO(raw)) as image:
                if image.width*image.height>20_000_000:raise ValueError('图片尺寸过大')
                image.verify()
                media={'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp'}[image.format]
        else:raise ValueError('请上传PDF、JPG、PNG或WebP')
    except Exception as error:raise HTTPException(400,'附件格式无效，请上传可阅读的PDF或图片') from error
    key='learning/'+uuid4().hex+suffix
    path=FILE_DIR/key;path.parent.mkdir(parents=True,exist_ok=True)
    try:
        path.write_bytes(raw)
        row=Asset(owner_id=user.id,circle_id=circle_id,filename=Path(file.filename).name[:200],key=key,media_type=media)
        db.add(row);db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {'id':row.id,'filename':row.filename,'media_type':media}


class DraftInput(BaseModel):
    title: str = Field(default='', max_length=100)
    body: str = Field(default='', max_length=20000)
    assets: list[int] = Field(default_factory=list, max_length=8)
    category: Category = '未分类'
    summary: str = Field(default='', max_length=500)
    share_id: int | None = Field(default=None,ge=1)
    base_version: int = Field(default=1, ge=1)
    expected_version: int = Field(default=1, ge=1)


class DraftVersion(BaseModel):
    expected_version: int = Field(ge=1)


def draft_owned(db, user, id):
    row=db.get(Draft,id)
    if not row or row.employee_id!=user.id:raise HTTPException(404,'草稿不存在')
    if not domain.editing(user,row.circle_id) or (not row.share_id and row.circle_id!=user.employee.attraction_id):
        raise HTTPException(403,'岗位或景点圈已变化，不能继续编辑此草稿')
    if row.share_id:
        share=db.get(Share,row.share_id);domain.require_read(db,user,share)
        if share.circle_id!=row.circle_id:raise HTTPException(403,'文档所属景点圈已变化')
    return row


def draft_payload(db,row):
    assets=db.query(Asset).filter(Asset.id.in_(json.loads(row.assets))).all()
    return {'id':row.id,'title':row.title,'body':row.body,'category':row.category,'summary':row.summary,
            'share_id':row.share_id,'base_version':row.base_version,'version':row.version,'updated_at':row.updated_at,
            'assets':[{'id':a.id,'filename':a.filename,'media_type':a.media_type} for a in assets]}


@router.get('/drafts')
def draft_list(page: int = Query(1,ge=1), keyword: str = Query('',max_length=100), category: Category | None = None, db: Session = Depends(get_db), user: V2User = Depends(member)):
    if not domain.editing(user,user.employee.attraction_id):raise HTTPException(403,'没有草稿编辑权限')
    allowed={c.id for c in domain.circles(db) if domain.editing(user,c.id)}
    q=db.query(Draft).filter(Draft.employee_id==user.id,Draft.circle_id.in_(allowed))
    if keyword.strip():q=q.filter(Draft.title.contains(keyword.strip(),autoescape=True))
    if category:q=q.filter_by(category=category)
    total=q.count()
    rows=q.order_by(Draft.updated_at.desc(),Draft.id.desc()).offset((page-1)*20).limit(20).all()
    return {'items':[{'id':r.id,'title':r.title,'category':r.category,'version':r.version,'updated_at':r.updated_at} for r in rows],
            'page':page,'pages':max(1,(total+19)//20),'total':total}


def validate_draft(db,user,data):
    circle_or_fail(db,user.employee.attraction_id)
    share=db.get(Share,data.share_id) if data.share_id else None
    if data.share_id:
        domain.require_read(db,user,share)
    elif not user.has_role('TR'):raise HTTPException(403,'仅本圈TR可起草新攻略')
    circle_id=share.circle_id if share else user.employee.attraction_id
    if not domain.editing(user,circle_id):raise HTTPException(403,'没有草稿编辑权限')
    assets_valid(db,user,data.assets,share)
    return circle_id


@router.post('/drafts')
def create_draft(data: DraftInput, db: Session = Depends(get_db), user: V2User = Depends(member)):
    circle_id=validate_draft(db,user,data)
    row=Draft(employee_id=user.id,circle_id=circle_id,share_id=data.share_id,base_version=data.base_version,
              title=data.title,body=data.body,category=data.category,summary=data.summary,assets=json.dumps(data.assets))
    db.add(row);db.flush()
    write_audit(db,user.employee,'保存知识库草稿','learning_draft',row.id)
    db.commit();return draft_payload(db,row)


@router.get('/drafts/{id}')
def draft_detail(id:int, db: Session = Depends(get_db), user: V2User = Depends(member)):
    return draft_payload(db,draft_owned(db,user,id))


@router.put('/drafts/{id}')
def update_draft(id:int,data: DraftInput, db: Session = Depends(get_db), user: V2User = Depends(member)):
    row=draft_owned(db,user,id)
    if (data.share_id,data.base_version)!=(row.share_id,row.base_version):raise HTTPException(409,'草稿来源已变化，请重新打开')
    validate_draft(db,user,data)
    count=db.query(Draft).filter_by(id=id,employee_id=user.id,version=data.expected_version).update(
        {'title':data.title,'body':data.body,'category':data.category,'summary':data.summary,'assets':json.dumps(data.assets),
         'version':Draft.version+1,'updated_at':datetime.now()},synchronize_session=False)
    if not count:raise HTTPException(409,'草稿已在其他页面更新，请重新打开后核对')
    db.commit();db.expire_all();return draft_payload(db,db.get(Draft,id))


@router.delete('/drafts/{id}')
def delete_draft(id:int,data: DraftVersion, db: Session = Depends(get_db), user: V2User = Depends(member)):
    draft_owned(db,user,id)
    if not db.query(Draft).filter_by(id=id,employee_id=user.id,version=data.expected_version).delete(synchronize_session=False):raise HTTPException(409,'草稿已变化，请刷新')
    db.commit();return {'ok':True}


@router.post('/drafts/{id}/publish')
def publish_draft(id:int,data: DraftVersion, db: Session = Depends(get_db), user: V2User = Depends(member)):
    row=draft_owned(db,user,id)
    if row.version!=data.expected_version:raise HTTPException(409,'草稿已变化，请重新预览')
    if not row.title.strip():raise HTTPException(400,'请填写标题')
    content=Content(title=row.title,body=row.body,assets=json.loads(row.assets),summary=row.summary,category=row.category,expected_version=row.base_version)
    # Claim the draft in the same transaction as publication, preventing duplicate publication.
    if not db.query(Draft).filter_by(id=id,employee_id=user.id,version=data.expected_version).delete(synchronize_session=False):raise HTTPException(409,'草稿已经被处理')
    result=create_revision(db,user,row.share_id,content) if row.share_id else create_share(db,user,content)
    db.commit();return result


@router.get('/assets/{asset_id}')
def private_asset(asset_id:int, db: Session = Depends(get_db), user: V2User = Depends(member)):
    a=db.get(Asset,asset_id)
    if not a or a.owner_id!=user.id or not domain.editing(user,a.circle_id):raise HTTPException(403,'没有草稿附件查看权限')
    path=(FILE_DIR/a.key).resolve()
    if FILE_DIR.resolve() not in path.parents or not path.is_file():raise HTTPException(404,'附件不存在')
    return FileResponse(path,media_type=a.media_type,headers={'Cache-Control':'private, no-store','Content-Disposition':'inline','X-Content-Type-Options':'nosniff'})


@router.get('/{id}')
def detail(id: int, db: Session = Depends(get_db), user: V2User = Depends(member)):
    row=db.get(Share,id);domain.require_read(db,user,row)
    owner=db.get(Employee,row.owner_id)
    assets=db.query(Asset).filter(Asset.id.in_(json.loads(row.assets))).all()
    reaction=db.query(Reaction).filter_by(share_id=id,employee_id=user.id).first()
    grant=db.query(Access).filter_by(employee_id=user.id,circle_id=row.circle_id,status='approved').first()
    return {'id':row.id,'title':row.title,'body':row.body,'category':row.category,'circle_id':row.circle_id,'version':row.version,
        'owner':owner.name,'owner_id':row.owner_id,'publisher_id':row.publisher_id,'updated_at':row.updated_at,
        'assets':[{'id':a.id,'filename':a.filename,'media_type':a.media_type} for a in assets],
        'can_edit':domain.editing(user,row.circle_id),'can_manage':domain.management(user,row.circle_id),
        'can_publish':domain.owner_can_publish(db,user,row),'can_review':domain.owner_can_publish(db,user,row),
        'liked':bool(reaction and reaction.liked),'starred':bool(reaction and reaction.starred),
        'likes':db.query(Reaction).filter_by(share_id=id,liked=True).count(),
        'viewer':{'name':user.name,'employee_no':user.employee.employee_no},
        'expires_at':grant.expires_at if grant and row.circle_id!=user.employee.attraction_id and not domain.management(user,row.circle_id) else None}


@router.get('/{id}/assets/{asset_id}')
def attachment(id: int, asset_id: int, db: Session = Depends(get_db), user: V2User = Depends(member)):
    row=db.get(Share,id);domain.require_read(db,user,row)
    if asset_id not in json.loads(row.assets):raise HTTPException(404,'附件未公开')
    a=db.get(Asset,asset_id)
    if not a:raise HTTPException(404,'附件不存在')
    path=(FILE_DIR/a.key).resolve()
    if FILE_DIR.resolve() not in path.parents or not path.is_file():raise HTTPException(404,'附件不存在')
    return FileResponse(path,media_type=a.media_type,headers={'Cache-Control':'private, no-store','Content-Disposition':'inline','X-Content-Type-Options':'nosniff'})


@router.put('/{id}/reaction')
def reaction(id: int, data: ReactionInput, db: Session = Depends(get_db), user: V2User = Depends(member)):
    domain.require_read(db,user,db.get(Share,id))
    stmt=insert(Reaction).values(share_id=id,employee_id=user.id,**data.model_dump())
    db.execute(stmt.on_conflict_do_update(index_elements=['share_id','employee_id'],set_=data.model_dump()))
    db.commit()
    return {'ok':True}


@router.post('/{id}/revisions')
def edit(id: int, data: Content, db: Session = Depends(get_db), user: V2User = Depends(member)):
    result=create_revision(db,user,id,data)
    db.commit();return result


def create_revision(db,user,id,data):
    row=db.get(Share,id);domain.require_read(db,user,row)
    if not domain.editing(user,row.circle_id):raise HTTPException(403,'仅本圈TR及主管以上可参与修改')
    if not data.summary.strip():raise HTTPException(400,'请填写修改说明')
    content_valid(db,user,data,row)
    if row.version!=data.expected_version:raise HTTPException(409,'公开版本已更新，请重新打开文档')
    direct=domain.owner_can_publish(db,user,row)
    revision=Revision(share_id=id,editor_id=user.id,base_version=row.version,title=data.title.strip(),body=data.body,
        assets=json.dumps(data.assets),category=data.category or row.category,summary=data.summary,status='published' if direct else 'pending',
        reviewed_by=user.id if direct else None,reviewed_at=datetime.now() if direct else None)
    db.add(revision)
    if direct:claim(db,row,data.expected_version,title=revision.title,body=revision.body,assets=revision.assets,category=revision.category)
    write_audit(db,user.employee,'修改学习攻略','learning_share',id,after={'direct':direct,'summary':data.summary})
    db.flush()
    return {'id':revision.id,'status':revision.status}


@router.get('/{id}/revisions')
def revisions(id: int, db: Session = Depends(get_db), user: V2User = Depends(member)):
    row=db.get(Share,id);domain.require_read(db,user,row)
    if not domain.editing(user,row.circle_id):raise HTTPException(403,'没有修改记录查看权限')
    q=db.query(Revision).filter_by(share_id=id)
    if row.owner_id!=user.id and not domain.management(user,row.circle_id):q=q.filter_by(editor_id=user.id)
    rows=q.order_by(Revision.created_at.desc()).limit(50).all()
    names={e.id:e.name for e in db.query(Employee).filter(Employee.id.in_({r.editor_id for r in rows}))}
    return {'items':[{'id':r.id,'editor':names[r.editor_id],'base_version':r.base_version,'title':r.title,'body':r.body,'category':r.category,
        'assets':[{'id':a.id,'filename':a.filename,'media_type':a.media_type} for a in db.query(Asset).filter(Asset.id.in_(json.loads(r.assets)))],
        'summary':r.summary,'status':r.status,'reason':r.reason,'created_at':r.created_at} for r in rows]}


@router.get('/{id}/revisions/{revision_id}/assets/{asset_id}')
def revision_attachment(id: int, revision_id: int, asset_id: int, db: Session = Depends(get_db), user: V2User = Depends(member)):
    share=db.get(Share,id);domain.require_read(db,user,share)
    row=db.get(Revision,revision_id)
    if not row or row.share_id!=id or not domain.editing(user,share.circle_id) or not (user.id in {share.owner_id,row.editor_id} or domain.management(user,share.circle_id)):
        raise HTTPException(403,'没有修改稿附件查看权限')
    if asset_id not in json.loads(row.assets):raise HTTPException(404,'附件不存在')
    a=db.get(Asset,asset_id)
    if not a:raise HTTPException(404,'附件不存在')
    path=(FILE_DIR/a.key).resolve()
    if FILE_DIR.resolve() not in path.parents or not path.is_file():raise HTTPException(404,'附件不存在')
    return FileResponse(path,media_type=a.media_type,headers={'Cache-Control':'private, no-store','Content-Disposition':'inline','X-Content-Type-Options':'nosniff'})


@router.post('/{id}/revisions/{revision_id}/decision')
def review(id: int, revision_id: int, data: Decision, db: Session = Depends(get_db), user: V2User = Depends(member)):
    row=db.get(Share,id);domain.require_read(db,user,row)
    if not domain.owner_can_publish(db,user,row):raise HTTPException(403,'仅文档当前负责TR可确认发布')
    r=db.get(Revision,revision_id)
    if not r or r.share_id!=id:raise HTTPException(404,'修改稿不存在')
    if data.action not in {'approve','reject'}:raise HTTPException(400,'操作不正确')
    if data.action=='reject' and not data.reason.strip():raise HTTPException(400,'请说明退回原因')
    if data.action=='approve' and (r.base_version!=row.version or data.expected_version!=row.version):raise HTTPException(409,'修改稿基于旧版本，请修改人核对最新内容后重新提交')
    count=db.query(Revision).filter_by(id=r.id,status='pending').update({'status':'published' if data.action=='approve' else 'returned',
        'reason':data.reason,'reviewed_by':user.id,'reviewed_at':datetime.now()},synchronize_session=False)
    if not count:raise HTTPException(409,'修改稿已被处理')
    if data.action=='approve':claim(db,row,data.expected_version,title=r.title,body=r.body,assets=r.assets,category=r.category)
    write_audit(db,user.employee,'审核学习攻略修改','learning_share',id,after={'revision_id':r.id,'action':data.action,'reason':data.reason})
    db.commit()
    return {'ok':True}


@router.post('/{id}/withdraw')
def withdraw(id: int, data: Decision, db: Session = Depends(get_db), user: V2User = Depends(member)):
    row=db.get(Share,id)
    if not row or not domain.management(user,row.circle_id):raise HTTPException(403,'仅本圈主管及以上可下架')
    if not data.reason.strip():raise HTTPException(400,'请填写下架原因')
    claim(db,row,data.expected_version,status='withdrawn')
    write_audit(db,user.employee,'下架学习攻略','learning_share',id,after={'reason':data.reason})
    db.commit();return {'ok':True}


@router.post('/{id}/transfer')
def transfer(id: int, data: TransferInput, db: Session = Depends(get_db), user: V2User = Depends(member)):
    row=db.get(Share,id)
    if not row or not domain.management(user,row.circle_id):raise HTTPException(403,'仅本圈主管及以上可交接')
    if not data.reason.strip() or not domain.active_tr(db,data.employee_id,row.circle_id):raise HTTPException(400,'请选择本圈有效TR并填写交接原因')
    # Ownership changes do not change the published content version or invalidate pending edits.
    count=db.query(Share).filter_by(id=id,owner_id=row.owner_id,version=data.expected_version,status='published').update({'owner_id':data.employee_id},synchronize_session=False)
    if not count:raise HTTPException(409,'文档已变化，请刷新')
    write_audit(db,user.employee,'交接学习攻略','learning_share',id,after={'owner_id':data.employee_id,'reason':data.reason})
    db.commit();return {'ok':True}


class FeedbackInput(BaseModel):
    kind: Literal['内容有误','需要补充','附件问题']
    content: str = Field(min_length=1,max_length=500)
    expected_version: int = Field(ge=1)


class FeedbackDecision(BaseModel):
    response: str = Field(min_length=1,max_length=500)
    expected_version: int = Field(ge=1)


def feedback_manager(user,share):
    return domain.owner_can_publish(None,user,share) or domain.management(user,share.circle_id)


@router.get('/{id}/feedback')
def feedback_list(id:int,page:int=Query(1,ge=1),db:Session=Depends(get_db),user:V2User=Depends(member)):
    share=db.get(Share,id);domain.require_read(db,user,share)
    manage=feedback_manager(user,share)
    q=db.query(Feedback).filter_by(share_id=id)
    if not manage:q=q.filter_by(employee_id=user.id)
    total=q.count();rows=q.order_by(Feedback.created_at.desc(),Feedback.id.desc()).offset((page-1)*20).limit(20).all()
    names={e.id:e.name for e in db.query(Employee).filter(Employee.id.in_({r.employee_id for r in rows}))}
    return {'items':[{'id':r.id,'author':names.get(r.employee_id,''),'kind':r.kind,'content':r.content,'share_version':r.share_version,
                      'status':r.status,'response':r.response,'created_at':r.created_at,'handled_at':r.handled_at,'version':r.version} for r in rows],
            'can_manage':manage,'page':page,'pages':max(1,(total+19)//20)}


@router.post('/{id}/feedback')
def submit_feedback(id:int,data:FeedbackInput,db:Session=Depends(get_db),user:V2User=Depends(member)):
    share=db.get(Share,id);domain.require_read(db,user,share)
    if not data.content.strip():raise HTTPException(400,'请填写具体问题')
    if data.expected_version!=share.version:raise HTTPException(409,'资料版本已更新，请重新查看后反馈')
    row=Feedback(share_id=id,employee_id=user.id,share_version=share.version,kind=data.kind,content=data.content.strip())
    db.add(row);db.flush();write_audit(db,user.employee,'提交知识库反馈','learning_feedback',row.id,after={'share_id':id,'kind':data.kind})
    db.commit();return {'id':row.id,'status':row.status}


@router.post('/{id}/feedback/{feedback_id}/resolve')
def resolve_feedback(id:int,feedback_id:int,data:FeedbackDecision,db:Session=Depends(get_db),user:V2User=Depends(member)):
    share=db.get(Share,id);domain.require_read(db,user,share)
    if not feedback_manager(user,share):raise HTTPException(403,'仅作者或本圈负责人员可处理反馈')
    if not data.response.strip():raise HTTPException(400,'请填写处理说明')
    count=db.query(Feedback).filter_by(id=feedback_id,share_id=id,status='pending',version=data.expected_version).update(
        {'status':'resolved','response':data.response.strip(),'handled_by':user.id,'handled_at':datetime.now(),'version':Feedback.version+1},synchronize_session=False)
    if not count:raise HTTPException(409,'反馈已处理或已变化，请刷新')
    write_audit(db,user.employee,'处理知识库反馈','learning_feedback',feedback_id,after={'share_id':id,'response':data.response.strip()})
    db.commit();return {'ok':True}
