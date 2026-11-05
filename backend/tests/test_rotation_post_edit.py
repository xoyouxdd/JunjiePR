"""Restored V2 post editing uses shared post operations and protected identities."""
import json

import pytest

from test_rotation_forecast_api import rotation, current, board, act, person, DAY
from app.rotation import service as S
from app.rotation.models import RotationEvent


def swap_body(r, mode='posts', target_line='A', target_i=1):
    day = board(r)
    source = day['lines'][0]['posts'][0]
    target = next(L for L in day['lines'] if L['id'] == target_line)['posts'][target_i]
    return {'action': 'post_order', 'line': 'A', 'i': 0, 'target_line': target_line, 'target_i': target_i,
            'swapMode': mode, 'source_post_key': source['key'], 'target_post_key': target['key'],
            'expected_snapshot': day['forecastSnapshot']}


def test_rename_preserves_people_timing_and_tokens_and_refreshes_route_names(current):
    r = current; before = board(r); own = person(r); post = before['lines'][0]['posts'][0]
    with r.factory() as db:
        cfg = S.load_config(db, r.attraction_id); config = (cfg.lines_json, cfg.settings_json)
    act(r, {'action': 'rename_post', 'post_key': post['key'], 'expected_name': post['name'], 'name': '新入口<&>'})
    after = board(r); changed = after['lines'][0]['posts'][0]
    assert changed['name'] == '新入口<&>' and changed['key'] == post['key'] and changed['occ'] == post['occ']
    assert person(r)['confirmationTokens'] == own['confirmationTokens'] and person(r)['readyAt'] == own['readyAt']
    assert any(p['name'] == '新入口<&>' for p in person(r)['task']['beforePosts'])
    assert [L['sortAt'] for L in after['lines']] == [L['sortAt'] for L in before['lines']]
    with r.factory() as db:
        event = db.query(RotationEvent).filter_by(event_type='rename_post').one()
        assert json.loads(event.detail_json)['from'] == post['name']
        assert json.loads(event.detail_json)['to'] == '新入口<&>'
        cfg = S.load_config(db, r.attraction_id); assert (cfg.lines_json, cfg.settings_json) == config


@pytest.mark.parametrize('name', ['', '   ', None, 123, '长'*41])
def test_invalid_rename_rolls_back(current, name):
    r = current; before = board(r); post = before['lines'][0]['posts'][0]
    with pytest.raises(S.ActionError): act(r, {'action': 'rename_post', 'post_key': post['key'], 'name': name})
    assert board(r)['lines'] == before['lines']


def test_duplicate_and_stale_name_do_not_overwrite(current):
    r = current; before = board(r); post = before['lines'][0]['posts'][0]
    duplicate = before['lines'][0]['posts'][1]['name']
    with pytest.raises(S.ActionError):act(r, {'action': 'rename_post', 'post_key': post['key'], 'name': duplicate})
    act(r, {'action': 'rename_post', 'post_key': post['key'], 'expected_name': post['name'], 'name': '已更新'})
    with pytest.raises(S.ConfirmationConflict):act(r, {'action': 'rename_post', 'post_key': post['key'], 'expected_name': post['name'], 'name': '旧页面覆盖'})
    assert board(r)['lines'][0]['posts'][0]['name'] == '已更新'


@pytest.mark.parametrize('mode', ['posts', 'with_people'])
@pytest.mark.parametrize('target_line', ['A', 'B'])
def test_same_and_cross_line_modes_keep_occupants_or_move_them_with_posts(current, mode, target_line):
    r = current
    if target_line == 'B':
        # The base API fixture repeats names on both lines. Make successful
        # cross-line cases distinct; duplicate-name rejection stays protected.
        for line in board(r)['lines']:
            for post in line['posts']:
                act(r, {'action': 'rename_post', 'post_key': post['key'], 'name': line['id']+'-'+post['name']})
    before = board(r); own = person(r); body = swap_body(r, mode, target_line)
    left = before['lines'][0]['posts'][0]
    right = next(L for L in before['lines'] if L['id'] == target_line)['posts'][1]
    act(r, body); after = board(r)
    actual_left = after['lines'][0]['posts'][0]
    actual_right = next(L for L in after['lines'] if L['id'] == target_line)['posts'][1]
    assert (actual_left['name'], actual_left['key']) == (right['name'], right['key'])
    assert (actual_right['name'], actual_right['key']) == (left['name'], left['key'])
    assert actual_left['occ'] == (left['occ'] if mode == 'posts' else right['occ'])
    assert actual_right['occ'] == (right['occ'] if mode == 'posts' else left['occ'])
    assert person(r)['readyAt'] == own['readyAt'] and person(r)['confirmationTokens'] == own['confirmationTokens']
    posted = [p['occ'] for L in after['lines'] for p in L['posts'] if p['occ']]
    assert len(posted) == len(set(posted)) == 4
    if mode == 'with_people' and target_line == 'B':
        assert person(r, left['occ'])['line'] == 'B' and person(r, right['occ'])['line'] == 'A'
    assert all(person(r, pid)['state'] == 'onpost' for pid in posted)


def test_stale_swap_snapshot_is_rejected(current):
    r = current; body = swap_body(r)
    post = board(r)['lines'][0]['posts'][0]
    act(r, {'action': 'rename_post', 'post_key': post['key'], 'name': '已经改名'})
    before = board(r)['lines']
    with pytest.raises(S.ConfirmationConflict):act(r, body)
    assert board(r)['lines'] == before


@pytest.mark.parametrize('key', ['source_post_key', 'target_post_key'])
def test_wrong_stable_post_identity_is_rejected(current, key):
    r = current; body = swap_body(r); body[key] = 'old-key'; before = board(r)['lines']
    with pytest.raises(S.ConfirmationConflict):act(r, body)
    assert board(r)['lines'] == before
