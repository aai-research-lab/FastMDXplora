"""A block of settings, and a number that may be whole or not, as the command
line reads them.

Both were read as text. A block -- umbrella, steered, metadynamics -- arrived as
a string, so it could not be given on the command line at all, `cli_command`
wrote it as a Python repr, and the validator listed the string's characters as
unknown settings. A number arrived as "310".
"""

from __future__ import annotations

import shlex

import pytest

pytest.importorskip("mdtraj")

UMBRELLA = {"collective_variable": "distance", "selection_a": "resname BNZ",
            "selection_b": "protein", "from": 0.3, "to": 1.5, "n_windows": 7,
            "force_constant": 5000}


def _parse(argv):
    from fastmdxplora.cli.main import _build_explore_config, _build_parser

    return _build_explore_config(_build_parser().parse_args(argv))


@pytest.mark.parametrize("block", ["umbrella", "steered", "metadynamics"])
def test_a_block_is_read_as_a_mapping(block):
    config = _parse(["explore", "--system", "1UBQ",
                     f"--simulate-{block}", "{collective_variable: distance, from: 0.3}"])
    assert config["simulation"][block] == {"collective_variable": "distance", "from": 0.3}


def test_text_where_a_block_belongs_is_refused_on_the_command_line(capsys):
    with pytest.raises(SystemExit):
        _parse(["explore", "--system", "1UBQ", "--simulate-umbrella", "distance"])
    assert "expects a mapping of settings" in capsys.readouterr().err


def test_and_by_the_loader_by_name_rather_than_by_its_characters():
    from fastmdxplora.config.loader import ConfigError, normalise_config

    with pytest.raises(ConfigError) as refused:
        normalise_config({"systems": [{"system": "1UBQ"}],
                          "simulation": {"umbrella": "collective_variable: distance"}})
    assert refused.value.code == "config.option.wrong_type"
    assert "`simulation.umbrella` is a block of settings" in str(refused.value)
    assert "Unknown umbrella setting" not in str(refused.value)


def test_cli_command_writes_a_block_that_reads_back():
    from fastmdxplora.config.languages import cli_command

    command = cli_command({"systems": [{"system": "1UBQ"}], "simulation": {"umbrella": UMBRELLA}})
    assert "{'" not in command                      # not a Python repr
    assert _parse(shlex.split(command)[1:])["simulation"]["umbrella"] == UMBRELLA


def test_a_number_that_may_be_whole_is_a_number():
    # A generated flag for a setting declared int-or-float; the hand-written
    # ones say float, and give 310.0 for 310, which is theirs to say.
    whole = _parse(["explore", "--system", "1UBQ", "--setup-hydrogen-mass-amu", "3"])["setup"]["hydrogen_mass_amu"]
    part = _parse(["explore", "--system", "1UBQ", "--setup-hydrogen-mass-amu", "2.5"])["setup"]["hydrogen_mass_amu"]
    assert whole == 3 and isinstance(whole, int)
    assert part == 2.5 and isinstance(part, float)


def test_the_resolved_config_keeps_the_budget_a_number(tmp_path):
    import yaml

    from fastmdxplora.config import write_resolved_config

    written = write_resolved_config({"system": "1UBQ", "budget_hours": 0.5, "options": {}},
                                    tmp_path)
    assert yaml.safe_load(written.read_text(encoding="utf-8"))["budget_hours"] == 0.5


def test_a_script_for_an_umbrella_study_makes_its_windows():
    """Handed over whole: the keyword form runs one study directly, and an
    umbrella block given to it made one run rather than seven."""
    from fastmdxplora.config.languages import python_script

    script = python_script({"systems": [{"system": "1UBQ"}], "simulation": {"umbrella": UMBRELLA}})
    assert "config_data=" in script and "system='1UBQ'" not in script
