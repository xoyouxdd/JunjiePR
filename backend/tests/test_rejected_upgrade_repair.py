"""Rejected history survives manager/material re-pairing and concurrent claims."""
import importlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy import text
from test_upgrade_minimum_and_recovery import app,login,TestClient,setup_flow,SessionLocal,deduction_materials
from app.v2_models import DeductionRecord,DeductionUpgradeRequest,DeductionUpgradeTransfer,StoredFile,AuditLog,EmployeeLOAPeriod
from app.v2_database import FILE_DIR
from app.deduction_policy import deduction_counts_for_score
from app.score_queries import employee_month_scores


def rejected_flow(manager,reviewer,month):
    flow=setup_flow(manager,month,supplement=False)
    with SessionLocal() as db:wid=db.query(DeductionUpgradeRequest).filter_by(second_deduction_id=flow['sid']).one().id
    target=next(r for r in reviewer.get('/api/deduction-upgrades/reviewers').json()['items'] if r['employee_no']=='GSMTEST01')
    assert reviewer.post(f'/api/deduction-upgrades/{wid}/transfer',json={'reviewer_id':target['id'],'reason':'原工单转交历史'}).status_code==200
    rejected=manager.post(f'/api/deduction-upgrades/{wid}/resolve',json={'decision':'reject','handling_note':'原升级审核不通过，保留材料'})
    assert rejected.status_code==200,rejected.text
    flow['old_work']=wid;flow['manager_id']=target['id']
    with SessionLocal() as db:
        first=db.get(DeductionRecord,flow['first']);second=db.get(DeductionRecord,flow['sid'])
        assert first.status=='active' and first.upgrade_role is None and first.upgrade_request_id is None
        assert first.upgrade_state=='eligible' and second.status=='void'
        flow['immutable_first']=(first.description,first.points,first.occurred_on,first.document_file_id,first.submitter_id,first.submitted_at)
        flow['old_snapshot']={table:[tuple(row) for row in db.execute(text(f'SELECT * FROM {table} WHERE '+('id=:id' if table=='deduction_upgrade_requests' else 'request_id=:id')+' ORDER BY id'),{'id':wid})] for table in ('deduction_upgrade_requests','deduction_upgrade_transfers')}
        flow['old_second']=tuple(db.execute(text('SELECT * FROM deduction_records WHERE id=:id'),{'id':second.id}).one())
        flow['old_audit']=[tuple(row) for row in db.execute(text("SELECT * FROM audit_logs WHERE entity_type='deduction_upgrade' AND entity_id=:id ORDER BY id"),{'id':str(wid)})]
        flow['files']={}
        for fid in {first.document_file_id,second.document_file_id}:
            file=db.get(StoredFile,fid)
            flow['files'][fid]=(file.storage_key,file.sha256,(FILE_DIR/file.storage_key).read_bytes())
    return flow


def assert_history(db,flow):
    for table,original in flow['old_snapshot'].items():
        current=[tuple(row) for row in db.execute(text(f'SELECT * FROM {table} WHERE '+('id=:id' if table=='deduction_upgrade_requests' else 'request_id=:id')+' ORDER BY id'),{'id':flow['old_work']})]
        assert current==original
    assert tuple(db.execute(text('SELECT * FROM deduction_records WHERE id=:id'),{'id':flow['sid']}).one())==flow['old_second']
    assert [tuple(row) for row in db.execute(text("SELECT * FROM audit_logs WHERE entity_type='deduction_upgrade' AND entity_id=:id ORDER BY id"),{'id':str(flow['old_work'])})]==flow['old_audit']
    first=db.get(DeductionRecord,flow['first'])
    assert (first.description,first.points,first.occurred_on,first.document_file_id,first.submitter_id,first.submitted_at)==flow['immutable_first']
    for fid,(key,digest,content) in flow['files'].items():
        file=db.get(StoredFile,fid)
        assert file.status=='active' and file.storage_key==key and file.sha256==digest
        assert (FILE_DIR/key).read_bytes()==content


def new_second(manager,flow,date,supplement):
    form={**flow['form'],'occurred_on':date,'description':'新配对第二张声明'}
    if not supplement:
        return '/api/deduction-upgrades',{**form,'reviewer_id':flow['manager_id']},None
    pending=manager.post('/api/deductions',data=form)
    assert pending.status_code==200,pending.text
    sid=pending.json()['record']['id'];path=f'/api/deductions/{sid}/material'
    preview=manager.get(path+'-upgrade-preview')
    assert preview.status_code==200 and preview.json()['first_record']['id']==flow['first']
    return path,{'reviewer_id':flow['manager_id'],'expected_first_id':flow['first']},sid


def work_for(manager,flow,response,sid=None):
    assert response.status_code==200,response.text
    out=response.json()
    sid=sid or (out.get('record') or out['request']['second_record'])['id']
    if out.get('record',{}).get('material_status')=='processing':
        assert deduction_materials.process_next_deduction_material_job()
    with SessionLocal() as db:
        work=db.query(DeductionUpgradeRequest).filter_by(second_deduction_id=sid).one()
        assert work.first_deduction_id==flow['first'] and work.status=='pending'
        return work.id,sid


@pytest.mark.parametrize('supplement,loa,fail,month',[(False,False,False,'2031-01'),(True,False,False,'2032-01'),(True,True,False,'2033-01'),(True,False,True,'2037-01')])
def test_rejected_work_repair_preserves_scores_history_exports_and_material_permissions(monkeypatch,supplement,loa,fail,month):
    monkeypatch.setattr(importlib.import_module('app.main'),'start_deduction_material_worker',lambda:None)
    with TestClient(app) as manager,TestClient(app) as reviewer,TestClient(app) as unrelated:
        login(manager,'GSMTEST01');login(reviewer,'TAGSMTEST01');login(unrelated,'CMTEST02')
        f=rejected_flow(manager,reviewer,month)
        new_month=month[:5]+'02' if loa else month
        path,data,sid=new_second(manager,f,new_month+'-07',supplement)
        response=manager.post(path,data=data,files=f['pdf'])
        assert response.status_code==200,response.text
        failed_work=None
        if fail:
            def fail_conversion(*args):raise deduction_materials.MaterialError('repair_retry_test','重新配对后材料失败')
            with monkeypatch.context() as broken:
                broken.setattr(deduction_materials,'_make_compressed_pdf',fail_conversion)
                assert deduction_materials.process_next_deduction_material_job()
            with SessionLocal() as db:
                row=db.get(DeductionRecord,sid);failed_work=row.upgrade_request_id
                assert row.status=='material_failed'
                assert db.get(DeductionUpgradeRequest,failed_work).status=='awaiting_material'
                assert_history(db,f)
            response=manager.post(path,files=f['pdf'])
        wid,sid=work_for(manager,f,response,sid)
        if fail:assert wid==failed_work
        with SessionLocal() as db:
            assert_history(db,f)
            assert db.query(DeductionUpgradeRequest).filter_by(first_deduction_id=f['first']).count()==2
            assert db.get(DeductionRecord,f['first']).upgrade_request_id==wid
            if loa:
                db.add(EmployeeLOAPeriod(employee_id=f['form']['employee_id'],starts_on=new_month+'-20',ends_on=new_month+'-20',status='ended',created_by=f['manager_id'],created_by_name='经理'));db.commit()
        approved=manager.post(f'/api/deduction-upgrades/{wid}/resolve',json=dict(decision='approve',issued_confirmed=True,result_level_id=f['levels']['MEMO']['id'],handling_note='新工单真实开具备忘录'))
        assert approved.status_code==200,approved.text
        assert manager.post(f"/api/deduction-upgrades/{f['old_work']}/resolve",json=dict(decision='approve',issued_confirmed=True,result_level_id=f['levels']['MEMO']['id'],handling_note='不得处理旧工单')).status_code==404
        with SessionLocal() as db:
            assert_history(db,f)
            result=db.get(DeductionRecord,db.get(DeductionUpgradeRequest,wid).result_deduction_id)
            result_id=result.id
            rows=db.query(DeductionRecord).filter_by(employee_id=f['form']['employee_id']).filter(DeductionRecord.id.in_([f['first'],f['sid'],sid,result.id])).all()
            assert {r.id for r in rows if deduction_counts_for_score(r)}=={f['first'],result_id}
            assert float(sum(r.points for r in rows if deduction_counts_for_score(r)))==f['levels']['STATEMENT']['points']+f['levels']['MEMO']['points']
            if loa:
                assert employee_month_scores(db,new_month,[f['form']['employee_id']])[0]['deduction_score']==0
                assert float(db.get(DeductionRecord,f['first']).points)==f['levels']['STATEMENT']['points']
        params={'month':new_month,'keyword':'CMTEST01'}
        stats=manager.get('/api/declaration-statistics',params=params).json()
        expected=1 if loa else 3
        assert stats['total_records']==expected
        linked={r['id']:r for r in stats['related_records']}
        assert {link['request_id'] for link in linked[f['first']]['upgrade_links']}=={f['old_work'],wid}
        assert linked[f['sid']]['raw_status']=='void'
        assert linked[sid]['actual_points']==0
        if loa:assert linked[result_id]['actual_points']==0 and not linked[f['first']]['loa_excluded']
        result_only=manager.get('/api/declaration-statistics',params={**params,'level_id':f['levels']['MEMO']['id']}).json()
        assert {r['id'] for r in result_only['related_records']}=={f['first'],f['sid'],sid,result_id}
        exported=manager.get('/api/declaration-statistics/export',params=params)
        assert exported.status_code==200
        workbook=load_workbook(BytesIO(exported.content))
        assert workbook.worksheets[0]['B2'].value==expected and workbook.worksheets[1].max_row-1==expected
        for fid in f['files']:
            assert manager.get(f'/api/files/{fid}',params={'preview':'true'}).status_code==200
            assert unrelated.get(f'/api/files/{fid}',params={'preview':'true'}).status_code==403


@pytest.mark.parametrize('mode,month',[('manager','2034-01'),('material','2035-01'),('mixed','2036-01')])
def test_concurrent_repair_has_one_active_pair_and_can_reject_and_repair_again(monkeypatch,mode,month):
    monkeypatch.setattr(importlib.import_module('app.main'),'start_deduction_material_worker',lambda:None)
    with TestClient(app) as manager,TestClient(app) as other,TestClient(app) as reviewer:
        login(manager,'GSMTEST01');login(other,'GSMTEST01');login(reviewer,'TAGSMTEST01')
        f=rejected_flow(manager,reviewer,month)
        attempts=[new_second(manager,f,month+'-07',mode=='material'),new_second(manager,f,month+'-08',mode!='manager')]
        gate=Barrier(2,timeout=15)
        def submit(client,attempt):
            gate.wait();return client.post(attempt[0],data=attempt[1],files=f['pdf'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(submit,client,attempt) for client,attempt in zip((manager,other),attempts)]
            responses=[future.result(timeout=40) for future in futures]
        assert sorted(r.status_code for r in responses)==[200,409],[r.text for r in responses]
        winner=next(i for i,r in enumerate(responses) if r.status_code==200)
        wid,sid=work_for(manager,f,responses[winner],attempts[winner][2])
        with SessionLocal() as db:
            assert_history(db,f)
            assert db.query(DeductionUpgradeRequest).filter(DeductionUpgradeRequest.first_deduction_id==f['first'],DeductionUpgradeRequest.status!='rejected').count()==1
        assert manager.post(f'/api/deduction-upgrades/{wid}/resolve',json={'decision':'reject','handling_note':'第二次不通过，继续保留历史'}).status_code==200
        path,data,new_sid=new_second(manager,f,month+'-09',True)
        response=manager.post(path,data=data,files=f['pdf']);last_id,new_sid=work_for(manager,f,response,new_sid)
        assert manager.post(f'/api/deduction-upgrades/{last_id}/resolve',json=dict(decision='approve',issued_confirmed=True,result_level_id=f['levels']['MEMO']['id'],handling_note='最终确认')).status_code==200
        with SessionLocal() as db:
            assert_history(db,f)
            works=db.query(DeductionUpgradeRequest).filter_by(first_deduction_id=f['first']).order_by(DeductionUpgradeRequest.id).all()
            assert [w.status for w in works]==['rejected','rejected','approved']
            assert sum(w.result_deduction_id is not None for w in works)==1
