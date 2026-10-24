"""Known joins must reach the authoritative verdict, not just a report fallback."""

from __future__ import annotations

import json

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.base import Analysis, carry_wholeness
from fastmdxplora.analysis import loading
from fastmdxplora.analysis.loading import load_trajectory
from fastmdxplora.refusals import refusal_of
from fastmdxplora.statistics import mean_record

# Keep the regression tests collectible against the parent, which did not
# expose the marker yet, so they fail on behavior instead of at import.
SEGMENT_JOINS = getattr(loading, "SEGMENT_JOINS", "_fastmdxplora_segment_joins")


def _trajectory(folder, values, joins=()):
    folder.mkdir(parents=True, exist_ok=True)
    top = md.Topology()
    residue = top.add_residue("ALA", top.add_chain())
    top.add_atom("CA", md.element.carbon, residue)
    xyz = np.zeros((len(values), 1, 3), dtype=np.float32)
    xyz[:, 0, 0] = values
    trajectory = md.Trajectory(xyz, top, time=np.arange(len(values), dtype=float))
    path = folder / "production.xtc"
    trajectory.save_xtc(str(path))
    trajectory[0].save_pdb(str(folder / "production.pdb"))
    if joins:
        path.with_suffix(".xtc.join.json").write_text(json.dumps({"joins": joins}))
    return path


class _Coordinate(Analysis):
    name = "rg"  # A report-supported structural series.
    time_series = True

    def compute(self, traj):
        return traj.xyz[:, 0, 0].astype(float)

    def plot(self, result, ax):
        ax.plot(result)


@pytest.mark.parametrize("count", [3, 4])
def test_loading_analysis_manifest_and_report_share_the_drift_refusal(tmp_path, count):
    from fastmdxplora.report.document import _convergence_section
    from tests.test_statistics import _segments

    stationary, _ = _segments([0.0], frames=4000, seed=18)
    values = np.concatenate([10 + stationary + 2 * i for i in range(count)])
    joins = [4000 * i for i in range(1, count)]
    path = _trajectory(tmp_path / "simulation", values, joins)
    loaded = load_trajectory(path)
    # The orchestrator gives each analysis a copy made this way.
    copied = carry_wholeness(loaded, loaded[:])
    analysis = _Coordinate(output_dir=tmp_path / "analysis")
    result = analysis.run(copied)
    assert result.status == "ok", result.message
    record = analysis.findings["mean"]
    assert refusal_of(record["not_a_measurement"]).code == "analysis.sampling.drifting"
    assert np.isnan(record["standard_error"])
    assert record["segment_joins"] == joins
    assert record["drift_test"] == "weighted_slope"
    saved = json.loads(result.options_path.read_text())["findings"]["mean"]
    assert saved["segment_joins"] == joins and saved["not_a_measurement"]
    section = _convergence_section(tmp_path)
    row = next(line for line in section.splitlines() if line.startswith("| Rg |"))
    assert row.split("|")[6].strip() == "not determined", row


@pytest.mark.parametrize("stride,first,last,expected", [
    (None, None, None, (7, 17, 25)),
    (3, None, None, (3, 6, 9)),
    (3, 3, 9, (3,)),
    (3, 6, 10, (3,)),
    (3, 0, 3, ()),
])
def test_joins_follow_the_actual_loaded_frames(tmp_path, stride, first, last, expected):
    path = _trajectory(tmp_path, np.arange(30), [7, 17, 25])
    loaded = load_trajectory(path, stride=stride, first=first, last=last)
    assert getattr(loaded, SEGMENT_JOINS) == expected


def test_each_files_stride_is_mapped_before_concatenation(tmp_path):
    first = _trajectory(tmp_path / "a", np.arange(8), [5])
    second = _trajectory(tmp_path / "b", np.arange(11), [4, 8])
    loaded = load_trajectory([first, second], top=first.with_suffix(".pdb"), stride=3)
    assert getattr(loaded, SEGMENT_JOINS) == (2, 5, 6)


def test_an_unjoined_series_keeps_its_existing_estimate():
    from tests.test_statistics import _segments

    values, _ = _segments([0.0], frames=4000, seed=18)
    plain = mean_record(values)
    assert plain == mean_record(values, joins=[])
    joined = mean_record(np.tile(values, 3), joins=[4000, 8000])
    assert "not_a_measurement" not in joined
    without_guard = mean_record(np.tile(values, 3))
    assert joined["mean"] == without_guard["mean"]
    assert joined["standard_error"] == without_guard["standard_error"]


def test_state_joins_count_trimmed_nonmissing_samples_in_each_column(tmp_path):
    from fastmdxplora.analysis.thermodynamics import read_pieces_state_table

    pieces = []
    for i in range(3):
        path = tmp_path / f"energy-{i}.csv"
        path.write_text('#"Step","Time (ps)","Temperature (K)"\n'
                        '1,1,300\n2,2,\n3,3,302\n4,4,999\n')
        pieces.append({"path": path, "last_step": 3,
                       "steps_before": 3 * i, "ps_before": 3 * i})
    joins = {}
    table = read_pieces_state_table(pieces, joins=joins)
    assert table["Temperature (K)"].tolist() == [300, 302] * 3
    assert joins["Temperature (K)"] == [2, 4]
    assert joins["Time (ps)"] == [3, 6]


@pytest.mark.parametrize("count", [3, 4])
def test_thermodynamic_means_use_the_state_records_own_joins(tmp_path, monkeypatch, count):
    from fastmdxplora.analysis import thermodynamics as module
    from tests.test_statistics import _segments

    stationary, _ = _segments([0.0], frames=4000, seed=18)
    pieces = []
    for i in range(count):
        folder = tmp_path / f"piece-{i}" / "simulation"
        folder.mkdir(parents=True)
        path = folder / "energy.csv"
        values = 300 + stationary + 2 * i
        path.write_text('#"Step","Time (ps)","Temperature (K)"\n' +
                        "".join(f"{j},{j},{value}\n" for j, value in enumerate(values)))
        pieces.append({"path": path, "last_step": None,
                       "steps_before": 4000 * i, "ps_before": 4000 * i})
    monkeypatch.setattr(module, "state_record_pieces", lambda _: pieces)
    analysis = module.Thermodynamics()
    monkeypatch.setattr(analysis, "_state_path", lambda: pieces[0]["path"])
    result = analysis.compute(None)
    record = analysis.findings["thermodynamics"]["temperature"]
    assert refusal_of(record["not_a_measurement"]).code == "analysis.sampling.drifting"
    assert record["segment_joins"] == [4000 * i for i in range(1, count)]
    assert np.isnan(result[0, 1])


def test_a_join_sidecar_outside_a_held_study_is_not_read(tmp_path):
    from fastmdxplora.analysis.joining import joins_beside
    from fastmdxplora.record_access import confined_records
    from fastmdxplora.refusals import OutsideWorkspace

    root = tmp_path / "study"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"joins": [10, 20]}')
    trajectory = root / "production.xtc"
    trajectory.with_suffix(".xtc.join.json").symlink_to(outside)
    with confined_records(root, lambda path: path.is_relative_to(root)):
        with pytest.raises(OutsideWorkspace):
            joins_beside(trajectory)


def test_sharing_a_start_does_not_erase_a_joined_drift_refusal(tmp_path, monkeypatch):
    from fastmdxplora.simulation.stopping import share_the_start
    from tests.test_statistics import _segments

    stationary, _ = _segments([0.0], frames=4000, seed=18)
    values = np.concatenate([stationary + 2 * i for i in range(3)])
    runs = [tmp_path / "first", tmp_path / "second"]
    for run in runs:
        folder = run / "analysis" / "rmsd"
        folder.mkdir(parents=True)
        np.savetxt(folder / "rmsd.dat", values)
        record = mean_record(values, joins=[4000, 8000])
        (folder / "options.json").write_text(json.dumps(
            {"findings": {"mean": record}}, default=str))
    monkeypatch.setattr("fastmdxplora.statistics.shared_start", lambda _: len(values) - 1)
    share_the_start(runs, "rmsd")
    for run in runs:
        record = json.loads((run / "analysis/rmsd/options.json").read_text())["findings"]["mean"]
        assert record["segment_joins"] == [4000, 8000]
        assert record["not_a_measurement"] and np.isnan(record["standard_error"])
        assert record["drift_test"] == "weighted_slope"


@pytest.mark.parametrize("name", ["sasa", "ss", "moments_of_inertia"])
def test_component_means_keep_the_join_guard_after_atom_selection(monkeypatch, name):
    from importlib import import_module
    from tests.test_statistics import _segments

    module = import_module(f"fastmdxplora.analysis.{name}")
    stationary, _ = _segments([0.0], frames=4000, seed=18)
    values = np.concatenate([10 + stationary + 2 * i for i in range(3)])
    top = md.Topology()
    chain = top.add_chain()
    for i in range(100):
        residue = top.add_residue("ALA", chain, resSeq=i + 1)
        top.add_atom("CA", md.element.carbon, residue)
    top.add_atom("O", md.element.oxygen, top.add_residue("HOH", top.add_chain()))
    trajectory = md.Trajectory(np.zeros((len(values), 101, 3), dtype=np.float32), top)
    setattr(trajectory, SEGMENT_JOINS, (4000, 8000))
    if name == "sasa":
        def areas(selected, **kwargs):
            assert selected.n_atoms == 100  # The water was selected away.
            return np.tile(values[:, None] / 100, (1, 100))
        monkeypatch.setattr(module.md, "shrake_rupley", areas)
        analysis = module.SASA(mode="total")
        analysis.compute(trajectory)
        key = "hydrophobic_sasa"
    elif name == "ss":
        counts = np.rint(20 + 2 * np.tile(stationary, 3) +
                         np.repeat([0, 20, 40], 4000)).astype(int)
        codes = np.where(np.arange(100)[None, :] < counts[:, None], "H", "C")
        def dssp(selected, **kwargs):
            assert selected.n_atoms == 100
            return codes
        monkeypatch.setattr(module.md, "compute_dssp", dssp)
        analysis = module.SS(selection="protein")
        analysis.compute(trajectory)
        key = "helix_fraction"
    else:
        analysis = module.MomentsOfInertia()
        analysis._shape = np.tile(values[:, None], (1, len(module.SHAPE_DESCRIPTORS)))
        analysis._record_what_the_mean_is_worth(trajectory)
        key = module.SHAPE_DESCRIPTORS[0][0]
    record = analysis.findings[key]
    assert refusal_of(record["not_a_measurement"]).code == "analysis.sampling.drifting"
    assert np.isnan(record["standard_error"])
    assert record["segment_joins"] == [4000, 8000]
