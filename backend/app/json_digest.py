"""Canonical JSON text and its SHA-256 fingerprint.

Fingerprints are stored with previews and exports, so each caller keeps its
original serialization options; changing them would invalidate saved values.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable


def canonical_json(value: Any, *, compact: bool = False, default: Callable[[Any], Any] | None = None) -> str:
    separators = (",", ":") if compact else None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=separators, default=default)


def json_digest(value: Any, *, compact: bool = False, default: Callable[[Any], Any] | None = None) -> str:
    return hashlib.sha256(canonical_json(value, compact=compact, default=default).encode()).hexdigest()
