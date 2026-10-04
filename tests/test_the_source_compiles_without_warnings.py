"""The package compiles without a warning, on every Python it supports.

`report/document.py` wrote `Mol\\*` in a docstring and a plain string, an
invalid escape sequence: a DeprecationWarning nobody saw on Python 3.10 and
3.11, and a SyntaxWarning printed above the banner of every `fastmdx` on
3.12. Every source file is compiled here with warnings as errors.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted(path for folder in ("src", "tests", "scripts")
                 for path in (ROOT / folder).rglob("*.py") if "__pycache__" not in path.parts)


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: str(path.relative_to(ROOT)))
def test_it_compiles_without_a_warning(path):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
