"""Order parameters, checked against an answer that exists without them."""

from __future__ import annotations

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.order_parameters import (
    HALVES_TOLERANCE,
    OrderParameters,
    amide_pairs,
    correlation_plateau,
    order_parameters,
)


def _cone(cone_deg: float, n: int, seed: int = 1) -> np.ndarray:
    """A vector reorienting fast and uniformly inside a cone."""
    rng = np.random.RandomState(seed)
    half = np.deg2rad(cone_deg)
    out = np.zeros((n, 1, 3))
    for i in range(n):
        cos_t = 1.0 - rng.rand() * (1.0 - np.cos(half))
        sin_t = np.sqrt(max(0.0, 1.0 - cos_t * cos_t))
        phi = rng.rand() * 2.0 * np.pi
        out[i, 0] = [sin_t * np.cos(phi), sin_t * np.sin(phi), cos_t]
    return out


class TestS2AgreesWithAnAnswerItWasNotFittedTo:
    """Diffusion in a cone has a closed-form order parameter.

    For a vector reorienting uniformly within a cone of half-angle theta,
    S = cos(theta) (1 + cos(theta)) / 2, independently of this code. That
    makes it a reference rather than a self-consistency check: the two
    routes implemented here could agree with each other and both be wrong.
    """

    @pytest.mark.parametrize("cone", [0.0, 10.0, 20.0, 30.0, 45.0])
    def test_against_the_cone_model(self, cone: float) -> None:
        vectors = _cone(cone, 20000)
        cos_t = np.cos(np.deg2rad(cone))
        analytic = (0.5 * cos_t * (1.0 + cos_t)) ** 2
        assert abs(order_parameters(vectors)[0] - analytic) < 0.02

    def test_the_closed_form_is_the_plateau_of_the_definition(self) -> None:
        """The identity the closed form relies on, checked rather than assumed.

        S^2 is defined as the tail of the second-rank correlation function;
        the closed form is what that tail equals once the internal motion
        has decorrelated. Where a trajectory is too short for a plateau to
        exist, the two part company, and only one of them announces it.
        """
        vectors = _cone(25.0, 20000)
        assert abs(order_parameters(vectors)[0]
                   - correlation_plateau(vectors)[0]) < 0.02

    def test_a_rigid_vector_is_one_and_an_isotropic_one_is_zero(self) -> None:
        rng = np.random.RandomState(0)
        rigid = np.tile(np.array([[0.0, 0.0, 1.0]]), (500, 1, 1))
        assert order_parameters(rigid)[0] == pytest.approx(1.0, abs=1e-9)

        isotropic = rng.normal(size=(40000, 1, 3))
        isotropic /= np.linalg.norm(isotropic, axis=2, keepdims=True)
        assert order_parameters(isotropic)[0] == pytest.approx(0.0, abs=0.01)


def _peptide(n_residues: int = 6, n_frames: int = 200, wobble: float = 0.02,
             with_hydrogens: bool = True, proline_at: int | None = None):
    """A backbone with amide hydrogens, jittering about a fixed geometry."""
    rng = np.random.RandomState(4)
    top = md.Topology()
    chain = top.add_chain()
    positions: list[list[float]] = []
    for i in range(n_residues):
        name = "PRO" if proline_at == i else "ALA"
        res = top.add_residue(name, chain, resSeq=i + 1)
        top.add_atom("N", md.element.nitrogen, res)
        positions.append([i * 0.38, 0.0, 0.0])
        if with_hydrogens and name != "PRO":
            top.add_atom("H", md.element.hydrogen, res)
            positions.append([i * 0.38, 0.10, 0.0])
        top.add_atom("CA", md.element.carbon, res)
        positions.append([i * 0.38 + 0.15, -0.05, 0.0])
        top.add_atom("C", md.element.carbon, res)
        positions.append([i * 0.38 + 0.25, 0.05, 0.0])

    xyz = np.tile(np.array(positions)[None], (n_frames, 1, 1))
    xyz += rng.normal(scale=wobble, size=xyz.shape)
    return md.Trajectory(xyz=xyz.astype(np.float32), topology=top)


class TestWhichBondsAreEvenEligible:
    def test_proline_has_no_amide_hydrogen(self) -> None:
        """Absent by chemistry, not by convention.

        Its backbone nitrogen is in the ring, so there is no vector and no
        experimental value to compare a computed one against.
        """
        traj = _peptide(proline_at=3)
        residues = {r for _, _, r in amide_pairs(traj.topology)}
        assert 3 not in residues

    def test_the_first_residue_is_left_out(self) -> None:
        traj = _peptide()
        residues = {r for _, _, r in amide_pairs(traj.topology)}
        assert 0 not in residues
        assert residues == {1, 2, 3, 4, 5}

    def test_a_structure_without_hydrogens_is_refused(self) -> None:
        traj = _peptide(with_hydrogens=False)
        with pytest.raises(ValueError, match="without hydrogens"):
            OrderParameters().compute(traj)


class TestTheAnalysisSaysWhatItRests_On:
    def test_it_reports_one_value_per_eligible_residue(self) -> None:
        traj = _peptide()
        result = OrderParameters().compute(traj)
        assert result.shape == (5, 2)
        assert np.all(result[:, 1] <= 1.0 + 1e-9)
        assert np.all(result[:, 1] >= -0.5)

    def test_the_alignment_choice_is_recorded(self) -> None:
        """It changes the answer, so it belongs in the record.

        Aligning on a flexible terminus drags the frame with it and
        depresses every other residue's order parameter.
        """
        analysis = OrderParameters(align_selection="name CA")
        assert analysis.options["align_selection"] == "name CA"
        assert "superposition" in analysis.options["definition"]

    def test_too_short_a_run_is_called_an_upper_bound(self) -> None:
        """The failure is one-sided, so scatter cannot reveal it.

        A trajectory whose two halves disagree has not sampled what it is
        averaging over, and unsampled motion looks like rigidity rather
        than like noise.
        """
        np.random.RandomState(2)
        traj = _peptide(n_frames=400)
        # A slow drift in one amide's direction: each half is internally
        # consistent and the two disagree.
        xyz = np.array(traj.xyz)
        drift = np.linspace(0.0, 0.35, traj.n_frames)
        xyz[:, 5, 1] += drift
        drifting = md.Trajectory(xyz=xyz, topology=traj.topology)

        analysis = OrderParameters()
        analysis.compute(drifting)
        findings = analysis.findings["order_parameters"]

        assert findings["halves_max_difference"] > HALVES_TOLERANCE
        assert "upper bound" in findings["not_a_measurement"]
        assert "one direction" in findings["not_a_measurement"]

    def test_a_settled_run_is_not_qualified(self) -> None:
        traj = _peptide(n_frames=600, wobble=0.015)
        analysis = OrderParameters()
        analysis.compute(traj)
        findings = analysis.findings["order_parameters"]
        assert "not_a_measurement" not in findings

    def test_an_alignment_set_too_small_to_orient_is_refused(self) -> None:
        traj = _peptide()
        with pytest.raises(ValueError, match="at least three"):
            OrderParameters(align_selection="resid 0 and name CA").compute(
                traj)


def test_a_homodimer_s_order_parameters_are_saved(tmp_path) -> None:
    """Two copies numbered alike come back as a table naming the chain, and
    saving it as numbers failed: a symmetric dimer's run lost them."""
    one = _peptide()
    two = one.stack(one)
    result = OrderParameters(output_dir=tmp_path).run(two)
    assert result.status == "ok", result.message
    lines = result.data_path.read_text().splitlines()
    assert lines[0] == "chain,residue,s2"
    assert {line.split(",")[0] for line in lines[1:]} == {"A", "B"}


def _chains_as_openmm_writes_them(n_chains: int = 2, n_residues: int = 4,
                                  bonds: bool = True, gap_after: int | None = None,
                                  one_chain: bool = False) -> md.Topology:
    """Chains whose N-terminal NH3+ hydrogens are named H, H2 and H3.

    That is how OpenMM and PDBFixer write them (1HHO chain B VAL1 reads
    N, H, H2, H3), so the first terminal hydrogen answers to the amide
    name ``H``. With ``one_chain`` every residue goes into a single chain,
    as a file without TER records is read; ``gap_after`` leaves out the
    peptide bond after that residue number."""
    top = md.Topology()
    chain = top.add_chain()
    for copy in range(n_chains):
        if copy and not one_chain:
            chain = top.add_chain()
        previous_c = None
        for i in range(n_residues):
            res = top.add_residue("ALA", chain, resSeq=i + 1)
            n = top.add_atom("N", md.element.nitrogen, res)
            names = ("H", "H2", "H3") if i == 0 else ("H",)
            hs = [top.add_atom(name, md.element.hydrogen, res) for name in names]
            ca = top.add_atom("CA", md.element.carbon, res)
            c = top.add_atom("C", md.element.carbon, res)
            if bonds:
                for h in hs:
                    top.add_bond(n, h)
                top.add_bond(n, ca)
                top.add_bond(ca, c)
                if previous_c is not None and gap_after != i:
                    top.add_bond(previous_c, n)
            previous_c = c
    return top


class TestEveryNTerminusIsLeftOut:
    """Only the topology's first residue was excluded as an N-terminus, so
    the NH3+ of every later chain was counted as an amide through its
    hydrogen named H: on 1HHO chain B's VAL1."""

    def test_the_second_chain_s_first_residue_is_not_an_amide(self) -> None:
        top = _chains_as_openmm_writes_them()
        firsts = {next(iter(c.residues)).index for c in top.chains}
        residues = {r for _, _, r in amide_pairs(top)}
        assert firsts == {0, 4}
        assert residues == {1, 2, 3, 5, 6, 7}

    def test_a_terminus_inside_one_chain_is_found_by_its_hydrogens(self) -> None:
        """Two chains read as one, without bonds: H2 and H3 still say it."""
        top = _chains_as_openmm_writes_them(bonds=False, one_chain=True)
        assert top.n_chains == 1
        assert {r for _, _, r in amide_pairs(top)} == {1, 2, 3, 5, 6, 7}

    def test_a_residue_after_a_break_in_the_chain_is_left_out(self) -> None:
        """No peptide bond to the residue before: its N-H is not an amide
        of a continuous backbone, and no experimental set means it."""
        top = _chains_as_openmm_writes_them(n_chains=1, n_residues=6, gap_after=3)
        assert {r for _, _, r in amide_pairs(top)} == {1, 2, 4, 5}

    def test_haemoglobin_as_pdbfixer_writes_it(self) -> None:
        """1HHO with hydrogens added by PDBFixer, the case the audit found:
        chain B's VAL1 carries H, H2 and H3."""
        import gzip
        import tempfile
        from pathlib import Path

        pytest.importorskip("openmm")
        pytest.importorskip("pdbfixer")
        from openmm.app import PDBFile
        from pdbfixer import PDBFixer
        source = Path(__file__).parent / "data" / "assemblies" / "1HHO.pdb.gz"
        with tempfile.TemporaryDirectory() as folder:
            raw = Path(folder) / "1HHO.pdb"
            raw.write_bytes(gzip.decompress(source.read_bytes()))
            fixer = PDBFixer(filename=str(raw))
            fixer.removeHeterogens(False)
            fixer.missingResidues = {}
            fixer.findMissingAtoms()
            fixer.addMissingAtoms()
            fixer.addMissingHydrogens(7.0)
            built = Path(folder) / "1HHO_h.pdb"
            with open(built, "w") as handle:
                PDBFile.writeFile(fixer.topology, fixer.positions, handle, keepIds=True)
            topology = md.load(str(built)).topology
        firsts = {next(iter(c.residues)).index for c in topology.chains}
        assert len(firsts) == 2
        assert not [p for p in amide_pairs(topology) if p[2] in firsts]
