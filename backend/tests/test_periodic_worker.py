"""Periodic scheduling and worker lifecycle; no database writes."""
import threading

import pytest

from app import periodic_worker as worker
from app.background_worker import BackgroundWorker


@pytest.fixture
def isolated_worker(monkeypatch):
    state = worker._worker
    monkeypatch.setattr(state, "_thread", None)
    monkeypatch.setattr(state, "_stop", threading.Event())
    monkeypatch.setattr(state, "_guard", threading.Lock())
    monkeypatch.setattr(state, "_users", 0)
    monkeypatch.setattr(state, "_restart_prepare", None)


def test_stop_waits_for_start_before_setting_event_and_joining(isolated_worker, monkeypatch):
    entered, release, stopping, joined = (threading.Event() for _ in range(4))
    failures = []

    class PausedThread:
        started = False

        def __init__(self, **kwargs):
            assert kwargs["name"] == "periodic-maintenance" and kwargs["daemon"] is True

        def start(self):
            entered.set()
            assert release.wait(3)
            self.started = True

        def is_alive(self):
            return self.started and not joined.is_set()

        def join(self, timeout):
            assert self.started, "Cannot join an unpublished, unstarted worker"
            assert worker._worker._stop.is_set() and timeout == 5
            joined.set()

    monkeypatch.setattr(worker._worker, "_thread_factory", PausedThread)

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
        assert worker._worker._thread is None  # No half-started thread is observable.
        stopper.start()
        assert stopping.wait(3)
        assert not joined.wait(0.05)
        assert not worker._worker._stop.is_set()  # Startup cannot clear a concurrent stop.
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
            assert not worker._worker._stop.is_set()
            self.alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            assert worker._worker._stop.is_set() and timeout == 5
            self.alive = False

    monkeypatch.setattr(worker._worker, "_thread_factory", FakeThread)
    worker.stop_worker()
    worker.start_worker()
    worker.start_worker()
    assert len(instances) == 1 and worker._worker._thread is instances[0]
    worker.stop_worker()
    assert instances[0].is_alive()
    worker.stop_worker()
    worker.start_worker()
    assert len(instances) == 2 and worker._worker._thread is instances[1]
    worker.stop_worker()


def test_failed_start_does_not_publish_unjoinable_thread(isolated_worker, monkeypatch):
    class FailedThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("injected start failure")

        def join(self, timeout):
            pytest.fail("Failed startup must never publish a join target")

    monkeypatch.setattr(worker._worker, "_thread_factory", FailedThread)
    with pytest.raises(RuntimeError, match="injected start failure"):
        worker.start_worker()
    assert worker._worker._thread is None
    worker.stop_worker()
    assert worker._worker._stop.is_set()


def test_periodic_tasks_retain_initial_delays_and_survive_other_task_errors(caplog, monkeypatch):
    from app import activity_worker, deduction_materials

    class ClockAndStop:
        now = 0

        def wait(self, seconds):
            self.now += seconds
            return self.is_set()

        def is_set(self):
            return self.now >= 65

    stop = ClockAndStop()
    runs = []

    def expiry():
        runs.append(("expiry", stop.now))

    def settlement():
        runs.append(("settlement", stop.now))
        if stop.now == 5:
            raise RuntimeError("injected scan failure")

    def delivery():
        runs.append(("delivery", stop.now))

    def renewal():
        runs.append(("renewal", stop.now))

    run_periodic = worker._run_periodic
    monkeypatch.setattr(worker, "_run_periodic", lambda stop, tasks: run_periodic(stop, tasks, clock=lambda: stop.now))
    monkeypatch.setattr(worker, "_expire_internship_accounts", expiry)
    monkeypatch.setattr(worker, "_deliver_announcements", delivery)
    monkeypatch.setattr(activity_worker, "process_due", settlement)
    monkeypatch.setattr(deduction_materials, "refresh_material_leases", renewal)
    worker._run(stop)

    assert [elapsed for name, elapsed in runs if name == "expiry"] == [0, 60]
    assert [elapsed for name, elapsed in runs if name == "settlement"] == list(range(5, 65, 5))
    assert [elapsed for name, elapsed in runs if name == "renewal"] == [30, 60]
    assert [elapsed for name, elapsed in runs if name == "delivery"] == [60]
    assert "injected scan failure" in caplog.text


@pytest.mark.parametrize("counted", [False, True])
def test_restart_does_not_prepare_queue_until_previous_loop_returns(counted):
    entered, release = threading.Event(), threading.Event()
    prepared = []

    def run(_stop):
        entered.set()
        release.wait()

    queue_worker = BackgroundWorker("test-queue", run, join_timeout=0, counted=counted)

    def prepare():
        prepared.append(True)

    queue_worker.start(prepare)
    previous = queue_worker._thread
    try:
        assert entered.wait(3)
        if counted:
            queue_worker.start(prepare)
            queue_worker.stop()
            assert not queue_worker._stop.is_set()

        queue_worker.stop()
        assert queue_worker._stop.is_set() and previous.is_alive()
        queue_worker.start(prepare)
        assert prepared == [True]
        assert queue_worker._thread is previous

        release.set()
        previous.join(3)
        assert not previous.is_alive()
        assert len(prepared) == (2 if counted else 1)

        queue_worker.start(prepare)
        assert len(prepared) == (3 if counted else 2)
        assert queue_worker._thread is not previous
        if counted:
            queue_worker.stop()
            assert not queue_worker._stop.is_set()
    finally:
        release.set()
        previous.join(3)
        queue_worker.stop()


def test_counted_start_during_timed_out_stop_resumes_after_current_job():
    entered, release, resumed = (threading.Event() for _ in range(3))
    lifecycle = []

    def run(stop):
        if not entered.is_set():
            entered.set()
            release.wait()
            lifecycle.append("previous job finished")
        else:
            lifecycle.append("replacement loop entered")
            resumed.set()
            stop.wait()

    worker = BackgroundWorker("test-lifespan-restart", run, join_timeout=0, counted=True)
    worker.start()
    try:
        assert entered.wait(3)
        worker.stop()
        worker.start(lambda: lifecycle.append("replacement prepared"))
        assert not resumed.is_set() and not lifecycle
        release.set()
        assert resumed.wait(1)
        assert lifecycle == ["previous job finished", "replacement prepared", "replacement loop entered"]
    finally:
        release.set()
        thread = worker._thread
        worker.stop()
        if thread:
            thread.join(3)
