
import pytest

def test_removed_heterogens_are_reported() -> None:
    """A stripped ligand must not pass silently.

    Removing crystallization additives is usually correct, but a bound ligand
    is indistinguishable from a buffer molecule at this stage. Running 4W52
    without --setup-keep-heterogens discards the benzene and produces what
    looks like a holo run but is apo.
    """
    from fastmdxplora.setup.pdbfix import _heterogen_residue_counts

    class _Residue:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Topology:
        def __init__(self, names: list[str]) -> None:
            self._residues = [_Residue(n) for n in names]

        def residues(self):
            return iter(self._residues)

    # The real composition of PDB 4W52.
    counts = _heterogen_residue_counts(
        _Topology(["ALA", "GLY", "BNZ", "EPE", "HOH", "HOH", "CL", "MSE"])
    )
    assert counts == {"BNZ": 1, "EPE": 1}

    # Copies are counted so the message can say how many were lost.
    assert _heterogen_residue_counts(_Topology(["BNZ", "BNZ"])) == {"BNZ": 2}

    # Nothing to report for a plain protein or nucleic acid.
    assert _heterogen_residue_counts(_Topology(["ALA", "GLY", "HOH"])) == {}
    assert _heterogen_residue_counts(_Topology(["DA", "DC", "DG", "DT"])) == {}


class TestHeterogenRemovalIsAnnounced:
    """Removing a heterogen must never be silent.

    A bound ligand and a buffer molecule look identical in a PDB file at the
    preparation stage. Dropping both without a word produced runs that read as
    holo simulations but were apo.
    """

    @staticmethod
    def _topology(names):
        class Residue:
            def __init__(self, name):
                self.name = name

        class Topology:
            def __init__(self, residue_names):
                self._residues = [Residue(name) for name in residue_names]

            def residues(self):
                return iter(self._residues)

        return Topology(names)

    def test_ligand_and_buffer_are_both_reported(self) -> None:
        from fastmdxplora.setup.pdbfix import _heterogen_residue_counts

        # 4W52: benzene ligand, HEPES buffer, crystallographic waters.
        counts = _heterogen_residue_counts(
            self._topology(["ALA", "LEU"] * 20 + ["BNZ", "EPE"] + ["HOH"] * 80)
        )
        assert counts == {"BNZ": 1, "EPE": 1}

    def test_apo_structure_reports_nothing(self) -> None:
        """Water and common ions are not worth warning about."""
        from fastmdxplora.setup.pdbfix import _heterogen_residue_counts

        counts = _heterogen_residue_counts(
            self._topology(["ALA"] * 30 + ["HOH"] * 100 + ["NA", "CL", "MG"])
        )
        assert counts == {}

    def test_multiple_copies_are_counted(self) -> None:
        from fastmdxplora.setup.pdbfix import _heterogen_residue_counts

        counts = _heterogen_residue_counts(self._topology(["ALA"] * 10 + ["BNZ"] * 3))
        assert counts == {"BNZ": 3}

    def test_modified_residues_are_not_flagged(self) -> None:
        """MSE and protonation variants are protein, not heterogens."""
        from fastmdxplora.setup.pdbfix import _heterogen_residue_counts

        counts = _heterogen_residue_counts(
            self._topology(["ALA", "MSE", "HID", "HIE", "CYX", "DA", "DT"])
        )
        assert counts == {}


class TestMultipleLigands:
    """Several ligands, cofactors, or copies can be parameterized together.

    They must stay distinguishable: two ligands sharing a residue name cannot
    be told apart in the topology, and every analysis that selects by residue
    would silently conflate them.
    """

    def test_one_ligand_uses_the_name_given(self) -> None:
        from fastmdxplora.setup.prepare import _resolve_ligand_names

        assert _resolve_ligand_names(["bnz.sdf"], "BNZ") == ["BNZ"]
        assert _resolve_ligand_names(["x.sdf"], None) == ["LIG"]

    def test_several_ligands_take_their_names_from_the_files(self) -> None:
        from fastmdxplora.setup.prepare import _resolve_ligand_names

        names = _resolve_ligand_names(["BNZ.sdf", "ATP.sdf", "GOL.sdf"], None)
        assert names == ["BNZ", "ATP", "GOL"]

    def test_explicit_names_are_accepted_one_per_ligand(self) -> None:
        from fastmdxplora.setup.prepare import _resolve_ligand_names

        assert _resolve_ligand_names(["a", "b"], ["L01", "L02"]) == ["L01", "L02"]

    def test_one_name_for_several_ligands_is_refused(self) -> None:
        from fastmdxplora.setup.prepare import _resolve_ligand_names

        with pytest.raises(ValueError, match="single ligand name"):
            _resolve_ligand_names(["a", "b"], "LIG")

    def test_two_different_molecules_may_not_share_a_name(self) -> None:
        from fastmdxplora.setup.prepare import _resolve_ligand_names

        with pytest.raises(ValueError, match="same residue name"):
            _resolve_ligand_names(["a.sdf", "b.sdf"], ["X", "X"])

    def test_but_copies_of_one_component_may(self) -> None:
        """Two 7VF molecules in a structure are both called 7VF and told
        apart by chain and residue number, as they are in the entry.
        Requiring a distinct name per copy is what drove the generated names
        that lost the component code: 7VF became `700` and `701`, and 6B73's
        three distinct ligands became `C00`, `C01` and `O02`."""
        from fastmdxplora.setup.prepare import _resolve_ligand_names

        names = _resolve_ligand_names(
            ["7VF_A901.sdf", "7VF_B901.sdf"], ["7VF", "7VF"])
        assert names == ["7VF", "7VF"]

    def test_the_component_code_survives_several_ligands(self) -> None:
        from fastmdxplora.setup.prepare import _resolve_ligand_names

        names = _resolve_ligand_names(
            ["CLR_A2002.sdf", "CVV_A2001.sdf", "OLA_A2003.sdf"], None)
        assert names == ["CLR", "CVV", "OLA"]

    def test_a_mismatched_number_of_names_is_refused(self) -> None:
        from fastmdxplora.setup.prepare import _resolve_ligand_names

        with pytest.raises(ValueError, match="ligand names given"):
            _resolve_ligand_names(["a", "b"], ["X"])

    def test_charges_are_broadcast_or_given_per_ligand(self) -> None:
        from fastmdxplora.setup.prepare import _resolve_ligand_charges

        assert _resolve_ligand_charges(["a", "b"], None) == [None, None]
        assert _resolve_ligand_charges(["a", "b"], [0, -2]) == [0, -2]

        with pytest.raises(ValueError, match="ligand charges given"):
            _resolve_ligand_charges(["a", "b"], [0])


class TestRemovalReporting:
    """Removing a component and re-adding it are different events."""

    @staticmethod
    def _fix(tmp_path, **kwargs):
        """Run PDBFixer on a tiny structure, capturing what it logged."""
        import io
        import logging

        pytest.importorskip("pdbfixer")
        from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

        structure = tmp_path / "in.pdb"
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
        "HETATM   10  C1  BNZ A 200       9.000   9.000   9.000  1.00  0.00           C\n"
            "HETATM   20  C1  EPE A 300      20.000  20.000  20.000  1.00  0.00           C\n"
            "END\n",
            encoding="utf-8",
        )
        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        handler.setLevel(logging.INFO)
        package_logger = logging.getLogger("fastmdx")
        package_logger.addHandler(handler)
        # A handler alone is not enough: the logger's own level filters
        # records before any handler sees them, so an INFO message about a
        # reinstated component would never arrive.
        previous = package_logger.level
        package_logger.setLevel(logging.INFO)
        try:
            fix_pdb_with_pdbfixer(str(structure), str(tmp_path / "out.pdb"), **kwargs)
        finally:
            package_logger.removeHandler(handler)
            package_logger.setLevel(previous)
        return captured.getvalue()

    def test_dropping_everything_warns_about_everything(self, tmp_path) -> None:
        text = self._fix(tmp_path)
        assert "Removed heterogens" in text
        assert "BNZ" in text
        assert "EPE" in text

    def test_a_judged_component_is_not_second_guessed(self, tmp_path) -> None:
        """The classifier reported EPE as a buffer, with a reason.

        Warning that it "was removed, and might be the ligand you meant to
        simulate" contradicts that, and invites the user to overturn a correct
        decision.
        """
        text = self._fix(tmp_path, reinstated=("BNZ",), explained=("BNZ", "EPE"))
        assert "Removed heterogens" not in text
        assert "re-added with small-molecule parameters" in text

    def test_an_unjudged_component_still_warns(self, tmp_path) -> None:
        """The safety net stays: silence is only earned by having reasoned."""
        text = self._fix(tmp_path, reinstated=("BNZ",), explained=("BNZ",))
        assert "Removed heterogens" in text
        assert "EPE" in text

    def test_a_reinstated_component_is_not_reported_as_lost(self, tmp_path) -> None:
        """Under the auto policy the ligand comes back with parameters.

        Warning that it was removed describes a loss that did not happen, and
        would send a user looking for a ligand that is in the system.
        """
        text = self._fix(tmp_path, reinstated=("BNZ",))

        assert "re-added with small-molecule parameters" in text
        # The buffer really was discarded, so it still warrants the warning.
        assert "Removed heterogens" in text
        assert "EPE" in text
        # ...but benzene must not appear in the removal warning.
        warning_line = next(
            line for line in text.splitlines() if "Removed heterogens" in line
        )
        assert "BNZ" not in warning_line


class TestMultipleLigandNamesSurvive:
    """A list of names must reach preparation as a list.

    The auto path assigns one name per ligand, then the pipeline stringified
    the list, so three ligands arrived as a single name of
    "['Z00', 'Z01', 'Z02']". Multi-ligand support was defeated one line after
    it was added, and every metalloprotein with several ions failed.
    """

    def test_a_list_is_not_stringified(self, tmp_path) -> None:
        # Two ligands with a name each, through the setup phase to a
        # stand-in for preparation: the names arrive as the list they were.
        from tests._the_phase import a_real_setup

        for name in ("a", "b"):
            (tmp_path / f"{name}.sdf").write_text(_BENZENE_SDF, encoding="utf-8")
        handed = {}

        def stand_in(*args, **kwargs):
            handed.update(kwargs)
            return {"n_atoms_solvated": 1}

        a_real_setup(tmp_path, prepare_system=stand_in,
                     ligand=[str(tmp_path / "a.sdf"), str(tmp_path / "b.sdf")],
                     ligand_name=["Z00", "Z01"])
        assert handed["ligand_name"] == ["Z00", "Z01"]

    def test_prepare_accepts_the_list_the_pipeline_builds(self) -> None:
        """The two halves must agree on the shape of the value."""
        from fastmdxplora.setup.prepare import _resolve_ligand_names

        names = _resolve_ligand_names(["a.sdf", "b.sdf", "c.sdf"],
                                      ["Z00", "Z01", "Z02"])
        assert names == ["Z00", "Z01", "Z02"]

    def test_the_stringified_form_is_still_rejected_clearly(self) -> None:
        """If it ever regresses, the message should say what happened."""
        from fastmdxplora.setup.prepare import _resolve_ligand_names

        with pytest.raises(ValueError, match="single ligand name"):
            _resolve_ligand_names(["a", "b", "c"], "['Z00', 'Z01', 'Z02']")


class TestUntemplatedGapPruning:
    """Rebuilds are cancelled for residues with no chemical template.

    ``removeHeterogens`` strips components from the topology but not from the
    deposited sequence, so the gap they leave is read as unresolved polymer and
    scheduled for reconstruction. PDBFixer cannot build a component it just
    discarded, and fails with an ``AttributeError`` naming nothing. 6LU7 reaches
    this: its inhibitor chain interleaves standard residues with 010, 02J, PJE.
    """

    @staticmethod
    def _fixer_with_missing(missing, templates):
        class _Fixer:
            def __init__(self):
                self.missingResidues = dict(missing)

            def _getTemplate(self, name):
                return templates.get(name)

        return _Fixer()

    def test_untemplated_names_are_dropped(self):
        from fastmdxplora.setup.pdbfix import _drop_untemplated_gaps

        fixer = self._fixer_with_missing({(0, 1): ["PJE"]}, {"ALA": object()})
        _drop_untemplated_gaps(fixer)
        assert fixer.missingResidues == {}

    def test_buildable_names_survive_alongside_dropped_ones(self):
        from fastmdxplora.setup.pdbfix import _drop_untemplated_gaps

        fixer = self._fixer_with_missing(
            {(0, 3): ["PJE", "ALA"], (0, 7): ["GLY"]},
            {"ALA": object(), "GLY": object()},
        )
        _drop_untemplated_gaps(fixer)
        assert fixer.missingResidues == {(0, 3): ["ALA"], (0, 7): ["GLY"]}

    def test_insertion_indices_are_left_untouched(self):
        """The keys index the topology gaps were counted against.

        Recomputing them against a topology the heterogens have been removed
        from shifts every later insertion point down by one, which rebuilds an
        unresolved loop at the chain terminus instead of in its gap.
        """
        from fastmdxplora.setup.pdbfix import _drop_untemplated_gaps

        fixer = self._fixer_with_missing({(0, 12): ["GLY"]}, {"GLY": object()})
        _drop_untemplated_gaps(fixer)
        assert fixer.missingResidues == {(0, 12): ["GLY"]}

    def test_no_missing_residues_is_a_no_op(self):
        from fastmdxplora.setup.pdbfix import _drop_untemplated_gaps

        fixer = self._fixer_with_missing({}, {})
        _drop_untemplated_gaps(fixer)
        assert fixer.missingResidues == {}


class _FakeResidue:
    def __init__(self, name, rid, n_atoms):
        self.name, self.id = name, rid
        self._atoms = [_FakeAtom(self) for _ in range(n_atoms)]

    def atoms(self):
        return iter(self._atoms)


class _FakeAtom:
    def __init__(self, residue):
        self.residue = residue


class _FakeTopology:
    def __init__(self, bonds):
        self._bonds = list(bonds)


def _bond(res_a, res_b):
    return (next(res_a.atoms()), next(res_b.atoms()))


class TestIonCoordinationBonds:
    """Coordination written as a covalent bond is not honoured.

    OpenMM's monatomic ion templates declare no external bonds, so a CONECT
    record tying a metal to the residue holding it makes the ion unmatchable.
    1ZNI reaches this: two zincs and three chlorides, each named in a CONECT
    record, all five reported as having no template.
    """

    def test_ion_bonds_are_dropped(self):
        from fastmdxplora.setup.prepare import _drop_ion_coordination_bonds

        zn = _FakeResidue("ZN", "31", 1)
        cl = _FakeResidue("CL", "33", 1)
        topology = _FakeTopology([_bond(zn, cl)])
        dropped = _drop_ion_coordination_bonds(topology)
        assert dropped == ["ZN31-CL33"]
        assert topology._bonds == []

    def test_disulfides_survive(self):
        """Insulin carries three per monomer; losing them unfolds the protein."""
        from fastmdxplora.setup.prepare import _drop_ion_coordination_bonds

        cys_a = _FakeResidue("CYS", "6", 11)
        cys_b = _FakeResidue("CYS", "11", 11)
        topology = _FakeTopology([_bond(cys_a, cys_b)])
        assert _drop_ion_coordination_bonds(topology) == []
        assert len(topology._bonds) == 1

    def test_alpha_carbon_is_not_a_calcium_ion(self):
        """``CA`` names both; only the one-atom residue is the metal."""
        from fastmdxplora.setup.prepare import _is_monatomic_ion

        calcium = _FakeResidue("CA", "401", 1)
        assert _is_monatomic_ion(next(calcium.atoms())) is True

        # A residue named CA with many atoms is not an ion; and an alpha carbon
        # lives inside an amino acid residue, which is never one atom.
        alanine = _FakeResidue("ALA", "12", 10)
        assert _is_monatomic_ion(next(alanine.atoms())) is False

    def test_only_the_bonded_ions_are_touched(self):
        """A free ion has no bond to drop and is left exactly as it was."""
        from fastmdxplora.setup.prepare import _drop_ion_coordination_bonds

        zn_bound = _FakeResidue("ZN", "32", 1)
        cl = _FakeResidue("CL", "34", 1)
        cys_a, cys_b = _FakeResidue("CYS", "7", 11), _FakeResidue("CYS", "7", 11)
        topology = _FakeTopology([_bond(zn_bound, cl), _bond(cys_a, cys_b)])
        assert _drop_ion_coordination_bonds(topology) == ["ZN32-CL34"]
        assert len(topology._bonds) == 1


class TestSetupArtifactsAreNotNested:
    """``output_dir`` is the setup directory; appending "setup" buries things.

    This has been written three times: once for the ligand files, once for the
    structure built for the pKa calculation, and it was only caught the second
    time because a refusal message happened to quote the path. Pinning each
    function as it turns up does not stop the next one, so the whole module is
    checked instead.

    The name was what invited it. ``output_dir`` reads like the project's output
    directory, and for every other phase that is what it would be; here the
    caller has already appended the phase directory. The helpers now say
    ``setup_dir``, which makes the mistake hard to write rather than merely
    caught, and the phase entry point keeps ``output_dir`` because that name is
    the protocol every phase shares.
    """

    def test_nothing_appends_a_setup_directory_to_the_setup_directory(
            self, a_setup_run) -> None:
        # output_dir is already the setup directory, so a path that appends
        # "setup" to it writes into setup/setup/. After a real setup,
        # nothing below the setup directory is called setup.
        setup = a_setup_run.root / "setup"
        nested = [p for p in setup.rglob("*") if "setup" in p.relative_to(setup).parts]
        assert not nested, nested
        assert (setup / "system.xml").is_file()

    def test_the_pka_structure_sits_beside_the_other_artifacts(self, tmp_path) -> None:
        # Written into the setup directory it is given, beside the rest.
        from pathlib import Path

        pytest.importorskip("pdbfixer")
        from fastmdxplora.setup.pipeline import _repaired_complex
        from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

        structure = tmp_path / "peptide.pdb"
        structure.write_text(TRI_ALANINE, encoding="utf-8")
        setup = tmp_path / "study" / "setup"
        written = Path(_repaired_complex(structure, setup, 7.0))
        assert written == setup / "complex_for_pka.pdb"
        assert written.is_file()

class TestPhysiologicalPhIsTheDefault:
    """Blood is 7.4, and a protein studied without a stated reason is there."""

    def test_the_phase_and_the_schema_agree(self) -> None:
        from fastmdxplora.config.schema import SETUP
        from fastmdxplora.setup.pipeline import DEFAULTS

        assert DEFAULTS["ph"] == 7.4
        schema = {f.name: f.default for f in SETUP.fields}
        assert schema["ph"] == DEFAULTS["ph"]


class TestUnparameterizedResiduesAreNamed:
    """OpenMM reports the residue by topology index, which counts solvent.

    That number points at nothing a user can find in their input, so the
    residue is resolved back to a name, chain and composition before the error
    reaches them.
    """

    class _Element:
        def __init__(self, symbol):
            self.symbol = symbol

    class _Atom:
        def __init__(self, element):
            self.element = element

    class _Chain:
        def __init__(self, cid):
            self.id = cid

    class _Residue:
        def __init__(self, name, chain, rid, elements):
            self.name, self.chain, self.id = name, chain, rid
            self._atoms = [
                TestUnparameterizedResiduesAreNamed._Atom(
                    TestUnparameterizedResiduesAreNamed._Element(e))
                for e in elements
            ]

        def atoms(self):
            return iter(self._atoms)

    class _ForceField:
        def __init__(self, unmatched):
            self._unmatched = unmatched

        def getUnmatchedResidues(self, topology):
            return self._unmatched

    def _residue(self, name="HEM", chain="A", rid="401", elements=("Fe", "N", "C")):
        return self._Residue(name, self._Chain(chain), rid, elements)

    def test_an_unrelated_error_passes_through_untouched(self):
        from fastmdxplora.setup.prepare import _explain_unparameterized

        original = ValueError("Particle coordinate is NaN")
        assert _explain_unparameterized(
            original, self._ForceField([]), None) is original

    def test_the_residue_is_named_with_its_chain_and_composition(self):
        from fastmdxplora.setup.prepare import _explain_unparameterized

        explained = _explain_unparameterized(
            ValueError("No template found for residue 412 (HEM)."),
            self._ForceField([self._residue()]),
            None,
        )
        text = str(explained)
        assert "HEM (chain A, residue 401, 3 atom(s): C, Fe, N)" in text
        # The original wording is kept, since it is what a search will find.
        assert "No template found for residue 412" in text

    def test_several_unmatched_residues_are_all_named(self):
        from fastmdxplora.setup.prepare import _explain_unparameterized

        explained = _explain_unparameterized(
            ValueError("No template found for residue 5 (ZN)."),
            self._ForceField([
                self._residue("ZN", "B", "31", ("Zn",)),
                self._residue("CL", "B", "33", ("Cl",)),
            ]),
            None,
        )
        assert "ZN (chain B, residue 31, 1 atom(s): Zn)" in str(explained)
        assert "CL (chain B, residue 33, 1 atom(s): Cl)" in str(explained)

    def test_nothing_to_name_leaves_the_error_as_it_was(self):
        """The template failure is real even when the probe finds no culprit."""
        from fastmdxplora.setup.prepare import _explain_unparameterized

        original = ValueError("No template found for residue 9 (UNK).")
        assert _explain_unparameterized(
            original, self._ForceField([]), None) is original


class TestAFailureDuringSolvationExplainsItself:
    """Found by running a real complex: 181L failed at solvation with OpenMM's
    raw "No template found for residue 166 (HED)".

    An explanation for that failure existed and was wired to createSystem
    only. Solvation builds templates too and reaches them first, so the
    message a person saw named a residue and not what to do about it.
    """

    def test_solvation_is_wrapped_like_system_creation(self, a_setup_run, tmp_path,
                                                       monkeypatch) -> None:
        """Solvation builds templates too, and reaches them before
        createSystem does. A template failure in either says which component
        the force field cannot parameterize and what to do, rather than
        OpenMM's raw message. Made to fail in each, on the prepared peptide,
        with its first residue the one the force field cannot match."""
        app = pytest.importorskip("openmm").app
        from fastmdxplora.setup import prepare

        raw = ValueError("No template found for residue 1 (ALA).  The set of atoms "
                         "matches ALA, but the bonds are different.")

        def fail(*args, **kwargs):
            raise raw

        monkeypatch.setattr(app.ForceField, "getUnmatchedResidues",
                            lambda self, topology: [next(topology.residues())])
        for where, target in (("solvation", (prepare, "_solvate_with_room_for_the_cutoff")),
                              ("system creation", (app.ForceField, "createSystem"))):
            with monkeypatch.context() as each:
                each.setattr(*target, fail)
                with pytest.raises(ValueError) as raised:
                    prepare.prepare_system(a_setup_run.root / "setup" / "prepared.pdb",
                                           tmp_path / where.replace(" ", "_"),
                                           solvent_padding_nm=1.2, nonbonded_cutoff_nm=0.9)
            said = str(raised.value)
            assert said.startswith("The force field has no parameters for ALA (chain"), (where, said)
            assert "--setup-ligand" in said

    def test_the_explanation_says_what_to_do(self) -> None:
        from fastmdxplora.setup.prepare import _explain_unparameterized

        class _Residue:
            name = "HED"
            id = "166"

            class chain:
                id = "A"

            @staticmethod
            def atoms():
                class _Atom:
                    class element:
                        symbol = "S"
                return [_Atom(), _Atom()]

        class _FF:
            @staticmethod
            def getUnmatchedResidues(topology):
                return [_Residue()]

        explained = _explain_unparameterized(
            ValueError("No template found for residue 166 (HED)."), _FF(), None)
        message = str(explained)
        assert "HED" in message
        assert "--setup-ligand" in message, "it should name a way forward"


class TestTheReducingAgentIsAnAdditive:
    """HED is 2-hydroxyethyl disulfide -- what mercaptoethanol becomes when it
    oxidises. BME was already listed as a crystallisation additive and HED was
    not, so a structure reduced with mercaptoethanol carried the leftover into
    the simulation and failed for want of parameters.
    """

    def test_it_is_listed_beside_the_reducing_agent(self) -> None:
        from fastmdxplora.setup.heterogens import CRYSTALLIZATION_ADDITIVES

        assert "BME" in CRYSTALLIZATION_ADDITIVES
        assert "HED" in CRYSTALLIZATION_ADDITIVES

    def test_the_ligand_is_not_treated_as_one(self) -> None:
        """Benzene in 181L is the thing being studied."""
        from fastmdxplora.setup.heterogens import CRYSTALLIZATION_ADDITIVES

        assert "BNZ" not in CRYSTALLIZATION_ADDITIVES


_BENZENE_SDF = """benzene
  written by hand

  6  6  0  0  0  0  0  0  0  0999 V2000
    1.3900    0.0000    0.0000 C   0  0
    0.6950    1.2038    0.0000 C   0  0
   -0.6950    1.2038    0.0000 C   0  0
   -1.3900    0.0000    0.0000 C   0  0
   -0.6950   -1.2038    0.0000 C   0  0
    0.6950   -1.2038    0.0000 C   0  0
  1  2  2  0
  2  3  1  0
  3  4  2  0
  4  5  1  0
  5  6  2  0
  6  1  1  0
M  END
$$$$
"""


@pytest.fixture(scope="module")
def a_setup_run(tmp_path_factory):
    """One real setup of a three-residue peptide."""
    from tests._the_phase import a_real_setup

    return a_real_setup(tmp_path_factory.mktemp("layered_setup"))
