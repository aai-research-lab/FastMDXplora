"""Every subcommand's --help must format, and the schema must stay readable.

argparse expands help strings with `%`, so a literal percent becomes a format
spec: `71% of a cube` in box_shape read as a `% o` octal conversion and raised
at print_help() time while the parser still built fine. Shipped broken in
2.5.1 through 2.5.4.

The obvious fix -- doubling the percent in the schema -- is wrong, because
config/generate.py and gui/schema_payload.py print the same text verbatim and
would show `71%%`. The escaping belongs at the argparse boundary, so both
halves are asserted here.
"""
import pytest

from fastmdxplora.cli.main import _build_parser
from fastmdxplora.config import schema


@pytest.mark.parametrize(
    "cmd", ["explore", "analyze", "setup", "simulate", "report", "gui"])
def test_subcommand_help_formats(cmd, capsys):
    with pytest.raises(SystemExit) as excinfo:
        _build_parser().parse_args([cmd, "--help"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out


def test_top_level_help_formats(capsys):
    with pytest.raises(SystemExit) as excinfo:
        _build_parser().parse_args(["--help"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out


def test_schema_help_is_not_escaped():
    """The template and the GUI print these verbatim."""
    doubled = [
        f"{phase}.{f.name}"
        for phase, group in schema.PHASE_SCHEMAS.items()
        for f in group.fields
        if "%%" in (f.help or "")
    ]
    assert not doubled, doubled


# A setting that accepts a fixed list of values is listed in --help by its
# values, not as TEXT. Every case is (command, flag, argparse dest, a value, how
# the flag is shown), on a single-phase command and on `explore`.
FIXED_CHOICES = [
    ("simulate", "--agent", "agent", "assisted", "{assisted,autonomous,unvalidated}"),
    ("simulate", "--ensemble", "ensemble", "nvt", "{npt,nvt}"),
    ("analyze", "--figure-colours", "figure_colours", "greyscale",
     "{colour,greyscale,both}"),
    ("explore", "--agent", "agent", "assisted", "{assisted,autonomous,unvalidated}"),
    ("explore", "--simulate-ensemble", "simulate__ensemble", "nvt", "{npt,nvt}"),
    ("explore", "--analyze-figure-colours", "analyze__figure_colours", "greyscale",
     "{colour,greyscale,both}"),
]


@pytest.mark.parametrize("cmd, flag, dest, value, shown", FIXED_CHOICES)
def test_a_fixed_choice_is_listed_in_help(cmd, flag, dest, value, shown, capsys,
                                          monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    with pytest.raises(SystemExit):
        _build_parser().parse_args([cmd, "--help"])
    assert f"{flag} {shown}" in capsys.readouterr().out


@pytest.mark.parametrize("cmd, flag, dest, value, shown", FIXED_CHOICES)
def test_a_fixed_choice_still_takes_its_values_and_refuses_the_rest(
        cmd, flag, dest, value, shown, capsys):
    assert getattr(_build_parser().parse_args([cmd, flag, value]), dest) == value
    with pytest.raises(SystemExit) as excinfo:
        _build_parser().parse_args([cmd, flag, "nonsense"])
    assert excinfo.value.code == 2
    assert "invalid choice: 'nonsense'" in capsys.readouterr().err


def test_a_fixed_choice_on_explore_reaches_the_config():
    from fastmdxplora.cli.main import _build_explore_config

    config = _build_explore_config(_build_parser().parse_args([
        "explore", "--agent", "assisted", "--simulate-ensemble", "nvt",
        "--analyze-figure-colours", "greyscale", "--execution-mode", "parallel"]))
    assert config["agent"] == "assisted"
    assert config["simulation"]["ensemble"] == "nvt"
    assert config["analysis"]["figure_colours"] == "greyscale"
    assert config["execution"]["mode"] == "parallel"


def test_numbers_and_lists_keep_their_own_names_in_help(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["analyze", "--help"])
    out = capsys.readouterr().out
    assert "--analyses NAME [NAME ...]" in out
    assert "--dimred-components N" in out
