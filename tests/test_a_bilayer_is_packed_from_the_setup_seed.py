"""A bilayer is packed from the setup seed, and a packing that runs away is
tried again.

OpenMM's `Modeller.addMembrane` relaxes the lipids round the protein with a
Langevin integrator it makes itself and never seeds. With one
`setup.random_seed`, four preparations of 2POR gave four systems (279,726 to
279,807 atoms), and on 2026-10-04 the network corpus stopped 2POR with
"Particle coordinate is NaN" after 14 minutes of packing, where it had packed
the night before.
"""

from __future__ import annotations

import hashlib
import json
import random

import pytest

openmm = pytest.importorskip("openmm")


class _Modeller:
    """Packs as OpenMM does as far as the seed goes: makes its integrator
    through the name `openmm.app.modeller` imported, and runs it or fails."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.seeds: list[int] = []
        self.asked: list[dict] = []

    def addMembrane(self, forcefield, **kwargs):
        pytest.importorskip("openmm")
        from openmm.app import modeller as module

        self.asked.append(kwargs)
        integrator = module.LangevinIntegrator(10.0, 50.0, 0.001)
        self.seeds.append(integrator.getRandomNumberSeed())
        outcome = self.outcomes.pop(0)
        if outcome is not None:
            raise outcome


def _nan():
    return openmm.OpenMMException("Particle coordinate is NaN.  For more "
                                  "information, see https://github.com/openmm/openmm/wiki")


def test_the_packing_is_seeded_from_the_setup_stream_and_recorded():
    pytest.importorskip("openmm")
    from openmm.app import modeller as module

    from fastmdxplora.setup.prepare import _pack_the_bilayer

    original = module.LangevinIntegrator
    random.seed(11)
    first = _Modeller([None])
    packed = _pack_the_bilayer(first, None, "POPC", membraneCenterZ=0.0)
    random.seed(11)
    again = _Modeller([None])
    assert _pack_the_bilayer(again, None, "POPC", membraneCenterZ=0.0) == packed

    assert packed["packing_seed"] == first.seeds[0] == again.seeds[0] != 0
    assert packed["packing_attempts"] == 1
    assert first.asked[0] == {"lipidType": "POPC", "membraneCenterZ": 0.0}
    # Put back: nothing after setup makes a seeded integrator by accident.
    assert module.LangevinIntegrator is original
    assert original(300, 1, 0.002).getRandomNumberSeed() == 0


def test_a_packing_that_runs_away_is_tried_again_with_the_next_seed(caplog):
    from fastmdxplora.setup.prepare import _pack_the_bilayer

    random.seed(5)
    modeller = _Modeller([_nan(), None])
    packed = _pack_the_bilayer(modeller, None, "POPC")

    random.seed(5)
    drawn = [random.randint(1, 2**31 - 1) for _ in range(2)]
    assert modeller.seeds == drawn
    assert packed == {"packing_seed": drawn[1], "packing_attempts": 2}


def test_every_packing_running_away_is_refused_with_the_seeds_tried():
    from fastmdxplora.refusals import StudyError
    from fastmdxplora.setup.prepare import PACKING_ATTEMPTS, _pack_the_bilayer

    random.seed(3)
    modeller = _Modeller([_nan() for _ in range(PACKING_ATTEMPTS)])
    with pytest.raises(StudyError) as refused:
        _pack_the_bilayer(modeller, None, "POPC")

    assert refused.value.code == "setup.membrane.packing_failed"
    assert refused.value.refusal.details["seeds"] == modeller.seeds
    assert len(modeller.seeds) == PACKING_ATTEMPTS == 3
    message = str(refused.value)
    assert "POPC" in message and "NaN" in message
    assert all(str(seed) in message for seed in modeller.seeds)
    assert "setup.random_seed" in message


def test_any_other_failure_is_not_tried_again():
    from fastmdxplora.setup.prepare import _pack_the_bilayer

    other = openmm.OpenMMException("Called setPositions() on a Context with "
                                   "the wrong number of positions")
    modeller = _Modeller([other, None])
    with pytest.raises(openmm.OpenMMException, match="wrong number"):
        _pack_the_bilayer(modeller, None, "POPC")
    assert len(modeller.seeds) == 1


def test_a_packing_openmm_makes_some_other_way_is_said_not_to_repeat(monkeypatch):
    """Should a later OpenMM make its integrator otherwise, the seed reaches
    nothing: the record says no packing seed, and the log says it will not
    repeat, rather than recording a seed that was never used."""
    pytest.importorskip("openmm")
    import logging

    from openmm.app import modeller as module

    from fastmdxplora.setup.prepare import _pack_the_bilayer

    class Elsewhere:
        def addMembrane(self, forcefield, **kwargs):
            openmm.LangevinIntegrator(10.0, 50.0, 0.001)

    said: list[str] = []

    class Heard(logging.Handler):
        def emit(self, record):
            said.append(record.getMessage())

    log = logging.getLogger("fastmdx.setup.prepare")
    heard = Heard()
    log.addHandler(heard)
    try:
        packed = _pack_the_bilayer(Elsewhere(), None, "POPC")
        monkeypatch.delattr(module, "LangevinIntegrator")
        without = _pack_the_bilayer(Elsewhere(), None, "POPC")
    finally:
        log.removeHandler(heard)
    assert packed == without == {"packing_seed": None, "packing_attempts": 1}
    assert sum("will not repeat exactly" in line for line in said) == 2


def test_a_refusal_goes_through_preparation_as_itself(tmp_path, monkeypatch):
    """`prepare_system` turns a ValueError from the packing into a force
    field's missing template; the refusal is a ValueError too, and must
    reach the person as the packing's own."""
    pytest.importorskip("openmm")
    from openmm.app import Modeller

    from fastmdxplora.refusals import StudyError
    from fastmdxplora.setup import membrane
    from fastmdxplora.setup.prepare import prepare_system
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    monkeypatch.setattr(membrane, "place_for_membrane", lambda t, p, **k: membrane.Placement(
        positions=p, record={"lipid": "POPC", "placed_by": "a stand-in"}))
    monkeypatch.setattr(membrane, "check_chains_point_the_same_way", lambda *a, **k: None)
    monkeypatch.setattr(Modeller, "addMembrane",
                        lambda self, *a, **k: (_ for _ in ()).throw(_nan()))
    structure = tmp_path / "peptide.pdb"
    structure.write_text(TRI_ALANINE, encoding="utf-8")
    with pytest.raises(StudyError) as refused:
        prepare_system(prepared_pdb=str(structure), output_dir=str(tmp_path / "setup"),
                       membrane="POPC")
    assert refused.value.code == "setup.membrane.packing_failed"


@pytest.mark.slow
def test_one_seed_packs_the_same_bilayer_twice(tmp_path):
    """Setup for real, twice, on a tripeptide in OPM's frame with one
    `setup.random_seed`: the same system, byte for byte, and the packing seed
    in the record. Before the packing was seeded, three such preparations
    gave three different files."""
    pytest.importorskip("pdbfixer")
    from fastmdxplora import FastMDXplora
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    atoms = [line for line in TRI_ALANINE.splitlines() if line.startswith(("ATOM", "HETATM"))]
    opm = tmp_path / "tri_opm.pdb"
    opm.write_text("REMARK      1/2 of bilayer thickness:   13.0\n" + "\n".join(atoms) + "\n"
                   "HETATM 9001  N   DUM  9001      20.000  20.000 -13.000\n"
                   "HETATM 9002  O   DUM  9002      20.000  20.000  13.000\nEND\n",
                   encoding="utf-8")

    def prepared(name):
        study = FastMDXplora(system=str(opm), output_dir=str(tmp_path / name),
                             options={"setup": {"membrane": "DMPC", "forcefield": "amber14",
                                                "random_seed": 7}})
        results = study.explore(include_phase=["setup"])
        assert results[0].status == "ok", results[0].message
        setup = tmp_path / name / "setup"
        record = json.loads((setup / "setup_parameters.json").read_text(encoding="utf-8"))
        return hashlib.sha256((setup / "solvated.pdb").read_bytes()).hexdigest(), record

    first, record = prepared("first")
    second, _ = prepared("second")
    assert first == second
    assert record["bilayer"]["packing_attempts"] == 1
    assert isinstance(record["bilayer"]["packing_seed"], int)
