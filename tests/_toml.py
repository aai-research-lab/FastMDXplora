"""One TOML parser for the suite, on every Python this package supports.

`tomllib` is in the standard library from 3.11. This package declares
`requires-python = ">=3.10, <3.14"` and CI runs a 3.10 leg, where
`import tomllib` at module scope is not one failing test: it is a
collection error, and pytest abandons the whole run. Four test modules
read `pyproject.toml`; one of them imported `tomllib` at module scope, and
the 3.9 leg, when there was one, reported `3414/3519 tests collected ... 1 error` and stopped.
3,414 tests did not run because of one import.

`tomli` is the same parser under its pre-stdlib name, declared in the
`[test]` extra for exactly the versions that need it. Skipping those tests
below 3.11 was the other option and is worse: it would have hidden the
checks that read `pyproject.toml` on two of the five supported versions,
and a skip is invisible in a green run -- the reasoning already written
into the `[test]` extra about RDKit and PROPKA.

The import lives here so that the version split is decided once. See
`TestTheDeclaredPythonFloorIsReal` in `test_the_declarations_agree.py`,
which fails if a module-level import reaches past the declared floor
again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # Python 3.9, 3.10
    import tomli as _toml  # type: ignore[no-redef]


def load_toml(path: str | Path) -> dict[str, Any]:
    """Parse a TOML file. Which parser does it is decided in this module."""
    return _toml.loads(Path(path).read_text(encoding="utf-8"))
