"""轮岗运行：时钟、名单、当天状态的加载与保存、后台推进、待办同步、页面数据。

所有修改当天状态的操作都在 RUNTIME.lock 内进行；后台线程每秒按时钟推进一次。
时间一律用“当天零点起的分钟数”；推进时按 STEP 分钟一步调用引擎 tick，快进时规则也按顺序生效。
"""
from __future__ import annotations

import json
import hashlib
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
from app.rotation.roster import classify, fmt, hm
from app.rotation.namespace import tables_for, runtime_for, runtime_for_row
from app.v2_database import SessionLocal


logger = logging.getLogger(__name__)

STEP = 0.25
DAY_END = 24 * 60
ActionError = E.ActionError


class ConfirmationConflict(ActionError):
    """The arrangement shown at confirmation time is no longer current."""


class Runtime:
    def __init__(self):
        self.lock = threading.RLock()
        self.version = 0
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

def load_config(db: Session, attraction_id: int) -> RotationConfig:
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
        db.add(cfg)
        db.flush()
    elif cfg not in db.dirty:
        # Dependencies or a caller may have loaded this row before taking the lock.
        db.refresh(cfg)
    return cfg


def settings_of(cfg: RotationConfig) -> dict:
    saved = json.loads(cfg.settings_json or "{}")
    return {**E.DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in E.DEFAULT_SETTINGS}}


def lines_of(cfg: RotationConfig) -> list[dict]:
    return json.loads(cfg.lines_json)


def clock_state(cfg: RotationConfig) -> dict:
    return json.loads(cfg.clock_json or '{"mode": "real"}')


def clock_now(cfg: RotationConfig) -> tuple[str, float]:
    """(日期, 分钟)。模拟时钟按倍速走，最多走到当天 24:00。"""
    state = clock_state(cfg)
    if state.get("mode") != "sim":
        now = datetime.now()
        return now.date().isoformat(), now.hour * 60 + now.minute + now.second / 60
    minute = float(state["minute"])
    if not state.get("paused"):
        minute += (time.time() - float(state["real_ts"])) * float(state.get("speed", 1)) / 60
    return state["date"], min(minute, DAY_END)


def set_sim_clock(cfg: RotationConfig, day: str, minute: float, *, speed: float | None = None, paused: bool | None = None) -> None:
    state = clock_state(cfg)
    cfg.clock_json = _dumps({
        "mode": "sim",
        "date": day,
        "minute": min(float(minute), DAY_END),
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


def roster_for(db: Session, attraction_id: int, day: str) -> dict | None:
    """把某天的名单转成引擎使用的结构。"""
    RotationRosterEntry = tables_for(db).RotationRosterEntry
    upload = roster_upload_for(db, attraction_id, day)
    if not upload:
        return None
    rows = db.query(RotationRosterEntry).filter(RotationRosterEntry.upload_id == upload.id, RotationRosterEntry.work_date == day).all()
    hours = json.loads(upload.hours_json or "{}")
    return {
        "people": {r.employee_no: {"pid": r.employee_no, "name": r.name, "type": r.person_type, "mark": r.mark} for r in rows},
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
        people.append({"employee_no": no, "name": roster["people"][no]["name"], "cell": cell, "kind": info["kind"], "label": info.get("label", "")})
    return {
        "date": day,
        "available": True,
        "scope": upload.scope,
        "file_name": upload.file_name,
        "hours": roster["hours"].get(day),
        "people": people,
    }


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


def write_duties(db: Session, attraction_id: int, day: str, plan: dict) -> None:
    RotationDuty = tables_for(db).RotationDuty
    db.query(RotationDuty).filter(RotationDuty.attraction_id == attraction_id, RotationDuty.work_date == day).delete(synchronize_session=False)
    for kind, key in (("push7", "push7"), ("lost", "lost")):
        for no in dict.fromkeys(plan.get(key, [])):
            db.add(RotationDuty(attraction_id=attraction_id, work_date=day, employee_no=no, kind=kind))


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
        if hasattr(actor, "kind"):
            actor_type, actor_id, actor_name = ('employee' if getattr(actor,'formal',False) and getattr(actor,'entered_by',None) else 'adjustment' if getattr(actor,'adjustment',False) else actor.kind), actor.actor_id, actor.name
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
        self.db.add(RotationEvent(
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
        ))

    def segment(self, pid, line, start, end):
        RotationSegment = tables_for(self.db).RotationSegment
        self.db.add(RotationSegment(
            attraction_id=self.attraction_id, work_date=self.day, employee_no=pid, line=line,
            start_min=start, end_min=end, minutes=max(0.0, end - start),
        ))
        self._week = None

    def week_minutes(self):
        if self._week is None:
            self.db.flush()
            self._week = week_minutes(self.db, self.attraction_id, self.day)
        return self._week


# ---------------------------------------------------------------- 当天状态

def day_row(db: Session, attraction_id: int, day: str) -> RotationDay | None:
    RotationDay = tables_for(db).RotationDay
    row = db.query(RotationDay).filter(RotationDay.attraction_id == attraction_id, RotationDay.work_date == day).first()
    if row is not None and row not in db.dirty:
        db.refresh(row)
    return row


def day_state(row: RotationDay) -> dict:
    """Return a private copy; engines must never mutate the committed cache."""
    RUNTIME = runtime_for_row(row)
    key = (row.attraction_id, row.work_date)
    cached = RUNTIME.cache.get(key)
    if cached and cached[0] == row.version:
        return deepcopy(cached[1])
    return json.loads(row.state_json)


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


def engine_for(db: Session, cfg: RotationConfig, row: RotationDay, state: dict) -> tuple[E.Engine, DbHooks]:
    hooks = DbHooks(db, row.attraction_id, row.work_date)
    from app.rotation import workflow as W
    eng = E.Engine(state, settings_of(cfg), hooks)
    eng._workflow_initialized = W.initialize(eng)
    return eng, hooks


def advance(db: Session, cfg: RotationConfig, row: RotationDay, state: dict, target: float) -> bool:
    """把运行中的当天推进到 target 分钟。"""
    if state.get("status") != "live":
        return False
    eng, hooks = engine_for(db, cfg, row, state)
    previous_tick = float(state.get("lastTick", target))
    t = previous_tick
    if t > target:
        t = target
    changed = bool(getattr(eng, '_workflow_initialized', False))
    while True:
        t = min(t + STEP, target) if t < target else target
        hooks.now = t
        step_changed=eng.tick(t)
        # Forecast boundaries also need a live-board event even when no post moves.
        if state.get('cooperativeEntry') and any(p['state'] == 'notyet' and
                any(t - STEP < p['start'] - lead <= t and previous_tick < p['start'] - lead
                    for lead in (30, 10)) for p in eng.P.values()):
            step_changed = True
        from app.rotation.cooperation import reconcile
        step_changed = reconcile(eng, t) or step_changed
        if step_changed:
            from app.rotation.daily import capture_snapshot
            capture_snapshot(db,row.attraction_id,state,settings_of(cfg),t)
        changed = step_changed or changed
        if eng.should_end(t):
            eng.end_day(t)
            changed = True
            break
        if t >= target:
            break
    state["lastTick"] = t
    if changed:
        sync_notices(db, row.attraction_id, state, eng, t)
    if changed or t != previous_tick:
        # Even a quiet tick must retain its cursor once private copies replace
        # the former shared mutable cache; only visible changes notify clients.
        save_day(db, row, state, notify=changed)
    return changed


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
                advance(db, cfg, row, day_state(row), minute)


_ticker_started = False
_ticker_stop = threading.Event()


def start_ticker(attraction_id_loader) -> None:
    """启动后台推进线程（每秒一次）。attraction_id_loader() 返回要推进的景点圈 id。"""
    global _ticker_started
    if _ticker_started:
        return
    _ticker_started = True

    def run():
        while not _ticker_stop.wait(1.0):
            try:
                attraction_id = attraction_id_loader()
                if attraction_id:
                    tick_attraction(attraction_id)
            except Exception:
                logger.exception("sandbox rotation tick failed")
            try:
                from app.rotation.namespace import FORMAL_TABLES
                with SessionLocal() as db:
                    formal_ids = [r[0] for r in db.query(FORMAL_TABLES.RotationConfig.attraction_id).all()]
            except Exception:
                logger.exception("formal rotation scopes unavailable")
                continue
            for formal_id in formal_ids:
                try:
                    tick_attraction(formal_id, formal=True)
                except Exception:
                    logger.exception("formal rotation tick failed for attraction %s", formal_id)

    threading.Thread(target=run, name="rotation-ticker", daemon=True).start()


def _rotation_attraction_id() -> int | None:
    from app.rotation.access import ROTATION_ATTRACTION_NAME
    from app.v2_models import Attraction

    db = SessionLocal()
    try:
        row = db.query(Attraction.id).filter(Attraction.name == ROTATION_ATTRACTION_NAME).first()
        return row[0] if row else None
    finally:
        db.close()


def start_rotation_ticker() -> None:
    cached: list[int] = []

    def attraction_id() -> int | None:
        if not cached:
            found = _rotation_attraction_id()
            if found:
                cached.append(found)
        return cached[0] if cached else None

    start_ticker(attraction_id)


# ---------------------------------------------------------------- 待办（测试阶段只在轮岗页显示）

def _go_text(eng: E.Engine, a: dict) -> str:
    text = f"{fmt(a['departAt'])} 出发去 {a['line']} 线"
    target = eng.P.get(a.get("target")) if a.get("target") else None
    if a.get("mode") == "push7" and target:
        text += f"，推 7 点：替换 {target['name']}（他下来休息）"
    elif a.get("mode") == "chain" and target:
        text += f"，{a.get('why') or '推下班'}：从入口岗推进，替换 {target['name']}"
    return text


def desired_notices(eng: E.Engine, now: float) -> dict:
    """每个人此刻应有的待办：{(工号, slot): (kind, title, body)}。"""
    out = {}
    if eng.d.get("status") != "live":
        return out
    taken = eng.targeted()
    early = eng.S["departEarly"]
    for p in eng.P.values():
        no, st, a = p["pid"], p.get("state"), p.get("assign")
        step = None
        if st == "walkback":
            if p.get("after") == "done":
                step = ("arrive", "请确认已到休息室", "在个人待办或大屏确认，今天轮岗即将结束，确认后即可下班。")
            elif p.get("after") == "closing":
                step = ("arrive", "请确认已到休息室", "在个人待办或大屏确认，已闭园，确认后转为闭园后工作。")
            else:
                reason = p.get("downReason") or "推岗"
                step = ("arrive", "请确认已到休息室", f"你已下线（{reason}），在个人待办或大屏确认后系统安排休息或吃饭。")
        elif p.get('role')=='op' and st in ('op','notyet'):
            if not eng.d.get('taskWorkflow') or now>=p['start']:
                step = ('arrive', '可确认去休息', '到休息室后确认，按实际确认时间开始休息。')
        elif st == "pending":
            step = ("arrive", "请确认已到休息室", "OP 阶段结束，在个人待办或大屏确认后转入休息区开始轮岗。")
        elif st in ("rest", "meal"):
            word = "吃饭" if st == "meal" else "饭后剩余休息时间" if p.get("breakKind") == "meal_rest" else "休息"
            body = "正常轮岗须等倒计时结束；现场强制安排须填写原因并预览确认。" if p.get("breakKind") == "meal_rest" else _go_text(eng, a) if a else "去向安排中，请留意大屏。"
            step = ("rest", f"{word}至 {fmt(p['readyAt'])}", body)
        elif st == "ready":
            if p.get('entryHold'):
                step = ('entry_return', '请本人到场后重新领取安排', '同行任务已调整；本提示不代表缺勤，请到场后在本人待办确认。')
                if p.get('taskTimeout'):
                    timeout=p['taskTimeout'];receiver=eng.P.get(timeout.get('transferredTo'),{}).get('name')
                    from app.rotation.workflow import locate
                    _,_,post=locate(eng,timeout.get('entryKey'))
                    step=('entry_return', f"{timeout['line']} · {post['name'] if post else '原上岗'}任务超时", ('已转由 '+receiver+' 执行。' if receiver else '等待下一位可分配人员承接。')+'本人确认知晓后再安排新任务。')
            elif a and now >= max(a["departAt"] - (0 if a.get('taskId') else early), a.get("notBefore", 0)):
                step = ("depart", f"现在出发去 {a['line']} 线", _go_text(eng, a) + "。在个人待办或休息室大屏确认「去轮岗」。")
            elif a:
                step = ("rest", f"{fmt(a['departAt'])} 出发去 {a['line']} 线", _go_text(eng, a))
            else:
                step = ("rest", "正在安排去向", "请留意休息室大屏。")
        elif st == "onpost" and no in taken:
            replacer = eng.P[taken[no]]
            ra = replacer["assign"]
            if ra.get("mode") == "chain":
                arrive = ra["departAt"] + eng.walk(ra["line"])
                word = "出圈" if ra.get("why") == "推出圈" else "下班"
                step = ("leaving", f"准备{word}", f"约 {fmt(arrive)} {replacer['name']} 从入口岗推进来，把你推{word}。")
        if step:
            out[(no, "step")] = step
        if st not in ("done", "excluded"):
            if "推7点" in p.get("flags", []):
                out[(no, "duty:push7")] = ("duty", "今天负责推 7 点", "07:15 上班后到 7 点岗位替换 07:00 班的人。")
            if "送失物" in p.get("flags", []):
                out[(no, "duty:lost")] = ("duty", "今天负责送失物", "")
    return out


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
    if a.get('taskId'):
        from app.rotation.workflow import task_view
        task = task_view(eng,p)
        if task:
            av.update(entryPost=task['entryPost'], deadlineAt=task['deadlineAt'],
                      transferredFromName=task['transferredFromName'])
            target=eng.P.get(a.get('target'))
            target_line,index=eng.find_post(a.get('target')) if target else (None,None)
            if target:av.update(targetName=target['name'],targetLine=target_line['id'] if target_line else None,
                                targetPost=target_line['posts'][index]['name'] if target_line else None)
            return av
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


def confirmation_token(eng: E.Engine, p: dict, action: str) -> str:
    # Bind to this person's step and the displayed route, not the global clock
    # or version: unrelated people and countdown ticks must not invalidate it.
    context = {"epoch": eng.d.get("confirmationEpoch"), "date": eng.d["date"],
               "pid": p["pid"], "action": action, "sequence": p.get("confirmationSequence", 0),
               "closed": eng.d.get("closed", False)}
    if action in ("depart", "entry_adjust", "group_depart"):
        from app.rotation.workflow import task_for
        task = task_for(eng,p)
        if task:
            context.update(task=task['id'],revision=task['revision'],expected=task['expected'])
            return hashlib.sha256(json.dumps(context,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        a = assignment_view(eng, p)
        context.update(assign=a, readyAt=p.get("readyAt"), breakStart=p.get("breakStart"))
        from app.rotation.cooperation import plan
        group = plan(eng, p, float(eng.d.get('lastTick', p['start'])))
        context['entryPlan'] = {'id': group['id'], 'soloRoutes': group['soloRoutes'],
                                'soloBlocked': group['soloBlocked']} if group else None
        context["posts"] = [{"name": x["name"], "open": x.get("open"), "occ": x.get("occ"), "hold": x.get("hold")}
                            for x in eng.line(a["line"])["posts"]] if a else []
    elif action == 'entry_return':
        context['hold'] = p.get('entryHold')
    else:
        context.update(state=p.get("state"), walkbackSince=p.get("walkbackSince"),
                       anchor=p.get("arriveAnchor"), after=p.get("after"), start=p["start"], end=p["end"])
    return hashlib.sha256(json.dumps(context, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


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


def person_view(eng: E.Engine, p: dict, now: float, taken: dict) -> dict:
    from app.rotation.cooperation import plan, simulate, upcoming, upcoming_shift
    projection = getattr(eng, '_read_projection', None)
    person_key = (p['pid'], float(now))
    if projection is not None and person_key in projection.setdefault('people', {}):
        return deepcopy(projection['people'][person_key])
    if not hasattr(eng, '_upcoming_routes'):
        if projection is not None and 'upcoming' in projection:
            eng._upcoming_routes = projection['upcoming']
        else:
            eng._upcoming_routes = upcoming(eng, now)
            if projection is not None:
                projection['upcoming'] = eng._upcoming_routes
    group = plan(eng, p, now)
    av = assignment_view(eng, p)
    if group and av:
        own=next((member for member in group['members'] if member['pid']==p['pid']),None)
        if own and own.get('entryPost'):
            av['entryPost']=own['entryPost']
    routes = group['routes'] if group else []
    departure_blocked = group['blocked'] if group else None
    if av and not eng.d.get('taskWorkflow') and not group and not av.get('taskId') and p.get('state') in ('rest', 'ready', 'meal'):
        try:
            routes = simulate(eng, [p], now)
            own_route = next((r for r in routes if r['pid'] == p['pid']), None)
            if own_route:
                av['entryPost'] = own_route['to']
        except E.ActionError as exc:
            departure_blocked = str(exc)
    a = p.get("assign")
    L, i = eng.find_post(p["pid"]) if p.get("state") == "onpost" else (None, None)
    preparing = None
    if p["pid"] in taken:
        ra = eng.P[taken[p["pid"]]].get("assign") or {}
        preparing = {"push7": "推7点", "chain": ra.get("why") or "推下班"}.get(ra.get("mode"))
    from app.rotation import workflow as W
    task = W.task_view(eng,p)
    if task:
        routes = task['routes']
        departure_blocked = None if W.valid(eng,W.task_for(eng,p)) else '接替路线已变化，请等待最新安排'
    timeout=None
    if p.get('taskTimeout'):
        _,_,timeout_post=W.locate(eng,p['taskTimeout'].get('entryKey'))
        timeout={**p['taskTimeout'],'entryPost':timeout_post['name'] if timeout_post else '原上岗',
                 'transferredToName':eng.P.get(p['taskTimeout'].get('transferredTo'),{}).get('name')}
    result = {
        "pid": p["pid"], "name": p["name"], "state": p.get("state"), "role": p.get("role"),
        "label": p.get("label", ""), "note": p.get("note", ""), "start": p["start"], "end": p["end"],
        "line": p.get("line"), "post": L["posts"][i]["name"] if L else None, "postIndex": i,
        "lineStart": p.get("lineStart"), "readyAt": p.get("readyAt"), "breakKind": p.get("breakKind"),
        "assign": av, "flags": p.get("flags", []), "tags": p.get("tags", []), "away": p.get("away"),
        "departureRoutes": routes, "departureBlocked": departure_blocked,
        "assignmentLocked": eng.assignment_locked(p, now),
        "entryGroup": group, "entryHold": p.get('entryHold'),
        "interruptedBreak": p.get('interruptedBreak'), "leaveReason": p.get('leaveReason'),
        "postHold": (L['posts'][i].get('hold') or L['posts'][i].get('hold101')) if L else None,
        "upcomingShift": upcoming_shift(p, now),
        "previewAssign": eng._upcoming_routes.get(p['pid']),
        "walkbackSince": p.get("walkbackSince"), "arriveAt": p.get("arriveAt"), "ate": p.get("ate"),
        "mealEligible": eng.meal_eligible(p), "downReason": p.get("downReason"), "after": p.get("after"),
        "mealOptions": eng.meal_options(p, now),
        "visited": p.get("visited", []), "mark": p.get("mark", ""), "absences": p.get("absences", []),
        "preparing": preparing,
        "continuousStart":p.get('continuousStart'),"continuousMinutes":W.continuous_minutes(p,now),
        "task":task,"taskTimeout":timeout,
        "waitingReplacement":p.get('state')=='onpost' and now>=p['end'],
        "scheduledRestStart": p['start'] if p.get('state') == 'notyet' and eng.opening_rest_candidate(p) else None,
        "confirmationTokens": {action: confirmation_token(eng, p, action) for action in
                               (["entry_return"] if p.get('entryHold') else ["arrive"] if p.get("state") in ("walkback", "pending", "op") or p.get('role')=='op' and p.get('state')=='notyet' else
                                (["depart", "entry_adjust", "group_depart"] if group else ["depart"]) if p.get("assign") and p.get("state") in ("rest", "ready", "meal") else [])},
        "canDepart": bool(not departure_blocked and p.get("state") == "ready" and a and now >= (p.get("readyAt") or now) - 0.01
                          and (not W.enabled(eng) or task)
                          and (not task or task['allowedAt']-1e-8<=now<=task['deadlineAt']+1e-8)
                          and now >= a.get("notBefore", 0) - 0.01
                          and now >= a["departAt"] - (0 if group else eng.S["departEarly"]) - 0.01),
    }

    if projection is not None:
        projection['people'][person_key] = deepcopy(result)
    return result


def lines_view(eng: E.Engine) -> list[dict]:
    return [{
        "id": L["id"], "group": L.get("group"), "active": L.get("active"), "standby": L.get("standby"),
        "walk": eng.walk(L["id"]),
        "posts": [{"name": x["name"], "open": x.get("open"), "occ": x.get("occ"),
                   "key":x.get('key'),"occName": eng.P.get(x.get("occ"), {}).get("name"), "since": x.get("since"), "hold": x.get("hold") or x.get('hold101'),
                   "hold101":bool(x.get('hold101')),"hold101Role":x.get('hold101Role'),
                   "manualHold":x.get('hold'),
                   "continuousStart":eng.P.get(x.get('occ'),{}).get('continuousStart'),
                   "openAt": x.get("openAt"), "closeAt": x.get("closeAt"), "closeMode": x.get("closeMode"), "opened": x.get("opened"), "seven": x.get("seven")} for x in L["posts"]],
    } for L in eng.d["lines"]]


def live_view(db: Session, cfg: RotationConfig, row: RotationDay, now: float) -> dict:
    state = day_state(row)
    if "confirmationEpoch" not in state:
        save_day(db, row, state, notify=False)
    projection = _read_projection_cache(db, cfg, row, now)
    if 'live' in projection:
        return deepcopy(projection['live'])
    eng, _ = engine_for(db, cfg, row, state)
    eng._read_projection = projection
    taken = eng.targeted() if state.get("status") == "live" else {}
    result = {
        "date": state["date"], "status": state["status"],
        "lines": lines_view(eng),
        "persons": [person_view(eng, p, now, taken) for p in state["persons"].values()],
        "off": state.get("off", []),
        "alerts": eng.alerts(now) if state["status"] == "live" else [],
        "notices": state.get("notices", [])[-10:],
        "plan": state["plan"],
        "draftAlerts": state.get("draftAlerts", []),
        "openTime": state.get("openTime"), "closeAt": state.get("closeAt"), "closed": state.get("closed", False),
        "taskWorkflow":state.get('taskWorkflow'),"hold101Active":state.get('hold101Active',False),
        "hold101Recent":state.get('hold101Recent',[]),
    }

    projection['live'] = deepcopy(result)
    return result


@transactional
def ensure_prearranged_day(db: Session, cfg: RotationConfig, attraction_id: int, day: str, minute: float) -> None:
    """有班表时04:00生成预排；开始时间前仅展示岗位，不计时。"""
    RUNTIME = runtime_for(db)
    with RUNTIME.lock:
        row=day_row(db,attraction_id,day)
        if minute < 240 or (row and (row.status != 'draft' or not day_state(row).get('dailyAutoStart'))) or not roster_for(db, attraction_id, day):
            return
        if row is None:
            do_draft_action(db, attraction_id, 'system', {"action": "draft_generate", "date": day})
            row = day_row(db, attraction_id, day)
        state = day_state(row)
        upload=roster_upload_for(db,attraction_id,day)
        if state.get('dailyAutoStart') and upload and state.get('rosterUploadId')!=upload.id:
            # Never run a superseded roster or silently overwrite manual adjustments.
            return
        remember_initial_draft(state, cfg)
        eng, hooks = engine_for(db, cfg, row, state)
        hooks.now = 240
        eng.publish(240, 'system')
        state["lastTick"] = 240
        write_duties(db, attraction_id, day, state["plan"])
        save_day(db, row, state)
        advance(db, cfg, row, state, minute)


@transactional
def board_payload(db: Session, attraction_id: int) -> dict:
    RotationDay = tables_for(db).RotationDay
    RotationRosterEntry = tables_for(db).RotationRosterEntry
    RUNTIME = runtime_for(db)
    cfg = load_config(db, attraction_id)
    day, minute = clock_now(cfg)
    ensure_prearranged_day(db, cfg, attraction_id, day, minute)
    row = day_row(db, attraction_id, day)
    out = {
        "clock": clock_payload(cfg),
        "version": RUNTIME.version,
        "settings": settings_of(cfg),
        "lines": lines_of(cfg),
        "roster": roster_summary(db, attraction_id, day),
        "rosterDates": [d for (d,) in db.query(RotationRosterEntry.work_date).filter(RotationRosterEntry.attraction_id == attraction_id).distinct().order_by(RotationRosterEntry.work_date.asc()).all()],
        "days": [{"date": r.work_date, "status": r.status} for r in db.query(RotationDay).filter(RotationDay.attraction_id == attraction_id).order_by(RotationDay.work_date.desc()).limit(30).all()],
        "day": None,
        "draft": None,
    }
    if row and row.status in ("live", "ended"):
        out["day"] = live_view(db, cfg, row, minute)
    elif row and row.status == "draft":
        out["draft"] = day_state(row)
    return out


@transactional
def screen_payload(db: Session, attraction_id: int) -> dict:
    RUNTIME = runtime_for(db)
    cfg = load_config(db, attraction_id)
    day, minute = clock_now(cfg)
    ensure_prearranged_day(db, cfg, attraction_id, day, minute)
    row = day_row(db, attraction_id, day)
    out = {"clock": clock_payload(cfg), "version": RUNTIME.version, "day": None,
           "settings": {"departEarly": settings_of(cfg)["departEarly"]}}
    if row and row.status in ("live", "ended"):
        view = live_view(db, cfg, row, minute)
        view.pop("alerts", None)
        view.pop("plan", None)
        view.pop("draftAlerts", None)
        out["day"] = view
    return out


@transactional
def member_payload(db: Session, attraction_id: int, employee_no: str) -> dict:
    RotationNotice = tables_for(db).RotationNotice
    RotationSegment = tables_for(db).RotationSegment
    RUNTIME = runtime_for(db)
    cfg = load_config(db, attraction_id)
    day, minute = clock_now(cfg)
    ensure_prearranged_day(db, cfg, attraction_id, day, minute)
    row = day_row(db, attraction_id, day)
    notices = (
        db.query(RotationNotice)
        .filter(RotationNotice.attraction_id == attraction_id, RotationNotice.employee_no == employee_no, RotationNotice.work_date == day)
        .order_by(RotationNotice.status.desc(), RotationNotice.updated_at.desc(), RotationNotice.id.desc())
        .limit(30)
        .all()
    )
    week = week_minutes(db, attraction_id, day).get(employee_no, {})
    out = {
        "clock": clock_payload(cfg),
        "version": RUNTIME.version,
        "employee_no": employee_no,
        "settings": {"departEarly": settings_of(cfg)["departEarly"]},
        "person": None,
        "line": None,
        "confirmations": [],
        "week_minutes": week,
        "notices": [{
            "id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "status": n.status,
            "updated_at": n.updated_at.strftime("%H:%M:%S") if n.updated_at else None,
        } for n in notices],
    }
    if not row or row.status not in ("live", "ended"):
        return out
    state = day_state(row)
    if "confirmationEpoch" not in state:
        save_day(db, row, state, notify=False)
    p = state["persons"].get(employee_no)
    if not p:
        return out
    eng, _ = engine_for(db, cfg, row, state)
    eng._read_projection = _read_projection_cache(db, cfg, row, minute)
    out["person"] = person_view(eng, p, minute, eng.targeted() if state["status"] == "live" else {})
    out["confirmations"] = [r for r in reversed(list(state.get("confirmations", {}).values()))
                            if r["pid"] == employee_no][:20]
    lid = p.get("line") or (p.get("assign") or {}).get("line")
    if lid:
        L = eng.line(lid)
        out["line"] = {
            "id": L["id"],
            "posts": [{"name": x["name"], "open": x.get("open"), "occ": x.get("occ"),
                       "occName": state["persons"][x["occ"]]["name"] if x.get("occ") in state["persons"] else None,
                       "since": x.get("since"), "hold": x.get("hold")} for x in L["posts"]],
        }
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

SCREEN_ACTIONS = {"arrive", "depart", "entry_return", "entry_adjust"}
LIVE_ACTIONS = {
    "arrive", "depart", "entry_return", "entry_adjust", "undo", "post", "line", "away", "back", "reassign", "leave", "flag",
    "set_close", "fix_undo_depart", "fix_remove", "fix_place", "add_person", "plan_place", "move", "post_order", "post_hold",
}
LIVE_ACTIONS.update({'rename_post','hold101','restore_line'})
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
def do_live_action(db: Session, attraction_id: int, actor, body: dict) -> dict | None:
    RotationRosterEntry = tables_for(db).RotationRosterEntry
    RUNTIME = runtime_for(db)
    from app.rotation.adjustment import revalidate_actor
    revalidate_actor(db, actor)
    act = body.get("action")
    if not isinstance(act, str) or act not in LIVE_ACTIONS:
        raise ActionError("不支持的操作")
    if act=='move' and body.get('force') is True and not body.get('expected_snapshot'):
        raise ActionError('强制安排必须先预览影响并确认')
    if act not in {"post", "post_order", "line", "set_close",'rename_post','hold101','restore_line'}:
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
        if body.get('expected_snapshot'):
            from app.rotation.manual import fingerprint
            if body['expected_snapshot'] != fingerprint(state):
                raise ConfirmationConflict('安排已变化，请重新预览后提交')
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
        if row.status != "live":
            raise ActionError("今天的轮岗还没有发布")
        advance(db, cfg, row, state, now)
        if body.get('expected_snapshot') and body['expected_snapshot'] != fingerprint(state):
            raise ConfirmationConflict('安排已变化，请重新预览后提交')
        eng, hooks = engine_for(db, cfg, row, state)
        hooks.now = now
        from app.rotation import history as H, workflow as W
        checkpoint_before=H.snapshot(state)
        pid = body.get("pid")
        if pid is not None and pid not in state["persons"] and act != "add_person":
            raise ActionError("今天的名单里没有这个人")
        before = None
        if token is not None:
            p = eng.P[pid]
            token_action='group_depart' if act=='depart' and body.get('group_confirm') is True else act
            if token != confirmation_token(eng, p, token_action):
                raise ConfirmationConflict("轮岗安排已更新，请查看最新安排后重新确认")
            before = assignment_view(eng, p) if act == "depart" else None
        reason = str(body.get("reason") or "").strip()
        entry_result = None
        if act.startswith("fix_") and not reason:
            raise ActionError("更正需要填写原因")
        if act == 'rename_post':
            _require(body,'post_key','name')
            line,_,post=W.locate(eng,body['post_key'])
            if not post:raise ActionError('岗位已变化，请刷新')
            name=str(body['name']).strip()
            if not name or len(name)>40:raise ActionError('岗位名称须为1至40字')
            if any(x is not post and x['name']==name for x in line['posts']):raise ActionError('本线路已有同名岗位')
            old=post['name'];post['name']=name
            eng.h.log('rename_post',actor,None,line['id'],name,{'key':post['key'],'from':old,'to':name})
        elif act == 'hold101':
            on=action_boolean(body,'on')
            recent=W.toggle_101(eng,on,now,actor)
            state['hold101Recent']=recent if on else []
        elif act == 'restore_line':
            _require(body,'line','checkpoint','expected_snapshot')
            if body.get('notified') is not True:raise ActionError('请先核实或确认可立即通知相关人员')
            H.execute(db,eng,body['line'],body['checkpoint'],now,actor)
        elif act == "arrive":
            eng.act_arrive(pid, now, actor, meal_choice=body.get('meal_choice'))
        elif act == 'post_hold':
            _require(body, 'line', 'i')
            if body.get('expected_post') is not None and eng.post(body['line'], body['i'])[2]['name'] != body['expected_post']:
                raise ConfirmationConflict('岗位顺序已变化，请刷新后重新设置定岗')
            if len(reason) > 100:
                raise ActionError('定岗说明最多100字')
            eng.act_post_hold(body['line'], body['i'], pid, action_boolean(body, 'on'), reason, now, actor)
        elif act == "plan_place":
            _require(body, "line", "i")
            eng.act_plan_place(pid, body["line"], body["i"], now, actor)
        elif act == "depart":
            if state.get('cooperativeEntry'):
                from app.rotation.cooperation import confirm
                solo = body.get('single', False)
                if not isinstance(solo, bool):
                    raise ActionError('单人模式参数无效')
                group_confirm=body.get('group_confirm',False)
                if not isinstance(group_confirm,bool):
                    raise ActionError('整组确认参数无效')
                if group_confirm:
                    if not token or solo:
                        raise ActionError('整组确认须使用当前步骤标识，不能同时选择单人模式')
                    from app.rotation.cooperation import confirm_group
                    entry_result=confirm_group(eng,pid,now,actor,body.get('expected_plan'))
                else:
                    entry_result = confirm(eng, pid, now, actor, token, solo, body.get('missing'))
            else:
                eng.act_depart(pid, now, actor)
        elif act == 'entry_return':
            p = eng.P[pid]
            if not p.get('entryHold') or p['state'] not in ('ready', 'rest'):
                raise ActionError('当前没有待恢复的上岗安排')
            own_member=actor.kind=='member' and actor.member_employee_no==pid
            if actor.kind!='screen' and not own_member and (not getattr(actor, 'formal', False) or not actor.entered_by or actor.entered_by.employee_no != pid):
                raise ActionError('请本人确认已到场')
            p.pop('entryHold')
            if p.get('taskTimeout'):
                p['taskTimeout']['acknowledged']=True
            eng.h.log('entry_return', actor, pid, None, None, {})
        elif act == 'entry_adjust':
            if W.enabled(eng):
                raise ActionError('现为个人独立任务；未确认的超时任务由系统转接，请刷新待办')
            from app.rotation.cooperation import apply_adjustment
            entry_result = apply_adjustment(eng, pid, now, actor, body.get('missing'), body.get('expected_plan'))
        elif act == "undo":
            eng.act_undo(pid, now, actor)
        elif act in {"post", "move", "post_order"}:
            from app.rotation.manual import apply
            apply(eng,body,now,actor)
        elif act == "line":
            _require(body, "line")
            eng.act_line(body["line"], action_boolean(body, "active"), now, actor)
        elif act == "away":
            eng.act_away(pid, reason or None, now, actor)
        elif act == "back":
            eng.act_back(pid, now, actor)
        elif act == "reassign":
            _require(body, "line")
            eng.act_reassign(pid, body["line"], now, actor)
        elif act == "leave":
            eng.act_leave(pid, reason or "离岗", now, actor)
        elif act == "flag":
            _require(body, "flag")
            flag = body["flag"]
            if flag not in ("送失物",):
                raise ActionError("只能标记送失物")
            on = action_boolean(body, "on")
            eng.act_flag(pid, flag, on, actor)
            lost = state["plan"]["lost"]
            if on and pid not in lost:
                lost.append(pid)
            if not on and pid in lost:
                lost.remove(pid)
            write_duties(db, attraction_id, day, state["plan"])
        elif act == "set_close":
            _require(body, "close")
            action_time(body["close"])
            eng.act_set_close(str(body["close"]), now, actor)
        elif act == "fix_undo_depart":
            eng.act_fix_undo_depart(pid, reason, now, actor)
        elif act == "fix_remove":
            eng.act_fix_remove(pid, reason, now, actor)
        elif act == "fix_place":
            _require(body, "line", "i")
            eng.act_fix_place(pid, body["line"], body["i"], reason, now, actor)
        elif act == "add_person":
            _require(body, "pid", "start", "end")
            upload = roster_upload_for(db, attraction_id, day)
            entry = None
            if upload:
                entry = db.query(RotationRosterEntry).filter(RotationRosterEntry.upload_id == upload.id, RotationRosterEntry.employee_no == pid).first()
            if not entry:
                raise ActionError("名单里没有这个工号")
            start, end = action_time(body["start"]), action_time(body["end"])
            if end <= start:
                end += DAY_END
            eng.act_add_person({"pid": pid, "name": entry.name, "type": entry.person_type, "mark": entry.mark, "start": start, "end": end}, now, actor)
        if pid in eng.P and eng.P[pid].get('entryHold') and eng.P[pid]['state'] not in ('ready', 'rest', 'notyet'):
            eng.P[pid].pop('entryHold', None)
        result = None
        if act in SCREEN_ACTIONS:
            p = eng.P[pid]
            p["confirmationSequence"] = p.get("confirmationSequence", 0) + 1
            L, i = eng.find_post(pid)
            result = {"pid": pid, "action": act, "date": day, "minute": now,
                      "source": getattr(actor, "kind", "manager"), "state": p["state"],
                      "line": L["id"] if L else None, "post": L["posts"][i]["name"] if L else None,
                      "readyAt": p.get("readyAt"), "assignment": before,
                      "sequence": p["confirmationSequence"]}
            if W.enabled(eng):result['status']='executed'
            if token is not None:
                result["token"] = token
                state.setdefault("confirmations", {})[token] = result
            if entry_result:
                result['status'] = entry_result['status']
                result['group'] = entry_result.get('group')
                if entry_result['status'] == 'executed':
                    for other_pid, other_token in entry_result.get('tokens', {}).items():
                        old = state.get('confirmations', {}).get(other_token)
                        if other_pid != pid or other_token != token:
                            other = eng.P[other_pid]
                            ol, oi = eng.find_post(other_pid)
                            if old:
                                old.update(status='executed', state=other['state'], minute=now,
                                           line=ol['id'] if ol else None, post=ol['posts'][oi]['name'] if ol else None)
                            else:
                                other['confirmationSequence']=other.get('confirmationSequence',0)+1
                                state.setdefault('confirmations',{})[other_token]={
                                    'pid':other_pid,'action':'depart','date':day,'minute':now,
                                    'source':getattr(actor,'kind','manager'),'state':other['state'],
                                    'line':ol['id'] if ol else None,'post':ol['posts'][oi]['name'] if ol else None,
                                    'status':'executed','group':entry_result.get('group'),'token':other_token,
                                    'sequence':other['confirmationSequence'],'assignment':entry_result.get('assignments',{}).get(other_pid)}
        eng.tick(now)
        if act!='restore_line':H.record(db,eng,checkpoint_before,'resume101' if act=='hold101' and not state.get('hold101Active') else act,actor,now)
        state["lastTick"] = max(float(state.get("lastTick", now)), now)
        sync_notices(db, attraction_id, state, eng, now)
        save_day(db, row, state)
        if act=='hold101':return {'ok':True,'recent':state.get('hold101Recent',[])}
        return {"ok": True, "duplicate": False, "result": result} if token is not None else None


def remember_initial_draft(state: dict, cfg: RotationConfig) -> None:
    """Save the pristine pre-publication state once, without recursive snapshots."""
    if "initialDraft" not in state:
        state["initialDraft"] = json.loads(_dumps(state))
        state["initialSettings"] = settings_of(cfg)


@transactional
def do_draft_action(db: Session, attraction_id: int, actor, body: dict) -> None:
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
        if act == "draft_generate":
            if row and any(x.get('hold') for L in day_state(row).get('lines', []) for x in L['posts']):
                raise ActionError('当日还有定岗人员，请先取消定岗再重新生成班表')
            if row and row.status in ("live", "ended") and not body.get("force"):
                raise ActionError(f"{day} 已经发布过轮岗，不能重新生成")
            roster = roster_for(db, attraction_id, day)
            if not roster:
                raise ActionError(f"{day} 还没有上传名单")
            if row is not None:
                # 必须先清掉当日计时，避免重新预排仍被刚撤回的本周累计影响。
                for model in (RotationEvent, RotationSegment, RotationNotice, RotationDuty, RotationSnapshot):
                    db.query(model).filter(model.attraction_id == attraction_id, model.work_date == day).delete(synchronize_session=False)
            prior = day_state(row) if row is not None else {}
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
                                      duty_counts(db, attraction_id, day), week_minutes(db, attraction_id, day))
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
        plan = state["plan"]
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
                    if pid and plan[k][kk] == pid:
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
            hooks = DbHooks(db, attraction_id, day)
            hooks.now = now
            remember_initial_draft(state, cfg)
            eng = E.Engine(state, state["initialSettings"], hooks)
            from app.rotation.workflow import initialize
            initialize(eng)
            eng.publish(now, actor)
            state["lastTick"] = now
            write_duties(db, attraction_id, day, plan)
            sync_notices(db, attraction_id, state, eng, now)
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
        "id": r.id, "minute": r.minute, "time": fmt(r.minute), "actor": r.actor_name, "actor_type": r.actor_type,
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
        "events": [{"date": e.work_date, "time": fmt(e.minute), "type": e.event_type, "actor": e.actor_name, "line": e.line, "post": e.post,
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
        cfg.settings_json = _dumps({k: v for k, v in merged.items() if v != E.DEFAULT_SETTINGS[k]})
    cfg.updated_by_id = employee_id
    cfg.updated_at = datetime.now()
