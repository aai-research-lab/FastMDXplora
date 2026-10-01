"""The Report page renders the report on any install, figures and all.

`markdown` was in the [pdf] extra alone, so a pip install without it showed
the report in the GUI as plain Markdown: no figure in it and no chip under
one. CI's browser job installs `.[test]` and `.[md]`, and its two chip
tests failed on exactly that (`rendered: 'plain'`). It is a dependency of
the package now, in pyproject.toml and the conda recipe both.
"""

from __future__ import annotations

from pathlib import Path

from tests._toml import load_toml

REPO = Path(__file__).resolve().parent.parent


def test_markdown_is_a_dependency_of_the_package():
    project = load_toml(REPO / "pyproject.toml")["project"]
    assert any(req.replace(" ", "").startswith("markdown>=") for req in project["dependencies"])
    assert not any("markdown" in req for req in project["optional-dependencies"]["pdf"])
    recipe = (REPO / "recipes" / "fastmdxplora" / "recipe.yaml").read_text(encoding="utf-8")
    run = recipe[recipe.index("  run:"):recipe.index("\ntests:")]
    assert run.index("- markdown") < run.index("- weasyprint")


def test_the_report_is_rendered_with_its_figures():
    from fastmdxplora.gui.report_page import render_markdown

    html, rendered = render_markdown("![RMSD](../analysis/rmsd/rmsd.png)\n")
    assert rendered == "html"
    assert '<img alt="RMSD" src="../analysis/rmsd/rmsd.png"' in html
