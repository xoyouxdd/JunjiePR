"""Read the application's actual HTML entry and reachable local JS modules.

Source assertions intentionally inspect only the dependency graph used by the
page; an abandoned copy of a function cannot satisfy a regression assertion.
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import json
import re
from urllib.parse import unquote, urljoin, urlsplit


STATIC_ROOT = Path(__file__).resolve().parents[1] / "app" / "static"
INDEX = STATIC_ROOT / "index.html"
IMPORT_PATTERN = re.compile(
    r"^[ \t]*(?:import|export)\s+(?:[\w*$ {},\n]+\s+from\s+)?"
    r"(?P<quote>['\"])(?P<specifier>[^'\"\n]+)(?P=quote)", re.MULTILINE,
)
DECLARATION_PATTERN = re.compile(
    r"^(?:export[ \t]+)?(?:(?:async[ \t]+)?function\b|const\b|let\b|class\b|import\b|export\b)",
    re.MULTILINE,
)


class _EntryScripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []
        self.imports: dict[str, str] = {}
        self._import_map: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attributes = dict(attrs)
        if attributes.get("type") == "importmap":
            self._import_map = []
        if attributes.get("src") and attributes.get("type", "") in {"", "module", "text/javascript"}:
            self.sources.append(attributes["src"])

    def handle_data(self, data: str) -> None:
        if self._import_map is not None:
            self._import_map.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._import_map is not None:
            self.imports.update(json.loads("".join(self._import_map)).get("imports", {}))
            self._import_map = None


def _local_path(specifier: str, parent: Path, *, entry: bool = False, imports: dict[str, str] | None = None) -> Path:
    if imports and not entry:
        page_url = "https://fixture.invalid/"
        module_base = page_url + parent.relative_to(STATIC_ROOT.parent).as_posix().strip(".") + "/"
        key = urljoin(module_base, specifier) if specifier.startswith(("./", "../", "/")) else specifier
        mappings = {urljoin(page_url, name) if name.startswith(("./", "../", "/")) else name: target for name, target in imports.items()}
        target = mappings.get(key)
        if target is None:
            prefixes = [name for name in mappings if name.endswith("/") and key.startswith(name)]
            if prefixes:
                prefix = max(prefixes, key=len)
                target = mappings[prefix] + key[len(prefix):]
        if target is not None:
            return _local_path(target, STATIC_ROOT.parent, entry=True)
    url = urlsplit(specifier)
    if url.scheme or url.netloc:
        raise ValueError(f"Frontend source must be local: {specifier}")
    relative = unquote(url.path)
    if not entry and not relative.startswith(("./", "../", "/")):
        raise ValueError(f"Frontend import must be relative: {specifier}")
    candidate = (STATIC_ROOT.parent / relative.lstrip("/")) if relative.startswith("/") else parent / relative
    resolved = candidate.resolve()
    if not resolved.is_relative_to(STATIC_ROOT.resolve()):
        raise ValueError(f"Frontend source escapes static root: {specifier}")
    return resolved


def frontend_sources() -> dict[Path, str]:
    """Return reachable modules once, in dependency-first order."""
    parser = _EntryScripts()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    if not parser.sources:
        raise ValueError("Application HTML has no JS entry")
    visited: set[Path] = set()
    sources: dict[Path, str] = {}

    def visit(path: Path) -> None:
        if path in visited:
            return
        visited.add(path)
        source = path.read_text(encoding="utf-8")
        for match in IMPORT_PATTERN.finditer(source):
            visit(_local_path(match["specifier"], path.parent, imports=parser.imports))
        sources[path] = source

    for specifier in parser.sources:
        visit(_local_path(specifier, STATIC_ROOT.parent, entry=True))
    return sources


def read_frontend_source() -> str:
    """Join real sources for existing cross-module text assertions."""
    return "\n".join(frontend_sources().values())


def frontend_function_source(name: str) -> str:
    """Extract one top-level real function for an isolated Node/VM fixture.

    The module export prefix is removed because the fixture supplies its own
    dependency stubs. Imports and neighbouring declarations are never copied.
    """
    pattern = re.compile(rf"^(?:export[ \t]+)?(?:async[ \t]+)?function[ \t]+{re.escape(name)}\(", re.MULTILINE)
    matches = [(source, match) for source in frontend_sources().values() for match in pattern.finditer(source)]
    if len(matches) != 1:
        raise ValueError(f"Expected one reachable function {name}, found {len(matches)}")
    source, match = matches[0]
    next_declaration = DECLARATION_PATTERN.search(source, match.end())
    fragment = source[match.start():next_declaration.start() if next_declaration else len(source)]
    return re.sub(r"^([ \t]*)export[ \t]+", r"\1", fragment, count=1)
