"""Opening completion clears its demand without advancing real or virtual headers."""
from copy import deepcopy
import json

import pytest

from app.rotation import forecast as F, service as S
from app.rotation.models import RotationEvent
from test_rotation_forecast import fixture, occupants
from test_rotation_forecast_api import rotation, current, board, act, person, DAY


def opening_engine(header=435):
    eng = fixture()
    eng.line('A')['sortAt'] = header
    eng.line('A')['posts'].append(dict(name='新增岗位',key='new-post',occ=None,open=True,opened=True,manual=False,since=None))
    eng.line('A')['adding'] = True
    return eng


@pytest.mark.parametrize('header', [0, 435, 518+12/60])
def test_opening_keeps_actual_header_and_clears_only_completed_demand(header):
    eng = opening_engine(header)
    before = deepcopy(eng.d['lines']);F.plan(eng,600)
    assert eng.P['new0']['assign']['kind'] == 'opening'
    assert eng.d['lines'] == before
    assert F.execute(eng,'new0',600,'test','A')['executed']
    assert eng.line('A')['sortAt'] == header and not eng.line('A')['adding']
    assert occupants(eng) == ['new0','A0','A1','A2']
    assert eng.P['new0']['state'] == 'onpost' and eng.P['new0']['assign'] is None
    assert eng.P['new0']['lineStart'] == eng.line('A')['posts'][0]['since'] == 600
    assert all(not p.get('assign') or p['assign']['line']!='A' or p['assign']['kind']!='opening' for p in eng.P.values())


def test_virtual_opening_preserves_clock_then_next_ordinary_push_advances_it():
    eng = opening_engine();eng.line('B')['sortAt'] = 440
    F.plan(eng,600)
    assert [(eng.P['new'+str(i)]['assign']['line'],eng.P['new'+str(i)]['assign']['kind']) for i in range(3)] == [('A','opening'),('A','push'),('B','push')]
    F.execute(eng,'new0',600,'test','A')
    assert eng.line('A')['sortAt'] == 435
    assert eng.P['new1']['assign']['line'] == 'A' and eng.P['new1']['assign']['kind'] == 'push'
    F.execute(eng,'new1',600,'test','A')
    assert eng.line('A')['sortAt'] == 600 and eng.P['new2']['assign']['line'] == 'B'


def test_overnight_opening_keeps_header_and_uses_actual_post_time():
    eng = opening_engine(1439)
    for p in eng.P.values():p['end']=1560
    for p in eng.P.values():
        if p['state']=='rest':p.update(readyAt=1441.5,breakStart=1426.5,arriveAnchor=1426.5)
    F.execute(eng,'new0',1441.5,'test','A')
    assert eng.line('A')['sortAt'] == 1439
    assert eng.line('A')['posts'][0]['since'] == 1441.5


@pytest.mark.parametrize('requested',[False,True])
def test_manual_drag_keeps_its_separate_explicit_option_on_an_adding_line(requested):
    eng = opening_engine()
    F.manual_place(eng,'new0','A',0,'push',600,'test',update_sort_time=requested)
    assert eng.line('A')['sortAt'] == (600 if requested else 435)
    assert not eng.line('A')['adding']


def add_day_post(r):
    with r.factory() as db,S.transaction(db):
        row=S.day_row(db,r.attraction_id,DAY);state=S.day_state(row)
        state['lines'][0]['posts'].append(dict(name='新增岗位',key='new-post',occ=None,open=False,opened=False,manual=False,since=None))
        S.save_day(db,row,state)
    act(r,{'action':'post','line':'A','i':2,'open':True})


def test_api_opening_completion_retains_header_receipt_and_no_lingering_task(current):
    r=current
    act(r,{'action':'sort_time','line':'A','time':'08:38:00'})
    add_day_post(r);day=board(r)
    assert day['lines'][0]['adding'] and day['lines'][0]['sortAt']==518
    p=person(r);assert p['assign']['kind']=='opening'
    body={'action':'depart','pid':'5','expected_line':'A','confirmation_token':p['confirmationTokens']['depart']}
    result=act(r,body);assert result['executed']
    day=board(r);assert day['lines'][0]['sortAt']==518 and not day['lines'][0]['adding']
    assert person(r)['task'] is None and person(r)['assign'] is None and person(r)['state']=='onpost'
    assert act(r,body)['duplicate']
    assert board(r)['lines']==day['lines']
    with r.factory() as db:
        event=db.query(RotationEvent).filter_by(event_type='depart').one()
        assert json.loads(event.detail_json)['purpose']=='opening'
        row=S.day_row(db,r.attraction_id,DAY);state=S.day_state(row)
        assert state['confirmations'][body['confirmation_token']]['status']=='executed'


def test_failed_opening_commit_preserves_flag_and_header(current,monkeypatch):
    r=current;add_day_post(r);p=person(r);before=board(r)
    with r.factory() as db:
        def fail():raise RuntimeError('commit fault')
        monkeypatch.setattr(db,'commit',fail)
        with pytest.raises(RuntimeError,match='commit fault'):
            S.do_live_action(db,r.attraction_id,'test',{'action':'depart','pid':'5','expected_line':'A','confirmation_token':p['confirmationTokens']['depart']})
    assert board(r)['lines']==before['lines'] and board(r)['lines'][0]['adding']
    assert person(r)['state']=='rest'
