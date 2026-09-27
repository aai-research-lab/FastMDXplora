"""A control should be a choice rather than an accident.

The T4 lysozyme unbound control began life as a bug: the ideal component's
coordinates were used on a complex, and benzene spent twenty nanoseconds in
solvent instead of the cavity. The trajectory turned out to be exactly the
known-negative the contact analyses needed -- and, once the bug was fixed,
impossible to produce on purpose. The pose always came from the structure,
and there was no way to say otherwise.

`ligand_pose` says otherwise, in both directions. `file` keeps the supplied
coordinates even on a complex: the unbound start, asked for by name.
`structure` requires the structure's pose, so every quiet fall-back to the
file's arbitrary geometry -- the seventeen-Angstroms failure -- becomes a
refusal instead of a run that looks right.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.setup.ligand import (
    POSE_POLICIES,
    LigandError,
    pose_by_policy,
    pose_from_structure,
)
from tests.test_the_pose_comes_from_the_structure import _Molecule, _structure


class TestAuto:
    """The default: the files decide, and where they cannot, it refuses."""

    def test_auto_is_the_default_everywhere(self) -> None:
        from fastmdxplora.config.schema import SETUP

        assert SETUP.defaults()["ligand_pose"] == "auto"

    def test_a_complex_still_gives_the_structure_pose(self, tmp_path: Path) -> None:
        molecule = _Molecule(offset=5.0)
        moved, said = pose_by_policy(
            molecule, _structure(tmp_path), "BNZ", policy="auto")
        heavy = moved.conformers[0].m_as("nanometer")[:6]
        assert "placed BNZ" in said
        assert not np.allclose(heavy, _Molecule(offset=5.0)
                               .conformers[0].m_as("nanometer")[:6])

    def test_an_apo_structure_still_lets_the_file_stand(self, tmp_path: Path) -> None:
        _, said = pose_by_policy(
            _Molecule(), _structure(tmp_path, resname="HED"), "BNZ",
            policy="auto")
        assert said is None

    @pytest.mark.parametrize("structure", ["four atoms", "one copy for two"])
    def test_a_residue_whose_pose_cannot_be_taken_is_refused(
            self, tmp_path: Path, structure: str) -> None:
        """The structure says the ligand is there and its pose cannot be
        read off it. `auto` fell back to the file's coordinates, which on a
        complex start the ligand away from its site with a log line to say
        so; it refuses now, and says how to ask for the file's pose."""
        path = _structure(tmp_path, atoms=4 if structure == "four atoms" else 6)
        copy = 1 if structure == "one copy for two" else 0
        with pytest.raises(LigandError) as refused:
            pose_by_policy(_Molecule(), path, "BNZ", policy="auto", copy=copy)
        assert refused.value.code == "setup.ligand.pose_unavailable"
        assert "ligand_pose: file" in str(refused.value)
        # Asked for by name, the file's pose still stands on the same files.
        _, said = pose_by_policy(_Molecule(), path, "BNZ", policy="file", copy=copy)
        assert "by request" in said


class TestFile:
    """The unbound start, by name."""

    def test_the_supplied_coordinates_stand_on_a_complex(self, tmp_path: Path) -> None:
        """The structure holds BNZ; the file's pose is kept anyway. This is
        the run that could previously only happen by accident."""
        molecule = _Molecule(offset=5.0)
        before = molecule.conformers[0].m_as("nanometer").copy()
        kept, said = pose_by_policy(
            molecule, _structure(tmp_path), "BNZ", policy="file")
        assert np.allclose(kept.conformers[0].m_as("nanometer"), before)
        assert "by request" in said and "ligand_pose: file" in said

    def test_it_needs_no_structure_at_all(self, tmp_path: Path) -> None:
        """A deliberate pose should not depend on a file it was told to
        ignore."""
        kept, said = pose_by_policy(
            _Molecule(), tmp_path / "never_written.pdb", "BNZ", policy="file")
        assert "by request" in said


class TestStructure:
    """The bound run that refuses to become the unbound one silently."""

    def test_a_matching_residue_is_taken_as_before(self, tmp_path: Path) -> None:
        _, said = pose_by_policy(
            _Molecule(), _structure(tmp_path), "BNZ", policy="structure")
        assert "placed BNZ" in said

    def test_no_such_residue_is_a_refusal_not_a_fallback(self, tmp_path: Path) -> None:
        """In `auto` this stands silently, which is right for an apo system
        and the seventeen-Angstroms failure on a complex. `structure` is the
        author saying which one this is."""
        with pytest.raises(LigandError) as caught:
            pose_by_policy(_Molecule(), _structure(tmp_path, resname="HED"),
                           "BNZ", policy="structure")
        said = str(caught.value)
        assert "holds no residue named BNZ" in said
        assert "ligand_pose: file" in said  # the road not taken, named

    def test_an_atom_count_mismatch_refuses_too(self, tmp_path: Path) -> None:
        with pytest.raises(LigandError, match="not the same molecule"):
            pose_by_policy(_Molecule(),
                           _structure(tmp_path, resname="BNZ", atoms=4),
                           "BNZ", policy="structure")

    def test_the_mechanism_carries_the_flag_itself(self, tmp_path: Path) -> None:
        """`required` lives on pose_from_structure, so a future caller cannot
        reach the mechanism and forget the policy."""
        with pytest.raises(LigandError):
            pose_from_structure(_Molecule(),
                                _structure(tmp_path, resname="HED"),
                                "BNZ", required=True)


class TestThePolicyItself:
    def test_an_unknown_policy_is_refused_with_the_list(self, tmp_path: Path) -> None:
        with pytest.raises(LigandError) as caught:
            pose_by_policy(_Molecule(), _structure(tmp_path), "BNZ",
                           policy="crystal")
        said = str(caught.value)
        assert all(option in said for option in POSE_POLICIES)

    def test_case_and_whitespace_do_not_matter(self, tmp_path: Path) -> None:
        _, said = pose_by_policy(_Molecule(), _structure(tmp_path), "BNZ",
                                 policy=" File ")
        assert "by request" in said

    def test_prepare_accepts_the_parameter(self) -> None:
        """Threaded, not just defined: the pipeline passes what the schema
        names, and prepare has somewhere for it to land."""
        import inspect

        from fastmdxplora.setup.prepare import prepare_system

        assert "ligand_pose" in inspect.signature(prepare_system).parameters
