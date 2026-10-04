"""A study's main motions, as the Viewer shows them.

The principal components the `dimred` analysis finds say which collective
motion the study spends most of its fluctuation on; it wrote only each
frame's projection onto them, a scatter that says when the study moved, not
how. It now keeps the motions (`dimred_pca_modes.npz`), and this gives each
to the Viewer as its atoms moved along it, from two standard deviations of
its projection one way to two the other, and as lines from each atom to
where that amplitude takes it, placed on the first frame played by fitting
the motion's mean structure there. A study analysed before the modes were
kept has them computed from the frames played, by the same steps (fitted on
the same atoms to the first frame, principal components of the coordinates),
and is said to.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["AMPLITUDE", "FRAMES", "SCALES", "motion", "modes_of"]

#: Each motion is shown to this many standard deviations of its projection.
AMPLITUDE = 2.0
#: How many frames one swing back and forth is shown in.
FRAMES = 40
#: What the amplitude may be multiplied by to be seen, and is then said to be.
SCALES = (1, 3)
_LOCK = threading.Lock()


def modes_of(root: str | Path) -> dict[str, Any]:
    """The study's motions: from the analysis where it kept them and read
    the trajectory played, else from the frames played; or why there are
    none. Each motion's atoms are numbered in the frames played."""
    out = Path(root)
    kept = _from_the_analysis(out)
    if kept is not None:
        return kept
    return _from_the_frames(out)


def _from_the_analysis(out: Path) -> dict[str, Any] | None:
    from fastmdxplora.gui.series import of_the_played_trajectory
    from fastmdxplora.gui.trajectory_frames import _source

    path = out / "analysis" / "dimred" / "dimred_pca_modes.npz"
    if not path.is_file() or not of_the_played_trajectory(out):
        return None
    source = _source(out)
    if source is None:
        return None
    import mdtraj as md

    from fastmdxplora.utils.native_output import suppress_native_output

    with np.load(path) as saved:
        modes = {key: saved[key] for key in saved.files}
    with suppress_native_output():
        whole = md.load_topology(str(source["topology"]))
    shown = whole.select("not water")
    places = np.searchsorted(shown, modes["atoms"])
    if np.any(places >= len(shown)) or np.any(shown[places] != modes["atoms"]):
        return None
    record = _load_json(out / "analysis" / "dimred" / "options.json")
    selection = str(record.get("selection") or "name CA")
    return {"ok": True, "from": "analysis", "atoms": places, "mean": modes["mean"],
            "vectors": modes["vectors"], "variance": modes["variance"],
            "ratio": modes["ratio"], "frames": int(modes["frames"]),
            "said": (f"From the dimred analysis's principal components of `{selection}` "
                     f"over the {int(modes['frames']):,} frames it analysed.")}


def _from_the_frames(out: Path) -> dict[str, Any]:
    import mdtraj as md

    from fastmdxplora.analysis.base import superposed
    from fastmdxplora.utils.native_output import suppress_native_output

    simulation = out / "simulation"
    index = _load_json(simulation / "frames_index.json")
    if not index.get("available") or not (simulation / "frames.dcd").is_file():
        return {"ok": False, "reason": "There are no frames to find a motion in yet."}
    record = simulation / "motion_modes.npz"
    signature = str(index.get("signature"))
    with _LOCK:
        if record.is_file():
            with np.load(record) as saved:
                if str(saved["signature"]) == signature:
                    return _said_of_the_frames({key: saved[key] for key in saved.files})
        with suppress_native_output():
            frames = md.load_dcd(str(simulation / "frames.dcd"),
                                 top=str(simulation / "frames_topology.pdb"))
        atoms = frames.topology.select("name CA")
        if len(atoms) < 3 or frames.n_frames < 3:
            return {"ok": False, "reason": "A motion needs three alpha carbons and three "
                                           "frames."}
        from sklearn.decomposition import PCA

        aligned = superposed(frames, frame=0, atom_indices=atoms)
        coords = aligned.xyz[:, atoms, :].reshape(frames.n_frames, -1).astype(np.float64)
        coords -= coords.mean(axis=0)
        if not np.any(coords.var(axis=0) > 0):
            return {"ok": False, "reason": "The frames do not move: there is no motion."}
        count = min(3, frames.n_frames - 1)
        model = PCA(n_components=count).fit(coords)
        modes = {"atoms": np.asarray(atoms, dtype=np.int64),
                 "mean": aligned.xyz[:, atoms, :].mean(axis=0).astype(np.float64),
                 "vectors": model.components_.reshape(count, len(atoms), 3),
                 "variance": model.explained_variance_, "ratio": model.explained_variance_ratio_,
                 "frames": np.int64(frames.n_frames), "signature": np.array(signature)}
        temporary = record.with_name(f".{record.name}.tmp.npz")
        np.savez(temporary, **modes)
        temporary.replace(record)
        return _said_of_the_frames(modes)


def _said_of_the_frames(modes: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "from": "frames", "atoms": modes["atoms"], "mean": modes["mean"],
            "vectors": modes["vectors"], "variance": modes["variance"],
            "ratio": modes["ratio"], "frames": int(modes["frames"]),
            "said": (f"From the principal components of the alpha carbons over the "
                     f"{int(modes['frames']):,} frames played: the study's dimred analysis "
                     "kept no motions to read.")}


def motion(root: str | Path, mode: Any = 1, scale: Any = 1) -> dict[str, Any]:
    """One motion as the Viewer shows it: the atoms it moves, as a PDB of
    ``FRAMES`` models swinging from minus to plus ``AMPLITUDE`` standard
    deviations of its projection and back, and as a PDB of lines from each
    atom to where the plus extreme takes it; both placed on the first frame
    played. ``mode`` counts from one; ``scale`` is one of ``SCALES``."""
    out = Path(root)
    try:
        number, times = int(mode), int(scale)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "A motion is asked for by its number and a scale."}
    if times not in SCALES:
        return {"ok": False, "reason": "A motion is shown as it is, or three times larger."}
    modes = modes_of(out)
    if not modes.get("ok"):
        return modes
    if not 1 <= number <= len(modes["variance"]):
        return {"ok": False, "reason": f"The study has {len(modes['variance'])} motions."}
    from fastmdxplora.gui.trajectory_frames import _atom_lines, _read

    simulation = out / "simulation"
    lines = _atom_lines(_read(simulation / "frames_topology.pdb"))
    first = _first_frame(out)
    atoms = np.asarray(modes["atoms"], dtype=int)
    if not len(lines) or atoms.max() >= len(lines) or first is None:
        return {"ok": False, "reason": "The motion's atoms are not those of the frames played."}
    place, turn = _fit(modes["mean"], first[atoms])
    mean = place(modes["mean"]) * 10.0
    vector = modes["vectors"][number - 1] @ turn.T
    sd = float(np.sqrt(modes["variance"][number - 1]))
    reach = AMPLITUDE * sd * times * 10.0
    models = []
    for k in range(FRAMES):
        at = reach * np.sin(2 * np.pi * k / FRAMES)
        models.append(f"MODEL     {k + 1:4d}\n" + _pdb(lines, atoms, mean + at * vector)
                      + "ENDMDL\n")

    tips = mean + reach * vector
    farthest = float(np.linalg.norm(reach * vector, axis=1).max())
    share = float(modes["ratio"][number - 1])
    said = (f"Motion {number}: {share * 100:.0f}% of the fluctuation of these atoms, its "
            f"projection's standard deviation {sd:.3g} nm; shown from {AMPLITUDE:g} standard "
            f"deviations one way to {AMPLITUDE:g} the other"
            + (f", made {times} times larger to be seen" if times > 1 else "")
            + f", which moves an atom at most {farthest:.1f} Å. {modes['said']}")
    return {"ok": True, "mode": number, "modes": [round(float(r), 4) for r in modes["ratio"]],
            "share": share, "sd_nm": sd, "scale": times, "atoms": int(len(atoms)),
            "from": modes["from"], "said": said, "frames": FRAMES,
            "pdb": "".join(models) + "END\n",
            "arrows": _arrows(lines, atoms, mean, tips)}


def _first_frame(out: Path) -> Any:
    import mdtraj as md

    from fastmdxplora.utils.native_output import suppress_native_output

    path = out / "simulation" / "frames.dcd"
    if not path.is_file():
        return None
    with suppress_native_output():
        with md.formats.DCDTrajectoryFile(str(path)) as handle:
            xyz, _, _ = handle.read(1)
    return xyz[0].astype(np.float64) / 10.0


def _fit(points: Any, reference: Any) -> tuple[Any, Any]:
    """The least-squares move and turn of ``points`` onto ``reference``, as
    a function of positions, and the turn alone."""
    here, there = points.mean(axis=0), reference.mean(axis=0)
    left, _, right = np.linalg.svd((points - here).T @ (reference - there))
    sign = np.sign(np.linalg.det(left @ right)) or 1.0
    turn = (left @ np.diag([1.0, 1.0, sign]) @ right).T
    return (lambda positions: (np.asarray(positions) - here) @ turn.T + there), turn


def _pdb(lines: list[str], atoms: Any, xyz: Any) -> str:
    return "".join(f"{lines[int(a)][:30]}{x:8.3f}{y:8.3f}{z:8.3f}{lines[int(a)][54:].rstrip()}\n"
                   for a, (x, y, z) in zip(atoms, xyz))


def _arrows(lines: list[str], atoms: Any, start: Any, end: Any) -> str:
    """A line from each atom to where the motion takes it: two atoms of one
    residue each, bonded."""
    written, bonds = [], []
    for i, (a, b) in enumerate(zip(start, end)):
        residue = lines[int(atoms[i])][17:26]
        for k, (name, point) in enumerate((("BEG", a), ("END", b))):
            serial = 2 * i + k + 1
            written.append(f"HETATM{serial:5d}  {name} {residue}    "
                           f"{point[0]:8.3f}{point[1]:8.3f}{point[2]:8.3f}  1.00  0.00"
                           f"           C\n")
        bonds.append(f"CONECT{2 * i + 1:5d}{2 * i + 2:5d}\n")
    return "".join(written) + "".join(bonds) + "END\n"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
