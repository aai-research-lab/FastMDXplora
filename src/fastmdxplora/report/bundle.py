"""Project bundle: zip the entire study into a shareable archive.

Produces a single ``project_bundle.zip`` containing every artifact written
during the run: setup, simulation, analysis and report. The bundle is
suitable for attaching to a publication's supplementary materials or sharing
with a collaborator.

The top-level ``manifest.json`` and ``resolved_config.yml`` are added by the
orchestrator once every phase has finished, because they do not exist while
this runs. Until they were, the archive went out with thirteen megabytes of
results and no record of what produced them -- and this docstring said
otherwise.
"""

from __future__ import annotations

from fastmdxplora.replaced import PREVIOUS
from fastmdxplora.utils.logging import get_logger
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmdxplora.orchestrator import FastMDXplora

logger = get_logger("report.bundle")


# Paths inside the project root that should be excluded from the bundle
EXCLUDE_NAMES: frozenset[str] = frozenset(
    {"project_bundle.zip", "fastmdxplora.log", ".DS_Store"}
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
        # What a phase run again replaced (`fastmdxplora.replaced`): kept in
        # the study's folder, not in the archive of what the report describes.
        if p.relative_to(root).parts[0] == PREVIOUS:
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

    files = _iter_project_files(project_root, bundle_path)

    with zipfile.ZipFile(
        bundle_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as zf:
        for f in files:
            arcname = f.relative_to(project_root).as_posix()
            zf.write(f, arcname=str(arcname))

    logger.debug("bundle: wrote %s (%d files)", bundle_path, len(files))
    return ["project_bundle.zip"]
