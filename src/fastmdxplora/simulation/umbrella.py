"""A free energy along a coordinate, from equilibrium sampling at each point.

Steered molecular dynamics drags a system along a coordinate and reports the
work, which depends on how fast it was dragged. Umbrella sampling does the
opposite: it holds the system at a series of positions, lets each equilibrate,
and recombines the sampling into a potential of mean force. Nothing is
hurried, so nothing is dissipated, and the result is a free energy rather than
an upper bound on one.

**It works only if adjacent windows sample overlapping ranges.** The
recombination stitches histograms together, and where two neighbours never
visit the same value there is nothing to stitch: the free energy on one side
cannot be placed relative to the other, and the curve through that gap is
interpolation dressed as a measurement. Overlap is therefore checked and a
gap is reported rather than bridged.

That check is the reason this module exists rather than a call to an external
WHAM program. Every implementation computes the same PMF; few of them say
when the windows could not support one.

**Where the starting structures come from matters.** Windows started from a
single structure are strained at the far end of the range, and the strain
relaxes into the sampling as drift. The usual source is a steered run: pull
once, take a frame near each window's centre, and each window begins near
where it will sit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from fastmdxplora.uncertainty import DEFAULT_RESAMPLES, block_bootstrap

logger = logging.getLogger(__name__)

__all__ = [
    "Window",
    "expand_umbrella",
    "plan_from_expanded",
    "UmbrellaPlan",
    "plan_windows",
    "windows_as_sweep",
    "collect_samples",
    "overlap_between",
    "compute_pmf",
]

#: Boltzmann's constant in kJ/mol/K, so a PMF comes out in kJ/mol.
KB_KJ = 0.008314462618


@dataclass(frozen=True)
class Window:
    """One position along the coordinate, held there by a spring."""

    index: int
    centre: float
    force_constant: float

    def plumed_lines(self, cv_lines: list[str]) -> str:
        return "\n".join(cv_lines + [
            "",
            f"restraint: RESTRAINT ARG=cv AT={self.centre:g} "
            f"KAPPA={self.force_constant:g}",
            "",
            "PRINT ARG=cv,restraint.bias STRIDE=100 FILE=COLVAR",
        ]) + "\n"


@dataclass(frozen=True)
class UmbrellaPlan:
    """Where the windows are, and how hard each holds."""

    windows: tuple[Window, ...]
    collective_variable: str
    #: Steps to discard at the start of each window before it counts. A
    #: window begins away from where it will settle, and counting that
    #: approach as sampling biases the histogram towards where it started.
    #: The fraction of each window's sampling discarded before its histogram
    #: is built. A window begins away from where it will settle and the
    #: approach is not sampling, so some must go; how much is a judgement
    #: about how long a window takes to settle, which depends on the barrier
    #: and the force constant, so it belongs to whoever is making the claim
    #: rather than to this file. A fifth is a common choice and the default.
    equilibration_fraction: float = 0.2
    #: How much two neighbours must share before their free energies can be
    #: placed relative to one another. Three per cent is enough to stitch and
    #: thin: on a real study, pairs at seven per cent passed while a reader
    #: might reasonably want fifteen. It is a judgement about how much
    #: evidence a joint needs, so it belongs to whoever is making the claim.
    minimum_overlap: float = 0.03
    #: How many values a window must have recorded before its histogram means
    #: anything. An overlap is the area two histograms share, and a histogram
    #: from tens of points is mostly noise -- so a run short enough to be a
    #: smoke test will produce overlaps, and gaps, that are arithmetic rather
    #: than evidence. Like the overlap threshold, it is a judgement, so a
    #: study can set its own.
    minimum_samples: int = 200

    def as_record(self) -> dict[str, Any]:
        return {
            "collective_variable": self.collective_variable,
            "n_windows": len(self.windows),
            "from": self.windows[0].centre if self.windows else None,
            "to": self.windows[-1].centre if self.windows else None,
            "force_constant": (self.windows[0].force_constant
                               if self.windows else None),
            "equilibration_fraction": self.equilibration_fraction,
            "minimum_overlap": self.minimum_overlap,
            "minimum_samples": self.minimum_samples,
        }


#: Every key the umbrella block reads. Declared beside the reader so the two
#: cannot drift: a setting added below without being added here is refused,
#: which is a loud failure in a test rather than a quiet one in a study.
_UMBRELLA_OWN_KEYS: frozenset[str] = frozenset({
    "force_constant", "centres", "centers", "from", "to", "n_windows",
    "equilibration_fraction",
    "minimum_overlap", "minimum_samples",
    # A finished pull to take starting structures from, instead of running
    # another. Documented in the schema before it was accepted here, which
    # is a config error waiting for the first person to follow the
    # documentation.
    "seed_from",
    # Written by the expansion onto each window, and read back from it.
    "centre", "index",
})


def _accepted_keys() -> frozenset[str]:
    """Umbrella's own settings, plus whatever names a collective variable.

    Taken from the collective-variable layer rather than copied, because the
    layer is shared -- a study biasing a ligand's depth in a bilayer names the
    bilayer the same way whichever method does the biasing, and a second copy
    of that list would go stale the next time a variable is added.
    """
    from fastmdxplora.simulation.metadynamics import COLLECTIVE_VARIABLE_KEYS

    return _UMBRELLA_OWN_KEYS | COLLECTIVE_VARIABLE_KEYS


#: The order a person reads them in: what is being biased, over what range,
#: how hard, and only then the selections that resolve the variable. An
#: alphabetical list opens with `axis_selection, bilayer_selection, centers,
#: centres` and buries the three keys every umbrella block has.
_READING_ORDER: tuple[str, ...] = (
    "collective_variable", "from", "to", "n_windows", "centres", "centers",
    "force_constant",
    "equilibration_fraction", "minimum_overlap", "minimum_samples",
    "seed_from",
)


def _in_reading_order(keys: "frozenset[str] | set[str]") -> list[str]:
    """`keys` ordered by role, with anything unlisted sorted after."""
    known = [k for k in _READING_ORDER if k in keys]
    return known + sorted(set(keys) - set(known))


def check_umbrella_keys(spec: dict[str, Any]) -> None:
    """Refuse a setting the block does not have.

    Nothing else checked this. `minimum_ovelap: 0.15` -- one letter -- was
    accepted, ignored, and the study stitched at the three per cent default
    while its author believed it had demanded fifteen. The run completes and
    produces a curve, and the guard that was supposed to stand behind that
    curve never existed.

    Every refusal this module makes can be switched off by a typo, so the
    typo is what has to be caught.
    """
    from fastmdxplora.config.loader import ConfigError, _suggest

    accepted = _accepted_keys()
    unknown = sorted(set(spec) - accepted)
    if not unknown:
        return
    if "equilibration_steps" in unknown:
        # Not a typo and not unknown: it was accepted, validated, recorded in
        # pmf.json and read by nothing. A study writing
        # `equilibration_steps: 500000` discarded no more than the default
        # fraction and had the record say otherwise. This module refuses a
        # *misspelled* key precisely so a guard cannot be silently switched
        # off; a correctly spelled inert one is the same failure with better
        # spelling, so it is named rather than listed.
        raise ConfigError(
            "'equilibration_steps' was accepted and never applied: it was "
            "recorded in pmf.json and read by nothing, so a study asking for "
            "it discarded only the default fraction. Use "
            "`equilibration_fraction` instead -- a fraction of each window's "
            "production, which is what the discard is actually measured in."
        )
    named = ", ".join(
        f"'{key}'{_suggest(key, set(accepted))}" for key in unknown)
    raise ConfigError(
        f"Unknown umbrella setting{'s' if len(unknown) > 1 else ''}: {named}. "
        "Accepted: " + ", ".join(_in_reading_order(
            accepted - {"centre", "index"})) + "."
    )


def _checked_fraction(value: Any) -> float:
    """The discard fraction, refused where it would not leave a histogram.

    At one, a window keeps nothing and the free energy has no evidence behind
    it. At zero it keeps the approach to its centre, which is not sampling of
    the restrained ensemble and pulls the histogram towards where the window
    started. Both are refused here rather than where the free energy comes
    out empty, because by then the study has already run.
    """
    try:
        fraction = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"equilibration_fraction must be a number, got {value!r}.") from None
    if not 0.0 < fraction < 1.0:
        raise ValueError(
            f"equilibration_fraction is {fraction:g}; it must be above 0 and "
            "below 1. A window begins away from where it settles, so some of "
            "it has to be discarded, and discarding all of it leaves no "
            "histogram to place.")
    return fraction


def plan_windows(spec: dict[str, Any]) -> UmbrellaPlan:
    """Read an umbrella block into a set of windows.

    Positions may be given explicitly, or as a range and a count. The count
    is the decision that matters: too few and adjacent windows do not overlap,
    which no amount of sampling repairs.
    """
    check_umbrella_keys(spec)

    variable = str(spec.get("collective_variable", "")).lower()
    if not variable:
        raise ValueError(
            "Umbrella sampling needs a `collective_variable` -- the coordinate "
            "the free energy is a function of."
        )

    force = spec.get("force_constant")
    if force is None:
        raise ValueError(
            "Umbrella sampling needs a `force_constant`: how firmly each "
            "window is held, in kJ/mol per unit of the variable squared. It "
            "sets how far a window wanders, and therefore whether neighbours "
            "overlap -- too stiff and they do not, too soft and the system "
            "escapes towards the nearest minimum. There is no value that is "
            "right for an arbitrary coordinate."
        )
    force = float(force)

    if "centres" in spec or "centers" in spec:
        centres = [float(c) for c in (spec.get("centres") or spec.get("centers"))]
        if len(centres) < 2:
            raise ValueError("Umbrella sampling needs at least two windows.")
    else:
        for key in ("from", "to", "n_windows"):
            if spec.get(key) is None:
                raise ValueError(
                    "Umbrella windows are given either as `centres`, or as "
                    "`from`, `to` and `n_windows`. "
                    f"`{key}` is missing."
                )
        count = int(spec["n_windows"])
        if count < 2:
            raise ValueError("Umbrella sampling needs at least two windows.")
        # Plain floats: numpy scalars serialise as "!!python/object" in YAML
        # and are not JSON, so a config written back out would not reload.
        centres = [float(c) for c in np.linspace(
            float(spec["from"]), float(spec["to"]), count)]

    return UmbrellaPlan(
        windows=tuple(Window(index=i, centre=c, force_constant=force)
                      for i, c in enumerate(centres)),
        collective_variable=variable,
        equilibration_fraction=_checked_fraction(
            spec.get("equilibration_fraction", 0.2)),
        minimum_overlap=float(spec.get("minimum_overlap", 0.03)),
        minimum_samples=int(spec.get("minimum_samples", 200)),
    )


def expand_umbrella(config: dict[str, Any]) -> dict[str, Any]:
    """Turn a config with an umbrella block into one with a run per window.

    An umbrella job is one system held at many positions, which is the shape
    the batch machinery already runs -- so the windows become `systems`
    entries differing in the position each holds, and the scheduling,
    parallelism and per-GPU pinning come from the code that already does
    those things.

    Returns the config unchanged where there is no umbrella block.
    """
    simulation = config.get("simulation") or {}
    spec = simulation.get("umbrella")
    if not spec:
        return config

    if config.get("systems") and len(config["systems"]) > 1:
        raise ValueError(
            "Umbrella sampling holds one system at many positions. This "
            f"config has {len(config['systems'])} systems, and a window set "
            "for each would be several separate free energies -- run them as "
            "separate studies so each has its own windows and its own "
            "overlap check."
        )

    plan = plan_windows(spec)
    base = dict(config.get("systems", [{}])[0]) if config.get("systems") else {}

    systems = []
    for window in plan.windows:
        entry = dict(base)
        entry["id"] = f"window_{window.index:02d}"
        # Everything the window needs to bias itself, resolved per run.
        # Every shared simulation setting, then this window's own. A
        # per-system block replaces the top-level one rather than merging, so
        # a block holding only the umbrella settings silently discarded the
        # step counts, the timestep and everything else the study asked for.
        # `steered` is excluded alongside `umbrella`: a pull beside an
        # umbrella block means "seed the windows from one pull", and copying
        # it into every window would have each of them drag the ligand out
        # again while restrained at a fixed point. The study keeps the block;
        # the windows do not.
        merged = {k: v for k, v in simulation.items()
                  if k not in ("umbrella", "steered")}
        merged.update(entry.get("simulation") or {})
        entry["simulation"] = merged
        entry["simulation"]["umbrella"] = dict(
            {k: v for k, v in spec.items()
             if k not in ("centres", "centers", "from", "to", "n_windows")},
            centre=window.centre,
            force_constant=window.force_constant,
            index=window.index,
        )
        systems.append(entry)

    expanded = dict(config)
    expanded["systems"] = systems
    # The block has been expanded; leaving it would have every run try to
    # expand it again.
    expanded["simulation"] = {k: v for k, v in simulation.items()
                              if k != "umbrella"}
    # The plan is deliberately not stashed in the config. It was, and
    # validation rejected the extra key -- correctly, since a config is what
    # a user wrote and not a place to hide state. Each window carries its own
    # block, so the set can be rebuilt from the runs when they finish.
    return expanded


def plan_from_expanded(config: dict[str, Any]) -> UmbrellaPlan | None:
    """Rebuild the window set from a config that has already been expanded.

    Returns ``None`` where this is not an umbrella study.
    """
    systems = config.get("systems") or []
    windows = []
    variable = ""
    fraction = 0.2
    minimum = 0.03
    fewest = 200
    for entry in systems:
        block = ((entry.get("simulation") or {}).get("umbrella")
                 if isinstance(entry, dict) else None)
        if not block or block.get("centre") is None:
            continue
        variable = str(block.get("collective_variable", variable))
        fraction = _checked_fraction(
            block.get("equilibration_fraction", fraction))
        minimum = float(block.get("minimum_overlap", minimum))
        fewest = int(block.get("minimum_samples", fewest))
        windows.append(Window(
            index=int(block.get("index", len(windows))),
            centre=float(block["centre"]),
            force_constant=float(block["force_constant"]),
        ))
    if len(windows) < 2:
        return None
    return UmbrellaPlan(
        windows=tuple(sorted(windows, key=lambda w: w.index)),
        collective_variable=variable,
        equilibration_fraction=fraction,
        minimum_overlap=minimum,
        minimum_samples=fewest,
    )


def windows_as_sweep(plan: UmbrellaPlan) -> list[dict[str, Any]]:
    """The windows as a sweep, which is the shape the batch machinery runs.

    An umbrella job is one system at many restraint positions, which is the
    same shape as a parameter sweep -- so the runs are expanded and scheduled
    by the machinery that already does that, rather than by a second one.
    """
    return [
        {
            "id": f"window_{w.index:02d}",
            "umbrella_centre": w.centre,
            "umbrella_force_constant": w.force_constant,
        }
        for w in plan.windows
    ]


def collect_samples(
    directories: dict[int, Any],
    *,
    equilibration_fraction: float = 0.2,
) -> dict[int, np.ndarray]:
    """Read each window's sampling back from its COLVAR file.

    Takes the directories rather than working them out. The first version
    guessed -- ``<output>/window_00/simulation/COLVAR`` -- and the runs are
    actually at ``<output>/runs/window-00/simulation/COLVAR``: under a
    ``runs`` directory, with the identifier slugged. Two mistakes in one
    path, neither visible until a real study finished and found nothing.
    The caller knows where it put things.

    The first part of each window is discarded. A window begins away from
    where it will settle, and counting the approach as sampling biases the
    histogram towards where the run started -- which is the one place the
    free energy is guaranteed not to be flat.
    """
    from pathlib import Path

    samples: dict[int, np.ndarray] = {}
    missing: list[str] = []

    for index, directory in sorted(directories.items()):
        colvar = Path(directory) / "simulation" / "COLVAR"
        if not colvar.is_file():
            missing.append(f"window {index} ({colvar})")
            continue
        rows = []
        for line in colvar.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    rows.append(float(parts[1]))
                except ValueError:
                    continue
        if not rows:
            missing.append(f"window {index} (empty COLVAR)")
            continue
        values = np.asarray(rows)
        cut = int(len(values) * equilibration_fraction)
        samples[index] = values[cut:]

    if missing:
        raise FileNotFoundError(
            "These windows produced no sampling: " + "; ".join(missing) + ". "
            "A free energy cannot be computed from a partial set, because "
            "the windows either side of a missing one have nothing between "
            "them to stitch through."
        )
    return samples


def overlap_between(a: np.ndarray, b: np.ndarray, bins: int = 50) -> float:
    """How much two windows' sampling shares ground, from 0 to 1.

    The overlap coefficient: the area shared by two normalised histograms.
    Zero means they never visited the same value, and no recombination can
    place one relative to the other.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.size == 0 or b.size == 0:
        return 0.0

    lo = min(a.min(), b.min())
    hi = max(a.max(), b.max())
    if hi <= lo:
        return 1.0 if abs(a.mean() - b.mean()) < 1e-12 else 0.0

    edges = np.linspace(lo, hi, bins + 1)
    pa, _ = np.histogram(a, bins=edges, density=True)
    pb, _ = np.histogram(b, bins=edges, density=True)
    width = edges[1] - edges[0]
    return float(np.minimum(pa, pb).sum() * width)


def windows_that_drifted(
    samples: dict[int, np.ndarray], plan: "UmbrellaPlan"
) -> list[dict[str, Any]]:
    """Windows whose sampling is not where the restraint was told to hold it.

    A window is a restraint at a position, and the histogram it produces is
    supposed to sit around that position. When it does not, the restraint has
    lost: usually because every window started from the same structure and the
    spring never dragged it out, so the far windows relax back towards the
    bound state and pile up on each other.

    That is a different failure from a genuine gap, and it wants the opposite
    remedy. A softer force constant -- the advice for windows that do not
    reach each other -- lets a window that has already slipped slip further.

    "Not where it was told" is measured against the spacing to its neighbour,
    because that is the distance the plan intends between one window and the
    next: sampling further from its own centre than half that is sampling
    somewhere another window was supposed to be.
    """
    ordered = [w for w in plan.windows if w.index in samples]
    periodic = getattr(plan, "collective_variable", None) in PERIODIC_VARIABLES
    drifted: list[dict[str, Any]] = []
    for position, window in enumerate(ordered):
        # The spacing to a neighbour is a distance on the coordinate, and on
        # a circle the first and last windows are neighbours -- so this is
        # the short way round too, or the window at the wrap is allowed a
        # tolerance set by the whole turn.
        neighbours = [
            float(np.abs(displacement(
                np.array([other.centre]), window.centre, periodic)[0]))
            for offset in (-1, 1)
            if 0 <= position + offset < len(ordered)
            for other in [ordered[position + offset]]
        ]
        if not neighbours:
            continue
        allowed = 0.5 * min(neighbours)
        # Measured with `displacement`, which is in this module for exactly
        # this and was not used here. On a torsion, twelve windows tiling the
        # full turn -- every one drawing an exact Boltzmann distribution
        # about its own centre, so nothing had drifted -- reported the window
        # straddling +-pi as 0.488 rad away, and the refusal that follows
        # advises holding it harder with a larger force_constant. The wrong
        # remedy, aimed at the one window behaving perfectly.
        #
        # The centre of a circular sample is its circular mean, not its
        # median: a median of values either side of the wrap lands opposite
        # where they are.
        held = samples[window.index]
        if periodic:
            sat_at = float(np.arctan2(np.mean(np.sin(held)),
                                      np.mean(np.cos(held))))
        else:
            sat_at = float(np.median(held))
        away = float(np.abs(displacement(
            np.array([sat_at]), window.centre, periodic)[0]))
        if away > allowed:
            drifted.append({
                "window": window.index,
                "centre": window.centre,
                "sampled_at": sat_at,
                "away_by": away,
            })
    return drifted


def windows_with_too_little_sampling(
    samples: dict[int, np.ndarray], plan: "UmbrellaPlan"
) -> list[dict[str, Any]]:
    """Windows whose histograms are too sparse to mean anything.

    An overlap is the area two histograms share. From tens of points a
    histogram is mostly noise, so a run short enough to be a smoke test
    produces overlaps -- and gaps -- that are arithmetic rather than
    evidence. A free energy stitched from them would carry that noise as
    structure and look exactly like a result.
    """
    thin = [
        {"window": window.index, "samples": int(len(samples[window.index]))}
        for window in plan.windows
        if window.index in samples
        and len(samples[window.index]) < plan.minimum_samples
    ]
    return thin


#: Collective variables that come back to where they started. A window study
#: on one of these has no ends: the arc between the last centre and the first
#: is as real as any other, and leaving it bare is a gap rather than a
#: boundary.
#: Coordinates that wrap. A torsion does: -pi and +pi are the same
#: arrangement, so a window may straddle the join and the profile must meet
#: itself. A bond *angle* does not -- PLUMED's ANGLE has domain [0, pi], and
#: treating it as a full turn built surface grids over negative angles no
#: geometry can occupy, took barriers as maxima across that fabricated half,
#: and advised tiling windows onto it. `displacement()` was unaffected, since
#: it is a no-op for values already inside [0, pi], so the bias and the
#: histogramming were right and only the advice and the reported extent were
#: wrong.
PERIODIC_VARIABLES = frozenset({"torsion"})


def warn_if_a_circle_is_left_open(plan: "UmbrellaPlan") -> str | None:
    """Say when a periodic coordinate has been tiled only part of the way.

    A real study placed nine windows from -30 to 165 degrees of a torsion and
    read as though it had measured a barrier. It had measured one of the two
    barriers between its minima; the other, through the 195 degrees nobody
    put a window on, was simply absent -- and the free energy grid spanned
    the whole circle regardless, because the coordinate goes there whether or
    not a window is holding it.
    """
    variable = getattr(plan, "collective_variable", None)
    if variable not in PERIODIC_VARIABLES or len(plan.windows) < 2:
        return None
    centres = sorted(w.centre for w in plan.windows)
    covered = centres[-1] - centres[0]
    gap = 2.0 * np.pi - covered
    if gap <= 0:
        return None
    spacing = covered / (len(centres) - 1)
    if gap <= 1.5 * spacing:
        # Closed to within a window's spacing: the circle is tiled.
        return None
    return (
        f"The windows cover {np.degrees(covered):.0f} degrees of a "
        f"{variable}, leaving {np.degrees(gap):.0f} degrees with no window "
        "on it. A periodic coordinate has no ends, so that arc is a gap and "
        "not a boundary: the free energy across it is not measured, and the "
        "barrier between the minima either side of it is unknown. Tile the "
        "full turn, or read the result as the one path it covers.")


def closure_gap(coordinate: Any, energy: Any) -> float | None:
    """How far a periodic profile misses meeting itself, in kJ/mol.

    The two ends of a full turn are the same place, so their free energies
    must be equal. They are computed from different windows by a chain of
    joins, and nothing in the arithmetic forces them to agree -- which makes
    the difference between them a measurement rather than a defect: it is
    what the study's own statistics are worth.

    Not forced to zero. A constraint would make the number vanish and take
    the information with it. On a well-sampled synthetic profile this comes
    out at zero on its own; on a real study of 0.2 ns windows it came out at
    2 kJ/mol, which is the honest size of that study's uncertainty.
    """
    coordinate = np.asarray(coordinate, dtype=float)
    energy = np.asarray([np.nan if v is None else float(v) for v in energy],
                        dtype=float)
    finite = np.isfinite(energy)
    if finite.sum() < 2:
        return None

    where, values = coordinate[finite], energy[finite]
    # Only when the profile spans the turn: two ends a long way short of
    # meeting are not failing to close, they are simply not a circle.
    span = float(where[-1] - where[0])
    if span < 2.0 * np.pi - (where[1] - where[0]) * 2.0:
        return None
    return abs(float(values[-1] - values[0]))


def describe_pmf(coordinate: Any, energy: Any,
                 covered: tuple[float, float] | None = None,
                 *, periodic: bool = False) -> dict[str, Any]:
    """The numbers a reader wants from a curve, computed once, here.

    Reading these by hand is how the curve gets misread. The grid spans
    wherever the coordinate went; the windows covered a part of it, and the
    rest comes back as gaps. Taking a minimum across the whole grid picks a
    reference in a region no window visited -- a real study looked to have a
    164 kJ/mol barrier that way, against 11 measured where the windows
    actually were.
    """
    coordinate = np.asarray(coordinate, dtype=float)
    energy = np.asarray([np.nan if v is None else float(v) for v in energy],
                        dtype=float)
    if coordinate.size == 0 or not np.any(np.isfinite(energy)):
        return {"barrier_kjmol": None, "covered": None, "minima": []}

    inside = np.isfinite(energy)
    if covered is not None:
        inside &= (coordinate >= covered[0]) & (coordinate <= covered[1])
    if not np.any(inside):
        return {"barrier_kjmol": None, "covered": None, "minima": []}

    where, values = coordinate[inside], energy[inside]
    values = values - float(np.min(values))
    top = int(np.argmax(values))

    # A minimum is a bin lower than both its neighbours. Endpoints are left
    # out: the profile does not stop there, the windows do.
    minima = [
        {"coordinate": float(where[i]), "free_energy_kjmol": float(values[i])}
        for i in range(1, values.size - 1)
        if values[i] < values[i - 1] and values[i] < values[i + 1]
    ]
    return {
        "barrier_kjmol": float(values[top]),
        "barrier_at": float(where[top]),
        "covered": [float(where.min()), float(where.max())],
        "minima": sorted(minima, key=lambda m: m["free_energy_kjmol"]),
        # Only where the coordinate is a circle. `closure_gap` decides "is
        # this a closed turn?" from the span alone -- span >= 2*pi - 2*delta
        # -- with no reference to whether the coordinate can close. A ligand
        # unbinding along a distance in nm spanning more than 6.28 satisfies
        # that arithmetic, and got a `closure_gap_kjmol` documented as "how
        # far a periodic profile misses meeting itself" and as saying "what
        # the study's own statistics are worth": a fabricated uncertainty
        # equal to the well depth of a coordinate with two ends.
        "closure_gap_kjmol": (closure_gap(coordinate, energy)
                              if periodic else None),
    }


def displacement(values: Any, centre: float, periodic: bool) -> np.ndarray:
    """How far each value sits from a window's centre.

    On a straight coordinate that is a subtraction. On a circle it is not: a
    torsion sample at +170 degrees is ten degrees from a window held at -180,
    not three hundred and fifty, and charging it the restraint energy for
    three hundred and fifty is charging it 933 kJ/mol instead of 0.76. The
    weight that follows is wrong by a factor of 10^162, the window's free
    energy is pushed up, and every join after it inherits the error.

    A real study found this: twelve windows tiling a full turn returned a
    monotonic ramp of 180 kJ/mol with no minimum in it, while every check
    passed -- twelve runs, no unsampled bins, every overlap above the
    threshold. The nine-window study before it covered only part of the turn,
    kept its windows away from the wrap, and looked entirely reasonable.
    """
    difference = np.asarray(values, dtype=float) - float(centre)
    if not periodic:
        return difference
    # Onto (-pi, pi]: the shorter way round is the real distance.
    return np.remainder(difference + np.pi, 2.0 * np.pi) - np.pi


#: How still the window free energies must be before WHAM is done, in
#: kJ/mol. Reached in a few hundred iterations for overlapping windows and
#: not reached at all for disjoint stiff ones, which is why what the loop
#: finished at is reported rather than assumed.
WHAM_TOLERANCE_KJMOL = 1e-6

#: Iterations before the loop gives up and says so.
WHAM_MAX_ITERATIONS = 2000


def compute_pmf(
    samples: dict[int, np.ndarray],
    plan: UmbrellaPlan,
    *,
    temperature_K: float = 300.0,
    bins: int = 60,
    minimum_overlap: float | None = None,
    bootstrap_resamples: int = DEFAULT_RESAMPLES,
    bootstrap_seed: int = 0,
    _edges: np.ndarray | None = None,
) -> dict[str, Any]:
    """A potential of mean force, or a refusal saying why not.

    ``samples`` maps a window's index to the collective-variable values it
    sampled, after equilibration has been discarded.

    The overlap between neighbours is checked first. Where a pair does not
    share ground, the free energy on one side cannot be placed relative to the
    other, and a curve drawn through the gap is interpolation presented as a
    measurement.
    """
    # The plan carries it, so a study's own threshold applies wherever the
    # recombination happens rather than only where somebody remembered to
    # pass it.
    if minimum_overlap is None:
        minimum_overlap = plan.minimum_overlap

    ordered = [w for w in plan.windows if w.index in samples]
    if len(ordered) < 2:
        raise ValueError(
            "A potential of mean force needs at least two windows with "
            f"sampling in them; {len(ordered)} were given."
        )

    gaps = []
    overlaps = []
    for left, right in zip(ordered, ordered[1:]):
        shared = overlap_between(samples[left.index], samples[right.index])
        overlaps.append({
            "between": [left.index, right.index],
            "centres": [left.centre, right.centre],
            "overlap": shared,
        })
        if shared < minimum_overlap:
            gaps.append((left, right, shared))

    drifted = windows_that_drifted(samples, plan)
    thin = windows_with_too_little_sampling(samples, plan)

    if thin:
        # Said before the gap and before the drift, because both are read off
        # histograms that this run did not fill. A refusal naming a specific
        # pair would be a precise claim resting on tens of points.
        fewest = min(t["samples"] for t in thin)
        reason = (
            f"{len(thin)} of {len(ordered)} windows recorded fewer than "
            f"{plan.minimum_samples} values after equilibration was discarded "
            f"-- the thinnest has {fewest}. An overlap is the area two "
            "histograms share, and a histogram from tens of points is mostly "
            "noise, so the overlaps below are arithmetic rather than "
            "evidence. A free energy stitched from them would carry that "
            "noise as structure and look exactly like a result.\n\n"
        )
        if drifted:
            where = "; ".join(
                f"window {d['window']} was held at {d['centre']:g} and "
                f"sampled around {d['sampled_at']:.3g}"
                for d in drifted
            )
            reason += (
                f"{len(drifted)} of them are also not at their centres: "
                f"{where}. A run this short explains that on its own -- a "
                "restraint needs time to pull a system to where it is held -- "
                "so whether the windows are too softly held cannot be told "
                "apart from a run that ended before they arrived. Sample for "
                "longer first, and read this again.\n\n"
            )
        reason += (
            f"`minimum_samples` ({plan.minimum_samples}) is a judgement about "
            "how much evidence a histogram needs, and a study that wants a "
            "smoke test to reach the recombination can lower it."
        )
        return {
            "pmf": None,
            "overlaps": overlaps,
            "drifted": drifted,
            "thin": thin,
            "refused": reason,
        }

    if gaps:
        described = "; ".join(
            f"windows {a.index} and {b.index} (at {a.centre:g} and "
            f"{b.centre:g}) share {s:.1%}"
            for a, b, s in gaps
        )
        reason = (
            "Adjacent windows do not overlap, so no free energy can be "
            f"computed across the gap: {described}. Recombination "
            "stitches histograms together, and where two neighbours never "
            "visit the same value there is nothing to stitch -- a curve "
            "drawn through the gap would be interpolation presented as a "
            "measurement.\n\n"
        )
        if drifted:
            # The windows are not where they were told to be, so the gap is
            # a symptom. Saying "use a softer force constant" here would make
            # it worse, which is what the generic advice would have said.
            where = "; ".join(
                f"window {d['window']} was held at {d['centre']:g} and "
                f"sampled around {d['sampled_at']:.3g}"
                for d in drifted
            )
            reason += (
                f"{len(drifted)} of {len(ordered)} windows did not sample "
                f"where they were held: {where}. That is the gap's cause "
                "rather than a shortage of windows -- a restraint that has "
                "lost its window leaves the ground between them unvisited "
                "however many more are added.\n\n"
                "Windows started from one structure are strained at the far "
                "end of the range and relax back towards the bound state. "
                "Seed each window from a steered run near its own centre, or "
                "hold them harder with a larger `force_constant`. A softer "
                "one will make this worse."
            )
        else:
            reason += (
                f"Each pair must share at least {minimum_overlap:.0%} "
                "(`minimum_overlap`). More windows between them, or a softer "
                "force constant so each wanders further, will close a gap. "
                "Sampling for longer will not."
            )
        return {
            "pmf": None,
            "overlaps": overlaps,
            "drifted": drifted,
            "thin": thin,
            "refused": reason,
        }

    # WHAM, iterated to self-consistency. MBAR is better where it is
    # available, and this needs no dependency for the common case.
    #
    # `WHAM_TOLERANCE_KJMOL` and the iteration ceiling are module constants so
    # a study that wants to know can read what it was held to.
    kT = KB_KJ * float(temperature_K)
    everything = np.concatenate([samples[w.index] for w in ordered])
    # `_edges` pins the grid when a bootstrap resample is recombined. The
    # edges are drawn from the data's extremes, so every resample would
    # otherwise land on a slightly different grid and the bin-by-bin spread
    # would measure where the bins moved as much as what the free energy did.
    edges = (np.asarray(_edges, dtype=float) if _edges is not None
             else np.linspace(everything.min(), everything.max(), bins + 1))
    centres = 0.5 * (edges[:-1] + edges[1:])

    counts = np.array([np.histogram(samples[w.index], bins=edges)[0]
                       for w in ordered], dtype=float)
    n_per_window = counts.sum(axis=1)
    # A torsion is a circle, and the distance to a window's centre has to be
    # measured the short way round. See `displacement`.
    periodic = getattr(plan, "collective_variable", None) in PERIODIC_VARIABLES
    bias = np.array([
        0.5 * w.force_constant * displacement(centres, w.centre, periodic) ** 2
        for w in ordered
    ])

    free_energies = np.zeros(len(ordered))
    # What the loop finished at, so the caller can be told. It used to run to
    # 2000 and stop with no `else`, no flag and no field in the returned
    # dict: nine stiff windows finished at 4.2e-05 against a 1e-06 tolerance
    # and the PMF was reported as though it had settled. A number produced by
    # an iteration that ran out is not the same number as one that converged,
    # and nothing said which this was.
    residual = float("inf")
    converged = False
    for _ in range(WHAM_MAX_ITERATIONS):
        weights = np.exp((free_energies[:, None] - bias) / kT)
        denominator = (n_per_window[:, None] * weights).sum(axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            probability = counts.sum(axis=0) / denominator
        probability = np.nan_to_num(probability)
        updated = -kT * np.log(
            np.clip((probability[None, :] * np.exp(-bias / kT)).sum(axis=1),
                    1e-300, None))
        updated -= updated[0]
        residual = float(np.max(np.abs(updated - free_energies)))
        free_energies = updated
        if residual < WHAM_TOLERANCE_KJMOL:
            converged = True
            break

    # The floor belongs in the iteration, where log(0) would poison the
    # self-consistency loop, and not in what comes out of it. Carried through,
    # a bin nobody sampled left with -kT*log(1e-300) -- 1724 kJ/mol at 300 K,
    # some seven hundred times RT -- sitting in the array between neighbours
    # of eleven and thirteen, indistinguishable from a measurement. Anyone
    # plotting it saw spikes a hundred times the real barrier; anyone taking a
    # minimum or fitting a curve got a number out of a clip.
    sampled = probability > 0
    with np.errstate(divide="ignore"):
        pmf = np.where(sampled, -kT * np.log(np.clip(probability, 1e-300, None)),
                       np.nan)
    if not sampled.any():
        raise ValueError(
            "No window contributed a single sample, so there is no free "
            "energy to report. Check that the windows ran and that the "
            "coordinate they biased is the one being histogrammed."
        )
    pmf -= np.nanmin(pmf)

    # `null` rather than a number: the coordinate exists and the free energy
    # there is unknown, which JSON says exactly and every plotting library
    # understands. A sentinel would be read as data by anything that did not
    # know to look for it.
    free_energy = [None if np.isnan(value) else float(value) for value in pmf]
    unsampled = int(np.count_nonzero(~sampled))
    if unsampled:
        logger.info(
            "%d of %d bins hold no samples and are reported as unknown rather "
            "than given a value. Windows further apart than their restraints "
            "are wide leave gaps like these; the overlaps above say whether "
            "the sampled parts still join up.",
            unsampled, len(pmf),
        )

    # The range the windows actually covered, stated rather than left to be
    # worked out from the overlaps. Without it a reader takes a minimum over
    # the whole grid, which on a periodic coordinate reaches round into an
    # arc no window was placed on.
    window_centres = [w.centre for w in ordered]
    covered = (min(window_centres), max(window_centres))

    # An error bar on the curve, from resampling each window's own samples in
    # contiguous blocks. Without it the PMF was a line with no width, and a
    # binding free energy taken from it was quoted to four figures with
    # nothing saying how many of them the sampling supports.
    #
    # `minimum_overlap=0.0` inside the loop deliberately: the overlap gate is
    # a statement about the real data and has been applied above. A resample
    # dipping below it has discovered nothing about the study, and refusing
    # there would abort the error bar rather than report it.
    uncertainty = None
    if bootstrap_resamples:
        def _curve(drawn: dict[int, np.ndarray]) -> np.ndarray:
            inner = compute_pmf(
                drawn, plan, temperature_K=temperature_K, bins=bins,
                minimum_overlap=0.0, bootstrap_resamples=0, _edges=edges)
            return np.array(
                [np.nan if v is None else v
                 for v in inner["pmf"]["free_energy_kjmol"]], dtype=float)

        uncertainty = block_bootstrap(
            {w.index: samples[w.index] for w in ordered}, _curve,
            resamples=int(bootstrap_resamples), seed=bootstrap_seed,
        ).as_dict()
        if uncertainty["note"]:
            logger.info("Free-energy uncertainty: %s", uncertainty["note"])

    return {
        "pmf": {"coordinate": centres.tolist(), "free_energy_kjmol": free_energy,
                "uncertainty": uncertainty},
        "covered": [float(covered[0]), float(covered[1])],
        "summary": describe_pmf(centres, free_energy, covered,
                                periodic=periodic),
        "unsampled_bins": unsampled,
        "overlaps": overlaps,
        "refused": None,
        "temperature_K": float(temperature_K),
        "n_windows": len(ordered),
        # Stated rather than implied. A caller reading `pmf` has no other way
        # to tell an answer that settled from one that ran out of iterations.
        "converged": converged,
        "final_residual_kjmol": None if residual == float("inf") else residual,
        "wham_tolerance_kjmol": WHAM_TOLERANCE_KJMOL,
    }
