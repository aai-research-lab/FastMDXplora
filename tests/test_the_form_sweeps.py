"""A sweep in the GUI's form, on the server's side of it.

The form had no way to hold a sweep: loading a study dropped it and building
one never wrote it, so the GUI was the one interface that could not run the
same study twice over a setting. The values typed into a row are read by the
rule the command line's --sweep reads, so the two cannot disagree.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mdtraj")

from fastmdxplora.batch.sweep import SweepError, text_from_values, values_from_text  # noqa: E402


@pytest.mark.parametrize("values", [
    [300, 310.5],
    [True, False],
    ["a.sdf", "b.sdf"],
    ["name CA, name CB", "protein"],        # a value holding a comma
    ["300", "true"],                         # strings that YAML would read otherwise
])
def test_what_the_form_shows_reads_back_as_what_was_meant(values):
    back = values_from_text(text_from_values(values))
    assert back == values and [type(v) for v in back] == [type(v) for v in values]


def test_an_empty_row_has_nothing_to_say():
    with pytest.raises(SweepError):
        values_from_text("  ")


def test_a_study_opened_in_the_form_keeps_its_sweep(tmp_path):
    import yaml

    from fastmdxplora.gui.config_builder import build_config, load_config_into_state

    sweep = {"simulation.temperature_K": [300.0, 310.0],
             "analysis.select_atoms": ["name CA, name CB", "protein"]}
    path = tmp_path / "study.yml"
    path.write_text(yaml.safe_dump({"systems": [{"system": "1UBQ"}], "sweep": sweep},
                                   sort_keys=False), encoding="utf-8")
    rows = load_config_into_state(str(path))["state"]["sweep"]
    assert [row["axis"] for row in rows] == list(sweep)
    assert build_config({"system": "1UBQ", "sweep": rows})["sweep"] == sweep


def test_the_rows_read_as_the_command_line_reads(tmp_path):
    from fastmdxplora.cli.main import _sweep_from_flags
    from fastmdxplora.gui.config_builder import build_config

    typed = {"simulation.temperature_K": "300, 310", "setup.keep_water": "true, false"}
    from_the_form = build_config({"system": "1UBQ", "sweep": [
        {"axis": axis, "values": text} for axis, text in typed.items()]})["sweep"]
    from_the_flags = _sweep_from_flags([f"{axis}={text}" for axis, text in typed.items()])
    assert from_the_form == from_the_flags


def test_a_row_left_empty_is_not_an_axis():
    from fastmdxplora.gui.config_builder import build_config

    config = build_config({"system": "1UBQ", "sweep": [
        {"axis": "simulation.temperature_K", "values": "300, 310"},
        {"axis": "", "values": ""}, {"axis": "setup.ph", "values": ""}]})
    assert config["sweep"] == {"simulation.temperature_K": [300, 310]}


def test_a_row_that_cannot_be_read_is_said_under_the_form():
    from fastmdxplora.gui.config_builder import config_yaml

    built = config_yaml({"system": "1UBQ", "sweep": [{"axis": "temperature", "values": "300"}]})
    assert built["ok"] is False and built["error"]


def test_the_form_is_offered_every_setting_that_takes_one_value():
    from fastmdxplora.gui.schema_payload import schema_payload

    axes = schema_payload()["sweep_axes"]
    assert "simulation.temperature_K" in axes and "setup.ph" in axes
    assert "simulation.umbrella" not in axes          # a block, not a value
