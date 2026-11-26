"""Read-only employee history from recorded transitions, never from sorting clocks."""
import base64
from datetime import date, timedelta
import json
import math
import zlib

from sqlalchemy import or_
from app.rotation.namespace import tables_for
from app.rotation.engine import ActionError


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def bounds(start, end):
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError, TypeError):
        raise ActionError('日期格式不正确')
    days = (b - a).days + 1
    if not 1 <= days <= 31:
        raise ActionError('请选择1至31天')
    return a, b, a - timedelta(days=days)


def search(db, aid, query, start, end):
    bounds(start, end)
    query = str(query or '').strip()[:100]
    if not query:
        return []
    T = tables_for(db)
    found = {}
    # Include independent/archived rotation identities, not just active PR accounts.
    escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    pattern = '%' + escaped + '%'
    rows = (db.query(T.RotationRosterEntry.employee_no, T.RotationRosterEntry.name)
            .filter(T.RotationRosterEntry.attraction_id == aid,
                    T.RotationRosterEntry.work_date.between(start, end),
                    or_(T.RotationRosterEntry.employee_no.like(pattern, escape='\\'),
                        T.RotationRosterEntry.name.like(pattern, escape='\\')))
            .distinct().order_by(T.RotationRosterEntry.employee_no).limit(12))
    for no, name in rows:
        found[no] = {'pid': no, 'name': name}
    # Today's retained board can include manually added employees without a roster row.
    for raw, in db.query(T.RotationDay.state_json).filter(T.RotationDay.attraction_id == aid,
                                                        T.RotationDay.work_date.between(start, end)):
        state = json.loads(raw)
        for no, p in state.get('persons', {}).items():
            if query.casefold() in (no + ' ' + p.get('name', '')).casefold():
                found[no] = {'pid': no, 'name': p.get('name', no)}
    return sorted(found.values(), key=lambda p: (p['pid'] != query, p['name'] != query, p['pid']))[:12]


def descriptor(state, pid):
    p = state.get('persons', {}).get(pid, {})
    if p.get('state') == 'onpost':
        slots = [(line, post) for line in state.get('lines', []) for post in line['posts'] if post.get('occ') == pid]
        if len(slots) == 1:
            line, post = slots[0]
            return ('post', line['id'], post.get('key') or post['name']), {
                'kind': 'post', 'line': line['id'], 'post': post['name'],
                'hint': p.get('lineStart') if p.get('lineStart') == post.get('since') else None,
                'continuous': p.get('continuousStart'), 'unverified': bool(p.get('timingUnverified'))}
    if p.get('state') in ('rest', 'meal', 'ready') and finite(p.get('breakStart')):
        kind = 'meal' if p.get('breakKind') == 'meal' else 'rest'
        return (kind, p['breakStart']), {'kind': kind, 'hint': p['breakStart']}
    return (p.get('state', 'missing'),), {'kind': 'other'}


def day_history(observations, pid, as_of, live):
    """Intervals have known boundaries only when both neighboring states were saved.

    A first already-occupied slot is deliberately left incomplete. Swaps/renames
    are read from occupancy transitions; entrySortAt and sortAt are never used.
    """
    rows, active, previous_key, previous_at = [], None, None, None
    previous_state = None
    rewind = False
    raw_at = None
    for at, state in observations:
        if finite(at):
            rewind |= raw_at is not None and at < raw_at
            raw_at = at
        if not finite(at) or at > as_of:
            continue
        if previous_at is not None and at < previous_at:
            rewind = True
            continue
        person_state = state.get('persons', {}).get(pid, {}).get('state')
        if person_state == 'exception' and active and active['kind'] in ('rest', 'meal'):
            # Timeout is waiting, not an actual departure or a second rest.
            active['exceptionWait'] = True
            previous_at, previous_state = at, person_state
            continue
        key, item = descriptor(state, pid)
        after_exception = previous_state == 'exception'
        previous_state = person_state
        if after_exception and active and item['kind'] == active['kind'] and item['kind'] in ('rest', 'meal'):
            # Recovery may reset breakStart, but no work happened between them.
            previous_key, previous_at = key, at
            continue
        if key == previous_key:
            previous_at = at
            continue
        hint = item.pop('hint', None)
        boundary = hint if previous_at is not None and finite(hint) and previous_at < hint <= at else at
        if active is not None:
            active['end'] = boundary
            rows.append(active)
        active = None
        if item['kind'] != 'other':
            # First observation alone cannot prove when this particular post began.
            known = (previous_at is not None and not item.get('unverified')
                     and not (after_exception and item['kind'] in ('rest', 'meal')))
            active = {**item, 'start': boundary, 'end': None, 'completeStart': known}
        previous_key, previous_at = key, at
    if active is not None:
        active.update(end=as_of, ongoing=live, incompleteEnd=not live)
        rows.append(active)
    if rewind:
        for row in rows: row['completeStart'] = False
    for row in rows:
        row['minutes'] = max(0, row['end'] - row['start']) if row['completeStart'] and not row.get('incompleteEnd') else None
    # Contiguous post intervals form one work bout, including cross-line moves.
    work, visits, last_work = None, [], None
    for row in rows:
        if row['kind'] == 'post':
            if work is None or row['start'] != work['end']:
                continuous = row.get('continuous')
                verified = finite(continuous) and continuous <= row['start'] and not row.get('unverified')
                work = {'start': continuous if verified else row['start'],
                        'known': verified or row['completeStart'], 'end': row['end']}
            else:
                work['end'] = row['end']; work['known'] &= not row.get('unverified')
            last_work = work
            if visits and visits[-1]['line'] == row['line'] and visits[-1]['end'] == row['start']:
                visit = visits[-1]; visit['end'] = row['end']; visit['known'] &= row['completeStart']
                visit['ongoing'] = bool(row.get('ongoing')); visit['incompleteEnd'] = bool(row.get('incompleteEnd'))
            else:
                visits.append({'line': row['line'], 'start': row['start'], 'end': row['end'],
                               'known': row['completeStart'], 'ongoing': bool(row.get('ongoing')),
                               'incompleteEnd': bool(row.get('incompleteEnd'))})
        else:
            row['beforeWork'] = max(0, last_work['end'] - last_work['start']) if last_work and last_work['known'] else None
            # The same work bout belongs only to its first following break.
            last_work = None
            work = None
    for visit in visits:
        visit['minutes'] = visit['end'] - visit['start'] if visit['known'] and not visit['incompleteEnd'] else None
    if rewind:
        for row in rows: row['beforeWork'] = None
    return {'rows': rows, 'visits': visits, 'rewound': rewind}


def union_intervals(intervals):
    merged = []
    for start, end in sorted((a, b) for a, b in intervals if finite(a) and finite(b) and b > a):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def accounting_gaps(rows, segments):
    """Use authoritative line accounting only outside already-measured post time.

    Deduplicate source segments too. These intervals do not establish a whole
    line visit or a post/rest assignment, so they never enter those averages.
    """
    gaps = []
    for line in sorted({s['line'] for s in segments}):
        covered = union_intervals((r['start'], r['end']) for r in rows
                                  if r['kind'] == 'post' and r['line'] == line and r['minutes'] is not None)
        recorded = union_intervals((s['start'], s['end']) for s in segments if s['line'] == line)
        for start, end in recorded:
            cursor = start
            for left, right in covered:
                if right <= cursor: continue
                if left >= end: break
                if left > cursor:
                    gaps.append({'line': line, 'start': cursor, 'end': left, 'minutes': left - cursor})
                cursor = max(cursor, right)
                if cursor >= end: break
            if cursor < end:
                gaps.append({'line': line, 'start': cursor, 'end': end, 'minutes': end - cursor})
    return gaps


def summary(days):
    rows = [r for d in days for r in d['rows']]
    rests = [r for r in rows if r['kind'] == 'rest' and r['minutes'] is not None and not r.get('ongoing')]
    works = [r['beforeWork'] for r in rows if r['kind'] in ('rest', 'meal') and r.get('beforeWork') is not None]
    avg = lambda xs: sum(xs) / len(xs) if xs else None
    return {'days': sum(bool(d['rows'] or d['fallback']) for d in days),
            'avgWork': avg(works), 'workSamples': len(works),
            'avgRest': avg([r['minutes'] for r in rests]), 'restSamples': len(rests),
            'uncertain': sum(r['minutes'] is None for r in rows),
            'postMinutes': (sum(r['minutes'] for r in rows if r['kind'] == 'post' and r['minutes'] is not None)
                            + sum(r['minutes'] for d in days for r in d['fallback']))}


def payload(db, aid, pid, start, end):
    from app.rotation import service as S
    a, b, previous = bounds(start, end)
    T = tables_for(db)
    cfg = db.query(T.RotationConfig).filter_by(attraction_id=aid).first()
    clock_day, now = S.clock_now(cfg) if cfg else (date.today().isoformat(), 0)
    observations, names, damaged = {}, {}, set()
    records = (db.query(T.RotationSnapshot.work_date, T.RotationSnapshot.minute, T.RotationSnapshot.payload)
               .filter(T.RotationSnapshot.attraction_id == aid, T.RotationSnapshot.work_date.between(previous.isoformat(), end))
               .order_by(T.RotationSnapshot.work_date, T.RotationSnapshot.id).yield_per(100))
    for day, minute, raw in records:
        try:
            state = json.loads(zlib.decompress(base64.b64decode(raw)))['state']
        except (ValueError, KeyError, TypeError, zlib.error):
            damaged.add(day)
            continue
        # Retain just this person's slot and state, never an entire month's rosters.
        p = state.get('persons', {}).get(pid)
        if p:
            p = {key: p.get(key) for key in ('name', 'state', 'lineStart', 'continuousStart',
                  'breakStart', 'breakKind', 'timingUnverified')}
        slim = {'persons': {pid: p} if p else {}, 'lines': [
            {'id': line['id'], 'posts': [{key: post.get(key) for key in ('occ', 'key', 'name', 'since')}
                                       for post in line['posts'] if post.get('occ') == pid]}
            for line in state.get('lines', [])]}
        observations.setdefault(day, []).append((minute, slim))
        if p: names[day] = p.get('name', pid)
    current_rows = db.query(T.RotationDay).filter(T.RotationDay.attraction_id == aid,
                                                 T.RotationDay.work_date.between(previous.isoformat(), end)).all()
    statuses = {}
    for saved in current_rows:
        state = json.loads(saved.state_json)
        statuses[saved.work_date] = (saved.status, float(state.get('lastTick', 0)))
        if pid in state.get('persons', {}): names[saved.work_date] = state['persons'][pid].get('name', pid)
    segments = db.query(T.RotationSegment).filter(T.RotationSegment.attraction_id == aid,
                    T.RotationSegment.employee_no == pid, T.RotationSegment.work_date.between(previous.isoformat(), end)).all()
    grouped = {}
    for seg in segments:
        grouped.setdefault(seg.work_date, []).append({'line': seg.line, 'start': seg.start_min, 'end': seg.end_min, 'minutes': seg.minutes})
    days = []
    for day in sorted(set(observations) | set(grouped)):
        status, tick = statuses.get(day, ('ended', max((t for t, _ in observations.get(day, [])), default=0)))
        live = day == clock_day and status == 'live'
        as_of = max(tick, now) if live else tick
        history = day_history(observations.get(day, []), pid, as_of, live)
        if day in damaged:
            for row in history['rows']:
                row['minutes'] = None
                row['beforeWork'] = None
            for visit in history['visits']: visit['minutes'] = None
        # Fill only uncovered recorded intervals, without inventing posts/rests.
        fallback = accounting_gaps(history['rows'], grouped.get(day, []))
        days.append({'date': day, **history, 'fallback': fallback, 'damaged': day in damaged,
                     'asOf': as_of, 'live': live})
    selected = [d for d in days if start <= d['date'] <= end]
    prior = [d for d in days if d['date'] < start]
    lines = {}
    for d in selected:
        for row in d['rows']:
            if row['kind'] == 'post':
                item = lines.setdefault(row['line'], {'line': row['line'], 'minutes': 0, 'samples': []})
                if row['minutes'] is not None: item['minutes'] += row['minutes']
        for v in d['visits']:
            item = lines.setdefault(v['line'], {'line': v['line'], 'minutes': 0, 'samples': []})
            if v['minutes'] is not None:
                if not v['ongoing']: item['samples'].append(v['minutes'])
        for v in d['fallback']:
            lines.setdefault(v['line'], {'line': v['line'], 'minutes': 0, 'samples': []})['minutes'] += v['minutes']
    for item in lines.values():
        samples = item.pop('samples'); item['count'] = len(samples)
        item['average'] = sum(samples) / len(samples) if samples else None
        old = [v['minutes'] for d in prior for v in d['visits']
               if v['line'] == item['line'] and v['minutes'] is not None and not v['ongoing']]
        item['previousAverage'] = sum(old) / len(old) if old else None
    return {'employee_no': pid, 'name': names[max(names)] if names else pid,
            'start': start, 'end': end, 'previous': [previous.isoformat(), (a - timedelta(days=1)).isoformat()],
            'summary': summary(selected), 'priorSummary': summary(prior),
            'lines': sorted(lines.values(), key=lambda l: l['line']), 'days': selected}
