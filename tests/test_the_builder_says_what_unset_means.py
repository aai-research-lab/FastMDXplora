"""The Config Builder offers each setting as the schema describes it.

A list of choices with no default showed its first choice, so a protein in
water read as embedded in POPC, and choosing it back wrote `membrane: POPC`.
A temperature, a production length and a timestep were text boxes, because
their type is "int or float". An empty box showed an example that read as a
value. What the payload now carries for each of these is checked here; the
page that shows them is checked in a browser in
test_the_builder_page_in_a_browser.py.
"""

from __future__ import annotations

import unittest


def _payload():
    from fastmdxplora.gui.schema_payload import schema_payload

    return schema_payload()


def _fields(payload):
    for phase, block in payload["phases"].items():
        for field in block["fields"]:
            yield phase, field
    for field in payload["run_options"]:
        yield None, field
    for field in payload["execution_options"]:
        yield "execution", field


class TestUnsetIsSaid(unittest.TestCase):
    def test_every_list_of_choices_without_a_default_says_what_unset_does(self):
        missing = [f"{phase}.{field['name']}" for phase, field in _fields(_payload())
                   if field["choices"] and field["default"] is None
                   and field["control"] == "select" and not field["unset"]]
        self.assertEqual(missing, [])

    def test_a_membrane_left_unset_is_no_membrane(self):
        setup = {f["name"]: f for f in _payload()["phases"]["setup"]["fields"]}
        self.assertEqual(setup["membrane"]["unset"], "no membrane")
        self.assertIsNone(setup["membrane"]["default"])

    def test_every_unset_entry_names_a_setting(self):
        from fastmdxplora.config.loader import settings_named
        from fastmdxplora.config.schema import UNSET_MEANS

        known = settings_named() | {"decisions"}
        self.assertEqual(sorted(set(UNSET_MEANS) - known), [])


class TestNumbersAreNumbers(unittest.TestCase):
    def test_an_int_or_float_setting_is_a_number(self):
        simulation = {f["name"]: f for f in _payload()["phases"]["simulation"]["fields"]}
        for name in ("temperature_K", "duration_ns", "timestep_fs", "pressure_bar"):
            self.assertEqual(simulation[name]["control"], "number", name)

    def test_a_bound_reaches_the_page(self):
        setup = {f["name"]: f for f in _payload()["phases"]["setup"]["fields"]}
        self.assertEqual((setup["ph"]["minimum"], setup["ph"]["maximum"]), (0.0, 14.0))
        self.assertEqual(setup["ion_concentration_M"]["maximum"], 20.0)


class TestLabels(unittest.TestCase):
    def test_a_label_and_its_unit_come_from_the_name(self):
        from fastmdxplora.gui.schema_payload import label_and_unit

        self.assertEqual(label_and_unit("ph"), ("pH", ""))
        self.assertEqual(label_and_unit("nonbonded_cutoff_nm"), ("Nonbonded cutoff", "nm"))
        self.assertEqual(label_and_unit("friction_per_ps"), ("Friction", "1/ps"))
        self.assertEqual(label_and_unit("minimize_tolerance_kjmol_per_nm"),
                         ("Minimize tolerance", "kJ/mol/nm"))
        self.assertEqual(label_and_unit("ion_concentration_M"), ("Salt", "M"))

    def test_every_setting_has_a_label_and_a_first_sentence(self):
        for phase, field in _fields(_payload()):
            self.assertTrue(field["label"], f"{phase}.{field['name']}")
            if field["help"]:
                self.assertTrue(field["help"].startswith(field["summary"]),
                                f"{phase}.{field['name']}")

    def test_a_first_sentence_does_not_end_at_an_abbreviation_or_a_number(self):
        from fastmdxplora.gui.schema_payload import _summary

        self.assertEqual(_summary("Water model (e.g. tip3p). More."), "Water model (e.g. tip3p).")
        self.assertEqual(_summary("At 0.15 M. Then more."), "At 0.15 M.")


class TestWhatIsShownFirst(unittest.TestCase):
    def test_every_essential_setting_exists(self):
        from fastmdxplora.config.schema import ESSENTIAL, all_schemas

        schemas = all_schemas()
        for phase, names in ESSENTIAL.items():
            known = schemas[phase].field_names()
            self.assertEqual(sorted(set(names) - known), [], phase)

    def test_the_payload_marks_them(self):
        setup = {f["name"]: f for f in _payload()["phases"]["setup"]["fields"]}
        self.assertTrue(setup["ph"]["essential"])
        self.assertFalse(setup["ewald_error_tolerance"]["essential"])


class TestTheAnalysesAreThemed(unittest.TestCase):
    def test_the_themes_are_the_analysis_page_s(self):
        from fastmdxplora.gui.report_dashboard import ANALYSIS_THEMES

        options = _payload()["analysis_options"]
        if not options["available"]:
            self.skipTest(options["reason"])
        themes = [theme for theme, _members in ANALYSIS_THEMES]
        self.assertEqual(options["category_order"][:-1], themes)
        # Sixteen of thirty were "Other" under the form's own grouping.
        others = [name for name, theme in options["categories"].items() if theme == "Other"]
        self.assertEqual(others, [])

    def test_what_an_analysis_needs_is_its_class_s(self):
        from fastmdxplora.analysis.orchestrator import get_analysis_class

        options = _payload()["analysis_options"]
        if not options["available"]:
            self.skipTest(options["reason"])
        self.assertIn("ligand", options["needs"]["ligand_rmsd"])
        self.assertIn("water", options["needs"]["water_sites"])
        self.assertIn("crystallographic_bfactors", options["needs"]["bfactor_comparison"])
        for name, needs in options["needs"].items():
            cls = get_analysis_class(name)
            for need in needs:
                self.assertIs(getattr(cls, "requires_" + need), True, name)


class TestChecks(unittest.TestCase):
    def test_the_checks_are_the_report_s(self):
        from fastmdxplora.report.convergence import CHECKS

        self.assertEqual(_payload()["checks"], [said for _k, said, _s in CHECKS])


if __name__ == "__main__":
    unittest.main()
