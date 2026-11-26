"""Read-only transfer fairness: reference push counts and a bounded known-crew projection."""
from copy import deepcopy
import hashlib
import json
import math

from app.rotation import forecast as F
from app.rotation.engine import Engine, ActionError
from app.rotation.manual import Hooks, fingerprint
from app.rotation.post_transfer import transfer

SIMULATION_LIMIT = 24
HORIZON = 60


def digest(eng, body):
    value = [fingerprint(eng.d), eng.S, body.get('line'), body.get('post_key'),
             body.get('target_line'), body.get('target_i'), 'fairness-v1']
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def private_engine(eng):
    hooks = Hooks({})
    # Read the raw historical base only; F.plan adds today's completions itself.
    reader = getattr(eng.h, 'push7_history', None)
    history = deepcopy(eng.d.get('push7History', {})) if 'push7History' in eng.d else deepcopy(reader() if reader else {})
    hooks.push7_history = lambda: deepcopy(history)
    return Engine(deepcopy(eng.d), deepcopy(eng.S), hooks)


def reference_positions(eng, line_ids, now):
    rows = {}
    for line in eng.d['lines']:
        if line['id'] not in line_ids:
            continue
        positions = F.indexes(line, now)
        vacancies = 0
        for n, index in enumerate(positions):
            post = line['posts'][index]
            pid = F.occupant(eng, post, now)
            person = eng.P.get(pid)
            if not pid:
                vacancies += 1
            if person and person['state'] == 'onpost':
                start = F.standing_since(person, now)
                age = now - start if math.isfinite(start) and not person.get('timingUnverified') else None
                rows[pid] = {'pid': pid, 'name': person['name'], 'line': line['id'], 'post': post['name'],
                             'position': index + 1, 'minutes': age,
                             'pushes': len(positions) - n + vacancies}
    return rows


def impacts(before, after):
    rows = []
    for pid, old in before.items():
        new = after.get(pid)
        if not new:
            continue
        rows.append({**old, 'beforeLine': old['line'], 'afterLine': new['line'],
                     'beforePosition': old['position'], 'afterPosition': new['position'],
                     'beforePushes': old['pushes'], 'afterPushes': new['pushes'],
                     'delta': new['pushes'] - old['pushes']})
    return sorted(rows, key=lambda r: (r['delta'] <= 0, -(r['minutes'] if r['minutes'] is not None else math.inf), r['pid']))


def score(rows, index):
    delayed = [row for row in rows if row['delta'] > 0]
    age = lambda row: row['minutes'] if row['minutes'] is not None else math.inf
    return (max((age(r) for r in delayed), default=0),
            sum((age(r) + 1) * r['delta'] for r in delayed),
            max((r['delta'] for r in delayed), default=0), len(delayed), index)


def project(eng, crew, now):
    """Each known employee enters at most once; never invent arrivals or rest confirmations."""
    trial = private_engine(eng)
    F.plan(trial, now)
    rest, departures, skipped = [], [], 0
    cursor = now
    for step, pid in enumerate(crew[:SIMULATION_LIMIT], 1):
        person = trial.P[pid]
        at = max(cursor, F.readiness(person))
        assignment, _ = F.confirmation_plan(trial, pid, at)
        if assignment and assignment.get('notBefore', 0) > at:
            at = assignment['notBefore']
            if at <= now + HORIZON:
                assignment, _ = F.confirmation_plan(trial, pid, at)
        if not assignment or at > now + HORIZON or not F.eligible(trial, person, at):
            skipped += 1
            continue
        moves = F.route(trial, assignment, at)
        for move in moves:
            if move['to'] is not None:
                continue
            outgoing = trial.P[move['pid']]
            outcome = F.exit_outcome(trial, outgoing, move, at)
            event = {'pid': outgoing['pid'], 'name': outgoing['name'], 'line': assignment['line'],
                     'outcome': outcome, 'at': at, 'step': step}
            departures.append(event)
            if outcome == 'rest':
                rest.append(event)
        F.execute_moves(trial, person, assignment, moves, at, 'preview')
        F.plan(trial, at)
        cursor = at
    return {'rest': rest, 'departures': departures, 'crewCount': min(len(crew), SIMULATION_LIMIT),
            'limited': len(crew) > SIMULATION_LIMIT or skipped > 0}


def preview(eng, body, now):
    source, target = eng.line(body.get('line')), eng.line(body.get('target_line'))
    line_ids = {source['id'], target['id']}
    before = reference_positions(eng, line_ids, now)
    options = []
    for index in range(len(target['posts']) + 1):
        trial = private_engine(eng)
        transfer(trial, {**body, 'target_i': index}, now, 'preview')
        rows = impacts(before, reference_positions(trial, line_ids, now))
        options.append((score(rows, index), index))
    recommended = min(options)[1]
    selected = body.get('target_i', recommended)
    trial = private_engine(eng)
    transfer(trial, {**body, 'target_i': selected}, now, 'preview')
    rows = impacts(before, reference_positions(trial, line_ids, now))
    crew = [p['pid'] for p in F.candidates(eng, now) if F.readiness(p) <= now + HORIZON]
    return {'selectedIndex': selected, 'recommendedIndex': recommended, 'impacts': rows,
            'before': project(eng, crew, now), 'after': project(trial, crew, now),
            'digest': digest(eng, {**body, 'target_i': selected}), 'snapshot': fingerprint(eng.d)}


def payload(db, attraction_id, body):
    from app.rotation import service as S
    cfg = S.load_config(db, attraction_id)
    day, now = S.clock_now(cfg)
    row = S.day_row(db, attraction_id, day)
    if row is None or row.status != 'live':
        raise ActionError('今天的轮岗尚未运行')
    state = deepcopy(S.day_state(row))
    if not body.get('expected_snapshot') or body['expected_snapshot'] != fingerprint(state):
        raise S.ConfirmationConflict('站位已变化，请重新打开移岗预览')
    eng, _ = S.engine_for(db, cfg, row, state)
    return preview(eng, body, now)
