"""Whether a model can write a study, and whether it wrote the one asked for.

`propose_config` has a cycle counter that nothing has ever counted, and a
repair loop tested only against the mistakes I imagined. This is the
instrument that turns that into a number when a model is available.

The distinction it exists for is between valid and correct. A config can
pass the validator and be the wrong study -- 300 K when the sentence said
310, an unrequested setting, a concentration in millimolar where the field
is molar. Validation catches ill-formed, not wrong, and a harness
reporting only the first would flatter every model and measure the thing
nobody cares about.

Run against a real model with `scripts/measure_nli.py`. These hold the
harness itself honest, with stubs, so that the number it eventually
reports means something.
"""

from __future__ import annotations

import unittest

from fastmdxplora.agent.evaluate import REQUESTS, Request, measure

CORRECT = {
    "plain": "systems:\n  - {id: a, system: 1UBQ}\nsimulation:\n"
             "  duration_ns: 10\n",
    "ph": "systems:\n  - {id: a, system: 1UBQ}\nsetup:\n  ph: 6.5\n"
          "simulation:\n  duration_ns: 5\n",
    "temperature": "systems:\n  - {id: a, system: 1UBQ}\nsimulation:\n"
                   "  temperature_K: 310\n  duration_ns: 20\n",
    "timestep": "systems:\n  - {id: a, system: 1UBQ}\nsimulation:\n"
                "  timestep_fs: 4\n  duration_ns: 50\n",
    "box": "systems:\n  - {id: a, system: 1UBQ}\nsetup:\n"
           "  box_shape: dodecahedron\nsimulation:\n  duration_ns: 10\n",
    "salt": "systems:\n  - {id: a, system: 1UBQ}\nsetup:\n"
            "  ion_concentration_M: 0.15\nsimulation:\n  duration_ns: 10\n",
    "no_minimise": "systems:\n  - {id: a, system: 1UBQ}\nsimulation:\n"
                   "  minimize: false\n  duration_ns: 10\n",
    "membrane": "systems:\n  - {id: a, system: 1UBQ}\nsetup:\n"
                "  membrane: POPC\n  membrane_orientation_checked: true\n"
                "simulation:\n  duration_ns: 20\n",
}

#: Valid configs meaning the wrong thing. Each is a mistake a model
#: actually makes rather than one invented to be caught: the right setting
#: with the wrong value, a setting simply omitted, and -- the dangerous one
#: -- a concentration in millimolar where the field is molar.
VALID_BUT_WRONG = {
    "plain": CORRECT["plain"].replace("duration_ns: 10", "duration_ns: 100"),
    "ph": CORRECT["ph"].replace("ph: 6.5", "ph: 7.0"),
    "temperature": CORRECT["temperature"].replace("310", "300"),
    "timestep": CORRECT["timestep"].replace("  timestep_fs: 4\n", ""),
    "box": CORRECT["box"].replace("dodecahedron", "cube"),
    "salt": CORRECT["salt"].replace("0.15", "150"),
    "no_minimise": CORRECT["no_minimise"].replace("  minimize: false\n", ""),
    "membrane": CORRECT["membrane"].replace(
        "  membrane_orientation_checked: true\n", ""),
}


def _scripted(answers: dict[str, str], requests=REQUESTS):
    """A stub that answers each request, and repeats itself when repaired.

    Repeating rather than advancing, because a repair prompt is the same
    request again and a stub that moved on to the next answer would be
    giving the loop a different study to validate. It also makes the
    unrepairable case the natural one to write: a stub that keeps saying
    the same wrong thing is what a model that cannot see its mistake does.
    """
    replies = iter([answers[r.name] for r in requests])
    state = {"current": None}

    def complete(prompt: str) -> str:
        if not prompt.startswith("That config was refused"):
            state["current"] = next(replies)
        return state["current"]

    return complete


class TestTheHarnessTellsValidFromCorrect(unittest.TestCase):

    def test_correct_configs_score_correct(self):
        report = measure(_scripted(CORRECT))
        self.assertEqual(report.correct, len(REQUESTS))
        self.assertEqual(report.accepted, len(REQUESTS))

    def test_valid_but_wrong_configs_score_valid_and_not_correct(self):
        # The whole point. Every one of these passes the validator, and a
        # harness measuring only validation would report a perfect score.
        report = measure(_scripted(VALID_BUT_WRONG))
        self.assertEqual(report.accepted, len(REQUESTS))
        self.assertEqual(report.correct, 0)

    def test_it_says_what_was_wrong_rather_than_that_something_was(self):
        report = measure(_scripted(VALID_BUT_WRONG))
        salt = next(o for o in report.outcomes if o.request == "salt")
        self.assertTrue(any("150" in complaint for complaint in salt.wrong))
        self.assertTrue(any("0.15" in complaint for complaint in salt.wrong))

    def test_a_request_only_checks_what_its_sentence_specified(self):
        # A request saying "at pH 6.5" checks the pH. Marking a model wrong
        # for choosing a box shape it was never asked about would measure
        # obedience rather than comprehension.
        request = Request("x", "Run 1UBQ at pH 6.5.", {"setup.ph": 6.5})
        self.assertEqual(
            request.failures({"setup": {"ph": 6.5, "box_shape": "cube"}}), [])

    def test_a_missing_branch_is_a_failure_and_not_a_crash(self):
        request = Request("x", "...", {"simulation.timestep_fs": 4})
        self.assertEqual(len(request.failures({"setup": {"ph": 7.0}})), 1)


class TestTheReportIsWorthReading(unittest.TestCase):

    def test_it_counts_cycles_only_for_what_validated(self):
        # A run that never reached a valid config has no cycles-to-valid,
        # and averaging in its cap would make a failing model look patient.
        never = {name: "not yaml at all" for name in CORRECT}
        report = measure(_scripted(never), max_cycles=2)
        self.assertEqual(report.accepted, 0)
        self.assertNotEqual(report.mean_cycles, report.mean_cycles)  # NaN

    def test_it_tallies_which_refusals_recur(self):
        # The most useful output. A code appearing in most runs is not a
        # model being careless; it is the schema description failing to say
        # something, and it says where to look.
        typo = {name: text.replace("systems:", "system:")
                for name, text in CORRECT.items()}
        report = measure(_scripted(typo), max_cycles=1)
        self.assertIn("config.option.unknown", report.recurring_codes())

    def test_it_is_json_serialisable(self):
        import json
        json.dumps(measure(_scripted(CORRECT)).as_record())

    def test_every_request_asserts_something(self):
        # A request with no assertions measures nothing and would quietly
        # raise the score.
        for request in REQUESTS:
            with self.subTest(request=request.name):
                self.assertTrue(request.must)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
