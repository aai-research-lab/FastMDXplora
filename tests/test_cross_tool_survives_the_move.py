"""The comparison script, now that it lives in the repository.

It cannot be run here: it needs ProLIF and MDAnalysis, which the package
does not depend on, and a finished run to read. What can be checked is that
it still imports, that the thresholds it was pre-registered with are the
ones in the file, and that the lessons it was hardened with have not been
tidied out of it -- each of those was a cluster-side failure that cost an
afternoon, and a rewrite that loses one will cost the next afternoon too.
"""

from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture(scope="module")
def module():
    return pytest.importorskip(
        "fastmdxplora.validation.cross_tool",
        reason="the cross-tool comparison needs ProLIF and MDAnalysis")


class TestThePreRegisteredThresholds:
    """Changed after the fact, a pre-registration is not one.

    These are the numbers the protocol was written with before any
    comparison had been run, so they are checked rather than trusted.
    """

    def test_the_three_tolerances_are_unchanged(self, module):
        assert module.HARMONIZED_OCCUPANCY_TOL_PP == 5.0
        assert module.OBSERVABLE_TOL_NM == 1e-3
        assert module.NEGATIVE_OCCUPANCY_MAX_PCT == 5.0


class TestTheLessonsAreStillInIt:
    """Each of these was found by a failure on the cluster."""

    # Re-rooting a path recorded on the cluster, and the ceiling that groups
    # pairs by protein atom, are run in test_cross_tool_helpers:
    # test_a_cluster_path_is_re_rooted, test_the_conventional_layout_is_the_
    # fallback and test_pairs_on_one_protein_atom_do_not_add. The checks that
    # stood here only looked for the words.

    def test_it_still_heals_the_periodic_boundary(self, module, tmp_path):
        """A reference tool reading raw coordinates sees molecules split
        across the box, and distance-guessed bonds cannot heal them. Healed
        here with mdtraj's template bonds, once, and cached beside the run."""
        import mdtraj as md

        split, chain_bonds = _a_chain_split_across_the_box(box=4.0)
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        raw, top = run_dir / "production.dcd", run_dir / "topology.pdb"
        split.save_dcd(str(raw))
        split[0].save_pdb(str(top))

        def longest_bond(path):
            frames = md.load(str(path), top=str(top))
            return float(md.compute_distances(frames, chain_bonds, periodic=False).max())

        assert longest_bond(raw) > 1.0, "the chain was not split to begin with"
        healed = module.healed_trajectory(run_dir, raw, top)
        assert longest_bond(healed) < 0.2
        written_at = healed.stat().st_mtime_ns
        assert module.healed_trajectory(run_dir, raw, top) == healed
        assert healed.stat().st_mtime_ns == written_at, "healed again rather than reused"

    def test_the_negative_control_is_judged_on_the_floor(self, module):
        """The ceiling once convicted a passing control on the summed flicker
        of correlated ring-carbon grazes: LEU118 at 24.8% against a union of
        about 3%. FastMDXplora is judged on its measured floor, and the
        reference on what it reports."""
        cavity = {"118"}
        grazing = {("LEU118", "hydrophobic"): (3.0, 24.8)}
        assert module.cavity_contacts(grazing, {}, cavity) == []
        held = {("LEU118", "hydrophobic"): (12.0, 30.0)}
        assert module.cavity_contacts(held, {}, cavity) == [
            ("LEU118", "hydrophobic", 12.0, "fastmdx")]
        assert module.cavity_contacts({}, {("LEU118", "hydrophobic"): 9.0}, cavity) == [
            ("LEU118", "hydrophobic", 9.0, "prolif")]
        assert module.cavity_contacts({("PHE200", "pi_stacking"): (40.0, 60.0)}, {}, cavity) == []


def _a_chain_split_across_the_box(*, box: float):
    """Six alanines among twenty waters, the chain wrapped so it straddles
    the periodic boundary. Waters because mdtraj images around the largest
    molecules by comparing them with the rest: a molecule alone in a box is
    never large enough relative to itself to be anchored."""
    import mdtraj as md

    topology = md.Topology()
    chain = topology.add_chain()
    xyz = []
    for index in range(6):
        residue = topology.add_residue("ALA", chain, resSeq=index + 1)
        x = index * 0.38
        for name, element, (px, py) in (
                ("N", md.element.nitrogen, (x - 0.12, 0.0)), ("CA", md.element.carbon, (x, 0.0)),
                ("C", md.element.carbon, (x + 0.12, 0.0)), ("O", md.element.oxygen, (x + 0.12, 0.12)),
                ("CB", md.element.carbon, (x, -0.15))):
            topology.add_atom(name, element, residue)
            xyz.append([px, py, 0.0])
    n_chain = len(xyz)
    waters = topology.add_chain()
    rng = np.random.default_rng(0)
    for index in range(20):
        residue = topology.add_residue("HOH", waters, resSeq=100 + index)
        for name, element in (("O", md.element.oxygen), ("H1", md.element.hydrogen),
                              ("H2", md.element.hydrogen)):
            topology.add_atom(name, element, residue)
        centre = rng.uniform(0.5, box - 0.5, 3)
        xyz += [list(centre), list(centre + [0.09, 0, 0]), list(centre + [0, 0.09, 0])]
    topology.create_standard_bonds()
    xyz = np.array(xyz, dtype=np.float32)
    xyz[:n_chain, 0] -= xyz[:n_chain, 0].mean()
    xyz[:n_chain, 0] = np.mod(xyz[:n_chain, 0], box)
    frames = md.Trajectory(np.repeat(xyz[None], 3, axis=0), topology)
    frames.unitcell_lengths = np.tile([box, box, box], (3, 1))
    frames.unitcell_angles = np.tile([90.0, 90.0, 90.0], (3, 1))
    chain_bonds = np.array([[a.index, b.index] for a, b in topology.bonds if a.index < n_chain])
    return frames, chain_bonds
