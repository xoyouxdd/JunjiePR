"""Real overlapping requests on an isolated file-backed SQLite database."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_declaration_admin_corrections import setup, system
from app.database.connection import get_db
from app.routers import deductions
from app.v2_auth import current_user
from app.v2_models import DeductionRecord, DeductionLevel, AuditLog


@pytest.mark.parametrize('scenario',['type_type','void_void','restore_restore','type_level'])
def test_real_overlapping_admin_requests_have_one_winner(setup,tmp_path,scenario):
    source_db,_,holder,_,_,types,record,body,*_=setup
    record_id=record.id
    # Copy the disposable fixture only. Every request below owns a new session/connection.
    source_db.rollback()
    # Authentication is fixed for this test; detach its fully loaded objects so
    # the concurrent request threads cannot lazy-load from the fixture session.
    for actor in (holder['user'].employee, holder['user'].role):
        source_db.refresh(actor)
        source_db.expunge(actor)
    source_db.rollback()
    file=tmp_path/'concurrency.sqlite'
    source=source_db.get_bind().raw_connection()
    target=sqlite3.connect(file)
    try:source.driver_connection.backup(target)
    finally:target.close();source.close()
    engine=create_engine('sqlite:///'+str(file),connect_args={'check_same_thread':False,'timeout':10})
    sessions=sessionmaker(bind=engine,expire_on_commit=False)
    api=FastAPI();api.include_router(deductions.router,prefix='/api')
    def session_dependency():
        with sessions() as db:
            try:yield db
            finally:db.rollback()
    api.dependency_overrides[get_db]=session_dependency
    api.dependency_overrides[current_user]=lambda:holder['user']
    gate=Barrier(2)
    def request(action,payload):
        with TestClient(api) as client:
            gate.wait(timeout=10)
            response=(client.patch if action in {'type','level'} else client.post)(f'/api/deductions/{record_id}/{action}',json=payload)
            return response.status_code,response.text
    try:
        if scenario=='type_type':
            jobs=[('type',body),('type',{**body,'type_id':types[2].id})]
        elif scenario=='type_level':
            with sessions() as db:memo=db.query(DeductionLevel).filter_by(code='MEMO').one().id
            jobs=[('type',body),('level',{'level_id':memo,'expected_level_id':body['expected_level_id'],'expected_type_id':body['expected_type_id'],'expected_status':'active','reason':'并发更正等级'})]
        else:
            payload={'reason':'并发作废','expected_status':'active','expected_type_id':body['expected_type_id'],'expected_level_id':body['expected_level_id']}
            if scenario=='restore_restore':
                with TestClient(api) as client:assert client.post(f'/api/deductions/{record_id}/void',json=payload).status_code==200
                with sessions() as db:
                    row=db.get(DeductionRecord,record_id)
                    payload={**payload,'expected_voided_at':row.voided_at.isoformat(),'reason':'并发还原'}
                jobs=[('restore',payload)]*2
            else:jobs=[('void',payload)]*2
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures=[executor.submit(request,action,payload) for action,payload in jobs]
            results=[f.result(timeout=25) for f in futures]
        assert sorted(code for code,_ in results)==[200,409],results
        with sessions() as db:
            action='还原扣分' if scenario=='restore_restore' else '作废扣分' if scenario=='void_void' else None
            audits=db.query(AuditLog).filter(AuditLog.action==action).count() if action else db.query(AuditLog).filter(AuditLog.action.in_(['修改处分类型','修改处分级别'])).count()
            assert audits==1
            row=db.get(DeductionRecord,record_id)
            assert row.status==('void' if scenario=='void_void' else 'active')
            assert row.description=='original'
    finally:engine.dispose()
