"""UI/API adapters around the existing rotation clock and engine, local process only."""
from datetime import date, timedelta
from pathlib import Path
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.rotation import service as S
from app.rotation import forecast_service as F
from app.rotation.engine import rotation_end
from app.rotation.namespace import tables_for
from app.routers import rotation_board as B
from local_tools.rotation_clock.guard import require_local

SPEEDS = (1, 5, 10, 30, 60)
ASSETS = Path(__file__).parent / 'assets'
_restart_tokens = {}


def calendar_fields(clock):
    minute = clock['minute']
    day = date.fromisoformat(clock['date']) + timedelta(days=int(minute // 1440))
    return {'calendarDate': day.isoformat(), 'calendarTime': S.display_time(minute), 'localSimulation': True}


def _advance(db, cfg):
    day, minute = S.clock_now(cfg)
    row = S.day_row(db, cfg.attraction_id, day)
    if row and row.status == 'live':
        S.advance(db, cfg, row, S.day_state(row), minute)
    return day, minute, row


def _speed(value):
    if isinstance(value, bool) or value not in SPEEDS:
        raise S.ActionError('倍速只能为1、5、10、30或60')
    return float(value)


def _coordinates(db, cfg, selected, minute):
    """Keep only a still-applicable overnight day; a distant date is a new date."""
    Day = tables_for(db).RotationDay
    rows = db.query(Day).filter(Day.attraction_id == cfg.attraction_id, Day.status == 'live',
                                Day.work_date < selected).order_by(Day.work_date).all()
    for row in rows:
        state = S.day_state(row)
        elapsed = (date.fromisoformat(selected) - date.fromisoformat(row.work_date)).days * 1440 + minute
        end = rotation_end(state)
        if state.get('ruleVersion') == 2 and end > 1440 and elapsed <= end:
            return row.work_date, elapsed
        if elapsed > end:
            S.advance(db, cfg, row, state, end)
    return selected, minute


@S.transactional
def control(db, aid, body, actor):
    require_local()
    cfg = S.load_config(db, aid)
    day, minute, row = _advance(db, cfg)
    action = body.get('action')
    state = S.clock_state(cfg)
    if action == 'set':
        try:
            selected = date.fromisoformat(body.get('date', '')).isoformat()
        except (ValueError, TypeError) as exc:
            raise S.ActionError('请选择有效日期') from exc
        target = F.clock_value(body.get('time'))
        speed = _speed(body.get('speed', state.get('speed', 1)))
        # Preserve the applicable overnight workday, without trapping an
        # explicit distant calendar selection in an old live day.
        anchored_day, anchored_minute = _coordinates(db, cfg, selected, target)
        S.set_sim_clock(cfg, anchored_day, anchored_minute, speed=speed, paused=True)
    elif action in {'pause', 'resume', 'speed', 'jump'}:
        if state.get('mode') != 'sim':
            raise S.ActionError('请先应用本地模拟时间')
        if action == 'speed':
            S.set_sim_clock(cfg, day, minute, speed=_speed(body.get('speed')))
        elif action == 'jump':
            amount = body.get('minutes')
            if isinstance(amount, bool) or amount not in (1, 10):
                raise S.ActionError('快进只能为1或10分钟')
            S.set_sim_clock(cfg, day, minute + amount)
        else:
            S.set_sim_clock(cfg, day, minute, paused=action == 'pause')
    elif action == 'restart-preview':
        if not row:
            raise S.ActionError('当前工作日没有可重新开始的轮岗')
        original = S.day_state(row)
        if not original.get('initialDraft') or original.get('ruleVersion') != 2:
            raise S.ActionError('没有新版初始班表，不能重新开始')
        token = secrets.token_urlsafe(32)
        # Wall-clock expiry deliberately does not use the simulated clock.
        for key, value in list(_restart_tokens.items()):
            if value['expires'] <= time.time():
                _restart_tokens.pop(key, None)
        _restart_tokens[token] = {'day': day, 'epoch': original.get('confirmationEpoch'),
                                  'actor': actor, 'aid': aid, 'expires': time.time() + 300}
        return {'restart_token': token, 'date': day,
                'message': '仅清理此景点圈此工作日的轮岗测试结果，保留初始班表、其他日期和其他业务数据。'}
    elif action == 'restart':
        token = body.get('restart_token')
        grant = _restart_tokens.get(token) if isinstance(token, str) else None
        original = S.day_state(row) if row else {}
        if not grant or grant['expires'] <= time.time() or grant['actor'] != actor or grant['aid'] != aid or grant['day'] != day or grant['epoch'] != original.get('confirmationEpoch') or body.get('confirm_date') != day:
            raise S.ActionError('重测确认已失效或日期不符，请重新进行两次确认')
        baseline = original.get('initialDraft')
        if not baseline or original.get('ruleVersion') != 2:
            raise S.ActionError('初始班表不可用')
        start = max(0, min(240, min((p['start'] for p in baseline['persons'].values()), default=240)))
        S.set_sim_clock(cfg, day, start, speed=1, paused=True)
        # Existing draft reset checks holds. Release only this run's holds in the
        # private transaction before restoring the saved initial draft.
        for line in original['lines']:
            for post in line['posts']:
                post.pop('hold', None)
        S.save_day(db, row, original)
        S.do_draft_action(db, aid, actor, {'action': 'draft_generate', 'date': day,
                                        'force': True, 'restore_initial': True})
        S.do_draft_action(db, aid, actor, {'action': 'publish', 'date': day})
    else:
        raise S.ActionError('不支持的本地测试时钟操作')
    _advance(db, cfg)
    S.mark_changed(db)
    return {'clock': S.clock_payload(cfg)}


def install(app):
    require_local()
    if getattr(app.state, 'rotation_local_clock', False):
        return
    app.state.rotation_local_clock = True
    # Do not rewind the engine cursor or replay past automatic transitions.
    advance = S.advance
    def forward_only(db, cfg, row, state, target):
        if target < state.get('lastTick', target):
            return False
        return advance(db, cfg, row, state, target)
    S.advance = forward_only
    payload = S.clock_payload
    def local_payload(cfg):
        clock = payload(cfg)
        return {**clock, **calendar_fields(clock)}
    S.clock_payload = local_payload

    router = APIRouter(prefix='/__local/rotation-clock', include_in_schema=False)
    @router.get('/state')
    def read_clock(attraction_id: int | None = None, db: Session = Depends(B.formal_db), user=Depends(B.staff)):
        require_local()
        from app.rotation.participation import personal_scope
        if attraction_id is None and B.capabilities(user)['can_operate']:
            aid=B.board_attraction_id(db,user)
        else:
            aid, _ = personal_scope(db, user, attraction_id)
        with S.transaction(db):
            cfg = S.load_config(db, aid)
            _advance(db, cfg)
            return {'clock': S.clock_payload(cfg), 'attraction_id': aid}
    @router.post('')
    def update_clock(body: dict, db: Session = Depends(B.formal_db), user=Depends(B.manager)):
        try:
            return control(db, B.board_attraction_id(db,user), body, user.employee.employee_no)
        except S.ActionError as exc:
            raise HTTPException(400, str(exc)) from exc
    @router.get('/assets/{name}')
    def asset(name: str):
        require_local()
        if name not in {'clock.js', 'clock.css'}:
            raise HTTPException(404)
        return FileResponse(ASSETS / name, headers={'Cache-Control': 'no-store'})
    app.include_router(router)

    # Only this launcher injects external assets. Production HTML and static
    # resources contain neither this entry point nor a hidden activation flag.
    import app.main as main
    render = main.render_static_page
    def local_page(path):
        response = render(path)
        if path.suffix == '.html':
            html = response.body.decode('utf-8').replace('</head>', '<link rel="stylesheet" href="/__local/rotation-clock/assets/clock.css"></head>')
            html = html.replace('</body>', '<script src="/__local/rotation-clock/assets/clock.js"></script></body>')
            headers = dict(response.headers)
            headers.pop('content-length', None)
            return HTMLResponse(html, headers=headers)
        return response
    main.render_static_page = local_page
