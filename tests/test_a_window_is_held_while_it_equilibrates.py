"""An umbrella window is held from the first step, not from production.

The restraint is what makes a window a window. Attaching it at production --
correct for metadynamics, which has no position to hold, and for a pull,
which starts wherever equilibration left it -- means every window minimises
and equilibrates with nothing holding its coordinate, relaxes down the
gradient the seed was chosen from, and begins production somewhere else.

Measured on a thirty-window study: seeds placed to within 0.0006 nm of their
centres, and twenty-six windows began production more than four sigma away.
The window seeded at 2.0020 nm started at 0.8879, having slid 1.11 nm while
nothing held it. The restraint then dragged each back out, which mostly
works, and did not work at the one window whose centre sits just past a
barrier -- so the study refused for want of overlap between two windows,
one of which had never been where it was told.

The COLVAR of a held window therefore covers its equilibration as well, and
those rows are the window arriving rather than sampling. The run records
where production began so they can be dropped, which keeps
`equilibration_fraction` meaning a fraction of the production run.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from fastmdxplora.simulation import runner as _runner
from fastmdxplora.simulation.umbrella import collect_samples


def _window_on_disk(root: Path, index: int, *, equilibration, production,
                    step_ps=1.0, equilibration_starts_at=0.0):
    """One window's COLVAR, written the way a held window writes one.

    The equilibration rows carry a clock running forward from
    `equilibration_starts_at`; the production rows restart from zero, which
    is what `setStepCount(0)` at production does to PLUMED's clock. Giving
    no equilibration rows produces the file a window biased only for
    production writes -- one series, no jump.
    """
    directory = root / f"window-{index:02d}" / "simulation"
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["#! FIELDS time cv restraint.bias"]
    lines += [f"{equilibration_starts_at + n * step_ps:.3f} {v:.6f} 0.0"
              for n, v in enumerate(equilibration)]
    lines += [f"{n * step_ps:.3f} {v:.6f} 0.0"
              for n, v in enumerate(production)]
    (directory / "COLVAR").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory.parent


class TestTheArrivalIsNotSampling:

    def test_rows_written_before_production_are_dropped(self, tmp_path):
        """The window's approach to its centre is not a measurement of it."""
        directory = _window_on_disk(
            tmp_path, 0,
            equilibration=np.linspace(0.55, 0.90, 100),
            production=np.full(100, 0.90))

        samples = collect_samples({0: directory}, equilibration_fraction=0.0)

        assert samples[0].size == 100
        assert np.allclose(samples[0], 0.90)

    def test_the_fraction_is_a_fraction_of_production(self, tmp_path):
        """Not of the whole file.

        Applying it to equilibration and production together would discard a
        fifth of a run that is mostly equilibration and leave the rest of the
        approach in the histogram.
        """
        directory = _window_on_disk(
            tmp_path, 0,
            equilibration=np.full(400, 0.55),
            production=np.full(100, 0.90))

        samples = collect_samples({0: directory}, equilibration_fraction=0.2)

        assert samples[0].size == 80        # a fifth of 100, not of 500
        assert np.allclose(samples[0], 0.90)

    def test_a_file_with_one_clock_is_read_as_it_always_was(self, tmp_path):
        """Every window that has already run was biased at production only.

        Those files are still on disk and still readable: one series, no
        backwards jump, the whole COLVAR is the production run.
        """
        directory = _window_on_disk(
            tmp_path, 0,
            equilibration=[],
            production=np.full(100, 0.90))

        samples = collect_samples({0: directory}, equilibration_fraction=0.2)

        assert samples[0].size == 80

    def test_the_equilibration_clock_need_not_start_at_zero(self, tmp_path):
        """It does not, in practice.

        Adding the barostat reinitialises the context, PLUMED reopens COLVAR
        and truncates what NVT wrote, so the file a real run leaves begins
        part-way through equilibration -- 50 ps in, on the smoke test that
        found this. The boundary is the jump, not the value either side.
        """
        directory = _window_on_disk(
            tmp_path, 0,
            equilibration=np.full(252, 0.55),
            production=np.full(1000, 0.90),
            step_ps=0.2, equilibration_starts_at=50.0)

        samples = collect_samples({0: directory}, equilibration_fraction=0.0)

        assert samples[0].size == 1000
        assert np.allclose(samples[0], 0.90)


class TestFindingWhereProductionBegins:

    def test_it_is_the_row_after_the_clock_goes_backwards(self):
        from fastmdxplora.simulation.umbrella import production_begins_at

        times = np.array([50.0, 50.2, 50.4, 0.2, 0.4, 0.6])

        assert production_begins_at(times) == 3

    def test_a_clock_that_only_runs_forward_starts_at_the_top(self):
        from fastmdxplora.simulation.umbrella import production_begins_at

        assert production_begins_at(np.arange(10.0)) == 0

    def test_the_last_reset_wins(self):
        """A file carrying two resets should give the final run, not the
        middle one."""
        from fastmdxplora.simulation.umbrella import production_begins_at

        times = np.array([5.0, 5.2, 0.2, 0.4, 0.6, 0.2, 0.4])

        assert production_begins_at(times) == 5

    def test_an_empty_or_single_row_file_does_not_raise(self):
        from fastmdxplora.simulation.umbrella import production_begins_at

        assert production_begins_at(np.array([])) == 0
        assert production_begins_at(np.array([1.0])) == 0


def _events_of_a_run(tmp_path, **kwargs):
    """The order a run does things in: which stages ran, and when the bias
    went on.

    Against a stand-in for OpenMM, because what is being tested is the order
    of the runner's own steps and not anything OpenMM computes.
    """
    from tests.test_simulation_phase import _build_fake_omm

    omm = _build_fake_omm()
    events: list[str] = []

    def watched_stage(*_args, **kw):
        events.append(f"stage:{kw.get('label', '?')}")
        return int(kw.get("current_step", 0)) + int(kw.get("n_steps", 0))

    def watched_bias(_omm, _system, _plumed, _out, **_kw):
        events.append(f"bias:{Path(_plumed['script']).name}")
        return None       # as when openmm-plumed is not installed

    for name in ("system.xml", "state.xml"):
        (tmp_path / name).write_text("<x/>", encoding="utf-8")
    (tmp_path / "topology.pdb").write_text("ATOM\nEND\n", encoding="utf-8")

    plan = SimpleNamespace(collective_variable="ligand_distance",
                           bias_factor=10.0)

    with patch.object(_runner, "_import_openmm", return_value=omm), \
         patch.object(_runner, "_run_md_stage", side_effect=watched_stage), \
         patch.object(_runner, "_remove_force",
                      side_effect=lambda *a: events.append("remove")), \
         patch.object(_runner, "_run_minimize",
                      side_effect=lambda *a, **k: events.append("minimise")), \
         patch("fastmdxplora.simulation.plumed.add_plumed_force",
               side_effect=watched_bias), \
         patch("fastmdxplora.simulation.metadynamics.plan_from_config",
               return_value=plan), \
         patch("fastmdxplora.simulation.metadynamics.cv_lines",
               return_value=["cv: DISTANCE ATOMS=1,2"]), \
         patch("fastmdxplora.simulation.metadynamics.build_plumed_script",
               return_value="# script\n", create=True):
        _runner.run_simulation(
            system_xml=tmp_path / "system.xml",
            state_xml=tmp_path / "state.xml",
            topology_pdb=tmp_path / "topology.pdb",
            output_dir=tmp_path / "out",
            nvt_steps=10, npt_steps=10, production_steps=10,
            minimize=True,
            **kwargs,
        )
    return events


_UMBRELLA = {"collective_variable": "ligand_distance", "centre": 0.9,
             "force_constant": 3000.0, "index": 9}


class TestWhenTheBiasGoesOn:

    def test_a_window_is_held_before_anything_moves(self, tmp_path):
        events = _events_of_a_run(tmp_path, umbrella=dict(_UMBRELLA))
        biases = [e for e in events if e.startswith("bias:")]

        assert biases, "the window was never held"
        assert events.index(biases[0]) < events.index("minimise"), (
            "Equilibration ran before the restraint went on, which is the "
            "defect this file exists for: the window equilibrates wherever "
            "the free energy takes it and production starts there.")

    def test_the_restraint_is_never_doubled(self, tmp_path):
        """It goes on twice -- once writing the settling, once writing
        production -- and the first must come off before the second goes on.

        Two live PLUMED forces would hold the same coordinate at the same
        centre with twice the constant, halving the width of every window and
        the overlap between them. The study would then refuse for a gap the
        fix had opened, which is the worst way to be wrong.
        """
        events = _events_of_a_run(tmp_path, umbrella=dict(_UMBRELLA))
        order = [e for e in events if e == "remove" or e.startswith("bias:")]

        assert order == ["bias:umbrella_equilibration.plumed",
                         "remove",
                         "bias:umbrella.plumed"]

    def test_colvar_is_production_and_the_settling_has_its_own_file(
            self, tmp_path):
        """A COLVAR holding both was a file that began part-way through
        equilibration, lost what NVT wrote, and ran its clock backwards in the
        middle. Every reader had to know all three things."""
        _events_of_a_run(tmp_path, umbrella=dict(_UMBRELLA))
        out = tmp_path / "out"

        production = (out / "umbrella.plumed").read_text("utf-8")
        settling = (out / "umbrella_equilibration.plumed").read_text("utf-8")

        assert "FILE=COLVAR\n" in production
        assert "FILE=COLVAR.equilibration" in settling
        assert settling.startswith("RESTART"), (
            "Without it the barostat's reinitialise reopens the file and "
            "everything NVT wrote is gone.")
        # Same window, same hold, either side of the swap.
        for script in (production, settling):
            assert "AT=0.9 KAPPA=3000" in script

    def test_metadynamics_still_equilibrates_unbiased(self, tmp_path):
        """There is no position to hold, and hills deposited while the system
        is still settling bias a surface with a transient."""
        events = _events_of_a_run(
            tmp_path,
            metadynamics={"collective_variable": "ligand_rmsd",
                          "sigma": 0.05, "height": 1.0, "pace": 500,
                          "bias_factor": 10.0})
        biases = [e for e in events if e.startswith("bias:")]

        if not biases:                  # the branch may refuse this stand-in
            pytest.skip("metadynamics did not reach the attach in this fake")
        assert len(biases) == 1
        assert events.index(biases[0]) > events.index("minimise")

    def test_the_window_records_what_it_held(self, tmp_path):
        _events_of_a_run(
            tmp_path,
            umbrella={"collective_variable": "ligand_distance",
                      "centre": 0.9, "force_constant": 3000.0, "index": 9})

        written = json.loads(
            (tmp_path / "out" / "umbrella_window.json").read_text("utf-8"))

        assert written["index"] == 9
        assert written["centre"] == 0.9
        assert written["held_from_the_start"] is True
        # 20 steps of equilibration at the default 2 fs.
        assert written["equilibration_steps"] == 20
        assert written["equilibration_ps"] == pytest.approx(0.04)

    def test_the_record_does_not_claim_to_locate_production(self):
        """It cannot, and saying so cost a smoke test.

        Production resets the context's step counter and clock, which rewinds
        PLUMED's clock behind the runner's back -- so a step count recorded
        here described a file whose production rows begin at 0.2 ps. The
        reader finds the boundary in the clock instead.
        """
        import inspect

        from fastmdxplora.simulation import umbrella

        source = inspect.getsource(umbrella.collect_samples)

        assert "production_start_ps" not in source
        assert "production_begins_at" in source


class TestTheLogSaysWhatItDoes:

    def test_it_no_longer_says_production_only(self, tmp_path, caplog):
        """The line a user reads when a window starts said the restraint
        applied to production only, and it did. Someone reading the log had
        no way to know that was the whole problem."""
        import logging

        with caplog.at_level(logging.INFO):
            _events_of_a_run(
                tmp_path,
                umbrella={"collective_variable": "ligand_distance",
                          "centre": 0.9, "force_constant": 3000.0,
                          "index": 9})

        said = " ".join(record.getMessage() for record in caplog.records)

        assert "production only" not in said
        assert "minimisation onwards" in said
        assert "held at 0.9" in said
