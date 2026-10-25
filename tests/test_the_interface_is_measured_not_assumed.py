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
    # Was here as the dangerous valid-but-wrong case: 150 mM meant, 150 M
    # written, a thousandfold slip that parsed. It no longer parses --
    # `ion_concentration_M` now has a ceiling of 20 M, above the molarity
    # of water -- so it is a refusal rather than a silent wrong answer, and
    # it moved to the hard tier where the model is asked to get it right.
    "salt": CORRECT["salt"].replace("0.15", "0.015"),
    "no_minimise": CORRECT["no_minimise"].replace("  minimize: false\n", ""),
    "membrane": CORRECT["membrane"].replace(
        "  membrane_orientation_checked: true\n", ""),
}


def _answer_for(request) -> str:
    """A correct reply, built from what the request says it must contain.

    So a stub covers the whole set without a hand-written config per
    request. The tests that use it are about the loop and the report, not
    about YAML, and a dictionary that had to grow with REQUESTS would fall
    behind the first time somebody added one -- which it did.
    """
    import yaml

    config: dict = {"systems": [{"id": "a", "system": "1UBQ"}]}
    for path, value in request.must.items():
        block, key = path.split(".")
        config.setdefault(block, {})[key] = value
    return yaml.safe_dump(config)


def _scripted(answers: dict[str, str], requests=REQUESTS):
    """A stub that answers each request, and repeats itself when repaired.

    Repeating rather than advancing, because a repair prompt is the same
    request again and a stub that moved on to the next answer would be
    giving the loop a different study to validate. It also makes the
    unrepairable case the natural one to write: a stub that keeps saying
    the same wrong thing is what a model that cannot see its mistake does.
    """
    replies = iter([answers.get(r.name) or _answer_for(r)
                    for r in requests])
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
        original = tuple(r for r in REQUESTS if r.name in VALID_BUT_WRONG)
        report = measure(_scripted(VALID_BUT_WRONG, original),
                         requests=original)
        self.assertEqual(report.correct, 0)

    def test_it_says_what_was_wrong_rather_than_that_something_was(self):
        original = tuple(r for r in REQUESTS if r.name in VALID_BUT_WRONG)
        report = measure(_scripted(VALID_BUT_WRONG, original),
                         requests=original)
        wrong = next(o for o in report.outcomes if o.request == "temperature")
        self.assertTrue(any("300" in complaint for complaint in wrong.wrong))
        self.assertTrue(any("310" in complaint for complaint in wrong.wrong))

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
        original = tuple(r for r in REQUESTS if r.name in CORRECT)
        never = {name: "not yaml at all" for name in CORRECT}
        report = measure(_scripted(never, original), requests=original,
                         max_cycles=2)
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


class TestTheTiers(unittest.TestCase):
    """Easy, medium and hard, reported apart.

    The first eight answered one question: does the interface work at all.
    It does — claude-sonnet-4-6 scored 8/8, seven of them first time. So
    harder cases become informative, where before a failure could have been
    the harness.

    Easy states the value outright. Medium makes the model supply something
    the sentence did not: the number behind "physiological", a unit the
    field does not use, four settings at once. Hard is where a plausible
    answer is wrong.

    Reported per tier because one number over three difficulties hides the
    interesting thing, which is not whether a model succeeds but where it
    stops.
    """

    def test_every_tier_is_represented(self):
        from collections import Counter

        counted = Counter(r.tier for r in REQUESTS)
        for tier in ("easy", "medium", "hard"):
            with self.subTest(tier=tier):
                self.assertGreaterEqual(counted[tier], 3)

    def test_the_report_separates_them(self):
        report = measure(_scripted(CORRECT | _tiered_answers()))
        self.assertEqual(set(report.by_tier()), {"easy", "medium", "hard"})

    def test_a_tier_that_fails_is_visible_on_its_own(self):
        # The point of splitting them. A model that handles the easy set
        # and falls over on units should read as exactly that, not as a
        # slightly lower total.
        answers = CORRECT | _tiered_answers()
        answers["microsecond"] = (
            "systems:\n  - {id: a, system: 1UBQ}\n"
            "simulation:\n  duration_ns: 1\n")
        report = measure(_scripted(answers))
        self.assertEqual(report.by_tier()["easy"][0], report.by_tier()["easy"][1])
        self.assertLess(*report.by_tier()["medium"])

    def test_must_not_catches_an_invented_setting(self):
        # "Run it for a microsecond" gives a duration. A model that writes
        # a timestep has not misread the units, it has answered a question
        # nobody asked.
        request = next(r for r in REQUESTS if r.name == "microsecond")
        wrong = {"systems": [], "simulation": {"duration_ns": 1000,
                                               "timestep_fs": 1000}}
        self.assertTrue(any("timestep_fs" in f
                            for f in request.failures(wrong)))

    def test_the_hard_tier_leans_on_the_bounds(self):
        # `millimolar` and `acidic` are hard because a plausible answer --
        # 150 rather than 0.15, or a pH the scale does not have -- now
        # refuses at validation rather than running.
        from fastmdxplora.config.loader import ConfigError, validate_config

        with self.assertRaises(ConfigError):
            validate_config({"systems": [{"id": "a", "system": "x.pdb"}],
                             "setup": {"ion_concentration_M": 150}})


def _tiered_answers() -> dict[str, str]:
    """Correct replies for the requests beyond the original eight."""
    return {r.name: _answer_for(r) for r in REQUESTS if r.name not in CORRECT}
