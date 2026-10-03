"""What the tests ask of the molecule viewer, in one place.

The viewer renders with Mol* behind its own engine (static/viewer-engine.js);
these are the page's and the engine's hooks the browser tests use, so a
test reads as what is done to the page, not how Mol* is driven.
"""

from __future__ import annotations

VIEWER = "window.FastMDXMoleculeViewer"

#: Whether the Viewer page's engine has rendered something.
RENDERED = f"() => !!({VIEWER} && {VIEWER}.STATE.engine && {VIEWER}.STATE.model)"

#: Whether the Overview's preview has rendered something.
MINI_RENDERED = f"() => !!({VIEWER} && {VIEWER}.STATE.miniEngine && {VIEWER}.STATE.miniModel)"

#: The atoms a selection names ({resn, chain, resi, atom, elem}), as rendered.
ATOMS = f"(selection) => {VIEWER}.atoms(selection)"

#: A click on the first atom a selection names; the atom clicked.
CLICK = f"""(selection) => {{
    const atom = {VIEWER}.atoms(selection)[0];
    if (atom) {VIEWER}.STATE.engine.click(atom.index);
    return atom || null;
}}"""

#: The Viewer page's engine's canvas.
CANVAS = f"() => {VIEWER}.STATE.engine.plugin.canvas3d.webgl.gl.canvas"


def atoms(page, **selection) -> list[dict]:
    return page.evaluate(ATOMS, selection)


def click(page, **selection) -> dict | None:
    return page.evaluate(CLICK, selection)
