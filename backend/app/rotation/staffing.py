"""Read-only short-term capacity warning, not a promise of a completed handover."""
from app.rotation import forecast as F
from app.rotation.engine import rotation_end, _m


def warnings(eng, now, horizon=60):
    if eng.d.get('status') != 'live' or eng.d.get('closed'):
        return []
    until = min(now + horizon, rotation_end(eng.d))
    posts = [(line, post) for line in eng.d['lines'] if line.get('active')
             for post in line['posts'] if post.get('open')]
    occupied = {post.get('occ') for _, post in posts} - {None}
    if not posts or now >= until:
        return []
    people = [p for p in eng.P.values() if p.get('role') in ('rotation', 'op') or p['pid'] in occupied]
    if people and not any(p['state'] == 'onpost' for p in people) and now < min(p['start'] for p in people):
        return []
    boundaries = [value for p in people for value in (p['start'], p['end'], p.get('readyAt'))
                  if isinstance(value, (int, float)) and now < value <= until]
    boundaries.extend(value for p in people for absence in p.get('absences', []) if not absence.get('done')
                      for value in (absence['start'], absence['end']) if now < value <= until)
    points = sorted({now, *boundaries})
    result = []
    previous = None
    for at in points:
        if at >= rotation_end(eng.d):
            continue
        retained = [(line, post) for line, post in posts
                    if not (post.get('occ') in eng.P and eng.P[post['occ']]['state'] == 'notyet'
                            and eng.P[post['occ']]['start'] > at)]
        required = len(retained)
        available, uncertain = [], []
        for p in people:
            if p.get('role') == 'op' and p['pid'] not in occupied and at < _m(eng.S['opReleaseAt']):
                continue
            if at >= p['end'] or (p['state'] != 'onpost' and at < p['start']) or p.get('manualPaused'):
                continue
            if p['state'] in {'sick', 'away', 'done', 'excluded', 'other', 'exception'}:
                continue
            if p.get('requestedExit') or any(not a.get('done') and a['start'] <= at < a['end'] for a in p.get('absences', [])):
                continue
            state = p['state']
            ready = p.get('readyAt')
            if (state in {'onpost', 'ready'} or state == 'notyet' and F.checked_in(p)
                    or state in {'rest', 'meal'} and ready is not None and ready <= at):
                available.append(p['pid'])
            elif state in {'notyet', 'pending', 'walkback', 'op'} or ready is None:
                uncertain.append({'pid': p['pid'], 'name': p['name'],
                                  'reason': '未签到' if state == 'notyet' else '待确认休息'})
        leaving = [{'pid': post['occ'], 'name': eng.P[post['occ']]['name'],
                    'line': line['id'], 'post': post['name'], 'end': eng.P[post['occ']]['end']}
                   for line, post in retained if post.get('occ') in eng.P and eng.P[post['occ']]['end'] <= at]
        gap = max(0, required - len(available))
        key = (gap, tuple(p['pid'] for p in leaving), tuple(p['pid'] for p in uncertain))
        if key == previous or not (gap or leaving):
            continue
        previous = key
        result.append({'at': at, 'requiredPosts': required, 'available': len(available), 'gap': gap,
                       'leaving': leaving, 'uncertain': uncertain})
    return result
