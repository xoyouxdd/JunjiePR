"""Real local upgrade flows: minimum levels and restricted assignee recovery."""
import importlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
from threading import Event

from PIL import Image
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_material_upgrade_entry import app,login,make_pdf,TestClient,SessionLocal,deduction_materials,ensure_second_gsm,SECOND_GSM
from app.v2_database import FILE_DIR
from app.month_closure import month_close_work_queries
from app.v2_models import DeductionRecord,DeductionUpgradeNoticeDismissal,DeductionUpgradeRequest,DeductionUpgradeTransfer,UserAccount,AuditLog,MonthClosure,DeductionMaterialJob
from app.v2_models import EmployeeActingDuty, Permission, Role, RolePermission, StoredFile
from app.deduction_policy import deduction_counts_for_score
from io import BytesIO
from openpyxl import load_workbook


def setup_flow(manager,month,*,supplement=True,higher=False,actor=None,registrar=None,photos=False):
    ensure_second_gsm()
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
        pending=(registrar or actor).post('/api/deductions',data=form)
        assert pending.status_code==200,pending.text
        sid=pending.json()['record']['id'];path=f'/api/deductions/{sid}/material'
        preview=actor.get(path+'-upgrade-preview')
    else:
        path='/api/deduction-upgrades'
        preview=actor.get(path+'/preview',params={k:form[k] for k in ('employee_id','deduction_type_id','occurred_on')})
    assert preview.status_code==200,preview.text
    p=preview.json();reviewer=next(r for r in p['reviewers'] if r['employee_no']==SECOND_GSM)
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


@pytest.mark.parametrize("decision,month", [("approve", "2040-01"), ("reject", "2040-05")])
def test_assisted_upgrade_notice_is_private_and_can_be_closed(monkeypatch, decision, month):
    monkeypatch.setattr(importlib.import_module("app.main"), "start_deduction_material_worker", lambda: None)
    with TestClient(app) as helper, TestClient(app) as owner, TestClient(app) as ta, TestClient(app) as outsider:
        login(helper, "GSMTEST01")
        login(ta, "TAGSMTEST01")
        login(outsider, "CMTEST02")
        flow = setup_flow(helper, month, supplement=False)
        login(owner, SECOND_GSM)
        owner_id = owner.get("/api/me").json()["id"]
        with SessionLocal() as db:
            work = db.query(DeductionUpgradeRequest).filter_by(second_deduction_id=flow["sid"]).one()
            request_id = work.id
            second = db.get(DeductionRecord, flow["sid"])
            file_id, circle_id = second.document_file_id, second.attraction_id_snapshot
        pending_path = "/api/deduction-upgrades/pending"
        resolve_path = f"/api/deduction-upgrades/{request_id}/resolve"
        dismiss_path = f"/api/deduction-upgrades/{request_id}/dismiss-notice"
        for gsm in (helper, owner):
            item = next(row for row in gsm.get(pending_path).json()["items"] if row["id"] == request_id)
            assert item["reviewer_id"] == owner_id
        assert request_id not in {row["id"] for row in helper.get(pending_path+"?mine_only=true").json()["items"]}
        payload = {"decision": decision, "handling_note": "协助核对两张声明",
                   "result_level_id": flow["levels"]["MEMO"]["id"], "issued_confirmed": True}
        for other in (ta, outsider):
            assert other.get(pending_path).status_code == 403
            assert other.post(resolve_path, json=payload).status_code == 403
            assert other.post(dismiss_path).status_code == 403
        assert owner.post(dismiss_path).status_code == 404
        for client, expected in ((helper, 200), (owner, 200), (ta, 200), (outsider, 403)):
            assert client.get(f"/api/files/{file_id}?preview=true").status_code == expected
        with SessionLocal() as db:
            db.add(MonthClosure(closure_month=month, attraction_id=circle_id, status="closed"))
            db.commit()
        assert helper.post(resolve_path, json=payload).status_code == 423
        with SessionLocal() as db:
            db.query(MonthClosure).filter_by(closure_month=month, attraction_id=circle_id).delete()
            db.commit()
        result = helper.post(resolve_path, json=payload)
        assert result.status_code == 200, result.text
        expected_status = "approved" if decision == "approve" else "rejected"
        for gsm in (helper, owner):
            assert request_id not in {row["id"] for row in gsm.get(pending_path).json()["items"]}
        notice = next(row for row in owner.get(pending_path).json()["notices"] if row["id"] == request_id)
        assert notice["resolved_by_name"] == helper.get("/api/me").json()["name"]
        assert notice["status"] == expected_status and notice["resolved_at"]
        assert notice["result_level"] == ("备忘录" if decision == "approve" else "")
        assert request_id not in {row["id"] for row in helper.get(pending_path).json()["notices"]}
        assert helper.post(dismiss_path).status_code == 404
        for _ in range(2):
            assert owner.post(dismiss_path).status_code == 200
        login(owner, SECOND_GSM)
        assert request_id not in {row["id"] for row in owner.get(pending_path).json()["notices"]}
        with SessionLocal() as db:
            work = db.get(DeductionUpgradeRequest, request_id)
            assert work.reviewer_id == owner_id and work.status == expected_status
            assert work.resolved_by_name == notice["resolved_by_name"]
            assert db.get(DeductionUpgradeNoticeDismissal, request_id).dismissed_at
            assert db.query(AuditLog).filter_by(action="处理声明升级工单", entity_id=str(request_id)).count() == 1
            assert db.get(DeductionRecord, flow["sid"]).document_file_id == file_id


@pytest.mark.parametrize('supplement,higher,month',[(False,True,'2041-01'),(True,True,'2041-05'),(False,False,'2041-09'),(True,False,'2042-01')])
def test_only_statement_pair_matters_during_preview_entry_and_approval(monkeypatch,supplement,higher,month):
    monkeypatch.setattr(importlib.import_module('app.main'),'start_deduction_material_worker',lambda:None)
    with TestClient(app) as manager,TestClient(app) as reviewer:
        ensure_second_gsm()
        login(manager,'GSMTEST01');login(reviewer,SECOND_GSM)
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
        assert reviewer.post(f'/api/deduction-upgrades/{wid}/resolve',json=decision).status_code==409


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


@pytest.mark.parametrize("ta_is_registrar,month", [(True, "2046-01"), (False, "2046-05")])
def test_ta_gsm_can_recover_own_upgrade_only_while_awaiting_material(monkeypatch, ta_is_registrar, month):
    monkeypatch.setattr(importlib.import_module("app.main"), "start_deduction_material_worker", lambda: None)
    with TestClient(app) as gsm, TestClient(app) as ta, TestClient(app) as outsider:
        login(gsm, "GSMTEST01")
        login(ta, "TAGSMTEST01")
        login(outsider, "CMTEST02")
        gsm_id = gsm.get("/api/me").json()["id"]
        ta_id = ta.get("/api/me").json()["id"]
        flow = setup_flow(gsm, month, actor=gsm if ta_is_registrar else ta,
                          registrar=ta if ta_is_registrar else gsm, photos=True)
        second_id, first_id = flow["sid"], flow["first"]
        with SessionLocal() as db:
            second = db.get(DeductionRecord, second_id)
            work = db.get(DeductionUpgradeRequest, second.upgrade_request_id)
            work_id, old_reviewer = work.id, work.reviewer_id
            circle = second.attraction_id_snapshot
            original = (second.document_file_id, second.material_revision, second.material_job_id, second.points)
            assert work.status == "awaiting_material"
            assert (second.submitter_id, work.submitted_by) == ((ta_id, gsm_id) if ta_is_registrar else (gsm_id, ta_id))
        recovery = "/api/deduction-upgrades/recovery"
        pending = "/api/deduction-upgrades/pending"
        recover_path = f"/api/deduction-upgrades/{work_id}/recover"
        resolve_path = f"/api/deduction-upgrades/{work_id}/resolve"
        decision = {"decision": "approve", "result_level_id": flow["levels"]["MEMO"]["id"],
                    "issued_confirmed": True, "handling_note": "核对材料并开具备忘录"}
        reassignment = {"reviewer_id": gsm_id, "expected_reviewer_id": old_reviewer,
                        "expected_status": "awaiting_material", "reason": "原审核人已停用"}
        assert work_id not in {item["id"] for item in ta.get(recovery).json()["items"]}
        assert ta.post(recover_path, json=reassignment).status_code == 409
        with SessionLocal() as db:
            db.query(UserAccount).filter_by(employee_id=old_reviewer).one().enabled = False
            db.commit()

        tasks = ta.get(recovery).json()
        assert next(item for item in tasks["items"] if item["id"] == work_id)["status"] == "awaiting_material"
        assert {reviewer["role_code"] for reviewer in tasks["reviewers"]} == {"GSM"}
        assert gsm_id in {reviewer["id"] for reviewer in tasks["reviewers"]}
        assert outsider.post(recover_path, json=reassignment).status_code == 403
        assert ta.get(pending).status_code == 403
        assert ta.post(resolve_path, json=decision).status_code == 403
        for invalid in (ta_id, old_reviewer):
            assert ta.post(recover_path, json={**reassignment, "reviewer_id": invalid}).status_code == 400

        with SessionLocal() as db:
            db.add(MonthClosure(closure_month=month, attraction_id=circle, status="closed"))
            db.commit()
        assert ta.post(recover_path, json=reassignment).status_code == 423
        with SessionLocal() as db:
            assert db.get(DeductionUpgradeRequest, work_id).reviewer_id == old_reviewer
            db.query(MonthClosure).filter_by(closure_month=month, attraction_id=circle).delete()
            db.commit()
        recovered = ta.post(recover_path, json=reassignment)
        assert recovered.status_code == 200, recovered.text
        with SessionLocal() as db:
            second = db.get(DeductionRecord, second_id)
            assert (second.document_file_id, second.material_revision, second.material_job_id, second.points) == original
            assert db.get(DeductionMaterialJob, second.material_job_id).reviewer_id == gsm_id
            assert db.query(DeductionUpgradeTransfer).filter_by(request_id=work_id).count() == 1
            assert db.query(AuditLog).filter_by(action="重新指派失效升级审核经理", entity_id=str(work_id)).count() == 1

        assert deduction_materials.process_next_deduction_material_job()
        with SessionLocal() as db:
            work = db.get(DeductionUpgradeRequest, work_id)
            assert work.status == "pending" and work.reviewer_id == gsm_id
            first_points = db.get(DeductionRecord, first_id).points
            second = db.get(DeductionRecord, second_id)
            assert not deduction_counts_for_score(second)
            material_id = second.document_file_id
            db.query(UserAccount).filter_by(employee_id=old_reviewer).one().enabled = True
            db.query(UserAccount).filter_by(employee_id=gsm_id).one().enabled = False
            db.commit()
        assert work_id not in {item["id"] for item in ta.get(recovery).json()["items"]}
        ready_reassignment = {**reassignment, "reviewer_id": old_reviewer,
                              "expected_reviewer_id": gsm_id, "expected_status": "pending"}
        assert ta.post(recover_path, json=ready_reassignment).status_code == 403
        assert ta.get(pending).status_code == 403
        assert ta.post(resolve_path, json=decision).status_code == 403
        assert ta.get(f"/api/files/{material_id}?preview=true").status_code == 200
        assert outsider.get(f"/api/files/{material_id}?preview=true").status_code == 403
        with SessionLocal() as db:
            assert db.get(DeductionUpgradeRequest, work_id).reviewer_id == gsm_id
            db.query(UserAccount).filter_by(employee_id=gsm_id).one().enabled = True
            db.commit()
        approved = gsm.post(resolve_path, json=decision)
        assert approved.status_code == 200, approved.text
        with SessionLocal() as db:
            work = db.get(DeductionUpgradeRequest, work_id)
            result = db.get(DeductionRecord, work.result_deduction_id)
            scores = [row.points for row in db.query(DeductionRecord).filter_by(deduction_month=month) if deduction_counts_for_score(row)]
            assert sum(scores) == first_points + result.points
            assert db.query(DeductionRecord).filter_by(deduction_month=month, upgrade_role="result").count() == 1


@pytest.mark.parametrize("change,month", [("acting_role", "2047-01"), ("permission", "2047-05")])
def test_recovery_checks_current_authority_after_waiting_for_writer(monkeypatch, change, month):
    monkeypatch.setattr(importlib.import_module("app.main"), "start_deduction_material_worker", lambda: None)
    with TestClient(app) as gsm, TestClient(app) as actor:
        login(gsm, "GSMTEST01")
        login(actor, "SUPTEST01")
        actor_info = actor.get("/api/me").json()
        assert actor_info["role_code"] == "SUPERVISOR"
        flow = setup_flow(gsm, month, actor=actor, photos=True)
        assert deduction_materials.process_next_deduction_material_job()
        with SessionLocal() as db:
            second = db.get(DeductionRecord, flow["sid"])
            work = db.get(DeductionUpgradeRequest, second.upgrade_request_id)
            work_id, original_reviewer = work.id, work.reviewer_id
            assert work.status == "pending" and work.submitted_by == actor_info["id"]
            db.query(UserAccount).filter_by(employee_id=original_reviewer).one().enabled = False
            db.commit()
        assert work_id in {item["id"] for item in actor.get("/api/deduction-upgrades/recovery").json()["items"]}
        body = {"reviewer_id": gsm.get("/api/me").json()["id"], "expected_reviewer_id": original_reviewer,
                "expected_status": "pending", "reason": "原审核人已停用"}
        request_at_lock, allow_write = Event(), Event()
        execute = Session.execute

        def pause_at_write_lock(session, statement, *args, **kwargs):
            if str(statement).strip() == "BEGIN IMMEDIATE" and not request_at_lock.is_set():
                request_at_lock.set()
                assert allow_write.wait(10)
            return execute(session, statement, *args, **kwargs)

        duty_id = None
        removed_grant = None
        try:
            with monkeypatch.context() as paused, ThreadPoolExecutor(max_workers=1) as pool:
                paused.setattr(Session, "execute", pause_at_write_lock)
                queued = pool.submit(actor.post, f"/api/deduction-upgrades/{work_id}/recover", json=body)
                try:
                    assert request_at_lock.wait(10)
                    with SessionLocal() as db:
                        if change == "acting_role":
                            ta_gsm = db.query(Role).filter_by(code="TA_GSM").one()
                            duty = EmployeeActingDuty(employee_id=actor_info["id"], role_id=ta_gsm.id,
                                                      starts_on=date.today().isoformat(), status="active", reason="并发切换角色")
                            db.add(duty)
                            db.flush()
                            duty_id = duty.id
                        else:
                            role = db.query(Role).filter_by(code="SUPERVISOR").one()
                            permission = db.query(Permission).filter_by(code="DEDUCTION_DIRECT").one()
                            grant = db.query(RolePermission).filter_by(role_id=role.id, permission_id=permission.id).one()
                            removed_grant = (grant.role_id, grant.permission_id)
                            db.delete(grant)
                        db.commit()
                finally:
                    allow_write.set()
                response = queued.result(timeout=15)

            assert response.status_code == 403, response.text
            with SessionLocal() as db:
                work = db.get(DeductionUpgradeRequest, work_id)
                assert work.status == "pending" and work.reviewer_id == original_reviewer
                assert db.query(DeductionUpgradeTransfer).filter_by(request_id=work_id).count() == 0
                assert db.query(AuditLog).filter_by(action="重新指派失效升级审核经理", entity_id=str(work_id)).count() == 0
        finally:
            with SessionLocal() as db:
                if duty_id is not None:
                    db.delete(db.get(EmployeeActingDuty, duty_id))
                if removed_grant is not None:
                    db.add(RolePermission(role_id=removed_grant[0], permission_id=removed_grant[1]))
                db.query(UserAccount).filter_by(employee_id=original_reviewer).one().enabled = True
                db.commit()


@pytest.mark.parametrize("action,month", [("resolve", "2048-01"), ("dismiss-notice", "2048-05")])
def test_revoked_gsm_cannot_audit_or_dismiss_after_waiting_for_writer(monkeypatch, action, month):
    monkeypatch.setattr(importlib.import_module("app.main"), "start_deduction_material_worker", lambda: None)
    with TestClient(app) as helper, TestClient(app) as owner:
        login(helper, "GSMTEST01")
        flow = setup_flow(helper, month, supplement=False)
        login(owner, SECOND_GSM)
        with SessionLocal() as db:
            work = db.query(DeductionUpgradeRequest).filter_by(second_deduction_id=flow["sid"]).one()
            work_id = work.id
        pending_path = "/api/deduction-upgrades/pending"
        path = f"/api/deduction-upgrades/{work_id}/{action}"
        if action == "resolve":
            client = helper
            assert work_id in {row["id"] for row in client.get(pending_path).json()["items"]}
            body = {"decision": "approve", "handling_note": "已核对材料", "issued_confirmed": True,
                    "result_level_id": flow["levels"]["MEMO"]["id"]}
        else:
            rejected = helper.post(f"/api/deduction-upgrades/{work_id}/resolve",
                                   json={"decision": "reject", "handling_note": "协助审核不通过"})
            assert rejected.status_code == 200, rejected.text
            client = owner
            assert work_id in {row["id"] for row in client.get(pending_path).json()["notices"]}
            body = {}

        at_write_lock = Event()
        execute = Session.execute

        def signal_write_lock(session, statement, *args, **kwargs):
            if str(statement).strip() == "BEGIN IMMEDIATE":
                at_write_lock.set()
            return execute(session, statement, *args, **kwargs)

        with SessionLocal() as revoker:
            # Authentication sees the old grant until the revocation commits.
            revoker.execute(text("BEGIN IMMEDIATE"))
            role = revoker.query(Role).filter_by(code="GSM").one()
            permission = revoker.query(Permission).filter_by(code="DEDUCTION_ALL").one()
            grant = revoker.query(RolePermission).filter_by(role_id=role.id, permission_id=permission.id).one()
            role_id, permission_id = grant.role_id, grant.permission_id
            revoker.delete(grant)
            revoker.flush()
            try:
                with monkeypatch.context() as concurrent, ThreadPoolExecutor(max_workers=1) as pool:
                    concurrent.setattr(Session, "execute", signal_write_lock)
                    queued = pool.submit(client.post, path, json=body)
                    try:
                        assert at_write_lock.wait(10)
                    finally:
                        revoker.commit()
                    response = queued.result(timeout=15)
                assert response.status_code == 403, response.text
            finally:
                with SessionLocal() as db:
                    db.add(RolePermission(role_id=role_id, permission_id=permission_id))
                    db.commit()

        with SessionLocal() as db:
            work = db.get(DeductionUpgradeRequest, work_id)
            assert db.get(DeductionUpgradeNoticeDismissal, work_id) is None
            if action == "resolve":
                assert work.status == "pending" and work.result_deduction_id is None
                assert db.query(DeductionRecord).filter_by(deduction_month=month, upgrade_role="result").count() == 0
                assert db.query(AuditLog).filter_by(action="处理声明升级工单", entity_id=str(work_id)).count() == 0
            else:
                assert work.status == "rejected"
        if action == "dismiss-notice":
            assert work_id in {row["id"] for row in owner.get(pending_path).json()["notices"]}


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

        def snapshot(db):
            first = db.get(DeductionRecord, f["first"])
            second = db.get(DeductionRecord, f["sid"])
            return (
                (first.status, first.upgrade_state, first.upgrade_request_id),
                (second.status, second.material_status, second.material_revision, second.document_file_id,
                 second.material_job_id, second.upgrade_request_id, second.points),
                db.query(DeductionUpgradeRequest).count(), db.query(DeductionUpgradeTransfer).count(),
                db.query(DeductionMaterialJob).count(), db.query(StoredFile).count(), db.query(AuditLog).count(),
            )

        with SessionLocal() as db:
            before = snapshot(db)
        files_before = set(FILE_DIR.iterdir())
        routes = importlib.import_module("app.routers.deductions")
        stage = routes.stage_pdf_material
        staging, resume = Event(), Event()

        async def pause_before_staging(*args, **kwargs):
            staging.set()
            assert resume.wait(10)
            return await stage(*args, **kwargs)

        removed_grant = None
        try:
            with monkeypatch.context() as paused, ThreadPoolExecutor(max_workers=1) as pool:
                paused.setattr(routes, "stage_pdf_material", pause_before_staging)
                waiting = pool.submit(manager.post, path, data=data, files=f["pdf"])
                try:
                    assert staging.wait(10)
                    with SessionLocal() as db:
                        role = db.query(Role).filter_by(code="GSM").one()
                        permission = db.query(Permission).filter_by(code="DEDUCTION_ALL").one()
                        grant = db.query(RolePermission).filter_by(role_id=role.id, permission_id=permission.id).one()
                        removed_grant = (grant.role_id, grant.permission_id)
                        db.delete(grant)
                        db.commit()
                finally:
                    resume.set()
                revoked = waiting.result(timeout=15)
            assert revoked.status_code == 403, revoked.text
            assert revoked.json()["detail"] == "没有重新指派原审核经理的权限"
            with SessionLocal() as db:
                assert snapshot(db) == before
            assert set(FILE_DIR.iterdir()) == files_before
        finally:
            if removed_grant is not None:
                with SessionLocal() as db:
                    db.add(RolePermission(role_id=removed_grant[0], permission_id=removed_grant[1]))
                    db.commit()

        submitted=manager.post(path,data=data,files=f['pdf'])
        assert submitted.status_code==200,submitted.text
        assert deduction_materials.process_next_deduction_material_job()
        with SessionLocal() as db:
            row=db.get(DeductionRecord,f['sid']);work=db.get(DeductionUpgradeRequest,row.upgrade_request_id)
            assert work.reviewer_id==p['default_reviewer_id'] and work.status=='pending'
            assert db.query(DeductionRecord).filter_by(deduction_month='2045-01').count()==2
            assert db.query(AuditLog).filter_by(action='重新指派失效升级审核经理',entity_id=str(work.id)).count()==1
            db.query(UserAccount).filter_by(employee_id=f['reviewer']['id']).one().enabled=True;db.commit()


def test_legacy_material_reassigns_an_enabled_gsm_who_lost_review_permission(monkeypatch):
    monkeypatch.setattr(importlib.import_module("app.main"), "start_deduction_material_worker", lambda: None)
    with TestClient(app) as manager:
        login(manager, "GSMTEST01")
        manager_id = manager.get("/api/me").json()["id"]
        flow = setup_flow(manager, "2045-05", supplement=False, photos=True)
        old_reviewer = flow["reviewer"]["id"]
        with SessionLocal() as db:
            first = db.get(DeductionRecord, flow["first"])
            second = db.get(DeductionRecord, flow["sid"])
            old_work = db.get(DeductionUpgradeRequest, second.upgrade_request_id)
            first.upgrade_request_id = second.upgrade_request_id = None
            db.delete(old_work)
            gsm = db.query(Role).filter_by(code="GSM").one()
            ta_gsm = db.query(Role).filter_by(code="TA_GSM").one()
            permission = db.query(Permission).filter_by(code="DEDUCTION_ALL").one()
            gsm_role_id, permission_id = gsm.id, permission.id
            # Keep the replacement's effective role GSM and grant via a separate active duty.
            duties = [EmployeeActingDuty(employee_id=manager_id, role_id=role.id,
                                        starts_on=date.today().isoformat(), status="active", reason="权限合并测试")
                      for role in (gsm, ta_gsm)]
            db.add_all(duties)
            db.flush()
            duty_ids = [duty.id for duty in duties]
            db.commit()

        def set_gsm_permission(enabled):
            with SessionLocal() as db:
                grant = db.query(RolePermission).filter_by(role_id=gsm_role_id, permission_id=permission_id).first()
                if enabled and not grant:
                    db.add(RolePermission(role_id=gsm_role_id, permission_id=permission_id))
                elif not enabled and grant:
                    db.delete(grant)
                db.commit()

        try:
            set_gsm_permission(False)
            assert deduction_materials.process_next_deduction_material_job()
            with SessionLocal() as db:
                second = db.get(DeductionRecord, flow["sid"])
                old_job = db.get(DeductionMaterialJob, second.material_job_id)
                assert second.status == "material_failed" and second.upgrade_request_id is None
                assert old_job.error_code == "upgrade_reviewer_unavailable"
                assert db.query(UserAccount).filter_by(employee_id=old_reviewer).one().enabled
                revision, job_id = second.material_revision, old_job.id

            set_gsm_permission(True)
            path = f"/api/deductions/{flow['sid']}/material"
            selected = {"reviewer_id": old_reviewer, "expected_first_id": flow["first"], "recovery_reason": "改选审核经理"}
            before_files = set(FILE_DIR.iterdir())
            routes = importlib.import_module("app.routers.deductions")
            stage = routes.stage_pdf_material
            staging, resume = Event(), Event()

            async def pause_upload(*args, **kwargs):
                staging.set()
                assert resume.wait(10)
                return await stage(*args, **kwargs)

            with monkeypatch.context() as paused, ThreadPoolExecutor(max_workers=1) as pool:
                paused.setattr(routes, "stage_pdf_material", pause_upload)
                waiting = pool.submit(manager.post, path, data=selected, files=flow["pdf"])
                try:
                    assert staging.wait(10)
                    set_gsm_permission(False)
                finally:
                    resume.set()
                denied = waiting.result(timeout=15)
            assert denied.status_code == 400 and "审核权限" in denied.text
            with SessionLocal() as db:
                second = db.get(DeductionRecord, flow["sid"])
                assert (second.status, second.material_revision, second.material_job_id, second.upgrade_request_id) == (
                    "material_failed", revision, job_id, None,
                )
            assert set(FILE_DIR.iterdir()) == before_files

            preview = manager.get(path + "-upgrade-preview").json()
            assert preview["recover_legacy"] and preview["default_reviewer_id"] == manager_id
            assert {reviewer["id"] for reviewer in preview["reviewers"]} == {manager_id}
            recovered = manager.post(path, data={**selected, "reviewer_id": manager_id}, files=flow["pdf"])
            assert recovered.status_code == 200, recovered.text
            assert deduction_materials.process_next_deduction_material_job()
            with SessionLocal() as db:
                second = db.get(DeductionRecord, flow["sid"])
                work = db.get(DeductionUpgradeRequest, second.upgrade_request_id)
                assert work.reviewer_id == manager_id and work.status == "pending"
                assert not deduction_counts_for_score(second)
                transfer = db.query(DeductionUpgradeTransfer).filter_by(request_id=work.id).one()
                assert (transfer.from_reviewer_id, transfer.to_reviewer_id) == (old_reviewer, manager_id)
                work_id = work.id
            approved = manager.post(f"/api/deduction-upgrades/{work_id}/resolve", json={
                "decision": "approve", "issued_confirmed": True, "handling_note": "改派后审核通过",
                "result_level_id": flow["levels"]["MEMO"]["id"],
            })
            assert approved.status_code == 200, approved.text
            assert approved.json()["request"]["result_points"] == flow["levels"]["MEMO"]["points"]
        finally:
            set_gsm_permission(True)
            with SessionLocal() as db:
                db.query(EmployeeActingDuty).filter(EmployeeActingDuty.id.in_(duty_ids)).delete(synchronize_session=False)
                db.commit()


@pytest.mark.parametrize("timing,month", [
    ("before_finalize", "2048-09"), ("after_finalize", "2049-01"), ("permission_restored", "2049-05"),
])
def test_linked_conversion_preserves_pdf_when_reviewer_is_revoked(monkeypatch, timing, month):
    monkeypatch.setattr(importlib.import_module("app.main"), "start_deduction_material_worker", lambda: None)
    with TestClient(app) as helper, TestClient(app) as owner, TestClient(app) as outsider:
        login(helper, "GSMTEST01")
        login(owner, "TAGSMTEST01")
        login(outsider, "CMTEST02")
        helper_id = helper.get("/api/me").json()["id"]
        flow = setup_flow(helper, month, actor=owner, photos=True)
        second_id = flow["sid"]
        with SessionLocal() as db:
            gsm = db.query(Role).filter_by(code="GSM").one()
            ta_gsm = db.query(Role).filter_by(code="TA_GSM").one()
            permission = db.query(Permission).filter_by(code="DEDUCTION_ALL").one()
            gsm_role_id, permission_id = gsm.id, permission.id
            # The replacement keeps review permission through a separate active duty.
            duties = [EmployeeActingDuty(employee_id=helper_id, role_id=role.id, starts_on=date.today().isoformat(),
                                        status="active", reason="权限合并测试") for role in (gsm, ta_gsm)]
            db.add_all(duties)
            db.flush()
            duty_ids = [duty.id for duty in duties]
            second = db.get(DeductionRecord, second_id)
            work = db.get(DeductionUpgradeRequest, second.upgrade_request_id)
            work_id, original_reviewer = work.id, work.reviewer_id
            circle, job_id, file_id = second.attraction_id_snapshot, second.material_job_id, second.document_file_id
            assert work.status == "awaiting_material"
            db.commit()

        def set_gsm_permission(enabled):
            with SessionLocal() as db:
                grant = db.query(RolePermission).filter_by(role_id=gsm_role_id, permission_id=permission_id).first()
                if enabled and not grant:
                    db.add(RolePermission(role_id=gsm_role_id, permission_id=permission_id))
                elif not enabled and grant:
                    db.delete(grant)
                db.commit()

        generated, resume = Event(), Event()
        convert = deduction_materials._make_pdf

        def pause_after_generation(*args, **kwargs):
            result = convert(*args, **kwargs)
            generated.set()
            assert resume.wait(12)
            return result

        recovery_path = f"/api/deduction-upgrades/{work_id}/recover"
        resolve_path = f"/api/deduction-upgrades/{work_id}/resolve"
        recovery_list = "/api/deduction-upgrades/recovery"
        pending_list = "/api/deduction-upgrades/pending"
        reassignment = {"reviewer_id": helper_id, "expected_reviewer_id": original_reviewer,
                        "expected_status": "awaiting_material", "reason": "审核经理转换期间被撤权"}
        try:
            with monkeypatch.context() as paused, ThreadPoolExecutor(max_workers=1) as pool:
                paused.setattr(deduction_materials, "_make_pdf", pause_after_generation)
                worker = pool.submit(deduction_materials.process_next_deduction_material_job)
                try:
                    assert generated.wait(12)
                    set_gsm_permission(False)
                    if timing == "before_finalize":
                        response = owner.post(recovery_path, json=reassignment)
                        assert response.status_code == 200, response.text
                        assert work_id not in {row["id"] for row in helper.get(pending_list).json()["items"]}
                finally:
                    resume.set()
                assert worker.result(timeout=20)

            with SessionLocal() as db:
                second = db.get(DeductionRecord, second_id)
                work = db.get(DeductionUpgradeRequest, work_id)
                assert work.first_deduction_id == flow["first"] and work.second_deduction_id == second_id
                assert second.material_status == "ready" and second.document_file_id == file_id
                assert second.material_job_id == job_id and not deduction_counts_for_score(second)
                assert db.get(DeductionMaterialJob, job_id).status == "succeeded"
                pdf = db.get(StoredFile, file_id)
                assert pdf.status == "active" and (FILE_DIR / pdf.storage_key).read_bytes().startswith(b"%PDF")
                saved_pdf = (pdf.storage_key, pdf.sha256, second.material_revision)
                if timing != "before_finalize":
                    assert (work.status, work.reviewer_id, second.upgrade_state) == (
                        "awaiting_material", original_reviewer, "awaiting_reviewer",
                    )
                    blockers = month_close_work_queries(db, month, circle)["deduction_upgrade"]
                    assert blockers.filter(DeductionUpgradeRequest.id == work_id).count() == 1
            assert owner.get(f"/api/files/{file_id}?preview=true").status_code == 200
            assert outsider.get(f"/api/files/{file_id}?preview=true").status_code == 403
            if timing != "before_finalize":
                assert work_id not in {row["id"] for row in helper.get(pending_list).json()["items"]}
                if timing == "permission_restored":
                    set_gsm_permission(True)
                task = next(row for row in owner.get(recovery_list).json()["items"] if row["id"] == work_id)
                assert task["awaiting_reviewer"] and task["second_record"]["material_status"] == "ready"
                assert "材料已就绪" in task["status_name"]
                assert outsider.post(recovery_path, json=reassignment).status_code == 403
                assert helper.post(resolve_path, json={"decision": "reject", "handling_note": "未完成改派"}).status_code == 404
                with SessionLocal() as db:
                    db.add(MonthClosure(closure_month=month, attraction_id=circle, status="closed"))
                    db.commit()
                assert owner.post(recovery_path, json=reassignment).status_code == 423
                with SessionLocal() as db:
                    db.query(MonthClosure).filter_by(closure_month=month, attraction_id=circle).delete()
                    db.commit()
                response = owner.post(recovery_path, json=reassignment)
                assert response.status_code == 200, response.text

            assert work_id not in {row["id"] for row in owner.get(recovery_list).json()["items"]}
            assert owner.get(pending_list).status_code == 403
            pending = next(row for row in helper.get(pending_list).json()["items"] if row["id"] == work_id)
            assert pending["reviewer_id"] == helper_id
            with SessionLocal() as db:
                second = db.get(DeductionRecord, second_id)
                pdf = db.get(StoredFile, second.document_file_id)
                assert (pdf.storage_key, pdf.sha256, second.material_revision) == saved_pdf
                assert second.material_job_id == job_id and second.upgrade_state == "pending"
                assert db.query(DeductionMaterialJob).filter_by(deduction_id=second_id).count() == 1
                transfer = db.query(DeductionUpgradeTransfer).filter_by(request_id=work_id).one()
                assert (transfer.from_reviewer_id, transfer.to_reviewer_id) == (original_reviewer, helper_id)
            decision = {"decision": "approve", "handling_note": "用原材料审核", "issued_confirmed": True,
                        "result_level_id": flow["levels"]["MEMO"]["id"]}
            approved = helper.post(resolve_path, json=decision)
            assert approved.status_code == 200, approved.text
            assert approved.json()["request"]["result_points"] == flow["levels"]["MEMO"]["points"]
            assert approved.json()["request"]["second_record"]["actual_points"] == 0
        finally:
            set_gsm_permission(True)
            with SessionLocal() as db:
                db.query(EmployeeActingDuty).filter(EmployeeActingDuty.id.in_(duty_ids)).delete(synchronize_session=False)
                db.commit()


def test_existing_warning_does_not_escalate_pending_statement_pair(monkeypatch):
    monkeypatch.setattr(importlib.import_module('app.main'),'start_deduction_material_worker',lambda:None)
    with TestClient(app) as manager,TestClient(app) as reviewer:
        ensure_second_gsm()
        login(manager,'GSMTEST01');login(reviewer,SECOND_GSM)
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
