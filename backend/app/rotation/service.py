"""轮岗运行：时钟、名单、当天状态的加载与保存、后台推进、待办同步、页面数据。

所有修改当天状态的操作都在 RUNTIME.lock 内进行；后台线程每秒按时钟推进一次。
时间一律用“当天零点起的分钟数”；推进时按 STEP 分钟一步调用引擎 tick，快进时规则也按顺序生效。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date as Date, datetime, timedelta

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
)
from app.rotation.roster import classify, fmt, hm
from app.v2_database import SessionLocal


logger = logging.getLogger(__name__)

STEP = 0.25
DAY_END = 24 * 60
ActionError = E.ActionError


class Runtime:
    def __init__(self):
        self.lock = threading.RLock()
        self.version = 0
        self.cache: dict[tuple[int, str], tuple[int, dict]] = {}

    def bump(self):
        self.version += 1


RUNTIME = Runtime()


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- 配置与时钟

def load_config(db: Session, attraction_id: int) -> RotationConfig:
    cfg = db.query(RotationConfig).filter(RotationConfig.attraction_id == attraction_id).first()
    if not cfg:
        cfg = RotationConfig(
            attraction_id=attraction_id,
            lines_json=_dumps(E.DEFAULT_LINES),
            settings_json=_dumps({}),
            clock_json=_dumps({"mode": "real"}),
        )
        db.add(cfg)
        db.flush()
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


def save_roster(db: Session, attraction_id: int, parsed: dict, *, scope: str, file_name: str, uploader_id: int | None, uploader_name: str, only_date: str | None = None) -> RotationRosterUpload:
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
        if hasattr(actor, "kind"):
            actor_type, actor_id, actor_name = actor.kind, actor.actor_id, actor.name
        else:
            actor_type, actor_id, actor_name = "system", None, "系统" if actor == "system" else str(actor)
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
    return db.query(RotationDay).filter(RotationDay.attraction_id == attraction_id, RotationDay.work_date == day).first()


def day_state(row: RotationDay) -> dict:
    key = (row.attraction_id, row.work_date)
    cached = RUNTIME.cache.get(key)
    if cached and cached[0] == row.version:
        return cached[1]
    state = json.loads(row.state_json)
    RUNTIME.cache[key] = (row.version, state)
    return state


def save_day(db: Session, row: RotationDay, state: dict) -> None:
    row.state_json = _dumps(state)
    row.status = state["status"]
    row.version += 1
    row.updated_at = datetime.now()
    RUNTIME.cache[(row.attraction_id, row.work_date)] = (row.version, state)
    RUNTIME.bump()


def engine_for(db: Session, cfg: RotationConfig, row: RotationDay, state: dict) -> tuple[E.Engine, DbHooks]:
    hooks = DbHooks(db, row.attraction_id, row.work_date)
    return E.Engine(state, settings_of(cfg), hooks), hooks


def advance(db: Session, cfg: RotationConfig, row: RotationDay, state: dict, target: float) -> bool:
    """把运行中的当天推进到 target 分钟。"""
    if state.get("status") != "live":
        return False
    eng, hooks = engine_for(db, cfg, row, state)
    t = float(state.get("lastTick", target))
    if t > target:
        t = target
    changed = False
    while True:
        t = min(t + STEP, target) if t < target else target
        hooks.now = t
        changed = eng.tick(t) or changed
        if eng.should_end(t):
            eng.end_day(t)
            changed = True
            break
        if t >= target:
            break
    state["lastTick"] = t
    if changed:
        sync_notices(db, row.attraction_id, state, eng, t)
        save_day(db, row, state)
    return changed


def end_stale_days(db: Session, cfg: RotationConfig, attraction_id: int, today: str) -> None:
    """时钟已经到了新的一天，前面还在运行的轮岗按 24:00 收尾。"""
    for row in db.query(RotationDay).filter(RotationDay.attraction_id == attraction_id, RotationDay.status == "live", RotationDay.work_date != today).all():
        state = day_state(row)
        advance(db, cfg, row, state, DAY_END)


def tick_attraction(attraction_id: int) -> None:
    with RUNTIME.lock:
        db = SessionLocal()
        try:
            cfg = load_config(db, attraction_id)
            day, minute = clock_now(cfg)
            end_stale_days(db, cfg, attraction_id, day)
            row = day_row(db, attraction_id, day)
            if row and row.status == "live":
                advance(db, cfg, row, day_state(row), minute)
            db.commit()
        finally:
            db.close()


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
            except Exception:  # 推进失败不能拖垮服务；下一秒重试
                logger.exception("rotation tick failed")

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
                step = ("arrive", "请到休息室大屏点「去休息」", "今天轮岗即将结束，点完即可下班。")
            elif p.get("after") == "closing":
                step = ("arrive", "请到休息室大屏点「去休息」", "已闭园，点完转为闭园后工作。")
            else:
                reason = p.get("downReason") or "推岗"
                step = ("arrive", "请到休息室大屏点「去休息」", f"你已下线（{reason}），到大屏确认后系统安排休息或吃饭。")
        elif st == "pending":
            step = ("arrive", "请到休息室大屏点「去休息」", "OP 阶段结束，点完转入休息区开始轮岗。")
        elif st in ("rest", "meal"):
            word = "吃饭" if st == "meal" else "休息"
            body = _go_text(eng, a) if a else "去向安排中，请留意大屏。"
            step = ("rest", f"{word}至 {fmt(p['readyAt'])}", body)
        elif st == "ready":
            if a and now >= a["departAt"] - early:
                step = ("depart", f"现在出发去 {a['line']} 线", _go_text(eng, a) + "。到休息室大屏点「去轮岗」。")
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

def person_view(eng: E.Engine, p: dict, now: float, taken: dict) -> dict:
    a = p.get("assign")
    av = None
    if a:
        av = dict(a)
        if a.get("target") in eng.P:
            av["targetName"] = eng.P[a["target"]]["name"]
    L, i = eng.find_post(p["pid"]) if p.get("state") == "onpost" else (None, None)
    preparing = None
    if p["pid"] in taken:
        ra = eng.P[taken[p["pid"]]].get("assign") or {}
        preparing = {"push7": "推7点", "chain": ra.get("why") or "推下班"}.get(ra.get("mode"))
    return {
        "pid": p["pid"], "name": p["name"], "state": p.get("state"), "role": p.get("role"),
        "label": p.get("label", ""), "note": p.get("note", ""), "start": p["start"], "end": p["end"],
        "line": p.get("line"), "post": L["posts"][i]["name"] if L else None, "postIndex": i,
        "lineStart": p.get("lineStart"), "readyAt": p.get("readyAt"), "breakKind": p.get("breakKind"),
        "assign": av, "flags": p.get("flags", []), "tags": p.get("tags", []), "away": p.get("away"),
        "walkbackSince": p.get("walkbackSince"), "arriveAt": p.get("arriveAt"), "ate": p.get("ate"),
        "mealEligible": eng.meal_eligible(p), "downReason": p.get("downReason"), "after": p.get("after"),
        "visited": p.get("visited", []), "mark": p.get("mark", ""), "absences": p.get("absences", []),
        "preparing": preparing,
        "canDepart": bool(p.get("state") == "ready" and a and now >= a["departAt"] - eng.S["departEarly"] - 0.01),
    }


def lines_view(eng: E.Engine) -> list[dict]:
    return [{
        "id": L["id"], "group": L.get("group"), "active": L.get("active"), "standby": L.get("standby"),
        "walk": eng.walk(L["id"]),
        "posts": [{"name": x["name"], "open": x.get("open"), "occ": x.get("occ"), "since": x.get("since"),
                   "openAt": x.get("openAt"), "opened": x.get("opened"), "seven": x.get("seven")} for x in L["posts"]],
    } for L in eng.d["lines"]]


def live_view(db: Session, cfg: RotationConfig, row: RotationDay, now: float) -> dict:
    state = day_state(row)
    eng, _ = engine_for(db, cfg, row, state)
    taken = eng.targeted() if state.get("status") == "live" else {}
    return {
        "date": state["date"], "status": state["status"],
        "lines": lines_view(eng),
        "persons": [person_view(eng, p, now, taken) for p in state["persons"].values()],
        "off": state.get("off", []),
        "alerts": eng.alerts(now) if state["status"] == "live" else [],
        "notices": state.get("notices", [])[-10:],
        "plan": state["plan"],
        "draftAlerts": state.get("draftAlerts", []),
        "openTime": state.get("openTime"), "closeAt": state.get("closeAt"), "closed": state.get("closed", False),
    }


def board_payload(db: Session, attraction_id: int) -> dict:
    cfg = load_config(db, attraction_id)
    day, minute = clock_now(cfg)
    row = day_row(db, attraction_id, day)
    out = {
        "clock": clock_payload(cfg),
        "version": RUNTIME.version,
        "settings": settings_of(cfg),
        "lines": lines_of(cfg),
        "roster": roster_summary(db, attraction_id, day),
        "day": None,
        "draft": None,
    }
    if row and row.status in ("live", "ended"):
        out["day"] = live_view(db, cfg, row, minute)
    elif row and row.status == "draft":
        out["draft"] = day_state(row)
    return out


def screen_payload(db: Session, attraction_id: int) -> dict:
    cfg = load_config(db, attraction_id)
    day, minute = clock_now(cfg)
    row = day_row(db, attraction_id, day)
    out = {"clock": clock_payload(cfg), "version": RUNTIME.version, "day": None}
    if row and row.status in ("live", "ended"):
        view = live_view(db, cfg, row, minute)
        view.pop("alerts", None)
        view.pop("plan", None)
        view.pop("draftAlerts", None)
        out["day"] = view
    return out


def member_payload(db: Session, attraction_id: int, employee_no: str) -> dict:
    cfg = load_config(db, attraction_id)
    day, minute = clock_now(cfg)
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
        "person": None,
        "line": None,
        "week_minutes": week,
        "notices": [{
            "id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "status": n.status,
            "updated_at": n.updated_at.strftime("%H:%M:%S") if n.updated_at else None,
        } for n in notices],
    }
    if not row or row.status not in ("live", "ended"):
        return out
    state = day_state(row)
    p = state["persons"].get(employee_no)
    if not p:
        return out
    eng, _ = engine_for(db, cfg, row, state)
    out["person"] = person_view(eng, p, minute, eng.targeted() if state["status"] == "live" else {})
    lid = p.get("line") or (p.get("assign") or {}).get("line")
    if lid:
        L = eng.line(lid)
        out["line"] = {
            "id": L["id"],
            "posts": [{"name": x["name"], "open": x.get("open"), "occ": x.get("occ"),
                       "occName": state["persons"][x["occ"]]["name"] if x.get("occ") in state["persons"] else None,
                       "since": x.get("since")} for x in L["posts"]],
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

SCREEN_ACTIONS = {"arrive", "depart"}
LIVE_ACTIONS = {
    "arrive", "depart", "undo", "post", "line", "away", "back", "reassign", "leave", "flag",
    "set_close", "fix_undo_depart", "fix_remove", "fix_place", "add_person",
}
DRAFT_ACTIONS = {"draft_generate", "draft_set", "draft_list", "draft_close", "draft_role", "draft_discard", "publish"}


def _require(body: dict, *keys):
    for key in keys:
        if body.get(key) in (None, ""):
            raise ActionError(f"缺少参数：{key}")


def do_live_action(db: Session, attraction_id: int, actor, body: dict) -> None:
    act = body.get("action")
    if act not in LIVE_ACTIONS:
        raise ActionError("不支持的操作")
    with RUNTIME.lock:
        cfg = load_config(db, attraction_id)
        day, now = clock_now(cfg)
        row = day_row(db, attraction_id, day)
        if not row or row.status != "live":
            raise ActionError("今天的轮岗还没有发布")
        state = day_state(row)
        advance(db, cfg, row, state, now)
        eng, hooks = engine_for(db, cfg, row, state)
        hooks.now = now
        pid = body.get("pid")
        if pid is not None and pid not in state["persons"] and act != "add_person":
            raise ActionError("今天的名单里没有这个人")
        reason = str(body.get("reason") or "").strip()
        if act.startswith("fix_") and not reason:
            raise ActionError("更正需要填写原因")
        if act == "arrive":
            eng.act_arrive(pid, now, actor)
        elif act == "depart":
            eng.act_depart(pid, now, actor)
        elif act == "undo":
            eng.act_undo(pid, now, actor)
        elif act == "post":
            _require(body, "line", "i")
            eng.act_post(body["line"], int(body["i"]), bool(body.get("open")), now, actor)
        elif act == "line":
            _require(body, "line")
            eng.act_line(body["line"], bool(body.get("active")), now, actor)
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
            on = bool(body.get("on"))
            eng.act_flag(pid, flag, on, actor)
            lost = state["plan"]["lost"]
            if on and pid not in lost:
                lost.append(pid)
            if not on and pid in lost:
                lost.remove(pid)
            write_duties(db, attraction_id, day, state["plan"])
        elif act == "set_close":
            _require(body, "close")
            hm(str(body["close"]))
            eng.act_set_close(str(body["close"]), now, actor)
        elif act == "fix_undo_depart":
            eng.act_fix_undo_depart(pid, reason, now, actor)
        elif act == "fix_remove":
            eng.act_fix_remove(pid, reason, now, actor)
        elif act == "fix_place":
            _require(body, "line", "i")
            eng.act_fix_place(pid, body["line"], int(body["i"]), reason, now, actor)
        elif act == "add_person":
            _require(body, "pid", "start", "end")
            upload = roster_upload_for(db, attraction_id, day)
            entry = None
            if upload:
                entry = db.query(RotationRosterEntry).filter(RotationRosterEntry.upload_id == upload.id, RotationRosterEntry.employee_no == pid).first()
            if not entry:
                raise ActionError("名单里没有这个工号")
            start, end = hm(str(body["start"])), hm(str(body["end"]))
            if end <= start:
                end += DAY_END
            eng.act_add_person({"pid": pid, "name": entry.name, "type": entry.person_type, "mark": entry.mark, "start": start, "end": end}, now, actor)
        eng.tick(now)
        state["lastTick"] = max(float(state.get("lastTick", now)), now)
        sync_notices(db, attraction_id, state, eng, now)
        save_day(db, row, state)


def do_draft_action(db: Session, attraction_id: int, actor, body: dict) -> None:
    act = body.get("action")
    if act not in DRAFT_ACTIONS:
        raise ActionError("不支持的操作")
    with RUNTIME.lock:
        cfg = load_config(db, attraction_id)
        clock_day, now = clock_now(cfg)
        day = str(body.get("date") or clock_day)
        row = day_row(db, attraction_id, day)
        if act == "draft_generate":
            if row and row.status in ("live", "ended") and not body.get("force"):
                raise ActionError(f"{day} 已经发布过轮岗，不能重新生成")
            roster = roster_for(db, attraction_id, day)
            if not roster:
                raise ActionError(f"{day} 还没有上传名单")
            state = E.build_draft(day, roster, {"lines": lines_of(cfg)}, settings_of(cfg),
                                  duty_counts(db, attraction_id, day), week_minutes(db, attraction_id, day))
            if row is None:
                row = RotationDay(attraction_id=attraction_id, work_date=day, status="draft", state_json="{}", version=0)
                db.add(row)
                db.flush()
            else:
                # 重新生成：清掉这一天原有的运行记录（测试阶段）
                for model in (RotationEvent, RotationSegment, RotationNotice, RotationDuty):
                    db.query(model).filter(model.attraction_id == attraction_id, model.work_date == day).delete(synchronize_session=False)
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
            pids = [x for x in body.get("pids", []) if x]
            for x in pids:
                if x not in state["persons"]:
                    raise ActionError("名单里没有这个人")
            plan[body["kind"]] = pids
        elif act == "draft_close":
            _require(body, "close")
            hm(str(body["close"]))
            state["closeAt"] = str(body["close"])
        elif act == "draft_role":
            _require(body, "pid", "role")
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
            RUNTIME.cache.pop((attraction_id, day), None)
            RUNTIME.bump()
            return
        elif act == "publish":
            if day != clock_day:
                raise ActionError(f"只能发布当天（{clock_day}）的轮岗；测试时请先把测试时钟调到 {day}")
            hooks = DbHooks(db, attraction_id, day)
            hooks.now = now
            eng = E.Engine(state, settings_of(cfg), hooks)
            eng.publish(now, actor)
            state["lastTick"] = now
            write_duties(db, attraction_id, day, plan)
            sync_notices(db, attraction_id, state, eng, now)
        save_day(db, row, state)


# ---------------------------------------------------------------- 记录

def day_log(db: Session, attraction_id: int, day: str, limit: int = 500) -> list[dict]:
    rows = (
        db.query(RotationEvent)
        .filter(RotationEvent.attraction_id == attraction_id, RotationEvent.work_date == day)
        .order_by(RotationEvent.id.desc())
        .limit(limit)
        .all()
    )
    names = {}
    roster = roster_for(db, attraction_id, day)
    if roster:
        names = {no: p["name"] for no, p in roster["people"].items()}
    return [{
        "id": r.id, "minute": r.minute, "time": fmt(r.minute), "actor": r.actor_name, "actor_type": r.actor_type,
        "type": r.event_type, "employee_no": r.employee_no, "name": names.get(r.employee_no, r.employee_no),
        "line": r.line, "post": r.post, "detail": json.loads(r.detail_json) if r.detail_json else None,
    } for r in rows]


def person_record(db: Session, attraction_id: int, employee_no: str, day: str) -> dict:
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
    return {
        "employee_no": employee_no,
        "week": [start, end],
        "segments": [{"date": s.work_date, "line": s.line, "start": s.start_min, "end": s.end_min, "minutes": s.minutes} for s in segs],
        "totals": totals,
        "duties": [{"date": d.work_date, "kind": d.kind} for d in duties],
        "events": [{"date": e.work_date, "time": fmt(e.minute), "type": e.event_type, "actor": e.actor_name, "line": e.line, "post": e.post,
                    "detail": json.loads(e.detail_json) if e.detail_json else None} for e in events],
    }


def update_config(cfg: RotationConfig, lines, settings, employee_id: int | None) -> None:
    if lines is not None:
        if not isinstance(lines, list) or not lines:
            raise ActionError("线配置必须是非空列表")
        seen = set()
        for L in lines:
            if not isinstance(L, dict) or not L.get("id") or not isinstance(L.get("posts"), list) or not L["posts"]:
                raise ActionError("每条线需要 id 和至少一个岗位")
            if L["id"] in seen:
                raise ActionError(f"线 {L['id']} 重复")
            seen.add(L["id"])
            for x in L["posts"]:
                if not isinstance(x, dict) or not x.get("name"):
                    raise ActionError(f"{L['id']} 线有岗位缺少名称")
                for key in ("openAt", "closeAt"):
                    if x.get(key):
                        hm(str(x[key]))
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
                hm(str(value))
                merged[key] = str(value)
            else:
                try:
                    merged[key] = type(default)(value)
                except (TypeError, ValueError) as exc:
                    raise ActionError(f"参数 {key} 必须是数字") from exc
        cfg.settings_json = _dumps({k: v for k, v in merged.items() if v != E.DEFAULT_SETTINGS[k]})
    cfg.updated_by_id = employee_id
    cfg.updated_at = datetime.now()
    RUNTIME.bump()
