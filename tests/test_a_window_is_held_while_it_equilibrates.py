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
                    began_at_ps=None, step_ps=1.0):
    """One window's COLVAR, optionally with a record of where production began.

    `equilibration` and `production` are the collective-variable values, in
    order. Written as PLUMED writes them: a header, then time and value.
    """
    directory = root / f"window-{index:02d}" / "simulation"
    directory.mkdir(parents=True, exist_ok=True)
    values = list(equilibration) + list(production)
    lines = ["#! FIELDS time cv restraint.bias"]
    lines += [f"{n * step_ps:.3f} {v:.6f} 0.0" for n, v in enumerate(values)]
    (directory / "COLVAR").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if began_at_ps is not None:
        (directory / "umbrella_window.json").write_text(
            json.dumps({"index": index, "centre": 0.9,
                        "force_constant": 3000.0,
                        "held_from_the_start": True,
                        "production_start_step": 0,
                        "production_start_ps": began_at_ps}),
            encoding="utf-8")
    return directory.parent


class TestTheArrivalIsNotSampling:

    def test_rows_written_before_production_are_dropped(self, tmp_path):
        """The window's approach to its centre is not a measurement of it."""
        directory = _window_on_disk(
            tmp_path, 0,
            equilibration=np.linspace(0.55, 0.90, 100),
            production=np.full(100, 0.90),
            began_at_ps=100.0)

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
            production=np.full(100, 0.90),
            began_at_ps=400.0)

        samples = collect_samples({0: directory}, equilibration_fraction=0.2)

        assert samples[0].size == 80        # a fifth of 100, not of 500
        assert np.allclose(samples[0], 0.90)

    def test_a_run_without_the_record_is_read_as_it_always_was(self, tmp_path):
        """Every window that has already run was not held while it settled.

        Those files are still on disk and still readable, and nothing about
        them changes: no record means no production marker, and the whole
        COLVAR is the production run, which is what it was.
        """
        directory = _window_on_disk(
            tmp_path, 0,
            equilibration=[],
            production=np.full(100, 0.90))

        samples = collect_samples({0: directory}, equilibration_fraction=0.2)

        assert samples[0].size == 80

    def test_a_marker_past_the_end_does_not_empty_the_window(self, tmp_path):
        """A run that stopped before production would otherwise vanish here.

        Reporting no sampling at all reads as a missing file and would be
        refused as one. Better to read what is there and let the sampling
        gate say the window is thin, which is what it is.
        """
        directory = _window_on_disk(
            tmp_path, 0,
            equilibration=np.full(50, 0.55),
            production=[],
            began_at_ps=10_000.0)

        samples = collect_samples({0: directory}, equilibration_fraction=0.0)

        assert samples[0].size == 50


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

    def watched_bias(_omm, _system, _plumed, _out):
        events.append("bias")
        return None       # as when openmm-plumed is not installed

    for name in ("system.xml", "state.xml"):
        (tmp_path / name).write_text("<x/>", encoding="utf-8")
    (tmp_path / "topology.pdb").write_text("ATOM\nEND\n", encoding="utf-8")

    plan = SimpleNamespace(collective_variable="ligand_distance",
                           bias_factor=10.0)

    with patch.object(_runner, "_import_openmm", return_value=omm), \
         patch.object(_runner, "_run_md_stage", side_effect=watched_stage), \
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


class TestWhenTheBiasGoesOn:

    def test_a_window_is_held_before_anything_moves(self, tmp_path):
        events = _events_of_a_run(
            tmp_path,
            umbrella={"collective_variable": "ligand_distance",
                      "centre": 0.9, "force_constant": 3000.0, "index": 9})

        assert "bias" in events, "the window was never held"
        assert events.index("bias") < events.index("minimise"), (
            "Equilibration ran before the restraint went on, which is the "
            "defect this file exists for: the window equilibrates wherever "
            "the free energy takes it and production starts there.")

    def test_it_is_held_once(self, tmp_path):
        """Attaching at both points would apply the restraint twice, halving
        the width of every window and the overlap between them -- and the
        study would refuse for a gap that the fix had opened."""
        events = _events_of_a_run(
            tmp_path,
            umbrella={"collective_variable": "ligand_distance",
                      "centre": 0.9, "force_constant": 3000.0, "index": 9})

        assert events.count("bias") == 1

    def test_metadynamics_still_equilibrates_unbiased(self, tmp_path):
        """There is no position to hold, and hills deposited while the system
        is still settling bias a surface with a transient."""
        events = _events_of_a_run(
            tmp_path,
            metadynamics={"collective_variable": "ligand_rmsd",
                          "sigma": 0.05, "height": 1.0, "pace": 500,
                          "bias_factor": 10.0})

        if "bias" not in events:        # the branch may refuse this stand-in
            pytest.skip("metadynamics did not reach the attach in this fake")
        assert events.index("bias") > events.index("minimise")

    def test_the_window_records_where_production_began(self, tmp_path):
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
        assert written["production_start_step"] == 20
        assert written["production_start_ps"] == pytest.approx(0.04)


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
