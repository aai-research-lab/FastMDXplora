"""`verlet` and `variable_verlet` are not offered to a new study.

Both have no thermostat, and a new study naming one is refused when it is
checked (`test_an_integrator_holds_the_temperature.py`). They were still
the schema's choices, so the command line, the GUI's control, the config
template and the JSON schema an AI app reads offered two integrators the
check then refused. An older study's record that names one still reads.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from fastmdxplora.config.loader import ConfigError, validate_config
from fastmdxplora.config.schema import SIMULATION

WITHDRAWN = ("verlet", "variable_verlet")


def _field():
    return SIMULATION.get("integrator")


class TestTheyAreNotOffered:

    def test_not_in_the_schema_s_choices(self):
        assert not set(WITHDRAWN) & set(_field().choices)
        assert set(_field().withdrawn) == set(WITHDRAWN)

    def test_not_in_the_json_schema(self):
        from fastmdxplora.config.describe import schema_as_json

        enum = schema_as_json(phases=["simulation"])
        found = [entry for entry in _walk(enum) if isinstance(entry, dict)
                 and "langevin_middle" in (entry.get("enum") or [])]
        assert found, "the integrator's enum was not found"
        for entry in found:
            assert not set(WITHDRAWN) & set(entry["enum"])

    def test_not_in_the_gui_s_control(self):
        from fastmdxplora.gui.schema_payload import field_payload

        payload = field_payload(_field(), "simulation")
        assert "langevin_middle" in payload["choices"]
        assert not set(WITHDRAWN) & set(payload["choices"])

    def test_not_in_the_template(self):
        from fastmdxplora.config.describe import describe_field

        said = describe_field(_field())
        assert "one of: langevin_middle, langevin, brownian, variable_langevin" in said

    def test_not_on_the_command_line(self, capsys):
        from fastmdxplora.cli.main import main

        with pytest.raises(SystemExit):
            main(["explore", "--system", "1L2Y", "--simulate-integrator", "verlet",
                  "--dry-run"])
        assert "--simulate-integrator: invalid choice: 'verlet'" in capsys.readouterr().err

    def test_their_tolerance_names_only_one_offered(self):
        assert "variable_verlet" not in SIMULATION.get("integrator_error_tolerance").help


@pytest.mark.parametrize("name", WITHDRAWN)
def test_a_new_study_naming_one_gets_the_thermostat_refusal(name):
    """Not the generic "does not accept": the reason, as before."""
    with pytest.raises(ConfigError) as caught:
        validate_config({"systems": [{"id": "a", "system": "x.pdb"}],
                         "simulation": {"integrator": name}}, require_systems=True)
    said = str(caught.value)
    assert said.startswith(f"The {name} integrator has no thermostat")
    assert "does not accept" not in said
    refusal = caught.value.refusal
    assert refusal.code == "config.option.not_permitted"
    assert refusal.details["suggestion"] == "langevin_middle"
    assert refusal.details["permitted"] == ["langevin_middle", "langevin", "variable_langevin",
                                           "brownian"]


def test_a_name_never_offered_is_still_refused_as_unknown():
    with pytest.raises(ConfigError, match="does not accept 'leapfrog'"):
        validate_config({"simulation": {"integrator": "leapfrog"}})


@pytest.mark.parametrize("name", WITHDRAWN)
class TestAnOlderStudyThatRanOne:

    @staticmethod
    def _study(root: Path, name: str) -> Path:
        from fastmdxplora.config.recorded import RECORDED

        root.mkdir(parents=True, exist_ok=True)
        (root / RECORDED).write_text(yaml.safe_dump({
            "output": str(root), "systems": [{"id": "a", "system": "x.pdb"}],
            "simulation": {"integrator": name, "timestep_fs": 1.0,
                           "duration_ns": 0.01}}), encoding="utf-8")
        return root

    def test_its_record_reads(self, tmp_path, name):
        from fastmdxplora.config.recorded import RECORDED, phase_settings

        root = self._study(tmp_path / "study", name)
        assert phase_settings(root / RECORDED, "simulation", study=root)["integrator"] == name

    def test_its_record_is_carried_on_from(self, tmp_path, name):
        """`fastmdx run --output STUDY` reads the settings the study recorded
        to run its later phases again there."""
        from fastmdxplora.cli.main import _the_study_s_config

        root = self._study(tmp_path / "study", name)
        args = argparse.Namespace(output_dir=str(root), system=None,
                                  include=["analysis", "report"])
        config = _the_study_s_config(args)
        assert config is not None
        assert config["simulation"]["integrator"] == name

    def test_its_production_reads_for_an_extension(self, tmp_path, name):
        from fastmdxplora.simulation.resume import production_unreadable

        root = self._study(tmp_path / "study", name)
        assert production_unreadable(root) is None

    def test_its_methods_say_what_ran(self, name):
        from fastmdxplora.report.methods import methods_paragraphs

        text = methods_paragraphs(Path("."), {}, {"parameters": {
            "integrator": name, "timestep_fs": 1.0, "temperature_K": 300.0,
            "production_steps": 1000}})
        assert "Verlet" in text
        assert "friction" not in text


def _walk(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)
