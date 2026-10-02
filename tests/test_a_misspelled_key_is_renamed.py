"""A misspelled key's fix renames the key.

"Set `simulation.temprature_K` to one of: agent, barostat_frequency, ..."
asked for the misspelled key's value to be a key name: the fix for every
refusal with permitted values was built the same way, including a key the
schema does not know, whose permitted list is of keys. Found running
`fastmdx mcp` from an AI app (2026-10-01).
"""

from __future__ import annotations

import pytest

from fastmdxplora.config.loader import validate_config
from fastmdxplora.refusals import refusal_of
from fastmdxplora.remedies import remedy_for


def _fix(config):
    with pytest.raises(Exception) as refused:
        validate_config(config)
    return remedy_for(refusal_of(refused.value)).fix


def test_a_misspelled_phase_setting_is_renamed():
    assert _fix({"simulation": {"temprature_K": 300}}) == (
        "Rename `simulation.temprature_K` to `temperature_K`.")


def test_a_misspelled_top_level_key_is_renamed():
    assert _fix({"sytems": [{"system": "1L2Y"}]}) == "Rename `sytems` to `systems`."


def test_a_key_like_nothing_lists_the_keys_it_could_be():
    fix = _fix({"simulation": {"zzzqqq": 1}})
    assert fix.startswith("Use one of the `simulation` settings: agent, ")
    assert "temperature_K" in fix and "Set `" not in fix
    assert _fix({"zzzqqq": 1}).startswith("Use one of the top-level keys: ")


def test_a_value_out_of_its_choices_is_still_set():
    assert _fix({"setup": {"box_shape": "sphere"}}) == (
        "Set `setup.box_shape` to one of: cube, dodecahedron, octahedron.")
