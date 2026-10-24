"""Synthetic-only web backend for roster exception choices and circle switching."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_rotation_roster_independence import app,setup,seed_employee,ids,rows,live,SessionLocal,UserAccount,Employee,S
from test_rotation_temporary import book,date
from fastapi.responses import Response

generator=setup.__wrapped__();next(generator)
seed_employee('9730220','合成异圈主管',circle='矮人迷宫',role='SUPERVISOR')
for i in range(3):
    seed_employee(str(9740300+i),'合成停用支援人员'+str(i),enabled=False)
    seed_employee(str(9740310+i),'合成缺少账号人员'+str(i),account=False)

@app.get('/__fixture/roster')
def workbook(index:int=0):
    return Response(book(rows(('9730000','本圈'),('9730001','跨圈'),(str(9740300+index),'停用'),
        (str(9740310+index),'无账号'),(str(9740320+index),'合成临时支援人员'+str(index)))),media_type='application/octet-stream')

@app.post('/__fixture/live')
def initialize(aid:int,no:str):
    live(aid,no);return {'ok':True}

@app.post('/__fixture/source')
def source():
    aid=ids()['矮人迷宫']
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        S.save_roster(db,aid,{'people':{'9730001':{'name':'合成员工9730001'}},
            'days':{date.today().isoformat():{'9730001':'00:00-23:59'}},'hours':{}},scope='day',
            file_name='synthetic-source.xlsx',uploader_id=None,uploader_name='合成数据')
    live(aid,'9730001');return {'ok':True}

@app.get('/__fixture/info')
def info(no:str):
    with SessionLocal() as db:
        e=db.query(Employee).filter_by(employee_no=no).one()
        a=db.query(UserAccount).filter_by(employee_id=e.id).first()
        return {'circles':ids(),'account_enabled':a.enabled if a else None,'owner':e.attraction_id}

import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
