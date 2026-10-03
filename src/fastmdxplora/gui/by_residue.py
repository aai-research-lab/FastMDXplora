"""What the viewer colours the structure by and shapes its cartoon from.

Two things a viewer of a trajectory should show and this one did not.

**The study's per-residue results, on the structure.** The analyses compute
each residue's RMSF, solvent-accessible surface, contact with the ligand and
N-H order parameter, and the B-factors of the deposited structure as the
fluctuation they imply; each was a plot against residue number, and the
residues a reader wanted to find had to be found by number. The values are
read here from the files the analyses wrote, as written, so the colours say
what the report says; nothing is computed again.

**Secondary structure by DSSP, for each frame shown.** 3Dmol shapes a
cartoon from its own estimate when a PDB has no HELIX or SHEET records, and
no frame the viewer is sent has them: an N-O distance under 3.2 Angstrom
between residues four or more apart. It is not DSSP, so the cartoon could
disagree with the study's secondary structure plot about the same frame.
DSSP is computed here, as the `ss` analysis computes it (MDTraj's
implementation, in its three classes), for the structure, the live frame or
every frame of the playback, from the file the viewer was sent.

Each residue is named as the viewer will find it: by chain, number,
insertion code and name, and by which occurrence of that name it is, so a
structure whose insertion codes were lost on the way (MDTraj writes none)
still has one residue for each entry.
"""

from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path
from typing import Any

__all__ = ["values_by_residue", "secondary_structure", "residue_runs"]

#: A label the analyses write for one residue: ``ASP189``, ``GLY184A`` with
#: an insertion code, ``A:ASP189`` where the chain is named.
_NAMED = re.compile(r"^(?:(?P<chain>[^:]+):)?(?P<resn>[A-Za-z][A-Za-z0-9]{0,3}?)"
                    r"(?P<number>-?\d+)(?P<icode>[A-Za-z]?)$")


# ---------------------------------------------------------------------------
# Per-residue results
# ---------------------------------------------------------------------------

def values_by_residue(root: str | Path) -> dict[str, Any]:
    """Each per-residue result the study's analyses wrote, ready to colour by.

    One entry per analysis folder, in the order the folders sort: its key
    (the folder), what it is called and its unit, the range the colours
    span, what a residue the table does not list stands for (zero, or no
    value), a sentence saying what the number is, and the values as
    ``[chain, number, insertion code, value]`` with the chain None where
    the study has one chain and the analyses name none."""
    base = Path(root)
    frames = _frames_analysed(base)
    found: list[dict[str, Any]] = []
    for options in sorted((base / "analysis").glob("*/options.json")):
        try:
            record = json.loads(options.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        name = str(record.get("analysis") or options.parent.name)
        reader = _READERS.get(name)
        if reader is None:
            continue
        try:
            said = reader(options.parent, record, frames)
        except Exception:  # noqa: BLE001 - one unreadable table hides only itself
            said = None
        if not said or not said.get("values"):
            continue
        said["key"] = options.parent.name
        said["analysis"] = name
        said["source"] = f"analysis/{options.parent.name}"
        found.append(said)
    _one_scale_for_fluctuations(found)
    return {"properties": found}


def _frames_analysed(base: Path) -> int | None:
    try:
        manifest = json.loads((base / "analysis" / "analysis_manifest.json")
                              .read_text(encoding="utf-8"))
        count = int(manifest.get("n_frames"))
    except (OSError, ValueError, TypeError):
        return None
    return count if count > 0 else None


def _over(frames: int | None) -> str:
    return f"the {frames:,} frames analysed" if frames else "the frames analysed"


def _one_scale_for_fluctuations(found: list[dict[str, Any]]) -> None:
    """RMSF and the RMSF the B-factors imply on one scale, from zero.

    They are the same quantity, set side by side to be compared; on scales
    of their own, the same colour would mean two different amplitudes."""
    same = [entry for entry in found if entry["analysis"] in ("rmsf", "bfactor_comparison")]
    if len(same) < 2:
        return
    high = max(entry["high"] for entry in same)
    for entry in same:
        entry["low"], entry["high"] = 0.0, high


def _table(path: Path) -> Any:
    """A table an analysis wrote, read the way it was written: numbers under
    a ``#`` line, or comma-separated under a header row."""
    import numpy as np
    import pandas as pd

    with path.open(encoding="utf-8", errors="replace") as handle:
        first = handle.readline()
    if first.startswith("#"):
        numbers = np.loadtxt(path, ndmin=2)
        return pd.DataFrame(numbers)
    return pd.read_csv(path)


def _rows(table: Any, value: Any) -> list[list[Any]]:
    """``[chain, number, insertion code, value]`` for each finite value of
    ``value`` (a column name, or a position where the table is numbers)."""
    numbered = isinstance(value, int)
    values = table.iloc[:, value] if numbered else table[value]
    numbers = table.iloc[:, 0] if numbered else table["residue"]
    chains = None if numbered or "chain" not in table else table["chain"]
    codes = None if numbered or "insertion" not in table else table["insertion"]
    rows: list[list[Any]] = []
    for position in range(len(table)):
        try:
            said = float(values.iloc[position])
            number = int(float(numbers.iloc[position]))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(said):
            continue
        chain = None if chains is None else _text(chains.iloc[position])
        code = "" if codes is None else _text(codes.iloc[position])
        rows.append([chain or None, number, code, said])
    return rows


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def _span(rows: list[list[Any]], *, low: float | None = None,
          high: float | None = None) -> tuple[float, float]:
    values = [row[3] for row in rows]
    top = max(values) if high is None else high
    bottom = 0.0 if low is None else low
    if top <= bottom:
        top = bottom + 1.0
    return float(bottom), float(top)


def _rmsf(folder: Path, record: dict[str, Any], frames: int | None) -> dict[str, Any] | None:
    options = record.get("options") or {}
    if options.get("per_residue") is False:
        return None
    path = folder / "rmsf.dat"
    rows = _rows(_table(path), "rmsf_nm" if _has_header(path) else 1)
    if not rows:
        return None
    low, high = _span(rows)
    selection = str(record.get("selection") or "name CA")
    atoms = "its alpha carbon" if selection == "name CA" else f"its atoms in `{selection}`"
    return {"label": "RMSF", "unit": "nm", "low": low, "high": high, "absent": None,
            "values": rows,
            "about": (f"Each residue's fluctuation about its mean position over {_over(frames)}, "
                      f"from {atoms}, after superposing them.")}


def _sasa(folder: Path, record: dict[str, Any], frames: int | None) -> dict[str, Any] | None:
    mode = str((record.get("options") or {}).get("mode") or "total")
    if mode == "average_residue":
        rows = _rows(_table(folder / "sasa.dat"), "mean_sasa_nm2")
    elif mode == "residue":
        rows = _mean_of_each_residue(folder / "sasa.dat")
    else:
        return None
    if not rows:
        return None
    low, high = _span(rows)
    return {"label": "Mean SASA", "unit": "nm²", "low": low, "high": high,
            "absent": None, "values": rows,
            "about": (f"Each residue's solvent-accessible surface area, its mean over "
                      f"{_over(frames)}.")}


def _mean_of_each_residue(path: Path) -> list[list[Any]]:
    """Each residue's mean from the per-frame table, by chain and insertion
    code as well as number where the table has them.

    The average written beside the per-frame table was grouped by number
    alone in studies analysed before that was corrected, so on a structure
    of several chains it averaged the copies of each residue together. The
    per-frame table is right in every study, and it is read instead."""
    table = _table(path)
    keys = [key for key in ("chain", "residue", "insertion") if key in table]
    if "residue" not in keys or "sasa_nm2" not in table:
        return []
    means = table.groupby(keys, sort=False, dropna=False)["sasa_nm2"].mean().reset_index()
    return _rows(means, "sasa_nm2")


def _pl_contacts(folder: Path, record: dict[str, Any], frames: int | None) -> dict[str, Any] | None:
    options = record.get("options") or {}
    ligand = str(options.get("ligand_resname") or "the ligand")
    path = folder / "pl_contacts_per_residue.csv"
    if not path.is_file():
        return None
    table = _table(path)
    rows: list[list[Any]] = []
    for label, value in zip(table["residue"], table["contact_frequency"]):
        named = _NAMED.match(str(label).strip())
        try:
            said = float(value)
        except (TypeError, ValueError):
            continue
        if named is None or not math.isfinite(said):
            continue
        rows.append([named["chain"] or None, int(named["number"]), named["icode"], said])
    if not rows:
        return None
    cutoff = options.get("cutoff")
    within = f"within {float(cutoff):g} nm of" if isinstance(cutoff, (int, float)) else "near"
    # The table lists the residues that were ever in contact. Over the whole
    # protein, one it does not list was never within the cutoff: zero, not
    # unknown. Over a narrower selection, unlisted is also unasked.
    whole = str(options.get("protein_selection") or "protein") == "protein"
    about = (f"The fraction of {_over(frames)} in which any atom of the residue is {within} "
             f"{ligand}.")
    if whole:
        about += " A residue the analysis does not list was never that close: zero."
    return {"label": f"Contact with {ligand}", "unit": "fraction of frames", "low": 0.0,
            "high": 1.0, "absent": 0.0 if whole else None, "values": rows, "about": about}


def _order_parameters(folder: Path, record: dict[str, Any],
                      frames: int | None) -> dict[str, Any] | None:
    path = folder / "order_parameters.dat"
    rows = _rows(_table(path), "s2" if _has_header(path) else 1)
    if not rows:
        return None
    found = (record.get("findings") or {}).get("order_parameters") or {}
    about = (f"The Lipari-Szabo order parameter of each residue's backbone N-H bond over "
             f"{_over(frames)}: one is rigid, zero freely reorienting. Coloured so that the "
             "more mobile end is the same colour as a large RMSF.")
    if isinstance(found, dict) and found.get("not_a_measurement"):
        about += (" The two halves of the run disagree, so these are upper bounds: motion "
                  "slower than the run reads as rigidity.")
    return {"label": "Order parameter S²", "unit": "", "low": 0.0, "high": 1.0,
            "absent": None, "reverse": True, "values": rows, "about": about}


def _bfactor_comparison(folder: Path, record: dict[str, Any],
                        frames: int | None) -> dict[str, Any] | None:
    path = folder / "bfactor_comparison.dat"
    rows = _rows(_table(path), "implied_nm" if _has_header(path) else 2)
    if not rows:
        return None
    low, high = _span(rows)
    return {"label": "RMSF implied by B-factors", "unit": "nm", "low": low, "high": high,
            "absent": None, "values": rows,
            "about": ("The fluctuation each alpha carbon's B-factor in the deposited structure "
                      "implies, from B = (8π²/3)⟨u²⟩. A B-factor holds "
                      "the lattice's static disorder and the refinement's model as well as "
                      "motion, so this is a comparison, not the same quantity.")}


def _has_header(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            return not handle.readline().startswith("#")
    except OSError:
        return False


_READERS = {
    "rmsf": _rmsf,
    "sasa": _sasa,
    "pl_contacts": _pl_contacts,
    "order_parameters": _order_parameters,
    "bfactor_comparison": _bfactor_comparison,
}


# ---------------------------------------------------------------------------
# Secondary structure
# ---------------------------------------------------------------------------

#: Where each kind of structure the viewer shows is read from.
SOURCES = ("structure", "live", "playback", "frames")

METHOD = ("DSSP (Kabsch and Sander, 1983), as MDTraj computes it and the study's "
          "secondary structure analysis reports it: helix, strand or coil.")

_LOCK = threading.Lock()
_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_CACHE_SIZE = 6


def secondary_structure(root: str | Path, of: str = "structure") -> dict[str, Any]:
    """DSSP for each residue of the structure the viewer was sent, in each of
    its frames, or why there is none.

    ``of`` is the structure (as `/structure/topology.pdb` sends it, solvent
    stripped), the live frame, the playback, or the binary frames
    (`gui/trajectory_frames.py`), read from their DCD. The answer names the
    residues as :func:`residue_runs` does and gives one string of codes for
    each frame, ``H``, ``E`` or ``C`` for each residue in that order."""
    if of not in SOURCES:
        return {"available": False, "of": of, "reason": f"No structure called {of!r}."}
    base = Path(root)
    path, text = _structure_for(base, of)
    if text is None:
        return {"available": False, "of": of,
                "reason": "There is no such structure in this study yet."}
    try:
        stat = path.stat()
        key: tuple[Any, ...] = (of, str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        if of == "frames":
            frames = base / "simulation" / "frames.dcd"
            key += (frames.stat().st_mtime_ns, frames.stat().st_size)
    except OSError:
        key = (of, str(path), hash(text))
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        if of == "frames":
            said = _dssp(text, _frames_coordinates(base))
            said["signature"] = _frames_signature(base)
        else:
            said = _dssp(text, *(_trajectory_frames(base) if of == "playback"
                                 else (None, None)))
        said["of"] = of
        _CACHE[key] = said
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.pop(next(iter(_CACHE)))
        return said


def _frames_coordinates(base: Path) -> Any:
    """The binary frames' coordinates, in nm, as the viewer is sent them."""
    try:
        import mdtraj as md

        from fastmdxplora.utils.native_output import suppress_native_output

        with suppress_native_output():
            with md.formats.DCDTrajectoryFile(str(base / "simulation" / "frames.dcd")) as handle:
                xyz = handle.read()[0]
        return xyz / 10.0
    except Exception:  # noqa: BLE001 - said by the caller as no coordinates
        return None


def _frames_signature(base: Path) -> str | None:
    try:
        index = json.loads((base / "simulation" / "frames_index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return index.get("signature") if isinstance(index, dict) else None


def _structure_for(base: Path, of: str) -> tuple[Path, str | None]:
    simulation = base / "simulation"
    if of == "frames":
        path = simulation / "frames_topology.pdb"
        try:
            return path, path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return path, None
    if of in ("live", "playback"):
        path = simulation / ("live_frame.pdb" if of == "live" else "playback.pdb")
        try:
            return path, path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return path, None
    from fastmdxplora.gui.protein_preview import find_structure, find_system
    from fastmdxplora.gui.server import _display_structure_bytes

    target = find_system(base)
    if target is None or not target.is_file():
        target = find_structure(base)
    if target is None or not target.is_file():
        return base, None
    return target, _display_structure_bytes(target).decode("utf-8", errors="replace")


def _trajectory_frames(base: Path) -> tuple[Any, Any]:
    """The playback's frames as the trajectory holds them, and the topology
    they were written from; ``(None, None)`` where it did not come from one.

    A playback made from the trajectory is written to PDB, three decimals of
    an Angstrom, and DSSP's hydrogen-bond energy has a threshold: a bond on
    it can fall either side when the coordinates are rounded, and the
    cartoon then disagreed with the secondary structure analysis, which read
    the trajectory, about that residue in that frame. Read from the
    trajectory, the coordinates are the ones the analysis read. The topology
    keeps residues apart that the playback cannot: it is written without
    insertion codes, and MDTraj reads 184 and 184A from it as one residue."""
    try:
        index = json.loads((base / "simulation" / "playback_index.json")
                           .read_text(encoding="utf-8"))
        if index.get("source_kind") != "production-dcd":
            return None, None
        trajectory = base / str(index["source_signature"]).split(":", 1)[0]
        frames = [int(frame) for frame in index["frame_indices"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None, None
    # The topology and atoms the playback was written from
    # (gui/trajectory_playback.py).
    simulation = base / "simulation"
    topology = simulation / "trajectory_topology.pdb"
    if not topology.is_file():
        topology = simulation / "topology.pdb"
    if trajectory.parent.name == "joined":
        try:
            named = Path(str(json.loads((base / "joined" / "joined.json")
                                        .read_text(encoding="utf-8")).get("topology") or ""))
        except (OSError, ValueError):
            named = Path()
        if named.is_file():
            topology = named
    if not trajectory.is_file() or not topology.is_file():
        return None, None
    try:
        import mdtraj as md
        import numpy as np

        from fastmdxplora.utils.native_output import suppress_native_output

        with suppress_native_output():
            whole = md.load_topology(str(topology))
            shown = whole.select("not water")
            read, lengths, angles = [], [], []
            with md.formats.DCDTrajectoryFile(str(trajectory)) as handle:
                for frame in frames:
                    handle.seek(frame)
                    xyz, length, angle = handle.read(1, atom_indices=shown)
                    read.append(xyz[0])
                    lengths.append(None if length is None else length[0])
                    angles.append(None if angle is None else angle[0])
        topology_shown = whole.subset(shown)
        boxed = all(length is not None and np.all(np.asarray(length) > 0) for length in lengths)
        coordinates = md.Trajectory(
            np.asarray(read, dtype=np.float32) / 10.0, topology_shown,
            unitcell_lengths=np.asarray(lengths, dtype=np.float32) / 10.0 if boxed else None,
            unitcell_angles=np.asarray(angles, dtype=np.float32) if boxed else None)
        if boxed:
            # Whole, as the playback and the analyses have them: DSSP of a
            # chain split across the box is DSSP of two pieces.
            from fastmdxplora.analysis.loading import _made_whole

            coordinates = _made_whole(coordinates)
        return coordinates.xyz, topology_shown
    except Exception:  # noqa: BLE001 - the playback's own coordinates stand in
        return None, None


def residue_runs(lines: list[str]) -> list[tuple[str, int, str, str, int, list[int]]]:
    """The residues of one model, in order, as the viewer will find them.

    Each is ``(chain, number, insertion code, name, occurrence, atoms)``: a
    new residue starts wherever one of the first four changes from the atom
    before, or an atom's name comes round again (two residues of one name
    and number, side by side, where an insertion code was lost); the
    occurrence counts how many residues before it had the same four, and
    the atoms are its positions among the model's atom lines."""
    runs: list[tuple[str, int, str, str, int, list[int]]] = []
    seen: dict[tuple[str, int, str, str], int] = {}
    previous: tuple[str, int, str, str] | None = None
    names: set[str] = set()
    for position, line in enumerate(lines):
        try:
            number = int(line[22:26])
        except ValueError:
            number = 0
        # Read as 3Dmol reads them, so the viewer finds the same residues.
        key = (line[21:22].strip(), number, line[26:27].strip(), line[17:20].replace(" ", ""))
        name = line[12:16].replace(" ", "")
        if key != previous or name in names:
            occurrence = seen.get(key, 0)
            seen[key] = occurrence + 1
            runs.append((*key, occurrence, []))
            previous = key
            names = set()
        names.add(name)
        runs[-1][5].append(position)
    return runs


def _models(text: str) -> list[list[str]]:
    """Each model's atom lines; one model where there are no MODEL records.

    Only an atom's first alternate location is kept, as MDTraj keeps it."""
    models: list[list[str]] = [[]]
    for line in text.splitlines():
        if line.startswith("ENDMDL"):
            if models[-1]:
                models.append([])
            continue
        if line.startswith(("ATOM", "HETATM")) and line[16:17] in (" ", "A", ""):
            models[-1].append(line)
    return [model for model in models if model]


def _same_residues(topology: Any, runs: list[Any], atoms: int) -> bool:
    """Whether MDTraj's residues are the runs, one to one: as many, each of
    the same number and with as many atoms."""
    if topology.n_atoms != atoms or topology.n_residues != len(runs):
        return False
    return all(residue.resSeq == run[1] and residue.n_atoms == len(run[5])
               for residue, run in zip(topology.residues, runs))


def _dssp(text: str, coordinates: Any = None, topology: Any = None) -> dict[str, Any]:
    import tempfile

    import mdtraj as md
    import numpy as np

    from fastmdxplora.utils.native_output import suppress_native_output

    models = _models(text)
    if not models:
        return {"available": False, "reason": "The structure has no atoms."}
    first = models[0]
    if any(len(model) != len(first) for model in models):
        return {"available": False,
                "reason": "Its frames do not all have the same atoms."}
    runs = residue_runs(first)
    if topology is None or not _same_residues(topology, runs, len(first)):
        if topology is not None:
            # Read with a topology that is not this file's: not these atoms.
            coordinates = None
        try:
            with tempfile.TemporaryDirectory() as folder:
                one = Path(folder) / "model.pdb"
                one.write_text("\n".join(first) + "\nEND\n", encoding="utf-8")
                with suppress_native_output():
                    topology = md.load_pdb(str(one)).topology
        except Exception as exc:  # noqa: BLE001 - said, not raised
            return {"available": False, "reason": f"MDTraj could not read it: {exc}"}
    if not _same_residues(topology, runs, len(first)):
        return {"available": False,
                "reason": "Its residues could not be matched one to one with MDTraj's."}
    protein = [residue for residue in topology.residues if residue.is_protein]
    if not protein:
        return {"available": False, "reason": "It has no protein."}
    if coordinates is not None and coordinates.ndim == 3 \
            and coordinates.shape[1:] == (len(first), 3) \
            and (coordinates.shape[0] == len(models) or len(models) == 1):
        # The coordinates given are the frames: the trajectory's own, for the
        # playback, or the binary frames, whose topology is one model.
        xyz = coordinates
        if len(models) == 1:
            models = [first] * coordinates.shape[0]
    else:
        try:
            xyz = np.array([[(float(line[30:38]), float(line[38:46]), float(line[46:54]))
                             for line in model] for model in models],
                           dtype=np.float32) / 10.0
        except ValueError:
            return {"available": False, "reason": "A coordinate could not be read."}
    atoms = [atom.index for residue in protein for atom in residue.atoms]
    trajectory = md.Trajectory(xyz[:, atoms, :], topology.subset(atoms))
    with suppress_native_output():
        codes = md.compute_dssp(trajectory, simplified=True)
    keep = [column for column in range(codes.shape[1]) if codes[0, column] != "NA"]
    if not keep:
        return {"available": False,
                "reason": "No residue has a protein backbone for DSSP to read."}
    named = [runs[residue.index] for residue in protein]
    residues = [list(named[column][:5]) for column in keep]
    frames = ["".join(codes[frame, keep]) for frame in range(codes.shape[0])]
    return {"available": True, "method": METHOD, "residues": residues, "frames": frames,
            "n_frames": len(frames),
            # Each model's first atom where it stands: the viewer compares it
            # with the file it drew, which a run may have rewritten since.
            "fingerprint": "".join(model[0][30:54] for model in models)}
