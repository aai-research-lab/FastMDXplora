"""The image check's note on an analysis is counted over the frames that
analysis uses: those from its ``start`` (RMSF, ``ligand_rmsf``,
``bfactor_comparison``), the energy rows each thermodynamic mean averages,
and, for an analysis without a main mean, the frames its means use.

An RMSF over frames 149 to 299, none of which came within the cutoff, said
"100 of 300 frames used (33%)" and that its shape may be biased.
"""

from __future__ import annotations

import json

import mdtraj as md
import numpy as np
import pytest

import fastmdxplora.analysis  # noqa: F401 -- registers the analyses


def _beads(n: int = 300, reaching: int = 100, box: float = 3.0,
           every: int | None = None) -> md.Trajectory:
    """Ten alpha carbons bonded in a chain, 10 ps a frame: stretched along x
    (its ends 0.12 nm from each other's image) in the first ``reaching``
    frames (or in every ``every``-th frame), a compact coil otherwise."""
    rng = np.random.default_rng(3)
    top = md.Topology()
    chain = top.add_chain()
    previous = None
    for i in range(10):
        atom = top.add_atom("CA", md.element.carbon,
                            top.add_residue("ALA", chain, resSeq=i + 1))
        if previous is not None:
            top.add_bond(previous, atom)
        previous = atom
    t = np.arange(10) * 1.7
    coil = np.c_[0.25 * np.cos(t), 0.25 * np.sin(t), 0.15 * np.arange(10)]
    straight = np.c_[np.arange(10) * 0.32, np.zeros(10), np.zeros(10)]
    xyz = np.asarray([(straight if (f % every == 0 if every else f < reaching) else coil)
                      + rng.normal(scale=0.01, size=(10, 3)) + 1.0 for f in range(n)])
    # As the loader clocks them: frame i at (i + 1) times the interval.
    traj = md.Trajectory(xyz.astype(np.float32), top, time=(np.arange(n) + 1) * 10.0)
    traj.unitcell_vectors = np.repeat(np.diag([box] * 3)[None], n, axis=0).astype(np.float32)
    return traj


def _orchestrated(folder, traj, include, options=None):
    from fastmdxplora.analysis import AnalysisOrchestrator

    traj[0].save_pdb(str(folder / "top.pdb"))
    traj.save_dcd(str(folder / "traj.dcd"))
    results = AnalysisOrchestrator(str(folder / "traj.dcd"), str(folder / "top.pdb"),
                                   output_dir=folder / "analysis",
                                   saving_interval_ps=10.0).run(include=include,
                                                                options=options or {})
    assert all(r.status == "ok" for r in results.values()), {
        n: r.message for n, r in results.items()}
    return folder / "analysis"


def _findings(folder, name):
    return json.loads((folder / name / "options.json").read_text())["findings"]


class TestFromItsStart:
    def test_rmsf_from_past_the_frames_that_met_it_carries_no_note(self, tmp_path):
        folder = _orchestrated(tmp_path, _beads(), ["rmsf", "rg"],
                               {"rmsf": {"start": 1.5}})
        assert _findings(folder, "image_check")["solute_image"]["frames_under_cutoff"] == 100
        found = _findings(folder, "rmsf")
        assert found["frames"]["first_frame"] > 100
        assert "solute_image" not in found

    def test_rmsf_from_among_them_counts_from_its_start(self, tmp_path):
        folder = _orchestrated(tmp_path, _beads(), ["rmsf"], {"rmsf": {"start": 0.5}})
        found = _findings(folder, "rmsf")
        first = found["frames"]["first_frame"]
        assert 0 < first < 100
        assert found["solute_image"]["frames_used"] == 300 - first
        assert found["solute_image"]["frames_under_cutoff"] == 100 - first

    @pytest.mark.parametrize("name", ["rmsf", "ligand_rmsf", "bfactor_comparison"])
    def test_each_reads_the_frames_its_record_says(self, name):
        """The three set their first frame in `findings.frames` (from
        `starting_frame.first_frame`), and the note reads it there."""
        from fastmdxplora.analysis.image_check import solute_image_record, solute_atoms
        from fastmdxplora.analysis.orchestrator import get_analysis_class

        traj = _beads()
        series, finding = solute_image_record(traj, solute_atoms(traj.topology, None)[0])
        analysis = get_analysis_class(name)(
            **({"ligand_resname": "LIG"} if name == "ligand_rmsf" else {}))
        analysis.solute_image, analysis.solute_image_series = finding, series
        analysis.findings["frames"] = {"first_frame": 149, "n_frames_given": 300}
        assert analysis._image_note_for(traj, None) is None
        analysis.findings["frames"] = {"first_frame": 60, "n_frames_given": 300}
        note = analysis._image_note_for(traj, None)
        assert note["frames_used"] == 240 and note["frames_under_cutoff"] == 40


def _energy(folder, times_ps, columns=None):
    """An OpenMM state record beside the study: its rows at ``times_ps``,
    a temperature and potential energy that settle after the first rows."""
    (folder / "simulation").mkdir(exist_ok=True)
    rng = np.random.default_rng(5)
    lines = ['#"Step","Time (ps)","Potential Energy (kJ/mole)","Temperature (K)"']
    for i, t in enumerate(times_ps):
        drift = 200.0 * np.exp(-i / 3.0)
        lines.append(f"{i + 1},{t},{-1000.0 + drift + rng.normal(scale=5.0)},"
                     f"{300.0 + drift / 10 + rng.normal(scale=2.0)}")
    (folder / "simulation" / "energy.csv").write_text("\n".join(lines) + "\n")


class TestTheThermodynamicsOverItsRows:
    """Its means average the energy record's rows, on the record's own
    interval: a run whose solute met its image only before the rows used
    carries no note; one that did after counts the rows."""

    def test_counted_over_the_rows_each_mean_averages(self, tmp_path):
        traj = _beads()
        # Every 25 ps, frames being 10 ps apart (frames 0 to 299 at 10 ps on).
        times = np.asarray(traj.time, dtype=float)
        _energy(tmp_path, np.arange(times[0], times[-1] + 1, 25.0))
        folder = _orchestrated(tmp_path, traj, ["thermodynamics"])
        found = _findings(folder, "thermodynamics")
        thermo = found["thermodynamics"]
        rows = thermo["samples"]
        reaching = times[100]
        for key in ("potential_energy", "temperature"):
            entry = thermo[key]
            used = np.arange(times[0], times[-1] + 1, 25.0)[entry["discard"]:]
            expected = int(np.count_nonzero(used < reaching - 5.0))
            note = entry.get("solute_image")
            if expected:
                assert note["frames_used"] == rows - entry["discard"]
                assert note["frames_under_cutoff"] == expected
                assert note["counted_over"] == "energy rows"
            else:
                assert note is None
        if "solute_image" in found:
            assert "energy rows used" in found["solute_image"]["said"]
            assert found["solute_image"]["frames_used"] <= rows

    def test_none_where_the_solute_met_it_only_before_the_rows_used(self, tmp_path):
        traj = _beads(reaching=20)
        times = np.asarray(traj.time, dtype=float)
        # Its rows start after the frames that met the image.
        _energy(tmp_path, np.arange(times[60], times[-1] + 1, 30.0))
        folder = _orchestrated(tmp_path, traj, ["thermodynamics"])
        found = _findings(folder, "thermodynamics")
        assert _findings(folder, "image_check")["solute_image"]["frames_under_cutoff"] == 20
        assert "solute_image" not in found
        assert all("solute_image" not in entry for entry in found["thermodynamics"].values()
                   if isinstance(entry, dict))

    def test_without_a_clock_over_every_frame_and_said(self, tmp_path):
        from fastmdxplora.analysis.image_check import solute_atoms, solute_image_record
        from fastmdxplora.analysis.thermodynamics import Thermodynamics

        traj = _beads()
        series, finding = solute_image_record(traj, solute_atoms(traj.topology, None)[0])
        analysis = Thermodynamics()
        analysis.solute_image, analysis.solute_image_series = finding, series
        analysis._row_times = None
        note = analysis._image_note_for(traj, {"units": "K", "discard": 3, "mean": 1.0})
        assert note["frames_used"] == 300 and "no time" in note["said"]


class TestAnAnalysisWithoutAMainMean:
    """The moments of inertia and the secondary structure have no main mean:
    their note is over the frames their means use, never every frame. The
    moments' said "600 of the 3,000 frames used" where each of its three
    means recorded 344 of 2,744."""

    def test_the_moments_say_it_over_their_means_frames(self, tmp_path):
        folder = _orchestrated(tmp_path, _beads(every=5), ["moments_of_inertia"])
        found = _findings(folder, "moments_of_inertia")
        means = [value for value in found.values()
                 if isinstance(value, dict) and "n_frames" in value and "mean" in value]
        assert len(means) == 3
        first = min(m["discard"] for m in means)
        assert first > 0
        note = found["solute_image"]
        assert note["frames_used"] == 300 - first
        assert note["frames_under_cutoff"] == len(range(-(-first // 5) * 5, 300, 5))
        if len({m["discard"] for m in means}) > 1:
            assert "frames its means use" in note["said"] and "union" in note["said"]
        # The record holding only a reason is not a mean, and carries no note.
        assert "solute_image" not in found["mean"]

    def test_means_that_differ_are_said_as_the_union(self):
        from fastmdxplora.analysis.image_check import solute_atoms, solute_image_record
        from fastmdxplora.analysis.orchestrator import get_analysis_class

        traj = _beads()
        series, finding = solute_image_record(traj, solute_atoms(traj.topology, None)[0])
        analysis = get_analysis_class("ss")()
        analysis.solute_image, analysis.solute_image_series = finding, series
        analysis.findings = {
            "helix_fraction": {"mean": 0.2, "n_frames": 300, "discard": 233},
            "strand_fraction": {"mean": 0.1, "n_frames": 300, "discard": 80},
        }
        analysis._qualify_by_the_solute_image(traj)
        note = analysis.findings["solute_image"]
        assert note["frames_used"] == 220 and note["frames_under_cutoff"] == 20
        assert note["counted_over"] == "means' frames"
        assert "frames its means use" in note["said"] and "union" in note["said"]
        # One bracket, the share's: the union said after it.
        assert "(9%), the union of their frames, which differ" in note["said"]
        assert "solute_image" not in analysis.findings["helix_fraction"]
        assert analysis.findings["strand_fraction"]["solute_image"]["frames_used"] == 220

    def test_means_that_agree_are_said_over_their_frames(self):
        from fastmdxplora.analysis.image_check import solute_atoms, solute_image_record
        from fastmdxplora.analysis.orchestrator import get_analysis_class

        traj = _beads()
        series, finding = solute_image_record(traj, solute_atoms(traj.topology, None)[0])
        analysis = get_analysis_class("ss")()
        analysis.solute_image, analysis.solute_image_series = finding, series
        analysis.findings = {
            "helix_fraction": {"mean": 0.2, "n_frames": 300, "discard": 90},
            "strand_fraction": {"mean": 0.1, "n_frames": 300, "discard": 90},
        }
        analysis._qualify_by_the_solute_image(traj)
        note = analysis.findings["solute_image"]
        assert note["frames_used"] == 210 and note["frames_under_cutoff"] == 10
        assert "union" not in note["said"]
