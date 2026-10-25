"""Real-day formal fixture, isolated from all user data."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_rotation_formal import seed, seed_today, seed_cohort, seed_cross_line, seed_assignment_window, app
import pytest
from rotation_test_clock import install_rotation_test_clock
clock_patch=pytest.MonkeyPatch();install_rotation_test_clock(clock_patch)
setup=seed.__wrapped__()
next(setup)
seed_today()
from app.v2_database import engine
@app.post('/__fixture/assignment-window')
def assignment_window(payload: dict):
    seed_assignment_window(float(payload.get('remaining',2)))
    return {'ok':True}

@app.get('/__fixture/pool')
def pool():
    return {'checked_out':engine.pool.checkedout()}
@app.post('/__fixture/cohort')
def cohort(payload: dict | None = None):
    if payload and payload.get('cross'):
        seed_cross_line()
        return {'ok':True}
    seed_cohort(three=bool(payload and payload.get('three')), mixed=bool(payload and payload.get('mixed')))
    return {'ok':True}
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
