"""A report names the software that produced each phase, not its own.

The Methods and Reproducibility sections named the FastMDXplora and the
libraries installed where the report was written. A study simulated under
2.5.4 and analysed again under 2.5.8, which is how the benchmark runs were
re-read for the papers, was said to have been set up and simulated with
2.5.8, with whatever OpenMM the analysing machine had. Each phase records
what produced it (`produced_by` in the Manifest); the report reads that.

The record gained the two programs that decide a number and are not Python
modules: AmberTools, whose `sqm` gives every ligand its AM1-BCC charges, and
PLUMED, which computes every bias.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastmdxplora.provenance import environment_record
from fastmdxplora.report.context import load_phase_context
from fastmdxplora.report.document import _by_phase, _methods_section


def _produced(version: str, **packages: str) -> dict:
    return {"version": version, "host": "h",
            "environment": {"python": "3.11.9", "platform": "linux", **packages}}


def _study(root: Path, phases: list[dict]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps({"phases": phases}), encoding="utf-8")
    return root


def _ok(name: str, produced_by: dict) -> dict:
    return {"name": name, "status": "ok", "produced_by": produced_by}


def _methods(root: Path, results: list | None = None) -> str:
    here = SimpleNamespace(output_dir=root, results=results or [])
    return _methods_section(root, load_phase_context(root), here)


class TestTheMethodsNamesWhatRan:

    def test_a_study_analysed_again_under_a_later_release(self, tmp_path) -> None:
        root = _study(tmp_path / "run", [
            _ok("setup", _produced("2.5.4", openmm="8.4.0", mdtraj="1.10.3")),
            _ok("simulation", _produced("2.5.4", openmm="8.4.0", mdtraj="1.10.3")),
            _ok("analysis", _produced("2.5.8", mdtraj="1.11.1", numpy="2.1.0")),
        ])
        text = _methods(root)
        assert ("**Software.** System setup and simulation were performed with "
                "FastMDXplora 2.5.4, and analysis with FastMDXplora 2.5.8.") in text
        assert "MDTraj 1.10.3 (setup and simulation) and 1.11.1 (analysis)" in text
        assert "OpenMM 8.4.0" in text and "NumPy 2.1.0" in text
        assert "installed where this report was written" not in text

    def test_one_release_throughout(self, tmp_path) -> None:
        root = _study(tmp_path / "run", [
            _ok(name, _produced("2.5.8", openmm="8.6.1")) for name in
            ("setup", "simulation", "analysis")])
        text = _methods(root)
        assert ("System setup, simulation and analysis were performed with "
                "FastMDXplora 2.5.8.") in text
        assert "**Tools.** FastMDXplora calls OpenMM 8.6.1." in text

    def test_only_the_phases_that_ran_are_credited(self, tmp_path) -> None:
        """An analysis of an existing trajectory was not set up here."""
        root = _study(tmp_path / "run", [_ok("analysis", _produced("2.5.8"))])
        assert "Analysis was performed with FastMDXplora 2.5.8." in _methods(root)

    def test_this_sessions_phases_are_read_from_the_run(self, tmp_path) -> None:
        """The Manifest is written after the last phase, so the phases of the
        session writing the report are not in it yet."""
        root = _study(tmp_path / "run", [_ok("setup", _produced("2.5.4"))])
        now = SimpleNamespace(name="simulation", status="ok",
                              produced_by=_produced("2.5.8"))
        text = _methods(root, [now])
        assert ("System setup was performed with FastMDXplora 2.5.4, and "
                "simulation with FastMDXplora 2.5.8.") in text

    def test_a_version_carried_over_from_an_older_manifest(self, tmp_path) -> None:
        root = _study(tmp_path / "run", [
            _ok("simulation", {"version": "2.5.4", "inferred": True})])
        text = _methods(root)
        assert "Simulation was performed with FastMDXplora 2.5.4 or earlier." in text
        # No phase recorded its libraries, so the ones named are said to be
        # this machine's.
        assert "installed where this report was written" in text

    def test_a_run_with_no_record_says_so(self, tmp_path) -> None:
        text = _methods(tmp_path)
        assert "the run does not record which version produced it" in text
        assert "System setup, simulation and analysis were performed" not in text

    def test_a_system_prepared_by_another_study(self, tmp_path) -> None:
        """`setup_from`: the system was produced by what that study says."""
        other = _study(tmp_path / "other", [_ok("setup", _produced("2.5.6"))])
        (other / "setup").mkdir()
        (other / "setup" / "setup_parameters.json").write_text("{}", encoding="utf-8")
        root = _study(tmp_path / "run", [
            {"name": "setup", "status": "skipped", "taken_from": str(other / "setup")},
            _ok("simulation", _produced("2.5.8")),
        ])
        assert ("System setup was performed with FastMDXplora 2.5.6, and "
                "simulation with FastMDXplora 2.5.8.") in _methods(root)

    def test_a_package_loaded_without_a_version(self, tmp_path) -> None:
        root = _study(tmp_path / "run", [
            _ok("simulation", _produced("2.5.8", plumed="loaded"))])
        assert "PLUMED (version not recorded)" in _methods(root)


class TestTheReproducibilityLines:

    def test_one_value_everywhere_is_one_value(self) -> None:
        assert _by_phase("Python", {"setup": "3.11.9"}, "3.11.9") == "- **Python**: `3.11.9`"

    def test_values_that_differ_are_given_by_phase(self) -> None:
        line = _by_phase("FastMDXplora version",
                         {"setup": "2.5.4", "simulation": "2.5.4", "analysis": "2.5.8"},
                         "2.5.8")
        assert line == ("- **FastMDXplora version**: `2.5.4` (setup, simulation); "
                        "`2.5.8` (analysis, this report)")


class TestTheChargesAndTheBiasAreRecorded:

    @pytest.fixture
    def conda(self, tmp_path, monkeypatch) -> Path:
        meta = tmp_path / "conda-meta"
        meta.mkdir()
        monkeypatch.setattr(sys, "prefix", str(tmp_path))
        return meta

    @staticmethod
    def _installed(meta: Path, name: str, version: str) -> None:
        (meta / f"{name}-{version}-h0_0.json").write_text(
            json.dumps({"name": name, "version": version}), encoding="utf-8")

    def test_nothing_is_said_of_programs_the_run_did_not_use(self, conda, monkeypatch) -> None:
        self._installed(conda, "ambertools", "24.8")
        self._installed(conda, "plumed", "2.9.2")
        monkeypatch.setitem(sys.modules, "openff.toolkit", None)
        monkeypatch.setitem(sys.modules, "openmmplumed", None)
        record = environment_record()
        assert (record["am1bcc"], record["ambertools"], record["plumed"]) == (None, None, None)

    def test_the_charge_program_and_its_version(self, conda, monkeypatch) -> None:
        from fastmdxplora.setup import ligand

        self._installed(conda, "ambertools", "24.8")
        # A package whose name begins the same is not it.
        (conda / "ambertools-extras-1.0-h0_0.json").write_text(
            json.dumps({"name": "ambertools-extras", "version": "1.0"}), encoding="utf-8")
        monkeypatch.setitem(sys.modules, "openff.toolkit", SimpleNamespace())
        monkeypatch.setattr(ligand, "am1bcc_provider", lambda: "AmberTools")
        record = environment_record()
        assert (record["am1bcc"], record["ambertools"]) == ("AmberTools", "24.8")

    def test_openeye_names_no_ambertools(self, conda, monkeypatch) -> None:
        from fastmdxplora.setup import ligand

        self._installed(conda, "ambertools", "24.8")
        monkeypatch.setitem(sys.modules, "openff.toolkit", SimpleNamespace())
        monkeypatch.setattr(ligand, "am1bcc_provider", lambda: "OpenEye")
        record = environment_record()
        assert (record["am1bcc"], record["ambertools"]) == ("OpenEye", None)

    def test_plumed_from_conda_or_loaded_without_a_version(self, conda, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "openmmplumed", SimpleNamespace())
        assert environment_record()["plumed"] == "loaded"
        self._installed(conda, "plumed", "2.9.2")
        assert environment_record()["plumed"] == "2.9.2"


class TestWhatIsNotRecordedIsNotInvented:

    def test_a_conda_record_that_cannot_be_read_is_passed_over(self, tmp_path, monkeypatch) -> None:
        meta = tmp_path / "conda-meta"
        meta.mkdir()
        (meta / "plumed-2.9.2-h0_0.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(sys, "prefix", str(tmp_path))
        monkeypatch.setitem(sys.modules, "openmmplumed", SimpleNamespace())
        assert environment_record()["plumed"] == "loaded"

    def test_a_charge_provider_that_raises_names_none(self, monkeypatch) -> None:
        from fastmdxplora.setup import ligand

        def broken():
            raise RuntimeError("no toolkit registry")

        monkeypatch.setitem(sys.modules, "openff.toolkit", SimpleNamespace())
        monkeypatch.setattr(ligand, "am1bcc_provider", broken)
        record = environment_record()
        assert (record["am1bcc"], record["ambertools"]) == (None, None)

    def test_a_phase_that_names_no_version_is_left_out(self, tmp_path) -> None:
        root = _study(tmp_path / "run", [
            _ok("setup", {"host": "h"}), _ok("simulation", _produced("2.5.8"))])
        assert "Simulation was performed with FastMDXplora 2.5.8." in _methods(root)
