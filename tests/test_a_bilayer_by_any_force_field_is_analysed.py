"""A bilayer is analysed whichever force field named its lipids and water.

The bilayer analyses were written against OpenMM's own patches and the
CHARMM36 names, and read other systems wrongly without saying so: a Lipid21
bilayer, whose phosphorus is P31, had no heads and an area per lipid of
infinity; a lipid whose name was not on a short list was counted as protein;
water named OPC was counted as protein where it reached the core. Each case
is built here from an OpenMM patch, renamed or rearranged as the other force
field writes it, so the right answer is the patch's own.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import pytest

from fastmdxplora.refusals import StudyError

md = pytest.importorskip("mdtraj")
app = pytest.importorskip("openmm.app")

from fastmdxplora.analysis.area_per_lipid import AreaPerLipid  # noqa: E402
from fastmdxplora.analysis.bilayer_thickness import BilayerThickness  # noqa: E402


def _patch(lipid: str):
    return md.load(str(Path(app.__file__).parent / "data" / f"{lipid}.pdb"))


def _run(cls, traj):
    analysis = cls(output_dir=tempfile.mkdtemp())
    return analysis.compute(traj), analysis.findings


def _renamed(traj, rename):
    top = traj.topology.copy()
    rename(top)
    return md.Trajectory(traj.xyz, top, unitcell_lengths=traj.unitcell_lengths,
                         unitcell_angles=traj.unitcell_angles)


def _lipid21(traj, names=("PA", "PC", "OL")):
    """Each POPC as AMBER Lipid21 writes it: sn-1 tail, head, sn-2 tail as
    three residues, the carbonyls in the head, the phosphorus named P31."""
    top = md.Topology()
    chain = top.add_chain()
    mapping: dict[int, Any] = {}
    order: list[int] = []
    for residue in traj.topology.residues:
        atoms = list(residue.atoms)
        if residue.name != "POP":
            new = top.add_residue(residue.name, chain, resSeq=residue.resSeq)
            for atom in atoms:
                mapping[atom.index] = top.add_atom(atom.name, atom.element, new)
                order.append(atom.index)
            continue
        sn1 = [a for a in atoms if (re.fullmatch(r"C3\d+", a.name) and int(a.name[2:]) >= 2)
               or re.fullmatch(r"H\d+[XYZ]", a.name)]
        sn2 = [a for a in atoms if (re.fullmatch(r"C2\d+", a.name) and int(a.name[2:]) >= 2)
               or re.fullmatch(r"H\d+[RST]|H91|H101", a.name)]
        head = [a for a in atoms if a not in sn1 and a not in sn2]
        for name, part in zip(names, (sn1, head, sn2)):
            new = top.add_residue(name, chain, resSeq=residue.resSeq)
            for atom in part:
                mapping[atom.index] = top.add_atom(
                    "P31" if atom.name == "P" else atom.name, atom.element, new)
                order.append(atom.index)
    for bond in traj.topology.bonds:
        top.add_bond(mapping[bond[0].index], mapping[bond[1].index])
    return md.Trajectory(traj.xyz[:, order], top, unitcell_lengths=traj.unitcell_lengths,
                         unitcell_angles=traj.unitcell_angles)


class TestTheHeadIsThePhosphorus:

    def test_a_lipid21_bilayer_has_its_heads(self) -> None:
        """Lipid21 names the phosphorus P31; it is found by its element."""
        traj = _patch("POPC")
        split = _lipid21(traj)
        area, findings = _run(AreaPerLipid, split)
        assert findings["bilayer"]["lipids"] == 128
        assert area[0] == pytest.approx(_run(AreaPerLipid, traj)[0][0], rel=1e-6)
        assert _run(BilayerThickness, split)[0][0] == pytest.approx(
            _run(BilayerThickness, traj)[0][0], abs=1e-4)

    def test_lipids_without_a_head_atom_are_refused_not_infinite(self) -> None:
        """Beads with no element, as a coarse-grained model has: no head is
        found, and the area is refused rather than divided by zero."""
        traj = _renamed(_patch("POPC"), lambda top: [
            setattr(a, "element", md.element.virtual_site)
            for a in top.atoms if a.name == "P"])
        with pytest.raises(StudyError) as refused:
            _run(AreaPerLipid, traj)
        assert refused.value.code == "analysis.system.inapplicable"
        assert "head" in str(refused.value)


class TestALipidByAnyNameIsALipid:

    @pytest.mark.parametrize("name", ["DSPE", "PLPC", "SOPE", "DLPG", "DAPC", "POPI15",
                                      "TOCL1", "PSM"])
    def test_it_is_counted_as_a_lipid_not_a_protein(self, name) -> None:
        """A third of the lipids renamed: CHARMM36 has templates for each,
        and the short list kept by hand had none of these."""
        traj = _patch("POPC")
        mixed = _renamed(traj, lambda top: [
            setattr(r, "name", name)
            for r in [r for r in top.residues if r.name == "POP"][::3]])
        area, findings = _run(AreaPerLipid, mixed)
        assert findings["bilayer"]["lipids"] == 128
        assert findings["area"]["protein_cross_section_nm2_mean"] == 0.0
        assert "protein_share" not in findings and "unread_lipids" not in findings
        assert area[0] == pytest.approx(_run(AreaPerLipid, traj)[0][0], rel=1e-6)

    def test_lipid21_s_stearoyl_and_docosahexaenoyl_tails_are_tails(self) -> None:
        """SDPC as Lipid21 writes it: SA, PC, DHA."""
        traj = _patch("POPC")
        split = _lipid21(traj, names=("SA", "PC", "DHA"))
        area, findings = _run(AreaPerLipid, split)
        assert findings["bilayer"]["lipids"] == 128
        assert findings["area"]["protein_cross_section_nm2_mean"] == 0.0
        assert area[0] == pytest.approx(_run(AreaPerLipid, traj)[0][0], rel=1e-6)

    def test_a_phosphorus_among_the_heads_under_another_name_is_said(self) -> None:
        """A lipid under a name no force field uses is counted as protein,
        and the findings say a residue with a phosphorus is among the heads."""
        traj = _patch("POPC")
        mixed = _renamed(traj, lambda top: [
            setattr(r, "name", "XLIP")
            for r in [r for r in top.residues if r.name == "POP"][::8]])
        _, findings = _run(AreaPerLipid, mixed)
        assert findings["bilayer"]["lipids"] == 112
        assert findings["unread_lipids"].startswith("16 XLIP residue(s)")
        assert "unread_lipids" not in _run(AreaPerLipid, traj)[1]


class TestTheLipidNamesAreOpenMMs:

    def test_the_names_kept_here_hold_every_lipid_template_openmm_ships(self) -> None:
        """Read from OpenMM's force field files where it is installed; the
        copy kept for an install without it must not fall behind."""
        from fastmdxplora import lipids

        read = lipids.openmm_lipid_templates()
        assert read is not None
        templates, without_phosphate, sterols = read
        assert templates - lipids._SHIPPED_LIPIDS == set()
        assert without_phosphate - lipids._SHIPPED_WITHOUT_PHOSPHATE == set()
        assert sterols - lipids._SHIPPED_STEROLS == set()
        assert {"DSPE", "PLPC", "SOPE", "DAPC", "POPI15", "TOCL1", "CER160", "ERG",
                "SITO", "PSM", "DOPC", "CHL1"} <= templates
        assert {"ERG", "SITO", "CHL1"} <= sterols and not {"DSPE", "PSM"} & sterols
        assert {"CER160", "ERG"} <= without_phosphate and "PSM" not in without_phosphate
        # Detergents and free fatty acids have one chain, and are not
        # bilayer lipids; a protein residue is not one either.
        assert not {"SDS", "DPC", "PAL", "OLE", "ALA", "LYS", "HEME"} & templates

    def test_without_openmm_the_same_names_are_read(self) -> None:
        import subprocess
        import sys

        from fastmdxplora.lipids import LIPID_RESIDUE_NAMES, STEROLS, WITHOUT_PHOSPHATE

        code = (
            "import sys, importlib.abc\n"
            "class Block(importlib.abc.MetaPathFinder):\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] == 'openmm': raise ImportError(name)\n"
            "sys.meta_path.insert(0, Block())\n"
            "import fastmdxplora.lipids as lipids\n"
            "assert lipids._READ_FROM_OPENMM is None\n"
            "print(sorted(lipids.LIPID_RESIDUE_NAMES)); print(sorted(lipids.STEROLS))\n"
            "print(sorted(lipids.WITHOUT_PHOSPHATE))\n")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, timeout=120)
        assert done.returncode == 0, done.stderr[-2000:]
        names, sterols, without_phosphate = done.stdout.strip().splitlines()
        assert names == str(sorted(LIPID_RESIDUE_NAMES))
        assert sterols == str(sorted(STEROLS))
        assert without_phosphate == str(sorted(WITHOUT_PHOSPHATE))

    def test_the_lipid21_split_names_are_lipids(self) -> None:
        from fastmdxplora.lipids import TAIL_RESIDUES, is_lipid, lipid_count

        for name in ("SA", "DHA", "AR", "LAL", "PGS", "PH-", "SPM", "CHL"):
            assert is_lipid(name)
        assert {"SA", "DHA", "AR", "LAL", "OL", "PA", "MY", "ST"} <= TAIL_RESIDUES
        assert lipid_count(["SA", "PC", "DHA", "AR", "PE", "OL"]) == 2

    def test_every_name_can_be_selected(self) -> None:
        """Lipid21's PH- and CHARMM36's 23SM are not words MDTraj reads bare."""
        from fastmdxplora.lipids import lipid_selection

        top = md.Topology()
        chain = top.add_chain()
        for name in ("PH-", "23SM", "POP", "WAT"):
            top.add_atom("C1", md.element.carbon, top.add_residue(name, chain))
        assert list(top.select(lipid_selection())) == [0, 1, 2]
