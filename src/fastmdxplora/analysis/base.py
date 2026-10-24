"""Abstract base class for all FastMDXplora analysis modules.

Every analysis (RMSD, RMSF, Rg, ...) subclasses :class:`Analysis` and
implements ``compute()`` and ``plot()``. The base class handles everything
else: output directory creation, options-manifest serialization, atom
selection resolution, status tracking, and the ``run()`` convenience method
that does ``compute() -> save_data() -> plot() -> save_figure()`` in order.

The contract is deliberately small:

  - ``compute(traj)`` returns a Python object (usually a numpy array or
    pandas DataFrame). It must be deterministic, side-effect-free, and
    inexpensive to call again with the same input.
  - ``plot(result, ax)`` draws onto a matplotlib Axes. It must not call
    ``plt.show()`` or close the figure — the caller controls that.
  - ``save_data(result, path)`` writes the computed result to disk. The
    default implementation handles numpy arrays and DataFrames; analyses
    with non-tabular output can override it.

Outputs land in ``<output_dir>/<analysis_name>/``:

  ::

    <output_dir>/
    └── rmsd/
        ├── rmsd.dat       # the numerical data
        ├── rmsd.png       # the figure
        └── options.json   # parameter manifest for this analysis
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis.plotting import (
    closes_what_it_opens, colour, drawn_in, fit_legend, new_figure, save_figure,
    settle_figure_colours,
    settle_figure_width,
    sized_for,
)
from fastmdxplora.utils.logging import get_logger
from fastmdxplora.refusals import StudyError

logger = get_logger("analysis.base")


def _unit_in(label: str) -> str:
    """The unit an axis label states in its closing brackets, or "".

    "RMSD (nm)" gives "nm"; "Q (fraction native contacts)" gives "", since
    anything longer than a unit is a description.
    """
    if label.endswith(")") and "(" in label:
        inside = label[label.rindex("(") + 1:-1].strip()
        if inside and len(inside) <= 12:
            return inside
    return ""


def _frame_interval_ns(traj: Any) -> float | None:
    """How far apart the analysed frames are, from the clock the loader set.

    None where it set none: the loader fills the time with NaN when the run
    did not say, and a duration is then not invented.
    """
    try:
        time_ps = np.asarray(traj.time, dtype=float)
    except (AttributeError, TypeError, ValueError):
        return None
    if time_ps.size < 2 or not np.all(np.isfinite(time_ps)):
        return None
    steps = np.diff(time_ps)
    if not np.all(steps > 0):
        return None
    return float(np.median(steps)) / 1000.0


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass
class AnalysisResult:
    """Lightweight record of one analysis invocation.

    Returned by :meth:`Analysis.run` and aggregated by the orchestrator.
    Includes both the computed data (for in-memory consumers) and the
    on-disk artifact paths (for report generation and provenance).
    """

    name: str
    status: str  # "ok" | "error" | "skipped"
    data: Any = None
    output_dir: Path | None = None
    figure_path: Path | None = None
    data_path: Path | None = None
    options_path: Path | None = None
    artifacts: list[Path] = field(default_factory=list)
    message: str = ""
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for inclusion in the analysis manifest."""
        return {
            "name": self.name,
            "status": self.status,
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "figure_path": str(self.figure_path) if self.figure_path else None,
            "data_path": str(self.data_path) if self.data_path else None,
            "options_path": str(self.options_path) if self.options_path else None,
            "artifacts": [str(path) for path in self.artifacts],
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------
class Analysis(ABC):
    """Base class for a single trajectory analysis module.

    Subclasses must:
      - Set the class attribute ``name`` to a short identifier (e.g. ``"rmsd"``).
      - Implement :meth:`compute` which takes an MDTraj trajectory and
        returns the analysis result.
      - Implement :meth:`plot` which renders ``self.result`` onto an Axes.

    Subclasses may override :meth:`save_data` and :attr:`default_selection`.
    """

    #: Short identifier used in output paths and the manifest.
    name: str = "analysis"

    #: Whether ``selection`` means anything for this analysis. An analysis
    #: that decides its own atoms -- a protein-ligand measure works out both
    #: sides from the ligand's residue name, dihedrals from the backbone --
    #: has nothing to apply it to, and offering a control that does nothing is
    #: worse than not offering one: it looks like it worked.
    honours_selection: bool = True

    #: Default atom selection (MDTraj selection language). ``None`` means
    #: "use the whole trajectory". Subclasses override when an analysis
    #: only makes sense on a subset of atoms (e.g. RMSF on CA atoms).
    default_selection: str | None = None

    #: The per-frame scalar whose ensemble average means something, as
    #: ``(column, label)``. ``column`` names a column when ``compute()``
    #: returns a DataFrame and is ``None`` when it returns a bare per-frame
    #: array. ``None`` for the attribute itself -- the default -- means no
    #: weighted average of this analysis is offered.
    #:
    #: On a metadynamics run the trajectory is not a Boltzmann ensemble, so
    #: every mean reported from it is an average over a distribution the bias
    #: flattened on purpose. Declaring this lets that mean be recomputed
    #: against the deposited bias and reported beside the raw one. Analyses
    #: whose result is not one number per frame leave it unset: reweighting a
    #: clustering or a projection is a harder question than a weighted mean,
    #: and the report says so rather than guessing.
    reweightable: tuple[str | None, str] | None = None

    #: Whether ``compute()`` returns per-frame categorical labels, either as
    #: an array or as a mapping of method name to array. A population is a
    #: weighted count of an indicator, so it reweights exactly as a mean
    #: does, and how often a state is visited is the thing a biased run
    #: distorts most.
    #:
    #: What this does not correct is which states exist. The clustering was
    #: performed on the biased frames, so the groupings themselves are shaped
    #: by where the bias sent the system; reweighting says how often each was
    #: really visited, not that the right ones were found.
    reweightable_populations: bool = False

    #: How many atoms this analysis's selection must match before a rigid
    #: body superposition onto it is defined. Three is the minimum for a
    #: rotation; below that there is no unique answer.
    #:
    #: Alanine dipeptide has one CA, which is the default selection for RMSD
    #: and RMSF, and MDTraj responded by printing "UNCONVERGED ROTATION
    #: MATRIX. RETURNING IDENTITY" once per frame from its C extension and
    #: returning distances measured against no alignment at all. Thousands of
    #: lines of it, and a column of numbers that looked like results.
    min_atoms_to_align: int = 0

    #: Human-readable description used in figure titles.
    description: str = ""

    #: How close the solute came to its own periodic images in these frames,
    #: as the image check found it (``findings.solute_image`` of
    #: :mod:`fastmdxplora.analysis.image_check`), and its series of closest
    #: approaches, one a frame. The orchestrator hands both to every analysis
    #: of the same frames; where the solute met its image in the frames a
    #: mean uses, the mean carries a note of it, its error kept
    #: (:meth:`_qualify_by_the_solute_image`).
    solute_image: dict[str, Any] | None = None
    solute_image_series: Any = None
    #: The study whose analysis folder this is, where the orchestrator runs
    #: it (its setup record gives the run's cutoff); None run alone.
    study_dir: Path | None = None


    #: True for analyses that only apply to protein-ligand complexes (e.g.
    #: ligand pose RMSD). The orchestrator runs these automatically when a
    #: ligand is present and skips them otherwise. They can still be
    #: explicitly requested via ``include``.
    requires_ligand: bool = False

    #: True for analyses of a lipid bilayer (area per lipid, thickness,
    #: chain order). Left out of the automatic plan of a system without one,
    #: and honoured when named in ``include``, where they say why not.
    requires_bilayer: bool = False

    #: Whether ``compute`` returns one value per frame.
    #:
    #: A quantity measured every frame has a mean, and a mean is not a
    #: measurement until two things are known: whether the system had
    #: equilibrated by the time the averaging started, and how many
    #: *independent* observations the average rests on. Both are recorded
    #: automatically for
    #: an analysis that says yes here.
    #:
    #: Declared rather than inferred from the array's length. A per-atom
    #: result on a trajectory that happens to have as many frames as atoms
    #: would otherwise be summarised as though it were a time series, and the
    #: numbers would look right.
    time_series: bool = False

    def __init__(
        self,
        *,
        selection: str | None = None,
        output_dir: str | Path | None = None,
        title: str | None = None,
        xlabel: str | None = None,
        ylabel: str | None = None,
        figsize: tuple[float, float] | None = None,
        xunit: str | None = None,
        figure_colours: str | None = None,
        figure_width: str | None = None,
        **options: Any,
    ) -> None:
        """Initialize the analysis with user-supplied options.

        Parameters
        ----------
        selection : str, optional
            MDTraj atom selection string. Overrides ``default_selection``.
        output_dir : path, optional
            Where to write outputs. The analysis appends its own ``name``
            subdirectory. If omitted, defaults to the current directory.
        title, xlabel, ylabel : str, optional
            Figure customization hooks. If provided, these override the
            defaults used by :meth:`figure_title`, :meth:`default_xlabel`,
            and :meth:`default_ylabel` respectively.
        figsize : (float, float), optional
            Figure size in inches.
        xunit : {"ns", "ps", "frames", None}, optional
            X-axis unit for time-series analyses (RMSD, Rg, etc.). The
            default is ``"ns"`` when the trajectory carries a timestep,
            else ``"frames"``. Analyses without a time axis ignore this.
        figure_colours : {"colour", "greyscale", "both"}, optional
            What the figure is drawn in. ``"both"`` writes the colour figure
            as ``<name>.png`` and a greyscale copy as
            ``<name>_greyscale.png``, which is what a paper usually wants:
            colour for the online version, greyscale for print. American
            spellings are accepted. Default ``"colour"``.
        figure_width : {"page", "single_column", "double_column"}, optional
            How wide the figure is drawn, with type sized for that width:
            the page (6.5 in, the default), one journal column (89 mm) or
            two (183 mm).
        **options
            Analysis-specific keyword arguments. Subclasses access these
            via ``self.options``.
        """
        if selection is not None and not self.honours_selection:
            raise StudyError(
                f"{type(self).__name__} works out its own atoms, so "
                f"`selection` would have no effect. Accepting it would let a "
                f"result look as though it had been restricted when it "
                f"had not."
            , code="analysis.option.inapplicable")
        #: What the analysis worked out while running, as opposed to what it
        #: was told. Recorded beside the options and kept out of them, because
        #: a report lists the options.
        self.findings: dict[str, Any] = {}
        self.selection: str | None = (
            selection if selection is not None else self.default_selection
        )
        self.output_dir: Path = (
            Path(output_dir) if output_dir is not None else Path.cwd()
        ) / self.name
        self.options: dict[str, Any] = dict(options)

        # User-overridable plot customizations
        self._user_title: str | None = title
        self._user_xlabel: str | None = xlabel
        self._user_ylabel: str | None = ylabel
        self._user_figsize: tuple[float, float] | None = figsize
        self._user_xunit: str | None = xunit
        #: Settled here rather than at each use, so a misspelling is refused
        #: once at construction and not discovered when the figure is drawn.
        self.figure_colours: str = settle_figure_colours(figure_colours)
        self.figure_width: str = settle_figure_width(figure_width)

        self.result: Any = None

    # ------------------------------------------------------------------
    # Subclass hooks (mandatory)
    # ------------------------------------------------------------------
    @abstractmethod
    def compute(self, traj: md.Trajectory) -> Any:
        """Compute the analysis. Must be deterministic and side-effect-free.

        Parameters
        ----------
        traj : mdtraj.Trajectory
            The trajectory to analyze, already sliced/strided as the user
            requested. The analysis should respect ``self.selection`` if
            relevant.

        Returns
        -------
        Any
            The analysis result. Most commonly a 1-D or 2-D NumPy array or
            a pandas DataFrame. The same object is later passed to
            :meth:`plot` and :meth:`save_data`.
        """

    @abstractmethod
    def plot(self, result: Any, ax: plt.Axes) -> None:
        """Render ``result`` onto ``ax``. Do not call plt.show()."""

    # ------------------------------------------------------------------
    # Subclass hooks (optional)
    # ------------------------------------------------------------------
    def save_data(self, result: Any, path: Path) -> Path:
        """Write the computed result to ``path``.

        Default behaviour:
          - 1-D / 2-D numpy arrays: ``np.savetxt`` (whitespace-delimited).
          - pandas DataFrames: ``to_csv`` (comma-delimited).
          - Anything else: subclass must override.

        Returns the path actually written.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        # Which of the two a `.dat` file is, said in the file as well as
        # recorded. Both extensions are `.dat` because both are this
        # analysis's data, but one is whitespace with no header and the
        # other is comma-separated with one, so a reader that guesses wrong
        # gets a ValueError at best and a column of NaN at worst.
        #
        # `options.json` has carried the answer since 2.5.4, which helps
        # only a reader who knows to look and still has the run directory.
        # A deposited file travels: into a supplementary archive, an email,
        # a student's home directory. So the array form now names its own
        # layout on a `#` line, which `np.loadtxt` skips by default and
        # `pd.read_csv(comment="#")` skips on request. The comma form needs
        # no such line -- its header row already is one.
        if isinstance(result, np.ndarray):
            # %.8e preserves ~8 significant figures — well beyond what
            # MD trajectories ever resolve, and round-trips cleanly through
            # np.loadtxt.
            np.savetxt(
                path, result, fmt="%.8e",
                header=(f"{self.name}: whitespace-delimited, no column "
                        f"header. Read with np.loadtxt(path)."),
            )
            self._data_format = {
                "layout": "whitespace-delimited, no header",
                "read_with": "np.loadtxt(path)",
            }
        elif isinstance(result, pd.DataFrame):
            result.to_csv(path, index=False)
            self._data_format = {
                "layout": "comma-separated, one header line",
                "read_with": (
                    "pd.read_csv(path), or "
                    "np.loadtxt(path, delimiter=',', skiprows=1)"),
                "columns": [str(c) for c in result.columns],
            }
        else:
            raise NotImplementedError(
                f"{type(self).__name__}.save_data does not know how to "
                f"serialize {type(result).__name__}. Override save_data() "
                f"in the subclass."
            )
        return path

    def figure_title(self) -> str:
        """Title shown above the figure.

        If the user passed ``title=`` at construction, that wins. Otherwise
        the subclass's :attr:`description` is used; failing that, the
        analysis name in uppercase.
        """
        if self._user_title is not None:
            return self._user_title
        return self.description or self.name.upper()

    def default_xlabel(self) -> str | None:
        """X-axis label when the user has not overridden it.

        Override in subclasses to set a domain-specific default. Returning
        ``None`` means "leave whatever the plot() method set". User-supplied
        ``xlabel=`` at construction always wins regardless.
        """
        return None

    def default_ylabel(self) -> str | None:
        """Y-axis label when the user has not overridden it. See :meth:`default_xlabel`."""
        return None

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------
    def run(self, traj: md.Trajectory) -> AnalysisResult:
        """Compute, plot, and save in one call.

        This is the orchestrator's standard entry point. Returns an
        :class:`AnalysisResult` regardless of success — check
        ``result.status`` for ``"ok"`` vs ``"error"``.
        """
        started = datetime.now(timezone.utc).isoformat()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        options_path = self._write_options_manifest()

        try:
            # Said before `compute`, and written again below if `compute`
            # then fails, so a failed run still says it; cleared first, so
            # an instance run again does not carry it.
            self.findings.pop("made_whole", None)
            traj, made_whole = _whole_if_broken(traj)
            if made_whole:
                self.findings["made_whole"] = made_whole
            self.result = self.compute(traj)
            self._record_what_the_mean_is_worth(traj)
            self._qualify_by_the_solute_image(traj)
            # Written again, because an analysis can only record some things
            # once it has looked at the trajectory: how confidently it knew
            # the ligand's chemistry, which measurements it could not make and
            # why, what binding modes it found. Writing the manifest before
            # `compute` and never again meant that everything an analysis
            # learned about its own run was thrown away.
            options_path = self._write_options_manifest()
            data_path = self.save_data(self.result, self.output_dir / f"{self.name}.dat")
            with closes_what_it_opens():
                figures = self._do_plot()
            # The first is the primary -- `<name>.png` -- and is what
            # `figure_path` has always meant. Any greyscale copy is an
            # artifact beside it, so a reader that knows only about
            # `figure_path` is unaffected by asking for both.
            figure_path = figures[0]
            figure_artifacts = []
            for drawn in figures:
                figure_artifacts.append(drawn)
                svg_path = drawn.with_suffix(".svg")
                if svg_path.is_file():
                    figure_artifacts.append(svg_path)
            finished = datetime.now(timezone.utc).isoformat()
            return AnalysisResult(
                name=self.name,
                status="ok",
                data=self.result,
                output_dir=self.output_dir,
                figure_path=figure_path,
                data_path=data_path,
                options_path=options_path,
                artifacts=[data_path, *figure_artifacts, options_path],
                message=f"{self.name}: ok",
                started_at=started,
                finished_at=finished,
            )
        except Exception as exc:  # noqa: BLE001 -- captured, reported, propagated via status
            finished = datetime.now(timezone.utc).isoformat()
            # ERROR with its traceback, in the log; not on the console,
            # where the results table already carries this analysis's row
            # and the reason beneath it. This line arrived several analyses
            # earlier and said only that something failed.
            logger.error("Analysis '%s' failed", self.name, exc_info=True,
                         extra={"to_console": False})
            if self.findings.get("made_whole"):
                try:
                    options_path = self._write_options_manifest()
                except Exception:  # noqa: BLE001 -- the failure being reported matters more
                    logger.debug("Could not rewrite %s's options", self.name, exc_info=True)
            return AnalysisResult(
                name=self.name,
                status="error",
                output_dir=self.output_dir,
                options_path=options_path,
                message=f"{self.name}: {exc}",
                started_at=started,
                finished_at=finished,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _record_what_the_mean_is_worth(self, traj: md.Trajectory) -> None:
        """Put the mean, its error and what stands behind it in the findings.

        Recorded rather than enforced: an analysis whose series does not
        support a mean still produces its figure and its data, and the reason
        travels with them. Withholding an RMSD plot because the run was short
        would hide the evidence that it was short.
        """
        if not self.time_series:
            return

        import numpy as np

        from fastmdxplora.statistics import mean_record

        values = self.result
        if hasattr(values, "columns"):
            # A frame of (frame, value): the frame number is the axis, not a
            # quantity, and averaging it would give the middle of the run.
            columns = [c for c in values.columns if str(c).lower() != "frame"]
            if len(columns) != 1:
                return
            values = values[columns[0]]
        if hasattr(values, "to_numpy"):
            values = values.to_numpy()
        try:
            series = np.asarray(values, dtype=float).squeeze()
        except (TypeError, ValueError):
            return
        if series.ndim != 1 or series.size != traj.n_frames:
            return

        record = mean_record(series, frame_interval_ns=_frame_interval_ns(traj))
        unit = self._recorded_unit()
        if unit is not None:
            record["unit"] = unit
        self.findings["mean"] = record

        # Kept so the figure can show what the mean rests on. The x axis is
        # taken from the same call the plot uses, rather than reconstructed
        # from frame numbers, so the shading lands where the data is
        # whichever unit the axis ended up in.
        try:
            self._x_for_overlay = self.frame_axis(traj)[0]
        except Exception:  # an analysis with an axis of its own
            self._x_for_overlay = None

    def _withhold_the_error(self, traj: md.Trajectory, reason: str, *,
                            because: str, record: dict[str, Any] | None = None) -> None:
        """Put ``reason`` in the mean's record and take its error bar away.

        For what the analysis found that the mean cannot carry: a chain
        reaching its own periodic image, a molecule broken across the box.
        Kept in a finding of its own, the reason reached no reader: the
        report, the GUI and the Agent read why a mean is qualified from the
        mean's record, and printed the mean with its error bar. ``because``
        is a few words for the figure's legend. ``record`` is a record other
        than ``findings["mean"]``, which is made where there is none.
        """
        if record is None:
            record = self.findings.get("mean")
            if not isinstance(record, dict):
                record = {"n_frames": int(traj.n_frames)}
                self.findings["mean"] = record
        earlier = record.get("not_a_measurement")
        record["not_a_measurement"] = f"{reason} {earlier}" if earlier else reason
        record["error_withheld_because"] = because
        if "standard_error" in record:
            record["standard_error"] = float("nan")

    def _frames_used_by_all(self, n: int) -> "np.ndarray | None":
        """The frames, of the ``n`` analysed, that an analysis uses where it
        has no mean over the frames of its own: from its ``start``, at its
        ``stride``, as the record of which frames it read says
        (``findings.frames``, written by
        :func:`fastmdxplora.analysis.starting_frame.first_frame` where the
        frames are set: RMSF, ``ligand_rmsf``, ``bfactor_comparison``,
        clustering, the projections); every frame where None."""
        frames = self.findings.get("frames")
        frames = frames if isinstance(frames, dict) else {}

        def whole(value: Any) -> int | None:
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        first = whole(frames.get("first_frame"))
        if first is None:
            first = whole(getattr(self, "_first_frame", None)) or 0
        stride = whole(frames.get("stride")) or whole(getattr(self, "stride", 1)) or 1
        stride = stride if stride > 0 else 1
        if first <= 0 and stride == 1:
            return None
        return np.arange(max(first, 0), n, stride)

    def _frames_of(self, traj: md.Trajectory, record: dict[str, Any] | None
                   ) -> tuple["np.ndarray | None", str, str]:
        """The frames of the check's series that ``record`` (a mean record,
        or the analysis where None) uses, what the count is of and how they
        were found (:func:`fastmdxplora.analysis.image_check.note_on_frames`):
        those after its discard where it is a mean over the frames analysed,
        else those the analysis used (:meth:`_frames_used_by_all`)."""
        n = len(self.solute_image_series)
        if isinstance(record, dict) and record.get("n_frames") == n:
            discard = record.get("discard")
            discard = discard if isinstance(discard, int) and not isinstance(discard, bool) else 0
            return np.arange(min(max(discard, 0), n), n), "frames", ""
        return self._frames_used_by_all(n), "frames", ""

    def _union_of_means(self, traj: md.Trajectory, records: list[dict[str, Any]]
                        ) -> tuple["np.ndarray | None", str, str]:
        """The frames the means of an analysis without a main mean use: the
        same frames where they agree, else every frame any of them uses,
        said as the union."""
        n = len(self.solute_image_series)
        found = [self._frames_of(traj, record) for record in records]
        arrays = [np.arange(n) if used is None else np.asarray(used, dtype=np.int64)
                  for used, _, _ in found]
        if all(np.array_equal(array, arrays[0]) for array in arrays[1:]):
            return arrays[0], found[0][1], found[0][2]
        return (np.unique(np.concatenate(arrays)), "means' frames",
                ", the union of their frames, which differ")

    def _image_note_for(self, traj: md.Trajectory,
                        record: dict[str, Any] | None, *,
                        union: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        """The note on ``record`` (a mean record, or the analysis where None)
        of the frames it uses in which the solute met its own image
        (:meth:`_frames_of`), or, given ``union``, of the frames those means
        use (:meth:`_union_of_means`). None where there is nothing to note."""
        from fastmdxplora.analysis.image_check import note_on_frames

        series = self.solute_image_series
        if series is None:
            return None
        used, over, how = (self._union_of_means(traj, union) if union
                           else self._frames_of(traj, record))
        times = getattr(traj, "time", None)
        return note_on_frames(self.solute_image, series, used, times_ps=times,
                              analysis=self.name, counted_over=over, how=how)

    def _mean_records(self) -> list[dict[str, Any]]:
        """Every mean the findings hold: the analysis's own, each further one
        (SASA's hydrophobic and polar, the moments' shape descriptors), and
        those of a group read from another record (thermodynamics). A record
        holding only the reason it has no mean (the moments' own, of a
        molecule broken across the box) is not one."""
        found: list[dict[str, Any]] = []
        for key, value in self.findings.items():
            if key == "solute_image" or not isinstance(value, dict):
                continue
            if "n_frames" in value and ("mean" in value or "not_a_measurement" in value):
                if "mean" in value:
                    found.append(value)
                else:
                    value.pop("solute_image", None)
                continue
            for inner in value.values():
                if isinstance(inner, dict) and "mean" in inner and (
                        "units" in inner or "discard" in inner):
                    found.append(inner)
        return found

    def _qualify_by_the_solute_image(self, traj: md.Trajectory) -> None:
        """Where the solute met its own periodic image in the frames this
        analysis uses, say so: once for the analysis, in
        ``findings.solute_image`` (counted over the frames its mean uses, or
        every frame it used where it has no mean), and on each mean record,
        its numbers counted over that mean's own frames. Every error bar is
        kept: seeing its copy may bias a mean, it does not make its error
        unknown. One function for every mean
        (:func:`fastmdxplora.analysis.image_check.note_on_frames`); said
        once, however often this is called. An analysis without a main mean
        says it over the frames its means use (the union where they
        differ). The image check notes nothing of itself: its series is the
        finding."""
        self._qualified_by_the_image = True
        if self.name == "image_check":
            return
        finding = self.solute_image
        if not isinstance(finding, dict) or not finding.get("reaches_its_image"):
            return
        main = self.findings.get("mean")
        records = self._mean_records()
        if isinstance(main, dict) and "mean" in main:
            note = self._image_note_for(traj, main)
        else:
            # No main mean (the moments' shape descriptors, the secondary
            # structure's fractions, the thermodynamics): over the frames its
            # means use, never every frame.
            note = self._image_note_for(traj, None, union=records or None)
        if note:
            self.findings["solute_image"] = note
        else:
            self.findings.pop("solute_image", None)
        from fastmdxplora.analysis.image_check import note_numbers

        for record in records:
            own = self._image_note_for(traj, record)
            if own:
                record["solute_image"] = note_numbers(own)
            else:
                record.pop("solute_image", None)

    def _mark_what_the_mean_rests_on(self, ax: plt.Axes) -> None:
        """Draw the equilibrated region, its mean, and the error bar or its absence.

        The software works out where a series equilibrated, averages only
        that, and decides whether the run is long enough against its own
        correlation time for an error bar to mean anything. Every one of
        those numbers was computed and written to `options.json`, and none of
        them reached the figure -- so a reader saw a line, and had to take on
        trust both which part of it the reported mean came from and whether
        that mean carried an uncertainty at all.

        Where the software refuses an error bar, the figure says so in the
        same place it would have drawn one. A mean printed without that is
        the claim this package exists not to make.
        """
        record = self.findings.get("mean") or {}
        mean = record.get("mean")
        if mean is None or not np.isfinite(mean):
            return

        x = getattr(self, "_x_for_overlay", None)
        n_frames = int(record.get("n_frames") or 0)
        if x is None or len(x) != n_frames or n_frames == 0:
            return
        low, high = ax.get_xlim()
        if not (low - 1e-9 <= float(x[0]) and float(x[-1]) <= high + 1e-9):
            return  # the plot uses an axis of its own; do not guess

        discard = int(record.get("discard") or 0)
        if 0 < discard < n_frames:
            # Light. The excluded frames still carry the evidence that the
            # run needed that long to equilibrate, so they are marked as not
            # counted rather than hidden under a block of grey.
            share = discard / n_frames
            ax.axvspan(float(x[0]), float(x[discard]),
                       facecolor=colour("FAINT"), alpha=0.30, zorder=0,
                       linewidth=0,
                       label=f"equilibration, excluded ({share:.0%} of frames)")
            ax.axvline(float(x[discard]), color=colour("GUIDE"),
                       linestyle=":", linewidth=0.8, zorder=1)

        equilibrated_from = float(x[discard]) if discard < n_frames else float(x[0])
        error = record.get("standard_error")
        has_error = error is not None and np.isfinite(error) and error > 0
        if has_error:
            ax.fill_between([equilibrated_from, float(x[-1])],
                            mean - error, mean + error,
                            color=colour("BAND"), alpha=0.35,
                            zorder=1, linewidth=0)
        ax.plot([equilibrated_from, float(x[-1])], [mean, mean],
                color=colour("ACCENT"), linewidth=1.3,
                linestyle="--", zorder=3,
                label=self._mean_label(record, has_error))
        ax.legend(loc="best", fontsize=7.5, framealpha=0.85)
        # A legend wider than the axes runs past the frame; matplotlib does
        # not check. It only bites at a journal column width, which is
        # exactly where these labels matter.
        fit_legend(ax)

    def _mean_unit(self) -> str:
        """The unit for the legend, taken from the axis that already states it.

        `default_ylabel` declares it -- "RMSD (nm)", "Mean SASA (nm2)" -- so
        it is read from there rather than declared a second time on each of
        twenty-three analyses. Two places naming one unit is how they come to
        disagree.
        """
        try:
            label = self._user_ylabel or self.default_ylabel() or ""
        except Exception:  # noqa: BLE001 - a label depending on run state
            return ""
        inside = _unit_in(label)
        return f" {inside}" if inside else ""

    def _recorded_unit(self) -> str | None:
        """The unit written beside the mean in the findings.

        Whoever reads the findings (a caption, a table, the Agent) is told
        the unit rather than left to guess one: the Agent was handed an RMSD
        of 0.013 with nothing to say whether that was nm or Angstrom. Read
        from the analysis's own axis, never a label the person set, which
        may say anything. An empty string is a count or a fraction; ``None``
        is an analysis with no axis of its own, and records nothing.
        """
        try:
            label = self.default_ylabel()
        except Exception:  # noqa: BLE001 - a label depending on run state
            return None
        if not label:
            return None
        # "nm2" on an axis (as two analyses write it) is nm squared.
        return re.sub(r"\b(nm|m)([23])\b",
                      lambda m: m.group(1) + "\u00b2\u00b3"[int(m.group(2)) - 2],
                      _unit_in(label))

    def _mean_label(self, record: dict[str, Any], has_error: bool) -> str:
        """What the dashed line is, said in the legend where it is read.

        Two lines: the value, then what stands behind it. One line carrying
        both was long enough to overflow the axes at a journal column width,
        and it read as jargon -- the author of this package had to ask what
        "9.8 effective samples" meant, which decides whether it was clear.

        "Independent" rather than "effective": consecutive frames of a
        trajectory are not independent observations, and independence is the
        thing the number is about. "Too few for an error bar" states the
        consequence, so the absence reads as a decision rather than an
        omission.
        """
        mean = record["mean"]
        unit = self._mean_unit()
        counted = self._independent_samples(record.get("effective_samples"))
        if record.get("error_withheld_because"):
            return (f"mean after equilibration {mean:.4g}{unit}\n"
                    f"(no error bar: {record['error_withheld_because']})")
        if has_error:
            from fastmdxplora.statistics import with_its_error

            head = (f"mean after equilibration "
                    f"{with_its_error(mean, record['standard_error'])}{unit}")
            if counted is None:
                return head
            return f"{head}\n({counted})"
        if counted is None:
            return f"mean after equilibration {mean:.4g}{unit}\n(no error bar)"
        return (f"mean after equilibration {mean:.4g}{unit}\n"
                f"{counted}: too few for an error bar")

    @staticmethod
    def _independent_samples(effective: Any) -> str | None:
        """How many independent observations the mean rests on, as a count.

        The quantity is N/g -- equilibrated frames over the statistical
        inefficiency -- so arithmetically it is a ratio and comes out at
        9.77. Printing that as "9.8 independent samples" claims a precision
        it does not have: `g` is itself an estimate, and on a run this short
        it reads low, to a third of the truth at five times its length. There
        is also no such thing as eight tenths of an observation.

        So it is rounded and hedged. "About 10" is both more honest about
        the precision and easier to read than "9.8", and nothing downstream
        turns on the difference -- ten is as far below a usable count as
        nine point eight is.
        """
        if effective is None or not np.isfinite(effective):
            return None
        if effective < 1.0:
            return "fewer than one independent sample"
        rounded = int(round(float(effective)))
        noun = "sample" if rounded == 1 else "samples"
        return f"about {rounded} independent {noun}"

    def select_atoms(self, traj: md.Trajectory) -> np.ndarray:
        """Resolve :attr:`selection` to atom indices on a given trajectory.

        Returns the full atom index array when ``selection`` is ``None``.
        Raises ``ValueError`` if the selection matches zero atoms.
        """
        if self.selection is None:
            return np.arange(traj.n_atoms)
        idx = traj.topology.select(self.selection)
        if len(idx) == 0:
            raise StudyError(
                f"Atom selection {self.selection!r} matched zero atoms in "
                f"this trajectory."
            , code="analysis.selection.empty")
        return idx

    def frame_axis(self, traj: md.Trajectory) -> tuple[np.ndarray, str]:
        """Return ``(x_values, x_label)`` for a time-series plot.

        Resolution order for the unit:
          1. User-supplied ``xunit`` at construction (``"ns"``, ``"ps"``, or ``"frames"``).
          2. ``"ns"`` if the trajectory carries usable timing information
             (``traj.time`` or ``traj.timestep``).
          3. ``"frames"`` as the always-safe fallback.

        Parameters
        ----------
        traj : mdtraj.Trajectory

        Returns
        -------
        x : np.ndarray, shape (n_frames,)
            The numerical values for the x axis.
        label : str
            A pre-formatted axis label, e.g. ``"Time (ns)"`` or ``"Frame"``.

        Notes
        -----
        MDTraj stores ``traj.time`` in picoseconds. When the trajectory
        lacks timing (e.g., loaded from a PDB without a timestep), the
        method falls back to frame indices.
        """
        unit = self._user_xunit
        if unit is not None:
            unit = unit.lower()
            if unit not in {"ns", "ps", "frames"}:
                raise StudyError(
                    f"xunit must be one of 'ns', 'ps', or 'frames'; got {unit!r}"
                , code="analysis.option.not_permitted")

        # If user didn't specify, prefer ns when timing data is available.
        #
        # "Available" has to mean more than "varies". A DCD read through
        # MDTraj comes back with time equal to the frame index in
        # picoseconds, which varies perfectly and passed this test for
        # months -- so every time-series figure from a 100 ns run drew a
        # 2 ns axis. `loading._with_a_real_clock` now either sets the run's
        # own interval or fills time with NaN to say there isn't one, and
        # NaN is why the finite check comes first: np.allclose against NaN
        # is False, so a NaN clock would otherwise read as usable.
        time_ps = np.asarray(traj.time, dtype=float)
        has_real_time = (
            time_ps is not None
            and len(time_ps) > 1
            and bool(np.all(np.isfinite(time_ps)))
            and not np.allclose(time_ps, time_ps[0])
        )

        if unit is None:
            unit = "ns" if has_real_time else "frames"

        if unit == "frames" or not has_real_time:
            return np.arange(traj.n_frames), "Frame"
        if unit == "ps":
            return time_ps, "Time (ps)"
        # ns
        return time_ps / 1000.0, "Time (ns)"

    def _draw_one(self, path: Path) -> Path:
        """One figure, in whichever mode is in force, written to ``path``."""
        fig, ax = new_figure(title=self.figure_title(), figsize=self._user_figsize)
        self.plot(self.result, ax)

        # Apply user/default label overrides AFTER the subclass plot() runs
        # so users can replace anything the analysis set internally.
        xlabel = self._user_xlabel if self._user_xlabel is not None else self.default_xlabel()
        ylabel = self._user_ylabel if self._user_ylabel is not None else self.default_ylabel()
        if xlabel is not None:
            ax.set_xlabel(xlabel)
        if ylabel is not None:
            ax.set_ylabel(ylabel)

        # After the labels, so the unit the axis ended up in is available,
        # after plot(), so an analysis that draws its own legend keeps it.
        if self.time_series:
            try:
                self._mark_what_the_mean_rests_on(ax)
            except Exception:  # noqa: BLE001 - a figure beats no figure
                logger.debug("could not mark the equilibrated mean on %s",
                             self.name, exc_info=True)

        return save_figure(fig, path)

    def _do_plot(self) -> list[Path]:
        """Every figure this analysis was asked for, primary first.

        ``figure_colours`` decides how many. The primary is always
        ``<name>.png`` whichever mode it is drawn in, so the report, the
        dashboard and every existing reader keep finding it where they look;
        asking for both adds ``<name>_greyscale.png`` beside it rather than
        renaming anything.

        Drawing it twice costs one extra render and settles a question that
        otherwise has to be answered before the journal has been chosen.
        """
        primary = self.output_dir / f"{self.name}.png"
        if self.figure_colours == "both":
            wanted = [("colour", primary),
                      ("greyscale",
                       self.output_dir / f"{self.name}_greyscale.png")]
        else:
            wanted = [(self.figure_colours, primary)]

        written: list[Path] = []
        with sized_for(self.figure_width):
            for mode, path in wanted:
                with drawn_in(mode):
                    written.append(self._draw_one(path))
        return written

    def _write_options_manifest(self) -> Path:
        """Record what this analysis was asked to do, and what it found out.

        The two are kept apart. ``options`` is what somebody set, and a report
        listing them is a record of the run. ``findings`` is what the analysis
        worked out along the way -- how confidently it knew the ligand's
        chemistry, which binding modes it saw, whether a rate was supportable.
        Both belong in the manifest and only the first belongs in a document:
        putting the findings in ``options`` filled two pages of a report with
        raw tuples of atom indices.
        """
        manifest = {
            "analysis": self.name,
            "class": type(self).__name__,
            "selection": self.selection,
            "options": self.options,
            "findings": self.findings,
        }
        data_format = getattr(self, "_data_format", None)
        if data_format:
            manifest["data_format"] = data_format
        path = self.output_dir / "options.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        return path


#: Frames whose bonds are measured at once when looking for a molecule
#: split across the box: every frame is looked at, in pieces this size.
FRAMES_PER_SPLIT_CHECK = 100

#: Set on a trajectory whose molecules could not be made whole, by the
#: loader or here, so the attempt is not repeated for every analysis.
IMAGING_FAILED = "_fastmdxplora_imaging_failed"

#: Set by the loader on a trajectory it has measured and found whole, so an
#: analysis does not measure every bond of every frame again: 5 ms a frame
#: for 108,000 atoms, in every analysis of a study. It holds a fingerprint
#: of the coordinates and cell measured (:func:`_fingerprint`), so
#: coordinates changed since, wrapped into the cell by a caller, say, are
#: measured again rather than trusted.
WHOLE_CHECKED = "_fastmdxplora_whole_checked"


def _fingerprint(traj) -> tuple:
    """The shape and two sums of the coordinates and of the cell: 22 ms for
    100 frames of 108,000 atoms, against 580 ms to measure every bond."""
    xyz = np.asarray(traj.xyz)
    cell = getattr(traj, "unitcell_vectors", None)
    cell = np.zeros(0) if cell is None else np.asarray(cell, dtype=np.float64)
    return (xyz.shape, float(xyz.sum(dtype=np.float64)),
            float(np.square(xyz, dtype=np.float64).sum()), float(cell.sum()))


def _mark(traj, name: str, value: Any = True) -> None:
    try:
        setattr(traj, name, value)
    except AttributeError:  # pragma: no cover - a stand-in with slots
        pass


def _checked_whole(traj) -> bool:
    mark = getattr(traj, WHOLE_CHECKED, None)
    return isinstance(mark, tuple) and mark == _fingerprint(traj)


def carry_wholeness(source, copy):
    """``copy`` with what is known of ``source``'s wholeness: an MDTraj
    slice is a new object and keeps none of it."""
    if getattr(source, IMAGING_FAILED, False):
        _mark(copy, IMAGING_FAILED)
    mark = getattr(source, WHOLE_CHECKED, None)
    if mark is not None:
        _mark(copy, WHOLE_CHECKED, mark)
    return copy


def _bonds_and_widths(traj):
    widths = narrowest_width(traj)
    if widths is None or traj.topology.n_bonds == 0:
        return None, None
    bonds = np.array([[a.index, b.index] for a, b in traj.topology.bonds],
                     dtype=np.int64).reshape(-1, 2)
    return bonds, widths


def mark_wholeness(traj) -> None:
    """Measure once, after loading, whether every molecule is whole, and
    mark the trajectory either way (:data:`WHOLE_CHECKED`,
    :data:`IMAGING_FAILED`)."""
    if getattr(traj, "unitcell_vectors", None) is None:
        return
    bonds, widths = _bonds_and_widths(traj)
    if bonds is None:
        return
    if _split_frames(traj, bonds, widths).any():
        _mark(traj, IMAGING_FAILED)
    else:
        _mark(traj, WHOLE_CHECKED, _fingerprint(traj))


def _split_bonds(traj, bonds: np.ndarray, widths: np.ndarray) -> np.ndarray:
    """How many bonds, in each frame, are longer than half the cell's
    narrowest width: a molecule split across a face."""
    split = np.zeros(traj.n_frames, dtype=np.int64)
    for first in range(0, traj.n_frames, FRAMES_PER_SPLIT_CHECK):
        xyz = traj.xyz[first:first + FRAMES_PER_SPLIT_CHECK]
        lengths = np.linalg.norm(xyz[:, bonds[:, 0]] - xyz[:, bonds[:, 1]], axis=2)
        split[first:first + len(xyz)] = np.count_nonzero(
            lengths > 0.5 * widths[first:first + len(xyz), None], axis=1)
    return split


def _split_frames(traj, bonds: np.ndarray, widths: np.ndarray) -> np.ndarray:
    """Whether, in each frame, a molecule is split across a face."""
    return _split_bonds(traj, bonds, widths) > 0


def _whole_if_broken(traj) -> tuple[Any, str | None]:
    """The trajectory with its molecules whole, and a sentence if it had to
    be made so.

    The loader makes molecules whole, and every analysis that reads raw
    coordinates (RMSD, RMSF, radius of gyration, the moments, clustering)
    relies on it. One handed a trajectory straight from ``md.load``, as the
    docstrings' examples do, was not checked: a rigid body wrapped atom by
    atom in a dodecahedron read an RMSD of 2.7 nm, a ligand RMSF of 1.4 nm,
    for an exact answer of 0. A bond longer than half the cell's narrowest
    width in any frame (every frame is measured: a molecule straddling a
    face in 16 of 200 frames read a radius of gyration of 2.88 nm against
    0.91) is a molecule split across a face, and the loader's imaging is
    then applied, to a copy. Where it fails, the trajectory is left as given
    and marked, so the next analysis does not try again; the mark skips only
    the attempt, not the measurement, so a trajectory whose imaging failed
    but which is whole (pure water the loader found no anchor for, or one a
    caller has since made whole) is not said to be split.
    """
    if getattr(traj, "unitcell_vectors", None) is None or traj.topology.n_bonds == 0:
        return traj, None
    if _checked_whole(traj):
        return traj, None
    bonds, widths = _bonds_and_widths(traj)
    if bonds is None:
        return traj, None
    split = int(_split_bonds(traj, bonds, widths).sum())
    if not split:
        return traj, None
    if getattr(traj, IMAGING_FAILED, False):
        return traj, _NOT_MADE_WHOLE
    from fastmdxplora.analysis.loading import _made_whole

    # On a copy: the caller's trajectory is theirs, and imaging is in place.
    whole = _made_whole(traj.slice(np.arange(traj.n_frames), copy=True))
    left = int(_split_bonds(whole, bonds, widths).sum())
    if left:
        _mark(traj, IMAGING_FAILED)
        # What could be made whole is, as the loader leaves it: a molecule
        # running through the boundary does not keep a ligand in pieces.
        return (whole if left < split else traj), _NOT_MADE_WHOLE
    return whole, (
        "The trajectory was given with molecules split across the periodic "
        "boundary, and they were made whole before this analysis, as the "
        "loader makes them.")


_NOT_MADE_WHOLE = (
    "Molecules are split across the periodic boundary and could not all be "
    "made whole (a topology the imaging cannot anchor, or a molecule running "
    "through the boundary): an analysis reading raw coordinates (RMSD, RMSF, "
    "radius of gyration, shape) may describe the pieces rather than the "
    "molecule.")


def narrowest_width(traj) -> "np.ndarray | None":
    """The cell's narrowest width in each frame, in nm, or None without a cell.

    The perpendicular distance between the closest pair of opposite faces,
    V / |b x c| and its two companions. A separation under half of it is
    found exactly by the minimum-image convention in a cell of any shape,
    however the images are searched, so a check against it never passes a
    separation the convention folded. It is conservative, not exact: in a
    reduced cell whose images are searched, as MDTraj searches them, a
    separation is found exactly up to half the shortest lattice vector (in
    the rhombic dodecahedron setup builds by default, half a box length
    against 0.354 of one), so a check against the narrowest width flags some
    separations that were in fact found exactly.
    """
    vectors = getattr(traj, "unitcell_vectors", None)
    if vectors is None:
        return None
    box = np.asarray(vectors, dtype=np.float64)
    a, b, c = box[:, 0], box[:, 1], box[:, 2]
    volume = np.abs(np.einsum("fi,fi->f", a, np.cross(b, c)))
    faces = np.stack([np.linalg.norm(np.cross(b, c), axis=1),
                      np.linalg.norm(np.cross(c, a), axis=1),
                      np.linalg.norm(np.cross(a, b), axis=1)], axis=1)
    return volume / faces.max(axis=1)


def superposed(traj, *, frame=0, atom_indices=None, reference=None, ref_atom_indices=None):
    """Align a copy, and drop the box that no longer describes it.

    Two hazards, both silent, both met on the same run.

    mdtraj's ``superpose`` rotates coordinates **in place** and returns the
    same object. An analysis that aligned therefore left every later
    analysis in the same run reading rotated coordinates, so a measure's
    result depended on which other measures had run before it. Nothing in
    the record could show this: every setting was identical either way.

    And rotation does not rotate ``unitcell_vectors``. Minimum-image
    distances computed afterwards map atoms through a box that no longer
    corresponds to the frame, which does not fail -- it answers, wrongly.
    On a 20 ns trypsin-benzamidine run the same ligand-protein pair
    measured 1.64 nm before alignment and 1.83 nm after, and
    ``pl_interactions`` reported 252 hydrophobic contacts in company
    against 10 alone.

    So: align a copy, and remove the box. An analysis that wants periodic
    distances must take them from the unaligned trajectory, where the box
    is still true. Absent is better than stale, because stale is the one
    a caller cannot detect.

    ``reference`` is the trajectory whose ``frame`` the copy is fitted to,
    itself by default, with ``ref_atom_indices`` its atoms matched to
    ``atom_indices`` (the Viewer fits frames to the structure a study
    started from, or to the deposited one, the same way).
    """
    aligned = traj[:]
    aligned.superpose(traj if reference is None else reference, frame=frame,
                      atom_indices=atom_indices, ref_atom_indices=ref_atom_indices)
    aligned.unitcell_vectors = None
    return aligned
