"""Project bundle: zip the entire study into a shareable archive.

Produces a single ``project_bundle.zip`` containing every artifact written
during the run: setup, simulation, analysis and report, without the
Viewer's and the live view's scratch (`fastmdxplora.study_files`). The bundle is
suitable for attaching to a publication's supplementary materials or sharing
with a collaborator.

It holds results and records, not working matrices (:func:`left_out`): the
clustering's whole frame-by-frame matrix, and any matrix the analyses saved
past :data:`MOST_MATRIX_BYTES`, stay in the study's folder, and
``left_out_of_bundle.txt`` inside the bundle lists each with where it is. A
chignolin study's bundle was 7.3 GB, most of it two copies of one matrix
the dendrogram never needed. The report says the same list where it names
the bundle.

The top-level ``manifest.json`` and ``resolved_config.yml`` are added by the
orchestrator once every phase has finished, because they do not exist while
this runs. Until they were, the archive went out with thirteen megabytes of
results and no record of what produced them -- and this docstring said
otherwise.
"""

from __future__ import annotations

from fastmdxplora.replaced import PREVIOUS
from fastmdxplora.study_files import is_scratch
from fastmdxplora.utils.logging import get_logger
import zipfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmdxplora.orchestrator import FastMDXplora

logger = get_logger("report.bundle")


# Paths inside the project root that should be excluded from the bundle
# The study's carrying-on lock (simulation/resume.py) is held while the
# report is made; on Windows a locked file cannot be read, even here.
EXCLUDE_NAMES: frozenset[str] = frozenset(
    {"project_bundle.zip", "fastmdxplora.log", ".DS_Store", ".fastmdxplora-carrying-on"}
)
EXCLUDE_DIR_NAMES: frozenset[str] = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", ".ipynb_checkpoints"}
)
EXCLUDE_SUFFIXES: tuple[str, ...] = (
    ".pyc",
    ".pyo",
    ".tmp",
    ".temp",
    ".part",
    ".swp",
    "~",
)


#: Past this, a matrix the analyses saved (``.npy``, ``.npz``) is a working
#: file the bundle leaves in the study's folder, and says so. 100 MB: the
#: clustering's own map is at most about 4 MB now, and its whole matrix was
#: 3.3 GB for 31,000 frames in a study clustered before.
MOST_MATRIX_BYTES = 100 * 10**6

#: The clustering's whole frame-by-frame matrix: written only when asked
#: (``save_full_matrix``), or by a study clustered before, and left out
#: whatever its size.
WORKING_MATRICES = frozenset({"cluster_rmsd_matrix_full.npy",
                              "hierarchical_distance_matrix.npy"})

#: The list of what was left out, written inside the bundle.
LEFT_OUT_LIST = "left_out_of_bundle.txt"


def left_out(rel: str, size: int) -> str | None:
    """Why the study's file at ``rel`` (``size`` bytes) is left out of the
    bundle, or None where it goes in."""
    path = PurePosixPath(rel)
    if "analysis" not in path.parts[:-1]:
        return None
    if path.name in WORKING_MATRICES:
        return "the clustering's whole frame-by-frame matrix, a working file"
    if path.suffix.lower() in (".npy", ".npz") and size > MOST_MATRIX_BYTES:
        return (f"a matrix the analyses saved, past the bundle's bound of "
                f"{size_said(MOST_MATRIX_BYTES)}")
    return None


def size_said(n_bytes: int) -> str:
    """Bytes as a person reads them: 7.7 GB, 98 MB, 512 KB."""
    for unit, scale in (("GB", 10**9), ("MB", 10**6), ("KB", 10**3)):
        if n_bytes >= scale:
            value = n_bytes / scale
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
    return f"{n_bytes} bytes"


def left_out_of_bundle(root: Path) -> list[tuple[str, int, str]]:
    """What a bundle of the study at ``root`` leaves out: each file's path in
    the study's folder, its size and why, in path order. Read by the bundle
    and by the report, so the two give one list."""
    bundle_path = root / "report" / "project_bundle.zip"
    return _split(root, bundle_path)[1]


def _split(root: Path, bundle_path: Path) -> tuple[list[Path], list[tuple[str, int, str]]]:
    files: list[Path] = []
    left: list[tuple[str, int, str]] = []
    for path in _iter_project_files(root, bundle_path):
        rel = path.relative_to(root).as_posix()
        size = path.stat().st_size
        why = left_out(rel, size)
        if why is None:
            files.append(path)
        else:
            left.append((rel, size, why))
    return files, sorted(left)


def left_out_said(left: list[tuple[str, int, str]]) -> str:
    """The list written inside the bundle."""
    lines = ["These files of the study were left out of this bundle. They are "
             "working matrices the analyses saved, which its results and records "
             "do not need; each is in the study's folder at the path given.", ""]
    lines += [f"{rel}\t{size_said(size)}\t{why}" for rel, size, why in left]
    return "\n".join(lines) + "\n"


def _iter_project_files(root: Path, bundle_path: Path) -> list[Path]:
    """Walk the project tree, excluding the bundle itself and the live log."""
    files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.resolve() == bundle_path.resolve():
            continue
        if p.name in EXCLUDE_NAMES:
            continue
        if any(part in EXCLUDE_DIR_NAMES for part in p.relative_to(root).parts[:-1]):
            continue
        rel = p.relative_to(root)
        # What a phase run again replaced (`fastmdxplora.replaced`): kept in
        # the study's folder, not in the archive of what the report describes.
        # Nor the windows a study of windows ran again, nor a data deposit,
        # itself an archive of the study.
        if rel.parts[0] in (PREVIOUS, "superseded", "deposit"):
            continue
        # The Viewer's and the live view's scratch, written again when
        # needed: trypsin's bundle carried the 200 snapshots the live view
        # keeps, 52 MB of PDB beside a 7.8 MB trajectory.
        if is_scratch(rel.as_posix()):
            continue
        if p.name.endswith(EXCLUDE_SUFFIXES):
            continue
        files.append(p)
    return files


def build_bundle(
    *,
    orchestrator: "FastMDXplora",
    output_dir: Path,
) -> list[str]:
    """Zip the project tree.

    The archive is written inside the report directory (so a re-run that
    skips the report phase doesn't leave a stale bundle in the project
    root). Paths inside the archive are relative to the project root.
    """
    project_root = orchestrator.output_dir
    bundle_path = output_dir / "project_bundle.zip"

    files, left = _split(project_root, bundle_path)

    with zipfile.ZipFile(
        bundle_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as zf:
        for f in files:
            arcname = f.relative_to(project_root).as_posix()
            zf.write(f, arcname=str(arcname))
        if left:
            zf.writestr(LEFT_OUT_LIST, left_out_said(left))

    if left:
        logger.info("bundle: left out %d working file(s), %s, listed in %s inside it",
                    len(left), size_said(sum(size for _, size, _ in left)), LEFT_OUT_LIST)
    logger.debug("bundle: wrote %s (%d files)", bundle_path, len(files))
    return ["project_bundle.zip"]
