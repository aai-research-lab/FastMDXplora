"""A refusal says which refusal it is, and says it the same way every time.

The prose is for a person and it is not going anywhere. These hold the
other half: that the identifier beside it is stable, that it is total --
every refusal has one, including the ones nobody has classified yet -- and
that the registry cannot leak a suggestion the software has not earned the
right to make.

That last one is the reason this module exists rather than a docstring
saying the same thing. The rule is:

    The validator may say what the schema permits. It may never say what
    the chemistry requires.

A rule stated in prose is a rule until somebody in a hurry passes
``permitted=`` to a semantic refusal because it was convenient at that
raise site. So the gate lives in :class:`~fastmdxplora.refusals.Refusal`,
which reads the registry rather than the call, and this checks that it
does.
"""

from __future__ import annotations

import unittest

from fastmdxplora.config.loader import ConfigError, validate_config
from fastmdxplora.refusals import (
    CODES,
    SUPERSEDED,
    Code,
    CodedError,
    Disclosure,
    Kind,
    Refusal,
    code_for,
    codes_under,
    known,
    refusal_of,
    resolve,
)


class TestTheRegistryIsWellFormed(unittest.TestCase):
    """Facts about the registry that a reader is entitled to assume."""

    def test_identifiers_are_unique(self):
        ids = [c.id for c in CODES]
        self.assertEqual(len(ids), len(set(ids)), "duplicate refusal code")

    def test_identifiers_are_lowercase_dotted(self):
        for code in CODES:
            with self.subTest(code=code.id):
                self.assertRegex(code.id, r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

    def test_kinds_and_disclosures_are_from_the_vocabulary(self):
        for code in CODES:
            with self.subTest(code=code.id):
                self.assertIn(code.kind, Kind.ALL)
                self.assertIn(code.disclosure, Disclosure.ALL)

    def test_summaries_describe_the_condition_not_the_remedy(self):
        # A summary that says "use X instead" has put the remedy in the
        # registry, where it cannot know the particulars. The message at
        # the raise site is where a remedy belongs, if anywhere.
        for code in CODES:
            with self.subTest(code=code.id):
                self.assertTrue(code.summary.endswith("."))
                lowered = code.summary.lower()
                for imperative in ("use ", "try ", "install ", "set "):
                    self.assertFalse(
                        lowered.startswith(imperative),
                        f"{code.id}: summary reads as a remedy",
                    )

    def test_only_external_failures_are_retryable(self):
        # Retryable means the identical study may succeed unchanged. A
        # missing package does not qualify: something has to be installed,
        # which is a change to the machine.
        for code in CODES:
            if code.retryable:
                with self.subTest(code=code.id):
                    self.assertEqual(code.kind, Kind.ENVIRONMENTAL)
                    self.assertTrue(code.id.startswith("environment.service."))

    def test_superseded_identifiers_resolve_to_live_ones(self):
        for old, new in SUPERSEDED.items():
            with self.subTest(old=old):
                self.assertTrue(known(old))
                self.assertIsNotNone(code_for(new))
                # No cycles, and no chain that outlives one hop of patience.
                self.assertNotEqual(resolve(old), old)

    def test_a_family_can_be_asked_for_by_prefix(self):
        family = codes_under("setup.chemistry")
        self.assertTrue(family)
        for code in family:
            self.assertTrue(code.id.startswith("setup.chemistry."))
        # An exact identifier answers with itself, so a caller need not
        # know whether what it holds is a leaf or a family.
        self.assertEqual(
            [c.id for c in codes_under("setup.chemistry.unavailable")],
            ["setup.chemistry.unavailable"],
        )


class TestDisclosureIsGatedByTheRegistry(unittest.TestCase):
    """The rule the software turns on, held by machinery rather than care."""

    def test_a_structural_refusal_may_offer_the_permitted_set(self):
        exc = CodedError(
            "no", code="config.option.not_permitted",
            permitted=["cube", "dodecahedron", "octahedron"],
        )
        self.assertEqual(
            exc.refusal.permitted, ("cube", "dodecahedron", "octahedron")
        )

    def test_a_semantic_refusal_may_not_even_when_the_raise_site_offers_one(self):
        # The raise site here is wrong to pass `permitted`. The point is
        # that being wrong at a raise site cannot widen what the software
        # tells a caller, because the gate reads the registry.
        exc = CodedError(
            "no", code="setup.chemistry.protonation_undetermined",
            permitted=["protonated", "deprotonated"],
        )
        self.assertIsNone(exc.refusal.permitted)

    def test_every_code_that_promises_a_permitted_set_declares_the_key(self):
        for code in CODES:
            if code.disclosure == Disclosure.PERMITTED_VALUES:
                with self.subTest(code=code.id):
                    self.assertIn(
                        "permitted", code.detail_keys,
                        f"{code.id} discloses permitted values and does not "
                        "declare the key a caller should read them from",
                    )

    def test_no_semantic_code_declares_a_permitted_key(self):
        # Declaring one would invite a raise site to fill it and a reader
        # to look for it, and the gate would then silently drop it. Better
        # that the registry never suggests it exists.
        for code in CODES:
            if code.kind == Kind.SEMANTIC:
                with self.subTest(code=code.id):
                    self.assertNotIn("permitted", code.detail_keys)


class TestEveryRefusalHasOne(unittest.TestCase):
    """Total from the day it lands, so a caller can be written against it."""

    def test_an_uncoded_exception_still_yields_a_refusal(self):
        refusal = refusal_of(ValueError("something from a dependency"))
        self.assertEqual(refusal.code, "unclassified")
        self.assertEqual(refusal.message, "something from a dependency")
        self.assertEqual(refusal.details["exception"], "ValueError")

    def test_an_unregistered_code_degrades_rather_than_confusing_a_study(self):
        # A typo in a raise site is a defect here. It must be caught by the
        # suite, not by somebody's run failing strangely three hours in.
        exc = CodedError("no", code="setup.chemistry.protonatoin_undetermined")
        self.assertEqual(exc.code, "unclassified")

    def test_the_message_is_unchanged(self):
        # 152 test modules and the corpus's `mentioning` checks read
        # str(exc). Nothing here may alter it.
        exc = CodedError("Unknown setup option 'pH' (did you mean 'ph'?).",
                         code="config.option.unknown")
        self.assertEqual(str(exc), "Unknown setup option 'pH' (did you mean 'ph'?).")

    def test_none_valued_details_are_dropped(self):
        # A raise site passes whatever it has. An absent particular should
        # be absent, not present and null, or every reader has to check
        # both.
        exc = CodedError("x", code="config.option.unknown",
                         option="ph", suggestion=None)
        self.assertIn("option", exc.refusal.details)
        self.assertNotIn("suggestion", exc.refusal.details)


class TestTheLoaderSaysWhichRefusalItIs(unittest.TestCase):
    """The four the agent meets first, on real configs."""

    def refuse(self, cfg) -> Refusal:
        with self.assertRaises(ConfigError) as caught:
            validate_config(cfg)
        return refusal_of(caught.exception)

    def test_an_unknown_key_names_itself_and_the_nearest_valid_one(self):
        refusal = self.refuse({"setup": {"pH": 7.4}})
        self.assertEqual(refusal.code, "config.option.unknown")
        self.assertEqual(refusal.details["option"], "pH")
        self.assertEqual(refusal.details["suggestion"], "ph")
        self.assertIn("ph", refusal.permitted)

    def test_the_escape_this_module_was_written_about(self):
        # `box_shape: dodecahedran` passed the name check and the type
        # check, survived PDBFixer and force-field construction, and died
        # inside addSolvent minutes later. It is refused at the door now,
        # and says so in a word.
        refusal = self.refuse({"setup": {"box_shape": "dodecahedran"}})
        self.assertEqual(refusal.code, "config.option.not_permitted")
        self.assertEqual(refusal.details["suggestion"], "dodecahedron")
        self.assertEqual(
            refusal.permitted, ("cube", "dodecahedron", "octahedron")
        )

    def test_a_wrong_type_says_what_was_wanted_and_what_arrived(self):
        refusal = self.refuse({"setup": {"ph": "seven"}})
        self.assertEqual(refusal.code, "config.option.wrong_type")
        self.assertEqual(refusal.details["found_type"], "str")
        self.assertIn("number", refusal.details["expected_type"])

    def test_an_unknown_phase_offers_the_four(self):
        refusal = self.refuse({"include": ["setup", "simulate"]})
        self.assertEqual(refusal.code, "config.phase.unknown")
        self.assertEqual(refusal.details["given"], ["simulate"])
        self.assertIn("simulation", refusal.permitted)

    def test_a_family_matches_without_naming_every_leaf(self):
        refusal = self.refuse({"setup": {"pH": 7.4}})
        self.assertTrue(refusal.matches("config"))
        self.assertTrue(refusal.matches("config.option"))
        self.assertFalse(refusal.matches("setup"))
        # A prefix that is a string prefix but not a path segment must not
        # match: `config.opt` is not a family of `config.option.unknown`.
        self.assertFalse(refusal.matches("config.opt"))


class TestTheRecordThatReachesTheManifest(unittest.TestCase):

    def test_it_carries_the_classification_not_only_the_code(self):
        # A reader a year from now should not need this version of this
        # module to know a refusal was environmental and worth retrying.
        refusal = Refusal(
            code="environment.service.unreachable",
            message="could not reach https://files.rcsb.org: timed out",
            details={"url": "https://files.rcsb.org"},
        )
        record = refusal.as_dict()
        self.assertEqual(record["kind"], Kind.ENVIRONMENTAL)
        self.assertTrue(record["retryable"])
        self.assertEqual(record["disclosure"], Disclosure.NOTHING)
        self.assertEqual(record["details"]["url"], "https://files.rcsb.org")

    def test_it_is_json_serialisable(self):
        import json
        for code in CODES:
            with self.subTest(code=code.id):
                json.dumps(Refusal(code=code.id, message="x").as_dict())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class TestSamplingRefusalsCarryTheirNumbers(unittest.TestCase):
    """Not enough data is a refusal like any other, and says how much short.

    The family this software is most distinctive for. `summarise` already
    withheld a mean it could not stand behind; what is added here is that
    the withholding says *which* condition it was, and that the numbers
    which decide it travel with it instead of only appearing in prose.
    """

    def setUp(self):
        import numpy as np
        self.rng = np.random.default_rng(20260911)
        self.np = np

    def withheld(self, series):
        from fastmdxplora.statistics import summarise
        equilibrated, why = summarise(series)
        self.assertIsNotNone(why, "expected a withholding")
        return refusal_of(why), why

    def test_a_series_too_short_to_average(self):
        refusal, _ = self.withheld(self.np.array([1.0, 2.0]))
        self.assertEqual(refusal.code, "analysis.sampling.too_few_frames")
        self.assertEqual(refusal.details["found"], 2)

    def correlated(self, n: int = 200, phi: float = 0.98, seed: int = 0):
        """An AR(1) series with a known correlation time.

        A random walk would do as a hard case and is a poor fixture: it is
        non-stationary, so which of the two correlation refusals fires
        depends on where the equilibration detector happens to cut, and the
        answer moves with the seed. AR(1) is stationary with
        ``g ~ (1 + phi) / (1 - phi)``, so a chosen ``phi`` fixes roughly how
        many frames an independent sample costs -- about 39 at 0.95.
        """
        # Its own generator, not the shared one: drawing from `self.rng`
        # made the series depend on how many other tests had drawn from it
        # first, so the verdict moved with test ordering rather than with
        # anything about the series.
        noise = self.np.random.default_rng(seed).normal(size=n)
        series = self.np.empty(n)
        series[0] = noise[0]
        for i in range(1, n):
            series[i] = phi * series[i - 1] + noise[i]
        return series

    def test_a_correlated_run_says_how_correlated(self):
        # Which of the two correlation refusals fires depends on whether
        # the run is long enough to measure its own correlation time, and
        # both are correct answers for a series this short. The contract
        # is the family and the numbers, not the leaf: a caller deciding
        # whether to extend a run acts on the same information either way.
        refusal, _ = self.withheld(self.correlated())
        self.assertTrue(
            refusal.matches("analysis.sampling"),
            f"expected a sampling refusal, got {refusal.code}",
        )
        self.assertEqual(refusal.kind, Kind.INSUFFICIENT)
        self.assertGreater(refusal.details["statistical_inefficiency"], 5.0)
        self.assertLess(refusal.details["independent"], refusal.details["frames"])

    def test_an_independent_series_is_reported(self):
        from fastmdxplora.statistics import summarise
        equilibrated, why = summarise(self.rng.normal(size=2000))
        self.assertIsNone(why)
        self.assertIsNotNone(equilibrated)

    def test_the_withholding_is_still_a_plain_string(self):
        # Everything that reads this today reads a str. Gaining a code must
        # not change that, or the report layer and the corpus both break.
        _, why = self.withheld(self.np.array([1.0, 2.0]))
        self.assertIsInstance(why, str)
        self.assertIn("nothing to average", f"{why}")

    def test_the_shortfall_says_how_much_further(self):
        from fastmdxplora.statistics import sampling_shortfall
        short = sampling_shortfall(self.correlated(),
                                    target_independent=40,
                                    frame_interval_ns=0.01)
        self.assertFalse(short.met)
        self.assertGreater(short.more_frames, 0)
        self.assertAlmostEqual(short.more_ns, short.more_frames * 0.01, places=9)
        self.assertIn("further frames", str(short))

    def test_a_met_target_asks_for_nothing(self):
        from fastmdxplora.statistics import sampling_shortfall
        short = sampling_shortfall(self.rng.normal(size=2000),
                                   target_independent=10)
        self.assertTrue(short.met)
        self.assertEqual(short.more_frames, 0)
        self.assertIsNone(short.more_ns)

    def test_a_duration_is_not_invented(self):
        # Without a frame interval there is no honest nanosecond figure,
        # and a plausible wrong one would be planned around.
        from fastmdxplora.statistics import sampling_shortfall
        short = sampling_shortfall(self.correlated(), target_independent=40)
        self.assertIsNone(short.more_ns)


class TestTheWideningClassesDoNotNarrow(unittest.TestCase):
    """Three classes exist so that raise sites could gain codes.

    Each replaced a builtin at sites that already existed, so the thing to
    hold is that nothing which caught them before stops catching them.
    Widening is safe; narrowing would be a silent break in somebody's
    downstream handler, and the kind that only shows up in production.
    """

    def test_a_study_error_is_still_a_value_error(self):
        from fastmdxplora.refusals import StudyError
        self.assertTrue(issubclass(StudyError, ValueError))

    def test_a_missing_result_is_still_a_file_not_found(self):
        from fastmdxplora.refusals import MissingResultError
        self.assertTrue(issubclass(MissingResultError, FileNotFoundError))
        self.assertTrue(issubclass(MissingResultError, OSError))

    def test_an_unavailable_backend_answers_to_both_names(self):
        # These sites raised ImportError in one module and RuntimeError in
        # another, for no reason either module could state. Inheriting from
        # both means an except written for either keeps working.
        from fastmdxplora.refusals import BackendUnavailable
        self.assertTrue(issubclass(BackendUnavailable, ImportError))
        self.assertTrue(issubclass(BackendUnavailable, RuntimeError))

    def test_they_all_carry_refusals(self):
        from fastmdxplora.refusals import (
            BackendUnavailable, MissingResultError, StudyError)
        for cls in (StudyError, MissingResultError, BackendUnavailable):
            with self.subTest(cls=cls.__name__):
                self.assertIsInstance(refusal_of(cls("x")).code, str)

    def test_a_default_code_is_registered(self):
        # A class whose default is a typo would give every one of its sites
        # `unclassified` and nothing would say so.
        import fastmdxplora.refusals as module
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and issubclass(obj, CodedError):
                with self.subTest(cls=name):
                    self.assertTrue(known(obj.default_code))

    def test_a_missing_result_answers_to_both_names(self):
        # Narrowing is the failure mode these classes exist to avoid, and
        # this is the one that caught us: the sites replaced raised
        # FileNotFoundError in analysis/ and RuntimeError in simulation/,
        # and inheriting only the first stopped the simulation pipeline's
        # graceful-degradation path from catching its own refusal.
        from fastmdxplora.refusals import MissingResultError
        self.assertTrue(issubclass(MissingResultError, FileNotFoundError))
        self.assertTrue(issubclass(MissingResultError, RuntimeError))

    def test_every_widening_class_only_widens(self):
        # The general rule, asserted rather than remembered. A class that
        # replaced a builtin must still be an instance of it, or somebody's
        # handler stops running and nothing says so.
        from fastmdxplora import refusals as module
        replaced = {
            "StudyError": (ValueError,),
            "MissingResultError": (FileNotFoundError, RuntimeError),
            "MissingPathError": (FileNotFoundError,),
            "OutputExistsError": (FileExistsError,),
            "BackendUnavailable": (ImportError, RuntimeError),
            "UnstableRun": (RuntimeError,),
        }
        for name, bases in replaced.items():
            cls = getattr(module, name)
            for base in bases:
                with self.subTest(cls=name, base=base.__name__):
                    self.assertTrue(issubclass(cls, base))

    def test_a_defective_backend_is_still_a_runtime_error(self):
        # Distinct from BackendUnavailable, and the distinction decides what
        # a reader does: an absent backend is installed, a defective one
        # cannot be and the remedy is another platform.
        from fastmdxplora.refusals import BackendDefect
        self.assertTrue(issubclass(BackendDefect, RuntimeError))
        self.assertFalse(issubclass(BackendDefect, ValueError))
