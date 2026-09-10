"""A free energy surface from a metadynamics run, or a refusal saying why not.

Metadynamics wrote its hills and its trajectory of the collective variable and
stopped there. Nothing read them back, so the run produced PLUMED's output
files and no result: the module said "a run that has not converged has no free
energy" while offering no free energy either way, and somebody wanting a
surface ran ``plumed sum_hills`` themselves and got one with nothing attached.

Summing the hills is arithmetic. The part worth having is what comes with it.
A metadynamics surface is only a measurement if the bias has stopped growing
and the system has crossed the barriers more than once, and both of those are
answerable from the files the run already writes.

**The bias must have flattened.** In well-tempered metadynamics the deposited
hills shrink as a basin fills, and the height of the last hills is the direct
evidence. Hills still arriving at nearly their initial height mean the surface
is still being built, and reporting it is reporting a snapshot of a filling
process as though it were the shape of the landscape.

**The system must have come back.** A barrier crossed once has been observed
once. The height of that barrier then rests on a single event, and no amount
of sampling either side of it adds a second observation. Recrossings are what
make a barrier a measurement rather than an anecdote.

**And the surface must have stopped moving.** Built from three quarters of the
hills and from all of them, a converged surface gives the same answer. This is
the check the other two are proxies for, and the one that catches a run that
satisfies them both for the wrong reasons.

That last check returns one number for the whole surface -- the largest
movement anywhere it is judged -- which decides the verdict and says nothing
about where the surface is solid. A run can be converged to a tenth of a kJ/mol
across both its wells and moving by two at the shoulder of a barrier, and the
single figure reports the shoulder. `convergence_band` is the same measurement
made point by point, from several cumulative cuts through the later deposition
rather than one, so a plotted surface can carry the width of its own drift.

**It is a convergence indicator and not a standard error, and this module says
so wherever it reports one.** The cuts are nested -- each contains the hills of
the one before -- and they all come from a single trajectory, so they are not
independent samples of anything. What they measure is whether the bias has
stopped moving; what they cannot see is how much the surface would differ in a
second run from a different seed, which is the quantity a `±` in a paper is
read as. A run that never left one basin gives a narrow band for exactly that
reason. Independent replicas are the honest route to the second number, and the
calibration experiment exists to establish what the first is worth.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

__all__ = [
    "Hills",
    "read_hills",
    "surface_from_hills",
    "surface_from_hills_nd",
    "cumulative_surfaces",
    "cumulative_surfaces_nd",
    "convergence_band",
    "marginal_profile",
    "recrossings",
    "compute_surface",
    "compute_surface_2d",
]

#: Boltzmann's constant in kJ/(mol K), for turning a surface back into the
#: populations it came from when a dimension is integrated out.
KB_KJMOL = 0.008314462618

#: Below this, a barrier has been seen too few times to be a measurement
#: rather than an anecdote. A judgement, like the overlap an umbrella study
#: demands, so a study can set its own.
MINIMUM_RECROSSINGS = 4

#: The last hills, as a fraction of the first, below which the bias is taken to
#: have flattened. Well-tempered deposition decays as exp(-V/kΔT), so a tenth
#: is already deep into the tail; more than that and the basin is still filling.
SETTLED_HEIGHT_FRACTION = 0.1

#: How much the surface may still move, in kJ/mol, between three quarters of
#: the hills and all of them. Roughly thermal energy at room temperature: a
#: feature that moves by less than kT is not a feature that has moved.
SETTLED_DRIFT_KJMOL = 2.5

#: How far above its minimum a surface is still judged for drift. Beyond
#: this the free energy is estimated from too few visits to be stable, and
#: including it means reporting the reliability of the least-sampled point on
#: the grid rather than of the surface anyone reads. Twenty kJ/mol is about
#: eight times RT at 300 K: a state that rare is not one a simulation is
#: measuring, and the barrier heights above it are read off the shape rather
#: than trusted point by point.
DRIFT_CEILING_KJMOL = 20.0

#: How far a coordinate must travel, in hill widths, before the run counts
#: as having explored it. A bias built from Gaussians of width sigma cannot
#: resolve anything narrower than sigma, so a variable that moved only a few
#: hill widths has been biased across a region no wider than the bias
#: itself, and the surface along it is the shape of the deposition rather
#: than of the landscape. This catches the case a drift check cannot: a
#: narrow well is perfectly reproducible between three quarters of the
#: hills and all of them, so it looks converged precisely because nothing
#: happened.
MINIMUM_EXPLORED_HILL_WIDTHS = 6.0

#: Where the convergence band starts looking, as a fraction of the
#: deposition. The first half of a well-tempered run is the bias filling the
#: landscape: including it measures the filling, which is large and already
#: known to be large, rather than what is left of it once the basins are
#: full. Half is also what makes the band comparable between runs of
#: different lengths -- an absolute cut in nanoseconds would mean a different
#: fraction of every run.
CONVERGENCE_FROM_FRACTION = 0.5

#: How many cumulative cuts the band is taken over. Five is the fewest that
#: gives a spread rather than a difference: with two cuts the standard
#: deviation is the gap between them over root two, which is the existing
#: single-number drift check wearing a different name, and with three the
#: estimate is itself so noisy that the band's own scatter across the grid
#: is read as structure in the surface.
CONVERGENCE_BLOCKS = 5


@dataclass(frozen=True)
class Hills:
    """What a metadynamics run deposited, read back from its HILLS file."""

    time_ps: np.ndarray
    centre: np.ndarray
    sigma: np.ndarray
    height: np.ndarray
    #: (T + ΔT) / T. One means the run was not well-tempered, and the
    #: relationship between bias and free energy is a different one.
    bias_factor: float

    def __len__(self) -> int:
        return int(self.centre.shape[0])

    @property
    def n_dims(self) -> int:
        """How many collective variables were biased."""
        return 1 if self.centre.ndim == 1 else int(self.centre.shape[1])

    def column(self, dim: int) -> np.ndarray:
        """The centres along one variable, whatever the dimensionality."""
        return self.centre if self.centre.ndim == 1 else self.centre[:, dim]

    def sigma_column(self, dim: int) -> np.ndarray:
        return self.sigma if self.sigma.ndim == 1 else self.sigma[:, dim]


def read_hills(path: str | Path) -> Hills:
    """Read a PLUMED HILLS file, in as many variables as it holds.

    PLUMED writes a ``#! FIELDS`` header naming every column, and that
    header is what makes the file readable for more than one variable: the
    layout is time, then one centre per variable, then one sigma per
    variable, then height and the bias factor, so a two-variable file has
    the height in column six where a one-variable file has the bias factor.
    Reading by position, which is what this did, silently takes a sigma for
    a height on any run that biases two things.

    The sigma columns are found by name, so the count of variables comes
    from the file rather than from an assumption about it. A file with no
    header is read by position as one variable, which is what such a file
    used to be.
    """
    fields: list[str] = []
    rows: list[list[float]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and "FIELDS" in stripped:
            after = stripped.split("FIELDS", 1)[1].split()
            fields = [name for name in after]
            continue
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 4:
            continue
        rows.append([float(value) for value in parts])

    if not rows:
        raise ValueError(
            f"{path} holds no hills. A metadynamics run that deposited "
            "nothing has not biased anything, and there is no surface to "
            "build from it."
        )

    width = min(len(row) for row in rows)
    table = np.array([row[:width] for row in rows], dtype=float)

    sigma_at = [i for i, name in enumerate(fields[:width])
                if name.startswith("sigma")]
    if not sigma_at:
        # No header, or none this recognises: the historical layout.
        factor = float(table[0, 4]) if width > 4 else 1.0
        return Hills(
            time_ps=table[:, 0],
            centre=table[:, 1],
            sigma=table[:, 2],
            height=table[:, 3],
            bias_factor=factor,
        )

    n_dims = len(sigma_at)
    first_sigma = sigma_at[0]
    centre_at = list(range(1, first_sigma))
    if len(centre_at) != n_dims:
        raise ValueError(
            f"{path} names {len(centre_at)} value columns and {n_dims} "
            "sigma columns, which is not a layout this can read. PLUMED "
            "writes one sigma per biased variable."
        )
    height_at = first_sigma + n_dims
    factor_at = height_at + 1
    factor = (float(table[0, factor_at])
              if width > factor_at else 1.0)

    def squeeze(columns: list[int]) -> np.ndarray:
        block = table[:, columns]
        return block[:, 0] if len(columns) == 1 else block

    return Hills(
        time_ps=table[:, 0],
        centre=squeeze(centre_at),
        sigma=squeeze(sigma_at),
        height=table[:, height_at],
        bias_factor=factor,
    )


def periodic_dimensions(
    script_path: str | Path,
    n_dims: int,
) -> tuple[bool, ...]:
    """Which biased variables are circular, from PLUMED's own definitions."""
    import re

    path = Path(script_path)
    text = (path.read_text(encoding="utf-8").upper()
            if path.is_file() else "")
    # A torsion wraps and a bond angle does not: PLUMED's ANGLE has domain
    # [0, pi]. Calling it circular put the surface grid on [-pi, pi], so half
    # of every angle surface was ground the coordinate cannot occupy, the
    # barrier was a maximum taken across that half, and `basins` treated
    # -pi and +pi as neighbours. The umbrella side declared the same pair
    # and is corrected with it.
    circular = ("TORSION",)
    if n_dims == 1:
        return (any(
            f"{action}\n" in text or f"{action} " in text
            for action in circular),)

    actions = dict(re.findall(
        r"^\s*(CV\d+)\s*:\s*(\w+)", text, re.MULTILINE))
    return tuple(
        actions.get(f"CV{i + 1}", "") in circular
        for i in range(n_dims))


def bias_from_hills_nd(
    hills: Hills,
    points: np.ndarray,
    *,
    heights: "np.ndarray | None" = None,
    start: int = 0,
    upto: "int | None" = None,
    periodic: "tuple[bool, ...] | None" = None,
    chunk: int = 512,
) -> np.ndarray:
    """Gaussian bias at N-dimensional points, with per-axis periodicity."""
    coordinates = np.asarray(points, dtype=float)
    if coordinates.ndim != 2 or coordinates.shape[1] != hills.n_dims:
        raise ValueError(
            f"The hills hold {hills.n_dims} variables and bias points have "
            f"shape {coordinates.shape}; expected one column per variable."
        )

    first = max(0, int(start))
    stop = len(hills) if upto is None else min(len(hills), int(upto))
    if stop <= first:
        return np.zeros(coordinates.shape[0], dtype=float)

    wrap = tuple(periodic or (False,) * hills.n_dims)
    if len(wrap) != hills.n_dims:
        raise ValueError(
            f"The hills hold {hills.n_dims} variables but periodicity has "
            f"{len(wrap)} entries."
        )

    centres = np.asarray(hills.centre, dtype=float)
    sigmas = np.asarray(hills.sigma, dtype=float)
    if centres.ndim == 1:
        centres = centres[:, None]
        sigmas = sigmas[:, None]
    amplitudes = (np.asarray(hills.height, dtype=float)
                  if heights is None else np.asarray(heights, dtype=float))

    bias = np.zeros(coordinates.shape[0], dtype=float)
    for block_start in range(first, stop, chunk):
        block = slice(block_start, min(block_start + chunk, stop))
        exponent = np.zeros((coordinates.shape[0], centres[block].shape[0]))
        for dim in range(hills.n_dims):
            delta = (coordinates[:, dim][:, None]
                     - centres[block][:, dim][None, :])
            if wrap[dim]:
                delta = (delta + np.pi) % (2.0 * np.pi) - np.pi
            exponent += (
                delta ** 2 / (2.0 * sigmas[block][:, dim][None, :] ** 2))
        bias += (amplitudes[block][None, :] * np.exp(-exponent)).sum(axis=1)
    return bias


def surface_from_hills(
    hills: Hills, grid: np.ndarray, *, upto: int | None = None,
    periodic: bool = False,
) -> np.ndarray:
    """The free energy on ``grid``, from the first ``upto`` hills.

    The bias is the sum of the deposited Gaussians. For a well-tempered run
    the bias approaches -(1 - 1/γ) F, so the free energy is the bias scaled by
    γ/(γ - 1) and negated. Without tempering the bias approaches -F directly,
    and the scaling would divide by zero, so that case is its own.

    Shifted so the lowest point is zero, because only differences mean
    anything: the absolute value of a free energy from metadynamics is set by
    where the sum happened to start.
    """
    stop = len(hills) if upto is None else int(upto)
    centres = hills.centre[:stop, None]
    sigmas = hills.sigma[:stop, None]
    heights = hills.height[:stop, None]

    # On a circle the separation is measured the short way round. A hill
    # deposited at +170 degrees is 15 degrees from a grid point at -175, not
    # 345: computed straight, it contributes nothing there, and the surface
    # grows an artificial wall exactly where the coordinate is continuous.
    separation = grid[None, :] - centres
    if periodic:
        separation = np.remainder(separation + np.pi, 2.0 * np.pi) - np.pi

    bias = np.sum(
        heights * np.exp(-(separation ** 2) / (2.0 * sigmas ** 2)),
        axis=0,
    )

    # No rescaling, for either tempering. PLUMED multiplies the height it
    # deposits by y/(y-1) *before writing HILLS*, precisely so that summing
    # the file gives the free energy directly -- which is what `plumed
    # sum_hills` does, and why its --negbias flag exists to divide the factor
    # back out. `reweighted_averages.deposited_heights` states this
    # convention and undoes it to recover the bias; applying it again here
    # squared the factor and made every well-tempered surface too large by
    # y/(y-1): 11.1% at a bias factor of 10, 100% at 2.
    #
    # An untempered run stores what it deposited and the sum converges on
    # the negative of the free energy, so the same expression serves both.
    surface = -bias
    return surface - float(np.min(surface))


def _convergence_cuts(
    n_hills: int,
    *,
    blocks: int = CONVERGENCE_BLOCKS,
    from_fraction: float = CONVERGENCE_FROM_FRACTION,
) -> list[int]:
    """Hill counts at which to cut, spanning the later deposition.

    Returned as counts rather than fractions because the cuts have to be
    distinct *hills*: on a run that deposited nine of them, five evenly
    spaced fractions land on four separate counts and one repeat, and
    keeping the repeat would put a surface into the spread twice and narrow
    the band by counting it as agreement with itself.
    """
    n = int(n_hills)
    if n < 2:
        return []
    first = max(1, int(round(float(from_fraction) * n)))
    wanted = max(2, int(blocks))
    cuts = sorted({
        int(round(value))
        for value in np.linspace(first, n, wanted)
        if 1 <= round(value) <= n
    })
    return cuts


def cumulative_surfaces(
    hills: Hills,
    grid: np.ndarray,
    *,
    blocks: int = CONVERGENCE_BLOCKS,
    from_fraction: float = CONVERGENCE_FROM_FRACTION,
    periodic: bool = False,
) -> "tuple[list[int], list[np.ndarray]]":
    """The surface at several points through the later deposition.

    One pass over the hills, not one per cut. The bias is a sum over
    Gaussians, so the surface at each cut is the previous one plus the hills
    deposited between: calling `surface_from_hills` once per cut recomputes
    the whole history every time and returns the identical numbers for
    `blocks` times the work, which on a run with forty thousand hills is
    minutes rather than seconds.

    Each surface is reported against its own minimum, as `surface_from_hills`
    does; `convergence_band` re-aligns them before comparing, because a
    minimum that moves between cuts is a shift and not a disagreement.
    """
    coordinates = np.asarray(grid, dtype=float)
    cuts = _convergence_cuts(
        len(hills), blocks=blocks, from_fraction=from_fraction)
    if not cuts:
        return [], []

    bias = np.zeros(coordinates.shape[0], dtype=float)
    surfaces: list[np.ndarray] = []
    start = 0
    for stop in cuts:
        centres = hills.centre[start:stop, None]
        sigmas = hills.sigma[start:stop, None]
        heights = hills.height[start:stop, None]
        separation = coordinates[None, :] - centres
        if periodic:
            separation = (
                np.remainder(separation + np.pi, 2.0 * np.pi) - np.pi)
        bias = bias + np.sum(
            heights * np.exp(-(separation ** 2) / (2.0 * sigmas ** 2)),
            axis=0,
        )
        surface = -bias
        surfaces.append(surface - float(np.min(surface)))
        start = stop
    return cuts, surfaces


def convergence_band(
    surfaces: "Sequence[np.ndarray]",
    *,
    judged: "np.ndarray | None" = None,
    hills_at: "Sequence[int] | None" = None,
    from_fraction: float = CONVERGENCE_FROM_FRACTION,
) -> dict[str, Any]:
    """How much the surface still moved over the later part of its deposition.

    **This is a convergence indicator. It is not a standard error, and the
    dictionary it returns says so in a field and again in a sentence**, because
    a number in kJ/mol sitting beside a free energy will be read as one
    otherwise. The distinction is not pedantry: the two differ by an unknown
    factor and in a known direction.

    The cuts are nested, each holding the hills of the one before, so
    consecutive surfaces agree partly because they are largely the same sum.
    And every cut comes from one trajectory: whatever that trajectory did not
    visit is missing from all of them alike, and the spread across them cannot
    report a basin nobody entered. Both effects push the same way, so the band
    is a lower bound on run-to-run spread -- narrowest, unhelpfully, exactly
    where the sampling was worst.

    What it does answer is the question the single drift number answers, asked
    point by point: has the bias stopped moving *here*. That is worth having on
    a plotted surface, where the wells and the barrier tops have very different
    claims to be believed and the one summary figure is always the barrier's.

    Surfaces are aligned before comparison, because a free energy has no
    absolute zero and a constant offset between two cuts is not a
    disagreement. The offset is the **median** difference from the last cut,
    over the judged region, and the choice of median matters more than it
    looks:

    * On the minimum, which is how the surfaces arrive, every point is tied to
      the single lowest grid point. A minimum that hops one bin between cuts
      then moves the whole surface, and the band reports a shift as though the
      landscape had changed everywhere.
    * On the mean, a change confined to one region is shared out over the whole
      grid in proportion to its size. A right-hand third that deepens by 3
      kJ/mol lifts the converged two thirds by 1, so the wells acquire a band
      they did not earn and the ratio between the moving part and the quiet
      part is flattened towards one.
    * On the median, a region that did not move sets the reference as long as
      it is more than half the judged grid, which is what "most of the surface
      has converged" means. Where it is not -- a run whose surface is moving
      nearly everywhere -- the reference lands in the moving part and the band
      understates, which is the same direction as every other limitation here
      and is reported as a small band on a run whose drift check has already
      failed.
    """
    stack = np.asarray(surfaces, dtype=float)
    unavailable = {
        "band_kjmol": None,
        "typical_kjmol": None,
        "largest_kjmol": None,
        "blocks": int(stack.shape[0]) if stack.ndim >= 1 else 0,
        "hills_at": ([int(x) for x in hills_at] if hills_at else []),
        "from_fraction": float(from_fraction),
        "is_a_standard_error": False,
        "note": (
            "Too few hills to cut the deposition into separate pieces, so "
            "there is no spread to report. This says nothing about whether "
            "the run converged; it says the run is too short for this "
            "particular measurement to be made on it."
        ),
    }
    if stack.ndim < 2 or stack.shape[0] < 2:
        return unavailable

    flat = stack.reshape(stack.shape[0], -1)
    where = (np.ones(flat.shape[1], dtype=bool) if judged is None
             else np.asarray(judged, dtype=bool).ravel())
    if where.size != flat.shape[1] or not where.any():
        where = np.ones(flat.shape[1], dtype=bool)

    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        # Against the last cut, which is the surface the run actually
        # reports; the others are asked how far they are from it.
        reference = flat[-1]
        offsets = np.nanmedian(
            (flat - reference[None, :])[:, where], axis=1)
        aligned = flat - offsets[:, None]
        # ddof=1: the cuts are a sample of the surface's late history, not
        # the whole of it. With five of them the difference from ddof=0 is
        # 12%, which is larger than several of the drifts being judged.
        band = np.nanstd(aligned, axis=0, ddof=1)
        typical = float(np.nanmedian(band[where]))
        largest = float(np.nanmax(band[where]))

    at = [int(x) for x in hills_at] if hills_at else []
    span = (f"the last {100.0 * (1.0 - float(from_fraction)):.0f}% of the "
            f"deposition")
    return {
        "band_kjmol": [float(x) for x in band],
        "typical_kjmol": typical,
        "largest_kjmol": largest,
        "blocks": int(flat.shape[0]),
        "hills_at": at,
        "from_fraction": float(from_fraction),
        # Repeated as a field so a script reading the manifest can refuse to
        # print it as a plus-or-minus without parsing English.
        "is_a_standard_error": False,
        "note": (
            f"A convergence indicator, not a standard error: the spread of "
            f"the surface across {int(flat.shape[0])} cumulative cuts through "
            f"{span}, aligned on the region judged. The cuts are nested and "
            "share a single trajectory, so they cannot see how far this "
            "surface would move in an independent run, and a coordinate that "
            "stayed in one basin gives a narrow band for that reason. Read it "
            "as whether the bias has converged at each point. Independent "
            "replicas are the statistical error; the calibration experiment "
            "is what relates the two."
        ),
    }


def basins(coordinate: np.ndarray, surface: np.ndarray,
           periodic: bool = False) -> tuple[float, float] | None:
    """The two deepest minima, which are the states a crossing goes between.

    Returns ``None`` where there is only one, because a coordinate with a
    single basin has nothing to cross to and a count of crossings would be a
    count of nothing.
    """
    finite = np.isfinite(surface)
    if finite.sum() < 3:
        return None
    where, values = coordinate[finite], surface[finite]

    if periodic and values.size > 2 and abs(
            (where[-1] - where[0]) - 2.0 * np.pi) < 1e-6:
        # A grid spanning -pi to pi inclusive names the same place twice, so
        # the wrap-around neighbour of the first point is the second to last,
        # not the last. Dropping the duplicate is simpler than special-casing
        # every comparison that follows.
        where, values = where[:-1], values[:-1]

    interior = [
        i for i in range(1, values.size - 1)
        if values[i] < values[i - 1] and values[i] < values[i + 1]
    ]
    if periodic and values.size > 2:
        # The ends are neighbours, so a minimum can sit across the join.
        if values[0] < values[1] and values[0] < values[-1]:
            interior.append(0)
        if values[-1] < values[-2] and values[-1] < values[0]:
            interior.append(values.size - 1)
    if len(interior) < 2:
        return None

    interior.sort(key=lambda i: values[i])
    return float(where[interior[0]]), float(where[interior[1]])


def transitions(values: np.ndarray, *, between: tuple[float, float],
                periodic: bool = False) -> int:
    """How many times the variable travelled from one basin to the other.

    Counted by which minimum each frame is nearer to, measured the short way
    round where the coordinate is a circle, with a frame counted as arrived
    only once it is within a quarter of the separation. A run rattling
    between them does not accumulate crossings it did not make.

    The band used to be placed a quarter of the way in from the extremes of
    the hill range, which is a statement about the grid rather than about the
    system. On a full turn that put the thresholds at -90 and +90 degrees,
    and a real study whose minima sat at -17 and 155 had one of them inside
    the dead band: the count was of excursions past an arbitrary line.
    """
    if values.size == 0:
        return 0

    def separation(a, b):
        d = np.asarray(a, dtype=float) - b
        if periodic:
            d = np.remainder(d + np.pi, 2.0 * np.pi) - np.pi
        return np.abs(d)

    first, second = between
    apart = float(separation(first, second))
    if apart <= 0:
        return 0
    close_enough = 0.25 * apart

    to_first = separation(values, first)
    to_second = separation(values, second)

    crossings = 0
    side: str | None = None
    for near_first, near_second in zip(to_first, to_second):
        if near_first <= close_enough:
            here = "first"
        elif near_second <= close_enough:
            here = "second"
        else:
            continue
        if side is not None and here != side:
            crossings += 1
        side = here
    return crossings


def surface_from_hills_nd(
    hills: Hills,
    axes: "tuple[np.ndarray, ...]",
    *,
    upto: int | None = None,
    periodic: "tuple[bool, ...] | None" = None,
    chunk: int = 512,
) -> np.ndarray:
    """The free energy on a grid of two or more variables.

    The same arithmetic as the one-dimensional case, with the Gaussian a
    product over dimensions. Summed in chunks of hills because the direct
    form allocates one number per hill per grid point: a 200 by 200 grid
    and ten thousand hills is four hundred million floats, which is several
    gigabytes for an intermediate nobody reads.

    Periodicity is per dimension. A run biasing a torsion against a
    distance is periodic in one and not the other, and treating either the
    way the other wants gives a surface that is wrong at one edge.
    """
    stop = len(hills) if upto is None else int(upto)
    n_dims = hills.n_dims
    if len(axes) != n_dims:
        raise ValueError(
            f"The hills hold {n_dims} variables and {len(axes)} axes were "
            "given. A surface needs one axis per biased variable."
        )
    mesh = np.meshgrid(*axes, indexing="ij")
    shape = mesh[0].shape
    points = np.column_stack([m.ravel() for m in mesh])
    bias = bias_from_hills_nd(
        hills, points, upto=stop, periodic=periodic, chunk=chunk)

    # As in one dimension: the stored heights already carry y/(y-1), so the
    # sum is the free energy and no rescaling belongs here.
    free = -bias
    free = free.reshape(shape)
    return free - float(np.min(free))


def cumulative_surfaces_nd(
    hills: Hills,
    axes: "tuple[np.ndarray, ...]",
    *,
    blocks: int = CONVERGENCE_BLOCKS,
    from_fraction: float = CONVERGENCE_FROM_FRACTION,
    periodic: "tuple[bool, ...] | None" = None,
    chunk: int = 512,
) -> "tuple[list[int], list[np.ndarray]]":
    """`cumulative_surfaces` over two or more variables.

    The saving matters more here. An 80 by 80 grid is 6,400 points, and
    building five surfaces from scratch on a run with tens of thousands of
    hills is five full passes over the deposition where one will do --
    `bias_from_hills_nd` already takes a `start`, so each cut is the previous
    bias plus one segment.
    """
    cuts = _convergence_cuts(
        len(hills), blocks=blocks, from_fraction=from_fraction)
    if not cuts:
        return [], []

    mesh = np.meshgrid(*axes, indexing="ij")
    shape = mesh[0].shape
    points = np.column_stack([m.ravel() for m in mesh])

    bias = np.zeros(points.shape[0], dtype=float)
    surfaces: list[np.ndarray] = []
    start = 0
    for stop in cuts:
        bias = bias + bias_from_hills_nd(
            hills, points, start=start, upto=stop,
            periodic=periodic, chunk=chunk)
        free = (-bias).reshape(shape)
        surfaces.append(free - float(np.min(free)))
        start = stop
    return cuts, surfaces


def marginal_profile(
    surface: np.ndarray, axis: int, *, temperature_K: float = 300.0
) -> np.ndarray:
    """The free energy along one variable, with the others integrated out.

    Not the minimum along the other coordinate, which is the projection
    people usually draw: that reports the bottom of the valley rather than
    how much room there is in it, so a broad shallow channel and a narrow
    deep one come out alike. Integrating the populations keeps the width,
    which is what the free energy along a single coordinate means.
    """
    kt = KB_KJMOL * float(temperature_K)
    other = tuple(i for i in range(surface.ndim) if i != axis)
    weights = np.exp(-(surface - float(np.min(surface))) / kt).sum(axis=other)
    with np.errstate(divide="ignore"):
        profile = -kt * np.log(weights)
    finite = profile[np.isfinite(profile)]
    if finite.size == 0:
        return np.zeros_like(profile)
    return profile - float(np.min(finite))


def recrossings(values: np.ndarray, *, low: float, high: float) -> int:
    """How many times the variable travelled from one side to the other.

    Counted with a hysteresis band: a crossing is only counted once the
    variable has reached the far region, so a run rattling around a threshold
    does not accumulate crossings it did not make. That is the difference
    between a barrier observed several times and a coordinate jittering at
    the top of one.
    """
    if values.size == 0:
        return 0

    crossings = 0
    side: str | None = None
    for value in values:
        if value <= low:
            here = "low"
        elif value >= high:
            here = "high"
        else:
            continue
        if side is not None and here != side:
            crossings += 1
        side = here
    return crossings


def compute_surface(
    hills_path: str | Path,
    colvar_values: np.ndarray | None = None,
    *,
    points: int = 200,
    minimum_recrossings: int = MINIMUM_RECROSSINGS,
    periodic: bool = False,
) -> dict[str, Any]:
    """A free energy surface, or a refusal saying what the run cannot support.

    ``colvar_values`` is the trajectory of the collective variable, which the
    run writes beside the hills. Without it the recrossing count cannot be
    made, and that is said rather than skipped -- a surface reported without
    knowing whether the system ever came back is the claim this exists to
    avoid.
    """
    hills = read_hills(hills_path)

    lowest = float(np.min(hills.centre))
    highest = float(np.max(hills.centre))
    if highest <= lowest:
        return {
            "surface": None,
            "grid": None,
            "refused": (
                "Every hill was deposited at the same value of the collective "
                "variable, so the run explored nothing. There is no surface "
                "along a coordinate that did not move."
            ),
        }

    if periodic:
        # The whole turn. A grid bounded by where hills happened to land
        # stops short of the arc they wrap around, and the coordinate does
        # not stop there.
        grid = np.linspace(-np.pi, np.pi, points)
    else:
        grid = np.linspace(lowest, highest, points)
    surface = surface_from_hills(hills, grid, periodic=periodic)
    earlier = surface_from_hills(
        hills, grid, upto=int(len(hills) * 0.75), periodic=periodic)

    # Judged where the surface means something, not everywhere on the grid.
    #
    # Both surfaces are reported against their own minimum, so they are
    # compared as shapes. The comparison used to run over every point, which
    # makes the number the worst point rather than the typical one -- and the
    # worst point is always the top of the highest barrier, estimated from a
    # handful of visits out of thousands of hills. A tripeptide's psi torsion
    # converged to 1.2 kJ/mol within 10 of its minimum and 2.3 within 20, while
    # the whole-grid figure read 5.4 from a single point at the top of a 65
    # kJ/mol barrier. On that measure no steep coordinate can ever pass,
    # however well its wells are resolved: the test could not say yes to a
    # correct answer.
    shape = surface - float(np.min(surface))
    earlier_shape = earlier - float(np.min(earlier))
    difference = np.abs(shape - earlier_shape)
    judged = shape <= DRIFT_CEILING_KJMOL
    if not judged.any():
        judged = np.ones_like(shape, dtype=bool)
    drift = float(np.max(difference[judged]))
    drift_over_the_whole_grid = float(np.max(difference))

    # Where the run actually put hills. For a periodic variable the grid is
    # always the whole turn -- see the `linspace(-pi, pi)` above -- so a run
    # that explored two thirds of it still has a surface defined on all of
    # it, built from the tails of Gaussians rather than from deposition.
    # Taking the barrier as a maximum over that is reading a number off
    # ground nothing visited.
    explored = (grid >= float(np.min(hills.centre))) & (
        grid <= float(np.max(hills.centre)))
    if not explored.any():
        explored = np.ones_like(grid, dtype=bool)

    # The same movement, point by point rather than as its worst value. The
    # verdict below is unchanged and still rests on `drift`: this is what a
    # reader plots the surface with, and what tells them the wells are firm
    # where the shoulder of the barrier is not.
    cuts, later = cumulative_surfaces(hills, grid, periodic=periodic)
    convergence = convergence_band(
        later, judged=judged & explored, hills_at=cuts)

    first = float(np.mean(hills.height[:max(1, len(hills) // 20)]))
    last = float(np.mean(hills.height[-max(1, len(hills) // 20):]))
    converged = last <= SETTLED_HEIGHT_FRACTION * first if first > 0 else False

    # Between the two deepest basins, which are the states a crossing goes
    # between. Falls back to a band placed a quarter in from the extremes of
    # the hill range where the surface has only one minimum -- there is
    # nothing to cross to, and the older measure at least says whether the
    # coordinate moved.
    span = highest - lowest
    low_edge = lowest + 0.25 * span
    high_edge = highest - 0.25 * span
    two_basins = basins(grid, surface, periodic=periodic)

    crossed: int | None = None
    if colvar_values is not None and colvar_values.size:
        sampled = np.asarray(colvar_values, dtype=float)
        if two_basins is not None:
            crossed = transitions(
                sampled, between=two_basins, periodic=periodic)
        else:
            crossed = recrossings(sampled, low=low_edge, high=high_edge)

    evidence: dict[str, Any] = {
        "basins": (None if two_basins is None
                   else [float(x) for x in two_basins]),
        "hills": len(hills),
        "bias_factor": hills.bias_factor,
        "first_hill_height_kjmol": first,
        "last_hill_height_kjmol": last,
        "drift_kjmol": drift,
        "drift_ceiling_kjmol": DRIFT_CEILING_KJMOL,
        "drift_over_the_whole_grid_kjmol": drift_over_the_whole_grid,
        # Deliberately not called an uncertainty, an error or a sigma. It is
        # one of the two numbers a metadynamics surface deserves and it is
        # the weaker one; the other comes from independent replicas and is
        # not computable from a single run's files, which is the whole
        # reason this one is labelled rather than quietly reported as a
        # plus-or-minus.
        "convergence": convergence,
        "recrossings": crossed,
        # Named, because "recrossings" is a word whose definition changes the
        # number. Counting sign changes of the raw coordinate gave 97 on a run
        # this recorded as 61 -- a 60% difference, and the smaller number is
        # the honest one, since it only counts a crossing once the coordinate
        # has reached the far side. Anyone comparing their own count against
        # this one, and not knowing that, would draw the wrong conclusion
        # about their sampling.
        # It has to say which measure was used, because the two answer
        # different questions and the number alone does not distinguish them.
        "recrossings_definition": (
            (f"travel between the basins at {two_basins[0]:.3g} and "
             f"{two_basins[1]:.3g}, a frame counted as arrived once it is "
             "within a quarter of their separation, so a coordinate "
             "rattling between them does not accumulate crossings it never "
             "made")
            if two_basins is not None else
            (f"transitions between the regions below {low_edge:.3g} and "
             f"above {high_edge:.3g} -- the surface has only one minimum, so "
             "there is no second basin to travel to and this says whether "
             "the coordinate moved rather than whether it changed state")
        ),
        # Measured where the hills actually went, not across the whole grid.
        # For a periodic variable the grid is always the full turn, so a run
        # that explored two thirds of it had its barrier taken as a maximum
        # over an arc no hill was deposited on -- which is the identical
        # failure `umbrella.describe_pmf` was written to fix, its docstring
        # recording "a real study looked to have a 164 kJ/mol barrier that
        # way, against 11 measured where the windows actually were". The
        # umbrella side was corrected and this one was not.
        "barrier_kjmol": float(np.max(surface[explored])),
        "covered": [float(np.min(hills.centre)), float(np.max(hills.centre))],
    }

    reasons: list[str] = []
    if not converged:
        reasons.append(
            f"the hills are still arriving at {last:.2f} kJ/mol against "
            f"{first:.2f} at the start, so the bias is still filling the "
            "landscape rather than having flattened it -- the surface below "
            "is a snapshot of that filling"
        )
    if drift > SETTLED_DRIFT_KJMOL:
        reasons.append(
            f"the surface moved {drift:.1f} kJ/mol between three quarters of "
            f"the hills and all of them, against a tolerance of "
            f"{SETTLED_DRIFT_KJMOL:g}, so it has not stopped changing "
            f"(measured where the surface is within "
            f"{DRIFT_CEILING_KJMOL:g} kJ/mol of its minimum; over the whole "
            f"grid it moved {drift_over_the_whole_grid:.1f}, which is "
            "dominated by the top of the highest barrier and says little)"
        )
    if crossed is None:
        reasons.append(
            "the trajectory of the collective variable was not available, so "
            "there is no way to tell whether the system crossed the barriers "
            "more than once"
        )
    elif two_basins is None:
        # One minimum means there is no second state to cross to, so the
        # count above is movement within a single well and not evidence of
        # anything. Its own definition string has always said so; the gate
        # did not read it, and a run whose coordinate merely rattled passed
        # on 43 crossings of the middle of its own range. The
        # two-dimensional path was given this reason and the
        # one-dimensional one was not.
        reasons.append(
            "the surface has a single minimum, so there is no second state "
            "to cross to and no barrier along this coordinate has been "
            "measured -- what was counted is movement within one well"
        )
    elif crossed < minimum_recrossings:
        reasons.append(
            f"the system crossed between the ends of the range {crossed} "
            f"time(s), against {minimum_recrossings} asked for -- a barrier "
            "crossed once has been observed once, and its height rests on "
            "that single event"
        )

    if reasons:
        return {
            # The surface, not None. The refusal says "the surface below is a
            # snapshot of that filling" and "the hills are on disk, so the
            # check can be made again" -- and then withheld it, so a run whose
            # wells had converged to half of RT left nothing to plot. "Not
            # converged" and "not available" are different claims, and only
            # the first one is true here. It is provisional, which is what the
            # refusal beside it is for.
            "surface": surface,
            "provisional": True,
            "grid": grid,
            "evidence": evidence,
            "refused": (
                "This run does not support a free energy surface: "
                + "; ".join(reasons) + ".\n\n"
                "Sampling for longer is the remedy for all three. The hills "
                "and the trajectory are on disk, so the check can be made "
                "again on a longer run without repeating this one."
            ),
        }

    return {
        "surface": surface,
        "provisional": False,
        "grid": grid,
        "evidence": evidence,
        "refused": None,
    }


def compute_surface_2d(
    hills_path: str | Path,
    colvar_values: "np.ndarray | None" = None,
    *,
    points: int = 80,
    minimum_recrossings: int = MINIMUM_RECROSSINGS,
    periodic: "tuple[bool, bool]" = (False, False),
    temperature_K: float = 300.0,
    names: "tuple[str, str]" = ("cv1", "cv2"),
) -> dict[str, Any]:
    """A two-dimensional surface, judged one dimension at a time.

    The gates are the one-dimensional ones, applied to the free energy
    along each variable with the other integrated out. That is the whole
    point of doing it this way: a run can fill a torsion thoroughly while
    the distance it was also biasing never left one basin, and a verdict
    on the surface as a whole reports either a pass that hides the second
    coordinate or a failure that buries the first. The refusal, when there
    is one, names the dimension.

    ``colvar_values`` is the trajectory of both variables, shape
    (frames, 2). Without it no recrossing count can be made in either
    dimension, and that is said rather than skipped.

    The default grid is coarser than the one-dimensional default because
    the cost is the square of it: 80 by 80 is 6,400 points where 200 by 200
    is 40,000, and the second resolves nothing a metadynamics surface
    supports.
    """
    hills = read_hills(hills_path)
    if hills.n_dims != 2:
        raise ValueError(
            f"{hills_path} holds {hills.n_dims} biased variable(s), and this "
            "builds a surface over exactly two. A one-variable run is "
            "`compute_surface`."
        )

    axes: list[np.ndarray] = []
    for dim in range(2):
        centres = hills.column(dim)
        low, high = float(np.min(centres)), float(np.max(centres))
        if high <= low:
            return {
                "surface": None,
                "axes": None,
                "refused": (
                    f"Every hill was deposited at the same value of "
                    f"{names[dim]}, so the run explored nothing along it. "
                    "There is no surface across a coordinate that did not "
                    "move, and the other coordinate cannot make up for it."
                ),
            }
        if periodic[dim]:
            axes.append(np.linspace(-np.pi, np.pi, points))
        else:
            axes.append(np.linspace(low, high, points))

    grid = tuple(axes)
    surface = surface_from_hills_nd(hills, grid, periodic=periodic)
    earlier = surface_from_hills_nd(
        hills, grid, upto=int(len(hills) * 0.75), periodic=periodic)

    # Cut once, marginalised per dimension below. The marginal is a log of a
    # sum of exponentials, so it cannot be taken after averaging surfaces:
    # each cut has to be integrated in its own right.
    cuts, later = cumulative_surfaces_nd(hills, grid, periodic=periodic)

    first = float(np.mean(hills.height[:max(1, len(hills) // 20)]))
    last = float(np.mean(hills.height[-max(1, len(hills) // 20):]))
    converged = last <= SETTLED_HEIGHT_FRACTION * first if first > 0 else False

    sampled = (None if colvar_values is None
               else np.asarray(colvar_values, dtype=float))
    if sampled is not None and sampled.ndim != 2:
        raise ValueError(
            "colvar_values for a two-variable run is one column per "
            f"variable, and an array of shape {sampled.shape} is not that."
        )

    per_dimension: list[dict[str, Any]] = []
    reasons: list[str] = []
    for dim in range(2):
        # This dimension's hill centres, read before anything uses them. The
        # assignment used to sit *after* the recrossing band was computed
        # from `centres`, so dimension 0 took its range from dimension 1's
        # hills and dimension 1 from dimension 0's -- a leftover from the
        # axis-building loop on the first pass, and the previous iteration's
        # value on the second. Any pair of CVs on different scales then gave
        # 0 recrossings or a saturated count: a coordinate sweeping its full
        # range fifty times reported none.
        centres = hills.column(dim)

        profile = marginal_profile(surface, dim, temperature_K=temperature_K)
        profile_earlier = marginal_profile(
            earlier, dim, temperature_K=temperature_K)

        # Judged where the profile means something, as in one dimension.
        judged = profile <= DRIFT_CEILING_KJMOL
        if not judged.any():
            judged = np.ones_like(profile, dtype=bool)
        drift = float(np.max(np.abs(profile - profile_earlier)[judged]))
        convergence = convergence_band(
            [marginal_profile(cut, dim, temperature_K=temperature_K)
             for cut in later],
            judged=judged, hills_at=cuts)

        two_basins = basins(grid[dim], profile, periodic=periodic[dim])
        crossed: int | None = None
        if sampled is not None and sampled.shape[0]:
            values = sampled[:, dim]
            if two_basins is not None:
                crossed = transitions(
                    values, between=two_basins, periodic=periodic[dim])
            else:
                low, high = float(np.min(centres)), float(np.max(centres))
                span = high - low
                crossed = recrossings(
                    values, low=low + 0.25 * span, high=high - 0.25 * span)

        travelled = float(np.max(centres) - np.min(centres))
        typical_width = float(np.median(hills.sigma_column(dim)))
        in_widths = (travelled / typical_width
                     if typical_width > 0 else float("inf"))

        record = {
            "name": names[dim],
            "periodic": bool(periodic[dim]),
            "hill_range": [float(np.min(centres)), float(np.max(centres))],
            "explored_in_hill_widths": in_widths,
            "basins": (None if two_basins is None
                       else [float(x) for x in two_basins]),
            "drift_kjmol": drift,
            "convergence": convergence,
            "recrossings": crossed,
            "barrier_kjmol": float(np.max(profile)),
            "marginal": "the other variable integrated out, not minimised over",
            "recrossings_definition": (
                (f"travel between the basins at {two_basins[0]:.3g} and "
                 f"{two_basins[1]:.3g}")
                if two_basins is not None else
                ("movement across the middle of the explored range -- the "
                 "marginal has one minimum, so this says whether the "
                 "coordinate moved, not whether it changed state, and it "
                 "is not evidence that a barrier was crossed")),
        }
        per_dimension.append(record)

        if in_widths < MINIMUM_EXPLORED_HILL_WIDTHS:
            reasons.append(
                f"along {names[dim]} the hills span {in_widths:.1f} hill "
                f"widths, under the {MINIMUM_EXPLORED_HILL_WIDTHS:g} a "
                "coordinate has to travel before the surface across it is "
                "the landscape rather than the shape of the bias"
            )
        if two_basins is None:
            reasons.append(
                f"along {names[dim]} the surface has a single minimum, so "
                "there is no second state to cross to and no barrier along "
                "it has been measured"
            )
        if drift > SETTLED_DRIFT_KJMOL:
            reasons.append(
                f"along {names[dim]} the surface is still moving: "
                f"{drift:.1f} kJ/mol between three quarters of the hills and "
                f"all of them, against {SETTLED_DRIFT_KJMOL} allowed"
            )
        if crossed is None:
            reasons.append(
                f"along {names[dim]} no trajectory of the variable was given, "
                "so whether the system ever came back is unknown"
            )
        elif two_basins is not None and crossed < minimum_recrossings:
            reasons.append(
                f"along {names[dim]} the system crossed {crossed} time(s), "
                f"against {minimum_recrossings} needed for a barrier to be a "
                "measurement rather than an anecdote"
            )

    if not converged:
        reasons.append(
            f"the hills have not flattened: the last are {last:.3g} kJ/mol "
            f"against {first:.3g} at the start, and a bias still growing is "
            "a snapshot of a filling process rather than a landscape"
        )

    # The band over the surface itself, judged where it means something, and
    # without the per-point array: on an 80 by 80 grid that is 6,400 numbers
    # in a manifest nobody reads point by point, and the per-dimension bands
    # above are what a reader plots against. The two summary figures are
    # here because the marginals integrate a coordinate out, and a surface
    # can be converged along both marginals while a corner of it is not.
    whole = convergence_band(
        later, judged=(surface <= DRIFT_CEILING_KJMOL), hills_at=cuts)
    whole.pop("band_kjmol", None)

    evidence = {
        "hills": len(hills),
        "bias_factor": hills.bias_factor,
        "first_hill_height_kjmol": first,
        "last_hill_height_kjmol": last,
        "temperature_K": float(temperature_K),
        "per_dimension": per_dimension,
        "convergence": whole,
        "barrier_kjmol": float(np.max(surface)),
    }

    return {
        "surface": surface,
        "axes": [axis.tolist() for axis in grid],
        "evidence": evidence,
        "refused": (None if not reasons else
                    "This surface is not a measurement: " + "; ".join(reasons)
                    + "."),
    }
