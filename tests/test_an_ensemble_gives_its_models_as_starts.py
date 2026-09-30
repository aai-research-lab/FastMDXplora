"""One model of an ensemble is prepared by name, and the models are starts.

An NMR entry deposits its molecule as an ensemble of models; setup prepared
the first and said nothing of the others. `setup.model` names the model to
prepare, as the file numbers them. Unset on a file holding several, setup
and the builder say how many there are. Swept, the models are replicas
started from different structures: the independent starts a stopping rule
asks for, which replicas over a seed, sharing one structure, give only as
far as their dynamics carry them apart.
"""

from __future__ import annotations

import copy
import json

import pytest

from fastmdxplora.refusals import StudyError
from fastmdxplora.setup.ensemble import models_in, one_model
from tests.test_what_setup_will_build_is_said_before_it_runs import TRIPEPTIDE, _file


def _shifted(text: str, by: float) -> str:
    out = []
    for line in text.splitlines():
        if line.startswith("ATOM"):
            x = float(line[30:38]) + by
            line = f"{line[:30]}{x:8.3f}{line[38:]}"
        out.append(line)
    return "\n".join(out) + "\n"


ATOMS = TRIPEPTIDE.replace("END\n", "")
ENSEMBLE = ("HEADER    AN ENSEMBLE\n"
            + "".join(f"MODEL     {n:4d}\n{_shifted(ATOMS, 0.5 * (n - 1))}ENDMDL\n"
                      for n in (1, 2, 3))
            + "CONECT  135  136\nEND\n")


class TestOneModel:
    def test_the_models_it_holds(self):
        assert models_in(ENSEMBLE.splitlines()) == [1, 2, 3]
        assert models_in(TRIPEPTIDE.splitlines()) == [1]
        assert models_in(["MODEL", "ATOM", "ENDMDL", "MODEL", "ENDMDL"]) == [1, 2]

    def test_the_one_named_and_everything_outside_the_models(self):
        kept = one_model(ENSEMBLE.splitlines(), 2)
        atoms = [line for line in kept if line.startswith("ATOM")]
        assert len(atoms) == 25 and float(atoms[0][30:38]) == pytest.approx(18.869 + 0.5)
        assert kept[0].startswith("HEADER") and "CONECT  135  136" in kept
        assert not any(line.startswith(("MODEL", "ENDMDL")) for line in kept)

    def test_a_file_of_one_model(self):
        assert one_model(TRIPEPTIDE.splitlines(), 1) == TRIPEPTIDE.splitlines()

    @pytest.mark.parametrize("asked", [4, 0, "two", True, None])
    def test_a_model_it_does_not_hold(self, asked):
        with pytest.raises(StudyError, match="holds 3 models, numbered 1, 2, 3") as refused:
            one_model(ENSEMBLE.splitlines(), asked)
        assert refused.value.code == "config.option.not_permitted"

    def test_a_long_ensemble_is_said_short(self):
        many = "".join(f"MODEL     {n:4d}\nENDMDL\n" for n in range(1, 41)).splitlines()
        with pytest.raises(StudyError, match=r"numbered 1, 2, 3, 4, 5, 6, \.\.\., 38, 39, 40"):
            one_model(many, 41)


class TestBeforeSetup:
    def test_the_estimate_reads_the_model_named(self, tmp_path):
        from fastmdxplora.setup.estimate import estimate_system

        path = _file(tmp_path, ENSEMBLE)
        first = estimate_system(path, {"nonbonded_method": "NoCutoff"})
        third = estimate_system(path, {"nonbonded_method": "NoCutoff", "model": 3})
        assert third.solute_atoms == first.solute_atoms
        assert third.centre_angstrom[0] == pytest.approx(first.centre_angstrom[0] + 1.0, abs=1e-3)
        with pytest.raises(StudyError):
            estimate_system(path, {"model": 7})

    def test_the_builder_says_it_holds_several(self):
        from fastmdxplora.advisories import advise

        said = [a for a in advise({"models": 38}) if a.setting == "model"]
        assert said and said[0].summary == "This structure holds 38 models."
        assert not [a for a in advise({"models": 38}, {"model": 4}) if a.setting == "model"]
        assert not [a for a in advise({"models": 1}) if a.setting == "model"]

    def test_the_plan_says_the_replicas(self):
        from fastmdxplora.gui.plan import plan_of

        plan = {line["label"]: line["value"] for line in plan_of(
            {"systems": [{"system": "1L2Y"}], "sweep": {"setup.model": [1, 5, 12]}})}
        assert plan["Replicas"] == "3, from models 1, 5, 12 of the ensemble"

    def test_a_stopping_rule_counts_them_as_independent_starts(self):
        from fastmdxplora.config.loader import validate_config

        config = {"systems": [{"system": "1L2Y"}], "sweep": {"setup.model": [1, 5, 12]},
                  "simulation": {"duration_ns": 5, "stop_when": {
                      "measures": [{"analysis": "rmsd", "standard_error": 0.01}],
                      "max_duration_ns": 50}}}
        validate_config(copy.deepcopy(config), require_systems=True)
        from fastmdxplora.batch.aggregate import SEED_AXES

        assert "setup.model" in SEED_AXES


def test_setup_prepares_the_model_named(tmp_path) -> None:
    pytest.importorskip("openmm")
    pytest.importorskip("pdbfixer")
    from fastmdxplora import FastMDXplora

    structure = _file(tmp_path, ENSEMBLE)

    def prepare(where, **setup):
        FastMDXplora(config_data={
            "systems": [{"id": "p", "system": str(structure)}], "include_phase": ["setup"],
            "setup": {"random_seed": 11, "heterogens": "drop", **setup}},
            output_dir=str(where)).explore(check=True)
        notes = json.loads((where / "setup" / "setup_parameters.json")
                           .read_text(encoding="utf-8"))["notes"]
        first = next(line for line in (where / "setup" / "input.pdb").read_text(
            encoding="utf-8").splitlines() if line.startswith("ATOM"))
        return notes, float(first[30:38])

    notes, x = prepare(tmp_path / "second", model=2)
    assert x == pytest.approx(18.869 + 0.5)
    assert "Model 2 of the 3 the structure holds is prepared." in notes
    notes, x = prepare(tmp_path / "unnamed")
    assert x == pytest.approx(18.869)
    assert any(note.startswith("The structure holds 3 models; model 1 is prepared.")
               for note in notes)
