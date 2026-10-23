"""Real synthetic video transport/transcode/watermark + scope and session negatives."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime, timedelta, date

os.environ['RECOGNITION_V2_DATA_DIR'] = tempfile.mkdtemp(prefix='fz-video-test-')
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS'] = '1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD'] = '1234'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD'] = 'HR123'
os.environ['RECOGNITION_VIDEO_WORKER'] = '0'

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.v2_database import SessionLocal
from app.v2_models import Employee, EmployeeActingDuty, AuditLog, Attraction, UserAccount, AnnouncementGrant
from app.video_models import LearningVideo, VideoCircle, VideoAudiencePolicy, VideoPlayback, VideoJob, VideoProgress
from app import video_media as media
from app.video_worker import run_one, cleanup

BASE = '/api/video-learning'


def login(client, account='SUPTEST01'):
    client.post('/api/logout')
    result = client.post('/api/login', json={'employee_no': account, 'password': '1234'})
    assert result.status_code == 200, result.text


@pytest.fixture(scope='module')
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope='module')
def clip(tmp_path_factory):
    directory = tmp_path_factory.mktemp('clip')
    path = directory/'synthetic.mp4'
    subprocess.run([media.tool('ffmpeg'), '-nostdin', '-y', '-v', 'error', '-f', 'lavfi',
                    '-i', 'testsrc=size=320x180:rate=12', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
                    '-t', '1.5', '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(path)], check=True)
    return path.read_bytes()


def config(client, content, **extra):
    circle = client.get(BASE+'/options').json()['circles'][0]['id']
    hashes = ''.join(hashlib.sha256(content[i:i+media.CHUNK_BYTES]).hexdigest() for i in range(0,len(content),media.CHUNK_BYTES))
    body = {'title': '合成教学视频', 'cover_title':'合成教学', 'attraction_id': circle,
            'size':len(content), 'fingerprint':hashlib.sha256(hashes.encode()).hexdigest(), **extra}
    if ('attraction_ids' in extra or extra.get('all_circles')) and 'attraction_id' not in extra:
        body.pop('attraction_id')
    return body


def upload(client, content, **extra):
    result = client.post(BASE+'/uploads',json=config(client,content,**extra))
    assert result.status_code == 200,result.text
    task=result.json();url=BASE+'/uploads/'+task['upload_id']
    if task['video_id']:
        return task['video_id']
    for offset in range(task['offset'], len(content), media.CHUNK_BYTES):
        result=client.put(url,params={'offset':offset},content=content[offset:offset+media.CHUNK_BYTES])
        assert result.status_code==200,result.text
    result=client.post(url+'/finish');assert result.status_code==200,result.text
    return result.json()['video_id']


def ready(client, clip, title):
    login(client)
    video_id=upload(client,clip,title=title)
    while run_one():
        pass
    response=client.get(BASE+f'/videos/{video_id}')
    assert response.json()['status']=='ready',response.text
    return video_id


def grant(client,video_id):
    options=client.get(BASE+'/options').json()
    result=client.post(BASE+f'/videos/{video_id}/consent',json={'accepted':True,'notice_version':options['notice_version']})
    assert result.status_code==200,result.text
    return result.json()['token']


def test_resumable_chunks_no_loss_and_cancel(client,clip,monkeypatch):
    monkeypatch.setattr(media,'CHUNK_BYTES',4096)
    login(client)
    body=config(client,clip,title='断点续传')
    start=client.post(BASE+'/uploads',json=body).json();url=BASE+'/uploads/'+start['upload_id']
    part=clip[:4096]
    assert client.put(url,params={'offset':0},content=part).json()['offset']==len(part)
    # A lost acknowledgement may repeat exactly the same chunk, never append twice.
    assert client.put(url,params={'offset':0},content=part).json()['offset']==len(part)
    assert client.put(url,params={'offset':0},content=b'x'*len(part)).status_code==409
    resumed=client.post(BASE+'/uploads',json=body).json()
    assert resumed['upload_id']==start['upload_id'] and resumed['offset']==4096
    assert client.post(url+'/finish').status_code==409
    assert client.put(url,params={'offset':4096},content=b'x'*4097).status_code==413
    login(client,'TRTEST01')
    assert client.get(url).status_code==404
    login(client)
    assert client.delete(url).status_code==200
    assert client.get(url).status_code==404


def test_real_compression_cover_publish_and_watermarked_range(client,clip):
    video_id=ready(client,clip,'真实压缩水印验收')
    row=client.get(BASE+f'/videos/{video_id}').json()
    assert set(row['profiles'])=={'720','1080'}
    assert all(p['width']<=320 and p['height']<=180 for p in row['profiles'].values())
    assert client.get(BASE+f'/videos/{video_id}/cover').headers['content-type'].startswith('image/jpeg')
    assert client.post(BASE+f'/videos/{video_id}/cover',json={'second':0,'title':'封面重新生成'}).status_code==200
    assert client.post(BASE+f'/videos/{video_id}/publish').status_code==409
    assert run_one()
    assert client.post(BASE+f'/videos/{video_id}/publish').status_code==200
    login(client,'TRTEST01')
    assert client.get(BASE+'/videos').json()['total']>=1
    assert client.post(BASE+f'/videos/{video_id}/publish').status_code==403
    assert client.post(BASE+f'/videos/{video_id}/consent',json={'accepted':False,'notice_version':'old'}).status_code==400
    token=grant(client,video_id);params={'token':token,'quality':'720'}
    assert client.get(BASE+f'/videos/{video_id}/stream',params=params).status_code==200
    assert client.post(BASE+f'/videos/{video_id}/playback',params=params).json()['status']=='ready'
    assert not run_one()
    status=client.get(BASE+f'/videos/{video_id}/playback',params=params).json()
    assert status['status']=='ready',status
    response=client.get(status['url'],headers={'Range':'bytes=0-99'})
    assert response.status_code==206,response.text
    assert len(response.content)==100
    assert response.headers['cache-control']=='private, no-store'
    assert response.headers['content-type'].startswith('video/mp4')
    path=media.video_dir(video_id)/'master-720.mp4'
    assert path.is_file() and media.probe(path)['duration']>0
    assert 'FZ团队工作台' in (path.parent/'brand.txt').read_text(encoding='utf-8')
    assert status['employee_no']=='TRTEST01' and status['mode']=='shared'
    assert client.put(BASE+f'/videos/{video_id}/progress',params={'token':token},json={'position':1200}).status_code==200
    assert client.get(BASE+f'/videos/{video_id}').json()['position']==row['duration']
    # Direct filesystem guesses have no API endpoint; no original/master route.
    assert client.get(BASE+f'/videos/{video_id}/source.mov').status_code==404
    assert client.get('/static/learning-videos/'+str(video_id)+'/source.mov').status_code==404
    # Tokens are bound to account AND login session, not shareable URLs.
    login(client,'SUPTEST01')
    assert client.get(status['url']).status_code==403
    login(client,'TRTEST01')
    assert client.get(status['url']).status_code==403


def test_cross_circle_and_withdraw_immediately_reject_requests(client,clip):
    video_id=ready(client,clip,'范围撤下测试')
    assert client.post(BASE+f'/videos/{video_id}/publish').status_code==200
    login(client,'TRTEST01');token=grant(client,video_id)
    with SessionLocal() as db:
        employee=db.query(Employee).filter_by(employee_no='TRTEST01').one();old_circle=employee.attraction_id
        employee.attraction_id=db.query(Attraction).filter(Attraction.employee_circle.is_(True),Attraction.id!=old_circle).first().id;db.commit()
    assert client.get(BASE+f'/videos/{video_id}').status_code==404
    assert client.get(BASE+f'/videos/{video_id}/stream',params={'token':token}).status_code==404
    with SessionLocal() as db:
        employee=db.query(Employee).filter_by(employee_no='TRTEST01').one();employee.attraction_id=old_circle;db.commit()
    login(client)
    assert client.post(BASE+f'/videos/{video_id}/withdraw').status_code==200
    login(client,'TRTEST01')
    assert client.get(BASE+f'/videos/{video_id}/cover').status_code==404


def test_version_replacement_preserves_old_history_and_resets_progress(client,clip):
    old=ready(client,clip,'旧版示范');client.post(BASE+f'/videos/{old}/publish')
    login(client,'TRTEST01');token=grant(client,old)
    client.put(BASE+f'/videos/{old}/progress',params={'token':token},json={'position':1})
    login(client);new=upload(client,clip,title='新版示范',replaces_id=old)
    while run_one(): pass
    assert client.post(BASE+f'/videos/{new}/publish').status_code==200
    login(client,'TRTEST01')
    assert client.get(BASE+f'/videos/{old}').status_code==404
    newrow=client.get(BASE+f'/videos/{new}').json()
    assert newrow['version']==2 and newrow['position']==0
    with SessionLocal() as db:
        assert db.query(VideoProgress).filter_by(video_id=old).count()==1
        assert db.query(AuditLog).filter_by(entity_type='video_learning',entity_id=str(new),action='发布学习视频').count()==1


def test_expired_acting_duty_cannot_upload(client):
    login(client,'TATEST01')
    assert client.get(BASE+'/options').json()['can_upload']
    with SessionLocal() as db:
        employee=db.query(Employee).filter_by(employee_no='TATEST01').one()
        employee_id=employee.id
        duty=db.query(EmployeeActingDuty).filter_by(employee_id=employee_id).first()
        old_ends,old_starts=duty.ends_on,duty.starts_on
        duty.starts_on=(date.today()-timedelta(days=2)).isoformat()
        duty.ends_on=(date.today()-timedelta(days=1)).isoformat();db.commit()
    assert not client.get(BASE+'/options').json()['can_upload']
    with SessionLocal() as db:
        duty=db.query(EmployeeActingDuty).filter_by(employee_id=employee_id).first();duty.ends_on=old_ends;duty.starts_on=old_starts;db.commit()


def test_invalid_video_fails_closed_and_retry_is_explicit(client):
    login(client)
    video_id=upload(client,b'not a video',title='损坏文件测试')
    assert run_one()
    row=client.get(BASE+f'/videos/{video_id}').json()
    assert row['status']=='failed'
    assert client.post(BASE+f'/videos/{video_id}/publish').status_code==409
    assert client.post(BASE+f'/videos/{video_id}/retry').status_code==200
    assert run_one()
    assert client.get(BASE+f'/videos/{video_id}').json()['status']=='failed'


def test_runtime_missing_tools_no_new_upload(client,clip,monkeypatch):
    login(client)
    monkeypatch.setattr(media,'tools_ready',lambda:False)
    assert client.post(BASE+'/uploads',json=config(client,clip,title='工具未配置')).status_code==503


def test_upload_permissions_circle_and_integrity(client,clip):
    login(client,'TRTEST01')
    assert not client.get(BASE+'/options').json()['can_upload']
    assert client.post(BASE+'/uploads',json=config(client,clip)).status_code==403
    login(client)
    body=config(client,clip,title='范围检查')
    with SessionLocal() as db:
        other=db.query(Attraction).filter(Attraction.employee_circle.is_(True),Attraction.id!=body['attraction_id']).first().id
    assert client.post(BASE+'/uploads',json={**body,'attraction_id':other}).status_code==403
    assert client.post(BASE+'/uploads',json={**body,'size':media.MAX_BYTES+1}).status_code==422
    task=client.post(BASE+'/uploads',json={**body,'fingerprint':'0'*64}).json()
    url=BASE+'/uploads/'+task['upload_id']
    assert client.put(url,params={'offset':0},content=clip).status_code==200
    assert client.post(url+'/finish').status_code==400
    client.delete(url)


def test_grants_expire_and_disabled_accounts_cannot_read(client,clip):
    video_id=ready(client,clip,'授权失效验收');client.post(BASE+f'/videos/{video_id}/publish')
    login(client,'TRTEST01');token=grant(client,video_id)
    with SessionLocal() as db:
        employee_id=db.query(Employee.id).filter_by(employee_no='TRTEST01').scalar()
        db.query(VideoPlayback).filter_by(video_id=video_id,employee_id=employee_id).update({'expires_at':datetime.now()-timedelta(seconds=1)})
        db.commit()
    assert client.post(BASE+f'/videos/{video_id}/playback',params={'token':token}).status_code==403
    token=grant(client,video_id)
    with SessionLocal() as db:
        db.query(UserAccount).filter_by(employee_id=employee_id).update({'enabled':False});db.commit()
    assert client.get(BASE+f'/videos/{video_id}/stream',params={'token':token}).status_code==401
    with SessionLocal() as db:
        db.query(UserAccount).filter_by(employee_id=employee_id).update({'enabled':True});db.commit()


def test_edit_metadata_rebuilds_cover_and_stale_jobs_recover(client,clip):
    video_id=ready(client,clip,'元数据编辑验收')
    row=client.get(BASE+f'/videos/{video_id}').json()
    body={'title':'编辑后的完整标题','cover_title':'编辑后短标题','description':'保留教学说明','attraction_id':row['attraction_id'],'replaces_id':None}
    assert client.patch(BASE+f'/videos/{video_id}',json=body).status_code==200
    assert client.post(BASE+f'/videos/{video_id}/publish').status_code==409
    assert run_one()
    assert client.get(BASE+f'/videos/{video_id}').json()['title']==body['title']
    with SessionLocal() as db:
        row=db.get(LearningVideo,video_id);row.status='processing'
        job=db.query(VideoJob).filter_by(key=f'source:{video_id}').one();job.status='processing';job.updated_at=datetime.now()-timedelta(hours=3);db.commit()
    assert not run_one()
    assert client.get(BASE+f'/videos/{video_id}').json()['status']=='failed'
    assert client.post(BASE+f'/videos/{video_id}/retry').status_code==200
    assert run_one()


def test_old_playback_caches_clean_without_deleting_sources(client,clip):
    video_id=ready(client,clip,'个人缓存清理验收');token=grant(client,video_id)
    with SessionLocal() as db:
        row=db.get(LearningVideo,video_id)
        key=media.playback_key(row,'SUPTEST01','720')
        path=media.playback_path(key);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'legacy personal cache')
        db.add(VideoJob(video_id=video_id,key='playback:'+key,kind='playback',status='ready',updated_at=datetime.now()-timedelta(days=8)));db.commit()
    assert media.playback_path(key).is_file()
    cleanup()
    assert not media.playback_path(key).is_file()
    assert (media.video_dir(video_id)/'source.mov').is_file()
    assert (media.video_dir(video_id)/'master-720.mp4').is_file()


def test_silent_rotated_mobile_video_keeps_orientation(client,clip,tmp_path):
    original=tmp_path/'original.mp4';original.write_bytes(clip)
    rotated=tmp_path/'rotated.mp4'
    subprocess.run([media.tool('ffmpeg'),'-nostdin','-y','-v','error','-display_rotation','90','-i',str(original),'-an','-c:v','copy',str(rotated)],check=True)
    info=media.probe(rotated)
    assert info['width']==180 and info['height']==320
    video_id=ready(client,rotated.read_bytes(),'无声竖屏方向验收')
    data=client.get(BASE+f'/videos/{video_id}').json()
    assert all(p['height']>p['width'] for p in data['profiles'].values())


@pytest.mark.parametrize('account', ['GSMTEST01', 'TAGSMTEST01', 'AMTEST01', 'OMTEST01'])
def test_senior_video_upload_all_circles_independent_of_announcement_grants(client, account):
    login(client, account)
    options = client.get(BASE+'/options').json()
    assert options['can_select_multiple'] and options['can_upload']
    assert len(options['upload_circles']) == len(options['circles']) == 3
    with SessionLocal() as db:
        employee_id = db.query(Employee.id).filter_by(employee_no=account).scalar()
        row = AnnouncementGrant(employee_id=employee_id, enabled=False, attraction_ids_json='[]',
                                starts_on=date.today().isoformat(), changed_by=employee_id,
                                reason='公告停发不影响视频授权')
        db.add(row); db.commit(); grant_id=row.id
    try:
        current=client.get(BASE+'/options').json()
        assert current['can_upload'] and len(current['upload_circles'])==3
    finally:
        with SessionLocal() as db:
            db.delete(db.get(AnnouncementGrant,grant_id));db.commit()


def test_lead_cannot_publish_other_circle_or_all(client,clip):
    login(client)
    options=client.get(BASE+'/options').json()
    assert options['can_upload'] and not options['can_select_multiple']
    assert len(options['upload_circles'])==1
    own=options['upload_circles'][0]['id']
    with SessionLocal() as db:
        other=db.query(Attraction.id).filter(Attraction.active.is_(True),Attraction.employee_circle.is_(True),Attraction.id!=own).first()[0]
    for extra in ({'attraction_id':other},{'attraction_ids':[own,other]},{'audience_codes':['all_tr']}):
        assert client.post(BASE+'/uploads',json=config(client,clip,**extra)).status_code==403
    assert client.post(BASE+'/uploads',json=config(client,clip,attraction_ids=[])).status_code==400


def test_targeted_tiers_union_live_edit_and_immediate_revocation(client,clip):
    login(client,'GSMTEST01')
    video_id=upload(client,clip,title='分层受众验收',audience_codes=['local_tr'])
    while run_one():pass
    assert client.post(BASE+f'/videos/{video_id}/publish').status_code==200
    for account,allowed in [('TRTEST01',True),('CMTEST01',False),('SUPTEST01',False),('TATEST01',False),('TAGSMTEST01',False),('AMTEST01',False),('OMTEST01',False)]:
        login(client,account)
        assert client.get(BASE+f'/videos/{video_id}').status_code == (200 if allowed else 404)
        notice=client.get(BASE+'/options').json()
        assert client.post(BASE+f'/videos/{video_id}/consent',json={'accepted':True,'notice_version':notice['notice_version']}).status_code==(200 if allowed else 404)
        listed=client.get(BASE+'/videos',params={'keyword':'分层受众验收'}).json()
        assert listed['total']==int(allowed)
    login(client,'TRTEST01');token=grant(client,video_id)
    assert client.post(BASE+f'/videos/{video_id}/playback',params={'token':token}).json()['status']=='ready'
    assert not run_one()
    login(client,'GSMTEST01')
    before=client.get(BASE+f'/videos/{video_id}').json()['audience']
    changed=client.patch(BASE+f'/videos/{video_id}/audience',json={'audience_codes':['local_lead','all_gsm','am','om'],'revision':before['revision']})
    assert changed.status_code==200
    assert client.patch(BASE+f'/videos/{video_id}/audience',json={'audience_codes':['all_tr'],'revision':before['revision']}).status_code==409
    login(client,'TRTEST01')
    # The cookie changed after obtaining token, but current policy must reject first.
    assert client.get(BASE+f'/videos/{video_id}/stream',params={'token':token}).status_code==404
    for account in ['SUPTEST01','TATEST01','TAGSMTEST01','AMTEST01','OMTEST01']:
        login(client,account);grant(client,video_id)
    login(client,'AMTEST01')
    assert client.patch(BASE+f'/videos/{video_id}/audience',json={'audience_codes':['all_tr'],'revision':changed.json()['revision']}).status_code==403
    login(client,'GSMTEST01')
    current=client.get(BASE+f'/videos/{video_id}').json()
    assert client.patch(BASE+f'/videos/{video_id}/audience',json={'audience_codes':['all_tr','all_lead'],'revision':current['audience']['revision']}).status_code==200
    assert client.get(BASE+'/videos',params={'keyword':'分层受众验收'}).json()['total']==1
    login(client,'TRTEST01')
    with SessionLocal() as db:
        tr=db.query(Employee).filter_by(employee_no='TRTEST01').one();tr_id=tr.id;own=tr.attraction_id
        other=db.query(Attraction.id).filter(Attraction.employee_circle.is_(True),Attraction.id!=own).first()[0]
        tr.attraction_id=other;db.commit()
    try:grant(client,video_id)
    finally:
        with SessionLocal() as db:db.get(Employee,tr_id).attraction_id=own;db.commit()


def test_lead_audience_limits_and_expired_proxy_recipient(client,clip):
    login(client)
    assert [r['code'] for r in client.get(BASE+'/options').json()['audience_options']]==['local_tr']
    for codes in [[],['local_lead'],['all_tr'],['all_gsm'],['am'],['om'],['local_all'],['all_people']]:
        assert client.post(BASE+'/uploads',json=config(client,clip,audience_codes=codes)).status_code in (400,403)
    video_id=ready(client,clip,'LEAD仅TR视频');client.post(BASE+f'/videos/{video_id}/publish')
    login(client,'CMTEST01')
    assert client.get(BASE+f'/videos/{video_id}').status_code==404
    login(client,'TRTEST01')
    assert not client.get(BASE+f'/videos/{video_id}').json()['can_edit_audience']
    assert client.patch(BASE+f'/videos/{video_id}/audience',json={'audience_codes':['all_tr'],'revision':1}).status_code==403
    login(client)
    assert client.patch(BASE+f'/videos/{video_id}/audience',json={'audience_codes':['local_lead'],'revision':1}).status_code==403
    assert client.patch(BASE+f'/videos/{video_id}/audience',json={'audience_codes':['local_tr'],'revision':1}).status_code==200


def test_audience_change_replacement_conflict_keeps_current_version(client,clip):
    login(client,'GSMTEST01')
    old=upload(client,clip,title='受众变更旧版',audience_codes=['all_tr'])
    while run_one():pass
    client.post(BASE+f'/videos/{old}/publish')
    new=upload(client,clip,title='受众变更新版',audience_codes=['all_tr'],replaces_id=old)
    while run_one():pass
    assert client.patch(BASE+f'/videos/{old}/audience',json={'audience_codes':['all_lead'],'revision':1}).status_code==200
    assert client.post(BASE+f'/videos/{new}/publish').status_code==409
    assert client.get(BASE+f'/videos/{old}').json()['status']=='published'
    assert client.patch(BASE+f'/videos/{new}/audience',json={'audience_codes':['all_lead'],'revision':1}).status_code==200
    assert client.post(BASE+f'/videos/{new}/publish').status_code==200
    assert client.get(BASE+f'/videos/{old}').json()['status']=='withdrawn'


def test_all_nine_audiences_against_actual_current_roles(client):
    accounts=['TRTEST01','CMTEST01','SUPTEST01','TATEST01','TAGSMTEST01','GSMTEST01','AMTEST01','OMTEST01']
    expected={'local_tr':{'TRTEST01'},'local_lead':{'SUPTEST01','TATEST01'},'all_tr':{'TRTEST01'},
              'all_lead':{'SUPTEST01','TATEST01'},'all_gsm':{'TAGSMTEST01','GSMTEST01'},'am':{'AMTEST01'},'om':{'OMTEST01'}}
    with SessionLocal() as db:
        owner=db.query(Employee).filter_by(employee_no='HR01').one()
        circle=db.query(Employee).filter_by(employee_no='TRTEST01').one().attraction_id
        expected['local_all']={e.employee_no for e in db.query(Employee).filter(Employee.employee_no.in_(accounts),Employee.attraction_id==circle)}
        expected['all_people']=set(accounts)
        videos={}
        for code in expected:
            video=LearningVideo(owner_id=owner.id,attraction_id=circle,title='隔离受众矩阵 '+code,
                                cover_title='矩阵',original_bytes=1,status='published')
            db.add(video);db.flush();videos[code]=video.id
            db.add(VideoAudiencePolicy(video_id=video.id,local_attraction_id=circle,codes=json.dumps([code])))
        db.commit()
    for account in accounts:
        login(client,account)
        for code,allowed in expected.items():
            assert client.get(BASE+f'/videos/{videos[code]}').status_code==(200 if account in allowed else 404),(account,code)


def test_audience_revocation_rejects_existing_same_session_media_token(client,clip):
    login(client,'GSMTEST01')
    id=upload(client,clip,title='同会话即时撤权',audience_codes=['all_tr'])
    while run_one():pass
    client.post(BASE+f'/videos/{id}/publish')
    viewer=TestClient(app)
    login(viewer,'TRTEST01');token=grant(viewer,id)
    assert viewer.post(BASE+f'/videos/{id}/playback',params={'token':token}).json()['status']=='ready'
    assert not run_one()
    assert viewer.get(BASE+f'/videos/{id}/stream',params={'token':token},headers={'Range':'bytes=0-99'}).status_code==206
    source_hash=hashlib.sha256((media.video_dir(id)/'source.mov').read_bytes()).hexdigest()
    assert client.patch(BASE+f'/videos/{id}/audience',json={'audience_codes':['all_lead'],'revision':1}).status_code==200
    assert viewer.get(BASE+f'/videos/{id}/stream',params={'token':token}).status_code==404
    assert viewer.get(BASE+f'/videos/{id}/playback',params={'token':token}).status_code==404
    assert viewer.put(BASE+f'/videos/{id}/progress',params={'token':token},json={'position':1}).status_code==404
    assert hashlib.sha256((media.video_dir(id)/'source.mov').read_bytes()).hexdigest()==source_hash
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(entity_type='video_learning',entity_id=str(id),action='调整学习视频观看范围').count()==1
    viewer.close()


def test_publisher_move_disables_publish_until_scope_is_reconfirmed(client,clip):
    id=ready(client,clip,'调动后按钮权限验收')
    with SessionLocal() as db:
        owner=db.query(Employee).filter_by(employee_no='SUPTEST01').one();owner_id=owner.id;original=owner.attraction_id
        other=db.query(Attraction.id).filter(Attraction.employee_circle.is_(True),Attraction.id!=original).first()[0]
        owner.attraction_id=other;db.commit()
    try:
        row=client.get(BASE+f'/videos/{id}').json()
        assert row['can_edit_audience'] and not row['can_publish'] and row['publish_block_reason']
        assert client.post(BASE+f'/videos/{id}/publish').status_code==403
        assert client.patch(BASE+f'/videos/{id}/audience',json={'audience_codes':['local_tr'],'revision':1}).status_code==200
        assert client.get(BASE+f'/videos/{id}').json()['can_publish']
        assert client.post(BASE+f'/videos/{id}/publish').status_code==200
    finally:
        with SessionLocal() as db:db.get(Employee,owner_id).attraction_id=original;db.commit()


def test_expired_acting_publisher_can_cancel_own_unfinished_bytes(client,clip):
    login(client,'TATEST01')
    response=client.post(BASE+'/uploads',json=config(client,clip,title='代理到期取消上传'))
    assert response.status_code==200
    url=BASE+'/uploads/'+response.json()['upload_id']
    with SessionLocal() as db:
        owner=db.query(Employee).filter_by(employee_no='TATEST01').one()
        duty=db.query(EmployeeActingDuty).filter_by(employee_id=owner.id).first()
        duty_id=duty.id;start,end=duty.starts_on,duty.ends_on
        duty.starts_on=(date.today()-timedelta(days=2)).isoformat();duty.ends_on=(date.today()-timedelta(days=1)).isoformat();db.commit()
    try:
        assert client.put(url,params={'offset':0},content=clip).status_code==403
        assert client.delete(url).status_code==200
        assert client.delete(url).status_code==404
    finally:
        with SessionLocal() as db:
            row=db.get(EmployeeActingDuty,duty_id);row.starts_on=start;row.ends_on=end;db.commit()


def test_admin_local_scope_edit_uses_publisher_circle_not_admin_circle(client,clip):
    id=ready(client,clip,'管理员本圈语义验收')
    own=client.get(BASE+f'/videos/{id}').json()['audience']['local_attraction_id']
    client.post('/api/logout')
    assert client.post('/api/login',json={'employee_no':'HR01','password':'HR123'}).status_code==200
    row=client.get(BASE+f'/videos/{id}').json()
    assert 'local_tr' in {r['code'] for r in row['audience_editor']['audience_options']}
    assert client.patch(BASE+f'/videos/{id}/audience',json={'audience_codes':['local_tr'],'revision':1}).status_code==200
    assert client.get(BASE+f'/videos/{id}').json()['audience']['local_attraction_id']==own
    assert client.post(BASE+f'/videos/{id}/publish').status_code==200


def test_everyone_scopes_cross_circle_cm_and_live_revocation(client, clip):
    login(client, 'GSMTEST01')
    options=client.get(BASE+'/options').json()
    assert {'local_all','all_people'} <= {r['code'] for r in options['audience_options']}
    video_id=upload(client,clip,title='所有人范围与转圈验收',audience_codes=['local_all'])
    while run_one():pass
    assert client.post(BASE+f'/videos/{video_id}/publish').status_code==200
    viewer=TestClient(app)
    try:
        login(viewer,'CMTEST01')
        assert viewer.get(BASE+f'/videos/{video_id}').status_code==200
        token=grant(viewer,video_id)
        assert viewer.post(BASE+f'/videos/{video_id}/playback',params={'token':token}).status_code==200
        while run_one():pass
        assert viewer.get(BASE+f'/videos/{video_id}/stream',params={'token':token},headers={'Range':'bytes=0-99'}).status_code==206
        with SessionLocal() as db:
            cm=db.query(Employee).filter_by(employee_no='CMTEST01').one();cm_id=cm.id;own=cm.attraction_id
            other=db.query(Attraction.id).filter_by(active=True,employee_circle=True).filter(Attraction.id!=own).first()[0]
            cm.attraction_id=other;db.commit()
        try:
            assert viewer.get(BASE+f'/videos/{video_id}').status_code==404
            assert client.patch(BASE+f'/videos/{video_id}/audience',json={'audience_codes':['all_people'],'revision':1}).status_code==200
            assert viewer.get(BASE+f'/videos/{video_id}').status_code==200
            listed=viewer.get(BASE+'/videos',params={'attraction_id':other,'keyword':'所有人范围与转圈验收'}).json()
            assert listed['total']==1
            assert client.patch(BASE+f'/videos/{video_id}/audience',json={'audience_codes':['all_tr'],'revision':2}).status_code==200
            assert viewer.get(BASE+f'/videos/{video_id}/stream',params={'token':token}).status_code==404
        finally:
            with SessionLocal() as db:db.get(Employee,cm_id).attraction_id=own;db.commit()
    finally:viewer.close()


def test_450mb_upload_initialization_is_allowed_not_a_100mb_cap(client,clip):
    # Metadata/reservation only: no claim of a real iPhone picker or 450MB transfer.
    login(client,'GSMTEST01')
    body=config(client,clip,title='450MB边界',audience_codes=['all_people'])
    response=client.post(BASE+'/uploads',json={**body,'size':450*1024*1024})
    assert response.status_code==200,response.text
    assert response.json()['offset']==0
    assert client.delete(BASE+'/uploads/'+response.json()['upload_id']).status_code==200


def test_shared_media_no_personal_jobs_and_live_authorization(client,clip):
    login(client,'GSMTEST01')
    id=upload(client,clip,title='共享播放不排个人队列',audience_codes=['all_people'])
    while run_one():pass
    assert client.post(BASE+f'/videos/{id}/publish').status_code==200
    expected=None
    for account in ['TRTEST01','CMTEST01','SUPTEST01','TAGSMTEST01']:
        login(client,account);token=grant(client,id)
        params={'token':token,'quality':'720'}
        assert client.post(BASE+f'/videos/{id}/playback',params=params).json()=={'status':'ready','mode':'shared'}
        info=client.get(BASE+f'/videos/{id}/authorize',params=params)
        assert info.status_code==200 and info.headers['cache-control']=='no-store'
        assert info.json()['employee_no']==account
        response=client.get(BASE+f'/videos/{id}/stream',params=params,headers={'Range':'bytes=0-99'})
        assert response.status_code==206
        if expected is None:expected=response.content
        else:assert response.content==expected
    with SessionLocal() as db:
        assert db.query(VideoJob).filter_by(video_id=id,kind='playback').count()==0
    login(client,'GSMTEST01');client.post(BASE+f'/videos/{id}/withdraw')
    login(client,'TRTEST01');assert client.get(BASE+f'/videos/{id}/authorize',params=params).status_code in (403,404)


def test_delete_only_withdrawn_version_preserves_history_and_access_audit(client,clip):
    old=ready(client,clip,'删除旧版保留关联');client.post(BASE+f'/videos/{old}/publish')
    token=grant(client,old)
    assert client.delete(BASE+f'/videos/{old}').status_code==409
    new=upload(client,clip,title='保留替代新版',replaces_id=old)
    while run_one():pass
    assert client.post(BASE+f'/videos/{new}/publish').status_code==200
    login(client,'GSMTEST01');assert client.delete(BASE+f'/videos/{old}').status_code==403
    login(client);assert client.delete(BASE+f'/videos/{old}').status_code==200
    for suffix in ('','/cover','/authorize?token='+token,'/stream?token='+token):
        assert client.get(BASE+f'/videos/{old}'+suffix).status_code==404
    assert all(item['id']!=old for item in client.get(BASE+'/videos',params={'mine':True}).json()['items'])
    assert run_one()
    assert not media.video_dir(old).exists()
    assert media.video_dir(new).is_dir()
    row=client.get(BASE+f'/videos/{new}').json()
    assert row['status']=='published' and row['replaces_id']==old
    assert client.patch(BASE+f'/videos/{new}/audience',json={'audience_codes':['local_tr'],'revision':1}).status_code==200
    with SessionLocal() as db:
        assert db.get(LearningVideo,old).status=='deleted'
        assert db.query(AuditLog).filter_by(entity_type='video_learning',entity_id=str(old),action='删除学习视频').count()==1
        assert db.query(VideoPlayback).filter_by(video_id=old).count()>=1


def test_delete_during_legacy_migration_cannot_resurrect(client,clip,monkeypatch):
    id=ready(client,clip,'旧视频升级与删除竞争');client.post(BASE+f'/videos/{id}/publish')
    with SessionLocal() as db:
        row=db.get(LearningVideo,id);profiles=json.loads(row.profiles)
        for p in profiles.values():p.pop('brand',None)
        row.profiles=json.dumps(profiles);db.commit()
    token=grant(client,id)
    assert client.post(BASE+f'/videos/{id}/playback',params={'token':token}).json()['status']=='queued'
    original=media.process_source
    def racing(video,stop=None):
        assert client.post(BASE+f'/videos/{id}/withdraw').status_code==200
        assert client.delete(BASE+f'/videos/{id}').status_code==200
        return original(video,stop)
    monkeypatch.setattr(media,'process_source',racing)
    assert run_one()
    with SessionLocal() as db:assert db.get(LearningVideo,id).status=='deleted'
    assert run_one()
    assert not media.video_dir(id).exists()
    assert client.get(BASE+f'/videos/{id}').status_code==404


def test_delete_cleanup_retries_and_removes_legacy_cache(client,clip,monkeypatch):
    id=ready(client,clip,'清理失败重试')
    with SessionLocal() as db:
        v=db.get(LearningVideo,id);key=media.playback_key(v,'SUPTEST01','720')
        path=media.playback_path(key);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'legacy')
        db.add(VideoJob(video_id=id,key='playback:'+key,kind='playback',status='ready'));db.commit()
    assert client.post(BASE+f'/videos/{id}/withdraw').status_code==200
    assert client.delete(BASE+f'/videos/{id}').status_code==200
    erase=media.erase_video_files
    monkeypatch.setattr(media,'erase_video_files',lambda _:(_ for _ in ()).throw(RuntimeError('暂时占用')))
    assert run_one()
    assert client.get(BASE+f'/videos/{id}').status_code==404
    monkeypatch.setattr(media,'erase_video_files',erase)
    cleanup();assert run_one()
    assert not media.video_dir(id).exists() and not path.exists()


def test_transferred_author_can_find_and_delete_own_withdrawn_history(client,clip):
    id=ready(client,clip,'作者转圈后删除历史')
    assert client.post(BASE+f'/videos/{id}/withdraw').status_code==200
    with SessionLocal() as db:
        author=db.query(Employee).filter_by(employee_no='SUPTEST01').one();author_id=author.id;old=author.attraction_id
        other=db.query(Attraction.id).filter_by(active=True,employee_circle=True).filter(Attraction.id!=old).first()[0]
        author.attraction_id=other;db.commit()
    try:
        records=client.get(BASE+'/videos',params={'mine':True,'keyword':'作者转圈后删除历史'}).json()
        assert records['total']==1 and records['items'][0]['can_delete']
        assert client.get(BASE+f'/videos/{id}').json()['can_delete']
        assert client.delete(BASE+f'/videos/{id}').status_code==200
        assert run_one() and not media.video_dir(id).exists()
    finally:
        with SessionLocal() as db:db.get(Employee,author_id).attraction_id=old;db.commit()
