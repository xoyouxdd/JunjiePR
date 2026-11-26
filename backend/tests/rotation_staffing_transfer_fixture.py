"""HTTP acceptance fixture, entirely isolated from 8011 and all user rosters."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from copy import deepcopy
from datetime import date
import pytest
from test_rotation_formal import seed, seed_today, app, SessionLocal, S
from test_rotation_forecast import fixture
from rotation_test_clock import install_rotation_test_clock

patch = pytest.MonkeyPatch()
install_rotation_test_clock(patch)
setup = seed.__wrapped__(formal_daytime_runtime=patch)
next(setup)
aid = seed_today()
def reset():
    with SessionLocal() as db:
        db.info['rotation_formal'] = True
        with S.transaction(db):
            cfg = S.load_config(db, aid)
            row = S.day_row(db, aid, date.today().isoformat())
            state = S.day_state(row)
            sample = fixture((5, 3), now=1140).d
            aliases = {'A0': '9710020', 'A1': '9710010', 'new0': '9710000'}
            people = {}
            for pid, person in sample['persons'].items():
                p = deepcopy(person)
                p.update(pid=aliases.get(pid, pid), name={'A0': '接班测试主管', 'A1': '接班测试TR', 'new0': '接班测试员工'}.get(pid, pid), end=1320)
                if pid.startswith('new'):
                    p.update(state='rest' if pid == 'new0' else 'pending', readyAt=1190 if pid == 'new0' else None)
                if pid in ('A0', 'B0'):
                    p['end'] = 1200
                people[p['pid']] = p
            for line in sample['lines']:
                for post in line['posts']:
                    post['occ'] = aliases.get(post['occ'], post['occ'])
            state.update(persons=people, lines=sample['lines'], plan=sample['plan'], ruleVersion=2,
                         lastTick=1140, confirmations={}, completedShiftBatches={})
            S.set_sim_clock(cfg, date.today().isoformat(), 1140, paused=True)
            eng, _ = S.engine_for(db, cfg, row, state)
            from app.rotation import forecast
            forecast.plan(eng, 1140)
            S.save_day(db, row, state)

reset()

@app.post('/__fixture/reset')
def reset_fixture():
    reset()
    return {'ok': True}

import uvicorn
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
