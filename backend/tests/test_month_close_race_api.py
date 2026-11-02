"""Real API: a close committed between initial validation and the first write."""
from datetime import date
import pytest
from fastapi.testclient import TestClient
from test_concurrency_smoke import app, login, recognition_form, activate_circle_hr, SessionLocal
from app.routers import recognitions
from app.v2_models import RecognitionRecord, MonthClosure, AuditLog


def test_close_wins_against_delayed_recognition_without_partial_records(monkeypatch):
    class Clock(date):
        @classmethod
        def today(cls):return cls(2100,1,10)
    monkeypatch.setattr('app.month_closure.date',Clock)
    with TestClient(app) as client,TestClient(app) as closer:
        login(client,'TATEST01');payload=recognition_form(client)
        activate_circle_hr();login(closer,'HR-HEAT')
        circle=next(r['id'] for r in closer.get('/api/options').json()['employee_circles'] if r['name']=='热力追踪')
        original=recognitions.ensure_month_open
        def close_between_check_and_write(db,month,attraction_id,operation):
            original(db,month,attraction_id,operation)
            response=closer.post(f'/api/month-closes/{month}/close',json={'attraction_id':circle,'reason':'并发回归'})
            assert response.status_code==200,response.text
        monkeypatch.setattr(recognitions,'ensure_month_open',close_between_check_and_write)
        response=client.post('/api/recognitions',data=payload)
        assert response.status_code==423,response.text
        with SessionLocal() as db:
            assert db.query(MonthClosure).filter_by(closure_month=payload['recognition_date'][:7],attraction_id=circle,status='closed').count()==1
            assert db.query(RecognitionRecord).filter_by(content=payload['content']).count()==0
            assert db.query(AuditLog).filter_by(action='登记签卡').count()==0


@pytest.mark.parametrize('path',['/api/recognitions','/api/recognitions/batch','/api/sick-leave-imports/single'])
def test_validation_failure_audit_ignores_untrusted_forwarded_address(path):
    with TestClient(app) as client:
        response=client.post('/api/login',json={'employee_no':'HR01','password':'HR123'})
        assert response.status_code==200,response.text
        response=client.post(path,data={},headers={'X-Forwarded-For':'203.0.113.99'})
        assert response.status_code==422,response.text
        with SessionLocal() as db:
            row=db.query(AuditLog).order_by(AuditLog.id.desc()).first()
            assert row.ip_address=='testclient'
