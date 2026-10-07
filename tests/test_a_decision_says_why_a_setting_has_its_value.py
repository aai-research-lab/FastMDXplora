"""`decisions`: why a setting has the value it has, recorded with the study.

A study said what was used and never why. The decision behind a setting
(the reason, who or what decided it, and what was set aside) is a block of
the config, checked as the rest is, carried into the resolved config every
run writes, and given in the report beside the methods.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

GOOD = {"setup.ph": {"why": "the assay buffer is pH 7.0", "source": "person",
                     "alternatives": ["7.4"]},
        "budget_hours": {"why": "a day of the lab's GPU", "source": "agent"}}


def _study(decisions):
    return {"systems": [{"system": "1L2Y"}], "setup": {"ph": 7.0}, "budget_hours": 24.0,
            "decisions": decisions}


class TestTheBlockIsChecked(unittest.TestCase):
    def test_a_sound_block_is_accepted(self):
        from fastmdxplora.config.loader import validate_config

        validate_config(_study(dict(GOOD)), require_systems=True)

    def _refused(self, decisions):
        from fastmdxplora.config.loader import ConfigError, validate_config

        with self.assertRaises(ConfigError) as caught:
            validate_config(_study(decisions))
        return caught.exception

    def test_a_setting_that_does_not_exist_is_refused_with_the_nearest(self):
        refused = self._refused({"setup.phh": {"why": "x"}})
        self.assertEqual(refused.code, "config.option.unknown")
        self.assertIn("setup.ph", str(refused))

    def test_a_decision_without_a_reason_is_refused(self):
        self.assertEqual(self._refused({"setup.ph": {"source": "person"}}).code,
                         "config.option.missing_companion")

    def test_an_unknown_part_of_a_decision_is_refused(self):
        self.assertIn("why", str(self._refused({"setup.ph": {"wy": "x"}})))

    def test_its_parts_have_their_types(self):
        self.assertEqual(self._refused({"setup.ph": {"why": "x", "alternatives": "7.4"}}).code,
                         "config.option.wrong_type")
        self.assertEqual(self._refused({"setup.ph": {"why": "x", "source": 3}}).code,
                         "config.option.wrong_type")
        self.assertEqual(self._refused(["setup.ph"]).code, "config.option.wrong_type")

    def test_a_scheduling_setting_is_named_under_execution(self):
        from fastmdxplora.config.loader import validate_config

        validate_config(_study({"execution.mode": {"why": "one GPU"}}))


class TestItTravels(unittest.TestCase):
    def test_the_resolved_config_writes_it_as_a_mapping(self):
        from fastmdxplora.config.generate import write_resolved_config

        out = Path(tempfile.mkdtemp())
        path = write_resolved_config({"system": "1L2Y", "decisions": dict(GOOD)}, out, full=False)
        written = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(written["decisions"], GOOD)

    def test_the_builder_writes_it(self):
        from fastmdxplora.gui.config_builder import config_yaml

        built = config_yaml({"systems": [{"system": "1L2Y"}], "setup": {"ph": 7.0},
                             "study": {"decisions": {"setup.ph": GOOD["setup.ph"]}}})
        self.assertTrue(built["ok"], built.get("error"))
        self.assertEqual(yaml.safe_load(built["yaml"])["decisions"], {"setup.ph": GOOD["setup.ph"]})

    def test_the_builder_reads_it_back(self):
        from fastmdxplora.gui.config_builder import state_from_config

        state = state_from_config(_study(dict(GOOD)))["state"]
        self.assertEqual(state["study"]["decisions"], GOOD)

    def test_the_python_script_carries_it(self):
        from fastmdxplora.config.languages import python_script

        script = python_script({"systems": [{"system": "1L2Y"}], "setup": {"ph": 7.0},
                                "decisions": {"setup.ph": {"why": "buffer"}}})
        self.assertIn("'decisions': {", script)
        self.assertIn("'why': 'buffer'", script)

    def test_the_command_line_carries_it(self):
        from fastmdxplora.cli.main import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["explore", "--system", "1L2Y", "--decisions",
                                  '{"setup.ph": {"why": "buffer"}}'])
        self.assertEqual(args.decisions, {"setup.ph": {"why": "buffer"}})


class TestTheReportGivesIt(unittest.TestCase):
    def test_each_reason_beside_its_value(self):
        from fastmdxplora.report.document import _decisions_said

        root = Path(tempfile.mkdtemp())
        (root / "resolved_config.yml").write_text(yaml.safe_dump(
            {"systems": [{"system": "1L2Y"}], "setup": {"ph": 7.0}, "budget_hours": 24.0,
             "decisions": GOOD}, sort_keys=False), encoding="utf-8")
        lines = _decisions_said(root)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("- `setup.ph` = `7.0`: the assay buffer is pH 7"), lines[0])
        self.assertIn("decided by person", lines[0])
        self.assertIn("set aside: `7.4`", lines[0])
        self.assertTrue(lines[1].startswith("- `budget_hours` = `24.0`: a day of the lab"), lines[1])

    def test_a_study_without_any_says_nothing(self):
        from fastmdxplora.report.document import _decisions_said

        self.assertEqual(_decisions_said(Path(tempfile.mkdtemp())), [])


if __name__ == "__main__":
    unittest.main()
