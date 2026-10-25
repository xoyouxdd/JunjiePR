"""Synthetic global/local month-close fixture; never accesses user data."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_employee_types import setup,app,SessionLocal
from test_employee_import_conflicts import seed,circle_id
from app.v2_models import MonthClosure,Employee
from datetime import datetime
run=setup.__wrapped__();next(run)

@app.post('/__fixture/month-close-scope')
def seed_scope(payload:dict):
    aid=circle_id('小熊罐子');index=int(payload.get('index',0));no=str(9876200+index)
    with SessionLocal() as db:employee=db.query(Employee).filter_by(employee_no=no).first()
    eid=employee.id if employee else seed(no,aid)
    with SessionLocal() as db:
        for target in (None,aid):
            row=db.query(MonthClosure).filter_by(closure_month='2026-09',attraction_id=target).first()
            if row is None:
                row=MonthClosure(closure_month='2026-09',attraction_id=target);db.add(row)
            row.status='closed' if target is None or payload.get('local') else 'open'
            row.closed_by_name='最高管理员（合成）';row.closed_at=datetime.now();row.close_reason='合成月结复现'
        db.commit()
    return {'attraction_id':aid,'employee_id':eid,'month':'2026-09'}

import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
