"""A config option the schema accepts and nothing acts on.

`resume_from` was added to the schema, added to `run_simulation`'s
signature, and never connected between the two. So a segment's config said
where to continue from, validation accepted it, and the run started from
the pre-equilibration state instead -- silently. The trajectories looked
fine. What gave it away was a run going unstable, which is luck: at a
sensible density it would have produced a plausible trajectory that was
not the study anybody asked for.

That is the failure this package exists to prevent, and it happened inside
it. The loader refuses a setting it does not know; nothing was checking
that a setting it does know reaches the code that would honour it.

So: every option the simulation schema declares must appear in the phase
that builds the runner's arguments, or be named here as something consumed
elsewhere. The exemption list is short, explicit, and says where each one
goes -- an exemption without a reason is how this bug would come back.
"""

from __future__ import annotations

import inspect
import re
import unittest

from fastmdxplora.config.schema import PHASE_SCHEMAS
from fastmdxplora.simulation import pipeline
from fastmdxplora.simulation.runner import run_simulation

#: Settings the simulation phase declares and the runner never sees,
#: because something else consumes them. Each needs a reason.
CONSUMED_ELSEWHERE = {
    "extra_ns": "read by the continuation planner, not the runner: it says "
                "how much more production to run on top of what a study "
                "already has, which resume_from naming a study settles "
                "before a runner is built at all",
    # Not a setting about the science. It says how the phase was written,
    # and `config.agent_modes` reads it to decide which artifacts carry the
    # unchecked mark. The runner does not need it and should not: a phase
    # runs the same way whoever wrote it, which is the point.
    "agent": "read by config.agent_modes for the record and the marking",
    "dashboard_binding_pocket_cutoff_A": "read by the dashboard, not the run",
    "dashboard_ligand_resname": "read by the dashboard, not the run",
    "dashboard_max_playback_frames": "read by the dashboard, not the run",
}


def _settings_the_phase_reads() -> set[str]:
    """Names pulled out of `params` where the runner's arguments are built.

    Read from the source rather than by calling it, because calling it
    needs a prepared system and the question is structural. The pattern
    allows capitals: `temperature_K` is passed, and a lowercase-only
    pattern reported it as missing -- a false alarm that cost a minute and
    is worth remembering, since a test that cries wolf gets muted.
    """
    source = inspect.getsource(pipeline)
    return set(re.findall(r"""params(?:\.get\(|\[)["']([A-Za-z0-9_]+)""",
                          source))


def _settings_referenced_anywhere(phase: str) -> set[str]:
    """Every schema name mentioned outside the declaration itself.

    A weaker check than the simulation one, and the right one for phases
    with no single place where arguments are assembled. `analysis` and
    `report` settings are read where they are used rather than marshalled
    in one function, so "is it read where the runner is built" has no
    answer for them. "Is it read at all" does.
    """
    import pathlib

    import fastmdxplora

    root = pathlib.Path(fastmdxplora.__file__).parent
    source = "\n".join(f.read_text(encoding="utf-8")
                       for f in root.rglob("*.py"))
    referenced = set()
    for field in PHASE_SCHEMAS[phase].fields:
        name = field.name
        # The Field(...) declaration itself does not count as a reference,
        # or every setting would look wired.
        elsewhere = source.replace('Field("' + name + '"', "")
        if ('"' + name + '"') in elsewhere or ("'" + name + "'") in elsewhere:
            referenced.add(name)
    return referenced


class TestEverySettingReachesSomething(unittest.TestCase):

    def test_no_simulation_setting_is_silently_ignored(self):
        declared = {f.name for f in PHASE_SCHEMAS["simulation"].fields}
        reached = _settings_the_phase_reads()
        stranded = sorted(declared - reached - set(CONSUMED_ELSEWHERE))
        self.assertEqual(
            stranded, [],
            f"these are declared in the simulation schema and never read "
            f"where the runner's arguments are built: {stranded}. A setting "
            "that validates and does nothing is worse than one that is "
            "refused: the study runs, the output looks ordinary, and it is "
            "not the study that was asked for. Pass it through, or add it "
            "to CONSUMED_ELSEWHERE with a note saying what does read it.")

    def test_resume_from_in_particular(self):
        # Named on its own because it is the one that got through, and a
        # general assertion that passes tells you nothing about the
        # specific case that failed.
        self.assertIn("resume_from", _settings_the_phase_reads())
        self.assertIn("resume_from",
                      inspect.signature(run_simulation).parameters)

    def test_no_setup_setting_is_silently_ignored(self):
        # Checked the same way as simulation, because setup also builds its
        # arguments in one place. It came back clean at 42 of 42, which is
        # what made the simulation result worth trusting rather than
        # dismissing as a quirk of how the check was written.
        from fastmdxplora.setup import pipeline as setup_pipeline

        declared = {f.name for f in PHASE_SCHEMAS["setup"].fields}
        source = inspect.getsource(setup_pipeline)
        reached = set(re.findall(
            r"""params(?:\.get\(|\[)["']([A-Za-z0-9_]+)""", source))
        stranded = sorted(declared - reached - set(CONSUMED_ELSEWHERE))
        self.assertEqual(stranded, [], f"stranded in setup: {stranded}")

    def test_no_analysis_or_report_setting_is_unreferenced(self):
        for phase in ("analysis", "report"):
            with self.subTest(phase=phase):
                declared = {f.name for f in PHASE_SCHEMAS[phase].fields}
                stranded = sorted(
                    declared - _settings_referenced_anywhere(phase))
                self.assertEqual(
                    stranded, [],
                    f"declared in the {phase} schema and mentioned nowhere "
                    f"else in the package: {stranded}")

    def test_the_agent_exemption_is_real(self):
        # An exemption is a promise that something else reads the setting.
        # Worth checking for this one, because it was added by the person
        # who also added the setting, and an exemption nobody verifies is
        # how a stranded setting hides.
        from fastmdxplora.config.agent_modes import resolve_agent_modes

        modes = resolve_agent_modes(
            {"agent": "assisted", "analysis": {"agent": "unvalidated"}})
        self.assertEqual(modes.of("analysis"), "unvalidated")
        self.assertFalse(modes.is_checked("analysis"))

    def test_every_exemption_says_why(self):
        for name, reason in CONSUMED_ELSEWHERE.items():
            with self.subTest(setting=name):
                self.assertTrue(reason and len(reason) > 10)

    def test_no_exemption_outlives_its_setting(self):
        # An exemption for a setting that no longer exists is a note about
        # nothing, and it makes the list harder to read and so less likely
        # to be read.
        declared = {f.name for f in PHASE_SCHEMAS["simulation"].fields}
        for name in CONSUMED_ELSEWHERE:
            with self.subTest(setting=name):
                self.assertIn(name, declared)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
