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

import numpy as np
import pytest

from fastmdxplora.refusals import StudyError

md = pytest.importorskip("mdtraj")
app = pytest.importorskip("openmm.app")

from fastmdxplora.analysis.area_per_lipid import AreaPerLipid  # noqa: E402
from fastmdxplora.analysis.bilayer_thickness import BilayerThickness  # noqa: E402
from fastmdxplora.analysis.lipid_order import LipidOrder  # noqa: E402


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


def _lipid21(traj, names=("PA", "PC", "OL"), bonds=True):
    """Each POPC as AMBER Lipid21 writes it: sn-1 tail, head, sn-2 tail as
    three residues, the carbonyls in the head, the phosphorus named P31.
    Without ``bonds``, as a PDB written without CONECT records loads."""
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
    for bond in traj.topology.bonds if bonds else ():
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


class TestWaterByAnyNameIsWater:
    """OpenMM's POPC patch has water molecules among the head groups, within
    the planes the protein's cross section is taken in, so water read as
    protein takes area from the lipids."""

    @pytest.mark.parametrize("name", ["OPC", "TIP3P", "TP3", "SPCE", "TIP4P", "TP4E",
                                      "XWAT"])
    def test_it_is_not_counted_as_protein(self, name) -> None:
        traj = _patch("POPC")
        renamed = _renamed(traj, lambda top: [
            setattr(r, "name", name) for r in top.residues if r.name == "HOH"])
        area, findings = _run(AreaPerLipid, renamed)
        assert findings["area"]["protein_cross_section_nm2_mean"] == 0.0
        assert area[0] == pytest.approx(_run(AreaPerLipid, traj)[0][0], rel=1e-9)

    def test_four_site_water_with_its_virtual_site_is_water(self) -> None:
        """OPC as OpenMM writes it: O, H1, H2 and a massless site, under a
        name of the user's own."""
        from fastmdxplora.analysis.bilayer import find_bilayer

        traj = _patch("POPC")
        top = md.Topology()
        chain = top.add_chain()
        xyz = []
        for residue in traj.topology.residues:
            water = residue.name == "HOH"
            new = top.add_residue("W4" if water else residue.name, chain)
            for atom in residue.atoms:
                top.add_atom(atom.name, atom.element, new)
                xyz.append(traj.xyz[0, atom.index])
            if water:
                top.add_atom("MW", md.element.virtual_site, new)
                xyz.append(traj.xyz[0, atom.index])
        four = md.Trajectory(np.asarray(xyz)[None], top, unitcell_lengths=traj.unitcell_lengths,
                             unitcell_angles=traj.unitcell_angles)
        assert len(find_bilayer(four.topology).occupants) == 0
        area, findings = _run(AreaPerLipid, four)
        assert findings["area"]["protein_cross_section_nm2_mean"] == 0.0

    def test_a_molecule_of_three_atoms_that_is_not_water_is_an_occupant(self) -> None:
        from fastmdxplora.analysis.bilayer import _is_water

        top = md.Topology()
        chain = top.add_chain()
        for name, elements in (("MOH", [md.element.oxygen, md.element.carbon,
                                        md.element.hydrogen]),
                               ("AMN", [md.element.nitrogen, md.element.hydrogen,
                                        md.element.hydrogen])):
            residue = top.add_residue(name, chain)
            for index, element in enumerate(elements):
                top.add_atom(f"X{index}", element, residue)
            assert not _is_water(residue, list(residue.atoms))


def _order(traj):
    table = _run(LipidOrder, traj)[0].sort_values(["chain", "carbon"])
    return table["chain"].to_list(), table["s_cd"].to_numpy()


class TestTheChainOrder:

    def test_a_bilayer_whose_normal_is_not_z_is_refused(self) -> None:
        """The order is the C-H bonds' angle to z. With x and z swapped the
        sn-1 C2 read +0.128 against -0.233, and nothing was refused."""
        traj = _patch("DMPC")
        turned = traj[:]
        turned.xyz = turned.xyz[..., [2, 1, 0]]
        turned.unitcell_lengths = traj.unitcell_lengths[:, [2, 1, 0]]
        with pytest.raises(StudyError) as refused:
            _run(LipidOrder, turned)
        assert refused.value.code == "analysis.system.inapplicable"
        assert "two layers normal to z" in str(refused.value)

    def test_atoms_wrapped_one_by_one_into_the_box_give_the_same_order(self) -> None:
        """A trajectory written with every atom wrapped splits C-H bonds
        across the x and y faces as well as z."""
        traj = _patch("DMPC")
        wrapped = traj[:]
        wrapped.xyz[0] = np.mod(wrapped.xyz[0], wrapped.unitcell_lengths[0])
        chains, whole = _order(traj)
        assert _order(wrapped)[0] == chains
        assert np.allclose(_order(wrapped)[1], whole, atol=1e-5)

    def test_in_a_triclinic_box_too(self) -> None:
        """The patch sheared into a cell whose second vector leans 0.3 of
        the first, then each atom wrapped in the cell's own coordinates."""
        traj = _patch("DMPC")
        length = traj.unitcell_lengths[0]
        sheared = traj[:]
        sheared.xyz[0, :, 0] += 0.3 * length[0] * sheared.xyz[0, :, 1] / length[1]
        second = np.hypot(0.3 * length[0], length[1])
        sheared.unitcell_lengths = np.array([[length[0], second, length[2]]])
        sheared.unitcell_angles = np.array([[90.0, 90.0,
                                             np.degrees(np.arccos(0.3 * length[0] / second))]])
        cell = sheared.unitcell_vectors[0].astype(np.float64)
        fractional = sheared.xyz[0].astype(np.float64) @ np.linalg.inv(cell)
        wrapped = sheared[:]
        wrapped.xyz[0] = (np.mod(fractional, 1.0) @ cell).astype(np.float32)
        assert np.abs(wrapped.xyz[0] - sheared.xyz[0]).max() > 1.0
        assert np.allclose(_order(wrapped)[1], _order(sheared)[1], atol=1e-5)

    def test_a_split_lipid_without_bonds_in_its_topology_is_one_lipid(self) -> None:
        """Lipid21's POPC from a PDB with no CONECT records: the head and its
        two tails are joined by distance, and the order is the whole lipid's."""
        traj = _patch("POPC")
        bare = _lipid21(traj, bonds=False)
        assert bare.topology.n_bonds == 0
        table, _ = _run(LipidOrder, bare)
        assert set(table["lipid"]) == {"PA-PC-OL"}
        assert set(table["chain"]) == {"sn-1 (16:0)", "sn-2 (18:1)"}
        assert (table["lipids"] == 128).all()
        chains, whole = _order(traj)
        assert _order(bare)[0] == chains
        assert np.allclose(_order(bare)[1], whole, atol=1e-5)


class TestALeafletChangeIsReadForWhatChanged:
    """A sterol crossing the bilayer is ordinary; a phospholipid crossing it
    in a simulation is not."""

    @staticmethod
    def _two_frames(move: str):
        """DMPC with a sterol in each leaflet; in the second frame the upper
        sterol, or one phospholipid, is moved into the lower leaflet."""
        traj = _patch("DMPC")
        top = traj.topology.copy()
        middle = float(np.median(traj.xyz[0, top.select("name P"), 2]))
        centre = traj.unitcell_lengths[0, :2] / 2
        points = []
        chain = top.add_chain()
        for side in (1.0, -1.0):
            residue = top.add_residue("CHL1", chain)
            for index, depth in enumerate((1.3, 1.0, 0.7, 0.4)):
                name, element = ("O3", md.element.oxygen) if index == 0 else (
                    f"C{index}", md.element.carbon)
                top.add_atom(name, element, residue)
                points.append([centre[0], centre[1], middle + side * depth])
        first = np.concatenate([traj.xyz[0], np.asarray(points, dtype=np.float32)])
        second = first.copy()
        if move == "sterol":
            atoms = top.select("resname CHL1")[:4]
        else:
            upper = [r for r in top.residues if r.name == "DMP"
                     and traj.xyz[0, next(a.index for a in r.atoms if a.name == "P"), 2] > middle]
            atoms = np.array([a.index for a in upper[0].atoms])
        second[atoms, 2] = 2 * middle - second[atoms, 2]
        return md.Trajectory(np.stack([first, second]), top,
                             unitcell_lengths=np.repeat(traj.unitcell_lengths, 2, axis=0),
                             unitcell_angles=np.repeat(traj.unitcell_angles, 2, axis=0))

    def test_a_sterol_crossing_is_noted_as_ordinary(self) -> None:
        _, findings = _run(AreaPerLipid, self._two_frames("sterol"))
        assert "leaflet_changes" not in findings
        assert "1 to 2" not in findings["sterol_leaflet_changes"]
        assert "0 to 1" in findings["sterol_leaflet_changes"]

    def test_a_phospholipid_crossing_is_the_flip_flop_warning(self) -> None:
        _, findings = _run(BilayerThickness, self._two_frames("phospholipid"))
        assert "63 to 64" in findings["leaflet_changes"]
        assert "sterol_leaflet_changes" not in findings


class TestEachLeafletHasItsOwnArea:

    @staticmethod
    def _asymmetric():
        """OpenMM's DMPC patch with eight lipids taken from the upper leaflet."""
        traj = _patch("DMPC")
        top = traj.topology
        middle = float(np.median(traj.xyz[0, top.select("name P"), 2]))
        upper = [r for r in top.residues if r.name == "DMP" and traj.xyz[
            0, next(a.index for a in r.atoms if a.name == "P"), 2] > middle]
        gone = {a.index for r in upper[:8] for a in r.atoms}
        kept = [a.index for a in top.atoms if a.index not in gone]
        return traj, traj.atom_slice(kept)

    def test_an_asymmetric_bilayer(self, tmp_path) -> None:
        traj, asymmetric = self._asymmetric()
        box = traj.unitcell_lengths[0, 0] * traj.unitcell_lengths[0, 1]
        both = md.join([asymmetric, asymmetric])
        analysis = AreaPerLipid(output_dir=tmp_path)
        result = analysis.run(both)
        assert result.status == "ok", result.message
        assert result.data[0] == pytest.approx(box / 60, rel=1e-6)
        found = analysis.findings
        assert found["per_leaflet"]["upper_nm2_mean"] == pytest.approx(box / 56, rel=1e-6)
        assert found["per_leaflet"]["lower_nm2_mean"] == pytest.approx(box / 64, rel=1e-6)
        assert "56 upper and 64 lower" in found["asymmetric"]
        table = np.loadtxt(result.data_path)
        assert table.shape == (2, 3)
        assert np.allclose(table[0], [box / 56, box / 64, box / 60], rtol=1e-6)
        assert analysis._data_format["columns"] == [
            "upper_leaflet_nm2", "lower_leaflet_nm2", "area_per_lipid_nm2"]
        # The bilayer's value is the last column, the one readers of the file take.
        from fastmdxplora.batch.compare import _load_series

        assert np.allclose(_load_series(result.data_path), result.data)

    def test_a_symmetric_bilayer_has_one_area_in_both(self) -> None:
        traj = _patch("DMPC")
        area, found = _run(AreaPerLipid, traj)
        assert found["per_leaflet"]["upper_nm2_mean"] == pytest.approx(area[0], rel=1e-9)
        assert found["per_leaflet"]["lower_nm2_mean"] == pytest.approx(area[0], rel=1e-9)
        assert "asymmetric" not in found


class TestTheMassDensityProfile:

    def test_the_water_profile_holds_the_water(self) -> None:
        """The profile integrated over the box's height, times its area, is
        the water's mass, on a box moved through its own face so the bilayer
        crosses it, over two frames of different heights."""
        from fastmdxplora.analysis.bilayer import density_profile, find_bilayer, leaflets

        traj = _patch("POPC")
        moved = traj[:]
        moved.xyz[..., 2] = np.mod(moved.xyz[..., 2] + 2.9, moved.unitcell_lengths[0, 2])
        taller = moved[:]
        taller.unitcell_lengths = moved.unitcell_lengths * np.array([1.0, 1.0, 1.04])
        both = md.join([moved, taller])
        table = density_profile(both, leaflets(both, find_bilayer(both.topology)))
        assert np.allclose(np.diff(table["z_nm"]), 0.1)
        assert table["z_nm"].abs().min() < 1e-9  # a slab is centred on the centre
        water = [a.index for a in both.topology.atoms if a.residue.name == "HOH"]
        mass = sum(both.topology.atom(i).element.mass for i in water)
        area = float(np.mean(both.unitcell_lengths[:, 0] * both.unitcell_lengths[:, 1]))
        integral = float((table["water_g_cm3"].fillna(0) * table["width_nm"]).sum()) * area
        assert integral / 1.66053906660e-3 == pytest.approx(mass, rel=1e-6)
        lipid = sum(a.element.mass for a in both.topology.atoms if a.residue.name == "POP")
        lipids = float(((table["lipid_heads_g_cm3"] + table["lipid_tails_g_cm3"])
                        * table["width_nm"]).sum()) * area
        assert lipids / 1.66053906660e-3 == pytest.approx(lipid, rel=1e-6)

    def test_it_reads_as_a_bilayer(self, tmp_path) -> None:
        """Water near 1 g/cm3 in bulk and absent at the centre, the chains
        densest at the centre, the heads peaking about D_PP / 2 out."""
        import pandas as pd

        traj = _patch("POPC")
        result = BilayerThickness(output_dir=tmp_path).run(md.join([traj, traj]))
        assert result.status == "ok", result.message
        folder = result.output_dir
        table = pd.read_csv(folder / "density_profile.dat")
        assert (folder / "density_profile.png").is_file()
        assert folder / "density_profile.dat" in result.artifacts
        far = table[table["z_nm"].abs() > 3.0]
        assert 0.9 < far["water_g_cm3"].mean() < 1.1
        assert table.loc[table["z_nm"].abs() < 0.3, "water_g_cm3"].max() < 0.01
        tails = table.set_index("z_nm")["lipid_tails_g_cm3"]
        assert abs(tails.idxmax()) < 1.5
        heads = table.set_index("z_nm")["lipid_heads_g_cm3"]
        peak = abs(heads[heads.index > 0].idxmax())
        assert peak == pytest.approx(result.data[0] / 2, abs=0.35)
        assert table["protein_g_cm3"].max() == 0.0


@pytest.mark.parametrize("height", [6.0, 6.14, 6.03])
def test_uniform_water_reads_the_same_density_to_both_faces(height: float) -> None:
    """The slabs reach past both faces of the box. With one slab fewer above
    the centre than below, the water beyond the last edge was clipped into
    the last slab, which read 1.47 times bulk at the top of a 6.00 nm box."""
    import types

    from fastmdxplora.analysis.bilayer import density_profile

    rng = np.random.default_rng(0)
    top = md.Topology()
    chain = top.add_chain()
    n, frames, side = 3000, 20, 4.0
    for _ in range(n):
        residue = top.add_residue("HOH", chain)
        top.add_atom("O", md.element.oxygen, residue)
    xyz = rng.uniform([0, 0, -height / 2], [side, side, height / 2], size=(frames, n, 3))
    traj = md.Trajectory(xyz.astype(np.float32), top,
                         unitcell_lengths=np.tile([side, side, height], (frames, 1)),
                         unitcell_angles=np.full((frames, 3), 90.0))
    profile = density_profile(traj, types.SimpleNamespace(centre=np.zeros(frames)))
    water = profile["water_g_cm3"].to_numpy()
    full = profile["width_nm"].to_numpy() > 0.04
    bulk = np.median(water)
    assert np.all(np.abs(water[full] / bulk - 1.0) < 0.2)
    assert profile["z_nm"].iloc[0] == pytest.approx(-profile["z_nm"].iloc[-1])
