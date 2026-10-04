"""Each interaction rule, against a geometry built to its own criterion.

Every rule in ``interactions`` names the published criterion it implements --
ProLIF's 4.5 A for a salt bridge, PLIP's centre/angle/offset for stacking,
the sigma-hole geometry for a halogen bond. None of them had a test that
placed atoms at those geometries and checked the rule fires, or just outside
them and checked it does not.

That gap matters more here than in most code. These rules decide what a
report tells a medicinal chemist is holding the ligand, the trajectory that
exercises them in production is exactly the one nobody has by hand, and a
threshold applied to the wrong quantity -- a degree confused with a radian, a
centre with a nearest atom -- produces confident, plausible, wrong output.

Everything is synthetic: a topology built atom by atom, coordinates placed at
known separations and angles, chemistry from RDKit on a SMILES. Each positive
geometry sits comfortably inside its window and each negative comfortably
outside, so the tests assert the criterion rather than the fifth decimal of
an implementation.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
# Skip the module where RDKit is absent, then import it properly. The probe
# used to bind `Chem` itself and was shadowed on the next line, so whichever
# of the two was wrong, nothing would have said which.
pytest.importorskip("rdkit.Chem")

from rdkit import Chem  # noqa: E402

from fastmdxplora.analysis.interactions import (  # noqa: E402
    donors_and_acceptors,
    halogen_bonds,
    hydrogen_bonds,
    hydrophobic_atoms,
    hydrophobic_contacts,
    ligand_aromatic_rings,
    ligand_charged_groups,
    metal_coordination,
    pi_cation,
    pi_stacking,
    protein_aromatic_rings,
    protein_charged_groups,
    residues_not_covered,
    salt_bridges,
    water_bridges,
)
from fastmdxplora.analysis.ligand_chemistry import ResolvedChemistry  # noqa: E402


# ---------------------------------------------------------------------------
# A complex, built atom by atom.
# ---------------------------------------------------------------------------

class _Builder:
    """A topology and coordinates assembled one residue at a time."""

    def __init__(self) -> None:
        self.topology = md.Topology()
        self._chain = self.topology.add_chain()
        self._xyz: list[list[float]] = []

    def residue(self, name: str, atoms: list[tuple[str, str, tuple]],
                *, chain: bool = False):
        """Add a residue: (atom name, element symbol, xyz in nm) each."""
        if chain:
            self._chain = self.topology.add_chain()
        residue = self.topology.add_residue(name, self._chain)
        first = len(self._xyz)
        for atom_name, element, xyz in atoms:
            self.topology.add_atom(
                atom_name, md.element.get_by_symbol(element), residue)
            self._xyz.append(list(xyz))
        return list(range(first, len(self._xyz)))

    def bond(self, i: int, j: int) -> None:
        atoms = list(self.topology.atoms)
        self.topology.add_bond(atoms[i], atoms[j])

    def trajectory(self) -> md.Trajectory:
        return md.Trajectory(
            np.array([self._xyz], dtype=float), self.topology)


def _chemistry(smiles: str, resname: str = "LIG", *,
               ambiguous: bool = False) -> ResolvedChemistry:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    return ResolvedChemistry(
        mol=mol, source="supplied", detail="built for a test",
        resname=resname, n_atoms=mol.GetNumAtoms(),
        charge_was_ambiguous=ambiguous)


def _ring(centre, normal, radius=0.14, n=6, start=0.0):
    """n points on a circle: an aromatic ring's carbons, placed exactly."""
    normal = np.asarray(normal, float)
    normal /= np.linalg.norm(normal)
    seed = np.array([1.0, 0.0, 0.0])
    if abs(normal @ seed) > 0.9:
        seed = np.array([0.0, 1.0, 0.0])
    u = np.cross(normal, seed); u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    return [tuple(np.asarray(centre) + radius * (np.cos(a) * u + np.sin(a) * v))
            for a in np.linspace(start, start + 2 * np.pi, n, endpoint=False)]


# ---------------------------------------------------------------------------
# Hydrogen bonds: Baker & Hubbard, via wernet_nilsson-style D-H...A geometry.
# ---------------------------------------------------------------------------

class TestDonorsAndAcceptors:
    def test_an_no_s_with_hydrogen_donates_and_a_lone_pair_accepts(self) -> None:
        """The backbone N-H donates and does not accept: its lone pair is in
        the amide. The hydroxyl O and a sulphur with no hydrogen accept."""
        b = _Builder()
        idx = b.residue("SER", [
            ("N", "N", (0, 0, 0)), ("H", "H", (0.10, 0, 0)),
            ("OG", "O", (0.5, 0, 0)), ("HG", "H", (0.6, 0, 0)),
            ("SD", "S", (1.0, 0, 0)),
            ("CA", "C", (1.5, 0, 0)),
        ])
        for pair in ((0, 1), (2, 3)):
            b.bond(*pair)
        donors, acceptors = donors_and_acceptors(b.topology, idx)
        assert {d for d, _h in donors} == {idx[0], idx[2]}
        assert set(acceptors) == {idx[2], idx[4]}  # S accepts, C and amide N do not


class TestHydrogenBonds:
    def _complex(self, oo_nm: float):
        """Ligand O-H pointing at a protein carbonyl O, at a set O...O gap."""
        b = _Builder()
        lig = b.residue("LIG", [
            ("O1", "O", (0.0, 0, 0)), ("H1", "H", (0.098, 0, 0)),
        ])
        b.bond(lig[0], lig[1])
        pro = b.residue("ASN", [("OD1", "O", (oo_nm, 0, 0))], chain=True)
        return b.trajectory(), lig, pro

    def test_a_straight_short_bond_is_found(self) -> None:
        traj, lig, pro = self._complex(0.28)
        found = hydrogen_bonds(traj, lig, pro)
        assert len(found) == 1
        got = found[0]
        assert (got.ligand_atom, got.protein_atom) == (lig[0], pro[0])
        assert got.distance_nm == pytest.approx(0.28, abs=1e-6)

    def test_too_far_apart_is_not_a_bond(self) -> None:
        traj, lig, pro = self._complex(0.45)
        assert hydrogen_bonds(traj, lig, pro) == []

    def test_a_bent_geometry_is_not_a_bond(self) -> None:
        """Same O...O separation; the hydrogen pointing away breaks it."""
        b = _Builder()
        lig = b.residue("LIG", [
            ("O1", "O", (0.0, 0, 0)), ("H1", "H", (-0.098, 0, 0)),
        ])
        b.bond(lig[0], lig[1])
        pro = b.residue("ASN", [("OD1", "O", (0.28, 0, 0))], chain=True)
        assert hydrogen_bonds(b.trajectory(), lig, pro) == []


# ---------------------------------------------------------------------------
# Hydrophobic contacts.
# ---------------------------------------------------------------------------

class TestHydrophobic:
    def test_carbons_touching_count_and_polar_atoms_do_not(self) -> None:
        b = _Builder()
        lig = b.residue("LIG", [("C1", "C", (0, 0, 0)),
                                ("H1", "H", (0, -0.1, 0)),
                                ("O1", "O", (0, 0.05, 0))])
        pro = b.residue("LEU", [("CD1", "C", (0.38, 0, 0)),
                                ("HD1", "H", (0.38, -0.1, 0)),
                                ("N", "N", (0.38, 0.05, 0))], chain=True)
        b.bond(lig[0], lig[1])
        b.bond(pro[0], pro[1])
        found = hydrophobic_contacts(b.trajectory(), lig, pro,
                                     periodic=False)
        assert len(found) == 1
        assert (found[0].ligand_atom, found[0].protein_atom) == (lig[0], pro[0])

    def test_a_carbon_with_no_bonds_is_not_assumed_hydrophobic(self) -> None:
        """No neighbours means no evidence either way, and the rule declines
        to guess -- an unbonded carbon in a topology is more likely a
        connectivity problem than a hydrophobe."""
        b = _Builder()
        idx = b.residue("LIG", [("C1", "C", (0, 0, 0))])
        assert hydrophobic_atoms(b.topology, idx) == []

    def test_beyond_the_threshold_is_nothing(self) -> None:
        b = _Builder()
        lig = b.residue("LIG", [("C1", "C", (0, 0, 0)),
                                ("H1", "H", (0, -0.1, 0))])
        pro = b.residue("LEU", [("CD1", "C", (0.60, 0, 0)),
                                ("HD1", "H", (0.60, -0.1, 0))], chain=True)
        b.bond(lig[0], lig[1])
        b.bond(pro[0], pro[1])
        assert hydrophobic_contacts(b.trajectory(), lig, pro,
                                    periodic=False) == []

    def test_hydrophobic_atoms_are_carbons_and_their_hydrogens_only(self) -> None:
        b = _Builder()
        idx = b.residue("LIG", [
            ("C1", "C", (0, 0, 0)), ("H1", "H", (0.1, 0, 0)),
            ("O1", "O", (0.2, 0, 0)), ("HO", "H", (0.3, 0, 0)),
        ])
        b.bond(idx[0], idx[1]); b.bond(idx[2], idx[3])
        assert hydrophobic_atoms(b.topology, idx) == [idx[0]]


# ---------------------------------------------------------------------------
# Charged groups and salt bridges: ProLIF's 4.5 A between group centres.
# ---------------------------------------------------------------------------

class TestChargedGroups:
    def test_a_carboxylate_is_one_group_across_both_oxygens(self) -> None:
        chemistry = _chemistry("CC(=O)[O-]")
        n = chemistry.mol.GetNumAtoms()
        positive, negative = ligand_charged_groups(chemistry, list(range(n)))
        assert positive == []
        assert len(negative) == 1
        assert len(negative[0]) == 2  # the charge, spread over the group

    def test_an_ammonium_is_positive(self) -> None:
        chemistry = _chemistry("C[NH3+]")
        n = chemistry.mol.GetNumAtoms()
        positive, negative = ligand_charged_groups(chemistry, list(range(n)))
        assert len(positive) == 1 and negative == []

    def test_arginine_and_aspartate_are_found_on_the_protein(self) -> None:
        b = _Builder()
        arg = b.residue("ARG", [
            ("NE", "N", (0, 0, 0)), ("NH1", "N", (0.1, 0, 0)),
            ("NH2", "N", (0.2, 0, 0)), ("CZ", "C", (0.1, 0.1, 0)),
        ])
        asp = b.residue("ASP", [
            ("OD1", "O", (1, 0, 0)), ("OD2", "O", (1.1, 0, 0)),
        ])
        positive, negative = protein_charged_groups(b.topology, arg + asp)
        assert len(positive) == 1 and len(negative) == 1
        assert set(positive[0]) <= set(arg) and set(negative[0]) <= set(asp)


class TestSaltBridges:
    def _complex(self, gap_nm: float):
        b = _Builder()
        lig = b.residue("LIG", [  # acetate: C, C, O, O + 3 H (RDKit order)
            ("C1", "C", (-0.3, 0, 0)), ("C2", "C", (-0.15, 0, 0)),
            ("O1", "O", (0.0, 0.05, 0)), ("O2", "O", (0.0, -0.05, 0)),
            ("H1", "H", (-0.4, 0.1, 0)), ("H2", "H", (-0.4, -0.1, 0)),
            ("H3", "H", (-0.4, 0, 0.1)),
        ])
        # Guanidinium centre at gap_nm from the carboxylate centre (0, 0, 0).
        pro = b.residue("ARG", [
            ("NE", "N", (gap_nm, 0.05, 0)), ("NH1", "N", (gap_nm, -0.05, 0)),
            ("NH2", "N", (gap_nm, 0, 0.05)),
        ], chain=True)
        return b.trajectory(), _chemistry("CC(=O)[O-]"), lig, pro

    def test_opposite_charges_within_the_window_bridge(self) -> None:
        traj, chemistry, lig, pro = self._complex(0.35)
        found = salt_bridges(traj, chemistry, lig, pro)
        assert len(found) == 1
        assert found[0].kind == "salt_bridge"
        assert found[0].distance_nm == pytest.approx(0.35, abs=0.02)

    def test_beyond_the_window_is_nothing(self) -> None:
        traj, chemistry, lig, pro = self._complex(0.60)
        assert salt_bridges(traj, chemistry, lig, pro) == []

    def test_an_ambiguous_charge_is_refused_not_reported(self) -> None:
        """A salt bridge is a claim about charge. Where the charge was
        guessed, the rule refuses rather than asserting more than is known."""
        traj, _, lig, pro = self._complex(0.35)
        guessed = _chemistry("CC(=O)[O-]", ambiguous=True)
        with pytest.raises(ValueError, match="charge"):
            salt_bridges(traj, guessed, lig, pro)
        assert salt_bridges(traj, guessed, lig, pro,
                            allow_ambiguous_charge=True)


# ---------------------------------------------------------------------------
# Aromatic rings, stacking, and pi-cation: PLIP's windows.
# ---------------------------------------------------------------------------

def _benzene_complex(*, centre, normal=(0, 0, 1), phe_normal=(0, 0, 1)):
    """A benzene ligand at the origin and a PHE ring where asked."""
    b = _Builder()
    lig = b.residue("LIG", [(f"C{i+1}", "C", xyz) for i, xyz in
                            enumerate(_ring((0, 0, 0), (0, 0, 1)))]
                    + [(f"H{i+1}", "H", xyz) for i, xyz in
                       enumerate(_ring((0, 0, 0), (0, 0, 1), radius=0.25))])
    phe = b.residue("PHE", [(name, "C", xyz) for name, xyz in
                            zip(("CG", "CD1", "CD2", "CE1", "CE2", "CZ"),
                                _ring(centre, phe_normal))], chain=True)
    return b.trajectory(), _chemistry("c1ccccc1"), lig, phe


class TestAromaticRings:
    def test_the_ligand_ring_is_found_from_chemistry(self) -> None:
        chemistry = _chemistry("c1ccccc1")
        rings = ligand_aromatic_rings(
            chemistry, list(range(chemistry.mol.GetNumAtoms())))
        assert len(rings) == 1 and len(rings[0]) == 6

    def test_cyclohexane_is_flat_but_not_aromatic(self) -> None:
        """Aromaticity is chemistry, not geometry: a saturated ring is not
        one, whatever shape its coordinates take."""
        chemistry = _chemistry("C1CCCCC1")
        assert ligand_aromatic_rings(
            chemistry, list(range(chemistry.mol.GetNumAtoms()))) == []

    def test_the_phe_ring_is_found_by_name(self) -> None:
        traj, _, _, phe = _benzene_complex(centre=(0, 0, 0.37), normal=(0, 0, 1))
        assert len(protein_aromatic_rings(traj.topology, phe)) == 1


class TestPiStacking:
    def test_parallel_rings_at_stacking_distance_are_face_to_face(self) -> None:
        traj, chemistry, lig, phe = _benzene_complex(
            centre=(0, 0, 0.37), normal=(0, 0, 1))
        found = pi_stacking(traj, chemistry, lig, phe)
        assert len(found) == 1
        assert found[0].kind == "pi_stacking_face_to_face"
        assert found[0].distance_nm == pytest.approx(0.37, abs=0.01)

    def test_perpendicular_rings_are_edge_to_face(self) -> None:
        traj, chemistry, lig, phe = _benzene_complex(
            centre=(0, 0, 0.45), phe_normal=(1, 0, 0))
        found = pi_stacking(traj, chemistry, lig, phe)
        assert len(found) == 1
        assert found[0].kind == "pi_stacking_edge_to_face"

    def test_a_large_lateral_offset_is_not_stacking(self) -> None:
        """Side by side at stacking height: the centres are close enough and
        the planes parallel, and it is still not a stack."""
        traj, chemistry, lig, phe = _benzene_complex(
            centre=(0.40, 0, 0.1), normal=(0, 0, 1))
        assert pi_stacking(traj, chemistry, lig, phe) == []

    def test_too_far_apart_is_nothing(self) -> None:
        traj, chemistry, lig, phe = _benzene_complex(
            centre=(0, 0, 0.80), normal=(0, 0, 1))
        assert pi_stacking(traj, chemistry, lig, phe) == []


class TestPiCation:
    def _complex(self, height_nm: float):
        b = _Builder()
        lig = b.residue("LIG", [(f"C{i+1}", "C", xyz) for i, xyz in
                                enumerate(_ring((0, 0, 0), (0, 0, 1)))]
                        + [(f"H{i+1}", "H", xyz) for i, xyz in
                           enumerate(_ring((0, 0, 0), (0, 0, 1), radius=0.25))])
        lys = b.residue("LYS", [("NZ", "N", (0, 0, height_nm))], chain=True)
        return b.trajectory(), _chemistry("c1ccccc1"), lig, lys

    def test_a_cation_over_the_ring_is_found(self) -> None:
        traj, chemistry, lig, lys = self._complex(0.40)
        found = pi_cation(traj, chemistry, lig, lys)
        assert len(found) == 1 and found[0].kind == "pi_cation"

    def test_too_high_above_the_ring_is_nothing(self) -> None:
        traj, chemistry, lig, lys = self._complex(0.90)
        assert pi_cation(traj, chemistry, lig, lys) == []


# ---------------------------------------------------------------------------
# Halogen bonds: the sigma-hole points along C-X.
# ---------------------------------------------------------------------------

class TestHalogenBonds:
    def _complex(self, *, angle_deg: float, gap_nm: float = 0.30,
                 smiles: str = "CCl"):
        """C-X aimed so the X...acceptor angle at the halogen is as asked."""
        b = _Builder()
        theta = np.radians(180.0 - angle_deg)
        lig = b.residue("LIG", [
            ("C1", "C", (-0.18, 0, 0)),
            ("X1", "Cl" if "Cl" in smiles else "F", (0, 0, 0)),
            ("H1", "H", (-0.25, 0.1, 0)), ("H2", "H", (-0.25, -0.1, 0)),
            ("H3", "H", (-0.25, 0, 0.1)),
        ])
        pro = b.residue("ASN", [
            ("OD1", "O", (gap_nm * np.cos(theta), gap_nm * np.sin(theta), 0)),
        ], chain=True)
        return b.trajectory(), _chemistry(smiles), lig, pro

    def test_a_straight_close_contact_is_a_halogen_bond(self) -> None:
        traj, chemistry, lig, pro = self._complex(angle_deg=170.0)
        found = halogen_bonds(traj, chemistry, lig, pro)
        assert len(found) == 1
        assert found[0].angle_deg == pytest.approx(170.0, abs=1.0)

    def test_a_side_on_approach_is_not_one(self) -> None:
        """Same separation; the sigma-hole points elsewhere."""
        traj, chemistry, lig, pro = self._complex(angle_deg=90.0)
        assert halogen_bonds(traj, chemistry, lig, pro) == []

    def test_fluorine_does_not_count_unless_asked(self) -> None:
        """Politzer's case: C-F has no sigma-hole worth the name. PLIP counts
        it anyway, so asking for PLIP's behaviour is a setting."""
        traj, chemistry, lig, pro = self._complex(angle_deg=170.0, smiles="CF")
        assert halogen_bonds(traj, chemistry, lig, pro) == []
        assert len(halogen_bonds(traj, chemistry, lig, pro,
                                 include_fluorine=True)) == 1


# ---------------------------------------------------------------------------
# Metal coordination and water bridges: PLIP's thresholds.
# ---------------------------------------------------------------------------

class TestMetalCoordination:
    def _complex(self, gap_nm: float):
        b = _Builder()
        lig = b.residue("LIG", [("O1", "O", (0, 0, 0))])
        zn = b.residue("ZN", [("ZN", "Zn", (gap_nm, 0, 0))], chain=True)
        return b.trajectory(), lig, zn

    def test_a_donor_at_coordination_distance_is_found(self) -> None:
        traj, lig, zn = self._complex(0.22)
        found = metal_coordination(traj, lig, zn)
        assert len(found) == 1 and found[0].kind == "metal_coordination"

    def test_beyond_three_angstroms_is_nothing(self) -> None:
        traj, lig, zn = self._complex(0.40)
        assert metal_coordination(traj, lig, zn) == []


class TestWaterBridges:
    """PLIP's first-degree water bridge: an acceptor on one side, a donor
    D-H on the other, both 2.5 to 4.1 A from the water O, the donor's H
    pointing at the water (theta > 100), and the angle at the water between
    the acceptor and that H inside 71 to 140 degrees."""

    def _complex(self, *, water_at, hydrogen_towards_water=True, donor=True):
        b = _Builder()
        lig = b.residue("LIG", [("C1", "C", (-0.12, 0, 0)), ("O1", "O", (0, 0, 0))])
        b.bond(lig[0], lig[1])
        og = np.array([0.60, 0.0, 0.0])
        towards = np.asarray(water_at, float) - og
        towards /= np.linalg.norm(towards)
        hg = og + 0.096 * (towards if hydrogen_towards_water else -towards)
        atoms = [("CB", "C", (0.72, 0.05, 0)), ("OG", "O", tuple(og))]
        if donor:
            atoms.append(("HG", "H", tuple(hg)))
        pro = b.residue("SER", atoms, chain=True)
        b.bond(pro[0], pro[1])
        if donor:
            b.bond(pro[1], pro[2])
        w = np.asarray(water_at, float)
        hoh = b.residue("HOH", [("O", "O", tuple(w)),
                                ("H1", "H", tuple(w + (0, 0, 0.096))),
                                ("H2", "H", tuple(w + (0, 0.09, -0.03)))], chain=True)
        b.bond(hoh[0], hoh[1]); b.bond(hoh[0], hoh[2])
        return b.trajectory(), lig, pro, hoh

    def test_a_water_between_both_sides_bridges(self) -> None:
        """Off the line, so omega at the water sits inside 71-140."""
        traj, lig, pro, water = self._complex(water_at=(0.30, 0.22, 0.0))
        found = water_bridges(traj, lig, pro, water)
        assert len(found) == 1 and found[0].kind == "water_bridge"
        assert (found[0].ligand_atom, found[0].protein_atom) == (lig[1], pro[1])
        assert 71.0 < found[0].angle_deg < 140.0

    def test_a_water_in_line_is_between_not_bridging(self) -> None:
        """The lower angle bound is the point of the criterion: a water on
        the straight line reads as near 180 degrees and is excluded."""
        traj, lig, pro, water = self._complex(water_at=(0.30, 0.0, 0.0))
        assert water_bridges(traj, lig, pro, water) == []

    def test_a_water_near_one_side_only_is_nothing(self) -> None:
        traj, lig, pro, water = self._complex(water_at=(0.02, 0.25, 0.0))
        assert water_bridges(traj, lig, pro, water) == []

    def test_a_donor_whose_hydrogen_points_away_does_not_bridge(self) -> None:
        traj, lig, pro, water = self._complex(water_at=(0.30, 0.22, 0.0),
                                              hydrogen_towards_water=False)
        assert water_bridges(traj, lig, pro, water) == []

    def test_two_acceptors_are_not_a_bridge(self) -> None:
        """A carbonyl on each side and no donor: PLIP pairs an acceptor with
        a donor, and two lone pairs facing one water are not that."""
        traj, lig, pro, water = self._complex(water_at=(0.30, 0.22, 0.0),
                                              donor=False)
        assert water_bridges(traj, lig, pro, water) == []

    def test_only_the_waters_near_the_ligand_are_measured(self, monkeypatch) -> None:
        """A box of water far from the ligand changes nothing and costs
        nothing: the distances taken are those of the waters within reach,
        not of every water against every polar atom (3.77 million pairs a
        frame on a solvated 1BHL)."""
        import fastmdxplora.analysis.interactions as rules

        traj, lig, pro, water = self._complex(water_at=(0.30, 0.22, 0.0))
        far = np.array([[2.0 + 0.3 * i, 2.0 + 0.3 * j, 2.0]
                        for i in range(20) for j in range(20)])
        top = traj.topology.copy()
        chain = top.add_chain()
        for _ in far:
            residue = top.add_residue("HOH", chain)
            top.add_atom("O", md.element.oxygen, residue)
        xyz = np.concatenate([traj.xyz[0], far])[None]
        big = md.Trajectory(xyz, top)
        waters = list(water) + list(range(traj.n_atoms, big.n_atoms))

        largest = []
        real = rules._distances

        def counted(t, pairs, periodic):
            largest.append(len(pairs))
            return real(t, pairs, periodic)

        monkeypatch.setattr(rules, "_distances", counted)
        found = water_bridges(big, lig, pro, waters)
        assert len(found) == 1
        assert max(largest) < len(far)


class TestResiduesNotCovered:
    def test_a_modified_residue_is_named_rather_than_guessed_at(self) -> None:
        b = _Builder()
        idx = b.residue("MSE", [("SE", "Se", (0, 0, 0))])
        idx += b.residue("ALA", [("CA", "C", (1, 0, 0))])
        left_out = residues_not_covered(b.topology, idx)
        assert "MSE" in left_out and "ALA" not in left_out


class TestTheDirectionOfAHydrogenBond:
    def test_each_direction_is_its_own_interaction(self) -> None:
        """Frame 0: the ligand O1-H1 donates to a serine OG. Frame 1: the
        serine OG-HG donates to O1 and the ligand's hydrogen has turned
        away. One kind for both made these one row present in both frames,
        a bond held throughout where its direction had reversed."""
        from fastmdxplora.analysis.interaction_summary import occupancies

        b = _Builder()
        lig = b.residue("LIG", [("C1", "C", (-0.14, 0, 0)), ("O1", "O", (0, 0, 0)),
                                ("H1", "H", (0.097, 0, 0))])
        b.bond(lig[0], lig[1]); b.bond(lig[1], lig[2])
        ser = b.residue("SER", [("CB", "C", (0.42, 0, 0)), ("OG", "O", (0.28, 0, 0)),
                                ("HG", "H", (0.38, 0.05, 0))], chain=True)
        b.bond(ser[0], ser[1]); b.bond(ser[1], ser[2])
        first = np.array(b._xyz, dtype=float)
        second = first.copy()
        second[lig[2]] = (-0.03, 0.093, 0)
        second[ser[2]] = (0.183, 0, 0)
        traj = md.Trajectory(np.stack([first, second]), b.topology)

        found = hydrogen_bonds(traj, lig, ser, periodic=False)
        assert {(c.frame, c.kind) for c in found} == {
            (0, "hydrogen_bond_ligand_donor"), (1, "hydrogen_bond_protein_donor")}
        table = occupancies(found, 2)
        assert sorted((o.kind, o.fraction) for o in table) == [
            ("hydrogen_bond_ligand_donor", 0.5), ("hydrogen_bond_protein_donor", 0.5)]
