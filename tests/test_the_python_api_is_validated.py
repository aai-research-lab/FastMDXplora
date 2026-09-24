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


@pytest.mark.parametrize("settings, said", [
    ({"duraton_ns": 0.001}, "did you mean 'duration_ns'"),
    ({"temperature_K": -50}, "below the smallest value"),
])
def test_one_phase_run_on_its_own_is_validated(tmp_path, settings, said) -> None:
    # simulate(...) passed its settings straight to the phase, which refused
    # only because setup had not run, never over the settings themselves.
    study = FastMDXplora(system="1UBQ", output_dir=str(tmp_path / "study"))
    with pytest.raises(ConfigError) as caught:
        study.simulate(**settings)
    assert said in str(caught.value)
    assert not (tmp_path / "study" / "simulation").exists()


def test_a_phase_command_is_validated(tmp_path, capsys) -> None:
    # `fastmdx simulate` builds the same keyword arguments from its flags.
    from fastmdxplora.cli.main import main

    try:
        code = main(["simulate", "--system", "1UBQ", "--output", str(tmp_path / "study"),
                     "--temperature-K", "-50"])
    except ConfigError as refused:
        said = str(refused)
    else:
        assert code != 0
        printed = capsys.readouterr()
        said = printed.out + printed.err
    assert "below the smallest value" in said
