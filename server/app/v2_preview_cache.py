from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from time import monotonic


@dataclass(frozen=True)
class PreviewCacheEntry:
    content: bytes
    media_type: str
    expires_at: float


class WatermarkedPreviewCache:
    """Small process-local LRU for already-watermarked image previews."""

    def __init__(self, *, ttl_seconds: float = 300.0, max_entries: int = 64, max_bytes: int = 96 * 1024 * 1024):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._entries: OrderedDict[str, PreviewCacheEntry] = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    def get(self, key: str) -> tuple[bytes, str] | None:
        now = monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if not entry:
                return None
            if entry.expires_at <= now:
                self._drop(key)
                return None
            self._entries.move_to_end(key)
            return entry.content, entry.media_type

    def put(self, key: str, content: bytes, media_type: str) -> None:
        if len(content) > self.max_bytes:
            return
        with self._lock:
            self._drop(key)
            self._entries[key] = PreviewCacheEntry(content, media_type, monotonic() + self.ttl_seconds)
            self._bytes += len(content)
            while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                oldest_key = next(iter(self._entries))
                self._drop(oldest_key)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def _drop(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry:
            self._bytes -= len(entry.content)


watermarked_preview_cache = WatermarkedPreviewCache()
