"""One daemon thread per process with race-safe start and stop.

Start and stop share one guard. The thread is published only after it has
started, so stop never joins a half-started thread and start never clears a
concurrent stop. If the join times out, keep the thread reference to prevent
overlapping work. For a counted worker, a start while stopping is honored by
the same thread after the previous loop returns.
With ``counted=True`` every start needs a matching stop before the thread ends
(the app lifespan and tests may start the same worker more than once).
"""
from __future__ import annotations

import threading
from typing import Callable


class BackgroundWorker:
    def __init__(self, name: str, loop: Callable[[threading.Event], None], *,
                 join_timeout: float, counted: bool = False):
        self.name = name
        self.join_timeout = join_timeout
        self.counted = counted
        self._loop = loop
        self._guard = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._users = 0
        self._restart_prepare = None
        self._thread_factory = threading.Thread

    def start(self, prepare: Callable[[], None] | None = None) -> None:
        with self._guard:
            self._users += 1
            if self._thread and self._thread.is_alive():
                if self.counted and self._stop.is_set() and self._users == 1:
                    self._restart_prepare = prepare
                return
            stop = self._stop = threading.Event()
            if prepare:
                prepare()
            thread = self._thread_factory(target=self._run, args=(stop,), name=self.name, daemon=True)
            thread.start()
            self._thread = thread

    def _run(self, stop: threading.Event) -> None:
        while True:
            self._loop(stop)
            with self._guard:
                if not (self.counted and stop.is_set() and self._users):
                    self._thread = None
                    return
                prepare, self._restart_prepare = self._restart_prepare, None
                if prepare:
                    prepare()
                stop = self._stop = threading.Event()

    def stop(self) -> None:
        with self._guard:
            self._users = max(0, self._users - 1)
            if self.counted and self._users:
                return
            self._restart_prepare = None
            self._stop.set()
            thread = self._thread
        if thread:
            thread.join(timeout=self.join_timeout)
            with self._guard:
                if self._thread is thread and not thread.is_alive():
                    self._thread = None
