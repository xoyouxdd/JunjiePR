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

@app.post('/__fixture/room')
def room(payload: dict):
    from app.v2_database import SessionLocal
    from app.rotation import service as S
    from datetime import date
    aid=seed_cohort()
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        with S.transaction(db):
            cfg=S.load_config(db,aid);settings=S.settings_of(cfg)
            settings.update(mealEarliest='00:00',mealAfterStart=0,mealForceAfterStart=0 if payload.get('forced') else 9999)
            cfg.settings_json=S._dumps(settings)
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row);now=state['lastTick']
            p=state['persons']['9710000']
            p.update(state='walkback',start=now-180,end=now+360,line=None,lineStart=None,continuousStart=None,
                     assign=None,ate=False,arriveAnchor=None,breakKind=None,breakStart=None,readyAt=None,
                     walkbackSince=now,lastDeparture={'line':'A1','post':'上客1'},after='rest')
            state['closeAt']='23:59'
            S.save_day(db,row,state)
    return {'ok':True}
@app.post('/__fixture/cohort')
def cohort(payload: dict | None = None):
    if payload and payload.get('cross'):
        seed_cross_line()
        return {'ok':True}
    aid=seed_cohort(three=bool(payload and payload.get('three')), mixed=bool(payload and payload.get('mixed')))
    if payload and payload.get('hold101'):
        from app.v2_database import SessionLocal
        from app.rotation import service as S
        from datetime import date
        with SessionLocal() as db:
            db.info['rotation_formal']=True
            with S.transaction(db):
                row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row)
                for post,role in zip(state['lines'][0]['posts'],['tower','tower_assist']):post['hold101Role']=role
                S.save_day(db,row,state)
    return {'ok':True}
@app.post('/__fixture/display')
def display():
    from app.v2_database import SessionLocal
    from app.rotation import service as S, workflow as W
    from datetime import date
    from copy import deepcopy
    from uuid import uuid4
    aid=seed_cohort(three=True)
    groups=[('A1','塔台,上客1,上协1,下协,大出口商店'),('A2','饮水童车,小C,核销,身高墙,分组1'),
            ('B1','塔台协助,上客2,上协2,分组2,快迎2'),('B2','迎宾童车,迎宾,豹协,汇合,快速汇合'),
            ('C1','上客3,上协3,预演,预演协助,单人队末'),('C2','快迎1,单人迎宾,分组3,上客4,上协4'),
            ('D1','河马池,铁马出口,迎宾协助,下客1,下客2'),('D2','铁马A,铁马B,铁马C'),('E','城内巡视,队末')]
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        with S.transaction(db):
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row);now=state['lastTick']
            base=deepcopy(state['persons']['9710020']);post_base=deepcopy(state['lines'][0]['posts'][0])
            state['persons']={pid:p for pid,p in state['persons'].items() if pid in ('9710000','9710010','9710050','9710020')}
            state['persons']['9710020'].update(state='excluded',role='excluded',line=None,lineStart=None,continuousStart=None,assign=None)
            state['lines']=[];state['rotationTasks']={};state['plan'].update(crew={},seven={},push7=[],lost=[])
            counter=0
            for lid,names in groups:
                line={'id':lid,'group':lid[0],'active':lid not in ('D2','E'),'standby':lid in ('D2','E'),'posts':[]}
                for i,name in enumerate(names.split(',')):
                    post=deepcopy(post_base);post.update(key=uuid4().hex,name=name,occ=None,since=None,hold101Role='tower' if name=='塔台' else 'tower_assist' if name=='塔台协助' else None)
                    post['open']=line['active'] and (i<4 or lid in ('A2','D1'))
                    if post['open']:
                        counter+=1;pid='display-'+str(counter);p=deepcopy(base)
                        p.update(pid=pid,name='轮岗员'+str(counter).zfill(2),state='onpost',role='rotation',line=lid,start=540,end=1080,
                                 lineStart=now-30-counter,continuousStart=now-30-counter,assign=None,flags=[],absences=[],visited=[lid],ate=False)
                        state['persons'][pid]=p;post.update(occ=pid,since=p['lineStart'])
                    line['posts'].append(post)
                state['lines'].append(line)
            for i,p in enumerate(state['persons'][pid] for pid in ('9710000','9710010','9710050')):
                p.update(name='轮岗员'+str(70+i),state='ready',readyAt=now-1+i/2,assign={'line':'A1','mode':'push','departAt':now-1+i/2},start=540,end=1080)
            for i in range(12):
                p=deepcopy(base);pid='rest-'+str(i);p.update(pid=pid,name='轮岗员'+str(40+i),state='rest',role='rotation',line=None,lineStart=None,
                    continuousStart=None,start=540,end=1080,breakStart=now-5,readyAt=now+4+i,assign=None,flags=[],absences=[],visited=[],ate=False)
                state['persons'][pid]=p
            eng,_=S.engine_for(db,S.load_config(db,aid),row,state);eng.tick(now);S.save_day(db,row,state)
    return {'ok':True}

import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
