"""轮岗运行：时钟、名单、当天状态的加载与保存、后台推进、待办同步、页面数据。

所有修改当天状态的操作都在 RUNTIME.lock 内进行；后台线程每秒按时钟推进一次。
时间一律用当天零点起的分钟数；后台按当前规则的时间边界推进。
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import date as Date, datetime, timedelta
from functools import wraps
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.rotation import engine as E
from app.rotation.models import (
    RotationConfig,
    RotationDay,
    RotationDuty,
    RotationEvent,
    RotationNotice,
    RotationRosterEntry,
    RotationRosterUpload,
    RotationSegment,
    RotationSnapshot,
)
from app.rotation.roster import SICK_LEAVE_RE, classify
from app.rotation.namespace import tables_for, runtime_for, runtime_for_row
from app.database.connection import SessionLocal
from app.runtime_environment import environment_kind


logger = logging.getLogger(__name__)

DAY_END = 24 * 60
PREARRANGE_MINUTE = 60
ActionError = E.ActionError


class ConfirmationConflict(ActionError):
    """The arrangement shown at confirmation time is no longer current."""


LEGACY_READ_ONLY = '旧版轮岗记录仅供查看，无法继续确认或调整'
ROTATION_CONTACTS_SETTING = 'rotationContacts'


class Runtime:
    def __init__(self):
        self.lock = threading.RLock()
        self.version = 0
        self.sync_epoch = uuid4().hex
        self.cache: dict[tuple[int, str], tuple[int, dict]] = {}
        self.projections: dict[tuple, tuple[tuple, dict]] = {}

    def bump(self):
        self.version += 1
        self.projections.clear()


RUNTIME = Runtime()

_TRANSACTION_KEY = "rotation_transaction"


@contextmanager
def transaction(db: Session):
    """Serialize a complete rotation transaction, including commit and cache publication.

    Nested operations (prearrangement and test reset) share the outer commit. A
    failed operation rolls back its events, duties, notices and private state;
    no uncommitted state or version is ever exposed to another request.
    """
    RUNTIME = runtime_for(db)
    with RUNTIME.lock:
        if _TRANSACTION_KEY in db.info:
            yield
            return
        pending = {"days": {}, "changed": False}
        db.info[_TRANSACTION_KEY] = pending
        try:
            yield
            db.commit()
        except BaseException:
            db.rollback()
            raise
        else:
            for key, saved in pending["days"].items():
                if saved is None:
                    RUNTIME.cache.pop(key, None)
                else:
                    version, serialized = saved
                    RUNTIME.cache[key] = (version, json.loads(serialized))
            if pending["changed"]:
                RUNTIME.bump()
        finally:
            db.info.pop(_TRANSACTION_KEY, None)


def transactional(fn):
    @wraps(fn)
    def wrapped(db: Session, *args, **kwargs):
        RUNTIME = runtime_for(db)
        with RUNTIME.lock:
            with transaction(db):
                result = fn(db, *args, **kwargs)
            if isinstance(result, dict) and "version" in result:
                result["version"] = RUNTIME.version
        return result
    return wrapped


def mark_changed(db: Session) -> None:
    db.info[_TRANSACTION_KEY]["changed"] = True


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- 配置与时钟

def load_config(db: Session, attraction_id: int, *, read_only: bool = False) -> RotationConfig:
    RotationConfig = tables_for(db).RotationConfig
    cfg = db.query(RotationConfig).filter(RotationConfig.attraction_id == attraction_id).first()
    if not cfg:
        baseline = None
        if db.info.get('rotation_formal'):
            from app.rotation.namespace import TEST_TABLES
            baseline = db.query(TEST_TABLES.RotationConfig).filter_by(attraction_id=attraction_id).first()
        cfg = RotationConfig(
            attraction_id=attraction_id,
            lines_json=baseline.lines_json if baseline else _dumps(E.DEFAULT_LINES),
            settings_json=baseline.settings_json if baseline else _dumps({}),
            clock_json=_dumps({"mode": "real"}),
        )
        if not read_only:
            db.add(cfg)
            db.flush()
    elif not read_only and cfg not in db.dirty:
        # Dependencies or a caller may have loaded this row before taking the lock.
        db.refresh(cfg)
    return cfg


def settings_of(cfg: RotationConfig) -> dict:
    saved = json.loads(cfg.settings_json or "{}")
    return {**E.DEFAULT_SETTINGS, **{k: v for k, v in saved.items()
                                    if k in E.DEFAULT_SETTINGS or k == 'lateShiftsByDay'}}


def lines_of(cfg: RotationConfig) -> list[dict]:
    lines = json.loads(cfg.lines_json)
    E.trim_unused_spares({'lines': lines})
    if E.seven_post_priorities(lines) is E.SEVEN_1126_PRIORITY:
        for line in lines:
            for post in line['posts']:
                if post['name'].strip() == '铁马出口':
                    post['seven'] = E.SEVEN_1126_PRIORITY['铁马出口']
    return lines


def clock_state(cfg: RotationConfig) -> dict:
    state = json.loads(cfg.clock_json or '{"mode": "real"}')
    # Old sandbox dates can be paused in production databases. After removing
    # the sandbox clock controls, read the real clock without rewriting the row.
    if state.get("mode") == "sim" and isinstance(cfg, RotationConfig) and environment_kind() == "production":
        return {"mode": "real"}
    return state


def clock_now(cfg: RotationConfig) -> tuple[str, float]:
    """(日期, 分钟)。模拟时钟按倍速走，最多走到当天 24:00。"""
    state = clock_state(cfg)
    if state.get("mode") != "sim":
        now = datetime.now()
        from app.rotation.forecast_service import work_clock
        return work_clock(cfg, now.date().isoformat(), now.hour * 60 + now.minute + now.second / 60)
    minute = float(state["minute"])
    if not state.get("paused"):
        minute += (time.time() - float(state["real_ts"])) * float(state.get("speed", 1)) / 60
    from app.rotation.forecast_service import work_clock
    return work_clock(cfg, state['date'], min(minute, 3 * DAY_END))


def set_sim_clock(cfg: RotationConfig, day: str, minute: float, *, speed: float | None = None, paused: bool | None = None) -> None:
    state = clock_state(cfg)
    cfg.clock_json = _dumps({
        "mode": "sim",
        "date": day,
        "minute": min(float(minute), 3 * DAY_END),
        "real_ts": time.time(),
        "speed": float(speed if speed is not None else state.get("speed", 1)),
        "paused": bool(paused if paused is not None else state.get("paused", False)),
    })


def clock_payload(cfg: RotationConfig) -> dict:
    state = clock_state(cfg)
    day, minute = clock_now(cfg)
    sim = state.get("mode") == "sim"
    return {
        "mode": state.get("mode", "real"),
        "date": day,
        "minute": minute,
        "speed": float(state.get("speed", 1)) if sim else 1.0,
        "paused": bool(state.get("paused")) if sim else False,
        "server_ts": time.time(),
    }


# ---------------------------------------------------------------- 名单

def week_bounds(day: str) -> tuple[str, str]:
    """周日到周六，与 ZTP 周班表一致。"""
    d = Date.fromisoformat(day)
    start = d - timedelta(days=(d.weekday() + 1) % 7)
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


def roster_upload_for(db: Session, attraction_id: int, day: str) -> RotationRosterUpload | None:
    """某天使用的名单：按天上传的优先，其次按周上传的，同类取最新一次。"""
    RotationRosterEntry = tables_for(db).RotationRosterEntry
    RotationRosterUpload = tables_for(db).RotationRosterUpload
    base = db.query(RotationRosterUpload).filter(
        RotationRosterUpload.attraction_id == attraction_id,
        RotationRosterUpload.start_date <= day,
        RotationRosterUpload.end_date >= day,
    )
    for scope in ("day", "week"):
        upload = (
            base.filter(RotationRosterUpload.scope == scope)
            .join(RotationRosterEntry, RotationRosterEntry.upload_id == RotationRosterUpload.id)
            .filter(RotationRosterEntry.work_date == day)
            .order_by(RotationRosterUpload.id.desc())
            .first()
        )
        if upload:
            return upload
    return None


def draft_roster_changed(db: Session, attraction_id: int, day: str, state: dict) -> bool:
    upload = roster_upload_for(db, attraction_id, day)
    return upload is not None and state.get('rosterUploadId') != upload.id


def roster_for(db: Session, attraction_id: int, day: str) -> dict | None:
    """把某天的名单转成引擎使用的结构。"""
    RotationRosterEntry = tables_for(db).RotationRosterEntry
    upload = roster_upload_for(db, attraction_id, day)
    if not upload:
        return None
    rows = db.query(RotationRosterEntry).filter(RotationRosterEntry.upload_id == upload.id, RotationRosterEntry.work_date == day).all()
    hours = json.loads(upload.hours_json or "{}")
    return {
        "people": {r.employee_no: {"pid": r.employee_no, "name": r.name, "type": r.person_type,
                                   "mark": r.mark, "name_remark": r.name_remark} for r in rows},
        "days": {day: {r.employee_no: r.cell_raw for r in rows}},
        "hours": {day: hours[day]} if day in hours else {},
    }


@transactional
def save_roster(db: Session, attraction_id: int, parsed: dict, *, scope: str, file_name: str, uploader_id: int | None, uploader_name: str, only_date: str | None = None) -> RotationRosterUpload:
    RotationRosterEntry = tables_for(db).RotationRosterEntry
    RotationRosterUpload = tables_for(db).RotationRosterUpload
    if scope not in ("week", "day"):
        raise ActionError("上传范围只能是按周或按天")
    dates = sorted(parsed["days"])
    if scope == "day":
        if only_date is None:
            if len(dates) != 1:
                raise ActionError("按天上传请选择要导入的日期")
            only_date = dates[0]
        if only_date not in parsed["days"]:
            raise ActionError(f"名单里没有 {only_date} 这一天")
        dates = [only_date]
    if not dates:
        raise ActionError("名单里没有任何日期")
    upload = RotationRosterUpload(
        attraction_id=attraction_id,
        scope=scope,
        start_date=dates[0],
        end_date=dates[-1],
        file_name=file_name[:255],
        hours_json=_dumps({d: parsed.get("hours", {}).get(d) for d in dates if parsed.get("hours", {}).get(d)}),
        uploaded_by_id=uploader_id,
        uploaded_by_name=uploader_name[:100],
    )
    db.add(upload)
    db.flush()
    name_remarks = parsed.get("name_remarks", {})
    count = 0
    for d in dates:
        for no, cell in parsed["days"][d].items():
            person = parsed["people"].get(no, {})
            db.add(RotationRosterEntry(
                upload_id=upload.id,
                attraction_id=attraction_id,
                work_date=d,
                employee_no=no,
                name=person.get("name", no),
                person_type=(person.get("type") or "")[:30],
                mark=(person.get("mark") or "")[:30],
                cell_raw=(cell or "")[:255],
                name_remark=name_remarks.get(d, {}).get(no, ""),
            ))
            count += 1
    upload.entry_count = count
    mark_changed(db)
    return upload


def roster_summary(db: Session, attraction_id: int, day: str) -> dict:
    roster = roster_for(db, attraction_id, day)
    if not roster:
        return {"date": day, "available": False, "people": []}
    upload = roster_upload_for(db, attraction_id, day)
    people = []
    for no, cell in sorted(roster["days"][day].items(), key=lambda kv: roster["people"][kv[0]]["name"]):
        info = classify(cell)
        person = roster["people"][no]
        people.append({"employee_no": no, "name": person["name"], "name_remark": person["name_remark"],
                       "cell": cell, "kind": info["kind"], "label": info.get("label", "")})
    return {
        "date": day,
        "available": True,
        "scope": upload.scope,
        "file_name": upload.file_name,
        "hours": roster["hours"].get(day),
        "people": people,
    }


def add_excluded_roster_remarks(db: Session, state: dict, people: list[dict]) -> None:
    upload_id = state.get('rosterUploadId')
    excluded = {p['pid']: p for p in people if p.get('role') == 'excluded'}
    if not upload_id or not excluded:
        return
    entry = tables_for(db).RotationRosterEntry
    rows = db.query(entry.employee_no, entry.name_remark).filter(
        entry.upload_id == upload_id, entry.work_date == state['date'],
        entry.employee_no.in_(excluded), entry.name_remark != '',
    )
    for pid, remark in rows:
        excluded[pid]['rosterNameRemark'] = remark


def off_people_view(db: Session, attraction_id: int, state: dict) -> list[dict]:
    off = state.get('off', [])
    missing_shifts = {p['pid'] for p in off if SICK_LEAVE_RE.search(p['label']) and 'start' not in p}
    upload_id = state.get('rosterUploadId')
    if not missing_shifts or not upload_id:
        return off
    entry = tables_for(db).RotationRosterEntry
    rows = db.query(entry.employee_no, entry.cell_raw).filter(
        entry.attraction_id == attraction_id, entry.upload_id == upload_id,
        entry.work_date == state['date'], entry.employee_no.in_(missing_shifts),
    )
    shifts = {}
    for pid, cell in rows:
        shift = classify(cell)
        if shift.get('label') == '病假' and 'start' in shift:
            shifts[pid] = {'start': shift['start'], 'end': shift['end']}
    return [{**p, **shifts.get(p['pid'], {})} for p in off]


# ---------------------------------------------------------------- 本周统计

def week_minutes(db: Session, attraction_id: int, day: str) -> dict:
    RotationSegment = tables_for(db).RotationSegment
    start, end = week_bounds(day)
    out: dict = {}
    rows = (
        db.query(RotationSegment.employee_no, RotationSegment.line, func.sum(RotationSegment.minutes))
        .filter(RotationSegment.attraction_id == attraction_id, RotationSegment.work_date >= start, RotationSegment.work_date <= end)
        .group_by(RotationSegment.employee_no, RotationSegment.line)
        .all()
    )
    for no, line, minutes in rows:
        out.setdefault(no, {})[line] = float(minutes or 0)
    return out


def duty_counts(db: Session, attraction_id: int, day: str) -> dict:
    RotationDuty = tables_for(db).RotationDuty
    start, end = week_bounds(day)
    out: dict = {}
    rows = (
        db.query(RotationDuty.employee_no, RotationDuty.kind, func.count(RotationDuty.id))
        .filter(RotationDuty.attraction_id == attraction_id, RotationDuty.work_date >= start, RotationDuty.work_date <= end, RotationDuty.work_date != day)
        .group_by(RotationDuty.employee_no, RotationDuty.kind)
        .all()
    )
    for no, kind, n in rows:
        out.setdefault(no, {})[kind] = n
    return out


def completed_push7(db: Session, attraction_id: int, day: str) -> dict:
    """Read actual executions, including older workflow logs, in workday order."""
    from app.employee_numbers import employee_number
    from app.rotation.history import reverted_legacy_push7
    from sqlalchemy import and_, or_

    event = tables_for(db).RotationEvent
    purpose = func.json_extract(event.detail_json, '$.purpose')
    rows = db.query(event.id, event.employee_no, event.work_date, event.minute, event.event_type, event.line,
                    event.detail_json).filter(
        event.attraction_id == attraction_id, event.work_date <= day,
        or_(event.event_type == 'push7',
            and_(event.event_type.in_(('depart', 'manual_forecast_complete')), purpose == 'push7'),
            event.event_type == 'workflow_restore'),
    ).order_by(event.id)
    undone, checkpoints, events = set(), {}, []
    for row in rows:
        detail = json.loads(row.detail_json or '{}')
        if row.event_type == 'workflow_restore':
            undone.update(detail.get('reverted_push7', []))
            if detail.get('checkpoints'):
                checkpoints.setdefault(row.work_date, set()).update(detail['checkpoints'])
        elif row.employee_no and not row.employee_no.startswith('temporary:'):
            events.append((row, detail))
    legacy_undone = reverted_legacy_push7(db, attraction_id, events, checkpoints)
    history = {}
    seen = set()
    for row, detail in events:
        if row.id in legacy_undone:
            continue
        execution = detail.get('push7_execution')
        # New same-day executions are read from the live state, including undo.
        if execution:
            if row.work_date == day or execution in undone or execution in seen:
                continue
            seen.add(execution)
        pid = employee_number(row.employee_no)
        count, last_day, last_minute = E.push7_rank(history, pid)
        history[pid] = [count + 1, *max((last_day, last_minute), (row.work_date, row.minute))]
    return history


def write_lost_property_duties(db: Session, attraction_id: int, day: str, plan: dict) -> None:
    RotationDuty = tables_for(db).RotationDuty
    db.query(RotationDuty).filter(RotationDuty.attraction_id == attraction_id, RotationDuty.work_date == day).delete(synchronize_session=False)
    for no in dict.fromkeys(plan.get("lost", [])):
        db.add(RotationDuty(attraction_id=attraction_id, work_date=day, employee_no=no, kind="lost"))


# ---------------------------------------------------------------- 引擎挂钩

class DbHooks:
    """引擎写日志、在线段，读本周累计。"""

    def __init__(self, db: Session, attraction_id: int, day: str):
        self.db = db
        self.attraction_id = attraction_id
        self.day = day
        self.now = 0.0
        self._week = None

    def log(self, typ, actor, pid, line, post, detail):
        RotationEvent = tables_for(self.db).RotationEvent
        self.db.add(RotationEvent(**self.event_values(typ, actor, pid, line, post, detail)))

    def event_values(self, typ, actor, pid, line, post, detail):
        if hasattr(actor, "kind"):
            actor_type = 'employee' if getattr(actor, 'formal', False) and getattr(actor, 'entered_by', None) else actor.kind
            actor_id, actor_name = actor.actor_id, actor.name
            if getattr(actor,'formal',False) and getattr(actor,'temporary',False): actor_type='temporary'
        else:
            actor_type, actor_id, actor_name = "system", None, "系统" if actor == "system" else str(actor)
        if self.db.info.get('rotation_formal') and pid:
            from app.rotation.participation import record_identity
            metadata=record_identity(self.db,self.attraction_id,self.day,pid)
            if metadata:detail={**(detail or {}),'rotation_identity':metadata}
            from app.rotation.temporary import was_temporary
            if metadata.get('temporary') or not metadata and was_temporary(self.db,self.attraction_id,pid,datetime.now()):
                detail={**(detail or {}),'rotation_temporary':True}
        return dict(
            attraction_id=self.attraction_id,
            work_date=self.day,
            minute=self.now,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name,
            event_type=typ,
            employee_no=pid,
            line=line,
            post=post,
            detail_json=_dumps(detail) if detail else None,
        )

    def segment(self, pid, line, start, end):
        RotationSegment = tables_for(self.db).RotationSegment
        self.db.add(RotationSegment(**self.segment_values(pid, line, start, end)))
        self.invalidate_week_minutes()

    def invalidate_week_minutes(self):
        self._week = None

    def segment_values(self, pid, line, start, end):
        return dict(
            attraction_id=self.attraction_id, work_date=self.day, employee_no=pid, line=line,
            start_min=start, end_min=end, minutes=max(0.0, end - start),
        )

    def week_minutes(self):
        if self._week is None:
            self.db.flush()
            self._week = week_minutes(self.db, self.attraction_id, self.day)
        return self._week

    def push7_history(self):
        return completed_push7(self.db, self.attraction_id, self.day)


# ---------------------------------------------------------------- 当天状态

def day_row(db: Session, attraction_id: int, day: str) -> RotationDay | None:
    RotationDay = tables_for(db).RotationDay
    row = db.query(RotationDay).filter(RotationDay.attraction_id == attraction_id, RotationDay.work_date == day).first()
    if row is not None and row not in db.dirty:
        db.refresh(row)
    return row


def day_state(row: RotationDay, *, use_cache: bool = True) -> dict:
    """Return a private copy; engines must never mutate the committed cache."""
    RUNTIME = runtime_for_row(row)
    key = (row.attraction_id, row.work_date)
    cached = RUNTIME.cache.get(key) if use_cache else None
    if cached and cached[0] == row.version:
        state = deepcopy(cached[1])
    else:
        state = json.loads(row.state_json)
    if state.get('ruleVersion') == 2:
        from app.rotation.forecast import upgrade_opening_requests
        upgrade_opening_requests(state)
        if state['status'] in ('draft', 'live'):
            E.clear_retired_101(state)
            E.trim_unused_spares(state)
            if state.get('initialDraft'):
                E.trim_unused_spares(state['initialDraft'])
    return state


def retired_draft(state: dict) -> bool:
    return state['status'] == 'draft' and state.get('ruleVersion') != 2


def save_day(db: Session, row: RotationDay, state: dict, *, notify: bool = True) -> None:
    state.setdefault("confirmationEpoch", uuid4().hex)
    row.state_json = _dumps(state)
    row.status = state["status"]
    row.version += 1
    row.updated_at = datetime.now()
    if notify and state['status'] in ('live','ended'):
        from app.rotation.daily import capture_snapshot
        capture_snapshot(db,row.attraction_id,state,settings_of(load_config(db,row.attraction_id)),float(state.get('lastTick',240)))
    pending = db.info[_TRANSACTION_KEY]
    pending["days"][(row.attraction_id, row.work_date)] = (row.version, row.state_json)
    pending["changed"] = pending["changed"] or notify


def require_current_rules(state: dict) -> None:
    if state.get('ruleVersion') != 2:
        raise ConfirmationConflict(LEGACY_READ_ONLY)


def engine_for(db: Session, cfg: RotationConfig, row: RotationDay, state: dict) -> tuple[E.Engine, DbHooks]:
    require_current_rules(state)
    hooks = DbHooks(db, row.attraction_id, row.work_date)
    return E.Engine(state, settings_of(cfg), hooks), hooks


def advance(db: Session, cfg: RotationConfig, row: RotationDay, state: dict, target: float) -> bool:
    """把采用当前规则的运行班表推进到指定分钟。"""
    if state.get('status') != 'live' or state.get('ruleVersion') != 2:
        return False
    from app.rotation.forecast_service import advance as advance_forecast
    return advance_forecast(db, cfg, row, state, target)


def end_stale_days(db: Session, cfg: RotationConfig, attraction_id: int, today: str) -> None:
    """时钟已经到了新的一天，前面还在运行的轮岗按 24:00 收尾。"""
    RotationDay = tables_for(db).RotationDay
    for row in db.query(RotationDay).filter(RotationDay.attraction_id == attraction_id, RotationDay.status == "live", RotationDay.work_date < today).all():
        state = day_state(row)
        advance(db, cfg, row, state, DAY_END)


def tick_attraction(attraction_id: int, *, formal=False) -> None:
    """后台每秒调用。没有运行中的轮岗时只读不写，避免和其他请求的写入冲突。"""
    with SessionLocal() as db:
        db.info["rotation_formal"] = formal
        with transaction(db):
            Config = tables_for(db).RotationConfig
            cfg = db.query(Config).filter(Config.attraction_id == attraction_id).first()
            if cfg is None:
                return
            day, minute = clock_now(cfg)
            end_stale_days(db, cfg, attraction_id, day)
            db.flush()
            ensure_prearranged_day(db, cfg, attraction_id, day, minute)
            row = day_row(db, attraction_id, day)
            if row and row.status == "live":
                state = day_state(row)
                if state.get('ruleVersion') != 2:
                    return
                needs_epoch = "confirmationEpoch" not in state
                advance(db, cfg, row, state, minute)
                if needs_epoch and "confirmationEpoch" not in state:
                    save_day(db, row, state)


_ticker_started = False
_ticker_stop = threading.Event()


def start_rotation_ticker() -> None:
    global _ticker_started
    if _ticker_started:
        return
    _ticker_started = True
    from app.rotation.namespace import FORMAL_TABLES

    def run():
        while not _ticker_stop.wait(1.0):
            try:
                with SessionLocal() as db:
                    attraction_ids = [r[0] for r in db.query(FORMAL_TABLES.RotationConfig.attraction_id).all()]
            except Exception:
                logger.exception("formal rotation scopes unavailable")
                continue
            for attraction_id in attraction_ids:
                try:
                    tick_attraction(attraction_id, formal=True)
                except Exception:
                    logger.exception("formal rotation tick failed for attraction %s", attraction_id)

    threading.Thread(target=run, name="rotation-ticker", daemon=True).start()


# ---------------------------------------------------------------- 待办（测试阶段只在轮岗页显示）

def display_time(minute: float) -> str:
    """Presentation only: keep clock minutes and fractional task seconds consistent."""
    whole = math.floor(minute)
    seconds = math.floor((minute - whole) * 60 + 1e-6)
    value = f"{(whole // 60) % 24:02d}:{whole % 60:02d}"
    return value + (f":{seconds:02d}" if seconds else "")


def desired_notices(eng: E.Engine, now: float) -> dict:
    from app.rotation.forecast_service import notices
    return notices(eng, now)


def sync_notices(db: Session, attraction_id: int, state: dict, eng: E.Engine, now: float) -> None:
    RotationNotice = tables_for(db).RotationNotice
    day = state["date"]
    desired = desired_notices(eng, now)
    stamp = datetime.now()
    existing = {
        (n.employee_no, n.slot): n
        for n in db.query(RotationNotice).filter(
            RotationNotice.attraction_id == attraction_id, RotationNotice.work_date == day, RotationNotice.status == "open"
        ).all()
    }
    for key, row in existing.items():
        want = desired.get(key)
        if want is None:
            row.status = "done"
            row.closed_at = stamp
        elif (row.kind, row.title, row.body) != want:
            if row.kind != want[0] or row.title != want[1]:
                # 进入下一步：旧的一条完成，另起一条
                row.status = "done"
                row.closed_at = stamp
                existing[key] = None
            else:
                row.body = want[2]
                row.updated_at = stamp
    for key, want in desired.items():
        if existing.get(key) is not None:
            continue
        db.add(RotationNotice(
            attraction_id=attraction_id, work_date=day, employee_no=key[0], slot=key[1],
            kind=want[0], title=want[1], body=want[2], status="open", created_at=stamp, updated_at=stamp,
        ))


# ---------------------------------------------------------------- 页面数据

def assignment_view(eng: E.Engine, p: dict) -> dict | None:
    a = p.get("assign")
    if not a:
        return None
    av = dict(a)
    L = eng.line(a["line"])
    target = eng.P.get(a.get("target"))
    TL, ti = eng.find_post(a.get("target")) if target else (None, None)
    if target:
        av.update(targetName=target["name"], targetLine=TL["id"] if TL else None,
                  targetPost=TL["posts"][ti]["name"] if TL else None)
    indexes = eng.rotation_posts(L)
    entry = ti if a.get("mode") == "push7" and TL is L else (indexes[0] if indexes else None)
    av["entryPost"] = L["posts"][entry]["name"] if entry is not None else None
    return av


def _read_projection_cache(db: Session, cfg: RotationConfig, row: RotationDay, now: float) -> dict:
    """Share only committed, exact-clock read projections; never an engine/session."""
    runtime = runtime_for_row(row)
    day_key = (row.attraction_id, row.work_date)
    if day_key in db.info.get(_TRANSACTION_KEY, {}).get('days', {}):
        return {}
    key = (id(db.get_bind()), *day_key)
    signature = (row.version, row.state_json, runtime.version, cfg.settings_json, cfg.lines_json, float(now))
    with runtime.lock:
        saved = runtime.projections.get(key)
        if saved and saved[0] == signature:
            return saved[1]
        if len(runtime.projections) >= 8:
            runtime.projections.clear()
        result = {}
        runtime.projections[key] = (signature, result)
        return result


def lines_view(eng: E.Engine, *, line_id: str | None = None, now: float | None = None) -> list[dict]:
    from app.rotation.replay import lines_view as project_lines
    from app.rotation import forecast as F

    lines = project_lines(eng.d)
    if now is not None and eng.d.get('ruleVersion') == 2 and eng.d['status'] == 'live':
        for line in lines:
            for post in line['posts']:
                worker = eng.P.get(post['occ'])
                if not worker:
                    continue
                if F.missed_check_in(worker, now, assigned_post=True):
                    post['missedCheckIn'] = True
                elif (worker['start'] in (F.SEVEN_SHIFT, F.RELIEF_SHIFT)
                      and F.can_check_in(eng, worker, now) and not F.attendance_recorded(worker)):
                    post['pendingCheckIn'] = True
    return [line for line in lines if line_id is None or line['id'] == line_id]


def live_view(db: Session, cfg: RotationConfig, row: RotationDay, now: float, *, committed_only: bool = False) -> dict:
    from app.rotation.forecast import closure_previews
    from app.rotation.forecast_service import person_view
    from app.rotation.manual import fingerprint
    state = day_state(row, use_cache=not committed_only)
    if state.get('ruleVersion') != 2:
        from app.rotation.replay import board_view
        result = board_view(state, state.get('lastTick', now), off_people_view(db, row.attraction_id, state))
        add_excluded_roster_remarks(db, state, result['persons'])
        return result
    if not committed_only and "confirmationEpoch" not in state:
        save_day(db, row, state, notify=False)
    from app.rotation.staffing import warnings as staffing_warnings
    projection = _read_projection_cache(db, cfg, row, now)
    if 'live' in projection:
        return deepcopy(projection['live'])
    eng, _ = engine_for(db, cfg, row, state)
    eng._read_projection = projection
    from app.rotation import daily as D
    result = {
        "date": state["date"], "status": state["status"],
        "ruleVersion": state.get('ruleVersion', 1),
        "forecastSnapshot": fingerprint(state),
        "closurePreviews": closure_previews(eng, now) if state["status"] == "live" else {},
        "prestartPlanRevision": D.plan_revision(state) if D.prestart(state, now) else None,
        "timeoutCases": [],
        "timeoutWaitingMatch": [],
        "lines": lines_view(eng, now=now),
        "persons": [person_view(eng, p, now) for p in state["persons"].values()],
        "completedShiftBatches": state.get("completedShiftBatches", {}),
        "off": off_people_view(db, row.attraction_id, state),
        "rosterSickPoolMoves": state['status'] == 'live',
        "exceptionReports": deepcopy(state.get("exceptionReports", [])),
        "staffingWarnings": staffing_warnings(eng, now),
        "postTransferAvailable": state["status"] == "live",
        "postTransferFairnessAvailable": state["status"] == "live",
        "alerts": eng.alerts(now) if state["status"] == "live" else [],
        "notices": state.get("notices", [])[-10:],
        "plan": state["plan"],
        "draftAlerts": state.get("draftAlerts", []),
        "openTime": state.get("openTime"), "closeAt": state.get("closeAt"), "closed": state.get("closed", False),
        "rotationEnd": E.rotation_end(state),
        "taskWorkflow": state.get('taskWorkflow'), "mode101Active": bool(state.get('mode101Active')),
    }

    add_excluded_roster_remarks(db, state, result['persons'])
    projection['live'] = deepcopy(result)
    return result


@transactional
def ensure_prearranged_day(db: Session, cfg: RotationConfig, attraction_id: int, day: str, minute: float) -> None:
    """有班表时 01:00 生成预排；首班开始前仅展示岗位，不计时。"""
    RUNTIME = runtime_for(db)
    with RUNTIME.lock:
        row=day_row(db,attraction_id,day)
        if minute < PREARRANGE_MINUTE or (row and (row.status != 'draft' or not day_state(row).get('dailyAutoStart'))) or not roster_for(db, attraction_id, day):
            return
        if row is None:
            do_draft_action(db, attraction_id, 'system', {"action": "draft_generate", "date": day})
            row = day_row(db, attraction_id, day)
        state = day_state(row)
        if retired_draft(state) or draft_roster_changed(db, attraction_id, day, state):
            # Never run a superseded roster or silently overwrite manual adjustments.
            return
        remember_initial_draft(state, cfg)
        eng, hooks = engine_for(db, cfg, row, state)
        hooks.now = PREARRANGE_MINUTE
        eng.publish(PREARRANGE_MINUTE, 'system')
        state["lastTick"] = PREARRANGE_MINUTE
        write_lost_property_duties(db, attraction_id, day, state["plan"])
        save_day(db, row, state)
        advance(db, cfg, row, state, minute)


@transactional
def board_payload(db: Session, attraction_id: int, *, committed_only: bool = False) -> dict:
    RotationDay = tables_for(db).RotationDay
    RotationRosterEntry = tables_for(db).RotationRosterEntry
    RUNTIME = runtime_for(db)
    cfg = load_config(db, attraction_id, read_only=committed_only)
    day, minute = clock_now(cfg)
    if not committed_only:
        ensure_prearranged_day(db, cfg, attraction_id, day, minute)
    row = day_row(db, attraction_id, day)
    out = {
        "clock": clock_payload(cfg),
        "version": RUNTIME.version,
        "syncEpoch": RUNTIME.sync_epoch,
        "settings": settings_of(cfg),
        "lines": lines_of(cfg),
        "structuralPosts": True,
        "roster": roster_summary(db, attraction_id, day),
        "rosterDates": [d for (d,) in db.query(RotationRosterEntry.work_date).filter(RotationRosterEntry.attraction_id == attraction_id).distinct().order_by(RotationRosterEntry.work_date.asc()).all()],
        "days": [{"date": r.work_date, "status": r.status} for r in db.query(RotationDay).filter(RotationDay.attraction_id == attraction_id).order_by(RotationDay.work_date.desc()).limit(30).all()],
        "day": None,
        "draft": None,
    }
    if row and row.status in ("live", "ended"):
        out["day"] = live_view(db, cfg, row, minute, committed_only=committed_only)
    elif row and row.status == "draft":
        out["draft"] = day_state(row)
        out["roster_changed"] = draft_roster_changed(db, attraction_id, day, out["draft"])
        out["requires_current_rules"] = retired_draft(out["draft"])
    return out


@transactional
def screen_payload(db: Session, attraction_id: int, *, committed_only: bool = False) -> dict:
    RUNTIME = runtime_for(db)
    cfg = load_config(db, attraction_id, read_only=committed_only)
    day, minute = clock_now(cfg)
    if not committed_only:
        ensure_prearranged_day(db, cfg, attraction_id, day, minute)
    row = day_row(db, attraction_id, day)
    out = {"clock": clock_payload(cfg), "version": RUNTIME.version, "syncEpoch": RUNTIME.sync_epoch, "day": None,
           "settings": {"departEarly": settings_of(cfg)["departEarly"]}}
    if row and row.status in ("live", "ended"):
        view = live_view(db, cfg, row, minute, committed_only=committed_only)
        view.pop("alerts", None)
        view.pop("plan", None)
        view.pop("draftAlerts", None)
        view.pop("exceptionReports", None)
        out["day"] = view
    return out


@transactional
def member_payload(db: Session, attraction_id: int, employee_no: str) -> dict:
    return _member_payload(db, attraction_id, employee_no, committed_only=False)


def committed_member_payload(db: Session, attraction_id: int, employee_no: str) -> dict:
    """Project the mobile view from the caller's SQLite read snapshot."""
    return _member_payload(db, attraction_id, employee_no, committed_only=True)


def _member_payload(db: Session, attraction_id: int, employee_no: str, *, committed_only: bool) -> dict:
    RotationNotice = tables_for(db).RotationNotice
    RotationSegment = tables_for(db).RotationSegment
    RUNTIME = runtime_for(db)
    cfg = load_config(db, attraction_id, read_only=committed_only)
    clock = clock_payload(cfg)
    day, minute = clock["date"], clock["minute"]
    if committed_only:
        RotationDay = tables_for(db).RotationDay
        row = db.query(RotationDay).filter_by(attraction_id=attraction_id, work_date=day).first()
    else:
        ensure_prearranged_day(db, cfg, attraction_id, day, minute)
        row = day_row(db, attraction_id, day)
    notices = (
        db.query(RotationNotice)
        .filter(RotationNotice.attraction_id == attraction_id, RotationNotice.employee_no == employee_no, RotationNotice.work_date == day)
        .order_by(RotationNotice.status.desc(), RotationNotice.updated_at.desc(), RotationNotice.id.desc())
        .limit(30)
        .all()
    )
    out = {
        "clock": clock,
        "version": RUNTIME.version,
        "syncEpoch": RUNTIME.sync_epoch,
        "employee_no": employee_no,
        "settings": {"departEarly": settings_of(cfg)["departEarly"]},
        "person": None,
        "line": None,
        "confirmations": [],
        "notices": [{
            "id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "status": n.status,
            "updated_at": n.updated_at.strftime("%H:%M:%S") if n.updated_at else None,
        } for n in notices],
    }
    if not committed_only:
        out["week_minutes"] = week_minutes(db, attraction_id, day).get(employee_no, {})
    if not row or row.status not in ("live", "ended"):
        return out
    state = day_state(row, use_cache=not committed_only)
    p = state["persons"].get(employee_no)
    if not p:
        return out
    out['ruleVersion'] = state.get('ruleVersion', 1)
    out['taskWorkflow'] = bool(state.get('taskWorkflow'))
    current_rules = state.get('ruleVersion') == 2
    if current_rules:
        from app.rotation.forecast_service import person_view
        if not committed_only and "confirmationEpoch" not in state:
            save_day(db, row, state, notify=False)
        eng, _ = engine_for(db, cfg, row, state)
        eng._read_projection = {} if committed_only else _read_projection_cache(db, cfg, row, minute)
        desired = desired_notices(eng, minute)
        current_notices = []
        for notice, view in zip(notices, out['notices']):
            if notice.status == 'open':
                current = desired.get((employee_no, notice.slot))
                if current is None:
                    continue
                view.update(zip(('kind', 'title', 'body'), current))
            current_notices.append(view)
        out['notices'] = current_notices
        out["person"] = person_view(eng, p, minute)
    else:
        from app.rotation.replay import person_view as replay_person
        out['readOnly'] = True
        out["person"] = replay_person(state, p, state.get('lastTick', minute))
        out["person"]["confirmationTokens"] = {}
    out["confirmations"] = [r for r in reversed(list(state.get("confirmations", {}).values()))
                            if r["pid"] == employee_no][:20]
    lid = p.get("line") or (p.get("assign") or {}).get("line")
    if current_rules:
        out["line"] = next(iter(lines_view(eng, line_id=lid, now=minute)), None) if lid else None
    L = next((line for line in state['lines'] if line['id'] == lid), None)
    if L and not current_rules:
        out["line"] = {
            "id": L["id"],
            "posts": [{"name": x["name"], "open": x.get("open"), "occ": x.get("occ"),
                       "occName": state["persons"][x["occ"]]["name"] if x.get("occ") in state["persons"] else None,
                       "since": x.get("since"), "hold": x.get("hold")} for x in L["posts"]],
        }
    if committed_only:
        return out
    segs = (
        db.query(RotationSegment)
        .filter(RotationSegment.attraction_id == attraction_id, RotationSegment.work_date == day, RotationSegment.employee_no == employee_no)
        .order_by(RotationSegment.start_min.asc())
        .all()
    )
    out["today"] = [{"line": s.line, "start": s.start_min, "end": s.end_min, "minutes": s.minutes} for s in segs]
    return out


# ---------------------------------------------------------------- 操作

@transactional
def confirmation_status(db: Session, attraction_id: int, work_date: str, token: str, employee_no: str | None) -> dict:
    try:
        Date.fromisoformat(work_date)
    except ValueError as exc:
        raise ActionError("日期格式需为 YYYY-MM-DD") from exc
    row = day_row(db, attraction_id, work_date)
    result = day_state(row).get("confirmations", {}).get(token) if row else None
    if result and employee_no is not None and result["pid"] != employee_no:
        raise ActionError("只能查看自己的确认结果")
    return {"confirmed": bool(result) and result.get('status') not in ('reversed','superseded'), "result": result}

SCREEN_ACTIONS = {"arrive", "depart", "batch_check_in", "correct_check_in", "resolve_exception"}
LIVE_ACTIONS = SCREEN_ACTIONS | {
    "post", "line", "away", "back", "leave", "set_close", "add_person", "plan_place", "move",
    "post_order", "post_transfer", "post_create", "post_delete", "post_hold", "rename_post", "mode101", "mode101_target", "mode101_swap", "pool", "break_time",
    "shift", "sort_time", "place", "sick",
}
DRAFT_ACTIONS = {"draft_generate", "draft_set", "draft_list", "draft_close", "draft_role", "draft_discard", "publish"}


def _require(body: dict, *keys):
    for key in keys:
        if body.get(key) in (None, ""):
            raise ActionError(f"缺少参数：{key}")


def action_boolean(body: dict, key: str) -> bool:
    value = body.get(key)
    if not isinstance(value, bool):
        raise ActionError(f"参数 {key} 必须是布尔值")
    return value


def action_time(value) -> int:
    try:
        hours, minutes = str(value).split(":")
        h, m = int(hours), int(minutes)
        if not hours.isdecimal() or not minutes.isdecimal() or not 0 <= h <= 24 or not 0 <= m < 60 or (h == 24 and m != 0):
            raise ValueError()
        return h * 60 + m
    except (ValueError, TypeError) as exc:
        raise ActionError("时间格式需为 HH:MM") from exc


@transactional
def do_live_action(db: Session, attraction_id: int, actor, body: dict, *,
                   add_identity: tuple[str, dict] | None = None) -> dict | None:
    from app.employee_numbers import employee_number
    if isinstance(body.get('pid'), str):
        body = {**body, 'pid': employee_number(body['pid'])}
    RUNTIME = runtime_for(db)
    act = body.get("action")
    if not isinstance(act, str) or act not in LIVE_ACTIONS:
        raise ActionError("不支持的操作")
    if getattr(actor, 'kind', None) == 'screen' and act not in SCREEN_ACTIONS:
        raise ActionError('休息室大屏只可确认去休息或去轮岗')
    if act=='move' and body.get('force') is True and not body.get('expected_snapshot'):
        raise ActionError('强制安排必须先预览影响并确认')
    if act not in {"post", "post_order", "post_transfer", "post_create", "post_delete", "line", "set_close", "rename_post",
                   "mode101", "mode101_target", "mode101_swap", "sort_time", "add_person"}:
        _require(body, "pid")
        if not isinstance(body["pid"], str):
            raise ActionError("工号必须是字符串")
    with RUNTIME.lock:
        cfg = load_config(db, attraction_id)
        day, now = clock_now(cfg)
        row = day_row(db, attraction_id, day)
        if not row:
            raise ActionError("今天的轮岗还没有发布")
        state = day_state(row)
        token = body.get("confirmation_token")
        if token is not None:
            if act not in SCREEN_ACTIONS or not isinstance(token, str) or len(token) != 64:
                raise ActionError("请刷新后重新确认")
            completed = state.get("confirmations", {}).get(token)
            if completed:
                if completed.get('status') in ('reversed','superseded'):
                    raise ConfirmationConflict('原确认已撤销，请查看最新安排')
                if completed["pid"] != body.get("pid") or completed["action"] != act:
                    raise ActionError("确认任务与人员不匹配")
                return {"ok": True, "duplicate": True, "result": completed}
        require_current_rules(state)
        if add_identity is not None and add_identity[0] != day:
            raise ConfirmationConflict('轮岗工作日已变化，请重新核对')
        if row.status != "live":
            raise ActionError("今天的轮岗还没有发布")
        expected = body.get('expected_snapshot')
        if act in {'post_transfer', 'post_create', 'post_delete', 'mode101', 'mode101_target', 'mode101_swap'} and not expected:
            raise ConfirmationConflict('请刷新看板后重新确认岗位安排')
        if expected:
            from app.rotation.manual import fingerprint
            if expected != fingerprint(state):
                raise ConfirmationConflict('安排已变化，请重新预览后提交')
        advance(db, cfg, row, state, now)
        if state['status'] != 'live':
            raise ActionError('今天的轮岗已结束')
        if expected and expected != fingerprint(state):
            raise ConfirmationConflict('安排已变化，请重新预览后提交')
        eng, hooks = engine_for(db, cfg, row, state)
        hooks.now = now
        from app.rotation.forecast_service import action as forecast_action
        return forecast_action(db, cfg, row, eng, body, now, actor,
                               add_identity=add_identity[1] if add_identity is not None else None)


def remember_initial_draft(state: dict, cfg: RotationConfig) -> None:
    """Save the pristine pre-publication state once, without recursive snapshots."""
    if "initialDraft" not in state:
        state["initialDraft"] = json.loads(_dumps(state))
        state["initialSettings"] = settings_of(cfg)


@transactional
def do_draft_action(db: Session, attraction_id: int, actor, body: dict) -> None:
    from app.employee_numbers import employee_number
    if isinstance(body.get('pid'), str):
        body = {**body, 'pid': employee_number(body['pid'])}
    RotationDay = tables_for(db).RotationDay
    RotationDuty = tables_for(db).RotationDuty
    RotationEvent = tables_for(db).RotationEvent
    RotationNotice = tables_for(db).RotationNotice
    RotationSegment = tables_for(db).RotationSegment
    RotationSnapshot = tables_for(db).RotationSnapshot
    RUNTIME = runtime_for(db)
    act = body.get("action")
    if not isinstance(act, str) or act not in DRAFT_ACTIONS:
        raise ActionError("不支持的操作")
    with RUNTIME.lock:
        cfg = load_config(db, attraction_id)
        clock_day, now = clock_now(cfg)
        day = str(body.get("date") or clock_day)
        try:
            Date.fromisoformat(day)
        except ValueError as exc:
            raise ActionError("日期格式需为 YYYY-MM-DD") from exc
        row = day_row(db, attraction_id, day)
        if row and row.status == 'draft' and day < clock_day and act != 'publish' and not body.get('force'):
            raise ActionError('往日班表只读，不能修改')
        if act == "draft_generate":
            prior = day_state(row) if row is not None else {}
            if row is not None and row.status != 'draft':
                require_current_rules(prior)
            if prior.get('ruleVersion') == 2 and any(x.get('hold') for L in prior['lines'] for x in L['posts']):
                raise ActionError('当日还有定岗人员，请先取消定岗再重新生成班表')
            if body.get("restore_initial") and prior.get("initialDraft"):
                require_current_rules(prior['initialDraft'])
            if row and row.status in ("live", "ended") and not body.get("force"):
                raise ActionError(f"{day} 已经发布过轮岗，不能重新生成")
            roster = roster_for(db, attraction_id, day)
            if not roster:
                raise ActionError(f"{day} 还没有上传名单")
            if row is not None:
                # 必须先清掉当日计时，避免重新预排仍被刚撤回的本周累计影响。
                for model in (RotationEvent, RotationSegment, RotationNotice, RotationDuty, RotationSnapshot):
                    db.query(model).filter(model.attraction_id == attraction_id, model.work_date == day).delete(synchronize_session=False)
            if body.get("restore_initial") and prior.get("initialDraft"):
                state = json.loads(_dumps(prior["initialDraft"]))
                state["initialDraft"] = json.loads(_dumps(prior["initialDraft"]))
                state["initialSettings"] = dict(prior.get("initialSettings") or settings_of(cfg))
            else:
                # Historical close-out must be visible to the weekly ranking
                # before generating today's first plan (autoflush is disabled).
                if day == clock_day:
                    end_stale_days(db, cfg, attraction_id, day)
                db.flush()
                state = E.build_draft(day, roster, {"lines": lines_of(cfg)}, settings_of(cfg),
                                      duty_counts(db, attraction_id, day), week_minutes(db, attraction_id, day),
                                      push7_history=completed_push7(db, attraction_id, day))
                from app.rotation.forecast import initialize as initialize_forecast
                initialize_forecast(E.Engine(state, settings_of(cfg), None))
            upload=roster_upload_for(db,attraction_id,day)
            state['rosterUploadId']=upload.id if upload else None
            if db.info.get('rotation_formal'):
                from app.rotation.participation import roster_metadata
                state['rosterIdentities']=roster_metadata(db,attraction_id,day,upload.id if upload else None)
            # A reset starts a new run, even when the first plan is identical.
            state["confirmationEpoch"] = uuid4().hex
            state.pop("confirmations", None)
            if row is None:
                row = RotationDay(attraction_id=attraction_id, work_date=day, status="draft", state_json="{}", version=0)
                db.add(row)
                db.flush()
            save_day(db, row, state)
            return
        if not row or row.status != "draft":
            raise ActionError("没有正在编辑的草稿，请先生成")
        state = day_state(row)
        if act != 'draft_discard' and retired_draft(state):
            raise ConfirmationConflict('旧版未启用预排仅供查看。请重新生成并核对后启用')
        plan = state["plan"]
        from app.rotation.daily import seven_relief_slots, normalize_seven_assignments
        previous_relief=seven_relief_slots(state) if 'push7Targets' in plan else None
        if act == "draft_set":
            _require(body, "kind", "key")
            kind, key, pid = body["kind"], body["key"], body.get("pid") or None
            if kind not in ("crew", "seven"):
                raise ActionError("只能设置开园岗位或 7 点岗位")
            E.Engine(state, settings_of(cfg), None).plan_post(key)
            if pid is not None and not isinstance(pid, str):
                raise ActionError("工号必须是字符串")
            if pid and pid not in state["persons"]:
                raise ActionError("名单里没有这个人")
            for k in ("crew", "seven"):
                for kk in list(plan[k]):
                    if kk==key or pid and plan[k][kk] == pid:
                        del plan[k][kk]
            if pid:
                plan[kind][key] = pid
                if pid in plan["push7"]:
                    plan["push7"].remove(pid)
            else:
                plan[kind].pop(key, None)
        elif act == "draft_list":
            _require(body, "kind")
            if body["kind"] not in ("push7", "lost"):
                raise ActionError("只能设置推 7 点或送失物")
            if not isinstance(body.get("pids", []), list):
                raise ActionError("人员名单必须是列表")
            pids = list(dict.fromkeys(x for x in body.get("pids", []) if isinstance(x, str) and x))
            if any(not isinstance(x, str) for x in body.get("pids", [])):
                raise ActionError("工号必须是字符串")
            for x in pids:
                if x not in state["persons"]:
                    raise ActionError("名单里没有这个人")
            plan[body["kind"]] = pids
            if body['kind']=='push7' and 'targets' in body:
                from app.rotation.daily import push7_target_slots
                targets=body['targets']
                expected=list(push7_target_slots(state).values())
                if targets!=expected or len(targets)!=len(body.get('pids',[])):
                    raise ConfirmationConflict('7点岗位或接替对象已变化，请刷新预排')
                bindings={}
                for pid,target in zip(body['pids'],targets):
                    if pid and pid not in bindings:bindings[pid]=target
                plan['push7Targets']=bindings
        elif act == "draft_close":
            _require(body, "close")
            action_time(body["close"])
            state["closeAt"] = str(body["close"])
        elif act == "draft_role":
            _require(body, "pid", "role")
            if not isinstance(body["pid"], str):
                raise ActionError("工号必须是字符串")
            if body["role"] not in ("rotation", "op", "excluded"):
                raise ActionError("角色只能是轮岗、OP 或不轮岗")
            p = state["persons"].get(body["pid"])
            if not p:
                raise ActionError("名单里没有这个人")
            p["role"] = body["role"]
            if p["role"] != "rotation":
                plan['push7'] = [pid for pid in plan['push7'] if pid != p['pid']]
                plan.get('push7Targets', {}).pop(p['pid'], None)
                for k in ("crew", "seven"):
                    for kk in list(plan[k]):
                        if plan[k][kk] == p["pid"]:
                            del plan[k][kk]
        elif act == "draft_discard":
            db.delete(row)
            db.info[_TRANSACTION_KEY]["days"][(attraction_id, day)] = None
            mark_changed(db)
            return
        elif act == "publish":
            if day != clock_day:
                raise ActionError(f"只能发布当天（{clock_day}）的轮岗；测试时请先把测试时钟调到 {day}")
            if draft_roster_changed(db, attraction_id, day, state):
                raise ActionError('上传班表已更新，旧预排已过期。请重新生成并核对预排后启用')
            hooks = DbHooks(db, attraction_id, day)
            hooks.now = now
            remember_initial_draft(state, cfg)
            eng = E.Engine(state, state["initialSettings"], hooks)
            eng.publish(now, actor)
            state["lastTick"] = now
            write_lost_property_duties(db, attraction_id, day, plan)
            sync_notices(db, attraction_id, state, eng, now)
        if previous_relief is not None:
            if act=='draft_list' and body.get('kind')=='push7' and 'targets' not in body:plan.pop('push7Targets',None)
            elif act in ('draft_set','draft_role'):normalize_seven_assignments(state,previous_relief)
        save_day(db, row, state)


# ---------------------------------------------------------------- 记录

def day_log(db: Session, attraction_id: int, day: str, limit: int = 500) -> list[dict]:
    RotationEvent = tables_for(db).RotationEvent
    rows = (
        db.query(RotationEvent)
        .filter(RotationEvent.attraction_id == attraction_id, RotationEvent.work_date == day)
        .filter(RotationEvent.event_type != 'workflow_checkpoint')
        .order_by(RotationEvent.id.desc())
        .limit(limit)
        .all()
    )
    names = {}
    roster = roster_for(db, attraction_id, day)
    if roster:
        names = {no: p["name"] for no, p in roster["people"].items()}
    if db.info.get('rotation_formal'):
        from app.rotation.temporary import TemporaryMember
        for member in db.query(TemporaryMember).filter_by(attraction_id=attraction_id):
            names.setdefault(member.employee_no,member.name)
    return [{
        "id": r.id, "minute": r.minute, "time": display_time(r.minute), "actor": r.actor_name, "actor_type": r.actor_type,
        "type": r.event_type, "employee_no": r.employee_no, "name": json.loads(r.detail_json or '{}').get('rotation_identity',{}).get('name') or names.get(r.employee_no, r.employee_no),
        "line": r.line, "post": r.post, "detail": json.loads(r.detail_json) if r.detail_json else None,
        "temporary": bool(json.loads(r.detail_json or '{}').get('rotation_temporary')),
        "actor_temporary": r.actor_type=='temporary',
        "identity_labels": _identity_labels(json.loads(r.detail_json or '{}').get('rotation_identity',{})),
    } for r in rows]


def person_record(db: Session, attraction_id: int, employee_no: str, day: str) -> dict:
    RotationDuty = tables_for(db).RotationDuty
    RotationEvent = tables_for(db).RotationEvent
    RotationSegment = tables_for(db).RotationSegment
    start, end = week_bounds(day)
    segs = (
        db.query(RotationSegment)
        .filter(RotationSegment.attraction_id == attraction_id, RotationSegment.employee_no == employee_no,
                RotationSegment.work_date >= start, RotationSegment.work_date <= end)
        .order_by(RotationSegment.work_date.asc(), RotationSegment.start_min.asc())
        .all()
    )
    totals: dict = {}
    for s in segs:
        totals[s.line] = totals.get(s.line, 0) + s.minutes
    duties = (
        db.query(RotationDuty)
        .filter(RotationDuty.attraction_id == attraction_id, RotationDuty.employee_no == employee_no,
                RotationDuty.work_date >= start, RotationDuty.work_date <= end)
        .all()
    )
    events = (
        db.query(RotationEvent)
        .filter(RotationEvent.attraction_id == attraction_id, RotationEvent.employee_no == employee_no,
                RotationEvent.work_date >= start, RotationEvent.work_date <= end)
        .order_by(RotationEvent.work_date.asc(), RotationEvent.minute.asc(), RotationEvent.id.asc())
        .all()
    )
    member=None
    if db.info.get('rotation_formal'):
        from app.rotation.temporary import TemporaryMember
        member=db.query(TemporaryMember).filter_by(attraction_id=attraction_id,employee_no=employee_no).first()
    def temporary_segment(segment):
        if not member: return False
        start_at=datetime.fromisoformat(segment.work_date)+timedelta(minutes=segment.start_min)
        end_at=datetime.fromisoformat(segment.work_date)+timedelta(minutes=segment.end_min)
        return member.created_at<=end_at and (not member.adopted_at or start_at<member.adopted_at)
    identity_by_day={}
    if db.info.get('rotation_formal'):
        from app.rotation.participation import record_identity
        identity_by_day={d:record_identity(db,attraction_id,d,employee_no) for d in {s.work_date for s in segs}}
    return {
        "employee_no": employee_no,
        "week": [start, end],
        "segments": [{"date": s.work_date, "line": s.line, "start": s.start_min, "end": s.end_min, "minutes": s.minutes,
                      "temporary":identity_by_day.get(s.work_date,{}).get('temporary',temporary_segment(s)),
                      "identity_labels":_identity_labels(identity_by_day.get(s.work_date,{}))} for s in segs],
        "totals": totals,
        "duties": [{"date": d.work_date, "kind": d.kind} for d in duties],
        "events": [{"date": e.work_date, "time": display_time(e.minute), "type": e.event_type, "actor": e.actor_name, "line": e.line, "post": e.post,
                    "temporary":bool(json.loads(e.detail_json or '{}').get('rotation_temporary')),
                    "actor_temporary":e.actor_type=='temporary',
                    "identity_labels":_identity_labels(json.loads(e.detail_json or '{}').get('rotation_identity',{})),
                    "detail": json.loads(e.detail_json) if e.detail_json else None} for e in events],
    }


def _identity_labels(metadata):
    from app.rotation.participation import labels
    return labels(metadata)


def update_config(cfg: RotationConfig, lines, settings, employee_id: int | None) -> None:
    if lines is not None:
        if not isinstance(lines, list) or not lines:
            raise ActionError("线配置必须是非空列表")
        seen = set()
        for L in lines:
            if not isinstance(L, dict) or not L.get("id") or not isinstance(L.get("posts"), list) or not L["posts"]:
                raise ActionError("每条线需要 id 和至少一个岗位")
            if not isinstance(L["id"], str) or L["id"] != L["id"].strip() or "#" in L["id"] or len(L["id"]) > 20:
                raise ActionError("线 id 必须是不含 # 的非空字符串，最多 20 字")
            if L["id"] in seen:
                raise ActionError(f"线 {L['id']} 重复")
            seen.add(L["id"])
            if L.get("walk") is not None:
                try:
                    walk = float(L["walk"])
                except (TypeError, ValueError) as exc:
                    raise ActionError("路程必须是非负数字") from exc
                if isinstance(L["walk"], bool) or not math.isfinite(walk) or walk < 0:
                    raise ActionError("路程必须是非负数字")
            if "standby" in L and not isinstance(L["standby"], bool):
                raise ActionError("备用线标记必须是布尔值")
            for x in L["posts"]:
                if not isinstance(x, dict) or not x.get("name"):
                    raise ActionError(f"{L['id']} 线有岗位缺少名称")
                if not isinstance(x["name"], str) or not x["name"].strip() or len(x["name"]) > 50:
                    raise ActionError("岗位名称必须是非空字符串，最多 50 字")
                if x.get("seven") is not None and (isinstance(x["seven"], bool) or not isinstance(x["seven"], int) or x["seven"] < 1):
                    raise ActionError("7 点岗顺序必须是正整数")
                for key in ("openAt", "closeAt"):
                    if x.get(key):
                        action_time(x[key])
        cfg.lines_json = _dumps(lines)
    if settings is not None:
        if not isinstance(settings, dict):
            raise ActionError("参数格式不正确")
        merged = settings_of(cfg)
        for key, value in settings.items():
            if key not in E.DEFAULT_SETTINGS:
                continue
            default = E.DEFAULT_SETTINGS[key]
            if isinstance(default, str):
                action_time(value)
                merged[key] = str(value)
            else:
                try:
                    number = float(value)
                    if isinstance(value, bool) or not math.isfinite(number) or number < 0 or not number.is_integer():
                        raise ValueError()
                    merged[key] = type(default)(number)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ActionError(f"参数 {key} 必须是非负整数") from exc
                if key == 'mealThreshold' and number < E.MIN_MEAL_SHIFT_MINUTES:
                    raise ActionError('用餐工时门槛至少为390分钟')
        if merged['mealReserveMinPeople'] < 1:
            raise ActionError('允许自选吃饭所需的储备人数至少为1人')
        saved = {k: v for k, v in merged.items() if v != E.DEFAULT_SETTINGS.get(k)}
        prior = json.loads(cfg.settings_json or '{}')
        if ROTATION_CONTACTS_SETTING in prior:
            saved[ROTATION_CONTACTS_SETTING] = prior[ROTATION_CONTACTS_SETTING]
        cfg.settings_json = _dumps(saved)
    cfg.updated_by_id = employee_id
    cfg.updated_at = datetime.now()
