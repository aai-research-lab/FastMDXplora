"""The Agent's questions about a study, answered from its records.

The Agent's start page offers three questions about the study open: what it
found, whether it ran long enough, and what would strengthen it most. With no
AI model set, asking any of them was refused, though every part of each answer
is already computed and recorded: the report's summary of what the run
supports, each analysis's mean with its error or the reason it gave none, the
checks the run was held to, how much more production the withheld means need
and what that takes here, and the rule that a single run's error cannot show a
state the run never left. Those are said here, as the records say them, and
marked as read from the records: no AI model was asked, and nothing is said
that the records do not hold.

With an AI model set, the questions go to it as before; it reads the same
records through its tools.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

__all__ = ["QUESTIONS", "answer_from_the_records", "answered_from_the_records"]

#: The questions the start page offers about a study, by the key its
#: buttons carry.
QUESTIONS = ("found", "long_enough", "strengthen")

MARK = "*From this study's records.*"

#: Said by every answer about whether to trust a single run's means.
TRAPPED = ("A single run's error is estimated from the run itself, so it cannot show "
           "a state the run never left: a run trapped in one state can equilibrate and "
           "hold enough independent samples there. Runs started independently are the "
           "check: sweep `simulation.random_seed` over three or more values, or "
           "`setup.model` over the models of an ensemble, and `simulation.stop_when` "
           "extends them until their means agree.")


def answer_from_the_records(root: str | Path | None, question: str) -> str | None:
    """The answer to one of :data:`QUESTIONS` about the study at ``root``, in
    Markdown, or None where there is no study there or no such question."""
    if question not in QUESTIONS or not root:
        return None
    base = Path(root)
    if not base.is_dir():
        return None
    if (base / "batch_manifest.json").is_file():
        return _several_runs(base)
    said = {"found": _found, "long_enough": _long_enough,
            "strengthen": _strengthen}[question](base)
    return MARK + "\n\n" + said


def _supports(base: Path) -> str:
    """The report's line on how much of the run can be interpreted, with its
    link to a section of the report said as the section."""
    from fastmdxplora.report.document import _what_the_run_supports

    try:
        line = _what_the_run_supports(base) or ""
    except Exception:  # noqa: BLE001 - the rest of the answer stands
        return ""
    return re.sub(r"\[Convergence\]\(#convergence\)",
                  "the report's Convergence section", line)


def _means(base: Path) -> list[str]:
    """Each mean the analyses recorded, as the Analysis page gives it
    (`analysis_overview.overview_of`): its name as the page says it, the
    mean to the place its error allows where it is determined, and else the
    mean alone with why not. Read from the page's own gathering, so the
    answer cannot give an error the page withholds, or a record's key
    ("hydrophobic_sasa") for its name; the thermodynamics analysis's means,
    a group of their own, were left out."""
    import math

    from fastmdxplora.gui.analysis_overview import overview_of

    try:
        rows = overview_of(base).get("rows") or []
    except Exception:  # noqa: BLE001 - the rest of the answer stands
        return []
    lines: list[str] = []
    for row in rows:
        for quantity in row.get("quantities") or []:
            if not isinstance(quantity, dict):
                continue
            label = quantity.get("label") or row.get("title") or row.get("analysis")
            key = str(quantity.get("key") or "")
            # A group's quantity (the thermodynamics analysis's density) is
            # named by itself; another of an analysis's means with it.
            if key != "mean" and "." not in key and row.get("title") not in (None, label):
                label = f"{row['title']}, {label[:1].lower()}{label[1:]}"
            said = str(quantity.get("said") or "")
            why = _first_clause(quantity.get("why"))
            if quantity.get("value") is None:
                if why.startswith("no mean"):
                    lines.append(f"{label}: {why}.")
                elif why:
                    lines.append(f"{label}: no mean; {why}.")
                continue
            if not quantity.get("determined"):
                lines.append(f"{label}: {said}, not determined" + (f"; {why}." if why else "."))
                continue
            samples = quantity.get("samples")
            discard, frames = quantity.get("from_frame"), quantity.get("of_frames")
            if isinstance(samples, (int, float)) and math.isfinite(samples):
                said += f", {samples:.0f} independent samples"
            if isinstance(discard, int) and isinstance(frames, int) and discard > 0:
                said += f", after the first {discard} of {frames} frames"
            lines.append(f"{label}: {said}.")
    if not any(row.get("analysis") == "thermodynamics" for row in rows):
        lines += _thermodynamic_means(base)
    return lines


def _thermodynamic_means(base: Path) -> list[str]:
    """The production's thermodynamic means as the Overview gives them, for
    a study with no thermodynamics analysis: the summary named none while
    the Overview and the report gave each."""
    from fastmdxplora.gui.overview_view import THERMODYNAMICS, overview_payload

    try:
        means = (overview_payload(base).get("thermodynamics") or {}).get("means") or {}
    except Exception:  # noqa: BLE001 - the rest of the answer stands
        return []
    lines = []
    for key, label, _unit in THERMODYNAMICS:
        said = means.get(key)
        if not isinstance(said, dict) or not said.get("said"):
            continue
        if said.get("determined"):
            samples = said.get("samples")
            more = f", {samples:.0f} independent samples" if isinstance(samples, (int, float)) else ""
            lines.append(f"{label}: {said['said']}{more}.")
        else:
            why = _first_clause(said.get("why"))
            lines.append(f"{label}: {said['said']}, not determined" + (f"; {why}." if why else "."))
    return lines


def _first_clause(reason: Any) -> str:
    """What a withheld mean's reason says first, without what follows it."""
    if not isinstance(reason, str) or not reason.strip():
        return ""
    text = reason.strip()
    for stop in (": ", ". ", "; "):
        if stop in text:
            text = text.split(stop, 1)[0]
    text = text.rstrip(".")
    return text[:1].lower() + text[1:]


def _checks(base: Path) -> list[str]:
    from fastmdxplora.gui.agent_panel import _checks_summary

    said = _checks_summary(base)
    return [line.strip() for line in said.splitlines()[1:] if line.strip()]


def _ask(base: Path) -> tuple[str, str] | None:
    """What the withheld means need, and the command that runs it where the
    study can be extended; None where no mean asked for more."""
    from fastmdxplora.simulation.resume import extension_of
    from fastmdxplora.simulation.sampling_ask import sampling_asked_for

    ask = sampling_asked_for(base)
    if ask is None:
        return None
    command = ""
    if extension_of(base, more_ns=ask.more_ns).possible:
        command = (f"fastmdx explore --simulate-resume-from {base.resolve()} "
                   f"--simulate-extra-ns {ask.more_ns:g}")
    return ask.as_text(), command


def _found(base: Path) -> str:
    from fastmdxplora.report.document import _study_in_one_paragraph

    parts: list[str] = []
    study = _study_in_one_paragraph(base)
    head = " ".join(piece for piece in (study, _supports(base)) if piece)
    if head:
        parts.append(head)
    means = _means(base)
    if means:
        parts.append("What each analysis gives (each error a standard error):\n"
                     + "\n".join(f"- {line}" for line in means))
    else:
        parts.append("No analysis has recorded a mean yet.")
    checks = _checks(base)
    if checks:
        parts.append("The checks it was held to:\n" + "\n".join(f"- {line}" for line in checks))
    return "\n\n".join(parts)


#: Said where the means the run withheld recorded no figure for the
#: production they need.
NO_FIGURE = ("Its analyses recorded no figure for how much more production they need: "
             "a study analysed before that figure was recorded has none, and one with no "
             "record of its frames' spacing (a trajectory analysed without its run's "
             "records) cannot have one. Analysing it again with this release records it "
             "where it can.")


#: Where a mean was withheld because it still drifts: no figure says how
#: much longer, and analysing again records none (it was told to).
STILL_DRIFTING = ("A mean still drifting over the frames it averages has no figure for how "
                  "much longer: carry the run on until it stops drifting, then analyse it "
                  "again.")


def _no_figure(base: Path) -> str:
    """Why no figure says how much more production the means need."""
    from fastmdxplora.gui.analysis_overview import overview_of

    try:
        rows = overview_of(base).get("rows") or []
    except Exception:  # noqa: BLE001 - the plainer answer stands
        return NO_FIGURE
    drifting = any(str(q.get("why") or "").startswith("Still drifting")
                   for row in rows for q in (row.get("quantities") or [])
                   if isinstance(q, dict))
    return STILL_DRIFTING if drifting else NO_FIGURE


def _determined(supports: str) -> bool:
    """Whether the report's line says every observable equilibrated and holds
    enough independent samples to average."""
    return supports.startswith("All ") and "too few" not in supports


def _not_analysed(base: Path) -> str:
    """For a study with no analysis: that nothing has judged it yet, and a
    run that ended short is carried on first. It was told its analyses
    "recorded no figure" and to analyse again, and sent to a report it did
    not have."""
    said = ("Its records do not say yet: it has not been analysed, so no analysis "
            "has judged whether its series equilibrated.")
    try:
        from fastmdxplora.gui.telemetry import status_as_it_stands

        status = str(status_as_it_stands(base).get("status") or "").lower()
    except Exception:  # noqa: BLE001 - the plainer answer stands
        status = ""
    if status in ("stopped", "failed", "interrupted"):
        # What to do is the fix card's: carried on where a checkpoint was
        # written, run again or changed where none was or the run failed.
        said += (" It ended before it finished: What would fix it, on the Overview, "
                 "says what to do first.")
    return said


def _long_enough(base: Path) -> str:
    parts: list[str] = []
    if not (base / "analysis").is_dir():
        return "\n\n".join([_not_analysed(base), TRAPPED])
    supports = _supports(base)
    asked = _ask(base)
    if asked is not None:
        text, command = asked
        parts.append(" ".join(piece for piece in ("Not for all of its means.", supports)
                              if piece))
        # Every mean withheld, the quantities beside the report's
        # observables among them: the line above counts the observables.
        parts.append("What they need, every mean withheld counted: " + text)
        if command:
            parts.append(f"To run it, extending the study in place:\n\n`{command}`")
    elif _determined(supports):
        parts.append("By its records, yes. " + supports)
    elif supports:
        parts.append("Not by its records. " + supports)
        parts.append(_no_figure(base))
    else:
        parts.append("Its records do not say yet: no analysis has judged whether its "
                     "series equilibrated.")
    parts.append(TRAPPED)
    return "\n\n".join(parts)


def _strengthen(base: Path) -> str:
    from fastmdxplora.gui.exploration import study_a_run_belongs_to

    parts: list[str] = []
    supports = _supports(base)
    asked = _ask(base)
    if asked is not None:
        text, command = asked
        parts.append("A longer run first: the means it withheld have to be given before "
                     "replicas can be compared on them.")
        parts.append("What they need: " + text)
        if command:
            parts.append(f"`{command}`")
    elif _determined(supports):
        parts.append("Its means are determined, so a longer run of this one adds "
                     "precision but not a check.")
    elif supports:
        parts.append("A longer run first. " + supports)
        parts.append(_no_figure(base))
    of = study_a_run_belongs_to(base)
    if of:
        parts.append(f"This run is one of the study {of.get('study')}. Its Report page "
                     "sets the runs' spread against each run's own error, which is the "
                     "check a single run cannot make.")
    else:
        parts.append("Then replicas. " + TRAPPED)
    return "\n\n".join(parts)


def _several_runs(base: Path) -> str:
    from fastmdxplora.gui.exploration import runs_of_a_study

    runs = runs_of_a_study(base) or []
    done = sum(1 for run in runs if run.get("state") == "completed")
    parts = [f"This is a study of {len(runs)} runs, {done} completed. Each run keeps its "
             "own records: view one from Runs in the sidebar and ask about it, or read the "
             "comparison across them on the Report page."]
    energy = _free_energy(base)
    if energy:
        parts.insert(0, energy)
    return MARK + "\n\n" + "\n\n".join(parts)


def _free_energy(base: Path) -> str:
    """What an umbrella study's windows gave, as its record says: the binding
    free energy with its error and warnings, the profile alone, or why there
    is none. Empty where the study is not one."""
    import json

    from fastmdxplora.statistics import with_its_error

    try:
        record = json.loads((base / "pmf.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(record, dict):
        return ""
    if record.get("refused"):
        return ("Its windows gave no free energy: "
                + _first_clause(record["refused"]) + ".")
    binding = record.get("binding") if isinstance(record.get("binding"), dict) else {}
    value = binding.get("delta_g_kjmol")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        error = binding.get("delta_g_standard_error_kjmol")
        error = error if isinstance(error, (int, float)) and not isinstance(error, bool) \
            else None
        said = (f"Its windows give a binding free energy of {with_its_error(value, error)} "
                "kJ/mol (standard state, 1 M; the error a standard error).")
        warnings = (binding.get("reference") or {}).get("warnings") or []
        return said + "".join(f" {warning}" for warning in warnings)
    if binding.get("refused"):
        return ("Its windows were recombined into a free energy profile, and no binding "
                "free energy is given: " + _first_clause(binding["refused"]) + ".")
    return ("Its windows were recombined into a free energy profile, on its Report "
            "page.")


def answered_from_the_records(payload: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
    """The propose endpoint's answer when no AI model can be asked and the
    question is one of the start page's about the study open; None
    otherwise, and the refusal stands."""
    question = str(payload.get("records_question") or "")
    if question not in QUESTIONS or runtime is None:
        return None
    root = (runtime.snapshot() or {}).get("active_run")
    said = answer_from_the_records(root, question)
    if said is None:
        return None
    return {"ok": True, "answer": said, "cites": [], "looks": [], "from_records": True}
