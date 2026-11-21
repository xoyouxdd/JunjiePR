"""轮岗当天状态、预排生成和当前规则的共享操作。

时间以工作日零点起的分钟数计，运行操作在服务层持有写入锁。
"""
from __future__ import annotations

from copy import deepcopy

from app.rotation.roster import classify, fmt, hm

MIN_MEAL_SHIFT_MINUTES = 390

DEFAULT_SETTINGS = {
    'lineOpenAt': '07:15',        # 开园岗位开始时间
    'opReleaseAt': '07:15',       # OP 转为待定的时间
    'walkMin': 3,                 # 默认路程（分钟），可在线配置里按线覆盖
    'breakMin': 15,
    'mealMin': 45,
    'mealThreshold': MIN_MEAL_SHIFT_MINUTES,  # 班次超过 6.5 小时才有吃饭
    'mealEarliest': '09:30',
    'mealAfterStart': 150,        # 上班后多久才开始安排吃饭（早班自然优先）
    'mealForceAfterStart': 300,   # 上班超过这么久仍未吃饭，强制安排
    'mealReserveLeft': 20,        # 剩余餐休不超过这么多分钟的人计入吃饭储备
    'mealReserveMinPeople': 5,    # 达到此储备人数才可自选吃饭（不含本人）
    'mealWarnLeft': 180,          # 离下班不到这么多分钟仍没吃饭，提醒主管
    'offLead': 20,                # 下班/出圈前多少分钟被替下（替换者到岗时间）
    'noBoardBefore': 30,          # 到岗时距下班/出圈不超过这么多分钟，不再上岗
    'outWarnAfter': 5,            # 过了出圈时间多少分钟仍没人可推，提醒主管
    'endLead': 45,               # 比替换到岗时间提前多少分钟开始安排替换的人
    'departEarly': 1,             # 最多可比计划出发时间提前几分钟点出发
    'futureWait': 15,             # 等定时开岗最多等多久
    'readyYellow': 0,             # 超过出发时间多少分钟变黄
    'readyRed': 2,                # 超过多少分钟变红闪烁
    'readyNotify': 5,             # 超过多少分钟提醒主管
    'walkBackWarn': 10,           # 下线后多少分钟未到大屏确认提示
    'unassignedWarn': 10,         # 待出发超过多少分钟仍没有去向提示
    'parkClose': '21:30',         # 周班表里没有营业时间时的默认闭园时间
    'closeStopPush': 20,          # 当日轮岗结束前多少分钟起不再派新人进线
    'lostCount': 2,
}

DEFAULT_LINES = [
    {'id': 'A1', 'group': 'A', 'posts': [{'name': '塔台'}, {'name': '上客1'}, {'name': '上协1'}, {'name': '下协'}, {'name': '大出口商店', 'openAt': '09:00'}]},
    {'id': 'A2', 'group': 'A', 'posts': [{'name': '饮水童车'}, {'name': '小C', 'seven': 3}, {'name': '核销'}, {'name': '身高墙'}, {'name': '分组1'}]},
    {'id': 'B1', 'group': 'B', 'posts': [{'name': '塔台协助'}, {'name': '上客2'}, {'name': '上协2'}, {'name': '分组2'}, {'name': '快迎2', 'openAt': '09:00'}]},
    {'id': 'B2', 'group': 'B', 'posts': [{'name': '迎宾童车'}, {'name': '迎宾', 'seven': 1}, {'name': '豹协'}, {'name': '汇合'}, {'name': '快速汇合', 'openAt': '09:00'}]},
    {'id': 'C1', 'group': 'C', 'posts': [{'name': '上客3'}, {'name': '上协3'}, {'name': '预演'}, {'name': '预演协助'}, {'name': '单人队末', 'openAt': '09:00'}]},
    {'id': 'C2', 'group': 'C', 'posts': [{'name': '快迎1', 'seven': 2}, {'name': '单人迎宾', 'openAt': '08:15'}, {'name': '分组3'}, {'name': '上客4'}, {'name': '上协4'}]},
    {'id': 'D1', 'group': 'D', 'posts': [{'name': '河马池'}, {'name': '铁马出口'}, {'name': '迎宾协助'}, {'name': '下客1'}, {'name': '下客2'}]},
    {'id': 'D2', 'group': 'D', 'standby': True, 'posts': [{'name': '铁马A'}, {'name': '铁马B'}, {'name': '铁马C'}, {'name': '备用'}, {'name': '备用'}]},
    {'id': 'E', 'group': 'E', 'standby': True, 'posts': [{'name': '城内巡视'}, {'name': '队末'}, {'name': '备用'}, {'name': '备用'}, {'name': '备用'}]},
]

STANDARD_POST_COUNT = 6


def append_standard_spares(lines, count=STANDARD_POST_COUNT):
    standard_ids = {line['id'] for line in DEFAULT_LINES}
    added = {}
    for line in lines:
        posts = line['posts']
        if line['id'] not in standard_ids or len(posts) >= count:
            continue
        added[line['id']] = count - len(posts)
        while len(posts) < count:
            post = {'name': '备用'}
            if len(posts) == STANDARD_POST_COUNT - 1:
                post['draftClosed'] = True
            posts.append(post)
    return added


append_standard_spares(DEFAULT_LINES)

CLOSING_WORK = '闭园后工作'
TARGET_MODES = ('push7', 'chain')
SHIFT_PREPARE_MINUTES = 25
SHIFT_FINISH_MINUTES = 30


class ActionError(Exception):
    pass


def _m(v, default=None):
    if v is None or v == '':
        return default
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and len(v.split(':')) == 3:
        h, m, s = v.split(':')
        return float(h) * 60 + float(m) + float(s) / 60
    return float(hm(v))


def rotation_end(day):
    return max((p['end'] for p in day['persons'].values()), default=1440)


# ---------------------------------------------------------------- 草稿生成

SEVEN_POST_PRIORITY = {name: rank for rank, name in enumerate(
    ('迎宾', '快迎1', '小C', '铁马入口', '铁马检票1', '熊出口'), 1)}
SEVEN_CONFIGURATION_ALERT = '07:00 岗位未配置、重复、停用或未到开放时间：'


def open_seven_slots(lines):
    named = {SEVEN_POST_PRIORITY[post['name'].strip()] for line in lines for post in line['posts']
             if post['name'].strip() in SEVEN_POST_PRIORITY}
    slots = {}
    for line in lines:
        if line.get('standby'):
            continue
        for i, post in enumerate(line['posts']):
            name_rank = SEVEN_POST_PRIORITY.get(post['name'].strip())
            rank = name_rank or post.get('seven')
            if (rank in SEVEN_POST_PRIORITY.values() and (name_rank or rank not in named) and not post.get('draftClosed')
                    and (not post.get('openAt') or _m(post['openAt']) <= 420)
                    and (not post.get('closeAt') or _m(post['closeAt']) > 420)):
                slots.setdefault(rank, []).append((rank, line['id'], i))
    # A single configured slot owns each priority. An ambiguous rank requires
    # an explicit configuration correction, not a choice by line order.
    return [matches[0] for _, matches in sorted(slots.items()) if len(matches) == 1]


def refresh_seven_configuration_alert(alerts, slots, early_count):
    alerts[:] = [alert for alert in alerts if not alert.startswith(SEVEN_CONFIGURATION_ALERT)]
    if len(slots) < min(early_count, len(SEVEN_POST_PRIORITY)):
        present = {rank for rank, _, _ in slots}
        missing = [name for name, rank in SEVEN_POST_PRIORITY.items() if rank not in present]
        if missing:
            alerts.append(SEVEN_CONFIGURATION_ALERT + '、'.join(missing))


def push7_rank(history, pid):
    return tuple(history.get(pid, (0, '', 0)))


def build_draft(date, roster, config, settings, duty_counts, week_minutes, *, push7_history):
    """根据周班表生成当天初始轮岗草稿。

    duty_counts: {pid: {'push7': n, 'lost': n}} 本周已做次数
    week_minutes: {pid: {line: minutes}} 本周各线累计
    push7_history: 使用实际完成记录。
    """
    cells = roster['days'].get(date)
    if cells is None:
        raise ActionError('名单里没有 %s 这一天' % date)
    S = settings
    open_at = _m(S['lineOpenAt'])
    persons, off = {}, []
    for pid, cell in cells.items():
        info = classify(cell)
        base = roster['people'].get(pid, {'name': pid, 'type': '', 'mark': ''})
        if info['kind'] == 'off':
            if info.get('label'):
                entry = {'pid': pid, 'name': base['name'], 'label': info['label']}
                if 'start' in info:
                    entry.update(start=info['start'], end=info['end'])
                off.append(entry)
            continue
        persons[pid] = {
            'pid': pid, 'name': base['name'], 'type': base.get('type', ''), 'mark': base.get('mark', ''),
            'start': info['start'], 'end': info['end'], 'note': info.get('note', ''),
            'tags': list(info.get('tags', [])), 'absences': info.get('absences', []),
            'role': 'rotation' if info['kind'] == 'rotation' else 'excluded',
            'label': info.get('label', ''),
        }
    lines = []
    for L in config['lines']:
        lines.append({
            'id': L['id'], 'group': L.get('group', ''), 'standby': bool(L.get('standby')),
            'walk': L.get('walk'),
            'posts': [dict(name=x['name'], openAt=x.get('openAt'), closeAt=x.get('closeAt'),
                           seven=SEVEN_POST_PRIORITY.get(x['name'].strip(), x.get('seven')),
                           **({'draftClosed': True} if x.get('draftClosed') else {}))
                      for x in L['posts']],
        })
    rot = [p for p in persons.values() if p['role'] == 'rotation']
    alerts = []
    # OP：早于开园岗位 1 小时以上上班的人
    for p in rot:
        if p['start'] <= open_at - 60:
            p['role'] = 'op'
    # 07:00 班按岗位优先级和开放情况依次预排。
    sevens = sorted([p for p in rot if p['role'] == 'rotation' and p['start'] == 420],
                    key=lambda p: (-(p['end'] - p['start']), p['name']))
    seven_posts = open_seven_slots(lines)
    refresh_seven_configuration_alert(alerts, seven_posts, len(sevens))
    if len(sevens) < 2:
        alerts.append('07:00 上班只有 %d 人，少于 2 人，7 点岗位需要主管安排' % len(sevens))
    use = seven_posts[:len(sevens)]
    seven = {}
    for (_, lid, i), p in zip(use, sevens):
        seven['%s#%d' % (lid, i)] = p['pid']
    # 保留可立即上岗的开班候选。新版派发时再从当前可上岗人员中排序。
    early = [p for p in rot if p['role'] == 'rotation' and p['start'] == open_at and not p['absences']]
    early.sort(key=lambda p: (push7_rank(push7_history, p['pid']), p['name'], p['pid']))
    push7 = [p['pid'] for p in early[:len(seven)]]
    if len(push7) < len(seven):
        alerts.append('07:15 班可做推 7 点的只有 %d 人，少于 7 点岗位 %d 个，请主管安排' % (len(push7), len(seven)))
    # 送失物：12 点后上班的长班，本周次数最少优先
    lates = [p for p in rot if p['role'] == 'rotation' and p['start'] >= 720
             and p['end'] - p['start'] > S['mealThreshold']]
    lates.sort(key=lambda p: (duty_counts.get(p['pid'], {}).get('lost', 0), p['name']))
    lost = [p['pid'] for p in lates[:S['lostCount']]]
    # 开园岗位：07:15 班次填满 07:15 开放的非 7 点岗位
    seated7 = set(seven.values())
    crew_pool = [p for p in rot if p['role'] == 'rotation' and open_at - 60 < p['start'] <= open_at
                 and p['pid'] not in push7 and p['pid'] not in seated7]
    crew_pool.sort(key=lambda p: (-(p['end'] - p['start']), p['name']))
    crew = {}
    slots = [(L['id'], i) for L in lines if not L['standby'] for i, post in enumerate(L['posts'])
             if not post.get('openAt') and not post.get('draftClosed') and '%s#%d' % (L['id'], i) not in seven]
    used = set()
    missing = []
    for lid, i in slots:
        best, bscore = None, None
        for p in crew_pool:
            if p['pid'] in used:
                continue
            sc = week_minutes.get(p['pid'], {}).get(lid, 0)
            if bscore is None or sc < bscore:
                best, bscore = p, sc
        if best is None:
            missing.append('%s %s' % (lid, lines_post_name(lines, lid, i)))
            continue
        used.add(best['pid'])
        crew['%s#%d' % (lid, i)] = best['pid']
    if missing:
        alerts.append('开园班次人数不足，%d 个开园岗位没有人：%s' % (len(missing), '、'.join(missing)))
    hours = (roster.get('hours') or {}).get(date) or {}
    return {
        'date': date, 'status': 'draft', 'version': 1, 'ruleVersion': 2,
        'openTime': hours.get('open'), 'closeAt': hours.get('close') or S.get('parkClose', '21:30'),
        'persons': persons, 'off': off, 'lines': lines,
        'plan': {'seven': seven, 'push7': push7, 'lost': lost, 'crew': crew},
        'draftAlerts': alerts,
    }


def lines_post_name(lines, lid, i):
    for L in lines:
        if L['id'] == lid:
            return L['posts'][i]['name']
    return '?'


# ---------------------------------------------------------------- 运行引擎

class Engine:
    """操作一个“已发布”的当天状态。

    hooks 对象需要提供：
      log(type, actor, pid, line, post, detail)
      segment(pid, line, start, end)
      week_minutes() -> {pid: {line: minutes}}
    """

    def __init__(self, day, settings, hooks):
        if day.get('ruleVersion') != 2:
            raise ActionError('旧版轮岗仅供回放')
        self.d = day
        self.S = settings
        self.h = hooks

    @property
    def P(self):
        return self.d['persons']

    def line(self, lid):
        if not isinstance(lid, str) or not lid:
            raise ActionError('线 id 必须是非空字符串')
        for L in self.d['lines']:
            if L['id'] == lid:
                return L
        raise ActionError('没有这条线：%s' % lid)

    def post(self, lid, index):
        L = self.line(lid)
        if isinstance(index, str) and index.strip().isdecimal():
            index = int(index)
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(L['posts']):
            raise ActionError('岗位索引超出范围，必须是有效的非负整数')
        return L, index, L['posts'][index]

    def plan_post(self, key):
        if not isinstance(key, str) or key.count('#') != 1:
            raise ActionError('岗位标识格式需为 线id#岗位索引')
        lid, index = key.split('#')
        result = self.post(lid, index)
        if key != f'{lid}#{result[1]}':
            raise ActionError('岗位标识必须使用标准的非负整数索引')
        return result

    def open_posts(self, L):
        return [i for i, x in enumerate(L['posts']) if x.get('open')]

    def rotation_posts(self, L):
        return [i for i in self.open_posts(L) if not (L['posts'][i].get('hold') or L['posts'][i].get('hold101'))]

    def held(self, pid):
        L, i = self.find_post(pid)
        return L is not None and bool(L['posts'][i].get('hold') or L['posts'][i].get('hold101'))

    def act_post_hold(self, lid, i, pid, on, reason, now, actor):
        L, i, x = self.post(lid, i)
        if not L.get('active') or not x.get('open') or x.get('occ') != pid or self.person(pid)['state'] != 'onpost':
            raise ActionError('岗位或在岗人员已变化，请刷新后重新选择')
        if on:
            if x.get('hold'):
                return
            if self.d.get('closed'):
                raise ActionError('闭园后不能新设定岗；已有定岗仍保留到取消')
            x['hold'] = {'pid': pid, 'since': now, 'reason': reason or '现场任务',
                         'by': getattr(actor, 'name', str(actor))}
        else:
            if not x.get('hold'):
                return
            previous = x.pop('hold')
        self._reset_line_plans(lid)
        self._clear_stale_assigns()
        info = x['hold'] if on else previous
        self.h.log('post_hold' if on else 'post_resume', actor, pid, lid, x['name'],
                   {'reason': reason or info['reason'], 'since': info['since'],
                    'duration': None if on else max(0, now-info['since'])})
        if not on and self.d.get('closed'):
            from app.rotation.forecast import finish_continuous
            p = self.person(pid)
            self._leave_line(p, L, i, now)
            finish_continuous(p, now)
            p.update(state='walkback', walkbackSince=now, downReason='闭园', arriveAnchor=None, after='closing')
            self.h.log('pushed_off', 'system', pid, L['id'], x['name'], {'reason': '闭园', 'outcome': 'walkback'})
            x.update(occ=None, open=False)


    def find_post(self, pid):
        for L in self.d['lines']:
            for i, x in enumerate(L['posts']):
                if x.get('occ') == pid:
                    return L, i
        return None, None

    def person(self, pid):
        if not isinstance(pid, str):
            raise ActionError('工号必须是字符串')
        p = self.P.get(pid)
        if p is None:
            raise ActionError('今天的名单里没有这个人')
        return p

    def next_absence(self, p, now):
        """下一段还没处理的固定暂离（出圈）。已开始但人还在岗上的也算，直到暂离结束。"""
        pending = [a for a in p.get('absences', []) if not a.get('done') and a['end'] > now and a['start'] < p['end']]
        return min(pending, key=lambda a: a['start']) if pending else None

    def leave_at(self, p, now):
        """这个人必须离开轮岗的时间：(时间, 'off' 下班 / 'out' 出圈, 暂离段)。"""
        a = self.next_absence(p, now)
        if a is not None:
            return a['start'], 'out', a
        return p['end'], 'off', None

    def can_board(self, p, arrival, now):
        """按下班、出圈限制及已开始的餐休判断能否上岗。"""
        t, kind, _ = self.leave_at(p, now)
        if kind == 'off':
            from app.rotation import forecast
            if forecast.late_shift(self, p) or forecast.returning_from_break(p, arrival):
                return arrival < t
        return t - arrival > self.S['noBoardBefore']

    def targeted(self):
        """已被安排替换（推下班、推出圈、推 7 点）的人 -> 去替换他的人。"""
        out = {}
        for q in self.P.values():
            a = q.get('assign')
            if a and a.get('mode') in TARGET_MODES and a.get('target'):
                out[a['target']] = q['pid']
        return out

    def publish(self, now, actor):
        d = self.d
        for kind in ('crew', 'seven'):
            for key, pid in d['plan'][kind].items():
                self.plan_post(key)
                self.person(pid)
        d['status'] = 'live'
        for L in d['lines']:
            L['active'] = not L['standby']
            for x in L['posts']:
                x['occ'] = None
                x['since'] = None
                x['open'] = L['active'] and not x.get('openAt') and not x.get('draftClosed')
                x['opened'] = not x.get('openAt')
                x['manual'] = bool(x.get('draftClosed'))
        for p in self.P.values():
            p.update(state='notyet', assign=None, visited=[], ate=False, flags=[],
                     line=None, lineStart=None, continuousStart=None, entrySortAt=None, readyAt=None, breakKind=None, breakStart=None,
                     walkbackSince=None, arriveAt=None, away=None, after=None, lastLine=None,
                     push7done=False, departedAt=None, downReason=None, walkBack=None, arriveAnchor=None,
                     readySince=None)
            for a in p.get('absences', []):
                a.pop('done', None)
            if p['role'] == 'excluded':
                p['state'] = 'excluded'
            if p['pid'] in d['plan']['lost']:
                p['flags'].append('送失物')
        self.h.log('publish', actor, None, None, None, {'plan': d['plan']})
        for kind in ('crew', 'seven'):
            for key, pid in d['plan'][kind].items():
                if self.P[pid]['start'] > now:
                    _, _, x = self.plan_post(key)
                    if x['open']:
                        x['occ'] = pid
        self.tick(now)

    def tick(self, now):
        from app.rotation.forecast import tick
        return tick(self, now)


    def _freeze_daily_opening(self, now, *, freeze_at=None):
        if self.d.get('dailyOpening') or not self.P:
            return False
        first = min(p['start'] for p in self.P.values())
        if now < (first if freeze_at is None else freeze_at) or any(p['state'] not in ('notyet', 'excluded') for p in self.P.values()):
            return False
        excluded = {'dailyOpening', 'initialDraft', 'initialSettings', 'confirmations', 'entryVotes'}
        state = deepcopy({k: v for k, v in self.d.items() if k not in excluded})
        for kind in ('crew', 'seven'):
            for key, pid in state['plan'][kind].items():
                lid, i = key.split('#')
                line = next(l for l in state['lines'] if l['id'] == lid)
                if line['posts'][int(i)].get('open'):
                    line['posts'][int(i)].update(occ=pid, since=None)
        self.d['dailyOpening'] = {'minute': first, 'captured_at': now,
                                  'late_publish': 'lastTick' not in self.d and now > first,
                                  'state': state, 'settings': deepcopy(self.S)}
        return True

    def opening_rest_candidate(self, p, push7=None):
        """Unseated 07:15 crew rest at shift start; push7 and seated crew keep their flow."""
        plan = self.d['plan']
        return (p['start'] in (420, 435) and p['role'] == 'rotation'
                and p['pid'] not in (plan['push7'] if push7 is None else push7)
                and all(p['pid'] not in plan[kind].values() for kind in ('crew', 'seven'))
                and not any(a['start'] <= p['start'] < a['end'] for a in p.get('absences', [])))

    def start_opening_rest(self, p, now):
        start = p['start']
        minutes = 15 if self.d.get('ruleVersion') == 2 and start == 435 else self.S['breakMin']
        p.update(state='rest', breakKind='rest', breakStart=start, arriveAnchor=start,
                 readyAt=start + minutes, readySince=None, assign=None)
        if now >= p['readyAt']:
            p.update(state='ready', readySince=p['readyAt'])
        self.h.log('initial_rest', 'system', p['pid'], None, None, {'start': start, 'until': p['readyAt']})

    def _shift_start(self, p, now, seat, seat7, push7=None):
        t = p['start']
        if p['role'] == 'op':
            p['state'] = 'op'
            return
        if p['role'] != 'rotation':
            return
        if self.d.get('closed'):
            p['state'] = 'away'
            p['away'] = {'reason': CLOSING_WORK, 'until': None}
            return
        key = seat7.get(p['pid']) or seat.get(p['pid'])
        if key:
            lid, i = key.split('#')
            L, i = self.line(lid), int(i)
            x = L['posts'][i]
            if L['active'] and x.get('open') and x.get('occ') in (None, p['pid']):
                x['open'] = True
                x['occ'] = p['pid']
                x['since'] = t
                p.update(state='onpost', line=lid, lineStart=t, continuousStart=t, lastLine=lid)
                p['visited'].append(lid)
                if p['pid'] in seat7 or (t == 420 and not p.get('sevenReplaced')):
                    p['flags'].append('7点岗')
                self.h.log('crew_start', 'system', p['pid'], lid, x['name'], {})
                hold = x.pop('draftHold', None)
                if hold and hold['pid'] == p['pid']:
                    self.act_post_hold(lid, i, p['pid'], True, hold['reason'], t, hold['by'])
                return
        if self.opening_rest_candidate(p, push7):
            self.start_opening_rest(p, now)
            return
        p['state'] = 'ready'
        p['readyAt'] = now
        p['readySince'] = now
        if p['pid'] in (self.d['plan']['push7'] if push7 is None else push7):
            p['flags'].append('推7点')

    def _leave_line(self, p, L, i, now):
        if (L['posts'][i].get('hold') or L['posts'][i].get('hold101')):
            raise ActionError('该人员正在定岗，请先取消定岗')
        if p.get('lineStart') is not None:
            self.h.segment(p['pid'], L['id'], p['lineStart'], max(now, p['lineStart']))
        p['lastDeparture'] = {'line': L['id'], 'post': L['posts'][i]['name']}
        p['line'] = None
        p['lineStart'] = None

    def _enter(self, p, L, depart):
        if p.get('continuousStart') is None:
            p['continuousStart'] = depart
        p['state'] = 'onpost'
        p['line'] = L['id']
        p['lineStart'] = depart
        p['assign'] = None
        p['arriveAnchor'] = None
        if L['id'] not in p['visited']:
            p['visited'].append(L['id'])
        p['lastLine'] = L['id']

    def assignment_issue(self, p):
        a = p.get('assign')
        if not a:
            return None
        try:
            L = self.line(a['line'])
            if not L.get('active'):
                return '原线路已停用'
            if a.get('mode') in TARGET_MODES:
                target = self.P.get(a.get('target'))
                if not target or target['state'] != 'onpost' or target.get('line') != a['line']:
                    return '原接替对象已离开该线路'
                if self.held(target['pid']):
                    return '原接替岗位已持续定岗'
            if a.get('mode') == 'opening':
                _, _, post = self.post(a['line'], a['post'])
                if not post.get('openRequest') or post.get('occ') or (post.get('hold') or post.get('hold101')):
                    return '原加岗位置已变化'
                if post.get('openAt') and a.get('notBefore') is not None and abs(_m(post['openAt'])-a['notBefore']) > .01:
                    return '原加岗时间已变化'
            elif not self.rotation_posts(L) and not any(x.get('openAt') and not x.get('opened') for x in L['posts']):
                return '原线路暂无可轮换岗位'
        except ActionError:
            return '原线路或岗位已变化'
        return None

    def _clear_stale_assigns(self):
        changed = False
        for q in self.P.values():
            a = q.get('assign')
            if not a or q['state'] == 'heading':
                continue
            issue = self.assignment_issue(q)
            if issue:
                q['assign'] = None
                self.h.log('assign_cleared', 'system', q['pid'], a.get('line'), None, {'mode': a.get('mode'), 'target': a.get('target')})
                changed = True
            elif a.pop('blocked', None) is not None:
                changed = True
        return changed

    def preparing_shift_end(self, p, now):
        from app.rotation.forecast import late_shift
        return p['state'] == 'onpost' and p['end'] - now <= SHIFT_PREPARE_MINUTES and not late_shift(self, p)

    def meal_eligible(self, p):
        return p['end'] - p['start'] > max(MIN_MEAL_SHIFT_MINUTES, self.S['mealThreshold'])

    def meal_deadline(self, p):
        return p['end']

    def meal_options(self, p, now, *, meal_minutes=None):
        from app.rotation import forecast as F

        S = self.S
        supply = 0
        for q in self.P.values():
            ready = q.get('readyAt')
            if (q['pid'] == p['pid'] or q['role'] not in ('rotation', 'op')
                    or q.get('exception') or q.get('manualPaused')):
                continue
            if q['state'] in F.WAITING_TO_REST or F.preparing_shift_start(q, now):
                supply += 1
                continue
            if q['state'] not in ('rest', 'ready', 'meal') or ready is None or ready - now > S['mealReserveLeft']:
                continue
            if not q.get('manualRestored') and (not q['start'] <= now < q['end']
                                                or not self.can_board(q, max(now, ready), now)):
                continue
            supply += 1
        result = {'canChoose': False, 'forced': False, 'supply': supply,
                  'required': S.get('mealReserveMinPeople', DEFAULT_SETTINGS['mealReserveMinPeople']), 'reserveWithin': S['mealReserveLeft'],
                  'lines': sum(1 for L in self.d['lines'] if L.get('active') and any(x.get('open') for x in L['posts']))}
        result['reserveText'] = f"可选吃饭储备 {supply} / {result['required']} 人（剩余餐休≤{result['reserveWithin']}分钟，含待确定休息和准备上班，不含本人）"
        if not self.meal_eligible(p) or p.get('ate'):
            return result
        if now < max(_m(S['mealEarliest']), p['start'] + S['mealAfterStart']):
            return result
        left = self.meal_deadline(p) - now
        # 吃完后剩余时间太少也不安排
        if left < (S['mealMin'] if meal_minutes is None else meal_minutes) + 30:
            return result
        result['forced'] = now >= p['start'] + S['mealForceAfterStart']
        result['canChoose'] = not result['forced'] and result['supply'] >= result['required']
        return result

    def act_plan_place(self, pid, lid, i, now, actor):
        """预排拖拽：仅未开始班次，可交换岗位，不产生任何在岗计时。"""
        from app.rotation import daily

        p = self.person(pid)
        if not daily.prestart(self.d, now) or p['state'] != 'notyet' or now >= p['start'] or p['role'] != 'rotation':
            raise ActionError('只能拖拽尚未开始班次的轮岗人员')
        key = f'{lid}#{i}'
        line, index, kind = daily.slot(self.d, key)
        if not line['active']:
            raise ActionError('目标线路未启用')
        plan = self.d['plan']
        previous = daily.seven_relief_slots(self.d)
        source = next((k for group in ('crew', 'seven') for k, value in plan[group].items() if value == pid), None)
        if source == key:
            return
        target = plan['seven'].get(key) or plan['crew'].get(key) or line['posts'][index].get('occ')
        if target and self.P[target]['state'] != 'notyet':
            raise ActionError('不能交换已开始班次的人员')
        source_kind = daily.slot(self.d, source)[2] if source else None
        for group in ('crew', 'seven'):
            plan[group] = {k: value for k, value in plan[group].items() if value not in (pid, target)}
        plan[kind][key] = pid
        if target and source:
            plan[source_kind][source] = target
        daily.normalize_seven_assignments(self.d, previous)
        daily.validate_plan(self.d)
        assigned = set(plan['crew'].values()) | set(plan['seven'].values())
        plan['push7'] = [value for value in plan['push7'] if value not in assigned]
        for changed in [key] + ([source] if source else []):
            owner, slot_index, _ = daily.slot(self.d, changed)
            post = owner['posts'][slot_index]
            who = plan['seven'].get(changed) or plan['crew'].get(changed)
            post.update(occ=who if post['open'] else None, since=None)
        self.h.log('plan_place', actor, pid, lid, line['posts'][index]['name'], {'swapped': target})

    def act_post_order(self, lid, i, target_i, now, actor, swap_mode=None, target_lid=None):
        if target_lid is not None and not isinstance(target_lid, str):
            raise ActionError('目标线路选择不正确')
        if target_lid is not None and target_lid != lid:
            return self._swap_cross_line_posts(lid, i, target_lid, target_i, now, actor, swap_mode)
        L,i,_=self.post(lid,i);_,target_i,_=self.post(lid,target_i)
        if i==target_i:
            return
        if swap_mode not in (None,'posts','with_people'):
            raise ActionError('请选择只交换岗位或岗位和人员一起交换')
        if any((x.get('hold') or x.get('hold101')) for x in L['posts'][min(i,target_i):max(i,target_i)+1]):
            raise ActionError('涉及定岗岗位，请先取消定岗')
        if swap_mode=='posts':
            self._swap_post_configs(L, i, L, target_i, now)
            self._reset_line_plans(lid)
            self.h.log('post_order',actor,None,lid,L['posts'][i]['name'],{'from':i,'to':target_i,'mode':swap_mode})
            return
        old=list(L['posts'])
        if swap_mode=='with_people':
            L['posts'][i],L['posts'][target_i]=L['posts'][target_i],L['posts'][i]
            post=L['posts'][target_i]
        else:
            post=L['posts'].pop(i);L['posts'].insert(target_i,post)
        for kind in ('crew','seven'):
            previous=dict(self.d['plan'][kind])
            for j,item in enumerate(L['posts']):
                key=f'{lid}#{j}';old_j=next(k for k,x in enumerate(old) if x is item)
                self.d['plan'][kind].pop(key,None)
                if f'{lid}#{old_j}' in previous:
                    self.d['plan'][kind][key]=previous[f'{lid}#{old_j}']
        self._reset_line_plans(lid)
        self.h.log('post_order',actor,None,lid,post['name'],{'from':i,'to':target_i,'mode':swap_mode or 'order'})

    def _swap_cross_line_posts(self, lid, i, target_lid, target_i, now, actor, mode):
        L, i, left = self.post(lid, i)
        R, target_i, right = self.post(target_lid, target_i)
        if mode not in ('posts', 'with_people'):
            raise ActionError('跨线交换请选择只交换岗位或岗位和人员一起交换')
        if (left.get('hold') or left.get('hold101')) or (right.get('hold') or right.get('hold101')):
            raise ActionError('涉及定岗岗位，请先取消定岗')
        for line, index, incoming in ((L, i, right), (R, target_i, left)):
            if any(j != index and x['name'] == incoming['name'] for j, x in enumerate(line['posts'])):
                raise ActionError('交换后同一线路的岗位名称重复，请先调整岗位名称')
            if incoming.get('occ') and mode == 'with_people':
                if not line.get('active') or not incoming.get('open'):
                    raise ActionError('有人员的岗位只能交换到启用线路，且岗位必须开放')
                if self.P[incoming['occ']]['state'] not in ('onpost', 'notyet'):
                    raise ActionError('岗位人员状态已变化，请刷新后重试')
        if mode == 'posts':
            self._swap_post_configs(L, i, R, target_i, now)
        else:
            for line, index, post in ((L, i, left), (R, target_i, right)):
                if post.get('occ') and self.P[post['occ']]['state'] == 'onpost':
                    self._leave_line(self.P[post['occ']], line, index, now)
            L['posts'][i], R['posts'][target_i] = right, left
            for line, post in ((L, right), (R, left)):
                if post.get('occ') and self.P[post['occ']]['state'] == 'onpost':
                    self._enter(self.P[post['occ']], line, now)
            for kind in ('crew', 'seven'):
                plan = self.d['plan'][kind]
                a, b = f'{lid}#{i}', f'{target_lid}#{target_i}'
                av, bv = plan.pop(a, None), plan.pop(b, None)
                if bv is not None: plan[a] = bv
                if av is not None: plan[b] = av
        self._reset_line_plans(lid); self._reset_line_plans(target_lid)
        self.h.log('post_order', actor, None, lid, L['posts'][i]['name'],
                   {'from': i, 'to': target_i, 'target_line': target_lid, 'mode': mode})

    def _swap_post_configs(self, left_line, i, right_line, j, now):
        from app.rotation import daily as D
        left, right = left_line['posts'][i], right_line['posts'][j]
        if left.get('open') != right.get('open') and (left.get('occ') or right.get('occ')):
            raise ActionError('有人员时不能只交换开放状态不同的岗位')
        seven_order_changes = (left.get('seven') or 0) != (right.get('seven') or 0)
        reclassify = seven_order_changes and D.prestart(self.d, now)
        previous_reliefs = D.seven_relief_slots(self.d) if reclassify else None
        plan = self.d['plan']
        if seven_order_changes and not reclassify and 'push7Targets' not in plan:
            # Once the first shift starts, keep the selected people and their duties.
            plan['push7Targets'] = {relief: plan['seven'][key]
                                    for key, relief in D.seven_relief_slots(self.d).items()}
        fixed = {'occ', 'since', 'hold'}
        configs = [{k: v for k, v in post.items() if k not in fixed} for post in (right, left)]
        for post, config in zip((left, right), configs):
            keep = {k: v for k, v in post.items() if k in fixed}
            post.clear()
            post.update(config, **keep)
        if reclassify:
            for line, index in ((left_line, i), (right_line, j)):
                key = f"{line['id']}#{index}"
                pid = plan['seven'].pop(key, None) or plan['crew'].pop(key, None)
                if pid:
                    seven = line['posts'][index].get('seven') and self.P[pid]['start'] == 420
                    plan['seven' if seven else 'crew'][key] = pid
            # A relief follows its early worker. A post-only swap does not move that worker.
            plan.setdefault('push7Targets', {})
            D.normalize_seven_assignments(self.d, previous_reliefs)
            D.validate_plan(self.d)

    def act_line(self, lid, active, now, actor):
        from app.rotation import daily

        if daily.prestart(self.d, now):
            if daily.set_prestart_line(self.d, lid, active, settings=self.S,
                                      read_history=getattr(self.h, 'push7_history', None), now=now):
                self.h.log('line_on' if active else 'line_off', actor, None, lid, None, {'prestart': True})
            return
        L = self.line(lid)
        if not active and any((x.get('hold') or x.get('hold101')) for x in L['posts']):
            raise ActionError('本线还有定岗人员，请先取消定岗再停用')
        if active:
            L['active'] = True
            for x in L['posts']:
                if not x.get('openAt') or x['opened']:
                    if not x.get('draftClosed') and (not x['manual'] or x['open']):
                        x['open'] = True
            self.h.log('line_on', actor, None, lid, None, {})
        else:
            from app.rotation.forecast import close_post
            for i, x in enumerate(L['posts']):
                if x.get('open'):
                    close_post(self, L, i, now, actor)
                x['manual'] = False
            L['active'] = False
            self.h.log('line_off', actor, None, lid, None, {})
        self._reset_line_plans(lid)

    def _reset_line_plans(self, lid):
        for q in self.P.values():
            if q.get('assign') and q['assign']['line'] == lid and q['state'] != 'heading' \
                    and q['assign'].get('mode') in ('push', 'opening'):
                q['assign'] = None

    def act_add_person(self, person, now, actor):
        if person['pid'] in self.P:
            raise ActionError('%s 今天已在名单中' % person['name'])
        person.update(state='notyet', assign=None, visited=[], ate=False, flags=[], line=None, lineStart=None,
                      readyAt=None, role='rotation', absences=[], tags=[], label='', note='临时加人',
                      arriveAnchor=None, readySince=None)
        self.P[person['pid']] = person
        self.h.log('add_person', actor, person['pid'], None, None,
                   {'start': person['start'], 'end': person['end']})

    def act_set_close(self, close, now, actor):
        self.d['closeAt'] = close
        self.h.log('set_close', actor, None, None, None, {'close': close})

    def should_end(self, now):
        return self.d['status'] == 'live' and now >= rotation_end(self.d)

    def end_day(self, now):
        from app.rotation.forecast import finish_continuous
        self.d['hold101Active']=False
        for L in self.d['lines']:
            for x in L['posts']:
                x.pop('hold101',None)
                hold = x.pop('hold', None)
                if hold:
                    self.h.log('post_resume', 'system', hold['pid'], L['id'], x['name'],
                                {'reason': '轮岗结束，定岗自动结束', 'original_reason': hold['reason'],
                                'since': hold['since'], 'duration': max(0, now-hold['since'])})
                x['open'] = False
        for p in self.P.values():
            if p['state'] in ('done', 'excluded'):
                continue
            if p['state'] == 'onpost':
                L, i = self.find_post(p['pid'])
                if L is not None:
                    self._leave_line(p, L, i, now)
                    L['posts'][i]['occ'] = None
            finish_continuous(p, now)
            p['assign'] = None
            p.pop('entryHold', None)
            p['state'] = 'done'
        self.d['status'] = 'ended'
        self.h.log('day_end', 'system', None, None, None, {})

    def alerts(self, now):
        """给主管看的实时提醒。"""
        S = self.S
        out = []
        taken = self.targeted()
        for p in self.P.values():
            st = p.get('state')
            a = p.get('assign')
            if st == 'ready' and a:
                over = now - a['departAt']
                if over >= S['readyNotify']:
                    out.append({'level': 'notify', 'pid': p['pid'], 'msg': '%s 超过出发时间 %d 分钟未出发' % (p['name'], over)})
            if st == 'ready' and not a and now - (p.get('readySince') or now) >= S['unassignedWarn']:
                out.append({'level': 'warn', 'pid': p['pid'], 'msg': '%s 待出发 %d 分钟仍没有去向' % (p['name'], now - p['readySince'])})
            if st in ('walkback', 'pending') and p.get('walkbackSince') is not None and now - p['walkbackSince'] >= S['walkBackWarn']:
                out.append({'level': 'warn', 'pid': p['pid'], 'msg': '%s 下线 %d 分钟仍未在大屏点去休息' % (p['name'], now - p['walkbackSince'])})
            if st == 'onpost':
                t, kind, _ = self.leave_at(p, now)
                if self.held(p['pid']):
                    if self.d.get('closed') or now >= t:
                        out.append({'level': 'notify', 'pid': p['pid'], 'msg': '%s 仍在定岗，结束现场任务后请手动取消；当天轮岗结束时自动解除' % p['name']})
                    continue
                if kind == 'out':
                    if now >= t + S['outWarnAfter'] and p['pid'] not in taken:
                        out.append({'level': 'notify', 'pid': p['pid'], 'msg': '%s 已过出圈时间 %s %d 分钟，暂无人可推' % (p['name'], fmt(t), now - t)})
                elif self.preparing_shift_end(p, now) and p['pid'] not in taken:
                    out.append({'level': 'notify', 'pid': p['pid'], 'msg': '%s 已准备下班，暂无人可替' % p['name']})
            if p.get('role') in ('rotation', 'op') and st not in ('done', 'excluded', 'notyet', 'meal') and not p.get('ate') \
                    and self.meal_eligible(p) and not self.d.get('closed') \
                    and self.meal_deadline(p) - now < S['mealWarnLeft']:
                out.append({'level': 'notify', 'pid': p['pid'], 'msg': '%s 还没吃饭，离下班不到 %d 小时' % (
                    p['name'], S['mealWarnLeft'] // 60)})
        if now >= _m(S['lineOpenAt']) + 2 and not self.d.get('closed'):
            incoming = {q['assign']['line'] for q in self.P.values() if q.get('assign')}
            for L in self.d['lines']:
                if L.get('active') and L['id'] not in incoming:
                    for x in L['posts']:
                        if x.get('open') and not x.get('occ'):
                            out.append({'level': 'warn', 'msg': '%s %s 空岗，暂无人前往' % (L['id'], x['name'])})
        return out
