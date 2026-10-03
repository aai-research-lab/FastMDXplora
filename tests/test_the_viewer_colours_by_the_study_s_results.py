"""The viewer colours the protein by the study's per-residue results.

Each residue's RMSF, solvent-accessible surface, contact with the ligand and
order parameter, and the fluctuation its B-factor implies, were plots
against residue number, and the residues a reader wanted had to be found by
number. They are now colourings of the protein in the Viewer, read from the
files the analyses wrote (gui/by_residue.py), with a colour bar that the
saved picture carries too and each residue's values in the Selection tab.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.gui.by_residue import values_by_residue
from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study


def _analysis(root: Path, name: str, files: dict[str, str], *, options=None,
              findings=None, selection=None, folder=None) -> None:
    where = root / "analysis" / (folder or name)
    where.mkdir(parents=True, exist_ok=True)
    (where / "options.json").write_text(json.dumps(
        {"analysis": name, "selection": selection, "options": options or {},
         "findings": findings or {}}), encoding="utf-8")
    for file, text in files.items():
        (where / file).write_text(text, encoding="utf-8")


def _by_key(root: Path) -> dict[str, dict]:
    return {entry["key"]: entry for entry in values_by_residue(root)["properties"]}


NUMBERS = "# rmsf: whitespace-delimited, no column header. Read with np.loadtxt(path).\n"


def test_each_result_is_read_as_it_was_written(tmp_path):
    root = tmp_path / "study"
    (root / "analysis").mkdir(parents=True)
    (root / "analysis" / "analysis_manifest.json").write_text(
        json.dumps({"n_frames": 250}), encoding="utf-8")
    _analysis(root, "rmsf", {"rmsf.dat": NUMBERS + "1 0.05\n2 0.10\n3 nan\n"},
              options={"per_residue": True}, selection="name CA")
    _analysis(root, "bfactor_comparison", {
        "bfactor_comparison.dat": "# bfactor_comparison: whitespace-delimited\n"
                                  "1 0.05 0.20\n2 0.10 0.30\n"})
    _analysis(root, "sasa", {"sasa.dat": "chain,residue,mean_sasa_nm2,std_sasa_nm2\n"
                                         "A,1,0.5,0.1\nB,1,1.5,0.2\n"},
              options={"mode": "average_residue"})
    _analysis(root, "pl_contacts", {
        "pl_contacts.dat": "frame,n_contacts\n0,2\n",
        "pl_contacts_per_residue.csv": "residue,contact_frequency\n"
                                       "A:ASP189,0.9\nB:GLY184A,0.25\nnot a residue,1\n"},
              options={"ligand_resname": "BEN", "cutoff": 0.4, "protein_selection": "protein"})
    _analysis(root, "order_parameters", {
        "order_parameters.dat": "# order_parameters: whitespace-delimited.\n"
                                "# columns: residue  S2_simulated  S2_measured\n"
                                "2 0.85 0.8\n3 0.4 nan\n"},
              findings={"order_parameters": {"not_a_measurement": "The two halves disagree."}})
    found = _by_key(root)

    rmsf = found["rmsf"]
    assert rmsf["values"] == [[None, 1, "", 0.05], [None, 2, "", 0.10]]
    assert rmsf["unit"] == "nm" and rmsf["absent"] is None
    assert "the 250 frames analysed" in rmsf["about"] and "alpha carbon" in rmsf["about"]
    # One scale for the two fluctuations, from zero, so a colour means one
    # amplitude in both.
    implied = found["bfactor_comparison"]
    assert implied["values"][1] == [None, 2, "", 0.30]
    assert (rmsf["low"], rmsf["high"]) == (implied["low"], implied["high"]) == (0.0, 0.30)

    assert found["sasa"]["values"] == [["A", 1, "", 0.5], ["B", 1, "", 1.5]]
    contacts = found["pl_contacts"]
    assert contacts["values"] == [["A", 189, "", 0.9], ["B", 184, "A", 0.25]]
    assert contacts["label"] == "Contact with BEN" and contacts["absent"] == 0.0
    assert (contacts["low"], contacts["high"]) == (0.0, 1.0)
    assert "within 0.4 nm of BEN" in contacts["about"]
    order = found["order_parameters"]
    assert order["values"] == [[None, 2, "", 0.85], [None, 3, "", 0.4]]
    assert order["reverse"] is True and "upper bounds" in order["about"]


def test_what_is_not_offered_and_why_nothing_breaks(tmp_path):
    root = tmp_path / "study"
    _analysis(root, "rmsf", {"rmsf.dat": NUMBERS + "1 0.05\n"}, options={"per_residue": False})
    _analysis(root, "sasa", {"sasa.dat": "frame,sasa_nm2\n0,10\n"}, options={"mode": "total"})
    _analysis(root, "rg", {"rg.dat": NUMBERS + "0 1.2\n"})
    _analysis(root, "pl_contacts", {"pl_contacts.dat": "frame,n_contacts\n"},
              options={"ligand_resname": "LIG"})
    _analysis(root, "order_parameters", {"order_parameters.dat": "not, a table\n\x00\x01"},
              folder="order_parameters")
    (root / "analysis" / "broken").mkdir()
    (root / "analysis" / "broken" / "options.json").write_text("{", encoding="utf-8")
    assert values_by_residue(root) == {"properties": []}
    assert values_by_residue(tmp_path / "nowhere") == {"properties": []}


def test_a_contact_over_part_of_the_protein_leaves_the_rest_unknown(tmp_path):
    root = tmp_path / "study"
    _analysis(root, "pl_contacts", {
        "pl_contacts_per_residue.csv": "residue,contact_frequency\nASP189,0.5\n"},
              options={"ligand_resname": "LIG", "protein_selection": "chainid 0"})
    contacts = _by_key(root)["pl_contacts"]
    assert contacts["absent"] is None
    assert "never that close" not in contacts["about"]


def test_a_per_frame_surface_is_averaged_by_chain(tmp_path):
    """A study analysed before the average beside the per-frame table was
    taken chain by chain has a wrong average; the per-frame table is read."""
    root = tmp_path / "study"
    _analysis(root, "sasa", {
        "sasa.dat": "frame,chain,residue,sasa_nm2\n0,A,1,1.0\n0,B,1,3.0\n1,A,1,2.0\n1,B,1,5.0\n",
        "sasa_average_per_residue.csv": "residue,mean_sasa_nm2,std_sasa_nm2\n1,2.75,1.7\n"},
              options={"mode": "residue"})
    assert _by_key(root)["sasa"]["values"] == [["A", 1, "", 1.5], ["B", 1, "", 4.0]]


# ---------------------------------------------------------------------------
# In the browser
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    return _helical_study(tmp_path_factory.mktemp("coloured") / "study")


def _open(study: Path, then) -> object:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900},
                                    accept_downloads=True)
            page.set_default_timeout(90000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => window.FastMDXMoleculeViewer"
                " && window.FastMDXMoleculeViewer.STATE.secondaryStructure")
            said = then(page)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    return said


#: The colour the viewer gives the alpha carbon of a residue.
COLOUR_OF = """([chain, resi, icode]) => { const v = window.FastMDXMoleculeViewer;
    const atom = v.atoms({resi, atom: 'CA'})
        .find((a) => (!chain || a.chain === chain) && (a.icode || ' ').trim() === (icode || ''));
    return atom ? v.byResidue.colourOf(atom) : 'missing'; }"""


def _rgb(colour: str) -> tuple[int, int, int]:
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def test_the_protein_is_coloured_by_its_rmsf(study):
    pytest.importorskip("playwright.sync_api")

    def look(page):
        page.wait_for_function("() => document.getElementById('viewer-color-results')")
        said = {"offered": page.eval_on_selector_all(
            "#viewer-color-results option", "o => o.map((x) => [x.value, x.textContent])")}
        page.select_option("#viewer-color", "result:rmsf")
        page.wait_for_function("() => !document.getElementById('viewer-legend').hidden")
        said["legend"] = page.inner_text("#viewer-legend")
        said["said"] = page.text_content("#viewer-colour-said")
        said["a"] = page.evaluate(COLOUR_OF, ["A", 60, ""])
        said["b"] = page.evaluate(COLOUR_OF, ["B", 60, ""])
        page.evaluate("""() => { const v = window.FastMDXMoleculeViewer;
            v.byResidue.describe(v.atoms({chain: 'B', resi: 60, atom: 'CA'})[0]); }""")
        said["rows"] = page.eval_on_selector_all(
            "#selection-tab-tbody tr",
            "rows => rows.map((r) => [r.cells[0].textContent, r.cells[1].textContent])")
        with page.expect_download() as download:
            page.click('[data-action="screenshot"]')
        said["picture"] = download.value.path().read_bytes()
        page.select_option("#viewer-color", "spectrum")
        said["after"] = page.evaluate(
            "() => document.getElementById('viewer-legend').hidden")
        return said

    said = _open(study, look)
    assert said["offered"] == [["result:rmsf", "RMSF"], ["result:sasa", "Mean SASA"]]
    assert said["legend"].splitlines()[0] == "RMSF (nm)"
    assert "287 of the 287 residues shown have a value" in said["said"]
    # Chain A was stretched out of its helices and chain B held: red and blue.
    red, blue = _rgb(said["a"]), _rgb(said["b"])
    assert red[0] > red[2] and blue[2] > blue[0]
    rows = dict(said["rows"])
    assert rows["Secondary structure"] == "Helix (DSSP)"
    assert rows["RMSF"].endswith(" nm") and rows["Mean SASA"].endswith(" nm²")
    # The picture carries the colour bar: blue at its left, red at its right.
    from PIL import Image

    image = Image.open(io.BytesIO(said["picture"])).convert("RGB")
    assert image.width >= 2400
    scale = image.width / 900
    row = round(image.height - 14 * scale - 56 * scale + 10 * scale + 23 * scale)
    left = image.getpixel((round(14 * scale + 12 * scale), row))
    right = image.getpixel((round(14 * scale + 188 * scale), row))
    assert left[2] > left[0] + 60 and right[0] > right[2] + 60
    assert said["after"] is True


def _numbered_with_a_code(root: Path) -> Path:
    """Ten alanines, the sixth numbered 5A, with an RMSF for each and a
    trajectory whose topology keeps the code; the prepared structure was
    written without it, so it has two residues 5."""
    (root / "setup").mkdir(parents=True)
    (root / "simulation").mkdir()
    numbers = [(1, ""), (2, ""), (3, ""), (4, ""), (5, ""), (5, "A"), (6, ""), (7, ""),
               (8, ""), (9, "")]
    lines, serial = [], 1
    for place, (number, code) in enumerate(numbers):
        for offset, name in enumerate(("N", "CA", "C", "O", "CB")):
            lines.append(f"ATOM  {serial:5d}  {name:<3} ALA A{number:4d}{code or ' '}   "
                         f"{3.8 * place + offset:8.3f}{0.5 * offset:8.3f}{0.0:8.3f}"
                         f"  1.00  0.00           {name[0]}")
            serial += 1
    text = "\n".join(lines) + "\nEND\n"
    (root / "setup" / "topology.pdb").write_text(
        "\n".join(line[:26] + " " + line[27:] if line.startswith("ATOM") else line
                  for line in text.splitlines()) + "\n", encoding="utf-8")
    (root / "simulation" / "trajectory_topology.pdb").write_text(text, encoding="utf-8")
    topology = md.load_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    md.Trajectory(np.repeat(topology.xyz, 3, axis=0), topology.topology).save_dcd(
        str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(
        json.dumps({"status": "completed"}), encoding="utf-8")
    rows = "".join(f"{number},{code},{0.01 * (place + 1)}\n"
                   for place, (number, code) in enumerate(numbers))
    _analysis(root, "rmsf", {"rmsf.dat": "residue,insertion,rmsf_nm\n" + rows},
              options={"per_residue": True})
    return root


def test_a_residue_named_twice_is_left_grey(tmp_path):
    """Two residues the structure shown cannot tell apart are not both given
    one residue's value: the prepared structure, written without insertion
    codes, has two residues 5, and the frames, read with the trajectory's
    topology, keep 5 and 5A apart."""
    pytest.importorskip("playwright.sync_api")
    root = _numbered_with_a_code(tmp_path / "study")

    def look(page):
        page.wait_for_function("() => document.getElementById('viewer-color-results')")
        page.select_option("#viewer-color", "result:rmsf")
        page.wait_for_function("() => !document.getElementById('viewer-legend').hidden")
        said = {"structure": page.evaluate("""() => { const v = window.FastMDXMoleculeViewer;
            return v.atoms({resi: 5, atom: 'CA'}).map((atom) => v.byResidue.colourOf(atom)); }""")}
        said["neighbour"] = page.evaluate(COLOUR_OF, ["A", 6, ""])
        said["legend"] = page.inner_text("#viewer-legend")
        said["structure_said"] = page.text_content("#viewer-colour-said")
        page.evaluate("() => window.FastMDXMoleculeViewer.loadPlayback()")
        page.wait_for_function(
            "() => window.FastMDXMoleculeViewer.STATE.secondaryStructure"
            " && window.FastMDXMoleculeViewer.STATE.secondaryStructure.of === 'frames'")
        said["frames"] = [page.evaluate(COLOUR_OF, ["A", 5, code]) for code in ("", "A")]
        said["frames_said"] = page.text_content("#viewer-colour-said")
        said["cartoon"] = page.evaluate(
            "() => window.FastMDXMoleculeViewer.STATE.secondaryStructure.applied")
        return said

    said = _open(root, look)
    assert said["structure"] == ["#5c5c66", "#5c5c66"]
    assert said["neighbour"] != "#5c5c66"
    assert "No value: 2" in said["legend"]
    assert "8 of the 10 residues shown have a value" in said["structure_said"]
    assert len(set(said["frames"])) == 2 and "#5c5c66" not in said["frames"]
    assert "10 of the 10 residues shown have a value" in said["frames_said"]
    # DSSP is had for the frames, which MDTraj reads as ten residues.
    assert said["cartoon"] is True


def test_rows_that_are_not_numbers_are_left_out(tmp_path):
    root = tmp_path / "study"
    _analysis(root, "rmsf", {"rmsf.dat": "residue,rmsf_nm\n1,0.0\nx,0.1\n2,0.0\n"},
              options={"per_residue": True})
    _analysis(root, "pl_contacts", {
        "pl_contacts_per_residue.csv": "residue,contact_frequency\nASP189,often\nSER190,0.5\n"},
              options={"ligand_resname": "LIG"})
    found = _by_key(root)
    # Every value zero: the range still spans something, so nothing divides by it.
    assert found["rmsf"]["values"] == [[None, 1, "", 0.0], [None, 2, "", 0.0]]
    assert found["rmsf"]["high"] > found["rmsf"]["low"]
    assert found["pl_contacts"]["values"] == [[None, 190, "", 0.5]]
