"""No test reads the package's source in place of running it.

A test that searches a function's source for a phrase passes the moment it
is written and fails only when the wording changes, never when the
behaviour breaks. Converting such tests to run the code found real
defects they had been hiding -- a backend crash that killed `fastmdx
info`, reporter files a failed run left open on every path -- and some
that asserted the defect itself.

This is a ratchet: the list below may only shrink. A new test that reads
source (through `inspect.getsource`, `getsourcelines`, `getfile` or
`getsourcefile`, directly or through a helper in its file) fails here
until it is listed with a reason, and a test that stops reading source
fails here until it is taken off. A few are kept on purpose, where the
text is itself the thing being checked; each says why.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent
READS = {"getsource", "getsourcelines", "getfile", "getsourcefile"}

#: test -> why it reads source. "not yet converted" marks the ones still to
#: be rewritten to run the code; anything else is kept on purpose.
READS_THE_SOURCE: dict[str, str] = {'test_a_figure_says_what_it_shows.py::TestEveryRowCanBeToldFromEveryOther.test_the_compute_path_records_the_atom_names': 'not '
                                                                                                                          'yet '
                                                                                                                          'converted',
 'test_a_log_says_where_a_run_begins.py::TestItNeverStopsARun.test_the_first_run_gets_no_banner': 'not '
                                                                                                  'yet '
                                                                                                  'converted',
 'test_a_run_says_which_platforms_it_found.py::TestItNeverStopsARunItCannotDescribe.test_it_runs_before_a_platform_is_chosen': 'not '
                                                                                                                               'yet '
                                                                                                                               'converted',
 'test_a_setting_that_validates_is_a_setting_that_runs.py::TestEverySettingReachesSomething.test_no_setup_setting_is_silently_ignored': 'not '
                                                                                                                                        'yet '
                                                                                                                                        'converted',
 'test_a_setting_that_validates_is_a_setting_that_runs.py::TestEverySettingReachesSomething.test_no_simulation_setting_is_silently_ignored': 'not '
                                                                                                                                             'yet '
                                                                                                                                             'converted',
 'test_a_setting_that_validates_is_a_setting_that_runs.py::TestEverySettingReachesSomething.test_resume_from_in_particular': 'not '
                                                                                                                             'yet '
                                                                                                                             'converted',
 'test_a_superposition_needs_three_atoms.py::TestTheThresholdIsDeclared.test_every_analysis_that_superposes_is_covered': 'not '
                                                                                                                         'yet '
                                                                                                                         'converted',
 'test_analyses_do_not_disturb_each_other.py::TestAnAnalysisGivesTheSameAnswerInCompany.test_the_orchestrator_hands_out_copies': 'not '
                                                                                                                                 'yet '
                                                                                                                                 'converted',
 'test_analysis_layer.py::TestASettingThatDoesNothingIsNotOffered.test_every_analysis_that_ignores_it_declares_so': 'kept: '
                                                                                                                    'a '
                                                                                                                    'declaration '
                                                                                                                    'and '
                                                                                                                    'its '
                                                                                                                    'use '
                                                                                                                    'agreeing '
                                                                                                                    'across '
                                                                                                                    'every '
                                                                                                                    'analysis; '
                                                                                                                    'shown '
                                                                                                                    'by '
                                                                                                                    'behaviour '
                                                                                                                    'it '
                                                                                                                    'would '
                                                                                                                    'run '
                                                                                                                    'each '
                                                                                                                    'analysis '
                                                                                                                    'twice, '
                                                                                                                    'most '
                                                                                                                    'needing '
                                                                                                                    'a '
                                                                                                                    'ligand, '
                                                                                                                    'water '
                                                                                                                    'or '
                                                                                                                    'a '
                                                                                                                    'biased '
                                                                                                                    'run',
 'test_binding.py::TestItSitsBehindTheOverlapGate.test_the_campaign_uses_the_rule_rather_than_repeating_it': 'not '
                                                                                                             'yet '
                                                                                                             'converted',
 'test_environment_record.py::TestItReachesTheManifest.test_the_orchestrator_writes_it': 'not yet '
                                                                                         'converted',
 'test_interface_parity.py::TestArtifactLayout.test_ligands_are_not_written_to_a_nested_setup_directory': 'not '
                                                                                                          'yet '
                                                                                                          'converted',
 'test_live_status.py::TestEveryStageShowsProgress.test_every_call_site_passes_it': 'not yet '
                                                                                    'converted',
 'test_live_status.py::TestTheWordmarkIsDrawnOnce.test_there_is_one_glyph_set': 'kept: one '
                                                                                'definition of the '
                                                                                'glyphs and one of '
                                                                                'the wordmark is a '
                                                                                'fact about the '
                                                                                'text; what the '
                                                                                'glyphs are is '
                                                                                'tested beside it',
 'test_one_card_one_line.py::TestTheMachineCanMeasureItself.test_it_warms_up_before_timing': 'not '
                                                                                             'yet '
                                                                                             'converted',
 'test_one_study_three_languages.py::TestAResolvedConfigTranslatesToWhatWasChosen.test_the_one_setting_dropped_by_the_mirror_is_reached_anyway': 'not '
                                                                                                                                                 'yet '
                                                                                                                                                 'converted',
 'test_orchestrator.py::test_the_resolved_config_exists_before_the_first_phase_runs': 'not yet '
                                                                                      'converted',
 'test_presenter.py::TestTheRunSaysHowFarThroughItIs.test_a_stage_steps_in_chunks_so_it_can_report': 'not '
                                                                                                     'yet '
                                                                                                     'converted',
 'test_settings_a_novice_cannot_guess.py::TestAskingForWorkersAsksForParallel.test_the_source_infers_it': 'not '
                                                                                                          'yet '
                                                                                                          'converted',
 'test_simulation_phase.py::TestDefaults.test_nothing_still_reads_one': 'not yet converted',
 'test_the_box_is_consulted.py::TestTheRuleIsGivenTheOption.test_every_dispatch_line_passes_periodic': 'not '
                                                                                                       'yet '
                                                                                                       'converted'}


def _called(node: ast.AST) -> set[str]:
    names = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            func = call.func
            names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return names


def _tests_that_read_the_source() -> set[str]:
    """Every test function that reads source itself, or calls a function in
    its own file that does -- followed through helpers, so a new test cannot
    slip past by reading through an old one's helper."""
    found = set()
    for path in sorted(TESTS.glob("*.py")):
        functions: dict[str, tuple[str, set[str]]] = {}

        def visit(node, prefix="", functions=functions):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    visit(child, prefix + child.name + ".")
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions[prefix + child.name] = (child.name, _called(child))

        visit(ast.parse(path.read_text(encoding="utf-8")))
        reading = {name for name, (_, calls) in functions.items() if calls & READS}
        while True:
            names = {functions[name][0] for name in reading}
            more = {name for name, (_, calls) in functions.items()
                    if name not in reading and calls & names}
            if not more:
                break
            reading |= more
        found |= {f"{path.name}::{name}" for name in reading
                  if name.split(".")[-1].startswith("test_")}
    return found


def test_no_new_test_reads_the_source() -> None:
    new = sorted(_tests_that_read_the_source() - set(READS_THE_SOURCE))
    assert not new, (
        "these read the package's source instead of running it; test the "
        "behaviour, or list the test in READS_THE_SOURCE with the reason the "
        f"text itself is what needs checking: {new}")


def test_a_converted_test_is_taken_off_the_list() -> None:
    gone = sorted(set(READS_THE_SOURCE) - _tests_that_read_the_source())
    assert not gone, f"these no longer read source; take them off READS_THE_SOURCE: {gone}"


def test_every_entry_says_why() -> None:
    assert all(reason.strip() for reason in READS_THE_SOURCE.values())
