"""Tests for the simulation phase.

Two strategies (same as setup-phase tests):

1. **Mock-based**: OpenMM isn't installable in the sandbox (conda-forge
   only). The runner's OpenMM calls are exercised via mocks that capture
   call arguments so we verify the wiring without needing the package.

2. **Real end-to-end**: ``@pytest.mark.skipif`` gated on OpenMM
   availability. On a developer machine with OpenMM installed (typically
   via conda-forge), the skip becomes a pass and verifies a real DCD
   gets written.

Planning logic (``plan_stages``, ``trajectory_interval_for``) is tested
directly — no mocks needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fastmdxplora.simulation import pipeline as _pipeline
from fastmdxplora.simulation import runner as _runner


# ---------------------------------------------------------------------------
# Optional-deps detection
# ---------------------------------------------------------------------------
try:
    import openmm  # noqa: F401
    HAS_OPENMM = True
except ImportError:
    HAS_OPENMM = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def setup_outputs(tmp_path: Path):
    """A fake setup/ directory with placeholder XML+PDB files."""
    setup_dir = tmp_path / "proj" / "setup"
    setup_dir.mkdir(parents=True)
    (setup_dir / "system.xml").write_text("<System />")
    (setup_dir / "state.xml").write_text("<State />")
    # Minimal PDB so OpenMM's PDBFile parser (in mock) accepts it
    (setup_dir / "topology.pdb").write_text(
        # A tripeptide rather than a lone residue: one amino acid is
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
        "END\n"
    )
    return setup_dir.parent  # the project root


@pytest.fixture
def stub_orchestrator(setup_outputs: Path):
    orch = MagicMock()
    orch.output_dir = setup_outputs
    orch.system = str(setup_outputs / "setup" / "input.pdb")
    orch._presenter = None
    return orch


# ===========================================================================
# plan_stages
# ===========================================================================
class TestPlanStages:
    def test_default_equilibration_and_production_lengths(self):
        plan = _runner.plan_stages(
            duration_ns=None, timestep_fs=2.0,
            nvt_steps=None, npt_steps=None, production_steps=None,
        )
        assert plan["nvt_steps"] == 250_000
        assert plan["npt_steps"] == 500_000
        assert plan["production_steps"] == 1_000_000

    def test_duration_ns_is_production_only(self):
        """duration_ns sets *production* time; equilibration uses fixed defaults.

        A 10 ns "simulation" means 10 ns of production. NVT and NPT keep
        the default lengths (500 ps + 1 ns) — equilibration
        doesn't scale with production length because reaching a stable
        ensemble takes the same wall time regardless.
        """
        plan = _runner.plan_stages(
            duration_ns=10.0, timestep_fs=2.0,
            nvt_steps=None, npt_steps=None, production_steps=None,
        )
        # 10 ns @ 2 fs = 5,000,000 steps of production
        assert plan["production_steps"] == 5_000_000
        # Equilibration: fixed defaults regardless of production length
        assert plan["nvt_steps"] == _runner.DEFAULT_NVT_STEPS    # 250k = 500 ps
        assert plan["npt_steps"] == _runner.DEFAULT_NPT_STEPS    # 500k = 1 ns

    def test_long_production_does_not_balloon_equilibration(self):
        """For a 1000 ns production, equilibration stays at the fixed defaults.

        The whole point of decoupling: 100 ns + 100 ns + 1000 ns would be
        absurd; the user wants 1000 ns of production, period.
        """
        plan = _runner.plan_stages(
            duration_ns=1000.0, timestep_fs=2.0,
            nvt_steps=None, npt_steps=None, production_steps=None,
        )
        assert plan["production_steps"] == 500_000_000     # 1000 ns
        assert plan["nvt_steps"] == _runner.DEFAULT_NVT_STEPS   # still 500 ps
        assert plan["npt_steps"] == _runner.DEFAULT_NPT_STEPS   # still 1 ns

    def test_ns_flavored_equilibration_kwargs(self):
        """nvt_duration_ns / npt_duration_ns let users specify equilibration in ns."""
        plan = _runner.plan_stages(
            duration_ns=10.0, timestep_fs=2.0,
            nvt_steps=None, npt_steps=None, production_steps=None,
            nvt_duration_ns=2.0, npt_duration_ns=5.0,
        )
        # 2 ns @ 2 fs = 1,000,000 steps; 5 ns = 2,500,000 steps
        assert plan["nvt_steps"] == 1_000_000
        assert plan["npt_steps"] == 2_500_000

    def test_per_stage_override_wins(self):
        plan = _runner.plan_stages(
            duration_ns=10.0,
            timestep_fs=2.0,
            nvt_steps=42,                  # explicit step count beats everything
            npt_steps=None,
            production_steps=None,
            nvt_duration_ns=99.0,          # ignored — step count wins
        )
        assert plan["nvt_steps"] == 42
        # Other stages still come from their respective sources
        assert plan["npt_steps"] == _runner.DEFAULT_NPT_STEPS
        assert plan["production_steps"] == 5_000_000

    def test_zero_duration_runs_no_production(self):
        # `duration_ns` is the production length, so zero equilibrates and
        # stops. It used to fall through to the 2 ns default while the cost
        # estimate counted no production.
        plan = _runner.plan_stages(
            duration_ns=0.0, timestep_fs=2.0,
            nvt_steps=None, npt_steps=None, production_steps=None,
        )
        assert plan["nvt_steps"] == 250_000
        assert plan["production_steps"] == 0


class TestTrajectoryInterval:
    def test_short_run_uses_min_floor(self):
        # 1000 steps total -> would compute 0 frames with default 2000 target
        interval = _runner.trajectory_interval_for(1000, target_frames=2000)
        # Floor is min_interval=100
        assert interval >= 100

    def test_long_run_scales_up(self):
        # 50 M steps, target 2000 frames -> 25,000 steps/frame
        interval = _runner.trajectory_interval_for(50_000_000)
        assert interval == 25_000

    def test_zero_steps_returns_default(self):
        assert _runner.trajectory_interval_for(0) == _runner.DEFAULT_TRAJECTORY_INTERVAL_STEPS


# ===========================================================================
# Lazy OpenMM import
# ===========================================================================
class TestLazyImport:
    def test_module_imports_without_openmm(self):
        """Importing simulation.runner doesn't require OpenMM."""
        # If this test runs, the import already succeeded — but be explicit
        from fastmdxplora.simulation import runner  # noqa: F401

    def test_import_error_message_mentions_conda(self):
        """When OpenMM is missing the error tells the user how to install."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "openmm" or name.startswith("openmm."):
                raise ImportError(f"No module named {name!r}")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with pytest.raises(ImportError, match="conda-forge"):
                _runner._import_openmm()


# ===========================================================================
# Platform selection
# ===========================================================================
class TestPlatformSelection:
    def test_auto_tries_in_priority_order(self):
        """auto should try CUDA, then OpenCL, then CPU until one loads."""
        # Build a fake Platform class where only "CPU" succeeds
        attempts = []

        class FakePlatform:
            @staticmethod
            def getPlatformByName(name):
                attempts.append(name)
                if name == "CPU":
                    return MagicMock(name="CPU")
                raise Exception(f"no {name}")

        omm = {"openmm": MagicMock(Platform=FakePlatform)}
        platform, props, name = _runner.select_platform(omm, requested="auto")
        assert attempts == ["CUDA", "OpenCL", "CPU"]
        assert name == "CPU"
        # CPU should not have a Precision property
        assert "Precision" not in props

    def test_explicit_request_does_not_fall_back(self):
        """If user asks for CUDA and CUDA fails, we raise rather than try CPU."""

        class FakePlatform:
            @staticmethod
            def getPlatformByName(name):
                raise Exception("nope")

        omm = {"openmm": MagicMock(Platform=FakePlatform)}
        with pytest.raises(RuntimeError, match="No usable OpenMM platform"):
            _runner.select_platform(omm, requested="CUDA")

    def test_gpu_platform_gets_precision_property(self):
        class FakePlatform:
            @staticmethod
            def getPlatformByName(name):
                return MagicMock(name=name)

        omm = {"openmm": MagicMock(Platform=FakePlatform)}
        _, props, name = _runner.select_platform(
            omm, requested="CUDA", precision="mixed"
        )
        assert name == "CUDA"
        assert props.get("Precision") == "mixed"


# ===========================================================================
# Pipeline graceful degradation
# ===========================================================================
class TestPipelineGracefulDegradation:
    def test_missing_setup_outputs_writes_manifest(self, tmp_path: Path):
        """No setup/system.xml -> fail with an informative note."""
        orch = MagicMock()
        orch.output_dir = tmp_path
        orch._presenter = None
        out = tmp_path / "simulation"
        out.mkdir()

        with patch.object(_pipeline, "missing_dependencies", return_value=[]):
            with pytest.raises(RuntimeError, match="setup outputs are missing"):
                _pipeline.run(orchestrator=orch, output_dir=out)

        assert (out / "simulation_parameters.json").exists()
        manifest = json.loads((out / "simulation_parameters.json").read_text(encoding="utf-8"))
        joined_notes = " ".join(manifest["notes"])
        assert "Setup outputs not found" in joined_notes

    @pytest.mark.skipif(HAS_OPENMM, reason="run only when OpenMM is absent")
    def test_openmm_missing_skips_with_note(self, stub_orchestrator, tmp_path: Path):
        """Without OpenMM, the phase fails with installation guidance."""
        out = stub_orchestrator.output_dir / "simulation"
        out.mkdir()
        with pytest.raises(RuntimeError, match="conda install -c conda-forge"):
            _pipeline.run(orchestrator=stub_orchestrator, output_dir=out)
        assert (out / "simulation_parameters.json").exists()
        # Real DCD should NOT exist
        assert not (out / "production.dcd").exists()
        manifest = json.loads((out / "simulation_parameters.json").read_text(encoding="utf-8"))
        joined_notes = " ".join(manifest["notes"])
        assert "OpenMM" in joined_notes


# ===========================================================================
# Mock-based: pipeline calls the runner with the right plumbing
# ===========================================================================
class TestPipelineCallsRunner:
    def test_pipeline_invokes_run_simulation(self, stub_orchestrator):
        """Setup outputs present + runner mocked -> pipeline calls runner."""
        out = stub_orchestrator.output_dir / "simulation"
        out.mkdir()

        fake_result = _runner.SimulationResult(
            trajectory=out / "production.dcd",
            topology=out / "topology.pdb",
            final_state=out / "state_final.xml",
            energy_csv=out / "energy.csv",
            log_file=out / "simulation.log",
            platform_used="CPU",
            n_production_frames=1000,
            duration_ns_actual=2.0,
        )
        # Create the stub files so pipeline can record them
        for p in (fake_result.trajectory, fake_result.topology,
                  fake_result.final_state, fake_result.energy_csv, fake_result.log_file):
            p.write_text("stub")

        with patch.object(_pipeline, "run_simulation", return_value=fake_result, create=True):
            # Force the import inside the try block to succeed
            import sys
            from types import ModuleType
            fake_mod = ModuleType("fastmdxplora.simulation.runner")
            fake_mod.run_simulation = lambda **kw: fake_result
            with patch.dict(sys.modules, {"fastmdxplora.simulation.runner": fake_mod}):
                _pipeline.run(orchestrator=stub_orchestrator, output_dir=out)

        manifest = json.loads((out / "simulation_parameters.json").read_text(encoding="utf-8"))
        assert manifest["platform_used"] == "CPU"
        assert manifest["n_production_frames"] == 1000
        assert manifest["duration_ns_actual"] == 2.0

    def test_pipeline_user_options_reach_manifest(self, stub_orchestrator):
        """User-supplied options are recorded in the manifest."""
        out = stub_orchestrator.output_dir / "simulation"
        out.mkdir()

        fake_result = _runner.SimulationResult(
            trajectory=out / "production.dcd",
            topology=out / "topology.pdb",
            final_state=out / "state_final.xml",
            energy_csv=out / "energy.csv",
            log_file=out / "simulation.log",
            platform_used="CUDA",
            n_production_frames=2000,
            duration_ns_actual=4.0,
        )
        for p in (fake_result.trajectory, fake_result.topology,
                  fake_result.final_state, fake_result.energy_csv, fake_result.log_file):
            p.write_text("stub")

        import sys
        from types import ModuleType
        fake_mod = ModuleType("fastmdxplora.simulation.runner")
        fake_mod.run_simulation = lambda **kw: fake_result
        with patch.dict(sys.modules, {"fastmdxplora.simulation.runner": fake_mod}):
            _pipeline.run(
                orchestrator=stub_orchestrator,
                output_dir=out,
                duration_ns=4.0,
                temperature_K=310.0,
                platform="CUDA",
            )

        manifest = json.loads((out / "simulation_parameters.json").read_text(encoding="utf-8"))
        assert manifest["parameters"]["duration_ns"] == 4.0
        assert manifest["parameters"]["temperature_K"] == 310.0
        assert manifest["parameters"]["platform"] == "CUDA"


# ===========================================================================
# Mock-based: runner builds the right integrator and reporters
# ===========================================================================
class TestRunnerWiring:
    def test_runner_calls_correct_integrator_constructor(self, tmp_path: Path):
        """Verify LangevinMiddleIntegrator gets the user temperature."""
        captured = {}

        def fake_langevin(temp, friction, dt):
            captured["temp"] = temp
            captured["friction"] = friction
            captured["dt"] = dt
            return MagicMock()

        # Build a comprehensive fake OpenMM dict
        fake_omm = _build_fake_omm()
        fake_omm["openmm"].LangevinMiddleIntegrator = fake_langevin

        # Stub setup files
        system_xml = tmp_path / "system.xml"
        state_xml = tmp_path / "state.xml"
        topo = tmp_path / "topology.pdb"
        for p in (system_xml, state_xml, topo):
            p.write_text("<x />" if p.suffix == ".xml" else "ATOM\nEND\n")

        with patch.object(_runner, "_import_openmm", return_value=fake_omm):
            _runner.run_simulation(
                system_xml=system_xml, state_xml=state_xml,
                topology_pdb=topo, output_dir=tmp_path / "out",
                temperature_K=310.0, friction_per_ps=2.0, timestep_fs=4.0,
                nvt_steps=0, npt_steps=0, production_steps=0,  # skip MD
                minimize=False,
            )

        # Captured values are Quantities — our fake unit module made them
        # pass through as numbers
        assert captured["temp"] == 310.0
        assert captured["friction"] == 2.0
        assert captured["dt"] == 4.0

    def test_runner_writes_all_artifacts(self, tmp_path: Path):
        """With everything skipped, the runner still writes topology, state, log."""
        system_xml = tmp_path / "system.xml"
        state_xml = tmp_path / "state.xml"
        topo = tmp_path / "topology.pdb"
        for p in (system_xml, state_xml, topo):
            p.write_text("<x />" if p.suffix == ".xml" else "ATOM\nEND\n")

        with patch.object(_runner, "_import_openmm", return_value=_build_fake_omm()):
            result = _runner.run_simulation(
                system_xml=system_xml, state_xml=state_xml,
                topology_pdb=topo, output_dir=tmp_path / "out",
                nvt_steps=0, npt_steps=0, production_steps=0,
                minimize=False,
            )

        # The four mandatory artifacts
        assert result.final_state.exists()
        assert result.log_file.exists()
        assert result.topology.exists()
        # energy.csv is only opened when MD runs (and the reporter is
        # attached only at the NVT step). With 0 steps it's still
        # attached but nothing is written yet — file may or may not
        # exist depending on OpenMM internals. Don't assert on it here.

    def test_minimization_resets_velocities_to_simulation_temperature(self, tmp_path: Path):
        """After minimization, inherited setup velocities are replaced."""
        system_xml = tmp_path / "system.xml"
        state_xml = tmp_path / "state.xml"
        topo = tmp_path / "topology.pdb"
        for p in (system_xml, state_xml, topo):
            p.write_text("<x />" if p.suffix == ".xml" else "ATOM\nEND\n")

        fake_omm = _build_fake_omm()
        fake_context = fake_omm["Simulation"].return_value.context

        with patch.object(_runner, "_import_openmm", return_value=fake_omm):
            result = _runner.run_simulation(
                system_xml=system_xml,
                state_xml=state_xml,
                topology_pdb=topo,
                output_dir=tmp_path / "out",
                temperature_K=100.0,
                random_seed=123,
                nvt_steps=0,
                npt_steps=0,
                production_steps=0,
                minimize=True,
            )

        fake_context.setVelocitiesToTemperature.assert_called_once_with(100.0, 123)
        assert result.minimized_state == tmp_path / "out" / "state_minimized.xml"
        assert result.minimized_state.exists()

    def test_validation_rejects_nan_positions(self):
        """State validation fails clearly before opaque OpenMM NaN crashes."""
        fake_omm = _build_fake_omm()
        bad_state = MagicMock()
        bad_state.getPositions.return_value = [[0.0, float("nan"), 0.0]]
        bad_state.getPotentialEnergy.return_value = 0.0
        fake_simulation = MagicMock()
        fake_simulation.context.getState.return_value = bad_state

        with pytest.raises(RuntimeError, match="positions contain NaN or Inf"):
            _runner._validate_state_finite(fake_omm, fake_simulation, stage="NVT")
        with pytest.raises(RuntimeError, match="simulate-timestep-fs"):
            _runner._validate_state_finite(fake_omm, fake_simulation, stage="NVT")

    def test_runner_missing_input_raises(self, tmp_path: Path):
        """Missing system.xml raises FileNotFoundError before any OpenMM work."""
        with patch.object(_runner, "_import_openmm", return_value=_build_fake_omm()):
            with pytest.raises(FileNotFoundError, match="system_xml"):
                _runner.run_simulation(
                    system_xml=tmp_path / "missing.xml",
                    state_xml=tmp_path / "also_missing.xml",
                    topology_pdb=tmp_path / "no_topo.pdb",
                    output_dir=tmp_path / "out",
                )


# ===========================================================================
# Defaults
# ===========================================================================
class TestDefaults:
    def test_pipeline_default_simulation_lengths(self):
        d = _pipeline.DEFAULTS
        assert d["timestep_fs"] == 2.0
        assert d["temperature_K"] == 300.0
        # Step counts come from runner defaults via plan_stages
        assert d["nvt_steps"] is None  # falls through to runner default
        assert d["npt_steps"] is None

    def test_runner_step_defaults(self):
        # These are the default simulation-length values
        assert _runner.DEFAULT_NVT_STEPS == 250_000
        assert _runner.DEFAULT_NPT_STEPS == 500_000
        assert _runner.DEFAULT_PRODUCTION_STEPS == 1_000_000
        assert _runner.DEFAULT_TIMESTEP_FS == 2.0
        assert _runner.DEFAULT_TEMPERATURE_K == 300.0

    def test_runner_barostat_defaults(self):
        assert _runner.DEFAULT_PRESSURE_BAR == 1.0
        assert _runner.DEFAULT_BAROSTAT_FREQUENCY == 25

    def test_there_is_no_preset(self) -> None:
        """A `gentle` preset dropped the temperature to 100 K along with
        shortening the run. That is not a smoke test, it is different physics:
        water is ice at 100 K, and a run that survives there says nothing
        about whether the system is stable at 300 K -- which is the question a
        smoke test is asked.

        Shortening a run at the conditions it will actually use is simpler and
        more informative, and is what the documentation teaches.
        """
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        assert PHASE_SCHEMAS["simulation"].get("preset") is None

    def test_nothing_still_reads_one(self) -> None:
        import inspect

        from fastmdxplora.simulation import pipeline
        from fastmdxplora.utils import presenter

        for module in (pipeline, presenter):
            source = inspect.getsource(module)
            assert "_SIM_PRESETS" not in source
            assert 'options.get("preset")' not in source


# ===========================================================================
# Real end-to-end (skipped when OpenMM absent)
# ===========================================================================
@pytest.mark.skipif(not HAS_OPENMM, reason="needs openmm")
class TestRealSimulation:
    """End-to-end test using a real OpenMM run on a small system."""

    def test_minimal_system_through_full_pipeline(self, tmp_path: Path):
        """Build a tiny water box, parameterize it, run a few hundred MD steps."""
        import openmm
        from openmm import unit
        from openmm.app import (
            ForceField, HBonds, Modeller, PDBFile, PME,
        )

        # Build a single-water system via Modeller + amber14
        # (we don't need PDBFixer for this — water has no missing atoms)
        water_pdb_str = (
            "CRYST1   20.000   20.000   20.000  90.00  90.00  90.00 P 1\n"
            "ATOM      1  O   HOH A   1      10.000  10.000  10.000  1.00  0.00           O\n"
            "ATOM      2  H1  HOH A   1      10.800  10.500  10.000  1.00  0.00           H\n"
            "ATOM      3  H2  HOH A   1       9.200  10.500  10.000  1.00  0.00           H\n"
            "END\n"
        )
        seed_pdb = tmp_path / "seed.pdb"
        seed_pdb.write_text(water_pdb_str)

        pdb = PDBFile(str(seed_pdb))
        ff = ForceField("amber14-all.xml", "amber14/tip3pfb.xml")
        modeller = Modeller(pdb.topology, pdb.positions)
        # Padding must be large enough that the box edge is at least twice
        # the nonbonded cutoff below (0.9 nm), or PME rejects it. 1.0 nm
        # padding around the water gives a ~2 nm box (half-box >= cutoff).
        modeller.addSolvent(ff, padding=1.0 * unit.nanometer)
        system = ff.createSystem(
            modeller.topology,
            nonbondedMethod=PME,
            nonbondedCutoff=0.9 * unit.nanometer,
            constraints=HBonds,
        )

        # Serialize to setup-style XMLs
        setup_dir = tmp_path / "setup"
        setup_dir.mkdir()
        with (setup_dir / "system.xml").open("w") as fh:
            fh.write(openmm.XmlSerializer.serialize(system))

        # Build an initial State via a Context
        integ = openmm.VerletIntegrator(0.001 * unit.picoseconds)
        ctx = openmm.Context(system, integ)
        ctx.setPositions(modeller.positions)
        ctx.setVelocitiesToTemperature(300 * unit.kelvin)
        state = ctx.getState(getPositions=True, getVelocities=True,
                             enforcePeriodicBox=True)
        with (setup_dir / "state.xml").open("w") as fh:
            fh.write(openmm.XmlSerializer.serialize(state))
        with (setup_dir / "topology.pdb").open("w") as fh:
            PDBFile.writeFile(modeller.topology, modeller.positions, fh)

        # Run the runner
        result = _runner.run_simulation(
            system_xml=setup_dir / "system.xml",
            state_xml=setup_dir / "state.xml",
            topology_pdb=setup_dir / "topology.pdb",
            output_dir=tmp_path / "sim",
            nvt_steps=100, npt_steps=100, production_steps=200,
            trajectory_interval_steps=50,
            state_interval_steps=50,
            platform="CPU",  # avoid GPU dependency in CI
        )

        # All artifacts on disk
        assert result.trajectory.exists()
        assert result.final_state.exists()
        assert result.energy_csv.exists()
        assert result.log_file.exists()
        # n_production_frames consistent with planning
        assert result.n_production_frames == 200 // 50

        # Round-trip the final state
        with result.final_state.open() as fh:
            final = openmm.XmlSerializer.deserialize(fh.read())
        # Same number of particles as the system we built
        assert isinstance(final, openmm.State)


# ===========================================================================
# Helpers
# ===========================================================================
def _build_fake_omm() -> dict:
    """A fake OpenMM dict that supports the runner's call sequence."""
    fake_unit = MagicMock(
        kelvin=1, picoseconds=1, femtoseconds=1, bar=1, nanometer=1,
        kilojoules_per_mole=1, amu=1,
    )

    fake_system = MagicMock()
    fake_system.addForce = MagicMock(return_value=0)

    fake_xmlserializer = MagicMock()
    fake_xmlserializer.deserialize = MagicMock(return_value=fake_system)
    fake_xmlserializer.serialize = MagicMock(return_value="<xml/>")

    fake_pdbfile_cls = MagicMock()
    fake_pdbfile_cls.return_value.topology = MagicMock()
    fake_pdbfile_cls.writeFile = MagicMock()

    fake_state = MagicMock()
    fake_state.getPositions.return_value = [[0.0, 0.0, 0.0]]
    fake_state.getPotentialEnergy.return_value = 0.0
    fake_context = MagicMock()
    fake_context.getState = MagicMock(return_value=fake_state)
    fake_context.setState = MagicMock()
    fake_context.setVelocitiesToTemperature = MagicMock()

    fake_simulation_cls = MagicMock()
    fake_simulation_inst = MagicMock()
    fake_simulation_inst.context = fake_context
    fake_simulation_inst.reporters = []
    fake_simulation_inst.step = MagicMock()
    fake_simulation_inst.minimizeEnergy = MagicMock()
    fake_simulation_cls.return_value = fake_simulation_inst

    fake_openmm = MagicMock()
    fake_openmm.LangevinMiddleIntegrator = MagicMock(return_value=MagicMock())
    fake_openmm.MonteCarloBarostat = MagicMock(return_value=MagicMock())
    fake_openmm.Context = MagicMock(return_value=fake_context)
    fake_openmm.XmlSerializer = fake_xmlserializer

    # Platform that succeeds for CPU
    class FakePlatform:
        @staticmethod
        def getPlatformByName(name):
            return MagicMock(name=name)
    fake_openmm.Platform = FakePlatform

    return {
        "openmm": fake_openmm,
        "unit": fake_unit,
        "DCDReporter": MagicMock(return_value=MagicMock()),
        "StateDataReporter": MagicMock(return_value=MagicMock()),
        "CheckpointReporter": MagicMock(return_value=MagicMock()),
        "Simulation": fake_simulation_cls,
        "PDBFile": fake_pdbfile_cls,
    }


class TestSimulatingFromASystemPreparedElsewhere:
    """A run ordinarily prepares its own system into ``setup/`` beside its
    results. ``prepared_from`` points somewhere else instead: a run that
    already prepared this molecule.

    Useful on its own -- a reference system carried from a cluster, or one
    prepared once by hand and reused -- and it is what lets a set of runs
    share a preparation rather than each solvating separately.
    """

    @staticmethod
    def _orchestrator(output_dir):
        class _Stub:
            pass

        stub = _Stub()
        stub.output_dir = Path(output_dir)
        return stub

    def test_the_setting_is_declared_where_settings_are_declared(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = PHASE_SCHEMAS["simulation"].get("prepared_from")
        assert field is not None
        assert field.default is None, "a run prepares its own unless told otherwise"

    def test_so_the_command_line_has_it_without_being_told(self) -> None:
        """The flags are generated from the schema, which is the point of
        generating them."""
        from fastmdxplora.cli.main import _PHASE_SPEC

        table, _prefix = _PHASE_SPEC["simulate"]
        assert "prepared_from" in {dest for _flag, dest, _kw in table}

    def test_without_it_a_run_reads_its_own_setup(self, tmp_path) -> None:
        where, named = _pipeline._where_the_system_was_prepared(
            self._orchestrator(tmp_path), None)
        assert where == tmp_path / "setup"
        assert named is False

    def test_with_it_the_run_reads_what_it_was_pointed_at(self, tmp_path) -> None:
        elsewhere = tmp_path / "reference" / "setup"
        where, named = _pipeline._where_the_system_was_prepared(
            self._orchestrator(tmp_path), str(elsewhere))
        assert where == elsewhere
        assert named is True

    def test_a_relative_path_is_relative_to_where_you_typed_it(self) -> None:
        """Not to the output directory, which is somewhere the person running
        the command has not necessarily looked."""
        where, _named = _pipeline._where_the_system_was_prepared(
            self._orchestrator("/somewhere/else"), "runs/reference/setup")
        assert where == Path.cwd() / "runs/reference/setup"

    def test_a_path_that_holds_no_system_is_refused_as_a_wrong_path(
        self, tmp_path
    ) -> None:
        """Rather than as a setup phase that has not run yet. It was named
        explicitly, so 'run setup first' would send somebody to fix something
        that is not broken.

        The likeliest mistake is naming the run directory instead of the
        setup directory inside it, so the message says which is wanted.
        """
        empty = tmp_path / "not-a-setup-directory"
        empty.mkdir()
        with pytest.raises(RuntimeError) as raised:
            _pipeline.run(orchestrator=self._orchestrator(tmp_path),
                          output_dir=tmp_path / "simulation",
                          prepared_from=str(empty))
        assert "prepared_from" in str(raised.value)
        assert "setup directory" in str(raised.value)

    def test_a_missing_setup_still_says_to_run_setup(
        self, tmp_path, monkeypatch
    ) -> None:
        """The other message is still the right one when nothing was named.

        With the backends declared present, because a missing one is reported
        first and is the more useful thing to say -- so on a machine without
        OpenMM this test would be reading that message instead, and passing or
        failing on what happens to be installed rather than on the code. It
        did: green on a laptop with OpenMM, red in CI without it.
        """
        monkeypatch.setattr(_pipeline, "missing_dependencies", lambda: [])
        (tmp_path / "simulation").mkdir()
        with pytest.raises(RuntimeError) as raised:
            _pipeline.run(orchestrator=self._orchestrator(tmp_path),
                          output_dir=tmp_path / "simulation")
        assert "Run the setup phase first" in str(raised.value)
