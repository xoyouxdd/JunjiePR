"""Real-day formal fixture, isolated from all user data."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_rotation_formal import seed, seed_today, app
import pytest
from rotation_test_clock import install_rotation_test_clock
clock_patch=pytest.MonkeyPatch();install_rotation_test_clock(clock_patch)
# This HTTP fixture keeps the daytime patch alive for the whole server runtime.
setup=seed.__wrapped__(formal_daytime_runtime=clock_patch)
next(setup)
seed_today()
from app.database.connection import engine

@app.get('/__fixture/pool')
def pool():
    return {'checked_out':engine.pool.checkedout()}

import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
