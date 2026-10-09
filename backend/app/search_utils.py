"""Literal LIKE search escaping for SQL query parameters."""
from __future__ import annotations



def like_escaped_pattern(keyword: str, escape_char: str = "\\") -> str:
    """Wrap a user keyword in a LIKE pattern with wildcards escaped."""
    value = str(keyword or "").strip()
    escaped = value.replace(escape_char, escape_char * 2).replace("%", escape_char + "%").replace("_", escape_char + "_")
    return f"%{escaped}%"
