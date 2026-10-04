"""Another study beside this one in the Viewer: a wild type and a mutant, or
any two studies of related proteins.

Two studies were compared on the All studies page by their means, and their
structures one window at a time. Here the other study's frames are played
beside this one's. Their residues are paired by their sequences (difflib's
matching of the one-letter sequences, which for two forms of one protein
pairs every residue but those inserted or deleted, and pairs a substituted
one as a mutation); the other's frames are fitted on the paired alpha
carbons to this study's first frame; and the two are played by simulation
time, frame k of this study beside the other's frame nearest it in time,
since two studies need not save frames alike. Where both have an RMSF, the
difference over the paired residues colours this study's protein.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["BESIDE", "ONE_LETTER", "beside", "beside_file"]

#: Where the other study's fitted frames are written, under this study.
BESIDE = "viewer_beside"
#: A residue's letter, as the Viewer's sequence gives it; others are X.
ONE_LETTER = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "ASH": "D", "CYS": "C", "CYX": "C",
    "CYM": "C", "GLN": "Q", "GLU": "E", "GLH": "E", "GLY": "G", "HIS": "H", "HID": "H",
    "HIE": "H", "HIP": "H", "HSD": "H", "HSE": "H", "HSP": "H", "ILE": "I", "LEU": "L",
    "LYS": "K", "LYN": "K", "MET": "M", "MSE": "M", "PHE": "F", "PRO": "P", "SER": "S",
    "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V", "SEC": "U", "PYL": "O",
}


def beside_file(key: str, suffix: str) -> str | None:
    """A file of the other study's frames, by its key alone (never a path)."""
    if len(key) != 16 or any(c not in "0123456789abcdef" for c in key):
        return None
    return f"{key}{suffix}" if suffix in (".dcd", ".pdb") else None


def _residues(lines: list[str]) -> list[dict[str, Any]]:
    """Each residue with an alpha carbon, in order: its chain, number,
    insertion code, name, letter and the alpha carbon's place."""
    found = []
    for place, line in enumerate(lines):
        if line[12:16].strip() != "CA" or line[17:20].strip() not in ONE_LETTER:
            continue
        found.append({"chain": line[21].strip() or None, "resi": int(line[22:26]),
                      "icode": line[26].strip(), "name": line[17:20].strip(),
                      "letter": ONE_LETTER[line[17:20].strip()], "atom": place})
    return found


def paired(mine: list[dict[str, Any]], theirs: list[dict[str, Any]]
           ) -> tuple[list[tuple[int, int]], list[tuple[int, int]], int]:
    """The residues paired by sequence, the pairs whose residues differ, and
    how many of either were left without a partner."""
    first = "".join(r["letter"] for r in mine)
    second = "".join(r["letter"] for r in theirs)
    pairs, mutations, unpaired = [], [], 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, first, second,
                                                         autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for i, j in zip(range(i1, i2), range(j1, j2)):
                pairs.append((i, j))
                if tag == "replace":
                    mutations.append((i, j))
        else:
            unpaired += (i2 - i1) + (j2 - j1)
    return pairs, mutations, unpaired


def beside(root: str | Path, other: str | Path, *, most_frames: int) -> dict[str, Any]:
    """The other study's frames fitted beside this one's and timed to them,
    written once beside this study's frames, with the residues paired and
    those that differ; or why not."""
    import mdtraj as md

    from fastmdxplora.analysis.base import superposed
    from fastmdxplora.gui.browse import is_study
    from fastmdxplora.gui.runs_together import run_shown
    from fastmdxplora.gui.trajectory_frames import (FRAMES_FILE, FRAMES_TOPOLOGY, _atom_lines,
                                                    _read, _write_dcd, _write_text, frames_info)
    from fastmdxplora.utils.native_output import suppress_native_output

    this = Path(root)
    that = Path(other).expanduser()
    try:
        that = that.resolve()
    except OSError:
        pass
    if not that.is_dir() or not is_study(that):
        return {"ok": False, "reason": f"{that} is not a study."}
    name = that.name
    that = run_shown(that) or that
    if that.resolve() == this.resolve():
        return {"ok": False, "reason": "That is the study shown."}
    # With each run's length, as the server gives the frames, so the frames
    # have their times.
    mine, theirs = (frames_info(study, most_frames=most_frames,
                                simulation_time_ns_total=_load_json(
                                    study / "simulation" / "simulation_parameters.json")
                                .get("duration_ns_actual"))
                    for study in (this, that))
    if not mine.get("available"):
        return {"ok": False, "reason": "This study has no frames to play beside yet."}
    if not theirs.get("available"):
        return {"ok": False, "reason": f"{name} has no frames to play yet."}
    key = hashlib.sha1(str(that).encode("utf-8")).hexdigest()[:16]
    out = this / BESIDE
    signature = json.dumps([mine.get("signature"), theirs.get("signature"), str(that)])
    cached = _load_json(out / f"{key}.json")
    if cached.get("signature") == signature and (out / f"{key}.dcd").is_file():
        return cached["said"]
    lines_a = _atom_lines(_read(this / "simulation" / FRAMES_TOPOLOGY))
    lines_b = _atom_lines(_read(that / "simulation" / FRAMES_TOPOLOGY))
    residues_a, residues_b = _residues(lines_a), _residues(lines_b)
    pairs, mutations, unpaired = paired(residues_a, residues_b)
    if len(pairs) < 3:
        return {"ok": False, "reason": f"{name}'s residues have too little sequence in common "
                                       "with this study's to be fitted."}
    atoms_a = np.array([residues_a[i]["atom"] for i, _ in pairs])
    atoms_b = np.array([residues_b[j]["atom"] for _, j in pairs])
    chosen, timed = _timed(mine, theirs)
    with suppress_native_output():
        first = md.load_dcd(str(this / "simulation" / FRAMES_FILE),
                            top=str(this / "simulation" / FRAMES_TOPOLOGY), frame=0)
        frames = md.load_dcd(str(that / "simulation" / FRAMES_FILE),
                             top=str(that / "simulation" / FRAMES_TOPOLOGY))
    frames = frames[chosen]
    reference = md.Trajectory(first.xyz[:, atoms_a], first.topology.subset(atoms_a))
    fitted = superposed(frames, atom_indices=atoms_b, reference=reference,
                        ref_atom_indices=np.arange(len(atoms_a)))
    out.mkdir(exist_ok=True)
    _write_dcd(fitted, out / f"{key}.dcd")
    _write_text(out / f"{key}.pdb", "\n".join(lines_b) + "\nEND\n")
    differ = [{"chain": residues_a[i]["chain"], "resi": residues_a[i]["resi"],
               "icode": residues_a[i]["icode"], "from": residues_a[i]["name"],
               "to": residues_b[j]["name"], "theirs": residues_b[j]["resi"]}
              for i, j in mutations]
    said = {"ok": True, "name": name, "key": key, "frames": len(chosen),
            "paired": len(pairs), "unpaired": unpaired, "mutations": differ,
            "topology": f"/structure/beside.pdb?key={key}",
            "coordinates": f"/structure/beside.dcd?key={key}",
            "said": (f"{name}, its {len(pairs):,} residues paired with this study's by sequence"
                     + (f" ({len(differ)} differ" + (f", {unpaired} unpaired)" if unpaired
                                                     else ")") if differ or unpaired else "")
                     + f", fitted on their alpha carbons to this study's first frame, {timed}."),
            "property": _difference(this, that, name, residues_a, residues_b, pairs)}
    _write_text(out / f"{key}.json", json.dumps({"signature": signature, "said": said}))
    return said


def _timed(mine: dict[str, Any], theirs: dict[str, Any]) -> tuple[list[int], str]:
    """Which of the other's frames is beside each of this study's: the one
    nearest in simulation time, while the other's run lasts; by place in the
    run where either has no clock."""
    ours = mine.get("frame_times_ns") or []
    other = theirs.get("frame_times_ns") or []
    count = int(theirs.get("n_frames_browser") or 0)
    if ours and other and all(t is not None for t in ours + other):
        times = np.asarray(other, dtype=float)
        step = float(np.median(np.diff(times))) if len(times) > 1 else 0.0
        chosen = [int(np.abs(times - t).argmin()) for t in ours if t <= times[-1] + step / 2]
        return chosen, "each frame beside the other's nearest it in simulation time"
    n = len(ours) or int(mine.get("n_frames_browser") or 0)
    chosen = [min(count - 1, round(k * (count - 1) / max(1, n - 1))) for k in range(n)]
    return chosen, ("each frame beside the other's at the same place in its run, since a run's "
                    "clock was not recorded")


def _difference(this: Path, that: Path, name: str, residues_a: list[dict[str, Any]],
                residues_b: list[dict[str, Any]], pairs: list[tuple[int, int]]
                ) -> dict[str, Any] | None:
    """The other study's RMSF less this one's over the paired residues, as
    the Viewer colours by a result, white at no difference."""
    from fastmdxplora.gui.by_residue import values_by_residue

    def rmsf(study: Path) -> dict[tuple, float] | None:
        found = next((p for p in values_by_residue(study)["properties"]
                      if p.get("analysis") == "rmsf"), None)
        if found is None:
            return None
        return {(row[0], row[1], row[2] or ""): row[3] for row in found["values"]}

    ours, theirs = rmsf(this), rmsf(that)
    if not ours or not theirs:
        return None

    def value(table: dict[tuple, float], residue: dict[str, Any]) -> float | None:
        named = (residue["chain"], residue["resi"], residue["icode"])
        return table.get(named, table.get((None, residue["resi"], residue["icode"])))

    rows = []
    for i, j in pairs:
        a, b = value(ours, residues_a[i]), value(theirs, residues_b[j])
        if a is not None and b is not None:
            r = residues_a[i]
            rows.append([r["chain"], r["resi"], r["icode"], round(b - a, 5)])
    if not rows:
        return None
    reach = max(abs(row[3]) for row in rows) or 0.01
    return {"key": "rmsf-difference", "label": f"RMSF, {name} less this study", "unit": "nm",
            "low": -reach, "high": reach, "absent": None, "values": rows,
            "source": "analysis/rmsf of both studies",
            "about": (f"Each paired residue's RMSF in {name} less its RMSF in this study: red "
                      f"where it moves more in {name}, blue where less, white where alike.")}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
