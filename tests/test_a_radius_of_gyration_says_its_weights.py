"""A radius of gyration weighted as its record says, and chains named.

One atom without an element turned the whole radius unweighted while the
options still read ``mass_weighted: true``; a TIP4P-style virtual site,
which has no mass, did the same. And ``by_chain`` wrote bare columns
numbered by position, with no frame column, against a docstring that
promised otherwise.
"""

from __future__ import annotations

import json

import mdtraj as md
import numpy as np

from fastmdxplora.analysis.rg import Rg


def _two_chains(ids=("A", "B"), frames=5, extra=None):
    """Two chains of carbon, oxygen and hydrogen, jittering."""
    top = md.Topology()
    for chain_id in ids:
        chain = top.add_chain(chain_id) if chain_id else top.add_chain()
        for i in range(4):
            residue = top.add_residue("ALA", chain, resSeq=i + 1)
            top.add_atom("CA", md.element.carbon, residue)
            top.add_atom("O", md.element.oxygen, residue)
            top.add_atom("H", md.element.hydrogen, residue)
    if extra is not None:
        residue = top.add_residue("HOH", top.add_chain(), resSeq=1)
        top.add_atom("EP", extra, residue)
    rng = np.random.default_rng(1)
    xyz = rng.normal(scale=0.6, size=(frames, top.n_atoms, 3))
    return md.Trajectory(xyz.astype(np.float32), top)


def _mass_weighted(traj, atoms=None):
    atoms = range(traj.n_atoms) if atoms is None else atoms
    atoms = list(atoms)
    mass = np.array([traj.topology.atom(i).element.mass for i in atoms])
    x = traj.xyz[:, atoms].astype(np.float64)
    centre = (x * mass[None, :, None]).sum(axis=1) / mass.sum()
    return np.sqrt((mass * ((x - centre[:, None]) ** 2).sum(axis=2)).sum(axis=1) / mass.sum())


def test_a_virtual_site_has_no_weight():
    traj = _two_chains(extra=md.element.virtual)
    analysis = Rg(selection="all")
    got = analysis.compute(traj)

    assert np.allclose(got, _mass_weighted(traj, range(traj.n_atoms - 1)), atol=1e-6)
    assert analysis.options["mass_weighted"] is True
    assert "EP" in analysis.findings["weights"]


def _beads(frames=5):
    """A bead chain built without elements, as a coarse-grained model is."""
    top = md.Topology()
    chain = top.add_chain()
    for i in range(6):
        top.add_atom("B", None, top.add_residue("BEA", chain, resSeq=i + 1))
    xyz = np.random.default_rng(2).normal(scale=0.5, size=(frames, 6, 3))
    return md.Trajectory(xyz.astype(np.float32), top)


def test_no_masses_at_all_is_said_and_recorded(tmp_path):
    beads = _beads()
    analysis = Rg(selection="all", output_dir=tmp_path)
    assert analysis.run(beads).status == "ok"

    assert np.allclose(analysis.result, md.compute_rg(beads), atol=1e-6)
    manifest = json.loads((tmp_path / "rg" / "options.json").read_text())
    assert manifest["options"]["mass_weighted"] is False
    assert "weighted equally" in manifest["findings"]["weights"]


def test_an_atom_without_an_element_is_said_and_recorded():
    traj = _two_chains()
    traj.topology.atom(0).element = None
    analysis = Rg(selection="all")
    analysis.compute(traj)

    assert analysis.options["mass_weighted"] is False
    assert "CA" in analysis.findings["weights"]


def test_by_chain_names_each_chain_by_its_id(tmp_path):
    traj = _two_chains(ids=("H", "L"))
    analysis = Rg(selection="all", by_chain=True, output_dir=tmp_path)
    result = analysis.compute(traj)

    assert list(result.columns) == ["total", "chain H", "chain L"]
    assert np.allclose(result["chain L"], _mass_weighted(traj, range(12, 24)), atol=1e-6)
    assert np.allclose(result["total"], _mass_weighted(traj), atol=1e-6)

    run = Rg(selection="all", by_chain=True, output_dir=tmp_path).run(traj)
    assert run.status == "ok"
    assert run.data_path.read_text().splitlines()[0] == "total,chain H,chain L"


def test_the_legend_names_the_chains(tmp_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    traj = _two_chains(ids=("H", "L"))
    analysis = Rg(selection="all", by_chain=True, output_dir=tmp_path)
    _fig, ax = plt.subplots()
    analysis.plot(analysis.compute(traj), ax)
    labels = [text.get_text() for text in ax.get_legend().get_texts()]
    plt.close("all")
    assert labels == ["total", "chain H", "chain L"]


def test_the_total_of_a_chain_table_is_reweighted():
    from fastmdxplora.analysis.reweighted_averages import frame_series

    traj = _two_chains()
    table = Rg(selection="all", by_chain=True).compute(traj)
    assert np.allclose(frame_series(table, Rg.reweightable, traj.n_frames), table["total"])
    plain = Rg(selection="all").compute(traj)
    assert np.allclose(frame_series(plain, Rg.reweightable, traj.n_frames), plain)


def test_one_analysis_reused_keeps_what_it_was_asked():
    """Falling back on one trajectory does not carry over to the next."""
    analysis = Rg(selection="all")
    analysis.compute(_beads())
    assert analysis.options["mass_weighted"] is False

    traj = _two_chains()
    assert np.allclose(analysis.compute(traj), _mass_weighted(traj), atol=1e-6)
    assert analysis.options["mass_weighted"] is True
    assert "weights" not in analysis.findings
