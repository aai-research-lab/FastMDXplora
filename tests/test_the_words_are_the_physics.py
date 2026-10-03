"""What a person reads says equilibrated, converged or determined.

"Settled" is not a physics term, and it was doing the work of three that
are: an observable equilibrates, a bias or a surface converges, and a mean
or a state is determined. The name was retired from the statistics (the
`Settled` alias stays for old callers, the `settled` key for old records),
and it lingered in what the software says: the Agent's instructions, the
refusals, the builder, the Overview, a case of the guardrail corpus. This
holds the words a person reads to the physics.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "fastmdxplora"
WORD = re.compile(r"settl", re.IGNORECASE)


def test_the_agent_is_not_taught_it():
    from fastmdxplora.agent.propose import prompt_for

    assert not WORD.search(prompt_for("simulate chignolin until its RMSD is known"))


def test_no_refusal_says_it():
    from fastmdxplora.refusals import CODES

    assert [str(code) for code in CODES if WORD.search(str(code))] == []


def test_no_explanation_says_it():
    from fastmdxplora.explain import EXPLANATIONS

    assert [key for key, said in EXPLANATIONS.items()
            if WORD.search(said.why + (said.reference or ""))] == []


@pytest.mark.parametrize("page", ["gui/templates/dashboard.html", "gui/static/stopping.js",
                                  "gui/static/series-chart.js", "gui/static/run-builder.js",
                                  "gui/static/dashboard.js", "gui/static/frame-series.js",
                                  "gui/static/frame-interactions.js",
                                  "gui/static/viewer-views.js"])
def test_no_page_shows_it(page):
    # What is shown is written as a string; names in the code (a function
    # that settles two inputs into one, `Promise.allSettled`) are not shown.
    text = (SRC / page).read_text(encoding="utf-8")
    shown = re.findall(r'"[^"\n]*"|\'[^\'\n]*\'|>[^<\n]+<', text)
    assert [s for s in shown if WORD.search(s)] == []


def test_the_stopping_rule_speaks_of_determining():
    from fastmdxplora.simulation.stopping import _DECIDED, _OUTCOME, rule_said

    assert _OUTCOME["met"] == "Determined as asked"
    assert _OUTCOME["ceiling"] == "Not determined as asked"
    assert _DECIDED["met"] == "stopped: determined as asked"
    assert rule_said({"measures": [{"analysis": "rmsd", "standard_error": 0.01}],
                      "max_duration_ns": 20,
                      "independent_starts": "not_required"}).startswith(
        "rmsd to ±0.01 nm is determined")


def test_the_corpus_case_is_named_for_equilibration():
    from fastmdxplora.validation.corpus import CLEAN, DEFECTS

    names = [case.name for case in DEFECTS + CLEAN]
    assert "a mean pooled over segments of a run that never equilibrated" in names
    assert "a mean from a run that equilibrated, transient included" in names
    assert not [name for name in names if WORD.search(name)]
