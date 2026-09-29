"""The builder says what setup will build and cost while the settings are open.

The box, the particle count and the time were learned from setup's log once
a run had started. The builder now asks for them under the structure as the
form changes (`/api/preview-system`), with what is worth knowing about the
structure under those settings and a way to the setting it is about.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from fastmdxplora.gui import preview
from fastmdxplora.gui.preview import structure_file, system_preview
from fastmdxplora.refusals import StudyError

ATOM = "ATOM  {:5d}  {:<3s} {:>3s} {}{:4d}    {:8.3f}{:8.3f}{:8.3f}  1.00  0.00          {:>2s}\n"


def _peptide(n: int = 3) -> str:
    lines, serial = [], 1
    for i in range(n):
        for k, name in enumerate(("N", "CA", "C", "O")):
            lines.append(ATOM.format(serial, name, "GLY", "A", i + 1, 3.6 * i + 0.4 * k,
                                     0.0, 0.0, name[0]))
            serial += 1
    return "".join(lines)


def _zinc() -> str:
    return (_peptide() + "HETATM   99 ZN    ZN A 401       1.000   1.000   0.000  1.00  0.00"
            "          ZN\n")


@pytest.fixture(autouse=True)
def _own_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(preview, "_platform", lambda requested, precision: ("CPU", precision))


def _state(system: str, **setup) -> dict:
    state = {"system": system, "include_phase": ["setup", "simulation"]}
    if setup:
        state["setup"] = setup
    return state


class TestWhatItSays:

    def test_the_system_and_the_box(self, tmp_path) -> None:
        path = tmp_path / "peptide.pdb"
        path.write_text(_peptide() + "END\n", encoding="utf-8")
        answer = system_preview(_state(str(path)))
        assert answer["ok"], answer
        estimate = answer["estimate"]
        assert estimate["residues"] == 3 and estimate["particles"] > estimate["solute_atoms"]
        assert estimate["grows"], "three residues get the smallest box the cutoff allows"
        assert (estimate["positive_ion"], estimate["negative_ion"]) == ("Na+", "Cl-")

    def test_the_time_where_the_machine_has_been_timed(self, tmp_path) -> None:
        from fastmdxplora.cost import calibrate

        path = tmp_path / "peptide.pdb"
        path.write_text(_peptide() + "END\n", encoding="utf-8")
        calibrate(particles=30_000, steps=5_000, seconds=42.0, platform_name="CPU",
                  precision="mixed")
        state = _state(str(path))
        state["simulation"] = {"duration_ns": 1}
        time = system_preview(state)["time"]
        assert time["ok"] and time["platform"] == "CPU" and time["runs"] == 1
        assert time["steps"] == 250_000 + 500_000 + 500_000
        assert "about" in time["text"]

    def test_a_machine_never_timed_is_said_to_be(self, tmp_path) -> None:
        path = tmp_path / "peptide.pdb"
        path.write_text(_peptide() + "END\n", encoding="utf-8")
        time = system_preview(_state(str(path)))["time"]
        assert not time["ok"] and time["code"] == "environment.calibration.absent"
        assert "has not been timed" in time["reason"]

    def test_one_timed_otherwise_is_said_to_be(self, tmp_path) -> None:
        from fastmdxplora.cost import calibrate

        path = tmp_path / "peptide.pdb"
        path.write_text(_peptide() + "END\n", encoding="utf-8")
        calibrate(particles=30_000, steps=5_000, seconds=4.2, platform_name="CUDA",
                  precision="mixed")
        time = system_preview(_state(str(path)))["time"]
        assert time["code"] == "environment.calibration.stale"
        assert "timed on another platform" in time["reason"]

    def test_what_is_worth_knowing_names_its_setting(self, tmp_path) -> None:
        path = tmp_path / "zinc.pdb"
        path.write_text(_zinc() + "END\n", encoding="utf-8")
        answer = system_preview(_state(str(path), solvent_padding_nm=0.3))
        settings = [a["setting"] for a in answer["advisories"]]
        assert "forcefield" in settings
        assert "solvent_padding_nm" not in settings, "the estimate says the box"


class TestWhatItCannotSay:

    def test_nothing_named(self) -> None:
        assert "Name a structure" in system_preview(_state(""))["reason"]

    def test_a_file_that_is_not_there(self, tmp_path) -> None:
        assert "no file at" in system_preview(_state(str(tmp_path / "gone.pdb")))["reason"]

    def test_a_path_outside_the_workspace(self, tmp_path) -> None:
        answer = system_preview(_state("/etc/passwd"), path_for=lambda given: None)
        assert "outside the workspace" in answer["reason"]

    def test_a_membrane(self, tmp_path) -> None:
        path = tmp_path / "peptide.pdb"
        path.write_text(_peptide() + "END\n", encoding="utf-8")
        assert "bilayer" in system_preview(_state(str(path), membrane="POPC"))["reason"]

    def test_a_form_that_does_not_build(self, monkeypatch) -> None:
        from fastmdxplora.gui import config_builder

        def refuse(state, full=False):
            raise ValueError("the sweep cannot be read")

        monkeypatch.setattr(config_builder, "build_config", refuse)
        assert system_preview(_state("x.pdb"))["reason"] == "the sweep cannot be read"


class TestAPDBIdentifier:

    def test_it_is_fetched_once_and_kept(self, tmp_path, monkeypatch) -> None:
        from fastmdxplora.setup import pipeline

        fetched: list[str] = []

        def fetch(pdb_id, dest):
            fetched.append(pdb_id)
            dest.write_text(_peptide() + "END\n", encoding="utf-8")
            return dest

        monkeypatch.setattr(pipeline, "_fetch_pdb_from_rcsb", fetch)
        monkeypatch.chdir(tmp_path)
        assert system_preview(_state("1abc"))["ok"]
        assert system_preview(_state("1ABC"))["ok"]
        assert fetched == ["1ABC"]

    def test_one_that_cannot_be_reached_is_said_plainly(self, tmp_path, monkeypatch) -> None:
        from fastmdxplora.setup import pipeline

        def unreachable(pdb_id, dest):
            raise StudyError("no route", code="environment.service.unreachable")

        monkeypatch.setattr(pipeline, "_fetch_pdb_from_rcsb", unreachable)
        monkeypatch.chdir(tmp_path)
        assert "could not be fetched" in system_preview(_state("9xyz"))["reason"]

    def test_another_refusal_is_passed_on(self, tmp_path, monkeypatch) -> None:
        from fastmdxplora.setup import pipeline

        def refused(pdb_id, dest):
            raise StudyError("RCSB refused it.", code="setup.input.unrecognised")

        monkeypatch.setattr(pipeline, "_fetch_pdb_from_rcsb", refused)
        monkeypatch.chdir(tmp_path)
        assert system_preview(_state("9xyz"))["reason"] == "RCSB refused it."

    def test_a_file_of_that_name_is_the_file(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "1abc").write_text(_peptide() + "END\n", encoding="utf-8")
        assert structure_file("1abc") == Path("1abc")


def test_an_mmcif_file_is_read_as_setup_reads_it(tmp_path) -> None:
    app = pytest.importorskip("openmm.app")

    pdb = tmp_path / "peptide.pdb"
    pdb.write_text(_peptide() + "END\n", encoding="utf-8")
    structure = app.PDBFile(str(pdb))
    cif = tmp_path / "peptide.cif"
    with cif.open("w", encoding="utf-8") as handle:
        app.PDBxFile.writeFile(structure.topology, structure.positions, handle)
    converted = structure_file(str(cif))
    assert converted.suffix == ".pdb" and structure_file(str(cif)) == converted
    assert system_preview(_state(str(cif)))["estimate"]["residues"] == 3


class TestTheRoute:

    def test_it_answers_on_this_machine(self, tmp_path) -> None:
        from fastmdxplora.gui.server import start_dashboard_session

        path = tmp_path / "peptide.pdb"
        path.write_text(_peptide() + "END\n", encoding="utf-8")
        session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
        try:
            request = urllib.request.Request(
                session.url.rstrip("/") + "/api/preview-system",
                data=json.dumps(_state(str(path))).encode(),
                headers={"Content-Type": "application/json",
                         "Origin": session.url.rstrip("/")},
                method="POST")
            with urllib.request.urlopen(request, timeout=30) as response:
                answer = json.loads(response.read())
        finally:
            session.server.shutdown()
        assert answer["ok"] and answer["estimate"]["residues"] == 3

    def test_not_beyond_it(self) -> None:
        """It reads the file a request names."""
        from fastmdxplora.gui.server import POSTS_ANSWERED_BEYOND_LOOPBACK

        assert "/api/preview-system" not in POSTS_ANSWERED_BEYOND_LOOPBACK

    def test_hosted_it_reads_inside_the_workspace(self, tmp_path) -> None:
        from fastmdxplora.gui.hosting import Hosting

        (tmp_path / "peptide.pdb").write_text(_peptide() + "END\n", encoding="utf-8")
        hosting = Hosting(workspace=tmp_path, allowed_hosts=frozenset(), secret="x")
        assert system_preview(_state("peptide.pdb"), path_for=hosting.inside)["ok"]
        assert "outside" in system_preview(_state("../x.pdb"), path_for=hosting.inside)["reason"]




class TestInTheBrowser:
    """Driven in a page: the preview arrives under the structure as it is
    typed, and an advisory opens the setting it is about."""

    @pytest.fixture(scope="class")
    def page(self, tmp_path_factory):
        sync_api = pytest.importorskip("playwright.sync_api")
        from fastmdxplora.gui.server import start_dashboard_session

        root = tmp_path_factory.mktemp("builder")
        (root / "zinc.pdb").write_text(_zinc() + "END\n", encoding="utf-8")
        session = start_dashboard_session(output=str(root / "study"), host="127.0.0.1", port=0)
        with sync_api.sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url.rstrip("/") + "/#run", wait_until="domcontentloaded")
            page.wait_for_selector("#run-start option[value='structure']", state="attached")
            page.select_option("#run-start", "structure")
            page.fill("#run-system", str(root / "zinc.pdb"))
            page.wait_for_function(
                "() => /particles/.test(document.getElementById('run-system-preview-body')"
                ".textContent)", timeout=60000)
            yield page
            browser.close()
        session.server.shutdown()

    def test_it_says_the_system_under_the_structure(self, page) -> None:
        headline = page.text_content(".system-preview-headline")
        assert headline.startswith("about ") and "dodecahedron" in headline
        assert "nm from face to face" in headline
        rows = page.text_content(".system-preview-rows")
        assert "3 residues in chain A" in rows and "Na+" in rows
        assert "grown from 1 to" in rows
        assert "Time here" in rows

    def test_an_advisory_opens_its_setting(self, page) -> None:
        page.click("text=Show forcefield")
        page.wait_for_selector('#run-settings [data-setting="forcefield"].is-pointed')
        focused = page.evaluate(
            "() => document.activeElement.closest('[data-setting]')?.dataset.setting")
        assert focused == "forcefield"

    def test_a_change_to_the_settings_is_said_again(self, page) -> None:
        before = page.text_content(".system-preview-headline")
        page.select_option('#run-settings [data-setting="box_shape"] select', "cube")
        page.wait_for_function(
            "(was) => document.querySelector('.system-preview-headline')?.textContent !== was"
            " && /cube/.test(document.querySelector('.system-preview-headline').textContent)",
            arg=before, timeout=60000)

    def test_an_assembly_s_copies_are_said(self, page) -> None:
        """Last: it draws an answer of its own over the page's."""
        page.evaluate("""() => {
            document.getElementById('run-system-preview').hidden = false;
        }""")
        rows = page.evaluate("""() => {
            const answer = {ok: true, estimate: {particles: 100, box_shape: 'cube', narrowest_nm: 4,
                width_nm: 4,
                volume_nm3: 64, chains: ['A', 'B'], copies: 2, residues: 574, gaps_built: 0,
                solute_atoms: 9146, net_charge: 2, ligands: [], waters: 10, water_model: 'tip3p',
                ions_positive: 1, ions_negative: 1, positive_ion: 'Na+', negative_ion: 'Cl-',
                padding_nm: 1, padding_used_nm: 1, grows: false, refuses: true, notes: []},
                time: {ok: true, seconds: 7200, runs: 3, platform: 'CUDA', steps: 3000},
                advisories: []};
            window.FastMDXRun.renderPreview(answer);
            return document.querySelector('.system-preview-rows').textContent;
        }""")
        assert "4 chains (chains A, B and a copy of each by symmetry)" in rows
        assert "setup will refuse it" in rows
        assert "about 2.0 hours for 3 runs on CUDA" in rows
