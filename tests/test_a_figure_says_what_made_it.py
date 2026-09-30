"""A figure says what made it, and the command that makes it again.

A figure copied into a paper leaves its study behind, and with it the
release that drew it, the frames it rests on, the selection and options,
and how to draw it again. All of that is recorded, and the Analysis page
showed none of it. Each figure now carries a chip that opens it, and the
command is the command line's own rendering of a config that reruns the one
analysis over the same frames into a folder of its own.

The proof is the rerun: the command, run, writes the same numbers.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")


def _trajectory(where: Path) -> tuple[Path, Path]:
    top = md.Topology()
    chain = top.add_chain()
    for _ in range(3):
        residue = top.add_residue("ALA", chain)
        for name, element in (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O"), ("CB", "C")):
            top.add_atom(name, md.element.get_by_symbol(element), residue)
    rng = np.random.default_rng(1)
    base = rng.normal(size=(15, 3)) * 0.3
    frames = np.array([base + rng.normal(scale=0.02, size=base.shape) for _ in range(60)])
    trajectory = md.Trajectory(frames, top, time=np.arange(60) * 10.0)
    where.mkdir(parents=True, exist_ok=True)
    trajectory[0].save_pdb(str(where / "top.pdb"))
    trajectory.save_dcd(str(where / "traj.dcd"))
    return where / "traj.dcd", where / "top.pdb"


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    from fastmdxplora.cli.main import main

    base = tmp_path_factory.mktemp("figures")
    dcd, pdb = _trajectory(base / "input")
    root = base / "study"
    assert main(["explore", "-s", str(pdb), "--output", str(root),
                 "--include-phase", "analysis", "--analyze-trajectory", str(dcd),
                 "--analyze-topology", str(pdb), "--analyze-analyses", "rmsd", "rg",
                 "--analyze-stride", "2"]) == 0
    return root


class TestWhatItSays:
    def test_what_made_it(self, study):
        from fastmdxplora import __version__
        from fastmdxplora.gui.figure_provenance import figure_provenance

        made = figure_provenance(study)
        assert sorted(made) == ["rg", "rmsd"]
        rmsd = made["rmsd"]
        assert rmsd["version"] == rmsd["this_version"] == __version__
        assert rmsd["packages"]["mdtraj"] == md.__version__
        assert rmsd["frames"] == 30 and rmsd["stride"] == 2
        assert rmsd["selection"] == "name CA"
        assert rmsd["made"] and rmsd["command"].startswith("fastmdx explore ")

    def test_the_command_is_one_the_command_line_takes(self, study):
        from fastmdxplora.cli.main import _build_explore_config, _build_parser
        from fastmdxplora.gui.figure_provenance import figure_provenance

        command = figure_provenance(study)["rmsd"]["command"]
        config = _build_explore_config(_build_parser().parse_args(shlex.split(command)[1:]))
        analysis = config["analysis"]
        assert analysis["include"] == ["rmsd"] and analysis["stride"] == 2
        assert analysis["options"]["rmsd"]["selection"] == "name CA"
        # Into a folder of its own, beside the study, never over it.
        assert Path(config["output"]) == study.parent / "study_rmsd_again"

    def test_it_makes_the_same_numbers_again(self, study):
        from fastmdxplora.cli.main import main
        from fastmdxplora.gui.figure_provenance import figure_provenance

        command = figure_provenance(study)["rmsd"]["command"]
        assert main(shlex.split(command)[1:]) == 0
        again = study.parent / "study_rmsd_again" / "analysis" / "rmsd"
        first = study / "analysis" / "rmsd"
        assert (again / "rmsd.dat").read_bytes() == (first / "rmsd.dat").read_bytes()
        before = json.loads((first / "options.json").read_text(encoding="utf-8"))
        after = json.loads((again / "options.json").read_text(encoding="utf-8"))
        assert after["options"] == before["options"]
        assert after["findings"]["mean"]["mean"] == before["findings"]["mean"]["mean"]

    def test_a_setting_with_no_flag_is_given_as_a_config(self, study, monkeypatch):
        from fastmdxplora.config import languages
        from fastmdxplora.gui.figure_provenance import figure_provenance

        def refuse(config):
            raise languages.UntranslatableSetting("no flag for it")
        monkeypatch.setattr(languages, "cli_command", refuse)
        rg = figure_provenance(study)["rg"]
        assert rg["command"] is None
        assert "include_phase:\n- analysis" in rg["config"] and "- rg" in rg["config"]

    def test_a_study_not_analysed_has_none(self, tmp_path):
        from fastmdxplora.gui.figure_provenance import figure_provenance

        assert figure_provenance(tmp_path) == {}


def test_each_figure_carries_its_chip(study) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora import __version__
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            chip = '.analysis-card[data-analysis="rmsd"] .figure-chip'
            page.wait_for_selector(chip)
            assert page.text_content(chip) == "v" + __version__.split("+")[0].split(".dev")[0]
            page.click(chip)
            panel = '.analysis-card[data-analysis="rmsd"] .figure-provenance'
            page.wait_for_selector(f"{panel}:not([hidden])")
            said = page.text_content(panel)
            assert "Made by" in said and __version__ in said
            assert "30 frames, every 2nd frame" in said and "name CA" in said
            command = page.text_content(f"{panel} .figure-provenance-command")
            assert command.startswith("fastmdx explore ") and "--analyze-analyses rmsd" in command
            assert page.get_attribute(chip, "aria-expanded") == "true"
            page.click(chip)
            page.wait_for_selector(f"{panel}[hidden]", state="attached")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
