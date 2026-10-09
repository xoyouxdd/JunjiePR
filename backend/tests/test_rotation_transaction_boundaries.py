"""Rotation transactions against an isolated SQLite database; no app startup."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import v2_models  # Register referenced tables without importing app.main.
from app.rotation import service as S, test_clock
from app.rotation.models import RotationConfig, RotationDay, RotationDuty, RotationEvent, RotationNotice, RotationSegment
from app.v2_database import Base


DAY = "2026-10-04"
LINES = [{"id": "A", "posts": [{"name": "入口"}, {"name": "出口"}]},
         {"id": "B", "posts": [{"name": "入口"}, {"name": "出口"}]}]


@pytest.fixture
def rotation(monkeypatch):
    # Use inherited project-directory permissions; pytest's private mode-0700
    # basetemp directories are inaccessible to some Windows sandbox identities.
    test_dir = Path(__file__).resolve().parents[2] / ".handover_review" / "rotation-transaction-tests" / uuid4().hex
    test_dir.mkdir(parents=True)
    engine = create_engine(f"sqlite:///{(test_dir / 'rotation.db').as_posix()}",
                           connect_args={"check_same_thread": False, "timeout": 3})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(S, "RUNTIME", S.Runtime())
    monkeypatch.setattr(S, "SessionLocal", factory)
    with factory() as db:
        attraction = v2_models.Attraction(name="热力追踪")
        db.add(attraction)
        db.commit()
        attraction_id = attraction.id
        cfg = S.load_config(db, attraction_id)
        cfg.lines_json = S._dumps(LINES)
        S.set_sim_clock(cfg, DAY, 240, paused=True)
        db.commit()
        parsed = {"people": {str(i): {"name": f"员工{i}", "type": "", "mark": ""} for i in range(1, 6)},
                  "days": {DAY: {str(i): "07:15-16:15" for i in range(1, 6)}}, "hours": {}}
        S.save_roster(db, attraction_id, parsed, scope="day", file_name="隔离名单.xlsx",
                      uploader_id=None, uploader_name="测试")
    yield SimpleNamespace(factory=factory, attraction_id=attraction_id)
    engine.dispose()
    (test_dir / "rotation.db").unlink()
    test_dir.rmdir()


def draft(rotation):
    with rotation.factory() as db:
        S.do_draft_action(db, rotation.attraction_id, "test", {"action": "draft_generate", "date": DAY})


def live(rotation):
    draft(rotation)
    with rotation.factory() as db:
        S.do_draft_action(db, rotation.attraction_id, "test", {"action": "publish", "date": DAY})


def snapshot(rotation):
    with rotation.factory() as db:
        row = S.day_row(db, rotation.attraction_id, DAY)
        return (row.state_json if row else None,
                db.query(RotationEvent).count(), db.query(RotationSegment).count(),
                db.query(RotationDuty).count(), db.query(RotationNotice).count())


def fail_commit(db, monkeypatch):
    def fail():
        raise RuntimeError("injected commit failure")
    monkeypatch.setattr(db, "commit", fail)


def test_cache_copies_are_private_and_publish_only_after_commit(rotation, monkeypatch):
    live(rotation)
    before = snapshot(rotation)
    cache_before = deepcopy(S.RUNTIME.cache)
    version_before = S.RUNTIME.version
    with rotation.factory() as db:
        row = S.day_row(db, rotation.attraction_id, DAY)
        state = S.day_state(row)
        state["lines"][0]["posts"][0]["open"] = False
        assert S.RUNTIME.cache == cache_before
        original_commit = db.commit

        def commit():
            assert S.RUNTIME.cache == cache_before
            assert S.RUNTIME.version == version_before
            original_commit()

        monkeypatch.setattr(db, "commit", commit)
        S.do_live_action(db, rotation.attraction_id, "test", {"action": "post", "line": "A", "i": 0, "open": False})
    assert snapshot(rotation) != before
    assert S.RUNTIME.version == version_before + 1
    assert not S.RUNTIME.cache[(rotation.attraction_id, DAY)][1]["lines"][0]["posts"][0]["open"]


def test_failed_live_commit_rolls_back_state_and_side_effects(rotation, monkeypatch):
    live(rotation)
    before, cache_before, version_before = snapshot(rotation), deepcopy(S.RUNTIME.cache), S.RUNTIME.version
    with rotation.factory() as db:
        fail_commit(db, monkeypatch)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            S.do_live_action(db, rotation.attraction_id, "test", {"action": "post", "line": "A", "i": 0, "open": False})
    assert snapshot(rotation) == before
    assert S.RUNTIME.cache == cache_before and S.RUNTIME.version == version_before


def test_failed_action_after_time_advance_discards_all_mutations(rotation):
    live(rotation)
    with rotation.factory() as db:
        cfg = S.load_config(db, rotation.attraction_id)
        S.set_sim_clock(cfg, DAY, 500, paused=True)
        db.commit()
    before, cache_before, version_before = snapshot(rotation), deepcopy(S.RUNTIME.cache), S.RUNTIME.version
    with rotation.factory() as db, pytest.raises(S.ActionError, match="岗位索引"):
        S.do_live_action(db, rotation.attraction_id, "test", {"action": "post", "line": "A", "i": -1, "open": False})
    assert snapshot(rotation) == before
    assert S.RUNTIME.cache == cache_before and S.RUNTIME.version == version_before


def test_serialization_includes_commit_and_preloaded_stale_rows(rotation):
    live(rotation)
    committing, release_commit, second_attempt, second_done = Event(), Event(), Event(), Event()
    failures = []
    # The second session deliberately loads the old row before the first commit.
    second_db = rotation.factory()
    # SQLAlchemy's identity map holds weak references: keep both rows alive so
    # this case actually exercises refresh of previously loaded ORM instances.
    second_row = S.day_row(second_db, rotation.attraction_id, DAY)
    second_cfg = S.load_config(second_db, rotation.attraction_id)
    initial_version = second_row.version
    assert initial_version == S.RUNTIME.cache[(rotation.attraction_id, DAY)][0]
    assert json.loads(second_row.state_json)["lines"][0]["posts"][0]["open"]
    initial_clock = second_cfg.clock_json
    refreshed = {}

    def first():
        try:
            with rotation.factory() as db:
                original_commit = db.commit

                def commit():
                    committing.set()
                    assert release_commit.wait(5)
                    original_commit()

                db.commit = commit
                S.do_live_action(db, rotation.attraction_id, "test", {"action": "post", "line": "A", "i": 0, "open": False})
        except BaseException as exc:
            failures.append(exc)

    def second():
        try:
            second_attempt.set()
            assert second_row.version == initial_version
            assert second_cfg.clock_json == initial_clock
            S.do_live_action(second_db, rotation.attraction_id, "test", {"action": "post", "line": "B", "i": 0, "open": False})
            refreshed["version"] = second_row.version
            refreshed["state"] = json.loads(second_row.state_json)
            refreshed["clock"] = second_cfg.clock_json
        except BaseException as exc:
            failures.append(exc)
        finally:
            second_db.close()
            second_done.set()

    a, b = Thread(target=first), Thread(target=second)
    a.start()
    try:
        assert committing.wait(5)
        b.start()
        assert second_attempt.wait(5)
        assert not second_done.wait(0.1), "second request escaped the lock before the first commit"
    finally:
        release_commit.set()
        a.join(5)
        if b.ident is not None:
            b.join(5)
        else:
            second_db.close()
    assert not a.is_alive() and not b.is_alive()
    assert not failures
    assert refreshed["version"] == initial_version + 2
    assert refreshed["clock"] == initial_clock
    assert not refreshed["state"]["lines"][0]["posts"][0]["open"]
    assert not refreshed["state"]["lines"][1]["posts"][0]["open"]
    state = json.loads(snapshot(rotation)[0])
    assert state == refreshed["state"]
    assert not state["lines"][0]["posts"][0]["open"]
    assert not state["lines"][1]["posts"][0]["open"]


@pytest.mark.parametrize("reader", [S.board_payload, S.screen_payload])
def test_get_prearrangement_failure_is_atomic(rotation, monkeypatch, reader):
    before, version_before = snapshot(rotation), S.RUNTIME.version
    with rotation.factory() as db:
        fail_commit(db, monkeypatch)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            reader(db, rotation.attraction_id)
    assert snapshot(rotation) == before
    assert (rotation.attraction_id, DAY) not in S.RUNTIME.cache
    assert S.RUNTIME.version == version_before


def test_get_prearrangement_and_test_reset_have_one_commit(rotation, monkeypatch):
    with rotation.factory() as db:
        commits = []
        original_commit = db.commit

        def commit():
            commits.append(True)
            original_commit()

        monkeypatch.setattr(db, "commit", commit)
        first = S.board_payload(db, rotation.attraction_id)
        assert len(commits) == 1 and first["day"]["status"] == "live"
        plan = deepcopy(first["day"]["plan"])
        S.do_live_action(db, rotation.attraction_id, "test", {"action": "post", "line": "A", "i": 0, "open": False})
        commits.clear()
        result = test_clock.control(db, rotation.attraction_id, {"action": "reset"})
        assert len(commits) == 1 and result["clock"]["minute"] == 240
        state = json.loads(snapshot(rotation)[0])
        assert state["plan"] == plan and state["lines"][0]["posts"][0]["open"]
        assert snapshot(rotation)[1] == 1  # Reset retains just the new publish event.


@pytest.mark.parametrize("action", ["draft_discard", "draft_role", "publish"])
def test_failed_draft_commit_keeps_committed_cache_and_state(rotation, monkeypatch, action):
    draft(rotation)
    before, cache_before, version_before = snapshot(rotation), deepcopy(S.RUNTIME.cache), S.RUNTIME.version
    with rotation.factory() as db:
        fail_commit(db, monkeypatch)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            S.do_draft_action(db, rotation.attraction_id, "test", {"action": action, "pid": "1", "role": "excluded"})
    assert snapshot(rotation) == before
    assert S.RUNTIME.cache == cache_before and S.RUNTIME.version == version_before


@pytest.mark.parametrize("action", ["jump", "reset", "next"])
def test_failed_test_clock_commit_rolls_back_clock_and_day(rotation, monkeypatch, action):
    live(rotation)
    before, cache_before, version_before = snapshot(rotation), deepcopy(S.RUNTIME.cache), S.RUNTIME.version
    with rotation.factory() as db:
        clock_before = S.load_config(db, rotation.attraction_id).clock_json
        fail_commit(db, monkeypatch)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            test_clock.control(db, rotation.attraction_id, {"action": action, "minutes": 200})
    with rotation.factory() as db:
        assert S.load_config(db, rotation.attraction_id).clock_json == clock_before
    assert snapshot(rotation) == before
    assert S.RUNTIME.cache == cache_before and S.RUNTIME.version == version_before


def test_ticker_commit_failure_cannot_publish_uncommitted_prearrangement(rotation, monkeypatch):
    def failed_session():
        db = rotation.factory()
        fail_commit(db, monkeypatch)
        return db
    monkeypatch.setattr(S, "SessionLocal", failed_session)
    before, version_before = snapshot(rotation), S.RUNTIME.version
    with pytest.raises(RuntimeError, match="injected commit failure"):
        S.tick_attraction(rotation.attraction_id)
    assert snapshot(rotation) == before
    assert (rotation.attraction_id, DAY) not in S.RUNTIME.cache
    assert S.RUNTIME.version == version_before


def test_quiet_ticker_retains_cursor_without_notifying_every_second(rotation):
    live(rotation)
    version_before = S.RUNTIME.version
    with rotation.factory() as db:
        cfg = S.load_config(db, rotation.attraction_id)
        S.set_sim_clock(cfg, DAY, 241, paused=True)
        db.commit()
    S.tick_attraction(rotation.attraction_id)
    assert json.loads(snapshot(rotation)[0])["lastTick"] == 241
    assert S.RUNTIME.version == version_before


@pytest.mark.parametrize("index", [-1, 2, "no", 1.5, True, None, [], {}])
@pytest.mark.parametrize("action", ["post", "fix_place", "plan_place"])
def test_post_actions_reject_invalid_indices_without_changes(rotation, action, index):
    live(rotation)
    before = snapshot(rotation)
    with rotation.factory() as db, pytest.raises(S.ActionError):
        S.do_live_action(db, rotation.attraction_id, "test", {"action": action, "line": "A", "i": index,
                                                            "pid": "1", "reason": "测试", "open": False})
    assert snapshot(rotation) == before


@pytest.mark.parametrize("key", ["A#-1", "A#2", "A#x", "A", "X#0", "A#0#1", "A#00", "A# 0", 1])
def test_draft_rejects_invalid_post_keys_before_publish(rotation, key):
    draft(rotation)
    before = snapshot(rotation)
    with rotation.factory() as db, pytest.raises(S.ActionError):
        S.do_draft_action(db, rotation.attraction_id, "test", {"action": "draft_set", "kind": "crew", "key": key, "pid": "1"})
    assert snapshot(rotation) == before


@pytest.mark.parametrize("lines,settings", [
    ([{"id": 1, "posts": [{"name": "岗"}]}], None),
    ([{"id": [], "posts": [{"name": "岗"}]}], None),
    ([{"id": "A#1", "posts": [{"name": "岗"}]}], None),
    ([{"id": "A", "posts": [{"name": "岗", "seven": "1"}]}], None),
    ([{"id": "A", "posts": [{"name": "岗", "openAt": "99:99"}]}], None),
    (None, {"offLead": -1}), (None, {"offLead": "NaN"}),
    (None, {"lostCount": 1.5}), (None, {"lineOpenAt": "99:99"}),
])
def test_configuration_rejects_values_that_cannot_build_or_publish(rotation, lines, settings):
    with rotation.factory() as db:
        before = S.load_config(db, rotation.attraction_id).lines_json
        with pytest.raises(S.ActionError), S.transaction(db):
            S.update_config(S.load_config(db, rotation.attraction_id), lines, settings, None)
        assert S.load_config(db, rotation.attraction_id).lines_json == before


@pytest.mark.parametrize("body", [
    {"action": "set", "date": "invalid"}, {"action": "set", "time": "99:99"},
    {"action": "set", "speed": 500}, {"action": "speed", "speed": "NaN"},
    {"action": "jump", "minutes": -1}, {"action": "jump", "minutes": "invalid"},
])
def test_test_clock_invalid_values_are_domain_errors(rotation, body):
    with rotation.factory() as db, pytest.raises(S.ActionError):
        test_clock.control(db, rotation.attraction_id, body)


def test_configuration_route_publishes_version_only_after_commit(rotation, monkeypatch):
    from app.routers.rotation import rotation_config_update

    before = S.RUNTIME.version
    actor = SimpleNamespace(entered_by=None)
    with rotation.factory() as db:
        original_commit = db.commit

        def commit():
            assert S.RUNTIME.version == before
            original_commit()

        monkeypatch.setattr(db, "commit", commit)
        assert rotation_config_update({"settings": {"offLead": 21}}, db, actor) == {"ok": True}
    assert S.RUNTIME.version == before + 1
    with rotation.factory() as db:
        fail_commit(db, monkeypatch)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            rotation_config_update({"settings": {"offLead": 22}}, db, actor)
    assert S.RUNTIME.version == before + 1
    with rotation.factory() as db:
        assert S.settings_of(S.load_config(db, rotation.attraction_id))["offLead"] == 21


def test_configuration_route_maps_invalid_line_id_to_400(rotation):
    from fastapi import HTTPException
    from app.routers.rotation import rotation_config_update

    with rotation.factory() as db, pytest.raises(HTTPException) as error:
        rotation_config_update({"lines": [{"id": 1, "posts": [{"name": "岗"}]}]}, db, SimpleNamespace(entered_by=None))
    assert error.value.status_code == 400


def test_valid_string_post_index_and_configuration_survive_generation_and_publish(rotation):
    with rotation.factory() as db, S.transaction(db):
        S.update_config(S.load_config(db, rotation.attraction_id), LINES, {"offLead": "20"}, None)
        S.mark_changed(db)
    live(rotation)
    with rotation.factory() as db:
        S.do_live_action(db, rotation.attraction_id, "test", {"action": "post", "line": "A", "i": "1", "open": False})
    assert not json.loads(snapshot(rotation)[0])["lines"][0]["posts"][1]["open"]


@pytest.mark.parametrize("action", [[], {}, None, 1, True])
def test_screen_route_maps_invalid_action_types_to_400(rotation, action):
    from fastapi import HTTPException
    from app.routers.rotation import rotation_screen_act

    with rotation.factory() as db, pytest.raises(HTTPException) as error:
        rotation_screen_act({"action": action}, db, SimpleNamespace())
    assert error.value.status_code == 400
