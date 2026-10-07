"""Whether the Agent's looking helps, measured on a real AI model.

The Agent may look with the software's own tools before it answers
(:mod:`fastmdxplora.agent.tools`). That it can is tested; whether a real
AI model uses them, and whether its answers then agree with the software, is a
measurement, and this is the harness for it. Each question has an answer
the software itself computes, with no AI model involved: a solvated system's
size, a box's width, what a structure contains, what a selection matches.
The same questions go to the configured AI model twice over, with the tools
and without, and each reply is judged against the software's answer by a
rule written here before any reply is seen.

What is measured is agreement with the software, not with experiment: the
size is setup's estimate, which is within a few per cent of what setup then
builds. A reply is judged correct when a number in it is within the
question's tolerance of the software's, or, for a question with a set of
names, when it names every one. Both are lenient to a reply that states
several numbers or names more than asked; the reply is kept whole in the
record, so that can be read.

For each arm the record gives how many replies agreed, and with the tools
how many looked with the tool the question calls for. Nothing here decides
whether the difference is large enough to claim: the counts are reported
as counted.

Run it with an AI model chosen (`fastmdx agent model`), on a machine that can
fetch from the PDB::

    python -m fastmdxplora.validation.agent_looks --repeats 3 --out agent_looks.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

__all__ = ["Question", "QUESTIONS", "numbers_in", "judge", "ask", "run", "summary",
           "build_parser", "main"]


@dataclass(frozen=True)
class Question:
    """One question, the software's own answer, and how a reply is judged."""

    name: str
    #: What the person asks, as they would type it.
    request: str
    #: The tool an AI model that looks would use for it.
    tool: str
    #: The software's answer, computed with no AI model: a number, or a list
    #: of names every one of which a reply must give.
    truth: Callable[[], Any]
    #: For a number, how far a reply's may be from it, as a fraction.
    tolerance: float = 0.0
    #: Why the tolerance is what it is.
    because: str = ""


def _preview(system: str) -> dict[str, Any]:
    from fastmdxplora.gui.preview import preview_of_config

    answer = preview_of_config({"systems": [{"system": system}]})
    if not answer.get("ok"):
        from fastmdxplora.refusals import StudyError

        raise StudyError(f"No preview of {system}: {answer.get('reason')}",
                         code="setup.structure.unreadable")
    return answer["estimate"]


def _counted(system: str) -> dict[str, Any]:
    from fastmdxplora.gui.preview import structure_file
    from fastmdxplora.structure_info import count_structure

    return count_structure(structure_file(system, None))


def _selected(system: str, expression: str) -> int:
    import mdtraj as md

    from fastmdxplora.gui.preview import structure_file

    return int(len(md.load(str(structure_file(system, None))).topology.select(expression)))


def _histidines(system: str) -> int:
    from fastmdxplora.gui.preview import structure_file, titratable_residues
    from fastmdxplora.setup.estimate import estimate_system

    atoms = estimate_system(structure_file(system, None), {}).atoms
    return sum(1 for r in titratable_residues(atoms) if r["resname"] == "HIS")


#: Written before any reply was seen. The tolerances are the software's own
#: resolution: the size estimate is within 3% of setup on the structures it
#: was checked on (1099), so 10% separates a number read from the estimate
#: from one guessed; a box width is set by the padding and the solute's
#: extent, and 5% is a tenth of the usual padding on a small protein. Counts
#: of residues, atoms selected and histidines are exact.
QUESTIONS: tuple[Question, ...] = (
    Question("particles_1l2y",
             "About how many atoms will the solvated system have if I simulate "
             "1L2Y with the default settings? Give the number.",
             "preview_setup", lambda: _preview("1L2Y")["particles"], 0.10,
             "the estimate is within 3% of setup; 10% tells it from a guess"),
    Question("box_1ake",
             "With the default settings, how wide will the box be for 1AKE, face "
             "to face, in nm?",
             "preview_setup", lambda: _preview("1AKE")["width_nm"], 0.05,
             "a twentieth of the width is a tenth of the default padding here"),
    Question("residues_1ubq",
             "How many protein residues does 1UBQ have?",
             "inspect_structure", lambda: _counted("1UBQ")["protein_residues"], 0.0,
             "a count"),
    Question("ligands_1ake",
             "Which ligands does 1AKE contain? Give their residue names.",
             "inspect_structure",
             lambda: sorted(str(n) for n in _counted("1AKE").get("ligand_resnames") or []),
             0.0, "every name the structure file gives"),
    Question("selection_1l2y",
             "How many atoms does the selection `name CA and resSeq 1 to 10` match "
             "in 1L2Y?",
             "check_selection", lambda: _selected("1L2Y", "name CA and resSeq 1 to 10"),
             0.0, "a count"),
    Question("histidines_1hho",
             "A study of 1HHO with the default settings: how many histidines will "
             "the system setup builds have, counting every chain?",
             "inspect_structure", lambda: _histidines("1HHO"), 0.0,
             "a count, of the biological assembly setup builds"),
)

_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def numbers_in(text: str) -> list[float]:
    """The numbers a reply states: digits with thousands separators or a
    decimal point, and small numbers written as words. A digit inside a
    name (1L2Y) or an exponent (nm^3) is not one."""
    found = [float(m.replace(",", "")) for m in
             re.findall(r"(?<![\w.^])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\w)",
                        text or "")]
    found += [float(_WORDS[w]) for w in re.findall(r"[a-z]+", (text or "").lower())
              if w in _WORDS]
    return found


def judge(question: Question, truth: Any, reply: str) -> tuple[bool, str]:
    """Whether a reply agrees with the software's answer, and why."""
    if isinstance(truth, (list, tuple)):
        missing = [name for name in truth
                   if not re.search(rf"(?<![A-Za-z0-9]){re.escape(str(name))}(?![A-Za-z0-9])",
                                    reply or "")]
        if not truth:
            said_none = re.search(r"\b(no|none|not any)\b", (reply or "").lower()) is not None
            return said_none, ("the structure has none; "
                               + ("the reply says so" if said_none else "the reply does not say so"))
        return not missing, ("names every one" if not missing
                             else "does not name " + ", ".join(map(str, missing)))
    target = float(truth)
    stated = numbers_in(reply)
    if not stated:
        return False, "states no number"
    nearest = min(stated, key=lambda n: abs(n - target))
    off = abs(nearest - target) / abs(target) if target else abs(nearest)
    within = off <= question.tolerance + 1e-12
    return within, (f"{nearest:g} against {target:g}"
                    + ("" if within else f", {100 * off:.1f}% off"))


@dataclass
class Trial:
    question: str
    arm: str
    reply: str
    kind: str
    looks: list[str] = field(default_factory=list)
    looked: bool = False
    correct: bool = False
    why: str = ""

    def as_record(self) -> dict[str, Any]:
        return dict(self.__dict__)


def ask(question: Question, truth: Any, complete: Callable[[str], str], *,
        with_tools: bool, toolbox: Callable[[], Any] | None = None,
        judge_with: Callable[[Question, Any, str], tuple[bool, str]] | None = None) -> Trial:
    """One question to the AI model, one arm, judged (by :func:`judge` unless
    another rule is given, as a later registration's is)."""
    from fastmdxplora.agent import propose_config
    from fastmdxplora.agent.tools import Toolbox

    tools = (toolbox or Toolbox)() if with_tools else None
    arm = "tools" if with_tools else "no tools"
    try:
        # As registered: in the text protocol, whatever the AI model takes,
        # since the registrations fix the prompt the replies were judged on.
        proposal = propose_config(question.request, complete, tools=tools,
                                  as_registered=True)
    except Exception as exc:  # noqa: BLE001 - a failure is a result here
        return Trial(question.name, arm, "", "failed", why=str(exc))
    if proposal.answer:
        reply, kind = proposal.answer, "answer"
    elif proposal.question:
        reply, kind = proposal.question, "question"
    elif proposal.config is not None:
        reply, kind = json.dumps(proposal.config), "config"
    else:
        reply, kind = (proposal.refusal.message if proposal.refusal else ""), "refused"
    looks = [look.tool for look in proposal.looks]
    correct, why = (judge_with or judge)(question, truth, reply) if kind == "answer" else (
        False, f"a {kind}, not an answer")
    return Trial(question.name, arm, reply, kind, looks, question.tool in looks, correct, why)


def run(complete: Callable[[str], str], *, repeats: int = 3,
        questions: tuple[Question, ...] = QUESTIONS, arms: tuple[bool, ...] = (True, False),
        toolbox: Callable[[], Any] | None = None,
        judge_with: Callable[[Question, Any, str], tuple[bool, str]] | None = None
        ) -> dict[str, Any]:
    """Every question, in every arm, ``repeats`` times; the software's
    answer computed once per question before any reply."""
    truths: dict[str, Any] = {}
    for question in questions:
        try:
            truths[question.name] = question.truth()
        except Exception as exc:  # noqa: BLE001 - said, and the question left out
            truths[question.name] = None
            print(f"{question.name}: the software's answer could not be computed "
                  f"({exc}); left out.", file=sys.stderr)
    trials = []
    for question in questions:
        if truths[question.name] is None:
            continue
        for _ in range(max(1, int(repeats))):
            for with_tools in arms:
                trials.append(ask(question, truths[question.name], complete,
                                  with_tools=with_tools, toolbox=toolbox,
                                  judge_with=judge_with))
    return {"truths": truths, "trials": [t.as_record() for t in trials],
            "summary": summary(trials)}


def summary(trials: list[Trial]) -> dict[str, Any]:
    """Counts per arm and per question: agreed, looked, answered."""
    out: dict[str, Any] = {}
    for arm in ("tools", "no tools"):
        mine = [t for t in trials if t.arm == arm]
        if not mine:
            continue
        per: dict[str, dict[str, int]] = {}
        for t in mine:
            row = per.setdefault(t.question, {"asked": 0, "agreed": 0, "looked": 0})
            row["asked"] += 1
            row["agreed"] += int(t.correct)
            row["looked"] += int(t.looked)
        out[arm] = {"asked": len(mine), "agreed": sum(t.correct for t in mine),
                    "looked": sum(t.looked for t in mine),
                    "answered": sum(t.kind == "answer" for t in mine),
                    "per_question": per}
    return out


def _table(result: dict[str, Any]) -> str:
    lines = [f"{'question':<18} {'with tools':>16} {'looked':>8} {'without':>10}"]
    got = result["summary"]
    names = list(dict.fromkeys(t["question"] for t in result["trials"]))
    for name in names:
        tools = got.get("tools", {}).get("per_question", {}).get(name, {})
        without = got.get("no tools", {}).get("per_question", {}).get(name, {})
        lines.append(f"{name:<18} {tools.get('agreed', 0):>7}/{tools.get('asked', 0):<8} "
                     f"{tools.get('looked', 0):>6}/{tools.get('asked', 0):<2} "
                     f"{without.get('agreed', 0):>5}/{without.get('asked', 0):<4}")
    for arm in ("tools", "no tools"):
        if arm in got:
            s = got[arm]
            lines.append(f"{arm}: {s['agreed']} of {s['asked']} agreed with the software"
                         + (f", {s['looked']} looked with the tool the question calls for"
                            if arm == "tools" else ""))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m fastmdxplora.validation.agent_looks",
        description="Ask the configured AI model questions the software can answer, "
                    "with its tools and without, and count the replies that agree.")
    parser.add_argument("--repeats", type=int, default=3,
                        help="how many times each question is asked in each arm")
    parser.add_argument("--only", nargs="+", metavar="NAME",
                        help="ask only these questions: " + ", ".join(q.name for q in QUESTIONS))
    parser.add_argument("--out", help="write every reply and the counts here, as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from fastmdxplora import __version__
    from fastmdxplora.agent import completion_for, load_choice

    chosen = load_choice()
    complete = completion_for()
    questions = tuple(q for q in QUESTIONS if not args.only or q.name in args.only)
    result = run(complete, repeats=args.repeats, questions=questions)
    result["model"] = f"{chosen.provider}/{chosen.model}" if chosen is not None else None
    result["version"] = __version__
    result["when"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result["repeats"] = args.repeats
    print(_table(result))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, default=str)
        print(f"Every reply is in {args.out}.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
