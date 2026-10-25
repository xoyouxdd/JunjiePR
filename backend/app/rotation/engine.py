"""轮岗引擎：当天状态、初始草稿、推岗与分配。

由原型 rotation-board/engine.py 迁移，并按 2026-10 确认的规则修改：
- 没有“上线满多少分钟才推”的保护；派人先推出口岗站得最久的人，站岗时长（整分钟）相同时
  再看当天没进过这条线、本周在这条线累计少；
- 推下班、推出圈（固定暂离）最优先：替换者在下班/出圈前 offLead 分钟到岗，从入口岗推进，
  链条后移到要走的人为止把他推出来；被推出的人直接转为已下班/出圈中，不需要到大屏确认；
- 推 7 点：07:15 班的人直接在 7 点岗位对换 07:00 班的人，被替下的人照常休息后轮岗；
- 到出圈时间仍没人替：继续在岗并保持准备出圈，一直找人来推，超过 outWarnAfter 分钟提醒主管；
- 吃饭：休息中、待出发和吃饭/饭后休息剩余15分钟及以内的人数达到开放线数时，可自选休息或吃饭；默认休息，保留强制饭点；
  离下班或闭园不到 mealWarnLeft 分钟仍没吃饭的，提醒主管；
- 到岗时距下班/出圈不超过 noBoardBefore 分钟就不再上岗；
- 出发只能在计划出发时间前 departEarly 分钟以内点。

时间一律用“当天零点起的分钟数”（浮点）。所有方法都在外部锁内调用。
"""
from __future__ import annotations

import math
from copy import deepcopy

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
    'mealWarnLeft': 180,          # 离下班或闭园不到这么多分钟仍没吃饭，提醒主管
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

    def walk(self, lid):
        self.line(lid)
        # 点击去轮岗即进线，不再增加路程等待。
        return 0.0

    def open_posts(self, L):
        return [i for i, x in enumerate(L['posts']) if x.get('open')]

    def rotation_posts(self, L):
        return [i for i in self.open_posts(L) if not L['posts'][i].get('hold')]

    def held(self, pid):
        L, i = self.find_post(pid)
        return L is not None and bool(L['posts'][i].get('hold'))

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
        self._reset_line_plans(lid, now)
        self._clear_stale_assigns(now)
        info = x['hold'] if on else previous
        self.h.log('post_hold' if on else 'post_resume', actor, pid, lid, x['name'],
                   {'reason': reason or info['reason'], 'since': info['since'],
                    'duration': None if on else max(0, now-info['since'])})
        if not on:
            if self.d.get('closed'):
                self._push_off(pid, L, i, now, '闭园')
                x.update(occ=None, open=False)
            else:
                p = self.person(pid)
                if now >= p['end']:
                    self._leave_line(p, L, i, now)
                    x['occ'] = None
                    p.update(state='done', assign=None)
                    self.h.log('shift_end_onpost', 'system', pid, lid, x['name'], {})
                else:
                    absence = self.next_absence(p, now)
                    if absence and absence['start'] <= now < absence['end']:
                        self._leave_line(p, L, i, now)
                        x['occ'] = None
                        self._go_out(p, absence, now, actor)

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
        for kind in ('crew', 'seven'):
            for key, pid in d['plan'][kind].items():
                if self.P[pid]['start'] > now:
                    _, _, x = self.plan_post(key)
                    if x['open']:
                        x['occ'] = pid
        self.tick(now)

    # -------- 每秒推进
    def tick(self, now):
        d = self.d
        if d['status'] != 'live':
            return False
        changed = self._freeze_daily_opening(now)
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
                if x.get('closeAt') and x['open'] and not x.get('hold') and now >= _m(x['closeAt']) and not x.get('closedAuto'):
                    x['closedAuto'] = True
                    self._close_post(L, i, now, 'system', x.get('closeMode', 'direct'))
                    changed = True
        cm = self.close_min()
        if cm is not None and not d.get('closed') and now >= cm + S.get('closeDelay', 0):
            self._park_close(now)
            changed = True
        off_lead = S['offLead']
        for p in list(self.P.values()):
            st = p['state']
            if p.get('entryHold') and st not in ('notyet', 'rest', 'ready'):
                p.pop('entryHold', None)
                changed = True
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
            elif st == 'meal' and now < p['readyAt'] and p['readyAt'] - now <= S['mealReserveLeft']:
                p['state'] = 'rest'
                p['breakKind'] = 'meal_rest'
                changed = True
            elif st in ('rest', 'meal') and now >= p['readyAt']:
                p['state'] = 'ready'
                p['breakKind'] = 'rest'
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
            # 到出圈时间仍没人替：继续在岗、保持准备出圈，一直找人来推；暂离结束仍没推出则视为错过
            if p['state'] == 'onpost':
                for a in p.get('absences', []):
                    if not a.get('done') and now >= a['end']:
                        a['done'] = True
                        a['missed'] = True
                        self.h.log('absence_missed', 'system', p['pid'], p.get('line'), None, {'reason': a['label']})
                        changed = True
            # 下班：池子里、暂离中、下线途中的人在下班前 offLead 分钟直接下班
            if p['state'] in ('walkback', 'rest', 'meal', 'ready', 'pending', 'away') and now >= p['end'] - off_lead:
                was = p['state']
                p['assign'] = None
                p['state'] = 'done'
                p.pop('entryHold', None)
                self.h.log('shift_end_unconfirmed' if was == 'walkback' else 'shift_end', 'system', p['pid'], None, None, {})
                changed = True
            if p['state'] == 'onpost' and now >= p['end'] and not self.held(p['pid']):
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

    def _freeze_daily_opening(self, now):
        if self.d.get('dailyOpening') or not self.P:
            return False
        first = min(p['start'] for p in self.P.values())
        # Never fabricate an opening snapshot from an already running legacy day.
        if now < first or any(p['state'] not in ('notyet', 'excluded') for p in self.P.values()):
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

    def _park_close(self, now):
        """闭园：撤下所有岗位，线上的人回板面后转为闭园后工作，池子里的人直接转入。"""
        self.d['closed'] = True
        for L in self.d['lines']:
            for i, x in enumerate(L['posts']):
                if x.get('hold'):
                    continue
                x['openBeforeClose'] = bool(x.get('open'))
                if x.get('occ'):
                    self._push_off(x['occ'], L, i, now, '闭园')
                x['occ'] = None
                x['open'] = False
        for p in self.P.values():
            p['assign'] = None
            p.pop('entryHold', None)
            if p['state'] in ('pending', 'rest', 'meal', 'ready') or (p['state'] == 'notyet' and p['role'] in ('rotation', 'op')):
                p['state'] = 'away'
                p['away'] = {'reason': CLOSING_WORK, 'until': None}
        self.h.log('park_close', 'system', None, None, None, {})

    def opening_rest_candidate(self, p):
        """Unseated 07:15 crew rest at shift start; push7 and seated crew keep their flow."""
        plan = self.d['plan']
        return (p['start'] == 435 and p['role'] == 'rotation'
                and p['pid'] not in plan['push7']
                and all(p['pid'] not in plan[kind].values() for kind in ('crew', 'seven'))
                and not any(a['start'] <= p['start'] < a['end'] for a in p.get('absences', [])))

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
            if L['active'] and x.get('open') and x.get('occ') in (None, p['pid']):
                x['open'] = True
                x['occ'] = p['pid']
                x['since'] = t
                p.update(state='onpost', line=lid, lineStart=t, lastLine=lid)
                p['visited'].append(lid)
                if p['pid'] in seat7:
                    p['flags'].append('7点岗')
                self.h.log('crew_start', 'system', p['pid'], lid, x['name'], {})
                return
        if self.opening_rest_candidate(p):
            p.update(state='rest', breakKind='rest', breakStart=t, arriveAnchor=t,
                     readyAt=t + self.S['breakMin'], readySince=None, assign=None)
            if now >= p['readyAt']:
                p.update(state='ready', readySince=p['readyAt'])
            self.h.log('initial_rest', 'system', p['pid'], None, None,
                       {'start': t, 'until': p['readyAt']})
            return
        p['state'] = 'ready'
        p['readyAt'] = now
        p['readySince'] = now
        if p['pid'] in self.d['plan']['push7']:
            p['flags'].append('推7点')

    # -------- 下线
    def _leave_line(self, p, L, i, now):
        if L['posts'][i].get('hold'):
            raise ActionError('该人员正在定岗，请先取消定岗')
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
        if L is None or not L.get('active') or not self.rotation_posts(L):
            p['state'] = 'ready'
            p['readyAt'] = now
            p['readySince'] = now
            p['assign'] = None
            self.alert('%s 到达 %s 时暂无可轮换岗位，已重新分配' % (p['name'], lid), now)
            return
        depart = p.get('departedAt', now)
        tgt = a.get('target')
        TL, ti = self.find_post(tgt) if tgt else (None, None)
        if TL is L and L['posts'][ti].get('hold'):
            p.update(state='ready', assign=None, readyAt=now, readySince=now)
            self.alert('%s 的目标岗位已定岗，请按新安排上岗' % p['name'], now)
            return
        if a.get('mode') == 'push7' and TL is L:
            # 推 7 点：在 7 点岗位直接对换
            x = L['posts'][ti]
            self._push_off(tgt, L, ti, now, '推7点下来')
            self.P[tgt]['flags'] = [flag for flag in self.P[tgt]['flags'] if flag not in ('7点岗', '推7点下来')]
            self.P[tgt]['sevenReplaced'] = True
            p['flags'] = [flag for flag in p['flags'] if flag != '推7点']
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
            self.h.log('chain_out', 'system', p['pid'], L['id'], L['posts'][self.rotation_posts(L)[0]]['name'], {'target': tgt, 'why': a.get('why')})
            return
        if tgt:
            self.h.log('target_gone', 'system', p['pid'], L['id'], None, {'target': tgt})
        # 普通推岗：从入口岗进，依次后移，遇到空岗停止，否则出口岗的人下线
        carry = p['pid']
        out = None
        for i in self.rotation_posts(L):
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
        self.h.log('push', 'system', p['pid'], L['id'], L['posts'][self.rotation_posts(L)[0]]['name'], {'out': carry})

    def _chain_out(self, L, new_pid, target, ti, now):
        """入口岗到 target 所在岗位之间整体后移一位，target 腾出。中间有空岗时前面的人往后补齐。"""
        seq = [i for i in self.rotation_posts(L) if i <= ti]
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

    def assignment_locked(self, p, now):
        def own(q):
            return bool(q.get('assign') and q['state'] in ('rest', 'meal', 'ready')
                        and (q['state'] == 'ready' or (q.get('readyAt') if q.get('readyAt') is not None else now) - now <= 3 + 1e-9))
        if own(p):
            return True
        a = p.get('assign')
        if not a or not self.d.get('cooperativeEntry'):
            return False
        # Keep the shared route intact when one same-time teammate reaches the window.
        return any(own(q) and not q.get('entryHold') and q['assign']['line'] == a['line']
                   and abs(q['assign']['departAt'] - a['departAt']) < .01 for q in self.P.values())

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
                if not post.get('openRequest') or post.get('occ') or post.get('hold'):
                    return '原加岗位置已变化'
            elif not self.rotation_posts(L) and not any(x.get('openAt') and not x.get('opened') for x in L['posts']):
                return '原线路暂无可轮换岗位'
        except ActionError:
            return '原线路或岗位已变化'
        return None

    def _clear_stale_assigns(self, now=None):
        now = float(self.d.get('lastTick', 0)) if now is None else now
        changed = False
        for q in self.P.values():
            a = q.get('assign')
            if not a or q['state'] == 'heading':
                continue
            issue = self.assignment_issue(q)
            if issue and self.assignment_locked(q, now):
                message = issue + '，安排已固定，请联系主管核对'
                if a.get('blocked') != message:
                    a['blocked'] = message
                    self.h.log('assign_blocked', 'system', q['pid'], a['line'], None, {'reason': message})
                    changed = True
            elif issue:
                q['assign'] = None
                self.h.log('assign_cleared', 'system', q['pid'], a.get('line'), None, {'mode': a.get('mode'), 'target': a.get('target')})
                changed = True
            elif a.pop('blocked', None) is not None:
                changed = True
        return changed

    def _refresh_future_assignments(self, now):
        board = [[L['id'], L.get('active'), [[x['name'], x.get('open'), x.get('occ'), bool(x.get('hold')),
                  x.get('openAt'), x.get('closeAt')] for x in L['posts']]] for L in self.d['lines']]
        if self.d.get('assignmentBoard') == board:
            return False
        self.d['assignmentBoard'] = board
        changed = False
        for p in self.P.values():
            a = p.get('assign')
            if (a and a.get('mode') == 'push' and not a.get('manual')
                    and p['state'] in ('rest', 'meal') and not self.assignment_locked(p, now)):
                p['assign'] = None
                changed = True
        return changed

    def _replace_needs(self, now):
        needs = []
        taken = self.targeted()
        cm = self.close_min()
        S = self.S
        for p in self.P.values():
            if p['state'] != 'onpost' or p['pid'] in taken or self.held(p['pid']):
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
        changed = self._clear_stale_assigns(now)
        changed = self._refresh_future_assignments(now) or changed
        S = self.S
        plan = self.d['plan']
        # 推 7 点：07:15 班的人在 7 点岗位对换 07:00 班的人
        for idx, a, b in self._push7_pairs():
            pa, pb = self.P.get(a), self.P.get(b)
            if not pb or pb['state'] != 'onpost' or self.held(pb['pid']) or '7点岗' not in pb['flags'] or '推7点下来' in pb['flags']:
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
            if pa and pa['state'] in ('rest', 'ready') and pa.get('breakKind') != 'meal_rest' and not pa.get('push7done') \
                    and not (pa.get('assign') and pa['assign'].get('mode') in TARGET_MODES) and not self.assignment_locked(pa, now):
                pa['assign'] = {'line': pb['line'], 'mode': 'push7', 'target': b,
                                'departAt': max(now, self._ready_time(pa, now)), 'why': '推7点'}
                pa['push7done'] = True
                changed = True
        # 推下班 / 推出圈
        for need_at, x, kind in self._replace_needs(now):
            walk = self.walk(x['line'])
            cands = []
            for p in self.P.values():
                if p.get('entryHold') or p['state'] not in REST_STATES or p['role'] not in ('rotation', 'op'):
                    continue
                if self.assignment_locked(p, now):
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
        if self._assign_openings(now):
            changed = True
        todo = [p for p in self.P.values() if not p.get('entryHold') and p['state'] in REST_STATES and not p.get('assign')
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

    def _assign_openings(self, now):
        """人工加岗优先预留下一位可轮岗人员，从入口推进补空岗。"""
        changed = False
        requests = [(L, i, x) for L in self.d['lines'] if L.get('active')
                    for i, x in enumerate(L['posts']) if x.get('openRequest')]
        requests.sort(key=lambda req: _m(req[2]['openAt']))
        for L, i, x in requests:
            if x.get('occ'):
                x['openRequest'] = False
                changed = True
                continue
            at = _m(x['openAt'])
            if at - now > self.S['futureWait']:
                continue
            if any(p.get('assign') and p['assign'].get('mode') == 'opening'
                   and p['assign']['line'] == L['id'] and p['assign']['post'] == i for p in self.P.values()):
                continue
            candidates = []
            for p in self.P.values():
                a = p.get('assign') or {}
                if p.get('entryHold') or p['state'] not in ('rest', 'meal', 'ready') or p['role'] not in ('rotation', 'op'):
                    continue
                if self.assignment_locked(p, now) or a.get('mode') in TARGET_MODES \
                        or a.get('mode') == 'opening' or a.get('manual'):
                    continue
                ready = self._ready_time(p, now)
                depart = max(at, ready, now)
                if self.can_board(p, depart, now):
                    candidates.append((depart, ready, p['start'], p['pid'], p))
            if not candidates:
                continue
            depart, _, _, _, p = min(candidates, key=lambda c: c[:4])
            p['assign'] = {'line': L['id'], 'mode': 'opening', 'post': i, 'departAt': depart, 'notBefore': at}
            self.h.log('opening_assign', 'system', p['pid'], L['id'], x['name'], {'departAt': depart})
            changed = True
        return changed

    def _push7_substitute(self, now):
        open_at = _m(self.S['lineOpenAt'])
        plan = self.d['plan']
        cands = [p for p in self.P.values() if not p.get('entryHold') and p['role'] == 'rotation' and p['start'] == open_at
                 and p['state'] in ('rest', 'ready') and p.get('breakKind') != 'meal_rest'
                 and p['pid'] not in plan['push7'] and not p['absences']
                 and not (p.get('assign') and p['assign'].get('mode') in TARGET_MODES) and not self.assignment_locked(p, now)]
        if not cands:
            return None
        return min(cands, key=lambda p: (self._ready_time(p, now), -(p['end'] - p['start']), p['name']))

    def _seven_order(self, key):
        lid, i = key.split('#')
        return self.line(lid)['posts'][int(i)].get('seven') or 9

    def _chain(self, L):
        """从出口岗往入口岗的在岗人列表。"""
        return [L['posts'][i]['occ'] for i in reversed(self.rotation_posts(L)) if L['posts'][i].get('occ')]

    def _best_line(self, p, now, wk):
        """选线：先补空岗或马上定时开的岗位；否则推出口岗站得最久的人。

        站岗时长按整分钟比，相同再看：不是刚下来的线、当天没进过、本周在该线累计少，
        最后看已排去这条线的人少。
        """
        S = self.S
        ready = self._ready_time(p, now)
        cm = self.close_min()
        best = None
        for order, L in enumerate(self.d['lines']):
            if not L.get('active'):
                continue
            idx = self.rotation_posts(L)
            future = sorted(_m(x['openAt']) for x in L['posts']
                            if x.get('openAt') and not x['opened'] and not x['manual'])
            if not idx and not future:
                continue
            walk = self.walk(L['id'])
            arrive0 = ready + walk
            res = sorted([q for q in self.P.values() if q.get('assign') and q['assign']['line'] == L['id']
                          and q['assign'].get('mode') in ('push', 'opening') and not q['assign'].get('blocked')
                          and q['assign']['departAt'] <= arrive0 + .01],
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
            # 最后按已排去这条线的人数分散，避免同时上班的一批人全挤进同一条线
            key = (cls, rank, p.get('lastLine') == L['id'], L['id'] in p.get('visited', []),
                   wk.get(p['pid'], {}).get(L['id'], 0), k, order)
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

    def act_arrive(self, pid, now, actor, meal_choice=None):
        p = self.person(pid)
        early_op = p.get('role') == 'op' and p['state'] in ('op', 'notyet')
        if p['state'] not in ('walkback', 'pending') and not early_op:
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
        menu = self.meal_options(p, now)
        if meal_choice not in (None, 'rest', 'meal'):
            raise ActionError('请选择休息或吃饭')
        if meal_choice == 'meal' and not menu['canChoose'] and not menu['forced']:
            raise ActionError('当前不满足吃饭条件或轮岗储备不足，请重新查看')
        kind = 'meal' if menu['forced'] or menu['canChoose'] and meal_choice == 'meal' else 'rest'
        if not first:
            kind = p.get('breakKind') or kind
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
        return 'meal' if self.meal_options(p, now)['forced'] else 'rest'

    def meal_options(self, p, now):
        S = self.S
        result = {'canChoose': False, 'forced': False, 'supply': 0, 'lines': sum(1 for L in self.d['lines'] if L.get('active') and any(x.get('open') for x in L['posts']))}
        if not self.meal_eligible(p) or p.get('ate'):
            return result
        if now < max(_m(S['mealEarliest']), p['start'] + S['mealAfterStart']):
            return result
        left = self.meal_deadline(p) - now
        # 吃完后剩余时间太少也不安排
        if left < S['mealMin'] + 30:
            return result
        result['forced'] = now >= p['start'] + S['mealForceAfterStart']
        result['supply'] = sum(1 for q in self.P.values() if q['pid'] != p['pid'] and q['role'] in ('rotation','op') and (q['state'] in ('rest','ready') and (q.get('breakKind')!='meal_rest' or q['readyAt']-now<=15) or q['state']=='meal' and q['readyAt']-now<=15))
        result['canChoose'] = not result['forced'] and result['supply'] >= result['lines']
        return result

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
        if p.get('entryHold'):
            raise ActionError('请先确认本人已到场，重新领取安排')
        if p['state'] != 'ready':
            raise ActionError('%s 还不能出发' % p['name'])
        if now < (p.get('readyAt') or now) - 0.01:
            raise ActionError('%s 休息还没结束' % p['name'])
        if not p.get('assign'):
            raise ActionError('%s 还没有分配去向' % p['name'])
        depart_at = p['assign']['departAt']
        if now < p['assign'].get('notBefore', 0) - 0.01:
            raise ActionError('%s 还没到加岗时间（%s）' % (p['name'], fmt(p['assign']['notBefore'])))
        if now < depart_at - self.S['departEarly'] - 0.01:
            raise ActionError('%s 还没到出发时间（%s）' % (p['name'], fmt(depart_at)))
        issue = self.assignment_issue(p)
        if issue:
            raise ActionError(issue + '，请联系主管核对原安排')
        L = self.line(p['assign']['line'])
        p['departedAt'] = now
        p['arriveAt'] = None
        self.h.log('depart', actor, pid, L['id'], None, dict(p['assign']))
        self._arrive_line(p, now)

    def act_plan_place(self, pid, lid, i, now, actor):
        """预排拖拽：仅未开始班次，可交换岗位，不产生任何在岗计时。"""
        p = self.person(pid)
        if p['state'] != 'notyet' or now >= p['start'] or p['role'] != 'rotation':
            raise ActionError('只能拖拽尚未开始班次的轮岗人员')
        L, i, post = self.post(lid, i)
        if not L['active'] or not post['open']:
            raise ActionError('目标岗位未开放')
        target = L['posts'][i].get('occ')
        if target and self.P[target]['state'] != 'notyet':
            raise ActionError('不能交换已开始班次的人员')
        oldL, oldi = self.find_post(pid)
        if target and oldL is None:
            raise ActionError('目标已有预排人员，请先选择空岗')
        if oldL:
            oldL['posts'][oldi]['occ'] = target
        L['posts'][i]['occ'] = pid
        plan = self.d['plan']
        for kind in ('crew', 'seven'):
            plan[kind] = {key: value for key, value in plan[kind].items() if value not in (pid, target)}
        plan['seven' if p['start'] == 420 else 'crew'][f'{lid}#{i}'] = pid
        if target:
            plan['seven' if self.P[target]['start'] == 420 else 'crew'][f"{oldL['id']}#{oldi}"] = target
        self.h.log('plan_place', actor, pid, lid, L['posts'][i]['name'], {'swapped': target})

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

    def _refresh_prestart_plan(self, pids):
        kinds = {pid: kind for kind in ('crew', 'seven') for pid in self.d['plan'][kind].values()}
        for kind in ('crew', 'seven'):
            self.d['plan'][kind] = {key: pid for key, pid in self.d['plan'][kind].items() if pid not in pids}
        for pid in pids:
            line, index = self.find_post(pid)
            if line is not None and self.P[pid]['state'] == 'notyet':
                self.d['plan'][kinds.get(pid, 'crew')][f"{line['id']}#{index}"] = pid

    def _close_post(self, L, i, now, actor, mode='direct'):
        x = L['posts'][i]
        if x.get('hold'):
            raise ActionError('该岗位正在定岗，请先取消定岗')
        if mode not in ('direct', 'push'):
            raise ActionError('撤岗方式不正确')
        prestart = bool(x.get('occ') and self.P[x['occ']]['state'] == 'notyet')
        affected = {x['occ']} if x.get('occ') else set()
        if mode == 'push' and x.get('occ'):
            # Validate the whole chain before changing any post (also used by previews).
            for target in L['posts'][i+1:]:
                if not target.get('open') or target.get('hold'):
                    continue
                if target.get('occ') and (self.P[target['occ']]['state'] == 'notyet') != prestart:
                    raise ActionError('预排人员与已上岗人员不能混合顺推')
                if not target.get('occ'):
                    break
            carry = x['occ']
            last_index = i
            for j in range(i+1, len(L['posts'])):
                target = L['posts'][j]
                if not target.get('open') or target.get('hold'):
                    continue
                last_index = j
                if target.get('occ'):
                    affected.add(target['occ'])
                target['occ'], carry = carry, target.get('occ')
                target['since'] = None if prestart else now
                if not carry:
                    break
            if carry and not prestart:
                self._push_off(carry, L, last_index, now, '撤岗顺推')
            x['occ'] = None
        if x.get('occ'):
            if self.P[x['occ']]['state'] != 'notyet':
                self._push_off(x['occ'], L, i, now, '撤岗')
            else:
                for kind in ('crew', 'seven'):
                    self.d['plan'][kind].pop(f"{L['id']}#{i}", None)
        x['occ'] = None
        x['since'] = None
        x['open'] = False
        if prestart:
            self._refresh_prestart_plan(affected)
        self.h.log('post_close', actor, None, L['id'], x['name'], {'mode':mode})

    def act_post(self, lid, i, open_, now, actor, open_at=None, close_at=None, close_mode='direct', cancel_close=False):
        L, i, x = self.post(lid, i)
        if cancel_close:
            if not x.get('open') or not x.get('closeAt') or hm(x['closeAt'])<=now:raise ActionError('没有待撤岗计划')
            x.update(closeAt=None,closeMode='direct',closedAuto=False)
            self.h.log('post_cancel_close',actor,None,lid,x['name'],{})
            return
        if open_:
            if not L.get('active'):
                raise ActionError('%s 线未启用，请先启用该线' % lid)
            if x.get('occ'):
                raise ActionError('岗位已有人，不能修改加岗时间')
            at = math.ceil(now + 3) if open_at is None else open_at
            if at < now - 0.01 or at >= 1440:
                raise ActionError('加岗时间不得早于当前时间，且须在当天24:00前')
            if self.d.get('closed') or (self.close_min() is not None and at >= self.close_min()):
                raise ActionError('加岗时间必须在闭园前')
            if x.get('closeAt') and hm(x['closeAt'])<=now:x.update(closeAt=None,closedAuto=False)
            x.update(manual=False, opened=at <= now, open=at <= now, openAt=fmt(at), openRequest=True)
            self.h.log('post_schedule', actor, None, lid, x['name'], {'openAt': fmt(at)})
        else:
            if close_mode not in ('direct','push'):
                raise ActionError('撤岗方式不正确')
            if close_at is not None and close_at > now:
                if not x.get('open') or close_at >= 1440 or close_at >= (self.close_min() or 1440):
                    raise ActionError('撤岗时间应在已开岗后、闭园前')
                x.update(closeAt=fmt(close_at), closeMode=close_mode, closedAuto=False)
                self.h.log('post_schedule_close',actor,None,lid,x['name'],{'closeAt':fmt(close_at),'mode':close_mode})
                return
            if close_at is not None and close_at < now:
                raise ActionError('撤岗时间不得早于当前时间')
            x.update(manual=True, opened=True, openRequest=False)
            self._close_post(L, i, now, actor, close_mode)
        self._reset_line_plans(lid, now)

    def act_move(self, pid, lid, i, now, actor, force=False, reason=''):
        p=self.person(pid)
        if not isinstance(force, bool):
            raise ActionError('强制安排参数必须为布尔值')
        if p['state']=='notyet':
            if force:
                raise ActionError('未到班次人员只能调整预排，不能强制提前上岗')
            return self.act_plan_place(pid,lid,i,now,actor)
        if p['state'] in ('rest','meal','ready') and self.assignment_locked(p,now):
            failed = bool(self.assignment_issue(p))
            if force and str(reason).strip() and not failed and self.d.get('cooperativeEntry'):
                from app.rotation.cooperation import plan
                group = plan(self,p,now)
                failed = bool(group and group['blocked'])
            if not (force and str(reason).strip() and failed):
                raise ActionError('剩余餐休3分钟及以内或已待出发，原安排已固定，不能调配')
        target, i, slot=self.post(lid,i)
        if slot.get('hold') or self.held(pid):
            raise ActionError('定岗人员不能调换岗位，请先取消定岗')
        if not target.get('active') or not slot.get('open'):
            raise ActionError('只能移动到启用线路的已开岗位')
        if p['state']!='onpost':
            if force:
                if p['state'] not in ('rest', 'meal', 'ready') or not str(reason).strip() or len(str(reason)) > 100:
                    raise ActionError('强制安排仅限休息/吃饭/待出发人员，并须填写100字内原因')
                other=slot.get('occ')
                if other and self.P[other]['state']!='onpost':
                    raise ActionError('目标是未到班次的预排人员，请先调整预排')
                previous={'state':p['state'],'kind':p.get('breakKind'),'start':p.get('breakStart'),
                          'until':p.get('readyAt'),'remaining':max(0,(p.get('readyAt') or now)-now)}
                if other:
                    self._push_off(other,target,i,now,'强制安排替下')
                    self.P[other].update(readyAt=None,readySince=None,breakStart=None,breakKind=None)
                    slot['occ']=None
                p['interruptedBreak']={**previous,'at':now,'reason':str(reason).strip()}
                p.update(assign=None,breakKind=None,breakStart=None,readyAt=None,readySince=None,arriveAnchor=None)
                p.pop('entryHold',None)
                slot.update(occ=pid,since=now)
                self._enter(p,target,now)
                self._reset_line_plans(lid, now)
                self.h.log('force_place',actor,pid,lid,slot['name'],
                           {'reason':str(reason).strip(),'previous':previous,'replaced':other})
                return
            if p['state']=='rest' and now < (p.get('readyAt') or now):
                raise ActionError('休息未结束，请填写原因并确认强制安排')
            if slot.get('occ'):
                raise ActionError('休息人员不能直接替换在岗人员，请安排轮岗')
            return self.act_fix_place(pid,lid,i,'现场拖动调整',now,actor)
        source, old=self.find_post(pid)
        if source['id']==lid and old==i:
            return
        other=slot.get('occ')
        if other and self.P[other]['state']!='onpost':
            raise ActionError('运行中不能与未到班次人员交换')
        source['posts'][old]['occ']=other;slot['occ']=pid
        for who,line in [(pid,target),(other,source)]:
            if who and self.P[who].get('line')!=line['id']:
                self._leave_line(self.P[who],source if who==pid else target,old if who==pid else i,now)
                self._enter(self.P[who],line,now)
        self._reset_line_plans(source['id'], now);self._reset_line_plans(lid, now)
        self.h.log('move',actor,pid,lid,slot['name'],{'swapped':other,'from':source['id'],'fromPost':source['posts'][old]['name']})

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
        if any(x.get('hold') for x in L['posts'][min(i,target_i):max(i,target_i)+1]):
            raise ActionError('涉及定岗岗位，请先取消定岗')
        if swap_mode=='posts':
            left,right=L['posts'][i],L['posts'][target_i]
            if left.get('open')!=right.get('open') and (left.get('occ') or right.get('occ')):
                raise ActionError('有人员时不能只交换开放状态不同的岗位')
            fixed={'occ','since','hold'}
            configs=[{k:v for k,v in x.items() if k not in fixed} for x in (left,right)]
            for post,config in ((left,configs[1]),(right,configs[0])):
                keep={k:v for k,v in post.items() if k in fixed};post.clear();post.update(config);post.update(keep)
            self._reset_line_plans(lid, now)
            self.h.log('post_order',actor,None,lid,left['name'],{'from':i,'to':target_i,'mode':swap_mode})
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
        self._reset_line_plans(lid, now)
        self.h.log('post_order',actor,None,lid,post['name'],{'from':i,'to':target_i,'mode':swap_mode or 'order'})

    def _swap_cross_line_posts(self, lid, i, target_lid, target_i, now, actor, mode):
        L, i, left = self.post(lid, i)
        R, target_i, right = self.post(target_lid, target_i)
        if mode not in ('posts', 'with_people'):
            raise ActionError('跨线交换请选择只交换岗位或岗位和人员一起交换')
        if left.get('hold') or right.get('hold'):
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
            if left.get('open') != right.get('open') and (left.get('occ') or right.get('occ')):
                raise ActionError('有人员时不能只交换开放状态不同的岗位')
            fixed = {'occ', 'since', 'hold'}
            configs = [{k: v for k, v in x.items() if k not in fixed} for x in (left, right)]
            for post, config in ((left, configs[1]), (right, configs[0])):
                keep = {k: v for k, v in post.items() if k in fixed}
                post.clear(); post.update(config); post.update(keep)
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
        self._reset_line_plans(lid, now); self._reset_line_plans(target_lid, now)
        self.h.log('post_order', actor, None, lid, L['posts'][i]['name'],
                   {'from': i, 'to': target_i, 'target_line': target_lid, 'mode': mode})

    def act_line(self, lid, active, now, actor):
        L = self.line(lid)
        if not active and any(x.get('hold') for x in L['posts']):
            raise ActionError('本线还有定岗人员，请先取消定岗再停用')
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
        self._reset_line_plans(lid, now)

    def _reset_line_plans(self, lid, now=None):
        now = float(self.d.get('lastTick', 0)) if now is None else now
        for q in self.P.values():
            if q.get('assign') and q['assign']['line'] == lid and q['state'] != 'heading' \
                    and q['assign'].get('mode') in ('push', 'opening') and not self.assignment_locked(q, now):
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
        if self.assignment_locked(p,now):
            raise ActionError('剩余餐休3分钟及以内或已待出发，原安排已固定，不能改派')
        if p['state'] not in REST_STATES:
            raise ActionError('%s 当前不能改派' % p['name'])
        L = self.line(lid)
        if not L.get('active'):
            raise ActionError('%s 线未启用' % lid)
        p['assign'] = {'line': lid, 'mode': 'push', 'departAt': self._ready_time(p, now), 'manual': True}
        self.h.log('reassign', actor, pid, lid, None, {})

    def act_leave(self, pid, reason, now, actor):
        p = self.person(pid)
        L, i = self.find_post(pid)
        if L is not None:
            self._leave_line(p, L, i, now)
            L['posts'][i]['occ'] = None
            L['posts'][i]['since'] = None
        for kind in ('crew','seven'):
            self.d['plan'][kind]={key:value for key,value in self.d['plan'][kind].items() if value!=pid}
        p.pop('entryHold',None)
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
        if self.assignment_locked(p,now):
            raise ActionError('原上岗安排已固定；若已失效，请预览并填写原因强制安排')
        if p['state'] == 'meal' or (p.get('breakKind') == 'meal_rest' and now < p['readyAt']):
            raise ActionError('吃饭及饭后休息倒计时未结束，不能进线')
        L, i, x = self.post(lid, i)
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
        for L in self.d['lines']:
            for x in L['posts']:
                hold = x.pop('hold', None)
                if hold:
                    self.h.log('post_resume', 'system', hold['pid'], L['id'], x['name'],
                               {'reason': '零点定岗自动结束', 'original_reason': hold['reason'],
                                'since': hold['since'], 'duration': max(0, now-hold['since'])})
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
                if self.held(p['pid']):
                    if self.d.get('closed') or now >= t:
                        out.append({'level': 'notify', 'pid': p['pid'], 'msg': '%s 仍在定岗，结束现场任务后请手动取消；零点自动结束' % p['name']})
                    continue
                word = '出圈' if kind == 'out' else '下班'
                if kind == 'out':
                    if now >= t + S['outWarnAfter'] and p['pid'] not in taken:
                        out.append({'level': 'notify', 'pid': p['pid'], 'msg': '%s 已过出圈时间 %s %d 分钟，暂无人可推' % (p['name'], fmt(t), now - t)})
                elif now >= t - S['offLead'] - 10 and p['pid'] not in taken:
                    out.append({'level': 'notify', 'pid': p['pid'], 'msg': '%s 需在 %s 前推%s，暂无人可替' % (p['name'], fmt(t - S['offLead']), word)})
            if p.get('role') in ('rotation', 'op') and st not in ('done', 'excluded', 'notyet', 'meal') and not p.get('ate') \
                    and self.meal_eligible(p) and not self.d.get('closed') \
                    and self.meal_deadline(p) - now < S['mealWarnLeft']:
                out.append({'level': 'notify', 'pid': p['pid'], 'msg': '%s 还没吃饭，离%s不到 %d 小时' % (
                    p['name'], '闭园' if self.meal_deadline(p) < p['end'] else '下班', S['mealWarnLeft'] // 60)})
        if now >= _m(S['lineOpenAt']) + 2 and not self.d.get('closed'):
            incoming = {q['assign']['line'] for q in self.P.values() if q.get('assign')}
            for L in self.d['lines']:
                if L.get('active') and L['id'] not in incoming:
                    for x in L['posts']:
                        if x.get('open') and not x.get('occ'):
                            out.append({'level': 'warn', 'msg': '%s %s 空岗，暂无人前往' % (L['id'], x['name'])})
        return out
