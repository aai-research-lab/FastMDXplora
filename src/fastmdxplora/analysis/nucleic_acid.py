"""The shape of a double helix: base-pair and step parameters, as 3DNA
defines them.

Each base is given the standard reference frame of Olson et al. (J. Mol.
Biol. 313, 229 (2001)): the ideal base of that kind, with its axes, fitted
by least squares onto the base's ring atoms in each frame. A pair's two
frames (the second strand's turned over, so that both point the same way)
give its six base-pair parameters, and two successive pairs' frames give
the six step parameters, by the construction 3DNA uses (El Hassan and
Calladine, J. Mol. Biol. 259, 95 (1996); Lu and Olson, Nucleic Acids Res.
31, 5108 (2003)): each frame turned half way onto the other about the axis
their z axes hinge on, the middle frame between them, and

- the **base-pair parameters**: shear, stretch and stagger, the second
  base's place from the first along the middle frame's x, y and z, and
  buckle, propeller and opening, the turn about them;
- the **step parameters**: shift, slide and rise, and tilt, roll and twist.

Distances are in nm (3DNA gives Å) and angles in degrees. For B-DNA, rise
is about 0.33 nm, twist about 36 degrees, and propeller about -10 degrees.

The pairs are found in the first frame: a purine and a pyrimidine of
different strands that are Watson-Crick partners (A with T or U, G with C)
whose N1 and N3 are within 0.35 nm and whose bases face each other. A pair
that comes apart later is still followed, and how often its N1 and N3 are
more than 0.4 nm apart is recorded beside it.

Not computed: groove widths, the helical axis and its bending, sugar
puckers and backbone torsions (3DNA, Curves+ and cpptraj's ``nastruct`` give
these).
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError

#: The standard bases of Olson et al. (2001), in Å, their frames the axes.
STANDARD = {
    "A": {"N9": (-1.291, 4.498, 0.0), "C8": (0.024, 4.897, 0.0), "N7": (0.877, 3.902, 0.0),
          "C5": (0.071, 2.771, 0.0), "C6": (0.369, 1.398, 0.0), "N1": (-0.668, 0.532, 0.0),
          "C2": (-1.912, 1.023, 0.0), "N3": (-2.320, 2.290, 0.0), "C4": (-1.267, 3.124, 0.0)},
    "G": {"N9": (-1.289, 4.551, 0.0), "C8": (0.023, 4.962, 0.0), "N7": (0.870, 3.969, 0.0),
          "C5": (0.071, 2.833, 0.0), "C6": (0.424, 1.460, 0.0), "N1": (-0.700, 0.641, 0.0),
          "C2": (-1.999, 1.087, 0.0), "N3": (-2.342, 2.364, 0.001), "C4": (-1.265, 3.177, 0.0)},
    "C": {"N1": (-1.285, 4.542, 0.0), "C2": (-1.472, 3.158, 0.0), "N3": (-0.391, 2.344, 0.0),
          "C4": (0.837, 2.868, 0.0), "C5": (1.056, 4.275, 0.0), "C6": (-0.023, 5.068, 0.0)},
    "T": {"N1": (-1.284, 4.500, 0.0), "C2": (-1.462, 3.135, 0.0), "N3": (-0.298, 2.407, 0.0),
          "C4": (0.994, 2.897, 0.0), "C5": (1.106, 4.338, 0.0), "C6": (-0.024, 5.057, 0.0)},
    "U": {"N1": (-1.284, 4.500, 0.0), "C2": (-1.462, 3.131, 0.0), "N3": (-0.302, 2.397, 0.0),
          "C4": (0.989, 2.884, 0.0), "C5": (1.089, 4.311, 0.0), "C6": (-0.024, 5.053, 0.0)},
}
PURINES = {"A", "G"}
PARTNERS = {("A", "T"), ("A", "U"), ("G", "C")}
PAIR_PARAMETERS = ("shear", "stretch", "stagger", "buckle", "propeller", "opening")
STEP_PARAMETERS = ("shift", "slide", "rise", "tilt", "roll", "twist")


def base_letter(name: str) -> str | None:
    """A, C, G, T or U for a nucleotide's residue name (DA, DA5, A, RA3, ADE
    ...), or None."""
    name = name.strip().upper().rstrip("35")
    full = {"ADE": "A", "GUA": "G", "CYT": "C", "THY": "T", "URA": "U"}
    if name in full:
        return full[name]
    if len(name) == 2 and name[0] in "DR":
        name = name[1]
    return name if name in STANDARD else None


def _rotation(axis: np.ndarray, angle: float) -> np.ndarray:
    """Rotation by ``angle`` (radians) about the unit vector ``axis``."""
    x, y, z = axis
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                     [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                     [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]])


def _signed(a: np.ndarray, b: np.ndarray, about: np.ndarray) -> float:
    """The angle from ``a`` to ``b``, signed by the right hand about ``about``."""
    angle = np.arctan2(np.dot(np.cross(a, b), about), np.dot(a, b))
    return float(angle)


def frame_parameters(first: np.ndarray, first_origin: np.ndarray, second: np.ndarray,
                     second_origin: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The six parameters of ``second`` from ``first`` (frames as 3x3 with
    the axes as columns), as 3DNA's CEHS gives them: the three displacements
    and the turns about x, y and z (degrees), with the middle frame and its
    origin."""
    z1, z2 = first[:, 2], second[:, 2]
    gamma = float(np.arccos(np.clip(np.dot(z1, z2), -1.0, 1.0)))
    hinge = np.cross(z1, z2)
    size = np.linalg.norm(hinge)
    if size < 1e-10:
        hinge, gamma = first[:, 1].copy(), 0.0
    else:
        hinge = hinge / size
    turned_second = _rotation(hinge, -gamma / 2) @ second
    turned_first = _rotation(hinge, gamma / 2) @ first
    middle = turned_first + turned_second
    middle /= np.linalg.norm(middle, axis=0, keepdims=True)
    xm, ym, zm = middle[:, 0], middle[:, 1], middle[:, 2]
    twist = _signed(turned_first[:, 1], turned_second[:, 1], zm)
    phase = np.arctan2(np.dot(hinge, xm), np.dot(hinge, ym))
    about_y = gamma * np.cos(phase)
    about_x = gamma * np.sin(phase)
    moved = second_origin - first_origin
    values = np.array([np.dot(moved, xm), np.dot(moved, ym), np.dot(moved, zm),
                       np.degrees(about_x), np.degrees(about_y), np.degrees(twist)])
    return values, middle, (first_origin + second_origin) / 2


def step_from(frame: np.ndarray, origin: np.ndarray, parameters) -> tuple[np.ndarray, np.ndarray]:
    """The next frame from ``frame`` by step ``parameters`` (shift, slide,
    rise, tilt, roll, twist), as 3DNA's rebuilding places it: the inverse of
    :func:`frame_parameters`."""
    shift, slide, rise, tilt, roll, twist = parameters
    gamma = np.radians(np.hypot(roll, tilt))
    phase = np.arctan2(np.radians(tilt), np.radians(roll))
    omega = np.radians(twist)
    z, y = np.array([0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0])
    following = (frame @ _rotation(z, omega / 2 - phase) @ _rotation(y, gamma)
                 @ _rotation(z, omega / 2 + phase))
    middle = frame @ _rotation(z, omega / 2 - phase) @ _rotation(y, gamma / 2) @ _rotation(z, phase)
    return following, origin + middle @ np.array([shift, slide, rise])


def base_frames(xyz: np.ndarray, letter: str, atoms: dict[str, int]) -> tuple[np.ndarray, np.ndarray]:
    """Each frame's standard reference frame of one base, fitted onto its
    ring atoms: rotations (frames, 3, 3), axes as columns, and origins
    (frames, 3), in nm."""
    names = [n for n in STANDARD[letter] if n in atoms]
    ideal = np.array([STANDARD[letter][n] for n in names]) / 10.0
    seen = xyz[:, [atoms[n] for n in names]].astype(np.float64)
    ideal_centre, seen_centre = ideal.mean(axis=0), seen.mean(axis=1)
    covariance = np.einsum("ai,faj->fij", ideal - ideal_centre, seen - seen_centre[:, None])
    left, _, right = np.linalg.svd(covariance)
    sign = np.sign(np.linalg.det(np.einsum("fij,fjk->fik", left, right)))
    flip = np.ones((len(xyz), 3))
    flip[:, 2] = sign
    rotation = np.einsum("fji,fj,fkj->fik", right, flip, left)
    origin = seen_centre - np.einsum("fij,j->fi", rotation, ideal_centre)
    return rotation, origin


class NucleicAcid(Analysis):
    """Base-pair and step parameters of a double helix, frame by frame.

    Options
    -------
    pair_cutoff_nm : float, default 0.35
        How close a purine's N1 and a pyrimidine's N3 are, in the first
        frame, for the two to be a Watson-Crick pair.

    Output
    ------
    ``nucleic_acid.dat`` -- comma-separated, one row per step: ``step``
    (its two pairs, 5' to 3' along the first strand), then the mean and
    standard deviation over the frames of ``shift``, ``slide``, ``rise``
    (nm), ``tilt``, ``roll`` and ``twist`` (degrees).
    ``nucleic_acid_pairs.dat`` -- one row per pair: ``pair``, the means and
    standard deviations of the six base-pair parameters, and ``apart``,
    the share of frames its N1 and N3 are over 0.4 nm apart.
    ``nucleic_acid.npz`` -- every frame's ``pairs`` (frames, pairs, 6) and
    ``steps`` (frames, steps, 6).
    """

    name = "nucleic_acid"
    description = "Base-pair and step parameters"
    default_selection = "all"
    #: Run where the system has a double helix and the study asks about it.
    requires_naming = True

    def __init__(self, *, pair_cutoff_nm: float = 0.35, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.pair_cutoff_nm = float(pair_cutoff_nm)
        if not 0.25 <= self.pair_cutoff_nm <= 0.5:
            raise StudyError(
                f"`pair_cutoff_nm` is from 0.25 to 0.5 nm; it was {self.pair_cutoff_nm:g}.",
                code="analysis.option.out_of_range")
        self.options.update(pair_cutoff_nm=self.pair_cutoff_nm)
        self._pairs_table: pd.DataFrame | None = None
        self._every: dict[str, np.ndarray] | None = None

    # ------------------------------------------------------------------
    def _bases(self, traj: md.Trajectory) -> list[dict[str, Any]]:
        chosen = set(int(a) for a in self.select_atoms(traj))
        bases = []
        for residue in traj.topology.residues:
            letter = base_letter(residue.name)
            if letter is None:
                continue
            atoms = {a.name: a.index for a in residue.atoms if a.index in chosen}
            if sum(n in atoms for n in STANDARD[letter]) < 6:
                continue
            bases.append({"residue": residue, "letter": letter, "atoms": atoms})
        return bases

    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        bases = self._bases(traj)
        if len(bases) < 4:
            raise StudyError(
                f"{self.selection!r} holds {len(bases)} nucleotide base(s) with their "
                "ring atoms; a double helix needs two pairs at least.",
                code="analysis.system.inapplicable")
        frames = {}
        for k, base in enumerate(bases):
            frames[k] = base_frames(traj.xyz, base["letter"], base["atoms"])

        def contact(k: int) -> int:
            return bases[k]["atoms"]["N1" if bases[k]["letter"] in PURINES else "N3"]

        pairs, used = [], set()
        for i, first in enumerate(bases):
            if first["letter"] not in PURINES and i in used:
                continue
            best, nearest = None, self.pair_cutoff_nm
            for j, second in enumerate(bases):
                if (j in used or i in used or second["residue"].chain is first["residue"].chain
                        or tuple(sorted((first["letter"], second["letter"]),
                                        key=lambda letter: letter not in PURINES))
                        not in PARTNERS):
                    continue
                apart = float(np.linalg.norm(traj.xyz[0, contact(i)] - traj.xyz[0, contact(j)]))
                facing = float(np.dot(frames[i][0][0][:, 2], frames[j][0][0][:, 2])) < 0
                if apart < nearest and facing:
                    best, nearest = j, apart
            if best is not None:
                pairs.append((i, best))
                used.update((i, best))
        if len(pairs) < 2:
            raise StudyError(
                f"{len(pairs)} Watson-Crick pair(s) were found in the first frame (a "
                f"purine's N1 within {self.pair_cutoff_nm:g} nm of a partner's N3 on "
                "another strand, the bases facing): a step needs two.",
                code="analysis.system.inapplicable")
        # Along the first strand, 5' to 3'.
        strand = bases[pairs[0][0]]["residue"].chain
        pairs = [(i, j) if bases[i]["residue"].chain is strand else (j, i) for i, j in pairs]
        pairs = [p for p in pairs if bases[p[0]]["residue"].chain is strand]
        pairs.sort(key=lambda p: bases[p[0]]["residue"].index)

        n_frames = traj.n_frames
        flip = np.diag([1.0, -1.0, -1.0])
        pair_values = np.empty((n_frames, len(pairs), 6))
        pair_frames = np.empty((n_frames, len(pairs), 3, 3))
        pair_origins = np.empty((n_frames, len(pairs), 3))
        apart_share = []
        for p, (i, j) in enumerate(pairs):
            (r1, o1), (r2, o2) = frames[i], frames[j]
            for f in range(n_frames):
                values, middle, origin = frame_parameters(r2[f] @ flip, o2[f], r1[f], o1[f])
                pair_values[f, p], pair_frames[f, p], pair_origins[f, p] = values, middle, origin
            gap = np.linalg.norm(traj.xyz[:, contact(i)] - traj.xyz[:, contact(j)], axis=1)
            apart_share.append(float((gap > 0.4).mean()))

        def label(p: int) -> str:
            i, j = pairs[p]
            return (f"{bases[i]['letter']}{bases[i]['residue'].resSeq}-"
                    f"{bases[j]['letter']}{bases[j]['residue'].resSeq}")

        steps = [p for p in range(len(pairs) - 1)
                 if bases[pairs[p + 1][0]]["residue"].index
                 == bases[pairs[p][0]]["residue"].index + 1]
        step_values = np.empty((n_frames, len(steps), 6))
        for s, p in enumerate(steps):
            for f in range(n_frames):
                step_values[f, s] = frame_parameters(
                    pair_frames[f, p], pair_origins[f, p],
                    pair_frames[f, p + 1], pair_origins[f, p + 1])[0]

        def summary(values: np.ndarray, names: tuple[str, ...]) -> dict[str, np.ndarray]:
            out: dict[str, np.ndarray] = {}
            for k, name in enumerate(names):
                out[name] = np.round(values[:, :, k].mean(axis=0), 4)
                out[f"{name}_sd"] = np.round(values[:, :, k].std(axis=0), 4)
            return out

        self._pairs_table = pd.DataFrame({
            "pair": [label(p) for p in range(len(pairs))],
            **summary(pair_values, PAIR_PARAMETERS), "apart": apart_share})
        table = pd.DataFrame({"step": [f"{label(p)}/{label(p + 1)}" for p in steps],
                              **summary(step_values, STEP_PARAMETERS)})
        self._every = {"pairs": pair_values.astype(np.float32),
                       "steps": step_values.astype(np.float32)}

        self.findings["helix"] = {
            "pairs": len(pairs), "steps": len(steps),
            "first_strand": f"chain {strand.index + 1}",
            "mean_rise_nm": float(step_values[:, :, 2].mean()) if steps else None,
            "mean_twist_deg": float(step_values[:, :, 5].mean()) if steps else None,
            "mean_propeller_deg": float(pair_values[:, :, 4].mean()),
            "frames": "the standard reference frames of Olson et al., J. Mol. Biol. 313, "
                      "229 (2001), fitted on each base's ring atoms",
            "parameters": "3DNA's CEHS construction (Lu and Olson, Nucleic Acids Res. 31, "
                          "5108 (2003)); distances in nm, angles in degrees",
        }
        broken = [label(p) for p, share in enumerate(apart_share) if share > 0.2]
        if broken:
            self.findings["pairs_apart"] = (
                f"{', '.join(broken)} had N1 and N3 over 0.4 nm apart in more than a fifth "
                "of the frames: they are followed as pairs, and their parameters describe "
                "what they became.")
        self.findings["not_computed"] = (
            "Groove widths, the helical axis and its bending, sugar puckers and backbone "
            "torsions (3DNA, Curves+, cpptraj's nastruct).")
        return table

    # ------------------------------------------------------------------
    def save_data(self, result: pd.DataFrame, path):
        written = super().save_data(result, path)
        if self._pairs_table is not None:
            self._pairs_table.to_csv(path.with_name("nucleic_acid_pairs.dat"), index=False)
        if self._every is not None:
            np.savez_compressed(path.with_suffix(".npz"), **self._every)
        return written

    def default_xlabel(self) -> str | None:
        return "Step"

    def default_ylabel(self) -> str | None:
        return "Twist (degrees)"

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        x = np.arange(len(result))
        ax.errorbar(x, result["twist"], yerr=result["twist_sd"], fmt="o-", markersize=3,
                    linewidth=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels(result["step"], rotation=90, fontsize=6)


register_analysis(NucleicAcid.name, NucleicAcid)
