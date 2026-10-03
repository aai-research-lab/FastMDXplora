"""A per-residue SASA run averages each chain's residues apart.

Run per residue, SASA writes each residue's mean beside its per-frame table.
The mean was grouped by residue number alone, so on a structure of several
chains the copies of each residue were averaged together: a residue buried
at an interface in one chain and exposed in the other read as half exposed
in both, and the table had one row where there were two residues.
"""

from __future__ import annotations

import sys

import mdtraj as md
import numpy as np
import pandas as pd
import pytest


def _two_chains() -> md.Trajectory:
    """Two chains numbered alike, the second far from the first and with
    its first residue buried against a third body, so the copies differ."""
    topology = md.Topology()
    xyz = []
    for copy in range(2):
        chain = topology.add_chain()
        for number in range(1, 4):
            residue = topology.add_residue("ALA", chain, resSeq=number)
            for offset, (name, element) in enumerate((
                    ("N", md.element.nitrogen), ("CA", md.element.carbon),
                    ("C", md.element.carbon), ("O", md.element.oxygen))):
                topology.add_atom(name, element, residue)
                xyz.append([0.38 * number + 0.11 * offset, 3.0 * copy, 0.0])
    crowd = topology.add_residue("LIG", topology.add_chain(), resSeq=1)
    for n in range(8):
        topology.add_atom(f"C{n}", md.element.carbon, crowd)
        xyz.append([0.38 + 0.12 * (n % 4), 3.0 + 0.2 * (n // 4), 0.2])
    rng = np.random.default_rng(1)
    frames = np.array(xyz)[None] + rng.normal(scale=0.004, size=(4, len(xyz), 3))
    return md.Trajectory(frames.astype(np.float32), topology)


@pytest.mark.xfail(sys.platform == "win32", strict=False,
                   reason="MDTraj's surface areas on Windows; see test_concrete_analyses.")
def test_each_chain_s_residues_are_averaged_apart(tmp_path):
    from fastmdxplora.analysis.sasa import SASA

    SASA(mode="residue", selection="protein", output_dir=str(tmp_path)).run(_two_chains())
    folder = tmp_path / "sasa"
    per_frame = pd.read_csv(folder / "sasa.dat")
    average = pd.read_csv(folder / "sasa_average_per_residue.csv")
    assert list(average.columns[:2]) == ["chain", "residue"]
    assert len(average) == 6, "one row for each residue of each chain"
    expected = per_frame.groupby(["chain", "residue"])["sasa_nm2"].mean()
    for row in average.itertuples():
        assert row.mean_sasa_nm2 == pytest.approx(expected[(row.chain, row.residue)])
    # The crowded first residue of the second chain is not the first chain's.
    first = average.set_index(["chain", "residue"])["mean_sasa_nm2"]
    assert first[("B", 1)] < first[("A", 1)]
