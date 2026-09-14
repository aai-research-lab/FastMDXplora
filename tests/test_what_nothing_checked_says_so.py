"""A figure from an unchecked phase says so, on the figure.

`agent: unvalidated` means a phase worked outside the schema. The manifest
records which phases those were, and for anyone holding the directory that
is enough.

It is not enough for the case that matters. A figure ends up in a slide
deck, an email, a supervisor's folder, a paper draft — and it leaves the
manifest behind on the first copy. From then on it is a plot like any
other plot, and nothing can tell a reader that nothing checked it.

So the mark goes on the figure itself. These hold three properties: that
it appears where it should, that it does not appear where it should not,
and that no setting turns it off.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from fastmdxplora.analysis.marking import (  # noqa: E402
    MARK_TEXT,
    mark_for,
    sidecar_for,
    stamp_figure,
    write_sidecar,
)
from fastmdxplora.analysis.plotting import save_figure  # noqa: E402

UNCHECKED = {"agent": "assisted", "analysis": {"agent": "unvalidated"}}
CHECKED = {"agent": "assisted"}


class TestWhoGetsMarked(unittest.TestCase):

    def test_an_unvalidated_phase_does(self):
        self.assertIn(MARK_TEXT, mark_for(UNCHECKED, "analysis"))

    def test_the_validated_phases_of_the_same_study_do_not(self):
        # The whole reason `agent` is per phase. A trajectory from a
        # validated simulation is sound even when the analysis over it was
        # not, and marking it anyway is crying wolf — a mark that appears
        # on everything stops being read.
        self.assertEqual(mark_for(UNCHECKED, "simulation"), "")
        self.assertEqual(mark_for(UNCHECKED, "setup"), "")

    def test_a_study_a_person_wrote_is_not_marked(self):
        self.assertEqual(mark_for({"systems": []}, "analysis"), "")
        self.assertEqual(mark_for(None, "analysis"), "")

    def test_assisted_and_autonomous_are_not_marked(self):
        # Both went through the validator. The difference between them is
        # who approved, which is a provenance question the manifest
        # answers, not a warning.
        for mode in ("assisted", "autonomous"):
            with self.subTest(mode=mode):
                self.assertEqual(mark_for({"agent": mode}, "analysis"), "")

    def test_a_wholly_unvalidated_study_marks_every_phase(self):
        whole = {"agent": "unvalidated"}
        for phase in ("setup", "simulation", "analysis", "report"):
            with self.subTest(phase=phase):
                self.assertIn(MARK_TEXT, mark_for(whole, phase))

    def test_the_mark_carries_a_date(self):
        # So a reader can tell a figure marked last year from one marked
        # this morning, without the manifest.
        import time

        self.assertIn(time.strftime("%Y"), mark_for(UNCHECKED, "analysis"))


class TestItReachesTheFigure(unittest.TestCase):
    """The part that has to survive leaving the directory."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def figure(self, name, mark):
        fig, ax = plt.subplots(figsize=(4, 3))
        ax.plot([1, 2, 3], [2, 1, 3])
        return save_figure(fig, self.root / name, mark=mark, write_svg=False)

    def ink_in_lower_left(self, path):
        image = plt.imread(path)
        height, width = image.shape[:2]
        corner = image[int(height * 0.93):, : int(width * 0.35), :3]
        return int((corner.reshape(-1, 3).sum(axis=1) < 2.8).sum())

    def test_a_marked_figure_has_visible_ink_where_a_plain_one_does_not(self):
        # Pixels rather than a call count. A stamp that was drawn and
        # clipped, or drawn in white, would pass any check that only asked
        # whether the function ran.
        plain = self.figure("plain.png", "")
        marked = self.figure("marked.png", mark_for(UNCHECKED, "analysis"))
        self.assertGreater(self.ink_in_lower_left(marked),
                           self.ink_in_lower_left(plain) * 2)

    def test_an_unmarked_figure_is_untouched(self):
        # Most studies are not unvalidated, and the ordinary path must not
        # change because this feature exists.
        before = self.figure("a.png", "")
        after = self.figure("b.png", "")
        self.assertEqual(plt.imread(before).shape, plt.imread(after).shape)

    def test_stamping_nothing_does_nothing(self):
        fig, _ = plt.subplots()
        before = len(fig.texts)
        stamp_figure(fig, "")
        self.assertEqual(len(fig.texts), before)
        plt.close(fig)

    def test_the_stamp_sits_outside_the_axes(self):
        # Figure coordinates, so it cannot be mistaken for an annotation on
        # the data, and so it lands in the same place whatever the plot is.
        fig, ax = plt.subplots()
        stamp_figure(fig, "unvalidated · 2026-01-01")
        self.assertEqual(len(fig.texts), 1)
        self.assertEqual(len(ax.texts), 0)
        plt.close(fig)


class TestTheSidecar(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_a_data_file_gets_a_note(self):
        # A sidecar rather than a header comment, because a DCD has nowhere
        # to put a sentence and a rule that covered some formats and not
        # others would be one nobody could rely on.
        import json

        target = self.root / "rmsd.dat"
        target.write_text("1 0.2\n")
        note = write_sidecar(target, "analysis", mark_for(UNCHECKED, "analysis"))
        self.assertEqual(note, sidecar_for(target))
        record = json.loads(note.read_text())
        self.assertTrue(record["unvalidated"])
        self.assertEqual(record["phase"], "analysis")

    def test_it_says_what_is_absent_rather_than_what_is_wrong(self):
        # The result may be perfectly good. What is missing is the
        # checking, and overstating that would teach the first person who
        # found a sound unvalidated figure to ignore the mark.
        import json

        target = self.root / "rmsd.dat"
        target.write_text("1 0.2\n")
        note = write_sidecar(target, "analysis",
                             mark_for(UNCHECKED, "analysis"))
        meaning = json.loads(note.read_text())["what_this_means"]
        self.assertIn("nothing checked the method", meaning)
        self.assertNotIn("wrong", meaning)

    def test_no_note_for_a_checked_phase(self):
        target = self.root / "rmsd.dat"
        target.write_text("1 0.2\n")
        self.assertIsNone(
            write_sidecar(target, "analysis", mark_for(CHECKED, "analysis")))
        self.assertFalse(sidecar_for(target).exists())


class TestItCannotBeTurnedOff(unittest.TestCase):
    """No setting removes it. Rerunning inside the schema does."""

    def test_the_schema_offers_no_way_to_suppress_it(self):
        from fastmdxplora.config.schema import PHASE_SCHEMAS, TOP_LEVEL

        names = {f.name for f in TOP_LEVEL.fields}
        for schema in PHASE_SCHEMAS.values():
            names |= {f.name for f in schema.fields}
        for suppressor in ("mark", "marking", "unvalidated_mark",
                           "suppress_mark", "no_mark"):
            with self.subTest(setting=suppressor):
                self.assertNotIn(suppressor, names)

    def test_the_only_way_out_is_to_be_validated(self):
        # Which is the action the mark exists to prompt. A flag would make
        # it a formality.
        marked = mark_for({"analysis": {"agent": "unvalidated"}}, "analysis")
        unmarked = mark_for({"analysis": {"agent": "assisted"}}, "analysis")
        self.assertTrue(marked)
        self.assertFalse(unmarked)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
