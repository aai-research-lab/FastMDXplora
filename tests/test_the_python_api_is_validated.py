"""A study given to the Python API is validated like any other.

The command line, the browser form, a configuration file and the language-
model interface all pass the validator. A system given to the Python API
with its options did not, so a misspelled `duraton_ns` ran the default
million production steps without a word and a temperature of -50 K was
planned as given.
"""

from __future__ import annotations

import pytest

from fastmdxplora import FastMDXplora
from fastmdxplora.config.loader import ConfigError
from fastmdxplora.refusals import refusal_of


@pytest.mark.parametrize("options, code, said", [
    ({"simulation": {"duraton_ns": 0.001}}, "config.option.unknown", "did you mean 'duration_ns'"),
    ({"simulation": {"temperature_K": -50}}, None, "below the smallest value"),
])
def test_a_setting_the_validator_refuses_is_refused_here(tmp_path, options, code, said) -> None:
    output = tmp_path / "study"
    # ConfigError, as a configuration file with the same settings raises.
    with pytest.raises(ConfigError) as caught:
        FastMDXplora(system="1UBQ", options=options, output_dir=str(output))
    assert said in str(caught.value)
    if code:
        assert refusal_of(caught.value).code == code
    # Refused before anything was created.
    assert not output.exists()


def test_settings_the_validator_accepts_are_taken(tmp_path) -> None:
    study = FastMDXplora(system="1UBQ", options={"simulation": {"duration_ns": 0.001}},
                         output_dir=str(tmp_path / "study"))
    assert study.options["simulation"]["duration_ns"] == 0.001
