"""轮岗引擎规则的单元测试：只用引擎本身，不经过接口和数据库。"""
from __future__ import annotations

import pytest

from app.rotation.engine import DEFAULT_SETTINGS, ActionError, Engine, build_draft


DAY = "2026-10-04"


class Hooks:
    def __init__(self):
        self.events = []
        self.segments = []
        self.week = {}

    def log(self, typ, actor, pid, line, post, detail):
        self.events.append({"type": typ, "pid": pid, "line": line, "post": post, "detail": detail})

    def segment(self, pid, line, start, end):
        self.segments.append((pid, line, start, end))

    def week_minutes(self):
        return self.week

    def types(self, pid=None):
        return [e["type"] for e in self.events if pid is None or e["pid"] == pid]


def hm(text: str) -> float:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def two_lines(posts: int = 3) -> list[dict]:
    return [
        {"id": "A", "group": "A", "posts": [{"name": f"A{i}"} for i in range(posts)]},
        {"id": "B", "group": "B", "posts": [{"name": f"B{i}"} for i in range(posts)]},
    ]


def person(pid, start, end, absences=None):
    return {
        "pid": pid, "name": pid, "type": "", "mark": "", "start": hm(start), "end": hm(end), "note": "",
        "tags": [], "absences": absences or [], "role": "rotation", "label": "",
    }


def live_day(people, lines=None, now="09:00", close="21:30", **settings):
    """发布一个当天：所有人上班后都在池子里待出发，然后由测试摆放岗位。"""
    S = dict(DEFAULT_SETTINGS, **settings)
    day = {
        "date": DAY, "status": "draft", "closeAt": close, "persons": {p["pid"]: p for p in people}, "off": [],
        "lines": [dict(id=L["id"], group=L["group"], standby=False, walk=None,
                       posts=[dict(name=x["name"], openAt=None, closeAt=None, seven=x.get("seven")) for x in L["posts"]])
                  for L in (lines or two_lines())],
        "plan": {"seven": {}, "push7": [], "lost": [], "crew": {}},
    }
    hooks = Hooks()
    eng = Engine(day, S, hooks)
    # 发布时先不派人：等测试摆好岗位再推进
    eng._assign_all = lambda now: False
    eng.publish(hm(now), "test")
    del eng._assign_all
    return eng, hooks


def seat(eng, pid, line, index, since):
    """把某人放到岗位上，并设定他从何时开始在线。"""
    eng.act_fix_place(pid, line, index, "测试摆位", hm(since), "test")
    eng.P[pid]["lineStart"] = hm(since)
    eng.line(line)["posts"][index]["since"] = hm(since)


def occupants(eng, line):
    return [x["occ"] for x in eng.line(line)["posts"]]


# ---------------------------------------------------------------- 派人顺序

def test_longest_standing_exit_is_pushed_first_without_minimum_online_time():
    people = [person(p, "07:00", "21:00") for p in ("a0", "a1", "a2", "b0", "b1", "b2", "new")]
    eng, _ = live_day(people)
    for i, p in enumerate(("a0", "a1", "a2")):
        seat(eng, p, "A", i, "08:55")  # A 线出口岗只站了 5 分钟
    for i, p in enumerate(("b0", "b1", "b2")):
        seat(eng, p, "B", i, "08:20")  # B 线出口岗站了 40 分钟
    eng.tick(hm("09:00"))
    assign = eng.P["new"]["assign"]
    assert assign["line"] == "B" and assign["mode"] == "push"

    # 没有“上线满 40 分钟”的保护：只剩 A 线时，站了 5 分钟的人也会被推下来
    eng.act_line("B", False, hm("09:00"), "test")
    eng.tick(hm("09:00"))
    assert eng.P["new"]["assign"]["line"] == "A"
    eng.act_depart("new", hm("09:00"), "screen")
    eng.tick(hm("09:03"))
    assert occupants(eng, "A") == ["new", "a0", "a1"]
    assert eng.P["a2"]["state"] == "walkback"


def test_same_standing_minute_prefers_line_not_visited_today():
    people = [person(p, "07:00", "21:00") for p in ("a0", "a1", "a2", "b0", "b1", "b2", "new")]
    eng, _ = live_day(people)
    for i, p in enumerate(("a0", "a1", "a2")):
        seat(eng, p, "A", i, "08:20")
    for i, p in enumerate(("b0", "b1", "b2")):
        seat(eng, p, "B", i, "08:20")
    eng.P["new"]["visited"] = ["B"]
    eng.tick(hm("09:00"))
    assert eng.P["new"]["assign"]["line"] == "A"

    # 站岗时长相差 1 分钟以上，仍然推站得久的，哪怕当天去过
    eng.P["new"]["assign"] = None
    eng.P["b2"]["lineStart"] = hm("08:18")
    eng.tick(hm("09:00"))
    assert eng.P["new"]["assign"]["line"] == "B"


def test_same_standing_and_visits_compare_weekly_minutes():
    people = [person(p, "07:00", "21:00") for p in ("a0", "a1", "a2", "b0", "b1", "b2", "new")]
    eng, hooks = live_day(people)
    for i, p in enumerate(("a0", "a1", "a2")):
        seat(eng, p, "A", i, "08:20")
    for i, p in enumerate(("b0", "b1", "b2")):
        seat(eng, p, "B", i, "08:20")
    hooks.week = {"new": {"A": 300, "B": 60}}
    eng.tick(hm("09:00"))
    assert eng.P["new"]["assign"]["line"] == "B"


# ---------------------------------------------------------------- 推下班、推出圈

def test_push_off_shift_enters_from_entrance_and_pushes_the_leaver_out():
    people = [person("a0", "07:00", "21:00"), person("leaver", "07:00", "10:00"), person("a2", "07:00", "21:00"),
              person("b0", "07:00", "21:00"), person("b1", "07:00", "21:00"), person("b2", "07:00", "21:00"),
              person("new", "07:00", "21:00")]
    eng, hooks = live_day(people)
    seat(eng, "a0", "A", 0, "08:50")
    seat(eng, "leaver", "A", 1, "08:30")
    seat(eng, "a2", "A", 2, "08:10")
    for i, p in enumerate(("b0", "b1", "b2")):
        seat(eng, p, "B", i, "08:59")
    eng.tick(hm("09:00"))
    assign = eng.P["new"]["assign"]
    # 下班前 20 分钟到岗：09:40 到，路程 3 分钟，09:37 出发
    assert assign == {"line": "A", "mode": "chain", "target": "leaver", "departAt": hm("09:37"), "why": "推下班"}
    assert eng.targeted() == {"leaver": "new"}

    with pytest.raises(ActionError, match="还没到出发时间"):
        eng.act_depart("new", hm("09:30"), "screen")
    eng.act_depart("new", hm("09:36"), "screen")  # 可提前 1 分钟
    eng.tick(hm("09:39"))
    # 入口岗进，链条后移到要走的人为止；出口岗的人不动
    assert occupants(eng, "A") == ["new", "a0", "a2"]
    leaver = eng.P["leaver"]
    assert leaver["state"] == "done" and leaver["downReason"] == "推下班"
    assert "walkback" not in [e["detail"].get("reason") for e in hooks.events if e["pid"] == "leaver"]
    assert eng.P["a2"]["state"] == "onpost"


def test_push_out_of_circle_for_fixed_absence_then_returns_to_pool():
    ssei = {"start": hm("12:00"), "end": hm("13:00"), "label": "SSEI"}
    people = [person("a0", "07:00", "21:00"), person("out", "07:00", "21:00", [ssei]), person("a2", "07:00", "21:00"),
              person("b0", "07:00", "21:00"), person("b1", "07:00", "21:00"), person("b2", "07:00", "21:00"),
              person("new", "07:00", "21:00")]
    eng, _ = live_day(people, now="11:00")
    seat(eng, "a0", "A", 0, "10:50")
    seat(eng, "out", "A", 1, "10:40")
    seat(eng, "a2", "A", 2, "10:30")
    for i, p in enumerate(("b0", "b1", "b2")):
        seat(eng, p, "B", i, "10:59")
    eng.tick(hm("11:00"))
    assert eng.P["new"]["assign"]["why"] == "推出圈"
    assert eng.P["new"]["assign"]["departAt"] == hm("11:37")
    eng.act_depart("new", hm("11:37"), "screen")
    eng.tick(hm("11:40"))
    out = eng.P["out"]
    assert out["state"] == "away" and out["away"]["reason"] == "SSEI" and out["away"]["fixed"]
    assert occupants(eng, "A") == ["new", "a0", "a2"]
    eng.tick(hm("13:00"))
    assert out["state"] == "ready"


def test_fixed_absence_wins_even_without_replacement_and_the_gap_is_filled_first():
    ssei = {"start": hm("12:00"), "end": hm("13:00"), "label": "SSEI"}
    people = [person("a0", "07:00", "21:00"), person("out", "07:00", "21:00", [ssei]), person("a2", "07:00", "21:00"),
              person("b0", "07:00", "21:00"), person("b1", "07:00", "21:00"), person("b2", "07:00", "21:00"),
              person("late", "12:00", "21:00")]
    eng, hooks = live_day(people, now="11:00")
    seat(eng, "a0", "A", 0, "10:50")
    seat(eng, "out", "A", 1, "10:40")
    seat(eng, "a2", "A", 2, "10:30")
    for i, p in enumerate(("b0", "b1", "b2")):
        seat(eng, p, "B", i, "08:00")
    eng.tick(hm("11:45"))
    assert any("需在 11:40 前推出圈" in a["msg"] for a in eng.alerts(hm("11:45")))
    # 到出圈时间没人替：按时出圈，岗位空出来
    eng.tick(hm("12:00"))
    out = eng.P["out"]
    assert out["state"] == "away" and out["away"]["reason"] == "SSEI"
    assert occupants(eng, "A") == ["a0", None, "a2"]
    assert "out_unreplaced" in hooks.types("out")
    assert any("空岗待补" in n["msg"] for n in eng.d["notices"])
    # 下一个可出发的人优先补空岗，而不是去推 B 线站得更久的人
    assert eng.P["late"]["assign"]["line"] == "A"
    eng.act_depart("late", hm("12:00"), "screen")
    eng.tick(hm("12:03"))
    assert occupants(eng, "A") == ["late", "a0", "a2"]
    assert eng.P["b2"]["state"] == "onpost"


def test_stale_replacement_is_cleared_when_target_leaves_the_line():
    people = [person("a0", "07:00", "21:00"), person("leaver", "07:00", "10:00"), person("a2", "07:00", "21:00"),
              person("new", "07:00", "21:00")]
    eng, hooks = live_day(people, lines=[two_lines()[0]])
    seat(eng, "a0", "A", 0, "08:50")
    seat(eng, "leaver", "A", 1, "08:30")
    seat(eng, "a2", "A", 2, "08:10")
    eng.tick(hm("09:00"))
    assert eng.P["new"]["assign"]["target"] == "leaver"
    eng.act_away("leaver", "主管暂离", hm("09:05"), "test")
    eng.tick(hm("09:05"))
    assert "assign_cleared" in hooks.types("new")
    assert eng.P["new"]["assign"]["mode"] == "push"


# ---------------------------------------------------------------- 上岗时间限制

def test_no_boarding_within_30_minutes_of_shift_end():
    people = [person("a0", "07:00", "21:00"), person("a1", "07:00", "21:00"), person("a2", "07:00", "21:00"),
              person("short", "07:00", "09:33")]
    eng, _ = live_day(people, lines=[two_lines()[0]])
    for i, p in enumerate(("a0", "a1", "a2")):
        seat(eng, p, "A", i, "08:00")
    eng.tick(hm("09:00"))
    # 09:03 到岗，距下班 30 分钟：不再上岗
    assert eng.P["short"]["assign"] is None


def test_arrive_goes_home_directly_when_no_more_boarding_possible():
    people = [person("a0", "07:00", "21:00"), person("a1", "07:00", "21:00"), person("late", "07:00", "10:00")]
    eng, _ = live_day(people, lines=[two_lines()[0]])
    seat(eng, "a0", "A", 0, "08:00")
    seat(eng, "a1", "A", 1, "08:00")
    seat(eng, "late", "A", 2, "08:00")
    eng.act_fix_remove("late", "测试下线", hm("09:05"), "test")
    assert eng.P["late"]["after"] is None
    eng.act_arrive("late", hm("09:08"), "screen")
    # 09:08 休息 15 分钟 + 路程 3 分钟 = 09:26 到岗，距下班 34 分钟可以再上；改到 09:13 到大屏就不行
    assert eng.P["late"]["state"] == "rest"
    eng.act_undo("late", hm("09:09"), "test")
    eng.P["late"]["arriveAnchor"] = None
    eng.act_arrive("late", hm("09:13"), "screen")
    assert eng.P["late"]["state"] == "done"


def test_depart_only_from_one_minute_before_planned_time():
    people = [person(p, "07:00", "21:00") for p in ("a0", "a1", "a2", "new")]
    eng, _ = live_day(people, lines=[two_lines()[0]])
    for i, p in enumerate(("a0", "a1", "a2")):
        seat(eng, p, "A", i, "08:00")
    eng.tick(hm("09:00"))
    eng.P["new"]["assign"]["departAt"] = hm("09:10")
    with pytest.raises(ActionError, match="还没到出发时间（09:10）"):
        eng.act_depart("new", hm("09:08"), "screen")
    eng.act_depart("new", hm("09:09"), "screen")
    assert eng.P["new"]["state"] == "heading"


# ---------------------------------------------------------------- 大屏、撤回、下班

def test_undo_then_arrive_keeps_rest_anchored_to_first_arrival():
    people = [person(p, "07:00", "21:00") for p in ("a0", "a1", "a2")]
    eng, _ = live_day(people, lines=[two_lines()[0]])
    for i, p in enumerate(("a0", "a1", "a2")):
        seat(eng, p, "A", i, "08:00")
    eng.act_fix_remove("a2", "测试下线", hm("09:00"), "test")
    eng.act_arrive("a2", hm("09:04"), "screen")
    assert eng.P["a2"]["readyAt"] == hm("09:19")
    eng.act_undo("a2", hm("09:06"), "supervisor")
    assert eng.P["a2"]["state"] == "walkback"
    eng.act_arrive("a2", hm("09:10"), "screen")
    assert eng.P["a2"]["readyAt"] == hm("09:19")


def test_walkback_person_is_sent_home_at_shift_end_even_without_screen_click():
    people = [person("a0", "07:00", "21:00"), person("a1", "07:00", "21:00"), person("gone", "07:00", "10:00")]
    eng, hooks = live_day(people, lines=[two_lines()[0]])
    seat(eng, "a0", "A", 0, "08:00")
    seat(eng, "a1", "A", 1, "08:00")
    seat(eng, "gone", "A", 2, "08:00")
    eng.act_fix_remove("gone", "测试下线", hm("09:30"), "test")
    eng.tick(hm("09:40"))
    assert eng.P["gone"]["state"] == "done"
    assert "shift_end_unconfirmed" in hooks.types("gone")


def test_day_ends_when_everyone_is_done():
    eng, hooks = live_day([person("a0", "07:00", "09:30")], lines=[two_lines()[0]])
    eng.tick(hm("09:20"))
    assert eng.should_end(hm("09:20"))
    eng.end_day(hm("09:20"))
    assert eng.d["status"] == "ended"
    assert "day_end" in hooks.types()


# ---------------------------------------------------------------- 推 7 点

def seven_day(push7_absent=False):
    lines = [{"id": "B2", "group": "B", "posts": [{"name": "迎宾童车"}, {"name": "迎宾", "seven": 1}, {"name": "豹协"}]}]
    roster = {
        "people": {pid: {"name": pid} for pid in ("s1", "s2", "c1", "c2", "p1", "p2")},
        "days": {DAY: {"s1": "07:00-13:30", "c1": "07:15-16:15", "c2": "07:15-15:15",
                       "p1": "07:15-17:15", "p2": "07:15-14:00"}},
        "hours": {DAY: {"open": "08:30", "close": "21:30"}},
    }
    S = dict(DEFAULT_SETTINGS)
    day = build_draft(DAY, roster, {"lines": lines}, S, {}, {})
    hooks = Hooks()
    eng = Engine(day, S, hooks)
    eng.publish(hm("06:30"), "test")
    return eng, hooks


def test_push7_swaps_directly_at_the_seven_post_and_the_early_person_rests():
    eng, hooks = seven_day()
    plan = eng.d["plan"]
    assert plan["seven"] == {"B2#1": "s1"}
    pusher = plan["push7"][0]
    eng.tick(hm("07:00"))
    assert occupants(eng, "B2")[1] == "s1"
    eng.tick(hm("07:15"))
    assert eng.P[pusher]["assign"]["mode"] == "push7"
    eng.act_depart(pusher, hm("07:15"), "screen")
    eng.tick(hm("07:18"))
    assert occupants(eng, "B2")[1] == pusher
    s1 = eng.P["s1"]
    assert s1["state"] == "walkback" and "推7点下来" in s1["flags"] and s1["downReason"] == "推7点下来"
    eng.act_arrive("s1", hm("07:21"), "screen")
    assert s1["state"] == "rest" and s1["readyAt"] == hm("07:36")


def run(eng, start, end, step=0.25):
    """按大屏的正常操作推进：下线 3 分钟后点去休息，到出发时间点去轮岗。"""
    t = hm(start)
    while t <= hm(end):
        eng.tick(t)
        for p in list(eng.P.values()):
            if p["state"] == "walkback" and t - p["walkbackSince"] >= 3:
                eng.act_arrive(p["pid"], t, "screen")
            elif p["state"] == "ready" and p.get("assign") and t >= p["assign"]["departAt"]:
                eng.act_depart(p["pid"], t, "screen")
        t += step


def test_push7_label_is_not_reused_for_a_later_shift_end_replacement():
    """07:00 班的人带着“7点岗”标记，之后到下班被推下班时，应记为推下班而不是推7点下来。"""
    people = [person("a0", "07:00", "21:00"), person("s1", "07:00", "10:00"), person("a2", "07:00", "21:00"),
              person("new", "07:00", "21:00")]
    eng, hooks = live_day(people, lines=[two_lines()[0]])
    seat(eng, "a0", "A", 0, "08:50")
    seat(eng, "s1", "A", 1, "08:30")
    seat(eng, "a2", "A", 2, "08:10")
    eng.P["s1"]["flags"] = ["7点岗"]
    run(eng, "09:00", "09:45")
    s1 = eng.P["s1"]
    assert s1["state"] == "done" and s1["downReason"] == "推下班"
    assert "推7点下来" not in s1["flags"]
    reasons = [e["detail"].get("reason") for e in hooks.events if e["pid"] == "s1" and e["type"] in ("pushed_off", "pushed_out")]
    assert reasons == ["推下班"]


def test_push7_substitute_is_chosen_when_the_planned_person_is_away():
    eng, hooks = seven_day()
    planned = eng.d["plan"]["push7"][0]
    eng._assign_all = lambda now: False
    eng.tick(hm("07:15"))
    del eng._assign_all
    eng.act_leave(planned, "临时请假", hm("07:15"), "supervisor")
    eng.tick(hm("07:15"))
    sub = eng.d["plan"]["push7"][0]
    assert sub != planned
    assert eng.P[sub]["assign"]["mode"] == "push7"
    assert "push7_substitute" in hooks.types(sub)


# ---------------------------------------------------------------- 吃饭、闭园

def test_meal_only_when_enough_people_rest_for_every_open_line():
    lines = two_lines()
    people = [person(f"r{i}", "07:00", "21:00") for i in range(3)] + [person("eater", "07:00", "21:00")]
    eng, _ = live_day(people, lines=lines, now="11:00")
    for i in range(3):
        eng.P[f"r{i}"]["state"] = "rest"
        eng.P[f"r{i}"]["readyAt"] = hm("11:10")
    eater = eng.P["eater"]
    # 2 条线开着，休息中 3 人：够了，可以吃饭
    assert eng._meal_decision(eater, hm("11:00")) == "meal"
    eng.P["r0"]["state"] = "meal"
    eng.P["r0"]["readyAt"] = hm("11:40")
    eng.P["r1"]["state"] = "meal"
    eng.P["r1"]["readyAt"] = hm("11:40")
    # 只剩 1 人休息，不够 2 条线：只休息
    assert eng._meal_decision(eater, hm("11:00")) == "rest"
    # 吃饭剩不到 20 分钟的人算作休息中
    assert eng._meal_decision(eater, hm("11:21")) == "meal"


def test_meal_last_chance_does_not_wait_for_enough_rest():
    people = [person("late", "13:00", "21:30")]
    eng, _ = live_day(people, lines=[two_lines()[0]], now="17:00", close="21:30")
    late = eng.P["late"]
    # 离闭园 270 分钟以内、还没吃饭：即使没有人在休息也安排吃饭
    assert eng._meal_decision(late, hm("17:01")) == "meal"
    assert eng._meal_decision(late, hm("16:59")) == "rest"


def test_postponed_close_restores_posts_and_closing_work_people():
    people = [person(p, "07:00", "23:00") for p in ("a0", "a1", "a2", "pool")]
    eng, _ = live_day(people, lines=[two_lines()[0]], now="20:00", close="21:00")
    for i, p in enumerate(("a0", "a1", "a2")):
        seat(eng, p, "A", i, "20:00")
    eng.tick(hm("21:00"))
    assert eng.d["closed"] and eng.P["pool"]["state"] == "away"
    assert not any(x["open"] for x in eng.line("A")["posts"])
    eng.act_set_close("22:00", hm("21:05"), "supervisor")
    assert not eng.d["closed"]
    assert all(x["open"] for x in eng.line("A")["posts"])
    assert eng.P["pool"]["state"] == "ready"
