"""CLI smoke tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fastmdxplora.dependencies import missing_dependencies
from fastmdxplora.orchestrator import FastMDXplora, PhaseResult
from fastmdxplora.cli.main import main


def _chemistry_exit_code() -> int:
    """Exit code a chemistry workflow should return in this environment.

    CI runs without OpenMM and PDBFixer, where a run that reaches the setup
    or simulation phase is expected to stop cleanly with a non-zero status.
    On a full install the same command must succeed. Asserting against the
    environment keeps both paths honest; hard-coding the CI result would mean
    the suite never verifies that the pipeline actually works.
    """
    return 1 if missing_dependencies() else 0


def _make_pdb_stub(tmp_path: Path) -> Path:
    p = tmp_path / "stub.pdb"
    p.write_text(# A tripeptide rather than a lone residue: one amino acid is
        # simultaneously N- and C-terminal, which AMBER has no template for.
        # These fixtures test pipeline mechanics, not force-field edge cases.
                "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
                "ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C\n"
        "ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00           C\n"
        "ATOM      4  O   ALA A   1       1.251   2.390   0.000  1.00  0.00           O\n"
        "ATOM      5  CB  ALA A   1       1.988  -0.773  -1.199  1.00  0.00           C\n"
        "ATOM      6  N   GLY A   2       3.332   1.549   0.000  1.00  0.00           N\n"
        "ATOM      7  CA  GLY A   2       3.972   2.849   0.000  1.00  0.00           C\n"
        "ATOM      8  C   GLY A   2       5.486   2.705   0.000  1.00  0.00           C\n"
        "ATOM      9  O   GLY A   2       6.008   1.593   0.000  1.00  0.00           O\n"
        "ATOM     10  N   ALA A   3       6.171   3.845   0.000  1.00  0.00           N\n"
        "ATOM     11  CA  ALA A   3       7.623   3.845   0.000  1.00  0.00           C\n"
        "ATOM     12  C   ALA A   3       8.174   5.265   0.000  1.00  0.00           C\n"
        "ATOM     13  O   ALA A   3       7.416   6.235   0.000  1.00  0.00           O\n"
        "ATOM     14  CB  ALA A   3       8.153   3.072  -1.199  1.00  0.00           C\n"
        "ATOM     15  OXT ALA A   3       9.400   5.400   0.000  1.00  0.00           O\n"
        "END\n")
    return p


def test_cli_version_subprocess() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fastmdxplora.cli.main", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0
    assert "fastmdx" in result.stdout
    assert "FastMDXplora" in result.stdout


def test_cli_help_subprocess() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fastmdxplora.cli.main", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0
    assert "FastMDXplora" in result.stdout
    assert "explore" in result.stdout
    assert "xplore" in result.stdout
    assert "setup" in result.stdout
    assert "simulate" in result.stdout
    assert "analyze" in result.stdout
    assert "report" in result.stdout


def test_cli_analyze_accepts_explicit_ligand_resname() -> None:
    from fastmdxplora.cli.main import _build_parser

    args = _build_parser().parse_args([
        "analyze", "--system", "3PTB", "--ligand-resname", "BEN",
    ])
    assert args.ligand_resname == "BEN"


def test_cli_no_args_starts_dashboard_home() -> None:
    cli_main_module = sys.modules["fastmdxplora.cli.main"]
    with patch.object(cli_main_module, "_cmd_dashboard_home", return_value=0) as home:
        rc = main([])
    assert rc == 0
    home.assert_called_once_with()


def test_cli_cite() -> None:
    rc = main(["--cite"])
    assert rc == 0


def test_cli_info() -> None:
    rc = main(["info"])
    assert rc == 0


def test_repo_root_launcher_runs_without_installation() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(repo_root / "fastmdx"), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=repo_root,
    )
    assert result.returncode == 0
    assert "FastMDXplora" in result.stdout


def test_cli_explore(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    out = tmp_path / "run"
    rc = main(["explore", "-system", str(pdb), "--output", str(out), "--simulate-nvt-steps", "2", "--simulate-npt-steps", "2", "--simulate-production-steps", "4", "--simulate-trajectory-interval-steps", "1"])
    assert rc == _chemistry_exit_code()
    assert (out / "manifest.json").exists()
    assert (out / "setup" / "setup_parameters.json").exists()
    assert (out / "simulation" / "simulation_parameters.json").exists()


def test_cli_xplore_is_alias(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    out = tmp_path / "run"
    rc = main(["xplore", "-system", str(pdb), "--output", str(out), "--simulate-nvt-steps", "2", "--simulate-npt-steps", "2", "--simulate-production-steps", "4", "--simulate-trajectory-interval-steps", "1"])
    assert rc == _chemistry_exit_code()


# Fetches 1L2Y from RCSB, so it needs the network. It was unmarked and
# passing, because without PDBFixer installed it exited before the fetch
# and the assertion held for the wrong reason. Installing the backend
# unmasked it. An accidental skip is the worst kind: the test is green and
# is not testing what it says.
@pytest.mark.network
def test_cli_explore_with_pdb_id(tmp_path: Path) -> None:
    out = tmp_path / "run"

    # This test verifies that a 4-char PDB ID is fetched and routed through
    # setup. Simulation is mocked, so the exit code depends only on whether
    # the chemistry stack is present for the setup phase.
    def _fake_run_simulation(*, topology_pdb, output_dir, **kwargs):
        import mdtraj as md
        from fastmdxplora.simulation.runner import SimulationResult

        traj = md.load(str(topology_pdb))
        # Stack a few identical frames so analyses that need >1 frame work.
        multi = md.join([traj] * 4)
        traj_path = output_dir / "production.dcd"
        multi.save_dcd(str(traj_path))

        # The real run_simulation writes a topology copy into the simulation
        # output dir; the analysis phase loads it from there.
        sim_topology = output_dir / "topology.pdb"
        traj[0].save_pdb(str(sim_topology))

        final_state = output_dir / "final_state.xml"
        final_state.write_text("<State/>\n", encoding="utf-8")
        energy_csv = output_dir / "energy.csv"
        energy_csv.write_text("step,potential\n0,0.0\n", encoding="utf-8")
        log_file = output_dir / "simulation.log"
        log_file.write_text("mock simulation\n", encoding="utf-8")

        return SimulationResult(
            trajectory=traj_path,
            topology=sim_topology,
            final_state=final_state,
            energy_csv=energy_csv,
            log_file=log_file,
            platform_used="CPU",
            n_production_frames=multi.n_frames,
            duration_ns_actual=0.0,
        )

    with patch(
        "fastmdxplora.simulation.runner.run_simulation",
        side_effect=_fake_run_simulation,
    ):
        rc = main(["explore", "--system", "1L2Y", "--output", str(out)])
    assert rc == _chemistry_exit_code()
    assert (out / "setup" / "setup_parameters.json").exists()


def test_cli_explore_requires_input(tmp_path: Path) -> None:
    # No -s/--system and no --config: explore reports the error and returns
    # exit code 2 (rather than raising), consistent with other CLI errors.
    rc = main(["explore", "--output", str(tmp_path / "run")])
    assert rc == 2


def test_cli_per_phase_setup(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    out = tmp_path / "run"
    rc = main(["setup", "-system", str(pdb), "--output", str(out)])
    assert rc == 0
    assert (out / "setup" / "setup_parameters.json").exists()


def test_cli_report_can_rerun_from_existing_output_without_system(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    out = tmp_path / "run"
    out.mkdir()
    (out / "manifest.json").write_text(
        json.dumps({"system": str(pdb)}),
        encoding="utf-8",
    )

    rc = main(["report", "--output", str(out), "--no-slides", "--no-bundle"])

    assert rc == 0
    assert (out / "report" / "report.md").exists()


def test_cli_single_phase_manifest_preserves_and_records_phase(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    out = tmp_path / "run"
    out.mkdir()
    (out / "manifest.json").write_text(json.dumps({
        "system": str(pdb),
        "phases": [{"name": "simulation", "status": "ok"}],
    }), encoding="utf-8")
    result = PhaseResult(name="analysis", status="ok")

    with patch("fastmdxplora.orchestrator.FastMDXplora._run_phase", return_value=result):
        rc = main(["analyze", "-system", str(pdb), "--output", str(out)])

    assert rc == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert [phase["name"] for phase in manifest["phases"]] == [
        "simulation", "analysis"
    ]


def test_cli_explore_no_report(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    out = tmp_path / "run"
    rc = main(["explore", "-system", str(pdb), "--output", str(out), "--no-report", "--simulate-nvt-steps", "2", "--simulate-npt-steps", "2", "--simulate-production-steps", "4", "--simulate-trajectory-interval-steps", "1"])
    assert rc == _chemistry_exit_code()
    assert not (out / "report").exists() or not any((out / "report").iterdir())


def test_cli_explore_include_and_exclude_mutually_exclusive(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    rc = main(
        [
            "explore",
            "-system",
            str(pdb),
            "--output",
            str(tmp_path / "run"),
            "--include",
            "setup",
            "--exclude",
            "report",
        ]
    )
    assert rc == 2


def test_cli_dashboard_flags_parse_on_workflow_commands() -> None:
    from fastmdxplora.cli.main import _build_parser

    parser = _build_parser()
    parser.parse_args(["explore", "--system", "1L2Y", "--dashboard"])
    parser.parse_args(["explore", "--system", "1L2Y", "--live-dashboard"])
    parser.parse_args(
        [
            "explore",
            "--system",
            "1L2Y",
            "--dashboard",
            "--dashboard-host",
            "127.0.0.1",
            "--dashboard-port",
            "8877",
            "--dashboard-stop-on-complete",
        ]
    )
    for command in ("setup", "simulate", "analyze", "report"):
        parser.parse_args([command, "--system", "1L2Y", "--dashboard"])


class _FakeDashboardSession:
    def __init__(
        self,
        *,
        url: str = "http://127.0.0.1:8765",
        requested_port: int = 8765,
        port: int = 8765,
    ) -> None:
        self.url = url
        self.requested_port = requested_port
        self.port = port
        self.port_was_changed = requested_port != port
        self.stopped = False
        self.waited = False

    def stop(self) -> None:
        self.stopped = True

    def wait_forever(self) -> None:
        self.waited = True


def test_cli_dashboard_starts_server_and_prints_url(tmp_path: Path, capsys) -> None:
    pdb = _make_pdb_stub(tmp_path)
    out = tmp_path / "run"
    out.mkdir()
    (out / "manifest.json").write_text(json.dumps({"system": str(pdb)}), encoding="utf-8")
    session = _FakeDashboardSession()

    with patch(
        "fastmdxplora.gui.server.start_dashboard_session",
        return_value=session,
    ) as start:
        rc = main(
            [
                "report",
                "--output",
                str(out),
                "--no-slides",
                "--no-bundle",
                "--dashboard",
                "--dashboard-stop-on-complete",
            ]
        )

    assert rc == 0
    start.assert_called_once()
    assert start.call_args.kwargs["output"] == out
    assert start.call_args.kwargs["host"] == "127.0.0.1"
    assert start.call_args.kwargs["port"] == 8765
    assert session.stopped is True
    text = capsys.readouterr().out
    assert "FastMDXplora GUI running at: http://127.0.0.1:8765" in text
    assert f"Watching output folder: {out}" in text
    assert "Open this URL in your browser to monitor the run." in text


def test_cli_explore_dashboard_does_not_read_missing_output_dir(
    tmp_path: Path,
) -> None:
    pdb = _make_pdb_stub(tmp_path)
    out = tmp_path / "run"
    session = _FakeDashboardSession()

    def fake_explore(self, *, dry_run=False, force=False):
        self.output_dir = out
        return [SimpleNamespace(status="ok")]

    with patch(
        "fastmdxplora.gui.server.start_dashboard_session",
        return_value=session,
    ) as start, patch.object(FastMDXplora, "explore", fake_explore):
        rc = main(
            [
                "explore",
                "--system",
                str(pdb),
                "--output",
                str(out),
                "--include",
                "setup",
                "simulation",
                "analysis",
                "report",
                "--dashboard",
                "--dashboard-stop-on-complete",
            ]
        )

    assert rc == 0
    assert start.call_args.kwargs["output"] == out.resolve()
    assert session.stopped is True


def test_cli_phase_dashboard_uses_cli_output_path(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    out = tmp_path / "phase_run"
    session = _FakeDashboardSession()

    with patch(
        "fastmdxplora.gui.server.start_dashboard_session",
        return_value=session,
    ) as start, patch.object(
        FastMDXplora,
        "setup",
        return_value=PhaseResult(name="setup", status="ok"),
    ):
        rc = main(
            [
                "setup",
                "--system",
                str(pdb),
                "--output",
                str(out),
                "--dashboard",
                "--dashboard-stop-on-complete",
            ]
        )

    assert rc == 0
    assert start.call_args.kwargs["output"] == out.resolve()
    assert session.stopped is True


@pytest.mark.parametrize("command", ["simulate", "analyze", "report"])
def test_cli_dashboard_uses_cli_output_path_for_other_phases(
    tmp_path: Path,
    command: str,
) -> None:
    pdb = _make_pdb_stub(tmp_path)
    out = tmp_path / f"{command}_run"
    session = _FakeDashboardSession()

    method_name = {"simulate": "simulate", "analyze": "analyze", "report": "report"}[command]
    with patch(
        "fastmdxplora.gui.server.start_dashboard_session",
        return_value=session,
    ) as start, patch.object(
        FastMDXplora,
        method_name,
        return_value=PhaseResult(name=command, status="ok"),
    ):
        rc = main(
            [
                command,
                "--system",
                str(pdb),
                "--output",
                str(out),
                "--dashboard",
                "--dashboard-stop-on-complete",
            ]
        )

    assert rc == 0
    assert start.call_args.kwargs["output"] == out.resolve()
    assert session.stopped is True


def test_cli_dashboard_prints_port_conflict_and_host_warning(
    tmp_path: Path,
    capsys,
) -> None:
    pdb = _make_pdb_stub(tmp_path)
    out = tmp_path / "run"
    out.mkdir()
    (out / "manifest.json").write_text(json.dumps({"system": str(pdb)}), encoding="utf-8")
    session = _FakeDashboardSession(
        url="http://0.0.0.0:8766",
        requested_port=8765,
        port=8766,
    )

    with patch(
        "fastmdxplora.gui.server.start_dashboard_session",
        return_value=session,
    ):
        rc = main(
            [
                "report",
                "--output",
                str(out),
                "--no-slides",
                "--no-bundle",
                "--dashboard",
                "--dashboard-host",
                "0.0.0.0",
                "--dashboard-port",
                "8765",
                "--dashboard-stop-on-complete",
            ]
        )

    assert rc == 0
    text = capsys.readouterr().out
    assert "FastMDXplora GUI running at: http://0.0.0.0:8766" in text
    assert "Requested port 8765 was busy, so FastMDXplora used 8766." in text
    assert "Warning: dashboard is bound to 0.0.0.0" in text


def test_cli_dashboard_implies_live_telemetry_for_simulation(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    out = tmp_path / "run"
    session = _FakeDashboardSession()

    with patch(
        "fastmdxplora.gui.server.start_dashboard_session",
        return_value=session,
    ), patch.object(
        FastMDXplora,
        "simulate",
        return_value=PhaseResult(name="simulation", status="ok"),
    ) as simulate:
        rc = main(
            [
                "simulate",
                "--system",
                str(pdb),
                "--output",
                str(out),
                "--dashboard",
                "--dashboard-stop-on-complete",
            ]
        )

    assert rc == 0
    assert simulate.call_args.kwargs["live_telemetry"] is True
    assert session.stopped is True
