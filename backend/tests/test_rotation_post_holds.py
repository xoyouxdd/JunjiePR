"""Continuous post holds through the current engine and authenticated board."""
import pytest

from test_rotation_formal import (
    app, seed, formal_daytime_runtime, seed_today, login, ok, TestClient, SessionLocal, S, F, date,
)
from app.rotation import forecast
from app.rotation.engine import ActionError
from test_rotation_forecast import fixture, occupants


def hold(engine, pid='A1', on=True, now=600):
    line, index = engine.find_post(pid)
    engine.act_post_hold(line['id'], index, pid, on, '现场接待', now, 'test')


def reset_today():
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        # Keep the ticker out until both removal and recreation have completed.
        with S.runtime_for(db).lock:
            row=db.query(F.RotationDay).filter_by(work_date=date.today().isoformat()).first()
            if row:
                # Recreate this disposable fixture rather than turning a running day
                # back into a draft that is correctly locked after its first shift.
                with S.transaction(db):db.delete(row)
            return seed_today()


def test_ordinary_push_skips_fixed_person_and_preserves_standing_time():
    engine = fixture((3,))
    hold(engine)
    assert forecast.execute(engine, 'new0', 600, 'test', 'A')['executed']
    assert occupants(engine) == ['new0', 'A1', 'A0']
    assert engine.P['A1']['lineStart'] == 435

    hold(engine, on=False, now=605)
    assert not engine.held('A1')
    assert engine.P['A1']['lineStart'] == 435
    assert occupants(engine) == ['new0', 'A1', 'A0']


def test_scheduled_close_waits_for_last_shift_end_to_release_hold():
    engine = fixture((3,))
    hold(engine)
    engine.line('A')['posts'][1]['closeAt'] = '10:05'
    engine.tick(606)
    assert engine.line('A')['posts'][1]['open'] and engine.held('A1')

    engine.tick(1200)
    assert engine.d['status'] == 'ended'
    assert engine.P['A1']['state'] == 'done'
    assert not engine.held('A1')
    assert any(event[0] == 'post_resume' and event[-1]['reason'] == '轮岗结束，定岗自动结束'
               for event in engine.h.events)


def test_manual_post_closing_leaves_held_person_on_post():
    engine = fixture((3,))
    hold(engine)
    with pytest.raises(ActionError, match='定岗'):
        forecast.close_post(engine, engine.line('A'), 1, 605, 'test')
    assert engine.held('A1') and occupants(engine) == ['A0', 'A1', 'A2']


@pytest.mark.parametrize('role_index',[1,2,3,4,5])
def test_tr_lead_and_manager_can_set_cancel_and_share_state(role_index):
    reset_today()
    body={'action':'post_hold','line':'A1','i':0,'pid':'9710020','on':True,'reason':'现场接待'}
    with TestClient(app) as operator,TestClient(app) as cm:
        login(operator,f'97100{role_index}0');login(cm,'9710000')
        ok(operator.post('/api/rotation-board/act',json=body))
        board=ok(cm.get('/api/rotation-board/board'))
        assert board['day']['lines'][0]['posts'][0]['hold']['reason']=='现场接待'
        assert cm.post('/api/rotation-board/act',json={**body,'on':False}).status_code==403
        ok(operator.post('/api/rotation-board/act',json={**body,'on':False}))
        assert not ok(cm.get('/api/rotation-board/board'))['day']['lines'][0]['posts'][0].get('hold')


def test_hold_rejects_stale_person_and_preserves_fixed_worker_on_confirmation():
    reset_today()
    with TestClient(app) as tr,TestClient(app) as cm:
        login(tr,'9710010');login(cm,'9710000')
        person = ok(cm.get('/api/rotation-board/me'))['person']
        body={'action':'post_hold','line':'A1','i':1,'pid':'9710010','on':True}
        assert tr.post('/api/rotation-board/act',json={**body,'pid':'9710020'}).status_code==400
        assert tr.post('/api/rotation-board/act',json={**body,'on':'true'}).status_code==400
        ok(tr.post('/api/rotation-board/act',json=body))
        result = ok(cm.post('/api/rotation-board/me/act', json={
            'action': 'depart', 'pid': '9710000',
            'confirmation_token': person['confirmationTokens']['depart'],
            'expected_line': person['task']['line'],
        }))
        assert result['result']['status'] == 'executed'
        fixed = ok(tr.get('/api/rotation-board/board'))['day']['lines'][0]['posts'][1]
        assert fixed['occ'] == '9710010' and fixed['hold']['pid'] == '9710010'
        with SessionLocal() as db:
            db.info['rotation_formal']=True
            aid=db.query(F.RotationDay).filter_by(work_date=date.today().isoformat()).one().attraction_id
            with pytest.raises(ActionError,match='定岗'):
                S.do_draft_action(db,aid,'test',{'action':'draft_generate','date':date.today().isoformat(),'force':True})
