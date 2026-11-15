"""What has to be true of a structure FastMDXplora has prepared.

Run these deliberately, because they fetch from RCSB and take minutes:

    pytest -m network tests/validation -v

An ordinary `pytest` skips them, which is why the marker exists.
"""

from __future__ import annotations

import numpy as np
import pytest

from .conftest import load
from .structures import (
    CORPUS,
    EXTENT_TOLERANCE,
    SOLVATED_RATIO_RANGE,
    Expectation,
    kinds,
)

pytestmark = pytest.mark.network


def _expectation(kind: str) -> Expectation:
    return next(e for e in CORPUS if e.kind == kind)


def _residue_names(trajectory) -> set[str]:
    return {residue.name.upper() for residue in trajectory.topology.residues}


class TestEveryStructurePrepares:
    """Whether setup ran at all, before anything is asked about the result."""

    @pytest.mark.parametrize("kind", kinds())
    def test_it_either_builds_or_says_why(self, kind, prepared) -> None:
        result = prepared(kind)
        expectation = result.expectation

        if expectation.refused:
            # Recorded rather than skipped: when this starts building, the
            # corpus has to be updated deliberately and somebody reads why.
            assert not result.succeeded, (
                f"{expectation.label} now builds, where it was refused:\n"
                f"{expectation.refused}\n"
                "Remove the refusal from the corpus and add the assertions "
                "the case deserves."
            )
            return

        assert result.succeeded, (
            f"{expectation.label} ({expectation.description}) failed:\n"
            + result.output[-2000:]
        )

    @pytest.mark.parametrize("kind", kinds())
    def test_a_refusal_says_what_to_do(self, kind, prepared) -> None:
        """A structure this cannot handle should leave the reader with a next
        step, not a stack trace."""
        result = prepared(kind)
        if result.succeeded:
            return
        message = result.refusal().lower()
        assert "traceback" not in message, (
            f"{result.expectation.label} failed with a traceback rather than "
            "an explanation:\n" + result.output[-2000:]
        )


class TestTheStructureIsOneObject:
    """6B73 was prepared 51 nm across for 1,104 residues -- rebuilt terminal
    residues had walked off in every direction -- and nothing noticed until
    `addMembrane` failed with a NaN ten minutes later."""

    @pytest.mark.parametrize("kind", kinds())
    def test_its_extent_matches_its_size(self, kind, prepared) -> None:
        result = prepared(kind)
        if not result.succeeded:
            pytest.skip("did not build; covered by the preparation test")

        structure = load(result.prepared_pdb)
        coordinates = structure.xyz[0]
        extent = float(np.max(coordinates.max(axis=0) - coordinates.min(axis=0)))
        plausible = EXTENT_TOLERANCE * (structure.n_residues ** (1 / 3))

        assert extent <= plausible, (
            f"{result.expectation.label} is {extent:.0f} nm across for "
            f"{structure.n_residues} residues, where a folded structure of "
            f"that size would be under {plausible:.0f} nm."
        )

    @pytest.mark.parametrize("kind", kinds())
    def test_no_part_of_it_is_stranded(self, kind, prepared) -> None:
        """Every residue should have neighbours. One sitting alone is one
        that was placed rather than observed."""
        result = prepared(kind)
        if not result.succeeded:
            pytest.skip("did not build; covered by the preparation test")

        structure = load(result.prepared_pdb)
        centroids = np.array([
            structure.xyz[0][[a.index for a in residue.atoms]].mean(axis=0)
            for residue in structure.topology.residues
        ])
        if len(centroids) < 3:
            pytest.skip("too few residues for the question to mean anything")

        separations = np.linalg.norm(
            centroids[:, None, :] - centroids[None, :, :], axis=-1)
        np.fill_diagonal(separations, np.inf)
        loneliest = float(separations.min(axis=1).max())

        # A residue's nearest neighbour is a bond length away; 2 nm is far
        # beyond any packing and well short of flagging a genuine domain gap.
        assert loneliest < 2.0, (
            f"{result.expectation.label} has a residue {loneliest:.1f} nm "
            "from its nearest neighbour."
        )


class TestTheSystemHoldsWhatTheEntryDescribes:
    """5WYZ built 711,205 atoms and reported success. Nothing asked what was
    in it."""

    @pytest.mark.parametrize("kind", kinds())
    def test_the_residue_count_is_in_range(self, kind, prepared) -> None:
        result = prepared(kind)
        if not result.succeeded:
            pytest.skip("did not build; covered by the preparation test")

        structure = load(result.prepared_pdb)
        low, high = result.expectation.residues
        assert low <= structure.n_residues <= high, (
            f"{result.expectation.label} prepared {structure.n_residues} "
            f"residues, expected between {low} and {high}. Either the entry "
            "is not what the corpus says, or preparation added or lost "
            "something."
        )

    @pytest.mark.parametrize("kind", kinds())
    def test_the_ligands_survived(self, kind, prepared) -> None:
        """A ligand dropped somewhere in preparation is a run answering a
        different question than the one asked."""
        result = prepared(kind)
        expectation = result.expectation
        if not expectation.ligands:
            pytest.skip("nothing expected to survive")
        if not result.succeeded:
            pytest.skip("did not build; covered by the preparation test")

        present = _residue_names(load(result.solvated_pdb))
        missing = [name for name in expectation.ligands if name not in present]
        assert not missing, (
            f"{expectation.label} lost {', '.join(missing)} during "
            "preparation."
        )

    @pytest.mark.parametrize("kind", kinds())
    def test_what_should_be_gone_is_gone(self, kind, prepared) -> None:
        result = prepared(kind)
        expectation = result.expectation
        if not expectation.absent:
            pytest.skip("nothing expected to be discarded")
        if not result.succeeded:
            pytest.skip("did not build; covered by the preparation test")

        present = _residue_names(load(result.prepared_pdb))
        kept = [name for name in expectation.absent if name in present]
        assert not kept, (
            f"{expectation.label} kept {', '.join(kept)}, which should have "
            "been discarded."
        )


class TestTheBoxIsProportionate:
    """5WYZ solvated to 711,205 atoms where 147,514 was right: a cube around
    a structure stretched by residues nobody observed. Neither number was
    checked against anything."""

    @pytest.mark.parametrize("kind", kinds())
    def test_the_water_is_in_proportion_to_the_solute(self, kind, prepared) -> None:
        result = prepared(kind)
        if not result.succeeded:
            pytest.skip("did not build; covered by the preparation test")

        solute = load(result.prepared_pdb).n_atoms
        system = load(result.solvated_pdb).n_atoms
        ratio = system / max(1, solute)
        low, high = SOLVATED_RATIO_RANGE

        assert low <= ratio <= high, (
            f"{result.expectation.label} solvated to {system:,} atoms around "
            f"{solute:,}, a ratio of {ratio:.1f}. Outside {low}-{high} means "
            "the box is wrong rather than the science."
        )


class TestAMembraneSystemHoldsTheProtein:
    """6B73 completed with 193,605 atoms and one receptor embedded upside
    down: the bilayer was right and the protein in it was not."""

    MEMBRANE_KINDS = tuple(
        e.kind for e in CORPUS if e.membrane and not e.refused)

    @pytest.mark.parametrize("kind", MEMBRANE_KINDS)
    def test_a_bilayer_was_built(self, kind, prepared) -> None:
        result = prepared(kind)
        if not result.succeeded:
            pytest.skip("did not build; covered by the preparation test")

        structure = load(result.solvated_pdb)
        phosphates = [
            atom.index for atom in structure.topology.atoms
            if atom.residue.name.upper() in {"POPC", "POP"} and atom.name == "P"
        ]
        assert len(phosphates) > 50, "no lipid headgroups in the built system"

        z = structure.xyz[0][phosphates, 2]
        middle = float(np.median(z))
        lower = float(z[z < middle].mean())
        upper = float(z[z >= middle].mean())
        thickness = upper - lower

        # A POPC bilayer's phosphate-to-phosphate distance is about 3.7 nm.
        assert 2.5 < thickness < 5.0, (
            f"the leaflets are {thickness:.1f} nm apart, which is not a "
            "bilayer."
        )

    @pytest.mark.parametrize("kind", MEMBRANE_KINDS)
    def test_the_protein_spans_it(self, kind, prepared) -> None:
        result = prepared(kind)
        if not result.succeeded:
            pytest.skip("did not build; covered by the preparation test")

        structure = load(result.solvated_pdb)
        z = structure.xyz[0][:, 2]
        phosphates = [
            atom.index for atom in structure.topology.atoms
            if atom.residue.name.upper() in {"POPC", "POP"} and atom.name == "P"
        ]
        middle = float(np.median(z[phosphates]))
        lower = float(z[phosphates][z[phosphates] < middle].mean())
        upper = float(z[phosphates][z[phosphates] >= middle].mean())

        protein = [
            atom.index for atom in structure.topology.atoms
            if atom.residue.is_protein
        ]
        inside = float(np.mean((z[protein] > lower) & (z[protein] < upper)))

        # A protein embedded in a bilayer has a substantial part of itself
        # between the leaflets. A protein sitting beside one does not.
        assert inside > 0.15, (
            f"{result.expectation.label} has {inside:.0%} of its protein "
            "atoms between the leaflets: it is next to the membrane rather "
            "than in it."
        )

    @pytest.mark.parametrize("kind", MEMBRANE_KINDS)
    def test_no_lipid_is_inside_a_pore_of_the_protein(self, kind, prepared) -> None:
        """2POR's packings ran away on lipids OpenMM had left inside its
        barrels, and the one that packed held lipids in its pores. A pore is
        aqueous: no lipid heavy atom belongs in one."""
        from fastmdxplora.lipids import is_lipid
        from fastmdxplora.setup.membrane import (
            PORE_LIPID_MIN_ATOMS,
            lipid_z_range,
            pore_map,
        )

        result = prepared(kind)
        if not result.succeeded:
            pytest.skip("did not build; covered by the preparation test")

        structure = load(result.solvated_pdb)
        xyz = structure.xyz[0]
        # What setup mapped: everything that was there before the bilayer
        # (the protein and what it holds), across the lipids' z range in
        # setup's frame, which the written system does not move from.
        added = {"HOH", "NA", "CL", "K", "NA+", "CL-"}
        solute = np.array([
            atom.index for atom in structure.topology.atoms
            if not is_lipid(atom.residue.name)
            and atom.residue.name.upper() not in added
            and atom.element.symbol != "H"])
        lipid = np.array([
            atom.index for atom in structure.topology.atoms
            if is_lipid(atom.residue.name) and atom.element.symbol != "H"])
        pores = pore_map(xyz[solute], np.ones(len(solute), bool),
                         *lipid_z_range(result.expectation.membrane))
        inside = pores.holds(xyz[lipid])
        counted: dict[int, int] = {}
        for i in lipid[inside]:
            residue = structure.topology.atom(int(i)).residue.index
            counted[residue] = counted.get(residue, 0) + 1
        # As setup counts it: a lipid with this many heavy atoms in a pore.
        residues = sorted(r for r, n in counted.items() if n >= PORE_LIPID_MIN_ATOMS)

        assert not residues, (
            f"{result.expectation.label} has {len(residues)} lipids with heavy "
            "atoms inside the protein's pores, where the bilayer could only "
            "have put them by the protein cutting through them."
        )
