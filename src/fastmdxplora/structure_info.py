"""Structure metadata helpers for the live dashboard.

The dashboard needs lightweight, failure-isolated metadata for whichever
structure is being displayed. PDB records are parsed as fixed-width text and
mmCIF records with OpenMM's lazy reader. Results are cached by file version,
and always returned as JSON-serialisable dictionaries.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from functools import lru_cache
from math import isfinite
from pathlib import Path
from typing import Any

from fastmdxplora.ligand_detection import (
    ION_RESNAMES,
    POLYMER_RESNAMES,
    WATER_RESNAMES,
    detect_ligands,
)

# Keep the lightweight metadata scanner bounded. The viewer prefers the
# prepared solute PDB, so rendering paths stay well inside this limit.
_MAX_PDB_BYTES_FOR_INLINE_SCAN = 8 * 1024 * 1024

# Saying what a run contains means reading the solvated system, and a water
# box puts a modest protein past 8 MB: roughly 100k atoms is 8 MB of PDB, so
# the ceiling above would have turned "no ligand, no water, no ions" into
# "metadata not available" rather than into the answer. The scan streams a
# line at a time and is cached per file version, so the cost is one pass per
# run, not one per poll -- while still refusing a file large enough to stall
# the handler.
_MAX_PDB_BYTES_FOR_SYSTEM_SCAN = 256 * 1024 * 1024
_COORD_SANITY_LIMIT = 1_000_000.0  # angstrom


def _metadata_records(path: Path, *, model_ids: set[str] | None = None) -> Iterator[
    tuple[bool, str, str, str, tuple[str, str, str]]
]:
    """Yield first-model atoms, optionally collecting all model declarations."""
    if path.suffix.lower() in {".cif", ".mmcif"}:
        from fastmdxplora.refusals import StudyError
        from fastmdxplora.setup.mmcif import _given, _read_first_block, _rows

        rows = _rows(_read_first_block(path), "atom_site")
        if not rows:
            raise StudyError(
                "mmCIF atom_site contains no atoms.",
                code="setup.structure.unreadable",
                path=str(path),
            )
        first_model = _given(rows[0].get("pdbx_PDB_model_num")) or "1"
        for row in rows:
            model = _given(row.get("pdbx_PDB_model_num")) or "1"
            if model_ids is not None:
                model_ids.add(model)
            if model != first_model:
                continue
            chain = _given(row.get("auth_asym_id")) or _given(row.get("label_asym_id"))
            resname = _given(row.get("auth_comp_id")) or _given(row.get("label_comp_id"))
            resi = _given(row.get("auth_seq_id")) or _given(row.get("label_seq_id"))
            group = _given(row.get("group_PDB")).upper()
            coordinates = tuple(row.get(f"Cartn_{axis}", "") for axis in "xyz")
            if (not all((chain, resname, resi)) or group not in {"ATOM", "HETATM"}
                    or not all(isfinite(float(value)) for value in coordinates)):
                raise StudyError(
                    "mmCIF atom_site lacks usable residue identity or coordinates.",
                    code="setup.structure.unreadable", path=str(path),
                )
            yield (
                group == "HETATM", chain, resname.upper(),
                resi + _given(row.get("pdbx_PDB_ins_code")),
                coordinates,
            )
        return

    model = 0
    past_the_first = False
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith("MODEL"):
                model += 1
                if model_ids is not None:
                    model_ids.add(str(model))
                continue
            if line.startswith("ENDMDL"):
                if model_ids is None:
                    return
                past_the_first = True
                continue
            if past_the_first or not line.startswith(("ATOM", "HETATM")):
                continue
            yield (
                line.startswith("HETATM"),
                line[21:22].strip() or "A",
                line[17:20].strip().upper(),
                line[22:27].strip(),
                (line[30:38], line[38:46], line[46:54]),
            )


def count_structure(path: str | Path, *, max_bytes: int | None = None) -> dict[str, Any]:
    """Return chain/residue/atom/ligand counters for a PDB or mmCIF file.

    Results are cached by absolute path, file size, and nanosecond mtime.  A
    fresh dictionary is returned to callers so API code may safely enrich the
    payload without mutating the cached value.  ``max_bytes`` raises the size
    the caller is willing to scan: the composition of a simulated system is
    worth a solvated-topology pass, a browser preview is not.
    """

    limit = _MAX_PDB_BYTES_FOR_INLINE_SCAN if max_bytes is None else int(max_bytes)
    p = Path(path)
    if not p.is_file():
        return {"valid": False, "reason": "missing", "path": p.as_posix()}
    try:
        stat = p.stat()
    except OSError as exc:
        return {"valid": False, "reason": f"stat-error: {exc}", "path": p.as_posix()}
    if stat.st_size > limit:
        return {
            "valid": False,
            "reason": "too-large",
            "path": p.as_posix(),
            "size": stat.st_size,
            "max_inline_bytes": limit,
        }
    result = _count_structure_cached(
        str(p.resolve()),
        int(stat.st_mtime_ns),
        int(stat.st_size),
    )
    return dict(result)


@lru_cache(maxsize=32)
def _count_structure_cached(
    path_string: str,
    _mtime_ns: int,
    file_size: int,
) -> dict[str, Any]:
    p = Path(path_string)
    protein_chains: set[str] = set()
    all_chains: set[str] = set()
    protein_residue_keys: set[tuple[str, str, str]] = set()
    non_protein_residue_keys: set[tuple[str, str, str]] = set()
    water_residue_keys: set[tuple[str, str, str]] = set()
    ion_residue_keys: set[tuple[str, str, str]] = set()

    atoms = 0
    protein_atoms = 0
    hetatm_atoms = 0
    water_atoms = 0
    ion_atoms = 0
    min_coords = [float("inf"), float("inf"), float("inf")]
    max_coords = [float("-inf"), float("-inf"), float("-inf")]

    # The first model only. Every model of an NMR entry was counted, so a
    # 20-model file showed twenty times its atoms. Setup reads the first
    # model, and this describes what setup will read.
    model_ids: set[str] = set()
    try:
        for is_hetatm, chain_id, resname, resi, coordinates in _metadata_records(
            p, model_ids=model_ids
        ):
            atoms += 1
            if is_hetatm:
                hetatm_atoms += 1
            key = (chain_id, resname, resi)
            all_chains.add(chain_id)

            # Parse coordinates before classifying so the overall extent
            # remains meaningful even for solvated structures.
            try:
                x, y, z = (float(value) for value in coordinates)
                if all(-_COORD_SANITY_LIMIT <= value <= _COORD_SANITY_LIMIT
                       for value in (x, y, z)):
                    for idx, value in enumerate((x, y, z)):
                        min_coords[idx] = min(min_coords[idx], value)
                        max_coords[idx] = max(max_coords[idx], value)
            except (ValueError, TypeError):
                pass

            if resname in WATER_RESNAMES:
                water_atoms += 1
                water_residue_keys.add(key)
                continue
            if resname in ION_RESNAMES:
                ion_atoms += 1
                ion_residue_keys.add(key)
                continue
            if resname in POLYMER_RESNAMES:
                protein_atoms += 1
                protein_chains.add(chain_id)
                protein_residue_keys.add(key)
                continue

            # Any remaining residue is a ligand/cofactor candidate. The
            # detector applies its own cofactor exclusions.
            non_protein_residue_keys.add(key)
    except ImportError as exc:
        return {"valid": False, "reason": f"dependency-unavailable: {exc}", "path": p.as_posix()}
    except OSError as exc:
        return {"valid": False, "reason": f"read-error: {exc}", "path": p.as_posix()}
    except Exception as exc:
        return {"valid": False, "reason": f"parse-error: {exc}", "path": p.as_posix()}

    ligand_info = detect_ligands(non_protein_residue_keys)
    if min_coords[0] == float("inf"):
        bounding_extent = [0.0, 0.0, 0.0]
        centroid: list[float | None] = [None, None, None]
    else:
        bounding_extent = [max_coords[i] - min_coords[i] for i in range(3)]
        centroid = [(min_coords[i] + max_coords[i]) / 2 for i in range(3)]

    return {
        "valid": True,
        "path": p.as_posix(),
        "size": file_size,
        "models": max(len(model_ids), 1),
        "atoms": atoms,
        "protein_atoms": protein_atoms,
        "hetatm_atoms": hetatm_atoms,
        "water_atoms": water_atoms,
        "ion_atoms": ion_atoms,
        "chains": sorted(protein_chains or all_chains),
        "all_chains": sorted(all_chains),
        "n_chains": len(protein_chains or all_chains),
        "protein_residues": len(protein_residue_keys),
        "water_residues": len(water_residue_keys),
        "ions": len(ion_residue_keys),
        # Which ones, not only how many. A count cannot tell a structural
        # zinc from added salt, and that distinction decides whether a run
        # is going to hold its metal in place.
        "ion_resnames": sorted({key[1] for key in ion_residue_keys}),
        "ligand_residues_total": len(ligand_info["instances"]),
        "ligand_resnames": ligand_info["resnames"],
        "ligand_instances": ligand_info["instances"],
        "extents_angstrom": [round(value, 2) for value in bounding_extent],
        "centroid_angstrom": [
            round(value, 2) if value is not None else None for value in centroid
        ],
    }


def ligand_atom_counts(path: str | Path) -> dict[str, int]:
    """Return ligand residue-name to atom-count mappings.

    Both ``ATOM`` and ``HETATM`` records are considered because some input
    generators encode small molecules using ``ATOM`` records.
    """

    p = Path(path)
    if not p.is_file():
        return {}
    counts: Counter[str] = Counter()
    try:
        for _is_hetatm, _chain, resname, _resi, _coordinates in _metadata_records(p):
            if resname in WATER_RESNAMES or resname in ION_RESNAMES or resname in POLYMER_RESNAMES:
                continue
            counts[resname] += 1
    except Exception:
        return {}
    return dict(counts)
