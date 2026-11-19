"""Archived rotation states remain viewable without their original engine."""
from copy import deepcopy
import json

from app.rotation import daily as D, service as S
from app.rotation.models import RotationConfig, RotationDay, RotationEvent, RotationSnapshot
from test_rotation_transaction_boundaries import DAY, rotation


def archived_state():
    return {
        'date': DAY, 'status': 'live', 'ruleVersion': 1, 'lastTick': 480, 'taskWorkflow': True,
        'lines': [{'id': 'A', 'active': True, 'posts': [{'key': 'old-entry', 'name': '旧入口', 'open': True,
                                                      'occ': '1', 'since': 450, 'hold101': {'since': 465}}]}],
        'persons': {
            '1': {'pid': '1', 'name': '原上岗', 'state': 'onpost', 'role': 'rotation', 'line': 'A',
                  'start': 435, 'end': 960, 'lineStart': 450},
            '2': {'pid': '2', 'name': '接替人员', 'state': 'ready', 'role': 'rotation', 'start': 435, 'end': 990,
                  'readyAt': 478, 'assign': {'line': 'A', 'target': '1', 'taskId': 'saved', 'departAt': 480},
                  'confirmationTokens': {'depart': 'unusable-legacy-token'}},
        },
        'plan': {'crew': {'A#0': '1'}, 'seven': {}, 'lost': [], 'push7': []},
        'rotationTasks': {'saved': {'id': 'saved', 'line': 'A', 'status': 'assigned', 'owner': '2',
                                    'allowedAt': 480, 'deadlineAt': 483, 'template': {'mode': 'chain'},
                                    'moves': [{'pid': '2', 'from': None, 'to': 'old-entry'},
                                              {'pid': '1', 'from': 'old-entry', 'to': None, 'outcome': 'done'}]}},
    }


def store(db, attraction_id, state):
    row = RotationDay(attraction_id=attraction_id, work_date=DAY, status=state['status'], state_json=json.dumps(state))
    db.add(row)
    db.flush()
    return row


def test_old_snapshots_use_stored_time_order_routes_and_occupants(rotation, monkeypatch):
    aid = rotation.attraction_id
    state = archived_state()
    later = deepcopy(state)
    later['lines'][0]['posts'][0]['name'] = '末次岗位'
    with rotation.factory() as db, S.transaction(db):
        row = store(db, aid, later)
        persisted = row.state_json
        D.capture_snapshot(db, aid, state, {}, 480)
        D.capture_snapshot(db, aid, later, {}, 480)
        db.flush()
        snapshots = db.query(RotationSnapshot).order_by(RotationSnapshot.id).all()
        first_id, latest_id = (snapshot.id for snapshot in snapshots)

    def unexpected_engine(*args, **kwargs):
        raise AssertionError('档案读取不应创建正在运行的轮岗引擎')

    monkeypatch.setattr(S, 'advance', unexpected_engine)
    monkeypatch.setattr(S, 'engine_for', unexpected_engine)
    with rotation.factory() as db:
        opening = D.day_payload(db, aid, DAY)
        replay = D.day_payload(db, aid, DAY, '08:00')
        assert opening['opening_source'] == 'earliest_record' and opening['as_of'] == 480
        assert opening['day']['lines'][0]['posts'][0]['name'] == '旧入口'
        assert replay['times'] == [{'id': first_id, 'minute': 480}, {'id': latest_id, 'minute': 480}]
        assert replay['as_of'] == 480 and replay['day']['lines'][0]['posts'][0]['name'] == '末次岗位'
        assert replay['day']['readOnly'] and not replay['can_edit'] and not replay['can_generate']
        posted, ready = replay['day']['persons']
        assert posted['post'] == '末次岗位' and posted['continuousMinutes'] == 30
        assert ready['assign']['entryPost'] == '末次岗位' and ready['task']['deadlineAt'] == 483
        assert ready['departureRoutes'][-1]['to'] == '下班' and ready['task']['afterPosts'][0]['occ'] == '2'
        assert not ready['canDepart'] and 'confirmationTokens' not in ready
        assert D.day_payload(db, aid, DAY, '07:59')['day'] is None
        assert S.day_row(db, aid, DAY).state_json == persisted
        assert db.query(RotationSnapshot).count() == 2 and db.query(RotationEvent).count() == 0


def test_legacy_retained_plan_without_snapshots_stays_on_original_seats(rotation, monkeypatch):
    aid = rotation.attraction_id
    state = archived_state()
    state.pop('ruleVersion')  # Earliest states did not record their version.
    initial = deepcopy(state)
    initial['status'] = 'draft'
    initial['plan']['crew']['A#0'] = '2'
    initial['lines'][0]['posts'][0]['name'] = '最初预排岗位'
    state['initialDraft'] = initial
    with rotation.factory() as db, S.transaction(db):
        row = store(db, aid, state)
        persisted = row.state_json

    def unexpected_engine(*args, **kwargs):
        raise AssertionError('原始预排仅用于显示，不应调用发布排班')

    monkeypatch.setattr(S.E.Engine, 'publish', unexpected_engine)
    with rotation.factory() as db:
        result = D.day_payload(db, aid, DAY)
        assert result['frozen'] and result['legacy_history'] and result['opening_source'] == 'initial_draft'
        assert not result['can_edit'] and not result['can_publish']
        assert result['day']['lines'][0]['posts'][0]['name'] == '最初预排岗位'
        assert result['day']['lines'][0]['posts'][0]['occ'] == '2'
        assert result['day']['lines'][0]['posts'][0]['prearranged']
        assert all(person['state'] == 'notyet' and person['lineStart'] is None for person in result['day']['persons'])
        assert S.day_row(db, aid, DAY).state_json == persisted
        assert db.query(RotationSnapshot).count() == 0


def test_snapshot_only_archive_does_not_create_rotation_config(rotation):
    aid = rotation.attraction_id
    with rotation.factory() as db, S.transaction(db):
        db.query(RotationConfig).filter_by(attraction_id=aid).delete()
        D.capture_snapshot(db, aid, archived_state(), {}, 480)

    with rotation.factory() as db:
        month = D.month_payload(db, aid, DAY[:7])
        result = D.day_payload(db, aid, DAY, '08:00')
        assert month['month'] == DAY[:7]
        assert result['day']['lines'][0]['posts'][0]['occ'] == '1'
        assert result['revision'] == 0 and result['as_of'] == 480
        assert not result['can_edit'] and not result['can_generate']
        assert db.query(RotationConfig).filter_by(attraction_id=aid).count() == 0
