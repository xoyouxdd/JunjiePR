"""Synthetic role, historical backlog and pagination acceptance; no production data."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime
from pathlib import Path

os.environ["RECOGNITION_V2_DATA_DIR"] = str(Path(tempfile.mkdtemp(prefix="fz-usability-")))
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient
from app.main import app
from app.v2_database import SessionLocal
from app.v2_crypto import hash_password
from app.v2_models import AuditLog, DeductionFollowUp, DeductionRecord, DeductionUpgradeRequest, Employee, UserAccount


def login(client, account, password="1234"):
    client.post("/api/logout")
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def test_backlog_pages_role_details_and_month_blocker_scope():
    with TestClient(app) as client:
        login(client, "TATEST01")
        options = client.get('/api/options').json()
        with SessionLocal() as db:
            employee = db.query(Employee).filter_by(employee_no="CMTEST01").one()
            employee_id, heat_id = employee.id, employee.attraction_id
        created = client.post('/api/deductions', data={
            'employee_id': employee_id, 'deduction_type_id': options['deduction_types'][0]['id'],
            'deduction_level_id': next(r['id'] for r in options['deduction_levels'] if r['code']=='STATEMENT'),
            'occurred_on': date.today().isoformat(), 'description': '独立体验夹具待补材料',
        })
        assert created.status_code == 200, created.text
        record_id = created.json()['record']['id']
        with SessionLocal() as db:
            source = db.get(DeductionRecord, record_id)
            values = {col.name: getattr(source, col.name) for col in DeductionRecord.__table__.columns if col.name != 'id'}
            for index in range(205):
                db.add(DeductionRecord(**{**values, 'description': f'合成积压{index}', 'submitted_at': datetime(2026, 1, 1),
                                         'status': 'material_failed' if index % 2 else 'pending_material'}))
            supervisor = db.query(Employee).filter_by(employee_no='TATEST01').one()
            gsm = db.query(Employee).filter_by(employee_no='GSMTEST01').one()
            outside = db.query(Employee).filter(Employee.attraction_id.is_not(None), Employee.attraction_id != heat_id).first()
            assert outside
            outside_circle = outside.attraction_id
            outside_row = DeductionRecord(**{**values, 'attraction_id_snapshot': outside_circle, 'description': '圈外合成事项'})
            db.add(outside_row)
            db.add(DeductionFollowUp(supervisor_id=supervisor.id, supervisor_name=supervisor.name,
                employee_id=employee_id, employee_no='CMTEST01', employee_name='体验员工',
                deduction_type_id=source.deduction_type_id, deduction_type_name=source.deduction_type_name,
                occurred_on='2020-01-03', previous_record_ids='[]', status='pending'))
            db.add(DeductionFollowUp(supervisor_id=supervisor.id, supervisor_name=supervisor.name,
                employee_id=employee_id, employee_no='CMTEST01', employee_name='体验员工',
                deduction_type_id=source.deduction_type_id, deduction_type_name=source.deduction_type_name,
                occurred_on=date.today().isoformat(), previous_record_ids='[]', status='pending'))
            db.add(DeductionFollowUp(supervisor_id=supervisor.id, supervisor_name=supervisor.name,
                employee_id=outside.id, employee_no=outside.employee_no, employee_name=outside.name,
                deduction_type_id=source.deduction_type_id, deduction_type_name=source.deduction_type_name,
                occurred_on='2020-01-04', previous_record_ids='[]', status='pending'))
            db.flush()
            db.add(DeductionUpgradeRequest(employee_id=employee_id, deduction_type_id=source.deduction_type_id,
                first_deduction_id=source.id, second_deduction_id=source.id + 1, reviewer_id=gsm.id,
                reviewer_name=gsm.name, submitted_by=supervisor.id, submitted_by_name=supervisor.name, status='pending'))
            hr = db.query(Employee).filter_by(employee_no='HR-HEAT').one()
            account = db.query(UserAccount).filter_by(employee_id=hr.id).one()
            account.password_hash=hash_password('1234');account.enabled=True;account.must_change_password=False
            db.commit()

        first = client.get('/api/deductions/pending-materials', params={'scope':'all','page_size':50}).json()
        assert first['total'] == 207 and len(first['items']) == 50
        ids=[]
        for page in range(1,6):
            data=client.get('/api/deductions/pending-materials',params={'scope':'all','page_size':50,'page':page}).json()
            ids.extend(row['id'] for row in data['items'])
        assert len(ids)==len(set(ids))==207 and record_id in ids
        failed=client.get('/api/deductions/pending-materials',params={'scope':'all','status':'material_failed'}).json()
        assert failed['total']==102 and all(row['status']=='material_failed' for row in failed['items'])
        own=client.get('/api/deductions/pending-materials',params={'scope':'mine'}).json()
        assert own['total']==206
        assert client.get('/api/deductions/pending-materials?page=0').status_code==422
        assert client.get('/api/deductions/pending-materials?status=active').status_code==400
        own_follow=client.get('/api/action-center/deduction_follow_up/details').json()
        assert own_follow['total']==3 and all(row['can_handle'] for row in own_follow['items'])
        assert any(row['occurred_on']=='2020-01-03' for row in own_follow['items'])

        login(client,'GSMTEST01')
        action=next(row for row in client.get('/api/action-center').json()['items'] if row['type']=='deduction_follow_up')
        details=client.get('/api/action-center/deduction_follow_up/details').json()
        assert details['total']==action['count']
        assert any(row['employee_no']=='CMTEST01' and not row['can_handle'] for row in details['items'])
        assert all(row['occurred_on']!='2020-01-04' for row in details['items'])

        login(client,'HR-HEAT')
        month=date.today().strftime('%Y-%m')
        status=client.get(f'/api/month-closes/{month}',params={'attraction_id':heat_id}).json()
        with SessionLocal() as db:
            before=db.query(AuditLog).count()
        for code in ('recognition_review','deduction_material','deduction_upgrade','deduction_follow_up'):
            response=client.get(f'/api/month-closes/{month}/blockers/{code}',params={'attraction_id':heat_id,'page_size':1})
            assert response.status_code==200,response.text
            data=response.json()
            assert data['total']==next(row['count'] for row in status['checklist'] if row['code']==code)
            assert len(data['items'])<=1
            if data['items']:
                assert data['items'][0]['responsible'] and 'document_file_id' not in data['items'][0]
                assert data['items'][0]['occurred_on']
        denied=client.get(f'/api/month-closes/{month}/blockers/deduction_material',params={'attraction_id':outside_circle})
        assert denied.status_code==403
        assert client.get(f'/api/month-closes/{month}/blockers/deduction_material').status_code==400
        assert client.get('/api/action-center/deduction_follow_up/details').status_code==403
        assert client.get('/api/deductions/pending-materials?scope=all').status_code==403
        with SessionLocal() as db:
            assert db.query(AuditLog).count()==before

        login(client,'CMTEST01')
        assert client.get('/api/action-center/deduction_follow_up/details').status_code==403
        assert client.get(f'/api/month-closes/{month}/blockers/deduction_material',params={'attraction_id':heat_id}).status_code==403
