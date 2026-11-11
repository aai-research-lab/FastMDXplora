"""Pore radius along a channel: how wide the way through a protein is.

A channel, a porin or a transporter caught open has a way through it, and
what passes depends on how wide that way is at its narrowest. This is the
measure HOLE made standard (Smart, Neduvelil, Wang, Wallace and Sansom,
J Mol Graph 14, 354 (1996)): at each height along the pore's axis, the
largest sphere that fits among the protein's atoms with its centre in that
plane. The spheres' centres trace the pore's centre line, and their radii
against height are its profile.

**How it is found.** The path starts at the centre of the protein (or of
`start_selection`) and is followed plane by plane in both directions, each
plane's search beginning where the last one ended. In each plane the centre
is moved, by steps of 0.1 nm halved down to 0.005 nm, to wherever the
nearest atom's van der Waals surface is furthest, until no step widens the
sphere. HOLE does that search by Monte Carlo; this one tries a fixed set of
steps, so the same frames always give the same profile. The path ends in a
direction where the sphere reaches `end_radius_nm`, which is bulk solvent
rather than pore.

**What it is not told, and cannot find out.** Whether the protein is a
channel at all. Followed through a protein with no pore, the path squeezes
between packed helices and reports a radius near zero, which is true and
means nothing; so this analysis is never run by default, and runs where a
study names it. A path can also leave a pore sideways through a gap in its
wall, as HOLE's can; the profile then ends early, and `findings.pore` says
over which heights it was followed.

**Each frame is put in the protein's own frame first.** A channel drifts and
turns in its bilayer, so a profile against height in the box would smear.
Each frame's protein is fitted by least squares onto the first frame's, and
the axis, the heights and the centre line are those of the first frame.

Only the atoms selected line the pore (the protein, by default): water, ions
and lipid are what passes through or surrounds it, as HOLE leaves them out.
"""

from __future__ import annotations

import warnings
from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.analysis.plotting import (
    closes_what_it_opens,
    drawn_in,
    new_figure,
    save_figure,
    sized_for,
)
from fastmdxplora.refusals import StudyError

__all__ = ["PoreProfile", "widest_path", "TOO_NARROW_FOR_WATER_NM",
           "ONE_WATER_WIDE_NM"]

#: Below this radius a water molecule does not fit, and up to the next there
#: is room for one abreast: the radii HOLE colours its surface by (1.15 and
#: 2.30 angstrom).
TOO_NARROW_FOR_WATER_NM = 0.115
ONE_WATER_WIDE_NM = 0.230

#: Van der Waals radii (Bondi 1964; H after Rowland and Taylor 1996), nm, as
#: the bilayer's cross section takes them.
_VDW_NM = {"H": 0.110, "C": 0.170, "N": 0.155, "O": 0.152, "S": 0.180,
           "P": 0.180, "SE": 0.190}
_VDW_DEFAULT_NM = 0.170

#: The steps the centre is moved by in a plane, largest first, nm.
_STEPS_NM = (0.1, 0.05, 0.025, 0.01, 0.005)
#: The eight ways it is moved at each step.
_WAYS = np.array([[1, 0], [-1, 0], [0, 1], [0, -1],
                  [1, 1], [1, -1], [-1, 1], [-1, -1]], dtype=float)
_WAYS[4:] /= np.sqrt(2.0)
#: Frames searched together.
_FRAMES_AT_ONCE = 64
#: What keeps one frame's atoms from answering for another's in the tree the
#: frames share: frames are this far apart along a fourth coordinate, nm.
_BETWEEN_FRAMES_NM = 1.0e4


def _symbol(atom: Any) -> str:
    symbol = getattr(getattr(atom, "element", None), "symbol", None)
    if symbol:
        return str(symbol).upper()
    return "".join(ch for ch in atom.name if ch.isalpha())[:1].upper()


def _fitted(xyz: np.ndarray, on: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Every frame of ``xyz`` (frames, atoms, 3) moved and turned so that its
    atoms ``on`` fit ``reference`` (their place in the reference frame) by
    least squares (Kabsch); moved only where fewer than three are fitted."""
    xyz = np.asarray(xyz, dtype=np.float64)
    here = xyz[:, on].mean(axis=1)
    there = reference.mean(axis=0)
    if len(on) < 3:
        return xyz - here[:, None, :] + there
    covariance = np.einsum("fai,aj->fij", xyz[:, on] - here[:, None, :],
                           reference - there)
    left, _, right = np.linalg.svd(covariance)
    sign = np.sign(np.linalg.det(left @ right))
    sign[sign == 0] = 1.0
    flip = np.zeros((len(xyz), 3, 3))
    flip[:, 0, 0] = flip[:, 1, 1] = 1.0
    flip[:, 2, 2] = sign
    rotation = left @ flip @ right
    return (xyz - here[:, None, :]) @ rotation + there


def _turn_onto_z(axis: np.ndarray) -> np.ndarray:
    """The rotation whose rows are two directions across ``axis`` and then
    ``axis`` itself: ``x @ R.T`` has the axis along z."""
    axis = axis / np.linalg.norm(axis)
    across = np.eye(3)[int(np.argmin(np.abs(axis)))]
    u = np.cross(axis, across)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    return np.stack([u, v, axis])


def widest_path(xyz: np.ndarray, radii: np.ndarray, heights: np.ndarray,
                start: np.ndarray, end_radius: float) -> tuple[np.ndarray, np.ndarray]:
    """The widest sphere at each height, followed from ``start``.

    ``xyz`` is (frames, atoms, 3) with the pore's axis along z, ``radii`` each
    atom's radius, ``heights`` the planes' z in rising order and ``start`` a
    point (x, y, z) in the pore. Returns the spheres' radii (frames, planes)
    and their centres across the axis (frames, planes, 2); both are NaN
    beyond where a frame's sphere reached ``end_radius``. A radius is zero
    where the atoms' surfaces meet and leave no room for a sphere.
    """
    xyz = np.asarray(xyz, dtype=np.float64)
    heights = np.asarray(heights, dtype=np.float64)
    radius = np.full((len(xyz), len(heights)), np.nan)
    centre = np.full((len(xyz), len(heights), 2), np.nan)
    for begin in range(0, len(xyz), _FRAMES_AT_ONCE):
        stop = begin + _FRAMES_AT_ONCE
        radius[begin:stop], centre[begin:stop] = _widest_together(
            xyz[begin:stop], radii, heights, start, end_radius)
    return radius, centre


def _widest_together(chunk: np.ndarray, radii: np.ndarray, heights: np.ndarray,
                     start: np.ndarray, end_radius: float
                     ) -> tuple[np.ndarray, np.ndarray]:
    """:func:`widest_path` of a few frames, searched together: one tree for
    each kind of atom holds every frame's, the frames kept apart along a
    fourth coordinate, so a step of the search is one question of each tree
    however many frames are asked."""
    from scipy.spatial import cKDTree

    count, n_planes = len(chunk), len(heights)
    radius = np.full((count, n_planes), np.nan)
    centre = np.full((count, n_planes, 2), np.nan)
    first = int(np.argmin(np.abs(heights - start[2])))
    fourth = np.arange(count, dtype=np.float64) * _BETWEEN_FRAMES_NM
    trees = []
    for kind in np.unique(radii):
        of_kind = chunk[:, radii == kind]
        points = np.concatenate(
            [of_kind, np.broadcast_to(fourth[:, None, None],
                                      (count, of_kind.shape[1], 1))], axis=2)
        trees.append((float(kind), cKDTree(points.reshape(-1, 4))))

    def clearance(frames: np.ndarray, across: np.ndarray, height: float) -> np.ndarray:
        """How far each point is from the nearest atom's surface."""
        asked = np.empty(across.shape[:-1] + (4,))
        asked[..., :2] = across
        asked[..., 2] = height
        asked[..., 3] = fourth[frames].reshape((-1,) + (1,) * (across.ndim - 2))
        best = np.full(across.shape[:-1], np.inf)
        flat = asked.reshape(-1, 4)
        for kind, tree in trees:
            distance, _ = tree.query(flat, k=1)
            best = np.minimum(best, distance.reshape(best.shape) - kind)
        return best

    def widened(frames: np.ndarray, at: np.ndarray, height: float
                ) -> tuple[np.ndarray, np.ndarray]:
        """Each frame's centre moved to where its sphere is widest."""
        at = at.copy()
        wide = clearance(frames, at, height)
        for step in _STEPS_NM:
            # At the largest step, far enough to reach `end_radius` from
            # anywhere; then only as far as the step before allows.
            most = int(np.ceil(2 * end_radius / step)) + 4 if step == _STEPS_NM[0] else 4
            moving = np.flatnonzero(wide < end_radius)
            for _ in range(most):
                if not len(moving):
                    break
                tried = at[moving, None, :] + step * _WAYS[None, :, :]
                found = clearance(frames[moving], tried, height)
                best = np.argmax(found, axis=1)
                wider = found[np.arange(len(moving)), best]
                better = wider > wide[moving] + 1e-9
                moved = moving[better]
                at[moved] = tried[better, best[better]]
                wide[moved] = wider[better]
                moving = moved[wide[moved] < end_radius]
        return at, wide

    frames = np.arange(count)
    at, wide = widened(frames, np.tile(start[:2], (count, 1)), heights[first])
    enclosed = wide < end_radius
    # Where van der Waals surfaces meet there is no sphere: a radius of
    # zero, not the depth of the overlap.
    radius[frames[enclosed], first] = np.maximum(wide[enclosed], 0.0)
    centre[frames[enclosed], first] = at[enclosed]
    for planes in (range(first + 1, n_planes), range(first - 1, -1, -1)):
        going, here = frames[enclosed], at[enclosed]
        for plane in planes:
            if not len(going):
                break
            here, wide = widened(going, here, heights[plane])
            inside = wide < end_radius
            going, here = going[inside], here[inside]
            radius[going, plane] = np.maximum(wide[inside], 0.0)
            centre[going, plane] = here
    return radius, centre


class PoreProfile(Analysis):
    """The narrowest radius of the pore through a protein, per frame, and its
    radius against height along the pore.

    Parameters
    ----------
    selection : str, default "protein"
        The atoms that line the pore.
    axis : {"auto", "z", "x", "y", "principal"}, default "auto"
        The direction the pore runs in, in the first frame analysed: ``z``
        where the system has a bilayer (its normal), and otherwise
        ``principal``: of the selection's three principal axes, the one it is
        most nearly symmetric about (the two others alike in spread), which
        is the axis subunits are arranged round, or a barrel's or a helix's
        own.
    start_selection : str, default "auto"
        Atoms whose centre is a point in the pore, where the path starts.
        ``auto`` is the selection's own centre, which is in the pore of a
        channel whose subunits are arranged round it and not in the pores of
        one, such as an aquaporin, with a pore in each subunit: name one
        subunit, or the residues that line its pore.
    step_nm : float, default 0.05
        The spacing of the planes along the axis.
    end_radius_nm : float, default 1.0
        The radius at which the path has left the pore for bulk solvent.

    Output
    ------
    ``pore_profile.dat`` -- one column, nm, one row per frame: the pore's
    narrowest radius (zero where the pore is shut, the atoms' surfaces
    meeting; NaN for a frame with no enclosed pore).
    ``pore_radius.dat`` -- comma-separated, one row per plane: ``height_nm``
    (along the axis, from the selection's centre in the first frame),
    ``mean_nm``, ``sd_nm``, ``min_nm``, ``max_nm`` over the frames and
    ``frames``, how many frames' paths reached the plane.
    ``pore_path.npz`` -- ``height_nm`` (planes), ``radius_nm`` (frames,
    planes) and ``centre_nm`` (frames, planes, 3), the spheres' centres in
    the first frame's coordinates, with ``axis`` and ``origin_nm``.
    ``pore_radius.png`` -- the mean profile with the range over the frames.
    """

    name = "pore_profile"
    description = "Narrowest radius of the pore"
    time_series = True
    reweightable = (None, "Narrowest pore radius (nm)")
    default_selection = "protein"
    #: Whether a protein is a channel is not in the trajectory, and a pore
    #: profile of one that is not is a number about nothing.
    requires_naming = True

    AXES = ("auto", "z", "x", "y", "principal")

    def __init__(self, *, axis: str = "auto", start_selection: str = "auto",
                 step_nm: float = 0.05, end_radius_nm: float = 1.0,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if axis not in self.AXES:
            raise StudyError(
                f"The pore's `axis` is one of {', '.join(self.AXES)}; it was {axis!r}.",
                code="analysis.option.inapplicable")
        self.axis = axis
        self.start_selection = str(start_selection or "auto")
        self.step_nm = float(step_nm)
        self.end_radius_nm = float(end_radius_nm)
        if not 0.005 <= self.step_nm <= 0.5:
            raise StudyError(
                f"`step_nm` is the spacing of the planes along the pore, between "
                f"0.005 and 0.5 nm; it was {self.step_nm:g}.",
                code="analysis.option.inapplicable")
        if not 0.3 <= self.end_radius_nm <= 5.0:
            raise StudyError(
                f"`end_radius_nm` is the radius at which the path has left the pore, "
                f"between 0.3 and 5 nm; it was {self.end_radius_nm:g}.",
                code="analysis.option.inapplicable")
        self.options.update(axis=self.axis, start_selection=self.start_selection,
                            step_nm=self.step_nm, end_radius_nm=self.end_radius_nm)
        self._profile: pd.DataFrame | None = None
        self._path: dict[str, np.ndarray] | None = None
        self._traj_for_plot: md.Trajectory | None = None

    # ------------------------------------------------------------------
    def _direction(self, traj: md.Trajectory, lining: np.ndarray) -> tuple[np.ndarray, str]:
        """The pore's axis in the first frame, and how it was decided."""
        named = {"x": 0, "y": 1, "z": 2}
        if self.axis in named:
            return np.eye(3)[named[self.axis]], f"{self.axis}, as asked"
        if self.axis == "auto":
            from fastmdxplora.lipids import is_bilayer

            if (traj.unitcell_lengths is not None
                    and is_bilayer(r.name for r in traj.topology.residues)):
                return np.eye(3)[2], "z, the bilayer's normal"
        first = traj.xyz[0, lining].astype(np.float64)
        _, spread, rows = np.linalg.svd(first - first.mean(axis=0), full_matrices=False)
        # Subunits arranged round a pore, or a single barrel or helix, are
        # alike in every direction across it: the pore runs along the
        # principal axis whose spread is unlike the other two's.
        along = 0 if spread[0] - spread[1] >= spread[1] - spread[2] else 2
        return rows[along], (
            "the principal axis of the selection in the first frame that it is "
            "most nearly symmetric about"
            + (", as asked" if self.axis == "principal"
               else " (the system has no bilayer to give a normal)"))

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        topology = traj.topology
        lining = self.select_atoms(traj)
        if len(lining) < 20:
            raise StudyError(
                f"The selection {self.selection!r} matched {len(lining)} atom(s), "
                "too few to enclose a pore.", code="analysis.selection.empty")
        radii = np.array([_VDW_NM.get(_symbol(topology.atom(int(i))), _VDW_DEFAULT_NM)
                          for i in lining])
        alpha = np.array([k for k, i in enumerate(lining)
                          if topology.atom(int(i)).name == "CA"], dtype=int)
        on = alpha if len(alpha) >= 3 else np.arange(len(lining))
        first = traj.xyz[0, lining].astype(np.float64)
        origin = first.mean(axis=0)
        axis, how = self._direction(traj, lining)
        turn = _turn_onto_z(axis)

        if self.start_selection != "auto":
            try:
                begun = topology.select(self.start_selection)
            except Exception as exc:  # noqa: BLE001 - MDTraj's parser raises several
                raise StudyError(
                    f"`start_selection` {self.start_selection!r} is not a selection "
                    f"MDTraj reads: {exc}", code="analysis.selection.invalid") from exc
            if not len(begun):
                raise StudyError(
                    f"`start_selection` {self.start_selection!r} matched no atoms, so "
                    "there is nowhere to start the path.", code="analysis.selection.empty")
            start = (traj.xyz[0, begun].astype(np.float64).mean(axis=0) - origin) @ turn.T
        else:
            start = np.zeros(3)

        along = (first - origin) @ turn.T
        low = np.floor(along[:, 2].min() / self.step_nm) * self.step_nm
        high = np.ceil(along[:, 2].max() / self.step_nm) * self.step_nm
        heights = np.round(np.arange(low, high + self.step_nm / 2, self.step_nm), 6)

        radius = np.empty((traj.n_frames, len(heights)))
        centre = np.empty((traj.n_frames, len(heights), 2))
        for begin in range(0, traj.n_frames, 4 * _FRAMES_AT_ONCE):
            stop = begin + 4 * _FRAMES_AT_ONCE
            fitted = _fitted(traj.xyz[begin:stop, lining], on, first[on])
            radius[begin:stop], centre[begin:stop] = widest_path(
                (fitted - origin) @ turn.T, radii, heights, start, self.end_radius_nm)

        enclosed = np.isfinite(radius).any(axis=1)
        self.findings["axis"] = {"along": how, "vector": [round(float(v), 6) for v in axis]}
        self.findings["fitted_on"] = {
            "selection": self.selection, "atoms": int(len(on)),
            "of": "its alpha carbons" if on is alpha else "all its atoms",
            "to": "the first frame analysed"}
        self.findings["method"] = (
            "The largest sphere among the selection's van der Waals surfaces "
            "(Bondi's radii) with its centre in each plane, as HOLE defines it "
            "(Smart et al., J Mol Graph 14, 354 (1996)), found by a fixed "
            "pattern of steps down to 0.005 nm in place of HOLE's Monte Carlo.")
        if not enclosed.any():
            raise StudyError(
                f"No pore was found: from the centre of "
                f"{self.selection if self.start_selection == 'auto' else self.start_selection!r}, "
                "a sphere can grow "
                f"past {self.end_radius_nm:g} nm without meeting the selection's "
                "atoms, in every frame, so the start is in the open and not in a "
                "pore. Name atoms that line the pore in `start_selection`, or a "
                "narrower `selection`.", code="analysis.system.inapplicable")

        narrowest = np.full(traj.n_frames, np.nan)
        narrowest[enclosed] = np.nanmin(radius[enclosed], axis=1)
        reached = np.isfinite(radius)
        counted = reached.sum(axis=0)
        # A plane few frames' paths reached says little about the pore.
        kept = counted >= max(1, int(np.ceil(0.5 * enclosed.sum())))
        with warnings.catch_warnings():
            # A plane no frame's path reached is NaN, as it should be.
            warnings.simplefilter("ignore", category=RuntimeWarning)
            profile = pd.DataFrame({
                "height_nm": heights,
                "mean_nm": np.nanmean(radius, axis=0),
                "sd_nm": np.nanstd(radius, axis=0),
                "min_nm": np.nanmin(radius, axis=0),
                "max_nm": np.nanmax(radius, axis=0),
                "frames": counted,
            })
        self._profile = profile
        across = np.concatenate([centre, np.broadcast_to(
            heights[None, :, None], centre.shape[:2] + (1,))], axis=2)
        self._path = {
            "height_nm": heights.astype(np.float32),
            "radius_nm": radius.astype(np.float32),
            "centre_nm": (across @ turn + origin).astype(np.float32),
            "axis": axis.astype(np.float32),
            "origin_nm": origin.astype(np.float32),
        }

        followed = profile[kept]
        self.findings["pore"] = {
            "from_nm": float(followed["height_nm"].min()),
            "to_nm": float(followed["height_nm"].max()),
            "heights": "along the axis, from the selection's centre in the first frame",
            "step_nm": self.step_nm,
            "end_radius_nm": self.end_radius_nm,
            "frames_with_a_pore": int(enclosed.sum()),
            "frames": int(traj.n_frames),
        }
        tightest = followed.loc[followed["mean_nm"].idxmin()]
        plane = int(tightest.name)
        self.findings["narrowest"] = {
            "height_nm": float(tightest["height_nm"]),
            "mean_radius_nm": float(tightest["mean_nm"]),
            "lined_by": self._lining(traj, lining, radii, radius[:, plane],
                                     self._path["centre_nm"][:, plane], on, first[on]),
        }
        wide_enough = narrowest[enclosed] >= TOO_NARROW_FOR_WATER_NM
        self.findings["open_to_water"] = {
            "share_of_frames": float(wide_enough.mean()),
            "meaning": (f"the share of frames whose narrowest radius is at least "
                        f"{TOO_NARROW_FOR_WATER_NM} nm, below which a water molecule "
                        "does not fit (HOLE's threshold). Room for a water is not "
                        "conduction: a pore lined by hydrophobic side chains can be "
                        "wide enough and dry."),
        }
        if not enclosed.all():
            self.findings["frames_without_a_pore"] = (
                f"In {int((~enclosed).sum())} of {traj.n_frames} frames the start was "
                "in the open, and they have no radius.")
        return narrowest

    def _lining(self, traj: md.Trajectory, lining: np.ndarray, radii: np.ndarray,
                wide: np.ndarray, at: np.ndarray, on: np.ndarray,
                reference: np.ndarray) -> list[dict[str, Any]]:
        """The residues whose atoms touch the sphere at the narrowest plane
        (within 0.1 nm of its surface), most often first."""
        frames = np.flatnonzero(np.isfinite(wide))
        counts: dict[int, int] = {}
        residue_of = np.array([traj.topology.atom(int(i)).residue.index for i in lining])
        for begin in range(0, len(frames), 4 * _FRAMES_AT_ONCE):
            some = frames[begin:begin + 4 * _FRAMES_AT_ONCE]
            fitted = _fitted(traj.xyz[some][:, lining], on, reference)
            distance = np.linalg.norm(fitted - at[some][:, None, :], axis=2)
            touching = distance - radii[None, :] <= wide[some][:, None] + 0.1
            for row in touching:
                for residue in np.unique(residue_of[row]):
                    counts[int(residue)] = counts.get(int(residue), 0) + 1
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        said = []
        for index, count in ranked:
            residue = traj.topology.residue(index)
            said.append({"residue": f"{residue.name}{residue.resSeq}",
                         "chain": int(residue.chain.index),
                         "share_of_frames": round(count / len(frames), 3)})
        return said

    # ------------------------------------------------------------------
    def default_ylabel(self) -> str | None:
        return "Narrowest radius (nm)"

    def default_xlabel(self) -> str | None:
        if self._traj_for_plot is None:
            return "Frame"
        return self.frame_axis(self._traj_for_plot)[1]

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        if self._traj_for_plot is not None:
            x = self.frame_axis(self._traj_for_plot)[0]
        else:
            x = np.arange(len(result))
        ax.plot(x, result, linewidth=1.2)
        ax.axhline(TOO_NARROW_FOR_WATER_NM, linewidth=0.8, linestyle="--", color="0.4")

    def run(self, traj: md.Trajectory):
        """The narrowest radius, and the profile and path written beside it."""
        self._profile = self._path = None
        self._traj_for_plot = traj
        result = super().run(traj)
        if result.status != "ok" or self._profile is None or self._path is None:
            return result
        data = self.output_dir / "pore_radius.dat"
        self._profile.to_csv(data, index=False, float_format="%.5f")
        path = self.output_dir / "pore_path.npz"
        np.savez_compressed(path, **self._path)
        with closes_what_it_opens(), sized_for(self.figure_width), drawn_in(
                "greyscale" if self.figure_colours == "greyscale" else "colour"):
            fig, ax = new_figure(title="Pore radius along the axis",
                                 figsize=self._user_figsize,
                                 xlabel="Height along the pore (nm)",
                                 ylabel="Radius (nm)")
            followed = self.findings["pore"]
            shown = self._profile[(self._profile["height_nm"] >= followed["from_nm"])
                                  & (self._profile["height_nm"] <= followed["to_nm"])]
            ax.fill_between(shown["height_nm"], shown["min_nm"], shown["max_nm"],
                            alpha=0.25, linewidth=0, label="range over the frames")
            ax.plot(shown["height_nm"], shown["mean_nm"], linewidth=1.4, label="mean")
            ax.axhline(TOO_NARROW_FOR_WATER_NM, linewidth=0.8, linestyle="--",
                       color="0.4", label="too narrow for a water")
            ax.set_ylim(bottom=min(0.0, float(shown["min_nm"].min())))
            ax.legend(loc="best", fontsize=7.5)
            figure = save_figure(fig, self.output_dir / "pore_radius.png")
        written = [data, path, figure]
        if figure.with_suffix(".svg").is_file():
            written.append(figure.with_suffix(".svg"))
        result.artifacts.extend(written)
        self.findings["profile"] = {"data": data.name, "path": path.name,
                                    "figure": figure.name}
        self._write_options_manifest()
        return result


register_analysis(PoreProfile.name, PoreProfile)
