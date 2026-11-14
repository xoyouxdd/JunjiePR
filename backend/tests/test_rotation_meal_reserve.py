"""Configurable meal reserve, exact boundaries and committed revalidation."""
from datetime import date, timedelta

import pytest

from app.rotation import service as S
from test_rotation_forecast import fixture
from test_rotation_forecast_api import DAY, rotation, current, board, act, person


def reserve_engine():
    eng=fixture()
    for p in eng.P.values():p.update(state='other',assign=None,readyAt=None)
    eater=eng.P['A0']
    reserves=[p for p in eng.P.values() if p is not eater][:5]
    for p in reserves:p.update(state='rest',readyAt=620,breakKind='rest')
    return eng,eater,reserves


@pytest.mark.parametrize('state,kind',[('rest','rest'),('rest','meal_rest'),('meal','meal'),('ready','rest')])
def test_exact_twenty_minutes_and_default_five_excluding_current_person(state,kind):
    eng,p,crew=reserve_engine();p.update(state='rest',readyAt=610)
    crew[0].update(state=state,breakKind=kind,readyAt=620+1/60)
    before=eng.meal_options(p,600)
    assert before['supply']==4 and before['required']==5 and before['reserveWithin']==20 and not before['canChoose']
    crew[0]['readyAt']=620
    after=eng.meal_options(p,600)
    assert after['supply']==5 and after['canChoose']
    eng.S['mealReserveMinPeople']=6
    assert not eng.meal_options(p,600)['canChoose']
    eng.S['mealReserveMinPeople']=4;crew[0]['readyAt']=630
    assert eng.meal_options(p,600)['canChoose']


@pytest.mark.parametrize('change',[{'state':'exception'},{'state':'away'},{'state':'sick'},{'state':'notyet'},
                                  {'state':'done'},{'role':'excluded'},{'readyAt':None},{'exception':{'at':600}},
                                  {'manualPaused':True},{'start':605},{'end':620},
                                  {'absences':[{'start':610,'end':650}]}])
def test_unavailable_people_are_not_reserve(change):
    eng,p,crew=reserve_engine();crew[0].update(change)
    options=eng.meal_options(p,600)
    assert options['supply']==4 and not options['canChoose']


def test_force_time_and_manual_restore_are_explicit_existing_exceptions():
    eng,p,crew=reserve_engine();crew[0].update(end=625,manualRestored=True)
    assert eng.meal_options(p,600)['supply']==5
    for q in crew:q['state']='away'
    p['start']=300
    options=eng.meal_options(p,600)
    assert options['forced'] and not options['canChoose'] and options['supply']==0


def test_reserve_counts_waiting_and_preparing_including_preassigned_opening_staff():
    eng, eater, crew = reserve_engine()
    eater.update(state='pending')
    crew[0].update(state='pending', readyAt=None)
    crew[1].update(state='walkback', readyAt=None)
    crew[2].update(state='notyet', start=620, readyAt=None)
    eng.d['plan']['crew']['A#0'] = crew[2]['pid']
    crew[3].update(state='notyet', start=620 + 1 / 60, readyAt=None)
    eng.S['mealReserveLeft'] = 15
    crew[4]['readyAt'] = 615
    assert eng.meal_options(eater, 600)['supply'] == 4
    assert eng.meal_options(eater, 600 + 1 / 60)['supply'] == 5
    eng.d['ruleVersion'] = 1
    assert eng.meal_options(eater, 600)['supply'] == 1


def test_historical_settings_without_new_field_remain_readable_and_unmodified():
    eng,p,_=reserve_engine();eng.S.pop('mealReserveMinPeople')
    before=dict(eng.S)
    options=eng.meal_options(p,600)
    assert options['required']==5 and options['canChoose']
    assert eng.S==before


@pytest.mark.parametrize('start,duration,eligible', [(435,389,False), (435,390,False), (435,391,True),
                                                    (1140,390,False), (1140,391,True)])
def test_meal_eligibility_requires_strictly_more_than_six_and_a_half_hours(start,duration,eligible):
    eng, p, _ = reserve_engine()
    eng.S['mealThreshold'] = 360  # Existing configuration cannot lower the minimum.
    p.update(start=start, end=start+duration)
    assert eng.meal_eligible(p) is eligible
    eng.S['mealThreshold'] = 420
    assert not eng.meal_eligible(p)


def test_short_shift_cannot_enter_meal_pool_and_qualification_updates_after_shift_edit(current):
    with current.factory() as db:
        cfg = S.load_config(db, current.attraction_id)
        with pytest.raises(S.ActionError, match='至少为390分钟'), S.transaction(db):
            S.update_config(cfg, None, {'mealThreshold': 389}, None)
    act(current, {'action': 'pool', 'pid': '5', 'pool': 'pending'})
    act(current, {'action': 'shift', 'pid': '5', 'start': '07:15', 'end': '13:45'})
    short = person(current)
    assert not short['mealEligible'] and not short['mealOptions']['canChoose'] and not short['mealOptions']['forced']
    with pytest.raises(S.ActionError, match='未达到用餐工时要求'):
        act(current, {'action': 'pool', 'pid': '5', 'pool': 'meal'})
    act(current, {'action': 'shift', 'pid': '5', 'start': '07:15', 'end': '13:46'})
    assert person(current)['mealEligible'] and not person(current)['ate']
    act(current, {'action': 'pool', 'pid': '5', 'pool': 'meal'})
    assert person(current)['mealEligible'] and person(current)['ate']


@pytest.mark.parametrize('value',[0,-1,True,2.5,'NaN'])
def test_invalid_reserve_count_does_not_change_configuration(current,value):
    with current.factory() as db:
        cfg=S.load_config(db,current.attraction_id);before=cfg.settings_json
        with pytest.raises(S.ActionError),S.transaction(db):S.update_config(cfg,None,{'mealReserveMinPeople':value},None)
        assert S.load_config(db,current.attraction_id).settings_json==before


def test_saved_count_preserves_other_settings_and_stale_meal_choice_is_revalidated(current):
    r=current;act(r,{'action':'pool','pid':'5','pool':'pending'})
    reserve_ids=[]
    for i in range(5):
        name='餐休储备'+str(i)
        act(r,{'action':'add_person','temporary':True,'name':name,'start':'07:00','end':'20:00'})
        pid=next(p['pid'] for p in board(r)['persons'] if p['name']==name);reserve_ids.append(pid)
        act(r,{'action':'pool','pid':pid,'pool':'rest'});act(r,{'action':'break_time','pid':pid,'remaining':20})
    p=person(r);assert p['mealOptions']['canChoose'] and p['mealOptions']['supply']==5
    body={'action':'arrive','pid':'5','confirmation_token':p['confirmationTokens']['arrive'],'meal_choice':'meal'}
    act(r,{'action':'pool','pid':reserve_ids[0],'pool':'away'})
    with pytest.raises(S.ActionError,match='储备不足'):act(r,body)
    assert person(r)['state']=='pending' and not person(r)['ate']
    with r.factory() as db,S.transaction(db):
        cfg=S.load_config(db,r.attraction_id);before=S.settings_of(cfg)
        S.update_config(cfg,None,{'mealReserveMinPeople':4},None)
        after=S.settings_of(cfg)
        assert {k:v for k,v in before.items() if k!='mealReserveMinPeople'}=={k:v for k,v in after.items() if k!='mealReserveMinPeople'}
    assert person(r)['mealOptions']['required']==4 and person(r)['mealOptions']['canChoose']
    assert act(r,body)['executed'] and act(r,body)['duplicate']
    p=person(r);assert p['state']=='meal' and p['readyAt']==645 and p['task']['status']=='forecast'


def move_clock(r, minute):
    offset, clock_minute = divmod(minute, 1440)
    day = (date.fromisoformat(DAY) + timedelta(days=int(offset))).isoformat()
    with r.factory() as db, S.transaction(db):
        cfg = S.load_config(db, r.attraction_id)
        row = S.day_row(db, r.attraction_id, DAY)
        S.set_sim_clock(cfg, day, clock_minute, paused=True)
        S.advance(db, cfg, row, S.day_state(row), minute)


@pytest.mark.parametrize('start', [600, 1435])
def test_meal_switches_to_rest_at_twenty_minutes_and_preserves_the_deadline(current, start):
    r = current
    with r.factory() as db, S.transaction(db):
        cfg = S.load_config(db, r.attraction_id)
        S.update_config(cfg, None, {'mealReserveLeft': 10}, None)
        row = S.day_row(db, r.attraction_id, DAY)
        state = S.day_state(row)
        for p in state['persons'].values():
            p['end'] = max(start + 120, p['start'] + 391)
        state['persons']['5'].update(state='pending', readyAt=None, assign=None)
        S.save_day(db, row, state)
    move_clock(r, start)
    act(r, {'action': 'pool', 'pid': '5', 'pool': 'meal'})
    original = person(r)
    ready_at = start + 45
    assert original['readyAt'] == ready_at and original['state'] == 'meal'
    move_clock(r, ready_at - 20 - 1 / 60)
    assert person(r)['state'] == 'meal'
    move_clock(r, ready_at - 20)
    transferred = person(r)
    assert (transferred['state'], transferred['breakKind']) == ('rest', 'meal_rest')
    assert all(transferred[key] == original[key] for key in ('readyAt', 'breakStart', 'ate', 'timeoutAt'))
    assert not transferred['canDepart']
    with r.factory() as db:
        screen = next(p for p in S.screen_payload(db, r.attraction_id)['day']['persons'] if p['pid'] == '5')
        member = S.member_payload(db, r.attraction_id, '5')['person']
        assert all(p['state'] == 'rest' and p['readyAt'] == ready_at for p in (screen, member))
    move_clock(r, ready_at)
    assert person(r)['canDepart']


@pytest.mark.parametrize('elapsed,expected_state', [(10, 'meal'), (30, 'rest')])
def test_rest_conversion_revalidates_and_credits_time_before_a_timer_edit(current, elapsed, expected_state):
    r = current
    with r.factory() as db, S.transaction(db):
        S.update_config(S.load_config(db, r.attraction_id), None, {'mealReserveMinPeople': 1}, None)
    act(r, {'action': 'pool', 'pid': '5', 'pool': 'rest'})
    act(r, {'action': 'pool', 'pid': '1', 'pool': 'pending'})
    move_clock(r, 605)
    act(r, {'action': 'break_time', 'pid': '5', 'remaining': 35})
    move_clock(r, 600 + elapsed)
    before = person(r)
    assert before['mealOptions']['canChoose'] and before['breakStart'] == 600
    body = {'action': 'pool', 'pid': '5', 'pool': 'meal', 'person_context': {
        'state': before['state'], 'readyAt': before['readyAt'], 'sequence': before.get('confirmationSequence', 0),
        'line': None, 'postIndex': None}}
    act(r, {'action': 'pool', 'pid': '1', 'pool': 'away'})
    with pytest.raises(S.ActionError, match='储备不足'):
        act(r, body)
    rejected = person(r)
    assert rejected['state'] == 'rest' and rejected['readyAt'] == 640 and not rejected['ate']
    act(r, {'action': 'pool', 'pid': '1', 'pool': 'pending'})
    assert act(r, body)['executed']
    converted = person(r)
    assert converted['state'] == expected_state and converted['ate'] and converted['readyAt'] == 645
    assert converted['breakStart'] == 600 and converted['timeoutAt'] == 648
    assert converted['breakKind'] == ('meal_rest' if expected_state == 'rest' else 'meal')
    with pytest.raises(S.ConfirmationConflict):
        act(r, body)
    if expected_state == 'rest':
        with pytest.raises(S.ActionError, match='今天已吃饭'):
            act(r, {'action': 'pool', 'pid': '5', 'pool': 'meal', 'repeat_meal': True})


def test_finished_rest_cannot_be_restarted_by_a_meal_drag(current):
    act(current, {'action': 'pool', 'pid': '5', 'pool': 'rest'})
    move_clock(current, 615)
    with pytest.raises(S.ActionError, match='休息已结束'):
        act(current, {'action': 'pool', 'pid': '5', 'pool': 'meal'})
    assert person(current)['readyAt'] == 615 and not person(current)['ate']
