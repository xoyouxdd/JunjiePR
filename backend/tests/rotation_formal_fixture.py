"""Real-day formal fixture, isolated from all user data."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_rotation_formal import seed, seed_today, seed_cohort, app
setup=seed.__wrapped__()
next(setup)
seed_today()
from app.v2_database import engine
@app.get('/__fixture/pool')
def pool():
    return {'checked_out':engine.pool.checkedout()}
@app.post('/__fixture/cohort')
def cohort(payload: dict | None = None):
    seed_cohort(three=bool(payload and payload.get('three')))
    return {'ok':True}
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
