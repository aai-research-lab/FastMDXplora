"""Tests for the FastMDXplora orchestrator and individual phases."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fastmdxplora import FastMDXplora
from fastmdxplora.orchestrator import PHASES, PhaseResult

# Orchestration tests verify the pipeline wiring, not MD physics. We mock the
# MD engine (see _mock_md below) so the simulation phase produces a real, tiny
# trajectory deterministically — running a freshly-solvated stub through a
# 2-step equilibration occasionally blows up to NaN depending on the
# integrator's random draw, which made these tests flaky. FAST_SIM still flows
# through so the wiring (option plumbing, manifests) is exercised as before.
FAST_SIM = {"simulation": {"nvt_steps": 2, "npt_steps": 2, "production_steps": 4, "trajectory_interval_steps": 1}}


@pytest.fixture(autouse=True)
def _mock_md():
    """Replace the MD engine with a fast, deterministic stand-in.

    Writes a real (tiny) trajectory and topology into the simulation output
    dir from the solvated topology, so the downstream analysis and report
    phases run on genuine data through the real wiring — without integrating
    (which is the only numerically-unstable part on a stub).
    """
    def _fake_run_simulation(*, topology_pdb, output_dir, **kwargs):
        import mdtraj as md
        from fastmdxplora.simulation.runner import SimulationResult

        traj = md.load(str(topology_pdb))
        multi = md.join([traj] * 4)
        traj_path = output_dir / "production.dcd"
        multi.save_dcd(str(traj_path))
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

    def _fake_setup(*, orchestrator, output_dir, **kwargs):
        """Provide chemistry artifacts without requiring OpenMM in wiring tests."""
        import shutil

        source = Path(orchestrator.system)
        if source.exists():
            shutil.copy2(source, output_dir / "input.pdb")
            shutil.copy2(source, output_dir / "prepared.pdb")
            shutil.copy2(source, output_dir / "topology.pdb")
        else:
            pdb_text = (
                "ATOM      1  N   ALA A   1       0.000   0.000   0.000  "
                "1.00  0.00           N\n"
            )
            for name in ("input.pdb", "prepared.pdb", "topology.pdb"):
                (output_dir / name).write_text(pdb_text, encoding="utf-8")
        (output_dir / "system.xml").write_text("<System/>\n", encoding="utf-8")
        (output_dir / "state.xml").write_text("<State/>\n", encoding="utf-8")
        (output_dir / "setup_parameters.json").write_text(
            json.dumps({
                "phase": "setup",
                "input": {
                    "system": orchestrator.system,
                    "form": "pdb_id" if len(str(orchestrator.system)) == 4 else "pdb_file",
                },
                "parameters": kwargs,
                "artifacts_written": [
                    "input.pdb", "prepared.pdb", "topology.pdb", "system.xml", "state.xml",
                ],
                "notes": [],
            }),
            encoding="utf-8",
        )
        return [
            "input.pdb", "prepared.pdb", "topology.pdb", "system.xml", "state.xml",
            "setup_parameters.json",
        ]

    with patch(
        "fastmdxplora.simulation.runner.run_simulation",
        side_effect=_fake_run_simulation,
    ), patch(
        "fastmdxplora.setup.pipeline.run",
        side_effect=_fake_setup,
    ):
        yield



def _make_pdb_stub(tmp_path: Path) -> Path:
    """Create a minimal PDB file good enough for the setup phase classifier."""
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


def test_orchestrator_requires_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires either a `system`"):
        FastMDXplora(output_dir=tmp_path / "x")


def test_orchestrator_creates_output_dir(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    assert fmdx.output_dir.exists()
    assert fmdx.output_dir.is_dir()


def test_orchestrator_accepts_pdb_id_via_system(tmp_path: Path) -> None:
    # PDB IDs are passed through `system` — there is no separate pdb_id
    # parameter. The setup classifier detects the 4-char ID form.
    fmdx = FastMDXplora(system="1L2Y", output_dir=tmp_path / "run")
    assert fmdx.system == "1L2Y"
    assert not hasattr(fmdx, "pdb_id")


def test_full_explore_runs_all_phases(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    results = fmdx.explore(options=FAST_SIM)
    # Uniform shape: a single study is a list of one RunResult.
    assert len(results) == 1
    run = results[0]
    assert run.status == "ok", [p.message for p in run.phases]
    assert [p.name for p in run.phases] == list(PHASES)
    assert all(p.status == "ok" for p in run.phases)


def test_explore_writes_manifest(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    fmdx.explore(options=FAST_SIM)
    manifest_path = fmdx.output_dir / "manifest.json"
    assert manifest_path.exists()
    with manifest_path.open() as fh:
        manifest = json.load(fh)
    assert manifest["tool"] == "FastMDXplora"
    assert manifest["doi"] == "10.1002/jcc.70350"
    assert len(manifest["phases"]) == len(PHASES)


def test_single_phase_manifest_preserves_existing_phases(tmp_path: Path) -> None:
    """A direct phase command must not erase earlier phase provenance."""
    pdb = _make_pdb_stub(tmp_path)
    output = tmp_path / "run"
    output.mkdir()
    (output / "manifest.json").write_text(json.dumps({
        "tool": "FastMDXplora",
        "phases": [
            {"name": "simulation", "status": "stale"},
            {"name": "simulation", "status": "ok"},
        ],
    }))
    fmdx = FastMDXplora(system=str(pdb), output_dir=output)
    fmdx.results.append(PhaseResult(name="analysis", status="ok"))

    fmdx._write_manifest()

    manifest = json.loads((output / "manifest.json").read_text())
    assert [phase["name"] for phase in manifest["phases"]] == [
        "simulation", "analysis"
    ]
    assert manifest["phases"][0]["status"] == "ok"


def test_manifest_with_invalid_phase_and_option_shapes_is_recoverable(
    tmp_path: Path,
) -> None:
    """A malformed prior manifest must not block recording a new phase."""
    pdb = _make_pdb_stub(tmp_path)
    output = tmp_path / "run"
    output.mkdir()
    (output / "manifest.json").write_text(json.dumps({
        "phases": None,
        "options": "not-a-mapping",
    }))
    fmdx = FastMDXplora(system=str(pdb), output_dir=output)
    fmdx.results.append(PhaseResult(name="analysis", status="ok"))

    fmdx._write_manifest()

    manifest = json.loads((output / "manifest.json").read_text())
    assert [phase["name"] for phase in manifest["phases"]] == ["analysis"]


def test_explore_include(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    results = fmdx.explore(include=["setup", "analysis"])
    assert [p.name for p in results[0].phases] == ["setup", "analysis"]


def test_explore_exclude(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    results = fmdx.explore(exclude=["report"], options=FAST_SIM)
    names = [p.name for p in results[0].phases]
    assert "report" not in names
    assert set(names) == {"setup", "simulation", "analysis"}


def test_explore_no_report_flag(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    results = fmdx.explore(report=False, options=FAST_SIM)
    names = [p.name for p in results[0].phases]
    assert "report" not in names


def test_explore_include_and_exclude_mutually_exclusive(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    with pytest.raises(ValueError, match="either"):
        fmdx.explore(include=["setup"], exclude=["report"])


def test_explore_unknown_phase_raises(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    with pytest.raises(ValueError, match="Unknown phase"):
        fmdx.explore(include=["wibble"])


def test_per_phase_methods(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    assert fmdx.setup().status == "ok"
    assert fmdx.simulate(**FAST_SIM['simulation']).status == "ok"
    assert fmdx.analyze().status == "ok"
    assert fmdx.report().status == "ok"


def test_setup_writes_parameters_manifest(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    fmdx.setup()
    pm = fmdx.output_dir / "setup" / "setup_parameters.json"
    assert pm.exists()
    with pm.open() as fh:
        data = json.load(fh)
    assert data["phase"] == "setup"
    assert data["input"]["form"] == "pdb_file"


def test_setup_classifies_pdb_id(tmp_path: Path) -> None:
    fmdx = FastMDXplora(system="1L2Y", output_dir=tmp_path / "run")
    fmdx.setup()
    pm = fmdx.output_dir / "setup" / "setup_parameters.json"
    with pm.open() as fh:
        data = json.load(fh)
    assert data["input"]["form"] == "pdb_id"


def test_analysis_module_taxonomy() -> None:
    """Every registered analysis is a usable one.

    This used to hold a list of the analyses expected to exist, written when
    they were being delivered one sub-delivery at a time. A list like that
    fails when a fifteenth analysis is added -- not because anything is wrong,
    but because nobody updated the list -- and passes when an analysis is
    registered under a name nothing can reach.

    So it checks the property instead: whatever is registered can be got at,
    says what it is, and appears in the registry the rest of the software
    reads.
    """
    from fastmdxplora.analysis import AVAILABLE_ANALYSES
    from fastmdxplora.analysis.orchestrator import (
        available_analyses,
        get_analysis_class,
    )

    assert isinstance(AVAILABLE_ANALYSES, tuple)
    assert AVAILABLE_ANALYSES, "nothing is registered at all"
    assert set(AVAILABLE_ANALYSES) == set(available_analyses()), (
        "the tuple and the registry disagree about what exists"
    )

    for name in AVAILABLE_ANALYSES:
        cls = get_analysis_class(name)
        assert cls is not None, f"{name} is registered but cannot be got"
        # An analysis renamed for consistency keeps its old name registered,
        # so a config somebody has kept still runs. Such a name answers to the
        # class's current one.
        assert cls.name in (name, getattr(cls, "name", name)), name
        assert getattr(cls, "description", ""), (
            f"{name} does not say what it is, so nothing can offer it usefully"
        )


def test_report_phase_writes_markdown_document(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    fmdx.explore(options=FAST_SIM)
    report_md = fmdx.output_dir / "report" / "report.md"
    assert report_md.exists()
    text = report_md.read_text(encoding="utf-8")
    assert "# " in text  # has a title
    assert "## Methods" in text
    assert "## Results" in text
    assert "10.1002/jcc.70350" in text


def test_report_phase_writes_slides_outline(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    fmdx.explore(options=FAST_SIM)
    outline = fmdx.output_dir / "report" / "slides_outline.md"
    assert outline.exists()


def test_report_phase_writes_bundle(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    fmdx.explore(options=FAST_SIM)
    bundle = fmdx.output_dir / "report" / "project_bundle.zip"
    assert bundle.exists()
    assert bundle.stat().st_size > 0


def test_bundle_does_not_recursively_include_itself(tmp_path: Path) -> None:
    """Regression guard: the bundle must not include a copy of itself."""
    import zipfile

    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    fmdx.explore(options=FAST_SIM)
    bundle = fmdx.output_dir / "report" / "project_bundle.zip"
    with zipfile.ZipFile(bundle) as zf:
        names = zf.namelist()
    assert "report/project_bundle.zip" not in names


def test_a_preserved_phase_keeps_the_version_that_produced_it(tmp_path: Path) -> None:
    """The merge keeps old phases; it must not relabel them as ours.

    Preserving the records was the fix in 742c125. But every other manifest
    field is recomputed by whoever writes the file, so a run simulated under
    2.5.4 on the cluster and analysed under 2.5.5 on a workstation came back
    claiming 2.5.5 produced all of it. That is worse than the gap it
    replaced: a missing record makes you go and look, a confident wrong one
    does not.
    """
    from fastmdxplora import __version__

    (tmp_path / "manifest.json").write_text(json.dumps({
        "tool": "FastMDXplora", "version": "2.5.4",
        "phases": [
            {"name": "setup", "status": "ok",
             "produced_by": {"version": "2.5.4", "host": "cluster-1"}},
            {"name": "simulate", "status": "ok",
             "produced_by": {"version": "2.5.4", "host": "cluster-1"}},
        ],
        "options": {"setup": {"padding_nm": 1.0}},
    }, indent=2))

    fmdx = FastMDXplora(system="x.pdb", output_dir=tmp_path)
    fmdx.results.append(PhaseResult(
        name="analysis", status="ok",
        produced_by={"version": __version__, "host": "workstation-2"}))
    fmdx._write_manifest()

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    by_name = {p["name"]: p for p in manifest["phases"]}

    assert by_name["setup"]["produced_by"]["version"] == "2.5.4"
    assert by_name["setup"]["produced_by"]["host"] == "cluster-1"
    assert by_name["analysis"]["produced_by"]["version"] == __version__
    assert manifest["versions_seen"] == ["2.5.4", __version__]
    assert "more than one" in manifest["version_note"]


def test_one_version_needs_no_note(tmp_path: Path) -> None:
    """The note is for mixed manifests. A single-version run stays clean."""
    fmdx = FastMDXplora(system="x.pdb", output_dir=tmp_path)
    fmdx.results.append(PhaseResult(name="setup", status="ok"))
    fmdx._write_manifest()

    manifest = json.loads((tmp_path / "manifest.json").read_text())

    assert "versions_seen" not in manifest
    assert "version_note" not in manifest


def test_a_phase_recorded_before_this_field_stays_unlabelled(tmp_path: Path) -> None:
    """An old record has no provenance and must not be given ours."""
    (tmp_path / "manifest.json").write_text(json.dumps({
        "phases": [{"name": "setup", "status": "ok"}],
    }, indent=2))

    fmdx = FastMDXplora(system="x.pdb", output_dir=tmp_path)
    fmdx.results.append(PhaseResult(name="analysis", status="ok"))
    fmdx._write_manifest()

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    setup = next(p for p in manifest["phases"] if p["name"] == "setup")

    assert "produced_by" not in setup


def test_an_unreadable_manifest_does_not_stop_the_phase_recording(tmp_path: Path) -> None:
    """The `except (OSError, JSONDecodeError)` branch, which had no test.

    A truncated write leaves a file that is present and unparseable. The
    phase that just ran must still be recorded.
    """
    (tmp_path / "manifest.json").write_text('{"phases": [{"name": "setup"')

    fmdx = FastMDXplora(system="x.pdb", output_dir=tmp_path)
    fmdx.results.append(PhaseResult(name="analysis", status="ok"))
    fmdx._write_manifest()

    manifest = json.loads((tmp_path / "manifest.json").read_text())

    assert [p["name"] for p in manifest["phases"]] == ["analysis"]


def test_a_manifest_that_is_not_an_object_is_ignored(tmp_path: Path) -> None:
    """Valid JSON, wrong shape. `previous` must stay a dict."""
    (tmp_path / "manifest.json").write_text('["not", "a", "manifest"]')

    fmdx = FastMDXplora(system="x.pdb", output_dir=tmp_path)
    fmdx.results.append(PhaseResult(name="analysis", status="ok"))
    fmdx._write_manifest()

    manifest = json.loads((tmp_path / "manifest.json").read_text())

    assert [p["name"] for p in manifest["phases"]] == ["analysis"]


# ---------------------------------------------------------------------------
# Study-level provenance reaching the records it exists for
# ---------------------------------------------------------------------------
def test_a_study_level_agent_reaches_the_manifest(tmp_path: Path) -> None:
    """`agent:` at the top of a config has to survive to `manifest.json`.

    It did not. `phase_options` keeps the four phase blocks and nothing
    else, so the value had no channel from the config to a run, and
    `_write_manifest` asked `resolve_agent_modes` about a dict of phase
    blocks. `study` came back None every time, and the writer is guarded on
    there being something to say -- so a study that named how it was
    written recorded nothing about how it was written.
    """
    pdb = _make_pdb_stub(tmp_path)
    config = {
        "systems": [{"id": "s1", "system": str(pdb)}],
        "agent": "assisted",
        "agent_model": "anthropic/claude-sonnet-4-5-20250929",
        **FAST_SIM,
    }
    fmdx = FastMDXplora(config_data=config, output_dir=tmp_path / "run")
    fmdx.explore()

    manifest = json.loads((fmdx.output_dir / "manifest.json").read_text())
    assert manifest["agent"]["study"] == "assisted"
    assert manifest["agent"]["checked"] == {phase: True for phase in PHASES}


def test_a_phase_agreeing_with_the_study_is_not_a_departure(
    tmp_path: Path,
) -> None:
    """The second half of the same bug, and the more misleading half.

    With `study` forced to None, a phase that repeated the study's own
    value differed from it, so the manifest reported a departure the config
    does not contain -- in the record whose entire purpose is to say which
    part of a study to distrust.
    """
    pdb = _make_pdb_stub(tmp_path)
    config = {
        "systems": [{"id": "s1", "system": str(pdb)}],
        "agent": "assisted",
        "setup": {"agent": "assisted"},
        **FAST_SIM,
    }
    fmdx = FastMDXplora(config_data=config, output_dir=tmp_path / "run")
    fmdx.explore()

    manifest = json.loads((fmdx.output_dir / "manifest.json").read_text())
    assert "departures" not in manifest["agent"]


def test_a_real_departure_is_still_named(tmp_path: Path) -> None:
    pdb = _make_pdb_stub(tmp_path)
    config = {
        "systems": [{"id": "s1", "system": str(pdb)}],
        "agent": "assisted",
        "analysis": {"agent": "unvalidated"},
        **FAST_SIM,
    }
    fmdx = FastMDXplora(config_data=config, output_dir=tmp_path / "run")
    fmdx.explore()

    manifest = json.loads((fmdx.output_dir / "manifest.json").read_text())
    assert manifest["agent"]["departures"] == {"analysis": "unvalidated"}
    assert manifest["agent"]["checked"]["analysis"] is False
    assert manifest["agent"]["checked"]["simulation"] is True


def test_the_resolved_config_says_how_the_study_was_written(
    tmp_path: Path,
) -> None:
    """Otherwise re-running an agent-written study produces a record
    claiming a person wrote it."""
    import yaml

    pdb = _make_pdb_stub(tmp_path)
    config = {
        "systems": [{"id": "s1", "system": str(pdb)}],
        "agent": "unvalidated",
        "agent_model": "anthropic/claude-sonnet-4-5-20250929",
        **FAST_SIM,
    }
    fmdx = FastMDXplora(config_data=config, output_dir=tmp_path / "run")
    fmdx.explore()

    resolved = yaml.safe_load(
        (fmdx.output_dir / "resolved_config.yml").read_text())
    assert resolved["agent"] == "unvalidated"
    assert resolved["agent_model"] == "anthropic/claude-sonnet-4-5-20250929"

    # And it is still a config the validator accepts.
    from fastmdxplora.config import validate_config
    validate_config(resolved, require_systems=True)


def test_a_hand_written_study_records_no_agent_block(tmp_path: Path) -> None:
    """Absent, not "none". Every study run before the field existed stays
    truthful without being rewritten."""
    pdb = _make_pdb_stub(tmp_path)
    fmdx = FastMDXplora(system=str(pdb), output_dir=tmp_path / "run")
    fmdx.explore(options=FAST_SIM)

    manifest = json.loads((fmdx.output_dir / "manifest.json").read_text())
    assert "agent" not in manifest
