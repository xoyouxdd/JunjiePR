"""Track where a 101 worker would be in the sequence without moving them."""
from app.rotation.engine import ActionError


TOWER_NAMES = {'塔台', '塔协', '塔台协助'}


def fixed(post):
    return post.get('mode101', {}).get('phase') == 'fixed'


def returning(post):
    return post.get('mode101', {}).get('phase') == 'return'


def position(line, key):
    return next((i for i, post in enumerate(line['posts']) if post['key'] == key), None)


def target_index(line, index):
    marker = line['posts'][index]['mode101']
    if marker['target'] is None:
        return len(line['posts'])
    target = position(line, marker['target'])
    return max(index, target if target is not None else index)


def target_options(line, index):
    return [post for i, post in enumerate(line['posts'])
            if i == index or i > index and post.get('open') and not post.get('hold') and not post.get('mode101')]


def toggle(eng, on, now, actor):
    if on == bool(eng.d.get('mode101Active')):
        return
    if on and eng.d.get('closed'):
        raise ActionError('轮岗已关闭，不能开启 101 模式')
    posts = [post for line in eng.d['lines'] for post in line['posts']
             if post.get('mode101') or post['name'].replace(' ', '') in TOWER_NAMES]
    if on and not posts:
        raise ActionError('当前班表没有塔台或塔台协助岗位')
    for post in posts:
        if on:
            post.setdefault('mode101', {'target': post['key']})['phase'] = 'fixed'
        elif post.get('mode101'):
            if post.get('occ'):
                post['mode101']['phase'] = 'return'
            else:
                post.pop('mode101')
    eng.d['mode101Active'] = on
    eng.h.log('mode101_start' if on else 'mode101_end', actor, None, None, None,
              {'posts': [post['key'] for post in posts]})


def configure(eng, body, now, actor):
    from app.rotation import forecast as F

    line, index, post = F.locate_post(eng, body.get('post_key'))
    if not post or not post.get('mode101'):
        raise ActionError('此岗位当前不在 101 模式或待恢复状态')
    if body['action'] == 'mode101_target':
        if not post.get('occ'):
            raise ActionError('此岗位没有需要调整的人员')
        key = body.get('target_post_key')
        if key is not None and (not isinstance(key, str) or key not in {candidate['key'] for candidate in target_options(line, index)}):
            raise ActionError('请选择本线路的开放岗位或待下线')
        post['mode101']['target'] = key
        eng.h.log('mode101_target', actor, post['occ'], line['id'], post['name'], {'target': key})
        return

    if not fixed(post) or post.get('hold'):
        raise ActionError('只能更换 101 模式中且未单独定岗的岗位人员')
    incoming = eng.person(body.get('pid'))
    if incoming['state'] not in {'onpost', 'ready', 'rest', 'pending'}:
        raise ActionError('此人当前不能接替 101 岗位')
    destination_line, destination_index, destination = line, index, post
    fixed_posts = [post]
    if 'destination_post_key' in body:
        if post.get('occ') != incoming['pid'] or incoming['state'] != 'onpost':
            raise ActionError('101 岗位人员已变化，请重新拖拽')
        destination_line, destination_index, destination = F.locate_post(eng, body['destination_post_key'])
        if (not destination or destination is post or not destination_line.get('active')
                or not destination.get('open') or destination.get('hold') or returning(destination)):
            raise ActionError('请选择已开放且可交换的岗位')
        if fixed(destination):
            fixed_posts.append(destination)
    previous = destination.get('occ')
    saved_markers = [slot.pop('mode101') for slot in fixed_posts]
    try:
        F.manual_place(eng, incoming['pid'], destination_line['id'], destination_index, 'swap', now, actor)
    except ActionError:
        for slot, marker in zip(fixed_posts, saved_markers):
            slot['mode101'] = marker
        raise
    for slot in fixed_posts:
        slot['mode101'] = {'target': slot['key'], 'phase': 'fixed'}
    eng.h.log('mode101_swap', actor, incoming['pid'], destination_line['id'], destination['name'],
              {'previous': previous, 'fixedPost': post['key']})


def returned_index(line, moves):
    sources = {move['from'] for move in moves}
    return next((i for i, post in enumerate(line['posts']) if returning(post) and post['key'] in sources), None)


def after_push(line, moves, indexes, kind):
    """Apply cursor changes to the real route or a forecast copy."""
    returned = returned_index(line, moves)
    if returned is not None:
        line['posts'][returned].pop('mode101')
    if kind == 'push7' or not indexes or not moves or moves[0]['to'] != line['posts'][indexes[0]]['key']:
        return
    reached = {move['to'] for move in moves}
    farthest = max((i for i in indexes if line['posts'][i]['key'] in reached), default=-1)
    for index, post in enumerate(line['posts']):
        if not fixed(post) or not post.get('occ'):
            continue
        cursor = target_index(line, index)
        if cursor <= farthest:
            next_index = next((i for i in indexes if i > cursor), None)
            post['mode101']['target'] = line['posts'][next_index]['key'] if next_index is not None else None
