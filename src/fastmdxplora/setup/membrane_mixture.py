"""A bilayer of more than one lipid, or with leaflets that differ.

OpenMM builds a bilayer of one lipid from patches it ships, and of anything
else from a patch it is given: it tiles the patch round the protein, removes
the lipids the protein overlaps, and relaxes the rest. The patch here is
built by PACKMOL-Memgen (Schott-Verdugo and Gohlke, J. Chem. Inf. Model. 59,
2522 (2019)) from the composition asked, with CHARMM's lipid names, which
both CHARMM36 and AMBER Lipid17 have templates for, and given to OpenMM in
place of a lipid's name. Everything else about the bilayer (where the
protein is placed, the orientation checks, the packing tried again on a
NaN) is as for one lipid.

The composition is a ratio of lipids for both leaflets (``{POPC: 7, CHL1:
3}``), or one for each (``{upper: {...}, lower: {...}}``), an asymmetric
bilayer such as a plasma membrane's. The patch is a square ``patch_nm``
wide (5 nm), and its ratio is the closest whole numbers of lipids in that
area come to; the counts used are recorded.

What it is not: an equilibrated bilayer. PACKMOL places lipids without
overlaps and nothing more, and OpenMM's relaxation after tiling is short.
A mixed bilayer, cholesterol above all, needs a longer equilibration than a
pure one before its area and thickness settle, which the bilayer's own
analyses (area per lipid, thickness, order) show. Tiling also repeats the
patch, so lipids do not mix beyond its width until they diffuse.

A patch takes minutes to build (ten for 5 nm on a laptop's CPU), and is
kept in the user's cache by its composition and size, so a study run again,
or another of the same membrane, reuses it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import StudyError

#: Residues PACKMOL-Memgen's patch carries that OpenMM adds itself.
_IONS = {"K+", "Na+", "Cl-", "POT", "SOD", "CLA"}


def leaflets(composition: dict[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
    """The upper and lower leaflets' ratios, each lipid's share."""
    from fastmdxplora.config.loader import ConfigError

    if not isinstance(composition, dict) or not composition:
        raise ConfigError(
            "`setup.membrane_composition` is a ratio of lipids ({POPC: 7, CHL1: 3}), or "
            "one for each leaflet ({upper: {...}, lower: {...}}).",
            code="config.option.wrong_type")
    if set(composition) <= {"upper", "lower"}:
        if set(composition) != {"upper", "lower"}:
            raise ConfigError("An asymmetric bilayer names both leaflets, `upper` and "
                              "`lower`.", code="config.option.missing_companion")
        sides = (composition["upper"], composition["lower"])
    else:
        sides = (composition, composition)
    found = []
    for side in sides:
        if not isinstance(side, dict) or not side:
            raise ConfigError("Each leaflet is a ratio of lipids, such as {POPC: 7, CHL1: 3}.",
                              code="config.option.wrong_type")
        ratio = {}
        for name, share in side.items():
            try:
                value = float(share)
            except (TypeError, ValueError):
                value = -1.0
            if value <= 0:
                raise ConfigError(f"{name}'s share of its leaflet is a positive number; it "
                                  f"was {share!r}.", code="config.option.out_of_range")
            ratio[str(name).upper()] = value
        found.append(ratio)
    return found[0], found[1]


def _arguments(upper: dict[str, float], lower: dict[str, float]) -> tuple[str, str]:
    """PACKMOL-Memgen's ``--lipids`` and ``--ratio``: ``A:B//C`` for leaflets."""
    def side(ratio: dict[str, float]) -> tuple[str, str]:
        return (":".join(ratio), ":".join(f"{v:g}" for v in ratio.values()))

    lipids_up, ratio_up = side(upper)
    if upper == lower:
        return lipids_up, ratio_up
    lipids_down, ratio_down = side(lower)
    return f"{lipids_up}//{lipids_down}", f"{ratio_up}//{ratio_down}"


def _cache_dir() -> Path:
    """Kept with this user's settings, which `FASTMDXPLORA_CONFIG_DIR` moves."""
    from fastmdxplora.user_dir import user_config_dir

    return user_config_dir() / "membrane_patches"


def _template_bonds(topology: Any, forcefield: Any) -> int:
    """Each residue without bonds given those of the force field's template
    of its name: PACKMOL writes none, and OpenMM reads bonds from a PDB file
    only for standard residues."""
    added = 0
    for residue in topology.residues():
        template = forcefield._templates.get(residue.name)
        if template is None or any(True for _ in residue.bonds()):
            continue
        atoms = {a.name: a for a in residue.atoms()}
        for i, j in template.bonds:
            first, second = template.atoms[i].name, template.atoms[j].name
            if first in atoms and second in atoms:
                topology.addBond(atoms[first], atoms[second])
                added += 1
    return added


def build_patch(composition: dict[str, Any], forcefield: Any, *, patch_nm: float = 5.0,
                work_dir: Path | None = None) -> tuple[Any, dict[str, Any]]:
    """A bilayer patch of ``composition``, as OpenMM's ``addMembrane`` takes
    one (an object with ``topology`` and ``positions``), and what it holds."""
    import numpy as np
    from openmm import app, unit

    upper, lower = leaflets(composition)
    lipids, ratio = _arguments(upper, lower)
    unknown = sorted({name for name in list(upper) + list(lower)
                      if name not in forcefield._templates})
    if unknown:
        raise StudyError(
            f"The force field has no template for {', '.join(unknown)}, so a bilayer of it "
            "cannot be parameterised. CHARMM36 and AMBER Lipid17 name lipids as CHARMM "
            "does (POPC, POPE, POPS, DOPC, CHL1 for cholesterol, ...).",
            code="setup.membrane.lipid_unparameterized")
    key = hashlib.sha256(json.dumps([lipids, ratio, patch_nm]).encode()).hexdigest()[:16]
    cache = _cache_dir() / key
    patch_file = cache / "patch.pdb"
    if not patch_file.is_file():
        executable = shutil.which("packmol-memgen") or str(Path(sys.prefix) / "bin" / "packmol-memgen")
        if not Path(executable).exists():
            from fastmdxplora.dependencies import MissingBackendError, MissingDependency

            raise MissingBackendError([MissingDependency("PACKMOL-Memgen", "packmol_memgen",
                                                         "ambertools")])
        build = Path(work_dir or cache) / "build"
        build.mkdir(parents=True, exist_ok=True)
        environment = {**os.environ, "AMBERHOME": os.environ.get("AMBERHOME", sys.prefix)}
        command = [executable, "--lipids", lipids, "--ratio", ratio,
                   "--distxy_fix", f"{patch_nm * 10:g}", "--charmm", "--output", "patch.pdb"]
        done = subprocess.run(command, cwd=build, env=environment, capture_output=True,
                              text=True, check=False)
        made = build / "patch.pdb"
        if done.returncode != 0 or not made.is_file():
            tail = (done.stdout + done.stderr).strip().splitlines()[-6:]
            raise StudyError(
                "PACKMOL-Memgen did not build the bilayer patch "
                f"({lipids}, ratio {ratio}): " + " / ".join(tail),
                code="setup.membrane.lipid_unparameterized")
        cache.mkdir(parents=True, exist_ok=True)
        shutil.copy(made, patch_file)

    pdb = app.PDBFile(str(patch_file))
    modeller = app.Modeller(pdb.topology, pdb.positions)
    modeller.delete([r for r in modeller.topology.residues() if r.name in _IONS])
    xyz = np.array(modeller.positions.value_in_unit(unit.nanometer))
    low, high = xyz.min(axis=0), xyz.max(axis=0)
    # Centred on the bilayer's middle, as OpenMM's own patches are.
    lipid_atoms = [a.index for a in modeller.topology.atoms()
                   if a.residue.name in set(upper) | set(lower)]
    middle = xyz[lipid_atoms].mean(axis=0)
    modeller.positions = (xyz - middle) * unit.nanometer
    modeller.topology.setPeriodicBoxVectors(np.diag(high - low + 0.1) * unit.nanometer)
    bonds = _template_bonds(modeller.topology, forcefield)
    counts: dict[str, int] = {}
    for residue in modeller.topology.residues():
        if residue.name in set(upper) | set(lower):
            counts[residue.name] = counts.get(residue.name, 0) + 1
    record = {"composition": {"upper": upper, "lower": lower},
              "asymmetric": upper != lower, "lipids_in_patch": counts,
              "patch_nm": patch_nm, "built_by": "PACKMOL-Memgen, CHARMM lipid names",
              "bonds_from_templates": bonds, "cached_as": key,
              "equilibrated": False}
    return modeller, record
