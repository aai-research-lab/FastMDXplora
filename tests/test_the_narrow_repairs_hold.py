"""Batch 4 of the 2026-09-04 audit: eight small things, each independent.

Nothing here is a large mechanism. What they share is that each one was a
decision taken correctly in one place and not carried to the next: the
clustering module's pairwise-RMSD helper takes an atom selection and the
dimensionality-reduction module's copy did not; `superposed()` fixed a whole
class of bug and `explore()` still accumulated its results; `describe_pmf`
was written to stop a barrier being read across ground no window visited and
`closure_gap` beside it read across ground no coordinate can occupy.
"""

from __future__ import annotations

import mdtraj as md
import numpy as np
import pytest


def _states_plus_solvent(seed: int = 0):
    """Twelve CA atoms in two conformational states, plus diffusing water.

    The contrast between states is large on the CA atoms and swamped by the
    solvent, which is what makes this the fixture the MDS bug needed and the
    solvent-free peptide in the existing test could not be.
    """
    rng = np.random.RandomState(seed)
    top = md.Topology()
    chain = top.add_chain()
    for _ in range(12):
        residue = top.add_residue("ALA", chain)
        top.add_atom("CA", md.element.carbon, residue)
    for _ in range(200):
        water = top.add_residue("HOH", chain)
        top.add_atom("O", md.element.oxygen, water)

    along = np.linspace(0.0, 1.1, 12)
    frames = []
    for frame in range(20):
        # A hinge, not a translation: `md.rmsd` superposes before measuring,
        # so a rigidly shifted copy of the same shape reads as identical and
        # would have made this fixture a fixture of nothing.
        bend = 0.0 if frame < 10 else 0.9
        state = np.stack(
            [along, bend * np.clip(along - 0.55, 0, None) ** 2 * 4.0,
             np.zeros_like(along)], axis=1)
        state = state + rng.normal(0, 0.01, state.shape)
        solvent = rng.uniform(0, 4.0, (200, 3))
        frames.append(np.vstack([state, solvent]))
    return md.Trajectory(np.array(frames, dtype=np.float32), top)


class TestDimensionalityReductionMeasuresTheSelection:
    """AUD13. `cluster._pairwise_rmsd` takes `atom_idx`; dimred's did not."""

    def test_the_helper_takes_a_selection(self) -> None:
        import inspect

        from fastmdxplora.analysis.dimred import _pairwise_rmsd

        assert "atom_indices" in inspect.signature(_pairwise_rmsd).parameters

    def test_the_selection_is_what_separates_the_states(self) -> None:
        from fastmdxplora.analysis.dimred import _pairwise_rmsd

        traj = _states_plus_solvent()
        alpha = traj.topology.select("name CA")

        selected = _pairwise_rmsd(traj, alpha)
        everything = _pairwise_rmsd(traj)

        def contrast(matrix):
            between = matrix[:10, 10:].mean()
            within = (matrix[:10, :10].mean() + matrix[10:, 10:].mean()) / 2
            return between / max(within, 1e-9)

        assert contrast(selected) > 5.0
        assert contrast(everything) < 2.0

    def test_mds_and_pca_no_longer_disagree(self) -> None:
        """The same call resolved the transition with PCA and hid it with
        MDS, which is how a difference of this size stays invisible: one of
        the two methods always looked right."""
        from fastmdxplora.analysis.dimred import DimRed

        traj = _states_plus_solvent()

        def separated(method):
            embedding = DimRed(
                methods=[method], selection="name CA").compute(traj)[method]
            first, second = embedding[:10], embedding[10:]
            gap = float(np.linalg.norm(first.mean(0) - second.mean(0)))
            spread = float(np.std(first) + np.std(second))
            return gap > spread

        assert separated("pca")
        assert separated("mds")


class TestEveryHistidineKeepsItsChemistry:
    """AUD9. An imidazole is aromatic whatever the force field calls it."""

    @pytest.mark.parametrize(
        "resname", ["HIS", "HIE", "HID", "HIP", "HSD", "HSE", "HSP"])
    def test_it_has_a_ring(self, resname: str) -> None:
        from fastmdxplora.analysis.interactions import _AROMATIC_RINGS

        assert resname in _AROMATIC_RINGS

    @pytest.mark.parametrize("resname", ["HIP", "HSP"])
    def test_the_doubly_protonated_ones_are_positive(self, resname) -> None:
        """The residue name *is* the setup phase's protonation decision, so
        leaving them out did not defer to it -- it discarded it."""
        from fastmdxplora.analysis.interactions import _POSITIVE_GROUPS

        assert resname in _POSITIVE_GROUPS

    @pytest.mark.parametrize("resname", ["HIS", "HIE", "HID", "HSD", "HSE"])
    def test_the_singly_protonated_ones_are_not(self, resname) -> None:
        """Still deferred to the setup phase, as the table's docstring says."""
        from fastmdxplora.analysis.interactions import _POSITIVE_GROUPS

        assert resname not in _POSITIVE_GROUPS

    @pytest.mark.parametrize("resname", ["HIE", "HID", "HIP"])
    def test_a_ring_is_found_on_a_real_topology(self, resname: str) -> None:
        from fastmdxplora.analysis.interactions import protein_aromatic_rings

        top = md.Topology()
        residue = top.add_residue(resname, top.add_chain())
        for name in ("CG", "ND1", "CD2", "CE1", "NE2"):
            element = md.element.nitrogen if name.startswith("N") else md.element.carbon
            top.add_atom(name, element, residue)

        rings = protein_aromatic_rings(top, range(top.n_atoms))
        assert len(rings) == 1 and len(rings[0]) == 5

    def test_they_are_no_longer_called_uninteresting(self) -> None:
        """They were on the list of residues with "nothing to contribute",
        so an unexamined histidine was not reported as unexamined either."""
        from fastmdxplora.analysis.interactions import residues_not_covered

        top = md.Topology()
        residue = top.add_residue("HIE", top.add_chain())
        for name in ("CG", "ND1", "CD2", "CE1", "NE2"):
            element = md.element.nitrogen if name.startswith("N") else md.element.carbon
            top.add_atom(name, element, residue)

        # Covered because it is in the ring table, not because it was
        # declared dull.
        assert residues_not_covered(top, range(top.n_atoms)) == {}


class TestASelectionThatDropsProteinSaysSo:
    """AUD9's other half: MDTraj's `protein` excludes HIE, HID and HSP."""

    def _peptide(self, middle: str):
        top = md.Topology()
        chain = top.add_chain()
        for name in ("ALA", middle, "ALA"):
            residue = top.add_residue(name, chain)
            for atom, element in (("N", "N"), ("CA", "C"),
                                  ("C", "C"), ("O", "O")):
                top.add_atom(atom, md.element.get_by_symbol(element), residue)
        xyz = np.zeros((2, top.n_atoms, 3), dtype=np.float32)
        xyz[:] = np.arange(top.n_atoms)[None, :, None] * 0.15
        return md.Trajectory(xyz, top)

    @pytest.mark.parametrize("resname", ["HIE", "HID"])
    def test_the_finding_names_the_residue(self, resname: str) -> None:
        from fastmdxplora.analysis.sasa import SASA

        analysis = SASA()
        analysis.select_atoms(self._peptide(resname))

        note = analysis.findings.get("selection_dropped_residues")
        assert note is not None, (
            f"{resname} is outside MDTraj's 'protein' and nothing said so"
        )
        assert resname in note

    def test_nothing_is_said_when_nothing_is_dropped(self) -> None:
        from fastmdxplora.analysis.sasa import SASA

        analysis = SASA()
        analysis.select_atoms(self._peptide("HIS"))
        assert "selection_dropped_residues" not in analysis.findings


class TestABondAngleIsNotACircle:
    """AUD20. PLUMED's ANGLE has domain [0, pi]; TORSION wraps."""

    def test_only_a_torsion_is_periodic(self) -> None:
        from fastmdxplora.simulation.umbrella import PERIODIC_VARIABLES

        assert "torsion" in PERIODIC_VARIABLES
        assert "angle" not in PERIODIC_VARIABLES

    def test_the_metadynamics_side_agrees(self, tmp_path) -> None:
        # A torsion is a circle; a bond angle, on [0, pi], is not. Read from
        # a PLUMED script as a study writes one, in one and two dimensions.
        from fastmdxplora.simulation.metad_surface import periodic_dimensions

        script = tmp_path / "plumed.dat"
        script.write_text("cv1: TORSION ATOMS=5,7,9,15\ncv2: ANGLE ATOMS=5,7,9\n"
                          "METAD ARG=cv1,cv2 SIGMA=0.3,0.1 HEIGHT=1.2 PACE=500\n",
                          encoding="utf-8")
        assert periodic_dimensions(script, 2) == (True, False)
        script.write_text("cv1: ANGLE ATOMS=5,7,9\nMETAD ARG=cv1\n", encoding="utf-8")
        assert periodic_dimensions(script, 1) == (False,)
        script.write_text("cv1: TORSION ATOMS=5,7,9,15\nMETAD ARG=cv1\n", encoding="utf-8")
        assert periodic_dimensions(script, 1) == (True,)

class TestAClosureGapNeedsSomethingToClose:
    """AUD21. `closure_gap` judged from the span alone."""

    def _profile(self, low, high):
        coordinate = np.linspace(low, high, 60)
        # A single well: the two ends differ, which is what closure_gap
        # measures and what makes a fabricated value visible.
        energy = 20.0 * (coordinate - low) / (high - low)
        return coordinate, energy

    def test_a_distance_pmf_gets_none(self) -> None:
        """0.3 to 6.8 nm spans more than 2*pi, which was the whole test."""
        from fastmdxplora.simulation.umbrella import describe_pmf

        coordinate, energy = self._profile(0.3, 6.8)
        summary = describe_pmf(coordinate, energy, periodic=False)

        assert summary["closure_gap_kjmol"] is None

    def test_a_torsion_still_gets_one(self) -> None:
        from fastmdxplora.simulation.umbrella import describe_pmf

        coordinate, energy = self._profile(-np.pi, np.pi)
        summary = describe_pmf(coordinate, energy, periodic=True)

        assert summary["closure_gap_kjmol"] is not None


class TestASettingThatDoesNothingIsRefused:
    """AUD22. `equilibration_steps` was accepted, recorded, and read by
    nothing -- the same failure as a typo, with better spelling."""

    def test_it_is_refused_by_name(self) -> None:
        from fastmdxplora.config.loader import ConfigError
        from fastmdxplora.simulation.umbrella import check_umbrella_keys

        with pytest.raises(ConfigError) as refusal:
            check_umbrella_keys({"equilibration_steps": 500_000})
        assert "equilibration_fraction" in str(refusal.value)

    def test_the_setting_that_works_is_still_accepted(self) -> None:
        from fastmdxplora.simulation.umbrella import check_umbrella_keys

        check_umbrella_keys({"equilibration_fraction": 0.3})

    def test_it_is_gone_from_the_record(self) -> None:
        """`pmf.json` used to carry it, which said a discard had happened."""
        from fastmdxplora.simulation.umbrella import plan_windows

        plan = plan_windows({
            "collective_variable": "distance",
            "selection_a": "resid 1", "selection_b": "resid 2",
            "from": 0.3, "to": 0.9, "n_windows": 3, "force_constant": 1000,
        })
        assert "equilibration_steps" not in plan.as_record()


class TestTheSecondCallIsNotToldAboutTheFirst:
    """AUD24. `explore()` appended to results that __init__ had set."""

    def test_a_retry_after_a_failure_reports_the_retry(self, tmp_path) -> None:
        from fastmdxplora import FastMDXplora
        from fastmdxplora.orchestrator import PhaseResult

        study = FastMDXplora(system="1UBQ", output_dir=tmp_path / "run")
        failing = {"n": 0}

        def _phase(phase, options):
            failing["n"] += 1
            status = "error" if failing["n"] == 1 else "ok"
            return PhaseResult(name=phase, status=status,
                               message="", output_dir=study.output_dir)

        study._run_phase = _phase
        study._write_manifest = lambda *a, **k: None

        first = study.explore(include=["setup"], report=False)[0]
        second = study.explore(include=["setup"], report=False)[0]

        assert first.status == "error"
        assert second.status == "ok", (
            "the second call inherited the first call's failure"
        )
        assert [p.status for p in second.phases] == ["ok"], (
            "and its phase list still carries the first call's records"
        )


class TestTheReportersAreClosedWhateverHappens:
    """AUD25. `_detach_all_reporters` was the last statement in the try."""

    def test_a_stream_it_did_not_open_is_left_alone(self, tmp_path) -> None:
        # Closing a reporter's file is for files it opened: a state reporter
        # handed stdout, or a caller's stream, must still be usable after.
        import io
        import sys
        from types import SimpleNamespace

        app = pytest.importorskip("openmm.app")
        from fastmdxplora.simulation.runner import _detach_all_reporters

        mine = io.StringIO()
        reporters = [app.StateDataReporter(sys.stdout, 10, step=True),
                     app.StateDataReporter(mine, 10, step=True),
                     app.StateDataReporter(str(tmp_path / "own.csv"), 10, step=True)]
        simulation = SimpleNamespace(reporters=list(reporters))
        _detach_all_reporters(simulation)
        assert not sys.stdout.closed and not mine.closed
        assert reporters[2]._out.closed
        assert simulation.reporters == []

    def test_it_is_in_the_finally(self, tmp_path, monkeypatch) -> None:
        """A real run whose production raises after its reporters are
        attached: the reporters' files are closed and the reporters taken off
        the simulation anyway, so no file is left open behind the failure.
        Read from the source before, which is why it was missed that
        OpenMM's reporters have no close() and none of their files were
        closed on any path."""
        pytest.importorskip("openmm")
        from fastmdxplora.simulation import runner
        from tests._the_phase import a_prepared_water_box

        closed = []
        real_detach = runner._detach_all_reporters

        def watched_detach(simulation):
            closed.append(list(simulation.reporters))
            real_detach(simulation)
            closed.append(list(simulation.reporters))

        def fails_in_production(real):
            def stage(*args, **kwargs):
                if kwargs.get("label") == "Production":
                    raise RuntimeError("production failed")
                return real(*args, **kwargs)
            return stage

        monkeypatch.setattr(runner, "_detach_all_reporters", watched_detach)
        monkeypatch.setattr(runner, "_run_md_stage", fails_in_production(runner._run_md_stage))
        monkeypatch.setattr(runner, "_run_md_stage_with_live_metrics",
                            fails_in_production(runner._run_md_stage_with_live_metrics))
        with pytest.raises(Exception, match="production failed"):
            runner.run_simulation(**a_prepared_water_box(tmp_path), output_dir=str(tmp_path / "out"),
                                  production_steps=100, nvt_steps=10, npt_steps=0,
                                  minimize=False, platform="CPU", trajectory_interval_steps=10)
        assert closed, "the reporters were not detached when production failed"
        attached, after = closed[0], closed[-1]
        assert attached and after == []
        files = [reporter._out for reporter in attached
                 if hasattr(getattr(reporter, "_out", None), "closed")]
        assert files, "no reporter file to check: the run attached none"
        assert all(f.closed for f in files), [f.name for f in files if not f.closed]

class TestTheWhamLoopSaysWhetherItSettled:
    """AUD38. It ran to 2000 and stopped with no `else`, no flag and no
    field: nine stiff windows finished at 4.2e-05 against a 1e-06 tolerance
    and the PMF was reported as though it had converged."""

    def _windows(self, force_constant: float, spread: float, seed: int = 0):
        from fastmdxplora.simulation.umbrella import plan_windows

        plan = plan_windows({
            "collective_variable": "distance",
            "selection_a": "resid 1", "selection_b": "resid 2",
            "from": 0.3, "to": 0.3 + spread, "n_windows": 9,
            "force_constant": force_constant, "minimum_overlap": 0.0,
            "minimum_samples": 10,
        })
        rng = np.random.RandomState(seed)
        width = np.sqrt(2.5 / force_constant)
        samples = {w.index: w.centre + rng.normal(0, width, 500)
                   for w in plan.windows}
        return plan, samples

    def test_a_settled_run_says_so(self) -> None:
        from fastmdxplora.simulation.umbrella import compute_pmf

        plan, samples = self._windows(force_constant=300.0, spread=0.6)
        result = compute_pmf(samples, plan, temperature_K=300.0,
                             bootstrap_resamples=0)

        assert result["converged"] is True
        assert result["final_residual_kjmol"] < result["wham_tolerance_kjmol"]

    def test_the_field_is_always_there(self) -> None:
        """A caller reading `pmf` has no other way to tell the two apart."""
        from fastmdxplora.simulation.umbrella import compute_pmf

        plan, samples = self._windows(force_constant=300.0, spread=0.6)
        result = compute_pmf(samples, plan, temperature_K=300.0,
                             bootstrap_resamples=0)

        for key in ("converged", "final_residual_kjmol",
                    "wham_tolerance_kjmol"):
            assert key in result


class TestTheBarrierIsMeasuredWhereTheHillsWent:
    """AUD39. For a periodic variable the grid is always the full turn, so a
    run that explored two thirds of it had its barrier taken over an arc no
    hill was deposited on -- the identical failure `describe_pmf` was written
    to fix on the umbrella side and left uncorrected here."""

    def test_the_record_says_what_was_covered(self, tmp_path) -> None:
        from fastmdxplora.simulation.metad_surface import compute_surface

        from tests.test_metad_surface import (  # noqa: PLC0415
            _double_well, _grid, _hills_reaching, _written)

        grid = _grid()
        hills = _hills_reaching(_double_well(grid), grid)
        result = compute_surface(_written(tmp_path, hills),
                                 np.tile([0.0, 1.0], 20))

        covered = result["evidence"]["covered"]
        assert covered[0] < covered[1]
        assert covered[0] == pytest.approx(float(np.min(hills.centre)))
        assert covered[1] == pytest.approx(float(np.max(hills.centre)))


class TestAWindowAtTheWrapHasNotDrifted:
    """AUD29. Twelve windows tiling a full torsion, each sampling exactly
    about its own centre, and the one straddling +-pi was reported 0.488 rad
    away -- with the advice to hold it harder."""

    def test_nothing_drifts_when_nothing_has(self) -> None:
        from fastmdxplora.simulation.umbrella import (
            plan_windows, windows_that_drifted)

        plan = plan_windows({
            "collective_variable": "torsion",
            "selection_a": "resid 1", "selection_b": "resid 2",
            "from": -np.pi, "to": np.pi, "n_windows": 12,
            "force_constant": 200,
        })
        rng = np.random.RandomState(0)
        samples = {
            w.index: np.remainder(
                w.centre + rng.normal(0, 0.08, 400) + np.pi, 2 * np.pi) - np.pi
            for w in plan.windows
        }

        assert windows_that_drifted(samples, plan) == []

    def test_a_window_that_really_drifted_is_still_caught(self) -> None:
        """The guard has to keep working, or this is a fix that turns it off."""
        from fastmdxplora.simulation.umbrella import (
            plan_windows, windows_that_drifted)

        plan = plan_windows({
            "collective_variable": "torsion",
            "selection_a": "resid 1", "selection_b": "resid 2",
            "from": -np.pi, "to": np.pi, "n_windows": 12,
            "force_constant": 200,
        })
        rng = np.random.RandomState(0)
        samples = {
            w.index: np.remainder(
                w.centre + rng.normal(0, 0.08, 400) + np.pi, 2 * np.pi) - np.pi
            for w in plan.windows
        }
        # One window sampling where its neighbour should be.
        stray = plan.windows[5]
        samples[stray.index] = np.remainder(
            plan.windows[7].centre + rng.normal(0, 0.08, 400) + np.pi,
            2 * np.pi) - np.pi

        drifted = windows_that_drifted(samples, plan)
        assert len(drifted) == 1
        assert drifted[0]["centre"] == pytest.approx(stray.centre)


class TestTheTimeAxisRunsForwards:
    """AUD26. Each file kept its own clock, so a two-shot load came back
    with [0..9, 0..9] and every figure drew an x axis that doubled back."""

    def _shots(self):
        top = md.Topology()
        residue = top.add_residue("ALA", top.add_chain())
        for name in ("N", "CA", "C"):
            top.add_atom(name, md.element.carbon, residue)
        traj = md.Trajectory(np.zeros((20, 3, 3), dtype=np.float32), top)
        traj.time = np.concatenate([np.arange(10.0), np.arange(10.0)])
        return traj

    def test_two_shots_become_one_clock(self) -> None:
        from fastmdxplora.analysis.loading import _with_one_clock

        mended = _with_one_clock(self._shots(), 2)

        assert np.all(np.diff(mended.time) > 0)
        assert mended.time[-1] == pytest.approx(19.0)

    def test_the_spacing_the_files_used_is_kept(self) -> None:
        from fastmdxplora.analysis.loading import _with_one_clock

        mended = _with_one_clock(self._shots(), 2)
        assert np.allclose(np.diff(mended.time), 1.0)

    def test_one_file_is_left_alone(self) -> None:
        from fastmdxplora.analysis.loading import _with_one_clock

        traj = self._shots()
        traj.time = np.arange(20.0)
        assert np.array_equal(_with_one_clock(traj, 1).time, np.arange(20.0))


class TestPerResidueRmsfIsTheConventionalOne:
    """AUD37. GROMACS `rmsf -res` and cpptraj take sqrt(mean(MSF)); this
    averaged the RMSF, which reads low wherever a residue has one mobile
    atom among several rigid ones -- and a number produced that way is not
    comparable with a published per-residue RMSF."""

    def test_it_is_the_root_mean_square(self) -> None:
        one_floppy = np.array([0.5, 0.05, 0.05, 0.05, 0.05])

        averaging_rmsf = float(np.mean(one_floppy))
        conventional = float(np.sqrt(np.mean(one_floppy ** 2)))

        # Stated so the size of the difference is on the record.
        assert averaging_rmsf == pytest.approx(0.14, abs=0.001)
        assert conventional == pytest.approx(0.228, abs=0.001)

    def test_the_module_takes_the_second(self, tmp_path) -> None:
        # A residue of five atoms, one mobile among four still: the
        # per-residue value is the root of the mean of the per-atom squares,
        # not the mean of the per-atom values.
        import mdtraj as md

        from fastmdxplora.analysis.rmsf import RMSF

        topology = md.Topology()
        residue = topology.add_residue("ALA", topology.add_chain(), resSeq=1)
        for name in ("N", "CA", "C", "O", "CB"):
            topology.add_atom(name, md.element.carbon, residue)
        rng = np.random.default_rng(0)
        base = np.array([[0.0, 0, 0], [0.15, 0, 0], [0.3, 0, 0], [0.3, 0.12, 0], [0.15, 0.15, 0]])
        xyz = np.repeat(base[None], 200, axis=0) + rng.normal(0, 0.005, (200, 5, 3))
        xyz[:, 4] += rng.normal(0, 0.3, (200, 3))
        traj = md.Trajectory(xyz.astype(np.float32), topology)
        per_atom = RMSF(per_residue=False, selection="all",
                        output_dir=str(tmp_path / "a")).compute(traj[:])
        per_residue = RMSF(per_residue=True, selection="all",
                           output_dir=str(tmp_path / "r")).compute(traj[:])
        values = np.asarray(per_atom)[:, -1]
        assert per_residue[0, 1] == pytest.approx(float(np.sqrt(np.mean(values ** 2))), rel=1e-6)
        # Measurably apart here, so the check above tells the two apart.
        assert per_residue[0, 1] > 1.03 * float(np.mean(values))
