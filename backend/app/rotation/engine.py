"""轮岗引擎：当天状态、初始草稿、推岗与分配。

由原型 rotation-board/engine.py 迁移，并按 2026-10 确认的规则修改：
- 没有“上线满多少分钟才推”的保护；派人先推出口岗站得最久的人，站岗时长（整分钟）相同时
  再看当天没进过这条线、本周在这条线累计少；
- 推下班、推出圈（固定暂离）最优先：替换者在下班/出圈前 offLead 分钟到岗，从入口岗推进，
  链条后移到要走的人为止把他推出来；被推出的人直接转为已下班/出圈中，不需要到大屏确认；
- 推 7 点：07:15 班的人直接在 7 点岗位对换 07:00 班的人，被替下的人照常休息后轮岗；
- 固定暂离优先：到出圈时间仍没人替也按时出圈，空出的岗位由下一个人优先补上；
- 吃饭：休息中、待出发和吃饭剩不到 mealReserveLeft 分钟的人，够开着的线数时才安排吃饭；
- 到岗时距下班/出圈不超过 noBoardBefore 分钟就不再上岗；
- 出发只能在计划出发时间前 departEarly 分钟以内点。

时间一律用“当天零点起的分钟数”（浮点）。所有方法都在外部锁内调用。
"""
from __future__ import annotations

import math

from app.rotation.roster import classify, fmt, hm

DEFAULT_SETTINGS = {
    'lineOpenAt': '07:15',        # 开园岗位开始时间
    'opReleaseAt': '07:15',       # OP 转为待定的时间
    'walkMin': 3,                 # 默认路程（分钟），可在线配置里按线覆盖
    'breakMin': 15,
    'mealMin': 45,
    'mealThreshold': 390,         # 班次超过 6.5 小时才有吃饭
    'mealEarliest': '09:30',
    'mealAfterStart': 150,        # 上班后多久才开始安排吃饭（早班自然优先）
    'mealForceAfterStart': 300,   # 上班超过这么久仍未吃饭，强制安排
    'mealReserveLeft': 20,        # 吃饭剩不到这么多分钟的人算作“休息中”
    'mealLastChance': 270,        # 离下班或闭园不到这么多分钟仍没吃饭，不再等休息人数够了才吃
    'offLead': 20,                # 下班/出圈前多少分钟被替下（替换者到岗时间）
    'noBoardBefore': 30,          # 到岗时距下班/出圈不超过这么多分钟，不再上岗
    'endLead': 45,                # 比替换到岗时间提前多少分钟开始安排替换的人
    'departEarly': 1,             # 最多可比计划出发时间提前几分钟点出发
    'futureWait': 15,             # 等定时开岗最多等多久
    'readyYellow': 0,             # 超过出发时间多少分钟变黄
    'readyRed': 2,                # 超过多少分钟变红闪烁
    'readyNotify': 5,             # 超过多少分钟提醒主管
    'walkBackWarn': 10,           # 下线后多少分钟未到大屏确认提示
    'unassignedWarn': 10,         # 待出发超过多少分钟仍没有去向提示
    'parkClose': '21:30',         # 周班表里没有营业时间时的默认闭园时间
    'closeStopPush': 20,          # 闭园前多少分钟起不再派新人进线
    'closeDelay': 0,              # 闭园后多少分钟自动撤下所有岗位
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
    {'id': 'D2', 'group': 'D', 'standby': True, 'posts': [{'name': '铁马A'}, {'name': '铁马B'}, {'name': '铁马C'}]},
    {'id': 'E', 'group': 'E', 'standby': True, 'posts': [{'name': '城内巡视'}, {'name': '队末'}]},
]

POOL_STATES = ('walkback', 'pending', 'rest', 'meal', 'ready')
REST_STATES = ('rest', 'meal', 'ready')
CLOSING_WORK = '闭园后工作'
TARGET_MODES = ('push7', 'chain')


class ActionError(Exception):
    pass


def _m(v, default=None):
    if v is None or v == '':
        return default
    if isinstance(v, (int, float)):
        return float(v)
    return float(hm(v))


# ---------------------------------------------------------------- 草稿生成

def build_draft(date, roster, config, settings, duty_counts, week_minutes):
    """根据周班表生成当天初始轮岗草稿。

    duty_counts: {pid: {'push7': n, 'lost': n}} 本周已做次数
    week_minutes: {pid: {line: minutes}} 本周各线累计
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
                off.append({'pid': pid, 'name': base['name'], 'label': info['label']})
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
                           seven=x.get('seven')) for x in L['posts']],
        })
    rot = [p for p in persons.values() if p['role'] == 'rotation']
    alerts = []
    # OP：早于开园岗位 1 小时以上上班的人
    for p in rot:
        if p['start'] <= open_at - 60:
            p['role'] = 'op'
    # 7 点岗位：07:00 班站迎宾、快迎1、小C；只有 2 人时小C 不设 7 点岗
    sevens = sorted([p for p in rot if p['role'] == 'rotation' and p['start'] == 420],
                    key=lambda p: (-(p['end'] - p['start']), p['name']))
    seven_posts = sorted([(post['seven'], L['id'], i) for L in lines for i, post in enumerate(L['posts'])
                          if post.get('seven')])
    if len(sevens) < 2:
        alerts.append('07:00 上班只有 %d 人，少于 2 人，7 点岗位需要主管安排' % len(sevens))
    use = seven_posts if len(sevens) >= 3 else seven_posts[:min(2, len(sevens))]
    seven = {}
    for (_, lid, i), p in zip(use, sevens):
        seven['%s#%d' % (lid, i)] = p['pid']
    # 推 7 点：07:15 班次中本周次数最少的人，优先长班
    early = [p for p in rot if p['role'] == 'rotation' and p['start'] == open_at and not p['absences']]
    early.sort(key=lambda p: (duty_counts.get(p['pid'], {}).get('push7', 0), -(p['end'] - p['start']), p['name']))
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
             if not post.get('openAt') and '%s#%d' % (L['id'], i) not in seven]
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
        'date': date, 'status': 'draft', 'version': 1,
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
        self.d = day
        self.S = settings
        self.h = hooks

    # -------- 工具
    @property
    def P(self):
        return self.d['persons']

    def line(self, lid):
        for L in self.d['lines']:
            if L['id'] == lid:
                return L
        raise ActionError('没有这条线：%s' % lid)

    def walk(self, lid):
        L = self.line(lid)
        return float(L.get('walk') or self.S['walkMin'])

    def open_posts(self, L):
        return [i for i, x in enumerate(L['posts']) if x.get('open')]

    def find_post(self, pid):
        for L in self.d['lines']:
            for i, x in enumerate(L['posts']):
                if x.get('occ') == pid:
                    return L, i
        return None, None

    def close_min(self):
        c = self.d.get('closeAt')
        return _m(c) if c else None

    def person(self, pid):
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
        """到岗时距下班/出圈必须大于 noBoardBefore 分钟。"""
        t, _, _ = self.leave_at(p, now)
        return t - arrival > self.S['noBoardBefore']

    def targeted(self):
        """已被安排替换（推下班、推出圈、推 7 点）的人 -> 去替换他的人。"""
        out = {}
        for q in self.P.values():
            a = q.get('assign')
            if a and a.get('mode') in TARGET_MODES and a.get('target'):
                out[a['target']] = q['pid']
        return out

    # -------- 发布
    def publish(self, now, actor):
        d = self.d
        d['status'] = 'live'
        for L in d['lines']:
            L['active'] = not L['standby']
            for x in L['posts']:
                x['occ'] = None
                x['since'] = None
                x['open'] = L['active'] and not x.get('openAt')
                x['opened'] = not x.get('openAt')
                x['manual'] = False
        for p in self.P.values():
            p.update(state='notyet', assign=None, visited=[], ate=False, flags=[],
                     line=None, lineStart=None, readyAt=None, breakKind=None, breakStart=None,
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
        self.tick(now)

    # -------- 每秒推进
    def tick(self, now):
        d = self.d
        if d['status'] != 'live':
            return False
        changed = False
        S = self.S
        plan = d['plan']
        seat = {v: k for k, v in plan['crew'].items()}
        seat7 = {v: k for k, v in plan['seven'].items()}
        # 定时开岗 / 关岗
        for L in d['lines']:
            for i, x in enumerate(L['posts']):
                if x.get('openAt') and not x['opened'] and now >= _m(x['openAt']):
                    x['opened'] = True
                    if L['active'] and not x['manual'] and not d.get('closed'):
                        x['open'] = True
                        self.h.log('post_open', 'system', None, L['id'], x['name'], {'auto': True})
                    changed = True
                if x.get('closeAt') and x['open'] and now >= _m(x['closeAt']) and not x.get('closedAuto'):
                    x['closedAuto'] = True
                    self._close_post(L, i, now, 'system')
                    changed = True
        cm = self.close_min()
        if cm is not None and not d.get('closed') and now >= cm + S.get('closeDelay', 0):
            self._park_close(now)
            changed = True
        off_lead = S['offLead']
        for p in list(self.P.values()):
            st = p['state']
            if st == 'notyet' and now >= p['start']:
                self._shift_start(p, now, seat, seat7)
                changed = True
                continue
            if st == 'op' and now >= _m(S['opReleaseAt']):
                p['state'] = 'pending'
                p['walkbackSince'] = now
                changed = True
            elif st == 'heading' and now >= p['arriveAt']:
                self._arrive_line(p, now)
                changed = True
            elif st in ('rest', 'meal') and now >= p['readyAt']:
                p['state'] = 'ready'
                p['readySince'] = now
                changed = True
            elif st == 'away' and p['away'].get('until') is not None and now >= p['away']['until']:
                if p['away'].get('fixed'):
                    for a in p.get('absences', []):
                        if a.get('label') == p['away']['reason'] and a['end'] == p['away']['until']:
                            a['done'] = True
                if d.get('closed'):
                    p['away'] = {'reason': CLOSING_WORK, 'until': None}
                else:
                    p['away'] = None
                    p['state'] = 'ready'
                    p['readyAt'] = now
                    p['readySince'] = now
                    p['assign'] = None
                changed = True
            # 固定暂离开始：池子里的人直接出圈
            if p['state'] in ('rest', 'meal', 'ready', 'pending'):
                for a in p.get('absences', []):
                    if not a.get('done') and now >= a['start'] - off_lead and now < a['end']:
                        self._go_out(p, a, now, 'system')
                        changed = True
                        break
            # 固定暂离优先：到出圈时间仍没人替也按时出圈，空出的岗位由下一个人优先补上
            if p['state'] == 'onpost':
                for a in p.get('absences', []):
                    if not a.get('done') and a['start'] <= now < a['end']:
                        L, i = self.find_post(p['pid'])
                        if L is not None:
                            self._leave_line(p, L, i, now)
                            L['posts'][i]['occ'] = None
                            self.alert('%s 到出圈时间（%s）无人替换已出圈，%s %s 空岗待补' % (p['name'], a['label'], L['id'], L['posts'][i]['name']), now)
                        p['downReason'] = '出圈'
                        self._go_out(p, a, now, 'system')
                        self.h.log('out_unreplaced', 'system', p['pid'], L['id'] if L else None, L['posts'][i]['name'] if L else None, {'reason': a['label']})
                        changed = True
                        break
            # 下班：池子里、暂离中、下线途中的人在下班前 offLead 分钟直接下班
            if p['state'] in ('walkback', 'rest', 'meal', 'ready', 'pending', 'away') and now >= p['end'] - off_lead:
                was = p['state']
                p['assign'] = None
                p['state'] = 'done'
                self.h.log('shift_end_unconfirmed' if was == 'walkback' else 'shift_end', 'system', p['pid'], None, None, {})
                changed = True
            if p['state'] == 'onpost' and now >= p['end']:
                L, i = self.find_post(p['pid'])
                if L is not None:
                    self._leave_line(p, L, i, now)
                    L['posts'][i]['occ'] = None
                p['state'] = 'done'
                self.h.log('shift_end_onpost', 'system', p['pid'], L['id'] if L else None, None, {})
                changed = True
        if self._assign_all(now):
            changed = True
        return changed

    def _park_close(self, now):
        """闭园：撤下所有岗位，线上的人回板面后转为闭园后工作，池子里的人直接转入。"""
        self.d['closed'] = True
        for L in self.d['lines']:
            for i, x in enumerate(L['posts']):
                x['openBeforeClose'] = bool(x.get('open'))
                if x.get('occ'):
                    self._push_off(x['occ'], L, i, now, '闭园')
                x['occ'] = None
                x['open'] = False
        for p in self.P.values():
            p['assign'] = None
            if p['state'] in ('pending', 'rest', 'meal', 'ready') or (p['state'] == 'notyet' and p['role'] in ('rotation', 'op')):
                p['state'] = 'away'
                p['away'] = {'reason': CLOSING_WORK, 'until': None}
        self.h.log('park_close', 'system', None, None, None, {})

    def _shift_start(self, p, now, seat, seat7):
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
            if L['active'] and x.get('occ') is None:
                x['open'] = True
                x['occ'] = p['pid']
                x['since'] = t
                p.update(state='onpost', line=lid, lineStart=t, lastLine=lid)
                p['visited'].append(lid)
                if p['pid'] in seat7:
                    p['flags'].append('7点岗')
                self.h.log('crew_start', 'system', p['pid'], lid, x['name'], {})
                return
        p['state'] = 'ready'
        p['readyAt'] = now
        p['readySince'] = now
        if p['pid'] in self.d['plan']['push7']:
            p['flags'].append('推7点')

    # -------- 下线
    def _leave_line(self, p, L, i, now):
        if p.get('lineStart') is not None:
            self.h.segment(p['pid'], L['id'], p['lineStart'], now)
        p['line'] = None
        p['lineStart'] = None

    def _push_off(self, x_pid, L, i, now, reason):
        """把某人从线上替下，进入下线途中，之后要到大屏点“去休息”。"""
        p = self.P[x_pid]
        self._leave_line(p, L, i, now)
        p['state'] = 'walkback'
        p['walkbackSince'] = now
        p['downReason'] = reason
        p['arriveAnchor'] = None
        p['after'] = 'closing' if reason == '闭园' else self._arrive_outcome(p, now + self.S['walkMin'])[0]
        self.h.log('pushed_off', 'system', x_pid, L['id'], L['posts'][i]['name'], {'reason': reason})

    def _go_out(self, p, a, now, actor):
        """出圈（固定暂离）：转为暂离，到暂离结束回到池子。"""
        a['done'] = True
        p['assign'] = None
        p['state'] = 'away'
        p['away'] = {'reason': a['label'], 'until': a['end'], 'fixed': True}
        self.h.log('away', actor, p['pid'], None, None, {'reason': a['label'], 'fixed': True})

    def _remove_leaving(self, x_pid, L, i, now, why):
        """推下班 / 推出圈：被推出的人不回大屏，直接转为已下班或出圈中。"""
        p = self.P[x_pid]
        self._leave_line(p, L, i, now)
        p['downReason'] = why
        _, kind, a = self.leave_at(p, now)
        if kind == 'out' and a is not None:
            self._go_out(p, a, now, 'system')
        else:
            p['state'] = 'done'
            p['assign'] = None
        self.h.log('pushed_out', 'system', x_pid, L['id'], L['posts'][i]['name'], {'reason': why})

    # -------- 到线
    def _arrive_line(self, p, now):
        a = p['assign'] or {}
        lid = a.get('line')
        try:
            L = self.line(lid)
        except ActionError:
            L = None
        if self.d.get('closed'):
            p['state'] = 'away'
            p['away'] = {'reason': CLOSING_WORK, 'until': None}
            p['assign'] = None
            return
        if L is None or not L.get('active') or not self.open_posts(L):
            p['state'] = 'ready'
            p['readyAt'] = now
            p['readySince'] = now
            p['assign'] = None
            self.alert('%s 到达 %s 时该线已停用，已重新分配' % (p['name'], lid), now)
            return
        depart = p.get('departedAt', now)
        tgt = a.get('target')
        TL, ti = self.find_post(tgt) if tgt else (None, None)
        if a.get('mode') == 'push7' and TL is L:
            # 推 7 点：在 7 点岗位直接对换
            x = L['posts'][ti]
            self._push_off(tgt, L, ti, now, '推7点下来')
            self.P[tgt]['flags'].append('推7点下来')
            x['occ'] = p['pid']
            x['since'] = now
            self._enter(p, L, depart)
            self.h.log('push7', 'system', p['pid'], L['id'], x['name'], {'target': tgt})
            return
        if a.get('mode') == 'chain' and TL is L:
            # 推下班 / 推出圈：从入口岗进，链条后移到要走的人为止
            self._chain_out(L, p['pid'], tgt, ti, now)
            self._remove_leaving(tgt, L, ti, now, a.get('why') or '推下班')
            self._enter(p, L, depart)
            self.h.log('chain_out', 'system', p['pid'], L['id'], L['posts'][self.open_posts(L)[0]]['name'], {'target': tgt, 'why': a.get('why')})
            return
        if tgt:
            self.h.log('target_gone', 'system', p['pid'], L['id'], None, {'target': tgt})
        # 普通推岗：从入口岗进，依次后移，遇到空岗停止，否则出口岗的人下线
        carry = p['pid']
        out = None
        for i in self.open_posts(L):
            x = L['posts'][i]
            if x.get('occ') is None:
                x['occ'] = carry
                x['since'] = now
                carry = None
                break
            x['occ'], carry = carry, x['occ']
            x['since'] = now
            out = (carry, i)
        if carry is not None and out:
            self._push_off(carry, L, out[1], now, '推岗')
        self._enter(p, L, depart)
        self.h.log('push', 'system', p['pid'], L['id'], L['posts'][self.open_posts(L)[0]]['name'], {'out': carry})

    def _chain_out(self, L, new_pid, target, ti, now):
        """入口岗到 target 所在岗位之间整体后移一位，target 腾出。中间有空岗时前面的人往后补齐。"""
        seq = [i for i in self.open_posts(L) if i <= ti]
        if ti not in seq:
            seq.append(ti)
        others = [L['posts'][i]['occ'] for i in seq if L['posts'][i].get('occ') not in (None, target)]
        new_seq = [new_pid] + others
        for k, i in enumerate(seq):
            x = L['posts'][i]
            occ = new_seq[k] if k < len(new_seq) else None
            if x.get('occ') != occ:
                x['occ'] = occ
                x['since'] = now

    def _enter(self, p, L, depart):
        p['state'] = 'onpost'
        p['line'] = L['id']
        p['lineStart'] = depart
        p['assign'] = None
        p['arriveAnchor'] = None
        if L['id'] not in p['visited']:
            p['visited'].append(L['id'])
        p['lastLine'] = L['id']

    # -------- 分配
    def _ready_time(self, p, now):
        if p['state'] == 'ready':
            return max(now, p.get('readyAt') or now)
        return p.get('readyAt') or now

    def _clear_stale_assigns(self):
        """替换的对象已不在那条线、或去的线已停用：没出发的去向作废，重新分配。"""
        changed = False
        for q in self.P.values():
            a = q.get('assign')
            if not a or q['state'] == 'heading':
                continue
            stale = False
            try:
                L = self.line(a['line'])
                stale = not L.get('active')
            except ActionError:
                stale = True
            if not stale and a.get('mode') in TARGET_MODES:
                t = self.P.get(a.get('target'))
                stale = not t or t['state'] != 'onpost' or t.get('line') != a['line']
            if stale:
                q['assign'] = None
                self.h.log('assign_cleared', 'system', q['pid'], a.get('line'), None, {'mode': a.get('mode'), 'target': a.get('target')})
                changed = True
        return changed

    def _replace_needs(self, now):
        needs = []
        taken = self.targeted()
        cm = self.close_min()
        S = self.S
        for p in self.P.values():
            if p['state'] != 'onpost' or p['pid'] in taken:
                continue
            t, kind, _ = self.leave_at(p, now)
            need_at = t - S['offLead']
            if cm is not None and need_at >= cm + S.get('closeDelay', 0):
                continue  # 闭园时会统一撤下，不用另找替换
            if now >= need_at - S['endLead']:
                needs.append((need_at, p, kind))
        needs.sort(key=lambda n: n[0])
        return needs

    def _push7_pairs(self):
        plan = self.d['plan']
        order = sorted(plan['seven'], key=self._seven_order)
        return list(zip(range(len(plan['push7'])), plan['push7'], [plan['seven'][k] for k in order]))

    def _assign_all(self, now):
        if self.d.get('closed'):
            return False
        changed = self._clear_stale_assigns()
        S = self.S
        plan = self.d['plan']
        # 推 7 点：07:15 班的人在 7 点岗位对换 07:00 班的人
        for idx, a, b in self._push7_pairs():
            pa, pb = self.P.get(a), self.P.get(b)
            if not pb or pb['state'] != 'onpost' or '7点岗' not in pb['flags'] or '推7点下来' in pb['flags']:
                continue
            if b in self.targeted():
                continue
            if pa and pa['state'] in ('done', 'away', 'excluded') and not pa.get('push7done'):
                sub = self._push7_substitute(now)
                if sub:
                    plan['push7'][idx] = sub['pid']
                    if '推7点' not in sub['flags']:
                        sub['flags'].append('推7点')
                    self.h.log('push7_substitute', 'system', sub['pid'], None, None, {'instead_of': a})
                    self.alert('%s 不能推 7 点，已改由 %s 推' % (pa['name'], sub['name']), now)
                    pa = sub
                    changed = True
            if pa and pa['state'] == 'ready' and not pa.get('push7done') \
                    and not (pa.get('assign') and pa['assign'].get('mode') in TARGET_MODES):
                pa['assign'] = {'line': pb['line'], 'mode': 'push7', 'target': b, 'departAt': now, 'why': '推7点'}
                pa['push7done'] = True
                changed = True
        # 推下班 / 推出圈
        for need_at, x, kind in self._replace_needs(now):
            walk = self.walk(x['line'])
            cands = []
            for p in self.P.values():
                if p['state'] not in REST_STATES or p['role'] not in ('rotation', 'op'):
                    continue
                if p.get('assign') and p['assign'].get('mode') in TARGET_MODES:
                    continue
                ready = self._ready_time(p, now)
                if self.can_board(p, max(ready + walk, need_at), now):
                    cands.append((p, ready))
            if not cands:
                continue
            on_time = [(p, r) for p, r in cands if r + walk <= need_at]
            if on_time:
                pick, ready = min(on_time, key=lambda c: (x['line'] in c[0].get('visited', []), c[1], c[0]['start']))
            else:
                pick, ready = min(cands, key=lambda c: (c[1], c[0]['start']))
            pick['assign'] = {'line': x['line'], 'mode': 'chain', 'target': x['pid'],
                              'departAt': max(ready, need_at - walk),
                              'why': '推出圈' if kind == 'out' else '推下班'}
            changed = True
        # 普通分配
        todo = [p for p in self.P.values() if p['state'] in REST_STATES and not p.get('assign')
                and p['role'] in ('rotation', 'op')]
        todo.sort(key=lambda p: (self._ready_time(p, now), p['start']))
        if todo:
            wk = self.h.week_minutes()
            for p in todo:
                a = self._best_line(p, now, wk)
                if a:
                    p['assign'] = a
                    changed = True
        return changed

    def _push7_substitute(self, now):
        open_at = _m(self.S['lineOpenAt'])
        plan = self.d['plan']
        cands = [p for p in self.P.values() if p['role'] == 'rotation' and p['start'] == open_at
                 and p['state'] == 'ready' and p['pid'] not in plan['push7'] and not p['absences']
                 and not (p.get('assign') and p['assign'].get('mode') in TARGET_MODES)]
        if not cands:
            return None
        return min(cands, key=lambda p: (self._ready_time(p, now), -(p['end'] - p['start']), p['name']))

    def _seven_order(self, key):
        lid, i = key.split('#')
        return self.line(lid)['posts'][int(i)].get('seven') or 9

    def _chain(self, L):
        """从出口岗往入口岗的在岗人列表。"""
        return [L['posts'][i]['occ'] for i in reversed(self.open_posts(L)) if L['posts'][i].get('occ')]

    def _best_line(self, p, now, wk):
        """选线：先补空岗或马上定时开的岗位；否则推出口岗站得最久的人。

        站岗时长按整分钟比，相同再看：不是刚下来的线、当天没进过、本周在该线累计少。
        """
        S = self.S
        ready = self._ready_time(p, now)
        cm = self.close_min()
        best = None
        for order, L in enumerate(self.d['lines']):
            if not L.get('active'):
                continue
            idx = self.open_posts(L)
            future = sorted(_m(x['openAt']) for x in L['posts']
                            if x.get('openAt') and not x['opened'] and not x['manual'])
            if not idx and not future:
                continue
            walk = self.walk(L['id'])
            arrive0 = ready + walk
            res = sorted([q for q in self.P.values() if q.get('assign') and q['assign']['line'] == L['id']
                          and q['assign'].get('mode') == 'push'],
                         key=lambda q: q['assign']['departAt'])
            k = len(res)
            vac = [i for i in idx if L['posts'][i].get('occ') is None]
            option = None
            if k < len(vac):
                option = (0, 0, arrive0)  # 补空岗
            elif future:
                after = sum(1 for q in res if q['assign']['departAt'] + walk >= future[0] - 0.01)
                if after < len(future) and future[after] - arrive0 <= S['futureWait']:
                    arrival = max(arrive0, future[after])
                    option = (0, arrival - arrive0, arrival)  # 等定时开岗
            if option is None:
                chain = self._chain(L)
                j = k - len(vac)
                if 0 <= j < len(chain):
                    c = self.P[chain[j]]
                    start = c['lineStart'] if c.get('lineStart') is not None else now
                    standing = math.floor(arrive0 - start)
                    option = (1, -standing, arrive0)  # 推站得最久的
            if option is None:
                continue
            cls, rank, arrival = option
            if not self.can_board(p, arrival, now):
                continue
            if cm is not None and arrival >= cm - S.get('closeStopPush', 20):
                continue
            key = (cls, rank, p.get('lastLine') == L['id'], L['id'] in p.get('visited', []),
                   wk.get(p['pid'], {}).get(L['id'], 0), order)
            if best is None or key < best[0]:
                best = (key, {'line': L['id'], 'mode': 'push', 'departAt': arrival - walk})
        return best[1] if best else None

    # -------- 大屏操作
    def _arrive_outcome(self, p, now):
        """点“去休息”时的去向：('done' 下班 / 'away' 出圈 / None 正常休息)。"""
        anchor = p.get('arriveAnchor') or now
        t, kind, _ = self.leave_at(p, now)
        if t - (anchor + self.S['breakMin'] + self.S['walkMin']) <= self.S['noBoardBefore']:
            return ('away' if kind == 'out' else 'done'), kind
        return None, kind

    def act_arrive(self, pid, now, actor):
        p = self.person(pid)
        if p['state'] not in ('walkback', 'pending'):
            raise ActionError('%s 当前不是待到达状态，可能已经处理过' % p['name'])
        first = p.get('arriveAnchor') is None
        if first:
            p['walkBack'] = now - (p.get('walkbackSince') or now)
        if p.get('after') == 'closing' or self.d.get('closed'):
            p['state'] = 'away'
            p['away'] = {'reason': CLOSING_WORK, 'until': None}
            self.h.log('arrive_away', actor, pid, None, None, {'reason': CLOSING_WORK})
            return
        outcome, _ = self._arrive_outcome(p, now)
        if outcome == 'done':
            p['state'] = 'done'
            self.h.log('arrive_done', actor, pid, None, None, {})
            return
        if outcome == 'away':
            _, _, a = self.leave_at(p, now)
            self._go_out(p, a, now, actor)
            return
        anchor = p.get('arriveAnchor') or now
        kind = self._meal_decision(p, now)
        p['arriveAnchor'] = anchor
        p['breakKind'] = kind
        p['breakStart'] = anchor
        p['readyAt'] = anchor + (self.S['mealMin'] if kind == 'meal' else self.S['breakMin'])
        p['state'] = kind
        if kind == 'meal':
            p['ate'] = True
        if now >= p['readyAt']:
            p['state'] = 'ready'
            p['readySince'] = now
        self.h.log('arrive', actor, pid, None, None, {'kind': kind, 'walk': round(p.get('walkBack') or 0, 1), 'again': not first})

    def meal_eligible(self, p):
        return p['end'] - p['start'] > self.S['mealThreshold']

    def meal_deadline(self, p):
        """还能轮岗吃饭的最后时间：下班和闭园中较早的那个。"""
        cm = self.close_min()
        return min(p['end'], cm) if cm is not None else p['end']

    def _meal_decision(self, p, now):
        S = self.S
        if not self.meal_eligible(p) or p.get('ate'):
            return 'rest'
        if now < max(_m(S['mealEarliest']), p['start'] + S['mealAfterStart']):
            return 'rest'
        left = self.meal_deadline(p) - now
        # 吃完后剩余时间太少也不安排
        if left < S['mealMin'] + 30:
            return 'rest'
        if self.rest_supply(now, exclude=p['pid']) >= self.active_line_count() or now >= p['start'] + S['mealForceAfterStart'] \
                or left <= S['mealLastChance']:
            return 'meal'
        return 'rest'

    def active_line_count(self):
        return sum(1 for L in self.d['lines'] if L.get('active'))

    def rest_supply(self, now, exclude=None):
        """随时能去推岗的人：休息中、待出发，以及吃饭剩不到 mealReserveLeft 分钟的人。"""
        n = 0
        for q in self.P.values():
            if q['role'] not in ('rotation', 'op') or q['pid'] == exclude:
                continue
            if q['state'] in ('rest', 'ready'):
                n += 1
            elif q['state'] == 'meal' and q['readyAt'] - now <= self.S['mealReserveLeft']:
                n += 1
        return n

    def act_depart(self, pid, now, actor):
        p = self.person(pid)
        if p['state'] != 'ready':
            raise ActionError('%s 还不能出发' % p['name'])
        if now < (p.get('readyAt') or now) - 0.01:
            raise ActionError('%s 休息还没结束' % p['name'])
        if not p.get('assign'):
            raise ActionError('%s 还没有分配去向' % p['name'])
        depart_at = p['assign']['departAt']
        if now < depart_at - self.S['departEarly'] - 0.01:
            raise ActionError('%s 还没到出发时间（%s）' % (p['name'], fmt(depart_at)))
        L = self.line(p['assign']['line'])
        p['state'] = 'heading'
        p['departedAt'] = now
        p['arriveAt'] = now + self.walk(L['id'])
        self.h.log('depart', actor, pid, L['id'], None, dict(p['assign']))

    # -------- 主管操作
    def act_undo(self, pid, now, actor):
        """撤回到达：回到待到达；再次到达时休息仍从第一次到达算起。"""
        p = self.person(pid)
        if p['state'] not in ('rest', 'meal', 'ready'):
            raise ActionError('%s 当前不能撤回' % p['name'])
        if p.get('breakStart') is None:
            raise ActionError('%s 没有到达记录可撤回' % p['name'])
        if p.get('breakKind') == 'meal':
            p['ate'] = False
        p['assign'] = None
        p['state'] = 'walkback'
        p['breakStart'] = None
        p['breakKind'] = None
        self.h.log('undo_arrive', actor, pid, None, None, {})

    def _close_post(self, L, i, now, actor):
        x = L['posts'][i]
        if x.get('occ'):
            self._push_off(x['occ'], L, i, now, '撤岗')
        x['occ'] = None
        x['open'] = False
        self.h.log('post_close', actor, None, L['id'], x['name'], {})

    def act_post(self, lid, i, open_, now, actor):
        L = self.line(lid)
        x = L['posts'][i]
        x['manual'] = True
        x['opened'] = True
        if open_:
            if not L.get('active'):
                raise ActionError('%s 线未启用，请先启用该线' % lid)
            x['open'] = True
            self.h.log('post_open', actor, None, lid, x['name'], {})
        else:
            self._close_post(L, i, now, actor)
        self._reset_line_plans(lid)

    def act_line(self, lid, active, now, actor):
        L = self.line(lid)
        if active:
            L['active'] = True
            for x in L['posts']:
                if not x.get('openAt') or x['opened']:
                    if not x['manual'] or x['open']:
                        x['open'] = True
            self.h.log('line_on', actor, None, lid, None, {})
        else:
            for i, x in enumerate(L['posts']):
                if x.get('open'):
                    self._close_post(L, i, now, actor)
                x['manual'] = False
            L['active'] = False
            self.h.log('line_off', actor, None, lid, None, {})
        self._reset_line_plans(lid)

    def _reset_line_plans(self, lid):
        for q in self.P.values():
            if q.get('assign') and q['assign']['line'] == lid and q['state'] != 'heading' \
                    and q['assign'].get('mode') == 'push':
                q['assign'] = None

    def act_away(self, pid, reason, now, actor):
        p = self.person(pid)
        if p['state'] == 'onpost':
            L, i = self.find_post(pid)
            self._leave_line(p, L, i, now)
            L['posts'][i]['occ'] = None
        elif p['state'] not in POOL_STATES + ('heading',):
            raise ActionError('%s 当前状态不能标记暂离' % p['name'])
        p['assign'] = None
        p['state'] = 'away'
        p['away'] = {'reason': reason or '专项', 'until': None}
        self.h.log('away', actor, pid, None, None, {'reason': reason})

    def act_back(self, pid, now, actor):
        p = self.person(pid)
        if p['state'] != 'away':
            raise ActionError('%s 不在暂离中' % p['name'])
        p['away'] = None
        p['state'] = 'ready'
        p['readyAt'] = now
        p['readySince'] = now
        p['assign'] = None
        self.h.log('back', actor, pid, None, None, {})

    def act_reassign(self, pid, lid, now, actor):
        p = self.person(pid)
        if p['state'] not in REST_STATES:
            raise ActionError('%s 当前不能改派' % p['name'])
        L = self.line(lid)
        if not L.get('active'):
            raise ActionError('%s 线未启用' % lid)
        p['assign'] = {'line': lid, 'mode': 'push', 'departAt': self._ready_time(p, now), 'manual': True}
        self.h.log('reassign', actor, pid, lid, None, {})

    def act_leave(self, pid, reason, now, actor):
        p = self.person(pid)
        if p['state'] == 'onpost':
            L, i = self.find_post(pid)
            self._leave_line(p, L, i, now)
            L['posts'][i]['occ'] = None
        p['assign'] = None
        p['state'] = 'done'
        p['leaveReason'] = reason
        self.h.log('leave', actor, pid, None, None, {'reason': reason})

    def act_flag(self, pid, flag, on, actor):
        p = self.person(pid)
        if on and flag not in p['flags']:
            p['flags'].append(flag)
        if not on and flag in p['flags']:
            p['flags'].remove(flag)
        self.h.log('flag', actor, pid, None, None, {'flag': flag, 'on': on})

    # 更正（带原因，原记录保留在日志里）
    def act_fix_undo_depart(self, pid, reason, now, actor):
        p = self.person(pid)
        if p['state'] != 'heading':
            raise ActionError('%s 不在前往途中' % p['name'])
        p['state'] = 'ready'
        p['readyAt'] = now
        p['readySince'] = now
        p['arriveAt'] = None
        self.h.log('fix_undo_depart', actor, pid, None, None, {'reason': reason})

    def act_fix_remove(self, pid, reason, now, actor):
        p = self.person(pid)
        L, i = self.find_post(pid)
        if L is None:
            raise ActionError('%s 不在岗位上' % p['name'])
        self._push_off(pid, L, i, now, '更正移出')
        L['posts'][i]['occ'] = None
        self.h.log('fix_remove', actor, pid, L['id'], L['posts'][i]['name'], {'reason': reason})

    def act_fix_place(self, pid, lid, i, reason, now, actor):
        p = self.person(pid)
        L = self.line(lid)
        x = L['posts'][i]
        if not x.get('open'):
            raise ActionError('%s 未开放' % x['name'])
        if x.get('occ'):
            raise ActionError('%s 已有人，请先移出' % x['name'])
        if p['state'] == 'onpost':
            OL, oi = self.find_post(pid)
            self._leave_line(p, OL, oi, now)
            OL['posts'][oi]['occ'] = None
        elif p['state'] in ('done', 'excluded', 'notyet', 'op'):
            raise ActionError('%s 当前状态不能放入岗位' % p['name'])
        x['occ'] = pid
        x['since'] = now
        p['assign'] = None
        self._enter(p, L, now)
        self.h.log('fix_place', actor, pid, lid, x['name'], {'reason': reason})

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
        cm = _m(close)
        self.d['closeAt'] = close
        if self.d.get('closed') and now < cm + self.S.get('closeDelay', 0):
            # 推迟闭园：恢复闭园前开着的岗位，闭园后工作的人回到池子
            self.d['closed'] = False
            for L in self.d['lines']:
                for x in L['posts']:
                    if x.pop('openBeforeClose', False) and L.get('active'):
                        x['open'] = True
            for p in self.P.values():
                if p['state'] == 'away' and (p.get('away') or {}).get('reason') == CLOSING_WORK:
                    p['away'] = None
                    p['state'] = 'ready'
                    p['readyAt'] = now
                    p['readySince'] = now
                elif p['state'] == 'walkback' and p.get('after') == 'closing':
                    p['after'] = None
        self.h.log('set_close', actor, None, None, None, {'close': close})

    # -------- 当天收尾
    def should_end(self, now):
        """所有人都下班（或不轮岗）后，或到 24:00，当天收尾。"""
        if self.d['status'] != 'live':
            return False
        if now >= 24 * 60:
            return True
        return all(p['state'] in ('done', 'excluded') for p in self.P.values())

    def end_day(self, now):
        for p in self.P.values():
            if p['state'] in ('done', 'excluded'):
                continue
            if p['state'] == 'onpost':
                L, i = self.find_post(p['pid'])
                if L is not None:
                    self._leave_line(p, L, i, now)
                    L['posts'][i]['occ'] = None
            p['assign'] = None
            p['state'] = 'done'
        self.d['status'] = 'ended'
        self.h.log('day_end', 'system', None, None, None, {})

    # -------- 提醒
    def alert(self, msg, now):
        n = self.d.setdefault('notices', [])
        n.append({'t': now, 'msg': msg})
        del n[:-30]

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
                word = '出圈' if kind == 'out' else '下班'
                if now >= t - S['offLead'] - 10 and p['pid'] not in taken:
                    out.append({'level': 'notify', 'pid': p['pid'], 'msg': '%s 需在 %s 前推%s，暂无人可替' % (p['name'], fmt(t - S['offLead']), word)})
            if p.get('role') in ('rotation', 'op') and st in ('onpost', 'heading') and not p.get('ate') \
                    and self.meal_eligible(p) and not self.d.get('closed') \
                    and self.meal_deadline(p) - now <= S['mealMin'] + 30 + 30:
                out.append({'level': 'notify', 'pid': p['pid'], 'msg': '%s 还没吃饭，%s 前需推下来吃饭' % (p['name'], fmt(self.meal_deadline(p) - S['mealMin'] - 30))})
        if now >= _m(S['lineOpenAt']) + 2 and not self.d.get('closed'):
            incoming = {q['assign']['line'] for q in self.P.values() if q.get('assign')}
            for L in self.d['lines']:
                if L.get('active') and L['id'] not in incoming:
                    for x in L['posts']:
                        if x.get('open') and not x.get('occ'):
                            out.append({'level': 'warn', 'msg': '%s %s 空岗，暂无人前往' % (L['id'], x['name'])})
        return out
