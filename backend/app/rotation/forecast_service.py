"""Persistence/API adapter for the live forecast core; never deletes history."""
from copy import deepcopy
import math
from uuid import uuid4

from sqlalchemy import insert

from app.rotation import forecast as F
from app.rotation.engine import ActionError, Engine
from app.rotation.namespace import tables_for

MANUAL = {'pool', 'break_time', 'shift', 'sort_time', 'all_close', 'place', 'sick', 'add_person'}


def work_clock(cfg, day, minute):
    """Keep a still-live overnight workday addressable without editing its clock."""
    from datetime import date
    from sqlalchemy.orm import object_session
    from app.rotation.namespace import tables_for
    db = object_session(cfg)
    if db is not None:
        Day = tables_for(db).RotationDay
        rows = db.query(Day).filter(Day.attraction_id == cfg.attraction_id, Day.status == 'live',
                                    Day.work_date < day).order_by(Day.work_date).all()
        import json
        for row in rows:
            state = json.loads(row.state_json)
            if state.get('ruleVersion') == 2 and max((p['end'] for p in state['persons'].values()), default=0) > 1440:
                return row.work_date, (date.fromisoformat(day) - date.fromisoformat(row.work_date)).days * 1440 + minute
    return day, minute


class BatchRecords:
    def __init__(self, hooks):
        self.events = []
        self.segments = []
        self.push7_history = hooks.push7_history

    def log(self, typ, actor, pid, line, post, detail):
        self.events.append((typ, actor, pid, line, post, deepcopy(detail)))

    def segment(self, pid, line, start, end):
        self.segments.append((pid, line, start, end))


def apply_batch(eng, now, start, booked):
    """Calculate on a private copy, then persist final state and ordered records."""
    hooks = eng.h
    records = BatchRecords(hooks)
    trial = Engine(deepcopy(eng.d), eng.S, records)
    F.run_batch(trial, now, start, [trial.P[p['pid']] for p in booked])

    db = hooks.db
    tables = tables_for(db)
    if records.events or records.segments:
        # Preserve the order of already queued records, such as a due closure.
        db.flush()
        connection = db.connection()
        if records.events:
            connection.execute(insert(tables.RotationEvent.__table__),
                               [hooks.event_values(*entry) for entry in records.events])
        if records.segments:
            connection.execute(insert(tables.RotationSegment.__table__),
                               [hooks.segment_values(*entry) for entry in records.segments])
            hooks.invalidate_week_minutes()
    eng.d.update(trial.d)


def advance(db, cfg, row, state, target):
    """Jump between meaningful boundaries, not 15-second replay steps."""
    from app.rotation import service as S, daily as D, history as H
    from app.rotation.engine import _m, rotation_end
    eng, hooks = S.engine_for(db, cfg, row, state)
    previous = min(float(state.get('lastTick', target)), target)
    t = previous
    changed = bool(getattr(eng, '_workflow_initialized', False))
    if D.prestart(state, target):
        changed = bool(D.fill_prestart_spares(state)) or changed
    while True:
        hooks.now = t
        batch_before = H.snapshot(eng.d) if F.due_batch(eng, t) else None
        if batch_before is not None:
            batch_before['existingSegmentIds'] = H.existing_segment_ids(db, eng, t)
        step_changed = F.tick(eng, t, advance_batches=True, execute_batch=apply_batch)
        if batch_before is not None:
            H.record(db, eng, batch_before, 'batch_depart', 'system', t)
        changed = step_changed or changed
        if step_changed:
            D.capture_snapshot(db, row.attraction_id, state, S.settings_of(cfg), t)
        if state['status'] == 'ended' or t >= target:
            break
        boundaries = [target, rotation_end(state)]
        for p in eng.P.values():
            boundaries.extend([p['start'], p['end'] - 30, p['end'] - 25, p['end']])
            if F.batch_member(eng, p):
                boundaries.extend([p['start'] - F.BATCH_CHECK_IN_MINUTES,
                                   p['start'] - F.BATCH_LEAD_MINUTES])
            if p.get('readyAt') is not None:
                boundaries.extend([p['readyAt'], p.get('timeoutAt', p['readyAt'] + 3)])
                if p['state'] == 'meal':
                    boundaries.append(p['readyAt'] - F.MEAL_TO_REST_MINUTES)
            for a in p.get('absences', []):
                boundaries.extend([a['start'] - eng.S['offLead'], a['start'], a['end']])
        boundaries.append(_m(eng.S['opReleaseAt']))
        for line in state['lines']:
            for post in line['posts']:
                boundaries.extend(_m(post[k]) for k in ('openAt', 'closeAt') if post.get(k))
        t = min(value for value in boundaries if value > t + F.EPS)
    state['lastTick'] = t
    if not changed and state['status'] == 'live':
        # Both preparation windows can open without changing a person's state.
        changed = any((eng.preparing_shift_end(p, t) and not eng.preparing_shift_end(p, previous))
                      or (F.can_check_in(eng, p, t) != F.can_check_in(eng, p, previous))
                      or (F.preparing_shift_start(p, t) and not F.preparing_shift_start(p, previous))
                      for p in eng.P.values())
    if changed:
        S.sync_notices(db, row.attraction_id, state, eng, t)
    if changed or t != previous:
        S.save_day(db, row, state, notify=changed)
    return changed


def batch_result_view(result):
    if not result.get('line'):
        return result
    title = f"{result['line']}线 · {result['post']}"
    if 'handoff' not in result:
        if result.get('pushes'):
            title += f" · {result['pushes']}推"
        return {**result, 'heading': title, 'instruction': '、'.join(result.get('special', []))}
    handoff = result['handoff']
    if not handoff:
        return {**result, 'heading': title, 'instruction': ''}
    outcome = handoff['outcome']
    who = f"通知原{handoff['post']}的{handoff['name']}"
    if outcome == 'post':
        steps = handoff['steps']
        if steps > 0:
            title += f' · {steps}推'
        instruction = f"{who}去{handoff['destination']}"
    elif outcome == 'done':
        title += ' · 推下班'
        instruction = f'{who}下班'
    elif outcome in ('sick', 'away'):
        instruction = f"{who}{'下岗休病假' if outcome == 'sick' else '出圈'}"
    else:
        instruction = f'{who}下岗，回休息室'
    return {**result, 'heading': title, 'instruction': instruction}


def notices(eng, now):
    if eng.d.get('status') != 'live':
        return {}
    out = {}
    for p in eng.P.values():
        state = p['state']
        a = p.get('assign')
        if F.can_check_in(eng, p, now) and not F.checked_in(p):
            out[(p['pid'], 'step')] = ('batch_check_in', '请确认已到', '班前三分钟截止签到，签到后由系统统一排岗。')
        if p.get('batchResult'):
            result = batch_result_view(p['batchResult'])
            text = '。'.join(filter(None, (result['heading'], result['instruction']))) if result.get('line') else '本批岗位已排满，自动休息15分钟。'
            out[(p['pid'], 'batch')] = ('batch_result', '批量上岗结果', text)
        if state in F.WAITING_TO_REST:
            out[(p['pid'], 'step')] = ('arrive', '请选择休息或吃饭', '确认后开始餐休并显示预计线路。')
        elif state in {'rest', 'meal', 'ready'} and a:
            ready = now >= max(F.readiness(p), a.get('notBefore', 0))
            out[(p['pid'], 'step')] = ('depart' if ready else 'rest',
                                      ('可上岗 · ' if ready else '预计去 ') + a['line'],
                                      '确认时按最新站位重新计算，线路改变需再次确认。')
        elif state == 'exception':
            out[(p['pid'], 'step')] = ('rest', '上岗超时 · 等待人工安排', '本人不可继续执行，由大屏、主管、TR或经理拖拽处理。')
    return out


def clock_value(value):
    if not isinstance(value, str):
        raise ActionError('请输入时:分或时:分:秒')
    parts = value.split(':')
    if len(parts) not in (2, 3) or any(not x.isdigit() for x in parts):
        raise ActionError('时间格式不正确')
    h, m = map(int, parts[:2])
    s = int(parts[2]) if len(parts) == 3 else 0
    if not 0 <= h <= 23 or not 0 <= m < 60 or not 0 <= s < 60:
        raise ActionError('时间超出范围')
    return h * 60 + m + s / 60


def shift_values(body):
    offset = body.get('start_day_offset', 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset not in (0, 1):
        raise ActionError('班次开始日期不正确')
    start = clock_value(body.get('start')) + offset * 1440
    end = clock_value(body.get('end')) + offset * 1440
    return start, end + 1440 if end <= start else end


def scheduled_value(body, key):
    offset = body.get('day_offset', 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 2:
        raise ActionError('岗位计划日期不正确')
    at = clock_value(body[key]) + offset * 1440
    seconds = round(at * 60)
    text = body[key] if not offset else f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}'
    return at, text


def person_view(eng, p, now):
    from app.rotation import service as S
    L, i = eng.find_post(p['pid']) if p['state'] == 'onpost' else (None, None)
    task = F.task_view(eng, p, now) if eng.d['status'] == 'live' else None
    can = bool(task and F.eligible(eng, p, now) and now + F.EPS >= F.readiness(p)
               and now + F.EPS >= (p.get('assign') or {}).get('notBefore', 0)
               and now < F.deadline(eng, p, now, (p.get('assign') or {}).get('notBefore', 0)))
    actions = ['arrive'] if p['state'] in F.WAITING_TO_REST else ['depart'] if task else []
    check_in = F.can_check_in(eng, p, now) and not F.checked_in(p)
    if check_in:
        actions.append('batch_check_in')
    flags = [flag for flag in p.get('flags', []) if flag not in ('推7点', '7点岗')]
    if F.seven_target(p):
        flags.append('7点岗')
    if task and p['assign']['kind'] == 'push7':
        flags.append('推7点')
    preparing = {'sick': '推病假', 'out': '推出圈'}.get(p.get('requestedExit'))
    if eng.d['status'] == 'live' and L and eng.preparing_shift_end(p, now) and p.get('requestedExit') != 'sick':
        preparing = '推下班'
    upcoming = eng.d['status'] == 'live' and F.preparing_shift_start(p, now)
    preview = None
    if upcoming:
        projection = getattr(eng, '_read_projection', {})
        if 'shift_previews' not in projection:
            projection['shift_previews'] = F.shift_previews(eng, now)
        preview = projection['shift_previews'].get(p['pid'])
    return {**deepcopy(p), 'flags': flags, 'task': task, 'taskTimeout': None, 'timeoutCase': None, 'timeoutPending': None,
            'batchResult': batch_result_view(deepcopy(p['batchResult'])) if p.get('batchResult') else None,
            'entryHold': None, 'entryGroup': None, 'assignmentLocked': False,
            'post': L['posts'][i]['name'] if L else None, 'postIndex': i,
            'postHold': (L['posts'][i].get('hold') or L['posts'][i].get('hold101')) if L else None,
            'canDepart': can, 'canArrive': p['state'] in F.WAITING_TO_REST,
            'departureRoutes': task['routes'] if task else [], 'departureBlocked': None,
            'previewAssign': deepcopy(preview), 'upcomingShift': upcoming,
            'awaitingShiftEntry': eng.d['status'] == 'live' and F.awaiting_shift_entry(p),
            'canBatchCheckIn': check_in,
            'batchCheckedIn': upcoming and F.checked_in(p), 'arrivalOutcome': None,
            'preparing': preparing,
            'continuousMinutes': max(0, now - p['continuousStart']) if p.get('continuousStart') is not None and L else 0,
            'mealEligible': eng.meal_eligible(p), 'mealOptions': eng.meal_options(p, now),
            'confirmationTokens': {a: F.token(eng, p, a) for a in actions},
            'assign': S.assignment_view(eng, p) if p.get('assign') else None}


def add_person(db, eng, body, now, actor):
    from app.rotation.service import _require
    from app.v2_models import Employee
    _require(body, 'start', 'end')
    start, end = shift_values(body)
    temporary = body.get('temporary') is True
    if temporary:
        name = str(body.get('name') or '').strip()
        if not name or len(name) > 100:
            raise ActionError('临时人员姓名须为1至100字')
        pid = 'temporary:' + uuid4().hex
    else:
        employee = db.query(Employee).filter(Employee.employee_no == body.get('pid'), Employee.is_active.is_(True)).first()
        if not employee:
            raise ActionError('未找到有效员工')
        pid, name = employee.employee_no, employee.name
    eng.act_add_person({'pid': pid, 'name': name, 'type': '', 'mark': '', 'start': start, 'end': end}, now, actor)
    p = eng.P[pid]
    p.update(temporary=temporary, continuousStart=None, forecastOrder=uuid4().hex,
             state='notyet' if now < start else 'pending' if now < end else 'other',
             manualPaused=now >= end)
    eng.__dict__.pop('_forecast_shift_counts', None)


def action(db, cfg, row, eng, body, now, actor):
    from app.rotation import service as S, workflow as W, history as H
    act = body['action']
    pid = body.get('pid')
    if getattr(actor, 'kind', None) == 'screen' and act not in S.SCREEN_ALLOWED_ACTIONS:
        raise ActionError('休息室大屏只可确认去休息或去轮岗')
    if getattr(actor, 'kind', None) == 'member':
        if act not in {'arrive', 'depart', 'batch_check_in'} or actor.member_employee_no != pid:
            raise ActionError('只能确认本人轮岗')
    token = body.get('confirmation_token')
    if act in {'arrive', 'depart', 'batch_check_in'}:
        p = eng.person(pid)
        if not isinstance(token, str) or token != F.token(eng, p, act):
            raise S.ConfirmationConflict('人员状态已变化，请刷新后重新确认')
    before = H.snapshot(eng.d)
    before['existingSegmentIds'] = H.existing_segment_ids(db, eng, now)
    result = {'ok': True, 'executed': True}
    if act == 'batch_check_in':
        if not F.can_check_in(eng, p, now):
            raise ActionError('当前不在本班次签到时间内')
        p['batchCheckedInFor'] = p['start']
        eng.h.log('batch_check_in', actor, pid, None, None, {'start': p['start']})
    elif act == 'depart':
        if body.get('group_confirm'):
            raise ActionError('多人预排必须逐人确认上岗')
        result = F.execute(eng, pid, now, actor, body.get('expected_line'),
                           body.get('expected_push7', F.UNSPECIFIED_ROUTE), expected_kind=body.get('expected_kind'))
        if result.get('line_changed'):
            S.save_day(db, row, eng.d)
            return result
    elif act == 'arrive':
        choice = body.get('meal_choice') or 'rest'
        if choice not in {'rest', 'meal'}:
            raise ActionError('请选择休息或吃饭')
        if eng.person(pid)['state'] not in F.WAITING_TO_REST:
            raise ActionError('此人不在待确定休息')
        options = eng.meal_options(eng.person(pid), now)
        if options['forced']:
            choice = 'meal'
        elif choice == 'meal' and not options['canChoose']:
            raise ActionError('当前不满足吃饭条件或轮岗储备不足；人工安排请拖入吃饭区')
        F.pool(eng, pid, choice, now, actor, repeat_meal=body.get('repeat_meal') is True)
    elif act == 'pool':
        if body.get('person_context') is not None:
            p = eng.person(pid)
            L, i = eng.find_post(pid) if p['state'] == 'onpost' else (None, None)
            context = {'state': p['state'], 'readyAt': p.get('readyAt'),
                       'sequence': p.get('confirmationSequence', 0), 'line': L['id'] if L else None,
                       'postIndex': i}
            if context != body['person_context']:
                raise S.ConfirmationConflict('人员状态或岗位已变化，请重新拖拽')
        F.pool(eng, pid, body.get('pool'), now, actor, repeat_meal=body.get('repeat_meal') is True)
    elif act == 'break_time':
        p = eng.person(pid)
        if p['state'] not in {'rest', 'meal', 'ready'}:
            raise ActionError('只能修改餐休区的剩余时间')
        value = body.get('remaining')
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ActionError('请输入有效剩余分钟数')
        kind = p.get('breakKind')
        break_start, arrive_anchor = p.get('breakStart'), p.get('arriveAnchor')
        F.pool(eng, pid, 'meal' if kind == 'meal' else 'rest', now, actor, remaining=value, repeat_meal=True)
        if break_start is not None:
            p['breakStart'] = break_start
        if arrive_anchor is not None:
            p['arriveAnchor'] = arrive_anchor
        if kind == 'meal_rest':
            p['breakKind'] = kind
    elif act in {'sick', 'leave', 'away'}:
        p = eng.person(pid)
        purpose = 'out' if act == 'away' else 'sick'
        if eng.held(pid):
            raise ActionError('请先解除定岗')
        if p['state'] == 'onpost':
            p['requestedExit'] = purpose
        elif purpose == 'out':
            F.pool(eng, pid, 'away', now, actor)
        else:
            F.remove(eng, p, now)
            p.update(state='sick', manualPaused=True)
        eng.h.log('request_' + purpose, actor, pid, p.get('line'), None, {})
    elif act == 'back':
        if eng.person(pid)['state'] != 'away':
            raise ActionError('此人不在圈外')
        F.pool(eng, pid, 'rest', now, actor, remaining=15)
    elif act == 'shift':
        p = eng.person(pid)
        start, end = shift_values(body)
        old = [p['start'], p['end']]
        p.update(start=start, end=end)
        eng.__dict__.pop('_forecast_shift_counts', None)
        p.pop('batchCheckedInFor', None)
        p['confirmationSequence'] = p.get('confirmationSequence', 0) + 1
        if p['state'] in {'rest', 'meal', 'ready'} and start > F.readiness(p):
            p.update(readyAt=start, timeoutAt=start + 3)
        if start <= now < end:
            p['manualRestored'] = False
        if p['state'] in {'notyet', 'other', 'done'} and not p.get('manualPaused'):
            p['state'] = 'notyet' if now < start else 'pending' if now < end else 'done'
        eng.h.log('shift_edit', actor, pid, None, None, {'before': old, 'after': [start, end]})
    elif act == 'sort_time':
        L = eng.line(body.get('line'))
        old = L.get('sortAt', 0)
        offset = body.get('day_offset', 0)
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 2:
            raise ActionError('排序时间日期不正确')
        F.set_sort_time(L, clock_value(body.get('time')) + offset * 1440)
        eng.h.log('sort_time', actor, None, L['id'], None, {'before': old, 'after': L['sortAt']})
    elif act == 'place':
        # A shared snapshot protects against duplicate drag submissions and stale previews.
        if body.get('expected_snapshot') != snapshot(eng):
            raise S.ConfirmationConflict('站位已变化，请重新选择人员和岗位')
        L = eng.line(body.get('line'))
        i = body.get('i')
        if isinstance(i, bool) or not isinstance(i, int):
            raise ActionError('岗位位置不正确')
        update_sort_time = body.get('update_sort_time', False)
        if not isinstance(update_sort_time, bool):
            raise ActionError('是否更新表头轮岗时间必须为布尔值')
        F.manual_place(eng, pid, L['id'], i, body.get('place_mode'), now, actor, update_sort_time=update_sort_time)
    elif act in {'move', 'plan_place'}:
        from app.rotation.daily import prestart
        if not prestart(eng.d, now) or eng.person(pid)['state'] != 'notyet':
            raise ActionError('运行中请使用顺推或交换安排人员')
        eng.act_plan_place(pid, body.get('line'), body.get('i'), now, actor)
    elif act == 'all_close':
        eng.d['hold101Active'] = False
        for L in eng.d['lines']:
            for i, post in enumerate(L['posts']):
                post['resumeOpen'] = bool(post.get('open'))
                post['resumeScheduled'] = F.scheduled_opening(post)
                post.pop('hold', None)
                post.pop('hold101', None)
                if post.get('occ'):
                    F.cancel_active_absences(eng, eng.P[post['occ']], now, actor)
                F.close_post(eng, L, i, now, actor)
            L['active'] = False
    elif act == 'post':
        from app.rotation.engine import rotation_end
        L, i, post = eng.post(body.get('line'), body.get('i'))
        if body.get('post_key') is not None and body['post_key'] != post.get('key'):
            raise S.ConfirmationConflict('岗位顺序已变化，请重新选择')
        opened = S.action_boolean(body, 'open')
        if post.get('hold') or post.get('hold101'):
            raise ActionError('请先解除定岗')
        if opened:
            at, time_text = scheduled_value(body, 'openAt') if body.get('openAt') else (now, None)
            if at < now - F.EPS or at >= rotation_end(eng.d):
                raise ActionError('加岗时间须在当前时间至轮岗结束之前')
            immediate = time_text is None
            post.update(openAt=time_text, opened=immediate, manual=False, open=immediate, openRequest=True)
            post.pop('draftClosed', None)
            post.pop('openExpired', None)
            post.pop('resumeOpen', None)
            post.pop('resumeScheduled', None)
            post.update(closeAt=None, closedAuto=False)
            post.pop('closeAfterRelief', None)
            if immediate:
                L['active'] = True
        elif body.get('closeAt'):
            at, time_text = scheduled_value(body, 'closeAt')
            if at <= now or at >= rotation_end(eng.d):
                raise ActionError('计划撤岗时间须晚于当前时间且早于轮岗结束')
            post.update(closeAt=time_text, closedAuto=False)
            post.pop('closeAfterRelief', None)
        else:
            post.pop('resumeOpen', None)
            post.pop('resumeScheduled', None)
            F.close_post(eng, L, i, now, actor)
        eng.h.log('post_schedule', actor, None, L['id'], post['name'], {'open': opened})
    elif act == 'line':
        L = eng.line(body.get('line'))
        from app.rotation import daily as D
        context = {'date': eng.d['date'], 'active': L['active'],
                   'planRevision': D.plan_revision(eng.d) if D.prestart(eng.d, now) else None}
        if body.get('line_context') is not None and body['line_context'] != context:
            raise S.ConfirmationConflict('日期或线路安排已变化，请重新确认开停线')
        active = S.action_boolean(body, 'active')
        if active:
            for post in L['posts']:
                reopen = post.pop('resumeOpen', False)
                scheduled = post.pop('resumeScheduled', False)
                if reopen or scheduled:
                    post.update(draftClosed=False, manual=False, closedAuto=False,
                                closeAt=None, open=bool(reopen))
                    if scheduled:
                        post['openRequest'] = True
        if D.prestart(eng.d, now):
            eng.act_line(L['id'], active, now, actor)
        elif active:
            L['active'] = True
            for post in L['posts']:
                if not post.get('draftClosed') and not post.get('openAt'):
                    post.update(open=True, manual=False, opened=True)
        else:
            if any(post.get('hold') or post.get('hold101') for post in L['posts']):
                raise ActionError('请先解除定岗，或使用全线关闭')
            for i in range(len(L['posts'])):
                post = L['posts'][i]
                post['resumeOpen'] = bool(post.get('open'))
                post['resumeScheduled'] = F.scheduled_opening(post)
                F.close_post(eng, L, i, now, actor)
            L['active'] = False
        eng.h.log('line_change', actor, None, L['id'], None, {'active': L['active']})
    elif act == 'add_person':
        add_person(db, eng, body, now, actor)
    elif act == 'post_hold':
        eng.act_post_hold(body['line'], body['i'], pid, S.action_boolean(body, 'on'), str(body.get('reason') or ''), now, actor)
    elif act == 'hold101':
        W.toggle_101(eng, S.action_boolean(body, 'on'), now, actor)
    elif act == 'rename_post':
        L, _, post = W.locate(eng, body.get('post_key'))
        if not post:
            raise S.ConfirmationConflict('岗位已变化，请刷新后重新改名')
        if 'expected_name' in body and body['expected_name'] != post['name']:
            raise S.ConfirmationConflict('岗位名称已变化，请刷新后重新改名')
        if not isinstance(body.get('name'), str):
            raise ActionError('岗位名称须为1至40字')
        name = body['name'].strip()
        if not 1 <= len(name) <= 40 or any(x is not post and x['name'] == name for x in L['posts']):
            raise ActionError('岗位名称无效或重复')
        old = post['name']
        post['name'] = name
        eng.h.log('rename_post', actor, None, L['id'], name, {'key': post['key'], 'from': old, 'to': name})
    elif act == 'post_order':
        update_sort_time = body.get('update_sort_time', False)
        if not isinstance(update_sort_time, bool):
            raise ActionError('是否更新表头轮岗时间必须为布尔值')
        before_heads = F.head_occupants(eng, now)
        for key, line, index in [('source_post_key', body['line'], body['i']),
                                 ('target_post_key', body.get('target_line') or body['line'], body['target_i'])]:
            if key in body and eng.post(line, index)[2].get('key') != body[key]:
                raise S.ConfirmationConflict('岗位顺序已变化，请重新拖拽')
        eng.act_post_order(body['line'], body['i'], body['target_i'], now, actor,
                           swap_mode=body.get('swapMode'), target_lid=body.get('target_line'))
        F.update_manual_sort_times(eng, before_heads, now, actor, update_sort_time and body.get('swapMode') == 'with_people')
    elif act == 'restore_line':
        if body.get('notified') is not True:
            raise ActionError('请确认现场可通知相关人员')
        H.execute(db, eng, body['line'], body['checkpoint'], now, actor)
    elif act == 'set_close':
        clock_value(body.get('close'))
        eng.act_set_close(body['close'], now, actor)
    else:
        raise ActionError('新预排流程不支持此旧任务操作')
    F.tick(eng, now)
    if act != 'restore_line':
        H.record(db, eng, before, act, actor, now)
    if act in {'arrive', 'depart', 'batch_check_in'}:
        if act != 'depart':
            eng.P[pid]['confirmationSequence'] = eng.P[pid].get('confirmationSequence', 0) + 1
        p = eng.P[pid]
        L, i = eng.find_post(pid)
        receipt = {'pid': pid, 'action': act, 'status': 'executed', 'token': token,
                   'minute': now, 'date': eng.d['date'], 'state': p['state'],
                   'source': getattr(actor, 'kind', 'manager'), 'readyAt': p.get('readyAt'),
                   'sequence': p.get('confirmationSequence', 0),
                   'line': L['id'] if L else None, 'post': L['posts'][i]['name'] if L else None}
        eng.d.setdefault('confirmations', {})[token] = receipt
        result['result'] = receipt
    eng.d['lastTick'] = now
    S.sync_notices(db, row.attraction_id, eng.d, eng, now)
    S.save_day(db, row, eng.d)
    return result


def snapshot(eng):
    from app.rotation.manual import fingerprint
    return fingerprint(eng.d)


def search_people(db, query):
    from sqlalchemy import or_
    from app.v2_models import Employee, Attraction
    from app.employee_numbers import employee_number
    query = employee_number(str(query or '').strip()[:100])
    if not query:
        return []
    escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    rows = (db.query(Employee.employee_no, Employee.name, Attraction.name.label('attraction'))
            .outerjoin(Attraction, Employee.attraction_id == Attraction.id)
            .filter(Employee.is_active.is_(True), or_(Employee.employee_no.like('%' + escaped + '%', escape='\\'),
                                                       Employee.name.like('%' + escaped + '%', escape='\\')))
            .order_by(Employee.employee_no != query, Employee.employee_no).limit(3).all())
    return [{'pid': r.employee_no, 'name': r.name, 'attraction': r.attraction} for r in rows]
