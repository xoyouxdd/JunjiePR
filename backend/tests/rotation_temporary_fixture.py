"""Fictional rotation accounts and schedules for browser verification."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_rotation_temporary import app,setup,login,ok,register_temp,seed_live,book,TestClient,date
import pytest
from rotation_test_clock import install_rotation_test_clock
clock_patch=pytest.MonkeyPatch();install_rotation_test_clock(clock_patch)
from fastapi.responses import Response

generator=setup.__wrapped__();next(generator)
with TestClient(app) as c:
    ok(login(c));register_temp(c)
seed_live()

@app.get('/__fixture/roster')
def roster(no:str):
    return Response(book([[no,'合成轮岗成员',date.today().isoformat(),'07:15-16:15','']]),media_type='application/octet-stream')

import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
