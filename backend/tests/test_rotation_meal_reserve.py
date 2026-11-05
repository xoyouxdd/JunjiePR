"""Configurable meal reserve, exact boundaries and committed revalidation."""
import pytest

from app.rotation import service as S
from test_rotation_forecast import fixture
from test_rotation_forecast_api import rotation, current, board, act, person


def reserve_engine():
    eng=fixture()
    for p in eng.P.values():p.update(state='pending',assign=None,readyAt=None)
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
                                  {'manualPaused':True},{'start':605},{'end':625},
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


def test_historical_settings_without_new_field_remain_readable_and_unmodified():
    eng,p,_=reserve_engine();eng.S.pop('mealReserveMinPeople')
    before=dict(eng.S)
    options=eng.meal_options(p,600)
    assert options['required']==5 and options['canChoose']
    assert eng.S==before


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
