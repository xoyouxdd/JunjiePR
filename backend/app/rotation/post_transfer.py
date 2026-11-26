"""Move a physical post and its occupant, preserving all operational clocks."""
from app.rotation.engine import ActionError


def transfer(eng, body, now, actor):
    source = eng.line(body.get('line'))
    target = eng.line(body.get('target_line'))
    if source is target or not source.get('active') or not target.get('active'):
        raise ActionError('请选择另一条已启用的目标线路')
    post = next((p for p in source['posts'] if p['key'] == body.get('post_key')), None)
    if post is None:
        raise ActionError('原岗位已变化，请重新选择')
    index = body.get('target_i')
    if type(index) is not int or not 0 <= index <= len(target['posts']):
        raise ActionError('目标岗位位置不正确')
    if len(source['posts']) <= 1:
        raise ActionError('原线路至少须保留一个岗位')
    if any(p.get('mode101') for line in (source, target) for p in line['posts']):
        raise ActionError('涉及 101 或待恢复线路，请完成恢复后再跨线移岗')
    if not post.get('open') or any(post.get(k) for k in ('hold', 'draftHold', 'plannedHold', 'openRequest', 'closeAfterRelief')):
        raise ActionError('请选择未定岗、没有待执行加撤岗的开放岗位')
    if any(p['name'] == post['name'] for p in target['posts']):
        raise ActionError('目标线路已有同名岗位，请先调整名称')
    person = eng.P.get(post.get('occ'))
    if person and person['state'] not in {'onpost', 'notyet'}:
        raise ActionError('岗位人员状态已变化，请刷新后重试')
    original = source['posts'].index(post)
    mappings = {}
    for kind in ('crew', 'seven'):
        plan = eng.d.get('plan', {}).get(kind, {})
        mappings[kind] = {p['key']: plan.get(f"{line['id']}#{i}")
                          for line in (source, target) for i, p in enumerate(line['posts'])}
        for key in list(plan):
            if any(key.startswith(line['id'] + '#') for line in (source, target)):
                del plan[key]
    source['posts'].pop(original)
    target['posts'].insert(index, post)
    for kind, records in mappings.items():
        plan = eng.d.setdefault('plan', {}).setdefault(kind, {})
        for line in (source, target):
            for i, slot in enumerate(line['posts']):
                if records.get(slot['key']) is not None:
                    plan[f"{line['id']}#{i}"] = records[slot['key']]
    if person:
        # Accounting segments follow line membership, while displayed/continuous clocks stay intact.
        start = person.get('lineSegmentStart', person.get('lineStart'))
        if person['state'] == 'onpost' and start is not None:
            boundary = max(now, start)
            if boundary > start:
                eng.h.segment(person['pid'], source['id'], start, boundary)
            person['lineSegmentStart'] = boundary
        person['line'] = target['id']
        person['lastLine'] = target['id']
        if target['id'] not in person.setdefault('visited', []):
            person['visited'].append(target['id'])
        person['confirmationSequence'] = person.get('confirmationSequence', 0) + 1
        if person.get('batchResult'):
            person['batchResult']['superseded'] = True
    eng.h.log('post_transfer', actor, post.get('occ'), target['id'], post['name'],
              {'postKey': post['key'], 'fromLine': source['id'], 'fromPosition': original + 1,
               'toLine': target['id'], 'toPosition': index + 1, 'preservedClocks': True})
