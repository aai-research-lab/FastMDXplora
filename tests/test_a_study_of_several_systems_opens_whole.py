"""A study of several systems opened in the Config Builder comes back whole.

The form held one structure. A config of two systems opened in it came back
as the first, its name and the second system's own settings gone without a
word, while "Check it" had just said "2 system(s)". The Python script the
form wrote dropped a system's name too, and the command ran it as `s1`.
"""

from __future__ import annotations

import unittest

STUDY = {
    "systems": [
        {"system": "1L2Y", "id": "trpcage"},
        {"system": "1UBQ", "id": "ubq", "simulation": {"duration_ns": 20}},
    ],
    "simulation": {"duration_ns": 5},
}


class TestSeveralSystems(unittest.TestCase):
    def test_every_system_reaches_the_form(self):
        from fastmdxplora.gui.config_builder import state_from_config

        state = state_from_config(dict(STUDY))["state"]
        self.assertEqual(state["systems"], STUDY["systems"])

    def test_the_form_s_systems_are_written_back_whole(self):
        import yaml

        from fastmdxplora.gui.config_builder import config_yaml, state_from_config

        state = state_from_config(dict(STUDY))["state"]
        body = {"systems": state["systems"], "include_phase": state["include_phase"],
                "simulation": state["phases"]["simulation"]}
        written = yaml.safe_load(config_yaml(body)["yaml"])
        self.assertEqual(written["systems"], STUDY["systems"])
        self.assertEqual(written["simulation"], {"duration_ns": 5})

    def test_a_system_s_own_settings_are_read_from_the_form_s_text(self):
        from fastmdxplora.gui.config_builder import build_config

        config = build_config({"systems": [
            {"system": "1UBQ", "id": "", "setup": {"ph": "6.5"}, "simulation": {"duration_ns": "20"}},
            {"system": "  "},
        ]})
        self.assertEqual(config["systems"], [
            {"system": "1UBQ", "setup": {"ph": 6.5}, "simulation": {"duration_ns": 20}}])


class TestANamedSystemInEveryLanguage(unittest.TestCase):
    def test_the_script_carries_the_name(self):
        from fastmdxplora.config.languages import python_script

        script = python_script({"systems": [{"system": "1L2Y", "id": "trpcage"}]})
        self.assertIn("'id': 'trpcage'", script)

    def test_the_command_says_it_cannot(self):
        from fastmdxplora.config.languages import UntranslatableSetting, cli_command

        with self.assertRaises(UntranslatableSetting) as caught:
            cli_command({"systems": [{"system": "1L2Y", "id": "trpcage"}]})
        self.assertIn("name", str(caught.exception))

    def test_an_unnamed_system_is_still_one_command(self):
        from fastmdxplora.config.languages import cli_command

        self.assertIn("--system 1L2Y", cli_command({"systems": [{"system": "1L2Y"}]}))


class TestThePreviewReadsTheFirstSystem(unittest.TestCase):
    def test_its_own_setup_settings_are_applied(self):
        from unittest import mock

        from fastmdxplora.gui import preview

        seen = {}

        def estimate(structure, setup):
            seen.update(setup)
            raise ValueError("stop here")

        with mock.patch.object(preview, "structure_file", return_value="x.pdb"), \
                mock.patch("fastmdxplora.setup.estimate.estimate_system", estimate):
            answer = preview.preview_of_config({
                "systems": [{"system": "1UBQ", "setup": {"ph": 6.0}}], "setup": {"ph": 7.0}})
        self.assertFalse(answer["ok"])
        self.assertEqual(seen.get("ph"), 6.0)


if __name__ == "__main__":
    unittest.main()
