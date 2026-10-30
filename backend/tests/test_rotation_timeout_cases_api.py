"""Real formal API, isolated database; UI and API use the same preview executor."""
from datetime import date
from fastapi.testclient import TestClient
import pytest
from test_rotation_formal import seed, seed_cohort, SessionLocal, S, app, login, ok
from app.rotation import workflow as W, timeout_cases as C


def setup_case():
    aid=seed_cohort()
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        with S.transaction(db):
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row);now=state['lastTick']
            for key in ('timeoutCases','timeoutDemands','timeoutBlockedNeeds'):state.pop(key,None)
            state['rotationTasks']={}
            for p in state['persons'].values():
                p.pop('entryHold',None);p.pop('taskTimeout',None)
                if p['state']!='onpost':p['assign']=None
            first=state['persons']['9710000'];first.update(state='ready',readyAt=now-4,assign={'line':'A1','mode':'push','departAt':now-4})
            eng,_=S.engine_for(db,S.load_config(db,aid),row,state);W.plan(eng,now-4)
            second=state['persons']['9710010'];second.update(state='ready',readyAt=now,assign={'line':'A1','mode':'chain','target':state['lines'][0]['posts'][0]['occ'],'departAt':now})
            W.tick(eng,now);assert C.active(eng)
            S.save_day(db,row,state)
    return aid,now


@pytest.mark.parametrize('executed',[True,False])
def test_related_case_claim_preview_commit_and_permissions(monkeypatch,executed):
    aid,now=setup_case();monkeypatch.setattr(S,'clock_now',lambda cfg:(date.today().isoformat(),now))
    with TestClient(app) as manager,TestClient(app) as cm,TestClient(app) as other:
        login(manager,'9710020');login(cm,'9710000');login(other,'9710001')
        board=ok(manager.get('/api/rotation-board/board'));case=board['day']['timeoutCases'][0]
        assert any(x['type']=='rotation_timeout_related' for x in ok(manager.get('/api/action-center'))['items'])
        root=ok(cm.get('/api/rotation-board/me'))['person']
        assert root['timeoutCase'] and not root['canDepart']
        assert cm.post('/api/rotation-board/me/act',json={'action':'entry_return','confirmation_token':root['confirmationTokens']['entry_return']}).status_code==400
        assert cm.post('/api/rotation-board/act',json={'action':'timeout_claim','case_id':case['id']}).status_code==403
        assert other.post('/api/rotation-board/timeout/preview',json={'case_id':case['id']}).status_code==403
        ok(manager.post('/api/rotation-board/act',json={'action':'timeout_claim','case_id':case['id']}))
        case=ok(manager.get('/api/rotation-board/board'))['day']['timeoutCases'][0]
        positions=case['roots'][0]['suggestedPositions'] if executed else {p['key']:p['pid'] for p in case['posts']}
        body={'case_id':case['id'],'decisions':{'9710000':{'result':'executed' if executed else 'absent','actualAt':now-4 if executed else None}},'positions':positions,'positions_verified':True}
        preview=ok(manager.post('/api/rotation-board/timeout/preview',json=body))
        assert ok(manager.get('/api/rotation-board/board'))['day']['timeoutCases']
        commit={**preview['request'],'notified':True}
        # A changed task/line snapshot cannot be overwritten by an old preview.
        assert manager.post('/api/rotation-board/act',json={**commit,'case_snapshot':'outdated'}).status_code==409
        ok(manager.post('/api/rotation-board/act',json=commit))
        assert ok(manager.post('/api/rotation-board/act',json=commit))['duplicate']
        finished=ok(manager.get('/api/rotation-board/board'))['day']
        assert finished['timeoutCases']==[]
        original=next(p for p in finished['persons'] if p['pid']=='9710000')
        assert (original['state']=='onpost') is executed
        second=next(p for p in finished['persons'] if p['pid']=='9710010')
        assert second['task'] is not None, str({'second':second,'updates':preview['updates'],'waiting':finished.get('timeoutWaitingMatch')})
        assert second['task']['status']=='assigned' and second['task']['deadlineAt']==second['task']['allowedAt']+3
        assert not any(x['type']=='rotation_timeout_related' for x in ok(manager.get('/api/action-center'))['items'])
        if executed:
            undo=ok(manager.post('/api/rotation-board/undo/preview',json={'line':case['line']}))
            ok(manager.post('/api/rotation-board/act',json={'action':'restore_line','line':case['line'],
                'checkpoint':undo['checkpoint'],'expected_snapshot':undo['expected_snapshot'],'notified':True}))
            reopened=ok(manager.get('/api/rotation-board/board'))['day']
            assert reopened['timeoutCases'] and next(p for p in reopened['persons'] if p['pid']=='9710000')['timeoutCase']
