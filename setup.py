"""Builds the package with its docs inside it.

The Agent answers questions about the software from the software's own docs
(``fastmdxplora.software_docs``). Those are the Markdown pages in ``docs/``,
outside the package, so a wheel would not carry them and an installed copy
would have none to read. This copies them into the built package as
``fastmdxplora/_docs`` when it is built, so the docs read are those of the
version installed, offline. Everything else about the build is in
``pyproject.toml``; an editable install reads ``docs/`` where it is.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

HERE = Path(__file__).resolve().parent
#: Where the pages are, and where in the built package they go.
DOCS = HERE / "docs"
PACKAGED = Path("fastmdxplora") / "_docs"


class BuildWithDocs(build_py):
    """``build_py``, then the docs' pages copied in beside the package."""

    def run(self) -> None:
        super().run()
        if getattr(self, "editable_mode", False):
            return
        if not copy_docs(DOCS, Path(self.build_lib) / PACKAGED):
            self.warn(f"no docs/*.md beside setup.py ({DOCS}): this build carries no "
                      "docs, and its Agent will say it has none to read")


def copy_docs(source: Path, target: Path) -> list[Path]:
    """Each Markdown page of ``source`` copied into ``target``, which holds
    nothing else; the pages copied. An sdist without its docs builds a wheel
    without them, and the Agent then says it has no docs to read."""
    pages = sorted(source.glob("*.md")) if source.is_dir() else []
    if target.exists():
        shutil.rmtree(target)
    if not pages:
        return []
    target.mkdir(parents=True)
    copied = []
    for page in pages:
        shutil.copyfile(page, target / page.name)
        copied.append(target / page.name)
    return copied


if __name__ == "__main__":
    setup(cmdclass={"build_py": BuildWithDocs})
