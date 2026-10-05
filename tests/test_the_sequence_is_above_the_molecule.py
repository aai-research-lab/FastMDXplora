"""The sequence is above the molecule, numbered, and selects in it.

Each chain of the polymer is a line of one-letter codes over the canvas,
the structure's own residue numbers written over every fifth residue, a gap
where numbers are missing, and a bar under each residue in a helix or a
strand in the frame shown. A click selects a residue, a drag a run, Shift
extends and Ctrl adds or removes one; the selection is rendered in the
structure, an atom clicked there selects its residue here, and the keyboard
does what the mouse does.

Trypsin (3PTB) for the numbering: chymotrypsin's, with numbers missing and
insertion codes. Haemoglobin (1HHO) for the frames: chain A stretched out
of its helices in the second half, chain B kept.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
pytest.importorskip("playwright.sync_api")

DATA = Path(__file__).parent / "data" / "assemblies"
STATE = "window.FastMDXMoleculeViewer.STATE"
SEQ = "window.FastMDXSequence"
ONE = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E",
       "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F",
       "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V"}


def _residues_of(pdb: Path) -> list[tuple[str, int, str, str]]:
    """The protein's residues in the file's order: chain, number, insertion
    code, name (MDTraj drops insertion codes)."""
    seen: list[tuple[str, int, str, str]] = []
    for line in pdb.read_text(encoding="utf-8").splitlines():
        if not line.startswith("ATOM") or line[17:20].strip() not in ONE:
            continue
        key = (line[21].strip(), int(line[22:26]), line[26].strip(), line[17:20].strip())
        if not seen or seen[-1] != key:
            seen.append(key)
    return seen


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _open(browser, study: Path):
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(60000)
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.goto(session.url + "#viewer", wait_until="domcontentloaded")

    def close():
        page.close()
        session.server.shutdown()

    if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
        close()
        pytest.skip("this browser has no WebGL, so the viewer cannot render")
    page.wait_for_function(f"() => {SEQ} && {SEQ}.state.residues.length > 0")
    # Closed until opened.
    page.click("#sequence-strip > summary")
    return page, close


@pytest.fixture(scope="module")
def trypsin(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("sequence") / "study"
    (root / "setup").mkdir(parents=True)
    raw = root / "setup" / "input.pdb"
    raw.write_bytes(gzip.decompress((DATA / "3PTB.pdb.gz").read_bytes()))
    # The deposited lines themselves, so the insertion codes are kept.
    kept = [line for line in raw.read_text(encoding="utf-8").splitlines()
            if line.startswith(("ATOM", "HETATM")) and line[17:20].strip() != "HOH"]
    (root / "setup" / "topology.pdb").write_text("\n".join(kept + ["END", ""]),
                                                 encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def page(browser, trypsin):
    opened, close = _open(browser, trypsin)
    yield opened
    close()


def _strip(page) -> list[dict]:
    return page.evaluate(f"""() => {SEQ}.state.residues.map((r) => ({{
        chain: r.chain, resi: r.resi, icode: r.icode, resn: r.resn, ss: r.ss,
        code: r.node.textContent, n: r.node.dataset.n || null, row: r.row,
        first: r.first, last: r.last, selected: r.node.classList.contains('selected'),
        aria: r.node.getAttribute('aria-selected')}}))""")


def _selected(page) -> list[int]:
    return [k for k, r in enumerate(_strip(page)) if r["selected"]]


def _drag(page, a: int, b: int) -> None:
    first = page.locator(f"#seq-{a}")
    first.scroll_into_view_if_needed()
    one = first.bounding_box()
    page.mouse.move(one["x"] + 3, one["y"] + 6)
    page.mouse.down()
    last = page.locator(f"#seq-{b}")
    two = last.bounding_box()
    page.mouse.move(two["x"] + 3, two["y"] + 6, steps=6)
    page.mouse.up()


def _rendered_selection(page) -> int:
    """The atoms of the selection component the structure renders."""
    page.wait_for_timeout(200)
    return page.evaluate(f"""() => {{
        const c = {STATE}.engine.components && {STATE}.engine.components.selected;
        return c && c.cell && c.cell.obj ? c.cell.obj.data.elementCount : 0;
    }}""")


def test_each_chain_is_a_line_numbered_by_its_own_residues(page, trypsin):
    strip = _strip(page)
    expected = _residues_of(trypsin / "setup" / "topology.pdb")
    assert [(r["chain"], r["resi"], r["icode"], r["resn"]) for r in strip] == expected
    assert "".join(r["code"] for r in strip) == "".join(ONE[name] for *_, name in expected)
    # Numbered over every fifth residue, by the structure's numbers only.
    assert [r["n"] for r in strip] == [
        str(resi) if resi % 5 == 0 and not icode else None for _, resi, icode, _ in expected]
    assert any(r["icode"] for r in strip)
    # A gap where numbers are missing, none elsewhere.
    jumps = sum(1 for a, b in zip(expected, expected[1:])
                if not a[2] and not b[2] and a[0] == b[0] and b[1] - a[1] > 1)
    assert jumps > 0
    assert page.locator("#seq-scroll .seq-gap").count() == jumps
    rows = page.locator("#seq-scroll .seq-row")
    assert rows.count() == len({chain for chain, *_ in expected})
    assert rows.first.get_attribute("aria-label") == f"Chain A, {len(expected)} residues"
    # The ligand is no residue of the sequence.
    assert all(r["resn"] != "BEN" for r in strip)


def test_a_drag_selects_a_run_and_the_structure_renders_it(page):
    strip = _strip(page)
    _drag(page, 10, 19)
    page.wait_for_function(f"() => {STATE}.selection && {STATE}.selection.residues.length === 10")
    assert _selected(page) == list(range(10, 20))
    atoms = sum(r["last"] - r["first"] + 1 for r in strip[10:20])
    assert page.evaluate(f"() => {STATE}.selection.atoms.length") == atoms
    assert _rendered_selection(page) == atoms
    a, b = strip[10], strip[19]
    said = page.text_content("#seq-said")
    assert said == (f"Selected A {a['resi']}{a['icode']} to {b['resi']}{b['icode']}: "
                    f"10 residues, {atoms} atoms")
    assert {r["aria"] for r in _strip(page)[10:20]} == {"true"}


def test_shift_extends_and_ctrl_adds_or_removes(page):
    page.click("#seq-30")
    page.wait_for_function(f"() => {STATE}.selection.residues.length === 1")
    page.click("#seq-34", modifiers=["Shift"])
    page.wait_for_function(f"() => {STATE}.selection.residues.length === 5")
    page.click("#seq-40", modifiers=["Control"])
    page.wait_for_function(f"() => {STATE}.selection.residues.length === 6")
    assert _selected(page) == [30, 31, 32, 33, 34, 40]
    page.click("#seq-32", modifiers=["Control"])
    page.wait_for_function(f"() => {STATE}.selection.residues.length === 5")
    assert _selected(page) == [30, 31, 33, 34, 40]
    page.click("#seq-clear")
    page.wait_for_function(f"() => !{STATE}.selection")
    assert _selected(page) == [] and _rendered_selection(page) == 0


def test_one_residue_is_named_with_its_selection(page):
    strip = _strip(page)
    page.click("#seq-50")
    page.wait_for_selector('#selection-strings .selection-string[data-of="residue"]',
                           state="attached")
    residue = page.text_content('#selection-strings [data-of="residue"] code')
    assert f"resSeq {strip[50]['resi']}" in residue
    assert page.text_content("#seq-said").startswith(
        f"Selected {strip[50]['resn']} {strip[50]['resi']}{strip[50]['icode']}, chain A, ")


def test_an_atom_clicked_in_the_structure_selects_its_residue(page):
    from tests import viewer_hooks as hooks

    strip = _strip(page)
    k = next(i for i, r in enumerate(strip) if r["resi"] == 189 and not r["icode"])
    atom = hooks.click(page, resi=189, atom="CA")
    assert atom
    page.wait_for_function(f"(k) => {SEQ}.state.selected.has(k) && {SEQ}.state.selected.size === 1",
                           arg=k)
    other = page.evaluate(f"""() => {{
        const atom = window.FastMDXMoleculeViewer.atoms({{resi: 195, atom: 'CA'}})[0];
        {STATE}.engine.click(atom.index, {{shift: true}});
        return atom.index;
    }}""")
    assert other is not None
    page.wait_for_function(f"() => {SEQ}.state.selected.size === 2")
    assert _rendered_selection(page) == sum(strip[i]["last"] - strip[i]["first"] + 1
                                            for i in _selected(page))


def test_the_keyboard_moves_selects_and_clears(page):
    row = page.locator("#seq-scroll .seq-row").first
    row.focus()
    page.keyboard.press("Home")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    page.wait_for_function(f"() => {STATE}.selection && {STATE}.selection.residues.length === 1")
    assert _selected(page) == [1]
    for _ in range(3):
        page.keyboard.press("Shift+ArrowRight")
    page.wait_for_function(f"() => {STATE}.selection.residues.length === 4")
    assert _selected(page) == [1, 2, 3, 4]
    assert row.get_attribute("aria-activedescendant") == "seq-4"
    # Space selects here; it does not play.
    page.keyboard.press(" ")
    page.wait_for_function(f"() => {STATE}.selection.residues.length === 1")
    assert not page.evaluate(f"() => {STATE}.playbackPlaying")
    page.keyboard.press("Escape")
    page.wait_for_function(f"() => !{STATE}.selection")


def test_a_double_click_centres_on_the_selection(page):
    page.click("#seq-60")
    before = page.evaluate(f"() => {STATE}.engine.cameraSnapshot().target")
    page.dblclick("#seq-60")
    page.wait_for_timeout(100)
    page.wait_for_function(
        f"() => !{STATE}.engine.plugin.canvas3d.camera.transition.inTransition")
    after = page.evaluate(f"() => {STATE}.engine.cameraSnapshot().target")
    centre = page.evaluate(f"""() => {{
        const r = {SEQ}.state.residues[60];
        const atoms = {STATE}.engine.atoms([...Array(r.last - r.first + 1).keys()]
            .map((i) => i + r.first));
        return ['x', 'y', 'z'].map((a) => atoms.reduce((s, t) => s + t[a], 0) / atoms.length);
    }}""")
    assert np.linalg.norm(np.subtract(after, centre)) < np.linalg.norm(np.subtract(before, centre))
    assert np.linalg.norm(np.subtract(after, centre)) < 3.0
    assert page.errors == []


@pytest.fixture(scope="module")
def frames_page(browser, tmp_path_factory):
    from fastmdxplora.gui.trajectory_frames import frames_info
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    study = _helical_study(tmp_path_factory.mktemp("sequence-frames") / "study", analyse=False)
    assert frames_info(study)["available"]
    opened, close = _open(browser, study)
    opened.evaluate("async () => window.FastMDXMoleculeViewer.loadPlayback("
                    "await (await fetch('/api/frames-info')).json())")
    opened.wait_for_function(f"() => {STATE}.mode === 'playback'"
                             f" && {STATE}.engine.frameCount() === 6")
    opened.wait_for_function(f"() => {STATE}.secondaryStructure"
                             f" && {STATE}.secondaryStructure.of === 'frames'")
    yield opened
    close()


def _seek(page, frame: int) -> None:
    page.evaluate(f"() => window.dispatchEvent(new CustomEvent("
                  f"'dashboard:trajectory-seek', {{detail: {{frame: {frame}}}}}))")
    page.wait_for_function(f"(n) => {STATE}.engine.frame() === n", arg=frame)


def test_each_frame_shades_its_own_secondary_structure(frames_page):
    page = frames_page
    said = page.evaluate("async () => (await fetch('/api/secondary-structure?of=frames')).json()")
    assert said["available"]
    keys = [f"{row[0]}|{row[1]}|{row[2]}|{row[3]}" for row in said["residues"]]
    by_frame = {}
    for frame in (0, 5):
        _seek(page, frame)
        expected = "".join({"H": "h", "E": "s"}.get(code, "c") for code in said["frames"][frame])
        page.wait_for_function(f"(e) => {SEQ}.state.residues.map((r) => r.ss).join('') === e",
                               arg=expected)
        strip = _strip(page)
        assert [f"{r['chain']}|{r['resi']}|{r['icode']}|{r['resn']}" for r in strip] == keys
        by_frame[frame] = strip
    helices = {frame: {chain: sum(1 for r in strip if r["chain"] == chain and r["ss"] == "h")
                       for chain in ("A", "B")} for frame, strip in by_frame.items()}
    # Chain A leaves its helices; chain B keeps them.
    assert helices[5]["A"] < helices[0]["A"] / 2
    assert helices[5]["B"] == pytest.approx(helices[0]["B"], abs=6)
    assert page.locator("#seq-scroll .ss-h").count() == helices[5]["A"] + helices[5]["B"]


def test_the_selection_stays_as_the_frames_play(frames_page):
    page = frames_page
    _seek(page, 0)
    _drag(page, 2, 8)
    page.wait_for_function(f"() => {STATE}.selection && {STATE}.selection.residues.length === 7")
    atoms = page.evaluate(f"() => {STATE}.selection.atoms.length")
    assert _rendered_selection(page) == atoms
    for frame in (3, 5):
        _seek(page, frame)
        assert _rendered_selection(page) == atoms
        assert _selected(page) == list(range(2, 9))
    assert page.errors == []
