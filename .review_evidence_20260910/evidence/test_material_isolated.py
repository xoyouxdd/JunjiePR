"""Isolated function test, NOT the original application or original pytest suite.
Real SQLAlchemy+SQLite for a minimal model subset. Conversion and audit helpers are
controlled doubles. All files are synthetic and live in TemporaryDirectory.
"""
import json, tempfile, threading, sqlite3, sys
from datetime import datetime, timedelta
from pathlib import Path
import sqlalchemy
from sqlalchemy import Column, Integer, String, DateTime, create_engine, event, and_, or_, update
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.exc import OperationalError

ROOT=Path(__file__).resolve().parent
Base=declarative_base()
class StoredFile(Base):
    __tablename__='stored_files'
    id=Column(Integer,primary_key=True)
    storage_key=Column(String);original_filename=Column(String);extension=Column(String)
    mime_type=Column(String);file_size=Column(Integer);sha256=Column(String);status=Column(String)
class DeductionRecord(Base):
    __tablename__='deduction_records'
    id=Column(Integer,primary_key=True);employee_id=Column(Integer);deduction_type_id=Column(Integer)
    occurred_on=Column(String);submitter_id=Column(Integer);submitter_name=Column(String)
    status=Column(String);material_status=Column(String);material_error=Column(String)
    material_revision=Column(Integer,default=1);material_source_type=Column(String,default='photos')
    upgrade_request_id=Column(Integer);upgrade_state=Column(String);upgrade_role=Column(String)
class DeductionMaterialJob(Base):
    __tablename__='deduction_material_jobs'
    id=Column(Integer,primary_key=True);deduction_id=Column(Integer);output_file_id=Column(Integer)
    source_file_ids_json=Column(String);mode=Column(String,default='deduction');status=Column(String)
    started_at=Column(DateTime);created_at=Column(DateTime,default=datetime.now)
    attempts=Column(Integer,default=0);completed_at=Column(DateTime)
    first_deduction_id=Column(Integer);error_code=Column(String);error_message=Column(String)
class DeductionFollowUp(Base):
    __tablename__='deduction_follow_ups'
    id=Column(Integer,primary_key=True);employee_id=Column(Integer);deduction_type_id=Column(Integer)
    occurred_on=Column(String);status=Column(String)
    issued_deduction_id=Column(Integer);issued_by=Column(Integer);issued_by_name=Column(String);issued_at=Column(DateTime)
class Audit(Base):
    __tablename__='audit_logs'
    id=Column(Integer,primary_key=True);message=Column(String)
class MaterialError(Exception):
    def __init__(self,code,message):super().__init__(message);self.code=code;self.message=message

def scenario(name, action):
    with tempfile.TemporaryDirectory(prefix='junjie-audit-') as tmp:
        root=Path(tmp);engine=create_engine('sqlite:///'+str(root/'test.db'),connect_args={'check_same_thread':False,'timeout':30})
        @event.listens_for(engine,'connect')
        def pragma(c,_):
            c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA busy_timeout=30000')
        Base.metadata.create_all(engine)
        Factory=sessionmaker(bind=engine,autoflush=False)
        source=root/'source.jpg';source.write_bytes(b'synthetic source only')
        with Factory() as s:
            s.add_all([StoredFile(id=1,storage_key='source.jpg',status='processing_source'),StoredFile(id=2,storage_key='placeholder.pdf',status='pending_conversion')])
            s.add(DeductionRecord(id=1,employee_id=1,deduction_type_id=1,occurred_on='2026-09-10',submitter_id=1,submitter_name='synthetic',status='material_processing',material_status='processing'))
            s.add(DeductionMaterialJob(id=1,deduction_id=1,output_file_id=2,source_file_ids_json='[1]',status='queued'))
            s.commit()
        ns={**globals(),'SessionLocal':Factory,'_safe_file_path':lambda k: root/k}
        ns['_write_audit']=lambda db,action,deduction,after:db.add(Audit(message=action))
        def convert(sources):
            path=root/'generated.pdf';path.write_bytes(b'synthetic PDF stub')
            return path.name,path.stat().st_size,'a'*64
        ns['_make_pdf']=convert;ns['_make_compressed_pdf']=lambda src:convert([src])
        exec(compile((ROOT/'material_excerpt.py').read_text(),str(ROOT/'material_excerpt.py'),'exec'),ns)
        result=action(ns,Factory,root)
        with Factory() as s:
            j=s.get(DeductionMaterialJob,1);d=s.get(DeductionRecord,1);f=s.get(StoredFile,2)
            result.update(final_job_status=j.status,final_attempts=j.attempts,final_business_status=d.status,output_key=f.storage_key,audit_rows=s.query(Audit).count(),source_exists=source.exists(),remaining_files=sorted(p.name for p in root.glob('*') if p.suffix not in ['.db']))
        engine.dispose()
        return {'test':name,**result}

def normal_claim(ns,Factory,root):
    barrier=threading.Barrier(2);values=[];errors=[]
    def f():
        try:
            barrier.wait(5)
            with Factory() as s:values.append(ns['claim_next_deduction_material_job'](s))
        except Exception as e:errors.append(repr(e))
    ts=[threading.Thread(target=f) for _ in range(2)]
    for t in ts:t.start()
    for t in ts:t.join(10)
    return dict(claim_results=values,exceptions=errors,expected_single_claim=(values.count(1)==1 and values.count(None)==1))

def stale_owner(ns,Factory,root):
    original=ns['claim_next_deduction_material_job'];called=0;time0=datetime.now();states=[]
    def claim(db):
        nonlocal called
        called+=1
        return original(db,now=time0+timedelta(minutes=16 if called==2 else 0))
    ns['claim_next_deduction_material_job']=claim
    depth=0
    def converter(sources):
        nonlocal depth
        depth+=1
        if depth==1:
            # Worker A has a loaded job. B reclaims after a simulated 16 minutes.
            b=ns['process_next_deduction_material_job']()
            with Factory() as s:states.append({'worker_B_return':b,'B_output_after_commit':s.get(StoredFile,2).storage_key,'B_status':s.get(DeductionMaterialJob,1).status})
            key='worker_A_late.pdf'
        else:key='worker_B.pdf'
        (root/key).write_bytes(b'synthetic '+key.encode())
        return key,22,'c'*64
    ns['_make_pdf']=converter
    ret=ns['process_next_deduction_material_job']()
    return {'outer_worker_A_return':ret,'after_B':states,'scenario':'A paused; B reclaims after 16min and succeeds; then A finishes'}

def error_commit(ns,Factory,root):
    def convert(_):raise MaterialError('injected_bad_material','Injected expected conversion error')
    ns['_make_pdf']=convert
    class FailingSession(Session):
        def commit(self):
            self.info['commits']=self.info.get('commits',0)+1
            if self.info['commits']==2:raise OperationalError('COMMIT',{},RuntimeError('injected failure persisting error status'))
            return super().commit()
    ns['SessionLocal']=sessionmaker(bind=Factory.kw['bind'],class_=FailingSession,autoflush=False)
    exc=None
    try:ns['process_next_deduction_material_job']()
    except Exception as e:exc=type(e).__name__
    return {'exception_escaped_worker_function':exc,'scenario':'MaterialError -> delete source -> injected failure committing error status'}

def transient_success_commit(ns,Factory,root):
    class FailingSession(Session):
        def commit(self):
            self.info['commits']=self.info.get('commits',0)+1
            if self.info['commits']==2:raise OperationalError('COMMIT',{},RuntimeError('injected pre-commit fault'))
            return super().commit()
    ns['SessionLocal']=sessionmaker(bind=Factory.kw['bind'],class_=FailingSession,autoflush=False)
    ret=ns['process_next_deduction_material_job']()
    return {'worker_return':ret,'scenario':'conversion succeeds; injected failure before success commit'}

if __name__=='__main__':
    results={'mode':'isolated current function bodies + minimal model subset + controlled conversion/clock/commit faults','python':sys.version,'sqlite':sqlite3.sqlite_version,'sqlalchemy':sqlalchemy.__version__,'results':[scenario('atomic_normal_claim',normal_claim),scenario('stale_owner_can_overwrite_new_owner',stale_owner),scenario('failure_path_source_cleanup_before_commit',error_commit),scenario('success_path_precommit_fault_preserves_source',transient_success_commit)]}
    (ROOT/'material_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps(results,ensure_ascii=False,indent=2))
