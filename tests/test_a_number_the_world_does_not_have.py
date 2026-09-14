"""Bounds on numbers, where the bound is a fact rather than a preference.

`choices` refuses a name the software does not know. Until now nothing
refused a *value* the quantity cannot have: pH 25, a negative duration, a
temperature below absolute zero, a salt concentration above the molarity
of water. All four validated, and three of them would have run.

Found while measuring the natural-language interface. The eight requests
included one where the sentence says millimolar and the field is molar —
150 against 0.15 — and the reason that case is dangerous is precisely that
nothing caught it. A thousand times the intended ionic strength, a config
that parses, and a trajectory that looks ordinary.

The line these draw is between a fact and a view. A pH of 25 is not a
strict reading of pH, it is not a pH. A 10 fs timestep is unstable for
almost every system and is *not impossible* — somebody's coarse-grained
run may want it — so it has no upper bound here, and the runner refuses
the integration if it blows up. That is the honest place for a judgement
about stability, and this is the honest place for a fact about a scale.
"""

from __future__ import annotations

import unittest

from fastmdxplora.config.loader import ConfigError, validate_config
from fastmdxplora.config.schema import PHASE_SCHEMAS
from fastmdxplora.refusals import refusal_of


def _config(**blocks):
    return {"systems": [{"id": "a", "system": "x.pdb"}], **blocks}


class TestWhatIsRefused(unittest.TestCase):

    def refusal_for(self, **blocks):
        with self.assertRaises(ConfigError) as caught:
            validate_config(_config(**blocks))
        return refusal_of(caught.exception)

    def test_a_ph_off_the_scale(self):
        found = self.refusal_for(setup={"ph": 25})
        self.assertEqual(found.code, "config.option.out_of_range")
        # The bound is in the message. "Out of range" with no range is a
        # refusal a caller cannot act on.
        self.assertIn("14", found.message)

    def test_a_negative_duration(self):
        self.assertEqual(
            self.refusal_for(simulation={"duration_ns": -5}).code,
            "config.option.out_of_range")

    def test_a_temperature_below_absolute_zero(self):
        self.assertEqual(
            self.refusal_for(simulation={"temperature_K": -10}).code,
            "config.option.out_of_range")

    def test_millimolar_written_as_molar(self):
        # The one that would actually hurt somebody: 150 mM meant, 150 M
        # written, a thousandfold error that parses. Pure water is about
        # 55 M, so nothing dissolved in water reaches 150.
        found = self.refusal_for(setup={"ion_concentration_M": 150})
        self.assertEqual(found.code, "config.option.out_of_range")
        self.assertEqual(found.details["given"], 150)

    def test_the_refusal_carries_both_bounds(self):
        found = self.refusal_for(setup={"ph": 25})
        self.assertEqual(found.details["minimum"], 0.0)
        self.assertEqual(found.details["maximum"], 14.0)


class TestWhatIsNotRefused(unittest.TestCase):
    """A bound that refuses legitimate work is worse than no bound."""

    def test_an_ordinary_study(self):
        validate_config(_config(
            setup={"ph": 7.4, "ion_concentration_M": 0.15},
            simulation={"temperature_K": 310, "duration_ns": 50,
                        "timestep_fs": 4}))

    def test_a_strong_brine(self):
        # Saturated NaCl is near 6 M. The ceiling is 20, which catches a
        # thousandfold slip and leaves every real electrolyte alone.
        validate_config(_config(setup={"ion_concentration_M": 4.0}))

    def test_an_unstable_timestep(self):
        # Unstable for almost every system, and not impossible. A guessed
        # ceiling would refuse somebody's coarse-grained run; the runner
        # refuses the integration if it blows up, which is where a
        # judgement about stability belongs.
        validate_config(_config(simulation={"timestep_fs": 10}))

    def test_a_cryogenic_temperature(self):
        # 100 K is unusual and legitimate. Only below zero is impossible.
        validate_config(_config(simulation={"temperature_K": 100}))

    def test_the_extremes_of_the_ph_scale(self):
        # Inclusive bounds: 0 and 14 are pH values, not errors.
        for ph in (0, 14):
            with self.subTest(ph=ph):
                validate_config(_config(setup={"ph": ph}))


class TestTheBoundsAreDeclaredWhereTheyBelong(unittest.TestCase):

    def test_they_live_on_the_field(self):
        # Beside `choices`, in the one declaration the GUI, CLI, API and
        # validation all read. A bound enforced in the loader and absent
        # from the schema would be invisible to the other three.
        setup = {f.name: f for f in PHASE_SCHEMAS["setup"].fields}
        self.assertEqual(setup["ph"].minimum, 0.0)
        self.assertEqual(setup["ph"].maximum, 14.0)

    def test_a_setting_with_no_knowable_bound_declares_none(self):
        simulation = {f.name: f for f in PHASE_SCHEMAS["simulation"].fields}
        self.assertIsNone(simulation["timestep_fs"].maximum)

    def test_every_bounded_setting_is_numeric(self):
        # A bound on a string would be nonsense and the checker skips it,
        # so declaring one would be a note that does nothing.
        for phase, schema in PHASE_SCHEMAS.items():
            for field in schema.fields:
                if field.minimum is None and field.maximum is None:
                    continue
                with self.subTest(phase=phase, setting=field.name):
                    types = (field.type if isinstance(field.type, tuple)
                             else (field.type,))
                    self.assertTrue(set(types) & {int, float})

    def test_no_bound_is_inverted(self):
        for phase, schema in PHASE_SCHEMAS.items():
            for field in schema.fields:
                if field.minimum is None or field.maximum is None:
                    continue
                with self.subTest(phase=phase, setting=field.name):
                    self.assertLessEqual(field.minimum, field.maximum)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
