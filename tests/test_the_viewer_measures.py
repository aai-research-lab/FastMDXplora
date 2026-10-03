"""The viewer measures a distance, an angle and a dihedral, and a distance
over every frame.

The viewer said where a clicked atom was and nothing about how far it was
from another: the plainest question asked of a structure. With Measure on
(the ruler, or M), two atoms clicked give their distance, three the angle
at the middle one, four the dihedral, shown in the structure and listed in
the Selection tab, in the frame on screen, and following the trajectory as
it plays. Two atoms can be measured over every frame too: the command runs
the pair_distance analysis over the study's trajectory into a folder of its
own, from the atoms' selections, each checked to name one atom.
"""

from __future__ import annotations

import shlex

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.gui.measure import over_frames  # noqa: E402
from tests import viewer_hooks as hooks  # noqa: E402
from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402

A = "resSeq 3 and name CA"
B = "resSeq 6 and name CA"


@pytest.fixture
def study(tmp_path):
    return _write_study(tmp_path / "study")


class TestOverEveryFrame:
    def test_the_command_measures_the_two_atoms(self, study):
        answer = over_frames(study, A, B)
        assert answer["ok"] and answer["against"] == "trajectory_topology.pdb"
        command = answer["command"]
        assert command.startswith("fastmdx explore ")
        assert "--analyze-analyses pair_distance" in command
        assert A in command and B in command and "closest" in command
        assert answer["output"].endswith("study_distance")

    def test_it_runs_and_measures_what_the_frame_shows(self, study):
        """Run, the command writes the distance at every frame; the first is
        the distance between the two atoms in the first frame."""
        from fastmdxplora.cli.main import main

        answer = over_frames(study, A, B)
        assert main(shlex.split(answer["command"])[1:]) == 0
        written = np.loadtxt(next(
            (study.parent / "study_distance" / "analysis" / "pair_distance").glob("*.dat")),
            comments="#")
        trajectory = md.load(str(study / "simulation" / "production.dcd"),
                             top=str(study / "simulation" / "trajectory_topology.pdb"))
        a, b = (int(trajectory.topology.select(s)[0]) for s in (A, B))
        expected = md.compute_distances(trajectory, [[a, b]])[:, 0]
        values = written[:, -1] if written.ndim > 1 else written
        assert len(values) == trajectory.n_frames
        assert values == pytest.approx(expected, abs=1e-4)

    def test_a_later_measurement_has_a_folder_of_its_own(self, study):
        (study.parent / "study_distance").mkdir()
        assert over_frames(study, A, B)["output"].endswith("study_distance_2")

    @pytest.mark.parametrize("a,b,said", [
        ("resSeq 3", B, "does not name one atom"),
        ("resSeq 99 and name CA", B, "does not name one atom"),
        ("not a selection ((", B, "does not name one atom"),
        (A, A, "the same atom"),
        ("", B, "Two atoms"),
    ])
    def test_what_is_refused(self, study, a, b, said):
        answer = over_frames(study, a, b)
        assert not answer["ok"] and said in answer["reason"]

    def test_the_frames_the_analyses_read_come_first(self, study):
        """Where the study was analysed, the same trajectory and frames."""
        import json

        (study / "analysis").mkdir()
        (study / "analysis" / "analysis_manifest.json").write_text(json.dumps({"resolved": {
            "trajectory": str(study / "simulation" / "production.dcd"),
            "topology": str(study / "simulation" / "trajectory_topology.pdb"),
            "stride": 5, "first": 2}}), encoding="utf-8")
        command = over_frames(study, A, B)["command"]
        assert "--analyze-stride 5" in command and "--analyze-first 2" in command

    def test_no_topology_or_an_unreadable_one(self, study, tmp_path):
        assert over_frames(tmp_path / "nothing", A, B)["reason"] == \
            "No topology to check the atoms against."
        (study / "simulation" / "trajectory_topology.pdb").write_text("not a structure",
                                                                     encoding="utf-8")
        (study / "setup" / "topology.pdb").unlink()
        assert not over_frames(study, A, B)["ok"]

    def test_a_study_with_no_trajectory(self, study):
        (study / "simulation" / "production.dcd").unlink()
        assert over_frames(study, A, B)["reason"] == \
            "This study has no trajectory to compute it over."


def test_the_viewer_measures(study) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    viewer = "window.FastMDXMoleculeViewer"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)

    def click(page, resi):
        assert hooks.click(page, resi=resi, atom="CA")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(hooks.RENDERED)
            # The geometry, against numbers worked out here: MDTraj's
            # dihedral sign, a right angle, a known length.
            geometry = page.evaluate(f"""() => {viewer}.measurements([
                {{x: 1, y: 0, z: 0}}, {{x: 0, y: 0, z: 0}}, {{x: 0, y: 1, z: 0}},
                {{x: 0, y: 1, z: 1}}])""")
            page.click('[data-action="measure"]')
            pressed = page.get_attribute('[data-action="measure"]', "aria-pressed")
            page.wait_for_selector("#measure-said:not([hidden])")
            asked = page.text_content("#measure-said")
            click(page, 3)
            click(page, 6)
            page.wait_for_selector("#measure-said .measure-over-frames:not([disabled])")
            two = page.text_content("#measure-said .measure-values")
            page.wait_for_function(f"() => {viewer}.STATE.engine.measurementCount() === 1")
            shown = page.evaluate(f"() => {viewer}.STATE.engine.measurementCount()")
            atoms = [[a["x"], a["y"], a["z"]] for resi in (3, 6)
                     for a in hooks.atoms(page, resi=resi, atom="CA")]
            page.click("#measure-said .measure-over-frames")
            page.wait_for_selector("#measure-said .measure-command")
            command = page.text_content("#measure-said .measure-command")
            click(page, 7)
            click(page, 9)
            page.wait_for_function("() => /Dihedral/.test(document.querySelector("
                                   "'#measure-said .measure-values')?.textContent || '')")
            four = page.text_content("#measure-said .measure-values")
            page.keyboard.press("Escape")
            cleared = page.evaluate(f"() => {viewer}.STATE.picks.length")
            page.keyboard.press("m")
            off = page.get_attribute('[data-action="measure"]', "aria-pressed")
            browser.close()
    finally:
        session.server.shutdown()
    assert [round(m["value"], 6) for m in geometry] == [1.0, 1.0, 1.0, 90.0, 90.0, -90.0]
    assert [m["kind"] for m in geometry] == ["distance"] * 3 + ["angle"] * 2 + ["dihedral"]
    assert pressed == "true" and asked.startswith("MeasuringClick an atom")
    angstroms = float(np.linalg.norm(np.subtract(*atoms)))
    assert f"Distance, 1 to 2{angstroms:.2f} Å ({angstroms / 10:.3f} nm)" in two
    assert shown == 1
    assert "--analyze-analyses pair_distance" in command
    assert "Angle at 2" in four and "Angle at 3" in four and "Dihedral, 1-2-3-4" in four
    assert cleared == 0 and off == "false"
    assert errors == []
