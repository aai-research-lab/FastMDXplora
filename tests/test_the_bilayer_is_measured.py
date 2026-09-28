"""A bilayer's area per lipid, thickness and chain order are measured.

The three numbers a membrane run is checked against before anything about
the protein in it is believed, and which nothing measured: a membrane study
reported the protein's RMSD and radius of gyration and said nothing about
whether the bilayer around it was fluid, the right thickness, or packed at
the right area. Measured here on OpenMM's own pre-equilibrated patches, so
the values are those of a real bilayer.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from fastmdxplora.refusals import StudyError

md = pytest.importorskip("mdtraj")
app = pytest.importorskip("openmm.app")

from fastmdxplora.analysis.area_per_lipid import AreaPerLipid  # noqa: E402
from fastmdxplora.analysis.bilayer import cross_section  # noqa: E402
from fastmdxplora.analysis.bilayer_thickness import BilayerThickness  # noqa: E402
from fastmdxplora.analysis.lipid_order import LipidOrder  # noqa: E402


def _patch(lipid: str):
    return md.load(str(Path(app.__file__).parent / "data" / f"{lipid}.pdb"))


def _split_like_lipid21(traj):
    """Each POPC as three residues, sn-1 chain, head, sn-2 chain, bonded."""
    import re

    top = md.Topology()
    order: list[int] = []
    chain = top.add_chain()
    mapping: dict[int, Any] = {}
    for residue in traj.topology.residues:
        atoms = list(residue.atoms)
        if residue.name != "POP":
            new = top.add_residue(residue.name, chain, resSeq=residue.resSeq)
            for atom in atoms:
                mapping[atom.index] = top.add_atom(atom.name, atom.element, new)
                order.append(atom.index)
            continue
        sn1 = [a for a in atoms if re.fullmatch(r"C3\d+|O32|H\d+[XYZ]", a.name)]
        sn2 = [a for a in atoms if re.fullmatch(r"C2\d+|O22|H\d+[RST]|H91|H101", a.name)]
        head = [a for a in atoms if a not in sn1 and a not in sn2]
        for name, part in (("PA", sn1), ("PC", head), ("OL", sn2)):
            new = top.add_residue(name, chain, resSeq=residue.resSeq)
            for atom in part:
                mapping[atom.index] = top.add_atom(atom.name, atom.element, new)
                order.append(atom.index)
    for bond in traj.topology.bonds:
        top.add_bond(mapping[bond[0].index], mapping[bond[1].index])
    return md.Trajectory(traj.xyz[:, order], top, unitcell_lengths=traj.unitcell_lengths,
                         unitcell_angles=traj.unitcell_angles)


def _run(cls, traj):
    analysis = cls(output_dir=tempfile.mkdtemp())
    return analysis.compute(traj), analysis.findings


class TestAreaAndThickness:

    @pytest.mark.parametrize("lipid", ["DMPC", "POPC", "DOPC"])
    def test_a_bilayer_with_nothing_in_it_is_its_box_per_lipid(self, lipid) -> None:
        traj = _patch(lipid)
        area, findings = _run(AreaPerLipid, traj)
        lipids = findings["bilayer"]["lipids"]
        box = traj.unitcell_lengths[0]
        assert area[0] == pytest.approx(box[0] * box[1] / (lipids / 2), rel=1e-6)
        assert findings["area"]["protein_cross_section_nm2_mean"] == 0.0
        upper, lower = findings["bilayer"]["per_leaflet_first_frame"]
        assert upper == lower == lipids / 2
        # A fluid bilayer of these lipids: 0.6 to 0.7 nm2 in experiment.
        assert 0.58 < area[0] < 0.70

    @pytest.mark.parametrize("lipid, low, high", [("DLPC", 2.9, 3.3),
                                                  ("DMPC", 3.3, 3.7),
                                                  ("POPC", 3.5, 3.9)])
    def test_thickness_grows_with_the_chains(self, lipid, low, high) -> None:
        thickness, _ = _run(BilayerThickness, _patch(lipid))
        assert low < thickness[0] < high

    @pytest.mark.parametrize("shift", [0.0, 1.3, 2.9, 4.4])
    def test_a_bilayer_across_the_box_face_is_measured_the_same(self, shift) -> None:
        traj = _patch("POPC")
        before = _run(BilayerThickness, traj)[0][0]
        moved = traj[:]
        moved.xyz[..., 2] = np.mod(moved.xyz[..., 2] + shift, moved.unitcell_lengths[0, 2])
        assert _run(BilayerThickness, moved)[0][0] == pytest.approx(before, abs=1e-4)
        assert _run(AreaPerLipid, moved)[0][0] == pytest.approx(
            _run(AreaPerLipid, traj)[0][0], rel=1e-6)

    def test_a_protein_takes_its_cross_section_out_of_the_area(self) -> None:
        """A cylinder of carbon, 0.6 nm in radius, through the middle."""
        traj = _patch("DMPC")
        top = traj.topology.copy()
        residue = top.add_residue("ALA", top.add_chain(), resSeq=1)
        centre = traj.unitcell_lengths[0, :2] / 2
        mid = float(np.median(traj.xyz[0, top.select("name P"), 2]))
        points = []
        for z in np.arange(-2.5, 2.51, 0.15):
            for r in (0.0, 0.3, 0.6):
                count = max(1, int(2 * np.pi * r / 0.15))
                for angle in np.linspace(0, 2 * np.pi, count, endpoint=False):
                    points.append([centre[0] + r * np.cos(angle),
                                   centre[1] + r * np.sin(angle), mid + z])
        for index in range(len(points)):
            top.add_atom(f"C{index}", md.element.carbon, residue)
        xyz = np.concatenate([traj.xyz, np.asarray(points, dtype=np.float32)[None]], axis=1)
        with_protein = md.Trajectory(xyz, top, unitcell_lengths=traj.unitcell_lengths,
                                     unitcell_angles=traj.unitcell_angles)
        area, findings = _run(AreaPerLipid, with_protein)
        section = findings["area"]["protein_cross_section_nm2_mean"]
        assert section == pytest.approx(np.pi * (0.6 + 0.17) ** 2, rel=0.08)
        box = traj.unitcell_lengths[0]
        assert area[0] == pytest.approx(
            (box[0] * box[1] - section) / (findings["bilayer"]["lipids"] / 2), rel=1e-6)
        assert "protein_share" in findings


class TestASterolIsALipidWithoutChains:
    """Cholesterol shares the bilayer's area and has no phosphate and no acyl
    chain: counted by the area, left out of the thickness and the order."""

    @staticmethod
    def _with_cholesterol():
        traj = _patch("DMPC")
        top = traj.topology.copy()
        phosphorus = traj.xyz[0, top.select("name P"), 2]
        middle = float(np.median(phosphorus))
        centre = traj.unitcell_lengths[0, :2] / 2
        points = []
        chain = top.add_chain()
        for side in (1.0, -1.0):
            residue = top.add_residue("CHL1", chain)
            for index, depth in enumerate((1.3, 1.0, 0.7, 0.4)):
                name, element = ("O3", md.element.oxygen) if index == 0 else (
                    f"C{index}", md.element.carbon)
                top.add_atom(name, element, residue)
                points.append([centre[0], centre[1], middle + side * depth])
        xyz = np.concatenate([traj.xyz, np.asarray(points, dtype=np.float32)[None]], axis=1)
        return traj, md.Trajectory(xyz, top, unitcell_lengths=traj.unitcell_lengths,
                                   unitcell_angles=traj.unitcell_angles)

    def test_the_area_counts_it(self) -> None:
        plain, mixed = self._with_cholesterol()
        area, findings = _run(AreaPerLipid, mixed)
        assert findings["bilayer"]["lipids"] == 130
        assert findings["bilayer"]["composition"]["CHL1"] == 2
        assert "sterols" in findings
        box = plain.unitcell_lengths[0]
        assert area[0] == pytest.approx(box[0] * box[1] / 65, rel=1e-6)

    def test_the_thickness_and_the_order_leave_it_out(self) -> None:
        plain, mixed = self._with_cholesterol()
        assert _run(BilayerThickness, mixed)[0][0] == pytest.approx(
            _run(BilayerThickness, plain)[0][0], abs=1e-6)
        table, findings = _run(LipidOrder, mixed)
        assert set(table["lipid"]) == {"DMPC"}
        assert "no_hydrogens" not in findings and "no_chain_found" not in findings


class TestWhatIsNotABilayerIsNotMeasured:

    def test_lipids_on_their_side_are_refused(self) -> None:
        traj = _patch("DMPC")
        turned = traj[:]
        turned.xyz = turned.xyz[..., [2, 1, 0]]
        lengths = traj.unitcell_lengths[:, [2, 1, 0]]
        turned.unitcell_lengths = lengths
        with pytest.raises(StudyError) as refused:
            _run(BilayerThickness, turned)
        assert refused.value.code == "analysis.system.inapplicable"
        assert "two layers normal to z" in str(refused.value)

    def test_a_few_lipids_are_not_a_bilayer(self) -> None:
        traj = _patch("DMPC")
        few = traj.atom_slice(traj.topology.select("resid 0 to 4"))
        with pytest.raises(StudyError) as refused:
            _run(AreaPerLipid, few)
        assert refused.value.code == "analysis.system.inapplicable"

    def test_a_trajectory_without_a_box_is_refused(self) -> None:
        traj = _patch("DMPC")
        traj.unitcell_vectors = None
        with pytest.raises(StudyError) as refused:
            _run(AreaPerLipid, traj)
        assert "periodic box" in str(refused.value)


class TestChainOrder:

    @pytest.mark.parametrize("lipid, chains", [
        ("DMPC", {"sn-1 (14:0)", "sn-2 (14:0)"}),
        ("POPC", {"sn-1 (16:0)", "sn-2 (18:1)"}),
        ("DOPC", {"sn-1 (18:1)", "sn-2 (18:1)"}),
        ("POPE", {"sn-1 (16:0)", "sn-2 (18:1)"}),
        ("DLPE", {"sn-1 (12:0)", "sn-2 (12:0)"}),
    ])
    def test_the_chains_are_found_from_the_bonds(self, lipid, chains) -> None:
        table, findings = _run(LipidOrder, _patch(lipid))
        assert set(table["lipid"]) == {lipid}
        assert set(table["chain"]) == chains
        for chain, rows in table.groupby("chain"):
            length = int(chain.split("(")[1].split(":")[0])
            assert sorted(rows["carbon"]) == list(range(2, length + 1))

    def test_a_fluid_bilayer_has_a_plateau_that_falls_to_the_chain_end(self) -> None:
        table, _ = _run(LipidOrder, _patch("DMPC"))
        for _, rows in table.groupby("chain"):
            order = -rows.set_index("carbon")["s_cd"]
            plateau = order.loc[3:8].mean()
            assert 0.12 < plateau < 0.30
            assert order.loc[14] < plateau / 2
            assert (rows["standard_error"] > 0).all()
            assert (rows["lipids"] == rows["lipids"].iloc[0]).all()

    def test_a_lipid_split_into_head_and_tail_residues_is_one_lipid(self) -> None:
        """AMBER's Lipid21 makes each chain a residue of its own, bonded to the
        head: POPC is PA, PC and OL. Joined by the bonds, it is measured as
        the lipid it is, with the same numbers."""
        traj = _patch("POPC")
        split = _split_like_lipid21(traj)
        whole, _ = _run(LipidOrder, traj)
        parts, _ = _run(LipidOrder, split)
        assert set(parts["lipid"]) == {"PA-PC-OL"}
        assert set(parts["chain"]) == {"sn-1 (16:0)", "sn-2 (18:1)"}
        assert np.allclose(parts.sort_values(["chain", "carbon"])["s_cd"].to_numpy(),
                           whole.sort_values(["chain", "carbon"])["s_cd"].to_numpy(),
                           atol=1e-5)
        assert _run(AreaPerLipid, split)[0][0] == pytest.approx(
            _run(AreaPerLipid, traj)[0][0], rel=1e-6)
        assert _run(BilayerThickness, split)[0][0] == pytest.approx(
            _run(BilayerThickness, traj)[0][0], abs=1e-4)

    def test_it_needs_the_hydrogens(self) -> None:
        traj = _patch("DMPC")
        heavy = traj.atom_slice(traj.topology.select("not element H"))
        with pytest.raises(StudyError) as refused:
            _run(LipidOrder, heavy)
        assert refused.value.code == "analysis.data.absent"


class TestCrossSection:
    cell = np.array([[5.0, 0.0], [0.0, 5.0]])

    def test_a_disc(self) -> None:
        assert cross_section(np.array([[2.5, 2.5]]), np.array([1.0]), self.cell) == \
            pytest.approx(np.pi, rel=0.02)

    def test_a_disc_across_the_box_edge_is_counted_once(self) -> None:
        assert cross_section(np.array([[0.1, 4.95]]), np.array([1.0]), self.cell) == \
            pytest.approx(np.pi, rel=0.02)

    @pytest.mark.parametrize("offset", [[0.0, 0.0], [2.4, 2.4]])
    def test_what_a_ring_encloses_is_the_ring_s(self, offset) -> None:
        angle = np.linspace(0, 2 * np.pi, 40, endpoint=False)
        ring = np.c_[2.5 + np.cos(angle), 2.5 + np.sin(angle)] + offset
        radii = np.full(40, 0.17)
        assert cross_section(ring, radii, self.cell) < 2.2
        assert cross_section(ring, radii, self.cell, probe=0.2) == pytest.approx(
            np.pi * 1.17 ** 2, rel=0.03)


class TestTheyRunWhereThereIsABilayer:

    @staticmethod
    def _plan(traj):
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        orchestrator = AnalysisOrchestrator.__new__(AnalysisOrchestrator)
        orchestrator.traj = traj
        orchestrator.ligand_resname = None
        orchestrator.output_dir = Path(tempfile.mkdtemp())
        return orchestrator._build_plan(None, None)

    def test_in_a_bilayer(self) -> None:
        plan = self._plan(_patch("DMPC"))
        assert {"area_per_lipid", "bilayer_thickness", "lipid_order"} <= set(plan)

    def test_not_elsewhere(self) -> None:
        traj = _patch("DMPC")
        water = traj.atom_slice(traj.topology.select("water"))
        plan = self._plan(water)
        assert not {"area_per_lipid", "bilayer_thickness", "lipid_order"} & set(plan)

    def test_they_are_in_the_schema(self) -> None:
        from fastmdxplora.config.schema import ANALYSIS_NAMES

        assert {"area_per_lipid", "bilayer_thickness", "lipid_order"} <= set(ANALYSIS_NAMES)

    def test_a_run_writes_its_data_figure_and_mean(self, tmp_path) -> None:
        traj = _patch("DMPC")
        three = md.join([traj, traj, traj])
        for cls in (AreaPerLipid, BilayerThickness, LipidOrder):
            result = cls(output_dir=tmp_path / cls.name).run(three)
            assert result.status == "ok", result.message
            assert result.data_path.is_file() and result.figure_path.is_file()


def test_each_says_what_it_measures() -> None:
    """The report explains an analysis from its module's opening paragraph,
    so three analyses in one module would all be explained alike."""
    from fastmdxplora.analysis.describe import explain_analysis

    summaries = {name: explain_analysis(name)["summary"]
                 for name in ("area_per_lipid", "bilayer_thickness", "lipid_order")}
    assert len(set(summaries.values())) == 3
    assert summaries["area_per_lipid"].startswith("Area per lipid")
    assert "S_CD" in summaries["lipid_order"]
