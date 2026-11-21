"""Read saved rotation state without scheduling or issuing confirmations."""
from copy import deepcopy


def opening_status(state, line, index):
    post = line['posts'][index]
    if not line.get('active') or not post.get('open') or post.get('occ') or not post.get('openRequest'):
        return None
    for person in state['persons'].values():
        assignment = person.get('assign') or {}
        if (person.get('entryHold') or person.get('state') not in ('rest', 'meal', 'ready', 'heading')
                or assignment.get('blocked') or assignment.get('line') != line['id']):
            continue
        if assignment.get('taskId'):
            task = state.get('rotationTasks', {}).get(assignment['taskId']) or {}
            if (task.get('status') == 'assigned' and task.get('owner') == person['pid'] and post.get('key')
                    and any(move.get('to') == post['key'] for move in task.get('moves', []))):
                return 'preparing'
        elif assignment.get('mode') == 'opening':
            key = assignment.get('openingKey')
            if (key and key == post.get('key')) or (not key and assignment.get('post') == index):
                return 'preparing'
    return 'matching'


def lines_view(state):
    from app.rotation.forecast import sort_time

    people = state['persons']
    plan = state.get('plan', {})
    crew = plan.get('crew', {})
    seven = plan.get('seven', {})
    batches = state.get('completedShiftBatches', {})
    lines = []
    for line in state['lines']:
        posts = []
        for index, post in enumerate(line['posts']):
            pid = post.get('occ')
            if not pid and line.get('active'):
                key = f"{line['id']}#{index}"
                planned = seven.get(key) or crew.get(key)
                worker = people.get(planned, {})
                if worker.get('state') == 'notyet' and str(round(worker['start'] * 60)) not in batches:
                    pid = planned
            person = people.get(pid, {})
            posts.append({
                'name': post['name'], 'open': post.get('open'), 'occ': pid, 'key': post.get('key'),
                'occName': person.get('name'), 'since': post.get('since'), 'hold': post.get('hold') or post.get('hold101'),
                'prearranged': person.get('state') == 'notyet', 'hold101': bool(post.get('hold101')),
                'hold101Role': post.get('hold101Role'), 'manualHold': post.get('hold'),
                'continuousStart': person.get('continuousStart'), 'entrySortAt': person.get('entrySortAt'),
                'openingStatus': opening_status(state, line, index),
                'openAt': post.get('openAt'), 'openExpired': bool(post.get('openExpired')), 'closeAt': post.get('closeAt'),
                'closeMode': post.get('closeMode'), 'opened': post.get('opened'), 'seven': post.get('seven'),
            })
        lines.append({
            'id': line['id'], 'group': line.get('group'), 'active': line.get('active'), 'standby': line.get('standby'),
            'sortAt': sort_time(line), 'previousSortAt': line.get('previousSortAt'),
            'adding': bool(line.get('adding') or state.get('ruleVersion') == 2
                and line.get('active') and any(p.get('openRequest') and p.get('open') and not p.get('occ') for p in line['posts'])),
            'walk': 0.0, 'posts': posts,
        })
    return lines


def planned_state(state):
    """Display the retained assignments and configuration of an unstarted plan."""
    view = deepcopy(state)
    view['status'] = 'live'
    lost = set(view.get('plan', {}).get('lost', []))
    for line in view['lines']:
        line['active'] = not line.get('standby')
        for post in line['posts']:
            post.update(occ=None, since=None, opened=not post.get('openAt'),
                        open=line['active'] and not post.get('openAt') and not post.get('draftClosed'))
    for pid, person in view['persons'].items():
        person.update(state='excluded' if person.get('role') == 'excluded' else 'notyet',
                      flags=['送失物'] if pid in lost else [], line=None, lineStart=None,
                      continuousStart=None, entrySortAt=None, assign=None, readyAt=None, breakKind=None)
    return view


_PERSON_FIELDS = (
    'pid', 'name', 'state', 'role', 'label', 'note', 'start', 'end', 'line', 'lineStart', 'readyAt',
    'breakKind', 'breakStart', 'flags', 'tags', 'away', 'leaveReason', 'interruptedBreak', 'entryHold',
    'walkbackSince', 'arriveAt', 'ate', 'lastDeparture', 'downReason', 'after', 'visited', 'mark',
    'absences', 'continuousStart', 'entrySortAt', 'timingUnverified', 'sevenReplaced', 'requestedExit', 'initialPool', 'type',
    'scheduledRestStart', 'batchCheckedIn', 'batchResult', 'awaitingShiftEntry',
)


class _RecordedPeople:
    def __init__(self, state, minute):
        self.state = state
        self.minute = minute
        self.people = state['persons']
        self.lines = {line['id']: line for line in state['lines']}
        self.posts = {}
        self.occupants = {}
        for line in state['lines']:
            for index, post in enumerate(line['posts']):
                if post.get('key'):
                    self.posts[post['key']] = post
                if post.get('occ'):
                    self.occupants.setdefault(post['occ'], (line['id'], index, post))

    def task_view(self, assignment):
        task = self.state.get('rotationTasks', {}).get(assignment.get('taskId'))
        if not task:
            return None
        moves = task.get('moves', [])
        outcomes = {'rest': '下岗，回休息室确认休息／吃饭', 'done': '下班', 'away': '出圈', 'sick': '病假'}
        routes = [{
            'pid': move['pid'], 'name': self.people.get(move['pid'], {}).get('name'),
            'from': self.posts.get(move.get('from'), {}).get('name', '待上岗'),
            'to': self.posts.get(move.get('to'), {}).get('name', outcomes.get(move.get('outcome'), '下岗')),
        } for move in moves]
        entry = self.posts.get(moves[0]['to'], {}) if moves else {}
        posts = self.lines.get(task.get('line'), {}).get('posts', [])
        destinations = {move['to']: move['pid'] for move in moves if move.get('to')}
        vacancies = {move['from'] for move in moves if move.get('from')}

        def occupancy(post, pid):
            person = self.people.get(pid, {})
            return {
                'key': post.get('key'), 'name': post['name'], 'occ': pid, 'occName': person.get('name'),
                'open': post.get('open'), 'hold101': bool(post.get('hold101')),
                'hold': bool(post.get('hold') or post.get('hold101')), 'continuousStart': person.get('continuousStart'),
            }

        before = [occupancy(post, post.get('occ')) for post in posts]
        after = []
        for post in posts:
            key = post.get('key')
            prior_occupant = None if key in vacancies else post.get('occ')
            after.append(occupancy(post, destinations.get(key, prior_occupant)))
        keys = ('id', 'revision', 'batch', 'batchSize', 'line', 'allowedAt', 'deadlineAt', 'status', 'transferredFrom')
        return {**{key: task.get(key) for key in keys}, 'entryPost': entry.get('name'), 'routes': routes,
                'mode': task.get('template', {}).get('mode'), 'beforePosts': before, 'afterPosts': after,
                'transferredFromName': self.people.get(task.get('transferredFrom'), {}).get('name')}

    def assignment_view(self, saved, task):
        if not saved:
            return None
        assignment = dict(saved)
        target = self.people.get(assignment.get('target'))
        target_line, _, target_post = self.occupants.get(assignment.get('target'), (None, None, {}))
        if target:
            assignment.update(targetName=target['name'], targetLine=target_line, targetPost=target_post.get('name'))
        if task:
            assignment.update(entryPost=task['entryPost'], deadlineAt=task['deadlineAt'],
                              transferredFromName=task['transferredFromName'])
        elif not assignment.get('entryPost'):
            posts = self.lines.get(assignment.get('line'), {}).get('posts', [])
            entry = self.posts.get(assignment.get('openingKey'))
            index = assignment.get('post')
            if entry is None and isinstance(index, int) and 0 <= index < len(posts):
                entry = posts[index]
            if entry is None and assignment.get('mode') == 'push7' and target_line == assignment.get('line'):
                entry = target_post
            if entry is None:
                entry = next((post for post in posts if post.get('open') and not (post.get('hold') or post.get('hold101'))), {})
            assignment['entryPost'] = entry.get('name')
        return assignment

    def person_view(self, person):
        state = person.get('state')
        _, index, post = self.occupants.get(person['pid'], (None, None, {})) if state == 'onpost' else (None, None, {})
        start = person.get('continuousStart')
        if start is None:
            start = person.get('lineStart')
        assignment = person.get('assign') or {}
        task = self.task_view(assignment)
        timeout = person.get('taskTimeout')
        if timeout:
            timeout = {**{key: value for key, value in timeout.items() if key != 'originalTask'},
                       'entryPost': self.posts.get(timeout.get('entryKey'), {}).get('name', '原上岗'),
                       'transferredToName': self.people.get(timeout.get('transferredTo'), {}).get('name')}
        result = {field: person[field] for field in _PERSON_FIELDS if field in person}
        result.setdefault('lineStart', None)
        result.update(
            post=post.get('name'), postIndex=index, postHold=post.get('hold') or post.get('hold101'),
            assign=self.assignment_view(assignment, task), task=task, departureRoutes=task['routes'] if task else [],
            taskTimeout=timeout, continuousMinutes=max(0, self.minute - start) if state == 'onpost' and start is not None else 0,
            waitingReplacement=state == 'onpost' and self.minute >= person['end'],
            canDepart=False, canArrive=False, canBatchCheckIn=False,
        )
        return result


def person_view(state, person, minute):
    return deepcopy(_RecordedPeople(state, minute).person_view(person))


def board_view(state, minute, off=()):
    people = _RecordedPeople(state, minute)
    end = max((person['end'] for person in people.people.values()), default=1440)
    rule_version = state.get('ruleVersion', 1)
    return deepcopy({
        'date': state['date'], 'status': state['status'], 'ruleVersion': rule_version, 'readOnly': True,
        'forecastSnapshot': None, 'prestartPlanRevision': None, 'timeoutCases': [], 'timeoutWaitingMatch': [],
        'lines': lines_view(state), 'persons': [people.person_view(person) for person in people.people.values()],
        'off': list(off), 'alerts': [], 'notices': state.get('notices', [])[-10:], 'plan': state.get('plan', {}),
        'draftAlerts': state.get('draftAlerts', []), 'openTime': state.get('openTime'), 'closeAt': state.get('closeAt'),
        'closed': state.get('closed', False), 'rotationEnd': end if rule_version == 2 else min(1440, end),
        'taskWorkflow': state.get('taskWorkflow'), 'hold101Active': state.get('hold101Active', False),
        'hold101Recent': state.get('hold101Recent', []),
    })
