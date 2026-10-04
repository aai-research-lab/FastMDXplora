"""`protein` is the whole protein, and an alpha carbon is never a calcium.

MDTraj counted a residue as protein by its name, against a set without
AMBER's CYX, ASH, HID, HIE and HSP, so on a system with disulfides every
bridged cysteine fell out of `protein` and out of whatever was measured on
it. And `name CA`, the alpha-carbon default of RMSD, RMSF, cluster and
dimred, took in a calcium ion too: PDB and OpenMM both name its residue
and its atom CA, and setup keeps it as a structural metal.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import mdtraj as md
import numpy as np
import pytest

import fastmdxplora.analysis  # noqa: F401  (makes `protein` whole)

TRYPSIN = Path(__file__).parent / "data" / "assemblies" / "3PTB.pdb.gz"


def _trypsin(tmp_path: Path) -> md.Trajectory:
    pdb = tmp_path / "3ptb.pdb"
    pdb.write_bytes(gzip.decompress(TRYPSIN.read_bytes()))
    return md.load_pdb(str(pdb))


def _with_amber_names(structure: md.Trajectory) -> md.Trajectory:
    """Trypsin as tleap writes it: its twelve cysteines bridged, CYX."""
    renamed = structure[:]
    for residue in renamed.topology.residues:
        if residue.name == "CYS":
            residue.name = "CYX"
        elif residue.name == "HIS":
            residue.name = "HIE"
    return renamed


def test_a_disulfide_cysteine_is_protein(tmp_path: Path) -> None:
    trypsin = _trypsin(tmp_path)
    amber = _with_amber_names(trypsin)
    assert sum(r.name == "CYX" for r in amber.topology.residues) == 12
    assert len(amber.topology.select("protein")) == len(trypsin.topology.select("protein"))


def test_the_radius_of_gyration_is_of_the_whole_protein(tmp_path: Path) -> None:
    from fastmdxplora.analysis.rg import Rg

    trypsin = _trypsin(tmp_path)
    amber = _with_amber_names(trypsin)
    as_written = Rg(selection="protein").compute(trypsin)
    as_tleap_writes = Rg(selection="protein").compute(amber)
    assert np.allclose(as_written, as_tleap_writes, atol=1e-6)


def _calcium_moved(trypsin: md.Trajectory) -> md.Trajectory:
    """Two frames of a rigid trypsin; the calcium 1.5 nm away in the second."""
    frames = md.join([trypsin, trypsin])
    calcium = frames.topology.select("resname CA")
    assert len(calcium) == 1
    frames.xyz[1, calcium] += np.float32(1.5)
    return frames


def test_an_alpha_carbon_is_never_the_calcium(tmp_path: Path) -> None:
    trypsin = _trypsin(tmp_path)
    from fastmdxplora.analysis.protein_names import ALPHA_CARBONS

    assert len(trypsin.topology.select("name CA")) == 224
    assert len(trypsin.topology.select(ALPHA_CARBONS)) == 223


@pytest.mark.parametrize("analysis", ["rmsd", "rmsf", "cluster", "dimred"])
def test_the_alpha_carbon_default_leaves_the_ion_out(analysis: str) -> None:
    from fastmdxplora.analysis.orchestrator import get_analysis_class
    from fastmdxplora.analysis.protein_names import ALPHA_CARBONS

    assert get_analysis_class(analysis).default_selection == ALPHA_CARBONS


def test_a_rigid_protein_beside_a_moving_calcium_has_no_rmsd(tmp_path: Path) -> None:
    from fastmdxplora.analysis.rmsd import RMSD

    rmsd = RMSD().compute(_calcium_moved(_trypsin(tmp_path)))
    assert float(np.max(rmsd)) < 2e-3


def test_the_calcium_has_no_rmsf_row(tmp_path: Path) -> None:
    from fastmdxplora.analysis.rmsf import RMSF

    analysis = RMSF()
    analysis.compute(_calcium_moved(_trypsin(tmp_path)))
    atoms = analysis.select_atoms(_trypsin(tmp_path))
    names = {_trypsin(tmp_path).topology.atom(int(i)).residue.name for i in atoms}
    assert "CA" not in names
