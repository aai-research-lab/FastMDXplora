"""What a trajectory gives is analysed, computed or determined; not measured.

A simulation is not an experiment. An RMSD is computed from a trajectory, an
analysis is run on it, and a mean is determined, or is not, to the error
asked. "Measured" and "a measurement" were doing all of that work in what a
person reads: a withheld mean was "not a measurement", a stopping rule's
quantities were "measures", the convergence checks asked for a correlation
time "measurable". The word stays where it is the right one: a machine's
speed is measured, an experimental order parameter was measured, the
viewer's ruler measures a distance, and record keys (`not_a_measurement`,
`stop_when.measures`) keep their names so old studies still read.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "fastmdxplora"
SAID_OF_A_RESULT = re.compile(
    r"not (yet )?a measurement|\ba measure\b|\bmeasures\b|measurements"
    r"|measure the trajectory|measured means", re.IGNORECASE)
#: `measures` is the key a stopping rule is written with, not a word said.
KEY = re.compile(r"`measures`|\bmeasures:|\.measures\b")
PAGES = ["gui/templates/dashboard.html"] + sorted(
    str(path.relative_to(SRC)) for path in (SRC / "gui" / "static").glob("*.js"))


@pytest.mark.parametrize("page", PAGES)
def test_no_page_calls_a_result_a_measurement(page):
    # What is shown is written as a string with a space in it; an id
    # ("stopping-measures") or a record key is not shown.
    text = (SRC / page).read_text(encoding="utf-8")
    shown = re.findall(r'"[^"\n]*"|\'[^\'\n]*\'|>[^<\n]+<', text)
    assert [s for s in shown if " " in s and SAID_OF_A_RESULT.search(s)] == []


def test_the_checks_a_run_is_held_to_speak_of_observables():
    from fastmdxplora.report.convergence import CHECKS

    said = {key: (long, short) for key, long, short in CHECKS}
    assert said["equilibrated"] == ("each observable equilibrates before it is averaged",
                                    "each observable equilibrated")
    assert said["correlation"] == ("each observable's correlation time is resolved by the run",
                                   "each correlation time resolved")
    assert not [key for key, long, short in CHECKS if re.search("measur", long + short, re.I)]


def test_the_agent_is_taught_the_words():
    from fastmdxplora.agent.propose import prompt_for

    prompt = prompt_for("simulate chignolin until its RMSD is known")
    assert not SAID_OF_A_RESULT.search(_prose(prompt))
    assert "is one of the recorded means" in prompt


def _prose(text: str) -> str:
    return KEY.sub("", text)


def _strings_said(path: Path) -> list[str]:
    """The string literals of a module that are not docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)}
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docs]


@pytest.mark.parametrize("module", ["gui", "report", "batch", "simulation/stopping.py",
                                    "statistics.py", "explain.py"])
def test_no_message_calls_a_result_a_measurement(module):
    where = SRC / module
    for path in sorted(where.rglob("*.py")) if where.is_dir() else [where]:
        said = [s for s in _strings_said(path) if " " in s and SAID_OF_A_RESULT.search(_prose(s))]
        assert said == [], path.relative_to(SRC)


def test_no_refusal_calls_a_result_a_measurement():
    from fastmdxplora.refusals import CODES

    assert [code.id for code in CODES if SAID_OF_A_RESULT.search(code.summary)] == []


def test_what_a_machine_and_an_experiment_gave_is_still_measured():
    from fastmdxplora.refusals import CODES

    said = " ".join(code.summary for code in CODES)
    assert "This machine has not been measured" in said
    text = (SRC / "analysis" / "order_parameters.py").read_text(encoding="utf-8")
    assert "A measured order parameter is a model-free" in text
