"""Shared in-process response caches and invalidation."""
from __future__ import annotations

from threading import Lock


STATISTICS_CACHE_SECONDS = 2.0


_statistics_cache_lock = Lock()


_statistics_response_cache: dict[tuple, tuple[float, bytes]] = {}


PR_RANKING_CACHE_SECONDS = 5.0


_pr_ranking_cache_lock = Lock()


_pr_ranking_response_cache: dict[tuple, tuple[float, bytes]] = {}


def invalidate_data_caches() -> None:
    with _statistics_cache_lock:
        _statistics_response_cache.clear()
    with _pr_ranking_cache_lock:
        _pr_ranking_response_cache.clear()
