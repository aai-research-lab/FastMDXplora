"""The phase lists have one name, at every door.

They were `include` and `exclude`, which is also what the analysis block
calls its list of analyses: a config reading ``exclude: [setup]`` above
``analysis.exclude: [dimred]`` invites the reader to think the two are
one thing. They are `include_phase` and `exclude_phase` now -- in the
config, on the command line, in the GUI and in the Python API -- and the
earlier spellings are still accepted everywhere for existing work.

The API was briefly left as `include=` while the others moved, and the
script generator was bent to match it rather than the API corrected.
These tests are what would have caught that.
"""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

import yaml


class TestEveryDoorSpeaksTheSameName(unittest.TestCase):

    def test_the_config(self):
        from fastmdxplora.config.schema import all_schemas

        top = {f.name for f in all_schemas()["(top-level)"].fields}
        self.assertIn("include_phase", top)
        self.assertIn("exclude_phase", top)
        self.assertNotIn("include", top)
        self.assertNotIn("exclude", top)

    def test_the_command_line(self):
        from fastmdxplora.cli.main import _build_parser

        help_text = _build_parser()._subparsers._group_actions[0].choices["explore"].format_help()
        self.assertIn("--include-phase", help_text)
        self.assertIn("--exclude-phase", help_text)

    def test_the_python_api(self):
        from fastmdxplora import FastMDXplora

        for method in (FastMDXplora.__init__, FastMDXplora.explore):
            with self.subTest(method=method.__name__):
                names = list(inspect.signature(method).parameters)
                self.assertIn("include_phase", names)
                self.assertIn("exclude_phase", names)
                # The canonical name comes first, so it is what an IDE offers.
                self.assertLess(names.index("include_phase"), names.index("include"))

    def test_the_gui(self):
        # The phase lists are drawn as the phase checkboxes rather than as
        # generic fields, so they are absent from the payload's fields on
        # purpose. What must hold is that every top-level setting reaches
        # the form one way or the other -- a setting no door can set is the
        # drift this whole change was about.
        from fastmdxplora.config.schema import all_schemas
        from fastmdxplora.gui.schema_payload import _STRUCTURAL_TOP_LEVEL, schema_payload

        top = {f.name for f in all_schemas()["(top-level)"].fields}
        fields = {f["name"] for f in schema_payload()["run_options"]}
        self.assertIn("include_phase", _STRUCTURAL_TOP_LEVEL)
        self.assertIn("exclude_phase", _STRUCTURAL_TOP_LEVEL)
        self.assertEqual(top - fields - set(_STRUCTURAL_TOP_LEVEL), set(),
                         "a top-level setting the GUI cannot set")

    def test_the_generated_script_and_command_agree_with_the_config(self):
        from fastmdxplora.config.languages import cli_command, python_script

        config = {"systems": [{"system": "1L2Y"}], "include_phase": ["setup"]}
        self.assertIn("--include-phase setup", cli_command(config))
        self.assertIn("explore(include_phase=['setup'])", python_script(config))


class TestTheEarlierSpellingStillWorks(unittest.TestCase):
    """Every config, script and command written before the rename."""

    def test_a_config_file(self):
        from fastmdxplora.config.loader import load_config_file

        path = Path(tempfile.mkdtemp()) / "c.yml"
        path.write_text(yaml.safe_dump({"exclude": ["report"]}), encoding="utf-8")
        self.assertEqual(load_config_file(path), {"exclude_phase": ["report"]})

    def test_a_dict_given_to_the_validator(self):
        from fastmdxplora.config.loader import validate_config

        data = {"systems": [{"system": "1L2Y"}], "include": ["setup"]}
        validate_config(data)
        self.assertEqual(data["include_phase"], ["setup"])
        self.assertNotIn("include", data)

    def test_the_command_line(self):
        from fastmdxplora.cli.main import _build_parser

        parser = _build_parser()
        for flag in ("--include", "--include-phase"):
            with self.subTest(flag=flag):
                self.assertEqual(parser.parse_args(["explore", flag, "setup"]).include, ["setup"])

    def test_the_python_api(self):
        from fastmdxplora.orchestrator import _phase_selection

        self.assertEqual(_phase_selection(None, None, ["setup"], None), (["setup"], None))

    def test_an_earlier_spelled_config_generates_the_current_one(self):
        from fastmdxplora.config.languages import python_script

        script = python_script({"systems": [{"system": "1L2Y"}], "include": ["setup"]})
        self.assertIn("include_phase=['setup']", script)


class TestTwoSpellingsThatDisagreeAreRefused(unittest.TestCase):

    def test_the_api_does_not_guess(self):
        # A call that says two things does not say which it meant, and a
        # guess decides whether a simulation runs.
        from fastmdxplora.config.loader import ConfigError
        from fastmdxplora.orchestrator import _phase_selection

        with self.assertRaises(ConfigError) as caught:
            _phase_selection(["setup"], None, ["report"], None)
        self.assertIn("`include_phase` is the current name", str(caught.exception))
        # The same named refusal the config gives for two conflicting
        # settings, so it is told apart the same way.
        self.assertEqual(caught.exception.code, "config.option.conflicting")

    def test_agreeing_spellings_are_fine(self):
        from fastmdxplora.orchestrator import _phase_selection

        self.assertEqual(_phase_selection(["setup"], None, ["setup"], None), (["setup"], None))


class TestTheAnalysisListKeepsItsName(unittest.TestCase):
    """A different list, and renaming it would have been the mistake."""

    def test_the_analysis_block(self):
        from fastmdxplora.config.schema import all_schemas

        analysis = {f.name for f in all_schemas()["analysis"].fields}
        self.assertIn("include", analysis)
        self.assertIn("exclude", analysis)
