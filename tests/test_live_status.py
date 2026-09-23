"""What a run in progress says about itself.

Three defects of one shape: a page reading a file that does not hold the
answer, and reporting the gap as a value.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastmdxplora.setup.prepare import NPT_CONTRACTION_MARGIN
from urllib.request import urlopen

from fastmdxplora.gui import protein_preview
from fastmdxplora.gui.protein_preview import (
    STRUCTURE_CANDIDATES,
    SYSTEM_CANDIDATES,
    find_structure,
    find_system,
)
from fastmdxplora.gui.structure_info import (
    _MAX_PDB_BYTES_FOR_INLINE_SCAN,
    _MAX_PDB_BYTES_FOR_SYSTEM_SCAN,
    count_structure,
)
from fastmdxplora.gui.telemetry import TelemetryWriter, read_status

from tests.test_gui import DashboardConfig, start_test_server


def _protein_pdb() -> str:
    return "\n".join(
        f"ATOM  {index:>5}  CA  ALA A{index:>4}"
        f"{index:>12.3f}{0.0:>8.3f}{0.0:>8.3f}  1.00 10.00           C"
        for index in range(1, 5)
    )


def _solvated_pdb() -> str:
    """Protein, ligand, water and ions: what setup actually builds."""
    return "\n".join([
        _protein_pdb(),
        "HETATM  101  C1  BNZ B   1       5.000   0.000   0.000  1.00 10.00           C",
        "HETATM  102  C2  BNZ B   1       6.000   0.000   0.000  1.00 10.00           C",
        "HETATM  201  O   HOH C   1      10.000   0.000   0.000  1.00 10.00           O",
        "HETATM  202  O   HOH C   2      11.000   0.000   0.000  1.00 10.00           O",
        "HETATM  301 NA    NA D   1      20.000   0.000   0.000  1.00 10.00          NA",
        "END",
    ])


class TestThePanelsReadTheSystemThatWasSimulated:
    """`setup/prepared.pdb` is written before solvation and before the ligand
    is put back, so counting from it describes a different structure than the
    one that ran."""

    def test_the_system_is_looked_for_before_the_stages_toward_it(self) -> None:
        assert SYSTEM_CANDIDATES.index("setup/solvated.pdb") < SYSTEM_CANDIDATES.index(
            "setup/prepared.pdb"
        )
        assert SYSTEM_CANDIDATES.index("simulation/topology.pdb") < SYSTEM_CANDIDATES.index(
            "setup/prepared.pdb"
        )

    def test_what_is_drawn_is_still_the_solute(self, tmp_path: Path) -> None:
        """The viewer's preference is unchanged: a solvated topology sent whole
        to a browser is mostly water."""
        run = tmp_path / "run"
        for rel in ("setup/prepared.pdb", "simulation/topology.pdb"):
            path = run / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_solvated_pdb(), encoding="utf-8")

        assert find_structure(run) == run / "setup/prepared.pdb"
        assert STRUCTURE_CANDIDATES[0] == "setup/prepared.pdb"

    def test_but_what_is_counted_is_the_solvated_system(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        prepared = run / "setup" / "prepared.pdb"
        prepared.parent.mkdir(parents=True)
        prepared.write_text(_protein_pdb() + "\nEND\n", encoding="utf-8")
        topology = run / "simulation" / "topology.pdb"
        topology.parent.mkdir(parents=True)
        topology.write_text(_solvated_pdb(), encoding="utf-8")

        assert find_system(run) == topology

    def test_prepared_is_counted_when_it_is_all_there_is(self, tmp_path: Path) -> None:
        """Setup still going: describe what exists rather than nothing."""
        run = tmp_path / "run"
        prepared = run / "setup" / "prepared.pdb"
        prepared.parent.mkdir(parents=True)
        prepared.write_text(_protein_pdb() + "\nEND\n", encoding="utf-8")

        assert find_system(run) == prepared

    def test_the_endpoint_reports_the_ligand_water_and_ions(self, tmp_path: Path) -> None:
        """The whole complaint, end to end: BNZ in explicit solvent read as
        `Ligands none, Water 0, Ions 0`."""
        run = tmp_path / "run"
        prepared = run / "setup" / "prepared.pdb"
        prepared.parent.mkdir(parents=True)
        prepared.write_text(_protein_pdb() + "\nEND\n", encoding="utf-8")
        topology = run / "simulation" / "topology.pdb"
        topology.parent.mkdir(parents=True)
        topology.write_text(_solvated_pdb(), encoding="utf-8")

        server, base_url = start_test_server(run, config=DashboardConfig())
        try:
            payload = json.loads(urlopen(f"{base_url}/api/structure-info", timeout=5).read())
        finally:
            server.shutdown()
            server.server_close()

        assert payload["valid"] is True
        assert "BNZ" in payload["ligand_resnames"]
        assert payload["water_residues"] == 2
        assert payload["ions"] == 1

    def test_a_water_box_is_not_refused_as_too_large(self, tmp_path: Path) -> None:
        """Roughly 100k atoms is 8 MB of PDB, so the viewer's ceiling would
        have answered the counting question with `too-large`."""
        big = tmp_path / "topology.pdb"
        line = (
            "HETATM  201  O   HOH C   1      10.000   0.000   0.000  1.00 10.00           O\n"
        )
        with big.open("w", encoding="utf-8") as handle:
            handle.write(_protein_pdb() + "\n")
            handle.write(line * 130_000)
            handle.write("END\n")
        assert big.stat().st_size > _MAX_PDB_BYTES_FOR_INLINE_SCAN

        assert count_structure(big).get("reason") == "too-large"
        counted = count_structure(big, max_bytes=_MAX_PDB_BYTES_FOR_SYSTEM_SCAN)
        assert counted["valid"] is True
        assert counted["water_residues"] == 1

    def test_the_counting_endpoints_ask_the_counting_question(self) -> None:
        source = Path(protein_preview.__file__).with_name("server.py").read_text(
            encoding="utf-8"
        )
        for marker in ("def _structure_info_payload", "def _ligands_payload"):
            body = source.split(marker, 1)[1].split("\ndef ", 1)[0]
            assert "find_system(root)" in body, marker
            assert "max_bytes=_MAX_PDB_BYTES_FOR_SYSTEM_SCAN" in body, marker


class TestNothingDescribesItselfAsNotAvailable:
    """A placeholder that travels to the page is read as a value."""

    def test_the_writer_does_not_invent_a_stage(self, tmp_path: Path) -> None:
        writer = TelemetryWriter(tmp_path / "run" / "simulation", enabled=True)
        writer.write_status(status="running")

        assert read_status(tmp_path / "run")["stage"] is None

    def test_a_stage_that_was_reported_is_kept(self, tmp_path: Path) -> None:
        writer = TelemetryWriter(tmp_path / "run" / "simulation", enabled=True)
        writer.mark_stage("nvt", "current", status="running")

        assert read_status(tmp_path / "run")["stage"] == "nvt"

    def test_the_page_calls_a_run_without_one_starting(self) -> None:
        source = (
            Path(protein_preview.__file__).with_name("static") / "dashboard.js"
        ).read_text(encoding="utf-8")
        assert 'status.stage || "starting"' in source
        assert 'status.stage || "not available"' not in source


class TestThePrecisionIsRecordedWhileTheRunIsGoing:
    """The simulation manifest carries it, and is written when the run ends."""

    def test_the_writer_records_both(self, tmp_path: Path) -> None:
        writer = TelemetryWriter(
            tmp_path / "run" / "simulation",
            enabled=True,
            platform="CUDA",
            precision="mixed",
            precision_applied=True,
        )
        writer.write_status(status="running")

        status = read_status(tmp_path / "run")
        assert status["precision"] == "mixed"
        assert status["precision_applied"] is True

    def test_the_runner_passes_what_it_selected(self) -> None:
        from fastmdxplora.simulation import runner

        source = Path(runner.__file__).read_text(encoding="utf-8")
        assert "precision=precision," in source
        # `Precision` is a GPU-platform property, so what was requested and
        # what is in force are not the same claim.
        assert 'precision_applied=bool(platform_props.get("Precision"))' in source

    def test_a_none_on_disk_does_not_erase_a_live_value(self, tmp_path: Path) -> None:
        """The orchestrator opens the file to mark the setup phase before the
        runner exists. Merging its defaults back over the runner's own status
        is how platform came to read empty during a run."""
        run = tmp_path / "run"
        orchestrator = TelemetryWriter(run / "simulation", enabled=True)
        orchestrator.mark_stage("setup", "current", status="running")

        runner_writer = TelemetryWriter(
            run / "simulation",
            enabled=True,
            platform="CUDA",
            precision="mixed",
            precision_applied=True,
            total_steps=500,
        )
        runner_writer.write_status(stage="minimization", status="running", current_step=0)

        status = read_status(run)
        assert status["platform"] == "CUDA"
        assert status["precision"] == "mixed"
        assert status["total_planned_steps"] == 500


class TestTheViewerDrawsWhatWasSimulated:
    """The ligand is put back when the system is built, so the prepared solute
    is the one file that never has it."""

    def test_the_served_structure_has_the_ligand(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        prepared = run / "setup" / "prepared.pdb"
        prepared.parent.mkdir(parents=True)
        prepared.write_text(_protein_pdb() + "\nEND\n", encoding="utf-8")
        topology = run / "simulation" / "topology.pdb"
        topology.parent.mkdir(parents=True)
        topology.write_text(_solvated_pdb(), encoding="utf-8")

        server, base_url = start_test_server(run)
        try:
            body = urlopen(f"{base_url}/structure/topology.pdb", timeout=5).read().decode()
        finally:
            server.shutdown()
            server.server_close()

        assert "BNZ" in body
        assert "ALA" in body
        # ...and without the water box, which is what kept the viewer on the
        # prepared solute in the first place.
        assert "HOH" not in body
        assert " NA D" not in body

    def test_a_run_with_only_a_prepared_solute_still_draws(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        prepared = run / "setup" / "prepared.pdb"
        prepared.parent.mkdir(parents=True)
        prepared.write_text(_protein_pdb() + "\nEND\n", encoding="utf-8")

        server, base_url = start_test_server(run)
        try:
            body = urlopen(f"{base_url}/structure/topology.pdb", timeout=5).read().decode()
        finally:
            server.shutdown()
            server.server_close()

        assert "ALA" in body

    def test_the_version_stamp_follows_the_served_file(self, tmp_path: Path) -> None:
        """A stamp taken from a file nobody serves leaves a stale copy in the
        browser when the served one changes."""
        run = tmp_path / "run"
        prepared = run / "setup" / "prepared.pdb"
        prepared.parent.mkdir(parents=True)
        prepared.write_text(_protein_pdb() + "\nEND\n", encoding="utf-8")
        topology = run / "simulation" / "topology.pdb"
        topology.parent.mkdir(parents=True)
        topology.write_text(_solvated_pdb(), encoding="utf-8")

        source = Path(protein_preview.__file__).read_text(encoding="utf-8")
        body = source.split("def _structure_info(", 1)[1].split("\ndef ", 1)[0]
        assert "find_system(root)" in body


class TestTheConnectionRowReportsTheConnection:
    """It was filled with the run's own status, so a page whose server had
    gone away went on reporting the last thing a live run said about itself."""

    def _dashboard_js(self) -> str:
        return (
            Path(protein_preview.__file__).with_name("static") / "dashboard.js"
        ).read_text(encoding="utf-8")

    def test_the_run_status_no_longer_fills_it(self) -> None:
        source = self._dashboard_js()
        body = source.split("function applyStatus(", 1)[1].split("\n  function ", 1)[0]
        assert "sidebar-connection-state" not in body
        assert "sidebar-status-dot" not in body

    def test_the_connection_does(self) -> None:
        source = self._dashboard_js()
        body = source.split("function updateConnectionState(", 1)[1].split(
            "\n  function ", 1
        )[0]
        assert "sidebar-connection-state" in body
        for word in ("live", "reconnecting", "lost"):
            assert f'"{word}"' in body, word

    def test_the_stale_mark_is_removed_again(self) -> None:
        """Added and left, a dot reports a fault that has passed."""
        source = self._dashboard_js()
        body = source.split("function updateConnectionState(", 1)[1].split(
            "\n  function ", 1
        )[0]
        assert "classList.toggle" in body
        assert "classList.add" not in body


class TestTheStatusCardsDoNotContradictThePhaseTable:
    """`manifest.json` is written when the run ends, so during a run these
    cards read "Unknown / No run manifest found" and "0 / not available"
    directly above a table correctly saying Setup ok, Simulation running."""

    def _cards(self, root: Path) -> dict[str, tuple[str, str]]:
        from fastmdxplora.gui.report_dashboard import _summary_cards

        built = _summary_cards(
            project_root=root, manifest={}, analysis_manifest={}, sim_manifest={}
        )
        return {card.label: (card.value, card.detail) for card in built}

    def _reached(self, root: Path, *, stage: str) -> None:
        order = ("setup", "minimization", "nvt", "npt", "production")
        writer = TelemetryWriter(root / "simulation", enabled=True)
        for name in order[: order.index(stage)]:
            writer.mark_stage(name, "completed", status="running")
        writer.mark_stage(stage, "current", status="running")

    def test_a_run_in_production_has_finished_setup(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        self._reached(run, stage="production")

        cards = self._cards(run)
        assert cards["Project status"][0] == "Running"
        assert cards["Phases completed"] == ("1", "setup")

    def test_a_run_still_preparing_has_finished_nothing(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        self._reached(run, stage="setup")

        cards = self._cards(run)
        assert cards["Project status"][0] == "Running"
        assert cards["Phases completed"][0] == "0"
        # ...and says so as "not yet", not as a verdict about the run.
        assert cards["Phases completed"][1] == "none finished yet"

    def test_an_empty_folder_is_a_different_claim(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()

        cards = self._cards(empty)
        assert cards["Project status"][0] == "Not run"
        assert cards["Phases completed"][1] == "nothing has run"

    def test_no_card_reports_absence_as_a_value(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        self._reached(run, stage="npt")

        for value, detail in self._cards(run).values():
            assert "not available" not in f"{value} {detail}".lower()

    def test_a_finished_run_still_reads_from_its_manifest(self, tmp_path: Path) -> None:
        """The live path is a fallback, not a replacement."""
        from fastmdxplora.gui.report_dashboard import _summary_cards

        run = tmp_path / "run"
        self._reached(run, stage="production")
        manifest = {
            "phases": [
                {"name": "setup", "status": "ok"},
                {"name": "simulation", "status": "ok"},
            ]
        }
        built = _summary_cards(
            project_root=run, manifest=manifest, analysis_manifest={}, sim_manifest={}
        )
        cards = {card.label: (card.value, card.detail) for card in built}
        assert cards["Project status"][1] == "Recorded from manifest"
        assert cards["Phases completed"] == ("2", "setup, simulation")


class TestTheChartsDrawWhatIsRecorded:
    def _text(self, name: str) -> str:
        base = Path(protein_preview.__file__).parent
        folder = "templates" if name.endswith(".html") else "static"
        return (base / folder / name).read_text(encoding="utf-8")

    def test_total_energy_is_recorded(self) -> None:
        from fastmdxplora.gui.telemetry import METRIC_FIELDS, _normalise_energy_header

        assert "total_energy" in METRIC_FIELDS
        assert _normalise_energy_header("Total Energy (kJ/mole)") == "total_energy"

    def test_and_therefore_drawn(self) -> None:
        """It was written to disk from the first run and plotted in none of
        them: potential energy alone does not show whether the integration
        is holding."""
        assert '"total_energy"' in self._text("charts.js")
        assert 'data-chart="total_energy"' in self._text("dashboard.html")

    def test_every_chart_has_a_series_behind_it(self) -> None:
        import re

        drawn = set(re.findall(r'data-chart="(\w+)"', self._text("dashboard.html")))
        configured = set(re.findall(r'\{key: "(\w+)"', self._text("charts.js")))
        assert drawn == configured, drawn ^ configured

    def test_the_title_sits_above_its_canvas(self) -> None:
        """A title drawn over the canvas lands on the y-axis labels."""
        import re

        html = self._text("dashboard.html")
        rows = re.findall(r'<div class="chart-row">(.*?)<canvas', html, re.S)
        assert len(rows) == 5, len(rows)
        for row in rows:
            assert "chart-title" in row

    def test_the_chart_head_is_styled(self) -> None:
        """Markup added without a rule for it stacked as blocks against the
        left margin, and the canvas overflowed a clipping row by the height
        of the title."""
        import re

        html = self._text("dashboard.html")
        css = self._text("dashboard.css")

        used = set(re.findall(r'class="(chart-[\w-]+)"', html))
        used |= set(re.findall(r'class="(chart-[\w-]+) ', html))
        for name in used:
            assert f".{name}" in css, name
        assert "flex-direction: column" in css.split(".chart-row {")[-1][:200]

    def test_each_chart_carries_its_current_value(self) -> None:
        """The number and its trend, in one place. A separate strip repeated
        four of them without their history and was a third register a metric
        had to be added to."""
        import re

        html = self._text("dashboard.html")
        plotted = set(re.findall(r'data-chart="(\w+)"', html))
        valued = set(re.findall(r'data-chart-value="(\w+)"', html))
        assert plotted == valued, plotted ^ valued
        assert 'data-metric="potential_energy"' not in html
        assert "renderMetricCards" not in self._text("dashboard.js")


class TestOneHealthPanel:
    def test_the_duplicate_card_is_gone(self) -> None:
        base = Path(protein_preview.__file__).parent
        html = (base / "templates" / "dashboard.html").read_text(encoding="utf-8")
        js = (base / "static" / "dashboard.js").read_text(encoding="utf-8")

        assert html.count("Simulation health") == 1
        assert "live-health-message" not in html
        # ...and nothing is still writing to it.
        assert "live-health" not in js


class TestThePayloadsDoNotManufactureAVerdict:
    """`_display_value` existed to turn absence into the words "not available",
    which then reached the page as the system name in the top bar."""

    def _running(self, root: Path) -> None:
        writer = TelemetryWriter(root / "simulation", enabled=True, platform="CPU")
        writer.mark_stage("production", "current", status="running")

    def test_the_top_bar_gets_no_name_rather_than_a_wrong_one(
        self, tmp_path: Path
    ) -> None:
        from fastmdxplora.gui.server import _system_info

        run = tmp_path / "run"
        self._running(run)
        assert _system_info(run, {}, {}, {})["system"] == ""

    def test_what_is_known_is_still_reported(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.server import _system_info

        run = tmp_path / "run"
        self._running(run)
        assert _system_info(run, {}, {}, {})["platform"] == "CPU"

    def test_absence_is_a_dash(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.server import _system_info

        run = tmp_path / "run"
        self._running(run)
        info = _system_info(run, {}, {}, {})
        assert info["atoms"] == "—"
        assert "not available" not in " ".join(info.values())

    def test_a_run_without_a_manifest_is_in_progress_not_unknown(
        self, tmp_path: Path
    ) -> None:
        from fastmdxplora.gui.server import _summary_records

        run = tmp_path / "run"
        self._running(run)
        rows = {row["label"]: row["value"] for row in _summary_records(run, {}, {}, {})}
        assert rows["Project status"] == "in progress"
        assert "not available" not in " ".join(rows.values())

    def test_and_an_empty_folder_is_neither(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.server import _summary_records

        empty = tmp_path / "nothing"
        empty.mkdir()
        rows = {row["label"]: row["value"] for row in _summary_records(empty, {}, {}, {})}
        assert rows["Project status"] == "not run"


class TestTheFrameCountIsCountedNotPlanned:
    def test_frames_written_over_steps_actually_run(self) -> None:
        from fastmdxplora.simulation.runner import _frames_written

        assert _frames_written(50_000, 250) == 200
        # A run stopped a fifth of the way in wrote a fifth of the frames.
        assert _frames_written(10_000, 250) == 40
        assert _frames_written(0, 250) == 0
        assert _frames_written(50_000, None) == 0

    def test_the_sampler_reports_it_while_the_run_goes(self, a_watched_run) -> None:
        """It used to arrive once, when production ended, so a page watching
        a run showed a frame count of none for the whole of it. Reported at
        every telemetry sample through production, and rising."""
        counts = [m["frame_count"] for m in a_watched_run.metrics
                  if m["stage"].lower() == "production"]
        assert len(counts) > 3
        assert counts == sorted(counts) and counts[-1] > counts[0]

    def test_equilibration_reports_no_count_rather_than_zero(self, a_watched_run) -> None:
        """No trajectory is being written there, so a zero would be a claim."""
        # The run's closing sample is labelled "production" in lower case.
        during_equilibration = [m["frame_count"] for m in a_watched_run.metrics
                                if m["stage"].lower() != "production"]
        assert during_equilibration and all(c is None for c in during_equilibration)

    def test_the_recorded_total_is_not_the_plan_restated(self, a_watched_run) -> None:
        # The planned frame count is the production steps over the frame
        # interval, not the whole plan's steps: equilibration writes none.
        run = a_watched_run
        assert run.planned_frames == run.production_steps // run.frame_interval
        assert run.planned_frames < (run.production_steps + run.equilibration_steps) // run.frame_interval
        last = [m["frame_count"] for m in run.metrics if m["stage"].lower() == "production"][-1]
        assert last == run.planned_frames
        # And the file holds that many frames: the count is counted, not planned.
        import mdtraj as md

        dcd = next(run.out.rglob("*.dcd"))
        top = next(run.out.rglob("topology.pdb"))
        assert md.load(str(dcd), top=str(top)).n_frames == last

class TestARunThatFinishedSaysSo:
    """The project-level writer marks setup, marks analysis and report, and
    records the run ending. It was created only when a config named
    `live_telemetry` outright, so a run leaving it at its default got
    telemetry from the runner and none of those."""

    def _writer_for(self, tmp_path: Path, options: dict):
        from fastmdxplora.orchestrator import FastMDXplora

        class Stub:
            output_dir = tmp_path
            options: dict = {}

        return FastMDXplora._dashboard_writer(Stub(), options)

    def test_the_default_is_enough(self, tmp_path: Path) -> None:
        assert self._writer_for(tmp_path, {"simulation": {}}) is not None

    def test_and_the_schema_is_where_the_default_lives(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        assert PHASE_SCHEMAS["simulation"].get("live_telemetry").default is True

    def test_turning_it_off_still_turns_it_off(self, tmp_path: Path) -> None:
        options = {"simulation": {"live_telemetry": False}}
        assert self._writer_for(tmp_path, options) is None

    def test_a_completed_run_is_not_stale_telemetry(self, tmp_path: Path) -> None:
        """`analyze_health` gates staleness on the run still running, which is
        right -- it fired here only because nothing recorded the end."""
        from fastmdxplora.gui.telemetry import analyze_health

        old = "2020-01-01T00:00:00+00:00"
        running = analyze_health({"status": "running", "last_update_timestamp": old}, [])
        finished = analyze_health({"status": "completed", "last_update_timestamp": old}, [])

        assert running["state"] == "warning"
        assert finished["state"] != "warning"

    def test_a_run_that_does_not_simulate_writes_no_telemetry(
        self, tmp_path: Path
    ) -> None:
        """Telemetry lives under `simulation/`, so an analysis-only run would
        otherwise create that folder and claim to have recorded something."""
        from fastmdxplora.orchestrator import FastMDXplora

        class Stub:
            output_dir = tmp_path
            options: dict = {}

        writer = FastMDXplora._dashboard_writer(
            Stub(), {"simulation": {}}, ["analysis", "report"]
        )
        assert writer is None
        assert not (tmp_path / "simulation").exists()


class TestTheTopBarCarriesProgressNotTrivia:
    def _text(self, name: str) -> str:
        base = Path(protein_preview.__file__).parent
        folder = "templates" if name.endswith(".html") else "static"
        return (base / folder / name).read_text(encoding="utf-8")

    def test_the_platform_is_not_reported_twice(self) -> None:
        """It is static, and the sidebar footer already carries it."""
        html = self._text("dashboard.html")
        assert 'id="sidebar-platform"' in html
        assert 'id="topbar-platform"' not in html

    def test_one_observable_is_not_promoted_above_the_others(self) -> None:
        """Temperature sat in the top bar while density, energy and speed did
        not, and it appeared twice more on the same screen."""
        assert 'id="topbar-temperature"' not in self._text("dashboard.html")

    def test_what_replaced_them_is_progress(self) -> None:
        html = self._text("dashboard.html")
        js = self._text("dashboard.js")
        assert 'id="topbar-progress"' in html
        assert 'id="topbar-eta"' in html
        assert 'setText("topbar-eta", computeETA(status))' in js

    def test_the_output_folder_is_where_the_run_is_identified(self) -> None:
        """It is navigation, not a measurement, so it left the grid of things
        measured about the run."""
        from fastmdxplora.gui.report_dashboard import _summary_cards

        html = self._text("dashboard.html")
        assert 'id="sidebar-output-folder"' in html

        labels = [
            card.label
            for card in _summary_cards(
                project_root=Path("/tmp"),
                manifest={"phases": [{"name": "setup", "status": "ok"}]},
                analysis_manifest={},
                sim_manifest={},
            )
        ]
        assert "Output folder" not in labels


class TestInapplicableIsNotUnavailable:
    """A count has one value, not a sample. The standard-deviation column
    said "not available", which reads as a measurement that was attempted."""

    def test_a_count_has_no_standard_deviation(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.report_dashboard import _metric_rows

        rows = {
            row.metric: row
            for row in _metric_rows(tmp_path, {"n_frames": 200, "n_atoms": 36_843})
        }
        assert rows["Frame count"].average == "200"
        assert rows["Frame count"].stddev == "—"
        assert rows["Atom count"].stddev == "—"

    def test_an_empty_table_says_what_is_missing(self) -> None:
        from fastmdxplora.gui import report_dashboard

        source = Path(report_dashboard.__file__).read_text(encoding="utf-8")
        assert "No analysis outputs to summarise yet." in source
        assert '<td colspan="4" class="table-empty">not available' not in source

    def test_the_report_header_does_not_name_an_absent_system(self) -> None:
        from fastmdxplora.gui import report_dashboard

        source = Path(report_dashboard.__file__).read_text(encoding="utf-8")
        assert 'if system else "not available"' not in source


class TestTheSuiteDoesNotReportABusyMachineAsADefect:
    def test_one_ceiling_for_every_request(self) -> None:
        from fastmdxplora.gui import protein_preview

        tests_dir = Path(protein_preview.__file__).parents[3] / "tests"
        source = (tests_dir / "test_gui.py").read_text(encoding="utf-8")
        assert "HTTP_TIMEOUT = " in source
        # Not one raised number among a dozen that were left alone.
        assert "timeout=5" not in source


class TestTheBannerDescribesTheRunItIsStarting:
    """The frame interval was read from the command line alone. A run driven
    by a config file typed none of it, so the banner computed a default and
    announced a frame every 100 steps for a run writing one every 250 --
    500 frames promised, 200 written."""

    @staticmethod
    def _banner(argv, **fields) -> str:
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

    def test_the_config_is_read(self, tmp_path: Path) -> None:
        config = tmp_path / "study.yml"
        config.write_text(
            "simulation:\n"
            "  production_steps: 50000\n"
            "  trajectory_interval_steps: 250\n",
            encoding="utf-8",
        )

        text = self._banner(["explore", "--config", str(config)])

        assert "every 250 production steps" in text
        # 100 is what `trajectory_interval_for(50000)` computes, and what the
        # banner used to print regardless of what the run was told to do.
        assert "every 100 production steps" not in text

    def test_the_command_line_still_wins(self, tmp_path: Path) -> None:
        config = tmp_path / "study.yml"
        config.write_text(
            "simulation:\n  trajectory_interval_steps: 250\n", encoding="utf-8"
        )

        text = self._banner(
            ["explore", "--config", str(config),
             "--simulate-trajectory-interval-steps", "500"]
        )

        assert "every 500 production steps" in text


class TestTheTopBarNamesTheRunFromItsFirstMinute:
    """Reading only the manifest -- written when a run ends -- the top bar
    showed a placeholder for the whole run. Setup records the system before
    simulation starts."""

    def _setup_written(self, root: Path, system: str) -> None:
        import json

        (root / "setup").mkdir(parents=True)
        (root / "setup" / "setup_parameters.json").write_text(
            json.dumps({"phase": "setup", "input": {"system": system}}),
            encoding="utf-8",
        )

    def test_the_name_comes_from_setup_during_a_run(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.server import _system_info

        run = tmp_path / "run"
        self._setup_written(run, "181L")
        assert _system_info(run, {}, {}, {})["system"] == "181L"

    def test_the_manifest_still_wins_once_there_is_one(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.server import _system_info

        run = tmp_path / "run"
        self._setup_written(run, "181L")
        assert _system_info(run, {"system": "1UBQ"}, {}, {})["system"] == "1UBQ"

    def test_with_no_name_anywhere_the_browser_chooses(self, tmp_path: Path) -> None:
        """Empty, not a dash: a dash would override the page's own label."""
        from fastmdxplora.gui.server import _system_info

        run = tmp_path / "run"
        run.mkdir()
        assert _system_info(run, {}, {}, {})["system"] == ""


class TestTheTimelineShowsTheStagesThatRan:
    """A phase block in a config is not a statement about what runs.

    `write_resolved_config` used to write only phases with non-empty options,
    and it writes into the output directory when the run ends -- so a run
    whose analysis and report used defaults showed seven stages while running
    and five the moment it finished. It now names every setting every phase
    used, so the blocks are always all four and say even less about what ran;
    either way `include` and `exclude` are what answer this."""

    def _config(self, root: Path, text: str) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "resolved_config.yml").write_text(text, encoding="utf-8")

    def _manifest(self, root: Path, *names: str) -> None:
        import json

        root.mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(
            json.dumps({"phases": [{"name": n, "status": "ok"} for n in names]}),
            encoding="utf-8",
        )

    def test_a_finished_run_shows_what_it_ran(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.telemetry import run_phases, run_stages

        run = tmp_path / "run"
        self._config(run, "setup: {ph: 7.4}\nsimulation: {nvt_steps: 5000}\n")
        self._manifest(run, "setup", "simulation", "analysis", "report")

        assert run_phases(run) == ["setup", "simulation", "analysis", "report"]
        assert len(run_stages(run)) == 7

    def test_a_configured_phase_is_not_an_included_one(self, tmp_path: Path) -> None:
        """Without a manifest, a config naming two phase blocks says nothing
        about the other two -- so assume all rather than hide them."""
        from fastmdxplora.gui.telemetry import run_phases, run_stages

        run = tmp_path / "run"
        self._config(run, "setup: {ph: 7.4}\nsimulation: {nvt_steps: 5000}\n")

        assert run_phases(run) == []
        assert len(run_stages(run)) == 7

    def test_include_is_still_honoured(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.telemetry import run_phases

        run = tmp_path / "run"
        self._config(run, "include: [analysis, report]\n")
        assert run_phases(run) == ["analysis", "report"]

    def test_exclude_is_honoured_too(self, tmp_path: Path) -> None:
        """Stated outright, unlike a phase block, so it can be trusted."""
        from fastmdxplora.gui.telemetry import run_phases

        run = tmp_path / "run"
        self._config(run, "exclude: [report]\n")
        assert "report" not in run_phases(run)
        assert "setup" in run_phases(run)


class TestEveryTabIsReachable:
    """The Ligand tab was clipped off the right edge of the information
    panel: four flex items that refuse to shrink below their labels, on one
    unwrapped line, inside a container with `overflow: hidden`."""

    def _text(self, name: str) -> str:
        base = Path(protein_preview.__file__).parent
        folder = "templates" if name.endswith(".html") else "static"
        return (base / folder / name).read_text(encoding="utf-8")

    def test_the_strip_wraps(self) -> None:
        css = self._text("dashboard.css")
        rule = css.split(".info-tabs {", 1)[1].split("}", 1)[0]
        assert "flex-wrap: wrap" in rule

    def test_a_tab_keeps_its_label(self) -> None:
        """`flex: 1` is `1 1 0%`; a basis of zero with `min-width: auto` is
        what made the line overflow rather than the tabs share it."""
        css = self._text("dashboard.css")
        rule = css.split("\n.info-tab {", 1)[1].split("}", 1)[0]
        assert "flex: 1 1 auto" in rule

    def test_all_four_tabs_are_declared(self) -> None:
        import re

        html = self._text("dashboard.html")
        tabs = re.findall(r'class="info-tab[^"]*"[^>]*data-tab="(\w+)"', html)
        panes = re.findall(r'class="info-pane[^"]*" data-tab="(\w+)"', html)
        assert set(tabs) == {"structure", "simulation", "selection", "ligand"}
        assert set(tabs) == set(panes)


class TestAFactAppearsOnce:
    """Progress, ETA and step were on the page three times each -- the top
    bar, the Simulation progress card, and the Exploration status hero -- so
    a reader had to check three places to see whether they agreed."""

    def _text(self, name: str) -> str:
        base = Path(protein_preview.__file__).parent
        folder = "templates" if name.endswith(".html") else "static"
        return (base / folder / name).read_text(encoding="utf-8")

    def test_the_hero_keeps_only_what_it_says_best(self) -> None:
        html = self._text("dashboard.html")
        # The hero card is gone altogether: the sidebar's status line is
        # the one place the run's state is said, on every page.
        for gone in ("hero-status-text", "hero-stage", "hero-progress-fill",
                     "hero-progress-pct", "hero-sim-time", "hero-elapsed",
                     "hero-eta", "hero-step"):
            assert gone not in html, gone
        assert 'id="topbar-status-text"' in html
        assert 'id="topbar-stage"' in html

    def test_nothing_still_writes_to_them(self) -> None:
        js = self._text("dashboard.js")
        for gone in ("hero-progress-fill", "hero-sim-time", "hero-elapsed",
                     "hero-eta", "hero-step"):
            assert gone not in js, gone

    def test_the_numbers_still_have_a_home(self) -> None:
        """Removed from the hero, not from the page."""
        html = self._text("dashboard.html")
        for kept in ("live-progress-fill", "live-sim-time", "live-elapsed-cell",
                     "live-eta-cell", "live-step-cell", "topbar-progress", "topbar-eta"):
            assert kept in html, kept

    def test_no_cell_opens_with_a_verdict(self) -> None:
        """Placeholders the first poll overwrites. Until then a dash, not a
        claim that the value could not be obtained."""
        import re

        html = self._text("dashboard.html")
        filled = re.findall(r'id="(?:live|hero|topbar|sidebar|health)[\w-]*"[^>]*>([^<]*)<', html)
        assert "not available" not in filled


class TestAFinishedRunIsNotProgressing:
    """Every check in `analyze_health` reads the last sample. When nothing was
    wrong it fell through to "Normal progress / Simulation is progressing
    normally" -- present tense, on a page opened hours after the run ended."""

    def test_a_completed_run_says_so(self) -> None:
        from fastmdxplora.gui.telemetry import analyze_health

        health = analyze_health({"status": "completed"}, [])
        assert health["state"] == "ok"
        assert health["message"] == "Completed"
        assert "progressing" not in health["explanation"]

    def test_a_running_one_still_reads_the_same(self) -> None:
        from fastmdxplora.gui.telemetry import analyze_health

        health = analyze_health({"status": "running"}, [])
        assert health["message"] == "Normal progress"

    def test_a_finished_run_that_went_wrong_still_says_so(self) -> None:
        """The terminal case is about tense, not about suppressing faults."""
        from fastmdxplora.gui.telemetry import analyze_health

        health = analyze_health(
            {"status": "completed"}, [{"temperature": float("nan")}]
        )
        assert health["state"] == "failed"

    def test_the_advice_matches_the_default(self) -> None:
        """The no-telemetry text said the feature was off by default and told
        you to switch it on. It has been on by default since the schema
        changed, so that was a fix for a problem that no longer exists."""
        from fastmdxplora.config.schema import PHASE_SCHEMAS
        from fastmdxplora.gui.telemetry import analyze_health

        assert PHASE_SCHEMAS["simulation"].get("live_telemetry").default is True
        explanation = analyze_health({}, [])["explanation"]
        assert "off by default" not in explanation
        assert "on by default" in explanation


class TestThePanelSaysWhatHeldTheLigand:
    """Three rows read "Requires analysis output" unconditionally -- on a run
    that had produced `analysis/pl_interactions/`, they were asking for
    something already on disk. And they could not distinguish a contact type
    looked for and not found from one never looked for."""

    def _table(self, root: Path, *rows: str) -> None:
        folder = root / "analysis" / "pl_interactions"
        folder.mkdir(parents=True)
        header = (
            "kind,ligand_atom,protein_atom,residue,frames_present,"
            "frames_total,occupancy,episodes,standard_error,well_sampled\n"
        )
        (folder / "pl_interactions.dat").write_text(
            header + "".join(rows), encoding="utf-8"
        )

    def test_an_unanalysed_run_says_so(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.server import _ligand_interactions

        assert _ligand_interactions(tmp_path)["analysed"] is False

    def test_the_kinds_are_counted(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.server import _ligand_interactions

        self._table(
            tmp_path,
            "hydrophobic,1,10,LEU99,180,200,0.90,3,0.02,True\n",
            "hydrophobic,2,14,VAL111,40,200,0.20,7,0.03,False\n",
            "hydrogen_bond,3,22,SER117,120,200,0.60,2,0.02,True\n",
        )
        kinds = _ligand_interactions(tmp_path)["kinds"]

        # Two rows, two residues -- and where the same residue contributes
        # several atom pairs it is still one residue.
        assert kinds["hydrophobic"]["residues"] == 2
        assert kinds["hydrophobic"]["pairs"] == 2
        assert kinds["hydrophobic"]["best_occupancy"] == 0.90
        assert kinds["hydrophobic"]["best_residue"] == "LEU99"
        assert kinds["hbonds"]["residues"] == 1

    def test_looked_for_and_not_found_is_its_own_answer(self, tmp_path: Path) -> None:
        """Different from never looked for, which is what the row used to say
        in both cases."""
        from fastmdxplora.gui.server import _ligand_interactions

        self._table(tmp_path, "hydrophobic,1,10,LEU99,180,200,0.90,3,0.02,True\n")
        measured = _ligand_interactions(tmp_path)

        assert measured["analysed"] is True
        assert measured["kinds"]["salt_bridges"]["residues"] == 0

    def test_a_thinly_observed_contact_is_counted_as_such(self, tmp_path: Path) -> None:
        """A contact seen in a handful of frames is not the observation a bare
        count implies."""
        from fastmdxplora.gui.server import _ligand_interactions

        self._table(
            tmp_path,
            "hydrophobic,1,10,LEU99,180,200,0.90,3,0.02,True\n",
            "hydrophobic,2,14,VAL111,40,200,0.20,7,0.03,False\n",
        )
        assert _ligand_interactions(tmp_path)["kinds"]["hydrophobic"]["thinly_sampled"] == 1

    def test_the_rows_are_no_longer_hardcoded(self) -> None:
        base = Path(protein_preview.__file__).parent
        js = (base / "static" / "dashboard.js").read_text(encoding="utf-8")
        assert "<td>Requires analysis output</td>" not in js
        assert 'interactionCell(info, "salt_bridges")' in js

    def test_atom_pairs_are_not_reported_as_contacts(self, tmp_path: Path) -> None:
        """`pl_interactions.dat` has one row per ligand-atom / protein-atom
        pair. Benzene against three residues produced thirteen rows, and
        reporting that read as though the ligand were held by thirteen
        separate things."""
        from fastmdxplora.gui.server import _ligand_interactions

        rows = [
            f"hydrophobic,{i},{20 + i},LEU99,180,200,0.84,3,0.02,True\n"
            for i in range(1, 8)
        ] + [
            f"hydrophobic,{i},{40 + i},VAL111,30,200,0.15,9,0.03,False\n"
            for i in range(1, 6)
        ] + ["hydrophobic,9,60,ILE78,150,200,0.75,2,0.02,True\n"]
        self._table(tmp_path, *rows)

        hydrophobic = _ligand_interactions(tmp_path)["kinds"]["hydrophobic"]
        assert hydrophobic["pairs"] == 13
        assert hydrophobic["residues"] == 3
        assert hydrophobic["best_residue"] == "LEU99"
        # VAL111's every contact is thinly observed; the other two are not.
        assert hydrophobic["thinly_sampled"] == 1

    def test_the_numbering_names_the_structure_it_belongs_to(self) -> None:
        """The ligand's chain and residue id are OpenMM's, from the solvated
        system. Labelling them "(simulated)" stopped them being mistaken for
        the crystal numbering; the panel now gives both, because the run keeps
        the structure it started from and the crystal position can be read
        straight out of it."""
        base = Path(protein_preview.__file__).parent
        js = (base / "static" / "dashboard.js").read_text(encoding="utf-8")
        assert "Position in simulation" in js
        assert "Position in structure" in js
        assert "<th>Chain</th>" not in js


class TestTheViewerRespondsToClicksAndToggles:
    def _text(self, name: str) -> str:
        base = Path(protein_preview.__file__).parent
        return (base / "static" / name).read_text(encoding="utf-8")

    def test_atoms_are_made_clickable_after_they_exist(self) -> None:
        """3Dmol sets the property on the atoms currently selected. Called at
        viewer creation, before any model is added, it set it on nothing --
        so every atom arrived unclickable and the selection panel waited for
        an event that could not be raised."""
        js = self._text("molecule-viewer.js")
        install = js.split("function installPdb(", 1)[1].split("\n  function ", 1)[0]
        assert "setClickable" in install
        assert "setHoverable" in install

        creation = js.split("function ensureMainViewer(", 1)[1].split("\n  function ", 1)[0]
        assert "setClickable" not in creation

    def test_water_and_ions_are_fetched_rather_than_restyled(self) -> None:
        """They are stripped from the copy the browser gets, so restyling
        could never reveal them."""
        js = self._text("molecule-viewer.js")
        assert "solvent=1" in js
        handler = js.split('checkbox.addEventListener("change"', 1)[1].split("\n    });", 1)[0]
        assert 'which === "water" || which === "ions" || which === "box"' in handler
        assert "onStructureUpdated" in handler

    def test_the_route_serves_the_solvated_system_on_request(
        self, tmp_path: Path
    ) -> None:
        from urllib.request import urlopen

        run = tmp_path / "run"
        sim = run / "simulation"
        sim.mkdir(parents=True)
        (sim / "topology.pdb").write_text(_solvated_pdb(), encoding="utf-8")

        server, base_url = start_test_server(run)
        try:
            plain = urlopen(f"{base_url}/structure/topology.pdb", timeout=30).read().decode()
            full = urlopen(
                f"{base_url}/structure/topology.pdb?solvent=1", timeout=30
            ).read().decode()
        finally:
            server.shutdown()
            server.server_close()

        assert "HOH" not in plain and "BNZ" in plain
        assert "HOH" in full and "BNZ" in full


class TestTheFileListCanBeReadWithoutGuessing:
    def _text(self, name: str) -> str:
        base = Path(protein_preview.__file__).parent
        folder = "templates" if name.endswith(".html") else "static"
        return (base / folder / name).read_text(encoding="utf-8")

    def test_a_row_names_the_file_within_its_group(self) -> None:
        """Sixteen analyses each write an `options.json`. Listed by bare
        filename they were sixteen identical rows, told apart only by their
        byte counts -- and the payload already carried the path that
        distinguishes them."""
        js = self._text("dashboard.js")
        row = js.split("function fileRowHtml(file) {", 1)[1].split("\n  }", 1)[0]
        assert 'String(file.path || "").split("/").slice(1)' in row
        assert row.index("within") < row.index("file.name")

    def test_the_payload_carries_the_path_that_distinguishes_them(
        self, tmp_path: Path
    ) -> None:
        from fastmdxplora.gui.server import _artifact_records

        for analysis in ("rmsd", "rmsf"):
            target = tmp_path / "analysis" / analysis
            target.mkdir(parents=True)
            (target / "options.json").write_text("{}", encoding="utf-8")

        paths = {record["path"] for record in _artifact_records(tmp_path)}
        assert paths == {"analysis/rmsd/options.json", "analysis/rmsf/options.json"}
        # The names alone do not tell them apart, which is what the row used
        # to show.
        assert {record["name"] for record in _artifact_records(tmp_path)} == {
            "options.json"
        }

    def test_the_viewer_scratch_is_folded_away(self) -> None:
        """The live page writes a rolling PDB history as it goes. Sixty-four
        of them listed flat buried the trajectory, the checkpoint and the
        final state. They now land in the run record, which folds as a whole
        -- the same treatment for the same reason, applied to manifests and
        options files too."""
        from fastmdxplora.gui.server import _artifact_label

        label, group = _artifact_label(
            "simulation/live_frames/frame_000001_nvt_000000001000.pdb"
        )
        assert group == "record"
        assert "scratch" in label.lower()

        js = self._text("dashboard.js")
        assert 'key === "record"' in js
        assert "file-fold" in js
        assert ".file-fold" in self._text("dashboard.css")

    def test_a_row_says_what_the_file_is(self) -> None:
        """Deciding whether to download 85 MB should not require knowing that
        `production.dcd` is the trajectory."""
        from fastmdxplora.gui.server import _artifact_label

        assert _artifact_label("simulation/production.dcd") == (
            "Production trajectory", "simulation",
        )
        assert _artifact_label("report/report.pdf")[0] == "Report (PDF)"
        assert _artifact_label("analysis/rmsd/options.json")[0] == "rmsd: options used"

    def test_the_groups_are_purposes_not_directories(self) -> None:
        """`production.dcd` and the dashboard's own scratch shared a group
        because they share a folder."""
        from fastmdxplora.gui.server import _artifact_label

        assert _artifact_label("simulation/production.dcd")[1] == "simulation"
        assert _artifact_label("simulation/live_status.json")[1] == "record"
        assert _artifact_label("analysis/rmsd/rmsd.png")[1] == "figures"
        assert _artifact_label("analysis/rmsd/rmsd.dat")[1] == "analysis"

    def test_the_record_is_kept_rather_than_hidden(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.server import _artifact_records

        scratch = tmp_path / "simulation" / "live_frames"
        scratch.mkdir(parents=True)
        (scratch / "frame_000001_nvt_000000001000.pdb").write_text("x", encoding="utf-8")

        records = _artifact_records(tmp_path)
        assert len(records) == 1
        assert records[0]["group"] == "record"
        assert records[0]["href"].startswith("/artifacts/")

    def test_the_analysis_tab_does_not_offer_the_report(self) -> None:
        """The markdown report, slides, bundle and analysis manifest are the
        report phase's deliverables, and the Report tab lists all of them with
        size and date. Four of them repeated here made this the place people
        looked."""
        js = self._text("dashboard.js")
        section = js.split("function renderAnalysisSections(payload) {", 1)[1].split(
            "\n  function ", 1
        )[0]
        assert "quick_actions" not in section
        assert "actionHost.hidden = true" in section


class TestTheContactResiduesAreNamed:
    """The row read "Use \u201cShow pocket residues\u201d" -- an instruction where
    a value belongs, for residues the analysis had already identified."""

    def _table(self, root: Path, *rows: str) -> None:
        folder = root / "analysis" / "pl_interactions"
        folder.mkdir(parents=True)
        header = (
            "kind,ligand_atom,protein_atom,residue,frames_present,"
            "frames_total,occupancy,episodes,standard_error,well_sampled\n"
        )
        (folder / "pl_interactions.dat").write_text(
            header + "".join(rows), encoding="utf-8"
        )

    def test_the_contact_residues_are_named(self, tmp_path: Path) -> None:
        """Best observed first, and across every kind rather than one."""
        from fastmdxplora.gui.server import _ligand_interactions

        self._table(
            tmp_path,
            "hydrophobic,1,10,ALA99,180,200,0.84,3,0.02,True\n",
            "hydrophobic,2,14,VAL111,150,200,0.75,4,0.02,True\n",
            "hydrogen_bond,3,26,SER117,120,200,0.60,2,0.02,True\n",
        )
        residues = _ligand_interactions(tmp_path)["contact_residues"]

        # Best observed first, and across every kind rather than one of them.
        assert residues == ["ALA99", "VAL111", "SER117"]

    def test_a_contact_is_not_a_neighbour(self) -> None:
        """The viewer's pocket button takes every residue within a distance
        cutoff. These met an interaction criterion. Reporting one under a
        label meaning the other would overstate what was measured."""
        base = Path(protein_preview.__file__).parent
        js = (base / "static" / "dashboard.js").read_text(encoding="utf-8")

        assert "<th>Contact residues</th>" in js
        assert "<th>Pocket residues</th>" in js
        assert "Show pocket residues" in js
        assert "<th>Nearby residues</th>" not in js


class TestADirectoryHoldsOneRun:
    """Nothing stopped a second run writing over the first. It overwrote the
    science files as it produced them, appended to the metrics CSV and the
    event log, and merged its status forward from the previous run's -- so one
    run's platform and step count appeared against another's charts, with the
    two traces drawn as one line."""

    def _orchestrator(self, root: Path):
        from fastmdxplora.orchestrator import FastMDXplora

        return FastMDXplora(system="181L", output_dir=str(root))

    def _used(self, root: Path, phase: str) -> None:
        folder = root / phase
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "output.txt").write_text("x", encoding="utf-8")

    def test_a_second_run_is_refused(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        self._used(run, "setup")

        with pytest.raises(FileExistsError) as caught:
            self._orchestrator(run)._refuse_to_overwrite(
                ["setup", "simulation", "analysis", "report"], force=False
            )
        # The message has to say what to do, not only what is wrong.
        assert "--force" in str(caught.value)
        assert "setup" in str(caught.value)

    def test_force_overrides_it(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        self._used(run, "setup")
        self._orchestrator(run)._refuse_to_overwrite(["setup"], force=True)

    def test_a_fresh_directory_is_fine(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        self._orchestrator(run)._refuse_to_overwrite(["setup", "simulation"], force=False)

    def test_an_empty_phase_directory_is_fine(self, tmp_path: Path) -> None:
        """Created and not written to -- a previous run that got nowhere is
        not output to protect."""
        run = tmp_path / "run"
        (run / "setup").mkdir(parents=True)
        self._orchestrator(run)._refuse_to_overwrite(["setup"], force=False)

    def test_the_phase_by_phase_workflow_still_works(self, tmp_path: Path) -> None:
        """`analyze` into a directory holding a finished simulation is the
        intended use, so only the phases this run will produce are checked."""
        run = tmp_path / "run"
        self._used(run, "simulation")
        self._orchestrator(run)._refuse_to_overwrite(["analysis", "report"], force=False)

    def test_the_flag_exists_on_explore(self) -> None:
        from fastmdxplora.cli.main import _build_parser

        parser = _build_parser()
        explore = parser._subparsers._group_actions[0].choices["explore"]
        assert any("--force" in action.option_strings for action in explore._actions)

    def test_it_reaches_a_config_driven_study(self) -> None:
        """A study runs through the batch layer, which builds one orchestrator
        per system -- so the flag has to travel to reach the check."""
        import inspect

        from fastmdxplora.batch import explorer

        assert "force" in inspect.signature(explorer._execute_run).parameters
        assert "force" in inspect.signature(explorer.BatchExplorer.__init__).parameters

    def test_a_study_refuses_before_it_starts(self, tmp_path: Path) -> None:
        """The per-run refusal is raised inside a worker and recorded as a
        failed run: the study exits non-zero and says nothing about why. A
        study of eight systems should also not run seven before saying the
        eighth cannot start."""
        from fastmdxplora.batch.explorer import BatchExplorer

        run = tmp_path / "run"
        self._used(run, "setup")
        config = tmp_path / "study.yml"
        config.write_text(
            f"output: {run}\ninclude: [setup]\n"
            "systems:\n  - {id: a, system: 181L}\n",
            encoding="utf-8",
        )

        with pytest.raises(FileExistsError) as caught:
            BatchExplorer(config=str(config)).run()
        assert "--force" in str(caught.value)

    def test_the_cli_says_it_plainly(self, tmp_path: Path, capsys) -> None:
        """Refusing to overwrite is a decision, not a crash -- no traceback."""
        from fastmdxplora.cli.main import main

        run = tmp_path / "run"
        self._used(run, "setup")
        config = tmp_path / "study.yml"
        config.write_text(
            f"output: {run}\ninclude: [setup]\n"
            "systems:\n  - {id: a, system: 181L}\n",
            encoding="utf-8",
        )

        code = main(["explore", "--config", str(config)])
        message = capsys.readouterr().err

        assert code == 2
        assert message.startswith("fastmdx: ")
        assert "already hold results" in message
        assert "--force" in message
        assert "Traceback" not in message


class TestTheLigandIsIdentifiedTwoWays:
    """The panel gave OpenMM's numbering from the solvated system -- `X 0` for
    a benzene the PDB entry calls `BNZ A400`. Both are true, of different
    files: the crystal numbering is what a reader would check and what other
    tools expect, and OpenMM's is what the viewer selects on."""

    def _input_pdb(self, root: Path, *lines: str) -> None:
        folder = root / "setup"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "input.pdb").write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")

    def test_the_crystal_position_is_read_from_the_kept_input(
        self, tmp_path: Path
    ) -> None:
        """Not plumbed through setup: the run already keeps the structure it
        started from, unaltered, at `setup/input.pdb`."""
        from fastmdxplora.gui.server import _crystal_positions

        self._input_pdb(
            tmp_path,
            "ATOM      1  CA  ALA A  99       0.000   0.000   0.000  1.00  0.00           C",
            "HETATM 1305  C1  BNZ A 400       5.000   0.000   0.000  1.00  0.00           C",
        )
        assert _crystal_positions(tmp_path) == {"BNZ": ["A400"]}

    def test_several_copies_are_all_reported(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.server import _crystal_positions

        self._input_pdb(
            tmp_path,
            "ATOM      1  CA  ALA A  99       0.000   0.000   0.000  1.00  0.00           C",
            "HETATM 1305  C1  BNZ A 400       5.000   0.000   0.000  1.00  0.00           C",
            "HETATM 1306  C1  BNZ B 400       9.000   0.000   0.000  1.00  0.00           C",
        )
        assert _crystal_positions(tmp_path)["BNZ"] == ["A400", "B400"]

    def test_a_run_without_its_input_says_nothing(self, tmp_path: Path) -> None:
        """An analysis-only run pointed at a bare trajectory has no source
        structure to read, which is a gap rather than a wrong answer."""
        from fastmdxplora.gui.server import _crystal_positions

        assert _crystal_positions(tmp_path) == {}

    def test_the_panel_labels_which_numbering_is_which(self) -> None:
        base = Path(protein_preview.__file__).parent
        js = (base / "static" / "dashboard.js").read_text(encoding="utf-8")

        assert "<th>Position in structure</th>" in js
        assert "<th>Position in simulation</th>" in js
        # The unqualified labels are what made `X 0` look like a crystal
        # position in the first place.
        assert "<th>Chain</th>" not in js
        assert "<th>Residue ID</th>" not in js


class TestTheSolventTogglesShowSolvent:
    """`?solvent=1` fetched the solvated system, and the viewer went on
    drawing the live frame -- which is written to disk with water and ions
    already stripped, so live mode cannot show them at all."""

    def _text(self, name: str) -> str:
        base = Path(protein_preview.__file__).parent
        return (base / "static" / name).read_text(encoding="utf-8")

    def test_a_written_frame_has_no_solvent_to_reveal(self) -> None:
        from fastmdxplora.gui.live_frames import dashboard_display_pdb

        drawn = dashboard_display_pdb(
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
            "HETATM  201  O   HOH C   1      10.000   0.000   0.000  1.00 10.00           O\n"
            "HETATM  301 NA    NA D   1      20.000   0.000   0.000  1.00 10.00          NA\n"
        )
        assert "HOH" not in drawn
        assert " NA D" not in drawn

    def test_the_full_topology_is_requested_for_solvent_and_box(self) -> None:
        js = self._text("molecule-viewer.js")
        body = js.split("async function onStructureUpdated(", 1)[1].split(
            "\n  function ", 1
        )[0]
        assert "needsFullTopology()" in body
        assert "withSolvent(info.structure_url" in body

    def test_a_new_frame_does_not_replace_a_full_topology_overlay(self) -> None:
        """A solute-only live frame must not empty the selected environment."""
        js = self._text("molecule-viewer.js")
        live_frame = js.split("async function pollLiveFrame(", 1)[1].split(
            "\n  function ", 1
        )[0]
        assert "if (needsFullTopology())" in live_frame
        assert "return;" in live_frame

    def test_toggling_preserves_playback_and_reloads_static_structure(self) -> None:
        js = self._text("molecule-viewer.js")
        handler = js.split('checkbox.addEventListener("change"', 1)[1].split("\n    });", 1)[0]
        assert 'if (STATE.mode === "playback")' in handler
        assert "ensurePlaybackEnvironment" in handler
        assert "STATE.currentPdb = null" in handler
        assert "onStructureUpdated" in handler


class TestAListOfChoicesIsOfferedAsChoices:
    """`include` and `exclude` take several analyses from a known set, and the
    form gave a text box -- so a misspelling was found only when the run did
    not do what was asked."""

    def test_the_schema_names_the_analyses(self) -> None:
        import fastmdxplora.analysis  # noqa: F401  (populates the registry)
        from fastmdxplora.analysis.orchestrator import available_analyses
        from fastmdxplora.config.schema import ANALYSIS_NAMES

        # Held in step by this test, because the analyses import the schema
        # and reading the registry back from it would be a cycle.
        assert set(ANALYSIS_NAMES) == set(available_analyses())

    def test_both_fields_offer_them(self) -> None:
        from fastmdxplora.config.schema import ANALYSIS_NAMES, PHASE_SCHEMAS

        for name in ("include", "exclude"):
            field = PHASE_SCHEMAS["analysis"].get(name)
            assert tuple(field.choices) == ANALYSIS_NAMES, name

    def test_a_list_of_choices_is_not_a_single_choice(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS
        from fastmdxplora.gui.schema_payload import field_payload

        listed = field_payload(PHASE_SCHEMAS["analysis"].get("include"))
        assert listed["control"] == "multiselect"
        assert listed["choices"]

        # A single-valued field with choices is unaffected.
        single = PHASE_SCHEMAS["analysis"].get("scope")
        if single is not None and single.choices:
            assert field_payload(single)["control"] == "select"

    def test_the_builder_reads_a_list_back(self) -> None:
        from fastmdxplora.gui import protein_preview

        base = Path(protein_preview.__file__).parent
        js = (base / "static" / "run-builder.js").read_text(encoding="utf-8")
        assert 'field.control === "multiselect"' in js
        assert "input.readValue" in js
        # An empty selection means the field is absent, not an empty list.
        assert "Array.isArray(value) && value.length === 0" in js


class TestOneControlPerSetting:
    """The Measurements panel is the analysis picker -- sixteen analyses
    grouped by what they answer, each with what it measures and why. Offering
    `include` and `exclude` again in the options grid put two controls on one
    key, and the grid won: its config is merged over the panel's, so four
    chosen measurements could be silently replaced by whatever was typed
    into the grid, with neither control mentioning the other."""

    def _builder(self) -> str:
        from fastmdxplora.gui import protein_preview

        base = Path(protein_preview.__file__).parent
        return (base / "static" / "run-builder.js").read_text(encoding="utf-8")

    def test_the_grid_does_not_offer_the_analysis_picker_again(self) -> None:
        js = self._builder()
        assert "OWNED_ELSEWHERE" in js
        owned = js.split("const OWNED_ELSEWHERE = ", 1)[1].split("\n", 1)[0]
        assert '"include"' in owned and '"exclude"' in owned

    def test_the_panel_still_writes_the_key(self) -> None:
        js = self._builder()
        assert "analysis.include = Array.from(state.analyses)" in js

    def test_the_panel_says_what_it_writes(self) -> None:
        """The mapping from a picker to a config key should not be implicit."""
        assert "analysis.include" in self._builder()

    def test_the_grid_still_offers_lists_it_owns(self) -> None:
        """Suppression is per phase and per field, not a retreat from the
        multiselect control."""
        js = self._builder()
        assert 'field.control === "multiselect"' in js
        assert "OWNED_ELSEWHERE[phase]" in js

    def test_a_comma_string_from_the_browser_is_still_read_as_a_list(self) -> None:
        """The panel joins its choice with commas; the field is declared a
        list. `_coerce` splits it back, so the two agree."""
        from fastmdxplora.config.schema import PHASE_SCHEMAS
        from fastmdxplora.gui.config_builder import _coerce

        field = PHASE_SCHEMAS["analysis"].get("include")
        assert _coerce("rmsd, rmsf", field) == ["rmsd", "rmsf"]


class TestTheDefaultBoxIsNotACube:
    def test_a_dodecahedron_by_default(self) -> None:
        """Water is most of the atoms in a solvated system, and a cube keeps
        more of it than the clearance requires. 5WYZ came to 711,205 atoms."""
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = PHASE_SCHEMAS["setup"].get("box_shape")
        assert field.default == "dodecahedron"
        assert "cube" in field.choices

    def test_the_reason_is_recorded_where_the_default_is(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        help_text = PHASE_SCHEMAS["setup"].get("box_shape").help
        assert "71%" in help_text
        # ...and when a cube is still the right answer.
        assert "orthorhombic" in help_text

    def test_a_default_box_that_the_shape_made_invalid_is_adjusted(self, caplog) -> None:
        """A dodecahedron's smallest dimension is shorter than a cube's for
        the same padding, so the default padding that suits a cube leaves a
        dodecahedron under twice the cutoff. Solvated with a stand-in that
        builds the box the shape would: the loop re-solvates once, at a
        padding that reaches the target, and says so."""
        box = _a_box_builder(shape="dodecahedron", extent_nm=0.8)
        _solvate(box, cutoff_nm=1.0, padding_nm=1.2, caplog=caplog)
        assert box.solvations == 2
        assert "Re-solvating" in caplog.text
        # Reaches the target, not 1/sqrt(2) of it: the cube's arithmetic
        # undershot every dodecahedron by that factor, and this run's box
        # came out at 2.05 nm against a 2.20 nm floor and died in NPT.
        assert box.smallest >= 2.0 * 1.0 * NPT_CONTRACTION_MARGIN

    def test_but_contradictory_settings_are_reported_not_absorbed(self, caplog) -> None:
        """0.4 nm of padding with a 1.5 nm cutoff needs four times the box.
        Somebody who asked for both has made a mistake, and is told, rather
        than handed a system four times the size: the loop stops short of
        the growth it would take, and the check further down refuses."""
        box = _a_box_builder(shape="cube", extent_nm=0.8)
        _solvate(box, cutoff_nm=1.5, padding_nm=0.4, caplog=caplog)
        assert box.solvations == 1                        # not grown
        assert box.smallest < 2.0 * 1.5
        assert "Stopping at" in caplog.text

class TestATerminusIsNotALoop:
    """`findMissingResidues` schedules every residue SEQRES declares and the
    model does not have -- including those before the first and after the last
    anyone could see. A gap between two resolved residues is pinned at both
    ends. A run past the last resolved one is anchored at one end and free at
    the other, and what gets built there walks."""

    class _Chain:
        def __init__(self, count: int) -> None:
            self._count = count

        def residues(self):
            return iter(range(self._count))

    class _Topology:
        def __init__(self, *counts: int) -> None:
            self._chains = [TestATerminusIsNotALoop._Chain(n) for n in counts]

        def chains(self):
            return iter(self._chains)

    class _Fixer:
        def __init__(self, topology, missing) -> None:
            self.topology = topology
            self.missingResidues = missing

    def test_internal_gaps_are_built(self) -> None:
        from fastmdxplora.setup.pdbfix import _drop_terminal_extensions

        fixer = self._Fixer(
            self._Topology(281),
            {(0, 40): ["GLY", "SER"], (0, 150): ["ALA"]},
        )
        _drop_terminal_extensions(fixer)
        assert fixer.missingResidues == {(0, 40): ["GLY", "SER"], (0, 150): ["ALA"]}

    def test_terminal_extensions_are_not(self) -> None:
        from fastmdxplora.setup.pdbfix import _drop_terminal_extensions

        fixer = self._Fixer(
            self._Topology(281),
            {(0, 0): ["MET"] * 50, (0, 100): ["GLY"], (0, 281): ["LEU"] * 87},
        )
        _drop_terminal_extensions(fixer)
        # The loop survives; the two termini do not.
        assert fixer.missingResidues == {(0, 100): ["GLY"]}

    def test_asking_for_them_still_builds_them(self) -> None:
        from fastmdxplora.setup.pdbfix import _drop_terminal_extensions

        missing = {(0, 0): ["MET"], (0, 281): ["LEU"]}
        fixer = self._Fixer(self._Topology(281), dict(missing))
        _drop_terminal_extensions(fixer, build_termini=True)
        assert fixer.missingResidues == missing

    def test_the_option_exists_and_is_off(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = PHASE_SCHEMAS["setup"].get("build_missing_termini")
        assert field is not None
        assert field.default is False


class TestAStructureIsCheckedBeforeItIsSolvated:
    """6B73 arrived 51 nm across for 1,104 residues and the first anyone heard
    of it was `addMembrane` failing with a NaN ten minutes later."""

    class _Topology:
        def __init__(self, count: int) -> None:
            self._count = count

        def residues(self):
            return iter(range(self._count))

    class _Unit:
        nanometer = 1

    def _positions(self, span: float, atoms: int = 2000):
        import numpy as np

        return np.random.default_rng(0).uniform(0, span, size=(atoms, 3))

    def test_a_folded_complex_passes(self) -> None:
        from fastmdxplora.setup.prepare import _refuse_an_implausible_structure

        _refuse_an_implausible_structure(
            self._Topology(1104), self._positions(12.0), self._Unit()
        )

    def test_a_fibril_passes_too(self) -> None:
        """The bound is several times a folded protein's size, so a long
        assembly is not refused for being long."""
        from fastmdxplora.setup.prepare import _refuse_an_implausible_structure

        _refuse_an_implausible_structure(
            self._Topology(2000), self._positions(40.0), self._Unit()
        )

    def test_a_structure_that_is_not_one_object_is_refused(self) -> None:
        from fastmdxplora.setup.prepare import _refuse_an_implausible_structure

        positions = self._positions(12.0)
        positions[0] = [58.0, 58.0, 58.0]
        with pytest.raises(ValueError) as caught:
            _refuse_an_implausible_structure(
                self._Topology(1104), positions, self._Unit()
            )
        message = str(caught.value)
        assert "58 nm" in message and "1104 residues" in message
        # It names the usual cause and where to look.
        assert "build_missing_termini" in message
        assert "prepared.pdb" in message


class TestCopiesOfAChainFaceTheSameWay:
    """Every other membrane check asks about one chain at a time, and each
    copy passes them whichever way up it is. 6B73 completed with 193,605
    atoms and its two receptors antiparallel -- long axes at z -0.81 and
    +0.80, dot product -0.99 -- with their soluble partners on opposite faces
    of one bilayer."""

    def _chains(self, *specs):
        """specs: (axis direction, residue count) per chain."""
        import numpy as np

        coordinates: list = []
        chains: dict[int, list[int]] = {}
        counts: dict[int, int] = {}
        for index, (direction, residues) in enumerate(specs):
            start = len(coordinates)
            unit = np.asarray(direction, dtype=float)
            unit = unit / np.linalg.norm(unit)
            for residue in range(residues):
                for atom in range(5):
                    coordinates.append(
                        unit * (residue * 0.35)
                        + np.array([atom * 0.05, index * 3.0, 0.0])
                    )
            chains[index] = list(range(start, len(coordinates)))
            counts[index] = residues
        return chains, counts, np.asarray(coordinates)

    #: The axes measured on 6B73's built system.
    RECEPTOR_DOWN = (-0.08, -0.58, -0.81)
    RECEPTOR_UP = (-0.08, 0.59, 0.80)
    PARTNER_A = (-1.00, -0.07, -0.06)
    PARTNER_B = (-0.95, 0.32, -0.06)

    def test_the_real_case_is_caught(self) -> None:
        from fastmdxplora.setup.membrane import inverted_chain_pairs

        found = inverted_chain_pairs(
            *self._chains((self.RECEPTOR_DOWN, 289), (self.RECEPTOR_UP, 281))
        )
        assert found == [(0, 1)]

    def test_ragged_ends_do_not_make_two_copies_different_molecules(self) -> None:
        """289 and 281 residues: the same receptor, with a different number of
        unresolved terminal residues declined at each end. Requiring equal
        counts read them as different molecules and compared nothing, which
        is why the first version of this check passed on 6B73."""
        from fastmdxplora.setup.membrane import inverted_chain_pairs

        assert inverted_chain_pairs(
            *self._chains((self.RECEPTOR_DOWN, 289), (self.RECEPTOR_UP, 281))
        )

    def test_chains_lying_in_the_plane_are_left_alone(self) -> None:
        """6B73's two partners sit at z -0.06: chains lying in the membrane
        plane, not chains pointing down. A sign test on a number that small
        reports noise as a fault."""
        from fastmdxplora.setup.membrane import inverted_chain_pairs

        assert inverted_chain_pairs(
            *self._chains((self.PARTNER_A, 126), (self.PARTNER_B, 126))
        ) == []

    def test_copies_facing_the_same_way_are_accepted(self) -> None:
        from fastmdxplora.setup.membrane import inverted_chain_pairs

        assert inverted_chain_pairs(
            *self._chains((self.RECEPTOR_DOWN, 289), ((0.08, 0.58, -0.81), 281))
        ) == []

    def test_one_copy_cannot_disagree_with_itself(self) -> None:
        from fastmdxplora.setup.membrane import inverted_chain_pairs

        assert inverted_chain_pairs(*self._chains((self.RECEPTOR_DOWN, 289))) == []

    def test_chains_of_genuinely_different_length_are_not_copies(self) -> None:
        """A receptor and its partner point differently for good reasons."""
        from fastmdxplora.setup.membrane import inverted_chain_pairs

        assert inverted_chain_pairs(
            *self._chains((self.RECEPTOR_DOWN, 289), (self.RECEPTOR_UP, 140))
        ) == []

    def test_the_message_says_what_to_do(self) -> None:
        # Two chains pointing opposite ways across a bilayer: the refusal
        # names the database to check against and the alternative, not only
        # the symptom.
        from fastmdxplora.setup.membrane import check_chains_point_the_same_way

        problem = check_chains_point_the_same_way(*_two_antiparallel_helices())
        assert problem is not None
        assert "opm.phar.umich.edu" in problem
        assert "building one copy" in problem

    def test_it_runs_inside_the_membrane_path(self, tmp_path, monkeypatch) -> None:
        # Asked for a membrane, and not for water. Run through prepare_system
        # with the checks recorded, as the membrane tests do.
        from tests.test_membrane import _prepared

        bilayer = _prepared(tmp_path / "bilayer", monkeypatch, membrane="POPC",
                            membrane_orient=True)
        assert "check_chains_point_the_same_way" in bilayer.checked
        water = _prepared(tmp_path / "water", monkeypatch)
        assert "check_chains_point_the_same_way" not in water.checked

class TestTheWordmarkIsDrawnOnce:
    """The class held three `_GLYPHS` sets in a row -- a mixed-case one, then
    two uppercase ones -- each shadowing the last. The mixed-case set had
    never once been used, and the banner printed in capitals regardless."""

    def test_there_is_one_glyph_set(self) -> None:
        # Read from the source on purpose: one definition of the glyphs and
        # one of the wordmark, so an edit cannot leave two that disagree.
        # What the glyphs are is checked by the tests beside this one.
        import inspect

        from fastmdxplora.utils import presenter

        source = inspect.getsource(presenter.SessionPresenter)
        assert source.count("_GLYPHS: dict[str, tuple[str, ...]] = {") == 1
        assert source.count("_WORDMARK = ") == 1

    def test_every_glyph_is_a_rectangle(self) -> None:
        """One character short in any row shifts every letter after it."""
        from fastmdxplora.utils.presenter import SessionPresenter

        for name, glyph in SessionPresenter._GLYPHS.items():
            widths = {len(row) for row in glyph}
            assert len(widths) == 1, f"{name} has rows of {sorted(widths)}"
            assert len(glyph) == 5, f"{name} has {len(glyph)} rows"

    def test_the_wordmark_reads_in_mixed_case(self) -> None:
        from fastmdxplora.utils.presenter import SessionPresenter

        assert SessionPresenter._WORDMARK == "FastMDXplora"
        # Every letter it names has to be drawable.
        missing = set(SessionPresenter._WORDMARK) - set(SessionPresenter._GLYPHS)
        assert not missing, missing

    def test_it_prints(self) -> None:
        import io

        from fastmdxplora.utils.presenter import SessionPresenter

        buffer = io.StringIO()
        SessionPresenter(stream=buffer).welcome()
        printed = buffer.getvalue()

        # The descender on `p` is what gives the name a baseline rather than
        # a straight cut, and it is the row a four-row font would lose.
        assert "|_|   " not in printed.splitlines()[1]
        assert len(printed.splitlines()) >= 6
        assert "Fully" in printed and "eXploration" in printed


class TestChainsCanBeChosen:
    """A deposited entry is what the experiment produced, not what anyone
    means to simulate. 6B73 holds two copies of a receptor-G protein complex
    whose two-fold is not perpendicular to the membrane, so both together
    cannot be embedded and one alone can."""

    def _atom(self, record, serial, resname, chain, seq, x, y, z):
        return (
            f"{record:<6}{serial:>5} {'CA':^4} {resname:>3} {chain}{seq:>4}    "
            f"{x:>8.3f}{y:>8.3f}{z:>8.3f}  1.00  0.00"
        )

    def _structure(self, tmp_path: Path) -> Path:
        """Two copies of a chain, and one ligand on each -- both numbered
        into a chain of their own, as entries are free to do."""
        lines, serial = [], 1
        for chain, offset in (("A", 0.0), ("B", 50.0)):
            for residue in range(1, 21):
                lines.append(self._atom(
                    "ATOM", serial, "ALA", chain, residue, offset + residue * 1.5, 0, 0))
                serial += 1
        for seq, offset in ((900, 5.0), (901, 55.0)):
            for step in range(3):
                lines.append(self._atom(
                    "HETATM", serial, "LIG", "L", seq, offset + step * 1.4, 1.0, 0))
                serial += 1
        lines.append("END")
        path = tmp_path / "input.pdb"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _kept(self, path: Path):
        return [
            line for line in path.read_text(encoding="utf-8").splitlines()
            if line[:6].strip() in {"ATOM", "HETATM"}
        ]

    def test_only_the_named_chains_survive(self, tmp_path: Path) -> None:
        from fastmdxplora.setup.pipeline import _select_chains

        kept = self._kept(_select_chains(self._structure(tmp_path), "A"))
        assert "B" not in {line[21] for line in kept}

    def test_a_ligand_follows_the_chain_it_binds(self, tmp_path: Path) -> None:
        """Numbered into chain L, not A. Matching on the ligand's own chain ID
        would drop it out of a kept binding site and the run would go on
        without it -- a wrong answer that completes."""
        from fastmdxplora.setup.pipeline import _select_chains

        kept = self._kept(_select_chains(self._structure(tmp_path), "A"))
        residues = {line[17:20].strip() + line[22:27].strip() for line in kept}
        assert "LIG900" in residues

    def test_the_other_copys_ligand_does_not(self, tmp_path: Path) -> None:
        from fastmdxplora.setup.pipeline import _select_chains

        kept = self._kept(_select_chains(self._structure(tmp_path), "A"))
        residues = {line[17:20].strip() + line[22:27].strip() for line in kept}
        assert "LIG901" not in residues

    def test_choosing_both_keeps_both(self, tmp_path: Path) -> None:
        from fastmdxplora.setup.pipeline import _select_chains

        kept = self._kept(_select_chains(self._structure(tmp_path), "A,B"))
        residues = {line[17:20].strip() + line[22:27].strip() for line in kept}
        assert {"LIG900", "LIG901"} <= residues

    def test_every_shape_the_option_arrives_in(self, tmp_path: Path) -> None:
        """The field is declared a list, so `--setup-chains A,B` arrives as
        ["A,B"] -- one element holding two names -- while a config's
        `chains: [A, B]` arrives as two. Splitting only the outer shape read
        "A,B" as a single chain and refused a structure that has both."""
        from fastmdxplora.setup.pipeline import _select_chains

        structure = self._structure(tmp_path)
        both = {"A", "B"}
        for spec in ("A,B", ["A,B"], ["A", "B"], "A B", ("A", "B")):
            kept = self._kept(_select_chains(structure, spec))
            assert {line[21] for line in kept if line.startswith("ATOM")} == both, spec

    def test_one_chain_still_means_one(self, tmp_path: Path) -> None:
        from fastmdxplora.setup.pipeline import _select_chains

        structure = self._structure(tmp_path)
        for spec in ("A", ["A"]):
            kept = self._kept(_select_chains(structure, spec))
            assert {line[21] for line in kept if line.startswith("ATOM")} == {"A"}

    def test_naming_a_chain_that_is_not_there_is_refused(self, tmp_path: Path) -> None:
        """Silently simulating everything because a name was mistyped is the
        failure this feature exists to prevent."""
        from fastmdxplora.setup.pipeline import _select_chains

        with pytest.raises(ValueError) as caught:
            _select_chains(self._structure(tmp_path), "Z")
        assert "Z" in str(caught.value)
        # It says what is there instead.
        assert "A" in str(caught.value) and "B" in str(caught.value)

    def test_selecting_nothing_leaves_the_structure_alone(self, tmp_path: Path) -> None:
        from fastmdxplora.setup.pipeline import _select_chains

        structure = self._structure(tmp_path)
        assert _select_chains(structure, "") == structure

    def test_the_declared_sequence_follows_the_chains(self, tmp_path: Path) -> None:
        """`SEQRES` is what PDBFixer compares the model against. Dropping it
        left `findMissingResidues` with nothing to find: no terminal residues
        declined -- right by accident -- and no internal loops built either,
        so a chain break stayed in a structure that simulated anyway. 6B73
        selected to chains A and C came out with 0 SEQRES records where the
        deposited file has 88, and not one residue was rebuilt."""
        from fastmdxplora.setup.pipeline import _select_chains

        structure = self._structure(tmp_path)
        text = structure.read_text(encoding="utf-8")
        structure.write_text(
            "SEQRES   1 A   25  MET ALA LEU\n"
            "SEQRES   1 B   25  MET ALA LEU\n" + text,
            encoding="utf-8",
        )

        kept = _select_chains(structure, "A").read_text(encoding="utf-8").splitlines()
        seqres = [line for line in kept if line.startswith("SEQRES")]
        assert len(seqres) == 1
        assert seqres[0][11:12] == "A"

    def test_annotations_naming_a_dropped_chain_go_with_it(
        self, tmp_path: Path
    ) -> None:
        """A helix record for a chain that is no longer here describes a
        structure the file does not hold."""
        from fastmdxplora.setup.pipeline import _select_chains

        structure = self._structure(tmp_path)
        text = structure.read_text(encoding="utf-8")
        structure.write_text(
            "HELIX    1   1 ALA A    3  LEU A    9  1\n"
            "HELIX    2   2 ALA B    3  LEU B    9  1\n" + text,
            encoding="utf-8",
        )

        kept = _select_chains(structure, "A").read_text(encoding="utf-8").splitlines()
        helices = [line for line in kept if line.startswith("HELIX")]
        assert len(helices) == 1
        assert " A " in helices[0]

    def test_the_option_exists(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = PHASE_SCHEMAS["setup"].get("chains")
        assert field is not None and field.default is None


class TestTheBeltIsMeasuredOnTheSurface:
    """Every folded protein buries its hydrophobic residues and exposes its
    charged ones, so comparing all of them measures burial -- true of a
    soluble protein as much as a membrane one. That is why this check passed
    on a structure 51 nm across and on one with a receptor upside down.

    What distinguishes a membrane protein is a band of hydrophobic residues
    on its outside, where a soluble protein has polar ones."""

    def _blob(self, count: int, radius: float, half_height: float, seed: int = 0):
        import numpy as np

        rng = np.random.default_rng(seed)
        direction = rng.normal(size=(count, 3))
        direction /= np.linalg.norm(direction, axis=1)[:, None]
        points = direction * (rng.random(count) ** (1 / 3))[:, None]
        points[:, :2] *= radius
        points[:, 2] *= half_height
        return points, rng

    def _membrane_like(self):
        from fastmdxplora.setup.membrane import surface_residues

        points, rng = self._blob(400, 2.0, 3.0)
        exposed = surface_residues(points)
        kinds = [
            ("hydrophobic" if abs(points[i, 2]) < 1.6 else "charged")
            if exposed[i]
            else ("hydrophobic" if rng.random() < 0.7 else "charged")
            for i in range(len(points))
        ]
        return points, kinds

    def _soluble_like(self):
        from fastmdxplora.setup.membrane import surface_residues

        points, rng = self._blob(400, 2.2, 2.2, seed=1)
        exposed = surface_residues(points)
        kinds = [
            ("hydrophobic" if rng.random() < 0.35 else "charged")
            if exposed[i]
            else ("hydrophobic" if rng.random() < 0.7 else "charged")
            for i in range(len(points))
        ]
        return points, kinds

    def test_a_banded_surface_reads_low(self) -> None:
        from fastmdxplora.setup.membrane import surface_belt_ratio

        points, kinds = self._membrane_like()
        assert surface_belt_ratio(points, kinds)[2] < 0.5

    def test_a_mixed_surface_reads_high(self) -> None:
        from fastmdxplora.setup.membrane import surface_belt_ratio

        points, kinds = self._soluble_like()
        assert surface_belt_ratio(points, kinds)[2] > 0.7

    def test_the_two_separate_further_than_burial_does(self) -> None:
        """Burial gave 0.55 against 0.79 -- a factor of one and a half, with
        the threshold sitting inside the noise between them."""
        from fastmdxplora.setup.membrane import surface_belt_ratio

        membrane = surface_belt_ratio(*self._membrane_like())[2]
        soluble = surface_belt_ratio(*self._soluble_like())[2]
        assert soluble / membrane > 1.8

    def test_too_few_residues_says_nothing(self) -> None:
        """A claim from ten residues would be a claim from nothing."""
        from fastmdxplora.setup.membrane import surface_belt_ratio

        points, _ = self._blob(12, 2.0, 2.0)
        assert surface_belt_ratio(points, ["hydrophobic"] * 12) is None

    def test_only_one_kind_present_says_nothing(self) -> None:
        from fastmdxplora.setup.membrane import surface_belt_ratio

        points, _ = self._blob(400, 2.0, 3.0)
        assert surface_belt_ratio(points, ["hydrophobic"] * 400) is None

    def test_the_surface_is_a_relative_cut(self) -> None:
        """An absolute neighbour count depends on how big and how densely
        packed the structure is."""
        from fastmdxplora.setup.membrane import surface_residues

        for count in (100, 400):
            points, _ = self._blob(count, 2.0, 3.0)
            exposed = surface_residues(points)
            assert 0.2 < exposed.mean() < 0.6, count

    def test_a_helix_has_no_inside(self) -> None:
        """A single helix is all surface. Restricting to the least-surrounded
        residues picks its two ends, which says nothing about the arrangement
        between them -- and made the check pass on the soluble arrangement it
        exists to refuse."""
        import numpy as np

        from fastmdxplora.setup.membrane import surface_residues

        rng = np.random.RandomState(0)
        helix = np.array([[0.0, 0.0, (i - 15) * 0.15] for i in range(30)])
        helix = helix + rng.normal(scale=0.05, size=(30, 3))
        assert surface_residues(helix).all()

    def test_an_extended_chain_has_no_inside_either(self) -> None:
        """Even with enough residues to have a core, a structure whose
        residues are all similarly surrounded does not have one."""
        import numpy as np

        from fastmdxplora.setup.membrane import surface_residues

        rng = np.random.RandomState(1)
        chain = np.array([[0.0, 0.0, i * 0.15] for i in range(200)])
        chain = chain + rng.normal(scale=0.05, size=(200, 3))
        assert surface_residues(chain).all()

    def test_a_globule_does(self) -> None:
        from fastmdxplora.setup.membrane import surface_residues

        points, _ = self._blob(400, 2.0, 2.0)
        exposed = surface_residues(points)
        assert 0.2 < exposed.mean() < 0.6

    def test_the_refusal_renders(self) -> None:
        """The check reached the right verdict and then raised a NameError
        explaining it: the message named two variables a refactor had moved
        into the measurement. Nothing here caught it, because building the
        message needed a real topology and OpenMM, which the tests that would
        have reached it skip without."""
        from fastmdxplora.setup.membrane import belt_refusal

        text = belt_refusal(0.92, 1.10, 0.84)
        for number in ("0.92", "1.10", "0.84"):
            assert number in text, number
        assert "opm.phar.umich.edu" in text

    def test_the_measurement_returns_what_the_message_quotes(self) -> None:
        """Three numbers, in the order the refusal reads them."""
        from fastmdxplora.setup.membrane import surface_belt_ratio

        points, kinds = self._membrane_like()
        hydrophobic, charged, ratio = surface_belt_ratio(points, kinds)
        assert ratio == pytest.approx(hydrophobic / charged)

    def test_giving_up_is_said_out_loud(self, caplog) -> None:
        """The check further down reports the padding the config asked for
        and knows nothing of what was tried. Silent, this advised raising
        0.80 nm without mentioning that a larger value had already been
        tried; the message names the padding it would take."""
        box = _a_box_builder(shape="cube", extent_nm=0.8)
        _solvate(box, cutoff_nm=1.5, padding_nm=0.4, caplog=caplog)
        said = next(m for m in caplog.messages if m.startswith("Stopping at"))
        assert "0.40 nm padding" in said and "1.50 nm" in said
        assert "would need about" in said

class TestEveryStageShowsProgress:
    """`_run_md_stage` took `on_step_progress` and drove the bar;
    `_run_md_stage_with_live_metrics` had no such parameter. That second one
    is the branch taken whenever live telemetry is on, which is the default --
    so production, the stage that takes the hours, was the one stage that
    showed nothing. "Production: 1,000,000 steps" and then fifty minutes of a
    terminal indistinguishable from a hung one."""

    def test_the_chunked_stage_accepts_the_callback(self) -> None:
        import inspect

        from fastmdxplora.simulation.runner import (
            _run_md_stage_with_live_metrics,
        )

        parameters = inspect.signature(_run_md_stage_with_live_metrics).parameters
        assert "on_step_progress" in parameters

    def test_every_call_site_passes_it(self, tmp_path) -> None:
        """Three stages -- NVT, NPT and production -- and each has two
        branches depending on whether telemetry is on. All six should show a
        bar: each stage reports, with telemetry on and with it off."""
        from tests._the_phase import a_run_s_progress

        for telemetry in (True, False):
            labels = {label.split()[0] for label, _done, _total in
                      a_run_s_progress(tmp_path / str(telemetry), telemetry=telemetry)}
            assert {"NVT", "NPT", "Production"} <= labels, (telemetry, labels)

    def test_the_bar_is_driven_from_inside_the_chunk_loop(self, a_watched_run) -> None:
        """Where the work happens: a callback outside it would fire once."""
        production = [p for p in a_watched_run.progress if p["stage"] == "Production"]
        assert len(production) > 3
        done = [p["done"] for p in production]
        assert done == sorted(done) and done[-1] == a_watched_run.production_steps

    def test_it_reports_a_rate_and_a_time_left(self, a_watched_run) -> None:
        """A percentage alone does not say whether to wait or to stop."""
        production = [p for p in a_watched_run.progress if p["stage"] == "Production"]
        with_numbers = [p for p in production if p["rate"] is not None]
        assert with_numbers
        assert all(p["rate"] > 0 for p in with_numbers)
        assert all(p["left"] >= 0 for p in with_numbers)
        assert with_numbers[-1]["left"] == pytest.approx(0.0, abs=1e-9)

class _a_box_builder:
    """A modeller whose addSolvent builds the box the shape would, from the
    padding it is given. A cube is the solute's extent plus twice the
    padding. A rhombic dodecahedron follows what OpenMM built on the run
    the growth loop was fixed for: the smallest perpendicular width came
    out at root two times the padding, 1.70 nm at 1.20, the solute's own
    0.76 nm extent contributing almost nothing. Records how many times it
    was asked."""

    def __init__(self, *, shape: str, extent_nm: float):
        self.shape, self.extent, self.solvations = shape, extent_nm, 0
        self.smallest = None
        self.topology = self

    def addSolvent(self, ff, **kwargs):
        import math

        unit = pytest.importorskip("openmm").unit

        padding = kwargs["padding"].value_in_unit(unit.nanometer)
        if self.shape == "cube":
            self.smallest = self.extent + 2.0 * padding
        else:
            self.smallest = math.sqrt(2.0) * padding
        self.solvations += 1

    def deleteWater(self):
        pass

    def getPeriodicBoxVectors(self):
        openmm = pytest.importorskip("openmm")
        Vec3, unit = openmm.Vec3, openmm.unit

        s = self.smallest
        return [Vec3(s, 0, 0), Vec3(0, s, 0), Vec3(0, 0, s)] * unit.nanometer


def _solvate(box, *, cutoff_nm, padding_nm, caplog):
    import logging

    unit = pytest.importorskip("openmm").unit

    from fastmdxplora.setup.prepare import _solvate_with_room_for_the_cutoff

    with caplog.at_level(logging.INFO, logger="fastmdx"):
        _solvate_with_room_for_the_cutoff(
            box, None, {"padding": padding_nm * unit.nanometer,
                        "boxShape": box.shape},
            nonbonded_cutoff_nm=cutoff_nm, padding_nm=padding_nm,
            nonbonded_method="PME", unit=unit)


def _two_antiparallel_helices():
    """Two copies of one chain, one embedded upside down: each a long axis
    along the membrane normal with a bulky head at one end, as a receptor
    has its soluble domain, so its direction along the normal is defined;
    the second is the first mirrored in z. Over 100 atoms each, which is
    the size below which the check does not judge a chain."""
    import numpy as np
    pytest.importorskip("openmm.app")
    from openmm import app, unit

    rng = np.random.default_rng(0)
    topology = app.Topology()
    positions = []
    for chain_index, flip in enumerate((1.0, -1.0)):
        chain = topology.addChain()
        for i in range(60):
            residue = topology.addResidue("ALA", chain)
            z = i * 0.15 - 4.5
            # A head: the last twelve residues fan out wide, so the mass
            # sits at one end and the axis has a direction.
            spread = 0.23 if i < 48 else 1.2
            for name in ("N", "CA", "C"):
                topology.addAtom(name, app.element.carbon, residue)
                x = spread * np.cos(i * 1.7) + chain_index * 4.0 + rng.normal(0, 0.02)
                y = spread * np.sin(i * 1.7) + rng.normal(0, 0.02)
                positions.append((x, y, flip * z))
    return topology, positions * unit.nanometer


@pytest.fixture(scope="module")
def a_watched_run(tmp_path_factory):
    """A short real run with live telemetry on, its samples and its progress
    reports recorded as the stages made them."""
    from types import SimpleNamespace

    pytest.importorskip("openmm")
    from fastmdxplora.simulation import runner
    from tests._the_phase import a_prepared_water_box

    root = tmp_path_factory.mktemp("watched")
    metrics: list = []
    progress: list = []
    real_append = runner._append_live_metric

    def recording_append(omm, simulation, telemetry, **kwargs):
        metrics.append({"stage": kwargs.get("stage"), "frame_count": kwargs.get("frame_count"),
                        "step": kwargs.get("step")})
        return real_append(omm, simulation, telemetry, **kwargs)

    def on_bar(label, done, total, rate, left):
        progress.append({"stage": label, "done": done, "total": total, "rate": rate, "left": left})

    production_steps, equilibration_steps, frame_interval = 100, 40, 10
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "_append_live_metric", recording_append)
        result = runner.run_simulation(
            **a_prepared_water_box(root), output_dir=str(root / "out"),
            production_steps=production_steps, nvt_steps=equilibration_steps, npt_steps=0,
            minimize=False, platform="CPU", live_telemetry=True, telemetry_interval=10,
            trajectory_interval_steps=frame_interval, on_step_progress=on_bar)
    status = json.loads((root / "out" / "live_status.json").read_text(encoding="utf-8"))
    return SimpleNamespace(metrics=metrics, progress=progress, result=result, out=root / "out",
                           production_steps=production_steps,
                           equilibration_steps=equilibration_steps, frame_interval=frame_interval,
                           planned_frames=status.get("planned_frame_count"))
