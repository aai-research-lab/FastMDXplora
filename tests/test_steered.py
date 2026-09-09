"""Pulling a system along a coordinate, and what that does and does not give.

Some things do not happen on their own within reach of a simulation. Steered
MD attaches a spring to a collective variable and moves the anchor, dragging
the system whether or not it wants to go.
"""

from __future__ import annotations

import pytest


def _topology():
    import mdtraj as md

    top = md.Topology()
    chain = top.add_chain()
    for index in range(30):
        residue = top.add_residue("ALA", chain, resSeq=index + 1)
        for name in ("N", "CA", "C", "O"):
            top.add_atom(name, md.element.carbon, residue)
    ligand_chain = top.add_chain()
    ligand = top.add_residue("BNZ", ligand_chain, resSeq=900)
    for index in range(6):
        top.add_atom(f"C{index}", md.element.carbon, ligand)
    return top


def _plan(**extra):
    from fastmdxplora.simulation.steered import plan_steered

    return plan_steered(dict({
        "collective_variable": "ligand_distance",
        "ligand_resname": "BNZ",
        "site_selection": "resid 1 to 3 and name CA",
        # A pull has to say where it starts. Omitting it used to give a
        # literal AT0=0 in the PLUMED script, so the tests below were
        # exercising a plan no real run should produce.
        "from": 0.4,
        "to": 3.0,
    }, **extra), _topology())


class TestWhatItClaims:
    """A pathway and the work along it, not a free energy: the work depends on
    how fast the anchor moved, and a single fast pull overestimates a
    barrier.
    """

    def test_it_does_not_claim_a_free_energy(self) -> None:
        record = _plan(**{"from": 0.4}).as_record()
        assert "not a free energy" in record["gives"]
        assert "overestimates" in record["gives"]

    def test_the_module_says_what_it_is_for(self) -> None:
        """Generating starting structures for umbrella sampling, which does
        give a free energy from equilibrium sampling rather than from work
        done in a hurry."""
        import inspect

        from fastmdxplora.simulation import steered

        text = inspect.getdoc(steered).lower()
        assert "umbrella" in text
        assert "jarzynski" in text

    def test_it_reports_the_pulling_rate(self) -> None:
        """The number that decides whether the work means anything."""
        plan = _plan(**{"from": 0.4, "steps": 500000})
        assert plan.rate_per_ns(2.0) == pytest.approx(2.6, rel=0.01)

    def test_a_rate_needs_a_starting_value(self) -> None:
        """A plan built without one cannot say how far the anchor travels.
        `plan_steered` refuses to make such a plan at all now, so this holds
        the arithmetic rather than the policy."""
        from fastmdxplora.simulation.steered import SteeredPlan

        plan = SteeredPlan(cv=_plan().cv, to_value=3.0, from_value=None,
                           steps=500000)
        assert plan.rate_per_ns(2.0) is None


class TestAPullSaysWhereItStarts:
    """PLUMED's moving restraint travels between two given anchors. With no
    `from`, the script wrote a literal AT0=0 -- which is a real position for
    most coordinates, not an absence of one.

    A real pull on the radius of gyration of a folded protein began with the
    anchor at 0 nm against a system at 0.71 nm, so a 2000 kJ/mol/nm^2
    restraint spent the first half of the run hauling the protein towards a
    collapsed state. The work came out at -194 kJ/mol before returning to
    +8.7, and the +8.7 was reported as the work done by the pull.
    """

    def test_a_plan_without_one_is_refused(self) -> None:
        from fastmdxplora.simulation.steered import plan_steered

        with pytest.raises(ValueError, match="needs `from`"):
            plan_steered({"collective_variable": "ligand_distance",
                          "ligand_resname": "BNZ",
                          "site_selection": "resid 1 to 3 and name CA",
                          "to": 3.0}, _topology())

    def test_it_is_refused_before_the_run_not_after_equilibration(self) -> None:
        """The script is written once production begins. Refusing there
        would cost the whole of NVT before saying so."""
        from pathlib import Path as _P
        import fastmdxplora.simulation.steered as steered

        source = _P(steered.__file__).read_text(encoding="utf-8")
        assert source.index("needs `from`") < source.index(
            "def build_steered_script")

    def test_the_anchor_is_written_where_it_was_asked_for(self) -> None:
        from fastmdxplora.simulation.steered import build_steered_script

        script = build_steered_script(_plan(**{"from": 0.4, "to": 3.0}))
        assert "AT0=0.4" in script
        assert "AT0=0 " not in script


class TestTheScriptItWrites:
    def test_it_is_a_moving_restraint(self) -> None:
        from fastmdxplora.simulation.steered import build_steered_script

        script = build_steered_script(_plan(**{"from": 0.4, "steps": 100000}))
        assert "MOVINGRESTRAINT" in script
        assert "AT0=0.4" in script and "AT1=3" in script
        assert "STEP1=100000" in script

    def test_it_records_the_work(self) -> None:
        """Without it a steered run has produced a trajectory and no number."""
        from fastmdxplora.simulation.steered import build_steered_script

        assert "pull.work" in build_steered_script(_plan())

    def test_the_script_says_the_rate_matters(self) -> None:
        from fastmdxplora.simulation.steered import build_steered_script

        script = build_steered_script(_plan())
        assert "overestimates a barrier" in script

    def test_plumed_counts_atoms_from_one(self) -> None:
        from fastmdxplora.simulation.steered import build_steered_script

        # The ligand is atoms 120-125 counting from zero.
        assert "ATOMS=121,122,123,124,125,126" in build_steered_script(_plan())


class TestWhatItRefuses:
    def test_a_pull_needs_a_destination(self) -> None:
        from fastmdxplora.simulation.steered import plan_steered

        with pytest.raises(ValueError, match="needs a `to`"):
            plan_steered({"collective_variable": "torsion",
                          "selection": "index 0 1 2 3"}, _topology())

    def test_steps_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive number of `steps`"):
            _plan(steps=0)

    def test_it_reuses_the_variables_metadynamics_offers(self) -> None:
        """Same five, resolved the same way, with the same refusals."""
        from fastmdxplora.simulation.metadynamics import COLLECTIVE_VARIABLES
        from fastmdxplora.simulation.steered import plan_steered

        with pytest.raises(ValueError, match="Unknown collective variable"):
            plan_steered({"collective_variable": "vibes", "to": 1.0},
                         _topology())
        # Whatever metadynamics offers, steering offers -- one translation,
        # so a new variable arrives in both.
        assert len(COLLECTIVE_VARIABLES) >= 5

    def test_an_unbounded_pull_is_not_refused(self) -> None:
        """Metadynamics refuses an unbounded ligand run because the bias fills
        a basin that never fills. A pull has a destination, so it ends."""
        assert _plan().to_value == 3.0


class TestItReachesTheRunner:
    def test_the_setting_is_declared(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = PHASE_SCHEMAS["simulation"].get("steered")
        assert field is not None
        assert "not a free energy" in field.help

    def test_it_reaches_the_command_line(self) -> None:
        from fastmdxplora.cli.main import _PHASE_SPEC

        table, _prefix = _PHASE_SPEC["simulate"]
        assert "steered" in {dest for _flag, dest, _kw in table}

    def test_the_pipeline_passes_it(self) -> None:
        import inspect

        from fastmdxplora.simulation import pipeline

        assert "steered=params.get" in inspect.getsource(pipeline)

    def test_steering_and_metadynamics_together_are_refused(self) -> None:
        """Two ways of moving the same coordinate, whose forces would add."""
        import inspect

        from fastmdxplora.simulation import runner

        source = inspect.getsource(runner.run_simulation)
        assert "steered and metadynamics" in source
        assert "their\n            \"forces would add" in source or \
               "forces would add" in source

    def test_the_script_is_written_where_it_can_be_read(self) -> None:
        import inspect

        from fastmdxplora.simulation import runner

        assert "steered.plumed" in inspect.getsource(runner.run_simulation)


class TestAPullReportsItsWork:
    """Umbrella sampling goes from a config block to a curve or a refusal,
    and metadynamics to a surface or a refusal. A steered run ended with
    PLUMED's COLVAR on disk and nothing reading it -- and the work is the one
    number this method produces, the number the module's own documentation
    says is reported "so the claim can be judged". Left in a column of a
    PLUMED file, it is not reported; it is available."""

    def _colvar(self, path, *, points=101, work_per_step=0.35):
        (path / "COLVAR").write_text(
            "#! FIELDS time cv pull.work pull.bias\n"
            + "".join(
                f"{i * 0.2:.1f} {0.73 + i * 0.0027:.6f} "
                f"{i * work_per_step:.6f} {i * 0.1:.6f}\n"
                for i in range(points)),
            encoding="utf-8")

    def _written(self, tmp_path):
        import json

        from fastmdxplora.simulation.pipeline import _write_steered_work

        self._colvar(tmp_path)
        name = _write_steered_work(
            tmp_path, {"steered": {"to": 1.0}, "timestep_fs": 2.0}, None)
        return name, json.loads((tmp_path / name).read_text(encoding="utf-8"))

    def test_a_file_is_written(self, tmp_path) -> None:
        name, _ = self._written(tmp_path)
        assert name == "steered_work.json"

    def test_it_carries_the_work(self, tmp_path) -> None:
        _, record = self._written(tmp_path)
        assert record["work_kjmol"] == pytest.approx(35.0, abs=0.01)

    def test_it_says_the_work_is_not_a_free_energy(self, tmp_path) -> None:
        """The distinction is the whole point: dissipated work does not
        cancel, so one pull overestimates a barrier."""
        _, record = self._written(tmp_path)
        caveat = record["work_is_not_a_free_energy"]
        assert "Jarzynski" in caveat
        assert "overestimates" in caveat

    def test_it_records_where_the_pull_actually_went(self, tmp_path) -> None:
        """Against where it was asked to go, because a restraint pulls and
        the system does not always follow."""
        _, record = self._written(tmp_path)
        assert record["from"] == pytest.approx(0.73, abs=0.001)
        assert record["to"] == pytest.approx(1.0, abs=0.01)
        assert record["requested_to"] == 1.0

    def test_the_trajectory_is_kept(self, tmp_path) -> None:
        """A single number cannot show whether the work accumulated smoothly
        or in one jump, which is the difference between a pull and a snap."""
        _, record = self._written(tmp_path)
        assert len(record["trajectory"]["work_kjmol"]) == record["samples"]
        assert len(record["trajectory"]["coordinate"]) == record["samples"]

    def test_nothing_is_written_without_a_colvar(self, tmp_path) -> None:
        from fastmdxplora.simulation.pipeline import _write_steered_work

        assert _write_steered_work(tmp_path, {"steered": {}}, None) is None

    def test_a_malformed_colvar_is_passed_over(self, tmp_path) -> None:
        """Not a crash at the end of a run that otherwise worked."""
        from fastmdxplora.simulation.pipeline import _write_steered_work

        (tmp_path / "COLVAR").write_text("nonsense\n", encoding="utf-8")
        assert _write_steered_work(tmp_path, {"steered": {}}, None) is None

    def test_the_run_asks_for_it(self, tmp_path) -> None:
        """Wired in beside the metadynamics writer, on the same condition."""
        import inspect

        from fastmdxplora.simulation import pipeline

        source = inspect.getsource(pipeline)
        assert 'if params.get("steered"):' in source
        assert "_write_steered_work(output_dir, params, presenter)" in source


class TestAPullThatWroteNoRecordSaysWhy:
    """`_write_steered_work` returned None in three places and said nothing
    in any of them. A pull ran, PLUMED wrote a valid COLVAR, no record
    appeared, and finding out why took an hour of reading the pipeline --
    for a function written to fix exactly that: a result available rather
    than reported."""

    def _explanations(self, tmp_path, monkeypatch) -> list[str]:
        """What the writer said, captured at the logger it uses.

        `caplog` sees nothing here: the project attaches its own handler and
        the records do not propagate to the root logger pytest hooks into.
        The messages reach the terminal and not the fixture, so a test built
        on `caplog` passes where logging is unconfigured and fails inside the
        project -- which is precisely backwards.
        """
        from fastmdxplora.simulation import pipeline

        said: list[str] = []
        monkeypatch.setattr(
            pipeline.logger, "info",
            lambda message, *args, **kwargs: said.append(
                message % args if args else message))
        return said

    def test_a_missing_colvar_is_explained(self, tmp_path, monkeypatch) -> None:
        from fastmdxplora.simulation.pipeline import _write_steered_work

        said = self._explanations(tmp_path, monkeypatch)
        assert _write_steered_work(tmp_path, {"steered": {}}, None) is None
        assert any("no COLVAR" in line for line in said), said

    def test_a_colvar_without_the_work_column_is_explained(
        self, tmp_path, monkeypatch
    ) -> None:
        """PLUMED prints what the script asked for. A script without
        `pull.work` produces a file that looks fine and holds no work."""
        from fastmdxplora.simulation.pipeline import _write_steered_work

        (tmp_path / "COLVAR").write_text(
            "#! FIELDS time cv\n 1.0 0.7\n", encoding="utf-8")
        said = self._explanations(tmp_path, monkeypatch)
        assert _write_steered_work(tmp_path, {"steered": {}}, None) is None
        assert any("column" in line for line in said), said

    def test_a_single_row_is_a_short_pull_not_an_absent_one(
        self, tmp_path
    ) -> None:
        """`np.loadtxt` gives a 1-D array for one row, and the shape check
        read that as no data at all."""
        from fastmdxplora.simulation.pipeline import _write_steered_work

        (tmp_path / "COLVAR").write_text(
            "#! FIELDS time cv pull.work pull.bias\n 1.0 0.7 2.5 0.1\n",
            encoding="utf-8")
        assert _write_steered_work(
            tmp_path, {"steered": {"to": 0.95}}, None) == "steered_work.json"


class TestThePullFlushesAsItGoes:
    """PLUMED buffers its output and writes on teardown, and the work record
    is built at the end of the simulation phase -- before the force is
    finalised. `COLVAR` existed, held no rows, and the record was silently
    not written; run by hand afterwards on the same file it worked. A race
    rather than a logic error, which is why five readings of the pipeline
    found nothing."""

    def _script(self):
        import mdtraj as md
        import numpy as np

        top = md.Topology()
        chain = top.add_chain()
        for _ in range(3):
            residue = top.add_residue("ALA", chain)
            for name, element in (
                ("N", md.element.nitrogen), ("CA", md.element.carbon),
                ("C", md.element.carbon), ("O", md.element.oxygen),
            ):
                top.add_atom(name, element, residue)
        trajectory = md.Trajectory(
            np.zeros((1, 12, 3)), top)

        from fastmdxplora.simulation.steered import (
            build_steered_script,
            plan_steered,
        )

        plan = plan_steered({
            "collective_variable": "distance",
            "selection_a": "resid 0 and name CA",
            "selection_b": "resid 2 and name CA",
            "from": 0.5,
            "to": 0.95,
            "steps": 1000,
        }, trajectory.topology)
        return build_steered_script(plan)

    def test_the_script_asks_plumed_to_flush(self) -> None:
        assert "FLUSH" in self._script()

    def test_it_flushes_as_often_as_it_prints(self) -> None:
        """A flush rarer than the print leaves the last rows buffered, which
        is the same failure with fewer missing lines."""
        import re

        script = self._script()
        printed = re.search(r"PRINT[^\n]*STRIDE=(\d+)", script)
        flushed = re.search(r"FLUSH STRIDE=(\d+)", script)
        assert printed and flushed
        assert int(flushed.group(1)) <= int(printed.group(1))

    def test_the_work_is_still_printed(self) -> None:
        script = self._script()
        assert "pull.work" in script


def test_the_restraint_starts_where_plumed_actually_is():
    """`STEP0=0` is wrong whenever equilibration ran first, which is always.

    PLUMED counts from the start of the simulation, not from the start of
    production. Minimisation, NVT and NPT all run before the pull, so by
    the time production begins the counter is already at 750,000 steps for
    a 1500 ps equilibration at 2 fs.

    A restraint written with `STEP0=0` therefore begins production having
    already interpolated part of its path. In the pull this was found in --
    0.381 nm to 2.0 nm over 5,000,000 steps, after 1500 ps of equilibration
    -- the anchor was at

        0.381271 + (2 - 0.381271) * 750000/5000000 = 0.624080 nm

    while the ligand sat at 0.381271, so the first recorded bias was

        0.5 * 5000 * (0.381271 - 0.624080)^2 = 147.39 kJ/mol

    COLVAR's first row read 147.390866. The ligand was thrown out of the
    binding well in 400 fs, that well was never sampled, and the anchor
    reached its destination with 1.5 ns of the run still to go.

    Nothing failed. The anchor value was right, the script was valid, and
    the only trace was a bias where zero belonged.
    """
    from fastmdxplora.simulation.steered import build_steered_script

    class _CV:
        collective_variable = "ligand_distance"

    class _Plan:
        cv = _CV()
        from_value = 0.381271
        to_value = 2.0
        steps = 5_000_000
        force_constant = 5000.0

    import fastmdxplora.simulation.steered as steered_module

    original = steered_module.cv_lines
    steered_module.cv_lines = lambda cv, ref: ["cv: DISTANCE ATOMS=1,2"]
    try:
        at_zero = build_steered_script(_Plan())
        after_equilibration = build_steered_script(_Plan(), first_step=750_000)
    finally:
        steered_module.cv_lines = original

    # The default is unchanged for a run that really does start at zero.
    assert "STEP0=0 " in at_zero
    assert "STEP1=5000000 " in at_zero

    # And the whole schedule shifts, rather than only its start: an anchor
    # told to arrive at step 5,000,000 while production ends at 5,750,000
    # stops moving with 1.5 ns left to run.
    assert "STEP0=750000 " in after_equilibration
    assert "STEP1=5750000 " in after_equilibration
