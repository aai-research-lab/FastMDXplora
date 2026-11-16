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


@pytest.mark.parametrize("cmd", ["explore", "setup"])
def test_system_help_says_a_sequence_needs_a_predicted_structure(cmd):
    command = _build_parser()._subparsers._group_actions[0].choices[cmd]
    system = next(action for action in command._actions if action.dest == "system")
    system_help = " ".join(system.help.split())
    formatted_help = " ".join(command.format_help().split())

    assert system_help in formatted_help
    assert "or a one-letter sequence" not in system_help
    assert "sequence needs a structure predictor; predict it first" in system_help


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
