"""The source readers must follow shipped modules and reject unrelated paths."""
from pathlib import Path
import json
import shutil
import subprocess
from uuid import uuid4

import pytest

import frontend_source


@pytest.fixture
def graph(monkeypatch):
    output = Path(__file__).resolve().parents[2] / "output" / "refactor-validation" / "phase4"
    directory = output / f"frontend-source-{uuid4().hex}"
    static = directory / "app" / "static"
    scripts = static / "js"
    scripts.mkdir(parents=True)
    index = static / "index.html"
    index.write_text('<script type="module" src="static/js/app.js?v=current"></script>', encoding="utf-8")
    monkeypatch.setattr(frontend_source, "STATIC_ROOT", static)
    monkeypatch.setattr(frontend_source, "INDEX", index)
    try:
        yield scripts
    finally:
        resolved = directory.resolve()
        if not resolved.is_relative_to(output.resolve()):
            raise ValueError("Frontend fixture cleanup escaped output directory")
        shutil.rmtree(resolved)


def test_graph_follows_multiline_named_imports_once_and_excludes_unreachable_copy(graph):
    (graph / "app.js").write_text("import {\n  shared\n} from './shared.js?v=current';\nimport './views.js';\n", encoding="utf-8")
    (graph / "shared.js").write_text("import './app.js';\nexport function shared(){\n  const value = 7;\n  return value;\n}\nexport const other = 3;\n", encoding="utf-8")
    (graph / "views.js").write_text("import { shared } from './shared.js';\nexport function view(){return shared();}\n", encoding="utf-8")
    (graph / "abandoned.js").write_text("function oldOnly(){}\nfunction shared(){return 0;}\n", encoding="utf-8")
    sources = frontend_source.frontend_sources()
    assert [path.name for path in sources] == ["shared.js", "views.js", "app.js"]
    assert "oldOnly" not in frontend_source.read_frontend_source()
    function = frontend_source.frontend_function_source("shared")
    assert function.startswith("function shared(){")
    assert "const value = 7" in function and "return value" in function
    assert "export const other" not in function
    with pytest.raises(ValueError, match="found 0"):
        frontend_source.frontend_function_source("oldOnly")


@pytest.mark.parametrize("specifier", ["../../../outside.js", "https://outside.invalid/app.js", "//outside.invalid/app.js", "package-name"])
def test_graph_rejects_imports_outside_static_root(graph, specifier):
    (graph / "app.js").write_text(f"import '{specifier}';\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Frontend"):
        frontend_source.frontend_sources()


def test_graph_fails_on_ambiguous_function_owner(graph):
    (graph / "app.js").write_text("import './views.js';\nfunction shared(){}\n", encoding="utf-8")
    (graph / "views.js").write_text("export function shared(){}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="found 2"):
        frontend_source.frontend_function_source("shared")


def test_graph_resolves_import_map_targets_and_version_queries(graph):
    frontend_source.INDEX.write_text('<script type="importmap">{"imports":{"./static/js/mapped.js":"./static/js/current.js?v=current"}}</script><script type="module" src="static/js/app.js?v=current"></script>', encoding="utf-8")
    (graph / "app.js").write_text("import { shared } from './mapped.js';\n", encoding="utf-8")
    (graph / "current.js").write_text("export function shared(){return 9;}\n", encoding="utf-8")
    (graph / "mapped.js").write_text("export function oldOnly(){}\n", encoding="utf-8")
    assert [path.name for path in frontend_source.frontend_sources()] == ["current.js", "app.js"]
    assert "oldOnly" not in frontend_source.read_frontend_source()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_python_and_node_readers_agree_on_the_shipped_dependency_graph():
    reader = Path(__file__).with_name("frontend_source.js")
    response = subprocess.run([
        shutil.which("node"), "-e",
        "const reader=require(process.argv[1]);console.log(JSON.stringify([...reader.frontendSources().keys()]));",
        str(reader),
    ], check=True, capture_output=True, text=True, encoding="utf-8")
    assert [Path(filename) for filename in json.loads(response.stdout)] == list(frontend_source.frontend_sources())
