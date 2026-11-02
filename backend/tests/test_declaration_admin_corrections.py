"""Isolated administrator reclassification and void restoration regressions."""
from decimal import Decimal
import json
import hashlib
import fitz
import pytest
from test_monthly_self_ranks import system
from app.routers import deductions, declaration_statistics
from app.v2_models import DeductionRecord, DeductionType, DeductionLevel, StoredFile, AuditLog, MonthClosure, ScoringPolicy
from app.v2_auth import V2User
from app.record_payloads import deduction_payload

@pytest.fixture
def setup(system, tmp_path, monkeypatch):
    db,client,circles,roles,make,switch,holder,admin=system
    client.app.include_router(deductions.router,prefix='/api')
    client.app.include_router(declaration_statistics.router,prefix='/api')
    person=make('target');types=db.query(DeductionType).all()
    level=db.query(DeductionLevel).filter_by(code='STATEMENT').one()
    monkeypatch.setattr(deductions, 'FILE_DIR', tmp_path)
    path=tmp_path/'test/correct.pdf';path.parent.mkdir()
    with fitz.open() as document:
        document.new_page();content=document.tobytes()
    path.write_bytes(content)
    file=StoredFile(storage_key='test/correct.pdf',original_filename='correct.pdf',extension='.pdf',mime_type='application/pdf',file_size=len(content),sha256=hashlib.sha256(content).hexdigest(),uploaded_by=admin.id,status='active')
    db.add(file);db.commit()
    def add(status='active',dtype=None,material='ready'):
        dtype=dtype or types[0]
        row=DeductionRecord(employee_id=person.id,employee_no=person.employee_no,employee_name=person.name,employee_role_snapshot='CM',attraction_id_snapshot=circles[0].id,deduction_type_id=dtype.id,deduction_type_name=dtype.name,deduction_level_id=level.id,deduction_level_name=level.name,points=Decimal('1'),occurred_on='2026-11-01',deduction_month='2026-11',description='original',document_file_id=file.id,submitter_id=admin.id,submitter_name=admin.name,submitter_role_snapshot='SYSTEM_ADMIN',permission_scope_snapshot='test',status=status,material_status=material)
        db.add(row);db.commit();return row
    row=add()
    body=dict(type_id=types[1].id,expected_type_id=row.deduction_type_id,expected_status=row.status,expected_level_id=row.deduction_level_id,reason='复查更正')
    return db,client,holder,admin,roles,types,row,body,add,person,file

def call(client,row,action,body):
    return (client.patch if action in {'type','level'} else client.post)(f'/api/deductions/{row.id}/{action}',json=body)

def restore_body(row):
    return dict(reason='复查还原',expected_type_id=row.deduction_type_id,expected_level_id=row.deduction_level_id,expected_voided_at=row.voided_at.isoformat() if row.voided_at else None)


def test_level_correction_cannot_bypass_repeat_policy(setup):
    db,client,_,_,_,types,row,_,add,*_=setup
    dtype=next(t for t in types if t.code=='SICK_LEAVE_VIOLATION')
    memo=db.query(DeductionLevel).filter_by(code='MEMO').one()
    statement=db.query(DeductionLevel).filter_by(code='STATEMENT').one()
    row.deduction_type_id=dtype.id;row.deduction_type_name=dtype.name
    row.deduction_level_id=memo.id;row.deduction_level_name=memo.name
    row.points=memo.points;db.commit();add(dtype=dtype)
    response=call(client,row,'level',dict(level_id=statement.id,expected_level_id=memo.id,reason='复查更正'))
    assert response.status_code==409,response.text
    db.refresh(row)
    assert row.deduction_level_id==memo.id and row.points==memo.points
    assert db.query(AuditLog).filter_by(action='修改处分级别').count()==0


@pytest.mark.parametrize('damage',['missing','empty','outside','directory'])
def test_restore_rejects_unavailable_physical_material_without_changing_score(setup,damage,tmp_path):
    db,client,_,_,_,_,row,_,_,_,file=setup
    assert call(client,row,'void',{'reason':'复查作废'}).status_code==200
    db.refresh(row);body=restore_body(row)
    path=deductions.FILE_DIR/file.storage_key
    if damage=='missing':path.unlink()
    elif damage=='empty':path.write_bytes(b'')
    elif damage=='directory':path.unlink();path.mkdir()
    else:
        outside=tmp_path.parent/(tmp_path.name+'-outside.pdf');outside.write_bytes(b'%PDF-1.4')
        file.storage_key=str(outside);db.commit()
    response=call(client,row,'restore',body)
    assert response.status_code==409,response.text
    db.refresh(row)
    assert row.status=='void' and deduction_payload(row)['actual_points']==0
    assert db.query(AuditLog).filter_by(action='还原扣分').count()==0

@pytest.mark.parametrize('role',['CM','TR','TA_SUPERVISOR','SUPERVISOR','TA_GSM','GSM','AM','OM','HR_ADMIN','HR_CIRCLE'])
def test_only_system_admin_corrects_or_restores(setup,role):
    db,client,holder,admin,roles,types,row,body,*_=setup
    holder['user']=V2User(admin,None,roles[role],set())
    for action in ['type','restore']:assert call(client,row,action,body).status_code==403
    assert db.get(DeductionRecord,row.id).deduction_type_id==body['expected_type_id']

def test_type_updates_filters_export_and_audit_without_changing_material(setup):
    db,client,holder,admin,roles,types,row,body,add,person,file=setup
    holder['user'].permissions.add('DECLARATION_STATS_VIEW')
    original=(row.document_file_id,row.description,row.employee_id,row.submitter_id,row.deduction_level_id)
    response=call(client,row,'type',body);assert response.status_code==200,response.text
    db.refresh(row)
    assert row.deduction_type_id==types[1].id and row.deduction_type_name==types[1].name
    assert row.points==1 and original==(row.document_file_id,row.description,row.employee_id,row.submitter_id,row.deduction_level_id)
    assert db.query(AuditLog).filter_by(action='修改处分类型').count()==1
    data=client.get('/api/declaration-statistics',params={'month':'2026-11','type_id':types[1].id}).json()
    assert [r['id'] for r in data['records']]==[row.id]
    from app.routers.declaration_statistics import build_declaration_workbook
    assert build_declaration_workbook(data)['逐条明细'].cell(2,5).value==types[1].name
    assert call(client,row,'type',body).status_code==409

@pytest.mark.parametrize('field,value',[('type_id',999999),('type_id',True),('expected_type_id',True),('reason',''),('reason','x'*501)])
def test_invalid_type_edit_never_writes(setup,field,value):
    db,client,_,_,_,_,row,body,*_=setup
    assert call(client,row,'type',{**body,field:value}).status_code==400
    db.refresh(row);assert row.deduction_type_id==body['expected_type_id']
    assert db.query(AuditLog).count()==0

@pytest.mark.parametrize('state,material',[('active','ready'),('pending_material','missing'),('material_failed','failed')])
def test_void_restore_preserves_original_state_and_material(setup,state,material):
    db,client,_,_,_,_,row,_,add,*_=setup
    row=add(state,material=material)
    assert call(client,row,'void',dict(reason='复查作废',expected_status=state)).status_code==200
    db.refresh(row);assert deduction_payload(row)['actual_points']==0
    restore=restore_body(row)
    assert call(client,row,'void',dict(reason='重复')).status_code==400
    response=call(client,row,'restore',restore);assert response.status_code==200,response.text
    db.refresh(row);assert row.status==state and row.material_status==material and row.voided_at is None
    assert deduction_payload(row)['actual_points']==(1 if state=='active' else 0)
    assert call(client,row,'restore',restore).status_code==409
    assert db.query(AuditLog).filter_by(action='还原扣分').count()==1
    assert call(client,row,'void',dict(reason='第二次作废')).status_code==200
    assert call(client,row,'restore',restore).status_code==409

@pytest.mark.parametrize('action',['type','void','restore'])
def test_closed_month_and_upgrade_sources_block_operations(setup,action):
    db,client,_,admin,_,_,row,body,*_=setup
    if action=='restore':
        assert call(client,row,'void',dict(reason='作废')).status_code==200
        db.refresh(row);body=restore_body(row)
    elif action=='void':body={'reason':'作废'}
    row.upgrade_role='source_first';db.commit()
    assert call(client,row,action,body).status_code==400
    row.upgrade_role=None
    db.add(MonthClosure(closure_month=row.deduction_month,attraction_id=row.attraction_id_snapshot,status='closed',closed_by=admin.id));db.commit()
    assert call(client,row,action,body).status_code==423

def test_restore_requires_original_state_and_ready_active_material(setup):
    db,client,_,_,_,_,row,_,_,_,file=setup
    assert call(client,row,'void',dict(reason='作废')).status_code==200
    db.refresh(row);body=restore_body(row)
    assert call(client,row,'restore',{**body,'expected_voided_at':'old'}).status_code==409
    file.status='deleted';db.commit()
    assert call(client,row,'restore',body).status_code==409
    file.status='active';row.voided_from_status=None;db.commit()
    assert call(client,row,'restore',body).status_code==400
    db.refresh(row);assert row.status=='void'

def test_reclassification_fixed_score_and_frozen_level_rates(setup):
    db,client,_,_,_,types,row,body,*_=setup
    row.scoring_policy_json=json.dumps({'mode':'level','repeat':'sum','group':[],'levels':{'STATEMENT':'2'}})
    db.add(ScoringPolicy(kind='deduction',scope='all',code=types[1].code,effective_date='2026-01-01',policy_json=json.dumps({'mode':'fixed','score':7,'repeat':'sum'})))
    db.commit()
    response=call(client,row,'type',body);assert response.status_code==200,response.text
    db.refresh(row);assert row.points==7 and json.loads(row.scoring_policy_json)['levels']['STATEMENT']=='2'

def test_non_admin_void_permission_remains_submitter_only(setup):
    db,client,holder,admin,roles,_,row,_,_,person,_=setup
    holder['user']=V2User(person,None,roles['CM'],set())
    assert call(client,row,'void',{'reason':'不允许'}).status_code==403
    holder['user']=V2User(admin,None,roles['GSM'],set())
    assert call(client,row,'void',{'reason':'原登记人作废'}).status_code==200

@pytest.mark.parametrize('state',['pending_material','material_failed','void'])
def test_type_correction_preserves_nonactive_state(setup,state):
    db,client,_,_,_,_,row,body,*_=setup
    row.status=state;db.commit()
    assert call(client,row,'type',{**body,'expected_status':state}).status_code==200
    db.refresh(row);assert row.status==state and deduction_payload(row)['actual_points']==0

@pytest.mark.parametrize('action',['type','void','restore'])
def test_hidden_upgrade_foreign_keys_cannot_be_bypassed(setup,action):
    from app.v2_models import DeductionUpgradeRequest
    db,client,_,admin,_,_,row,body,add,*_=setup
    if action=='restore':
        assert call(client,row,'void',{'reason':'test'}).status_code==200
        db.refresh(row);body=restore_body(row)
    elif action=='void':body={'reason':'test'}
    second=add()
    db.add(DeductionUpgradeRequest(employee_id=row.employee_id,deduction_type_id=row.deduction_type_id,first_deduction_id=row.id,second_deduction_id=second.id,reviewer_id=admin.id,reviewer_name=admin.name,submitted_by=admin.id,submitted_by_name=admin.name))
    db.commit()
    assert not row.upgrade_request_id and not row.upgrade_role
    assert call(client,row,action,body).status_code==400

@pytest.mark.parametrize('action',['type','restore'])
def test_repeat_controlled_category_cannot_bypass_upgrade(setup,action):
    db,client,_,_,_,types,row,body,add,*_=setup
    dtype=next(t for t in types if t.code=='SICK_LEAVE_VIOLATION')
    if action=='type':
        add(dtype=dtype);body={**body,'type_id':dtype.id}
    else:
        row.deduction_type_id=dtype.id;row.deduction_type_name=dtype.name;db.commit()
        assert call(client,row,'void',{'reason':'test'}).status_code==200
        db.refresh(row);body=restore_body(row);add(dtype=dtype)
    response=call(client,row,action,body)
    assert response.status_code==409,response.text
    db.refresh(row)
    assert row.status==('void' if action=='restore' else 'active')
    if action=='type':assert row.deduction_type_id==body['expected_type_id']

@pytest.mark.parametrize('change',['inactive','processing','stale_status','stale_level'])
def test_category_edit_lifecycle_boundaries(setup,change):
    db,client,_,_,_,types,row,body,*_=setup
    expected=400
    if change=='inactive':types[1].active=False
    elif change=='processing':row.status='material_processing'
    elif change=='stale_status':row.status='pending_material';expected=409
    else:row.deduction_level_id=db.query(DeductionLevel).filter_by(code='MEMO').one().id;expected=409
    db.commit();assert call(client,row,'type',body).status_code==expected


def test_restore_updates_real_score_aggregation(setup):
    from app.score_queries import employee_month_scores
    from app.v2_models import AttendanceMonthlyScore
    db,client,_,_,_,_,row,_,_,person,_=setup
    db.add(AttendanceMonthlyScore(employee_id=person.id,attendance_month='2026-11',eligible=True,final_score=Decimal('0')));db.commit()
    def score():return employee_month_scores(db,'2026-11',[person.id])[0]['deduction_score']
    assert score()==1;db.rollback()
    assert call(client,row,'void',{'reason':'test'}).status_code==200
    db.refresh(row);body=restore_body(row)
    assert score()==0;db.rollback()
    assert call(client,row,'restore',body).status_code==200
    assert score()==1

@pytest.mark.parametrize('action',['type','void','restore'])
def test_audit_failure_rolls_back_all_changes(setup,monkeypatch,action):
    db,client,_,_,_,_,row,body,*_=setup
    if action=='restore':
        assert call(client,row,'void',{'reason':'test'}).status_code==200
        db.refresh(row);body=restore_body(row)
    elif action=='void':body={'reason':'test'}
    original=(row.status,row.deduction_type_id,row.points,row.voided_at,row.scoring_policy_json)
    monkeypatch.setattr(deductions,'write_audit',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('audit failed')))
    assert call(client,row,action,body).status_code==500
    db.refresh(row);assert (row.status,row.deduction_type_id,row.points,row.voided_at,row.scoring_policy_json)==original


def test_old_restore_form_cannot_restore_reclassified_record(setup):
    db,client,_,_,_,_,row,body,*_=setup
    assert call(client,row,'void',{'reason':'test'}).status_code==200
    db.refresh(row);restore=restore_body(row)
    assert call(client,row,'type',{**body,'expected_status':'void'}).status_code==200
    assert call(client,row,'restore',restore).status_code==409
    db.refresh(row);assert row.status=='void'

@pytest.mark.parametrize('change',['type','status'])
def test_level_edit_rejects_other_administrator_changes(setup,change):
    db,client,_,_,_,types,row,_,*_=setup
    old_type=row.deduction_type_id
    if change=='type':row.deduction_type_id=types[1].id;row.deduction_type_name=types[1].name
    else:row.status='pending_material'
    db.commit()
    memo=db.query(DeductionLevel).filter_by(code='MEMO').one()
    response=call(client,row,'level',{'level_id':memo.id,'expected_level_id':row.deduction_level_id,'expected_type_id':old_type,'expected_status':'active','reason':'旧页面修改'})
    assert response.status_code==409,response.text


def test_category_void_restore_summary_export_and_related_absence_stay_consistent(setup):
    from app.v2_models import SickLeaveRecord
    from app.routers.declaration_statistics import build_declaration_workbook
    db,client,holder,admin,_,types,row,body,add,person,file=setup
    holder['user'].permissions.add('DECLARATION_STATS_VIEW')
    pending=add('pending_material',material='missing')
    leave=SickLeaveRecord(employee_id=person.id,attendance_month='2026-11',leave_start_date='2026-11-01',leave_end_date='2026-11-01',leave_days=Decimal('1'),charged_days=Decimal('1'),proof_file_id=file.id,leave_type='病假',import_source='manual',is_violation=True,violation_deduction_id=row.id,note='original leave',status='active',submitted_by=admin.id,submitted_by_name=admin.name)
    db.add(leave);db.commit();leave_id=leave.id
    before={col.name:getattr(leave,col.name) for col in SickLeaveRecord.__table__.columns}
    def summary(**filters):
        response=client.get('/api/declaration-statistics',params={'month':'2026-11',**filters})
        assert response.status_code==200,response.text
        data=response.json()
        assert data['total_records']==sum(level['count'] for level in data['levels'])
        assert data['total_records']==sum(data['statuses'].values())
        assert data['total_records']==sum(circle['record_count'] for circle in data['circles'])
        workbook=build_declaration_workbook(data)
        assert workbook['逐条明细'].max_row-1==data['total_records']
        return data
    assert summary()['total_records']==2
    assert call(client,row,'type',body).status_code==200
    classified=summary(type_id=body['type_id'])
    assert [r['id'] for r in classified['records']]==[row.id]
    assert build_declaration_workbook(classified)['逐条明细'].cell(2,5).value==types[1].name
    assert [r['id'] for r in summary(type_id=body['expected_type_id'])['records']]==[pending.id]
    assert call(client,row,'void',{'reason':'复查作废'}).status_code==200
    assert summary()['statuses']=={'已生效':0,'待补材料':1,'审核中':0,'已作废':1}
    db.refresh(row)
    assert call(client,row,'restore',restore_body(row)).status_code==200
    assert summary()['statuses']=={'已生效':1,'待补材料':1,'审核中':0,'已作废':0}
    after=db.get(SickLeaveRecord,leave_id);db.refresh(after)
    assert before=={col.name:getattr(after,col.name) for col in SickLeaveRecord.__table__.columns}
    assert db.get(DeductionRecord,pending.id).status=='pending_material'
