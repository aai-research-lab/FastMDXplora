"""`agent` at the study level and in each phase, and why both.

A single value cannot say what is true of a real study. A simulation
written by hand because the protocol matters, an analysis explored outside
the schema, a setup a model drafted — that is one study, and flattening it
to one word loses the only thing a reader needs: which part to be
suspicious of.

The consequence is concrete. A trajectory from a validated simulation is
fine even when the analysis over it was not, and marking it anyway is
crying wolf. A mark that appears on everything stops being read, and then
it is not protecting anyone.
"""

from __future__ import annotations

import unittest

from fastmdxplora.config.agent_modes import (
    CHECKED_MODES,
    MODES,
    resolve_agent_modes,
    unchecked_phases,
)
from fastmdxplora.config.loader import validate_config
from fastmdxplora.config.schema import PHASE_SCHEMAS, TOP_LEVEL


class TestTheFieldIsAtBothLevels(unittest.TestCase):

    def test_the_study_and_every_phase_offer_it(self):
        self.assertTrue(any(f.name == "agent" for f in TOP_LEVEL.fields))
        for phase, schema in PHASE_SCHEMAS.items():
            with self.subTest(phase=phase):
                self.assertTrue(any(f.name == "agent" for f in schema.fields))

    def test_the_choices_are_the_same_at_both_levels(self):
        # One vocabulary. A mode that meant something different in a phase
        # than at the top would be two settings sharing a name.
        top = next(f for f in TOP_LEVEL.fields if f.name == "agent")
        for phase, schema in PHASE_SCHEMAS.items():
            with self.subTest(phase=phase):
                field = next(f for f in schema.fields if f.name == "agent")
                self.assertEqual(set(field.choices), set(top.choices))
                self.assertEqual(set(field.choices), set(MODES))

    def test_a_mixed_study_validates(self):
        validate_config({
            "systems": [{"id": "a", "system": "x.pdb"}],
            "agent": "assisted",
            "analysis": {"agent": "unvalidated"},
        })

    def test_a_mode_the_schema_does_not_know_is_refused_in_a_phase_too(self):
        from fastmdxplora.config.loader import ConfigError

        with self.assertRaises(ConfigError):
            validate_config({
                "systems": [{"id": "a", "system": "x.pdb"}],
                "analysis": {"agent": "supervised"},
            })


class TestResolution(unittest.TestCase):

    def test_a_phase_falls_back_to_the_study(self):
        modes = resolve_agent_modes({"agent": "assisted"})
        self.assertEqual(modes.of("simulation"), "assisted")

    def test_a_phase_overrides_the_study(self):
        modes = resolve_agent_modes(
            {"agent": "assisted", "analysis": {"agent": "unvalidated"}})
        self.assertEqual(modes.of("simulation"), "assisted")
        self.assertEqual(modes.of("analysis"), "unvalidated")

    def test_absent_everywhere_means_a_person_wrote_it(self):
        # The default, and the state of every study run before this field
        # existed. Those stay truthful without being rewritten.
        modes = resolve_agent_modes({"systems": []})
        self.assertIsNone(modes.study)
        self.assertIsNone(modes.of("setup"))
        self.assertTrue(modes.is_checked("setup"))
        self.assertEqual(str(modes), "written by hand")

    def test_a_human_written_phase_counts_as_checked(self):
        # A person writing a config is exactly what the validator was
        # built to check, so absent is checked rather than unknown.
        modes = resolve_agent_modes({"agent": None})
        self.assertTrue(modes.is_checked("analysis"))

    def test_only_unvalidated_is_unchecked(self):
        for mode in CHECKED_MODES:
            with self.subTest(mode=mode):
                self.assertTrue(
                    resolve_agent_modes({"agent": mode}).is_checked("setup"))
        self.assertFalse(
            resolve_agent_modes({"agent": "unvalidated"}).is_checked("setup"))


class TestTheDepartureIsNamedRatherThanFlattened(unittest.TestCase):
    """A claim at the top that a phase does not keep must be visible."""

    def test_a_phase_that_escapes_a_stricter_study_is_recorded(self):
        modes = resolve_agent_modes(
            {"agent": "assisted", "analysis": {"agent": "unvalidated"}})
        self.assertEqual(modes.departures, {"analysis": "unvalidated"})
        self.assertIn("analysis: unvalidated", str(modes))

    def test_a_phase_agreeing_with_the_study_is_not_a_departure(self):
        modes = resolve_agent_modes(
            {"agent": "assisted", "analysis": {"agent": "assisted"}})
        self.assertEqual(modes.departures, {})

    def test_a_phase_may_be_set_with_no_study_level_value(self):
        # "A person wrote the study, except the analysis, which a model
        # explored." A realistic shape and one the top level alone cannot
        # express.
        modes = resolve_agent_modes({"analysis": {"agent": "unvalidated"}})
        self.assertIsNone(modes.study)
        self.assertIsNone(modes.of("setup"))
        self.assertEqual(modes.of("analysis"), "unvalidated")
        self.assertEqual(unchecked_phases({"analysis":
                                           {"agent": "unvalidated"}}),
                         ("analysis",))


class TestWhatTheMarkingWillRead(unittest.TestCase):

    def test_only_the_unvalidated_phase_comes_back(self):
        # The reason for the whole per-phase design. A trajectory from a
        # validated simulation is fine even when the analysis over it was
        # not, and a mark on everything stops being read.
        unchecked = unchecked_phases(
            {"agent": "assisted", "analysis": {"agent": "unvalidated"}})
        self.assertEqual(unchecked, ("analysis",))

    def test_a_wholly_unvalidated_study_marks_everything(self):
        unchecked = unchecked_phases({"agent": "unvalidated"})
        self.assertEqual(set(unchecked), set(PHASE_SCHEMAS))

    def test_a_hand_written_study_marks_nothing(self):
        self.assertEqual(unchecked_phases({"systems": []}), ())


class TestTheRecord(unittest.TestCase):

    def test_it_says_per_phase_rather_than_summarising(self):
        # "Partly unvalidated" tells a reader to distrust the whole study.
        # The point of the per-phase setting is that they need only
        # distrust some of it.
        record = resolve_agent_modes(
            {"agent": "assisted",
             "analysis": {"agent": "unvalidated"}}).as_record()
        self.assertEqual(record["checked"]["simulation"], True)
        self.assertEqual(record["checked"]["analysis"], False)
        self.assertEqual(record["departures"], {"analysis": "unvalidated"})

    def test_it_is_json_serialisable(self):
        import json

        json.dumps(resolve_agent_modes({"agent": "autonomous"}).as_record())

    def test_a_hand_written_study_records_no_departures(self):
        record = resolve_agent_modes({"systems": []}).as_record()
        self.assertNotIn("departures", record)
        self.assertIsNone(record["study"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class TestTheRecordSaysWhichModel(unittest.TestCase):
    """`agent: assisted` says a model was involved, not which one.

    Six months on, that is the difference between a record and a note. A
    config that says a model wrote it and does not say which cannot be
    reasoned about: the answer to "why did this study pick 300 K" differs
    depending on whether a frontier model or a 7B on a laptop proposed it.

    And an alias is not a version. `claude-sonnet-4-6` names different
    software at different times, because it moves when a new snapshot
    lands. Anyone who needs the record to identify what ran should pin a
    dated string, and the field's help says so.
    """

    def test_the_field_exists_at_the_top_level(self):
        from fastmdxplora.config.schema import PHASE_SCHEMAS, TOP_LEVEL

        self.assertIn("agent_model", {f.name for f in TOP_LEVEL.fields})
        # Not per phase: which model wrote the study, not which wrote a
        # phase. A study drafted in two sittings by two models is not a
        # shape worth supporting before anybody has wanted it.
        for phase, schema in PHASE_SCHEMAS.items():
            with self.subTest(phase=phase):
                self.assertNotIn("agent_model",
                                 {f.name for f in schema.fields})

    def test_it_says_to_pin_a_version(self):
        from fastmdxplora.config.schema import TOP_LEVEL

        field = next(f for f in TOP_LEVEL.fields if f.name == "agent_model")
        self.assertIn("alias moves", field.help)
        self.assertIn("20250929", field.example)

    def test_a_config_carrying_it_validates(self):
        from fastmdxplora.config.loader import validate_config

        validate_config({
            "systems": [{"id": "a", "system": "x.pdb"}],
            "agent": "assisted",
            "agent_model": "anthropic/claude-sonnet-4-5-20250929",
        })

    def test_a_hand_written_study_carries_no_model(self):
        # Absent, not "none". Every study written before this existed stays
        # truthful without being rewritten.
        from fastmdxplora.config.loader import validate_config

        validate_config({"systems": [{"id": "a", "system": "x.pdb"}]})


class TestTheStudyLevelValueSurvivesTheRun(unittest.TestCase):
    """It has to reach the manifest, and it did not.

    `phase_options` keeps the four phase blocks and drops everything else,
    which is right -- it is named for what it does. The study-level value
    then had no channel to a run, and `_write_manifest` asked
    `resolve_agent_modes` about a dict of phase blocks.

    So `study` was permanently None, and two things followed. A config
    saying `agent: assisted` and nothing per phase recorded no `agent`
    block at all, because the writer is guarded on there being something to
    say. And one that also said `setup: {agent: assisted}` recorded that
    phase as *departing* from the study -- a disagreement the config does
    not contain, in the record whose whole purpose is to say which part of
    a study to distrust.
    """

    def test_phase_options_still_drops_them(self):
        # Not a regression to fix there: the function is named for the
        # phase blocks, and the batch layer reads both.
        from fastmdxplora.config import phase_options

        options = phase_options({
            "agent": "assisted",
            "agent_model": "anthropic/x",
            "setup": {"ph": 7.0},
        })
        self.assertEqual(options, {"setup": {"ph": 7.0}})

    def test_study_options_picks_them_up(self):
        from fastmdxplora.config import study_options

        self.assertEqual(
            study_options({
                "agent": "assisted",
                "agent_model": "anthropic/x",
                "setup": {"ph": 7.0},
            }),
            {"agent": "assisted", "agent_model": "anthropic/x"},
        )

    def test_a_hand_written_study_carries_nothing(self):
        from fastmdxplora.config import study_options

        self.assertEqual(study_options({"setup": {"ph": 7.0}}), {})

    def test_a_run_spec_carries_them_to_the_worker(self):
        # The parallel path pickles this dict into a subprocess, so the
        # channel has to be on `to_dict()` rather than on the object.
        from fastmdxplora.batch.sweep import RunSpec

        spec = RunSpec(run_id="s1", system="x.pdb",
                       study={"agent": "unvalidated"})
        self.assertEqual(spec.to_dict()["study"], {"agent": "unvalidated"})

    def test_the_resolver_sees_the_study_beside_the_phases(self):
        # The shape `_write_manifest` now builds: phase blocks merged with
        # the study-level settings, which is what a config looks like.
        options = {"setup": {"agent": "assisted"}, "simulation": {}}
        study = {"agent": "assisted"}

        modes = resolve_agent_modes({**options, **study})

        self.assertEqual(modes.study, "assisted")
        self.assertEqual(modes.departures, {},
                         "a phase agreeing with the study is not a departure")

    def test_without_it_the_agreement_reads_as_a_departure(self):
        # The bug, kept as a test so the fix cannot be undone quietly.
        modes = resolve_agent_modes({"setup": {"agent": "assisted"}})

        self.assertIsNone(modes.study)
        self.assertEqual(modes.departures, {"setup": "assisted"})
