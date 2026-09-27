"""The typed-interaction tables name a residue by chain and insertion code.

`pl_interactions` keyed its residue rows by name and number alone, so the same
residue of the two chains of a dimer was one row, and the frames in which the
ligand touched either were added into one occupancy. 184 and 184A of one chain
merged the same way. `pl_contacts` already named rows by chain; this is the
same naming, from one helper, for the residue table and the pair table.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
pytest.importorskip("rdkit")

from fastmdxplora.analysis import residues as residue_names  # noqa: E402
from fastmdxplora.analysis.pl_interactions import ProteinLigandInteractions  # noqa: E402


def _ring(centre, radius=0.14, n=6):
    return [(centre[0] + radius * np.cos(a), centre[1] + radius * np.sin(a), centre[2])
            for a in np.linspace(0, 2 * np.pi, n, endpoint=False)]


def _benzene(topology, chain):
    bnz = topology.add_residue("BNZ", chain)
    carbons = [topology.add_atom(f"C{i + 1}", md.element.carbon, bnz) for i in range(6)]
    hydrogens = [topology.add_atom(f"H{i + 1}", md.element.hydrogen, bnz) for i in range(6)]
    for i in range(6):
        topology.add_bond(carbons[i], carbons[(i + 1) % 6])
        topology.add_bond(carbons[i], hydrogens[i])
    return _ring((0, 0, 0)) + _ring((0, 0, 0), radius=0.25)


def _leucine(topology, chain, number):
    leu = topology.add_residue("LEU", chain, resSeq=number)
    carbon = topology.add_atom("CD1", md.element.carbon, leu)
    topology.add_bond(carbon, topology.add_atom("HD1", md.element.hydrogen, leu))


NEAR, FAR = [(0.35, 0, 0), (0.42, 0, 0)], [(3.0, 0, 0), (3.07, 0, 0)]


def _two_touched_in_turn(first_chain_ids: tuple[str, str] | None):
    """LEU45 of chain A touches the benzene in frame 0 and LEU45 of chain B in
    frame 1; each is 3 nm away in the other frame."""
    topology = md.Topology()
    chains = []
    for which in range(2):
        chain = topology.add_chain(first_chain_ids[which]) if first_chain_ids else \
            topology.add_chain()
        _leucine(topology, chain, 45)
        chains.append(chain)
    ligand = _benzene(topology, topology.add_chain())
    frames = [NEAR + FAR + ligand, FAR + NEAR + ligand]
    return md.Trajectory(np.array(frames, dtype=float), topology)


def _rows(analysis, kind="hydrophobic"):
    return {row["residue"]: row for row in analysis._by_residue if row["kind"] == kind}


def test_the_same_residue_of_two_chains_is_two_rows() -> None:
    analysis = ProteinLigandInteractions(ligand_resname="BNZ")
    table = analysis.compute(_two_touched_in_turn(("A", "B")))
    rows = _rows(analysis)
    assert set(rows) == {"A:LEU45", "B:LEU45"}
    assert rows["A:LEU45"]["frames_present"] == 1
    assert rows["B:LEU45"]["frames_present"] == 1
    assert set(table[table["kind"] == "hydrophobic"]["residue"]) == {"A:LEU45", "B:LEU45"}


def test_without_chain_ids_the_polymer_chains_are_lettered() -> None:
    analysis = ProteinLigandInteractions(ligand_resname="BNZ")
    analysis.compute(_two_touched_in_turn(None))
    assert set(_rows(analysis)) == {"A:LEU45", "B:LEU45"}


def test_one_chain_keeps_the_name_it_always_had() -> None:
    topology = md.Topology()
    _leucine(topology, topology.add_chain(), 45)
    ligand = _benzene(topology, topology.add_chain())
    analysis = ProteinLigandInteractions(ligand_resname="BNZ")
    analysis.compute(md.Trajectory(np.array([NEAR + ligand], dtype=float), topology))
    assert set(_rows(analysis)) == {"LEU45"}


def test_an_insertion_code_is_its_own_row(tmp_path, monkeypatch) -> None:
    """Trypsin numbers two residues 184 and 184A."""
    monkeypatch.setattr(residue_names, "_INSERTION_CODES", {})
    topology = md.Topology()
    chain = topology.add_chain("A")
    _leucine(topology, chain, 184)
    _leucine(topology, chain, 184)
    ligand = _benzene(topology, topology.add_chain("L"))
    frames = np.array([NEAR + FAR + ligand, FAR + NEAR + ligand], dtype=float)
    for atom in topology.atoms:
        atom.serial = atom.index + 1
    written = tmp_path / "complex.pdb"
    md.Trajectory(frames[:1], topology).save_pdb(str(written))
    # The second leucine's two atoms carry the code, as the deposition has it.
    text = []
    for line in written.read_text().splitlines():
        if line.startswith(("ATOM", "HETATM")) and int(line[6:11]) in (3, 4):
            line = line[:26] + "A" + line[27:]
        text.append(line)
    written.write_text("\n".join(text) + "\n")
    assert residue_names.remember_insertion_codes(written) == 1

    trajectory = md.Trajectory(frames, topology)
    analysis = ProteinLigandInteractions(ligand_resname="BNZ")
    analysis.compute(trajectory)
    rows = _rows(analysis)
    assert set(rows) == {"LEU184", "LEU184A"}
    assert rows["LEU184"]["frames_present"] == rows["LEU184A"]["frames_present"] == 1


def test_contacts_names_them_the_same_way() -> None:
    """One helper for both tables, so the two cannot drift apart."""
    from fastmdxplora.analysis.contacts import Contacts

    trajectory = _two_touched_in_turn(("A", "B"))
    interactions = ProteinLigandInteractions(ligand_resname="BNZ")
    interactions.compute(trajectory)
    contacts = Contacts(ligand_resname="BNZ", periodic=False)
    contacts.compute(trajectory)
    assert set(contacts._per_residue["residue"]) == set(_rows(interactions)) \
        == {"A:LEU45", "B:LEU45"}
