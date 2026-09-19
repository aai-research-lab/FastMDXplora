"""Tests for the local live dashboard.

These tests assert:

  - endpoint contracts (URLs, response keys) the frontend binds to,
  - failure isolation (traversal, missing files, empty run),
  - the redesigned HTML/CSS/JS assets exist on disk and contain the
    expected AAI-branded module structure.
"""

from __future__ import annotations

import re
import json
import socket
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from types import SimpleNamespace

import pytest

from fastmdxplora.cli.main import _build_parser, _enable_dashboard_telemetry
from fastmdxplora.gui import protein_preview
from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.gui.ligand_detection import detect_ligands, normalise_ligand_resname
from fastmdxplora.gui.live_frames import (
    dashboard_display_pdb,
    read_live_frame_history,
    write_live_frame,
)
from fastmdxplora.gui.protein_preview import protein_preview_payload
from fastmdxplora.gui.server import (
    DashboardConfig,
    make_handler,
    start_dashboard_session,
    start_test_server,
)
from fastmdxplora.gui.structure_info import count_structure, ligand_atom_counts
from fastmdxplora.simulation.runner import _maybe_write_live_frame
from fastmdxplora.gui.telemetry import (
    TelemetryWriter,
    analyze_health,
    read_events,
    read_metrics,
    read_status,
)
from fastmdxplora.gui.trajectory_playback import (
    neighborhood_residues,
    playback_info,
)


# ---------------------------------------------------------------------------
# Fixture: tiny four-atom helper used by several tests
# ---------------------------------------------------------------------------
#: How long a request to the test server may take before the test calls it a
#: failure. Five seconds was enough on an idle machine and a coin flip on a
#: busy one: these tests run a real HTTP server in a thread, and a laptop also
#: running an MD simulation pushed a fourteen-route loop past the ceiling. A
#: generous limit costs nothing when the server answers promptly, and stops
#: the suite reporting "the machine was busy" as a defect.
HTTP_TIMEOUT = 30


def _tiny_pdb() -> str:
    return "\n".join(
        [
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 10.00           C",
            "ATOM      2  CA  GLY A   2       1.500   0.400   0.300  1.00 10.00           C",
            "ATOM      3  CA  SER A   3       2.800   1.200   0.000  1.00 10.00           C",
            "ATOM      4  CA  LEU A   4       4.100   1.400   0.900  1.00 10.00           C",
            "END",
        ]
    )


# ---------------------------------------------------------------------------
# Telemetry basics (unchanged behavior)
# ---------------------------------------------------------------------------
def test_telemetry_writer_creates_status_metrics_and_events(tmp_path: Path) -> None:
    simulation_dir = tmp_path / "run" / "simulation"
    writer = TelemetryWriter(simulation_dir, total_steps=100, planned_frames=10, timestep_fs=2.0, platform="CPU", target_temperature_K=300.0)
    writer.write_status(stage="production", status="running", current_step=25)
    writer.append_metric(stage="production", step=25, simulation_time_ns=0.00005, potential_energy=-123.4, total_energy=-100.0, temperature=299.0, progress_percent=25.0)
    writer.event("frame written")

    status = read_status(tmp_path / "run")
    metrics = read_metrics(tmp_path / "run")
    events = read_events(tmp_path / "run")

    assert status["stage"] == "production"
    assert status["current_step"] == 25
    assert metrics[-1]["potential_energy"] == "-123.4"
    assert events[-1]["message"] == "frame written"


def test_telemetry_writer_file_errors_do_not_raise(tmp_path: Path) -> None:
    blocked_path = tmp_path / "not_a_directory"
    blocked_path.write_text("occupied", encoding="utf-8")
    writer = TelemetryWriter(blocked_path)
    writer.write_status(stage="production")
    writer.append_metric(stage="production", step=1)
    writer.event("event")


def test_health_detects_nan_and_energy_spike() -> None:
    status = {"status": "running", "last_update_timestamp": "2999-01-01T00:00:00+00:00"}
    nan_health = analyze_health(status, [{"stage": "production", "total_energy": "nan"}])
    assert nan_health["state"] == "failed"
    assert "numerically unstable" in nan_health["explanation"]

    spike_health = analyze_health(status, [{"stage": "production", "total_energy": "-100.0"}, {"stage": "production", "total_energy": "50000.0"}])
    assert spike_health["state"] == "warning"
    assert "Energy increased sharply" in spike_health["explanation"]


def test_telemetry_readers_handle_missing_and_malformed_files(tmp_path: Path) -> None:
    run = tmp_path / "run"
    sim = run / "simulation"
    sim.mkdir(parents=True)

    assert read_status(run) == {}
    assert read_metrics(run) == []
    assert read_events(run) == []

    (sim / "live_status.json").write_text("{not-json", encoding="utf-8")
    (sim / "live_metrics.csv").write_text('timestamp,stage,total_energy\n"unterminated', encoding="utf-8")
    (sim / "live_events.log").write_text("free-form event without tabs\n", encoding="utf-8")

    assert read_status(run) == {}
    assert isinstance(read_metrics(run), list)
    assert read_events(run) == [{"timestamp": "", "level": "info", "message": "free-form event without tabs"}]


def test_analyze_health_temperature_and_stale_warning() -> None:
    stale = analyze_health({"status": "running", "last_update_timestamp": "2000-01-01T00:00:00+00:00"}, [], stale_after_seconds=1)
    assert stale["state"] == "warning"
    assert "stale" in stale["message"].lower()

    hot = analyze_health({"status": "running", "target_temperature_K": 300.0}, [{"temperature": "420.0"}])
    assert hot["state"] == "warning"
    assert "Temperature" in hot["message"]


# ---------------------------------------------------------------------------
# Protein preview behaviour (unchanged endpoint contract)
# ---------------------------------------------------------------------------
def test_protein_preview_unavailable_without_structure(tmp_path: Path) -> None:
    payload = protein_preview_payload(tmp_path / "run")
    assert payload["available"] is False
    assert payload["message"] == "No topology/PDB found yet."


def test_protein_preview_uses_existing_image_without_structure(tmp_path: Path) -> None:
    run = tmp_path / "run"
    preview = run / "report" / "dashboard_assets" / "protein_preview.png"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"\x89PNG\r\n\x1a\n")

    payload = protein_preview_payload(run)
    assert payload["available"] is True
    assert payload["static_available"] is True
    assert payload["static_mode"] == "existing"
    assert payload["path"] == "report/dashboard_assets/protein_preview.png"


def test_structure_route_serves_topology(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(protein_preview, "_find_pymol_executable", lambda _root: None)
    run = tmp_path / "run"
    topology = run / "simulation" / "topology.pdb"
    topology.parent.mkdir(parents=True)
    topology.write_text(_tiny_pdb(), encoding="utf-8")

    server, base_url = start_test_server(run)
    try:
        response = urlopen(f"{base_url}/structure/topology.pdb", timeout=HTTP_TIMEOUT)
        body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()

    assert response.headers["Cache-Control"] == "no-store"
    assert "ATOM" in body and "ALA" in body


def test_pymol_script_uses_padded_uncropped_camera(tmp_path: Path, monkeypatch) -> None:
    structure = tmp_path / "topology.pdb"
    output = tmp_path / "protein_preview.png"
    structure.write_text(_tiny_pdb(), encoding="utf-8")
    captured: dict[str, str] = {}

    def _fake_run(command, **_kwargs):
        script = Path(command[-1])
        captured["script"] = script.read_text(encoding="utf-8")
        output.write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setattr(protein_preview.subprocess, "run", _fake_run)
    protein_preview._render_with_pymol("/fake/pymol", structure, output)

    script = captured["script"]
    assert "viewport 1800, 1400" in script
    assert "show cartoon, prot" in script
    assert "spectrum count, rainbow, prot, byres=1" in script
    assert "set_color fastmdx_res_1" in script
    assert "color fastmdx_res_1, prot and resi 1 and chain A" in script
    assert "set ray_opaque_background, off" in script
    assert "set cartoon_fancy_helices, 1" in script
    assert "set cartoon_smooth_loops, 1" in script
    assert "set ray_trace_mode, 1" in script
    assert "set cartoon_tube_radius, 0.45" in script
    assert "set cartoon_sampling, 14" in script
    assert "orient prot" in script
    assert "center prot" in script
    assert "zoom prot, 1.8" in script
    assert "ray 1800, 1200" in script
    assert "width=1800" in script
    assert "height=1200" in script
    assert "dpi=300" in script


def test_gui_command_parses() -> None:
    args = _build_parser().parse_args(
        ["gui", "--output", "local_runs/my_run", "--port", "8766"]
    )
    assert args.command == "gui"
    assert args.output == "local_runs/my_run"
    assert args.port == 8766


# ---------------------------------------------------------------------------
# New dashboards: structure_info, ligand_detection, live_frames, trajectory_playback
# ---------------------------------------------------------------------------
def test_count_structure_basic_protein(tmp_path: Path) -> None:
    (tmp_path / "topology.pdb").write_text(_tiny_pdb(), encoding="utf-8")
    info = count_structure(tmp_path / "topology.pdb")
    assert info["valid"] is True
    assert info["n_chains"] == 1
    assert info["protein_residues"] == 4
    assert info["water_residues"] == 0
    assert info["ions"] == 0
    assert info["ligand_residues_total"] == 0


def test_count_structure_water_atom_record_not_double_counted(tmp_path: Path) -> None:
    # Many PDBs list waters as ATOM HOH. They must not appear in
    # protein_residues.
    pdb = "\n".join([
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N",
        "ATOM      2  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C",
        "ATOM      3  OH2 HOH B  10       5.000   5.000   5.000  1.00  0.00           O",
        "END",
    ])
    (tmp_path / "topology.pdb").write_text(pdb, encoding="utf-8")
    info = count_structure(tmp_path / "topology.pdb")
    assert info["valid"] is True
    assert info["protein_residues"] == 1
    assert info["water_residues"] == 1


def test_count_structure_detects_ligand(tmp_path: Path) -> None:
    pdb = "\n".join([
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N",
        "ATOM      2  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C",
        "HETATM    3  C1  EPE A 100       4.000   4.000   4.000  1.00 10.00           C",
        "HETATM    4  O1  EPE A 100       4.300   4.300   4.300  1.00 10.00           O",
        "END",
    ])
    (tmp_path / "topology.pdb").write_text(pdb, encoding="utf-8")
    info = count_structure(tmp_path / "topology.pdb")
    assert "EPE" in info["ligand_resnames"]


def test_count_structure_handles_missing(tmp_path: Path) -> None:
    info = count_structure(tmp_path / "no.pdb")
    assert info["valid"] is False


def test_count_structure_rejects_too_large_files(tmp_path: Path) -> None:
    big = tmp_path / "huge.pdb"
    big.write_text("HEADER\n", encoding="utf-8")
    big.write_bytes(b"X" * (9 * 1024 * 1024))  # > 8 MB
    info = count_structure(big)
    assert info["valid"] is False
    assert info["reason"] == "too-large"


def test_detect_ligands_basic(tmp_path: Path) -> None:
    pdb = "\n".join([
        "HETATM    1  C1  EPE A 100       4.000   4.000   4.000  1.00 10.00           C",
        "HETATM    2  C2  BEN B   1       5.000   5.000   5.000  1.00 10.00           C",
        "ATOM      3  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N",
        "END",
    ])
    (tmp_path / "topology.pdb").write_text(pdb, encoding="utf-8")
    info = count_structure(tmp_path / "topology.pdb")
    keys = [(ins["chain"], ins["resname"], ins["resi"]) for ins in info["ligand_instances"]]
    detected = detect_ligands(keys)
    assert set(detected["resnames"]) >= {"EPE"}


def test_detect_ligands_respects_explicit_override() -> None:
    keys = [("A", "NA", "1")]
    out = detect_ligands(keys, explicit=["NA"])
    assert out["resnames"] == ["NA"]
    assert out["instances"][0]["explicit"] is True


def test_detect_ligands_excludes_water_and_ions() -> None:
    keys = [("A", "HOH", "1"), ("A", "NA", "2"), ("A", "EPE", "3")]
    out = detect_ligands(keys)
    assert sorted(out["resnames"]) == ["EPE"]


def test_detect_ligands_includes_cofactors_when_requested() -> None:
    keys = [("A", "GOL", "1"), ("A", "EPE", "2")]
    default = detect_ligands(keys)
    with_cof = detect_ligands(keys, include_cofactors=True)
    assert "GOL" in with_cof["cofactors"]
    assert "GOL" not in default["resnames"]
    assert "GOL" in with_cof["resnames"]


def test_detect_ligands_lexicographic_sort(tmp_path: Path) -> None:
    # Single chain so the (chain, resname, resi) sort key collapses to
    # numeric resi ordering only.
    keys = [("A", "EPE", str(r)) for r in [10, 2, 100]]
    detected = detect_ligands(keys)
    assert [ins["resi"] for ins in detected["instances"]] == ["2", "10", "100"]


def test_ligand_atom_counts_distinguishes_ligands(tmp_path: Path) -> None:
    pdb = "\n".join([
        "HETATM    1  C1  EPE A 100       4.000   4.000   4.000  1.00 10.00           C",
        "HETATM    2  C2  EPE A 100       4.300   4.300   4.300  1.00 10.00           C",
        "HETATM    3  C1  BEN B   1       5.000   5.000   5.000  1.00 10.00           C",
        "HETATM    4  H1  HOH A   2       5.500   5.500   5.500  1.00  0.00           H",
        "END",
    ])
    (tmp_path / "topology.pdb").write_text(pdb, encoding="utf-8")
    counts = ligand_atom_counts(tmp_path / "topology.pdb")
    assert counts == {"EPE": 2, "BEN": 1}


def test_normalise_ligand_resname() -> None:
    assert normalise_ligand_resname("epe") == "EPE"
    assert normalise_ligand_resname("LIG ") == "LIG"
    assert normalise_ligand_resname("") is None
    assert normalise_ligand_resname(None) is None


def test_write_live_frame_round_trip(tmp_path: Path) -> None:
    sim = tmp_path / "simulation"
    result = write_live_frame(sim, pdb_text=_tiny_pdb(), frame_index=42)
    assert result["ok"] is True
    assert (sim / "live_frame.pdb").is_file()
    index = json.loads((sim / "live_frame_index.json").read_text(encoding="utf-8"))
    assert index["live_frame_available"] is True
    assert index["live_frame_index"] == 42


def test_write_live_frame_handles_missing_directory_safely(tmp_path: Path) -> None:
    # write_live_frame must not crash when the target "directory" is a
    # regular file. Putting a regular file at the target slot makes
    # Path.mkdir(parents=True, exist_ok=True) fail both on POSIX and
    # Windows. Cross-platform safe; the original /nonexistent/... path
    # was environment-dependent on Windows because C:\ is normally
    # writable so the parent.mkdir succeeded.
    blocked = tmp_path / "i_am_a_file"
    blocked.write_text("not a directory", encoding="utf-8")
    result = write_live_frame(blocked, pdb_text="ATOM\n", frame_index=1)
    assert result["ok"] is False


def test_playback_returns_unavailable_when_missing(tmp_path: Path) -> None:
    info = playback_info(tmp_path)
    assert info["playback_available"] is False


def test_playback_generation_failure_is_safe(tmp_path: Path, monkeypatch) -> None:
    # Force a failure inside the writer.
    import fastmdxplora.gui.trajectory_playback as mod
    monkeypatch.setattr(mod, "_import_mdtraj", lambda: None)
    (tmp_path / "simulation").mkdir(parents=True)
    (tmp_path / "simulation" / "topology.pdb").write_text(_tiny_pdb(), encoding="utf-8")
    (tmp_path / "simulation" / "production.dcd").write_bytes(b"\x00\x00")
    info = playback_info(tmp_path)
    assert info["playback_available"] is False
    assert info["reason"] == "mdtraj-not-installed"


def test_neighborhood_residues_per_atom_check(tmp_path: Path) -> None:
    # Atom-wide coordinates in standard PDB column widths so the parser
    # finds the values in the right slots (cols 31-38, 39-46, 47-54).
    pdb = "\n".join([
        "HETATM    1  C1  EPE A 100       4.000   4.000   4.000  1.00 10.00           C",
        "ATOM      2  N   ALA A   1       4.500   4.500   4.500  1.00  0.00           N",  # near
        "ATOM      3  N   ALA A   2      30.000  30.000  30.000  1.00  0.00           N",  # far
        "END",
    ])
    (tmp_path / "topology.pdb").write_text(pdb, encoding="utf-8")
    residues = neighborhood_residues(topology_path=tmp_path / "topology.pdb", ligand_resname="EPE", cutoff_angstrom=5.0)
    assert ("A", 1) in residues
    assert ("A", 2) not in residues



def test_telemetry_stage_states_survive_multiple_writers(tmp_path: Path) -> None:
    sim = tmp_path / "run" / "simulation"
    orchestrator_writer = TelemetryWriter(sim)
    runner_writer = TelemetryWriter(sim)
    orchestrator_writer.write_status(
        stage="setup",
        status="running",
        stage_states={
            "setup": "current", "minimization": "waiting", "nvt": "waiting",
            "npt": "waiting", "production": "waiting", "analysis": "waiting",
            "report": "waiting",
        },
    )
    runner_writer.mark_stage("minimization", "current", status="running")
    runner_writer.mark_stage("minimization", "completed", status="running")
    orchestrator_writer.mark_stage("analysis", "current", status="running")
    status = read_status(tmp_path / "run")
    assert status["stage_states"]["setup"] == "current"
    assert status["stage_states"]["minimization"] == "completed"
    assert status["stage_states"]["analysis"] == "current"


def test_live_frame_history_builds_playback(tmp_path: Path) -> None:
    sim = tmp_path / "simulation"
    write_live_frame(
        sim, pdb_text=_tiny_pdb(), frame_index=10, stage="nvt",
        simulation_time_ns=0.001, archive=True,
    )
    moved = _tiny_pdb().replace("   0.000   0.000   0.000", "   0.200   0.000   0.000")
    write_live_frame(
        sim, pdb_text=moved, frame_index=20, stage="nvt",
        simulation_time_ns=0.002, archive=True,
    )
    history = read_live_frame_history(sim)
    assert history["count"] == 2
    info = playback_info(tmp_path, max_browser_frames=20)
    assert info["playback_available"] is True
    assert info["source_kind"] == "live-history"
    assert info["n_frames_browser"] == 2
    text = (sim / "playback.pdb").read_text(encoding="utf-8")
    assert text.count("MODEL") == 2


def test_dashboard_display_pdb_strips_solvent_and_keeps_ligand() -> None:
    pdb = "\n".join([
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 10.00           C",
        "HETATM    2  O   HOH B   2       2.000   2.000   2.000  1.00 10.00           O",
        "HETATM    3  NA   NA C   3       3.000   3.000   3.000  1.00 10.00          NA",
        "HETATM    4  C1  LIG D   4       4.000   4.000   4.000  1.00 10.00           C",
        "END",
    ])
    display = dashboard_display_pdb(pdb)
    assert "ALA" in display
    assert "LIG" in display
    assert "HOH" not in display
    assert " NA C" not in display

# ---------------------------------------------------------------------------
# Endpoint contract: the redesigned dashboard's HTML/JS layer
# ---------------------------------------------------------------------------
def test_dashboard_html_uses_separated_template() -> None:
    """The HTML shell lives on disk next to the module rather than as a giant f-string."""
    Path(make_handler.__module__.split(".")[0])
    import fastmdxplora.gui as gui_pkg
    layout = Path(gui_pkg.__file__).with_name("templates") / "dashboard.html"
    assert layout.is_file()


def test_dashboard_html_has_aai_branding(tmp_path: Path) -> None:
    run = tmp_path / "run"
    writer = TelemetryWriter(run / "simulation")
    writer.write_status(stage="NVT")
    server, base_url = start_test_server(run)
    try:
        html = urlopen(f"{base_url}/", timeout=HTTP_TIMEOUT).read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()

    # Branding hierarchy
    assert "FastMDXplora" in html
    assert "Fully Automated SysTem for Molecular Dynamics eXploration" in html

    # Sidebar / study block / nav structure. The top bar became the
    # sidebar's study block: the run's name, status, step count and ETA
    # belong with the study, and the bar's width was space the page could
    # use. Every id the JS writes to is still there, once each.
    assert "sidebar" in html
    assert "sidebar-study" in html
    assert "data-view-link" in html
    for run_id in ("topbar-run-title", "topbar-status-dot", "topbar-step",
                   "topbar-eta", "pause-toggle", "refresh-now", "open-output"):
        assert html.count(f'id="{run_id}"') == 1, run_id

    # Asset wiring
    assert "/static/dashboard.css" in html
    assert "/static/dashboard.js" in html
    assert "/static/charts.js" in html
    assert "/static/molecule-viewer.js" in html
    assert "/static/3Dmol-min.js" in html

    # Pages
    # "live" was a page of its own; its panels are on the overview now.
    for page in ("overview", "viewer", "analysis", "files", "settings", "cite"):
        assert f'data-page="{page}"' in html

    # Loading screen + branded particles
    assert "loading-screen" in html

    # No CDN references
    assert "cdn" not in html.lower()
    assert "googleapis" not in html.lower()
    # The documentation, GitHub and DOI links are the only legitimate external
    # refs. Listed explicitly and counted, so that anything else added later
    # has to be argued for here rather than appearing quietly -- which is the
    # point of the count, not the number itself.
    for allowed in (
        "https://fastmdxplora.readthedocs.io",
        "https://github.com/aai-research-lab/FastMDXplora",
        "https://doi.org/",
    ):
        assert allowed in html
    assert html.count("https://") == 3


def test_dashboard_css_uses_black_scientific_palette() -> None:
    import fastmdxplora.gui as gui_pkg

    static = Path(gui_pkg.__file__).with_name("static")
    # Design tokens live in theme.css (shared with the static report);
    # dashboard.css carries the layout that consumes them.
    theme = (static / "theme.css").read_text(encoding="utf-8")
    css = (static / "dashboard.css").read_text(encoding="utf-8")
    for token in ("--background-primary", "--accent-cyan", "--accent-violet",
                  "--text-primary", "Inter"):
        assert token in theme
    assert "prefers-reduced-motion" in css


def test_dashboard_endpoint_no_inline_html_css() -> None:
    """The server.py module no longer contains the giant HTML f-string.

    We assert the absence of known-fingerprinted fragments from the old
    inline template — a tautology-free check.
    """
    Path(make_handler.__module__.split(".")[0])
    import fastmdxplora.gui as gui_pkg
    src = (Path(gui_pkg.__file__).with_name("server.py")).read_text(encoding="utf-8")
    for marker in (
        'onclick="setProteinPreviewExpanded',
        'spectrum count, rainbow, prot, byres=1',
        'function setProteinPreviewExpanded(expanded)',
    ):
        assert marker not in src, f"legacy inline marker found: {marker!r}"


def test_dashboard_server_serves_new_routes(tmp_path: Path) -> None:
    run = tmp_path / "run"
    sim = run / "simulation"
    sim.mkdir(parents=True)
    (sim / "topology.pdb").write_text(_tiny_pdb(), encoding="utf-8")
    write_live_frame(sim, pdb_text=_tiny_pdb(), frame_index=10)

    server, base_url = start_test_server(run)
    try:
        for path in (
            "/api/status",
            "/api/metrics",
            "/api/events",
            "/api/artifacts",
            "/api/files",          # alias
            "/api/results",
            "/api/analyses",       # alias
            "/api/protein-preview",
            "/api/structure-info",
            "/api/ligands",
            "/api/live-frame-index",
            "/api/live-coordinates",
            "/api/playback-info",
            "/structure/topology.pdb",
            "/structure/live-frame.pdb",
        ):
            response = urlopen(f"{base_url}{path}", timeout=HTTP_TIMEOUT)
            response.read()
            assert response.headers["Cache-Control"] == "no-store", path
    finally:
        server.shutdown()
        server.server_close()


def test_structure_info_endpoint_payload_keys(tmp_path: Path) -> None:
    run = tmp_path / "run"
    sim = run / "simulation"
    sim.mkdir(parents=True)
    (sim / "topology.pdb").write_text(_tiny_pdb(), encoding="utf-8")
    server, base_url = start_test_server(run, config=DashboardConfig(ligand_resname="LIG"))
    try:
        payload = json.loads(urlopen(f"{base_url}/api/structure-info", timeout=HTTP_TIMEOUT).read())
    finally:
        server.shutdown()
        server.server_close()
    assert payload["valid"] is True
    assert payload["n_chains"] == 1
    assert payload["protein_residues"] == 4
    assert payload["explicit_ligand"] == "LIG"


def test_live_frame_endpoint_serves_pdb(tmp_path: Path) -> None:
    run = tmp_path / "run"
    sim = run / "simulation"
    sim.mkdir(parents=True)
    write_live_frame(sim, pdb_text=_tiny_pdb(), frame_index=5)
    server, base_url = start_test_server(run)
    try:
        body = urlopen(f"{base_url}/structure/live-frame.pdb", timeout=HTTP_TIMEOUT).read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
    assert "ATOM" in body and "ALA" in body


def test_live_frame_endpoint_404_when_missing(tmp_path: Path) -> None:
    run = tmp_path / "run"
    sim = run / "simulation"
    sim.mkdir(parents=True)
    server, base_url = start_test_server(run)
    try:
        try:
            urlopen(f"{base_url}/structure/live-frame.pdb", timeout=HTTP_TIMEOUT)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404 on missing live frame")
    finally:
        server.shutdown()
        server.server_close()


def test_playback_info_unavailable_when_no_trajectory(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    server, base_url = start_test_server(run)
    try:
        payload = json.loads(urlopen(f"{base_url}/api/playback-info", timeout=HTTP_TIMEOUT).read())
    finally:
        server.shutdown()
        server.server_close()
    assert payload["playback_available"] is False


def test_artifacts_response_includes_files(tmp_path: Path) -> None:
    run = tmp_path / "run"
    sim = run / "simulation"
    sim.mkdir(parents=True)
    (sim / "live_status.json").write_text("{}", encoding="utf-8")
    server, base_url = start_test_server(run)
    try:
        payload = json.loads(urlopen(f"{base_url}/api/files", timeout=HTTP_TIMEOUT).read())
    finally:
        server.shutdown()
        server.server_close()
    assert any(record["path"].endswith("live_status.json") for record in payload["artifacts"])


def test_results_response_keys_match_dashboard_bindings(tmp_path: Path) -> None:
    run = tmp_path / "run"
    sim = run / "simulation"
    sim.mkdir(parents=True)
    (sim / "live_status.json").write_text("{}", encoding="utf-8")
    server, base_url = start_test_server(run)
    try:
        payload = json.loads(urlopen(f"{base_url}/api/results", timeout=HTTP_TIMEOUT).read())
    finally:
        server.shutdown()
        server.server_close()
    for key in ("refreshed_at", "has_analysis", "has_report", "summary", "system", "phases", "plots", "key_plots", "reports", "artifacts"):
        assert key in payload


def test_dashboard_session_uses_next_port_when_busy(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    requested_port = blocker.getsockname()[1]
    session = start_dashboard_session(output=run, host="127.0.0.1", port=requested_port, max_port_tries=5)
    try:
        assert session.requested_port == requested_port
        assert session.port != requested_port
        assert session.port_was_changed is True
    finally:
        session.stop()
        blocker.close()


def test_dashboard_session_stop_is_safe(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    session = start_dashboard_session(output=run, port=0)
    assert session.thread.is_alive()
    session.stop()
    assert not session.thread.is_alive()


def test_live_server_rejects_artifact_path_traversal(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    server, base_url = start_test_server(run)
    try:
        try:
            urlopen(f"{base_url}/artifacts/../outside.txt", timeout=HTTP_TIMEOUT)
        except HTTPError as exc:
            assert exc.code in {403, 404}
        else:
            raise AssertionError("path traversal unexpectedly succeeded")
    finally:
        server.shutdown()
        server.server_close()


def test_live_server_does_not_serve_static_path_traversal(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    server, base_url = start_test_server(run)
    try:
        try:
            urlopen(f"{base_url}/static/../server.py", timeout=HTTP_TIMEOUT)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("static traversal unexpectedly succeeded")
    finally:
        server.shutdown()
        server.server_close()


def test_static_3dmol_asset_is_served_locally(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    server, base_url = start_test_server(run)
    try:
        response = urlopen(f"{base_url}/static/3Dmol-min.js", timeout=HTTP_TIMEOUT)
        body = response.read().decode("utf-8", errors="ignore")
    finally:
        server.shutdown()
        server.server_close()
    assert response.headers["Cache-Control"] == "no-store"
    assert "$3Dmol" in body
    assert "http://" not in body[:1000]
    assert "https://" not in body[:1000]


def test_live_json_endpoints_are_no_store(tmp_path: Path) -> None:
    run = tmp_path / "run"
    writer = TelemetryWriter(run / "simulation")
    writer.write_status(stage="production")
    server, base_url = start_test_server(run)
    try:
        for endpoint in (
            "status", "metrics", "events", "artifacts", "results", "files",
            "protein-preview", "structure-info", "ligands",
            "live-frame-index", "live-coordinates", "playback-info",
            "analyses",
        ):
            response = urlopen(f"{base_url}/api/{endpoint}", timeout=HTTP_TIMEOUT)
            response.read()
            assert response.headers["Cache-Control"] == "no-store", endpoint
    finally:
        server.shutdown()
        server.server_close()


def test_live_json_endpoints_are_sane_for_empty_run(tmp_path: Path) -> None:
    run = tmp_path / "empty_run"
    server, base_url = start_test_server(run)
    try:
        status_payload = json.loads(urlopen(f"{base_url}/api/status", timeout=HTTP_TIMEOUT).read())
        metrics_payload = json.loads(urlopen(f"{base_url}/api/metrics", timeout=HTTP_TIMEOUT).read())
        events_payload = json.loads(urlopen(f"{base_url}/api/events", timeout=HTTP_TIMEOUT).read())
        artifacts_payload = json.loads(urlopen(f"{base_url}/api/artifacts", timeout=HTTP_TIMEOUT).read())
        results_payload = json.loads(urlopen(f"{base_url}/api/results", timeout=HTTP_TIMEOUT).read())
        structure_payload = json.loads(urlopen(f"{base_url}/api/structure-info", timeout=HTTP_TIMEOUT).read())
        ligands_payload = json.loads(urlopen(f"{base_url}/api/ligands", timeout=HTTP_TIMEOUT).read())
    finally:
        server.shutdown()
        server.server_close()
    assert status_payload["status"] == {}
    assert status_payload["health"]["state"] == "unknown"
    assert metrics_payload["metrics"] == []
    assert events_payload["events"] == []
    assert artifacts_payload["artifacts"] == []
    assert results_payload["has_analysis"] is False
    assert results_payload["has_report"] is False
    assert results_payload["plots"] == []
    assert structure_payload["valid"] is False
    assert ligands_payload["valid"] is False


def test_static_assets_are_packaged(tmp_path: Path) -> None:
    import fastmdxplora.gui as gui_pkg
    static = Path(gui_pkg.__file__).with_name("static")
    assert (static / "dashboard.css").is_file()
    assert (static / "dashboard.js").is_file()
    assert (static / "charts.js").is_file()
    assert (static / "molecule-viewer.js").is_file()


def test_static_endpoint_serves_packaged_assets(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    server, base_url = start_test_server(run)
    try:
        response = urlopen(f"{base_url}/static/dashboard.css", timeout=HTTP_TIMEOUT)
        body = response.read().decode("utf-8", errors="ignore")
    finally:
        server.shutdown()
        server.server_close()
    assert response.headers["Content-Type"].startswith("text/css")
    assert ".sidebar" in body

# ---------------------------------------------------------------------------
# Regression coverage for the functional dashboard wiring
# ---------------------------------------------------------------------------
def test_find_structure_prefers_prepared_solute_over_solvated_topology(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    prepared = run / "setup" / "prepared.pdb"
    topology = run / "simulation" / "topology.pdb"
    solvated = run / "setup" / "solvated.pdb"
    for path, marker in (
        (prepared, "PREPARED"),
        (topology, "TOPOLOGY"),
        (solvated, "SOLVATED"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"REMARK {marker}\n{_tiny_pdb()}\n", encoding="utf-8")

    assert protein_preview.find_structure(run) == prepared


def test_read_metrics_falls_back_to_openmm_energy_csv(tmp_path: Path) -> None:
    sim = tmp_path / "run" / "simulation"
    sim.mkdir(parents=True)
    (sim / "energy.csv").write_text(
        '#"Step","Time (ps)","Potential Energy (kJ/mole)",'
        '"Kinetic Energy (kJ/mole)","Total Energy (kJ/mole)",'
        '"Temperature (K)","Box Volume (nm^3)","Density (g/mL)",'
        '"Speed (ns/day)","Progress (%)"\n'
        '100,2.0,-1000.5,200.5,-800.0,299.5,12.0,0.997,8.25,50.0\n',
        encoding="utf-8",
    )

    metrics = read_metrics(tmp_path / "run")

    assert len(metrics) == 1
    assert metrics[0]["step"] == "100"
    assert metrics[0]["simulation_time_ns"] == "0.002"
    assert metrics[0]["potential_energy"] == "-1000.5"
    assert metrics[0]["temperature"] == "299.5"
    assert metrics[0]["density"] == "0.997"
    assert metrics[0]["speed"] == "8.25"


def test_ligands_endpoint_returns_detected_instances(tmp_path: Path) -> None:
    run = tmp_path / "run"
    prepared = run / "setup" / "prepared.pdb"
    prepared.parent.mkdir(parents=True)
    prepared.write_text(
        "\n".join(
            [
                "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N",
                "HETATM    2  C1  EPE B 101       4.000   4.000   4.000  1.00 10.00           C",
                "HETATM    3  O1  EPE B 101       4.300   4.300   4.300  1.00 10.00           O",
                "END",
            ]
        ),
        encoding="utf-8",
    )

    server, base_url = start_test_server(run)
    try:
        payload = json.loads(urlopen(f"{base_url}/api/ligands", timeout=HTTP_TIMEOUT).read())
    finally:
        server.shutdown()
        server.server_close()

    assert payload["valid"] is True
    assert payload["resnames"] == ["EPE"]
    assert payload["ligands"] == [
        {"chain": "B", "resname": "EPE", "resi": "101", "explicit": False}
    ]


def test_results_endpoint_discovers_analysis_and_report_outputs(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    analysis = run / "analysis" / "rmsd"
    report = run / "report"
    analysis.mkdir(parents=True)
    report.mkdir(parents=True)

    (run / "analysis" / "analysis_manifest.json").write_text(
        json.dumps(
            {
                "n_frames": 10,
                "results": {
                    "rmsd": {
                        "status": "ok",
                        "message": "RMSD complete",
                    },
                    "rg": {
                        "status": "skipped",
                        "message": "Not enough frames",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (analysis / "rmsd.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (analysis / "rmsd.csv").write_text("frame,rmsd\n0,0.0\n", encoding="utf-8")
    (report / "dashboard.html").write_text("<html></html>", encoding="utf-8")
    (report / "report.md").write_text("# Report\n", encoding="utf-8")
    (report / "slides.pptx").write_bytes(b"PK")
    (report / "project_bundle.zip").write_bytes(b"PK")

    server, base_url = start_test_server(run)
    try:
        payload = json.loads(urlopen(f"{base_url}/api/results", timeout=HTTP_TIMEOUT).read())
    finally:
        server.shutdown()
        server.server_close()

    assert payload["has_analysis"] is True
    assert payload["has_report"] is True
    assert {item["name"] for item in payload["analyses"]} == {"rmsd", "rg"}
    rmsd = next(item for item in payload["analyses"] if item["name"] == "rmsd")
    assert rmsd["plot"]["path"] == "analysis/rmsd/rmsd.png"
    assert any(item["path"] == "analysis/rmsd/rmsd.csv" for item in rmsd["artifacts"])
    assert [item["path"] for item in payload["reports"]] == [
        "report/dashboard.html",
        "report/report.md",
        "report/slides.pptx",
        "report/project_bundle.zip",
    ]


def test_artifact_download_route_sets_attachment_header(tmp_path: Path) -> None:
    run = tmp_path / "run"
    report = run / "report"
    report.mkdir(parents=True)
    (report / "report.md").write_text("# Report\n", encoding="utf-8")

    server, base_url = start_test_server(run)
    try:
        response = urlopen(
            f"{base_url}/artifacts/report/report.md?download=1",
            timeout=HTTP_TIMEOUT,
        )
        response.read()
    finally:
        server.shutdown()
        server.server_close()

    assert response.headers["Content-Disposition"] == 'attachment; filename="report.md"'


def test_frontend_assets_wire_functional_dashboard_sections() -> None:
    import fastmdxplora.gui as gui_pkg

    root = Path(gui_pkg.__file__).parent
    html = (root / "templates" / "dashboard.html").read_text(encoding="utf-8")
    css = (root / "static" / "dashboard.css").read_text(encoding="utf-8")
    dashboard_js = (root / "static" / "dashboard.js").read_text(encoding="utf-8")
    charts_js = (root / "static" / "charts.js").read_text(encoding="utf-8")
    viewer_js = (root / "static" / "molecule-viewer.js").read_text(encoding="utf-8")

    assert 'id="reports-files"' in html
    assert 'id="simulation-files"' in html
    assert 'id="analysis-files"' in html
    assert 'id="mini-preview-canvas"' in html
    assert "[hidden]" in css and "display: none !important" in css
    assert "/api/results" in dashboard_js
    assert "renderFiles" in dashboard_js
    assert "renderAnalysis" in dashboard_js
    assert "ResizeObserver" in charts_js
    assert "mini-preview-canvas" in viewer_js
    assert "/structure/topology.pdb" in viewer_js


def test_dashboard_refresh_seconds_are_injected_into_html(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    server, base_url = start_test_server(
        run,
        config=DashboardConfig(refresh_seconds=1.5),
    )
    try:
        html = urlopen(base_url, timeout=HTTP_TIMEOUT).read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
    assert 'data-refresh-seconds="1.5"' in html
    assert 'id="setting-refresh-seconds" min="1" max="60" step="1" value="1.5"' in html


def test_runner_live_frame_helper_calls_writer_with_valid_keyword(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[dict] = []

    def fake_write_openmm_live_frame(output_dir, **kwargs):
        calls.append({"output_dir": output_dir, **kwargs})
        return {"ok": True}

    import fastmdxplora.gui.live_frames as live_frames_module

    monkeypatch.setattr(
        live_frames_module,
        "write_openmm_live_frame",
        fake_write_openmm_live_frame,
    )

    class FakeState:
        def getPositions(self):
            return "positions"

    class FakeContext:
        def getState(self, **_kwargs):
            return FakeState()

    simulation = SimpleNamespace(context=FakeContext(), topology="topology")
    telemetry = SimpleNamespace(root=tmp_path / "simulation")
    omm = {"PDBFile": SimpleNamespace(writeFile=lambda *_args, **_kwargs: None)}

    _maybe_write_live_frame(
        omm,
        simulation,
        telemetry,
        250,
        stage="nvt",
        simulation_time_ns=0.0005,
    )

    assert len(calls) == 1
    assert calls[0]["pdbfile_writer"] is omm["PDBFile"].writeFile
    assert calls[0]["frame_index"] == 250
    assert calls[0]["stage"] == "nvt"
    assert calls[0]["archive"] is True


def test_dashboard_frame_interval_is_forwarded_to_explore_config() -> None:
    config = {"simulation": {"temperature_K": 300.0}}
    args = SimpleNamespace(dashboard_frame_interval=125)
    _enable_dashboard_telemetry(config, args)
    assert config["simulation"]["live_telemetry"] is True
    assert config["simulation"]["telemetry_interval"] == 125
    assert config["simulation"]["temperature_K"] == 300.0


def test_results_endpoint_pairs_png_preview_with_svg_download(tmp_path: Path) -> None:
    run = tmp_path / "run"
    analysis = run / "analysis" / "rg"
    analysis.mkdir(parents=True)
    (run / "analysis" / "analysis_manifest.json").write_text(
        json.dumps({"results": {"rg": {"status": "ok", "message": "rg: ok"}}}),
        encoding="utf-8",
    )
    (analysis / "rg.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (analysis / "rg.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8"
    )
    (analysis / "rg.dat").write_text("frame,rg_nm\n0,1.0\n", encoding="utf-8")

    server, base_url = start_test_server(run)
    try:
        payload = json.loads(urlopen(f"{base_url}/api/results", timeout=HTTP_TIMEOUT).read())
    finally:
        server.shutdown()
        server.server_close()

    record = payload["analyses"][0]
    assert record["plot"]["path"] == "analysis/rg/rg.png"
    assert record["plot"]["svg_path"] == "analysis/rg/rg.svg"
    assert record["plot"]["svg_download_href"].startswith("/artifacts/analysis/rg/rg.svg")
    assert payload["svg_figure_count"] == 1


def test_svg_bundle_endpoint_downloads_generated_vector_figures(tmp_path: Path) -> None:
    import io
    import zipfile

    run = tmp_path / "run"
    figure = run / "analysis" / "rmsd" / "rmsd.svg"
    figure.parent.mkdir(parents=True)
    figure.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")

    server, base_url = start_test_server(run)
    try:
        response = urlopen(f"{base_url}/analysis-figures-svg.zip", timeout=HTTP_TIMEOUT)
        data = response.read()
    finally:
        server.shutdown()
        server.server_close()

    assert response.headers["Content-Type"] == "application/zip"
    assert "fastmdxplora_svg_figures.zip" in response.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == ["analysis/rmsd/rmsd.svg"]


def test_dashboard_assets_include_svg_download_and_first_model_miniviewer_fix() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "src" / "fastmdxplora" / "gui" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    dashboard_js = (root / "src" / "fastmdxplora" / "gui" / "static" / "dashboard.js").read_text(encoding="utf-8")
    viewer_js = (root / "src" / "fastmdxplora" / "gui" / "static" / "molecule-viewer.js").read_text(encoding="utf-8")

    assert 'id="download-all-svg"' in html
    assert "Download SVG" in dashboard_js
    assert "const hadModel" in viewer_js
    assert "opts.center !== false || !hadModel" in viewer_js
    assert "resolveProteinSelection" in viewer_js


def test_viewer_visibility_controls_render_solvent_and_hydrogen_atoms() -> None:
    root = Path(__file__).resolve().parents[1]
    viewer_js = (root / "src" / "fastmdxplora" / "gui" / "static" / "molecule-viewer.js").read_text(encoding="utf-8")

    assert '"TIP3P"' in viewer_js
    assert 'if (STATE.visibility.hydrogens)' in viewer_js
    assert 'addStyle(viewer, {elem: "H"' in viewer_js
    assert 'sphere: {scale: 0.28' in viewer_js
    assert 'color: "#4da3ff"' in viewer_js
    assert 'line: {color: "#4da3ff"' in viewer_js
    assert 'sphere: {scale: 0.55' in viewer_js
    assert "forceFit" in viewer_js


def test_viewer_solvent_toggles_preserve_playback_with_static_environment_overlay() -> None:
    """Water/ion visibility must not replace an animated trajectory model."""
    root = Path(__file__).resolve().parents[1]
    viewer_js = (root / "src" / "fastmdxplora" / "gui" / "static" / "molecule-viewer.js").read_text(encoding="utf-8")
    toggle_start = viewer_js.index('if (which === "water" || which === "ions" || which === "box")')
    toggle_end = viewer_js.index("restyleViewers();", toggle_start)
    toggle_handler = viewer_js[toggle_start:toggle_end]

    assert 'if (STATE.mode === "playback")' in toggle_handler
    assert "ensurePlaybackEnvironment" in toggle_handler
    assert 'STATE.mode = "structure"' not in toggle_handler
    assert "function ensurePlaybackEnvironment" in viewer_js
    assert "STATE.environmentPdb" in viewer_js
    assert "STATE.environmentModel" in viewer_js
    assert "alignPlaybackEnvironment" in viewer_js
    assert "function drawPeriodicBox" in viewer_js
    assert "viewer.addLine" in viewer_js
    assert "environmentModel.getID()" in viewer_js


def test_viewer_environment_overlay_keeps_the_solute_legible() -> None:
    """Dense solvent and the periodic-cell guide must not dominate the protein."""
    root = Path(__file__).resolve().parents[1]
    viewer_js = (root / "src" / "fastmdxplora" / "gui" / "static" / "molecule-viewer.js").read_text(encoding="utf-8")

    environment_style = viewer_js.split("function stylePlaybackEnvironment", 1)[1].split(
        "\n  function resolveProteinSelection", 1
    )[0]
    periodic_box = viewer_js.split("function drawPeriodicBox", 1)[1].split(
        "\n  function stylePlaybackEnvironment", 1
    )[0]

    assert 'resn: WATERS, elem: "O"' in environment_style
    assert "opacity: 0.42" in environment_style
    assert "const boxOpacity = STATE.visibility.water ? 0.55 : 0.38" in periodic_box
    assert "linewidth: 1.2, opacity: boxOpacity" in periodic_box


def test_dashboard_resets_run_dependent_state_when_active_run_changes() -> None:
    root = Path(__file__).resolve().parents[1]
    dashboard_js = (root / "src" / "fastmdxplora" / "gui" / "static" / "dashboard.js").read_text(encoding="utf-8")
    viewer_js = (root / "src" / "fastmdxplora" / "gui" / "static" / "molecule-viewer.js").read_text(encoding="utf-8")

    assert 'emit("run-changed"' in dashboard_js
    assert "if (!state.appState?.active_run)" in dashboard_js
    assert 'window.FastMDXDashboard?.on("run-changed", onRunChanged)' in viewer_js
    assert "STATE.playbackTimer = null" in viewer_js
    assert "STATE.viewerGeneration += 1" in viewer_js
    assert "function isViewerGenerationCurrent" in viewer_js
    assert "if (!isViewerGenerationCurrent(generation)) return" in viewer_js
    assert "STATE.playbackLoadPromise === loadPromise" in viewer_js
    assert "STATE.structurePdb = null" in viewer_js


def test_runtime_does_not_expose_old_telemetry_after_failed_reuse(tmp_path: Path) -> None:
    runtime = DashboardRuntime(
        workspace_root=tmp_path / "workspace",
        exploration_root=tmp_path / "runs",
    )
    old_run = runtime.exploration_root / "old_run"
    (old_run / "simulation").mkdir(parents=True)
    (old_run / "simulation" / "live_status.json").write_text(
        json.dumps(
            {
                "status": "running",
                "stage": "production",
                "run_started_at": "2026-08-18T18:03:14+00:00",
                "last_update_timestamp": "2026-08-18T18:38:45+00:00",
            }
        ),
        encoding="utf-8",
    )
    log_path = old_run / "exploration.log"
    log_path.write_text(
        "fastmdx: These output directories already hold results:\n"
        f"  {old_run}\n",
        encoding="utf-8",
    )
    runtime.active_root = old_run
    runtime.process_started_at = "2026-08-19T05:07:35+00:00"
    runtime.process_finished_at = "2026-08-19T05:07:43+00:00"
    runtime.process_returncode = 2
    runtime.process = SimpleNamespace(poll=lambda: 2)
    runtime.log_path = log_path
    runtime.command = ["python", "-m", "fastmdxplora.cli.main", "explore"]

    state = runtime.snapshot()

    assert state["status"] == "failed"
    assert state["active_run"] is None
    assert "already hold results" in state["error"]
    assert runtime.data_root() != old_run


def test_dashboard_assets_do_not_pause_playback_for_live_history_appends() -> None:
    """A new rolling live frame is not a replacement trajectory.

    ``/api/playback-info`` changes its signature as the bounded live history
    grows. That must not pause an already playing viewer every poll.
    """
    root = Path(__file__).resolve().parents[1]
    viewer_js = (root / "src" / "fastmdxplora" / "gui" / "static" / "molecule-viewer.js").read_text(encoding="utf-8")

    assert "previousPayload?.source_kind === \"live-history\"" in viewer_js
    assert "payload?.source_kind === \"live-history\"" in viewer_js
    assert "STATE.playbackLoaded" in viewer_js
    assert "sameLiveHistorySource" in viewer_js
    assert "if (changed && !sameLiveHistorySource)" in viewer_js



def test_run_dependent_nav_sections_are_marked(tmp_path: Path) -> None:
    """Sections that need a run are marked so the shell can dim them.

    Opening the GUI in an empty workspace should point at the builder rather
    than offering five views that have nothing to show.
    """
    run = tmp_path / "run"
    run.mkdir()
    server, base_url = start_test_server(run)
    try:
        html = urlopen(f"{base_url}/", timeout=HTTP_TIMEOUT).read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()

    # "live" was a view of its own; its panels are on the overview now.
    for view in ("overview", "viewer", "analysis", "files"):
        assert f'data-view-link="{view}" data-requires-run="true"' in html
    # The builder is always reachable; it is what an empty workspace needs.
    assert 'data-view-link="builder" data-requires-run' not in html


def test_live_gui_and_static_report_share_one_theme(tmp_path: Path) -> None:
    """Both surfaces read their design tokens from ``live/static/theme.css``.

    The live GUI links the file; a generated report inlines it so the page
    stays self-contained. Duplicating the palette is what made the two look
    like different products, so this asserts a single source of truth.
    """
    import datetime

    import fastmdxplora.gui as gui_pkg
    from fastmdxplora.gui import report_dashboard

    theme = Path(gui_pkg.__file__).with_name("static") / "theme.css"
    assert theme.is_file(), "theme.css must ship with the package"
    tokens = theme.read_text(encoding="utf-8")
    assert "--background-primary" in tokens
    assert "--accent-cyan" in tokens

    # The GUI links it ahead of its own stylesheet.
    template = Path(gui_pkg.__file__).with_name("templates") / "dashboard.html"
    shell = template.read_text(encoding="utf-8")
    assert shell.index("/static/theme.css") < shell.index("/static/dashboard.css")

    # The static report inlines the very same tokens.
    html = report_dashboard._render_dashboard(
        title="Theme check",
        system="1L2Y",
        status="completed",
        generated_at=datetime.datetime.now(datetime.timezone.utc),
        phase_notice="",
        cards=[],
        sections=[],
        links=[],
        phase_rows=[],
        metrics=[],
        output_folder=str(tmp_path),
        live_html="",
    )
    assert "--background-primary" in html
    assert "--accent-cyan" in html
    # The superseded navy palette must not reappear.
    assert "#07101b" not in html
    assert "var(--bg)" not in html


def test_results_payload_carries_report_panels(tmp_path: Path) -> None:
    """The GUI and the static report show the same numbers.

    Summary cards, metric statistics, and phase rows are computed once in
    ``gui.report_dashboard`` and served to the browser, rather than the two
    surfaces calculating them independently and drifting.
    """
    import json

    from fastmdxplora.gui.server import _results_payload

    run = tmp_path / "run"
    (run / "analysis").mkdir(parents=True)
    (run / "simulation").mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "system": "1L2Y",
                "phases": [
                    {
                        "name": "setup",
                        "status": "ok",
                        "started_at": "2026-08-01T00:00:00+00:00",
                        "finished_at": "2026-08-01T00:00:06+00:00",
                    },
                    {
                        "name": "simulation",
                        "status": "ok",
                        "started_at": "2026-08-01T00:00:06+00:00",
                        "finished_at": "2026-08-01T00:01:06+00:00",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (run / "analysis" / "analysis_manifest.json").write_text(
        json.dumps({"n_frames": 8, "n_atoms": 5080}), encoding="utf-8"
    )
    (run / "simulation" / "simulation_parameters.json").write_text(
        json.dumps({"parameters": {"temperature_K": 300.0, "production_steps": 400}}),
        encoding="utf-8",
    )

    payload = _results_payload(run)

    labels = {card["label"] for card in payload["summary_cards"]}
    assert {"Project status", "Phases completed", "Frames", "Atom count"} <= labels

    phases = {row["name"]: row["status"] for row in payload["phase_rows"]}
    assert phases["Setup"] == "ok"
    assert phases["Analysis"] == "not-run"

    metrics = {row["metric"] for row in payload["metric_rows"]}
    assert "Frame count" in metrics


def test_report_panels_never_break_the_dashboard(tmp_path: Path) -> None:
    """A malformed run must not take the whole payload down."""
    from fastmdxplora.gui.server import _results_payload

    run = tmp_path / "broken"
    run.mkdir()
    (run / "manifest.json").write_text("{not json", encoding="utf-8")

    payload = _results_payload(run)
    assert payload["summary_cards"] == [] or isinstance(payload["summary_cards"], list)
    assert "artifacts" in payload


def test_overview_hosts_the_report_panels(tmp_path: Path) -> None:
    """Overview has containers for the summary cards, phases, and statistics.

    These mirror what the generated report shows, so the browser is no longer
    the poorer of the two surfaces.
    """
    run = tmp_path / "run"
    run.mkdir()
    server, base_url = start_test_server(run)
    try:
        html = urlopen(f"{base_url}/", timeout=HTTP_TIMEOUT).read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()

    for container in (
        'id="overview-summary-cards"',
        'id="overview-phase-rows"',
        'id="overview-stat-rows"',
    ):
        assert container in html

    # Both tables carry the statistics columns the report shows.
    assert "Std. dev." in html
    assert "Trajectory statistics" in html
    # Renamed when the live panels joined this page: one card is a bar for the
    # running stage and the other a table of phases, and both were "progress".
    assert "Phases" in html

    import fastmdxplora.gui as gui_pkg

    script = (Path(gui_pkg.__file__).with_name("static") / "dashboard.js").read_text(
        encoding="utf-8"
    )
    assert "function renderReportPanels(" in script
    assert "renderReportPanels(payload);" in script


def test_results_payload_carries_categorised_sections(tmp_path: Path) -> None:
    """The GUI receives the report's grouping, not just a flat figure list."""
    import json

    from fastmdxplora.gui.server import _results_payload

    run = tmp_path / "run"
    for folder in ("analysis/rmsd", "analysis/cluster", "analysis/dimred", "report"):
        (run / folder).mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"system": "1L2Y", "phases": []}))
    (run / "analysis" / "analysis_manifest.json").write_text(json.dumps({"n_frames": 8}))
    for rel in (
        "analysis/rmsd/rmsd.png",
        "analysis/cluster/kmeans_trajectory.png",
        "analysis/dimred/pca.png",
    ):
        (run / rel).write_bytes(b"\x89PNG\r\n\x1a\n")

    payload = _results_payload(run)
    sections = payload["analysis_sections"]
    assert sections, "expected categorised sections"

    titles = {section["title"] for section in sections}
    # Each analysis keeps its own heading rather than being pooled.
    assert {"RMSD", "Clustering", "Dimensionality Reduction"} <= titles

    panels = [panel for section in sections for panel in section["panels"]]
    assert any(panel["title"] == "RMSD" for panel in panels)
    # Hrefs must be servable by the GUI, not relative to a report folder.
    assert all(panel["href"].startswith("/artifacts/") for panel in panels)


def test_data_files_are_summarised_without_plotting(tmp_path: Path) -> None:
    """Panel captions come straight from the data now.

    They used to be produced as a side effect of rendering a second copy of
    every figure, which meant drawing 17 charts nothing displayed.
    """
    from fastmdxplora.gui.report_dashboard import _summarise_data_file

    series = tmp_path / "rmsd.dat"
    series.write_text("\n".join(f"{i} {0.2 + i * 0.01:.4f}" for i in range(10)),
                      encoding="utf-8")
    assert _summarise_data_file(series, "line").startswith("avg ")

    clusters = tmp_path / "clusters.dat"
    clusters.write_text("\n".join(f"{i} {i % 3}" for i in range(30)), encoding="utf-8")
    assert _summarise_data_file(clusters, "cluster") == "3 clusters"
    assert _summarise_data_file(clusters, "cluster_counts") == "3 clusters"

    projection = tmp_path / "pca.dat"
    projection.write_text("\n".join(f"{i} {i * 0.1} {i * 0.2}" for i in range(12)),
                          encoding="utf-8")
    assert _summarise_data_file(projection, "scatter") == "12 frames"

    # Malformed data raises rather than inventing a caption.

    bad = tmp_path / "bad.dat"
    bad.write_text("1\n2\n3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        _summarise_data_file(bad, "scatter")

def test_analysis_view_hosts_sections_and_quick_actions(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    server, base_url = start_test_server(run)
    try:
        html = urlopen(f"{base_url}/", timeout=HTTP_TIMEOUT).read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()

    assert 'id="analysis-sections"' in html
    assert 'id="analysis-quick-actions"' in html

    import fastmdxplora.gui as gui_pkg

    script = (Path(gui_pkg.__file__).with_name("static") / "dashboard.js").read_text(
        encoding="utf-8"
    )
    assert "function renderAnalysisSections(" in script
    assert "renderAnalysisSections(payload);" in script


def test_run_title_does_not_repeat_the_product_name(tmp_path: Path) -> None:
    """The sidebar already says FastMDXplora; the run heading names the system."""
    from fastmdxplora.gui.server import _run_title

    assert _run_title(tmp_path, {"system": "1L2Y"}) == "1L2Y"
    assert _run_title(tmp_path, {"system": "/data/protein.pdb"}) == "protein"
    # An explicit report title still wins.
    assert (
        _run_title(tmp_path, {"system": "1L2Y", "options": {"report": {"title": "T4L apo"}}})
        == "T4L apo"
    )


def test_analysis_cards_show_the_publication_figure() -> None:
    """Cards display the figure the analysis wrote, not a second rendering.

    The analyses draw at publication settings; the report's compact variant is
    smaller, so showing it in the card would differ from what opening the
    figure gives you.
    """
    import fastmdxplora.gui as gui_pkg

    script = (Path(gui_pkg.__file__).with_name("static") / "dashboard.js").read_text(
        encoding="utf-8"
    )
    assert "const figure = panel.original_href || panel.href;" in script
    assert "Analysis figure</a>" not in script

    css = (Path(gui_pkg.__file__).with_name("static") / "dashboard.css").read_text(
        encoding="utf-8"
    )
    # A 180px-tall frame scaled a 1950px-wide figure to about 14%, which made
    # axis labels illegible regardless of source DPI.
    assert "aspect-ratio: 6.5 / 4.2;" in css
    assert "minmax(440px, 1fr)" in css


def test_pressure_is_not_advertised_as_a_live_metric(tmp_path: Path) -> None:
    """OpenMM's StateDataReporter cannot report pressure, so no card claims it."""
    run = tmp_path / "run"
    run.mkdir()
    server, base_url = start_test_server(run)
    try:
        html = urlopen(f"{base_url}/", timeout=HTTP_TIMEOUT).read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
    assert 'data-metric="pressure"' not in html


def test_each_analysis_gets_its_own_section(tmp_path: Path) -> None:
    """Sections follow the analyses, not invented groupings.

    Placement used to depend on figure count: a section holding three figures
    or fewer was merged into "Additional Analysis", so SASA appeared under its
    own heading or a catch-all depending on the run.
    """
    import json

    from fastmdxplora.gui.server import _results_payload

    run = tmp_path / "run"
    (run / "analysis").mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"system": "1L2Y", "phases": []}))
    for folder, figures in {
        "rmsd": ["rmsd"],
        "sasa": ["sasa_total"],
        "cluster": ["kmeans_trajectory", "kmeans_population", "dendrogram"],
    }.items():
        (run / "analysis" / folder).mkdir(parents=True)
        for name in figures:
            (run / "analysis" / folder / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (run / "analysis" / folder / f"{name}.svg").write_text("<svg/>", encoding="utf-8")

    sections = {s["title"]: s for s in _results_payload(run)["analysis_sections"]}

    # A one-figure analysis keeps its own heading.
    assert "Solvent Accessible Surface Area" in sections
    assert "RMSD" in sections
    assert "Clustering" in sections
    assert "Additional Analysis" not in sections
    assert "Core Metrics" not in sections

    # PNG and SVG of the same plot count once.
    assert len(sections["RMSD"]["panels"]) == 1
    assert len(sections["Clustering"]["panels"]) == 3

    # Anchors are derived from the title, so new analyses need no extra entry.
    assert sections["Solvent Accessible Surface Area"]["anchor"] == (
        "solvent-accessible-surface-area"
    )


def test_long_paths_truncate_instead_of_wrapping() -> None:
    """text-overflow only applies to a single unwrapped line."""
    import fastmdxplora.gui as gui_pkg

    css = (Path(gui_pkg.__file__).with_name("static") / "dashboard.css").read_text(
        encoding="utf-8"
    )
    import re

    for selector in (".footer-value", ".run-id", ".run-title"):
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert match, f"{selector} rule missing"
        body = match.group(1)
        if "text-overflow" in body:
            assert "white-space: nowrap" in body, f"{selector} truncation needs nowrap"

    script = (Path(gui_pkg.__file__).with_name("static") / "dashboard.js").read_text(
        encoding="utf-8"
    )
    # Truncated values stay reachable on hover.
    assert "function setTextWithTooltip(" in script
    assert 'setTextWithTooltip("sidebar-run-name"' in script


def test_summary_card_values_truncate_long_paths() -> None:
    """The Output folder card holds a full path and must not wrap.

    text-overflow needs white-space: nowrap to take effect, and the full value
    is kept in the title attribute so truncation loses nothing.
    """
    import re

    import fastmdxplora.gui as gui_pkg

    css = (Path(gui_pkg.__file__).with_name("static") / "dashboard.css").read_text(
        encoding="utf-8"
    )
    rule = re.search(r"\.metric-card-value\s*\{([^}]*)\}", css)
    assert rule, ".metric-card-value rule missing"
    body = rule.group(1)
    assert "text-overflow: ellipsis" in body
    assert "white-space: nowrap" in body

    script = (Path(gui_pkg.__file__).with_name("static") / "dashboard.js").read_text(
        encoding="utf-8"
    )
    assert 'title="${escapeAttr(value)}"' in script
    assert 'data-kind="${kind}"' in script


class TestTheBrowserCanReachEverySetting:
    """The form was written by hand, one control at a time.

    So it offered eleven of the eighty-three settings that exist, and whole
    phases were missing: there was no way to configure an analysis or a report
    from the browser at all. A field nobody thought to add was simply
    unreachable, and nothing said so.

    The page now reads the schema instead. Adding a field puts a control on the
    page, and there is no second list to keep in step.
    """

    def test_every_schema_field_is_offered(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS
        from fastmdxplora.gui.schema_payload import schema_payload

        payload = schema_payload()
        for phase, group in PHASE_SCHEMAS.items():
            offered = {f["name"] for f in payload["phases"][phase]["fields"]}
            declared = set(group.field_names())
            assert offered == declared, (
                f"{phase}: not offered {sorted(declared - offered)}"
            )

    def test_no_phase_is_left_out(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS
        from fastmdxplora.gui.schema_payload import schema_payload

        assert set(schema_payload()["phases"]) == set(PHASE_SCHEMAS)

    def test_a_field_carries_what_a_control_needs(self) -> None:
        from fastmdxplora.gui.schema_payload import schema_payload

        payload = schema_payload()
        for phase, block in payload["phases"].items():
            for field in block["fields"]:
                assert field["help"], f"{phase}.{field['name']} has no help"
                assert field["control"], f"{phase}.{field['name']} has no control"

    def test_a_field_with_choices_becomes_a_select(self) -> None:
        from fastmdxplora.gui.schema_payload import schema_payload

        fields = {f["name"]: f for f in
                  schema_payload()["phases"]["setup"]["fields"]}
        assert fields["heterogens"]["control"] == "select"
        assert fields["heterogens"]["choices"] == ["auto", "drop", "keep"]
        assert fields["ph"]["control"] == "number"

    def test_it_is_json(self) -> None:
        """It is sent over the wire, so it must survive the trip."""
        import json

        from fastmdxplora.gui.schema_payload import schema_payload

        restored = json.loads(json.dumps(schema_payload()))
        assert restored["phases"]["analysis"]["fields"]

    def test_the_dashboard_serves_it(self) -> None:
        """Found through the module, and read as the file was written.

        Reading by a path relative to the working directory only works when
        the tests are run from the repository root; and reading without naming
        the encoding uses whatever the machine happens to default to, which is
        ASCII on some and chokes on the first accented character in the file.
        """
        import pathlib

        from fastmdxplora.gui import server

        source = pathlib.Path(server.__file__).read_text(encoding="utf-8")
        assert "/api/schema" in source

    def test_per_analysis_settings_come_from_the_analyses(self) -> None:
        from fastmdxplora.gui.schema_payload import schema_payload

        options = schema_payload()["analysis_options"]
        if not options["available"]:
            import pytest

            pytest.skip(options["reason"])
        assert "cluster" in options["analyses"]
        names = {o["name"] for o in options["analyses"]["cluster"]}
        assert {"n_clusters", "linkage", "features"} <= names

    def test_shared_settings_are_marked_apart(self) -> None:
        """So a form can group what every analysis has separately."""
        from fastmdxplora.gui.schema_payload import schema_payload

        options = schema_payload()["analysis_options"]
        if not options["available"]:
            import pytest

            pytest.skip(options["reason"])
        by_name = {o["name"]: o for o in options["analyses"]["cluster"]}
        assert by_name["n_clusters"]["shared"] is False
        assert by_name["figsize"]["shared"] is True


class TestPointingAtAFolderOfResults:
    """A trajectory that already exists should be enough to start.

    From GROMACS, from AMBER, from someone's own OpenMM script, from a run this
    software did last week. Requiring a simulation to be reproduced here before
    the browser is any use excludes everyone who already has the data, which is
    most of the field.
    """

    @staticmethod
    def _a_finished_run(root):
        (root / "setup").mkdir(parents=True)
        (root / "simulation").mkdir(parents=True)
        (root / "setup" / "system.pdb").write_text("ATOM" * 200)
        (root / "simulation" / "topology.pdb").write_text("ATOM" * 100)
        (root / "simulation" / "production.dcd").write_bytes(b"x" * 5_000_000)
        (root / "simulation" / "equilibration.dcd").write_bytes(b"x" * 100_000)
        (root / "resolved_config.yml").write_text("setup: {}")
        return root

    def test_it_finds_the_trajectory_and_the_topology(self, tmp_path) -> None:
        from fastmdxplora.gui.directory_inspect import inspect_directory

        found = inspect_directory(self._a_finished_run(tmp_path / "run"))
        assert found["ok"] and found["can_analyse"]
        assert {t["name"] for t in found["trajectories"]} == {
            "production.dcd", "equilibration.dcd"}

    def test_it_suggests_the_production_run(self, tmp_path) -> None:
        """The long one, found by size rather than by a naming convention.

        Guessing from the name would only work for runs this software named.
        """
        from fastmdxplora.gui.directory_inspect import inspect_directory

        found = inspect_directory(self._a_finished_run(tmp_path / "run"))
        assert found["suggestion"]["trajectory"].endswith("production.dcd")

    def test_it_pairs_the_topology_beside_it(self, tmp_path) -> None:
        """Not the one in setup/, which describes a different stage.

        Compared as paths rather than by the end of a string: a path on
        Windows is joined with backslashes, and "simulation/topology.pdb" is
        the end of nothing there.
        """
        import pathlib

        from fastmdxplora.gui.directory_inspect import inspect_directory

        found = inspect_directory(self._a_finished_run(tmp_path / "run"))
        chosen = pathlib.Path(found["suggestion"]["topology"])
        assert chosen.name == "topology.pdb"
        assert chosen.parent.name == "simulation"

    def test_it_recognises_a_previous_run(self, tmp_path) -> None:
        from fastmdxplora.gui.directory_inspect import inspect_directory

        found = inspect_directory(self._a_finished_run(tmp_path / "run"))
        assert found["is_previous_run"]
        assert "resolved_config.yml" in found["run_markers"]

    @pytest.mark.parametrize("marker", [
        "resolved_config.yml", "manifest.json",
        "live_status.json", "setup_parameters.json",
    ])
    def test_every_marker_is_a_file_this_software_writes(
        self, marker: str
    ) -> None:
        """The comment above the list says so, and for two of them it was
        not true.

        `status.json` and `setup_manifest.json` sat there after being
        renamed to `live_status.json` and `setup_parameters.json`. Nothing
        failed: the two live names matched first, so a folder was still
        recognised and the dead pair simply never fired -- which is why a
        list of four could be half wrong for as long as it liked.

        Asked of the source rather than of a fixture, because a fixture
        writes whatever the test decides to write.
        """
        from pathlib import Path

        import fastmdxplora
        from fastmdxplora.gui.directory_inspect import _RUN_MARKERS

        assert marker in _RUN_MARKERS

        root = Path(fastmdxplora.__file__).parent
        writers = [
            path for path in root.rglob("*.py")
            if path.name != "directory_inspect.py"
            and marker in path.read_text(encoding="utf-8", errors="replace")
        ]
        assert writers, f"nothing outside directory_inspect names {marker}"

    def test_the_list_has_not_grown_a_name_nobody_writes(self) -> None:
        """The parametrize above pins the four that exist. This catches a
        fifth being added without the same check."""
        from fastmdxplora.gui.directory_inspect import _RUN_MARKERS

        assert set(_RUN_MARKERS) == {
            "resolved_config.yml", "manifest.json",
            "live_status.json", "setup_parameters.json",
        }

    def test_a_lone_structure_is_something_to_set_up(self, tmp_path) -> None:
        """A PDB on its own is a simulation waiting to happen, not a result."""
        from fastmdxplora.gui.directory_inspect import inspect_directory

        (tmp_path / "only.pdb").write_text("ATOM" * 50)
        found = inspect_directory(tmp_path)
        assert found["can_set_up"] and not found["can_analyse"]
        assert found["suggestion"]["trajectory"] is None

    def test_a_missing_folder_says_so(self, tmp_path) -> None:
        from fastmdxplora.gui.directory_inspect import inspect_directory

        found = inspect_directory(tmp_path / "nowhere")
        assert not found["ok"] and "No such directory" in found["error"]

    def test_nothing_opens_the_trajectory(self, tmp_path) -> None:
        """The files here are not trajectories at all -- they are filler.

        Recognising them by extension costs nothing and cannot fail. Opening a
        multi-gigabyte DCD to check would stall the page, and the frame count
        is not needed until an analysis is actually asked for.
        """
        from fastmdxplora.gui.directory_inspect import inspect_directory

        found = inspect_directory(self._a_finished_run(tmp_path / "run"))
        assert found["ok"], "reading the file would have raised here"

    def test_the_dashboard_serves_it(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        source = pathlib.Path(server.__file__).read_text(encoding="utf-8")
        assert "/api/inspect-directory" in source


class TestTheConfigTheBrowserWritesRunsElsewhere:
    """A laptop is a poor place to run fifty nanoseconds.

    So the browser's job is to decide what to run, and the file it writes has
    to work where the compute is. Which means it must be a file the command
    line accepts -- checked here, with the command line's own validator, while
    the form that produced it is still on the screen, rather than an hour into
    a queue on a cluster.
    """

    @staticmethod
    def _a_reasonable_form():
        return {
            "system": "1ubq",
            "system_id": "ubiquitin",
            "output": "runs/ubq",
            "include": ["setup", "simulation", "analysis"],
            "setup": {"ph": "6.5", "forcefield": "amber14",
                      "keep_water": "true"},
            "simulation": {"temperature_K": "310"},
            "analysis": {"scope": "protein", "include": "rmsd, rmsf"},
        }

    def test_the_command_line_reads_back_what_the_browser_wrote(
        self, tmp_path
    ) -> None:
        from fastmdxplora.config.loader import (
            load_config_file, phase_options, validate_config,
        )
        from fastmdxplora.gui.config_builder import config_yaml

        written = config_yaml(self._a_reasonable_form())
        assert written["ok"], written["error"]

        path = tmp_path / "exploration.yml"
        path.write_text(written["yaml"], encoding="utf-8")

        loaded = load_config_file(path)
        validate_config(loaded)
        options = phase_options(loaded)
        assert options["setup"]["ph"] == 6.5
        assert options["setup"]["forcefield"] == "amber14"
        assert options["analysis"]["scope"] == "protein"

    def test_a_setting_left_alone_is_left_out(self) -> None:
        """A file restating forty defaults buries the three that were chosen.

        Those three are what a reader of the methods section needs.
        """
        from fastmdxplora.gui.config_builder import build_config

        form = self._a_reasonable_form()
        form["setup"]["heterogens"] = "auto"      # the default
        built = build_config(form)
        assert "heterogens" not in built["setup"]
        assert built["setup"]["forcefield"] == "amber14"

    def test_text_from_a_form_becomes_the_right_type(self) -> None:
        """Everything arrives as text, and a type may be several at once."""
        from fastmdxplora.gui.config_builder import build_config

        built = build_config(self._a_reasonable_form())
        assert built["setup"]["ph"] == 6.5
        assert built["setup"]["keep_water"] is True
        assert built["simulation"]["temperature_K"] == 310
        assert built["analysis"]["include"] == ["rmsd", "rmsf"]

    def test_a_setting_that_does_not_exist_is_dropped(self) -> None:
        """The page must not be able to write a file the CLI will refuse."""
        from fastmdxplora.gui.config_builder import build_config

        form = self._a_reasonable_form()
        form["setup"]["not_a_real_setting"] = "x"
        form["not_a_real_phase"] = {"y": 1}
        built = build_config(form)
        assert "not_a_real_setting" not in built["setup"]
        assert "not_a_real_phase" not in built

    def test_a_configuration_that_would_be_refused_says_so_here(self) -> None:
        from fastmdxplora.gui.config_builder import config_yaml

        out = config_yaml({"output": "o", "include": ["not_a_phase"]})
        assert not out["ok"]
        assert out["error"] and out["yaml"] is None

    def test_one_system_is_written_the_way_forty_would_be(self) -> None:
        """So a file for one protein and a file for a batch have one shape."""
        from fastmdxplora.gui.config_builder import build_config

        built = build_config(self._a_reasonable_form())
        assert built["systems"] == [{"system": "1ubq", "id": "ubiquitin"}]

    def test_the_dashboard_serves_it(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        source = pathlib.Path(server.__file__).read_text(encoding="utf-8")
        assert "/api/config" in source


class TestARunStartsFromTheFileYouCouldTakeAway:
    """One artifact, whether the compute is here or on a cluster.

    A run used to start from a command assembled out of hand-picked flags --
    four setup settings, a few simulation ones -- so the browser could only
    start the kind of run somebody had thought to wire up, and the phase list
    was fixed at setup and simulation before anything else was considered.
    Analysing a trajectory that already existed was not expressible at all.

    Starting from a config settles both: a config names its phases, and the
    file that runs here is the file the download hands over.
    """

    @staticmethod
    def _analyse_what_exists():
        return {
            "output": "runs/analysis",
            "include": ["analysis"],
            "systems": [{"system": "topology.pdb", "id": "existing"}],
            "analysis": {
                "trajectory": "production.dcd",
                "topology": "topology.pdb",
                "scope": "protein",
                "include": "rmsd, rmsf, rg",
            },
        }

    def test_an_analysis_of_existing_data_is_expressible(self, tmp_path) -> None:
        from fastmdxplora.gui.run_from_config import prepare_run

        run = prepare_run(self._analyse_what_exists(), tmp_path)
        assert run["ok"], run["error"]

    def test_what_runs_here_is_what_the_download_gives(self, tmp_path) -> None:
        """Not a second implementation that happens to agree."""
        from fastmdxplora.gui.config_builder import config_yaml
        from fastmdxplora.gui.run_from_config import prepare_run

        state = self._analyse_what_exists()
        run = prepare_run(state, tmp_path)
        assert run["config_yaml"] == config_yaml(state)["yaml"]

    def test_the_config_is_written_beside_the_results(self, tmp_path) -> None:
        """So a run can be repeated from the output directory rather than
        from what somebody remembers typing."""
        import pathlib

        from fastmdxplora.gui.run_from_config import CONFIG_FILENAME, prepare_run

        run = prepare_run(self._analyse_what_exists(), tmp_path)
        written = pathlib.Path(run["config_path"])
        assert written.name == CONFIG_FILENAME
        assert written.parent == tmp_path
        assert written.read_text(encoding="utf-8") == run["config_yaml"]

    def test_the_command_runs_that_file(self, tmp_path) -> None:
        from fastmdxplora.gui.run_from_config import prepare_run

        run = prepare_run(self._analyse_what_exists(), tmp_path)
        assert "--config" in run["command"]
        assert run["config_path"] in run["command"]
        assert "explore" in run["command"]

    def test_the_command_line_accepts_that_file(self, tmp_path) -> None:
        """The parser is asked, rather than the flag being assumed to exist."""
        from fastmdxplora.cli.main import _build_parser
        from fastmdxplora.gui.run_from_config import prepare_run

        run = prepare_run(self._analyse_what_exists(), tmp_path)
        parser = _build_parser()
        # command[3:] drops the interpreter, -m and the module path.
        parsed = parser.parse_args(run["command"][3:])
        assert parsed.config == run["config_path"]

    def test_a_configuration_that_would_be_refused_starts_nothing(
        self, tmp_path
    ) -> None:
        from fastmdxplora.gui.run_from_config import prepare_run

        run = prepare_run({"include": ["not_a_phase"]}, tmp_path)
        assert not run["ok"]
        assert run["command"] is None
        assert not (tmp_path / "exploration.yml").exists()


class TestEveryPageCanBeReached:
    """A section in the document is not the same as a page you can get to.

    The router kept its own list of page names and sent anything not on it to
    the overview. So adding a section was not enough: the new page's link
    appeared, was clickable, and quietly showed the overview instead -- which
    looks exactly like a link wired to the wrong place.

    The pages are now read from the document, so a section that exists is a
    section you can reach.
    """

    @staticmethod
    def _files():
        import pathlib

        from fastmdxplora.gui import server

        root = pathlib.Path(server.__file__).parent
        return (
            (root / "templates" / "dashboard.html").read_text(encoding="utf-8"),
            (root / "static" / "dashboard.js").read_text(encoding="utf-8"),
        )

    def test_the_router_does_not_keep_its_own_list(self) -> None:
        import re

        _page, script = self._files()
        declared = re.search(r"pages:\s*\[([^\]]*)\]", script)
        assert declared, "the page list is gone entirely; check this test"
        assert not declared.group(1).strip(), (
            "the router names its pages, so a new section will not be reachable "
            f"until somebody remembers to add it here: {declared.group(1)}"
        )

    def test_the_router_reads_them_from_the_document(self) -> None:
        _page, script = self._files()
        assert 'state.pages = $$(\'.page\')' in script

    def test_every_section_has_a_link_and_every_link_a_section(self) -> None:
        import re

        page, _script = self._files()
        sections = set(re.findall(r'<section class="page" data-page="([^"]+)"', page))
        links = set(re.findall(r'data-view-link="([^"]+)"', page))
        # Links may point at a page from elsewhere on the page, but a section
        # nobody links to cannot be opened and a link to nothing is a dead end.
        assert sections, "no pages found at all"
        assert links <= sections, f"links with no section: {sorted(links - sections)}"
        assert sections <= links, f"sections with no link: {sorted(sections - links)}"

    def test_the_analyse_page_is_among_them(self) -> None:
        page, _script = self._files()
        assert 'data-page="run"' in page
        assert 'data-view-link="run"' in page


class TestChoosingAFolderWithoutTypingOne:
    """A browser cannot offer a dialog for a folder the server will read.

    The one it has uploads files to a page; this needs a path the process can
    open. So the walking happens here and the page draws it.
    """

    def test_it_lists_folders_and_the_way_back(self, tmp_path) -> None:
        from fastmdxplora.gui.browse import browse

        (tmp_path / "runs").mkdir()
        (tmp_path / "notes").mkdir()
        listing = browse(tmp_path)
        assert listing["ok"]
        assert {e["name"] for e in listing["entries"]} == {"runs", "notes"}
        assert listing["parent"] == str(tmp_path.parent)

    def test_it_says_which_folders_hold_data(self, tmp_path) -> None:
        """So a list of run folders can be read without opening each one."""
        from fastmdxplora.gui.browse import browse

        (tmp_path / "has_data").mkdir()
        (tmp_path / "has_data" / "run.dcd").write_bytes(b"x")
        (tmp_path / "has_data" / "top.pdb").write_text("ATOM")
        (tmp_path / "empty").mkdir()

        by_name = {e["name"]: e for e in browse(tmp_path)["entries"]}
        assert by_name["has_data"]["trajectories"] == 1
        assert by_name["has_data"]["structures"] == 1
        assert by_name["empty"]["trajectories"] == 0

    def test_nothing_is_opened_to_count_them(self, tmp_path) -> None:
        """The files here are not trajectories. Counting by extension cannot
        fail; reading them would."""
        from fastmdxplora.gui.browse import browse

        (tmp_path / "run").mkdir()
        (tmp_path / "run" / "not_really.dcd").write_text("this is not a DCD")
        assert browse(tmp_path)["entries"][0]["trajectories"] == 1

    def test_hidden_folders_are_left_out(self, tmp_path) -> None:
        from fastmdxplora.gui.browse import browse

        (tmp_path / ".git").mkdir()
        (tmp_path / "runs").mkdir()
        assert [e["name"] for e in browse(tmp_path)["entries"]] == ["runs"]

    def test_a_file_means_the_folder_holding_it(self, tmp_path) -> None:
        """Pasting a path from a terminal often lands on a file."""
        from fastmdxplora.gui.browse import browse

        target = tmp_path / "topology.pdb"
        target.write_text("ATOM")
        assert browse(target)["path"] == str(tmp_path.resolve())

    def test_no_path_starts_somewhere_readable(self) -> None:
        import pathlib

        from fastmdxplora.gui.browse import browse

        assert browse()["path"] == str(pathlib.Path.home().resolve())

    def test_a_folder_that_is_not_there_says_so(self, tmp_path) -> None:
        from fastmdxplora.gui.browse import browse

        listing = browse(tmp_path / "nowhere")
        assert not listing["ok"] and "No such folder" in listing["error"]

    def test_the_dashboard_serves_it(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        source = pathlib.Path(server.__file__).read_text(encoding="utf-8")
        assert "/api/browse" in source

    def test_both_folder_fields_can_be_browsed(self) -> None:
        """Through the shared picker rather than a button per page.

        Each page used to add its own, which is how the run page came to have
        five path fields and no way to browse any of them.
        """
        import pathlib
        import re

        from fastmdxplora.gui import server

        page = (pathlib.Path(server.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        for element_id in ("run-trajectory", "run-output"):
            field = re.search(rf'<input[^>]*id="{element_id}"[^>]*>', page)
            assert field, f"{element_id} is gone; check this test"
            assert "data-picks=" in field.group(0)


class TestASettingShowsWhatItWillDo:
    """A control that cannot say what happens if left alone is not much help.

    Two settings hid their default behind None and filled it in afterwards, so
    anything reading the signature -- a form, a config template, the help --
    saw no default at all. Clustering was worse than silent: its docstring
    named one method where the code ran two.
    """

    def test_clustering_declares_the_methods_it_runs(self) -> None:
        from fastmdxplora.analysis.cluster import Cluster

        assert Cluster().methods == ["kmeans", "hierarchical"]

    def test_and_the_description_agrees(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.describe import describe_analysis

        option = next(o for o in describe_analysis("cluster")
                      if o.name == "methods")
        assert option.default == ("kmeans", "hierarchical")

    def test_dimensionality_reduction_too(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.describe import describe_analysis
        from fastmdxplora.analysis.dimred import DimRed

        option = next(o for o in describe_analysis("dimred")
                      if o.name == "methods")
        assert option.default == ("pca",)
        assert DimRed().methods == ["pca"]

    def test_only_the_genuinely_undecidable_have_no_default(self) -> None:
        """A ligand's residue name depends on the structure, so it has none
        until one is read. Everything else should be able to say."""
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.describe import describe_all

        silent = {
            f"{name}.{o.name}"
            for name, options in describe_all().items()
            for o in options
            if o.default is None and o.owner != "Analysis"
            # These depend on the ligand rather than on a choice: its residue
            # name, its charge, and a file stating its chemistry are facts
            # about the molecule, not settings with a sensible default.
            # `structure` is the same kind of thing one level out: the file
            # a study started from is a fact about the study, and there is
            # no path that would be a sensible default. Absent, it is
            # discovered from the run directory.
            # `r_max` is not a choice left unmade either: it is half the
            # smallest box dimension, which the system fixes and no literal
            # can state.
            # `reference` names a measured dataset, which is a fact about
            # what is available to compare against rather than a setting
            # with a default; and `reference_exclude` deliberately defaults
            # to nothing, because a residue set silently applied is the
            # thing that makes such a comparison unreadable. Both belong to
            # a comparison that is off unless a study asks for it.
            # `pair_distance` measures between two groups somebody chose to
            # compare, and there is no pair a literal could name: unlike
            # every other selection in this package, its subject is not in
            # the trajectory. The analysis is left out of the automatic plan
            # for the same reason (`requires_naming`), so the absent default
            # is never silently filled in -- a study that wants it says
            # which two things.
            and o.name not in {"ligand_resname", "ligand_net_charge",
                               "ligand_chemistry", "structure", "state_csv",
                               "r_max", "reference", "reference_exclude"}
            and f"{name}.{o.name}" not in {"pair_distance.selection_a",
                                           "pair_distance.selection_b"}
        }
        assert not silent, f"these cannot say what they would do: {sorted(silent)}"


class TestAnAnalysisExplainsItselfOnThePage:
    """Every analysis module opens by saying what its measurement means.

    What a rising profile indicates, which paper the definition comes from,
    why the default is the default. That was written for somebody reading the
    source, and it is exactly what somebody choosing between checkboxes needs.
    It was not on the page.
    """

    def test_every_analysis_has_something_to_say(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.describe import explain_analysis
        from fastmdxplora.analysis.orchestrator import available_analyses

        silent = []
        for name in available_analyses():
            explanation = explain_analysis(name)
            if not explanation["summary"] or not explanation["detail"]:
                silent.append(name)
        assert not silent, f"these would appear with no explanation: {silent}"

    def test_the_explanation_reaches_the_payload(self) -> None:
        from fastmdxplora.gui.schema_payload import schema_payload

        options = schema_payload()["analysis_options"]
        if not options["available"]:
            import pytest

            pytest.skip(options["reason"])
        qvalue = options["explanations"]["qvalue"]
        assert "folding-state" in qvalue["detail"], qvalue["detail"][:80]
        assert qvalue["title"] == "Native contact fraction (Q)"

    def test_the_page_draws_it(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        root = pathlib.Path(server.__file__).parent
        script = (root / "static" / "run-builder.js").read_text(encoding="utf-8")
        css = (root / "static" / "dashboard.css").read_text(encoding="utf-8")
        assert "explanations" in script
        assert "analyse-explains" in script  # the class the run page reuses
        assert ".analyse-explains" in css


class TestChoosingBetweenSeveralTrajectories:
    """A run with replicates has a production.dcd in each of them.

    Listing them by name alone is no choice at all.
    """

    @staticmethod
    def _replicates(root):
        # Sizes must actually differ for "the longest is chosen" to mean
        # anything; naming them rep1..rep3 makes len() the same for each.
        for index, name in enumerate(("rep1", "rep2", "rep3"), start=1):
            (root / name).mkdir(parents=True)
            (root / name / "production.dcd").write_bytes(b"x" * (1000 * index))
            (root / name / "topology.pdb").write_text("ATOM")
        return root

    def test_all_of_them_are_offered(self, tmp_path) -> None:
        from fastmdxplora.gui.directory_inspect import inspect_directory

        found = inspect_directory(self._replicates(tmp_path))
        assert len(found["trajectories"]) == 3

    def test_they_are_told_apart_by_where_they_sit(self, tmp_path) -> None:
        """The paths differ even where the names do not, and the page labels
        each by its path below the folder that was searched."""
        import pathlib

        from fastmdxplora.gui import server
        from fastmdxplora.gui.directory_inspect import inspect_directory

        found = inspect_directory(self._replicates(tmp_path))
        names = {pathlib.Path(t["path"]).name for t in found["trajectories"]}
        paths = {t["path"] for t in found["trajectories"]}
        assert names == {"production.dcd"}, "the fixture should share a name"
        assert len(paths) == 3, "and differ by path"

        # Where the run page lists files, it labels them by path.
        script = (pathlib.Path(server.__file__).parent / "static"
                  / "file-picker.js").read_text(encoding="utf-8")
        assert "file.path" in script, "the picker labels by name alone"

    def test_the_largest_is_chosen_but_not_forced(self, tmp_path) -> None:
        from fastmdxplora.gui.directory_inspect import inspect_directory

        import pathlib

        found = inspect_directory(self._replicates(tmp_path))
        chosen = pathlib.Path(found["suggestion"]["trajectory"])
        assert chosen.name == "production.dcd"
        assert chosen.parent.name == "rep3"
        # and the others remain on offer
        assert len(found["trajectories"]) == 3


class TestFoldersBelowTheOneYouAreLookingAt:
    """A run keeps its trajectory in simulation/, a package keeps its data in
    <name>/data/, and the folder somebody is looking at is the one above.

    Counting only what sits directly inside marked almost nothing.
    """

    def test_data_a_few_levels_down_is_found(self, tmp_path) -> None:
        from fastmdxplora.gui.browse import browse

        deep = tmp_path / "_v1" / "fastmdanalysis" / "data"
        deep.mkdir(parents=True)
        (deep / "trp_cage.dcd").write_bytes(b"x")
        (deep / "trp_cage.pdb").write_text("ATOM")

        entry = next(e for e in browse(tmp_path)["entries"] if e["name"] == "_v1")
        assert entry["trajectories"] == 1
        assert entry["structures"] == 1

    def test_it_does_not_walk_the_whole_disk(self, tmp_path) -> None:
        """Bounded, because this runs once per row of a listing."""
        from fastmdxplora.gui.browse import browse

        far = tmp_path / "archive" / "a" / "b" / "c" / "d"
        far.mkdir(parents=True)
        (far / "buried.dcd").write_bytes(b"x")

        entry = next(e for e in browse(tmp_path)["entries"]
                     if e["name"] == "archive")
        assert entry["trajectories"] == 0

    def test_it_stops_counting_once_the_answer_is_clear(self, tmp_path) -> None:
        from fastmdxplora.gui.browse import browse

        many = tmp_path / "many"
        many.mkdir()
        for i in range(60):
            (many / f"frame{i}.dcd").write_bytes(b"x")

        entry = next(e for e in browse(tmp_path)["entries"] if e["name"] == "many")
        assert entry["trajectories"] < 60
        assert entry["truncated"]


class TestBothKindsOfConfigFile:
    """Two files answer two questions, and both run.

    Left to itself the file names only what was decided, because a file
    restating forty settings the software would have chosen anyway hides the
    three that were chosen. Asked for in full it names everything, which is
    the file to keep beside a result: a default that moves in a later version
    cannot then change what the file meant.
    """

    @staticmethod
    def _state():
        return {
            "system": "1ubq",
            "output": "runs/x",
            "include": ["setup", "simulation", "analysis"],
            "analysis": {"scope": "protein"},
        }

    def test_the_short_one_names_only_what_changed(self) -> None:
        from fastmdxplora.gui.config_builder import build_config

        built = build_config(self._state())
        assert built["analysis"] == {"scope": "protein"}
        assert "setup" not in built

    def test_the_full_one_names_every_phase_that_runs(self) -> None:
        """A phase the form never touched still runs, and still uses values
        worth recording."""
        from fastmdxplora.gui.config_builder import build_config

        built = build_config(self._state(), full=True)
        for phase in ("setup", "simulation", "analysis"):
            assert built[phase], f"{phase} runs but is not recorded"
        assert built["setup"]["ph"] == 7.4
        assert built["analysis"]["scope"] == "protein"

    def test_a_phase_that_does_not_run_is_not_recorded(self) -> None:
        from fastmdxplora.gui.config_builder import build_config

        built = build_config(self._state(), full=True)
        assert "report" not in built

    def test_both_are_accepted_by_the_command_line(self, tmp_path) -> None:
        from fastmdxplora.config.loader import load_config_file, validate_config
        from fastmdxplora.gui.config_builder import config_yaml

        for index, full in enumerate((False, True)):
            written = config_yaml(self._state(), full=full)
            assert written["ok"], written["error"]
            path = tmp_path / f"config{index}.yml"
            path.write_text(written["yaml"], encoding="utf-8")
            validate_config(load_config_file(path))

    def test_they_agree_on_what_the_run_will_do(self, tmp_path) -> None:
        """The full file must not change the run, only describe it.

        Every value it adds is the value the software would have used anyway,
        so the two files have to resolve to the same settings.
        """
        from fastmdxplora.config.schema import PHASE_SCHEMAS
        from fastmdxplora.gui.config_builder import build_config

        short = build_config(self._state())
        full = build_config(self._state(), full=True)
        for phase, group in PHASE_SCHEMAS.items():
            defaults = {f.name: f.default for f in group.fields}
            for name, value in full.get(phase, {}).items():
                if name == "options":
                    # The nested per-analysis settings have no schema default
                    # to compare against; they are checked against the
                    # analyses themselves in TestTheFullConfigReachesTheNested-
                    # Settings, which is where they come from.
                    continue
                chosen = short.get(phase, {}).get(name)
                assert value == (chosen if chosen is not None else defaults[name]), (
                    f"{phase}.{name} differs between the two files"
                )

    def test_the_header_says_which_kind_it_is(self) -> None:
        from fastmdxplora.gui.config_builder import config_yaml

        assert "Only settings that differ" in config_yaml(self._state())["yaml"]
        assert "Every setting is named" in config_yaml(
            self._state(), full=True)["yaml"]

    def test_the_page_offers_the_choice(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        root = pathlib.Path(server.__file__).parent
        page = (root / "templates" / "dashboard.html").read_text(encoding="utf-8")
        script = (root / "static" / "run-builder.js").read_text(encoding="utf-8")
        assert 'id="run-full-config"' in page
        assert 'el("run-full-config")' in script


class TestItIsCalledTheGUI:
    """A dashboard is the live view inside it, not the thing itself."""

    def test_the_page_is_titled_that_way(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        page = (pathlib.Path(server.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        assert "<title>FastMDXplora GUI</title>" in page

    def test_the_config_it_writes_says_so(self) -> None:
        from fastmdxplora.gui.config_builder import config_yaml

        written = config_yaml({"output": "o", "systems": [{"system": "1ubq"}]})
        assert "FastMDXplora GUI" in written["yaml"]
        assert "dashboard" not in written["yaml"].lower()


class TestTheFullConfigReachesTheNestedSettings:
    """"Every setting" has to mean the nested ones too.

    A full config that stopped at the phase boundary recorded the scope and
    the stride and said nothing about how the clustering was done -- which is
    where the decisions that change a result actually live.
    """

    @staticmethod
    def _clustering():
        return {
            "system": "x", "output": "o", "include": ["analysis"],
            "analysis": {"scope": "protein", "include": "cluster"},
        }

    def test_the_analysis_settings_are_recorded(self) -> None:
        from fastmdxplora.gui.config_builder import build_config

        built = build_config(self._clustering(), full=True)
        cluster = built["analysis"]["options"]["cluster"]
        assert {"n_clusters", "linkage", "features", "methods"} <= set(cluster)

    def test_they_are_the_values_the_run_would_use(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.cluster import Cluster
        from fastmdxplora.gui.config_builder import build_config

        recorded = build_config(self._clustering(), full=True)
        cluster = recorded["analysis"]["options"]["cluster"]
        running = Cluster()
        assert cluster["n_clusters"] == running.n_clusters
        assert cluster["linkage"] == running.linkage
        assert cluster["features"] == running.features
        assert cluster["methods"] == running.methods

    def test_a_choice_still_wins_over_the_default(self) -> None:
        from fastmdxplora.gui.config_builder import build_config

        state = self._clustering()
        state["analysis"]["options"] = {"cluster": {"n_clusters": "8"}}
        cluster = build_config(state, full=True)["analysis"]["options"]["cluster"]
        assert cluster["n_clusters"] == 8
        assert cluster["linkage"] == "average"     # untouched, still recorded

    def test_a_number_from_a_form_is_recorded_as_a_number(self) -> None:
        """Left as text the file would say n_clusters: '8', which runs -- the
        analysis casts it -- but reads as though somebody meant the string."""
        from fastmdxplora.gui.config_builder import build_config

        state = self._clustering()
        state["analysis"]["options"] = {"cluster": {"n_clusters": "8",
                                                    "eps": "0.35"}}
        cluster = build_config(state, full=True)["analysis"]["options"]["cluster"]
        assert isinstance(cluster["n_clusters"], int)
        assert isinstance(cluster["eps"], float)

    def test_only_the_chosen_analyses_are_recorded(self) -> None:
        from fastmdxplora.gui.config_builder import build_config

        built = build_config(self._clustering(), full=True)
        assert set(built["analysis"]["options"]) == {"cluster"}

    def test_the_short_config_leaves_them_out(self) -> None:
        from fastmdxplora.gui.config_builder import build_config

        built = build_config(self._clustering())
        assert "options" not in built["analysis"]

    def test_it_still_validates(self, tmp_path) -> None:
        from fastmdxplora.config.loader import load_config_file, validate_config
        from fastmdxplora.gui.config_builder import config_yaml

        written = config_yaml(self._clustering(), full=True)
        assert written["ok"], written["error"]
        path = tmp_path / "full.yml"
        path.write_text(written["yaml"], encoding="utf-8")
        validate_config(load_config_file(path))


class TestWhatTheGUIOpensOn:
    """With nothing running, the thing to do is start something.

    The overview was the only page left visible in the markup, so it was what
    the GUI opened on -- an overview of a run, before any state had loaded and
    usually when there was no run at all. It now opens on the page that builds
    one.
    """

    @staticmethod
    def _page():
        import pathlib

        from fastmdxplora.gui import server

        return (pathlib.Path(server.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")

    def test_only_one_page_is_visible_before_anything_loads(self) -> None:
        import re

        sections = re.findall(
            r'<section class="page" data-page="([^"]+)"([^>]*)>', self._page())
        visible = [name for name, attrs in sections if "hidden" not in attrs]
        assert visible == ["run"], f"visible at load: {visible}"

    def test_the_router_decides_once_it_knows(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "dashboard.js").read_text(encoding="utf-8")
        # A run to look at, a page asked for by name, or nothing yet.
        assert 'else if (activeRun) navigate("overview");' in script
        assert 'else navigate("run");' in script


class TestOpeningTheGUIWithNothingRunning:
    """`fastmdx gui` with no --output has no run to watch.

    The working directory is merely where the command was typed. Passing it as
    the active run made the GUI report one, and the router quite correctly
    showed the overview of it -- an overview of nothing. Marking the overview
    hidden did not help, because the router put it back a moment later.
    """

    def test_the_command_says_there_is_no_run(self, tmp_path, monkeypatch) -> None:
        import fastmdxplora.gui.server as server_module
        from fastmdxplora.cli.main import _build_parser

        seen = {}

        def _capture(**kwargs):
            seen.update(kwargs)

        monkeypatch.setattr(server_module, "serve_dashboard", _capture)
        monkeypatch.chdir(tmp_path)

        # `fastmdxplora.cli.main` is a function re-exported by the package,
        # so importing it as a module gets the function instead.
        from fastmdxplora.cli.main import _cmd_gui

        args = _build_parser().parse_args(["gui", "--no-browser"])
        _cmd_gui(args)
        assert seen["home_mode"] is True, (
            "the working directory was passed as an active run"
        )

    def test_an_output_given_is_a_run_to_watch(self, tmp_path, monkeypatch) -> None:
        import fastmdxplora.gui.server as server_module
        from fastmdxplora.cli.main import _build_parser

        seen = {}
        monkeypatch.setattr(server_module, "serve_dashboard",
                            lambda **kw: seen.update(kw))

        from fastmdxplora.cli.main import _cmd_gui

        args = _build_parser().parse_args(
            ["gui", "--no-browser", "--output", str(tmp_path)])
        _cmd_gui(args)
        assert seen["home_mode"] is False
        assert seen["output"] == tmp_path

    def test_the_runtime_reports_no_run_in_that_mode(self, tmp_path) -> None:
        from fastmdxplora.gui.server import DashboardRuntime

        runtime = DashboardRuntime(
            workspace_root=tmp_path, exploration_root=tmp_path.parent,
            active_root=None,
        )
        assert runtime.snapshot()["active_run"] is None

    def test_and_reports_one_when_there_is_one(self, tmp_path) -> None:
        from fastmdxplora.gui.server import DashboardRuntime

        runtime = DashboardRuntime(
            workspace_root=tmp_path, exploration_root=tmp_path.parent,
            active_root=tmp_path,
        )
        assert runtime.snapshot()["active_run"] == str(tmp_path.resolve())


class TestStartingAnAnalysisFromThePage:
    """Run here, without a form having been wired for this kind of run.

    The existing launch translates a fixed set of form fields into a fixed set
    of flags, which is why the phases were pinned at setup and simulation.
    Running from a config instead means anything a config can say, the browser
    can start -- and the file it ran from is written beside the results.
    """

    @staticmethod
    def _analysis_state(output="an_analysis"):
        return {
            "output": output,
            "include": ["analysis"],
            "systems": [{"system": "top.pdb"}],
            "analysis": {"trajectory": "x.dcd", "topology": "top.pdb",
                         "include": "rmsd"},
        }

    @staticmethod
    def _runtime(tmp_path):
        from fastmdxplora.gui.exploration import DashboardRuntime

        return DashboardRuntime(
            workspace_root=tmp_path / "ws",
            exploration_root=tmp_path,
            active_root=None,
        )

    def test_it_starts_and_says_where(self, tmp_path) -> None:
        runtime = self._runtime(tmp_path)
        try:
            started = runtime.launch_from_config(self._analysis_state())
            assert started["ok"], started.get("error")
            assert started["launched"]
            assert started["output"].endswith("an_analysis")
        finally:
            runtime.stop()

    def test_the_config_is_written_beside_the_results(self, tmp_path) -> None:
        import pathlib

        runtime = self._runtime(tmp_path)
        try:
            started = runtime.launch_from_config(self._analysis_state())
            written = pathlib.Path(started["config_path"])
            assert written.exists()
            assert written.parent == pathlib.Path(started["output"])
        finally:
            runtime.stop()

    def test_it_runs_that_config_and_not_a_flag_list(self, tmp_path) -> None:
        runtime = self._runtime(tmp_path)
        try:
            started = runtime.launch_from_config(self._analysis_state())
            assert "--config" in started["command"]
            assert started["config_path"] in started["command"]
        finally:
            runtime.stop()

    def test_a_second_run_is_refused_while_one_is_going(self, tmp_path) -> None:
        runtime = self._runtime(tmp_path)
        try:
            first = runtime.launch_from_config(self._analysis_state("first"))
            assert first["ok"]
            second = runtime.launch_from_config(self._analysis_state("second"))
            assert not second["ok"]
            assert "already running" in second["error"]
        finally:
            runtime.stop()

    def test_results_cannot_land_outside_the_workspace(self, tmp_path) -> None:
        """The GUI writes where it was started, not wherever it is told.

        A name with .. in it is flattened rather than refused, so there is
        nothing to reject: what matters is where the run actually lands.
        """
        import pathlib

        runtime = self._runtime(tmp_path)
        try:
            started = runtime.launch_from_config(
                self._analysis_state("../../somewhere_else"))
            assert started["ok"]
            landed = pathlib.Path(started["output"])
            assert landed.is_relative_to(tmp_path), landed
        finally:
            runtime.stop()

    def test_the_page_and_the_server_are_wired(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        root = pathlib.Path(server.__file__).parent
        source = pathlib.Path(server.__file__).read_text(encoding="utf-8")
        script = (root / "static" / "run-builder.js").read_text(encoding="utf-8")
        assert '"/api/run"' in source
        assert '"/api/run"' in script

    def test_starting_a_run_is_a_control_and_guarded_like_one(self) -> None:
        """Disabled when the GUI is not on loopback, like the other controls.

        Starting a run is the same kind of act as starting an exploration, so
        it belongs behind the same door.
        """
        import pathlib
        import re

        from fastmdxplora.gui import server

        source = pathlib.Path(server.__file__).read_text(encoding="utf-8")
        guarded = re.search(
            r"if not allow_control and path in \{([^}]*)\}", source)
        assert guarded, "the control guard has moved; check this test"
        assert '"/api/run"' in guarded.group(1)


class TestATimelineShowsOnlyWhatCanHappen:
    """A stage greyed out forever reads exactly like a run that stalled.

    The timeline lists seven stages. An analysis of a trajectory that already
    exists reaches one of them; the other six would sit waiting for a
    minimization and a production run that were never asked for.
    """

    def test_an_analysis_output_has_one_stage(self, tmp_path) -> None:
        from fastmdxplora.gui.telemetry import run_stages

        (tmp_path / "exploration.yml").write_text(
            "include: [analysis]\n", encoding="utf-8")
        assert run_stages(tmp_path) == ["analysis"]

    def test_a_full_run_has_all_of_them(self, tmp_path) -> None:
        from fastmdxplora.gui.telemetry import run_stages

        (tmp_path / "exploration.yml").write_text(
            "include: [setup, simulation, analysis, report]\n", encoding="utf-8")
        assert run_stages(tmp_path) == [
            "setup", "minimization", "nvt", "npt", "production",
            "analysis", "report",
        ]

    def test_the_stages_stay_in_the_order_they_happen(self, tmp_path) -> None:
        """Whatever order the config happens to list them in."""
        from fastmdxplora.gui.telemetry import run_stages

        (tmp_path / "exploration.yml").write_text(
            "include: [report, analysis, setup]\n", encoding="utf-8")
        assert run_stages(tmp_path) == ["setup", "analysis", "report"]

    def test_a_config_describing_a_phase_says_nothing_about_the_others(
        self, tmp_path
    ) -> None:
        """Phase blocks used to be read as the plan. But a block means the
        phase was configured, not that it was included -- and since the file
        lands in the output directory when the run ends, the timeline showed
        seven stages throughout and dropped to five the moment it finished.

        That happened because `write_resolved_config` wrote only phases with
        non-empty options, so a run whose analysis and report took defaults
        left no trace of them. It now names every setting every phase used,
        so the blocks are always all four and say even less about what ran.
        `include` and `exclude` are the only keys that answer this, which is
        the rule the test below gives.
        """
        from fastmdxplora.gui.telemetry import run_phases, run_stages

        (tmp_path / "exploration.yml").write_text(
            "setup: {ph: 7.4}\nanalysis: {scope: protein}\n", encoding="utf-8")
        assert run_phases(tmp_path) == []
        assert len(run_stages(tmp_path)) == 7

    def test_nothing_to_go_on_means_show_everything(self, tmp_path) -> None:
        """An older run, or one started outside the GUI. Hiding a stage that
        turns out to run is the worse mistake."""
        from fastmdxplora.gui.telemetry import run_stages

        assert len(run_stages(tmp_path)) == 7

    def test_an_unreadable_config_is_not_fatal(self, tmp_path) -> None:
        from fastmdxplora.gui.telemetry import run_stages

        (tmp_path / "exploration.yml").write_text(
            "include: [analysis\n  broken: [", encoding="utf-8")
        assert len(run_stages(tmp_path)) == 7

    def test_the_status_endpoint_reports_them(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        source = pathlib.Path(server.__file__).read_text(encoding="utf-8")
        assert '"stages": run_stages(root)' in source
        assert "run_stages," in source, "imported, or it fails when called"

    def test_the_page_hides_the_rest(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "dashboard.js").read_text(encoding="utf-8")
        assert "state.stages" in script
        assert "reachable.includes(stage)" in script


class TestWhereTheResultsGo:
    """A results folder may be a name or a path, and browsing gives a path.

    Running an absolute path through the slug turned /Users/someone/work into
    a folder called Users_someone_work inside the launch directory -- neither
    where they pointed nor anywhere they would think to look.
    """

    @staticmethod
    def _runtime(tmp_path):
        from fastmdxplora.gui.exploration import DashboardRuntime

        return DashboardRuntime(
            workspace_root=tmp_path / "ws", exploration_root=tmp_path,
            active_root=None,
        )

    @staticmethod
    def _state(output):
        return {
            "output": output, "include": ["analysis"],
            "systems": [{"system": "t.pdb"}],
            "analysis": {"trajectory": "x.dcd", "topology": "t.pdb",
                         "include": "rmsd"},
        }

    def test_an_absolute_path_is_where_the_results_land(self, tmp_path) -> None:
        import pathlib

        runtime = self._runtime(tmp_path)
        wanted = tmp_path / "somewhere" / "else"
        try:
            started = runtime.launch_from_config(self._state(str(wanted)))
            assert started["ok"], started.get("error")
            assert pathlib.Path(started["output"]) == wanted.resolve()
        finally:
            runtime.stop()

    def test_a_plain_name_lands_beside_the_other_runs(self, tmp_path) -> None:
        import pathlib

        runtime = self._runtime(tmp_path)
        try:
            started = runtime.launch_from_config(self._state("my_analysis"))
            assert pathlib.Path(started["output"]) == (
                tmp_path / "my_analysis").resolve()
        finally:
            runtime.stop()

    def test_left_empty_it_has_a_default(self, tmp_path) -> None:
        import pathlib

        runtime = self._runtime(tmp_path)
        try:
            started = runtime.launch_from_config(self._state(""))
            # Named by the one rule: the system and a timestamp, so a second
            # run does not collide and the folder says what it holds.
            assert re.fullmatch(r"fastmdxplora_.+_study_\d{14}", pathlib.Path(started["output"]).name)
        finally:
            runtime.stop()

    def test_the_page_shows_that_default(self) -> None:
        """The placeholder says what happens if the box is left alone."""
        import pathlib

        from fastmdxplora.gui import server

        page = (pathlib.Path(server.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        assert 'id="run-output"' in page
        marker = page[page.index('id="run-output"'):][:220]
        assert 'placeholder="fastmdxplora_<system>_study_<timestamp>"' in marker


class TestPanelsForPhasesThatAreNotRunning:
    """A card announcing that the simulation became numerically unstable is
    not merely useless on an analysis run -- it is alarming, and it is about a
    simulation that was never asked for."""

    def test_the_simulation_panels_declare_what_they_need(self) -> None:
        import pathlib
        import re

        from fastmdxplora.gui import server

        page = (pathlib.Path(server.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        # The status hero is gone -- the sidebar's status line says it on
        # every page. The health card stays, and the live facts and the
        # charts grid declare the same need, so nothing that reads the
        # simulation shows for a study that has none.
        for element_id in ("hero-health",):
            block = re.search(rf'<div[^>]*id="{element_id}"[^>]*>', page)
            assert block, f"{element_id} is gone; check this test"
            assert 'data-needs-phase="simulation"' in block.group(0)
        for cls in ("overview-facts card", "overview-stack"):
            block = re.search(rf'<div class="{cls}"[^>]*>', page)
            assert block, cls
            assert 'data-needs-phase="simulation"' in block.group(0)

    def test_the_page_hides_them_when_it_knows(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "dashboard.js").read_text(encoding="utf-8")
        assert "applyPhaseVisibility" in script
        assert "data-needs-phase" in script

    def test_and_hides_nothing_when_it_does_not(self) -> None:
        """An older run says nothing about its phases; hiding a panel that
        turns out to matter is the worse mistake."""
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "dashboard.js").read_text(encoding="utf-8")
        block = script[script.index("function applyPhaseVisibility"):][:700]
        assert "if (!phases || !phases.length)" in block
        assert "element.hidden = false;" in block


class TestOnePageBuildsAnyRun:
    """Two pages were the same thing.

    One started from a protein and one from a trajectory, and both wrote a
    config and ran it -- differing only in which phases the config named. Built
    separately, one of them offered eleven of the eighty-three settings that
    exist while the other offered all of them.
    """

    @staticmethod
    def _files():
        import pathlib

        from fastmdxplora.gui import server

        root = pathlib.Path(server.__file__).parent
        return (
            (root / "templates" / "dashboard.html").read_text(encoding="utf-8"),
            (root / "static" / "run-builder.js").read_text(encoding="utf-8"),
        )

    def test_the_page_exists_and_leads(self) -> None:
        page, _ = self._files()
        assert 'data-page="run"' in page
        assert 'data-view-link="run"' in page

    def test_it_is_what_the_gui_opens_on(self) -> None:
        import pathlib
        import re

        from fastmdxplora.gui import server

        page, _ = self._files()
        sections = re.findall(
            r'<section class="page" data-page="([^"]+)"([^>]*)>', page)
        visible = [name for name, attrs in sections if "hidden" not in attrs]
        assert visible == ["run"]

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "dashboard.js").read_text(encoding="utf-8")
        assert 'else navigate("run");' in script

    def test_every_phase_can_be_left_out(self) -> None:
        """A run that only analyses is a run with one phase ticked."""
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        _, script = self._files()
        for phase in PHASE_SCHEMAS:
            assert f'name: "{phase}"' in script, f"{phase} cannot be chosen"

    def test_the_settings_come_from_the_schema(self) -> None:
        _, script = self._files()
        assert "/api/schema" in script
        assert "state.schema.phases" in script

    def test_no_setting_is_named_in_the_page(self) -> None:
        """The moment one is, the page has a list to keep in step -- which is
        how the old form came to offer eleven of eighty-three."""
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        _, script = self._files()
        # Fields the page supplies itself from earlier answers, or that
        # another control on the page owns, are named on purpose; everything
        # else must come from the schema. `include` and `exclude` are the
        # Measurements panel's -- offered in the options grid as well, two
        # controls wrote one key and the grid's value won silently.
        supplied = {
            "trajectory", "topology", "system", "output", "include", "exclude",
        }
        named = [
            field.name
            for group in PHASE_SCHEMAS.values()
            for field in group.fields
            if field.name not in supplied
            and f'"{field.name}"' in script
        ]
        assert not named, f"these are written into the page: {named}"

    def test_no_analysis_is_named_either(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.orchestrator import available_analyses

        _, script = self._files()
        named = [n for n in available_analyses() if f'"{n}"' in script]
        assert not named, f"these are written into the page: {named}"

    def test_it_writes_a_config_and_can_run_it(self) -> None:
        _, script = self._files()
        assert "/api/config" in script
        assert "/api/run" in script

    def test_and_can_be_put_back(self) -> None:
        _, script = self._files()
        block = script[script.index("function resetEverything"):][:800]
        for cleared in ("state.values = {}", "state.analyses.clear()",
                        "state.analysisOptions = {}", "state.open.clear()"):
            assert cleared in block, f"{cleared} is not cleared"

    def test_the_styles_it_uses_are_defined(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        css = (pathlib.Path(server.__file__).parent / "static"
               / "dashboard.css").read_text(encoding="utf-8")
        for name in ("run-start", "run-start-choice", "run-phase",
                     "run-section", "run-section-body"):
            assert f".{name}" in css, f"{name} is used but never styled"

    def test_the_script_is_loaded(self) -> None:
        page, _ = self._files()
        assert "/static/run-builder.js" in page


class TestTheStartingPointIsOneChoice:
    """What you have decides what can happen, so it is asked once.

    Two buttons could not grow a third option without becoming a row of
    buttons; a control with options can, and the third is worth having: a
    config written earlier, edited by hand, or carried back from a cluster.
    """

    @staticmethod
    def _files():
        import pathlib

        from fastmdxplora.gui import server

        root = pathlib.Path(server.__file__).parent
        return (
            (root / "templates" / "dashboard.html").read_text(encoding="utf-8"),
            (root / "static" / "run-builder.js").read_text(encoding="utf-8"),
        )

    def test_it_is_a_single_control(self) -> None:
        page, _ = self._files()
        assert '<select id="run-start">' in page

    def test_the_description_belongs_to_the_option(self) -> None:
        """It is what the option means, so it changes as the option does."""
        page, script = self._files()
        assert 'id="run-start-detail"' in page
        assert "STARTING_POINTS[state.start].detail" in script

    def test_a_config_already_written_is_one_of_them(self) -> None:
        _, script = self._files()
        assert "config: {" in script or 'config:' in script
        assert "/api/check-config" in script

    def test_a_config_is_checked_before_anything_runs(self, tmp_path) -> None:
        from fastmdxplora.gui.config_builder import check_config_file

        good = tmp_path / "good.yml"
        good.write_text(
            "output: o\nsystems: [{system: 1ubq}]\ninclude: [setup]\n",
            encoding="utf-8")
        verdict = check_config_file(str(good))
        assert verdict["ok"] and verdict["phases"] == ["setup"]

    def test_a_hand_edited_file_reports_its_syntax_error(self, tmp_path) -> None:
        """Which is the most common thing to come back with."""
        from fastmdxplora.gui.config_builder import check_config_file

        broken = tmp_path / "broken.yml"
        broken.write_text("output: o\n  systems: [oops\n", encoding="utf-8")
        verdict = check_config_file(str(broken))
        assert not verdict["ok"] and "not valid YAML" in verdict["error"]

    def test_a_setting_that_does_not_exist_is_reported(self, tmp_path) -> None:
        from fastmdxplora.gui.config_builder import check_config_file

        wrong = tmp_path / "wrong.yml"
        wrong.write_text("output: o\nnot_a_phase: {x: 1}\n", encoding="utf-8")
        assert not check_config_file(str(wrong))["ok"]

    def test_a_missing_file_says_so(self, tmp_path) -> None:
        from fastmdxplora.gui.config_builder import check_config_file

        verdict = check_config_file(str(tmp_path / "nowhere.yml"))
        assert not verdict["ok"] and "No such file" in verdict["error"]


class TestWhatAStartingPointCanOffer:
    """A trajectory is already the result of setting up and simulating.

    Offering those again is offering to do work that cannot connect to the
    frames already recorded: there is no supported way to continue a run from
    a trajectory, only from a checkpoint.
    """

    @staticmethod
    def _script():
        import pathlib

        from fastmdxplora.gui import server

        return (pathlib.Path(server.__file__).parent / "static"
                / "run-builder.js").read_text(encoding="utf-8")

    def test_a_trajectory_offers_only_what_it_can_feed(self) -> None:
        script = self._script()
        block = script[script.index("trajectory: {"):][:600]
        assert 'offers: ["analysis", "report"]' in block

    def test_a_structure_offers_everything(self) -> None:
        script = self._script()
        block = script[script.index("structure: {"):][:600]
        assert '"setup", "simulation", "analysis", "report"' in block

    def test_the_unavailable_ones_are_shown_and_explained(self) -> None:
        """Rather than removed, so what the software does stays visible."""
        import pathlib

        from fastmdxplora.gui import server

        script = self._script()
        assert "box.disabled = !available" in script
        assert "Nothing to act on" in script
        css = (pathlib.Path(server.__file__).parent / "static"
               / "dashboard.css").read_text(encoding="utf-8")
        assert '.run-phase[data-available="false"]' in css


class TestNothingPromisesASequence:
    """The setup phase refuses one: sequence-to-structure is not implemented.

    Offering it anywhere is offering a capability that raises.
    """

    def test_the_setup_phase_still_refuses_it(self, tmp_path) -> None:

        from fastmdxplora.setup.pipeline import _resolve_input

        with pytest.raises(NotImplementedError, match="structure predictor"):
            _resolve_input("ACDEFGHIKLMNPQRSTVWY", "sequence", tmp_path)

    def test_the_page_does_not_offer_it(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        page = (pathlib.Path(server.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        marker = page[page.index('id="run-system"'):][:220]
        assert "sequence" not in marker.lower()

    def test_nor_does_the_config_template(self) -> None:
        from fastmdxplora.config.generate import generate_template

        assert "or sequence" not in generate_template()


class TestTheWordsMatchTheSoftware:
    """Setup is the phase's name; preparing is what a person does beforehand.
    Analyze is spelled the way the command spells it."""

    def test_the_first_phase_is_called_setup(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        assert 'label: "Setup"' in script
        assert 'label: "Prepare"' not in script

    def test_analyze_is_spelled_as_the_command_spells_it(self) -> None:
        import pathlib

        from fastmdxplora.cli.main import _build_parser
        from fastmdxplora.gui import server

        subcommands = _build_parser()._subparsers._group_actions[0].choices
        assert "analyze" in subcommands

        root = pathlib.Path(server.__file__).parent
        script = (root / "static" / "run-builder.js").read_text(encoding="utf-8")
        page = (root / "templates" / "dashboard.html").read_text(encoding="utf-8")
        assert 'label: "Analyze"' in script
        # "Analyses" is the plural of analysis and correct either way; it is
        # the verb that had drifted from the command.
        assert "Analyse " not in page and "Analyse<" not in page

    def test_the_results_folder_is_named_for_the_software(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        root = pathlib.Path(server.__file__).parent
        page = (root / "templates" / "dashboard.html").read_text(encoding="utf-8")
        script = (root / "static" / "run-builder.js").read_text(encoding="utf-8")
        assert 'placeholder="fastmdxplora_<system>_study_<timestamp>"' in page
        # The default is built by a function now -- timestamped, like the
        # CLI's -- so a second run does not collide with the first. The
        # name is still the software's.
        # The browser no longer names the folder; the server does, by one
        # rule. The note shows the pattern.
        assert '"fastmdxplora_output_"' not in script
        assert '_study_<timestamp>' in script


class TestEveryPathFieldCanBeBrowsed:
    """A browser will not offer a dialog for a path the server has to open.

    So the walking is done by the server and drawn by the page -- and every
    field that wants a path gets the same control, rather than one page having
    a picker and the rest asking people to type.
    """

    @staticmethod
    def _files():
        import pathlib

        from fastmdxplora.gui import server

        root = pathlib.Path(server.__file__).parent
        return (
            (root / "templates" / "dashboard.html").read_text(encoding="utf-8"),
            (root / "static" / "file-picker.js").read_text(encoding="utf-8"),
        )

    def test_no_path_field_is_left_without_one(self) -> None:
        import re

        page, _ = self._files()
        # Anything asking for a path, by the words its placeholder uses.
        asking = re.findall(
            r'<input[^>]*id="([^"]+)"[^>]*placeholder="([^"]*)"[^>]*>', page)
        wants_path = [
            (element_id, hint) for element_id, hint in asking
            if ("/" in hint or hint.endswith(("_output", "_run")))
            # A URL has slashes and is not a path. The server cannot walk
            # to http://localhost:11434/v1, so offering a picker for it
            # would be offering a control that could not work. Added when
            # the agent panel's base-URL field tripped this: the rule was
            # right about the shape and "contains a slash" is a proxy for
            # "is a path" that a URL breaks.
            and not hint.startswith(("http://", "https://"))
        ]
        assert wants_path, "no path fields found; check this test"
        marked = set(re.findall(r'id="([^"]+)"[^>]*data-picks=', page)) | set(
            re.findall(r'data-picks="[^"]*"[^>]*id="([^"]+)"', page))
        missing = [i for i, _ in wants_path if i not in marked]
        assert not missing, f"these ask for a path and offer no picker: {missing}"

    def test_a_url_is_not_a_path(self) -> None:
        """The exclusion above, asserted rather than trusted.

        It would be an easy place to hide a real path field: give it a
        placeholder starting with http:// and the rule stops looking. So
        check that a field asking for an ordinary path is still caught
        whatever else is in the page.
        """
        import re

        page, _ = self._files()
        made_up = (
            '<input id="made-up-path" placeholder="/home/you/study.pdb">')
        asking = re.findall(
            r'<input[^>]*id="([^"]+)"[^>]*placeholder="([^"]*)"[^>]*>',
            page + made_up)
        caught = [
            element_id for element_id, hint in asking
            if ("/" in hint or hint.endswith(("_output", "_run")))
            and not hint.startswith(("http://", "https://"))
        ]
        assert "made-up-path" in caught
        assert "agent-base-url" not in caught

    def test_a_new_field_needs_no_wiring(self) -> None:
        """data-picks is the whole of it, so the next one cannot be forgotten
        in the way these were."""
        _, script = self._files()
        assert "input[data-picks]" in script

    def test_it_can_pick_a_file_not_only_a_folder(self) -> None:
        _, script = self._files()
        assert 'state.mode !== "folder"' in script
        assert "else select(path);" in script

    def test_the_page_is_told_the_value_changed(self) -> None:
        """A value set from code fires neither event on its own, and the pages
        watch their inputs to decide what to enable."""
        _, script = self._files()
        assert 'new Event("input"' in script
        assert 'new Event("change"' in script

    def test_only_the_files_being_looked_for_are_listed(self, tmp_path) -> None:
        from fastmdxplora.gui.browse import browse

        (tmp_path / "run.dcd").write_bytes(b"x")
        (tmp_path / "top.pdb").write_text("ATOM")
        (tmp_path / "config.yml").write_text("a: 1")
        (tmp_path / "notes.txt").write_text("hello")

        assert [f["name"] for f in browse(tmp_path, "trajectory")["files"]] == ["run.dcd"]
        assert [f["name"] for f in browse(tmp_path, "structure")["files"]] == ["top.pdb"]
        assert [f["name"] for f in browse(tmp_path, "config")["files"]] == ["config.yml"]

    def test_asking_for_a_folder_lists_no_files(self, tmp_path) -> None:
        from fastmdxplora.gui.browse import browse

        (tmp_path / "run.dcd").write_bytes(b"x")
        (tmp_path / "runs").mkdir()
        listing = browse(tmp_path)
        assert listing["files"] == []
        assert [e["name"] for e in listing["entries"]] == ["runs"]

    def test_the_endpoint_takes_the_kind(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        source = pathlib.Path(server.__file__).read_text(encoding="utf-8")
        assert 'query.get("kind"' in source

    def test_there_is_only_one_picker(self) -> None:
        """It was written into the analyse page, and the moment a second page
        needed one the markup was duplicated."""
        import pathlib

        from fastmdxplora.gui import server

        root = pathlib.Path(server.__file__).parent
        page = (root / "templates" / "dashboard.html").read_text(encoding="utf-8")
        # The page that carried its own is gone; only the shared one is left.
        assert "analyse-picker" not in page
        assert not (root / "static" / "analysis-builder.js").exists()

    def test_the_markup_still_balances(self) -> None:
        page, _ = self._files()
        assert page.count("<div") == page.count("</div>")
        assert page.count("<section") == page.count("</section>")


class TestAConfigYouAlreadyHave:
    """Three things to do with one, and none of them writes to it.

    The file may be committed beside a paper, shared with somebody, or the
    record of a run that already happened. It can be checked, run as it
    stands, or opened into the form and changed -- and a change is saved as a
    new file, because the one on disk is somebody's.
    """

    @staticmethod
    def _config(tmp_path, name="study.yml"):
        path = tmp_path / name
        path.write_text(
            "output: runs/x\n"
            "include: [analysis]\n"
            "systems: [{system: top.pdb, id: mine}]\n"
            "analysis:\n"
            "  trajectory: /data/prod.dcd\n"
            "  topology: /data/top.pdb\n"
            "  scope: protein\n"
            "  include: [rmsd, cluster]\n"
            "  options: {cluster: {n_clusters: 8}}\n",
            encoding="utf-8")
        return path

    def test_it_can_be_checked(self, tmp_path) -> None:
        from fastmdxplora.gui.config_builder import check_config_file

        verdict = check_config_file(str(self._config(tmp_path)))
        assert verdict["ok"] and verdict["phases"] == ["analysis"]

    def test_it_can_be_opened_into_the_form(self, tmp_path) -> None:
        from fastmdxplora.gui.config_builder import load_config_into_state

        loaded = load_config_into_state(str(self._config(tmp_path)))
        assert loaded["ok"]
        got = loaded["state"]
        assert got["trajectory"] == "/data/prod.dcd"
        assert got["analyses"] == ["rmsd", "cluster"]
        assert got["analysis_options"]["cluster"]["n_clusters"] == 8

    def test_opening_works_out_where_it_was_meant_to_start(self, tmp_path) -> None:
        """A config naming a trajectory was written to analyse one."""
        from fastmdxplora.gui.config_builder import load_config_into_state

        loaded = load_config_into_state(str(self._config(tmp_path)))
        assert loaded["state"]["start"] == "trajectory"

        structure = tmp_path / "build.yml"
        structure.write_text(
            "output: o\ninclude: [setup, simulation]\n"
            "systems: [{system: 1ubq}]\n", encoding="utf-8")
        assert load_config_into_state(str(structure))["state"]["start"] == "structure"

    def test_it_can_be_run_exactly_as_it_stands(self, tmp_path) -> None:
        from fastmdxplora.gui.exploration import DashboardRuntime

        config = self._config(tmp_path)
        original = config.read_text(encoding="utf-8")
        runtime = DashboardRuntime(
            workspace_root=tmp_path / "ws", exploration_root=tmp_path,
            active_root=None)
        try:
            started = runtime.launch_existing_config(str(config))
            assert started["ok"], started.get("error")
            assert started["config_path"] == str(config)
            assert "--config" in started["command"]
            assert str(config) in started["command"]
        finally:
            runtime.stop()
        assert config.read_text(encoding="utf-8") == original, (
            "running it rewrote the file"
        )

    def test_its_results_land_beside_it(self, tmp_path) -> None:
        """A config kept with its data leaves its results there too."""
        import pathlib

        from fastmdxplora.gui.exploration import DashboardRuntime

        config = self._config(tmp_path)
        runtime = DashboardRuntime(
            workspace_root=tmp_path / "ws", exploration_root=tmp_path,
            active_root=None)
        try:
            started = runtime.launch_existing_config(str(config))
            assert pathlib.Path(started["output"]).name == "study_output"
            assert pathlib.Path(started["output"]).parent == tmp_path
        finally:
            runtime.stop()

    def test_a_config_that_will_not_run_starts_nothing(self, tmp_path) -> None:
        from fastmdxplora.gui.exploration import DashboardRuntime

        broken = tmp_path / "broken.yml"
        broken.write_text("output: o\n  systems: [oops\n", encoding="utf-8")
        runtime = DashboardRuntime(
            workspace_root=tmp_path / "ws", exploration_root=tmp_path,
            active_root=None)
        try:
            started = runtime.launch_existing_config(str(broken))
            assert not started["ok"]
            assert "not valid YAML" in started["error"]
        finally:
            runtime.stop()

    def test_the_page_offers_all_three(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        root = pathlib.Path(server.__file__).parent
        page = (root / "templates" / "dashboard.html").read_text(encoding="utf-8")
        script = (root / "static" / "run-builder.js").read_text(encoding="utf-8")
        for element_id in ("run-check-config", "run-open-config", "run-as-is"):
            assert f'id="{element_id}"' in page, f"{element_id} is not offered"
        assert "/api/load-config" in script
        assert "/api/run-config" in script

    def test_the_page_says_the_original_is_left_alone(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        page = (pathlib.Path(server.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        assert "saved as" in page and "new file" in page

    def test_running_one_is_a_control_and_guarded_like_one(self) -> None:
        import pathlib
        import re

        from fastmdxplora.gui import server

        source = pathlib.Path(server.__file__).read_text(encoding="utf-8")
        guarded = re.search(
            r"if not allow_control and path in \{([^}]*)\}", source)
        assert guarded and '"/api/run-config"' in guarded.group(1)


class TestTheWordsOnTheRunPage:
    """What a control says it does, and where a result will actually go."""

    @staticmethod
    def _files():
        import pathlib

        from fastmdxplora.gui import server

        root = pathlib.Path(server.__file__).parent
        return (
            (root / "templates" / "dashboard.html").read_text(encoding="utf-8"),
            (root / "static" / "run-builder.js").read_text(encoding="utf-8"),
        )

    def test_the_navigation_says_what_the_page_says(self) -> None:
        page, _ = self._files()
        assert "<span>Builder</span>" in page
        assert "New run" not in page

    def test_the_results_note_gives_the_path(self) -> None:
        """Saying "a name lands in /somewhere" left the reader to join the two
        halves themselves."""
        _, script = self._files()
        assert "Results will be saved in" in script
        assert "A name lands in" not in script

    def test_it_joins_the_folder_and_the_name(self) -> None:
        _, script = self._files()
        block = script[script.index("function describeOutput"):][:600]
        assert "defaultOutput()" in block, "the default is not shown"
        assert "absolute" in block, "an absolute path is not used as given"

    def test_the_structure_field_asks_for_what_it_accepts(self) -> None:
        """Not for one particular protein."""
        page, _ = self._files()
        field = page[page.index('id="run-system"'):][:220]
        assert "PDB ID" in field
        assert "1L2Y" not in field

    def test_the_structure_beside_a_trajectory_is_found_without_asking(
        self,
    ) -> None:
        """The button that did this said "Find beside it", which explained
        nothing. Choosing a trajectory now does it."""
        page, script = self._files()
        assert "Find beside it" not in page
        assert 'id="run-look"' not in page
        assert "function findStructureBeside" in script
        assert 'trajectory.addEventListener("change", findStructureBeside)' in script

    def test_it_does_not_overwrite_a_structure_already_given(self) -> None:
        _, script = self._files()
        block = script[script.index("function findStructureBeside"):][:500]
        assert "topology.value.trim()" in block, (
            "it would replace a structure the person had already chosen"
        )


class TestTheOldPagesAreGone:
    """One page builds a run, and it is the only one.

    Keeping the two it replaced would have left three ways to start the same
    thing, two of them worse -- which is the arrangement this set out to end,
    not a safety net for it.
    """

    @staticmethod
    def _page():
        import pathlib

        from fastmdxplora.gui import server

        return (pathlib.Path(server.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")

    def test_neither_section_remains(self) -> None:
        page = self._page()
        assert 'data-page="builder"' not in page
        assert 'data-page="analyse"' not in page

    def test_nor_their_navigation(self) -> None:
        page = self._page()
        assert 'data-view-link="builder"' not in page
        assert 'data-view-link="analyse"' not in page

    def test_nor_their_scripts(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        static = pathlib.Path(server.__file__).parent / "static"
        assert not (static / "simulation-builder.js").exists()
        assert not (static / "analysis-builder.js").exists()
        page = self._page()
        assert "simulation-builder.js" not in page
        assert "analysis-builder.js" not in page

    def test_every_section_still_has_a_link_and_the_other_way(self) -> None:
        import re

        page = self._page()
        sections = set(re.findall(r'<section class="page" data-page="([^"]+)"', page))
        links = set(re.findall(r'data-view-link="([^"]+)"', page))
        assert sections == links, (
            f"sections without a link: {sorted(sections - links)}; "
            f"links without a section: {sorted(links - sections)}"
        )

    def test_a_run_can_still_be_stopped(self) -> None:
        """This lived only on the page that was retired, so deleting it
        without bringing this across would have left the browser able to start
        something it could not stop."""
        import pathlib

        from fastmdxplora.gui import server

        page = self._page()
        script = (pathlib.Path(server.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        assert 'id="run-stop"' in page
        assert "/api/explore/stop" in script

    def test_the_stop_button_appears_only_while_something_runs(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        assert "function watchForARun" in script
        assert "active_run" in script


class TestPathsAreNotAssumedToUseSlashes:
    """The tests run on Windows too, and so does the software.

    Two assertions compared the end of a path string -- "rep3/production.dcd"
    -- which is the end of nothing on a machine that joins with backslashes.
    Both passed here and failed there, which is the worst way to find out.
    """

    def test_the_suggestion_is_compared_as_a_path(self) -> None:
        import pathlib
        import re

        from fastmdxplora.gui import server

        tests = pathlib.Path(server.__file__).parents[3] / "tests" / "test_gui.py"
        if not tests.exists():          # installed rather than checked out
            import pytest

            pytest.skip("running against an installed copy")
        source = tests.read_text(encoding="utf-8")
        slashed = re.findall(r'endswith\("[^"]*/[^"]*"\)', source)
        assert not slashed, (
            f"these compare a path by the end of a string: {slashed}"
        )

    def test_the_page_knows_a_windows_path_is_absolute(self) -> None:
        """C:\\Users\\... starts with neither a slash nor a tilde, and calling
        it relative would have the note claim the results land somewhere they
        will not."""
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        assert "[A-Za-z]:" in script, "a drive letter is not recognised"

    def test_and_joins_with_the_separator_that_machine_uses(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        block = script[script.index("function describeOutput"):][:900]
        assert "separator" in block

    def test_the_folder_of_a_trajectory_is_found_either_way(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        block = script[script.index("function findStructureBeside"):][:600]
        assert "\\\\" in block, "only forward slashes are split on"


class TestChoosingSeveralOfSomething:
    """An option taking several of its accepted values needs a control that
    offers them.

    Clustering runs two methods by default. Offered as a single select, the
    two are unreachable together; offered as a text box, somebody has to type
    "kmeans, hierarchical" and get the spelling right. Both are ways of making
    a form worse than the command line it replaced.
    """

    @staticmethod
    def _options():
        from fastmdxplora.gui.schema_payload import schema_payload

        payload = schema_payload()["analysis_options"]
        if not payload["available"]:
            import pytest

            pytest.skip(payload["reason"])
        return payload

    def test_the_accepted_values_come_from_the_analysis(self) -> None:
        """They live in a constant the analysis validates against, so a form
        offering them cannot drift from what will be accepted."""
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis import cluster, dimred
        from fastmdxplora.analysis.describe import describe_analysis

        for module, name in ((cluster, "cluster"), (dimred, "dimred")):
            option = next(o for o in describe_analysis(name)
                          if o.name == "methods")
            assert option.choices == module.VALID_METHODS, name

    def test_an_option_taking_several_says_so(self) -> None:
        payload = self._options()
        for analysis, name in (("cluster", "methods"), ("dimred", "methods"),
                               ("pl_interactions", "kinds")):
            option = next(o for o in payload["analyses"][analysis]
                          if o["name"] == name)
            assert option["control"] == "multiselect", f"{analysis}.{name}"
            assert option["choices"], f"{analysis}.{name} offers nothing"

    def test_an_option_taking_one_still_says_select(self) -> None:
        payload = self._options()
        option = next(o for o in payload["analyses"]["cluster"]
                      if o["name"] == "features")
        assert option["control"] == "select"

    def test_the_page_draws_the_difference(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        assert 'option.control === "multiselect"' in script
        # Drawn as checkboxes: a native multiple select needs ctrl-click,
        # which nobody guesses and which makes the control look broken.
        assert "run-checkbox-group" in script
        assert "boxes.filter((b) => b.checked)" in script


class TestFifteenAnalysesReadAsAFewKinds:
    """A flat list of fifteen is a list. Grouped, it is a few kinds of
    question, and somebody looking for what a ligand is doing can find it."""

    @staticmethod
    def _options():
        from fastmdxplora.gui.schema_payload import schema_payload

        payload = schema_payload()["analysis_options"]
        if not payload["available"]:
            import pytest

            pytest.skip(payload["reason"])
        return payload

    def test_every_analysis_has_a_group(self) -> None:
        """A grouping that silently drops one is worse than no grouping."""
        payload = self._options()
        missing = [name for name in payload["analyses"]
                   if name not in payload["categories"]]
        assert not missing, f"these appear in no group: {missing}"

    def test_every_group_used_is_in_the_order(self) -> None:
        payload = self._options()
        used = set(payload["categories"].values())
        assert used <= set(payload["category_order"]), (
            f"groups with no place in the order: "
            f"{sorted(used - set(payload['category_order']))}"
        )

    def test_the_protein_ligand_analyses_are_together(self) -> None:
        payload = self._options()
        together = {name for name, group in payload["categories"].items()
                    if group == "Protein and ligand together"}
        assert {"pl_contacts", "pl_hbonds", "pl_interactions"} <= together

    def test_the_page_groups_them(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        assert "options.category_order" in script
        assert "run-analysis-group" in script


class TestTheOutputButtonOpensTheChosenFolder:
    """The one thing a person checks first after a run, and the one that would
    be most quietly wrong."""

    def test_it_follows_a_run_started_from_the_browser(self, tmp_path) -> None:
        from fastmdxplora.gui.exploration import DashboardRuntime

        runtime = DashboardRuntime(
            workspace_root=tmp_path / "ws", exploration_root=tmp_path,
            active_root=None)
        wanted = tmp_path / "my_chosen_output"
        try:
            started = runtime.launch_from_config({
                "output": str(wanted), "include": ["analysis"],
                "systems": [{"system": "t.pdb"}],
                "analysis": {"trajectory": "x.dcd", "topology": "t.pdb",
                             "include": "rmsd"},
            })
            assert started["ok"], started.get("error")
            assert runtime.data_root() == wanted.resolve()
        finally:
            runtime.stop()

    def test_and_still_points_there_once_it_finishes(self, tmp_path) -> None:
        import time

        from fastmdxplora.gui.exploration import DashboardRuntime

        runtime = DashboardRuntime(
            workspace_root=tmp_path / "ws", exploration_root=tmp_path,
            active_root=None)
        wanted = tmp_path / "finished_run"
        try:
            runtime.launch_from_config({
                "output": str(wanted), "include": ["analysis"],
                "systems": [{"system": "t.pdb"}],
                "analysis": {"trajectory": "x.dcd", "topology": "t.pdb",
                             "include": "rmsd"},
            })
            time.sleep(0.5)
        finally:
            runtime.stop()
        assert runtime.data_root() == wanted.resolve()

    def test_the_endpoint_opens_what_that_resolves_to(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        source = pathlib.Path(server.__file__).read_text(encoding="utf-8")
        start = source.index('if path == "/api/open-output"')
        # To the next route, rather than a fixed number of characters: the
        # guard for a remote dashboard sits between the two.
        block = source[start:source.index('if path == ', start + 40)]
        assert "_open_local_path(root)" in block
        assert "app_runtime.data_root()" in source


class TestEverySettingGetsTheRightControl:
    """Written after finding that three options taking several values came
    through as free text, so somebody had to type "kmeans, hierarchical" and
    get the spelling right.

    Checking them one at a time found that one; checking the property finds
    the next.
    """

    @staticmethod
    def _options():
        from fastmdxplora.gui.schema_payload import schema_payload

        payload = schema_payload()["analysis_options"]
        if not payload["available"]:
            import pytest

            pytest.skip(payload["reason"])
        return payload

    def test_no_setting_has_a_control_that_does_not_suit_it(self) -> None:
        payload = self._options()
        wrong = []
        for analysis, options in payload["analyses"].items():
            for option in options:
                if option["shared"]:
                    continue
                name = f"{analysis}.{option['name']}"
                control, default = option["control"], option["default"]
                if isinstance(default, list) and control != "multiselect":
                    wrong.append(f"{name}: takes several, offers {control}")
                elif option["choices"] and control not in ("select", "multiselect"):
                    wrong.append(f"{name}: has choices, offers {control}")
                elif isinstance(default, bool) and control != "checkbox":
                    wrong.append(f"{name}: is yes or no, offers {control}")
                elif (isinstance(default, (int, float))
                      and not isinstance(default, bool)
                      and control != "number"):
                    wrong.append(f"{name}: is a number, offers {control}")
        assert not wrong, wrong

    def test_every_setting_says_what_it_does(self) -> None:
        """A control with no label is a box somebody has to guess at."""
        payload = self._options()
        silent = [
            f"{analysis}.{option['name']}"
            for analysis, options in payload["analyses"].items()
            for option in options
            if not option["shared"] and not option["help"]
        ]
        assert not silent, silent

    def test_what_the_run_works_out_is_not_asked_for(self) -> None:
        """Typing a ligand name that does not match the one detected would
        have the analysis find nothing and report the ligand as absent."""
        payload = self._options()
        supplied = {
            f"{analysis}.{option['name']}"
            for analysis, options in payload["analyses"].items()
            for option in options
            if option["supplied_by_the_run"]
        }
        assert "pl_interactions.ligand_resname" in supplied
        assert len(supplied) >= 5, supplied

    def test_the_page_shows_those_without_offering_them(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        assert "option.supplied_by_the_run" in script
        assert "run-supplied-value" in script

    def test_choosing_several_is_checkboxes_not_a_multiple_select(self) -> None:
        """A native multiple select needs ctrl-click, which nobody guesses and
        which makes the control look broken to anyone who tries it once."""
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        assert "run-checkbox-group" in script
        assert "input.multiple = true" not in script

    def test_a_path_setting_gets_the_picker(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        payload = self._options()
        chemistry = next(o for o in payload["analyses"]["pl_interactions"]
                         if o["name"] == "ligand_chemistry")
        assert chemistry["is_path"]
        script = (pathlib.Path(server.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        assert "option.is_path" in script
        assert "FastMDXPicker.attachAll" in script, (
            "settings are drawn after load, so the picker has to be told"
        )


class TestTheProteinLigandAnalysesShareANaming:
    """`contacts` measured protein-ligand contacts, so it is `pl_contacts`
    beside `pl_hbonds` and `pl_interactions`.

    The old name was kept as an alias at first, so a config somebody had would
    still run -- but nobody has one, the software has not been released under
    this name, and the alias made the orchestrator run the analysis twice,
    once under each name, into the same directory.
    """

    def test_the_new_name_works(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.orchestrator import get_analysis_class

        assert get_analysis_class("pl_contacts") is not None

    def test_the_old_name_is_gone(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.orchestrator import available_analyses

        assert "contacts" not in available_analyses()

    def test_it_sits_with_the_other_protein_ligand_analyses(self) -> None:
        from fastmdxplora.gui.schema_payload import schema_payload

        payload = schema_payload()["analysis_options"]
        if not payload["available"]:
            import pytest

            pytest.skip(payload["reason"])
        assert payload["categories"]["pl_contacts"] == "Protein and ligand together"


class TestASettingsBlockCanBeWrittenInTheBrowser:
    """Umbrella, steered and metadynamics are blocks of several settings, not
    one value each.

    They reached the form -- the schema declares them -- but as single-line
    text boxes, and what was typed arrived as a string that no phase could
    read. The browser was the one interface where enhanced sampling could not
    be set up at all, which is the interface the documentation tells a new
    user to start in.
    """

    @staticmethod
    def _fields():
        from fastmdxplora.gui.schema_payload import schema_payload

        return {f["name"]: f
                for f in schema_payload()["phases"]["simulation"]["fields"]}

    def test_a_block_is_offered_as_a_mapping(self) -> None:
        fields = self._fields()
        for name in ("umbrella", "steered", "metadynamics"):
            assert fields[name]["control"] == "mapping", name

    def test_and_the_page_draws_something_that_can_hold_one(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        block = script[script.index('field.control === "mapping"'):][:700]
        assert 'createElement("textarea")' in block

    def test_each_block_shows_what_one_looks_like(self) -> None:
        """A blank box for a block of seven settings says nothing about which
        seven."""
        fields = self._fields()
        for name in ("umbrella", "steered", "metadynamics"):
            example = fields[name].get("example")
            assert isinstance(example, dict) and example, name
            assert "collective_variable" in example, name

    def test_what_was_typed_arrives_as_a_mapping(self) -> None:
        from fastmdxplora.gui.config_builder import build_config

        typed = ("collective_variable: distance\n"
                 "selection_a: resname BNZ\nselection_b: protein\n"
                 "from: 0.3\nto: 1.5\nn_windows: 7\nforce_constant: 5000\n")
        built = build_config({"simulation": {"umbrella": typed}})
        block = built["simulation"]["umbrella"]

        assert isinstance(block, dict), "a string here reaches no phase"
        assert block["n_windows"] == 7
        assert block["force_constant"] == 5000

    def test_and_the_config_it_writes_validates(self) -> None:
        """Which is what says the round trip is real rather than shaped
        right."""
        from fastmdxplora.config import validate_config
        from fastmdxplora.gui.config_builder import build_config

        built = build_config({"simulation": {"umbrella": (
            "collective_variable: distance\nselection_a: resname BNZ\n"
            "selection_b: protein\nfrom: 0.3\nto: 1.5\nn_windows: 7\n"
            "force_constant: 5000\n")}})
        validate_config({"output": "out", "systems": [{"system": "181L"}],
                         **built}, require_systems=True)

    def test_something_unreadable_is_kept_rather_than_dropped(self) -> None:
        """Validation reports it. Silently discarding a block somebody typed
        is worse than a refusal that says why."""
        from fastmdxplora.gui.config_builder import build_config

        built = build_config({"simulation": {"umbrella": "from: [0.3\n"}})
        assert built["simulation"]["umbrella"] == "from: [0.3\n"

    def test_the_variables_the_help_names_are_the_variables_there_are(
        self
    ) -> None:
        """The help said five when there were eight. It is what the browser
        shows beside the box, what --help prints, and what the config template
        carries, so a stale sentence is stale in four places."""
        from fastmdxplora.config.schema import PHASE_SCHEMAS
        from fastmdxplora.simulation.metadynamics import COLLECTIVE_VARIABLES

        described = PHASE_SCHEMAS["simulation"].get("metadynamics").help
        for variable in COLLECTIVE_VARIABLES:
            assert variable in described, variable


class TestTheBannerSaysHowToWatchTheRun:
    """It computed the dashboard address and discarded it, on the reasoning
    that the GUI prints its own -- true while a server is running, and no help
    to somebody who has just started a run and wants to see it."""

    def test_it_names_the_command_where_no_server_is_running(self) -> None:
        import inspect

        from fastmdxplora.utils import presenter

        source = inspect.getsource(presenter)
        # The path goes through `_worth_watching` now, so that a study's
        # shared preparation points at the study rather than at a directory
        # that will not change again. What this test is for -- that the
        # command is named at all -- is unchanged.
        assert 'kv("Watch", f"fastmdx gui --output {_worth_watching(output)}"' \
            in source

    def test_and_the_address_where_one_is(self) -> None:
        """A bare URL printed for a server nobody started points at nothing."""
        import inspect

        from fastmdxplora.utils import presenter

        source = inspect.getsource(presenter)
        watch = source[source.index('if dashboard_enabled:'):]
        assert 'kv("Watch", dashboard_link' in watch[:200]


class TestTheLiveTabDoesNotPretendToHaveData:
    """Opened on a run that recorded no telemetry, the page rendered its whole
    apparatus with every field reading "not available": current stage, current
    step, total planned steps, frames written, simulation time, elapsed time,
    checkpoint, last update. Twelve of them.

    And it explained the absence by advising the reader to start the dashboard
    during a simulation -- which is what somebody looking at that page has
    just done, so the advice sent them to repeat what had already failed.
    """

    def test_the_reason_names_the_setting(self) -> None:
        """The message used to say telemetry was off by default and tell the
        reader to switch it on. The schema default is now True, so that had
        become advice for a problem that no longer exists: a run reaching this
        message either turned it off or predates the setting.
        """
        from fastmdxplora.config.schema import PHASE_SCHEMAS
        from fastmdxplora.gui.telemetry import analyze_health

        explanation = analyze_health({}, [])["explanation"]

        assert "live_telemetry" in explanation
        assert PHASE_SCHEMAS["simulation"].get("live_telemetry").default is True
        assert "on by default" in explanation
        assert "during a simulation to monitor progress" not in explanation

    def test_it_names_a_flag_that_exists(self) -> None:
        """The first attempt at this message invented `--simulate-live-
        telemetry` from the schema's prefix. The flag is `--live-telemetry`."""
        from fastmdxplora.cli.main import _build_parser
        from fastmdxplora.gui.telemetry import analyze_health

        parser = _build_parser()
        simulate = parser._subparsers._group_actions[0].choices["simulate"]
        real = {option for action in simulate._actions
                for option in action.option_strings}

        import re

        explanation = analyze_health({}, [])["explanation"]
        # A word starting with two hyphens and then a letter. The prose uses
        # a bare "--" as a dash, which a looser match reads as a flag.
        named = re.findall(r"--[a-z][a-z0-9-]+", explanation)
        # Naming no flag is fine -- the setting is on by default, so a flag is
        # not the remedy any more. Naming one that does not exist is not.
        for flag in named:
            assert flag in real, f"{flag} is not a flag this software has"

    def test_the_panels_are_hidden_when_there_is_nothing_to_read(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "dashboard.js").read_text(encoding="utf-8")
        # To the next function, not a fixed 1200 characters: a comment
        # explaining the two empty states pushed the return past the cut.
        block = script[script.index("function renderLiveProgress"):]
        block = block[:block.index("\n  function ", 10)]

        assert "live-panels" in block and "live-absent" in block
        assert "if (nothing) return;" in block

    def test_and_the_page_has_somewhere_to_put_the_reason(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        markup = (pathlib.Path(server.__file__).parent / "templates"
                  / "dashboard.html").read_text(encoding="utf-8")
        assert 'id="live-absent"' in markup
        assert 'id="live-panels"' in markup


class TestTheGUICitesTheSoftware:
    """The report, the slides and `fastmdx info` all print the citation and
    the GUI printed none -- so the interface the documentation sends a new
    user to first was the one that never said how to cite the work."""

    def test_the_live_page_carries_it(self, tmp_path: Path) -> None:
        from fastmdxplora import __citation__, __doi__

        run = tmp_path / "run"
        writer = TelemetryWriter(run / "simulation")
        writer.write_status(stage="NVT")
        server, base_url = start_test_server(run)
        try:
            html = urlopen(f"{base_url}/", timeout=HTTP_TIMEOUT).read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()

        assert 'data-page="cite"' in html
        assert __doi__ in html
        assert __citation__.split(".")[0] in html

    def test_it_is_injected_rather_than_written_into_the_template(self) -> None:
        """Hardcoding it would put a fourth copy beside the report's, the
        slides' and the CLI's, free to drift from all three."""
        from fastmdxplora.gui.server import _load_template

        template = _load_template()
        assert "__FASTMDX_CITATION__" in template
        assert "Aina" not in template, (
            "the citation is filled by the server, not stored in the page")

    def test_the_bibtex_is_there_to_be_taken(self) -> None:
        from fastmdxplora.gui.server import _load_template

        template = _load_template()
        assert "__FASTMDX_BIBTEX__" in template
        assert 'id="cite-copy"' in template

    def test_one_bibtex_entry_serves_every_surface(self) -> None:
        """There were two copies, in the report and in the GUI, and a third
        was very nearly added: the page was storing the entry rather than
        being given it, which the test above caught."""
        import pathlib

        from fastmdxplora import __bibtex__, __doi__

        assert __doi__ in __bibtex__

        root = pathlib.Path(__file__).resolve().parents[1] / "src"
        holders = [f for f in root.rglob("*")
                   if f.suffix in {".py", ".html"}
                   and f.name != "__init__.py"
                   and "@article{aina2026fastmd" in f.read_text(encoding="utf-8")]
        assert holders == [], (
            f"the BibTeX entry is written out in {[f.name for f in holders]} "
            "as well as in __init__.py")

    def test_the_report_dashboard_carries_it_too(self) -> None:
        import inspect

        from fastmdxplora.gui import report_dashboard

        source = inspect.getsource(report_dashboard._render_dashboard)
        assert "__citation__" in source
        assert "citation_html" in source


class TestTheGUIAsksToBeCited:
    """The citation page existed and was the last item in the sidebar,
    beneath Documentation and GitHub, under a heading reading Tools -- so the
    one thing a scientific tool most needs its user to find was placed after
    the two links that navigate away from it.
    """

    @staticmethod
    def _markup():
        import pathlib

        from fastmdxplora.gui import server

        return (pathlib.Path(server.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")

    def test_cite_comes_before_the_links_that_leave(self) -> None:
        # In the settings popup now, by the owner's choice, with the version
        # beside it. What the test still holds: it comes before Documentation
        # and GitHub in that popup, and neither of those is in the sidebar.
        markup = self._markup()
        popup = markup[markup.index('id="settings-popup"'):]
        cite = popup.index("Cite FastMDXplora")
        assert cite < popup.index("readthedocs.io")
        assert cite < popup.index("github.com/aai-research-lab")
        sidebar = markup[markup.index('<aside class="sidebar"'):markup.index("</aside>")]
        assert "readthedocs.io" not in sidebar and "github.com" not in sidebar

    def test_every_page_carries_the_reminder(self) -> None:
        # The popup is on every page, and Cite is in it, one click away.
        markup = self._markup()
        popup = markup[markup.index('id="settings-popup"'):markup.index('<div class="app-shell">')]
        assert 'data-view-link="cite">Cite FastMDXplora' in popup

    def test_the_page_itself_carries_the_reference_and_the_doi(self) -> None:
        markup = self._markup()
        assert "__FASTMDX_CITATION__" in markup
        assert "__FASTMDX_DOI__" in markup

    def test_and_the_server_fills_them_in(self) -> None:
        """A placeholder that reaches the browser unreplaced is worse than
        no citation at all."""
        import inspect

        from fastmdxplora.gui import server

        source = inspect.getsource(server)
        assert '"__FASTMDX_CITATION__"' in source
        assert '"__FASTMDX_DOI__"' in source


class TestOnePageForOneRun:
    """Overview and Live Simulation showed the same run, and the top bar
    showed it a third time: three places to look for one answer, and neither
    page complete on its own.

    They are one page now, with what is happening at the top and what has been
    recorded beneath it.
    """

    @staticmethod
    def _markup():
        import pathlib

        from fastmdxplora.gui import server

        return (pathlib.Path(server.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")

    def test_there_is_no_separate_live_page(self) -> None:
        markup = self._markup()
        assert 'data-page="live"' not in markup
        assert 'data-view-link="live"' not in markup

    def test_the_live_panels_are_on_the_overview(self) -> None:
        """Moved rather than rebuilt, so every identifier the script reads is
        where it was."""

        markup = self._markup()
        start = markup.index('<section class="page" data-page="overview"')
        end = markup.index('<section class="page"', start + 10)
        overview = markup[start:end]

        # `health-headline` rather than `live-health-message`: the overview
        # carried two health panels rendering the same `analyze_health` call,
        # and the surviving one is the hero card, which also lists what is
        # wrong. The claim here is unchanged -- the live panels are on this
        # page -- only which element proves it.
        # `events-list` is gone: the Log tab in the side panel is the event
        # stream now, on every page. The rest are where they were.
        for identifier in ("live-panels", "live-absent", "live-progress-fill",
                           "live-stage-cell", "health-headline",
                           "live-simtime-cell", "live-frames-cell",
                           "chart-stack", "mini-preview-canvas"):
            assert f'id="{identifier}"' in overview, identifier

    def test_what_is_happening_comes_before_what_was_recorded(self) -> None:
        """A page opened during a run answered "is it running, and how far"
        below a table of phases that had already finished."""
        import re

        markup = self._markup()
        start = markup.index('<section class="page" data-page="overview"')
        end = markup.index('<section class="page"', start + 10)
        cards = re.findall(r'card-title">([^<]+)<', markup[start:end])

        # The bar-and-table progress card is gone; the sidebar carries the
        # running stage. What is happening -- the charts, the structure --
        # still comes before what was recorded.
        assert cards.index("Structure") < cards.index("Live charts")
        assert cards.index("Live charts") < cards.index("Phases")
        assert cards.index("Live charts") < cards.index("Trajectory statistics")

    def test_the_two_progress_cards_say_which_is_which(self) -> None:
        """One is a bar for the running stage, the other a table of phases.
        Both were called progress."""
        markup = self._markup()
        # One progress display now -- the sidebar's, labelled Progress --
        # and one table of phases, labelled Phases. The Overview's own
        # "Simulation progress" card repeated the sidebar and is gone.
        assert 'card-title">Simulation progress<' not in markup
        assert 'card-title">Phases<' in markup
        assert 'card-title">Exploration progress<' not in markup
        sidebar = markup[markup.index('<aside class="sidebar"'):markup.index("</aside>")]
        assert 'study-label mono">Progress<' in sidebar

    def test_opening_the_overview_starts_the_polling(self) -> None:
        """It was keyed to opening a page that no longer exists, so nothing
        would have started."""
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "dashboard.js").read_text(encoding="utf-8")
        assert 'if (page === "overview") emit("live-page-opened"' in script


class TestWatchingIsOnByDefault:
    """The live dashboard could not show anything unless somebody knew to
    turn on a setting called telemetry -- a word that elsewhere means data
    sent to a vendor, and which here means four files in the run directory."""

    def test_it_is_on(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        assert PHASE_SCHEMAS["simulation"].get("live_telemetry").default is True

    def test_and_says_what_it_costs(self) -> None:
        """Measured at a tenth of a per cent, which is why there is no reason
        to leave it off."""
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        described = PHASE_SCHEMAS["simulation"].get("live_telemetry").help
        assert "tenth of a per cent" in described
        assert "Nothing leaves the machine" in described


class TestTheTrajectoryIsReadOnceAndQuietly:
    """The GUI's terminal filled with

        dcdplugin) detected standard 32-bit DCD file of native endianness
        dcdplugin) CHARMM format DCD file (also NAMD 2.1 and later)

    a pair every few seconds, for as long as it was open. The lines are VMD's
    DCD plugin, which MDTraj wraps: it announces every file it opens on the
    C-level file descriptor, where Python's logging cannot reach it.

    The noise was the symptom. The cause was the viewer asking for the
    playback payload with ``force=1`` whenever the browser did not already
    hold one -- so every mount and every reload re-streamed the whole
    trajectory to rebuild a file already sitting beside it.
    """

    def test_the_viewer_asks_for_the_cache_first(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        script = (pathlib.Path(server.__file__).parent / "static"
                  / "molecule-viewer.js").read_text(encoding="utf-8")
        block = script[script.index("async function ensurePlaybackPayload"):][:600]

        assert "requestPlaybackPayload(false)" in block, (
            "force means the user asked for a rebuild, not that the browser "
            "has not got it yet")

    def test_a_second_request_does_not_reopen_the_file(self, tmp_path) -> None:
        """The disk cache is keyed on the trajectory's size and modification
        time, so an unchanged file is read once."""
        import mdtraj as md

        from fastmdxplora.gui import trajectory_playback as playback

        run = _completed_run_with_trajectory(tmp_path)
        opens = {"n": 0}
        real = md.iterload

        def counted(*args, **kwargs):
            opens["n"] += 1
            return real(*args, **kwargs)

        md.iterload = counted
        try:
            for _ in range(4):
                playback.playback_info(run, max_browser_frames=50, force=False)
        finally:
            md.iterload = real

        assert opens["n"] == 1, (
            f"the trajectory was opened {opens['n']} times for four requests")

    def test_the_reader_does_not_write_to_the_terminal(self, tmp_path,
                                                       capfd) -> None:
        from fastmdxplora.gui import trajectory_playback as playback

        run = _completed_run_with_trajectory(tmp_path)
        playback.playback_info(run, max_browser_frames=50, force=False)

        captured = capfd.readouterr()
        assert "dcdplugin" not in captured.out
        assert "dcdplugin" not in captured.err


def _completed_run_with_trajectory(tmp_path):
    """A run directory with a topology and a short production trajectory."""
    import mdtraj as md
    import numpy as np

    sim = tmp_path / "simulation"
    sim.mkdir(parents=True)

    topology = md.Topology()
    chain = topology.add_chain()
    for index in range(4):
        residue = topology.add_residue("ALA", chain, resSeq=index + 1)
        for name, element in (("N", md.element.nitrogen), ("CA", md.element.carbon),
                              ("C", md.element.carbon), ("O", md.element.oxygen)):
            topology.add_atom(name, element, residue)

    rng = np.random.RandomState(0)
    xyz = (np.linspace(0, 1.6, topology.n_atoms)[None, :, None]
           + rng.normal(0, 0.01, (30, topology.n_atoms, 3))).astype(np.float32)
    traj = md.Trajectory(xyz, topology)
    traj[0].save_pdb(str(sim / "topology.pdb"))
    traj.save_dcd(str(sim / "production.dcd"))
    return tmp_path


class TestAPageWatchingARunSaysWhatIsHappening:
    """Opened on a run in progress, the page showed that run's energy, its
    temperature and its speed in ns/day above a table saying every phase was
    "Not run".

    The phase table reads the manifest, and the manifest is written when a run
    finishes. "Not run" is a claim that a phase did not happen; while a run is
    going the truth is that it has not finished.
    """

    def test_a_running_phase_says_which_stage(self) -> None:
        from fastmdxplora.gui.report_dashboard import _phase_rows

        rows = {row.name: row for row in _phase_rows(
            {}, {"status": "running", "stage": "Production"})}

        assert rows["Simulation"].status == "running"
        assert rows["Simulation"].detail == "Production"

    def test_the_phases_before_it_are_complete(self) -> None:
        """A run that is simulating has finished preparing, whatever the
        manifest says: it could not have started otherwise."""
        from fastmdxplora.gui.report_dashboard import _phase_rows

        rows = {row.name: row for row in _phase_rows(
            {}, {"status": "running", "stage": "NVT equilibration"})}

        assert rows["Setup"].status == "ok"
        assert "recorded when the run ends" in rows["Setup"].detail

    def test_the_phases_after_it_are_pending_not_absent(self) -> None:
        from fastmdxplora.gui.report_dashboard import _phase_rows

        rows = {row.name: row for row in _phase_rows(
            {}, {"status": "running", "stage": "Production"})}

        assert rows["Analysis"].status == "pending"
        assert rows["Report"].status == "pending"

    def test_with_no_run_at_all_they_are_not_run(self) -> None:
        """An empty directory is a different thing from a run in progress."""
        from fastmdxplora.gui.report_dashboard import _phase_rows

        for row in _phase_rows({}, None):
            assert row.status == "not-run"

    def test_a_finished_run_still_reads_from_its_manifest(self) -> None:
        from fastmdxplora.gui.report_dashboard import _phase_rows

        manifest = {"phases": [{"name": "setup", "status": "ok"},
                               {"name": "simulation", "status": "ok"}]}
        rows = {row.name: row for row in _phase_rows(manifest, None)}

        assert rows["Setup"].status == "ok"
        assert rows["Analysis"].status == "not-run"

    def test_the_chart_titles_are_not_drawn_over_the_charts(self) -> None:
        """Absolutely positioned at the top-left, the title sat exactly where
        the chart draws its y-axis labels."""
        import pathlib

        from fastmdxplora.gui import server

        static = pathlib.Path(server.__file__).parent / "static"
        css = (static / "dashboard.css").read_text(encoding="utf-8")
        block = css[css.index(".chart-title {"):][:260]
        assert "position: absolute" not in block

        markup = (pathlib.Path(server.__file__).parent / "templates"
                  / "dashboard.html").read_text(encoding="utf-8")
        row = markup[markup.index('<div class="chart-row">'):][:400]
        assert row.index("chart-title") < row.index("chart-canvas"), (
            "the title must come before the canvas to sit above it")


class TestTheSectionsThatAreNotPhasesReachTheConfig:
    """Two whole sections of the form collected settings and dropped them.

    "This run" and "How the runs are scheduled" are drawn from the schema
    and stored under sentinel keys, like every other section. But
    `currentState()` walked `PHASES` to assemble the payload, and neither
    key is a phase -- so everything set in them stayed in the browser.

    The server has read both keys all along, which is why it went
    unnoticed: `build_config` handles them correctly and was never given
    them. The execution section's own note says it exists because a study
    wanting two GPUs had to be written by hand; it still did.
    """

    @staticmethod
    def _builder() -> str:
        from pathlib import Path

        import fastmdxplora.gui as gui

        return (Path(gui.__file__).parent / "static" / "run-builder.js"
                ).read_text(encoding="utf-8")

    def test_the_payload_carries_the_run_options_key(self):
        source = self._builder()
        assert "RUN_OPTIONS_KEY" in source
        # Emitted, not merely drawn: the sentinel has to appear where the
        # payload is assembled, not only where the controls are.
        _, _, after = source.partition("function currentState()")
        emitted, _, _ = after.partition("function ready()")
        assert "RUN_OPTIONS_KEY" in emitted
        assert "EXECUTION_KEY" in emitted

    def test_the_server_turns_them_into_a_config(self):
        from fastmdxplora.gui.config_builder import build_config

        config = build_config({
            "system": "1UBQ",
            "include": ["setup"],
            "__run__": {"verbose": True},
            "__execution__": {"workers": 4, "mode": "parallel"},
        })

        assert config["verbose"] is True
        assert config["execution"] == {"workers": 4, "mode": "parallel"}

    def test_a_config_built_that_way_still_validates(self):
        from fastmdxplora.config import validate_config
        from fastmdxplora.gui.config_builder import build_config

        validate_config(
            build_config({
                "system": "1UBQ",
                "include": ["setup", "simulation"],
                "__run__": {"agent": "assisted"},
                "__execution__": {"devices": [0, 1]},
            }),
            require_systems=True,
        )
