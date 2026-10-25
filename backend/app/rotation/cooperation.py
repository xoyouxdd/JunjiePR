"""Same-line, same-time cohort plans. Projections never mutate the live board or hooks."""
from copy import deepcopy
import hashlib
import json

from app.rotation import engine as E


class PreviewHooks:
    def __init__(self, hooks):
        self.hooks = hooks

    def log(self, *args):
        pass

    def segment(self, *args):
        pass

    def week_minutes(self):
        return self.hooks.week_minutes()


def clone(eng):
    state = deepcopy(eng.d)
    state['cooperativeEntry'] = False
    return E.Engine(state, dict(eng.S), PreviewHooks(eng.h))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def first_entry(p):
    return p.get('role') == 'rotation' and not p.get('visited')


def order(p):
    a = p['assign']
    return (0 if a.get('mode') == 'push7' else 1 if a.get('mode') == 'chain' else 2,
            a['departAt'], p['start'], p['pid'])


def members(eng, p):
    a = p.get('assign')
    if not eng.d.get('cooperativeEntry') or not a or p.get('entryHold'):
        return []
    return sorted([q for q in eng.P.values() if q.get('role') in ('rotation', 'op') and not q.get('entryHold')
                   and q.get('state') in ('ready', 'rest', 'meal') and q.get('assign')
                   and q['assign']['line'] == a['line']
                   and abs(q['assign']['departAt'] - a['departAt']) < .01], key=order)


def simulate(eng, people, now, solo=None):
    trial = clone(eng)
    jobs = people
    if solo:
        # The earliest required relief stays ahead of an ordinary rotation.
        jobs = [eng.P[solo]]
        trial.P[solo]['assign'] = deepcopy(people[0]['assign'])
    at = max([now] + [max(trial.P[q['pid']].get('readyAt') or now, trial.P[q['pid']]['assign']['departAt'],
                         trial.P[q['pid']]['assign'].get('notBefore', 0)) for q in jobs])
    # Rehearse scheduled openings at the projected execution time without
    # reassigning anyone or advancing the live board/other timers.
    for line in trial.d['lines']:
        for post in line['posts']:
            if (post.get('openAt') and not post.get('opened') and E.hm(post['openAt']) <= at
                    and line.get('active') and not post.get('manual') and not trial.d.get('closed')):
                post.update(open=True, opened=True)
    before = {pid: (L['id'], x['name']) for L in trial.d['lines']
              for x in L['posts'] if (pid := x.get('occ'))}
    for q in jobs:
        t = trial.P[q['pid']]
        if not trial.can_board(t, at + trial.walk(t['assign']['line']), now):
            raise E.ActionError('人员已接近下班或出圈时间，请重新安排')
        t['state'] = 'ready'
        trial.act_depart(t['pid'], at, 'preview')
    if any(trial.P[q['pid']]['state'] != 'onpost' for q in jobs):
        raise E.ActionError('本线岗位不足，不能让同行人员刚上岗就被推出，请调整安排')
    after = {pid: (L['id'], x['name']) for L in trial.d['lines']
             for x in L['posts'] if (pid := x.get('occ'))}
    routes = []
    for pid in dict.fromkeys([q['pid'] for q in jobs] + list(before)):
        if before.get(pid) != after.get(pid):
            routes.append({'pid': pid, 'name': trial.P[pid]['name'],
                           'from': before.get(pid, (None, '待上岗'))[1],
                           'to': after.get(pid, (None, E_STATE.get(trial.P[pid]['state'], '离线')))[1]})
    return routes


E_STATE = {'done': '下班', 'away': '暂离／出圈', 'walkback': '下线，回休息室确认休息／吃饭'}


def plan(eng, p, now):
    group = members(eng, p)
    if len(group) < 2:
        return None
    line = eng.line(p['assign']['line'])
    context = {'epoch': eng.d.get('confirmationEpoch'), 'date': eng.d['date'],
               'closed': eng.d.get('closed'), 'settings': eng.S, 'line': line,
               'occupants': [{k: eng.P[x['occ']].get(k) for k in ('pid', 'state', 'end', 'absences', 'lineStart')}
                             for x in line['posts'] if x.get('occ')],
               'people': [{k: q.get(k) for k in ('pid', 'start', 'end', 'readyAt', 'assign', 'absences', 'visited')}
                          for q in group]}
    result = {'line': line['id'], 'at': max(q['assign']['departAt'] for q in group),
              'members': [{'pid': q['pid'], 'name': q['name'], 'start': q['start'], 'end': q['end'],
                           'task': q['assign'].get('why') or {'push7': '推7点', 'chain': '推下班／出圈', 'opening': '补岗', 'push': '正常轮岗'}.get(q['assign']['mode'], '上岗'),
                           'confirmed': False} for q in group],
              'confirmed': 0, 'routes': [], 'soloRoutes': [], 'blocked': None, 'soloBlocked': None}
    try:
        result['routes'] = simulate(eng, group, now)
        for member in result['members']:
            route=next((r for r in result['routes'] if r['pid']==member['pid']),None)
            member['entryPost']=route['to'] if route else None
    except E.ActionError as exc:
        result['blocked'] = str(exc)
    context.update(routes=result['routes'], blocked=result['blocked'])
    key = digest(context)
    votes = eng.d.get('entryVotes', {}).get(key, {})
    result.update(id=key, confirmed=len(votes))
    for member in result['members']:
        member['confirmed'] = member['pid'] in votes
    try:
        result['soloRoutes'] = simulate(eng, group, now, solo=p['pid'])
    except E.ActionError as exc:
        result['soloBlocked'] = str(exc)
    return result


def confirm(eng, pid, now, actor, token, solo=False, missing=None):
    """Called under the existing DB transaction/lock; returns pending or executed."""
    p = eng.P[pid]
    view = plan(eng, p, now)
    if not view:
        if solo:
            raise E.ActionError('同行任务已变化，请刷新后重新确认')
        if p.get('assign') and not eng.can_board(p, now + eng.walk(p['assign']['line']), now):
            raise E.ActionError('已接近下班或出圈时间，请重新安排')
        eng.act_depart(pid, now, actor)
        return {'status': 'executed', 'participants': [pid]}
    blocked = view['soloBlocked'] if solo else view['blocked']
    if blocked:
        raise E.ActionError(blocked)
    group = members(eng, p)
    # Only the actual staff member can cast their vote, including through /act.
    if not getattr(actor, 'formal', False) or getattr(getattr(actor, 'entered_by', None), 'employee_no', None) != pid:
        raise E.ActionError('协同上岗必须由本人在自己的账号确认')
    if p['state'] != 'ready' or now < max(p.get('readyAt') or now, p['assign']['departAt'], p['assign'].get('notBefore', 0)) - .01:
        raise E.ActionError('尚未到本人允许上岗时间')
    votes = eng.d.setdefault('entryVotes', {}).setdefault(view['id'], {})
    if solo:
        peers = sorted(q['pid'] for q in group if q['pid'] != pid)
        if not isinstance(missing, list) or any(not isinstance(v, str) for v in missing) or sorted(missing) != peers:
            raise E.ActionError('请明确确认本组其他同行人未到场')
        if any(q in votes for q in peers):
            raise E.ActionError('同行人已确认到位，不能标记其未到场，请联系主管核对')
        job = deepcopy(group[0]['assign'])
        if now < job['departAt'] - .01 or now < job.get('notBefore', 0) - .01:
            raise E.ActionError('还没到接替任务的上岗时间')
        for q in group:
            q['assign'] = None
            if q['pid'] != pid:
                # No absence/attendance judgement: hold only this first-entry task.
                q['entryHold'] = {'by': pid, 'at': now, 'reason': '同行人反馈暂未到场'}
        p['assign'] = job
        eng.act_depart(pid, now, actor)
        for old_token in votes.values():
            previous = eng.d.get('confirmations', {}).get(old_token)
            if previous:
                previous['status'] = 'superseded'
        eng.h.log('entry_solo', actor, pid, view['line'], None, {'missing': peers, 'routes': view['soloRoutes']})
        return {'status': 'executed', 'participants': [pid], 'single': True}
    votes[pid] = token
    if len(votes) != len(group):
        eng.h.log('entry_ready', actor, pid, view['line'], None, {'group': view['id']})
        return {'status': 'waiting', 'participants': [pid], 'group': view['id']}
    # Rehearse with current time before touching any live post.
    simulate(eng, group, now)
    for q in group:
        if q['state'] != 'ready' or now < max(q.get('readyAt') or now, q['assign']['departAt'], q['assign'].get('notBefore', 0)) - .01:
            raise E.ActionError('同行人当前尚不满足上岗条件，请刷新')
    for q in group:
        eng.act_depart(q['pid'], now, actor)
    return {'status': 'executed', 'participants': [q['pid'] for q in group], 'group': view['id'], 'tokens': dict(votes)}


def confirm_group(eng, pid, now, actor, expected):
    """An authorized board action is explicit whole-group confirmation, not a personal vote."""
    from app.rotation.service import ConfirmationConflict, confirmation_token, assignment_view
    if getattr(actor,'kind',None) not in ('screen','supervisor','manager'):
        raise E.ActionError('个人待办必须逐人确认，不能代替整组上岗')
    p=eng.P[pid];view=plan(eng,p,now)
    if not view or view['id']!=expected:
        raise ConfirmationConflict('协同岗位安排已变化，请刷新后重新确认')
    if view['blocked']:
        raise E.ActionError(view['blocked'])
    group=members(eng,p)
    for member in group:
        if member['state']!='ready' or now < max(member.get('readyAt') or now,member['assign']['departAt'],member['assign'].get('notBefore',0))-.01:
            raise E.ActionError('组员休息未结束或尚未到上岗时间，请单独预览强制安排')
    simulate(eng,group,now)
    votes=eng.d.get('entryVotes',{}).get(view['id'],{})
    tokens={member['pid']:votes.get(member['pid']) or confirmation_token(eng,member,'depart') for member in group}
    assignments={member['pid']:assignment_view(eng,member) for member in group}
    for member in group:
        eng.act_depart(member['pid'],now,actor)
    eng.h.log('entry_group',actor,pid,view['line'],None,{'group':view['id'],'participants':list(tokens),'routes':view['routes']})
    return {'status':'executed','participants':list(tokens),'group':view['id'],'tokens':tokens,'assignments':assignments}


def upcoming_shift(p, now):
    """Opening 07:00/07:15 crews use their normal areas, not later-shift forecasts."""
    return bool(p.get('state') == 'notyet' and p['start'] > 435 and 0 < p['start'] - now <= 30)


def upcoming(eng, now):
    """The ten-minute route is a forecast, never a reservation or timed action."""
    result = {}
    if not eng.d.get('cooperativeEntry') or eng.d.get('closed'):
        return result
    times = sorted({p['start'] for p in eng.P.values() if upcoming_shift(p, now) and p['start'] <= now + 10
                    and first_entry(p) and not p.get('entryHold')})
    for at in times:
        projected = clone(eng)
        projected.tick(at)
        projected.d['cooperativeEntry'] = True
        for p in eng.P.values():
            q = projected.P[p['pid']]
            if p['state'] == 'notyet' and p['start'] == at and q['state'] == 'ready' and q.get('assign'):
                a = deepcopy(q['assign'])
                line = projected.line(a['line'])
                indices = projected.open_posts(line)
                target_line, target_index = projected.find_post(a.get('target')) if a.get('target') else (None, None)
                entry = target_index if a['mode'] == 'push7' and target_line is line else (indices[0] if indices else None)
                a['entryPost'] = line['posts'][entry]['name'] if entry is not None else None
                a['group'] = plan(projected, q, at)
                if a['group']:
                    member=next(m for m in a['group']['members'] if m['pid']==p['pid'])
                    a['entryPost']=member.get('entryPost')
                result[p['pid']] = a
    return result


def reconcile(eng, now):
    """An old receipt records consent only; it must never claim a changed job executed."""
    changed = False
    if not eng.d.get('cooperativeEntry'):
        return changed
    for receipt in eng.d.get('confirmations', {}).values():
        if receipt.get('status') != 'waiting':
            continue
        p = eng.P.get(receipt['pid'])
        current = plan(eng, p, now) if p else None
        if not current or current['id'] != receipt.get('group'):
            receipt['status'] = 'superseded'
            changed = True
    return changed


def adjustment(eng, pid, now, missing):
    """Recombine a three-plus cohort; all remaining members must consent afresh."""
    p = eng.P[pid]
    group = members(eng, p)
    view = plan(eng, p, now)
    if not view or not isinstance(missing, list) or not missing or any(not isinstance(v, str) for v in missing):
        raise E.ActionError('请选择暂未到场的同行人')
    selected = set(missing)
    if len(selected) != len(missing) or pid in selected or not selected < {q['pid'] for q in group}:
        raise E.ActionError('暂未到场人员与当前同组任务不符')
    if any(m['confirmed'] and m['pid'] in selected for m in view['members']):
        raise E.ActionError('同行人已确认到位，不能标记其未到场')
    remaining = [q for q in group if q['pid'] not in selected]
    jobs = {q['pid']: deepcopy(group[i]['assign']) for i, q in enumerate(remaining)}
    projected = clone(eng)
    for q in remaining:
        projected.P[q['pid']]['assign'] = deepcopy(jobs[q['pid']])
    routes = simulate(projected, [projected.P[q['pid']] for q in remaining], now)
    key = digest({'group': view['id'], 'missing': sorted(selected), 'jobs': jobs, 'routes': routes})
    return {'id': key, 'group': view['id'], 'missing': sorted(selected), 'jobs': jobs,
            'routes': routes, 'members': [{'pid': q['pid'], 'name': q['name']} for q in remaining]}


def apply_adjustment(eng, pid, now, actor, missing, expected):
    if not getattr(actor, 'formal', False) or actor.entered_by.employee_no != pid:
        raise E.ActionError('请本人确认调整同行安排')
    revised = adjustment(eng, pid, now, missing)
    if revised['id'] != expected:
        raise E.ActionError('同行安排已变化，请重新预览')
    for other_pid in revised['missing']:
        eng.P[other_pid]['assign'] = None
        eng.P[other_pid]['entryHold'] = {'by': pid, 'at': now, 'reason': '同行人反馈暂未到场'}
    for other_pid, job in revised['jobs'].items():
        eng.P[other_pid]['assign'] = job
    for receipt in eng.d.get('confirmations', {}).values():
        if receipt.get('group') == revised['group'] and receipt.get('status') == 'waiting':
            receipt['status'] = 'superseded'
    eng.h.log('entry_adjust', actor, pid, None, None, revised)
    return {'status': 'revised', 'participants': [], 'group': revised['group']}
