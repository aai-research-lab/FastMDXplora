"""Looking at a structure says when setup builds more chains than the file.

Setup builds the biological assembly a file declares. The Agent's
`inspect_structure` gave the file's chains and residue count beside the
assembly's titratable residues without saying which was which: for 1HHO,
chains A and B and 287 residues beside 38 histidines, twice the file's.
It now says that setup builds the assembly, its chains, and that the
residues listed are the assembly's; a file that is its own assembly is
said as before.
"""

from __future__ import annotations

from fastmdxplora.agent.tools import Toolbox
from tests.test_what_setup_will_build_is_said_before_it_runs import _file, _residue

DIMER = (
    "REMARK 350 BIOMOLECULE: 1\n"
    "REMARK 350 AUTHOR DETERMINED BIOLOGICAL UNIT: DIMERIC\n"
    "REMARK 350 APPLY THE FOLLOWING TO CHAINS: A\n"
    "REMARK 350   BIOMT1   1  1.000000  0.000000  0.000000        0.00000\n"
    "REMARK 350   BIOMT2   1  0.000000  1.000000  0.000000        0.00000\n"
    "REMARK 350   BIOMT3   1  0.000000  0.000000  1.000000        0.00000\n"
    "REMARK 350   BIOMT1   2  1.000000  0.000000  0.000000       40.00000\n"
    "REMARK 350   BIOMT2   2  0.000000  1.000000  0.000000        0.00000\n"
    "REMARK 350   BIOMT3   2  0.000000  0.000000  1.000000        0.00000\n"
)
CHAIN = (_residue(1, "GLY", "A", 1, 0.0) + _residue(5, "HIS", "A", 2, 4.0)
         + _residue(9, "GLY", "A", 3, 8.0))


def test_the_assembly_is_said(tmp_path) -> None:
    said = Toolbox().use("inspect_structure",
                         {"system": str(_file(tmp_path, DIMER + CHAIN + "END\n"))}).said
    assert "chains: A; 3 protein residues" in said
    assert ("setup builds the biological assembly the file declares: chains A, B; "
            "the residues below are the assembly's") in said
    assert "HIS 2 (A:2, B:2)" in said


def test_a_file_that_is_its_own_assembly_is_not(tmp_path) -> None:
    said = Toolbox().use("inspect_structure",
                         {"system": str(_file(tmp_path, CHAIN + "END\n"))}).said
    assert "biological assembly" not in said
    assert "HIS 1 (A:2)" in said
