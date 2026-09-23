"""Metadynamics without writing PLUMED input.

PLUMED can express almost any enhanced-sampling scheme, and the cost of that
is a language to learn before running the commonest one. Most metadynamics on
a protein-ligand system biases one of a handful of things, and those do not
need a language.
"""

from __future__ import annotations

import pytest


def _topology():
    import mdtraj as md

    top = md.Topology()
    chain = top.add_chain()
    for index in range(6):
        residue = top.add_residue("ALA", chain, resSeq=index + 1)
        for name in ("N", "CA", "C", "O"):
            top.add_atom(name, md.element.carbon, residue)
    ligand_chain = top.add_chain()
    ligand = top.add_residue("BNZ", ligand_chain, resSeq=900)
    for index in range(6):
        top.add_atom(f"C{index}", md.element.carbon, ligand)
    return top


class TestChoosingWhatToBias:
    """Choosing a collective variable is the decision the method turns on: if
    it does not distinguish the states that matter, the surface converges and
    describes something that is not the system."""

    def test_every_variable_says_what_it_does_not_separate(self) -> None:
        """A name alone does not carry that, and it is the failure mode."""
        from fastmdxplora.simulation.metadynamics import COLLECTIVE_VARIABLES

        assert COLLECTIVE_VARIABLES
        for name, description in COLLECTIVE_VARIABLES.items():
            assert "Does not" in description or "does not" in description, name

    def test_an_unknown_variable_is_refused(self) -> None:
        from fastmdxplora.simulation.metadynamics import plan_from_config

        with pytest.raises(ValueError, match="Unknown collective variable"):
            plan_from_config({"collective_variable": "vibes", "sigma": 0.1},
                             _topology())

    def test_the_refusal_points_at_the_general_route(self) -> None:
        """Anything more elaborate is still written by hand: this is a shorter
        path to the common case, not a replacement for the general one."""
        from fastmdxplora.simulation.metadynamics import plan_from_config

        with pytest.raises(ValueError, match="PLUMED input directly"):
            plan_from_config({"collective_variable": "vibes", "sigma": 0.1},
                             _topology())


class TestTheHillWidthHasToBeGiven:
    """It should be about the size of the fluctuations within a single state,
    and there is no default that is right for an arbitrary coordinate."""

    def test_sigma_is_required(self) -> None:
        from fastmdxplora.simulation.metadynamics import plan_from_config

        with pytest.raises(ValueError, match="sigma"):
            plan_from_config(
                {"collective_variable": "radius_of_gyration"}, _topology())

    def test_and_the_refusal_says_what_a_reasonable_one_looks_like(self) -> None:
        from fastmdxplora.simulation.metadynamics import plan_from_config

        with pytest.raises(ValueError, match="0.05 nm"):
            plan_from_config(
                {"collective_variable": "radius_of_gyration"}, _topology())


class TestTheScriptItWrites:
    @staticmethod
    def _plan(**spec):
        from fastmdxplora.simulation.metadynamics import plan_from_config

        return plan_from_config(spec, _topology(), temperature_K=310.0)

    def test_plumed_counts_atoms_from_one(self) -> None:
        """Everything else here counts from zero. Getting it wrong biases the
        atom next door and produces a plausible surface for the wrong
        coordinate."""
        from fastmdxplora.simulation.metadynamics import build_plumed_script

        # Bounded, because an unbounded ligand run is refused: it would push
        # the ligand into bulk solvent and never come back.
        plan = self._plan(collective_variable="ligand_distance",
                          ligand_resname="BNZ",
                          site_selection="resid 1 to 3 and name CA",
                          sigma=0.05, walls={"upper": 2.5})
        script = build_plumed_script(plan)
        # The ligand is atoms 24-29 counting from zero.
        assert "ATOMS=25,26,27,28,29,30" in script

    def test_it_is_well_tempered_by_default(self) -> None:
        """Plain metadynamics deposits at full height forever, so the bias
        never settles and the surface never converges."""
        from fastmdxplora.simulation.metadynamics import build_plumed_script

        plan = self._plan(collective_variable="radius_of_gyration",
                          selection="protein and name CA", sigma=0.05)
        assert plan.bias_factor > 1.0
        assert "BIASFACTOR=" in build_plumed_script(plan)

    def test_it_records_the_variable_and_the_bias(self) -> None:
        """Without them there is no way to tell afterwards whether the run
        converged, and a run whose convergence cannot be checked has not
        measured a free energy."""
        from fastmdxplora.simulation.metadynamics import build_plumed_script

        plan = self._plan(collective_variable="radius_of_gyration",
                          selection="protein and name CA", sigma=0.05)
        script = build_plumed_script(plan)
        assert "PRINT ARG=cv,metad.bias" in script
        assert "FILE=COLVAR" in script
        assert "FILE=HILLS" in script

    def test_the_script_says_what_is_being_biased(self) -> None:
        """Somebody checking a result should be able to read what was biased
        without reading the module that wrote it."""
        from fastmdxplora.simulation.metadynamics import build_plumed_script

        plan = self._plan(collective_variable="torsion",
                          selection="resid 0 and name N CA C O", sigma=0.35)
        script = build_plumed_script(plan)
        assert script.startswith("#")
        assert "Does not" in script or "does not" in script

    def test_a_torsion_needs_exactly_four_atoms(self) -> None:
        """`resid 0` has exactly four in this topology, which is why the first
        version of this test selected four and asserted it would be
        refused."""
        with pytest.raises(ValueError, match="four atoms"):
            self._plan(collective_variable="torsion",
                       selection="resid 0 1", sigma=0.35)

    def test_a_ligand_distance_needs_somewhere_to_measure_to(self) -> None:
        with pytest.raises(ValueError, match="site_selection"):
            self._plan(collective_variable="ligand_distance",
                       ligand_resname="BNZ", sigma=0.05)

    def test_a_selection_matching_nothing_is_refused(self) -> None:
        with pytest.raises(ValueError, match="matched no atoms"):
            self._plan(collective_variable="radius_of_gyration",
                       selection="resname NOPE", sigma=0.05)

    def test_a_ligand_rmsd_needs_a_reference(self) -> None:
        """It is measured against a structure, and there is no sensible
        default for which one."""
        from fastmdxplora.simulation.metadynamics import build_plumed_script

        plan = self._plan(collective_variable="ligand_rmsd",
                          ligand_resname="BNZ", sigma=0.05,
                          walls={"upper": 1.0})
        with pytest.raises(ValueError, match="reference"):
            build_plumed_script(plan)


class TestWhatIsRecordedWithTheResults:
    def test_the_plan_carries_what_it_separates(self) -> None:
        from fastmdxplora.simulation.metadynamics import plan_from_config

        plan = plan_from_config(
            {"collective_variable": "radius_of_gyration",
             "selection": "protein and name CA", "sigma": 0.05},
            _topology())
        record = plan.as_record()
        assert record["well_tempered"] is True
        assert "does not separate" in record["what_it_separates"].lower() or \
               "Does not" in record["what_it_separates"]


class TestItReachesTheRunner:
    def test_the_setting_is_declared(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = PHASE_SCHEMAS["simulation"].get("metadynamics")
        assert field is not None
        assert "collective_variable" in field.help
        assert "sigma" in field.help

    def test_it_reaches_the_command_line(self) -> None:
        from fastmdxplora.cli.main import _PHASE_SPEC

        table, _prefix = _PHASE_SPEC["simulate"]
        assert "metadynamics" in {dest for _flag, dest, _kw in table}

    HILLS = {"collective_variable": "distance", "selection_a": "index 0",
             "selection_b": "index 3", "sigma": 0.02, "height_kjmol": 1.0,
             "pace_steps": 100}

    def test_the_pipeline_passes_it(self, tmp_path, monkeypatch) -> None:
        from tests._the_phase import what_the_runner_receives

        received = what_the_runner_receives(tmp_path, monkeypatch, metadynamics=self.HILLS)
        assert received["metadynamics"] == self.HILLS

    def test_the_runner_writes_the_script_where_it_can_be_read(self, tmp_path, monkeypatch) -> None:
        """It decides what the run measures, so it belongs beside the results
        rather than in memory."""
        from tests._the_phase import what_plumed_is_given

        _, out = what_plumed_is_given(tmp_path, monkeypatch, metadynamics=self.HILLS)
        script = out / "metadynamics.plumed"
        assert script.is_file()
        assert "METAD" in script.read_text(encoding="utf-8")

    def test_it_becomes_a_plumed_run(self, tmp_path, monkeypatch) -> None:
        """The existing integration does the biasing; this is a shorter way to
        describe it, not a second mechanism. PLUMED is handed the script the
        run wrote, and nothing else biases it."""
        from tests._the_phase import what_plumed_is_given

        given, out = what_plumed_is_given(tmp_path, monkeypatch, metadynamics=self.HILLS)
        assert given["config"]["enabled"] is True
        assert given["config"]["script"] == str(out / "metadynamics.plumed")
class TestBoundingWhereTheLigandGoes:
    """A metadynamics run on a ligand's distance will, given time, push the
    ligand into bulk solvent -- where the landscape is flat and unbounded, so
    the bias fills a basin that is effectively infinite and the run never
    comes back to the question.

    Without a bound, a ligand-distance run is not wrong so much as
    unfinishable.
    """

    @staticmethod
    def _spec(**extra):
        return dict({
            "collective_variable": "ligand_distance",
            "ligand_resname": "BNZ",
            "site_selection": "resid 1 to 3 and name CA",
            "sigma": 0.05,
        }, **extra)

    def test_an_unbounded_ligand_run_is_refused(self) -> None:
        from fastmdxplora.simulation.metadynamics import plan_from_config

        with pytest.raises(ValueError, match="bulk solvent"):
            plan_from_config(self._spec(), _topology())

    def test_but_can_be_asked_for_deliberately(self) -> None:
        from fastmdxplora.simulation.metadynamics import plan_from_config

        plan = plan_from_config(self._spec(unbounded=True), _topology())
        assert plan.walls is None and plan.funnel is None

    def test_a_wall_bounds_how_far(self) -> None:
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script,
            plan_from_config,
        )

        plan = plan_from_config(
            self._spec(walls={"upper": 2.5}), _topology())
        script = build_plumed_script(plan)
        assert "UPPER_WALLS ARG=cv AT=2.5" in script

    def test_a_wall_with_neither_end_is_refused(self) -> None:
        from fastmdxplora.simulation.metadynamics import plan_from_config

        with pytest.raises(ValueError, match="wall nowhere"):
            plan_from_config(self._spec(walls={"kappa": 100}), _topology())

    def test_a_funnel_bounds_where(self) -> None:
        """A flat wall bounds how far a ligand goes and not where, so the run
        still explores a whole shell of unbound positions at that distance."""
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script,
            plan_from_config,
        )

        plan = plan_from_config(
            self._spec(funnel={"axis_selection": "resid 4 5 and name CA"}),
            _topology())
        script = build_plumed_script(plan)
        assert "UPPER_WALLS ARG=outside AT=0" in script
        assert "proj: CUSTOM" in script and "rad: CUSTOM" in script

    def test_the_funnel_uses_only_standard_plumed(self) -> None:
        """Rather than the FUNNEL module, which is not in every build."""
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script,
            plan_from_config,
        )

        plan = plan_from_config(
            self._spec(funnel={"axis_selection": "resid 4 5 and name CA"}),
            _topology())
        script = build_plumed_script(plan)
        assert "FUNNEL " not in script.replace("# Funnel", "")
        for action in ("COM", "DISTANCE", "CUSTOM", "UPPER_WALLS"):
            assert action in script

    def test_it_is_wide_at_the_site_and_narrow_in_bulk(self) -> None:
        """Limongelli's shape: a cone over the site mouth opening into a
        cylinder, so the unbound state has a defined volume -- which is what
        makes an absolute binding free energy recoverable."""
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script,
            plan_from_config,
        )

        plan = plan_from_config(
            self._spec(funnel={"axis_selection": "resid 4 5 and name CA",
                               "cylinder_radius_nm": 0.1,
                               "switch_distance_nm": 1.5,
                               "alpha_rad": 0.55}),
            _topology())
        line = next(l for l in build_plumed_script(plan).splitlines()
                    if l.startswith("limit:"))
        assert "max(0.1," in line, "it never narrows below the cylinder"
        assert "(1.5-p)" in line, "and widens back towards the site"

    def test_a_funnel_needs_the_direction_the_ligand_leaves_by(self) -> None:
        """Nothing here can work that out, and one pointed the wrong way
        blocks the exit instead of following it."""
        from fastmdxplora.simulation.metadynamics import plan_from_config

        with pytest.raises(ValueError, match="axis_selection"):
            plan_from_config(self._spec(funnel={}), _topology())

    def test_a_funnel_only_applies_to_a_ligand_leaving(self) -> None:
        from fastmdxplora.simulation.metadynamics import plan_from_config

        with pytest.raises(ValueError, match="applies to ligand_distance"):
            plan_from_config(
                {"collective_variable": "radius_of_gyration",
                 "selection": "protein and name CA", "sigma": 0.05,
                 "funnel": {"axis_selection": "resid 4 and name CA"}},
                _topology())

    def test_a_bounded_run_records_its_bounds(self) -> None:
        from fastmdxplora.simulation.metadynamics import plan_from_config

        plan = plan_from_config(self._spec(walls={"upper": 2.5}), _topology())
        assert plan.as_record()["walls"]["upper"] == 2.5


class TestTheThreeAddedVariables:
    """Coordination, membrane depth and angle -- the ones missing from the
    first five that people actually use."""

    @staticmethod
    def _membrane_topology():
        import mdtraj as md

        top = _topology()
        lipids = top.add_chain()
        for index in range(8):
            lipid = top.add_residue("POP", lipids, resSeq=500 + index)
            for atom in range(4):
                top.add_atom(f"C{atom}", md.element.carbon, lipid)
        return top

    def test_coordination_counts_through_a_switching_function(self) -> None:
        """A hard cutoff has an infinite derivative at the boundary, and a
        bias needs a force."""
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script,
            plan_from_config,
        )

        plan = plan_from_config({
            "collective_variable": "coordination",
            "selection_a": "resname BNZ",
            "selection_b": "resid 1 to 3 and name CA",
            "sigma": 0.5}, _topology())
        script = build_plumed_script(plan)
        assert "COORDINATION" in script
        assert "NN=6 MM=12" in script, "a switching function, not a step"

    def test_the_switching_distance_is_a_setting_that_travels(self) -> None:
        """It was declared on the plan and read by the script and never
        passed from the config, so every run used the default whatever was
        asked for."""
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script,
            plan_from_config,
        )

        for wanted in (0.3, 0.45):
            plan = plan_from_config({
                "collective_variable": "coordination",
                "selection_a": "resname BNZ",
                "selection_b": "resid 1 to 3 and name CA",
                "sigma": 0.5, "coordination_r0": wanted}, _topology())
            assert plan.coordination_r0 == wanted
            assert f"R_0={wanted:g}" in build_plumed_script(plan)

    def test_coordination_needs_two_groups(self) -> None:
        from fastmdxplora.simulation.metadynamics import plan_from_config

        with pytest.raises(ValueError, match="selection_b"):
            plan_from_config({
                "collective_variable": "coordination",
                "selection_a": "resname BNZ", "sigma": 0.5}, _topology())

    def test_membrane_depth_is_measured_from_the_bilayer_itself(self) -> None:
        """A membrane drifts in the box over a long run, so depth against a
        fixed plane slowly becomes depth against nothing."""
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script,
            plan_from_config,
        )

        plan = plan_from_config({
            "collective_variable": "membrane_depth",
            "ligand_resname": "BNZ", "sigma": 0.05},
            self._membrane_topology())
        script = build_plumed_script(plan)
        assert "mem: COM" in script, "the bilayer's own centre"
        assert "sep.z" in script, "along the normal"

    def test_membrane_depth_needs_a_membrane(self) -> None:
        from fastmdxplora.simulation.metadynamics import plan_from_config

        with pytest.raises(ValueError, match="matched no atoms"):
            plan_from_config({
                "collective_variable": "membrane_depth",
                "ligand_resname": "BNZ", "sigma": 0.05}, _topology())

    def test_an_angle_is_three_atoms(self) -> None:
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script,
            plan_from_config,
        )

        plan = plan_from_config({
            "collective_variable": "angle",
            "selection": "resid 0 and name N CA C", "sigma": 0.2}, _topology())
        assert "ANGLE ATOMS=" in build_plumed_script(plan)

        with pytest.raises(ValueError, match="three atoms"):
            plan_from_config({
                "collective_variable": "angle",
                "selection": "resid 0", "sigma": 0.2}, _topology())

    def test_every_variable_says_what_it_does_not_separate(self) -> None:
        """The count is asserted so that adding one is a decision.

        A variable that arrives without saying what it fails to
        distinguish is the failure mode of the method shipped as a
        feature, so the check is on every entry rather than on new ones.
        """
        from fastmdxplora.simulation.metadynamics import COLLECTIVE_VARIABLES

        assert len(COLLECTIVE_VARIABLES) == 9
        for name, description in COLLECTIVE_VARIABLES.items():
            assert "oes not" in description, name

    def test_steered_md_gets_them_too(self) -> None:
        """It reuses the same layer, so a new variable arrives in both."""
        from fastmdxplora.simulation.steered import build_steered_script, plan_steered

        plan = plan_steered({
            "collective_variable": "membrane_depth",
            # Zero is the bilayer midplane here -- a real depth, not an
            # absent one -- which is why a pull has to say where it starts.
            "ligand_resname": "BNZ", "from": 0.0, "to": 2.5},
            self._membrane_topology())
        assert "sep.z" in build_steered_script(plan)


def test_one_translation_serves_every_method_that_biases(tmp_path, monkeypatch) -> None:
    """Steered MD had a copy of the collective-variable translation, so
    membrane_depth reached metadynamics and fell through steering's else
    branch into one expecting a radius of gyration.

    A variable should arrive everywhere at once. Whatever the one
    translation writes, every method that biases a coordinate writes:
    metadynamics, a pull, and an umbrella window's script from a real run.
    A method keeping its own copy would lack the marker.
    """
    from fastmdxplora.simulation import metadynamics, steered
    from fastmdxplora.simulation.metadynamics import build_plumed_script, plan_from_config
    from fastmdxplora.simulation.steered import build_steered_script, plan_steered

    # Steering holds the one function, not a copy of it: the same object.
    assert steered.cv_lines is metadynamics.cv_lines
    marker = "# written by the one translation"
    real = metadynamics.cv_lines

    def marked(*args, **kwargs):
        return [marker, *real(*args, **kwargs)]

    # Wherever it is held: steering imported it by name.
    monkeypatch.setattr(metadynamics, "cv_lines", marked)
    monkeypatch.setattr(steered, "cv_lines", marked)
    ligand = {"collective_variable": "ligand_distance", "ligand_resname": "BNZ",
              "site_selection": "resid 1 to 3 and name CA"}
    assert marker in build_plumed_script(plan_from_config(
        dict(ligand, sigma=0.05, height=1.0, pace=10, unbounded=True), _topology()))
    assert marker in build_steered_script(plan_steered(
        dict(ligand, **{"from": 0.4, "to": 3.0}), _topology()))
    window = {"collective_variable": "distance", "selection_a": "resid 0 and name O",
              "selection_b": "resid 1 and name O", "centre": 0.5,
              "force_constant": 500.0, "index": 0}
    out = _a_biased_run(tmp_path, monkeypatch, umbrella=window)
    assert marker in (out / "umbrella.plumed").read_text(encoding="utf-8")

class TestTheStagePlanSurvivesTheBiasPlan:
    """`plan = plan_from_config(...)` in the runner rebound the stage plan --
    a dict of step counts that the rest of the function subscripts -- to a
    MetadynamicsPlan. The next `plan["nvt_steps"]` raised
    "'MetadynamicsPlan' object is not subscriptable", so metadynamics failed
    on the first line of real use.

    Thirty-eight tests cover this module and twenty-three the surface it
    produces. None of them runs the runner, which is where the two names met.
    """

    def test_the_bias_plan_has_its_own_name(self, tmp_path, monkeypatch) -> None:
        # One variable biased: the run minimises, equilibrates at constant
        # volume and pressure, and produces, every stage reading the stage
        # plan after the bias plan was made.
        out = _a_biased_run(tmp_path, monkeypatch, metadynamics=_ONE_VARIABLE)
        assert (out / "metadynamics.plumed").is_file()
        assert (out / "production.dcd").is_file()

    def test_the_umbrella_path_already_did_this(self, tmp_path, monkeypatch) -> None:
        """`cv_plan` beside it: the pattern was there to copy. An umbrella
        window runs its stages under its restraint."""
        window = {"collective_variable": "distance", "selection_a": "resid 0 and name O",
                  "selection_b": "resid 1 and name O", "centre": 0.5,
                  "force_constant": 500.0, "index": 0}
        out = _a_biased_run(tmp_path, monkeypatch, umbrella=window)
        assert (out / "umbrella.plumed").is_file()
        assert (out / "production.dcd").is_file()

    def test_the_stage_plan_is_still_a_mapping_afterwards(self, tmp_path, monkeypatch) -> None:
        """The failure was a dict becoming a dataclass, so a run that biases
        two variables at once, through the other planner, runs its stages
        too."""
        pair = {"variables": [dict(_ONE_VARIABLE), dict(_ONE_VARIABLE, selection_a="resid 2 and name O",
                                                         selection_b="resid 3 and name O")],
                "height": 1.0, "pace": 5}
        out = _a_biased_run(tmp_path, monkeypatch, metadynamics=pair)
        assert (out / "production.dcd").is_file()

class TestQBiasesTheSameThingItReports:
    """The CV and the analysis have to share one definition of S.

    A collective variable built from its own contact set would bias one
    quantity while every reported Q described another, and a free energy
    would come out along a coordinate no number in the run corresponds to.
    That disagreement reads as a sampling problem, which is the expensive
    way to discover it, so these tests check the emitted PLUMED against
    the analysis rather than against a copy of the formula.
    """

    @staticmethod
    def _hairpin(n_frames: int = 60, peel: float = 0.5, noise: float = 0.01):
        import mdtraj as md
        import numpy as np

        rng = np.random.RandomState(3)
        top = md.Topology()
        chain = top.add_chain()
        for i in range(14):
            res = top.add_residue("ALA", chain, resSeq=i + 1)
            top.add_atom("CA", md.element.carbon, res)
            top.add_atom("CB", md.element.carbon, res)
        pos = []
        for i in range(7):
            pos += [[i * 0.38, 0.0, 0.0], [i * 0.38, 0.15, 0.0]]
        for i in range(7):
            pos += [[(6 - i) * 0.38, 0.42, 0.0], [(6 - i) * 0.38, 0.27, 0.0]]
        xyz = np.tile(np.array(pos)[None], (n_frames, 1, 1))
        xyz[:, 14:, 1] += np.linspace(0.0, peel, n_frames)[:, None]
        xyz += rng.normal(scale=noise, size=xyz.shape)
        return md.Trajectory(xyz=xyz.astype(np.float32), topology=top)

    @staticmethod
    def _parse(script: str):
        import re

        import numpy as np

        rows = re.findall(
            r"ATOMS\d+=(\d+),(\d+)\s+SWITCH\d+=\{Q R_0=\S+ BETA=(\S+) "
            r"LAMBDA=(\S+) REF=(\S+)\}\s+WEIGHT\d+=(\S+)",
            script,
        )
        assert rows, "no Q contacts found in the emitted script"
        return (
            np.array([[int(r[0]) - 1, int(r[1]) - 1] for r in rows]),
            np.array([float(r[2]) for r in rows]),
            np.array([float(r[3]) for r in rows]),
            np.array([float(r[4]) for r in rows]),
            np.array([float(r[5]) for r in rows]),
        )

    def _script(self, tmp_path, traj, **spec):
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script, plan_from_config)

        reference = tmp_path / "ref.pdb"
        traj[0].save_pdb(str(reference))
        plan = plan_from_config(
            {"collective_variable": "q", "selection": "all",
             "sigma": 0.02, **spec},
            traj.topology,
        )
        return plan, build_plumed_script(plan, reference_pdb=str(reference))

    def test_the_emitted_cv_computes_the_analysis_s_q(self, tmp_path) -> None:
        import mdtraj as md
        import numpy as np

        from fastmdxplora.analysis.qvalue import QValue

        traj = self._hairpin()
        _, script = self._script(tmp_path, traj)
        pairs, beta, lam, r0, weight = self._parse(script)

        # Evaluate what the script says, from the script's own numbers.
        r = md.compute_distances(traj, pairs)
        from_script = (
            weight * (1.0 / (1.0 + np.exp(beta * (r - lam * r0))))
        ).sum(axis=1)

        # The residual is the reference file's own precision, not the
        # translation's: PLUMED takes r0 from a PDB, which stores
        # coordinates to three decimals in Angstroms, so every REF carries
        # about 1e-4 nm no matter how it is formatted here.
        assert np.allclose(
            from_script, QValue(selection="all").compute(traj), atol=2e-4)

    def test_the_weights_make_it_a_fraction(self, tmp_path) -> None:
        import numpy as np

        traj = self._hairpin()
        _, script = self._script(tmp_path, traj)
        *_, weight = self._parse(script)
        # PLUMED sums weight*s(r); the weights are what turn a count of
        # contacts into the fraction the name promises.
        assert np.isclose(weight.sum(), 1.0)
        assert "  SUM" in script.splitlines()

    def test_it_uses_plumed_s_own_q_switching_function(self, tmp_path) -> None:
        """Rather than a hand-written CUSTOM sigmoid.

        PLUMED implements this switching function natively and cites the
        same paper for it, so writing the arithmetic out again would add a
        second place for the definition to drift.
        """
        traj = self._hairpin()
        _, script = self._script(tmp_path, traj)
        assert "SWITCH1={Q " in script
        assert "BETA=50" in script and "LAMBDA=1.8" in script

    def test_atom_numbering_is_plumed_s(self, tmp_path) -> None:
        """PLUMED counts atoms from one, mdtraj from zero.

        An off-by-one here biases a contact between neighbouring atoms and
        the run still completes.
        """
        import numpy as np

        from fastmdxplora.analysis.qvalue import native_contact_pairs

        traj = self._hairpin()
        _, script = self._script(tmp_path, traj)
        pairs, *_ = self._parse(script)
        expected, _ = native_contact_pairs(traj, ref=0)
        assert np.array_equal(np.sort(pairs, axis=0), np.sort(expected, axis=0))

    def test_q_without_a_reference_structure_is_refused(self, tmp_path) -> None:
        import pytest

        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script, plan_from_config)

        traj = self._hairpin()
        plan = plan_from_config(
            {"collective_variable": "q", "selection": "all", "sigma": 0.02},
            traj.topology,
        )
        with pytest.raises(ValueError, match="reference structure"):
            build_plumed_script(plan, reference_pdb=None)

    def test_a_reference_with_no_contacts_is_refused(self, tmp_path) -> None:
        """A fraction of nothing is not a coordinate.

        An extended reference has no native contacts, so S is empty and
        every frame would read the same value: PLUMED would run, deposit
        hills on a constant, and converge on a surface of one point.
        """
        import mdtraj as md
        import numpy as np
        import pytest

        traj = self._hairpin()
        extended = traj[:1]
        xyz = np.zeros_like(extended.xyz)
        xyz[0, :, 0] = np.arange(extended.n_atoms) * 2.0
        stretched = md.Trajectory(xyz=xyz, topology=extended.topology)

        with pytest.raises(ValueError, match="no native contacts"):
            self._script(tmp_path, stretched)

    def test_the_record_states_the_criteria(self, tmp_path) -> None:
        traj = self._hairpin()
        plan, _ = self._script(tmp_path, traj)
        criteria = plan.as_record()["q_criteria"]
        assert criteria["cutoff_nm"] == 0.45
        assert criteria["lambda"] == 1.8
        assert criteria["min_seq_separation"] == 4


class TestTwoVariablesUnderOneDeposition:
    """Biasing two coordinates is one METAD, not two runs.

    The hazard is labels. Every variable's translation emits helper
    definitions -- `lig` and `site` for a ligand distance, `a` and `b` for
    a generic one -- and two variables of the same kind would define them
    twice. PLUMED takes the second definition, so the run biases one
    coordinate twice and reports a surface across two, which is a failure
    that completes successfully.
    """

    @staticmethod
    def _topology():
        import mdtraj as md

        top = md.Topology()
        chain = top.add_chain()
        for i in range(8):
            res = top.add_residue("ALA", chain, resSeq=i + 1)
            for name in ("N", "CA", "C", "O", "CB"):
                top.add_atom(name, md.element.carbon, res)
        return top

    @staticmethod
    def _labels(script):
        out = []
        for line in script.splitlines():
            if not line or line.startswith("#") or ":" not in line:
                continue
            head = line.split(":")[0].strip()
            if head and " " not in head:
                out.append(head)
        return out

    def test_two_variables_of_the_same_kind_keep_their_own_labels(self):
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script_pair, plan_pair_from_config)

        pair = plan_pair_from_config({"variables": [
            {"collective_variable": "distance", "selection_a": "resid 0",
             "selection_b": "resid 1", "sigma": 0.05},
            {"collective_variable": "distance", "selection_a": "resid 2",
             "selection_b": "resid 3", "sigma": 0.05},
        ]}, self._topology())
        labels = self._labels(build_plumed_script_pair(pair))

        assert len(labels) == len(set(labels)), (
            "two definitions share a label, so PLUMED would keep one")
        assert {"a1", "b1", "a2", "b2", "cv1", "cv2"} <= set(labels)

    def test_one_deposition_over_both(self):
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script_pair, plan_pair_from_config)

        pair = plan_pair_from_config({"variables": [
            {"collective_variable": "torsion", "selection": "index 1 2 3 4",
             "sigma": 0.35},
            {"collective_variable": "torsion", "selection": "index 5 6 7 8",
             "sigma": 0.30},
        ]}, self._topology())
        script = build_plumed_script_pair(pair)

        # One METAD, both arguments, one sigma each: two METAD lines would
        # be two one-dimensional runs sharing a trajectory.
        assert script.count("METAD") == 1
        assert "ARG=cv1,cv2" in script
        assert "SIGMA=0.35,0.3 " in script
        assert "PRINT ARG=cv1,cv2,metad.bias" in script

    def test_a_single_variable_script_is_unchanged(self):
        """The suffix defaults to empty, so nothing already written moves."""
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script, plan_from_config)

        script = build_plumed_script(plan_from_config(
            {"collective_variable": "torsion", "selection": "index 1 2 3 4",
             "sigma": 0.35}, self._topology()))
        assert "cv: TORSION" in script
        assert "ARG=cv " in script

    def test_walls_belong_to_the_variable_that_asked_for_them(self):
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script_pair, plan_pair_from_config)

        pair = plan_pair_from_config({"variables": [
            {"collective_variable": "distance", "selection_a": "resid 0",
             "selection_b": "resid 1", "sigma": 0.05,
             "walls": {"upper": 2.5}},
            {"collective_variable": "torsion", "selection": "index 5 6 7 8",
             "sigma": 0.35},
        ]}, self._topology())
        script = build_plumed_script_pair(pair)

        assert "UPPER_WALLS ARG=cv1" in script
        assert "ARG=cv2" not in script.split("UPPER_WALLS")[1].split("\n")[0]

    def test_three_variables_are_refused(self):
        import pytest

        from fastmdxplora.simulation.metadynamics import plan_pair_from_config

        with pytest.raises(ValueError, match="exactly two"):
            plan_pair_from_config({"variables": [
                {"collective_variable": "torsion",
                 "selection": "index 1 2 3 4", "sigma": 0.35},
                {"collective_variable": "torsion",
                 "selection": "index 5 6 7 8", "sigma": 0.35},
                {"collective_variable": "torsion",
                 "selection": "index 9 10 11 12", "sigma": 0.35},
            ]}, self._topology())

    def test_the_record_names_both_and_says_the_hills_are_shared(self):
        from fastmdxplora.simulation.metadynamics import plan_pair_from_config

        pair = plan_pair_from_config({"variables": [
            {"collective_variable": "torsion", "selection": "index 1 2 3 4",
             "sigma": 0.35},
            {"collective_variable": "radius_of_gyration",
             "selection": "all", "sigma": 0.05},
        ]}, self._topology())
        record = pair.as_record()

        assert record["collective_variables"] == [
            "torsion", "radius_of_gyration"]
        assert len(record["dimensions"]) == 2
        assert "one METAD" in record["shared_deposition"]


class TestTheBiasIsLookedUpNotResummed:
    """Without a grid PLUMED sums every hill at every step, so a long run
    slows without bound.

    Found by running, not reading. The V3 alanine-dipeptide run deposits
    1,000 hills per nanosecond (`PACE=500`, 2 fs step) and its instantaneous
    rate fell 312 -> 272 ns/day between 2,000 and 8,000 hills; the fit
    `1/rate = 2.95e-3 + 9.98e-8 * n_hills` puts 100 ns at ~19 hours against
    ~7 with a grid. The run's own estimate never showed it -- OpenMM
    extrapolates the cumulative average speed as constant, so it reads low
    throughout and slides upward all day.

    A grid is exact for a torsion: the variable is periodic on [-pi, pi] and
    cannot leave the grid, so this buys the speed with no assumption.
    """

    @staticmethod
    def _plan(**spec):
        from fastmdxplora.simulation.metadynamics import plan_from_config

        return plan_from_config(spec, _topology(), temperature_K=310.0)

    @staticmethod
    def _metad_line(script: str) -> str:
        line = [ln for ln in script.splitlines() if ln.startswith("metad:")]
        assert len(line) == 1, f"expected one METAD line, got {line}"
        return line[0]

    def test_a_torsion_is_gridded_over_the_whole_turn(self) -> None:
        from fastmdxplora.simulation.metadynamics import build_plumed_script

        plan = self._plan(collective_variable="torsion",
                          selection="resid 0 and name N CA C O", sigma=0.35)
        line = self._metad_line(build_plumed_script(plan))
        assert "GRID_MIN=-pi" in line
        assert "GRID_MAX=pi" in line
        assert "GRID_BIN=" in line

    def test_the_spacing_is_finer_than_the_hill_it_resolves(self) -> None:
        """A grid coarser than sigma smooths the hills it is meant to store,
        which changes the surface rather than the speed."""
        import math
        import re

        from fastmdxplora.simulation.metadynamics import (
            GRID_POINTS_PER_SIGMA, build_plumed_script)

        for sigma in (0.10, 0.35, 0.60):
            plan = self._plan(collective_variable="torsion",
                              selection="resid 0 and name N CA C O",
                              sigma=sigma)
            line = self._metad_line(build_plumed_script(plan))
            bins = int(re.search(r"GRID_BIN=(\d+)", line).group(1))
            spacing = (2.0 * math.pi) / bins
            assert spacing <= sigma / GRID_POINTS_PER_SIGMA + 1e-12, (
                f"sigma={sigma}: spacing {spacing:.4f} is coarser than "
                f"sigma/{GRID_POINTS_PER_SIGMA}")

    def test_two_torsions_get_one_bound_per_variable(self) -> None:
        """PLUMED reads these positionally: one value short and it pairs the
        second variable's bound with the first's."""
        import re

        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script_pair, plan_pair_from_config)

        pair = plan_pair_from_config(
            {"variables": [
                {"collective_variable": "torsion",
                 "selection": "resid 0 and name N CA C O", "sigma": 0.35},
                {"collective_variable": "torsion",
                 "selection": "resid 1 and name N CA C O", "sigma": 0.20},
            ]},
            _topology(), temperature_K=300.0)
        line = self._metad_line(build_plumed_script_pair(pair))
        assert "GRID_MIN=-pi,-pi" in line
        assert "GRID_MAX=pi,pi" in line
        bins = re.search(r"GRID_BIN=(\S+)", line).group(1).split(",")
        assert len(bins) == 2
        # The narrower sigma needs the finer grid; equal counts would mean
        # the sigmas were not read per variable.
        assert int(bins[1]) > int(bins[0])

    def test_an_unbounded_variable_is_left_ungridded(self) -> None:
        """PLUMED stops the run when a variable steps outside its grid. A
        radius of gyration has no ceiling the setup knows, so a guessed one
        trades a slow run for one that dies partway with a partial HILLS."""
        from fastmdxplora.simulation.metadynamics import build_plumed_script

        plan = self._plan(collective_variable="radius_of_gyration",
                          selection="protein and name CA", sigma=0.05)
        assert "GRID_" not in self._metad_line(build_plumed_script(plan))

    def test_a_wall_does_not_count_as_a_bound(self) -> None:
        """Walls are restraints, not barriers: a soft one is crossed, and the
        crossing would end the run rather than push the variable back."""
        from fastmdxplora.simulation.metadynamics import build_plumed_script

        plan = self._plan(collective_variable="ligand_distance",
                          ligand_resname="BNZ",
                          site_selection="resid 1 to 3 and name CA",
                          sigma=0.05, walls={"upper": 2.5})
        line = self._metad_line(build_plumed_script(plan))
        assert "GRID_" not in line
        assert "UPPER_WALLS" in build_plumed_script(plan)

    def test_a_mixed_pair_is_left_ungridded(self) -> None:
        """One bounded variable does not make the pair griddable: PLUMED
        grids all arguments or none."""
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script_pair, plan_pair_from_config)

        pair = plan_pair_from_config(
            {"variables": [
                {"collective_variable": "torsion",
                 "selection": "resid 0 and name N CA C O", "sigma": 0.35},
                {"collective_variable": "radius_of_gyration",
                 "selection": "protein and name CA", "sigma": 0.05},
            ]},
            _topology(), temperature_K=300.0)
        assert "GRID_" not in self._metad_line(build_plumed_script_pair(pair))


_ONE_VARIABLE = {"collective_variable": "distance", "selection_a": "resid 0 and name O",
                 "selection_b": "resid 1 and name O", "sigma": 0.05, "height": 1.0, "pace": 5}


def _a_biased_run(root, monkeypatch, **bias):
    """A real run of a small water box with `bias`, through minimisation,
    both equilibrations and production. PLUMED's own force is the one part
    stood in, as it is not installed everywhere; the script it would be
    given is still written. Returns the run's folder."""
    from fastmdxplora.simulation import plumed
    from fastmdxplora.simulation.runner import run_simulation
    from tests._the_phase import a_prepared_water_box

    monkeypatch.setattr(plumed, "add_plumed_force", lambda *args, **kwargs: None)
    root.mkdir(parents=True, exist_ok=True)
    out = root / "out"
    run_simulation(**a_prepared_water_box(root), output_dir=str(out), production_steps=20,
                   nvt_steps=10, npt_steps=10, minimize=True, platform="CPU", **bias)
    return out
