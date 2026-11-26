"""Recorded personnel history acceptance, isolated SQLite and paused clock."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from copy import deepcopy
from datetime import date, timedelta
import pytest
from test_rotation_formal import seed, seed_today, app, SessionLocal, S
from test_rotation_person_insights import state
from rotation_test_clock import install_rotation_test_clock
from app.rotation.namespace import tables_for
from app.rotation.daily import capture_snapshot

patch = pytest.MonkeyPatch()
install_rotation_test_clock(patch)
setup = seed.__wrapped__(formal_daytime_runtime=patch)
next(setup)
aid = seed_today()
today = date.today().isoformat()
with SessionLocal() as db:
    db.info['rotation_formal'] = True
    with S.transaction(db):
        T = tables_for(db)
        cfg = S.load_config(db, aid)
        db.query(T.RotationSnapshot).filter_by(attraction_id=aid).delete()
        for offset in (7, 0):
            day = (date.today() - timedelta(days=offset)).isoformat()
            observations = [(400, state('notyet')), (420, state('onpost')),
                            (440, state('onpost', slot=1, start=440)), (460, state('onpost', slot=2, start=460)),
                            (480, state('pending')), (482, state('rest', start=482)), (497, state('ready', start=482)),
                            (498, state('exception')), (500, state('rest', start=500)),
                            (502, state('onpost', line='B', start=502, continuous=502)),
                            (532, state('onpost', line='B', slot=1, start=532, continuous=502)),
                            (552, state('pending')), (555, state('meal', start=555)),
                            (590, state('onpost', start=590, continuous=590))]
            if offset: observations.append((600, state('done')))
            for minute, sample in observations:
                sample['date'] = day
                p = deepcopy(sample['persons']['new0'])
                p.update(pid='9710000', name='接班测试员工', start=420, end=1320)
                sample['persons'] = {'9710000': p}
                for line in sample['lines']:
                    for post in line['posts']:
                        if post['occ'] == 'new0': post['occ'] = '9710000'
                sample['lastTick'] = minute
                sample['plan'] = {'crew': {}, 'seven': {}, 'push7': [], 'lost': []}
                capture_snapshot(db, aid, sample, S.settings_of(cfg), minute)
            sample['lastTick'] = 600
            sample['status'] = 'ended' if offset else 'live'
            row = S.day_row(db, aid, day)
            if row is None:
                row = T.RotationDay(attraction_id=aid, work_date=day, status=sample['status'], state_json=S._dumps(sample), version=1)
                db.add(row)
            else:
                row.state_json = S._dumps(sample); row.status = sample['status']
        S.set_sim_clock(cfg, today, 600, paused=True)

import uvicorn
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
