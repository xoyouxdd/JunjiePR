"""测试时钟（仅测试阶段使用，正式版删除本文件和 routers/rotation_test_clock.py）。

- 模拟时间：指定日期和时间，可暂停、调倍速；
- 快进：时间直接往后推若干分钟，规则按顺序逐步生效；
- 下一步：一直推进到出现需要在大屏点「去休息」或「去轮岗」的人为止，然后暂停。
"""
from __future__ import annotations

import math

from sqlalchemy.orm import Session

from app.rotation import service as S
from app.rotation.roster import fmt


MAX_SPEED = 120


def _require_sim(cfg) -> None:
    if S.clock_state(cfg).get("mode") != "sim":
        raise S.ActionError("请先开启模拟时间")


def _number(value, *, maximum: float, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise S.ActionError(f"{label}必须是数字") from exc
    if isinstance(value, bool) or not math.isfinite(number) or not 0 < number <= maximum:
        raise S.ActionError(f"{label}需大于 0 且不超过 {maximum:g}")
    return number


def pending_actions(state: dict, now: float, depart_early: float) -> list[str]:
    """此刻需要在大屏操作的人。"""
    out = []
    for p in state["persons"].values():
        st, a = p.get("state"), p.get("assign")
        if st in ("walkback", "pending"):
            out.append(f"{p['name']} 点「去休息」")
        elif st == "ready" and a and now >= max(a["departAt"] - depart_early, a.get("notBefore", 0), p.get("readyAt") or now) - 0.01:
            out.append(f"{p['name']} 点「去轮岗」（{a['line']} 线）")
    return out


def _live(db: Session, attraction_id: int, day: str):
    row = S.day_row(db, attraction_id, day)
    if row and row.status == "live":
        return row, S.day_state(row)
    return None, None


@S.transactional
def control(db: Session, attraction_id: int, body: dict) -> dict:
    action = body.get("action")
    with S.RUNTIME.lock:
        cfg = S.load_config(db, attraction_id)
        day, minute = S.clock_now(cfg)
        result: dict = {}
        if action == "reset":
            # 测试专用：原班表保留，清除当天所有预排调整和运行结果。
            S.set_sim_clock(cfg, day, 240, speed=1, paused=True)
            S.do_draft_action(db, attraction_id, 'test-reset', {"action": "draft_generate", "date": day, "force": True, "restore_initial": True})
            S.do_draft_action(db, attraction_id, 'test-reset', {"action": "publish", "date": day})
        elif action == "set":
            new_day = str(body.get("date") or day)
            try:
                S.Date.fromisoformat(new_day)
            except ValueError as exc:
                raise S.ActionError("日期格式需为 YYYY-MM-DD") from exc
            new_minute = S.action_time(body.get("time") or fmt(minute))
            speed = _number(body.get("speed", 1), maximum=MAX_SPEED, label="倍速")
            S.set_sim_clock(cfg, new_day, new_minute, speed=speed, paused=True)
        elif action == "real":
            cfg.clock_json = S._dumps({"mode": "real"})
        elif action in ("pause", "resume"):
            _require_sim(cfg)
            S.set_sim_clock(cfg, day, minute, paused=action == "pause")
        elif action == "speed":
            _require_sim(cfg)
            speed = _number(body.get("speed", 1), maximum=MAX_SPEED, label="倍速")
            S.set_sim_clock(cfg, day, minute, speed=speed)
        elif action == "jump":
            _require_sim(cfg)
            minutes = _number(body.get("minutes", 10), maximum=240, label="快进时长")
            target = min(minute + minutes, S.DAY_END)
            row, state = _live(db, attraction_id, day)
            if row:
                S.advance(db, cfg, row, state, target)
            S.set_sim_clock(cfg, day, target)
        elif action == "next":
            _require_sim(cfg)
            row, state = _live(db, attraction_id, day)
            if not row:
                raise S.ActionError("这一天的轮岗还没有发布")
            early = S.settings_of(cfg)["departEarly"]
            S.advance(db, cfg, row, state, minute)
            waiting = pending_actions(state, minute, early)
            if waiting:
                raise S.ActionError("请先处理：" + "；".join(waiting[:8]) + ("……" if len(waiting) > 8 else ""))
            t = minute
            while t < S.DAY_END and state.get("status") == "live":
                t = min(t + S.STEP, S.DAY_END)
                S.advance(db, cfg, row, state, t)
                waiting = pending_actions(state, t, early)
                if waiting:
                    break
            S.set_sim_clock(cfg, day, t, paused=True)
            result["waiting"] = waiting
        else:
            raise S.ActionError("不支持的测试时钟操作")
        S.mark_changed(db)
        result["clock"] = S.clock_payload(cfg)
        return result
