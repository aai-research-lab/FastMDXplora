"""STRUCTURE.md names every module of the package.

The map of the repository had fallen 82 files behind: the Agent's package,
the validation package, most of the GUI's modules and pages. A reader new
to the code reads it first, so a module it does not name is one they are
not told exists.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "fastmdxplora"


def _modules() -> list[Path]:
    found = [path for path in PACKAGE.rglob("*.py")
             if path.name != "__init__.py" and "__pycache__" not in path.parts]
    static = PACKAGE / "gui" / "static"
    found += [path for path in static.glob("*") if path.suffix in (".js", ".css")]
    return sorted(found)


def test_every_module_and_page_is_named():
    text = (ROOT / "STRUCTURE.md").read_text(encoding="utf-8")
    missing = [str(path.relative_to(PACKAGE)) for path in _modules()
               if not re.search(rf"(?<![\w-]){re.escape(path.name)}(?![\w-])", text)]
    assert missing == [], f"STRUCTURE.md does not name: {missing}"


def test_every_package_is_a_folder_of_the_map():
    text = (ROOT / "STRUCTURE.md").read_text(encoding="utf-8")
    packages = sorted(path.parent.name for path in PACKAGE.glob("*/__init__.py"))
    assert [name for name in packages if f"{name}/" not in text] == []
