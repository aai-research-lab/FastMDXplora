"""The browser's offer to continue a study goes on from its last segment.

The Agent in the browser is handed a ready-made continuation of the
active study, with the arithmetic done, and told to use it as the base.
It was planned from the study's own checkpoint, which is where the first
run stopped, so for a study already extended the config it offered ran
the extensions' span again and counted only the first run's production.
It is planned now as the command line's extension is: from the last
segment, counting all of them. A study never extended is offered what it
always was.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

from tests.test_a_study_is_extended_in_place import _study
from tests.test_an_extended_study_goes_on_from_its_last_segment import (
    _checkpoint,
    _segment,
)


def _offered(root: Path) -> tuple[str, dict | None]:
    """What the Agent is told, and the config it is told to start from."""
    from fastmdxplora.gui.agent_panel import _continuation_summary

    said = _continuation_summary(root)
    block = re.search(r"```yaml\n(.*)\n```", said, re.S)
    return said, (yaml.safe_load(block.group(1)) if block else None)


class TestTheOfferGoesOnFromTheLastSegment(unittest.TestCase):

    def setUp(self):
        # The first run was killed 0.1 ns into a 0.5 ns plan and extended
        # twice by 0.1 ns: 0.3 ns done, 0.2 ns of the plan left.
        self.root = _study(done_steps=50_000, finished=False)
        _segment(self.root, 1)
        self.second = _segment(self.root, 2)

    def test_it_resumes_from_the_second_segments_checkpoint(self):
        said, config = _offered(self.root)
        self.assertIsNotNone(config, said)
        simulation = config["simulation"]
        self.assertEqual(simulation["resume_from"], str(_checkpoint(self.second)))
        # Only the state moves on; the prepared system is the study's own.
        self.assertEqual(simulation["setup_from"], str(self.root.resolve()))
        self.assertIn("segment-002's checkpoint", said)

    def test_what_is_done_is_counted_across_every_segment(self):
        said, config = _offered(self.root)
        self.assertIn("production done 0.300 ns of 0.500 ns planned", said)
        self.assertIn("subtract 0.300 ns already done", said)
        self.assertAlmostEqual(config["simulation"]["duration_ns"], 0.2, places=6)

    def test_it_is_what_the_command_line_would_resume_from(self):
        from fastmdxplora.simulation.resume import extension_of

        said, config = _offered(self.root)
        plan = extension_of(self.root)
        self.assertEqual(config["simulation"]["resume_from"],
                         plan.config["simulation"]["resume_from"])
        self.assertIn(f"subtract {plan.production_done_ns:.3f} ns", said)


class TestAPlanTheSegmentsFinishedIsNotOfferedAgain(unittest.TestCase):

    def test_it_says_the_plan_is_met_rather_than_rerunning_the_extension(self):
        # Killed 0.3 ns into 0.5, and the first extension ran the other 0.2.
        # Counted from the first run alone, 0.2 ns remained, and the offer
        # resumed from where the first run stopped to run them again.
        root = _study(done_steps=150_000, finished=False)
        _segment(root, 1, done_steps=100_000, duration=0.2)
        said, config = _offered(root)
        self.assertIsNone(config, said)
        self.assertIn("production already reached 0.500 ns", said)


class TestAStudyNeverExtendedIsOfferedItsOwnCheckpoint(unittest.TestCase):

    def test_it_resumes_where_its_run_stopped(self):
        root = _study(done_steps=50_000, finished=False)
        said, config = _offered(root)
        self.assertIsNotNone(config, said)
        self.assertEqual(config["simulation"]["resume_from"], str(_checkpoint(root)))
        self.assertAlmostEqual(config["simulation"]["duration_ns"], 0.4, places=6)
        self.assertIn("production done 0.100 ns of 0.500 ns planned", said)
        self.assertNotIn("has been extended", said)


if __name__ == "__main__":
    unittest.main()
