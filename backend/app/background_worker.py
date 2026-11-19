"""One daemon thread per process with race-safe start and stop.

Start and stop share one guard. The thread is published only after it has
started, so stop never joins a half-started thread and start never clears a
concurrent stop. Each run gets a fresh stop event, so a thread that outlived
its join timeout still sees its own stop request instead of a cleared one.
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
        self._thread_factory = threading.Thread

    def start(self, prepare: Callable[[], None] | None = None) -> None:
        with self._guard:
            self._users += 1
            if self._thread and self._thread.is_alive():
                return
            stop = self._stop = threading.Event()
            if prepare:
                prepare()
            thread = self._thread_factory(target=self._loop, args=(stop,), name=self.name, daemon=True)
            thread.start()
            self._thread = thread

    def stop(self) -> None:
        with self._guard:
            self._users = max(0, self._users - 1)
            if self.counted and self._users:
                return
            self._stop.set()
            thread, self._thread = self._thread, None
            if thread:
                thread.join(timeout=self.join_timeout)
