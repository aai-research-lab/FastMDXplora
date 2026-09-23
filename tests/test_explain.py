"""Why each step happens, said while it happens.

Molecular dynamics has a lot of steps that are obvious once you know them and
opaque before that. A pipeline that does all of it silently is faster to use
and teaches nothing: somebody running their first simulation ends up with a
trajectory they cannot defend.
"""

from __future__ import annotations

import pytest

import io



class TestTheExplanationsThemselves:
    def test_every_one_says_why_rather_than_what(self) -> None:
        """The step already says what happened. An explanation that repeats it
        has added nothing."""
        from fastmdxplora.explain import EXPLANATIONS

        assert EXPLANATIONS
        for key, entry in EXPLANATIONS.items():
            assert len(entry.why) > 120, (
                f"{key}: too short to explain anything"
            )
            assert len(entry.why) < 700, (
                f"{key}: this is documentation, not a step note"
            )

    def test_a_reference_is_a_real_citation_or_absent(self) -> None:
        """Inventing one to look thorough would be worse than having none."""
        from fastmdxplora.explain import EXPLANATIONS

        for key, entry in EXPLANATIONS.items():
            if entry.reference is None:
                continue
            assert any(char.isdigit() for char in entry.reference), (
                f"{key}: a reference should carry a year"
            )
            assert "," in entry.reference, (
                f"{key}: a reference should name authors and a venue"
            )

    def test_an_unknown_key_returns_nothing_rather_than_raising(self) -> None:
        """A missing explanation should not stop a run. The guard below fails
        on one, so it does not go unnoticed either."""
        from fastmdxplora.explain import explain

        assert explain("no_such_step") is None

    def test_it_wraps_for_a_terminal(self) -> None:
        from fastmdxplora.explain import EXPLANATIONS

        entry = next(iter(EXPLANATIONS.values()))
        for line in entry.as_text().splitlines():
            assert len(line) <= 100, "an explanation should not run off screen"


class TestKeysRatherThanMatching:
    """A call site names the explanation it wants, so one cannot drift onto
    the wrong step and a step cannot quietly lose its explanation."""

    def test_every_key_a_call_site_asks_for_exists(self) -> None:
        import re
        from pathlib import Path

        from fastmdxplora.explain import EXPLANATIONS

        package = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora"
        asked = set()
        for path in package.rglob("*.py"):
            for match in re.finditer(r'explain=["\']([a-z_]+)["\']',
                                     path.read_text(encoding="utf-8")):
                asked.add(match.group(1))

        missing = sorted(asked - set(EXPLANATIONS))
        assert not missing, f"these steps ask for explanations that do not exist: {missing}"

    def test_the_presenter_takes_a_key_not_a_string(self) -> None:
        """Matching on the step's message would have made a mismatch
        silent."""
        import inspect

        from fastmdxplora.utils.presenter import SessionPresenter

        signature = inspect.signature(SessionPresenter.step)
        assert "explain" in signature.parameters


class TestItPrintsAndCanBeSilenced:
    @staticmethod
    def _run(explain_enabled):
        from fastmdxplora.utils.presenter import SessionPresenter

        out = io.StringIO()
        presenter = SessionPresenter(stream=out, explain=explain_enabled)
        presenter.step("Fixed PDB with PDBFixer (pH=7.4)", explain="protonation")
        return out.getvalue()

    def test_on_by_default(self) -> None:
        import inspect

        from fastmdxplora.utils.presenter import SessionPresenter

        assert inspect.signature(
            SessionPresenter.__init__).parameters["explain"].default is True

    def test_it_explains_when_on(self) -> None:
        printed = self._run(True)
        assert "X-rays do not see hydrogens" in printed
        assert "PROPKA3" in printed

    def test_it_is_silent_when_off(self) -> None:
        printed = self._run(False)
        assert "Fixed PDB" in printed, "the step itself still appears"
        assert "X-rays" not in printed

    def test_a_step_with_no_key_explains_nothing(self) -> None:
        from fastmdxplora.utils.presenter import SessionPresenter

        out = io.StringIO()
        SessionPresenter(stream=out, explain=True).step("Wrote report.md")
        assert out.getvalue().strip() == "✓ Wrote report.md".replace("✓ ", "✓ ")


class TestItReachesTheInterfaces:
    def test_the_setting_is_declared(self) -> None:
        from fastmdxplora.config.schema import TOP_LEVEL_KEYS

        assert "explain" in TOP_LEVEL_KEYS

    def test_it_defaults_to_on(self) -> None:
        from fastmdxplora.config.schema import TOP_LEVEL

        field = TOP_LEVEL.get("explain")
        assert field.default is True

    def test_the_command_line_can_turn_it_off(self) -> None:
        from fastmdxplora.cli.main import _build_parser

        args = _build_parser().parse_args(
            ["explore", "--system", "1UBQ", "--no-explain"])
        assert args.explain is False

    def test_and_leaves_it_on_otherwise(self) -> None:
        from fastmdxplora.cli.main import _build_parser

        args = _build_parser().parse_args(["explore", "--system", "1UBQ"])
        assert args.explain is True


class TestWhatTheStepsThemselvesSay:
    """A step's message is read far more often than any explanation under it,
    and two of them were wrong."""

    def test_the_solvation_message_has_no_nested_parentheses(self, a_setup_run) -> None:
        """It read "Solvating (box=cube) (padding=1.00 nm, ...)" -- a
        parenthesis inside a parenthesis, introduced when the message learned
        to choose between solvating and embedding. As logged by a real setup."""
        said = next(m for m in a_setup_run.messages if m.startswith("Solvating in a "))
        assert "padding=1.20 nm, ions=" in said
        assert "(" not in said and ")" not in said, said

    def test_the_force_field_step_says_what_it_resolved_to(self, a_setup_run) -> None:
        """It said "(auto)", which tells a reader nothing -- while the banner
        two inches above said amber-openff (auto). A setup left to choose its
        own force field names the one it chose."""
        from fastmdxplora.setup.forcefields import AUTO_FORCEFIELD

        step = next(s for s in a_setup_run.steps if s.startswith("Solvated and parameterized"))
        assert step == f"Solvated and parameterized ({AUTO_FORCEFIELD})"
        assert "auto" not in step.replace(str(AUTO_FORCEFIELD), "")

    def test_no_step_message_nests_parentheses(self) -> None:
        """This mistake has been made twice: once when the solvation message
        learned to choose between solvating and embedding, and again in the
        fix for it, when a label already carrying "(auto)" was passed to a
        step that wraps its label in parentheses.

        Checking the shape rather than the instance, because the instance is
        not what recurs.
        """
        import re
        from pathlib import Path

        package = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora"
        offenders = []
        for path in package.rglob("*.py"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if ".step(" not in line and "presenter.info(" not in line:
                    continue
                # A message that both wraps a value in parentheses and builds
                # that value with parentheses of its own.
                if re.search(r'\(\{[a-z_]+\}\)', line) and "(" in line:
                    text = re.search(r'f?"([^"]+)"', line)
                    if text and text.group(1).count("(") > 1:
                        offenders.append(f"{path.name}: {text.group(1)[:60]}")
        assert not offenders, f"these step messages nest parentheses: {offenders}"


class TestTheGuiCanTurnItOff:
    """The form was built from phase settings only, so a top-level one reached
    the command line and the config file and not the browser -- which was the
    one interface that could not turn explanations on or off.
    """

    def test_the_form_is_offered_them(self) -> None:
        from fastmdxplora.gui.schema_payload import schema_payload

        offered = {f["name"] for f in schema_payload()["run_options"]}
        assert "explain" in offered
        assert "verbose" in offered

    def test_structural_top_level_settings_are_not_drawn_twice(self) -> None:
        """Where the run goes and which phases run already have their own
        controls, so they are excluded with a reason rather than by
        omission."""
        from fastmdxplora.gui.schema_payload import (
            _STRUCTURAL_TOP_LEVEL,
            schema_payload,
        )

        offered = {f["name"] for f in schema_payload()["run_options"]}
        assert not (offered & set(_STRUCTURAL_TOP_LEVEL))
        for name, reason in _STRUCTURAL_TOP_LEVEL.items():
            assert reason, f"{name} is excluded without saying why"

    def test_every_top_level_setting_is_drawn_or_excluded(self) -> None:
        """So a new one cannot be added and quietly miss the form, which is
        how explain came to reach two interfaces out of three."""
        from fastmdxplora.config.schema import TOP_LEVEL
        from fastmdxplora.gui.schema_payload import (
            _STRUCTURAL_TOP_LEVEL,
            schema_payload,
        )

        offered = {f["name"] for f in schema_payload()["run_options"]}
        for field in TOP_LEVEL.fields:
            assert field.name in offered or field.name in _STRUCTURAL_TOP_LEVEL, (
                f"{field.name} reaches neither the form nor the exclusion list"
            )

    def test_turning_it_off_reaches_the_config(self) -> None:
        from fastmdxplora.gui.config_builder import build_config

        config = build_config({"system": "1UBQ", "__run__": {"explain": False}})
        assert config["explain"] is False

    def test_leaving_it_on_says_nothing(self) -> None:
        """Restating a default buries the settings that were chosen."""
        from fastmdxplora.gui.config_builder import build_config

        config = build_config({"system": "1UBQ", "__run__": {"explain": True}})
        assert "explain" not in config

    def test_the_form_draws_them_under_their_own_heading(self) -> None:
        from pathlib import Path

        script = (Path(__file__).resolve().parents[1] / "src" / "fastmdxplora"
                  / "gui" / "static" / "run-builder.js").read_text(encoding="utf-8")
        assert "runOptionsSection" in script
        assert "RUN_OPTIONS_KEY" in script


def test_the_run_options_section_uses_the_layout_the_stylesheet_has() -> None:
    """A bare heading with controls appended ran every label into its help
    text, because the stylesheet lays out a head and a body grid and nothing
    else."""
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "src" / "fastmdxplora"
              / "gui" / "static" / "run-builder.js").read_text(encoding="utf-8")
    section = script[script.index("function runOptionsSection"):]
    section = section[:section.index("function settingsSection")]

    assert "run-section-head" in section
    assert "run-section-body" in section
    assert "run-section-title" not in section, (
        "that class is not in the stylesheet"
    )


def test_an_analysis_needing_water_does_not_run_without_it() -> None:
    """water_sites refused on a system with no water, which is right, and it
    failed the analysis phase, which is not: an implicit-solvent run or a
    trajectory stripped of solvent has no water sites and that is not an
    error."""
    from fastmdxplora.analysis.water_sites import WaterSites

    assert WaterSites.requires_water is True


def test_the_gate_is_applied_wherever_the_ligand_gate_is() -> None:
    # A dry trajectory with no ligand, in a periodic box: the analyses that
    # need water are left out of the plan in every path that leaves out the
    # ones that need a ligand -- the default plan and one built by
    # exclusion. With water added, they come back.
    import tempfile
    from pathlib import Path

    import mdtraj as md
    import numpy as np

    from fastmdxplora.analysis.orchestrator import _REGISTRY, AnalysisOrchestrator

    def plans(waters: int):
        topology = md.Topology()
        chain = topology.add_chain()
        for _ in range(6):
            residue = topology.add_residue("ALA", chain)
            for name, element in (("N", "nitrogen"), ("CA", "carbon"), ("C", "carbon"),
                                  ("O", "oxygen"), ("CB", "carbon")):
                topology.add_atom(name, getattr(md.element, element), residue)
        solvent = topology.add_chain()
        for _ in range(waters):
            residue = topology.add_residue("HOH", solvent)
            topology.add_atom("O", md.element.oxygen, residue)
        n = topology.n_atoms
        xyz = np.random.default_rng(0).uniform(0.5, 2.5, size=(5, n, 3)).astype(np.float32)
        trajectory = md.Trajectory(xyz, topology, unitcell_lengths=np.full((5, 3), 3.0),
                                   unitcell_angles=np.full((5, 3), 90.0))
        orchestrator = AnalysisOrchestrator.__new__(AnalysisOrchestrator)
        orchestrator.traj = trajectory
        orchestrator.ligand_resname = None
        orchestrator.output_dir = Path(tempfile.mkdtemp())
        return orchestrator._build_plan(None, None), orchestrator._build_plan(None, ["rmsf"])

    needs_water = {n for n, c in _REGISTRY.items() if getattr(c, "requires_water", False)}
    needs_ligand = {n for n, c in _REGISTRY.items() if getattr(c, "requires_ligand", False)}
    for plan in plans(waters=0):
        assert not needs_ligand & set(plan)
        assert not needs_water & set(plan), sorted(needs_water & set(plan))
    by_default, by_exclusion = plans(waters=40)
    assert needs_water & set(by_default) and needs_water & set(by_exclusion)

def test_a_class_importing_openmm_is_guarded() -> None:
    """A regex removing one test swallowed the decorator above the class that
    followed it, so an end-to-end OpenMM test ran unguarded in CI.

    Checked per class, by parsing. A file-level check does not work here: the
    file still mentions HAS_OPENMM elsewhere, so it looked guarded while the
    class that needed the guard had lost it. The first version of this test
    passed with the decorator removed, which is a guard that does not guard.
    """
    import ast
    from pathlib import Path

    def imports_openmm(node: ast.AST) -> bool:
        for child in ast.walk(node):
            if isinstance(child, ast.Import):
                if any(a.name.split(".")[0] == "openmm" for a in child.names):
                    return True
            elif isinstance(child, ast.ImportFrom):
                if (child.module or "").split(".")[0] == "openmm":
                    return True
        return False

    def guarded(node: ast.AST) -> bool:
        text = " ".join(ast.dump(d) for d in getattr(node, "decorator_list", []))
        if "skipif" in text or "importorskip" in text:
            return True
        # Or the first statement in its body is an importorskip.
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                function = child.func
                if getattr(function, "attr", "") == "importorskip":
                    return True
        return False

    def tries_the_import(node: ast.AST) -> bool:
        """Every openmm import in it sits in a try that catches its absence."""
        catching = [t for t in ast.walk(node) if isinstance(t, ast.Try) and any(
            h.type is None or any(name in ast.dump(h.type) for name in
                                  ("ImportError", "ModuleNotFoundError", "Exception"))
            for h in t.handlers)]
        inside = {id(c) for t in catching for statement in t.body for c in ast.walk(statement)}
        imports = [c for c in ast.walk(node) if imports_openmm(c) and isinstance(
            c, (ast.Import, ast.ImportFrom))]
        return bool(imports) and all(id(i) in inside for i in imports)

    unguarded = []
    here = Path(__file__).resolve().parent
    # The shared helpers too: a helper that needs openmm fails every test
    # that calls it, wherever it lives.
    for path in sorted(here.glob("test_*.py")) + sorted(here.glob("_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                continue
            if not imports_openmm(node):
                continue
            # A helper whose whole job is to try the import is the guard, not
            # something needing one. Only that kind: a helper that imports it
            # to use it fails whatever calls it, and exempting every helper by
            # its name let one through that failed three tests on a machine
            # without openmm.
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("test_") \
                    and (tries_the_import(node) or guarded(node)):
                continue
            if guarded(node):
                continue
            # A class may guard each method rather than itself.
            if isinstance(node, ast.ClassDef):
                methods = [m for m in node.body
                           if isinstance(m, ast.FunctionDef) and imports_openmm(m)]
                if methods and all(guarded(m) for m in methods):
                    continue
            unguarded.append(f"{path.name}:{node.name}")

    assert not unguarded, (
        f"these import openmm with no guard: {unguarded}. The machines that "
        "skip such a test are the ones that cannot run it."
    )


class TestTheBannerReportsTheRunItIsAbout:
    """It reconstructed the settings from sys.argv, so a run driven by a
    config file printed the defaults for the step counts, the timestep and
    the temperature while using the config's values.

    A banner that reports different settings from the ones in force is worse
    than no banner: it is read at a glance and believed.
    """

    @staticmethod
    def _banner(**fields):
        import io

        from fastmdxplora.utils.presenter import SessionPresenter

        out = io.StringIO()
        SessionPresenter(stream=out, explain=False).banner(
            System="181L", Output="x", Version="2.3.0", **fields)
        return out.getvalue()

    def test_a_config_driven_run_shows_its_own_steps(self) -> None:
        printed = self._banner(nvt_steps="500", npt_steps="500",
                               production_steps="5000")
        assert "500 steps" in printed
        assert "5,000 steps" in printed
        assert "1,000,000" not in printed, "the default leaked through"

    def test_the_total_follows(self) -> None:
        printed = self._banner(nvt_steps="500", npt_steps="500",
                               production_steps="5000")
        assert "6,000 steps" in printed

    def test_without_fields_it_still_shows_the_defaults(self) -> None:
        printed = self._banner()
        assert "steps" in printed

    def test_the_orchestrator_passes_what_it_resolved(self, tmp_path, monkeypatch) -> None:
        # The banner is handed the run's own settings, not left to rebuild
        # them from a command line that never mentioned them: a study driven
        # by a config file showed the defaults while using the config's.
        from fastmdxplora.orchestrator import FastMDXplora
        from fastmdxplora.utils.presenter import SessionPresenter

        shown = {}
        monkeypatch.setattr(SessionPresenter, "banner",
                            lambda self, **fields: shown.update(fields))
        FastMDXplora(system="1UBQ", output_dir=tmp_path / "study",
                     options={"simulation": {"temperature_K": 310.0, "timestep_fs": 4.0}})
        assert shown["temperature_K"] == "310.0"
        assert shown["timestep_fs"] == "4.0"


@pytest.fixture(scope="module")
def a_setup_run(tmp_path_factory):
    """One real setup of a three-residue peptide, with what it said."""
    from tests._the_phase import a_real_setup

    return a_real_setup(tmp_path_factory.mktemp("explained_setup"))
