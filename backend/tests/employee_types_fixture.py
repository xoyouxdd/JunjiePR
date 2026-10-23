"""Isolated synthetic employee-Type UI backend; never opens user data."""
import sys
from pathlib import Path
from io import BytesIO
from datetime import date,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_employee_types import setup,app
from fastapi.responses import Response
from openpyxl import Workbook
generator=setup.__wrapped__();next(generator)

@app.get('/__fixture/workbook')
def workbook(existing: str, new: str):
    wb=Workbook();ws=wb.active
    ws.append(['员工号','姓名','角色代码','景点圈','Type','实习结束日期'])
    ws.append([existing,'不应覆盖旧姓名','CM','热力追踪','Comp28',''])
    ws.append([new,'导入合成实习生','tr','热力追踪','Intern',(date.today()+timedelta(days=60)).isoformat()])
    out=BytesIO();wb.save(out);return Response(out.getvalue(),media_type='application/octet-stream')

import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
