"""A time-series figure shows which frames its mean came from.

The software works out where a series settled, averages only after that,
and decides whether the run is long enough against its own correlation time
for an error bar to mean anything. Every one of those numbers went into
`options.json` and none reached the figure -- so a reader saw a line and had
to take on trust both which part of it the mean came from and whether that
mean carried an uncertainty at all.

The last of those is the one that matters. Drawing a mean without saying it
has no error bar is the claim this package exists not to make.
"""

from __future__ import annotations

import matplotlib
import numpy as np

matplotlib.use("Agg")

from fastmdxplora.analysis.plotting import new_figure  # noqa: E402


def _series(record, x=None):
    """A real Analysis, so the overlay is exercised on the actual object.

    Hand-binding the methods onto a stand-in was the first attempt, and it
    bound them under names the code does not call.
    """
    from fastmdxplora.analysis.base import Analysis

    class _Probe(Analysis):
        name = "probe"
        time_series = True

        def compute(self, traj):  # pragma: no cover - never run
            return None

        def plot(self, result, ax):  # pragma: no cover - never run
            return None

        def default_ylabel(self):
            return self._user_ylabel

    probe = _Probe.__new__(_Probe)
    probe.findings = {"mean": record}
    probe._x_for_overlay = (
        np.linspace(0.0, 100.0, record["n_frames"]) if x is None else x)
    probe._user_ylabel = "Ligand RMSD (nm)"
    return probe


def _record(**kwargs):
    base = {"mean": 0.1143, "discard": 1127, "n_frames": 2000,
            "standard_error": float("nan"), "effective_samples": 9.77}
    base.update(kwargs)
    return base


def _drawn(series):
    fig, ax = new_figure(title="t")
    ax.plot(series._x_for_overlay,
            np.linspace(0.0, 0.12, len(series._x_for_overlay)))
    series._mark_what_the_mean_rests_on(ax)
    return ax


class TestTheEquilibratedRegionIsVisible:

    def test_the_excluded_frames_are_marked_and_named(self):
        ax = _drawn(_series(_record()))
        labels = [t.get_text() for t in ax.get_legend().get_texts()]

        assert any("equilibration, excluded" in text for text in labels)
        assert any("56%" in text for text in labels), (
            "How much of the run was thrown away is the reader's first "
            "question about a mean drawn over part of it.")

    def test_nothing_is_shaded_when_nothing_was_discarded(self):
        ax = _drawn(_series(_record(discard=0)))
        labels = [t.get_text() for t in ax.get_legend().get_texts()]

        assert not any("equilibration, excluded" in text for text in labels)

    def test_the_mean_is_drawn_only_over_the_settled_part(self):
        series = _series(_record())
        ax = _drawn(series)
        settled = [line for line in ax.get_lines()
                   if line.get_linestyle() == "--"]

        assert settled, "The mean after equilibration should be drawn."
        left = settled[0].get_xdata()[0]
        assert left > 50.0, (
            "A mean line spanning the discarded frames says the average "
            "includes them.")


class TestAnAbsentErrorBarIsSaidSo:

    def test_a_refused_error_bar_is_named_in_the_legend(self):
        ax = _drawn(_series(_record()))
        labels = " ".join(t.get_text() for t in ax.get_legend().get_texts())

        assert "too few for an error bar" in labels, (
            "The consequence has to be stated, or the absence reads as an "
            "oversight rather than a decision.")
        assert "about 10 independent samples" in labels

    def test_the_count_is_a_whole_number(self):
        """N/g is a ratio; a fraction of an observation is not a thing.

        873 settled frames over a statistical inefficiency of 89 gives
        9.77, and printing "9.8 independent samples" claims a precision the
        estimate does not have -- `g` is itself uncertain, and on a run this
        short the software's own record says halving the frames changes it.
        """
        ax = _drawn(_series(_record()))
        labels = " ".join(t.get_text() for t in ax.get_legend().get_texts())

        assert "9.8" not in labels
        assert "about 10" in labels

    def test_a_count_below_one_is_said_in_words(self):
        from fastmdxplora.analysis.base import Analysis

        assert Analysis._independent_samples(0.4) == (
            "fewer than one independent sample")

    def test_one_sample_is_singular(self):
        from fastmdxplora.analysis.base import Analysis

        assert "1 independent sample" in Analysis._independent_samples(1.2)
        assert "samples" not in Analysis._independent_samples(1.2)

    def test_the_count_is_called_independent_not_effective(self):
        """Because the author of this package had to ask what it meant.

        "Effective samples" is the term of art and it did not communicate.
        Independence is what the number is about: consecutive frames of a
        trajectory are not independent observations of anything.
        """
        ax = _drawn(_series(_record()))
        labels = " ".join(t.get_text() for t in ax.get_legend().get_texts())

        assert "independent" in labels
        assert "effective" not in labels

    def test_the_label_is_two_lines(self):
        """One line carrying the value and the caveat overflowed the axes."""
        ax = _drawn(_series(_record()))
        settled = [t.get_text() for t in ax.get_legend().get_texts()
                   if "mean after equilibration" in t.get_text()]

        assert settled and "\n" in settled[0]


class TestGreyscaleStillCarriesTheOverlay:
    """The overlay must survive the mode it was not drawn in."""

    def test_the_mean_and_the_span_are_told_apart_without_hue(self):
        from fastmdxplora.analysis import plotting

        try:
            plotting.use_greyscale(True)
            ax = _drawn(_series(_record()))
            dashed = [line for line in ax.get_lines()
                      if line.get_linestyle() == "--"]
            assert dashed, "The settled mean is still drawn."
            assert ax.get_legend() is not None
            # Value and line style carry the distinction, not hue.
            assert plotting.colour("FAINT") != plotting.colour("ACCENT")
        finally:
            plotting.use_greyscale(False)

    def test_an_error_bar_that_exists_is_drawn_and_quoted(self):
        series = _series(_record(standard_error=0.0031))
        ax = _drawn(series)
        labels = " ".join(t.get_text() for t in ax.get_legend().get_texts())

        assert "±" in labels and "0.0031" in labels
        assert "no error bar" not in labels
        assert ax.collections, "The uncertainty band should be drawn."

    def test_the_unit_comes_from_the_axis_that_states_it(self):
        series = _series(_record())
        assert series._mean_unit() == " nm"

    def test_a_label_without_a_unit_invents_none(self):
        series = _series(_record())
        series._user_ylabel = "Fraction of frames present"
        assert series._mean_unit() == ""


class TestItDeclinesRatherThanGuesses:

    def test_no_overlay_without_a_mean(self):
        ax = _drawn(_series(_record(mean=float("nan"))))
        assert ax.get_legend() is None

    def test_no_overlay_when_the_axis_is_not_the_one_recorded(self):
        """An analysis plotting against something else must not be annotated.

        The overlay places the boundary using the x values `frame_axis`
        returned. If the plot used a different axis those coordinates mean
        nothing, and a confidently misplaced boundary is worse than none.
        """
        series = _series(_record())
        fig, ax = new_figure(title="t")
        ax.plot([0, 1, 2], [0, 1, 2])       # a different axis entirely
        ax.set_xlim(0, 2)
        series._mark_what_the_mean_rests_on(ax)

        assert ax.get_legend() is None

    def test_no_overlay_when_the_frame_count_disagrees(self):
        series = _series(_record(), x=np.linspace(0.0, 100.0, 7))
        ax = _drawn(series)
        assert ax.get_legend() is None


class TestTheLegendStaysInsideTheAxes:
    """`loc="best"` finds the emptiest corner; it does not check the width.

    Matplotlib will draw a legend wider than the axes, running past the
    frame into blank canvas. At the full 6.5-inch width nothing notices. At
    a journal single-column width it lands on the label most worth reading.
    """

    def _overflow(self, figsize):
        from fastmdxplora.analysis.plotting import fit_legend

        fig, ax = new_figure(title="t", figsize=figsize)
        ax.plot([0, 1], [0, 1], label="settled mean 0.1143 nm\n"
                                      "9.8 independent samples: too few "
                                      "for an error bar")
        ax.legend(loc="best", fontsize=7.5)
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        before = ax.get_legend().get_window_extent(renderer=renderer).width
        fit_legend(ax)
        fig.canvas.draw()
        after = ax.get_legend().get_window_extent(renderer=renderer).width
        available = ax.get_window_extent(renderer=renderer).width
        return before, after, available

    def test_a_column_width_legend_is_brought_inside(self):
        before, after, available = self._overflow((3.3, 2.4))

        assert before > available, (
            "If this stops overflowing, matplotlib has changed and the fix "
            "may no longer be needed.")
        assert after <= available * 1.02

    def test_a_legend_that_already_fits_is_left_alone(self):
        from fastmdxplora.analysis.plotting import fit_legend

        fig, ax = new_figure(title="t", figsize=(6.5, 4.2))
        ax.plot([0, 1], [0, 1], label="short")
        ax.legend(loc="best", fontsize=7.5)
        fig.canvas.draw()
        sizes_before = [t.get_fontsize() for t in ax.get_legend().get_texts()]
        fit_legend(ax)
        sizes_after = [t.get_fontsize() for t in ax.get_legend().get_texts()]

        assert sizes_before == sizes_after

    def test_no_legend_is_not_an_error(self):
        from fastmdxplora.analysis.plotting import fit_legend

        fig, ax = new_figure(title="t")
        ax.plot([0, 1], [0, 1])
        fit_legend(ax)   # must not raise
