"""Tests for settling a ligand's protonation from its pKa in the complex.

The decision logic is tested directly with pKa values, so these do not depend
on PROPKA succeeding on any particular structure. One test does exercise
PROPKA itself, on a protein with no ligand, to confirm the integration.
"""

from __future__ import annotations

import pytest

from fastmdxplora.setup.protonation import (
    GroupPka,
    ProtonationError,
    decide,
    ligand_pka,
)


def _group(pka: float, *, model: float = 4.0, kind: str = "COO") -> GroupPka:
    return GroupPka("LIG", "A", 201, kind, pka, model)


class TestDeterminedStates:
    def test_a_ligand_with_no_ionizable_group_proceeds(self) -> None:
        state = decide("BNZ", [], 7.4, expected_ionizable=False)
        assert state.protonated is False
        assert "unambiguous" in state.reason

    def test_an_acid_well_below_the_ph_is_deprotonated(self) -> None:
        state = decide("LIG", [_group(3.4)], 7.4, expected_ionizable=True)
        assert state.protonated is False
        assert "3.40" in state.reason

    def test_a_pocket_shifted_acid_above_the_ph_is_protonated(self) -> None:
        """The case a rule table gets wrong.

        A carboxylic acid is deprotonated at pH 7.4 in water. Buried against a
        carboxylate, or in a cavity with nothing to stabilise the charge, it
        need not be, and a shift of several units is ordinary. Deciding from
        the complex catches this; deciding from the molecule cannot.
        """
        state = decide("LIG", [_group(8.9)], 7.4, expected_ionizable=True)
        assert state.protonated is True

    def test_the_environment_shift_is_reported(self) -> None:
        group = _group(8.9, model=4.0)
        assert group.shift == pytest.approx(4.9)
        assert "shifted +4.90" in str(group)


class TestRefusals:
    def test_a_poised_group_stops(self) -> None:
        """Within a unit of the pH both states are appreciably populated."""
        with pytest.raises(ProtonationError, match="minor state"):
            decide("LIG", [_group(7.0)], 7.4, expected_ionizable=True)

    def test_one_poised_group_among_several_stops(self) -> None:
        with pytest.raises(ProtonationError, match="poised|both charge states"):
            decide("LIG", [_group(3.2), _group(6.9)], 7.4, expected_ionizable=True)

    def test_silence_from_propka_about_an_ionizable_ligand_stops(self) -> None:
        """Never fall back to a method that answers a different question.

        If the chemistry says the ligand is ionizable but PROPKA reports no
        pKa, the two disagree, and substituting a rule table would hide that.
        """
        with pytest.raises(ProtonationError, match="undetermined"):
            decide("LIG", [], 7.4, expected_ionizable=True)

    def test_the_message_says_what_to_do(self) -> None:
        with pytest.raises(ProtonationError) as excinfo:
            decide("LIG", [_group(7.2)], 7.4, expected_ionizable=True)
        message = str(excinfo.value)
        assert "SDF or MOL2" in message or "supply the ligand" in message.lower()
        assert "pH" in message


class TestPropkaIntegration:
    """One test that actually runs PROPKA, to catch API drift."""

    def test_protein_groups_are_read_from_a_real_calculation(self, tmp_path) -> None:
        pytest.importorskip("propka")

        structure = tmp_path / "complex.pdb"
        structure.write_text(
            "ATOM      1  N   ASP A   1      10.000  10.000  10.000  1.00 20.00           N\n"
            "ATOM      2  CA  ASP A   1      11.400  10.000  10.000  1.00 20.00           C\n"
            "ATOM      3  C   ASP A   1      12.000  11.400  10.000  1.00 20.00           C\n"
            "ATOM      4  O   ASP A   1      11.300  12.400  10.000  1.00 20.00           O\n"
            "ATOM      5  CB  ASP A   1      11.900   9.200  11.200  1.00 20.00           C\n"
            "ATOM      6  CG  ASP A   1      13.400   9.100  11.300  1.00 20.00           C\n"
            "ATOM      7  OD1 ASP A   1      14.000   8.400  10.400  1.00 20.00           O\n"
            "ATOM      8  OD2 ASP A   1      13.900   9.700  12.300  1.00 20.00           O\n"
            "TER\nEND\n",
            encoding="utf-8",
        )

        # Asking about a component that is not present must return nothing
        # rather than raising: absence is a valid answer.
        assert ligand_pka(structure, "BNZ", chain="A", resseq=201) == []


@pytest.mark.network
class TestRealComplex:
    """Evidence that deciding from the complex is operational, not aspirational.

    Deselected by default; run with ``pytest -m network``. It exists so that a
    regression in PROPKA's ligand handling is detectable, because the claim
    that FastMDXplora determines ligand protonation from the bound state rests
    entirely on this working.
    """

    def test_biotin_in_streptavidin_shifts_from_its_solution_pka(self, tmp_path) -> None:
        import urllib.request

        pytest.importorskip("propka")

        structure = tmp_path / "1STP.pdb"
        urllib.request.urlretrieve(  # noqa: S310 - RCSB
            "https://files.rcsb.org/download/1STP.pdb", structure
        )

        groups = ligand_pka(structure, "BTN", chain="A", resseq=300)
        assert groups, "PROPKA returned no ionizable group for biotin"

        # Biotin's carboxylate: about 4.5 in water, lower in the pocket.
        carboxylate = [g for g in groups if g.group_type == "OCO"]
        assert carboxylate, "the carboxylate should be detected"
        assert carboxylate[0].model_pka == pytest.approx(4.5, abs=0.3)
        assert carboxylate[0].shift < 0, "streptavidin should lower it"

        # Every group sits well below physiological pH, so the state is settled.
        state = decide("BTN", groups, 7.4, expected_ionizable=True)
        assert state.protonated is False


class TestWhenPropkaRuns:
    """The calculation should run where it can change the answer, and not else."""

    def test_a_ligand_with_no_ionizable_group_skips_the_calculation(self, tmp_path) -> None:
        """Benzene's protonation does not depend on its surroundings.

        Running PROPKA anyway costs a second and produces complaints about
        unmodelled side chains that could not affect the result.
        """
        from fastmdxplora.setup import protonation

        called = []
        original = protonation.ligand_pka
        protonation.ligand_pka = lambda *a, **k: called.append(a) or []
        try:
            state = protonation.settle(
                tmp_path / "nonexistent.pdb", "BNZ",
                chain="A", resseq=200, ph=7.4, expected_ionizable=False,
            )
        finally:
            protonation.ligand_pka = original

        assert called == [], "PROPKA should not have been consulted"
        assert state.protonated is False
        assert "unambiguous" in state.reason

    def test_a_titratable_ligand_does_consult_propka(self, tmp_path) -> None:
        from fastmdxplora.setup import protonation

        called = []
        original = protonation.ligand_pka
        protonation.ligand_pka = lambda *a, **k: called.append(a) or [
            GroupPka("AIN", "A", 1, "COO", 3.2, 4.0)
        ]
        try:
            state = protonation.settle(
                tmp_path / "x.pdb", "AIN",
                chain="A", resseq=1, ph=7.4, expected_ionizable=True,
            )
        finally:
            protonation.ligand_pka = original

        assert len(called) == 1
        assert state.protonated is False


class TestStructuralComplaintsAreCaptured:
    """PROPKA's own warnings belong in the log, not in the phase output."""

    def test_incomplete_residues_do_not_print_raw(self, tmp_path, capsys) -> None:
        pytest.importorskip("propka")

        structure = tmp_path / "gappy.pdb"
        # LYS missing its side chain past CB: PROPKA counts atoms per residue
        # and complains, which is legitimate but should not spill into output.
        structure.write_text(
            "ATOM      1  N   LYS A   1       0.000   0.000   0.000  1.00 20.00           N\n"
            "ATOM      2  CA  LYS A   1       1.400   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  C   LYS A   1       2.000   1.400   0.000  1.00 20.00           C\n"
            "ATOM      4  O   LYS A   1       1.300   2.400   0.000  1.00 20.00           O\n"
            "ATOM      5  CB  LYS A   1       1.900  -0.800   1.200  1.00 20.00           C\n"
            "TER\nEND\n",
            encoding="utf-8",
        )
        ligand_pka(structure, "BNZ", chain="A", resseq=200)

        captured = capsys.readouterr()
        assert "Unexpected number" not in captured.out
        assert "Unexpected number" not in captured.err


@pytest.fixture
def _needs_rdkit():
    """Adjusting a ligand's protonation needs RDKit, which is the [ligand] extra.

    The logic being tested is chemistry, not plumbing, so there is nothing
    useful to assert without it: a mock would only check that the mock was
    called.
    """
    pytest.importorskip("rdkit", reason="requires the [ligand] extra")


@pytest.mark.usefixtures("_needs_rdkit")
class TestSettledStateReachesTheForceField:
    """The pKa is determined in the pocket; the file must carry that answer.

    Settling a ligand's protonation and then handing the force field the
    reference chemistry simulates a charge state the pocket contradicts, and
    the charge is usually what binds it. Benzamidine exposed this by crashing
    on a stereocentre the amidinium does not have; retinoic acid and
    4-hydroxytamoxifen did not crash, and were simply wrong.
    """

    @staticmethod
    def _sdf(smiles):
        from rdkit import Chem
        from rdkit.Chem import AllChem

        mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
        AllChem.EmbedMolecule(mol, randomSeed=1)
        return Chem.MolToMolBlock(mol)

    @staticmethod
    def _chemistry(resname, groups, charge=0):
        from dataclasses import dataclass

        @dataclass
        class _Chemistry:
            resname: str
            formal_charge: int
            titratable_groups: tuple

        return _Chemistry(resname, charge, tuple(groups))

    @staticmethod
    def _state(resname, protonated):
        from fastmdxplora.setup.protonation import ProtonationState

        return ProtonationState(resname, protonated, (), "test")

    def _apply(self, resname, smiles, groups, protonated):
        from fastmdxplora.setup.protonation import apply_settled_state

        return apply_settled_state(
            self._sdf(smiles), self._chemistry(resname, groups),
            self._state(resname, protonated),
        )

    def test_an_amidine_protonates_on_its_sp2_nitrogen(self):
        """Benzamidine, the ligand of half the trypsin structures in the PDB.

        The cation is delocalised. Building it on the sp3 nitrogen gives a
        different molecule which also keeps the C=N double bond, and with it
        the undefined stereocentre that made the toolkit refuse the neutral
        form in the first place.
        """
        from rdkit import Chem

        text, charge = self._apply("BEN", "c1ccc(cc1)C(=N)N", ["amidine"], True)
        assert charge == 1
        mol = Chem.MolFromMolBlock(text, removeHs=False)
        assert Chem.MolToSmiles(Chem.RemoveHs(mol)) == "NC(=[NH2+])c1ccccc1"
        undefined = [e for e in Chem.FindPotentialStereo(mol)
                     if str(e.specified) == "Unspecified"]
        assert undefined == [], "the amidinium has no stereocentre to specify"

    def test_a_carboxylic_acid_settled_deprotonated_becomes_an_anion(self):
        """1CBS: retinoic acid at pKa 3.8 is anionic at pH 7."""
        text, charge = self._apply(
            "REA", "CC(C)=CC(=O)O", ["carboxylic acid"], False)
        assert charge == -1
        assert "M  CHG" in text

    def test_an_amine_settled_protonated_becomes_a_cation(self):
        """3ERT: the tamoxifen side chain at pKa 10.2 is cationic at pH 7."""
        text, charge = self._apply(
            "OHT", "CCN(CC)CCOc1ccccc1", ["tertiary amine"], True)
        assert charge == 1
        assert "M  CHG" in text

    def test_an_untitratable_ligand_is_returned_untouched(self):
        original = self._sdf("c1ccccc1")
        from fastmdxplora.setup.protonation import apply_settled_state

        text, charge = apply_settled_state(
            original, self._chemistry("BNZ", []), self._state("BNZ", False))
        assert text == original
        assert charge == 0

    def test_a_reference_already_in_the_settled_state_is_untouched(self):
        original = self._sdf("CC(C)=CC(=O)O")
        from fastmdxplora.setup.protonation import apply_settled_state

        # An acid settled protonated already holds its proton.
        text, charge = apply_settled_state(
            original, self._chemistry("ACD", ["carboxylic acid"]),
            self._state("ACD", True))
        assert text == original

    def test_a_zwitterion_is_built_as_one(self):
        """An amino acid is not uniformly acid or base; it is both.

        A single settled answer forced one side onto the other. Deciding each
        group on its own pKa builds the species that actually exists at pH 7.
        """
        from rdkit import Chem
        from fastmdxplora.setup.protonation import (
            ProtonationState, apply_settled_state,
        )

        state = ProtonationState(
            "ALA", True, (), "test",
            per_group=(("carboxylic acid", False),
                       ("primary or secondary amine", True)),
        )
        text, charge = apply_settled_state(
            self._sdf("CC(N)C(=O)O"),
            self._chemistry("ALA", ["carboxylic acid",
                                    "primary or secondary amine"]),
            state,
        )
        assert charge == 0, "the zwitterion is neutral overall"
        mol = Chem.MolFromMolBlock(text, removeHs=False)
        smiles = Chem.MolToSmiles(Chem.RemoveHs(mol))
        assert "[NH3+]" in smiles and "[O-]" in smiles, smiles

    def test_groups_of_one_class_that_straddle_the_ph_are_refused(self):
        """The calculation names the class, not which atom carries the pKa."""
        from fastmdxplora.setup.protonation import (
            ProtonationError, apply_settled_state, ProtonationState,
        )

        # No decision recorded for the acid: two of them disagreed.
        state = ProtonationState("DIA", True, (), "test",
                                 per_group=(("tertiary amine", True),))
        with pytest.raises(ProtonationError, match="not determined for that group"):
            apply_settled_state(
                self._sdf("CC(C)=CC(=O)O"),
                self._chemistry("DIA", ["carboxylic acid"]),
                state,
            )

    def test_more_sites_than_were_accounted_for_are_refused(self):
        """Two amines in the molecule but one reported: the match is not sound."""
        from fastmdxplora.setup.protonation import (
            ProtonationError, apply_settled_state,
        )

        with pytest.raises(ProtonationError, match="accounted for"):
            apply_settled_state(
                self._sdf("NCCCCCN"),
                self._chemistry("DAM", ["primary or secondary amine"]),
                self._state("DAM", True),
            )

    def test_one_answer_is_applied_to_every_site_of_its_class(self):
        """Methotrexate carries two carboxylates, both well below pH 7.

        Which is which never arises when both fall on the same side, so
        refusing over the ambiguity refused the ligand for no reason.
        """
        from rdkit import Chem
        from fastmdxplora.setup.protonation import (
            GroupPka, ProtonationState, apply_settled_state,
        )

        both = (GroupPka("MTX", "A", 1, "OCO", 3.4, 4.5),
                GroupPka("MTX", "A", 1, "OCO", 4.7, 4.5))
        state = ProtonationState("MTX", False, both, "test",
                                 per_group=(("carboxylic acid", False),))
        text, charge = apply_settled_state(
            self._sdf("OC(=O)CCC(N(C)C)C(=O)O"),
            self._chemistry("MTX", ["carboxylic acid"]),
            state,
        )
        assert charge == -2, "both carboxylates are deprotonated"
        assert Chem.MolToSmiles(
            Chem.RemoveHs(Chem.MolFromMolBlock(text, removeHs=False))
        ).count("[O-]") == 2

    def test_an_unplaceable_group_is_refused(self):
        """The phosphate patterns match either form and name no site."""
        from fastmdxplora.setup.protonation import (
            ProtonationError, apply_settled_state,
        )

        with pytest.raises(ProtonationError, match="not one this pipeline"):
            apply_settled_state(
                self._sdf("COP(=O)(O)O"),
                self._chemistry("PHO", ["phosphate or phosphonate"]),
                self._state("PHO", False),
            )


class TestPerGroupDecisions:
    """PROPKA reports a chemical class, which is enough to match a group.

    OCO is a carboxyl, C2N an amidinium, CG a guanidinium, N31/N32/N33 an amine
    by degree of substitution. Matching a reported pKa to a group in the
    molecule is therefore a lookup, not a search through atom names.
    """

    @staticmethod
    def _group(group_type, pka):
        from fastmdxplora.setup.protonation import GroupPka

        return GroupPka("LIG", "A", 1, group_type, pka, pka)

    def test_an_acid_and_a_base_are_decided_separately(self):
        from fastmdxplora.setup.protonation import _decide_per_group

        decided = dict(_decide_per_group(
            [self._group("OCO", 4.0), self._group("N31", 10.5)], 7.0))
        assert decided["carboxylic acid"] is False
        assert decided["primary or secondary amine"] is True

    def test_two_of_one_class_agreeing_is_still_decided(self):
        """Which carboxylate is which does not matter when both agree."""
        from fastmdxplora.setup.protonation import _decide_per_group

        decided = dict(_decide_per_group(
            [self._group("OCO", 3.9), self._group("OCO", 4.4)], 7.0))
        assert decided["carboxylic acid"] is False

    def test_two_of_one_class_disagreeing_is_left_undecided(self):
        """Here the site matters, and the class does not identify it."""
        from fastmdxplora.setup.protonation import _decide_per_group

        decided = dict(_decide_per_group(
            [self._group("OCO", 3.9), self._group("OCO", 9.1)], 7.0))
        assert "carboxylic acid" not in decided

    def test_an_amidine_is_not_read_as_a_guanidine(self):
        """PROPKA separates them: C2N has two nitrogens, CG has three."""
        from fastmdxplora.setup.protonation import PROPKA_GROUP_TO_LABEL

        assert PROPKA_GROUP_TO_LABEL["C2N"] == ("amidine",)
        assert PROPKA_GROUP_TO_LABEL["CG"] == ("guanidine",)


class TestThePoisedBand:
    """The band is where the calculation cannot tell, not a tolerance.

    A margin much below one pKa unit would decide a question finer than PROPKA
    resolves -- its error on protein residues is near 0.8 units, and less well
    characterised on ligands. Someone who knows their ligand can narrow it; the
    default should not.
    """

    def test_the_default_matches_the_calculation_s_resolution(self):
        from fastmdxplora.setup.protonation import POISED_MARGIN

        assert POISED_MARGIN == 1.0

    def test_the_phase_and_the_schema_agree(self):
        from fastmdxplora.setup.pipeline import DEFAULTS
        from fastmdxplora.setup.protonation import POISED_MARGIN

        assert DEFAULTS["protonation_margin"] == POISED_MARGIN

    def test_the_refusal_reports_the_share_of_the_ensemble_left_out(self):
        """A threshold in pKa units means little; a population can be weighed."""
        from fastmdxplora.setup.protonation import (
            GroupPka, ProtonationError, decide,
        )

        # 0.4 units from the pH puts about 28% in the state the pH disfavours.
        group = GroupPka("LIG", "A", 201, "OCO", 7.0, 4.0)
        with pytest.raises(ProtonationError, match=r"28% in the minor state"):
            decide("LIG", [group], ph=7.4, expected_ionizable=True)

    def test_narrowing_the_margin_lets_a_group_through(self):
        from fastmdxplora.setup.protonation import GroupPka, decide

        group = GroupPka("LIG", "A", 201, "OCO", 7.0, 4.0)
        state = decide("LIG", [group], ph=7.4, expected_ionizable=True,
                       margin=0.2)
        assert state.protonated is False


class TestGroupsTheMoleculeDoesNotCarry:
    """The disagreement between the two readings runs both ways.

    A ligand carrying an ionizable group the calculation reported nothing for
    is already refused. The reverse -- the calculation reporting a class the
    molecule does not have -- quoted the model pKa of that class, which is the
    wrong reference compound, and refused on it.
    """

    @staticmethod
    def _group(group_type, pka, model):
        from fastmdxplora.setup.protonation import GroupPka

        return GroupPka("FOL", "A", 187, group_type, pka, model)

    def test_a_class_the_molecule_lacks_is_ignored(self):
        """Folate's N10 lies between a methylene and an aromatic ring.

        It was read as an aliphatic secondary amine and given that class's
        model pKa of 10.0. An aniline is nearer 4.6 and this nitrogen is less
        basic still, so a value of 8.35 "within a unit of pH 7.4" was an
        arithmetic that never applied.
        """
        from fastmdxplora.setup.protonation import decide

        state = decide(
            "FOL",
            [self._group("N32", 8.35, 10.00), self._group("OCO", 3.5, 4.5)],
            ph=7.4,
            expected_ionizable=True,
            known_groups=("carboxylic acid", "aromatic nitrogen"),
        )
        decided = dict(state.per_group)
        # The carboxyl is decided, in whichever form it is written.
        assert decided["carboxylic acid"] is False
        # The amine the calculation invented is not there to be decided.
        assert "primary or secondary amine" not in decided

    def test_a_class_the_molecule_carries_still_decides(self):
        """Donepezil's piperidine is a real aliphatic tertiary amine."""
        from fastmdxplora.setup.protonation import ProtonationError, decide

        with pytest.raises(ProtonationError, match="ionizable groups"):
            decide("E20", [self._group("N33", 7.92, 10.00)], ph=7.4,
                   expected_ionizable=True, known_groups=("tertiary amine",))

    def test_nothing_is_dropped_when_the_chemistry_was_not_inspected(self):
        """Without a second reading there is nothing to disagree with."""
        from fastmdxplora.setup.protonation import ProtonationError, decide

        with pytest.raises(ProtonationError, match="ionizable groups"):
            decide("FOL", [self._group("N32", 8.35, 10.00)], ph=7.4,
                   expected_ionizable=True)
