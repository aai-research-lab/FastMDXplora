"""A preparation can be repeated.

OpenMM's Modeller places the hydrogens it adds at random before minimising
them, and picks at random which waters become ions, both from Python's
`random`. Setup did not seed it, so one structure prepared twice gave two
systems: a decapeptide came out at 4,265 to 4,556 atoms in boxes 2.855 to
2.916 nm wide over six preparations, because the hydrogens set the solute's
extent and the box is sized from it. `setup.random_seed` seeds those
choices; unset, a seed is drawn and recorded, and the resolved config
carries it, so any preparation can be made again. Setup's minimisations run
on one CPU thread, since several threads add the forces in whatever order
they finish and moved a hydrogen by 0.03 Angstrom, enough to change the
water by nine atoms in one preparation of four.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

import pytest
import yaml

from tests.test_what_setup_will_build_is_said_before_it_runs import DECAPEPTIDE


def _prepare(root: Path, **setup) -> dict:
    pytest.importorskip("openmm")
    pytest.importorskip("pdbfixer")
    from fastmdxplora import FastMDXplora

    root.mkdir(parents=True, exist_ok=True)
    (root / "p.pdb").write_text(DECAPEPTIDE, encoding="utf-8")
    FastMDXplora(config_data={"systems": [{"id": "p", "system": str(root / "p.pdb")}],
                              "include_phase": ["setup"],
                              "setup": {"heterogens": "drop", **setup}},
                 output_dir=str(root / "out")).explore(check=True)
    record = json.loads((root / "out" / "setup" / "setup_parameters.json").read_text())
    record["_hydrogens"] = [tuple(float(line[k:k + 8]) for k in (30, 38, 46)) for line in
                            (root / "out" / "setup" / "prepared.pdb").read_text().splitlines()
                            if line.startswith("ATOM") and line[12:16].strip().startswith("H")]
    record["_state"] = (root / "out" / "setup" / "state.xml").read_text()
    record["_resolved_config"] = yaml.safe_load((root / "out" / "resolved_config.yml").read_text())
    return record


def test_the_same_seed_gives_the_same_system(tmp_path):
    one = _prepare(tmp_path / "one", random_seed=11)
    two = _prepare(tmp_path / "two", random_seed=11)
    assert one["n_atoms_solvated"] == two["n_atoms_solvated"]
    assert one["box"] == two["box"]
    assert one["_hydrogens"] == two["_hydrogens"]
    # The positions to the last digit written. The velocities are drawn
    # from the seed too, and agree to about one part in 10^8: OpenMM
    # applies the constraints to them in parallel.
    positions = [[line for line in record["_state"].splitlines() if "<Position " in line]
                 for record in (one, two)]
    assert positions[0] == positions[1] and len(positions[0]) == one["n_atoms_solvated"]
    speeds = [[float(v) for line in record["_state"].splitlines() if "<Velocity " in line
               for v in re.findall(r'"(-?[0-9.eE+-]+)"', line)] for record in (one, two)]
    assert len(speeds[0]) == 3 * one["n_atoms_solvated"]
    assert max(abs(a - b) for a, b in zip(*speeds)) < 1e-6
    assert one["random_seed"] == {"seed": 11, "drawn": False}
    # Asked for, it is in the config already, not a decision of the phase.
    assert "random_seed" not in one["resolved"]


def test_a_drawn_seed_is_recorded_and_repeats_the_preparation(tmp_path):
    first = _prepare(tmp_path / "first")
    drawn = first["random_seed"]
    assert drawn["drawn"] is True and drawn["seed"] > 0
    assert first["resolved"]["random_seed"] == drawn["seed"]
    assert first["_resolved_config"]["setup"]["random_seed"] == drawn["seed"]
    again = _prepare(tmp_path / "again", random_seed=drawn["seed"])
    assert again["n_atoms_solvated"] == first["n_atoms_solvated"]
    assert again["_hydrogens"] == first["_hydrogens"]


def test_pythons_own_random_state_is_left_as_it_was(tmp_path):
    random.seed(2026)
    before = random.getstate()
    _prepare(tmp_path / "state", random_seed=5)
    assert random.getstate() == before


def test_the_methods_say_it(tmp_path):
    from fastmdxplora.report.methods import methods_paragraphs

    record = _prepare(tmp_path / "methods", random_seed=11)
    text = methods_paragraphs(tmp_path / "methods" / "out", record, {})
    assert "Hydrogens and ions were placed with random seed 11 (`setup.random_seed`)" in text


def test_it_is_a_setting_and_a_seed_axis():
    from fastmdxplora.batch.aggregate import SEED_AXES
    from fastmdxplora.config.loader import validate_config
    from fastmdxplora.simulation.stopping import replicas_of

    validate_config({"setup": {"random_seed": 3}})
    assert "setup.random_seed" in SEED_AXES
    assert replicas_of({"systems": [{"system": "1UAO"}],
                        "sweep": {"setup.random_seed": [1, 2, 3]}}) == (True, 3)


def test_the_cpu_thread_count_is_put_back(monkeypatch):
    pytest.importorskip("openmm")
    from openmm import Platform

    from fastmdxplora.setup.pipeline import _one_cpu_thread

    cpu = Platform.getPlatformByName("CPU")
    before = cpu.getPropertyDefaultValue("Threads")
    restore = _one_cpu_thread()
    assert cpu.getPropertyDefaultValue("Threads") == "1"
    restore()
    assert cpu.getPropertyDefaultValue("Threads") == before


def test_without_a_cpu_platform_nothing_is_changed(monkeypatch):
    openmm = pytest.importorskip("openmm")
    from fastmdxplora.setup.pipeline import _one_cpu_thread

    def missing(name):
        raise openmm.OpenMMException(f"There is no registered Platform called {name}")
    monkeypatch.setattr(openmm.Platform, "getPlatformByName", staticmethod(missing))
    assert _one_cpu_thread()() is None
