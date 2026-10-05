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
    import logging

    from fastmdxplora.cli.main import main
    from fastmdxplora.utils import logging as fastmdx_logging

    base = tmp_path_factory.mktemp("figures")
    dcd, pdb = _trajectory(base / "input")
    root = base / "study"
    # The CLI configures the package logger for a command-line session. A
    # fixture of the module is set up before the suite's own per-test
    # restoring fixture takes its baseline, so that baseline was the CLI's,
    # and every later test in the process inherited a logger that does not
    # propagate: `caplog` read nothing. The state is put back here.
    logger = logging.getLogger("fastmdx")
    kept = (logger.propagate, logger.level, list(logger.handlers),
            fastmdx_logging._console_handler)
    try:
        assert main(["explore", "-s", str(pdb), "--output", str(root),
                     "--include-phase", "analysis", "--analyze-trajectory", str(dcd),
                     "--analyze-topology", str(pdb), "--analyze-analyses", "rmsd", "rg",
                     "--analyze-stride", "2"]) == 0
    finally:
        logger.propagate = kept[0]
        logger.setLevel(kept[1])
        logger.handlers[:] = kept[2]
        fastmdx_logging._console_handler = kept[3]
    return root


def test_the_study_leaves_the_logger_as_it_found_it(study) -> None:
    import logging

    assert logging.getLogger("fastmdx").propagate is True


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
        assert rmsd["selection"] == "protein and name CA"
        assert rmsd["made"] and rmsd["command"].startswith("fastmdx explore ")

    def test_the_command_is_one_the_command_line_takes(self, study):
        from fastmdxplora.cli.main import _build_explore_config, _build_parser
        from fastmdxplora.gui.figure_provenance import figure_provenance

        command = figure_provenance(study)["rmsd"]["command"]
        config = _build_explore_config(_build_parser().parse_args(shlex.split(command)[1:]))
        analysis = config["analysis"]
        assert analysis["include"] == ["rmsd"] and analysis["stride"] == 2
        assert analysis["options"]["rmsd"]["selection"] == "protein and name CA"
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

    def test_and_at_a_journals_column_width(self, study):
        from PIL import Image

        from fastmdxplora.cli.main import main
        from fastmdxplora.gui.figure_provenance import figure_provenance

        made = figure_provenance(study)["rmsd"]
        assert made["width"] == "page"
        assert sorted(made["at_widths"]) == ["double_column", "single_column"]
        command = made["at_widths"]["single_column"]
        assert "--analyze-figure-width single_column" in command
        assert main(shlex.split(command)[1:]) == 0
        narrow = study.parent / "study_rmsd_again_single_column" / "analysis" / "rmsd"
        # 89 mm at 300 dpi is 1051 pixels; the tight crop takes a little off.
        width, _ = Image.open(narrow / "rmsd.png").size
        assert 950 <= width <= 1051
        page, _ = Image.open(study / "analysis" / "rmsd" / "rmsd.png").size
        assert page > 1800
        # The same numbers, only drawn narrower.
        assert (narrow / "rmsd.dat").read_bytes() == \
            (study / "analysis" / "rmsd" / "rmsd.dat").read_bytes()

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
            assert "Plotted for" in said and "the page (6.5 in)" in said
            page.click(f'{panel} .figure-provenance-widths [data-width="single_column"]')
            assert "--analyze-figure-width single_column" in page.text_content(
                f"{panel} .figure-provenance-command")
            page.click(f'{panel} .figure-provenance-widths [data-width=""]')
            assert page.text_content(f"{panel} .figure-provenance-command") == command
            assert page.get_attribute(chip, "aria-expanded") == "true"
            page.click(chip)
            page.wait_for_selector(f"{panel}[hidden]", state="attached")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []


class TestTheWidthsAFigureIsDrawnAt:
    def test_each_width_and_its_type(self):
        import matplotlib.pyplot as plt

        from fastmdxplora.analysis.plotting import new_figure, sized_for

        fig, _ = new_figure()
        assert tuple(round(v, 2) for v in fig.get_size_inches()) == (6.5, 4.2)
        plt.close(fig)
        with sized_for("single_column"):
            fig, _ = new_figure()
            assert round(fig.get_size_inches()[0] * 25.4, 1) == 89.0
            assert plt.rcParams["xtick.labelsize"] == 7.0
            plt.close(fig)
            # An analysis's own shape is kept, at the column's width.
            fig, _ = new_figure(figsize=(6.5, 3.8))
            assert round(fig.get_size_inches()[1] / fig.get_size_inches()[0], 4) == \
                round(3.8 / 6.5, 4)
            plt.close(fig)
        # And put back afterwards.
        assert plt.rcParams["xtick.labelsize"] == 9.0

    def test_the_usual_names_and_nothing_else(self):
        from fastmdxplora.analysis.plotting import settle_figure_width
        from fastmdxplora.refusals import StudyError

        assert settle_figure_width(None) == "page"
        assert settle_figure_width("single-column") == "single_column"
        assert settle_figure_width("Two column") == "double_column"
        with pytest.raises(StudyError, match="page, single_column, double_column"):
            settle_figure_width("poster")

    def test_it_is_a_setting_the_config_validates(self):
        from fastmdxplora.config.loader import ConfigError, validate_config

        validate_config({"systems": [{"system": "1L2Y"}],
                         "analysis": {"figure_width": "single_column"}})
        with pytest.raises(ConfigError):
            validate_config({"systems": [{"system": "1L2Y"}],
                             "analysis": {"figure_width": "poster"}})
