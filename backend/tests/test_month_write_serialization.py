"""Deterministic SQLite interleavings and month-close input contracts."""
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Event

os.environ['RECOGNITION_V2_DATA_DIR']=tempfile.mkdtemp(prefix='recognition-month-write-')

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.database.connection import Base
from app.month_closure import ensure_month_open, reserve_month_write
from app.v2_models import Attraction, MonthClosure
from test_monthly_self_ranks import system


@pytest.fixture
def sessions(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'race.db'),connect_args={'check_same_thread':False,'timeout':5})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine,autoflush=False)
    engine.dispose()


@pytest.mark.parametrize('write_kind',['flush','sql_update'])
def test_close_between_gate_and_write_rolls_back_entire_operation(sessions,write_kind):
    with sessions() as writer,sessions() as closer:
        ensure_month_open(writer,'2026-10',None,'新增签卡')
        closer.add(MonthClosure(closure_month='2026-10',status='closed'))
        closer.commit()
        with pytest.raises(HTTPException) as error:
            if write_kind=='flush':
                writer.add(Attraction(name='late',active=True));writer.flush()
            else:
                writer.execute(text("INSERT INTO attractions (name, active, employee_circle, recognition_venue, created_at) VALUES ('late',1,0,0,CURRENT_TIMESTAMP)"))
                writer.commit()
        assert error.value.status_code==423
        writer.rollback()
        assert writer.query(Attraction).count()==0


def test_closer_waits_for_writer_and_observes_committed_records(sessions):
    entered=Event();reserved=Event()
    with sessions() as writer:
        ensure_month_open(writer,'2026-10',None,'新增签卡')
        writer.add(Attraction(name='committed',active=True));writer.flush()
        def close():
            with sessions() as closer:
                entered.set();reserve_month_write(closer);reserved.set()
                return closer.query(Attraction).filter_by(name='committed').count()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(close)
            try:
                assert entered.wait(3)
                assert not reserved.wait(.15)
            finally:
                writer.commit()
            assert pending.result(timeout=5)==1


@pytest.mark.parametrize('finish',['commit','rollback'])
def test_month_checks_do_not_leak_into_reused_session(sessions,finish):
    with sessions() as db,sessions() as closer:
        ensure_month_open(db,'2026-10',None,'新增签卡')
        getattr(db,finish)()
        closer.add(MonthClosure(closure_month='2026-10',status='closed'));closer.commit()
        db.add(Attraction(name='unrelated',active=True));db.commit()


@pytest.mark.parametrize('action',['close','reopen'])
@pytest.mark.parametrize('value',['bad',[],{},True,1.5,0,-1,'-1','9'*100])
def test_invalid_close_scope_is_a_client_error_without_mutation(system,action,value):
    db,client,*_=system
    response=client.post(f'/api/month-closes/2026-10/{action}',json={'attraction_id':value,'reason':'test'})
    assert response.status_code==400,response.text
    assert db.query(MonthClosure).count()==0
