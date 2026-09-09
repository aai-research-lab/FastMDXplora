"""`fastmdx select` says what an expression matches.

A selection matching no atoms is refused when a study starts. A selection
matching the *wrong* atoms is not an error and nothing downstream can make it
one: `resid 189` and `resSeq 189` are both valid, both non-empty, and name
different residues in any structure not numbered from one without gaps. The
only way to know which was meant is to look, and this is how.
"""

from __future__ import annotations

from pathlib import Path

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.cli.main import main


@pytest.fixture
def gapped(tmp_path: Path) -> Path:
    """A structure numbered as a deposited entry is: from 16, with gaps.

    Numbering like this is what makes `resid` and `resSeq` disagree, which
    is the whole reason this command exists.
    """
    names = ["ALA", "GLY", "SER", "ASP", "SER", "CYS",
             "GLN", "GLY", "ASP", "SER", "VAL", "TRP"]
    numbers = [16, 17, 18, 189, 190, 191, 192, 193, 194, 195, 213, 215]

    topology = md.Topology()
    chain = topology.add_chain()
    for name, number in zip(names, numbers):
        residue = topology.add_residue(name, chain, resSeq=number)
        topology.add_atom("N", md.element.nitrogen, residue)
        topology.add_atom("CA", md.element.carbon, residue)
        topology.add_atom("C", md.element.carbon, residue)

    xyz = np.random.RandomState(0).rand(
        1, topology.n_atoms, 3).astype(np.float32)
    path = tmp_path / "gapped.pdb"
    md.Trajectory(xyz, topology).save_pdb(str(path))
    return path


class TestItSaysWhatMatched:

    def test_the_residues_are_named(self, gapped, capfd):
        rc = main(["select", "resSeq 189 to 195 and name CA", "-s", str(gapped)])
        out = capfd.readouterr().out

        assert rc == 0
        assert "7 of 36 atoms" in out
        for residue in ("ASP189", "SER190", "CYS191", "GLN192",
                        "GLY193", "ASP194", "SER195"):
            assert residue in out

    def test_resid_and_resseq_name_different_residues(self, gapped, capfd):
        """The defect this command exists to expose, on one structure."""
        main(["select", "resSeq 16 to 18 and name CA", "-s", str(gapped)])
        by_number = capfd.readouterr().out
        main(["select", "resid 16 to 18 and name CA", "-s", str(gapped)])
        by_index = capfd.readouterr().out

        assert "ALA16" in by_number and "GLY17" in by_number
        # Counting from zero, index 16 is well past the residues numbered 16-18.
        assert "ALA16" not in by_index
        assert by_number != by_index, (
            "If these ever agree the fixture has stopped being numbered like "
            "a deposited entry, and the test has stopped testing anything.")

    def test_atoms_can_be_listed_instead(self, gapped, capfd):
        main(["select", "resSeq 189 and name CA", "-s", str(gapped), "--atoms"])
        out = capfd.readouterr().out

        assert "ASP189-CA" in out

    def test_the_listing_is_truncated_and_says_so(self, gapped, capfd):
        main(["select", "all", "-s", str(gapped), "--limit", "3"])
        out = capfd.readouterr().out

        assert "more)" in out

    def test_limit_zero_lists_everything(self, gapped, capfd):
        main(["select", "all", "-s", str(gapped), "--limit", "0"])
        out = capfd.readouterr().out

        assert "more)" not in out
        assert "TRP215" in out


class TestItRefusesRatherThanMisleads:

    def test_an_empty_match_exits_non_zero(self, gapped, capfd):
        rc = main(["select", "resSeq 900", "-s", str(gapped)])
        out = capfd.readouterr().out

        assert rc != 0, "A script should be able to gate on this."
        assert "0 of 36 atoms" in out

    def test_an_empty_match_names_the_likely_cause(self, gapped, capfd):
        main(["select", "resSeq 900", "-s", str(gapped)])
        out = capfd.readouterr().out

        assert "resSeq" in out and "resid" in out, (
            "An empty match is the one case where the next thing to try is "
            "known, so it should be said rather than left to be inferred.")

    def test_an_invalid_expression_is_refused_by_name(self, gapped, capfd):
        rc = main(["select", "protein and nonsense", "-s", str(gapped)])
        err = capfd.readouterr().err

        assert rc == 2
        assert "protein and nonsense" in err

    def test_a_missing_structure_is_refused(self, tmp_path, capfd):
        rc = main(["select", "protein", "-s", str(tmp_path / "absent.pdb")])
        err = capfd.readouterr().err

        assert rc == 2
        assert "absent.pdb" in err


class TestItIsReachableAndDocumented:

    def test_the_command_is_in_the_parser(self):
        from fastmdxplora.cli.main import _build_parser

        parser = _build_parser()
        actions = [a for a in parser._actions
                   if hasattr(a, "choices") and a.choices]
        names = set()
        for action in actions:
            try:
                names.update(action.choices)
            except TypeError:  # pragma: no cover - non-iterable choices
                pass
        assert "select" in names

    def test_the_docs_offer_it_instead_of_a_script(self):
        """The reason it was written.

        The selections page used to answer "check before you run" with a
        Python snippet importing a trajectory library directly. Doing that
        by hand is the thing this software exists to remove.
        """
        page = (Path(__file__).resolve().parents[1]
                / "docs" / "selections.md").read_text(encoding="utf-8")

        assert "fastmdx select" in page
        assert "import mdtraj" not in page, (
            "A user should not have to write a script to find out what their "
            "own selection matches.")
