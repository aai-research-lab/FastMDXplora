"""The typed-interaction analysis, driven end to end on a known complex.

The rules are tested one geometry at a time elsewhere. This is the layer a
run actually calls: chemistry resolved and recorded, every asked-for rule
attempted, refusals reported beside results rather than in place of them, and
occupancies that say how well each contact was observed. The trajectory is
synthetic and the right answer is known in advance, which is the only way to
test an analysis whose production input nobody has by hand.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
pytest.importorskip("rdkit")

import pandas as pd  # noqa: E402

from fastmdxplora.analysis.pl_interactions import (  # noqa: E402
    ALL_KINDS,
    ProteinLigandInteractions,
)


def _ring(centre, radius=0.14, n=6):
    return [(centre[0] + radius * np.cos(a), centre[1] + radius * np.sin(a),
             centre[2]) for a in np.linspace(0, 2 * np.pi, n, endpoint=False)]


def _complex(*, frames: int = 2, ligand_at=(0.0, 0.0, 0.0)) -> md.Trajectory:
    """Benzene near a PHE ring and a LEU carbon: stacking and a hydrophobic
    contact, and nothing else -- benzene has no donors, acceptors or charge,
    so every other rule runs and correctly finds nothing."""
    topology = md.Topology()
    xyz: list[tuple] = []

    chain = topology.add_chain()
    phe = topology.add_residue("PHE", chain)
    for name, pos in zip(("CG", "CD1", "CD2", "CE1", "CE2", "CZ"),
                         _ring((0, 0, 0.37))):
        topology.add_atom(name, md.element.carbon, phe)
        xyz.append(pos)
    leu = topology.add_residue("LEU", chain)
    for name, element, pos in (("CD1", md.element.carbon, (0.35, 0, 0)),
                               ("HD1", md.element.hydrogen, (0.42, 0, 0))):
        topology.add_atom(name, element, leu)
        xyz.append(pos)
    atoms = list(topology.atoms)
    topology.add_bond(atoms[-2], atoms[-1])

    lig_chain = topology.add_chain()
    bnz = topology.add_residue("BNZ", lig_chain)
    for i, pos in enumerate(_ring(ligand_at)):
        topology.add_atom(f"C{i+1}", md.element.carbon, bnz)
        xyz.append(pos)
    for i, pos in enumerate(_ring(ligand_at, radius=0.25)):
        topology.add_atom(f"H{i+1}", md.element.hydrogen, bnz)
        xyz.append(pos)
    # Bonded like the molecule it is. A carbon with no bonds is deliberately
    # not counted as hydrophobic -- no neighbours is no evidence -- so an
    # unbonded ring here would silently test nothing.
    ligand_atoms = [a for a in topology.atoms if a.residue.name == "BNZ"]
    for i in range(6):
        topology.add_bond(ligand_atoms[i], ligand_atoms[(i + 1) % 6])
        topology.add_bond(ligand_atoms[i], ligand_atoms[i + 6])

    frame = np.array(xyz, dtype=float)
    return md.Trajectory(np.stack([frame] * frames), topology)


class TestTheWholeAnalysis:
    def test_it_finds_what_is_there_and_only_that(self) -> None:
        analysis = ProteinLigandInteractions(ligand_resname="BNZ")
        table = analysis.compute(_complex())
        kinds = set(table["kind"])
        assert any(k.startswith("pi_stacking") for k in kinds)
        assert "hydrophobic" in kinds
        # Benzene can form none of these; a rule finding one here would be
        # inventing it.
        assert not kinds & {"salt_bridge", "halogen_bond",
                            "water_bridge", "metal_coordination"}
        assert not any(k.startswith("hydrogen_bond") for k in kinds)

    def test_an_always_present_contact_has_occupancy_one(self) -> None:
        analysis = ProteinLigandInteractions(ligand_resname="BNZ")
        table = analysis.compute(_complex(frames=3))
        stacking = table[table["kind"].str.startswith("pi_stacking")]
        assert stacking["occupancy"].tolist() == pytest.approx(
            [1.0] * len(stacking))
        assert (stacking["frames_total"] == 3).all()

    def test_the_chemistry_is_recorded_beside_the_results(self) -> None:
        """An interaction computed from perceived bond orders is a different
        claim from one computed from stated chemistry, and the findings say
        which this was."""
        analysis = ProteinLigandInteractions(ligand_resname="BNZ")
        analysis.compute(_complex())
        record = analysis.findings["ligand_chemistry"]
        assert record["source"] == "perceived"
        assert record["bond_orders_perceived"] is True

    def test_binding_modes_are_reported(self) -> None:
        analysis = ProteinLigandInteractions(ligand_resname="BNZ")
        analysis.compute(_complex(frames=4))
        assert analysis.findings["binding_modes"]
        assert "mode_transitions" in analysis.findings


class TestRefusalsAreReportedNotFatal:
    def test_a_refused_rule_is_a_finding_and_the_rest_still_run(
            self, monkeypatch) -> None:
        """A ligand whose charge is unknown still has hydrophobic contacts.
        The refusal is recorded by name beside the results it did not stop."""
        import fastmdxplora.analysis.ligand_chemistry as chemistry_module

        real = chemistry_module.resolve_ligand_chemistry

        def ambiguous(*args, **kwargs):
            resolved = real(*args, **kwargs)
            object.__setattr__(resolved, "charge_was_ambiguous", True)
            return resolved

        monkeypatch.setattr(chemistry_module, "resolve_ligand_chemistry",
                            ambiguous)
        analysis = ProteinLigandInteractions(ligand_resname="BNZ")
        table = analysis.compute(_complex())
        assert "salt_bridge" in analysis.findings["not_measured"]
        assert "charge" in analysis.findings["not_measured"]["salt_bridge"]
        assert "hydrophobic" in set(table["kind"])  # the run went on


class TestOptionsAndEdges:
    def test_kinds_narrows_what_is_attempted(self) -> None:
        analysis = ProteinLigandInteractions(
            ligand_resname="BNZ", kinds=("hydrophobic",))
        table = analysis.compute(_complex())
        assert set(table["kind"]) == {"hydrophobic"}

    def test_all_kinds_is_the_declared_default(self) -> None:
        assert ProteinLigandInteractions(
            ligand_resname="BNZ").kinds == list(ALL_KINDS)

    def test_a_ligand_nowhere_near_gives_the_empty_table_with_columns(
            self) -> None:
        """Empty with the declared columns, so everything downstream -- the
        plot, the report -- reads a shape rather than a surprise."""
        analysis = ProteinLigandInteractions(ligand_resname="BNZ")
        table = analysis.compute(_complex(ligand_at=(5.0, 5.0, 5.0)))
        assert len(table) == 0
        assert "occupancy" in table.columns and "kind" in table.columns

    def test_a_modified_residue_is_noted_not_refused(self) -> None:
        traj = _complex()
        topology = traj.topology
        mse = topology.add_residue("MSE", list(topology.chains)[0])
        topology.add_atom("SE", md.element.get_by_symbol("Se"), mse)
        xyz = np.concatenate(
            [traj.xyz, np.full((traj.n_frames, 1, 3), 3.0)], axis=1)
        analysis = ProteinLigandInteractions(ligand_resname="BNZ")
        analysis.compute(md.Trajectory(xyz, topology))
        noted = analysis.options["residues_not_examined_for_charge_or_rings"]
        assert "MSE" in noted

    def test_no_ligand_resname_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="ligand_resname"):
            ProteinLigandInteractions()

    def test_no_matching_atoms_is_a_clear_error(self) -> None:
        analysis = ProteinLigandInteractions(ligand_resname="XYZ")
        with pytest.raises(ValueError, match="XYZ"):
            analysis.compute(_complex())


class TestADataFileSaysHowToReadIt:
    """Both formats are called `.dat`, and a reader cannot tell which.

    Array results are whitespace with no header; frame results are
    comma-separated with one. A one-liner that guesses wrong gets a
    ValueError at best and a column of NaN at worst, which is how
    `pl_contacts.dat` cost an afternoon. The answer is written into the
    record beside the file rather than left in somebody's notes.
    """

    def test_a_frame_result_declares_its_header(self, tmp_path):

        from fastmdxplora.analysis.rmsd import RMSD

        analysis = RMSD(output_dir=tmp_path)
        analysis.save_data(
            pd.DataFrame({"frame": [0, 1], "value": [0.1, 0.2]}),
            tmp_path / "x.dat")

        assert analysis._data_format["layout"].startswith("comma-separated")
        assert "skiprows=1" in analysis._data_format["read_with"]
        assert analysis._data_format["columns"] == ["frame", "value"]

    def test_an_array_result_declares_that_it_has_none(self, tmp_path):
        import numpy as np

        from fastmdxplora.analysis.rmsd import RMSD

        analysis = RMSD(output_dir=tmp_path)
        analysis.save_data(np.array([[0.0, 0.1], [1.0, 0.2]]),
                           tmp_path / "y.dat")

        assert analysis._data_format["layout"] == (
            "whitespace-delimited, no header")
        assert analysis._data_format["read_with"] == "np.loadtxt(path)"


class TestAMetalBetweenThem:
    def test_a_zinc_between_the_ligand_and_a_histidine_is_found(self) -> None:
        """An ion is its own residue, in neither the ligand's selection nor
        `protein`, and the rule was given only those two: metal coordination
        never fired, here with a zinc 0.20 nm from a ligand oxygen."""
        topology = md.Topology()
        xyz = []
        lig = topology.add_residue("MOH", topology.add_chain())
        for name, element, position in (
                ("C1", "C", (-0.143, 0, 0)), ("O1", "O", (0, 0, 0)),
                ("H1", "H", (-0.18, 0.1, 0)), ("H2", "H", (-0.18, -0.05, 0.09)),
                ("H3", "H", (-0.18, -0.05, -0.09)), ("HO", "H", (0.03, 0.09, 0))):
            topology.add_atom(name, md.element.get_by_symbol(element), lig)
            xyz.append(position)
        atoms = list(topology.atoms)
        for i, j in ((0, 1), (0, 2), (0, 3), (0, 4), (1, 5)):
            topology.add_bond(atoms[i], atoms[j])
        zn = topology.add_residue("ZN", topology.add_chain())
        topology.add_atom("ZN", md.element.zinc, zn)
        xyz.append((0.20, 0, 0))
        his = topology.add_residue("HIS", topology.add_chain())
        for name, element, position in (("NE2", "N", (0.40, 0, 0)),
                                        ("CE1", "C", (0.50, 0.1, 0))):
            topology.add_atom(name, md.element.get_by_symbol(element), his)
            xyz.append(position)
        traj = md.Trajectory(np.array([xyz], dtype=float), topology)

        analysis = ProteinLigandInteractions(
            ligand_resname="MOH", kinds=("metal_coordination",))
        table = analysis.compute(traj)
        assert table["kind"].tolist() == ["metal_coordination"]
        assert table["residue"].str.startswith("ZN").all()
        assert table["ligand_atom_name"].tolist() == ["O1"]
