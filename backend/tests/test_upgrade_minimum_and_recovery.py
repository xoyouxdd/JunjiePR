"""Real local upgrade flows: minimum levels and restricted assignee recovery."""
import importlib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from PIL import Image
import pytest
from test_material_upgrade_entry import app,login,make_pdf,TestClient,SessionLocal,deduction_materials
from app.v2_models import DeductionRecord,DeductionUpgradeRequest,DeductionUpgradeTransfer,UserAccount,AuditLog,MonthClosure,DeductionMaterialJob
from app.deduction_policy import deduction_counts_for_score
from io import BytesIO
from openpyxl import load_workbook


def setup_flow(manager,month,*,supplement=True,higher=False,actor=None,photos=False):
    options=manager.get('/api/options').json()
    target=next(r for r in manager.get('/api/employee-targets?usage=deduction&keyword=CMTEST01').json()['items'] if r['employee_no']=='CMTEST01')
    kind=next(r for r in options['deduction_types'] if r['code']=='SICK_LEAVE_VIOLATION')
    levels={r['code']:r for r in options['deduction_levels']}
    form=dict(employee_id=target['id'],deduction_type_id=kind['id'],deduction_level_id=levels['STATEMENT']['id'],occurred_on=month+'-01',description='保留原声明内容')
    pdf={'document':('proof.pdf',make_pdf(),'application/pdf')}
    first=manager.post('/api/deductions',data=form,files=pdf)
    assert first.status_code==200,first.text
    if higher:
        result=manager.post('/api/deductions',data={**form,'occurred_on':month+'-02','deduction_level_id':levels['MEMO']['id'],'repeat_confirmed':'true'},files=pdf)
        assert result.status_code==200,result.text
    actor=actor or manager
    form['occurred_on']=month+'-05'
    if supplement:
        pending=actor.post('/api/deductions',data=form)
        assert pending.status_code==200,pending.text
        sid=pending.json()['record']['id'];path=f'/api/deductions/{sid}/material'
        preview=actor.get(path+'-upgrade-preview')
    else:
        path='/api/deduction-upgrades'
        preview=actor.get(path+'/preview',params={k:form[k] for k in ('employee_id','deduction_type_id','occurred_on')})
    assert preview.status_code==200,preview.text
    p=preview.json();reviewer=next(r for r in p['reviewers'] if r['employee_no']=='TAGSMTEST01')
    assert p['minimum_level_id']==levels['MEMO']['id']
    data={'reviewer_id':reviewer['id'],'expected_first_id':p['first_record']['id']} if supplement else {**form,'reviewer_id':reviewer['id']}
    if photos:
        image=BytesIO();Image.new('RGB',(12,12),'white').save(image,format='JPEG')
        files={'document_images':('photo.jpg',image.getvalue(),'image/jpeg')}
    else:files=pdf
    submitted=actor.post(path,data=data,files=files)
    assert submitted.status_code==200,submitted.text
    out=submitted.json()
    if not supplement:sid=(out.get('record') or out['request']['second_record'])['id']
    return dict(sid=sid,first=first.json()['record']['id'],levels=levels,reviewer=reviewer,month=month,form=form,pdf=pdf)


@pytest.mark.parametrize('supplement,higher,month',[(False,True,'2041-01'),(True,True,'2041-05'),(False,False,'2041-09'),(True,False,'2042-01')])
def test_only_statement_pair_matters_during_preview_entry_and_approval(monkeypatch,supplement,higher,month):
    monkeypatch.setattr(importlib.import_module('app.main'),'start_deduction_material_worker',lambda:None)
    with TestClient(app) as manager,TestClient(app) as reviewer:
        login(manager,'GSMTEST01');login(reviewer,'TAGSMTEST01')
        f=setup_flow(manager,month,supplement=supplement,higher=higher)
        if supplement:assert deduction_materials.process_next_deduction_material_job()
        if not higher:
            submitted=manager.post('/api/deductions',data={**f['form'],'occurred_on':month+'-02','deduction_level_id':f['levels']['MEMO']['id'],'repeat_confirmed':'true'},files=f['pdf'])
            assert submitted.status_code==200,submitted.text
        with SessionLocal() as db:
            work=db.query(DeductionUpgradeRequest).filter_by(second_deduction_id=f['sid']).one();wid=work.id
            before=sum(r.points for r in db.query(DeductionRecord).filter_by(deduction_month=month) if deduction_counts_for_score(r))
        task=next(r for r in reviewer.get('/api/deduction-upgrades/pending').json()['items'] if r['id']==wid)
        assert task['minimum_level_id']==f['levels']['MEMO']['id']
        decision=dict(decision='approve',issued_confirmed=True,handling_note='真实开具',result_level_id=f['levels']['MEMO']['id'])
        approved=reviewer.post(f'/api/deduction-upgrades/{wid}/resolve',json=decision)
        assert approved.status_code==200,approved.text
        with SessionLocal() as db:
            result=db.get(DeductionRecord,db.get(DeductionUpgradeRequest,wid).result_deduction_id)
            assert float(result.points)==f['levels']['MEMO']['points']
            assert sum(r.points for r in db.query(DeductionRecord).filter_by(deduction_month=month) if deduction_counts_for_score(r))==before+result.points
        assert reviewer.post(f'/api/deduction-upgrades/{wid}/resolve',json=decision).status_code==404


@pytest.mark.parametrize('phase,lead,month', [('pending',False,'2043-01'),('processing',True,'2043-05'),('failed',False,'2043-09'),('fresh-processing',False,'2044-01'),('fresh-failed',False,'2044-05')])
def test_invalid_reviewer_recovery_preserves_record_material_and_audit(monkeypatch,phase,lead,month):
    monkeypatch.setattr(importlib.import_module('app.main'),'start_deduction_material_worker',lambda:None)
    with TestClient(app) as manager,TestClient(app) as actor,TestClient(app) as outsider,TestClient(app) as admin:
        login(manager,'GSMTEST01');login(actor,'TATEST01' if lead else 'GSMTEST01');login(outsider,'CMTEST02');login(admin,'HR01','HR123')
        fresh=phase.startswith('fresh-');phase=phase.removeprefix('fresh-')
        f=setup_flow(manager,month,actor=actor,photos=True,supplement=not fresh)
        if phase=='failed':
            def fail(*args):raise deduction_materials.MaterialError('test','失败重试测试')
            with monkeypatch.context() as broken:
                broken.setattr(deduction_materials,'_make_pdf',fail)
                assert deduction_materials.process_next_deduction_material_job()
        elif phase=='pending':assert deduction_materials.process_next_deduction_material_job()
        with SessionLocal() as db:
            row=db.get(DeductionRecord,f['sid']);work=db.query(DeductionUpgradeRequest).filter_by(second_deduction_id=row.id).one()
            wid=work.id;status=work.status;old_id=work.reviewer_id
            original=(row.id,row.description,row.points,row.document_file_id,row.submitter_id)
            revision=row.material_revision;job_id=row.material_job_id
            db.query(UserAccount).filter_by(employee_id=old_id).one().enabled=False;db.commit()
        tasks=actor.get('/api/deduction-upgrades/recovery').json()
        task=next(r for r in tasks['items'] if r['id']==wid)
        assert not task['reviewer_available']
        assert outsider.get('/api/deduction-upgrades/recovery').json()['items']==[]
        target=next(r for r in tasks['reviewers'] if r['employee_no']=='GSMTEST01')
        payload=dict(reviewer_id=target['id'],expected_reviewer_id=old_id,expected_status=status,reason='原审核经理停用，接手处理')
        path=f'/api/deduction-upgrades/{wid}/recover'
        assert outsider.post(path,json=payload).status_code==403
        assert actor.post(path,json={**payload,'reason':''}).status_code==400
        assert actor.post(path,json={**payload,'expected_reviewer_id':-1}).status_code==409
        assert actor.post(path,json={**payload,'reviewer_id':old_id}).status_code==400
        with SessionLocal() as db:
            row=db.get(DeductionRecord,f['sid'])
            db.add(MonthClosure(closure_month=month,attraction_id=row.attraction_id_snapshot,status='closed'));db.commit()
        assert actor.post(path,json=payload).status_code==423
        with SessionLocal() as db:
            db.query(MonthClosure).filter_by(closure_month=month).delete();db.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=[pool.submit(client.post,path,json=payload) for client in (actor,admin)]
            codes=sorted(r.result(timeout=30).status_code for r in results)
        assert codes==[200,409]
        with SessionLocal() as db:
            row=db.get(DeductionRecord,f['sid']);work=db.get(DeductionUpgradeRequest,wid)
            assert (row.id,row.description,row.points,row.document_file_id,row.submitter_id)==original
            assert row.material_revision==revision and row.material_job_id==job_id
            assert work.reviewer_id==target['id'] and work.status==status
            assert db.query(DeductionUpgradeTransfer).filter_by(request_id=wid).count()==1
            assert db.query(AuditLog).filter_by(action='重新指派失效升级审核经理',entity_id=str(wid)).count()==1
            if phase=='processing':
                # A stale conversion snapshot must not overwrite the recovered assignee.
                job=db.get(DeductionMaterialJob,job_id);job.reviewer_id=old_id
                deduction_materials._finalize_upgrade(db,job,row)
                assert db.get(DeductionUpgradeRequest,wid).reviewer_id==target['id']
                db.rollback()
        if phase=='failed':
            retry=actor.post(f"/api/deductions/{f['sid']}/material",files=f['pdf'])
            assert retry.status_code==200,retry.text
        if phase!='pending':assert deduction_materials.process_next_deduction_material_job()
        assert wid not in {r['id'] for r in actor.get('/api/deduction-upgrades/recovery').json()['items']}
        assert wid in {r['id'] for r in manager.get('/api/deduction-upgrades/pending').json()['items']}
        approved=manager.post(f'/api/deduction-upgrades/{wid}/resolve',json=dict(decision='approve',result_level_id=f['levels']['MEMO']['id'],issued_confirmed=True,handling_note='接手后真实开具'))
        assert approved.status_code==200,approved.text
        assert actor.post(path,json=payload).status_code==409
        with SessionLocal() as db:
            db.query(UserAccount).filter_by(employee_id=old_id).one().enabled=True;db.commit()


def test_legacy_failed_job_can_reselect_invalid_reviewer_without_new_declaration(monkeypatch):
    monkeypatch.setattr(importlib.import_module('app.main'),'start_deduction_material_worker',lambda:None)
    with TestClient(app) as manager:
        login(manager,'GSMTEST01');f=setup_flow(manager,'2045-01',supplement=False,photos=True)
        with SessionLocal() as db:
            row=db.get(DeductionRecord,f['sid']);first=db.get(DeductionRecord,f['first']);wid=row.upgrade_request_id
            row.upgrade_request_id=first.upgrade_request_id=None
            db.query(DeductionUpgradeRequest).filter_by(id=wid).delete();db.commit()
        def fail(*args):raise deduction_materials.MaterialError('test','legacy failure')
        with monkeypatch.context() as broken:
            broken.setattr(deduction_materials,'_make_pdf',fail)
            assert deduction_materials.process_next_deduction_material_job()
        with SessionLocal() as db:
            db.query(UserAccount).filter_by(employee_id=f['reviewer']['id']).one().enabled=False;db.commit()
        path=f"/api/deductions/{f['sid']}/material"
        p=manager.get(path+'-upgrade-preview').json()
        assert p['eligible'] and p['recover_legacy']
        data={'reviewer_id':p['default_reviewer_id'],'expected_first_id':f['first'],'recovery_reason':'原经理停用，恢复旧任务'}
        assert manager.post(path,data={**data,'recovery_reason':''},files=f['pdf']).status_code==400
        submitted=manager.post(path,data=data,files=f['pdf'])
        assert submitted.status_code==200,submitted.text
        assert deduction_materials.process_next_deduction_material_job()
        with SessionLocal() as db:
            row=db.get(DeductionRecord,f['sid']);work=db.get(DeductionUpgradeRequest,row.upgrade_request_id)
            assert work.reviewer_id==p['default_reviewer_id'] and work.status=='pending'
            assert db.query(DeductionRecord).filter_by(deduction_month='2045-01').count()==2
            assert db.query(AuditLog).filter_by(action='重新指派失效升级审核经理',entity_id=str(work.id)).count()==1
            db.query(UserAccount).filter_by(employee_id=f['reviewer']['id']).one().enabled=True;db.commit()


def test_existing_warning_does_not_escalate_pending_statement_pair(monkeypatch):
    monkeypatch.setattr(importlib.import_module('app.main'),'start_deduction_material_worker',lambda:None)
    with TestClient(app) as manager,TestClient(app) as reviewer:
        login(manager,'GSMTEST01');login(reviewer,'TAGSMTEST01')
        f=setup_flow(manager,'2046-01',supplement=False)
        warning=manager.post('/api/deductions',data={**f['form'],'occurred_on':'2046-01-06','deduction_level_id':f['levels']['WARNING_1']['id']},files=f['pdf'])
        assert warning.status_code==200,warning.text
        with SessionLocal() as db:wid=db.query(DeductionUpgradeRequest).filter_by(second_deduction_id=f['sid']).one().id
        approved=reviewer.post(f'/api/deduction-upgrades/{wid}/resolve',json=dict(decision='approve',issued_confirmed=True,result_level_id=f['levels']['MEMO']['id'],handling_note='按实际开具备忘录'))
        assert approved.status_code==200,approved.text


def test_completed_results_and_used_sources_do_not_chain_into_new_pair(monkeypatch):
    monkeypatch.setattr(importlib.import_module('app.main'),'start_deduction_material_worker',lambda:None)
    with TestClient(app) as manager:
        login(manager,'GSMTEST01')
        opts=manager.get('/api/options').json();levels={r['code']:r for r in opts['deduction_levels']}
        target=next(r for r in manager.get('/api/employee-targets?usage=deduction&keyword=CMTEST02').json()['items'] if r['employee_no']=='CMTEST02')
        kind=next(r for r in opts['deduction_types'] if r['code']=='SICK_LEAVE_VIOLATION')
        form=dict(employee_id=target['id'],deduction_type_id=kind['id'],deduction_level_id=levels['STATEMENT']['id'],occurred_on='2047-01-01',description='独立历史处分')
        pdf={'document':('proof.pdf',make_pdf(),'application/pdf')}
        history=[]
        for code in ('MEMO','WARNING_1','WARNING_2'):
            response=manager.post('/api/deductions',data={**form,'deduction_level_id':levels[code]['id']},files=pdf)
            assert response.status_code==200,response.text
            history.append(response.json()['record']['id'])
        pairs=[]
        for number in (0,1):
            first_data={**form,'occurred_on':f'2047-01-{5+number*10:02d}','description':'新的首次声明'}
            preview=manager.get('/api/deduction-upgrades/preview',params={k:first_data[k] for k in ('employee_id','deduction_type_id','occurred_on')})
            assert preview.status_code==200 and not preview.json()['eligible']
            first=manager.post('/api/deductions',data=first_data,files=pdf)
            assert first.status_code==200,first.text
            first_id=first.json()['record']['id']
            second=manager.post('/api/deductions',data={**first_data,'occurred_on':f'2047-01-{6+number*10:02d}','description':'待补材料的第二张'})
            assert second.status_code==200,second.text
            sid=second.json()['record']['id'];path=f'/api/deductions/{sid}/material'
            p=manager.get(path+'-upgrade-preview').json()
            assert p['eligible'] and p['first_record']['id']==first_id
            submitted=manager.post(path,data={'reviewer_id':p['default_reviewer_id'],'expected_first_id':first_id},files=pdf)
            assert submitted.status_code==200,submitted.text
            assert deduction_materials.process_next_deduction_material_job()
            with SessionLocal() as db:wid=db.query(DeductionUpgradeRequest).filter_by(second_deduction_id=sid).one().id
            approved=manager.post(f'/api/deduction-upgrades/{wid}/resolve',json=dict(decision='approve',issued_confirmed=True,result_level_id=levels['MEMO']['id'],handling_note='实际开具备忘录'))
            assert approved.status_code==200,approved.text
            pairs.append((first_id,sid,wid))
        with SessionLocal() as db:
            rows=db.query(DeductionRecord).filter_by(employee_id=target['id'],deduction_month='2047-01').all()
            assert len(rows)==9
            assert sum(float(r.points) for r in rows if deduction_counts_for_score(r))==sum(levels[c]['points'] for c in ('MEMO','WARNING_1','WARNING_2'))+2*levels['STATEMENT']['points']+2*levels['MEMO']['points']
            assert all(db.get(DeductionRecord,rid).upgrade_role is None for rid in history)
            for first_id,sid,wid in pairs:
                assert db.get(DeductionRecord,first_id).upgrade_role=='source_first'
                assert db.get(DeductionRecord,sid).upgrade_role=='source_second'
                assert db.get(DeductionUpgradeRequest,wid).first_deduction_id==first_id
        params={'month':'2047-01','keyword':'CMTEST02'}
        stats=manager.get('/api/declaration-statistics',params=params).json()
        assert stats['total_records']==7 and len(stats['detail_records'])==9
        exported=manager.get('/api/declaration-statistics/export',params=params)
        assert exported.status_code==200
        wb=load_workbook(BytesIO(exported.content))
        assert wb.worksheets[0]['B2'].value==7 and wb.worksheets[1].max_row-1==7
