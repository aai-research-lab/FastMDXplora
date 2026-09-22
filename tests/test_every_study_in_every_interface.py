"""Any study can be written in any interface -- config, command line, Python,
GUI -- because they are generated from one source. This holds that to account.

Every study setting is given a value other than its default and carried out
through each interface and back (`tests/_interfaces.py`): the command through
the real parser, the script executed against a stand-in that records what the
package would be given, the resolved config written and loaded, the form read
from the payload the GUI builds it from. The two sides are compared as studies,
after the normalisation every study goes through.

A ratchet. What does not survive is listed below with the reason, and the list
may only shrink: a setting newly lost fails, and so does a listed gap that has
been closed, until it is taken off. Renames wait for the breaking release, so
the names that differ from the config are recorded rather than changed.

The measurement reads the schema, the parser, the emitters and the loader, and
none of them needs OpenMM: it gives the same answer on Python 3.9 without it as
on 3.12 with it, which a ratchet must, since the lists are compared exactly.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mdtraj")

from tests._interfaces import INTERFACES, measure  # noqa: E402

#: interface -> setting -> why it does not survive. Closing one means taking
#: it off here.
KNOWN_GAPS = {'cli': {'(study).systems': 'undescribed in the schema, so nothing is generated from it',
         'simulation.dashboard_binding_pocket_cutoff_A': 'the command line has no flag for it',
         'simulation.dashboard_ligand_resname': 'the command line has no flag for it',
         'simulation.dashboard_max_playback_frames': 'the command line has no flag for it',
         'simulation.plumed': 'the command line has no flag for it'},
 'cli_command': {'(study).systems': 'undescribed in the schema, so nothing is generated from it',
                 'simulation.dashboard_binding_pocket_cutoff_A': 'refused by name: the command '
                                                                 'line has no flag for it',
                 'simulation.dashboard_ligand_resname': 'refused by name: the command line has no '
                                                        'flag for it',
                 'simulation.dashboard_max_playback_frames': 'refused by name: the command line '
                                                             'has no flag for it',
                 'simulation.plumed': 'refused by name: the command line has no flag for it'},
 'gui': {'(study).systems': 'undescribed in the schema, so nothing is generated from it',
         '(top-level).output': 'by design: the server places each study in its workspace'},
 'python_script': {},
 'resolved_config': {}}

#: Command-line flags whose name is not the config's by the one rule a flag
#: follows (the phase prefix, then the setting with `_` as `-`, negated with
#: `no-` where the default is on). For the breaking release.
KNOWN_FLAG_NAMES = {'analysis.exclude': '--analyze-exclude-analyses',
 'analysis.include': '--analyze-analyses',
 'report.include_methods': '--report-no-methods',
 'report.include_reproducibility': '--report-no-reproducibility',
 'setup.check_ligand_clashes': '--setup-no-ligand-clash-check'}

#: The command line and the Python methods name a phase for the verb; the
#: config, the GUI and `explore(options=...)` name it for the noun. Decided
#: 2026-09-22: the verbs win. In the breaking release the config, the GUI and
#: `explore(options=...)` take `simulate` and `analyze`; until then nothing is
#: renamed, and this pins the two spellings so neither moves by accident.
KNOWN_PHASE_NAMES = {"setup": "setup", "simulation": "simulate",
                      "analysis": "analyze", "report": "report"}


@pytest.fixture(scope="module")
def measured():
    return measure()


def _gaps(measured):
    return {(interface, f"{block}.{name}")
            for (block, name), row in measured.items()
            for interface in INTERFACES if not row[interface]}


def _listed():
    return {(interface, setting) for interface, lost in KNOWN_GAPS.items() for setting in lost}


def test_no_interface_has_lost_a_setting_it_did_not_already_lack(measured):
    new = sorted(_gaps(measured) - _listed())
    assert not new, f"these no longer survive their interface: {new}"


def test_a_closed_gap_is_taken_off_the_list(measured):
    closed = sorted(_listed() - _gaps(measured))
    assert not closed, f"these now survive; take them off KNOWN_GAPS: {closed}"


def test_the_flags_named_differently_from_the_config_are_the_ones_listed(measured):
    flags = {f"{block}.{name}": row["cli_name"] for (block, name), row in measured.items()
             if row["cli_name"]}
    def follows_the_rule(setting, flag):
        hyphen = setting.split(".", 1)[1].replace("_", "-")
        return (flag.endswith(f"-{hyphen}") or flag.endswith(f"-no-{hyphen}")
                or flag in (f"--{hyphen}", f"--no-{hyphen}"))
    differing = {s: f for s, f in flags.items() if not follows_the_rule(s, f)}
    assert differing == KNOWN_FLAG_NAMES


def test_the_phase_names_are_the_ones_listed():
    import importlib

    cli = importlib.import_module("fastmdxplora.cli.main")
    assert {noun: verb for verb, noun in cli._SCHEMA_KEY.items()} == KNOWN_PHASE_NAMES
