"""Deterministic announcement-worker startup/shutdown races; no database writes."""
import os
import tempfile
import threading
from types import SimpleNamespace

import pytest

os.environ["RECOGNITION_V2_DATA_DIR"] = tempfile.mkdtemp(prefix="announcement-worker-lifecycle-")
from app import announcement_worker as worker


@pytest.fixture
def isolated_worker(monkeypatch):
    monkeypatch.setattr(worker, "_thread", None)
    monkeypatch.setattr(worker, "_stop", threading.Event())
    monkeypatch.setattr(worker, "_guard", threading.Lock())


def test_stop_waits_for_start_before_setting_event_and_joining(isolated_worker, monkeypatch):
    entered, release, stopping, joined = (threading.Event() for _ in range(4))
    failures = []

    class PausedThread:
        started = False

        def __init__(self, **kwargs):
            assert kwargs["name"] == "announcement-delivery" and kwargs["daemon"] is True

        def start(self):
            entered.set()
            assert release.wait(3)
            self.started = True

        def is_alive(self):
            return self.started and not joined.is_set()

        def join(self, timeout):
            assert self.started, "Cannot join an unpublished, unstarted worker"
            assert worker._stop.is_set() and timeout == 3
            joined.set()

    monkeypatch.setattr(worker, "threading", SimpleNamespace(Thread=PausedThread))

    def start():
        try:
            worker.start_worker()
        except BaseException as error:
            failures.append(error)

    def stop():
        stopping.set()
        try:
            worker.stop_worker()
        except BaseException as error:
            failures.append(error)

    starter = threading.Thread(target=start)
    stopper = threading.Thread(target=stop)
    starter.start()
    try:
        assert entered.wait(3)
        assert worker._thread is None  # No half-started thread is observable.
        stopper.start()
        assert stopping.wait(3)
        assert not joined.wait(0.05)
        assert not worker._stop.is_set()  # Startup cannot clear a concurrent stop.
    finally:
        release.set()
        starter.join(3)
        if stopper.ident is not None:
            stopper.join(3)
    assert not starter.is_alive() and not stopper.is_alive()
    assert not failures and joined.is_set()


def test_duplicate_start_stop_and_restart(isolated_worker, monkeypatch):
    instances = []

    class FakeThread:
        def __init__(self, **kwargs):
            self.alive = False
            instances.append(self)

        def start(self):
            assert not worker._stop.is_set()
            self.alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            assert worker._stop.is_set() and timeout == 3
            self.alive = False

    monkeypatch.setattr(worker, "threading", SimpleNamespace(Thread=FakeThread))
    worker.stop_worker()
    worker.start_worker()
    worker.start_worker()
    assert len(instances) == 1 and worker._thread is instances[0]
    worker.stop_worker()
    worker.stop_worker()
    worker.start_worker()
    assert len(instances) == 2 and worker._thread is instances[1]
    worker.stop_worker()


def test_failed_start_does_not_publish_unjoinable_thread(isolated_worker, monkeypatch):
    class FailedThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("injected start failure")

        def join(self, timeout):
            pytest.fail("Failed startup must never publish a join target")

    monkeypatch.setattr(worker, "threading", SimpleNamespace(Thread=FailedThread))
    with pytest.raises(RuntimeError, match="injected start failure"):
        worker.start_worker()
    assert worker._thread is None
    worker.stop_worker()
    assert worker._stop.is_set()
