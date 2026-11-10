"""The Agent knows where the person is on three more pages.

The Analysis page names the section scrolled to and the analysis it is of;
the Config Builder its step, the setting last worked on, the settings
changed and whether the form holds changes no Config was made with; the
Viewer's residues chosen are read in the topology the analyses read, and
Ask about... names them in the Agent's box. Each hint says where to look:
the facts are read by the code, from the schema, the study's records or
its topology.
"""

from __future__ import annotations

import tempfile

import pytest

from fastmdxplora.agent.tools import MOST_CHANGED_HINTS, Toolbox, current_view_tool
from tests.test_the_workspace_says_its_studies import _study

TWO_CHAINS = """\
ATOM      1  N   ALA A  10       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A  10       1.458   0.000   0.000  1.00  0.00           C
ATOM      3  N   GLY B  48       3.000   0.000   0.000  1.00  0.00           N
ATOM      4  CA  GLY B  48       4.458   0.000   0.000  1.00  0.00           C
END
"""


def _look(hints, study=None):
    tool = current_view_tool(hints, active_root=str(study) if study else None)
    return Toolbox(extra=(tool,)).use("current_view", {})


# -- the Config Builder ---------------------------------------------------------------

def test_the_builder_s_step_setting_and_changes_are_said_with_the_schema_s_facts():
    look = _look({"page": "run", "section": "Protocol", "field": "simulation.duration_ns",
                  "changed": ["simulation.duration_ns = 50", "setup.forcefield = charmm36"],
                  "unsaved": True})
    assert look.ok
    said = look.said
    assert ("The person is in the Config Builder at its step 'Protocol', last working on "
            "the setting 'simulation.duration_ns'") in said
    assert ("The form shows these study-wide settings changed from their defaults, as typed "
            "and not checked by the validator: simulation.duration_ns = 50; "
            "setup.forcefield = charmm36.") in said
    assert "changes not in a Config downloaded, launched or opened in this window" in said
    # The setting as the schema declares it, not as the page says.
    assert "Factual evidence from the config schema: simulation.duration_ns" in said
    assert "at least 0.0" in said and "Production length in ns" in said
    # A builder look reads no study.
    assert "read_study" not in said


def test_a_top_level_and_an_execution_setting_are_found_and_a_made_up_one_is_not():
    assert "Factual evidence from the config schema: budget_hours" in _look(
        {"page": "run", "field": "budget_hours"}).said
    assert "Factual evidence from the config schema: execution.workers" in _look(
        {"page": "run", "field": "execution.workers"}).said
    # A row of the form's own is said as what it writes.
    said = _look({"page": "run", "field": "simulation.replicas"}).said
    assert ("'simulation.replicas' is a row of the form, not one setting of the config "
            "schema: replicas, written as a sweep of simulation.random_seed") in said
    assert "Factual evidence" not in said
    assert "'simulation.warp_speed' is not a setting of the config schema." in _look(
        {"page": "run", "field": "simulation.warp_speed"}).said
    # Saved as it is: nothing said of changes not made into a Config.
    assert "downloaded, launched" not in said
    # An analysis's row is named as the analysis.
    assert "last working on the analysis 'rmsd'" in _look(
        {"page": "run", "analysis": "rmsd"}).said


def test_a_list_cut_short_says_how_many_there_are():
    said = _look({"page": "run", "changed": ["simulation.duration_ns = 50"],
                  "changed_count": 25}).said
    assert "The form shows the first 1 of the 25 study-wide settings changed" in said


def test_the_file_open_is_read_on_the_builder_too(tmp_path):
    study = _study(tmp_path / "ubq", means={"rg": (1.21, 0.02)})
    said = _look({"page": "run", "study": str(study), "section": "Protocol",
                  "file": "analysis/rg/options.json"}, study).said
    assert "in the Config Builder at its step 'Protocol'" in said
    assert "has analysis/rg/options.json open in the file reader" in said
    assert '"mean": 1.21' in said


@pytest.mark.parametrize("changed", [
    [f"simulation.x{n} = 1" for n in range(MOST_CHANGED_HINTS + 1)],
    ["x" * 201],
    [3],
])
def test_the_changes_named_are_bounded(changed):
    look = _look({"page": "run", "changed": changed})
    assert not look.ok and "bounded" in look.said


@pytest.mark.parametrize("hint", [{"changed": "abc"}, {"unsaved": "false"}, {"field": 3}])
def test_a_builder_hint_of_another_kind_is_refused(hint):
    look = _look({"page": "run", **hint})
    assert not look.ok and "is not of its kind" in look.said


# -- the Analysis page ----------------------------------------------------------------

def test_the_analysis_page_s_section_and_analysis_are_read_from_the_record(tmp_path):
    study = _study(tmp_path / "ubq", means={"rmsd": (0.12, 0.01), "rg": (1.21, 0.02)})
    said = _look({"page": "analysis", "study": str(study), "section": "Radius of gyration",
                  "analysis": "rg"}, study).said
    assert "reading the Analysis page at its section 'Radius of gyration'" in said
    assert "Factual evidence from read_study" in said
    # The analysis on the page read first.
    assert said.index("1.21") < said.index("0.12")


# -- the Viewer's residues ------------------------------------------------------------

def _with_topology(tmp_path, text):
    study = _study(tmp_path / "ubq", means={"rmsd": (0.12, 0.01)})
    (study / "setup").mkdir()
    (study / "setup" / "topology.pdb").write_text(text, encoding="utf-8")
    return study


def test_the_residues_chosen_are_read_in_the_topology_the_analyses_read(tmp_path):
    pytest.importorskip("mdtraj")
    study = _with_topology(tmp_path, TWO_CHAINS)
    said = _look({"page": "viewer", "study": str(study), "selection": [
        {"chain": "B", "resi": 48, "resn": "GLY", "atom": "CA"},
        {"chain": "A", "resi": 11, "resn": "ALA"}]}, study).said
    assert ("Factual evidence from topology.pdb, the topology the analyses read, for the "
            "residues chosen in the Viewer:") in said
    assert ("GLY 48 (chain B): selected alone as `chainid 1 and resSeq 48`, 2 atoms; atom "
            "CA as `chainid 1 and resSeq 48 and name CA`") in said
    assert ("ALA 11 (chain A): not found (ALA 11 is not in topology.pdb, the topology the "
            "analyses read; the Viewer may show atoms the analyses do not read)") in said
    assert "Factual evidence from read_study" in said


def test_a_ligand_sharing_the_number_is_told_apart_by_its_name(tmp_path):
    pytest.importorskip("mdtraj")
    study = _with_topology(tmp_path, """\
ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C
HETATM    3  C1  LIG A   1       3.000   0.000   0.000  1.00  0.00           C
END
""")
    said = _look({"page": "viewer", "study": str(study),
                  "selection": [{"chain": "A", "resi": 1, "resn": "ALA"}]}, study).said
    assert "ALA 1 (chain A): selected alone as `resSeq 1 and resname ALA`, 2 atoms" in said


def test_a_topology_with_no_chain_letters_finds_the_residue_by_number_and_name(tmp_path):
    pytest.importorskip("mdtraj")
    study = _with_topology(tmp_path, """\
ATOM      1  N   ALA     1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA     1       1.458   0.000   0.000  1.00  0.00           C
END
""")
    said = _look({"page": "viewer", "study": str(study),
                  "selection": [{"chain": "A", "resi": 1, "resn": "ala"}]}, study).said
    assert "ala 1 (chain A): selected alone as `resSeq 1`, 2 atoms" in said


def test_a_topology_named_outside_the_study_is_not_read(tmp_path):
    import json

    pytest.importorskip("mdtraj")
    elsewhere = tmp_path / "elsewhere.pdb"
    elsewhere.write_text(TWO_CHAINS, encoding="utf-8")
    study = _study(tmp_path / "ubq", means={"rmsd": (0.12, 0.01)})
    (study / "analysis" / "analysis_manifest.json").write_text(json.dumps(
        {"resolved": {"topology": str(elsewhere)}}), encoding="utf-8")
    hints = {"page": "viewer", "study": str(study)}
    said = _look({**hints, "selection": [{"chain": "B", "resi": 48, "resn": "GLY"}]},
                 study).said
    assert "elsewhere.pdb" not in said and "chainid 1" not in said
    assert ("The residues chosen in the Viewer were not checked: the topology the analyses "
            "read was not read.") in said
    said = _look({**hints, "expression": "name CA"}, study).said
    assert "elsewhere.pdb" not in said and "matches" not in said


def test_records_that_cannot_be_opened_are_said_and_the_look_still_answers(
        tmp_path, monkeypatch):
    import fastmdxplora.gui.server as server

    study = _study(tmp_path / "ubq", means={"rmsd": (0.12, 0.01)})
    monkeypatch.setattr(server, "_confined", lambda root, read, **_: dict(
        server._RECORDS_UNREADABLE))
    look = _look({"page": "viewer", "study": str(study),
                  "selection": [{"chain": "A", "resi": 1, "resn": "ALA"}]}, study)
    assert look.ok
    assert ("The residues chosen in the Viewer were not checked: the study's records could "
            "not be read.") in look.said
    look = _look({"page": "viewer", "study": str(study), "expression": "name CA"}, study)
    assert look.ok and "The selection chosen in the Viewer was not checked" in look.said


def test_a_selection_typed_in_the_viewer_is_checked_in_the_topology(tmp_path):
    pytest.importorskip("mdtraj")
    study = _with_topology(tmp_path, TWO_CHAINS)
    said = _look({"page": "viewer", "study": str(study), "expression": "name CA"}, study).said
    assert ("the selection chosen in the Viewer, 'name CA', matches 2 of 4 atoms in 2 "
            "residues.") in said
    said = _look({"page": "viewer", "study": str(study), "expression": "resSeq ="}, study).said
    assert "'resSeq =' is not a valid selection in topology.pdb" in said


def test_residues_chosen_with_no_topology_are_said_not_checked(tmp_path):
    study = _study(tmp_path / "ubq")
    said = _look({"page": "viewer", "study": str(study),
                  "selection": [{"chain": "A", "resi": 1, "resn": "ALA"}]}, study).said
    assert ("The residues chosen in the Viewer were not checked: no topology to check a "
            "selection against.") in said


# -- in a browser ---------------------------------------------------------------------

def test_the_three_pages_hints_and_ask_about_in_a_browser(tmp_path, monkeypatch):
    """Driven: the Analysis page names its section and analysis; the Config
    Builder its step, the setting last focused (focus then in the Agent's
    box), a setting changed and that no Config was made; Ask about... names
    the residues chosen in the Agent's box, beside the Viewer, the words
    typed kept after them."""
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("mdtraj")
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests._agent_page import ready
    from tests.test_an_analysis_is_drawn_from_its_numbers import _analysis, _manifest, _series
    from tests.test_the_agent_and_the_picker_are_driven_in_a_browser import _browser
    from tests.test_the_drawing_scripts_run_in_a_browser import FRAMES, _write_study

    root = _write_study(tmp_path / "study")
    _manifest(root, saving_interval_ps=2.0)
    _analysis(root, "rmsd", _series(FRAMES), {
        "mean": 0.112, "standard_error": 0.002, "effective_samples": 11.0, "discard": 5,
        "n_frames": FRAMES, "unit": "nm"})
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page = _browser(pw)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector('.page[data-page="analysis"] .analysis-section [data-analysis]')
            analysis = page.evaluate("window.FastMDXPageHints.analysis()")

            page.goto(session.url + "#run", wait_until="domcontentloaded")
            page.wait_for_function("window.FastMDXRun && window.FastMDXRun.ready")
            page.evaluate("window.FastMDXRun.setStart('structure')")
            # A start picked alone is nothing to lose.
            started = page.evaluate("window.FastMDXPageHints.run() || {}")
            field = page.locator('#run-settings .builder-field[data-setting="duration_ns"] input')
            field.first.wait_for(state="attached")
            field.first.focus()
            field.first.fill("50")
            field.first.dispatch_event("change")
            # Tab lands on the next field: the one worked on is still this.
            page.keyboard.press("Tab")
            # The Agent beside the builder, its box taking the focus.
            page.evaluate("window.FastMDXAgentBeside.open()")
            page.wait_for_function("document.activeElement.id === 'agent-request'")
            builder = page.evaluate("window.FastMDXPageHints.run()")
            # Reset: nothing changed, nothing unsaved, no setting worked on.
            page.evaluate("document.getElementById('run-reset').click()")
            after_reset = page.evaluate("window.FastMDXPageHints.run()")

            page.evaluate("window.FastMDXAgentBeside.close()")
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            ready(page)
            hidden_before = page.is_hidden("#viewer-ask")
            page.evaluate("""() => {
              const viewer = window.FastMDXMoleculeViewer;
              viewer.STATE.selection = {residues: [{chain: "A", resi: 3, icode: "", resn: "ALA"}],
                                        atoms: []};
              window.dispatchEvent(new CustomEvent("dashboard:selection-changed"));
            }""")
            page.wait_for_selector("#viewer-ask:not([hidden])")
            # Dark on the canvas in either scheme.
            page.evaluate("document.documentElement.setAttribute('data-theme', 'light')")
            light = page.evaluate(
                "getComputedStyle(document.getElementById('viewer-ask')).backgroundColor")
            # Words typed before, on the Agent page: kept after the name.
            page.evaluate("document.getElementById('agent-request').value = 'is it buried?'")
            page.click("#viewer-ask")
            box = page.input_value("#agent-request")
            # Asked again, the name said once.
            page.click("#viewer-ask")
            again = page.input_value("#agent-request")
            page.evaluate("""() => {
              window.FastMDXMoleculeViewer.STATE.selection = null;
              window.dispatchEvent(new CustomEvent("dashboard:selection-changed"));
            }""")
            hidden_after = page.is_hidden("#viewer-ask")
            beside = page.evaluate("document.body.classList.contains('agent-beside')")
            focused = page.evaluate("document.activeElement.id")
            errors = list(page.errors)
            browser.close()
    finally:
        session.server.shutdown()
    assert analysis == {"section": "RMSD", "analysis": "rmsd"}
    # The step the field is in, scrolled to as it took focus.
    assert builder["section"] == "Protocol"
    assert builder["field"] == "simulation.duration_ns"
    assert "simulation.duration_ns = 50" in builder["changed"]
    assert "unsaved" not in started
    assert builder["unsaved"] is True and builder["changed_count"] == 1
    assert light == "rgba(5, 5, 5, 0.82)"
    assert "changed" not in after_reset and "unsaved" not in after_reset
    assert "field" not in after_reset
    assert hidden_before is True and hidden_after is True
    assert box == again == "About ALA 3 (chain A): is it buried?"
    assert beside is True and focused == "agent-request"
    assert errors == []
