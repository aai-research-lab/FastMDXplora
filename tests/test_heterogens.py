"""Tests for deciding what a structure's heterogens mean.

The contract under test is narrow and important: a component is simulated
only when the structure determines that it should be, discarded only when the
structure determines that it should be, and otherwise setup refuses. A wrong
answer here produces a trajectory that looks correct and is not.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from fastmdxplora.setup.heterogens import (
    Action,
    AmbiguousStructureError,
    resolve,
)


def _atom(record, serial, name, altloc, resname, chain, seq, x, y, z,
          occupancy=1.00, element="C"):
    return (
        f"{record:<6}{serial:>5} {name:<4}{altloc:1}{resname:>3} "
        f"{chain:1}{seq:>4}    {x:8.3f}{y:8.3f}{z:8.3f}"
        f"{occupancy:6.2f}  0.00          {element:>2}"
    )


def _structure(lines) -> Path:
    path = Path(tempfile.mkdtemp()) / "structure.pdb"
    path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
    return path


PROTEIN = [
    _atom("ATOM", 1, "N", " ", "ALA", "A", 1, 0, 0, 0, element="N"),
    _atom("ATOM", 2, "CA", " ", "ALA", "A", 1, 1.4, 0, 0),
    _atom("ATOM", 3, "O", " ", "ALA", "A", 1, 2.4, 0.5, 0, element="O"),
]


def _actions(path, **kwargs):
    return {d.resname: d.action for d in resolve(path, **kwargs)}


class TestDeterminateStructures:
    """Cases where the structure does answer the question."""

    def test_ligand_kept_buffer_and_water_dropped(self) -> None:
        """The 4W52 case: benzene is the point, HEPES and water are not."""
        lines = list(PROTEIN)
        lines += [_atom("HETATM", 10 + i, f"C{i}", " ", "BNZ", "A", 201,
                        5 + i * 0.1, 5, 5) for i in range(6)]
        lines += [_atom("HETATM", 30 + i, f"C{i}", " ", "EPE", "A", 301,
                        20 + i * 0.1, 20, 20) for i in range(8)]
        lines += [_atom("HETATM", 50, "O", " ", "HOH", "A", 401, 9, 9, 9,
                        element="O")]

        actions = _actions(_structure(lines))
        assert actions["BNZ"] is Action.SIMULATE
        assert actions["EPE"] is Action.DISCARD
        assert actions["HOH"] is Action.DISCARD

    def test_coordinated_metal_kept_free_metal_dropped(self) -> None:
        """A catalytic zinc is structural; a stray sodium is liquor."""
        lines = [
            _atom("ATOM", 1, "NE2", " ", "HIS", "A", 1, 0, 0, 0, element="N"),
            _atom("HETATM", 2, "ZN", " ", "ZN", "A", 101, 2.1, 0, 0, element="ZN"),
            _atom("HETATM", 3, "NA", " ", "NA", "A", 102, 30, 30, 30, element="NA"),
        ]
        actions = _actions(_structure(lines))
        assert actions["ZN"] is Action.SIMULATE
        assert actions["NA"] is Action.DISCARD

    def test_several_distinct_ligands_are_all_simulated(self) -> None:
        lines = list(PROTEIN)
        lines += [_atom("HETATM", 10, "C1", " ", "BNZ", "A", 201, 5, 5, 5)]
        lines += [_atom("HETATM", 11, "C1", " ", "STI", "A", 202, 9, 9, 9)]
        actions = _actions(_structure(lines))
        assert actions["BNZ"] is Action.SIMULATE
        assert actions["STI"] is Action.SIMULATE

    def test_major_conformation_wins_when_occupancy_separates_them(self) -> None:
        lines = list(PROTEIN)
        lines += [
            _atom("HETATM", 10, "C1", "A", "STI", "A", 201, 5, 5, 5, occupancy=0.70),
            _atom("HETATM", 11, "C1", "B", "STI", "A", 201, 6, 5, 5, occupancy=0.30),
        ]
        assert _actions(_structure(lines))["STI"] is Action.SIMULATE

    def test_water_can_be_retained_on_request(self) -> None:
        lines = list(PROTEIN)
        lines += [_atom("HETATM", 50, "O", " ", "HOH", "A", 401, 9, 9, 9,
                        element="O")]
        actions = _actions(_structure(lines), keep_water=True)
        assert actions["HOH"] is Action.SIMULATE

    def test_an_apo_structure_needs_no_decisions(self) -> None:
        assert resolve(_structure(PROTEIN)) == []


class TestRefusals:
    """Cases where guessing would produce a plausible, wrong trajectory."""

    def test_covalent_adduct_stops_setup(self) -> None:
        """A covalent ligand is part of the macromolecule, not a free molecule."""
        lines = list(PROTEIN)
        lines += ["LINK         C1  LIG A 201                 SG  CYS A   1"]
        lines += [_atom("HETATM", 10, "C1", " ", "LIG", "A", 201, 3, 0, 0)]

        with pytest.raises(AmbiguousStructureError, match="covalently bonded"):
            resolve(_structure(lines))

    def test_unknown_component_stops_setup(self) -> None:
        lines = list(PROTEIN)
        lines += [_atom("HETATM", 10, "C1", " ", "UNL", "A", 201, 5, 5, 5)]

        with pytest.raises(AmbiguousStructureError, match="does not say what"):
            resolve(_structure(lines))

    def test_cofactor_needing_special_parameters_stops_setup(self) -> None:
        """Heme is real and essential; OpenFF cannot describe it."""
        lines = list(PROTEIN)
        lines += [_atom("HETATM", 10 + i, f"C{i}", " ", "HEM", "A", 201, 5, 5, 5)
                  for i in range(4)]

        with pytest.raises(AmbiguousStructureError, match="heme"):
            resolve(_structure(lines))

    def test_tied_conformations_stop_setup(self) -> None:
        """Equal occupancy carries no information about which is real."""
        lines = list(PROTEIN)
        lines += [
            _atom("HETATM", 10, "C1", "A", "STI", "A", 201, 5, 5, 5, occupancy=0.50),
            _atom("HETATM", 11, "C1", "B", "STI", "A", 201, 6, 5, 5, occupancy=0.50),
        ]
        with pytest.raises(AmbiguousStructureError, match="occupancy"):
            resolve(_structure(lines))

    def test_a_link_record_to_a_metal_is_coordination_not_a_covalent_bond(self) -> None:
        """The legacy PDB format uses one record type for both.

        Only mmCIF distinguishes them (struct_conn.conn_type_id is "metalc"
        for a metal, "covale" for a covalent bond). Reading every LINK as
        covalent made every metalloprotein refuse: barnase, insulin, and
        haemoglobin all stopped on their zinc or iron.
        """
        lines = [
            _atom("ATOM", 1, "NE2", " ", "HIS", "A", 1, 10, 10, 10, element="N"),
            "LINK         NE2 HIS A   1                ZN    ZN A 112     1555   1555  2.10",
            _atom("HETATM", 50, "ZN", " ", "ZN", "A", 112, 12.1, 10, 10, element="ZN"),
        ]
        actions = _actions(_structure(lines))
        assert actions["ZN"] is Action.SIMULATE

    def test_a_link_from_a_ligand_to_a_metal_is_coordination(self) -> None:
        """The metal rule read from the other end of the same record.

        Both partners of a LINK are recorded, so a nucleotide chelating a
        magnesium was marked as linked and refused as a covalent adduct. 5P21
        (Ras with GppNHp and Mg) and 1ATP (protein kinase A with ATP and Mn)
        are the canonical cases, and neither ligand is bonded to anything.
        """
        lines = list(PROTEIN)
        lines += [
            "LINK        O2G  GNP A 200                MG    MG A 201     1555   1555  2.05",
            _atom("HETATM", 400, "O2G", " ", "GNP", "A", 200, 6, 0, 0, element="O"),
            _atom("HETATM", 401, "MG", " ", "MG", "A", 201, 8.05, 0, 0, element="MG"),
        ]
        actions = _actions(_structure(lines))
        assert actions["GNP"] is Action.SIMULATE

    def test_a_ligand_bonded_to_both_a_metal_and_the_polymer_still_stops(self) -> None:
        """One innocent partner does not excuse a genuine covalent linkage."""
        lines = list(PROTEIN)
        lines += [
            "LINK        O2G  GNP A 200                MG    MG A 201     1555   1555  2.05",
            "LINK         C1  GNP A 200                 SG  CYS A   1",
            _atom("HETATM", 400, "O2G", " ", "GNP", "A", 200, 6, 0, 0, element="O"),
            _atom("HETATM", 401, "MG", " ", "MG", "A", 201, 8.05, 0, 0, element="MG"),
        ]
        with pytest.raises(AmbiguousStructureError, match="covalently bonded"):
            resolve(_structure(lines))

    def test_the_refusal_names_what_the_component_is_bonded_to(self) -> None:
        """"Bonded to the polymer" does not say where to look."""
        lines = list(PROTEIN)
        lines += ["LINK         C1  PJE C   5                 SG  CYS A   1"]
        lines += [_atom("HETATM", 10, "C1", " ", "PJE", "C", 5, 3, 0, 0)]

        with pytest.raises(AmbiguousStructureError, match="bonded to CYS"):
            resolve(_structure(lines))

    def test_a_link_record_to_an_organic_group_is_still_covalent(self) -> None:
        """A covalent inhibitor or a glycan must still stop the run."""
        lines = list(PROTEIN)
        lines += ["LINK         C1  PJE C   5                 SG  CYS A   1"]
        lines += [_atom("HETATM", 10, "C1", " ", "PJE", "C", 5, 3, 0, 0)]

        with pytest.raises(AmbiguousStructureError, match="covalently bonded"):
            resolve(_structure(lines))

    def test_partly_coordinated_metal_stops_setup(self) -> None:
        """Keeping or dropping the component as a whole would both be wrong."""
        lines = [
            _atom("ATOM", 1, "NE2", " ", "HIS", "A", 1, 0, 0, 0, element="N"),
            _atom("HETATM", 2, "ZN", " ", "ZN", "A", 101, 2.1, 0, 0, element="ZN"),
            _atom("HETATM", 3, "ZN", " ", "ZN", "A", 102, 40, 40, 40, element="ZN"),
        ]
        with pytest.raises(AmbiguousStructureError, match="coordinated"):
            resolve(_structure(lines))

    def test_the_message_names_everything_unresolved(self) -> None:
        """One run should surface every problem, not the first one only."""
        lines = list(PROTEIN)
        lines += [_atom("HETATM", 10, "C1", " ", "UNL", "A", 201, 5, 5, 5)]
        lines += [_atom("HETATM", 20, "C1", " ", "HEM", "A", 202, 9, 9, 9)]

        with pytest.raises(AmbiguousStructureError) as excinfo:
            resolve(_structure(lines))
        message = str(excinfo.value)
        assert "UNL" in message
        assert "HEM" in message
        assert "Nothing has been simulated" in message


class TestParsing:
    """The classifier is only as good as what it reads out of the file."""

    def test_modified_polymer_residues_are_not_heterogens(self) -> None:
        """Selenomethionine and protonation variants are protein."""
        lines = [
            _atom("ATOM", 1, "N", " ", "MSE", "A", 1, 0, 0, 0, element="N"),
            _atom("ATOM", 2, "N", " ", "HID", "A", 2, 3, 0, 0, element="N"),
            _atom("ATOM", 3, "N", " ", "CYX", "A", 3, 6, 0, 0, element="N"),
        ]
        assert resolve(_structure(lines)) == []

    def test_malformed_lines_do_not_crash_the_run(self) -> None:
        lines = list(PROTEIN) + ["HETATM    x garbage record"]
        resolve(_structure(lines))  # must not raise


class TestSugarsAreNotAssumed:
    """A sugar can be a modification, a substrate, or antifreeze."""

    def test_n_acetylglucosamine_stops_rather_than_being_discarded(self) -> None:
        """NAG is a glycosylation site as often as it is a spectator."""
        lines = list(PROTEIN)
        lines += [_atom("HETATM", 10, "C1", " ", "NAG", "A", 201, 5, 5, 5)]

        with pytest.raises(AmbiguousStructureError, match="sugar"):
            resolve(_structure(lines))

    def test_sucrose_stops_too(self) -> None:
        """Usually a cryoprotectant, but a substrate for invertases."""
        lines = list(PROTEIN)
        lines += [_atom("HETATM", 10, "C1", " ", "SUC", "A", 201, 5, 5, 5)]

        with pytest.raises(AmbiguousStructureError, match="sugar"):
            resolve(_structure(lines))

    def test_the_message_points_at_carbohydrate_parameters(self) -> None:
        lines = list(PROTEIN)
        lines += [_atom("HETATM", 10, "C1", " ", "NAG", "A", 201, 5, 5, 5)]
        with pytest.raises(AmbiguousStructureError) as excinfo:
            resolve(_structure(lines))
        assert "GLYCAM" in str(excinfo.value)


class TestPolicy:
    """The three-way policy, and what each setting implies."""

    @staticmethod
    def _with(names):
        lines = list(PROTEIN)
        for i, name in enumerate(names):
            lines.append(_atom("HETATM", 10 + i, "C1", " ", name, "A", 201 + i,
                               5 + i, 5, 5))
        return _structure(lines)

    def test_drop_is_unconditional_and_is_the_default(self) -> None:
        from fastmdxplora.setup.pipeline import _keep_heterogens

        assert _keep_heterogens({}, self._with(["BNZ"])) is False
        assert _keep_heterogens({"heterogens": "drop"}, self._with(["BNZ"])) is False

    def test_keep_is_unconditional(self) -> None:
        from fastmdxplora.setup.pipeline import _keep_heterogens

        assert _keep_heterogens({"heterogens": "keep"}, self._with(["BNZ"])) is True

    def test_the_legacy_flag_still_means_keep(self) -> None:
        from fastmdxplora.setup.pipeline import _keep_heterogens

        assert _keep_heterogens({"keep_heterogens": True}, self._with(["BNZ"])) is True

    def test_auto_drops_a_structure_holding_only_additives(self) -> None:
        from fastmdxplora.setup.pipeline import _keep_heterogens

        assert _keep_heterogens({"heterogens": "auto"}, self._with(["GOL", "HOH"])) is False

    def test_auto_strips_the_originals_because_ligands_are_re_added(self) -> None:
        """Components worth simulating go through the small-molecule path.

        PDBFixer removes them from the structure, and they return carrying
        real parameters rather than being kept as unparameterized residues.
        """
        from fastmdxplora.setup.pipeline import _keep_heterogens

        assert _keep_heterogens({"heterogens": "auto"}, self._with(["BNZ"])) is False

    def test_an_unknown_policy_is_rejected(self) -> None:
        from fastmdxplora.setup.pipeline import _keep_heterogens

        with pytest.raises(ValueError, match="unknown policy"):
            _keep_heterogens({"heterogens": "sometimes"}, self._with(["GOL"]))


class TestFailuresExplainThemselves:
    """A refusal is only useful if its reason reaches the user."""

    def test_single_phase_runs_report_why_they_failed(self, tmp_path) -> None:
        """`explore` reports through the orchestrator; `setup` had no such path.

        A local file carries no PDB entry, so its ligand chemistry cannot be
        looked up. That refusal has to reach the user, or setup merely appears
        to have broken.
        """
        import io
        import logging

        from fastmdxplora.cli.main import main

        # The package logger does not propagate to root, so pytest's caplog
        # never sees it; attach to the package logger directly.
        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        package_logger = logging.getLogger("fastmdx")
        package_logger.addHandler(handler)

        structure = tmp_path / "s.pdb"
        structure.write_text(
            # A tripeptide rather than a lone residue: one amino acid is
        # simultaneously N- and C-terminal, which AMBER has no template for.
        # These fixtures test pipeline mechanics, not force-field edge cases.
                "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
                "ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C\n"
        "ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00           C\n"
        "ATOM      4  O   ALA A   1       1.251   2.390   0.000  1.00  0.00           O\n"
        "ATOM      5  CB  ALA A   1       1.988  -0.773  -1.199  1.00  0.00           C\n"
        "ATOM      6  N   GLY A   2       3.332   1.549   0.000  1.00  0.00           N\n"
        "ATOM      7  CA  GLY A   2       3.972   2.849   0.000  1.00  0.00           C\n"
        "ATOM      8  C   GLY A   2       5.486   2.705   0.000  1.00  0.00           C\n"
        "ATOM      9  O   GLY A   2       6.008   1.593   0.000  1.00  0.00           O\n"
        "ATOM     10  N   ALA A   3       6.171   3.845   0.000  1.00  0.00           N\n"
        "ATOM     11  CA  ALA A   3       7.623   3.845   0.000  1.00  0.00           C\n"
        "ATOM     12  C   ALA A   3       8.174   5.265   0.000  1.00  0.00           C\n"
        "ATOM     13  O   ALA A   3       7.416   6.235   0.000  1.00  0.00           O\n"
        "ATOM     14  CB  ALA A   3       8.153   3.072  -1.199  1.00  0.00           C\n"
        "ATOM     15  OXT ALA A   3       9.400   5.400   0.000  1.00  0.00           O\n"
        "HETATM   10  C1  BNZ A 201       5.000   5.000   5.000  1.00  0.00           C\n"
            "END\n",
            encoding="utf-8",
        )

        try:
            code = main([
                "setup", "--system", str(structure),
                "--output", str(tmp_path / "run"), "--heterogens", "auto",
            ])
        finally:
            package_logger.removeHandler(handler)

        assert code == 1
        assert "PDB identifier" in captured.getvalue()

    def test_a_local_file_cannot_have_its_chemistry_looked_up(self, tmp_path) -> None:
        """There is no entry to retrieve the component from."""
        from fastmdxplora.setup.pipeline import _auto_ligands

        structure = tmp_path / "s.pdb"
        structure.write_text(
            # A tripeptide rather than a lone residue: one amino acid is
        # simultaneously N- and C-terminal, which AMBER has no template for.
        # These fixtures test pipeline mechanics, not force-field edge cases.
                "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
                "ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C\n"
        "ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00           C\n"
        "ATOM      4  O   ALA A   1       1.251   2.390   0.000  1.00  0.00           O\n"
        "ATOM      5  CB  ALA A   1       1.988  -0.773  -1.199  1.00  0.00           C\n"
        "ATOM      6  N   GLY A   2       3.332   1.549   0.000  1.00  0.00           N\n"
        "ATOM      7  CA  GLY A   2       3.972   2.849   0.000  1.00  0.00           C\n"
        "ATOM      8  C   GLY A   2       5.486   2.705   0.000  1.00  0.00           C\n"
        "ATOM      9  O   GLY A   2       6.008   1.593   0.000  1.00  0.00           O\n"
        "ATOM     10  N   ALA A   3       6.171   3.845   0.000  1.00  0.00           N\n"
        "ATOM     11  CA  ALA A   3       7.623   3.845   0.000  1.00  0.00           C\n"
        "ATOM     12  C   ALA A   3       8.174   5.265   0.000  1.00  0.00           C\n"
        "ATOM     13  O   ALA A   3       7.416   6.235   0.000  1.00  0.00           O\n"
        "ATOM     14  CB  ALA A   3       8.153   3.072  -1.199  1.00  0.00           C\n"
        "ATOM     15  OXT ALA A   3       9.400   5.400   0.000  1.00  0.00           O\n"
        "HETATM   10  C1  BNZ A 201       5.000   5.000   5.000  1.00  0.00           C\n"
            "END\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="PDB identifier"):
            _auto_ligands({"ph": 7.0, "forcefield": "amber-openff"},
                          structure, tmp_path, None)

    def test_a_force_field_without_ligand_support_stops(self, tmp_path) -> None:
        """The protein force field is a scientific choice, not ours to change."""
        from fastmdxplora.setup.pipeline import _auto_ligands

        structure = tmp_path / "s.pdb"
        structure.write_text(
            # A tripeptide rather than a lone residue: one amino acid is
        # simultaneously N- and C-terminal, which AMBER has no template for.
        # These fixtures test pipeline mechanics, not force-field edge cases.
                "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
                "ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C\n"
        "ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00           C\n"
        "ATOM      4  O   ALA A   1       1.251   2.390   0.000  1.00  0.00           O\n"
        "ATOM      5  CB  ALA A   1       1.988  -0.773  -1.199  1.00  0.00           C\n"
        "ATOM      6  N   GLY A   2       3.332   1.549   0.000  1.00  0.00           N\n"
        "ATOM      7  CA  GLY A   2       3.972   2.849   0.000  1.00  0.00           C\n"
        "ATOM      8  C   GLY A   2       5.486   2.705   0.000  1.00  0.00           C\n"
        "ATOM      9  O   GLY A   2       6.008   1.593   0.000  1.00  0.00           O\n"
        "ATOM     10  N   ALA A   3       6.171   3.845   0.000  1.00  0.00           N\n"
        "ATOM     11  CA  ALA A   3       7.623   3.845   0.000  1.00  0.00           C\n"
        "ATOM     12  C   ALA A   3       8.174   5.265   0.000  1.00  0.00           C\n"
        "ATOM     13  O   ALA A   3       7.416   6.235   0.000  1.00  0.00           O\n"
        "ATOM     14  CB  ALA A   3       8.153   3.072  -1.199  1.00  0.00           C\n"
        "ATOM     15  OXT ALA A   3       9.400   5.400   0.000  1.00  0.00           O\n"
        "HETATM   10  C1  BNZ A 201       5.000   5.000   5.000  1.00  0.00           C\n"
            "END\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="cannot parameterize small molecules"):
            _auto_ligands({"ph": 7.0, "forcefield": "charmm36"},
                          structure, tmp_path, "4W52")


class TestModifiedResiduesAreNotLigands:
    """A modified residue is deposited as HETATM but is still polymer.

    An oxidised cysteine in a protease is written with a HETATM record, so
    requiring an ATOM record to count as polymer meant the list of known
    residue names never applied to any of them. 1HVR refused on its CSO as
    though a covalent inhibitor were bound.
    """

    def test_a_modified_cysteine_is_polymer_not_a_ligand(self) -> None:
        lines = list(PROTEIN)
        lines += ["LINK         SG  CSO A  67                 C   ALA A   1"]
        lines += [_atom("HETATM", 20, "SG", " ", "CSO", "A", 67, 3, 0, 0,
                        element="S")]

        # Nothing to decide: preparation replaces it with cysteine.
        assert resolve(_structure(lines)) == []

    def test_a_ligand_alongside_one_is_still_found(self) -> None:
        lines = list(PROTEIN)
        lines += [_atom("HETATM", 20, "SG", " ", "CSO", "A", 67, 3, 0, 0,
                        element="S")]
        lines += [_atom("HETATM", 30, "C1", " ", "BNZ", "A", 200, 9, 9, 9)]

        actions = _actions(_structure(lines))
        assert "CSO" not in actions
        assert actions["BNZ"] is Action.SIMULATE

    def test_selenomethionine_too(self) -> None:
        lines = list(PROTEIN)
        lines += [_atom("HETATM", 20, "SE", " ", "MSE", "A", 5, 3, 0, 0,
                        element="SE")]
        assert resolve(_structure(lines)) == []


class TestIonsSharingOneSite:
    """Partially occupied metals on a symmetry axis are one site, not several.

    Insulin's zincs sit on a three-fold axis at partial occupancy: alternate
    positions for a single ion. Keeping both puts two metals within bonding
    distance, and OpenMM rejects the result ("the set of externally bonded
    atoms has 1 Zn atom too many"). It would be wrong even if it did not.
    """

    @staticmethod
    def _two_zincs(occupancy_a: float, occupancy_b: float):
        return [
            _atom("ATOM", 1, "NE2", " ", "HIS", "A", 1, 0, 0, 0, element="N"),
            _atom("HETATM", 50, "ZN", " ", "ZN", "B", 31, 2.1, 0, 0,
                  occupancy=occupancy_a, element="ZN"),
            _atom("HETATM", 51, "ZN", " ", "ZN", "B", 32, 2.6, 0, 0,
                  occupancy=occupancy_b, element="ZN"),
        ]

    def test_equal_occupancy_stops(self) -> None:
        """Nothing in the structure says which position is real."""
        with pytest.raises(AmbiguousStructureError, match="same site"):
            resolve(_structure(self._two_zincs(0.5, 0.5)))

    def test_the_dominant_position_wins_when_occupancy_separates_them(self) -> None:
        actions = _actions(_structure(self._two_zincs(0.7, 0.3)))
        assert actions["ZN"] is Action.SIMULATE

    def test_genuinely_separate_sites_are_both_kept(self) -> None:
        """Two metals far apart are two sites and both belong."""
        lines = [
            _atom("ATOM", 1, "NE2", " ", "HIS", "A", 1, 0, 0, 0, element="N"),
            _atom("ATOM", 2, "NE2", " ", "HIS", "A", 2, 30, 0, 0, element="N"),
            _atom("HETATM", 50, "ZN", " ", "ZN", "B", 31, 2.1, 0, 0, element="ZN"),
            _atom("HETATM", 51, "ZN", " ", "ZN", "B", 32, 32.1, 0, 0, element="ZN"),
        ]
        actions = _actions(_structure(lines))
        assert actions["ZN"] is Action.SIMULATE


class TestGlycansAreIdentifiedNotGuessed:
    """A LINK to an asparagine is the structure answering the question.

    A sugar has three readings, but they are not equally uncertain. Attached to
    Asn, Ser or Thr it is a glycosylation site and nothing else. Attached to
    nothing it could be a substrate or a cryoprotectant, and that question
    stands.
    """

    def test_a_sugar_bonded_to_asparagine_is_a_glycan(self) -> None:
        lines = list(PROTEIN)
        lines += [
            "LINK         ND2 ASN A   1                 C1  NAG A 400",
            _atom("HETATM", 300, "C1", " ", "NAG", "A", 400, 4, 0, 0),
        ]
        actions = _actions(_structure(lines))
        assert actions["NAG"] is Action.DISCARD

    def test_an_inner_sugar_of_a_glycan_goes_with_it(self) -> None:
        """Only the first sugar touches the protein; the rest hang off it."""
        lines = list(PROTEIN)
        lines += [
            "LINK         ND2 ASN A   1                 C1  NAG A 400",
            "LINK         O4  NAG A 400                 C1  BMA A 401",
            _atom("HETATM", 300, "C1", " ", "NAG", "A", 400, 4, 0, 0),
            _atom("HETATM", 301, "C1", " ", "BMA", "A", 401, 6, 0, 0),
        ]
        actions = _actions(_structure(lines))
        assert actions["NAG"] is Action.DISCARD
        assert actions["BMA"] is Action.DISCARD

    def test_a_free_oligosaccharide_is_still_a_question(self) -> None:
        """Lysozyme's NAG3 is a substrate: sugars bonded only to each other."""
        lines = list(PROTEIN)
        lines += [
            "LINK         O4  NAG A 400                 C1  NAG A 401",
            _atom("HETATM", 300, "C1", " ", "NAG", "A", 400, 4, 0, 0),
            _atom("HETATM", 301, "C1", " ", "NAG", "A", 401, 6, 0, 0),
        ]
        with pytest.raises(AmbiguousStructureError, match="free sugar"):
            resolve(_structure(lines))

    def test_an_unattached_sugar_is_still_a_question(self) -> None:
        lines = list(PROTEIN)
        lines += [_atom("HETATM", 300, "C1", " ", "GAL", "A", 400, 4, 0, 0)]
        with pytest.raises(AmbiguousStructureError, match="free sugar"):
            resolve(_structure(lines))


#: An asparagine whose ND2 is at (10, 0, 0).
ASPARAGINE = [
    _atom("ATOM", 4, "N", " ", "ASN", "A", 2, 10, 3, 0, element="N"),
    _atom("ATOM", 5, "CA", " ", "ASN", "A", 2, 10, 2, 0),
    _atom("ATOM", 6, "ND2", " ", "ASN", "A", 2, 10, 0, 0, element="N"),
]


class TestAGlycanWithNoLinkToItsAsparagine:
    """1HZH, an IgG1, LINKs every sugar of its two glycans to the next and
    neither glycan to Asn 314, whose ND2 is 2.45 and 2.64 A from the first
    NAG's C1. Each sugar was refused as "a free sugar, attached to nothing",
    so the antibody could not be prepared."""

    @pytest.mark.parametrize("distance", [1.45, 2.64])
    def test_a_sugar_bonded_to_an_asparagine_by_distance_is_a_glycan(self, distance) -> None:
        lines = list(PROTEIN) + ASPARAGINE
        lines += [
            "LINK         O4  NAG A 400                 C1  BMA A 401",
            _atom("HETATM", 300, "C1", " ", "NAG", "A", 400, 10 + distance, 0, 0),
            _atom("HETATM", 301, "C1", " ", "BMA", "A", 401, 16, 0, 0),
        ]
        decisions = {d.resname: d for d in resolve(_structure(lines))}
        assert decisions["NAG"].action is Action.DISCARD
        assert decisions["BMA"].action is Action.DISCARD
        assert "ASN A2 (bonded by distance" in decisions["NAG"].reason
        assert "no LINK record names it" in decisions["BMA"].reason

    def test_a_sugar_in_contact_with_an_asparagine_is_still_a_question(self) -> None:
        # 3.3 A is a contact, not a bond: a free sugar in a pocket lined by
        # an asparagine is a substrate as likely as not.
        lines = list(PROTEIN) + ASPARAGINE
        lines += [_atom("HETATM", 300, "C1", " ", "NAG", "A", 400, 13.3, 0, 0)]
        with pytest.raises(AmbiguousStructureError, match="free sugar"):
            resolve(_structure(lines))

    def test_an_asparagine_a_link_bonds_to_a_sugar_takes_no_second_by_distance(self) -> None:
        # An amide nitrogen carries one sugar. NAG B500 at 2.5 A from an ND2
        # already LINKed to NAG A400 was read as a second glycan on ASN A2,
        # "bonded by distance; no LINK record names it".
        lines = list(PROTEIN) + ASPARAGINE
        lines += [
            "LINK         ND2 ASN A   2                 C1  NAG A 400",
            _atom("HETATM", 300, "C1", " ", "NAG", "A", 400, 11.45, 0, 0),
            _atom("HETATM", 310, "C1", " ", "NAG", "B", 500, 10, 2.5, 0),
        ]
        with pytest.raises(AmbiguousStructureError, match="NAG B500 is bonded to no glycosylated"):
            resolve(_structure(lines))

    @pytest.mark.parametrize("near, far", [("A400", "B500"), ("B500", "A400")])
    def test_of_two_sugars_near_one_asparagine_only_the_nearer_is_its_glycan(self, near, far) -> None:
        # No LINK: NAG at 1.45 A and NAG at 2.50 A from the ND2 of ASN A2 were
        # both read as glycans on it. The nearer is; the other is a free sugar.
        place = {near: (11.45, 0, 0), far: (10, 2.5, 0)}
        lines = list(PROTEIN) + ASPARAGINE
        lines += [
            _atom("HETATM", 300, "C1", " ", "NAG", "A", 400, *place["A400"]),
            _atom("HETATM", 310, "C1", " ", "NAG", "B", 500, *place["B500"]),
        ]
        with pytest.raises(AmbiguousStructureError) as raised:
            resolve(_structure(lines))
        said = str(raised.value)
        assert (f"NAG {near} is part of a glycan on ASN A2 (bonded by distance, 1.45 A; "
                "no LINK record names it)") in said
        assert f"NAG {far} is bonded to no glycosylated residue, so a free sugar" in said
        assert "2.50 A" not in said

    def test_only_the_anomeric_carbon_bonds_a_sugar_to_the_asparagine(self) -> None:
        lines = list(PROTEIN) + ASPARAGINE
        lines += [
            _atom("HETATM", 300, "O6", " ", "NAG", "A", 400, 11.4, 0, 0, element="O"),
            _atom("HETATM", 301, "C1", " ", "NAG", "A", 400, 15, 0, 0),
        ]
        with pytest.raises(AmbiguousStructureError, match="free sugar"):
            resolve(_structure(lines))


def test_a_link_trimmed_before_its_symmetry_columns_is_still_read() -> None:
    """An absent insertion code is a blank, not a different value.

    Depositions from RCSB pad LINK records past column 57, so the field is
    always present; a hand-edited or trimmed file has nothing there, and
    reading that as an empty string made the key miss the residue it named.
    The bond was then lost silently, which for a glycan means it stops looking
    attached to the protein at all.
    """
    lines = list(PROTEIN)
    lines += [
        "LINK         ND2 ASN A   1                 C1  NAG A 400",
        _atom("HETATM", 300, "C1", " ", "NAG", "A", 400, 4, 0, 0),
    ]
    actions = _actions(_structure(lines))
    assert actions["NAG"] is Action.DISCARD
