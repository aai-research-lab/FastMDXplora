"""Tests for the full MD engine controls.

Covers the newly-added options:
  - Integrator selection (all six OpenMM integrators)
  - pressure_atm / pressure_bar resolution + conversion
  - GPU device-index selection
  - Checkpoint reporter wiring
  - create_system pass-throughs (nonbonded method, ewald tol, switching,
    dispersion correction, CM-motion removal)
  - fixed_pdb (skip PDBFixer)
  - Schema coverage: every new key validates
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fastmdxplora.config import validate_config, generate_template
import yaml

from fastmdxplora.simulation import runner as _runner


# ===========================================================================
# Integrator factory
# ===========================================================================
class TestIntegratorFactory:
    def _fake_omm(self):
        unit = MagicMock(kelvin=1, picoseconds=1, femtoseconds=1)
        openmm = MagicMock()
        return {"openmm": openmm, "unit": unit}

    @pytest.mark.parametrize("name,attr,friction", [
        ("langevin_middle", "LangevinMiddleIntegrator", 1.0),
        ("langevin", "LangevinIntegrator", 1.0),
        ("brownian", "BrownianIntegrator", 200.0),
        ("variable_langevin", "VariableLangevinIntegrator", 1.0),
    ])
    def test_each_integrator_constructs(self, name, attr, friction):
        omm = self._fake_omm()
        _runner._make_integrator(
            omm, name=name, temperature_K=300.0, friction_per_ps=friction,
            timestep_fs=2.0, error_tolerance=0.001, random_seed=None,
        )
        # The matching OpenMM constructor was called
        assert getattr(omm["openmm"], attr).called

    @pytest.mark.parametrize("name", ["verlet", "variable_verlet"])
    def test_an_integrator_with_no_thermostat_is_refused(self, name):
        omm = self._fake_omm()
        with pytest.raises(ValueError, match="no thermostat"):
            _runner._make_integrator(
                omm, name=name, temperature_K=300.0, friction_per_ps=1.0,
                timestep_fs=2.0, error_tolerance=0.001, random_seed=None,
            )
        assert not omm["openmm"].VerletIntegrator.called
        assert not omm["openmm"].VariableVerletIntegrator.called

    def test_unknown_integrator_raises(self):
        omm = self._fake_omm()
        with pytest.raises(ValueError, match="Unknown integrator"):
            _runner._make_integrator(
                omm, name="leapfrog", temperature_K=300.0, friction_per_ps=1.0,
                timestep_fs=2.0, error_tolerance=0.001, random_seed=None,
            )

    def test_random_seed_applied(self):
        omm = self._fake_omm()
        integ = MagicMock()
        omm["openmm"].LangevinMiddleIntegrator = MagicMock(return_value=integ)
        _runner._make_integrator(
            omm, name="langevin_middle", temperature_K=300.0, friction_per_ps=1.0,
            timestep_fs=2.0, error_tolerance=0.001, random_seed=42,
        )
        integ.setRandomNumberSeed.assert_called_once_with(42)

    def test_supported_integrators_list(self):
        assert "langevin_middle" in _runner.SUPPORTED_INTEGRATORS
        assert len(_runner.SUPPORTED_INTEGRATORS) == 6


# ===========================================================================
# Pressure unit handling
# ===========================================================================
class TestPressureUnits:
    def test_atm_to_bar_constant(self):
        assert _runner.ATM_TO_BAR == 1.01325

    def test_pressure_bar_used_directly(self, tmp_path):
        captured = {}
        self._run_capturing_barostat(tmp_path, captured, pressure_bar=2.0)
        assert captured["pressure_bar"] == 2.0

    def test_pressure_atm_converted(self, tmp_path):
        captured = {}
        self._run_capturing_barostat(tmp_path, captured, pressure_atm=1.0)
        # 1 atm -> 1.01325 bar
        assert abs(captured["pressure_bar"] - 1.01325) < 1e-9

    def test_bar_wins_when_both_given(self, tmp_path):
        captured = {}
        self._run_capturing_barostat(
            tmp_path, captured, pressure_bar=5.0, pressure_atm=1.0
        )
        assert captured["pressure_bar"] == 5.0

    def test_neither_defaults_to_one_bar(self, tmp_path):
        captured = {}
        self._run_capturing_barostat(tmp_path, captured)
        assert captured["pressure_bar"] == _runner.DEFAULT_PRESSURE_BAR

    def _run_capturing_barostat(self, tmp_path, captured, **pressure_kwargs):
        """Run the runner with NPT enabled, capturing the barostat pressure."""
        from tests.test_simulation_phase import _build_fake_omm

        omm = _build_fake_omm()

        def capture_barostat(pressure, temperature, freq):
            # pressure is (value * unit.bar); our fake unit.bar == 1
            captured["pressure_bar"] = pressure
            return MagicMock()

        omm["openmm"].MonteCarloBarostat = MagicMock(side_effect=capture_barostat)

        # Stub setup files
        for n in ("system.xml", "state.xml"):
            (tmp_path / n).write_text("<x/>")
        (tmp_path / "topology.pdb").write_text("ATOM\nEND\n")

        with patch.object(_runner, "_import_openmm", return_value=omm):
            _runner.run_simulation(
                system_xml=tmp_path / "system.xml",
                state_xml=tmp_path / "state.xml",
                topology_pdb=tmp_path / "topology.pdb",
                output_dir=tmp_path / "out",
                nvt_steps=0, npt_steps=10, production_steps=0,
                minimize=False,
                **pressure_kwargs,
            )


# ===========================================================================
# Device index
# ===========================================================================
class TestDeviceIndex:
    def test_cuda_device_index_property(self):
        class FakePlatform:
            @staticmethod
            def getPlatformByName(name):
                return MagicMock(name=name)
        omm = {"openmm": MagicMock(Platform=FakePlatform)}
        _, props, name = _runner.select_platform(
            omm, requested="CUDA", precision="mixed", device_index="1"
        )
        assert props["CudaDeviceIndex"] == "1"

    def test_opencl_device_index_property(self):
        class FakePlatform:
            @staticmethod
            def getPlatformByName(name):
                return MagicMock(name=name)
        omm = {"openmm": MagicMock(Platform=FakePlatform)}
        _, props, _ = _runner.select_platform(
            omm, requested="OpenCL", precision="single", device_index="0,1"
        )
        assert props["OpenCLDeviceIndex"] == "0,1"

    def test_cpu_ignores_device_index(self):
        class FakePlatform:
            @staticmethod
            def getPlatformByName(name):
                if name == "CPU":
                    return MagicMock(name="CPU")
                raise Exception("no")
        omm = {"openmm": MagicMock(Platform=FakePlatform)}
        _, props, name = _runner.select_platform(
            omm, requested="auto", device_index="0"
        )
        assert name == "CPU"
        assert "CudaDeviceIndex" not in props


# ===========================================================================
# Checkpoint reporter
# ===========================================================================
class TestCheckpointReporter:
    def test_attached_when_interval_positive(self):
        omm = {"CheckpointReporter": MagicMock(return_value="chkrep")}
        sim = MagicMock(reporters=[])
        r = _runner._attach_checkpoint_reporter(
            omm, sim, Path("/tmp/x.chk"), interval=1000
        )
        assert r == "chkrep"
        assert "chkrep" in sim.reporters

    def test_skipped_when_interval_zero(self):
        omm = {"CheckpointReporter": MagicMock()}
        sim = MagicMock(reporters=[])
        r = _runner._attach_checkpoint_reporter(
            omm, sim, Path("/tmp/x.chk"), interval=0
        )
        assert r is None
        assert sim.reporters == []


# ===========================================================================
# Schema coverage — every new option validates
# ===========================================================================
class TestNewOptionsValidate:
    def test_new_setup_options(self):
        validate_config({
            "systems": [{"id": "a", "system": "x.pdb"}],
            "setup": {
                "fixed_pdb": "prepared.pdb",
                "nonbonded_method": "PME",
                "ewald_error_tolerance": 0.0001,
                "use_switching_function": True,
                "switch_distance_nm": 0.85,
                "dispersion_correction": True,
                "remove_cm_motion": False,
            },
        })

    def test_new_simulation_options(self):
        validate_config({
            "systems": [{"id": "a", "system": "x.pdb"}],
            "simulation": {
                "integrator": "langevin",
                "integrator_error_tolerance": 0.002,
                "pressure_atm": 1.0,
                "device_index": "0",
                "checkpoint_interval_steps": 5000,
            },
        })

    def test_pressure_atm_is_numeric(self):
        from fastmdxplora.config import ConfigError
        with pytest.raises(ConfigError, match="should be"):
            validate_config({
                "systems": [{"id": "a", "system": "x.pdb"}],
                "simulation": {"pressure_atm": "high"},
            })

    def test_template_includes_new_options(self):
        text = generate_template()
        for key in ("integrator", "pressure_atm", "device_index",
                    "checkpoint_interval_steps", "nonbonded_method",
                    "fixed_pdb", "dispersion_correction"):
            assert key in text, f"template missing {key}"

    def test_template_still_valid_after_additions(self):
        text = generate_template()
        validate_config(yaml.safe_load(text))


# ===========================================================================
# create_system pass-throughs reach OpenMM
# ===========================================================================
class TestCreateSystemPassthrough:
    def test_nonbonded_method_resolved(self, tmp_path):
        """A non-PME method name is mapped to the right OpenMM enum."""
        from fastmdxplora.setup import prepare as _prepare

        captured = {}

        def fake_create_system(topology, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        fake_ff = MagicMock()
        fake_ff.createSystem = fake_create_system
        fake_modeller_inst = MagicMock(
            topology=MagicMock(getNumAtoms=MagicMock(return_value=10), getPeriodicBoxVectors=MagicMock(return_value=None)),
            positions=MagicMock(),
        )

        # Distinct sentinels for each method enum so we can assert which was used
        sentinels = {k: object() for k in
                     ["PME", "NoCutoff", "CutoffNonPeriodic", "CutoffPeriodic", "Ewald"]}
        fake_omm = {
            "openmm": MagicMock(
                XmlSerializer=MagicMock(serialize=MagicMock(return_value="<x/>")),
                VerletIntegrator=MagicMock(),
                Context=MagicMock(),
            ),
            "unit": MagicMock(nanometer=1, molar=1, kelvin=1, picoseconds=1, amu=1),
            "ForceField": MagicMock(return_value=fake_ff),
            "HBonds": object(),
            "Modeller": MagicMock(return_value=fake_modeller_inst),
            "PDBFile": MagicMock(
                side_effect=lambda p: MagicMock(topology=MagicMock(), positions=MagicMock())
            ),
            **sentinels,
        }
        fake_omm["PDBFile"].writeFile = MagicMock()

        prepared = tmp_path / "prepared.pdb"
        prepared.write_text("ATOM\nEND\n")

        with patch.object(_prepare, "_import_openmm", return_value=fake_omm):
            _prepare.prepare_system(
                prepared, tmp_path / "out",
                nonbonded_method="CutoffPeriodic",
                use_switching_function=True,
                dispersion_correction=True,
                remove_cm_motion=True,
            )

        # The CutoffPeriodic sentinel should have been passed as nonbondedMethod
        assert captured["nonbondedMethod"] is sentinels["CutoffPeriodic"]
        assert captured["removeCMMotion"] is True
        # CutoffPeriodic is a cutoff method -> dispersion correction applies
        assert captured["useDispersionCorrection"] is True
        # but it's not PME/Ewald -> no ewaldErrorTolerance
        assert "ewaldErrorTolerance" not in captured

    def test_pme_gets_ewald_tolerance(self, tmp_path):
        from fastmdxplora.setup import prepare as _prepare

        captured = {}

        def fake_create_system(topology, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        fake_ff = MagicMock()
        fake_ff.createSystem = fake_create_system
        fake_modeller_inst = MagicMock(
            topology=MagicMock(getNumAtoms=MagicMock(return_value=10), getPeriodicBoxVectors=MagicMock(return_value=None)),
            positions=MagicMock(),
        )
        fake_omm = {
            "openmm": MagicMock(
                XmlSerializer=MagicMock(serialize=MagicMock(return_value="<x/>")),
                VerletIntegrator=MagicMock(), Context=MagicMock(),
            ),
            "unit": MagicMock(nanometer=1, molar=1, kelvin=1, picoseconds=1, amu=1),
            "ForceField": MagicMock(return_value=fake_ff),
            "HBonds": object(),
            "Modeller": MagicMock(return_value=fake_modeller_inst),
            "PDBFile": MagicMock(
                side_effect=lambda p: MagicMock(topology=MagicMock(), positions=MagicMock())
            ),
            "PME": object(), "NoCutoff": object(), "CutoffNonPeriodic": object(),
            "CutoffPeriodic": object(), "Ewald": object(),
        }
        fake_omm["PDBFile"].writeFile = MagicMock()

        prepared = tmp_path / "prepared.pdb"
        prepared.write_text("ATOM\nEND\n")

        with patch.object(_prepare, "_import_openmm", return_value=fake_omm):
            _prepare.prepare_system(
                prepared, tmp_path / "out",
                nonbonded_method="PME",
                ewald_error_tolerance=0.0001,
            )
        assert captured["ewaldErrorTolerance"] == 0.0001

    def test_unknown_nonbonded_method_raises(self, tmp_path):
        from fastmdxplora.setup import prepare as _prepare

        fake_omm = {
            "openmm": MagicMock(), "unit": MagicMock(nanometer=1, molar=1),
            "ForceField": MagicMock(), "HBonds": object(),
            "Modeller": MagicMock(return_value=MagicMock(
                topology=MagicMock(getNumAtoms=MagicMock(return_value=1), getPeriodicBoxVectors=MagicMock(return_value=None)),
                positions=MagicMock())),
            "PDBFile": MagicMock(side_effect=lambda p: MagicMock(
                topology=MagicMock(), positions=MagicMock())),
            "PME": object(), "NoCutoff": object(), "CutoffNonPeriodic": object(),
            "CutoffPeriodic": object(), "Ewald": object(),
        }
        prepared = tmp_path / "prepared.pdb"
        prepared.write_text("ATOM\nEND\n")
        with patch.object(_prepare, "_import_openmm", return_value=fake_omm):
            with pytest.raises(ValueError, match="Unknown nonbonded_method"):
                _prepare.prepare_system(
                    prepared, tmp_path / "out", nonbonded_method="MagicPME"
                )


# ===========================================================================
# fixed_pdb (skip PDBFixer)
# ===========================================================================
class TestFixedPdb:
    def test_fixed_pdb_skips_pdbfixer(self, tmp_path):
        """When fixed_pdb is given, PDBFixer is not called and the file is copied."""
        from fastmdxplora.setup.pipeline import run as setup_run

        # A pre-fixed PDB the user supplies. PDB is a fixed-column format
        # (x/y/z in columns 31-38, 39-46, 47-54), so coordinates must be
        # column-aligned or a strict parser (OpenMM) misreads them.
        _ATOM = (
            "ATOM      1  CA  ALA A   1       "
            "0.000   0.000   0.000  1.00  0.00           C\nEND\n"
        )
        fixed = tmp_path / "already_fixed.pdb"
        fixed.write_text(_ATOM)

        # Input system (would normally go through PDBFixer)
        sys_pdb = tmp_path / "input.pdb"
        sys_pdb.write_text(_ATOM)

        orch = MagicMock()
        orch.system = str(sys_pdb)
        orch.output_dir = tmp_path
        orch._presenter = None

        out = tmp_path / "setup"
        out.mkdir()

        # Even if pdbfixer were importable, it must not be called. We patch it
        # to blow up if touched.
        # Mock prepare_system too: this test verifies PDBFixer is skipped and
        # the fixed PDB is copied through, not that a single-atom stub can be
        # solvated (it can't form a valid residue).
        with patch("fastmdxplora.setup.pdbfix.fix_pdb_with_pdbfixer",
                   side_effect=AssertionError("PDBFixer should be skipped")), \
             patch("fastmdxplora.setup.prepare.prepare_system", return_value={}):
            artifacts = setup_run(
                orchestrator=orch, output_dir=out, fixed_pdb=str(fixed),
            )

        # prepared.pdb exists (copied from fixed) and PDBFixer wasn't called
        assert (out / "prepared.pdb").exists()
        assert "prepared.pdb" in artifacts

    def test_fixed_pdb_missing_degrades(self, tmp_path):
        from fastmdxplora.setup.pipeline import run as setup_run

        sys_pdb = tmp_path / "input.pdb"
        sys_pdb.write_text("ATOM\nEND\n")
        orch = MagicMock()
        orch.system = str(sys_pdb)
        orch.output_dir = tmp_path
        orch._presenter = None
        out = tmp_path / "setup"
        out.mkdir()

        setup_run(
            orchestrator=orch, output_dir=out,
            fixed_pdb=str(tmp_path / "does_not_exist.pdb"),
        )
        # Graceful: manifest written, note recorded, no crash
        import json
        manifest = json.loads((out / "setup_parameters.json").read_text(encoding="utf-8"))
        assert any("fixed_pdb not found" in n for n in manifest["notes"])


# ===========================================================================
# CLI routing for the new flags (end-to-end into the manifest)
# ===========================================================================
class TestMDEngineCLIRouting:
    def _stub_pdb(self, tmp_path):
        p = tmp_path / "p.pdb"
        p.write_text(
            "ATOM      1  CA  ALA A   1       0.0   0.0   0.0  1.00  0.00           C\n"
            "END\n"
        )
        return p

    def test_simulate_flags_reach_manifest(self, tmp_path):
        import json
        from fastmdxplora.cli.main import main as cli_main

        pdb = self._stub_pdb(tmp_path)
        out = tmp_path / "run"
        rc = cli_main([
            "explore", "--system", str(pdb), "--output", str(out),
            "--include", "simulation",
            "--simulate-integrator", "langevin",
            "--simulate-pressure-atm", "1.0",
            "--simulate-checkpoint-interval-steps", "2500",
        ])
        assert rc == 1
        # Missing setup/backend is a real workflow error, but the manifest
        # still records the parameters that were requested.
        params = json.loads(
            (out / "simulation" / "simulation_parameters.json").read_text(encoding="utf-8")
        )["parameters"]
        assert params["integrator"] == "langevin"
        assert params["pressure_atm"] == 1.0
        assert params["checkpoint_interval_steps"] == 2500

    def test_setup_flags_reach_manifest(self, tmp_path):
        import json
        from unittest.mock import patch
        from fastmdxplora.cli.main import main as cli_main

        pdb = self._stub_pdb(tmp_path)
        out = tmp_path / "run"
        # Mock the chemistry: this test verifies the flag reaches the manifest,
        # not that the single-atom stub can be fixed/solvated.
        import shutil

        def _fake_fix(inp, outp, **kw):
            shutil.copy(inp, outp)

        with patch("fastmdxplora.setup.pdbfix.fix_pdb_with_pdbfixer",
                   side_effect=_fake_fix), \
             patch("fastmdxplora.setup.prepare.prepare_system", return_value={}):
            rc = cli_main([
                "explore", "--system", str(pdb), "--output", str(out),
                "--include", "setup",
                "--setup-nonbonded-method", "CutoffPeriodic",
            ])
        assert rc == 0
        params = json.loads(
            (out / "setup" / "setup_parameters.json").read_text(encoding="utf-8")
        )["parameters"]
        assert params["nonbonded_method"] == "CutoffPeriodic"
