"""The chemistry from the file, the pose from the structure.

A supplied SDF carries bond orders, formal charges and aromaticity -- what a
PDB cannot express and a force field needs. What it does not carry is where
the ligand sits in this protein. An ideal component from the Chemical
Component Dictionary has idealised coordinates, and using them puts the
molecule wherever that geometry happens to lie.

On T4 lysozyme with benzene it lay seventeen Angstroms from the cavity. The
clash check said so -- "closest contact 1.730 nm" -- and passed, correctly,
because seventeen Angstroms is not a clash. The run went ahead and was a
benzene floating in solvent rather than a benzene in a binding site.

The pose was never missing. It is in the structure the author supplied, which
has the crystallographic coordinates and no chemistry. Taking one from each is
what supplying both means.

These tests use a stand-in for the OpenFF molecule: the toolkit is
conda-forge-only and not installable where these run. What they check is the
arithmetic and the refusals -- that the right atoms move, that hydrogens come
along, and that a mismatch is declined rather than forced.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from fastmdxplora.setup.ligand import pose_from_structure


class _Atom:
    def __init__(self, atomic_number: int):
        self.atomic_number = atomic_number


class _Quantity:
    """Stands in for an OpenFF unit-carrying array."""

    def __init__(self, values):
        self._values = np.asarray(values, dtype=float)

    def m_as(self, _unit):
        return self._values


class _Molecule:
    """Benzene: six carbons and six hydrogens, at an arbitrary place."""

    def __init__(self, offset: float = 5.0):
        self.atoms = [_Atom(6)] * 6 + [_Atom(1)] * 6
        ring = np.array([[np.cos(a), np.sin(a), 0.0]
                         for a in np.linspace(0, 2 * np.pi, 6, endpoint=False)])
        hydrogens = ring * 1.8
        self._conformers = [_Quantity(np.vstack([ring, hydrogens]) * 0.1
                                      + offset)]

    @property
    def conformers(self):
        return self._conformers


def _structure(tmp_path: Path, resname: str = "BNZ", atoms: int = 6) -> Path:
    """A PDB holding a residue at a known place."""
    lines = []
    for index in range(atoms):
        lines.append(
            f"HETATM{index + 1:5d}  C{index + 1:<2d} {resname:>3s} A 999    "
            f"{index:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C")
    lines.append("END")
    path = tmp_path / "input.pdb"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestThePoseIsTakenFromTheStructure:
    def test_the_heavy_atoms_move_there(self, tmp_path: Path) -> None:
        molecule = _Molecule(offset=5.0)
        moved, said = pose_from_structure(
            molecule, _structure(tmp_path), "BNZ")
        placed = moved.conformers[0].m_as("nanometer")[:6]
        # The structure has its carbons along x at 0,1,2... Angstrom, and
        # mdtraj returns nanometres: a tenth of the number in the file.
        assert np.allclose(placed[:, 0], [0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
                           atol=1e-3)
        assert said and "rather than the supplied file's" in said

    def test_the_hydrogens_come_along(self, tmp_path: Path) -> None:
        """Moving the heavy atoms and leaving the hydrogens would tear the
        molecule apart, and the clash check would then refuse a pose that is
        actually correct."""
        molecule = _Molecule(offset=5.0)
        before = molecule.conformers[0].m_as("nanometer").copy()
        moved, _ = pose_from_structure(molecule, _structure(tmp_path), "BNZ")
        after = moved.conformers[0].m_as("nanometer")
        shift = after[6:] - before[6:]
        assert np.allclose(shift, shift[0], atol=1e-6), "hydrogens dispersed"

    def test_it_says_what_it_did(self, tmp_path: Path) -> None:
        _, said = pose_from_structure(_Molecule(), _structure(tmp_path), "BNZ")
        assert "BNZ" in said and "input.pdb" in said


class TestItDeclinesRatherThanForces:
    def test_a_residue_that_is_not_there_leaves_the_file_alone(
            self, tmp_path: Path) -> None:
        """Placing a ligand deliberately, rather than reading it out of a
        complex, is a legitimate thing to do."""
        molecule = _Molecule()
        before = molecule.conformers[0].m_as("nanometer").copy()
        moved, said = pose_from_structure(
            molecule, _structure(tmp_path, resname="XYZ"), "BNZ")
        assert said is None
        assert np.allclose(moved.conformers[0].m_as("nanometer"), before)

    def test_a_different_number_of_atoms_is_refused(self, tmp_path: Path) -> None:
        """Sharing a residue name is not being the same molecule, and forcing
        the coordinates across would produce a plausible wrong geometry."""
        molecule = _Molecule()
        _, said = pose_from_structure(
            molecule, _structure(tmp_path, atoms=4), "BNZ")
        assert said and "not the same molecule" in said

    def test_an_unreadable_structure_is_not_an_error(self, tmp_path: Path) -> None:
        broken = tmp_path / "input.pdb"
        broken.write_text("not a pdb\n", encoding="utf-8")
        molecule = _Molecule()
        moved, said = pose_from_structure(molecule, broken, "BNZ")
        assert moved is molecule

    def test_a_missing_structure_is_not_an_error(self, tmp_path: Path) -> None:
        molecule = _Molecule()
        moved, said = pose_from_structure(
            molecule, tmp_path / "nothing.pdb", "BNZ")
        assert moved is molecule
        assert said is None or "could not read" in said


class TestThePreparationUsesTheOriginal:
    def test_it_reads_the_input_not_the_prepared_structure(self) -> None:
        """PDBFixer removes the heterogens before writing `prepared.pdb`, so
        the ligand's coordinates survive only in the input."""
        import fastmdxplora.setup.prepare as prepare

        source = Path(prepare.__file__).read_text(encoding="utf-8")
        assert 'Path(output_dir) / "input.pdb"' in source
        # The call gained a `copy` argument when the same component
        # appearing twice became placeable, and then became pose_by_policy
        # when the pose became choosable -- the policy wraps the same
        # mechanism, so reading the input is still what happens. This checks
        # the arguments rather than the exact call text.
        assert "pose_by_policy(" in source
        assert "original, name" in source


class TestBothSituationsAreDocumented:
    """A complex plus chemistry, and an apo protein plus a docked pose. The
    same two files, and opposite answers to which says where the ligand is."""

    @staticmethod
    def _page() -> str:
        return (Path(__file__).resolve().parents[1] / "docs" /
                "examples.md").read_text(encoding="utf-8")

    def test_the_complex_case_says_the_structure_wins(self) -> None:
        page = self._page()
        assert "### The ligand is in the structure" in page
        assert "**The structure wins.**" in page

    def test_the_docked_case_says_the_file_wins(self) -> None:
        page = self._page()
        assert "### The ligand is not in the structure" in page
        assert "**The file wins**" in page

    def test_it_says_the_author_owns_the_pose_in_that_case(self) -> None:
        # Not a phrase spanning a line break: the page wraps at 79
        # characters, so "a pose that is wrong" is split and an exact match
        # fails on prose that is present and correct.
        assert "will simulate perfectly well" in self._page()

    def test_the_docstring_names_both(self) -> None:
        from fastmdxplora.setup.ligand import pose_from_structure

        doc = pose_from_structure.__doc__ or ""
        assert "The ligand is in the structure" in doc
        assert "The ligand is not in the structure" in doc
