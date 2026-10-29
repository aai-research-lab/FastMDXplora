"""A test that drives a browser is skipped where Playwright is not installed.

Only the coverage leg, Ubuntu 3.13, installs Playwright. Three tests
imported it as their first line, before the helper that skips without it,
so they failed on every other leg (CI run #729).
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent

#: Fixtures pytest itself gives, none of which skips without Playwright.
BUILT_IN = {"tmp_path", "tmp_path_factory", "monkeypatch", "capsys", "capfd",
            "caplog", "request", "recwarn", "self"}


def _imports_playwright(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0] == "playwright"
    if isinstance(node, ast.Import):
        return any(a.name.split(".")[0] == "playwright" for a in node.names)
    return False


def _skips_without_it(node: ast.AST) -> bool:
    return any(isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "importorskip"
               and c.args and isinstance(c.args[0], ast.Constant)
               and str(c.args[0].value).startswith("playwright")
               for c in ast.walk(node))


def _marks_the_file_skipped(statement: ast.AST) -> bool:
    """`pytestmark = pytest.mark.skip...`, which skips every test in it."""
    return (isinstance(statement, ast.Assign)
            and any(getattr(target, "id", "") == "pytestmark" for target in statement.targets)
            and "skip" in ast.dump(statement.value))


def _caught(function: ast.AST, statement: ast.AST) -> bool:
    """Whether the import sits in a try that catches its absence."""
    return any(isinstance(t, ast.Try) and any(
        h.type is None or "ImportError" in ast.dump(h.type)
        or "ModuleNotFoundError" in ast.dump(h.type) for h in t.handlers)
        and any(statement is c for s in t.body for c in ast.walk(s))
        for t in ast.walk(function))


def _unguarded(tree: ast.Module, name: str) -> list[str]:
    """The tests in a file that import Playwright themselves before anything
    could skip them: no skip on the file, the class or the test, no
    importorskip of Playwright before the import, and no fixture but
    pytest's own that could skip first."""
    if any(_skips_without_it(s) or _marks_the_file_skipped(s) for s in tree.body
           if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))):
        return []
    tests = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            tests += [(f"{node.name}.{m.name}", m, node.decorator_list) for m in node.body
                      if isinstance(m, ast.FunctionDef) and m.name.startswith("test_")]
        elif isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            tests.append((node.name, node, []))
    found = []
    for test, function, above in tests:
        imports = [c for c in ast.walk(function) if _imports_playwright(c)]
        if not imports:
            continue
        if "skip" in " ".join(ast.dump(d) for d in [*above, *function.decorator_list]):
            continue
        if {a.arg for a in function.args.args} - BUILT_IN:
            continue
        if all(_caught(function, i) for i in imports):
            continue
        first = min(i.lineno for i in imports)
        if any(_skips_without_it(c) and c.lineno < first for c in ast.walk(function)
               if isinstance(c, ast.Call)):
            continue
        found.append(f"{name}::{test}")
    return found


def test_every_browser_test_skips_without_playwright() -> None:
    unguarded = []
    for path in sorted(TESTS.glob("test_*.py")):
        unguarded += _unguarded(ast.parse(path.read_text(encoding="utf-8")), path.name)
    assert not unguarded, (
        f"these import Playwright before anything skips them: {unguarded}. Call "
        "pytest.importorskip('playwright.sync_api') first, as the legs without "
        "it are the ones that cannot run them.")


def test_the_check_finds_a_test_that_imports_it_first() -> None:
    source = (
        "import pytest\n"
        "def _session():\n"
        "    pytest.importorskip('playwright.sync_api')\n"
        "def test_first(tmp_path):\n"
        "    from playwright.sync_api import sync_playwright\n"
        "    _session()\n"
        "def test_skipped_first(tmp_path):\n"
        "    sync = pytest.importorskip('playwright.sync_api').sync_playwright\n"
        "    from playwright.sync_api import Page\n"
        "def test_by_a_fixture(dashboard):\n"
        "    from playwright.sync_api import sync_playwright\n"
        "def test_other_skipped_first(tmp_path):\n"
        "    pytest.importorskip('numpy')\n"
        "    from playwright.sync_api import sync_playwright\n"
        "def test_caught(tmp_path):\n"
        "    try:\n"
        "        from playwright.sync_api import sync_playwright\n"
        "    except ImportError:\n"
        "        pytest.skip('no playwright')\n")
    assert _unguarded(ast.parse(source), "x.py") == [
        "x.py::test_first", "x.py::test_other_skipped_first"]
    marked = "import pytest\npytestmark = pytest.mark.skipif(True, reason='x')\n" + source
    assert _unguarded(ast.parse(marked), "x.py") == []
