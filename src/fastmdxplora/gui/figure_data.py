"""The numbers behind an analysis's other figures, for the GUI to plot them
in the page's own colours.

The Analysis page plots an analysis's own series from its numbers
(:mod:`fastmdxplora.gui.series`); every other figure was the picture the
analysis wrote, white in a dark page, with nothing to point at. This reads
the data each of those figures was plotted from and gives it in the shape
of the figure, so the page can plot it as the analysis did, in the scheme's
colours, with each value, time and frame under the pointer:

- secondary structure, residue against time, each cell its class;
- clustering: each frame's cluster over time, the clusters' populations,
  the hierarchy, and the RMSD between frames;
- a projection (PCA, t-SNE, UMAP) coloured by time, and its free-energy
  landscape;
- the backbone dihedrals' density over the Ramachandran plane.

Only what the analysis wrote is read, and a figure whose numbers are not
there keeps its picture. The frame and time of each frame analysed are
taken as the analysis loaded the trajectory (its stride and first frame).
"""

from __future__ import annotations

import csv
import json
import math
import re
import threading
from pathlib import Path
from typing import Any

__all__ = ["figure_payload", "FIGURES"]

#: The figures plotted here, by their path in the study.
FIGURES = re.compile(
    r"^analysis/(?:"
    r"(?P<ss>ss)/ss"
    r"|cluster/cluster_(?P<cluster>[a-z]+)(?P<cluster_part>_counts|_dendrogram)?"
    r"|cluster/(?P<matrix>cluster_rmsd_matrix)"
    r"|dimred/dimred_(?P<dimred>[a-z]+)(?P<landscape>_landscape)?"
    r"|(?P<dihedrals>dihedrals)/dihedrals"
    r")\.png$")

#: More points than a plot on a page can show; longer is thinned evenly.
MOST_POINTS = 5000
#: More cells a side than a map on a page can show; larger is averaged in
#: blocks.
MOST_CELLS = 200
#: More frames than a secondary-structure map is given across.
MOST_COLUMNS = 1500
#: A hierarchy of more frames is shown to this many of its last merges.
MOST_LEAVES = 400

#: The three classes secondary structure is shown in, from DSSP's codes as
#: the analysis groups them (``ss.HELIX_CODES``, ``ss.STRAND_CODES``).
SS_CLASSES = (("H", "Helix"), ("E", "β-strand"), ("C", "Coil/Other"))
_HELIX = frozenset("HGI")
_STRAND = frozenset("EB")


def figure_payload(root: str | Path, figure: str) -> dict[str, Any]:
    """The numbers of one figure, in its shape, or why there are none."""
    root = Path(root)
    found = FIGURES.fullmatch(figure or "")
    if not found:
        return {"ok": False, "reason": "not a figure plotted here"}
    try:
        if found["ss"]:
            payload = _secondary_structure(root)
        elif found["matrix"]:
            payload = _rmsd_matrix(root)
        elif found["cluster"]:
            method, part = found["cluster"], found["cluster_part"]
            if part == "_counts":
                payload = _populations(root, method)
            elif part == "_dendrogram":
                payload = _hierarchy(root, method)
            else:
                payload = _cluster_labels(root, method)
        elif found["dimred"]:
            payload = (_landscape(root, found["dimred"]) if found["landscape"]
                       else _projection(root, found["dimred"]))
        else:
            payload = _ramachandran(root)
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        return {"ok": False, "reason": f"its numbers could not be read: {exc}"}
    if payload is None:
        return {"ok": False, "reason": "the analysis wrote no numbers for it"}
    return {"ok": True, "figure": figure, **payload}


# ---------------------------------------------------------------------------
# The clock of the frames analysed
# ---------------------------------------------------------------------------

def _clock(root: Path, indices: list[int]) -> tuple[list[int], list[float], str]:
    """The trajectory frame and time (ns) of each frame analysed, by its
    index among the frames loaded, or the frame where no clock is kept."""
    from fastmdxplora.gui.series import _json, _non_negative_int, _positive_int

    manifest = _json(root / "analysis" / "analysis_manifest.json")
    loaded = manifest.get("load_kwargs") if isinstance(manifest.get("load_kwargs"), dict) else {}
    stride = _positive_int(loaded.get("stride")) or 1
    first = _non_negative_int(loaded.get("first")) or 0
    interval = loaded.get("saving_interval_ps")
    timed = isinstance(interval, (int, float)) and not isinstance(interval, bool) and interval > 0
    recorded = manifest.get("frame_times_ps")
    if timed and isinstance(recorded, list) and recorded and all(
            isinstance(t, (int, float)) for t in recorded) and max(indices, default=0) < len(recorded):
        frames = [int(round(float(recorded[k]) / float(interval))) - 1 for k in indices]
        return frames, [round(float(recorded[k]) / 1000.0, 12) for k in indices], "Time (ns)"
    frames = [(first + k) * stride for k in indices]
    if timed:
        return frames, [round((f + 1) * float(interval) / 1000.0, 12) for f in frames], "Time (ns)"
    return frames, [float(f) for f in frames], "Frame"


def _linked(root: Path) -> bool:
    from fastmdxplora.gui.series import _json, _of_the_played_trajectory

    return _of_the_played_trajectory(root, _json(root / "analysis" / "analysis_manifest.json"))


def _table(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [row for row in csv.DictReader(line for line in fh if not line.startswith("#"))]


def _thinned(n: int, most: int) -> list[int]:
    every = max(1, math.ceil(n / most))
    return list(range(0, n, every))


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _grid(values: Any) -> list[list[float | None]]:
    """A 2-D array as rows, an empty or undefined cell as null."""
    return [[(float(f"{float(v):.5g}") if math.isfinite(float(v)) else None) for v in row]
            for row in values]


def _options(root: Path, analysis: str) -> dict[str, Any]:
    try:
        record = json.loads((root / "analysis" / analysis / "options.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    options = record.get("options") if isinstance(record, dict) else None
    return options if isinstance(options, dict) else {}


#: Payloads read from a large file, kept by the file's size and time written.
_NOT_KEPT = object()
_KEPT: dict[str, tuple[tuple[int, int], Any]] = {}
_KEPT_LOCK = threading.Lock()


def _kept(path: Path) -> Any:
    """What was read from ``path`` before, unless it has changed since; None
    where it does not exist."""
    try:
        seen = path.stat()
    except OSError:
        return None
    with _KEPT_LOCK:
        found = _KEPT.get(str(path))
    if found and found[0] == (seen.st_size, seen.st_mtime_ns):
        return found[1]
    return _NOT_KEPT


def _keep(path: Path, payload: Any) -> None:
    try:
        seen = path.stat()
    except OSError:
        return
    with _KEPT_LOCK:
        if len(_KEPT) > 64:
            _KEPT.clear()
        _KEPT[str(path)] = ((seen.st_size, seen.st_mtime_ns), payload)


# ---------------------------------------------------------------------------
# Secondary structure
# ---------------------------------------------------------------------------

def _secondary_structure(root: Path) -> dict[str, Any] | None:
    path = root / "analysis" / "ss" / "ss.dat"
    if not path.is_file():
        return None
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(line for line in fh if not line.startswith("#"))
        header = next(reader, None)
        rows = [row for row in reader if row]
    if not header or not rows or header[0] != "frame":
        return None
    residues = header[1:]
    keep = _thinned(len(rows), MOST_COLUMNS)
    indices = [int(float(rows[i][0])) for i in keep]
    frames, x, x_label = _clock(root, indices)

    def letter(code: str) -> str:
        code = (code or " ").strip()[:1] or " "
        return "H" if code in _HELIX else "E" if code in _STRAND else "C"

    states = ["".join(letter(code) for code in rows[i][1:1 + len(residues)]) for i in keep]
    return {
        "kind": "states", "title": "Secondary structure (DSSP)",
        "x": x, "x_label": x_label, "frames": frames, "linked": _linked(root),
        "residues": residues, "y_label": "Residue", "states": states,
        "classes": [{"code": code, "label": label} for code, label in SS_CLASSES],
        "thinned": len(rows) if len(keep) < len(rows) else None,
    }


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def _cluster_labels(root: Path, method: str) -> dict[str, Any] | None:
    path = root / "analysis" / "cluster" / f"cluster_{method}.dat"
    if not path.is_file():
        return None
    rows = _table(path)
    if not rows:
        return None
    keep = _thinned(len(rows), MOST_POINTS)
    indices = [int(float(rows[i]["frame"])) for i in keep]
    frames, x, x_label = _clock(root, indices)
    labels = [int(float(rows[i]["cluster"])) for i in keep]
    return {
        "kind": "labels", "method": method,
        "title": f"Conformational clustering ({method})",
        "x": x, "x_label": x_label, "frames": frames, "linked": _linked(root),
        "labels": labels, "clusters": sorted(set(labels)), "y_label": "Cluster",
        "thinned": len(rows) if len(keep) < len(rows) else None,
    }


def _populations(root: Path, method: str) -> dict[str, Any] | None:
    folder = root / "analysis" / "cluster"
    table = folder / f"cluster_{method}_populations.csv"
    counts: dict[int, int] = {}
    if (folder / f"cluster_{method}.dat").is_file():
        for row in _table(folder / f"cluster_{method}.dat"):
            label = int(float(row["cluster"]))
            counts[label] = counts.get(label, 0) + 1
    total = sum(counts.values())
    clusters: list[dict[str, Any]] = []
    if table.is_file():
        for row in _table(table):
            medoid = _number(row.get("medoid_frame"))
            # The medoid is numbered among the frames analysed; the Viewer
            # and the reader number the trajectory's.
            frame = _clock(root, [int(medoid)])[0][0] if medoid is not None else None
            clusters.append({
                "cluster": int(float(row["cluster"])), "frames": int(float(row["frames"])),
                "fraction": _number(row.get("fraction")),
                "medoid_frame": frame, "medoid_time_ns": _number(row.get("medoid_time_ns")),
            })
    listed = {c["cluster"] for c in clusters}
    tabled = bool(clusters)
    # Noise has no medoid and no row in the table, but is a bar of the figure.
    for label, count in sorted(counts.items()):
        if label not in listed and (label == -1 or not tabled):
            clusters.append({"cluster": label, "frames": count,
                             "fraction": count / total if total else None,
                             "medoid_frame": None, "medoid_time_ns": None})
    if not clusters:
        return None
    clusters.sort(key=lambda c: c["cluster"])
    return {"kind": "bars", "method": method, "title": f"Cluster populations ({method})",
            "clusters": clusters, "x_label": "Cluster", "y_label": "Frames",
            "linked": _linked(root)}


def _hierarchy(root: Path, method: str) -> dict[str, Any] | None:
    """The hierarchy as SciPy lays it out, each merge below the cut that
    gives the clusters asked for in the colour of the cluster most of its
    frames were put in."""
    import numpy as np

    folder = root / "analysis" / "cluster"
    path = folder / f"{method}_linkage.npy"
    if not path.is_file():
        return None
    from scipy.cluster.hierarchy import dendrogram

    z = np.load(path, allow_pickle=False)
    if z.ndim != 2 or z.shape[1] != 4 or z.shape[0] < 1:
        return None
    n = z.shape[0] + 1
    asked = _options(root, "cluster").get("n_clusters")
    k = int(asked) if isinstance(asked, (int, float)) and not isinstance(asked, bool) else 0
    # Midway between the merge that leaves k clusters and the one that
    # would leave k - 1, so the line lies on no merge.
    cut = (float(z[-k, 2]) + float(z[-(k - 1), 2])) / 2 if 2 <= k <= n - 1 else None
    truncated = n > MOST_LEAVES
    # Each link is named by its merge, so its colour is its merge's however
    # SciPy lays the links out (ties and truncation included).
    laid = dendrogram(
        z, no_plot=True, link_color_func=lambda node: str(node - n),
        truncate_mode="lastp" if truncated else None, p=MOST_LEAVES if truncated else 30,
    )
    labels: list[int] | None = None
    if (folder / f"cluster_{method}.dat").is_file():
        rows = _table(folder / f"cluster_{method}.dat")
        if len(rows) == n:
            labels = [int(float(row["cluster"])) for row in rows]
    if labels is None and cut is not None:
        from scipy.cluster.hierarchy import fcluster

        labels = [int(v) - 1 for v in fcluster(z, k, criterion="maxclust")]
    groups = _merge_groups(z, cut, labels) if cut is not None else [None] * (n - 1)
    return {
        "kind": "dendrogram", "method": method, "title": "Hierarchical clustering dendrogram",
        "icoord": [[round(v, 3) for v in row] for row in laid["icoord"]],
        "dcoord": [[round(v, 6) for v in row] for row in laid["dcoord"]],
        "groups": [groups[int(merge)] for merge in laid["color_list"]],
        "leaves": len(laid["leaves"]), "frames": n, "truncated": truncated,
        "cut": cut, "x_label": "Frame", "y_label": "Distance",
    }


def _merge_groups(z: Any, cut: float, labels: list[int]) -> list[int | None]:
    """For each merge below the cut, the cluster most of its frames were
    put in, and None for each above it."""
    n = z.shape[0] + 1
    members: list[list[int]] = [[i] for i in range(n)]
    found: list[int | None] = []
    for row in z:
        merged = members[int(row[0])] + members[int(row[1])]
        members.append(merged)
        if float(row[2]) >= cut:
            found.append(None)
            continue
        counts: dict[int, int] = {}
        for leaf in merged:
            counts[labels[leaf]] = counts.get(labels[leaf], 0) + 1
        found.append(max(counts, key=lambda key: (counts[key], -key)))
    return found


def _rmsd_matrix(root: Path) -> dict[str, Any] | None:
    import numpy as np

    path = root / "analysis" / "cluster" / "cluster_rmsd_matrix.npz"
    kept = _kept(path)
    if kept is not _NOT_KEPT:
        return kept
    payload = None
    with np.load(path, allow_pickle=False) as found:
        values = np.asarray(found["rmsd_nm"], dtype=np.float32)
        times = np.asarray(found["time_ns"], dtype=float) if "time_ns" in found.files else None
        loaded = np.asarray(found["frames"], dtype=int) if "frames" in found.files else None
    n = values.shape[0]
    if values.ndim == 2 and n >= 2 and values.shape[1] == n:
        block = max(1, math.ceil(n / MOST_CELLS))
        if block > 1:
            # Averaged in blocks of frames, in float32: a 10,000-frame
            # matrix is not copied in float64 to be shown 200 cells across.
            starts = np.arange(0, n, block)
            sizes = np.diff(np.append(starts, n)).astype(np.float32)
            rows = np.add.reduceat(values, starts, axis=0)
            values = np.add.reduceat(rows, starts, axis=1) / np.outer(sizes, sizes)
        timed = times is not None and len(times) == n and bool(np.all(np.isfinite(times)))
        if timed:
            axis = times
        else:
            indices = loaded.tolist() if loaded is not None and len(loaded) == n else list(range(n))
            axis = np.asarray(_clock(root, indices)[0], dtype=float)
        step = float(np.median(np.diff(axis))) if n > 1 else 1.0
        cells = math.ceil(n / block)
        payload = {
            "kind": "matrix", "title": "RMSD between frames",
            "values": _grid(values),
            # Each cell is `block` frames wide, the last one too.
            "extent": [float(axis[0]) - step / 2, float(axis[0]) + (cells * block - 0.5) * step],
            "axis_label": "Time (ns)" if timed else "Frame", "label": "RMSD", "unit": "nm",
            "block": block if block > 1 else None, "low": 0.0,
        }
    _keep(path, payload)
    return payload


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------

_METHOD_WORDS = {"pca": ("PCA", "PC"), "tica": ("tICA", "IC"), "tsne": ("t-SNE", "t-SNE"),
                 "umap": ("UMAP", "UMAP")}


def _axes_of(root: Path, method: str) -> list[str]:
    """Each component's name, with the share of the variance it carries
    where the method records one (PCA)."""
    import numpy as np

    word = _METHOD_WORDS.get(method, (method.upper(), method.upper()))[1]
    ratio = None
    path = root / "analysis" / "dimred" / f"dimred_{method}_modes.npz"
    if path.is_file():
        try:
            with np.load(path, allow_pickle=False) as found:
                if "ratio" in found.files:
                    ratio = [float(v) for v in found["ratio"]]
        except (OSError, ValueError):
            ratio = None
    names = []
    for i in range(2):
        share = f" ({100 * ratio[i]:.1f}%)" if ratio and i < len(ratio) else ""
        names.append(f"{word} {i + 1}{share}")
    return names


def _projection(root: Path, method: str) -> dict[str, Any] | None:
    path = root / "analysis" / "dimred" / f"dimred_{method}.dat"
    if not path.is_file():
        return None
    rows = _table(path)
    if not rows or "component_1" not in rows[0] or "component_2" not in rows[0]:
        return None
    keep = _thinned(len(rows), MOST_POINTS)
    indices = [int(float(rows[i]["frame"])) for i in keep]
    frames, t, t_label = _clock(root, indices)
    x = [_number(rows[i]["component_1"]) for i in keep]
    y = [_number(rows[i]["component_2"]) for i in keep]
    words = _METHOD_WORDS.get(method, (method.upper(), method.upper()))[0]
    unit = "nm" if method == "pca" else ""
    return {
        "kind": "projection", "method": method,
        "title": f"Dimensionality reduction ({words})",
        "x": x, "y": y, "t": t, "t_label": t_label, "frames": frames,
        "axes": _axes_of(root, method), "unit": unit, "linked": _linked(root),
        "thinned": len(rows) if len(keep) < len(rows) else None,
    }


def _landscape(root: Path, method: str) -> dict[str, Any] | None:
    import numpy as np

    path = root / "analysis" / "dimred" / f"dimred_{method}_landscape.npz"
    if not path.is_file():
        return None
    with np.load(path, allow_pickle=False) as found:
        energy = np.asarray(found["free_energy"], dtype=float)
        edges = [k for k in found.files if k.startswith("edges_")]
        if len(edges) < 2:
            return None
        edges.sort()
        ex = [float(v) for v in found[edges[0]]]
        ey = [float(v) for v in found[edges[1]]]
        unit = str(found["unit"]) if "unit" in found.files else "kJ/mol"
    words = _METHOD_WORDS.get(method, (method.upper(), method.upper()))[0]
    axes = _axes_of(root, method)
    if method == "pca":
        axes = [f"{name}, nm" for name in axes]
    # energy[i, j] is bin i along the first component, j along the second.
    return {
        "kind": "landscape", "method": method, "title": f"Free-energy landscape ({words})",
        "values": _grid(energy.T), "edges_x": ex, "edges_y": ey,
        "axes": axes, "label": "Free energy", "unit": unit, "low": 0.0,
    }


# ---------------------------------------------------------------------------
# Backbone dihedrals
# ---------------------------------------------------------------------------

def _ramachandran(root: Path) -> dict[str, Any] | None:
    import numpy as np
    import pandas as pd

    path = root / "analysis" / "dihedrals" / "dihedrals.dat"
    kept = _kept(path)
    if kept is not _NOT_KEPT:
        return kept
    asked = _options(root, "dihedrals").get("bins")
    bins = int(asked) if isinstance(asked, (int, float)) and not isinstance(asked, bool) \
        and 4 <= asked <= 360 else 72
    counts = np.zeros((bins, bins))
    pairs = 0
    # Read in pieces, two columns of float32: a long run of a large protein
    # writes millions of rows.
    for piece in pd.read_csv(path, usecols=["phi_deg", "psi_deg"], dtype="float32",
                             comment="#", chunksize=500_000):
        both = piece.dropna().to_numpy()
        if not len(both):
            continue
        found, _, _ = np.histogram2d(both[:, 0], both[:, 1], bins=bins,
                                     range=[[-180, 180], [-180, 180]])
        counts += found
        pairs += len(both)
    payload = None
    if pairs:
        payload = {
            "kind": "density", "title": "Backbone dihedrals (Ramachandran)",
            "values": _grid(np.log1p(counts.T)),
            "edges_x": list(np.linspace(-180, 180, bins + 1)),
            "edges_y": list(np.linspace(-180, 180, bins + 1)),
            "axes": ["φ (degrees)", "ψ (degrees)"], "label": "log(1 + count)", "unit": "",
            "low": 0.0, "guides": [0.0], "ticks": [-180, -90, 0, 90, 180], "pairs": pairs,
        }
    _keep(path, payload)
    return payload
