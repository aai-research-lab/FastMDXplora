"""How fast molecules move: the mean squared displacement and the diffusion
coefficient it gives.

Each molecule of the selection is followed by its centre of mass. Its mean
squared displacement over a lag ``t``, averaged over the molecules and over
every pair of frames ``t`` apart, grows in proportion to ``t`` once the
motion is diffusive, and Einstein's relation reads the coefficient from the
slope::

    MSD(t) = <|r(t0 + t) - r(t0)|^2> = 2 d D t      (d dimensions)

Three things stand between the frames and that number, each silent.

**The frames are wrapped.** A molecule that leaves the cell on one side is
stored on the other, and the frames analysed are centred on the solute
besides. Each molecule's path is put back together a step at a time: the
displacement between consecutive frames is taken to the nearest periodic
image in the later frame's cell, and the steps are summed (the scheme of von
Bülow, Bullerjahn and Hummer, J. Chem. Phys. 153, 021101 (2020), which stays
right where a barostat changes the cell). That holds only while a molecule
moves less than half the cell between two frames; frames saved too far apart
for the molecule are refused, since a step past half the cell is read as a
shorter one the other way and the coefficient comes out too small with
nothing to show for it.

**The frame of reference moves.** The frames follow the solute, so a water
molecule's stored path is its own motion less the protein's. What is
subtracted here is the motion of the centre of mass of every atom stored,
which a simulation holds still: with the solvent stored, that is the frame
the run was made in. For lipids in a bilayer the two leaflets slide over
each other, and each molecule is measured against its own leaflet's centre
(the selection's molecules above and below their middle in the first frame),
in the plane of the bilayer.

**The motion is not diffusive at once.** At short lags a molecule rattles
in its cage, and at lags near the run's length few pairs of frames are left
to average. The slope is fitted between a tenth and a half of the run unless
told otherwise, and the exponent of the growth in that window (1 for
diffusion) is reported beside it: a value well below 1 says the molecules
are still caged or confined over the lags fitted, and the coefficient is
then not one.

What is not corrected is the size of the cell. A molecule in a periodic
cell drags its own images, and the coefficient is smaller than in an
unbounded liquid by ``2.837 kT / (6 pi eta L)`` in a cubic cell of side
``L`` (Yeh and Hummer, J. Phys. Chem. B 108, 15873 (2004)), about a tenth
for water in a 4 nm cell. That needs the viscosity, which is not in the
frames, so the coefficient reported is the cell's own and says so.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis.base import Analysis, _frame_interval_ns, narrowest_width
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError

#: Molecules whose transforms are taken together.
_MOLECULES_AT_ONCE = 256
#: Frames whose steps are taken together.
_FRAMES_AT_ONCE = 64
#: The groups of molecules the coefficient's error is taken across.
_GROUPS = 5
#: The largest root mean square step along one axis, as a share of the
#: cell's narrowest width, at which a path can still be put back together:
#: at a fifth, one step in eighty reaches half the cell.
_LARGEST_STEP = 0.2


def mean_squared_displacement(path: np.ndarray) -> np.ndarray:
    """The mean squared displacement of each path at every lag.

    ``path`` is (frames, molecules, dimensions), unwrapped. The result is
    (frames, molecules): at lag ``k``, the mean over every pair of frames
    ``k`` apart of the squared distance moved, by the Fourier transform of
    the path (Calandrini et al., Collection SFN 12, 201 (2011)), which takes
    the time of one transform where the pairs taken one by one take the
    square of the frames.
    """
    path = np.asarray(path, dtype=np.float64)
    frames = path.shape[0]
    counts = (frames - np.arange(frames))[:, None]
    squared = (path ** 2).sum(axis=2)
    # The sum over pairs of r(i)^2 + r(i+k)^2, from running totals.
    total = 2.0 * squared.sum(axis=0)
    ends = np.zeros_like(squared)
    ends[1:] = np.cumsum(squared[:-1] + squared[:0:-1], axis=0)
    both = total[None, :] - ends
    # The sum over pairs of r(i) . r(i+k), an autocorrelation.
    size = 2 * frames
    transformed = np.fft.rfft(path, n=size, axis=0)
    products = np.fft.irfft((transformed * transformed.conj()).real, n=size,
                            axis=0)[:frames].sum(axis=2)
    found = np.clip((both - 2.0 * products) / counts, 0.0, None)
    found[0] = 0.0  # nothing moves in no time, whatever the rounding leaves
    return found


def _steps_to_the_nearest_image(moved: np.ndarray, box: np.ndarray | None) -> np.ndarray:
    """``moved`` (steps, n, 3), each taken to the nearest periodic image in
    the cell (steps, 3, 3) of the later frame."""
    if box is None:
        return moved
    inverse = np.linalg.inv(box)
    around = np.round(np.einsum("sni,sij->snj", moved, inverse))
    return moved - np.einsum("sni,sij->snj", around, box)


class Diffusion(Analysis):
    """Mean squared displacement of the selection's molecules, and their
    diffusion coefficient.

    Options
    -------
    plane : str, default "auto"
        ``xyz`` for motion in three dimensions, ``xy`` for motion in the
        plane across z (lipids in a bilayer). ``auto`` is ``xy`` where the
        selection is the lipids of a bilayer and ``xyz`` otherwise.
    relative_to : str, default "auto"
        What each molecule's motion is measured against. ``system`` is the
        centre of mass of every atom stored; ``leaflet`` is the centre of
        the selection's molecules on the same side of the bilayer. ``auto``
        is ``leaflet`` where the selection is the lipids of a bilayer and
        ``system`` otherwise.
    fit_from, fit_to : float, default 0.1 and 0.5
        The lags the line is fitted between, as shares of the run's length.
    max_molecules : int, default 5000
        The most molecules followed, taken evenly through the selection.

    Output
    ------
    ``diffusion.dat`` -- comma-separated, one row per lag: ``lag_ns``,
    ``msd_nm2`` (the mean over the molecules) and ``sem_nm2`` (its standard
    error across five groups of molecules, empty where there are too few).
    The coefficient is in ``options.json`` (``findings.diffusion``), in
    nm^2/ns, which is 1e-5 cm^2/s.
    """

    name = "diffusion"
    requires_continuous_trajectory = True
    description = "Mean squared displacement"
    default_selection = "water"
    #: Which molecules a study wants followed is not in its frames, and the
    #: solvent is stored only where a study asks for it.
    requires_naming = True

    PLANES = ("auto", "xyz", "xy")
    RELATIVE_TO = ("auto", "system", "leaflet")

    def __init__(self, *, plane: str = "auto", relative_to: str = "auto",
                 fit_from: float = 0.1, fit_to: float = 0.5,
                 max_molecules: int = 5000, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if plane not in self.PLANES:
            raise StudyError(
                f"`plane` is one of {', '.join(self.PLANES)}; it was {plane!r}.",
                code="analysis.option.inapplicable")
        if relative_to not in self.RELATIVE_TO:
            raise StudyError(
                f"`relative_to` is one of {', '.join(self.RELATIVE_TO)}; it was "
                f"{relative_to!r}.", code="analysis.option.inapplicable")
        self.plane, self.relative_to = plane, relative_to
        self.fit_from, self.fit_to = float(fit_from), float(fit_to)
        self.max_molecules = int(max_molecules)
        if not 0.0 <= self.fit_from < self.fit_to <= 1.0:
            raise StudyError(
                "`fit_from` and `fit_to` are the lags the line is fitted between, "
                "as shares of the run's length, the first below the second and "
                f"both from 0 to 1; they were {self.fit_from:g} and {self.fit_to:g}.",
                code="analysis.option.out_of_range")
        if self.max_molecules < 1:
            raise StudyError(
                f"`max_molecules` is at least 1; it was {self.max_molecules}.",
                code="analysis.option.out_of_range")
        self.options.update(plane=self.plane, relative_to=self.relative_to,
                            fit_from=self.fit_from, fit_to=self.fit_to,
                            max_molecules=self.max_molecules)
        self._window: tuple[float, float, float, float] | None = None

    # ------------------------------------------------------------------
    def _molecules(self, traj: md.Trajectory, chosen: np.ndarray) -> list[np.ndarray]:
        """The selection's atoms, molecule by molecule, in the topology's order."""
        wanted = np.zeros(traj.n_atoms, dtype=bool)
        wanted[chosen] = True
        found = []
        for molecule in traj.topology.find_molecules():
            atoms = np.array(sorted(a.index for a in molecule), dtype=int)
            atoms = atoms[wanted[atoms]]
            if len(atoms):
                found.append(atoms)
        found.sort(key=lambda atoms: int(atoms[0]))
        return found

    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        from fastmdxplora.lipids import is_bilayer, is_lipid

        interval = _frame_interval_ns(traj)
        if interval is None:
            raise StudyError(
                "The frames carry no clock, so how far apart they are in time is "
                "not known, and a diffusion coefficient is a distance squared over "
                "a time. Give the trajectory's saving interval (`saving_interval_ps`).",
                code="analysis.data.absent")
        if traj.n_frames < 20:
            raise StudyError(
                f"A mean squared displacement needs a run of frames; there are "
                f"{traj.n_frames}, and 20 is the fewest a slope is fitted to.",
                code="analysis.sampling.too_few_frames")
        chosen = self.select_atoms(traj)
        if not len(chosen):
            raise StudyError(
                f"The selection {self.selection!r} matched no atoms. Water and "
                "ions are in the frames only where the study stored them "
                "(`simulation.save_selection: all`).", code="analysis.selection.empty")
        topology = traj.topology
        molecules = self._molecules(traj, chosen)
        every = len(molecules)
        if every > self.max_molecules:
            keep = np.unique(np.linspace(0, every - 1, self.max_molecules).astype(int))
            molecules = [molecules[k] for k in keep]
        masses = np.array([a.element.mass if a.element is not None else 12.0
                           for a in topology.atoms], dtype=np.float64)

        names = {topology.atom(int(atoms[0])).residue.name for atoms in molecules}
        lipids = (all(is_lipid(name) for name in names)
                  and is_bilayer(r.name for r in topology.residues))
        plane = self.plane if self.plane != "auto" else ("xy" if lipids else "xyz")
        relative_to = (self.relative_to if self.relative_to != "auto"
                       else ("leaflet" if lipids else "system"))
        dimensions = 2 if plane == "xy" else 3

        followed_mass = float(sum(masses[atoms].sum() for atoms in molecules))
        if (relative_to == "system" and every < 20
                and followed_mass > 0.5 * float(masses.sum())):
            raise StudyError(
                f"The {every} molecule(s) followed are "
                f"{followed_mass / masses.sum():.0%} of the mass in the frames, so "
                "there is little else to measure their motion against: the frames "
                "follow the solute, and what moves it is not stored. Store the "
                "solvent (`simulation.save_selection: all`) to measure a solute's "
                "diffusion, or name the molecules that move through the rest.",
                code="analysis.system.inapplicable")

        box = (None if traj.unitcell_vectors is None
               else np.asarray(traj.unitcell_vectors, dtype=np.float64))
        order = np.concatenate(molecules)
        starts = np.concatenate([[0], np.cumsum([len(a) for a in molecules])[:-1]])
        weight = masses[order]
        each = np.add.reduceat(weight, starts)

        def centres(frames: slice) -> np.ndarray:
            held = traj.xyz[frames][:, order].astype(np.float64) * weight[None, :, None]
            return np.add.reduceat(held, starts, axis=1) / each[None, :, None]

        # The molecules' steps and the system's, a run of frames at a time.
        steps = np.empty((traj.n_frames - 1, len(molecules), 3))
        drift = np.zeros((traj.n_frames - 1, 3))
        share = masses / masses.sum()
        for begin in range(0, traj.n_frames - 1, _FRAMES_AT_ONCE):
            stop = min(begin + _FRAMES_AT_ONCE, traj.n_frames - 1)
            later = None if box is None else box[begin + 1:stop + 1]
            centre = centres(slice(begin, stop + 1))
            steps[begin:stop] = _steps_to_the_nearest_image(np.diff(centre, axis=0), later)
            if relative_to == "system":
                moved = np.diff(traj.xyz[begin:stop + 1].astype(np.float64), axis=0)
                drift[begin:stop] = np.einsum(
                    "sni,n->si", _steps_to_the_nearest_image(moved, later), share)

        widths = narrowest_width(traj)
        along = np.sqrt((steps ** 2).mean(axis=(0, 1)))
        if widths is not None:
            narrowest = float(np.min(widths))
            if float(along.max()) > _LARGEST_STEP * narrowest:
                raise StudyError(
                    f"Between two frames ({interval * 1000:g} ps apart) the molecules "
                    f"move {along.max():.2f} nm along one axis, root mean square, "
                    f"which is over a fifth of the cell's narrowest width "
                    f"({narrowest:.2f} nm). A step past half the cell is stored as a "
                    "shorter one the other way, so the paths cannot be put back "
                    "together and the coefficient would come out too small. Save "
                    "frames closer together, or simulate a larger cell.",
                    code="analysis.sampling.too_few_frames")

        if relative_to == "leaflet":
            first = centres(slice(0, 1))[0, :, 2]
            upper = first >= np.median(first)
            for side in (upper, ~upper):
                if side.any():
                    steps[:, side] -= steps[:, side].mean(axis=1, keepdims=True)
            measured = ("the centre of the selection's molecules in the same leaflet "
                        f"({int(upper.sum())} above the middle in the first frame, "
                        f"{int((~upper).sum())} below)")
        else:
            steps -= drift[:, None, :]
            measured = f"the centre of mass of the {traj.n_atoms:,} atoms stored"

        frames = traj.n_frames
        msd = np.empty((frames, len(molecules)))
        for begin in range(0, len(molecules), _MOLECULES_AT_ONCE):
            some = slice(begin, begin + _MOLECULES_AT_ONCE)
            path = np.zeros((frames, steps[:, some].shape[1], dimensions))
            path[1:] = np.cumsum(steps[:, some, :dimensions], axis=0)
            msd[:, some] = mean_squared_displacement(path)
        lag = np.arange(frames) * interval
        mean = msd.mean(axis=1)

        low = max(1, int(np.ceil(self.fit_from * (frames - 1))))
        high = max(low + 2, int(np.floor(self.fit_to * (frames - 1))))
        if high > frames - 1:
            raise StudyError(
                f"The lags fitted, from {self.fit_from:g} to {self.fit_to:g} of the "
                f"run, hold fewer than three of its {frames} frames.",
                code="analysis.sampling.too_few_frames")
        window = slice(low, high + 1)

        def slope_of(values: np.ndarray) -> tuple[float, float]:
            slope, intercept = np.polyfit(lag[window], values[window], 1)
            return float(slope), float(intercept)

        slope, intercept = slope_of(mean)
        coefficient = slope / (2 * dimensions)
        positive = mean[window] > 0
        exponent = (float(np.polyfit(np.log(lag[window][positive]),
                                     np.log(mean[window][positive]), 1)[0])
                    if positive.sum() >= 3 else float("nan"))

        groups = [np.arange(k, len(molecules), _GROUPS) for k in range(_GROUPS)]
        sem_of_msd = np.full(frames, np.nan)
        error = None
        if len(molecules) >= 5 * _GROUPS:
            by_group = np.stack([msd[:, group].mean(axis=1) for group in groups], axis=1)
            sem_of_msd = by_group.std(axis=1, ddof=1) / np.sqrt(_GROUPS)
            each_slope = [slope_of(by_group[:, k])[0] / (2 * dimensions)
                          for k in range(_GROUPS)]
            error = float(np.std(each_slope, ddof=1) / np.sqrt(_GROUPS))
            error_said = (f"the standard error across {_GROUPS} groups of molecules, "
                          "which move in one liquid and are not wholly independent")
        else:
            error_said = (f"withheld: {len(molecules)} molecule(s) are too few to "
                          f"take an error across (it is taken across {_GROUPS} groups "
                          f"of at least five)")

        self._window = (float(lag[low]), float(lag[high]), slope, intercept)
        self.findings["diffusion"] = {
            "coefficient_nm2_per_ns": coefficient,
            "standard_error_nm2_per_ns": error,
            "error": error_said,
            "unit": "nm^2/ns, which is 1e-5 cm^2/s",
            "dimensions": dimensions,
            "plane": plane,
            "fitted_from_ns": float(lag[low]),
            "fitted_to_ns": float(lag[high]),
            "exponent": exponent,
            "exponent_meaning": (
                "how the displacement grows with the lag over the lags fitted: 1 "
                "is diffusion, and a value well below 1 is a molecule still caged "
                "or confined, whose coefficient is not one"),
        }
        self.findings["molecules"] = {
            "followed": len(molecules), "in_the_selection": every,
            "by": "each molecule's centre of mass over the selection's atoms in it",
            "residues": sorted(names)[:12],
        }
        self.findings["relative_to"] = measured
        self.findings["paths"] = {
            "put_together": (
                "each step between consecutive frames taken to the nearest periodic "
                "image in the later frame's cell, and summed (von Bülow, Bullerjahn "
                "and Hummer, J. Chem. Phys. 153, 021101 (2020))"
                if box is not None else "as stored: the frames have no periodic cell"),
            "largest_rms_step_along_an_axis_nm": float(along.max()),
            "frames_apart_ns": interval,
        }
        self.findings["cell_size"] = (
            "Not corrected for the size of the periodic cell: the coefficient is "
            "below that of an unbounded liquid by 2.837 kT / (6 pi eta L) in a cubic "
            "cell of side L (Yeh and Hummer, J. Phys. Chem. B 108, 15873 (2004)), "
            "which needs the viscosity.")
        if np.isfinite(exponent) and exponent < 0.8:
            self.findings["not_yet_diffusive"] = (
                f"Over the lags fitted the displacement grows as the lag to the "
                f"power {exponent:.2f}, not 1: the molecules are caged or confined "
                "over these times, and the slope is not a diffusion coefficient. "
                "A longer run, or later lags (`fit_from`, `fit_to`), may reach "
                "the diffusive regime.")
        return pd.DataFrame({"lag_ns": lag, "msd_nm2": mean, "sem_nm2": sem_of_msd})

    # ------------------------------------------------------------------
    def default_xlabel(self) -> str | None:
        return "Lag (ns)"

    def default_ylabel(self) -> str | None:
        return "Mean squared displacement (nm$^2$)"

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        lag, mean = result["lag_ns"].to_numpy(), result["msd_nm2"].to_numpy()
        ax.plot(lag, mean, linewidth=1.2, label="mean over the molecules")
        sem = result["sem_nm2"].to_numpy()
        if np.isfinite(sem).any():
            ax.fill_between(lag, mean - sem, mean + sem, alpha=0.25, linewidth=0)
        if self._window is not None:
            start, end, slope, intercept = self._window
            fitted = np.array([start, end])
            found = self.findings["diffusion"]
            ax.plot(fitted, slope * fitted + intercept, linewidth=1.6, linestyle="--",
                    color="0.2",
                    label=f"fitted: D = {found['coefficient_nm2_per_ns']:.3g} nm$^2$/ns")
            ax.legend(loc="best", fontsize=7.5)


register_analysis(Diffusion.name, Diffusion)
