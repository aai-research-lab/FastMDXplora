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
    # The positions and the velocities to the last digit written. The
    # velocities agreed only to about one part in 10^8 while they were
    # drawn on the CPU platform, which does not draw the same velocities
    # twice from one seed; they are drawn on the Reference platform.
    positions = [[line for line in record["_state"].splitlines() if "<Position " in line]
                 for record in (one, two)]
    assert positions[0] == positions[1] and len(positions[0]) == one["n_atoms_solvated"]
    speeds = [[float(v) for line in record["_state"].splitlines() if "<Velocity " in line
               for v in re.findall(r'"(-?[0-9.eE+-]+)"', line)] for record in (one, two)]
    assert len(speeds[0]) == 3 * one["n_atoms_solvated"]
    assert speeds[0] == speeds[1]
    assert one["_state"] == two["_state"]
    assert one["random_seed"] == {"seed": 11, "drawn": False}
    # Asked for, it is in the config already, not a decision of the phase.
    assert "random_seed" not in one["resolved"]


def test_the_velocities_are_drawn_on_the_reference_platform(tmp_path, monkeypatch):
    """Whatever platform the machine offers: the CPU platform drew
    different velocities from one seed in one draw of four, and only the
    Reference platform drew the same bytes every time."""
    openmm = pytest.importorskip("openmm")

    made: list[str] = []
    real = openmm.Context

    def context(system, integrator, *rest):
        made.append(rest[0].getName() if rest else "default")
        return real(system, integrator, *rest)

    monkeypatch.setattr(openmm, "Context", context)
    _prepare(tmp_path / "platform", random_seed=3)
    # The last Context setup makes is the one the state is drawn in.
    assert made and made[-1] == "Reference"


def test_the_velocities_repeat_in_processes_of_their_own(tmp_path):
    """A first draw in a process was where the CPU platform varied, so the
    draw is made in fresh processes, from one system and one seed."""
    pytest.importorskip("openmm")
    import subprocess
    import sys

    one = _prepare(tmp_path / "source", random_seed=5)
    script = (
        "import sys, json\n"
        "from fastmdxplora import FastMDXplora\n"
        "FastMDXplora(config_data={'systems': [{'id': 'p', 'system': sys.argv[1]}],"
        " 'include_phase': ['setup'], 'setup': {'heterogens': 'drop', 'random_seed': 5}},"
        " output_dir=sys.argv[2]).explore(check=True)\n")
    states = []
    for n in range(3):
        out = tmp_path / f"fresh{n}"
        subprocess.run([sys.executable, "-c", script, str(tmp_path / "source" / "p.pdb"),
                        str(out)], check=True, capture_output=True)
        states.append((out / "setup" / "state.xml").read_text())
    assert states[0] == states[1] == states[2] == one["_state"]


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


def test_the_methods_say_a_seeded_bilayer_repeats(tmp_path):
    from fastmdxplora.report.methods import methods_paragraphs

    setup = {"_random_seed": 11, "membrane": "POPC",
             "bilayer": {"lipid": "POPC", "lipids": 442, "lipids_per_leaflet": [221, 221],
                         "placed_by": "fitted normal", "packing_seed": 99,
                         "packing_attempts": 1}}
    text = methods_paragraphs(tmp_path, setup, {})
    assert ("Hydrogens, ions and the bilayer's packing were placed with random "
            "seed 11 (`setup.random_seed`; the packing with seed 99 taken from "
            "it), which reproduces the solvated system.") in text
    unseeded = methods_paragraphs(tmp_path, {**setup, "bilayer": {
        k: v for k, v in setup["bilayer"].items() if not k.startswith("packing")}}, {})
    assert "except the bilayer's packing" in unseeded


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
