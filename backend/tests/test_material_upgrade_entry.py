"""Real local API/worker flows for manager entries and original-record upgrades."""
import importlib
from io import BytesIO

from PIL import Image
import pytest
from fastapi.testclient import TestClient

from test_deduction_material_concurrency import app, login, make_pdf
from app import deduction_materials
from app.v2_database import SessionLocal
from app.v2_models import DeductionRecord, DeductionUpgradeRequest, MonthClosure, DeductionMaterialJob, StoredFile


@pytest.mark.parametrize('supplement,other,photo,failure,month',[
    (False,False,False,False,'2060-01'),
    (False,True,True,False,'2060-05'),
    (True,False,False,False,'2060-09'),
    (True,True,True,True,'2061-01'),
])
def test_manager_upgrade_end_to_end(monkeypatch,supplement,other,photo,failure,month):
    monkeypatch.setattr(importlib.import_module('app.main'),'start_deduction_material_worker',lambda:None)
    with TestClient(app) as manager,TestClient(app) as alternate:
        login(manager,'GSMTEST01');login(alternate,'TAGSMTEST01')
        options=manager.get('/api/options').json()
        target=next(r for r in manager.get('/api/employee-targets?usage=deduction&keyword=CMTEST01').json()['items'] if r['employee_no']=='CMTEST01')
        kind=next(r for r in options['deduction_types'] if r['code']=='SICK_LEAVE_VIOLATION')
        statement=next(r for r in options['deduction_levels'] if r['code']=='STATEMENT')
        memo=next(r for r in options['deduction_levels'] if r['code']=='MEMO')
        form=dict(employee_id=target['id'],deduction_type_id=kind['id'],deduction_level_id=statement['id'],occurred_on=month+'-01',description='原登记文字')
        pdf=make_pdf()
        first=manager.post('/api/deductions',data=form,files={'document':('first.pdf',pdf,'application/pdf')})
        assert first.status_code==200,first.text
        first_id=first.json()['record']['id']
        form['occurred_on']=month+'-05'
        if supplement:
            second=manager.post('/api/deductions',data=form)
            assert second.status_code==200,second.text
            second_id=second.json()['record']['id']
            endpoint=f'/api/deductions/{second_id}/material'
            preview=manager.get(endpoint+'-upgrade-preview').json()
        else:
            endpoint='/api/deduction-upgrades'
            preview=manager.get('/api/deduction-upgrades/preview',params={k:form[k] for k in ('employee_id','deduction_type_id','occurred_on')}).json()
        assert preview['eligible'] and preview['first_record']['id']==first_id
        reviewer=next(r for r in preview['reviewers'] if r['employee_no']==('TAGSMTEST01' if other else 'GSMTEST01'))
        assert preview['default_reviewer_id']==next(r['id'] for r in preview['reviewers'] if r['employee_no']=='GSMTEST01')
        data={'reviewer_id':reviewer['id'],'expected_first_id':first_id} if supplement else {**form,'reviewer_id':reviewer['id']}
        image=BytesIO();Image.new('RGB',(16,16),'white').save(image,format='JPEG')
        files={'document_images':('photo.jpg',image.getvalue(),'image/jpeg')} if photo else {'document':('proof.pdf',pdf,'application/pdf')}
        if supplement:
            data['expected_operator_id']=manager.get('/api/me').json()['id']
            with SessionLocal() as db:
                file_count=db.query(StoredFile).count()
                revision=db.get(DeductionRecord,second_id).material_revision
            changed_actor=alternate.post(endpoint,data=data,files=files)
            assert changed_actor.status_code==409
            assert changed_actor.json()['detail']['code']=='MATERIAL_OPERATOR_CHANGED'
            with SessionLocal() as db:
                assert db.query(StoredFile).count()==file_count
                assert db.get(DeductionRecord,second_id).material_revision==revision
                assert db.query(DeductionMaterialJob).filter_by(deduction_id=second_id).count()==0
            bad=manager.post(endpoint,data={**data,'expected_first_id':first_id+999},files=files)
            assert bad.status_code==409
            invalid_reviewer=manager.post(endpoint,data={**data,'reviewer_id':target['id']},files=files)
            assert invalid_reviewer.status_code==400
            with SessionLocal() as db:assert db.get(DeductionRecord,second_id).status=='pending_material'
        submitted=manager.post(endpoint,data=data,files=files)
        assert submitted.status_code==200,submitted.text
        if not supplement:
            second_id=(submitted.json().get('record') or submitted.json()['request']['second_record'])['id']
        else:
            with SessionLocal() as db:
                reserved=db.query(DeductionUpgradeRequest).filter_by(second_deduction_id=second_id).one()
                reserved_id=reserved.id
                assert reserved.status=='awaiting_material'
                assert float(db.get(DeductionRecord,second_id).points)==statement['points']
            selected=alternate if other else manager
            assert reserved_id not in {r['id'] for r in selected.get('/api/deduction-upgrades/pending').json()['items']}
        if photo or supplement:
            if failure:
                def fail(*args):raise deduction_materials.MaterialError('test','test failure')
                with monkeypatch.context() as broken:
                    broken.setattr(deduction_materials,'_make_pdf',fail)
                    assert deduction_materials.process_next_deduction_material_job()
                with SessionLocal() as db:
                    row=db.get(DeductionRecord,second_id);original_work=row.upgrade_request_id
                    assert row.status=='material_failed'
                retry=manager.post(f'/api/deductions/{second_id}/material',files={'document':('retry.pdf',pdf,'application/pdf')})
                assert retry.status_code==200,retry.text
            assert deduction_materials.process_next_deduction_material_job()
            if failure:
                with SessionLocal() as db:assert db.get(DeductionRecord,second_id).upgrade_request_id==original_work
        with SessionLocal() as db:
            work=db.query(DeductionUpgradeRequest).filter_by(second_deduction_id=second_id).one()
            wid=work.id
            assert work.reviewer_id==reviewer['id'] and work.status=='pending'
            row=db.get(DeductionRecord,second_id)
            assert row.description=='原登记文字' and row.submitter_name==db.get(DeductionRecord,first_id).submitter_name
            assert row.status=='pending_upgrade' and row.upgrade_role=='source_second'
            assert db.query(DeductionRecord).filter_by(employee_id=target['id'],deduction_month=month).count()==2
        selected=alternate if other else manager
        unselected=manager if other else alternate
        assert wid in {r['id'] for r in selected.get('/api/deduction-upgrades/pending').json()['items']}
        assert wid not in {r['id'] for r in unselected.get('/api/deduction-upgrades/pending').json()['items']}
        decision=dict(decision='approve',result_level_id=memo['id'],issued_confirmed=True,handling_note='真实开具备忘录')
        assert unselected.post(f'/api/deduction-upgrades/{wid}/resolve',json=decision).status_code==404
        approved=selected.post(f'/api/deduction-upgrades/{wid}/resolve',json=decision)
        assert approved.status_code==200,approved.text
        assert selected.post(f'/api/deduction-upgrades/{wid}/resolve',json=decision).status_code==404


def test_closed_month_blocks_material_preview_conversion_and_manager_upgrade(monkeypatch):
    monkeypatch.setattr(importlib.import_module('app.main'),'start_deduction_material_worker',lambda:None)
    with TestClient(app) as manager:
        login(manager,'GSMTEST01')
        opts=manager.get('/api/options').json()
        target=next(r for r in manager.get('/api/employee-targets?usage=deduction&keyword=CMTEST02').json()['items'] if r['employee_no']=='CMTEST02')
        kind=next(r for r in opts['deduction_types'] if r['code']=='SICK_LEAVE_VIOLATION')
        level=next(r for r in opts['deduction_levels'] if r['code']=='STATEMENT')
        form=dict(employee_id=target['id'],deduction_type_id=kind['id'],deduction_level_id=level['id'],occurred_on='2062-01-01',description='月结测试')
        pending=manager.post('/api/deductions',data=form).json()['record']
        with SessionLocal() as db:
            row=db.get(DeductionRecord,pending['id']);revision=row.material_revision
            db.add(MonthClosure(closure_month='2062-01',attraction_id=row.attraction_id_snapshot,status='closed'));db.commit()
        path=f"/api/deductions/{pending['id']}/material"
        assert manager.get(path+'-upgrade-preview').status_code==423
        assert manager.post(path,files={'document':('proof.pdf',make_pdf(),'application/pdf')}).status_code==423
        assert manager.post('/api/deduction-upgrades',data={**form,'reviewer_id':1},files={'document':('proof.pdf',make_pdf(),'application/pdf')}).status_code==423
        with SessionLocal() as db:
            row=db.get(DeductionRecord,pending['id'])
            assert row.material_revision==revision and row.status=='pending_material'
            assert db.query(DeductionMaterialJob).filter_by(deduction_id=row.id).count()==0


@pytest.mark.parametrize('code,month',[('MEMO','2063-01'),('WARNING_1','2063-05'),('WARNING_2','2063-09')])
def test_result_history_alone_does_not_upgrade_statement_or_expand_permissions(monkeypatch,code,month):
    monkeypatch.setattr(importlib.import_module('app.main'),'start_deduction_material_worker',lambda:None)
    with TestClient(app) as manager,TestClient(app) as unrelated:
        login(manager,'GSMTEST01');login(unrelated,'CMTEST02')
        opts=manager.get('/api/options').json()
        target=next(r for r in manager.get('/api/employee-targets?usage=deduction&keyword=CMTEST01').json()['items'] if r['employee_no']=='CMTEST01')
        kind=next(r for r in opts['deduction_types'] if r['code']=='SICK_LEAVE_VIOLATION')
        levels={r['code']:r['id'] for r in opts['deduction_levels']}
        form=dict(employee_id=target['id'],deduction_type_id=kind['id'],deduction_level_id=levels[code],occurred_on=month+'-01',description='已有独立处分')
        first=manager.post('/api/deductions',data=form,files={'document':('first.pdf',make_pdf(),'application/pdf')})
        assert first.status_code==200,first.text
        second=manager.post('/api/deductions',data={**form,'occurred_on':month+'-05','deduction_level_id':levels['STATEMENT']})
        assert second.status_code==200,second.text
        rid=second.json()['record']['id'];path=f'/api/deductions/{rid}/material'
        assert unrelated.get(path+'-upgrade-preview').status_code==403
        assert unrelated.post(path,files={'document':('proof.pdf',make_pdf(),'application/pdf')}).status_code==403
        assert unrelated.post('/api/deduction-upgrades',data={**form,'reviewer_id':1},files={'document':('proof.pdf',make_pdf(),'application/pdf')}).status_code==403
        preview=manager.get(path+'-upgrade-preview')
        assert preview.status_code==200 and not preview.json()['eligible']
        assert manager.post(path,data={'reviewer_id':1,'expected_first_id':first.json()['record']['id']},files={'document':('proof.pdf',make_pdf(),'application/pdf')}).status_code==409
        ordinary=manager.post(path,files={'document':('proof.pdf',make_pdf(),'application/pdf')})
        assert ordinary.status_code==200,ordinary.text
        assert deduction_materials.process_next_deduction_material_job()
        with SessionLocal() as db:
            assert db.get(DeductionRecord,rid).status=='active'
            assert db.get(DeductionRecord,rid).upgrade_role is None
            assert db.query(DeductionUpgradeRequest).filter_by(second_deduction_id=rid).count()==0
            assert db.query(DeductionMaterialJob).filter_by(deduction_id=rid).count()==1
