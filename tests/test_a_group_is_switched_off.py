"""Alchemical free energy: the schedule's rules, the restraint's correction,
two legs combined, the record read, and a water decoupled from water."""

from __future__ import annotations

import json
import math

import pytest

pytest.importorskip("openmmtools")


def test_a_stiff_restraint_confines_to_its_sphere():
    from fastmdxplora.simulation.alchemical import (KB, STANDARD_VOLUME_NM3,
                                                     restraint_free_energy)

    kt = KB * 300.0
    sphere = 4 / 3 * math.pi * 0.5 ** 3
    assert restraint_free_energy(0.5, 1e8, 300.0) == pytest.approx(
        -kt * math.log(sphere / STANDARD_VOLUME_NM3), abs=0.01)
    # A softer wall lets the ligand further out: a larger volume, a lower cost.
    assert restraint_free_energy(0.5, 100.0, 300.0) < restraint_free_energy(0.5, 1e8, 300.0)
    # 1 M is one molecule in 1.66 nm^3.
    assert STANDARD_VOLUME_NM3 == pytest.approx(1.6605, abs=1e-4)


@pytest.mark.parametrize("block, said", [
    ({"lambda_electrostatics": [1, 0], "lambda_sterics": [1, 0.5, 0]}, "as long as each other"),
    ({"lambda_electrostatics": [1, 0.5, 0], "lambda_sterics": [1, 0.5, 0]}, "turn the charges off first"),
    ({"lambda_electrostatics": [0.5, 0], "lambda_sterics": [1, 0]}, "both lambdas 1"),
    ({"restraint": {"radius_nm": 0.5}}, "with `receptor`"),
    ({"windows": 12}, "has no setting windows"),
])
def test_schedules_that_cannot_be_run_are_refused(block, said):
    from fastmdxplora.config.loader import ConfigError
    from fastmdxplora.simulation.alchemical import plan_from_block

    with pytest.raises(ConfigError, match=said):
        plan_from_block(block, "LIG")


def test_the_group_is_the_ligand_unless_named():
    from fastmdxplora.simulation.alchemical import plan_from_block

    plan = plan_from_block({}, "LIG")
    assert plan.group == "resname LIG" and len(plan.sterics) == 16
    assert plan_from_block({"group": "resid 3"}, "LIG").group == "resid 3"


def test_alchemical_beside_replica_exchange_is_refused():
    from fastmdxplora.config.loader import ConfigError, _check_alchemical

    with pytest.raises(ConfigError, match="cannot be run with replica_exchange"):
        _check_alchemical({"alchemical": {}, "replica_exchange": {}})


def test_two_legs_make_a_binding_free_energy():
    from fastmdxplora.refusals import StudyError
    from fastmdxplora.simulation.alchemical import binding_free_energy

    complex_leg = {"dG_decouple_kj_per_mol": 60.0, "uncertainty_kj_per_mol": 0.3,
                   "restraint": {"dG_restraint_kj_per_mol": 7.0}}
    solvent_leg = {"dG_decouple_kj_per_mol": 25.0, "uncertainty_kj_per_mol": 0.4}
    found = binding_free_energy(complex_leg, solvent_leg)
    assert found["dG_bind_kj_per_mol"] == pytest.approx(25.0 - 60.0 - 7.0)
    assert found["uncertainty_kj_per_mol"] == pytest.approx(0.5)
    with pytest.raises(StudyError, match="without a restraint"):
        binding_free_energy({"dG_decouple_kj_per_mol": 60.0, "uncertainty_kj_per_mol": 0.3},
                            solvent_leg)


def test_an_alchemical_run_is_not_split():
    from fastmdxplora.simulation.resume import segmentability

    verdict = segmentability({"simulation": {"alchemical": {}}})
    assert verdict.allowed is False and verdict.method == "alchemical"


def test_the_analysis_reads_the_record_and_says_where_overlap_is_thin(tmp_path):
    from fastmdxplora.analysis.alchemical_free_energy import AlchemicalFreeEnergy

    (tmp_path / "simulation").mkdir()
    (tmp_path / "simulation" / "alchemical.json").write_text(json.dumps({
        "group": "resid 0", "dG_decouple_kj_per_mol": 25.1, "uncertainty_kj_per_mol": 0.6,
        "neighbour_overlap": [0.2, 0.01], "thin_overlap": "Neighbouring states 1-2 ..."}))
    analysis = AlchemicalFreeEnergy(output_dir=tmp_path / "analysis")
    result = analysis.run(None)
    assert result.status == "ok", result.message
    assert result.data["overlap"].tolist() == [0.2, 0.01]
    assert analysis.findings["free_energy"]["dG_decouple_kj_per_mol"] == 25.1
    assert "thin_overlap" in analysis.findings


def test_plumed_turned_off_is_not_a_bias():
    from fastmdxplora.config.loader import _check_alchemical

    _check_alchemical({"alchemical": {}, "plumed": {"enabled": False}})
