"""The comparison keeps its reference basis and every selected frame."""

from __future__ import annotations

from pathlib import Path

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.dimred import DimRed
from fastmdxplora.refusals import StudyError


def _ensemble(offset: float = 0.0, *, seed: int = 71, n: int = 14) -> md.Trajectory:
    rng = np.random.default_rng(seed)
    shape = rng.normal(size=(8, 3)).astype(np.float32)
    shape -= shape.mean(axis=0)
    first = rng.normal(size=(8, 3)).astype(np.float32)
    first -= first.mean(axis=0)
    first /= np.linalg.norm(first)
    second = rng.normal(size=(8, 3)).astype(np.float32)
    second -= second.mean(axis=0)
    second -= np.sum(first * second) * first
    second /= np.linalg.norm(second)
    frames = [shape + (q + offset) * first + r * second
              for q, r in zip(np.linspace(-0.5, 0.5, n), np.sin(np.arange(n)) * 0.2)]
    topology = md.Topology()
    chain = topology.add_chain("A")
    for i in range(8):
        topology.add_atom("CA", md.element.carbon,
                          topology.add_residue("ALA", chain, resSeq=i + 1))
    return md.Trajectory(np.asarray(frames, dtype=np.float32), topology)


def _files(tmp_path, offset: float = 1.0):
    reference = _ensemble()
    target = _ensemble(offset)
    for frame in range(target.n_frames):
        angle = 0.11 * frame
        rotation = np.array([[np.cos(angle), -np.sin(angle), 0],
                             [np.sin(angle), np.cos(angle), 0],
                             [0, 0, 1]], dtype=np.float32)
        target.xyz[frame] = target.xyz[frame] @ rotation + np.array(
            [0.3 * frame, -0.2, 0.1], dtype=np.float32)
    top = tmp_path / "target.pdb"
    reference[0].save_pdb(str(top))
    trajectory = tmp_path / "target.dcd"
    target.save_dcd(str(trajectory))
    return reference, trajectory, top


def _analysis(trajectory, topology, *, output_dir=None, targets=None,
              selection="all"):
    return DimRed(
        methods=["pca"], n_components=2, selection=selection, landscape_bins=12,
        output_dir=output_dir,
        comparison={"reference": {"label": "WT", "temperature_K": 300,
                                   "sampling": "unbiased"},
                    "targets": targets or [{"label": "bound", "trajectory": str(trajectory),
                                            "topology": str(topology),
                                            "temperature_K": 310, "sampling": "biased"}]},
    )


def test_the_shift_survives_projection_on_the_reference_mean(tmp_path):
    reference, trajectory, topology = _files(tmp_path, 1.0)
    analysis = _analysis(trajectory, topology)

    result = analysis.compute(reference)

    assert len(result["projections"]) == 2
    difference = result["projections"][1].mean(axis=0) - result["projections"][0].mean(axis=0)
    assert abs(difference[0]) > 0.6
    assert np.allclose(result["projections"][0].mean(axis=0), 0, atol=2e-6)
    assert not (tmp_path / "dimred").exists()  # compute has no artifact side effects


def test_rigidly_aligned_shift_and_union_edges_are_written(tmp_path, monkeypatch):
    import fastmdxplora.analysis.common_landscapes as common

    reference, trajectory, topology = _files(tmp_path, 4.0)
    analysis = _analysis(trajectory, topology, output_dir=tmp_path / "out")
    analysis.output_dir.mkdir(parents=True)
    old_default = analysis.output_dir / "dimred_pca_landscape.png"
    old_default.write_bytes(b"standalone PCA output")
    box = np.repeat((np.eye(3, dtype=np.float32) * 4.0)[None, :, :],
                    reference.n_frames, axis=0)
    reference.unitcell_vectors = box.copy()
    original_xyz = reference.xyz.copy()
    figure_text = []
    save_figure = common.save_figure

    def inspect_figure(fig, path):
        fig.canvas.draw()
        figure_text.extend([axis.get_ylabel() for axis in fig.axes])
        figure_text.extend([axis.get_xlabel() for axis in fig.axes])
        figure_text.extend([axis.get_title() for axis in fig.axes])
        figure_text.extend(text.get_text() for axis in fig.axes for text in axis.texts)
        figure_text.extend([text.get_text() for text in fig.texts])
        assert np.asarray(fig.canvas.buffer_rgba()).std() > 0
        return save_figure(fig, path)

    monkeypatch.setattr(common, "save_figure", inspect_figure)

    result = analysis.run(reference)

    assert result.status == "ok", result.message
    assert np.array_equal(reference.xyz, original_xyz)
    assert np.array_equal(reference.unitcell_vectors, box)
    assert old_default.read_bytes() == b"standalone PCA output"
    folder = tmp_path / "out" / "dimred"
    assert result.figure_path == folder / "common_pca_landscapes.png"
    assert {path.name for path in result.artifacts} == {
        "options.json", "common_pca_model.npz", "common_pca_projections.dat",
        "common_pca_landscapes.npz", "common_pca_landscapes.png",
        "common_pca_landscapes.svg",
    }
    with np.load(folder / "common_pca_model.npz", allow_pickle=False) as model:
        assert model["schema_version"] == 1
        assert model["components"].shape == (2, 8, 3)
        assert model["units"] == "nm"
        assert model["atom_keys"].dtype.kind == "U"
        assert model["model_id"].item() == analysis.findings["comparison"]["model_id"]
        assert all(model[name].dtype.kind != "O" for name in model.files)
        selected = model["selected_indices"]
        alignment = reference[0]
        alignment.xyz[0, selected] = model["alignment_xyz"]
        aligned = common.superposed(reference, reference=alignment,
                                    atom_indices=selected, ref_atom_indices=selected)
        coordinates = aligned.xyz[:, selected].astype(np.float64).reshape(reference.n_frames, -1)
        reconstructed = ((coordinates - model["mean"].ravel())
                         @ model["components"].reshape(2, -1).T)
        assert np.allclose(reconstructed, analysis.result["projections"][0], atol=2e-5)
    with np.load(folder / "common_pca_landscapes.npz", allow_pickle=False) as grids:
        assert grids["counts"].shape == (2, 12, 12)
        assert grids["counts"].sum(axis=(1, 2)).tolist() == [14, 14]
        assert np.allclose(grids["probability"].sum(axis=(1, 2)), 1)
        area = np.outer(np.diff(grids["edges_pc1"]), np.diff(grids["edges_pc2"]))
        assert np.allclose((grids["density"] * area).sum(axis=(1, 2)), 1)
        assert grids["unit"] == "dimensionless"
        assert all(grids[name].dtype.kind != "O" for name in grids.files)
        for points, counts in zip(analysis.result["projections"], grids["counts"]):
            histogram, _, _ = np.histogram2d(
                points[:, 0], points[:, 1], bins=(grids["edges_pc1"], grids["edges_pc2"]))
            assert np.array_equal(histogram, counts)
    table = (folder / "common_pca_projections.dat").read_text(encoding="utf-8")
    assert "source_frame" in table and ",bound,0,0," in table
    assert "equilibrium and convergence are not established" in str(
        analysis.findings["comparison"]["qualifications"])
    assert "different user-declared temperatures" in str(
        analysis.findings["comparison"]["qualifications"])
    assert "biased occupancy" in str(analysis.findings["comparison"]["qualifications"]).lower()
    assert any("Reduced empirical occupancy landscape (dimensionless)" in text
               for text in figure_text)
    assert any("equilibrium and convergence are not established" in text.lower()
               for text in figure_text)
    assert analysis.findings["comparison"]["histogrammed_frames"] == [14, 14]
    assert analysis.findings["comparison"]["temperature_source"] == ["user", "user"]
    assert analysis.findings["comparison"]["sampling_source"] == ["user", "user"]
    assert analysis.findings["comparison"]["reference_source_frames"] == "unknown"


def test_target_order_keeps_model_and_per_label_projection(tmp_path):
    reference = _ensemble()
    first_dir, second_dir = tmp_path / "a", tmp_path / "b"
    first_dir.mkdir()
    second_dir.mkdir()
    _, first_path, top = _files(first_dir, 0.8)
    _, second_path, top2 = _files(second_dir, -0.6)
    first = {"label": "positive", "trajectory": str(first_path), "topology": str(top),
             "first": 1, "last": 7, "stride": 2}
    second = {"label": "negative", "trajectory": str(second_path), "topology": str(top2)}

    forward = _analysis(first_path, top, targets=[first, second]).compute(reference)
    reverse = _analysis(first_path, top, targets=[second, first]).compute(reference)

    assert forward["model_id"] == reverse["model_id"]
    all_x = np.concatenate([points[:, 0] for points in forward["projections"]])
    ref_x = forward["projections"][0][:, 0]
    assert forward["edges_x"][0] <= all_x.min()
    assert forward["edges_x"][-1] >= all_x.max()
    assert all_x.min() < ref_x.min() or all_x.max() > ref_x.max()
    positive_index = list(forward["labels"]).index("positive")
    assert forward["source_frames"][positive_index].tolist() == [2, 4, 6, 8, 10, 12]
    for label in ("positive", "negative"):
        a = forward["projections"][list(forward["labels"]).index(label)]
        b = reverse["projections"][list(reverse["labels"]).index(label)]
        assert np.allclose(a, b, atol=2e-5)


@pytest.mark.parametrize("change", ("atom", "element", "residue", "chain", "number",
                                    "order", "missing", "duplicate"))
def test_selected_atom_identity_mismatches_are_refused(tmp_path, change):
    folder = tmp_path / change
    folder.mkdir()
    reference, trajectory, topology = _files(folder)
    lines = topology.read_text().splitlines()
    atom_rows = [i for i, line in enumerate(lines) if line.startswith("ATOM")]
    first = lines[atom_rows[0]]
    if change == "atom":
        lines[atom_rows[0]] = first[:12] + " CB " + first[16:]
    elif change == "element":
        lines[atom_rows[0]] = first[:76] + "N " + first[78:]
    elif change == "residue":
        lines[atom_rows[0]] = first[:17] + "GLY" + first[20:]
    elif change == "chain":
        lines[atom_rows[0]] = first[:21] + "B" + first[22:]
    elif change == "number":
        lines[atom_rows[0]] = first[:22] + " 99 " + first[26:]
    elif change == "order":
        for row, value in zip(atom_rows, reversed([lines[i] for i in atom_rows])):
            lines[row] = value
    elif change == "missing":
        lines.pop(atom_rows[0])
    elif change == "duplicate":
        second = lines[atom_rows[1]]
        lines[atom_rows[1]] = second[:12] + first[12:26] + second[26:]
    changed_top = folder / "changed.pdb"
    changed_top.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if change == "missing":
        loaded = md.load(str(trajectory), top=str(topology))
        short = md.Trajectory(loaded.xyz[:, 1:].copy(), md.load_topology(str(changed_top)))
        trajectory = folder / "short.dcd"
        short.save_dcd(str(trajectory))
    with pytest.raises(StudyError, match="identit"):
        _analysis(trajectory, changed_top).compute(reference)


def test_a_mutated_residue_can_be_excluded_by_the_shared_selection(tmp_path):
    reference, trajectory, topology = _files(tmp_path)
    lines = topology.read_text().splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("ATOM"))
    lines[first] = lines[first][:17] + "GLY" + lines[first][20:]
    changed_top = tmp_path / "mutated.pdb"
    changed_top.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = _analysis(
        trajectory, changed_top, selection="protein and not resid 0").compute(reference)

    assert len(result["projections"]) == 2
    assert result["selected_indices"].tolist() == list(range(1, 8))
    collinear = md.load(str(trajectory), top=str(changed_top))
    collinear.xyz[:, :, 1:] = 0
    line_path = tmp_path / "line.dcd"
    collinear.save_dcd(str(line_path))
    with pytest.raises(StudyError, match="collinear"):
        _analysis(line_path, changed_top, selection="protein and not resid 0").compute(reference)


def test_a_static_target_keeps_its_reference_basis_position(tmp_path):
    reference, _, topology = _files(tmp_path)
    static = _ensemble(1.5)[0]
    trajectory = tmp_path / "static.dcd"
    static.save_dcd(str(trajectory))

    result = _analysis(trajectory, topology).compute(reference)

    points = result["projections"][1]
    assert points.shape == (1, 2)
    assert np.linalg.norm(points[0]) > 0.5
    shape = reference.xyz[0]
    centre = shape.mean(axis=0)
    frames = [centre + scale * (shape - centre) for scale in (0.9, 1.0, 1.1)]
    static_reference = md.Trajectory(np.asarray(frames, dtype=np.float32), reference.topology)
    with pytest.raises(StudyError, match="zero variance on PC2"):
        _analysis(trajectory, topology).compute(static_reference)


def test_rigid_transform_only_does_not_change_shared_projections(tmp_path):
    reference, trajectory, topology = _files(tmp_path, 0.0)

    result = _analysis(trajectory, topology).compute(reference)

    assert np.allclose(result["projections"][0], result["projections"][1], atol=2e-5)
    first, second = result["landscapes"]
    assert np.array_equal(first["counts"], second["counts"])
    assert np.allclose(first["density"], second["density"])
    assert np.array_equal(np.isnan(first["free_energy"]), np.isnan(second["free_energy"]))
    assert np.allclose(first["free_energy"], second["free_energy"], equal_nan=True)


def test_an_unselected_ligand_does_not_change_protein_identity(tmp_path):
    reference = _ensemble()
    protein = _ensemble(0.7)
    topology = md.Topology()
    chain = topology.add_chain("A")
    for i in range(8):
        topology.add_atom("CA", md.element.carbon,
                          topology.add_residue("ALA", chain, resSeq=i + 1))
    ligand = topology.add_residue("LIG", chain, resSeq=100)
    topology.add_atom("C1", md.element.carbon, ligand)
    ligand_xyz = np.zeros((protein.n_frames, 1, 3), dtype=np.float32)
    ligand_xyz[:, 0, :] = np.array([2.0, 2.0, 2.0], dtype=np.float32)
    target = md.Trajectory(np.concatenate((protein.xyz, ligand_xyz), axis=1), topology)
    top_path, traj_path = tmp_path / "with_ligand.pdb", tmp_path / "with_ligand.dcd"
    target[0].save_pdb(str(top_path))
    target.save_dcd(str(traj_path))

    result = _analysis(traj_path, top_path, selection="protein and name CA").compute(reference)

    assert len(result["projections"]) == 2
    assert result["selected_indices"].tolist() == list(range(8))


def test_low_room_refuses_before_the_target_trajectory_is_loaded(tmp_path, monkeypatch):
    import fastmdxplora.analysis.cluster as cluster
    import fastmdxplora.analysis.common_landscapes as common

    reference, trajectory, topology = _files(tmp_path)
    load = common.load_trajectory
    monkeypatch.setattr(cluster, "memory_available", lambda: (1, "test low room"))
    monkeypatch.setattr(cluster, "resident_room", lambda *_: (1, "test low room"))
    monkeypatch.setattr(cluster, "address_room", lambda *_: (1, "test low room"))
    monkeypatch.setattr(common, "load_trajectory",
                        lambda *args, **kwargs: pytest.fail("target allocated before refusal"))

    with pytest.raises(StudyError, match="above the 1-byte"):
        _analysis(trajectory, topology).compute(reference)
    monkeypatch.setattr(cluster, "memory_available", lambda: (10**9, "test"))
    monkeypatch.setattr(cluster, "resident_room", lambda *_: (10**9, "test"))
    monkeypatch.setattr(cluster, "address_room", lambda *_: (10**9, "test"))

    def allocation_failure(*args, **kwargs):
        raise MemoryError("target allocation")

    monkeypatch.setattr(common, "load_trajectory", allocation_failure)
    result = _analysis(trajectory, topology, output_dir=tmp_path / "allocation").run(reference)
    assert result.status == "error" and result.figure_path is None
    monkeypatch.setattr(common, "load_trajectory", load)
    analysis = _analysis(trajectory, topology, output_dir=tmp_path / "figure")
    monkeypatch.setattr(analysis, "_plotted", lambda *args, **kwargs: [])
    figure_result = analysis.run(reference)
    assert figure_result.status == "ok" and figure_result.figure_path is None
    assert figure_result.data_path.is_file()
    assert analysis.findings["comparison"]["figure_status"] == "unavailable: memory refusal"


def test_memory_estimate_includes_previously_retained_projections(tmp_path, monkeypatch):
    import fastmdxplora.analysis.cluster as cluster
    import fastmdxplora.analysis.common_landscapes as common

    reference, trajectory, topology = _files(tmp_path)
    analysis = _analysis(trajectory, topology)
    monkeypatch.setattr(cluster, "memory_available", lambda: (None, "test"))
    monkeypatch.setattr(cluster, "resident_room", lambda *_: (10**9, "test"))
    monkeypatch.setattr(cluster, "address_room", lambda *_: (10**9, "test"))
    estimate = common._memory_preflight(reference, 1, 8, 8, 12, "target", analysis,
                                        retained_frames=reference.n_frames)
    monkeypatch.setattr(cluster, "resident_room",
                        lambda *_: (estimate["estimate_bytes"] + 5000, "test"))
    with pytest.raises(StudyError, match="above the"):
        common._memory_preflight(reference, 1, 8, 8, 12, "target", analysis,
                                 retained_frames=1000)


def test_unknown_frame_count_refuses_before_loading(tmp_path, monkeypatch):
    import fastmdxplora.analysis.common_landscapes as common

    reference, trajectory, topology = _files(tmp_path)
    monkeypatch.setattr(common, "_frames_in", lambda *args: None)
    monkeypatch.setattr(common, "load_trajectory",
                        lambda *args, **kwargs: pytest.fail("unknown-size target was loaded"))

    with pytest.raises(StudyError, match="Cannot safely count frames"):
        _analysis(trajectory, topology).compute(reference)


def test_a_target_changed_during_loading_is_refused(tmp_path, monkeypatch):
    import fastmdxplora.analysis.common_landscapes as common

    reference, trajectory, topology = _files(tmp_path)
    load = common.load_trajectory

    def change_after_load(*args, **kwargs):
        loaded = load(*args, **kwargs)
        Path(args[0]).write_bytes(Path(args[0]).read_bytes() + b"changed")
        return loaded

    monkeypatch.setattr(common, "load_trajectory", change_after_load)
    with pytest.raises(StudyError, match="changed while"):
        _analysis(trajectory, topology).compute(reference)


def test_a_failed_rerun_does_not_advertise_an_old_comparison_figure(tmp_path):
    reference, trajectory, topology = _files(tmp_path)
    analysis = _analysis(trajectory, topology, output_dir=tmp_path / "out")
    first = analysis.run(reference)
    assert first.status == "ok"
    analysis.comparison["targets"][0]["trajectory"] = str(tmp_path / "missing.dcd")

    second = analysis.run(reference)

    assert second.status == "error"
    assert second.figure_path is None
    assert not (tmp_path / "out" / "dimred" / "common_pca_landscapes.png").exists()
    record = __import__("json").loads(second.options_path.read_text(encoding="utf-8"))
    assert "comparison" not in record["findings"]


def test_comparison_schema_rejects_wrong_method_and_unknown_fields(tmp_path):
    with pytest.raises(StudyError, match="requires methods"):
        DimRed(methods=["mds"], comparison={"reference": {"label": "WT"}, "targets": []})
    reference, trajectory, topology = _files(tmp_path)
    analysis = _analysis(trajectory, topology)
    analysis.comparison["targets"][0]["model_file"] = "untrusted.pkl"
    with pytest.raises(StudyError, match="unknown keys"):
        analysis.compute(reference)
