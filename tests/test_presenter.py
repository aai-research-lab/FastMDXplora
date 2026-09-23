"""Tests for the SessionPresenter structural output layer."""

from __future__ import annotations

import io
import re

import pytest

from fastmdxplora.utils.presenter import (
    SessionPresenter,
    _ansi_supported,
    _strip_ansi,
    _visual_width,
)


# ---------------------------------------------------------------------------
# Helper: build a presenter that writes to a captured StringIO
# ---------------------------------------------------------------------------
def _presenter(quiet: bool = False, width: int = 80) -> tuple[SessionPresenter, io.StringIO]:
    buf = io.StringIO()
    p = SessionPresenter(stream=buf, quiet=quiet, width=width)
    return p, buf


# ===========================================================================
# Helper functions
# ===========================================================================
class TestHelpers:
    def test_strip_ansi(self):
        s = "\x1b[31mhello\x1b[0m world"
        assert _strip_ansi(s) == "hello world"

    def test_visual_width_ignores_ansi(self):
        s = "\x1b[31mABC\x1b[0m"
        assert _visual_width(s) == 3

    def test_ansi_supported_no_color_env(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        # Doesn't matter if isatty would say True, NO_COLOR wins
        class FakeTTY:
            def isatty(self):
                return True

        assert _ansi_supported(FakeTTY()) is False

    def test_ansi_supported_non_tty(self):
        # StringIO is not a TTY → no ANSI
        assert _ansi_supported(io.StringIO()) is False


# ===========================================================================
# Banner
# ===========================================================================
class TestBanner:
    def test_draws_no_frame(self):
        """It used to. The values are the point, and a box around them made
        the least important thing on the screen the loudest."""
        p, buf = _presenter()
        p.banner(System="x.pdb", Output="/tmp/out", Version="0.1.0")
        out = buf.getvalue()
        for drawing in ("╭", "╮", "╰", "╯", "│"):
            assert drawing not in out
        assert "x.pdb" in out and "/tmp/out" in out

    def test_includes_all_supplied_fields(self):
        p, buf = _presenter()
        p.banner(System="protein.pdb", Output="/tmp/x", Version="0.1.0")
        out = buf.getvalue()
        assert "protein.pdb" in out
        assert "/tmp/x" in out
        assert "0.1.0" in out

    def test_skips_empty_fields(self):
        p, buf = _presenter()
        p.banner(System="x.pdb", Output="", Version="0.1.0")
        out = buf.getvalue()
        assert "x.pdb" in out
        # Empty value not rendered
        assert "Output:" not in _strip_ansi(out)

    def test_the_wordmark_names_the_software(self):
        """The tagline is justified to the wordmark's width, so the gaps
        between its words depend on how wide the wordmark happens to be.
        Asserting a particular gap pins that width rather than the intent --
        it broke when the letterforms changed and the wordmark narrowed from
        71 columns to 67."""
        import re

        p, buf = _presenter()
        p.banner(System="x.pdb")
        out = _strip_ansi(buf.getvalue())
        assert re.search(r"Molecular\s+Dynamics\s+eXploration", out)


# ===========================================================================
# Phase headers and status
# ===========================================================================
class TestPhases:
    def test_phase_start_emits_arrow_and_name(self):
        p, buf = _presenter()
        p.phase_start("setup")
        out = _strip_ansi(buf.getvalue())
        assert "▸" in out
        assert "Phase: setup" in out

    def test_phase_end_emits_status_icon(self):
        p, buf = _presenter()
        p.phase_start("setup")
        p.phase_end("setup", status="ok")
        out = _strip_ansi(buf.getvalue())
        assert "✓" in out
        assert "setup complete" in out

    def test_phase_end_failure_icon(self):
        p, buf = _presenter()
        p.phase_start("setup")
        p.phase_end("setup", status="error")
        out = _strip_ansi(buf.getvalue())
        assert "✗" in out

    def test_phase_end_includes_elapsed_time(self):
        p, buf = _presenter()
        p.phase_start("setup")
        p.phase_end("setup", status="ok")
        # Looking for pattern "(N.N s)"
        assert re.search(r"\(\d+\.\d+ s\)", _strip_ansi(buf.getvalue()))

    def test_step_indents_two_spaces(self):
        p, buf = _presenter()
        p.step("Loaded input PDB")
        line = buf.getvalue()
        assert line.startswith("  ")
        assert "Loaded input PDB" in line

    def test_info_uses_plain_indent_no_icon(self):
        p, buf = _presenter()
        p.info("Loading trajectory... 10000 frames")
        out = _strip_ansi(buf.getvalue())
        assert "  Loading trajectory" in out
        # No status icon since this is plain info, not a step
        assert "✓" not in out
        assert "✗" not in out


# ===========================================================================
# Analysis status table
# ===========================================================================
class TestAnalysisTable:
    def test_row_shows_name_status_path_elapsed(self):
        p, buf = _presenter()
        p.analysis_table_row("rmsd", "ok", "analysis/rmsd/", 0.42)
        out = _strip_ansi(buf.getvalue())
        assert "rmsd" in out
        assert "ok" in out
        assert "analysis/rmsd/" in out
        assert re.search(r"\(0\.\d+ s\)", out)

    def test_rows_align_to_name_width(self):
        p, buf = _presenter()
        # Pass name_width to force alignment
        p.analysis_table_row("rmsd", "ok", "analysis/rmsd/", 0.4, name_width=10)
        p.analysis_table_row("dihedrals", "ok", "analysis/dihedrals/", 0.8, name_width=10)
        lines = [_strip_ansi(line) for line in buf.getvalue().splitlines()]
        # Both rows should have the status column at the same column index
        idx1 = lines[0].index("ok")
        idx2 = lines[1].index("ok")
        assert idx1 == idx2

    def test_error_row_uses_error_icon(self):
        p, buf = _presenter()
        p.analysis_table_row("rmsd", "error", "analysis/rmsd/", 0.0)
        out = _strip_ansi(buf.getvalue())
        assert "✗" in out


# ===========================================================================
# Done footer
# ===========================================================================
class TestDone:
    def test_done_emits_total(self):
        p, buf = _presenter()
        p.banner(System="x.pdb")  # starts the session timer
        p.done()
        out = _strip_ansi(buf.getvalue())
        assert "Done" in out
        # Format is "Done in N.N s." or "Done in Nm Ns."
        assert re.search(r"Done in \S+", out)

    def test_done_without_session_start_uses_zero(self):
        """If banner() was never called, done() reports 0.0 s — should not crash."""
        p, buf = _presenter()
        p.done()  # never called banner
        out = _strip_ansi(buf.getvalue())
        assert "Done" in out


# ===========================================================================
# Quiet mode
# ===========================================================================
class TestQuietMode:
    def test_explicit_quiet_silences_everything(self):
        p, buf = _presenter(quiet=True)
        p.banner(System="x.pdb")
        p.phase_start("setup")
        p.step("Did stuff")
        p.phase_end("setup")
        p.analysis_table_row("rmsd", "ok", "analysis/rmsd/", 0.4)
        p.done()
        assert buf.getvalue() == ""

    def test_env_plain_auto_enables_quiet(self, monkeypatch):
        monkeypatch.setenv("FASTMDX_LOG_STYLE", "plain")
        # Use quiet=None so env autodetection kicks in
        buf = io.StringIO()
        p = SessionPresenter(stream=buf, quiet=None, width=80)
        p.banner(System="x.pdb")
        p.phase_start("setup")
        assert buf.getvalue() == ""

    def test_explicit_quiet_false_overrides_env(self, monkeypatch):
        monkeypatch.setenv("FASTMDX_LOG_STYLE", "plain")
        # explicit quiet=False wins over env
        buf = io.StringIO()
        p = SessionPresenter(stream=buf, quiet=False, width=80)
        p.banner(System="x.pdb")
        assert "Fully" in _strip_ansi(buf.getvalue())


# ===========================================================================
# Color handling
# ===========================================================================
class TestColor:
    def test_no_color_when_stream_is_not_tty(self):
        p, buf = _presenter()
        p.phase_start("setup")
        assert "\x1b[" not in buf.getvalue()

    def test_no_color_when_env_set(self, monkeypatch):
        # Simulate a TTY by stubbing isatty
        monkeypatch.setenv("NO_COLOR", "1")

        class FakeTTY:
            def isatty(self):
                return True

            def write(self, s):
                pass

            def flush(self):
                pass

        p = SessionPresenter(stream=FakeTTY(), width=80)
        # Internal _color is False due to NO_COLOR
        assert p._color is False


# ===========================================================================
# Context manager
# ===========================================================================
class TestPhaseContextManager:
    def test_normal_exit_closes_with_ok(self):
        p, buf = _presenter()
        with p.phase("setup"):
            p.step("did a thing")
        out = _strip_ansi(buf.getvalue())
        assert "setup complete" in out
        # No error icon
        assert "✗" not in out

    def test_exception_closes_with_error_and_reraises(self):
        p, buf = _presenter()
        with pytest.raises(RuntimeError, match="boom"):
            with p.phase("setup"):
                raise RuntimeError("boom")
        out = _strip_ansi(buf.getvalue())
        assert "✗" in out


# ===========================================================================
# Time formatting
# ===========================================================================
class TestTimeFormat:
    def test_seconds(self):
        assert SessionPresenter._fmt_elapsed(0.42) == "0.4 s"
        assert SessionPresenter._fmt_elapsed(12.345) == "12.3 s"

    def test_minutes(self):
        assert SessionPresenter._fmt_elapsed(83.0) == "1m 23s"

    def test_hours(self):
        assert SessionPresenter._fmt_elapsed(3725.0) == "1h 2m"


# ===========================================================================
# Width awareness
# ===========================================================================
def test_narrow_width_clamps_to_minimum():
    """Absurdly narrow terminals should not crash the banner."""
    p = SessionPresenter(stream=io.StringIO(), width=10)
    # Should clamp to at least 40
    assert p.width >= 40


def test_banner_respects_terminal_width():
    """Banner inner width should not exceed terminal width."""
    p, buf = _presenter(width=50)
    p.banner(System="x" * 100, Output="y" * 100)  # very long values
    lines = buf.getvalue().splitlines()
    # No emitted line should exceed terminal width (allowing for some margin)
    for line in lines:
        if line.strip():
            assert _visual_width(line) <= 50 + 20  # generous slack for box chars


def test_orchestrator_reuses_the_process_presenter(tmp_path) -> None:
    """The banner is shown once per presenter, so there must be only one.

    The orchestrator used to construct its own SessionPresenter, which made
    the startup wordmark print twice: once from the CLI and again when a
    study began.
    """
    from fastmdxplora.orchestrator import FastMDXplora
    from fastmdxplora.utils import presenter as presenter_module

    presenter_module._PRESENTER = None
    cli_presenter = presenter_module.get_presenter()
    # `tmp_path`, not a fixed path under /tmp. Constructing an orchestrator
    # creates the output directory and opens a log file inside it, and on a
    # shared machine a hardcoded /tmp path belongs to whoever created it
    # first. This failed with PermissionError on a multi-user workstation
    # where something else had already made the directory.
    fmdx = FastMDXplora(system="1L2Y", output_dir=tmp_path / "run")
    assert fmdx._presenter is cli_presenter


def test_run_summary_lists_the_gui_only_when_one_was_requested(monkeypatch) -> None:
    """`explore` does not start a server, so it must not advertise one.

    The run summary printed the dashboard address unconditionally, sending
    users to a refused connection.
    """
    import io
    import sys

    from fastmdxplora.utils import presenter as presenter_module

    # Other tests activate dashboard telemetry in this process; make the
    # precondition explicit instead of depending on test order.
    monkeypatch.delenv("FASTMDX_DASHBOARD_ACTIVE", raising=False)
    monkeypatch.delenv("FASTMDX_DASHBOARD_URL", raising=False)

    def render(argv: list[str]) -> str:
        presenter_module._PRESENTER = None
        pr = presenter_module.get_presenter()
        buffer = io.StringIO()
        pr.stream = buffer
        pr._color = False
        pr.width = 120
        monkeypatch.setattr(sys, "argv", ["fastmdx", *argv])
        pr.banner(System="1L2Y", Output="/tmp/run")
        return buffer.getvalue()

    without = render(["explore", "--system", "1L2Y"])
    assert "127.0.0.1" not in without

    with_gui = render(["explore", "--system", "1L2Y", "--dashboard"])
    assert "127.0.0.1" in with_gui


def test_banner_is_one_left_aligned_block() -> None:
    """All sections share one left-aligned block.

    Five separate centred boxes drifted to the middle of a wide terminal and
    read as five unrelated blocks; one box fixed that, and then the box itself
    became the loudest thing on the screen. The alignment is what mattered.
    """
    import io
    import sys

    from fastmdxplora.utils import presenter as presenter_module

    presenter_module._PRESENTER = None
    pr = presenter_module.get_presenter()
    buffer = io.StringIO()
    pr.stream = buffer
    pr._color = False
    pr.width = 200
    argv = sys.argv
    sys.argv = ["fastmdx", "explore", "--system", "1L2Y"]
    try:
        pr.banner(System="1L2Y", Output="/tmp/run", Version="2.1.0")
    finally:
        sys.argv = argv
    text = buffer.getvalue()

    # The banner no longer draws a frame: the values are the point, and a box
    # around them made the least important thing on the screen the loudest.
    for drawing in (0x256D, 0x256E, 0x2570, 0x256F, 0x2502):
        assert chr(drawing) not in text

    # But the alignment the box was there to give is kept: every line of the
    # summary sits at the same indent as the wordmark above it.
    body = [line for line in text.splitlines()
            if line.strip() and "\u2588" not in line]
    assert body
    assert all(line.startswith("  ") for line in body)

    # REPORT is gone with the list of formats it introduced: the banner
    # describes the run, not what this software can write.
    for heading in ("SETUP", "SIMULATION"):
        assert heading in text


class TestBannerReflectsTheRun:
    """The summary box must show what was asked for, not what is usual.

    The same option is spelled two ways: `fastmdx explore --setup-forcefield`
    and `fastmdx setup --forcefield`. The banner knew only the first, so every
    per-phase run displayed defaults regardless of its arguments, which is
    worse than showing nothing.
    """

    @staticmethod
    def _banner(argv):
        import contextlib
        import io
        import sys

        from fastmdxplora.utils.presenter import SessionPresenter

        original = sys.argv
        sys.argv = argv
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                SessionPresenter().banner(
                    System="4W52", Output="/tmp/x", Version="0.0.0"
                )
        finally:
            sys.argv = original
        return buffer.getvalue()

    def test_the_per_phase_spelling_is_recognised(self) -> None:
        text = self._banner([
            "fastmdx", "setup", "--system", "4W52",
            "--forcefield", "amber-openff", "--ph", "6.5",
        ])
        assert "amber-openff" in text
        assert "6.5" in text

    def test_the_explore_spelling_still_works(self) -> None:
        text = self._banner([
            "fastmdx", "explore", "--system", "4W52",
            "--setup-forcefield", "amber-openff", "--setup-ph", "6.5",
        ])
        assert "amber-openff" in text
        assert "6.5" in text

    def test_the_resolved_force_field_is_shown_not_the_word_auto(self) -> None:
        """"auto" tells the reader nothing about what was simulated."""
        text = self._banner(["fastmdx", "setup", "--system", "4W52"])
        assert "amber-openff" in text
        # ...while still making clear it was chosen rather than named.
        assert "(auto)" in text

    def test_a_named_force_field_is_shown_as_given(self) -> None:
        text = self._banner([
            "fastmdx", "setup", "--system", "4W52", "--forcefield", "charmm36",
        ])
        assert "charmm36" in text
        assert "(auto)" not in text

    def test_simulation_settings_follow_the_same_rule(self) -> None:
        text = self._banner([
            "fastmdx", "simulate", "--nvt-steps", "1234",
            "--production-steps", "9876",
        ])
        assert "1,234" in text or "1234" in text
        assert "9,876" in text or "9876" in text


class TestTheBannerDescribesThisRun:
    """It was built entirely from command-line flags.

    A run started with --config saw none of them and fell back to defaults, so
    it announced a million production steps for a run that only analysed a
    trajectory, and the setup pH of a config that said something else. Anyone
    reading exploration.log to find out what a run did was told the wrong
    thing.
    """

    @staticmethod
    def _banner(argv, **fields):
        import contextlib
        import io
        import sys

        from fastmdxplora.utils.presenter import SessionPresenter

        original = sys.argv
        sys.argv = ["fastmdx"] + argv
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                SessionPresenter(stream=buffer).banner(**fields)
        finally:
            sys.argv = original
        return buffer.getvalue()

    @staticmethod
    def _config(tmp_path, text):
        path = tmp_path / "study.yml"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_an_analysis_run_announces_no_simulation(self, tmp_path) -> None:
        config = self._config(tmp_path, (
            "output: o\ninclude: [analysis]\nsystems: [{system: t.pdb}]\n"
            "analysis: {trajectory: x.dcd, topology: t.pdb}\n"))
        printed = self._banner(["explore", "--config", config],
                               system="t.pdb", output="o")
        assert "SIMULATION" not in printed
        assert "SETUP" not in printed
        # And the banner still printed: it announced nothing about phases
        # that will not run, rather than printing nothing at all.
        assert "Output" in printed

    def test_a_full_run_announces_everything(self, tmp_path) -> None:
        config = self._config(tmp_path, (
            "output: o\ninclude: [setup, simulation, analysis]\n"
            "systems: [{system: 1ubq}]\n"))
        printed = self._banner(["explore", "--config", config],
                               system="1ubq", output="o")
        assert "SETUP" in printed and "SIMULATION" in printed

    def test_without_a_config_nothing_changes(self) -> None:
        """There is nothing to go on, and leaving a section out would be worse
        than showing one for a phase that happens not to run."""
        printed = self._banner(["explore", "--system", "1ubq"],
                               system="1ubq", output="o")
        assert "SETUP" in printed and "SIMULATION" in printed

    def test_it_reports_what_the_config_says(self, tmp_path) -> None:
        config = self._config(tmp_path, (
            "output: o\ninclude: [setup, simulation]\nsystems: [{system: 1ubq}]\n"
            "setup: {ph: 6.5, forcefield: charmm36}\n"
            "simulation: {temperature_K: 310}\n"))
        printed = self._banner(["explore", "--config", config],
                               system="1ubq", output="o")
        assert "6.5" in printed, "the default pH was printed instead"
        assert "charmm36" in printed
        assert "310" in printed

    def test_a_config_that_configures_one_phase_still_runs_them_all(
        self, tmp_path
    ) -> None:
        """A phase block says the phase was configured, not that it is the
        only one that runs. This config runs setup, simulation, analysis and
        report -- the last three on their defaults -- so hiding the
        SIMULATION section described a run that was not happening."""
        config = self._config(tmp_path, (
            "output: o\nsystems: [{system: 1ubq}]\nsetup: {ph: 7.0}\n"))
        printed = self._banner(["explore", "--config", config],
                               system="1ubq", output="o")
        assert "SETUP" in printed
        assert "SIMULATION" in printed

    def test_include_on_the_command_line_narrows_it(self, tmp_path) -> None:
        """`--include setup` printed a SIMULATION section announcing
        1,750,000 steps that were never going to run: the banner read the
        config for phase selection and the config was silent."""
        config = self._config(tmp_path, (
            "output: o\nsystems: [{system: 1ubq}]\nsetup: {ph: 7.0}\n"))
        printed = self._banner(["explore", "--config", config, "--include", "setup"],
                               system="1ubq", output="o")
        assert "SETUP" in printed
        assert "SIMULATION" not in printed

    def test_exclude_on_the_command_line_narrows_it_too(self, tmp_path) -> None:
        config = self._config(tmp_path, (
            "output: o\nsystems: [{system: 1ubq}]\n"))
        printed = self._banner(["explore", "--config", config, "--exclude", "simulation"],
                               system="1ubq", output="o")
        assert "SETUP" in printed
        assert "SIMULATION" not in printed

    def test_an_unreadable_config_still_prints_a_banner(self, tmp_path) -> None:
        """A banner is decoration; a run that cannot print one should start."""
        config = self._config(tmp_path, "include: [analysis\n  broken: [")
        printed = self._banner(["explore", "--config", config],
                               system="t.pdb", output="o")
        assert "Output" in printed  # the banner printed at all

    def test_a_config_named_with_an_equals_sign_is_found(self, tmp_path) -> None:
        config = self._config(tmp_path, (
            "output: o\ninclude: [analysis]\nsystems: [{system: t.pdb}]\n"))
        printed = self._banner([f"--config={config}", "explore"],
                               system="t.pdb", output="o")
        assert "SIMULATION" not in printed


class TestTheBannerIsValuesNotDecoration:
    """A box around the settings made the banner the loudest thing on the
    screen, and it is the least important: what a reader wants from it is the
    handful of values, not a frame around them.
    """

    @staticmethod
    def _banner(argv=None, **fields):
        import contextlib
        import io
        import sys

        from fastmdxplora.utils.presenter import SessionPresenter

        original = sys.argv
        sys.argv = ["fastmdx"] + (argv or ["explore", "--system", "1L2Y"])
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                SessionPresenter(stream=buffer).banner(**fields)
        finally:
            sys.argv = original
        return buffer.getvalue()

    def test_there_is_no_frame(self) -> None:
        printed = self._banner(system="1L2Y", output="runs/x")
        for drawing in ("╭", "╮", "╰", "╯", "│"):
            assert drawing not in printed, f"the banner still draws {drawing}"

    def test_the_values_are_still_there(self) -> None:
        """Removing the frame should remove the frame and nothing else."""
        printed = self._banner(system="1L2Y", output="runs/x")
        for value in ("System", "1L2Y", "Output", "runs/x", "SETUP",
                      "SIMULATION", "Timestep", "Temperature"):
            assert value in printed, f"the banner lost {value}"

    def test_it_does_not_announce_itself(self) -> None:
        """The wordmark above it already says what this is."""
        printed = self._banner(system="1L2Y", output="runs/x")
        assert "MD EXPLORATION" not in printed

    def test_the_sections_are_what_the_run_will_do(self) -> None:
        """REPORT was renamed from REPORTING & OUTPUTS and then removed
        outright: it introduced a list of formats this software can write,
        which is not a fact about the run."""
        printed = self._banner(system="1L2Y", output="runs/x")
        assert "SETUP" in printed and "SIMULATION" in printed
        assert "REPORTING & OUTPUTS" not in printed
        assert "ANALYSIS & REPORT" not in printed

    def test_a_section_with_nothing_to_say_is_left_out(self, monkeypatch) -> None:
        """The analysis section listed the report's title, which announced the
        name of a document that did not exist yet and said nothing about the
        run. Without it the section holds only the browser address, so it is
        printed when there is one and left out when there is not.

        The environment is cleared rather than assumed: a run started from the
        GUI sets these, so this test passed locally and failed in CI, which is
        a test depending on ambient state rather than controlling it.
        """
        for variable in ("FASTMDX_DASHBOARD_URL", "FASTMDX_DASHBOARD_ACTIVE"):
            monkeypatch.delenv(variable, raising=False)

        printed = self._banner(system="1L2Y", output="runs/x")
        assert "FastMDXplora Run" not in printed
        assert "\n    ANALYSIS\n" not in printed

    def test_but_a_browser_address_is_worth_a_section(self, monkeypatch) -> None:
        """It is the one thing in it a reader cannot work out for themselves."""
        monkeypatch.setenv("FASTMDX_DASHBOARD_ACTIVE", "1")
        monkeypatch.setenv("FASTMDX_DASHBOARD_URL", "http://127.0.0.1:8765")

        printed = self._banner(system="1L2Y", output="runs/x")
        assert "ANALYSIS" in printed
        assert "http://127.0.0.1:8765" in printed


class TestTheBannerDescribesTheRunNotTheSoftware:
    """It listed the output formats it can write and five feature badges.

    Both said what this software is rather than what this run is doing, to
    somebody who has already chosen to use it and is waiting for it to start.
    The formats are visible in the output directory afterwards, where they can
    be opened rather than admired.
    """

    @staticmethod
    def _banner():
        import contextlib
        import io
        import sys

        from fastmdxplora.utils.presenter import SessionPresenter

        original = sys.argv
        sys.argv = ["fastmdx", "explore", "--system", "1L2Y"]
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                SessionPresenter(stream=buffer).banner(
                    system="1L2Y", output="runs/x")
        finally:
            sys.argv = original
        return buffer.getvalue()

    def test_the_output_formats_are_not_listed(self) -> None:
        printed = self._banner()
        for advertisement in ("Markdown report", "PowerPoint slides",
                              "ZIP result bundle", "PNG/SVG plots"):
            assert advertisement not in printed

    def test_the_badges_are_gone(self) -> None:
        printed = self._banner()
        for badge in ("Reproducible", "Config-driven", "Energy-aware",
                      "Publication-ready"):
            assert badge not in printed

    def test_what_the_run_will_do_remains(self) -> None:
        """Removing what the banner said about itself should leave everything
        it said about the run."""
        printed = self._banner()
        for value in ("System", "1L2Y", "Output", "runs/x", "Platform",
                      "SETUP", "pH", "Force Field",
                      "SIMULATION", "Timestep", "Temperature", "Production"):
            assert value in printed, f"the banner lost {value}"


class TestTheRunSaysHowFarThroughItIs:
    """Molecular dynamics is the part that takes the time, and it announced
    how many steps it was about to take and then said nothing until the stage
    ended: half an hour of a terminal that looked exactly like a hung one."""

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    def test_a_terminal_gets_a_bar_on_one_line(self) -> None:
        from fastmdxplora.utils.presenter import SessionPresenter

        presenter = SessionPresenter(stream=self._Tty())
        for done in (100, 500, 1000):
            presenter.progress("Production", done, 1000)

        written = presenter.stream.getvalue()
        assert written.count("\r") == 3, "one line, rewritten"
        assert written.count("\n") == 1, "a newline only when it finishes"

    def test_it_reports_the_rate_and_what_is_left(self) -> None:
        """ns/day is the number somebody running a simulation actually wants,
        and the time remaining is what decides whether to wait."""
        from fastmdxplora.utils.presenter import SessionPresenter

        presenter = SessionPresenter(stream=self._Tty())
        presenter.progress("Production", 250, 50_000,
                           rate_ns_per_day=18.4, seconds_left=1776)

        written = presenter.stream.getvalue()
        assert "18.40 ns/day" in written
        assert "29m36s left" in written

    def test_a_file_gets_lines_rather_than_carriage_returns(self) -> None:
        """Which is where a worker's output goes when several run at once. A
        file full of one rewritten line is unreadable."""
        from fastmdxplora.utils.presenter import SessionPresenter

        presenter = SessionPresenter(stream=io.StringIO())
        for done in range(1, 101):
            presenter.progress("Production", done, 100)

        written = presenter.stream.getvalue()
        assert "\r" not in written
        assert 5 <= written.count("\n") <= 12, "about one line per tenth"

    def test_nothing_is_claimed_about_an_empty_stage(self) -> None:
        from fastmdxplora.utils.presenter import SessionPresenter

        presenter = SessionPresenter(stream=self._Tty())
        presenter.progress("Production", 0, 0)
        assert presenter.stream.getvalue() == ""

    def test_a_stage_steps_in_chunks_so_it_can_report(self, tmp_path) -> None:
        """A single blocking call to step() cannot say anything until it
        returns. A real run without live telemetry reports its production
        again and again as it goes, and last at the end."""
        from tests._the_phase import a_run_s_progress

        production = [done for label, done, total in a_run_s_progress(tmp_path, telemetry=False)
                      if label.startswith("Production")]
        assert len(production) > 3
        assert production == sorted(production) and production[-1] == 100

