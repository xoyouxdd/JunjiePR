"""Local browser fixture. Test-only routes are never mounted by production code."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_supervisor_performance import app
from test_activities import money, expire


@app.on_event('startup')
def seed_activity_browser():
    money('CMTEST01', 45)
    money('CMTEST02', 100)


@app.post('/__fixture/end/{lot_id}')
def end_lot(lot_id: int):
    expire(lot_id)
    return {'ok': True}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
