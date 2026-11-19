"""In-app release notes; entries live in changelog.json, newest first.

Keep APP_VERSION equal to the first entry's version.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.version import APP_VERSION

# audiences: role codes, or "all". permissions: optional extra match on user.permissions.
RELEASES: list[dict] = json.loads(Path(__file__).with_name("changelog.json").read_text(encoding="utf-8"))


def item_visible(item: dict, role_code: str | set[str], permissions: set[str]) -> bool:
    """`role_code` may be a set: base identity plus acting duties all count."""
    if item.get("feature") == "monthly_ranks":
        from app.month_rank_policy import monthly_ranks_enabled
        if not monthly_ranks_enabled():
            return False
    audiences = item.get("audiences") or ["all"]
    needed = set(item.get("permissions") or [])
    codes = {role_code} if isinstance(role_code, str) else set(role_code)
    return ("all" in audiences or bool(codes.intersection(audiences))) and needed.issubset(permissions)


def visible_releases(role_code: str | set[str], permissions: set[str]) -> list[dict]:
    releases = []
    for release in RELEASES:
        items = [
            {"summary": item["summary"], "detail": item.get("detail") or ""}
            for item in release.get("items") or []
            if item_visible(item, role_code, permissions)
        ]
        if not items:
            continue
        releases.append(
            {
                "version": release["version"],
                "date": release.get("date") or "",
                "status": release.get("status") or "",
                "current": release["version"] == APP_VERSION,
                "items": items,
            }
        )
    return releases
