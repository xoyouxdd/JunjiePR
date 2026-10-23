"""Current identity and scope checks, including each media Range request."""
import json
from fastapi import HTTPException
from sqlalchemy import or_, and_

from app.v2_models import Attraction, Employee
from app.video_models import VideoCircle, VideoAudiencePolicy, LearningVideo

LEAD_ROLES = {"TA_SUPERVISOR", "SUPERVISOR"}
SENIOR_ROLES = {"TA_GSM", "GSM", "AM", "OM"}
AUDIENCE_LABELS = {'local_tr':'本圈 TR', 'local_lead':'本圈 LEAD', 'all_tr':'所有 TR',
                   'all_lead':'所有 LEAD', 'all_gsm':'所有 GSM', 'am':'AM', 'om':'OM',
                   'local_all':'本圈所有人', 'all_people':'所有圈所有人'}
NOTICE_VERSION = "2026-10-22.1"
NOTICE_TEXT = "本视频仅供授权人员内部学习。未经许可，请勿下载、录屏、翻拍或向外传播。视频烧录内部学习品牌标记，页面显示您的员工号并记录访问；员工号为播放层水印，不保证在独立播放器或提取文件中保留。"
_CURRENT_CIRCLE = object()


def scope_ids(db, user):
    circles = {r.id for r in db.query(Attraction).filter(Attraction.active.is_(True), Attraction.employee_circle.is_(True))}
    if senior(user):
        return circles
    return circles & {user.employee.attraction_id}


def senior(user):
    return user.has_role('SYSTEM_ADMIN', *SENIOR_ROLES) or 'SYSTEM_ADMIN' in user.permissions


def administrator(user):
    return user.has_role('SYSTEM_ADMIN') or 'SYSTEM_ADMIN' in user.permissions


def audience_options(db, user, local_id=_CURRENT_CIRCLE):
    allowed = set(AUDIENCE_LABELS) if senior(user) else {'local_tr'} if user.role_codes & LEAD_ROLES else set()
    local_id = user.employee.attraction_id if local_id is _CURRENT_CIRCLE else local_id
    if local_id not in {r.id for r in db.query(Attraction).filter_by(active=True, employee_circle=True)}:
        allowed -= {'local_tr', 'local_lead', 'local_all'}
    return [{'code': code, 'label': label} for code, label in AUDIENCE_LABELS.items() if code in allowed]


def audience_tier(user):
    # Effective highest current identity; a TR acting as LEAD is not a TR recipient.
    for tier, roles in [('om', {'OM'}), ('am', {'AM'}), ('gsm', {'TA_GSM','GSM'}),
                        ('lead', LEAD_ROLES), ('tr', {'TR'})]:
        if user.role_codes & roles:
            return tier
    return None


def policy_data(db, video):
    row = db.get(VideoAudiencePolicy, video.id)
    result = {'codes': json.loads(row.codes), 'local_attraction_id': row.local_attraction_id,
              'revision': row.revision} if row else {'codes':['local_tr'], 'local_attraction_id':video.attraction_id, 'revision':0}
    circle = db.get(Attraction,result['local_attraction_id'])
    result['local_circle_name'] = circle.name if circle else ''
    return result


def validate_audience(db, user, codes, local_id=_CURRENT_CIRCLE):
    codes = set(codes)
    allowed = {r['code'] for r in audience_options(db, user,local_id)}
    if not codes:
        raise HTTPException(400, '请至少选择一类观看人员')
    if not codes <= allowed:
        raise HTTPException(403, '观看人员范围超出当前发布权限')
    return [code for code in AUDIENCE_LABELS if code in codes]


def audience_circles(db, local_id, codes):
    if any(not code.startswith('local_') for code in codes):
        return {r.id for r in db.query(Attraction).filter_by(active=True, employee_circle=True)}
    return {local_id}


def recipient_condition(db, user):
    tier = audience_tier(user)
    # Everyone scopes include CM and other valid identities, without broadening TR.
    active_circles = {r.id for r in db.query(Attraction).filter_by(active=True, employee_circle=True)}
    allowed = or_(VideoAudiencePolicy.codes.contains('"all_people"', autoescape=True),
                  and_(VideoAudiencePolicy.codes.contains('"local_all"', autoescape=True),
                       VideoAudiencePolicy.local_attraction_id == user.employee.attraction_id,
                       user.employee.attraction_id in active_circles))
    if tier:
        global_code = 'all_'+tier if tier in {'tr','lead','gsm'} else tier
        allowed = or_(allowed, VideoAudiencePolicy.codes.contains('"'+global_code+'"', autoescape=True))
    if tier in {'tr','lead'}:
        allowed = or_(allowed, and_(VideoAudiencePolicy.local_attraction_id == user.employee.attraction_id,
                                   VideoAudiencePolicy.codes.contains('"local_'+tier+'"',autoescape=True)))
    policy = db.query(VideoAudiencePolicy.video_id).filter(VideoAudiencePolicy.video_id == LearningVideo.id)
    match = policy.filter(allowed).exists()
    # Previous local candidates without a policy fail closed to TR in their old circles.
    legacy = db.query(VideoCircle.video_id).filter(VideoCircle.video_id == LearningVideo.id)
    local = or_(legacy.filter(VideoCircle.attraction_id == user.employee.attraction_id).exists(),
                and_(~legacy.exists(), LearningVideo.attraction_id == user.employee.attraction_id))
    return or_(match, and_(~policy.exists(), local, tier == 'tr'))


def is_recipient(db, user, video):
    return db.query(LearningVideo.id).filter(LearningVideo.id == video.id,recipient_condition(db,user)).first() is not None


def video_targets(db, video):
    ids = {r.attraction_id for r in db.query(VideoCircle).filter_by(video_id=video.id)}
    return ids or {video.attraction_id}


def upload_scope_ids(db, user):
    return scope_ids(db, user) if senior(user) or user.role_codes & LEAD_ROLES else set()


def can_upload(db, user):
    return bool(upload_scope_ids(db, user))


def require_upload(db, user, attraction_id):
    require_upload_targets(db, user, {attraction_id})


def require_upload_targets(db, user, ids):
    if not ids or not set(ids) <= upload_scope_ids(db, user):
        raise HTTPException(403, "没有在该景点上传视频的权限")


def can_manage(db, user, video):
    return can_upload(db, user) and (administrator(user) or video.owner_id == user.id)


def can_delete(user, video):
    return video.status == 'withdrawn' and (administrator(user) or video.owner_id == user.id)


def can_edit_audience(db, user, video):
    return can_upload(db, user) and (administrator(user) or video.owner_id == user.id)


def publisher_circle(db, user, video):
    employee = db.get(Employee,video.owner_id) if administrator(user) else user.employee
    return employee.attraction_id if employee else None


def publish_block_reason(db, user, video):
    if not can_manage(db,user,video):
        return '当前没有发布该视频的权限'
    if not db.get(VideoAudiencePolicy,video.id):
        return '请先保存观看范围，再发布'
    policy=policy_data(db,video)
    local_id=publisher_circle(db,user,video)
    allowed={r['code'] for r in audience_options(db,user,local_id)}
    if not policy['codes'] or not set(policy['codes']) <= allowed:
        return '观看范围超出现有发布权限，请先调整观看范围'
    if any(c.startswith('local_') for c in policy['codes']) and policy['local_attraction_id'] != local_id:
        return '发布者所属圈已变化，请先调整观看范围'
    targets=audience_circles(db,policy['local_attraction_id'],policy['codes'])
    if not targets or not targets <= upload_scope_ids(db,user):
        return '发布景点范围已变化，请先调整观看范围'
    return ''


def require_manage(db, user, video):
    if not can_manage(db, user, video):
        raise HTTPException(403, "没有管理该视频的权限")


def require_view(db, user, video, *, preview=False):
    if preview and video.status in {'ready','published'} and can_edit_audience(db,user,video):
        return
    if video.status != "published":
        raise HTTPException(404, "视频尚未发布或已经撤下")
    if not is_recipient(db,user,video):
        raise HTTPException(404, "视频不存在或不在可查看范围")


def audit(db, user, request, video_id, action, after=None):
    from app.services.audit import write_audit
    write_audit(db, user, action, "video_learning", video_id, after=after or {},
                ip_address=request.client.host if request.client else None)
