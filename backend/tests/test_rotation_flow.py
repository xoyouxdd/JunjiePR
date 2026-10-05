"""轮岗接口端到端：名单上传、草稿、发布、测试时钟、大屏操作、个人待办、记录与配置。"""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
from openpyxl import Workbook


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-rotation-flow-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402


DAY = "2026-10-04"
LINES = [
    {"id": "A", "group": "A", "posts": [{"name": "A入口"}, {"name": "迎宾", "seven": 1}, {"name": "A出口"}]},
    {"id": "B", "group": "B", "posts": [{"name": "B入口"}, {"name": "B中"}, {"name": "B出口"}]},
]
ROSTER = [
    ("9000001", "早班甲", "07:00-13:30", ""),
    ("9000002", "开园甲", "07:15-16:15", ""),
    ("9000003", "开园乙", "07:15-16:15", ""),
    ("9000004", "开园丙", "07:15-16:15", ""),
    ("9000005", "开园丁", "07:15-16:15", ""),
    ("9000006", "开园戊", "07:15-16:15", ""),
    ("9000007", "推七甲", "07:15-17:15", ""),
    ("9000008", "中班甲", "09:00-18:00", "1000-1100 SSEI"),
    ("9000009", "放休甲", "放休", ""),
]


def roster_file() -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.append(["工号", "姓名", "日期", "班次", "备注"])
    for no, name, shift, note in ROSTER:
        ws.append([no, name, DAY, shift, note])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def login(client: TestClient, account: str, password: str = "1234") -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def enter(client: TestClient, account: str) -> None:
    response = client.post("/api/rotation/enter", json={"account": account})
    assert response.status_code == 200, response.text


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def clock(client: TestClient, **body):
    return client.post("/api/rotation/test-clock", json=body)


def person(board: dict, no: str) -> dict:
    return next(p for p in board["day"]["persons"] if p["pid"] == no)


def test_full_day_flow_through_the_api() -> None:
    with TestClient(app) as manager, TestClient(app) as screen, TestClient(app) as member:
        login(manager, "GSMTEST01")
        enter(manager, "7777777")
        login(screen, "6666666", "1243")
        login(member, "TRTEST01")

        # 配置两条线，上传名单
        ok(manager.put("/api/rotation/config", json={"lines": LINES}))
        assert manager.put("/api/rotation/config", json={"lines": [{"id": "X", "posts": []}]}).status_code == 400
        assert manager.put("/api/rotation/config", json={"settings": {"offLead": "abc"}}).status_code == 400
        bad = manager.post("/api/rotation/roster/upload", files={"file": ("名单.txt", b"x", "text/plain")}, data={"scope": "week"})
        assert bad.status_code == 400
        uploaded = ok(manager.post("/api/rotation/roster/upload", files={"file": ("名单.xlsx", roster_file(), "application/octet-stream")}, data={"scope": "day"}))
        assert uploaded["entry_count"] == len(ROSTER) and uploaded["start_date"] == DAY
        summary = ok(manager.get("/api/rotation/roster", params={"date": DAY}))
        assert summary["available"] and {p["kind"] for p in summary["people"]} == {"rotation", "off"}

        # 模拟一个 CM 工号：名单有了才能进
        enter(member, "9000001")

        # 草稿只能在模拟时钟到这一天后发布
        ok(clock(manager, action="set", date=DAY, time="06:30"))
        ok(manager.post("/api/rotation/draft", json={"action": "draft_generate", "date": DAY}))
        board = ok(manager.get("/api/rotation/board"))
        assert board["draft"]["plan"]["seven"] == {"A#1": "9000001"}
        assert board["draft"]["plan"]["push7"] == ["9000007"]
        assert manager.post("/api/rotation/draft", json={"action": "publish", "date": "2026-10-05"}).status_code == 400
        ok(manager.post("/api/rotation/draft", json={"action": "publish", "date": DAY}))
        assert manager.post("/api/rotation/draft", json={"action": "draft_generate", "date": DAY}).status_code == 400

        # 下一步：推进到第一个需要在大屏操作的时刻
        step = ok(clock(manager, action="next"))
        assert step["clock"]["paused"] is True
        assert step["waiting"] == ["推七甲 点「去轮岗」（A 线）"]
        assert step["clock"]["minute"] == 7 * 60 + 15
        assert clock(manager, action="next").status_code == 400  # 还没处理，不能下一步

        # 大屏只能点去休息、去轮岗
        assert screen.post("/api/rotation/screen/act", json={"action": "undo", "pid": "9000007"}).status_code == 403
        assert screen.post("/api/rotation/act", json={"action": "depart", "pid": "9000007"}).status_code == 403
        ok(screen.post("/api/rotation/screen/act", json={"action": "depart", "pid": "9000007"}))
        assert screen.post("/api/rotation/screen/act", json={"action": "depart", "pid": "9000007"}).status_code == 400

        step = ok(clock(manager, action="next"))
        assert step["waiting"] == ["早班甲 点「去休息」"]
        board = ok(manager.get("/api/rotation/board"))
        assert person(board, "9000001")["downReason"] == "推7点下来"
        assert person(board, "9000007")["post"] == "迎宾"

        # 员工个人页：待办标测试，随状态更新
        me = ok(member.get("/api/rotation/me"))
        assert me["test_mode"] is True and me["person"]["state"] == "walkback"
        assert [n["title"] for n in me["notices"] if n["status"] == "open"] == ["请到休息室大屏点「去休息」"]
        ok(screen.post("/api/rotation/screen/act", json={"action": "arrive", "pid": "9000001"}))
        me = ok(member.get("/api/rotation/me"))
        open_titles = [n["title"] for n in me["notices"] if n["status"] == "open"]
        assert open_titles == ["休息至 07:33"]
        assert any(n["status"] == "done" and "去休息" in n["title"] for n in me["notices"])
        assert me["line"] is not None or me["person"]["assign"] is None

        # 主管撤回（大屏不能撤回），再到达时休息仍从第一次到达算起
        ok(manager.post("/api/rotation/act", json={"action": "undo", "pid": "9000001"}))
        ok(screen.post("/api/rotation/screen/act", json={"action": "arrive", "pid": "9000001"}))
        assert person(ok(manager.get("/api/rotation/board")), "9000001")["readyAt"] == 7 * 60 + 33
        assert manager.post("/api/rotation/act", json={"action": "fix_remove", "pid": "9000002"}).status_code == 400

        # 快进与倍速
        before = ok(manager.get("/api/rotation/board"))["clock"]["minute"]
        jumped = ok(clock(manager, action="jump", minutes=30))
        assert jumped["clock"]["minute"] == before + 30
        assert clock(manager, action="speed", speed=500).status_code == 400
        ok(clock(manager, action="speed", speed=30))

        # 记录
        log = ok(manager.get("/api/rotation/log", params={"date": DAY}))["items"]
        types = {item["type"] for item in log}
        assert {"publish", "crew_start", "push7", "depart", "arrive", "undo_arrive"} <= types
        assert any(item["actor"] == "热力追踪休息室大屏" for item in log)
        record = ok(manager.get("/api/rotation/person", params={"employee_no": "9000001", "date": DAY}))
        assert record["segments"][0]["line"] == "A" and record["duties"] == []
        assert ok(manager.get("/api/rotation/person", params={"employee_no": "9000007", "date": DAY}))["duties"] == [{"date": DAY, "kind": "push7"}]

        # 权限：大屏、模拟员工都不能看主管看板
        assert screen.get("/api/rotation/board").status_code == 403
        assert member.get("/api/rotation/board").status_code == 403
        assert member.post("/api/rotation/test-clock", json={"action": "jump"}).status_code == 403
        assert ok(screen.get("/api/rotation/screen/state"))["day"]["status"] == "live"

        ok(clock(manager, action="real"))
