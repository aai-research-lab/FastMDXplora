"""The package's modules the GUI's routes can reach, found by reading them.

A route imports what it needs when it is first asked for. A page asks for
many things at once, each answered in a thread of its own, and the package's
imports go round in circles (`fastmdxplora.analysis` imports every analysis,
and each of those imports `analysis.plotting`). Two threads importing into
one circle together is refused by Python's import locks with "deadlock
detected", and the route that lost answered 500. The server therefore
imports these before it serves anything.

The list was kept by hand, and a hand-kept list trails the routes: it
lacked `gui.schema_payload`, and a first page load on macOS answered 500 to
`/api/schema`. It is now read from the source: every module of the package
that the server imports, at the top of a file or inside a function, and
every module those import, followed to the end.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PACKAGE = "fastmdxplora"
#: Where the package's source is: names are found as files under it, so
#: reading what a route imports imports nothing.
ROOT = Path(__file__).resolve().parent.parent


def _source_of(name: str) -> Path | None:
    parts = name.split(".")
    if parts[0] != PACKAGE:
        return None
    where = ROOT.joinpath(*parts[1:])
    for candidate in (where / "__init__.py", where.with_suffix(".py")):
        if candidate.is_file():
            return candidate
    return None


#: An import statement, at the top of a file or inside a function. Read as
#: text rather than parsed: the imports are all that is wanted, and parsing
#: a hundred and eighty files whole took most of a second. An example in a
#: docstring that matches names a real module or nothing, and a real module
#: imported early is harmless.
_IMPORT = re.compile(r"^[ \t]*import[ \t]+([\w.]+)", re.MULTILINE)
_FROM = re.compile(r"^[ \t]*from[ \t]+(\.*[\w.]*)[ \t]+import[ \t]+(\([^)]*\)|[^\n#]*)",
                   re.MULTILINE)


def _named_in(name: str, source: Path) -> set[str]:
    """The package's modules one file imports, wherever in it."""
    package = name if source.name == "__init__.py" else name.rpartition(".")[0]
    text = source.read_text(encoding="utf-8")
    named = set(_IMPORT.findall(text))
    for base, names in _FROM.findall(text):
        if base.startswith("."):
            level = len(base) - len(base.lstrip("."))
            parts = package.split(".")
            rest = base.lstrip(".")
            base = ".".join(parts[:len(parts) - level + 1] + ([rest] if rest else []))
        named.add(base)
        # `from fastmdxplora.gui import plan` names a module, not a
        # function; a name that is not one is dropped by the caller.
        for alias in re.sub(r"#[^\n]*", "", names).strip("() \n").split(","):
            word = alias.split()[0] if alias.split() else ""
            if word.isidentifier():
                named.add(f"{base}.{word}")
    return {n for n in named if n == PACKAGE or n.startswith(PACKAGE + ".")}


@lru_cache(maxsize=None)
def modules_reached_from(name: str) -> tuple[str, ...]:
    """Every module of the package `name` can import, itself excluded."""
    reached: set[str] = set()
    waiting = [name]
    while waiting:
        current = waiting.pop()
        source = _source_of(current)
        if source is None:
            continue
        for found in _named_in(current, source):
            if found not in reached and found != name and _source_of(found) is not None:
                reached.add(found)
                waiting.append(found)
    return tuple(sorted(reached))
