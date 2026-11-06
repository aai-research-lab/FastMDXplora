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

import json

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


def _span_analysed(base: Path) -> float | None:
    """The production its analyses read, in ns: their frames times the run's
    frame interval and timestep (`analysis/analysis_manifest.json`). None
    for a variable step, whose frames are not evenly spaced in time, and
    where a record does not say."""
    import yaml

    from fastmdxplora import record_access
    from fastmdxplora.simulation.lengths import _variable_step
    from fastmdxplora.simulation.resume import trajectory_interval_of

    try:
        manifest = json.loads(record_access.read_text(
            base / "analysis" / "analysis_manifest.json", encoding="utf-8"))
        frames = manifest["n_frames"]
        # Its frames as the analysis read them: every stride-th, so each
        # spans that many of the run's.
        stride = (manifest.get("load_kwargs") or {}).get("stride") or 1
        simulation = (yaml.safe_load(record_access.read_text(
            base / "resolved_config.yml", encoding="utf-8")) or {}).get("simulation") or {}
        interval = trajectory_interval_of(base)
        if (_variable_step(simulation) or not interval or not isinstance(frames, int)
                or not isinstance(stride, int)):
            return None
        return frames * stride * interval * float(simulation.get("timestep_fs") or 2.0) / 1e6
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError):
        return None


def _the_means_are_of_less(base: Path) -> str | None:
    """Said first of a run whose means are of less than its production: its
    joined pieces' analyses owed, or its last piece not joined. Of what span
    its means are, and what gives them over the whole, as the resume reads
    it (`resume.how_a_resume_finds`): `fastmdx resume` of its study, or,
    where the run stopped with an error in its analyses, which a resume
    refuses, analysing it again. While a resume or an extension runs them,
    that they run (`being_carried_on`)."""
    from fastmdxplora.simulation.resume import (_extended_since_it_finished, analysis_owed,
                                                being_carried_on, extended_production,
                                                how_a_resume_finds, production_done_ns)
    from fastmdxplora.simulation.sampling_ask import length_said

    try:
        owed, unjoined = analysis_owed(base), _extended_since_it_finished(base)
        if not (owed or unjoined):
            return None
        from fastmdxplora.batch.explorer import campaign_of

        going = being_carried_on(base) or being_carried_on(campaign_of(base) or base)
        # Its length as the paragraph below says it (`extended_production`).
        extended = extended_production(base)
        whole = extended[0] if extended else production_done_ns(base)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        return None
    span = _span_analysed(base)
    if span is not None and whole and span >= whole - 1e-6:
        # Its means are of the whole of it.
        return None
    of = (f"of its first {length_said(span)}" if span is not None
          else "of its pieces before the join")
    the_whole = f"its {length_said(whole)}" if whole else "its whole production"
    if going:
        from fastmdxplora.gui.telemetry import _GOING_STATUSES, _pieces_of, read_status

        # Simulated only while its newest piece's own record says it goes;
        # otherwise the resume joins and analyses (a piece stopped at its
        # end is joined first).
        try:
            pieces = _pieces_of(base)
            simulated = bool(pieces) and str(read_status(pieces[-1]).get("status") or
                                             "").lower() in _GOING_STATUSES
        except Exception:  # noqa: BLE001 - a record, not a verdict
            simulated = False
        if owed or not simulated:
            return (f"Its pieces are being joined and analysed now: the means below are "
                    f"{of}, not of {the_whole}, until that ends.")
        return (f"Its next piece is being simulated now: the means below are {of}, not of "
                f"{the_whole}, until its pieces are joined and analysed.")
    found = how_a_resume_finds(base) or {}
    from fastmdxplora.batch.explorer import campaign_of

    study = campaign_of(base) or base
    if found.get("own"):
        then = (f"once what its analyses said is put right, fastmdx explore --output {base} "
                "--include analysis report --rerun runs them")
    else:
        then = f"fastmdx resume {study} " + ("joins it and runs them" if unjoined
                                               else "runs them")
    what = ("Its last piece is not joined" if unjoined else
            "Its pieces were joined and their analyses and report have not run")
    return f"{what}, so the means below are {of}, not of {the_whole}; {then}."


def answer_from_the_records(root: str | Path | None, question: str) -> str | None:
    """The answer to one of :data:`QUESTIONS` about the study at ``root``, in
    Markdown, or None where there is no study there or no such question."""
    if question not in QUESTIONS or not root:
        return None
    base = Path(root)
    if not base.is_dir():
        return None
    if (base / "batch_manifest.json").is_file():
        several = _several_runs(base)
        going = _still_going(base)
        if going and several and several.startswith(MARK):
            # Going, here or on another machine: said first, as a run's
            # answer says it.
            return f"{MARK}\n\n{going}{several[len(MARK):]}"
        return several
    said = {"found": _found, "long_enough": _long_enough,
            "strengthen": _strengthen}[question](base)
    from fastmdxplora.simulation.stopping import round_ended

    owed = _the_means_are_of_less(base)
    if owed:
        # Its means are of less than its production: said first, with what
        # gives them over the whole.
        said = f"{owed}\n\n{said}"
    going = _still_going(base)
    if going:
        # Going, here or on another machine (its study's
        # `being_carried_on`): said first; what is below is as it stands.
        said = f"{going}\n\n{said}"
    ended = round_ended(base)
    if ended is not None:
        # A run that completed whose rule's round did not: said first, as
        # its status and All studies say it (`stopping.round_ended`).
        said = f"{ended.said} {ended.carry_on_on_a_page}\n\n{said}"
    return MARK + "\n\n" + said


def _still_going(base: Path) -> str:
    """That the run's study is still going, here or on another machine
    (`resume.going_elsewhere` says where): its records are read as they
    stand. Empty where it is not."""
    from fastmdxplora.batch.explorer import campaign_of
    from fastmdxplora.simulation.resume import being_carried_on, going_elsewhere

    study = campaign_of(base) or base
    try:
        if not being_carried_on(study):
            return ""
        where = going_elsewhere(study)
    except Exception:  # noqa: BLE001 - its records as they stand
        return ""
    # Where it goes says it is going (`going_elsewhere`): said once.
    return ((where or "It is still going.")
            + " What follows is its records as they stand now.")


def _supports(base: Path) -> str:
    """How many of the run's means are determined, counted as the Overview
    counts them (:func:`_counted`); where it counts none, the report's line
    on how much of the run can be interpreted, with its link to a section
    of the report said as the section."""
    counted = _counted(base)
    if counted:
        return counted
    from fastmdxplora.report.document import _what_the_run_supports

    try:
        line = _what_the_run_supports(base) or ""
    except Exception:  # noqa: BLE001 - the rest of the answer stands
        return ""
    return re.sub(r"\[Convergence\]\(#convergence\)",
                  "the report's Convergence section", line)


def _thermodynamic_counted(base: Path, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The production's thermodynamic means the Overview gives in a panel
    of their own, for a study with no thermodynamics analysis (whose means
    the Overview counts with the rest, :func:`_means`)."""
    if (base / "analysis" / "thermodynamics").is_dir():
        return []
    found = ((payload.get("thermodynamics") or {}).get("means") or {}) \
        if isinstance(payload.get("thermodynamics"), dict) else {}
    return [m for m in found.values() if isinstance(m, dict) and m.get("mean") is not None]


def _counted(base: Path) -> str:
    """How many of the run's means are determined, in a sentence, counted
    once, as the Overview counts them: the analyses' means as its lead
    counts them (:func:`fastmdxplora.gui.overview_view.means_counted`), then
    the production's thermodynamic means its panel gives, and any result not
    read. An empty string where the Overview counts none. The answers said
    "All 4 observables assessed had equilibrated, and each mean is
    determined" (the report's count) of a run whose lead counted one mean,
    then asked for more production for the total energy, not one of the
    four."""
    from fastmdxplora.gui.overview_view import means_counted, overview_payload

    try:
        payload = overview_payload(base)
    except Exception:  # noqa: BLE001 - the report's line is said instead
        return ""

    def count(key: str) -> int:
        value = payload.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    means, determined, not_read = count("means"), count("determined"), count("not_read")
    thermodynamic = _thermodynamic_counted(base, payload)
    if not means and not thermodynamic:
        return ""
    parts = [means_counted(means, determined)] if means else []
    undetermined = 0
    if thermodynamic:
        total = len(thermodynamic)
        undetermined = sum(1 for m in thermodynamic if not m.get("determined"))
        of = ("the production's thermodynamic mean" if total == 1
              else f"the production's {total} thermodynamic means")
        parts.append(f"{of} is {'not ' if undetermined else ''}determined" if total == 1
                     else f"{of} are determined" if not undetermined
                     else f"none of {of} is determined" if undetermined == total
                     else f"{undetermined} of {of} "
                          f"{'is' if undetermined == 1 else 'are'} not determined")
    said = "; ".join(parts)
    said = said[:1].upper() + said[1:] + "."
    if not_read:
        said += f" {not_read} result{' was' if not_read == 1 else 's were'} not read."
    return _Counted(said, determined=determined >= means and not undetermined and not not_read)


class _Counted(str):
    """The sentence :func:`_counted` says, with whether its counts make every
    mean determined (``determined``), which :func:`_determined` reads rather
    than the words: "none of the production's 4 thermodynamic means is
    determined" has none of the words it looked for, and the answer said "By
    its records, yes" before it."""

    determined: bool

    def __new__(cls, said: str, *, determined: bool) -> "_Counted":
        counted = super().__new__(cls, said)
        counted.determined = bool(determined)
        return counted


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
    from fastmdxplora.statistics import noted_beside

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
            # Its image note beside its verdict, which it does not change.
            noted = noted_beside(quantity.get("noted"))
            if not quantity.get("determined"):
                lines.append(f"{label}: {said}, not determined" + (f"; {why}" if why else "")
                             + noted + ".")
                continue
            samples = quantity.get("samples")
            discard, frames = quantity.get("from_frame"), quantity.get("of_frames")
            if isinstance(samples, (int, float)) and math.isfinite(samples):
                said += f", {samples:.0f} independent samples"
            if isinstance(discard, int) and isinstance(frames, int) and discard > 0:
                said += f", after the first {discard} of {frames} frames"
            if quantity.get("judged_earlier"):
                # Determined by an earlier version's rules, which may not
                # have withheld a mean still drifting; the report may differ.
                said += (", determined by an earlier version's rules (Analyze again "
                         "to judge it by this version's)")
            lines.append(f"{label}: {said}{noted}.")
    if not any(row.get("analysis") == "thermodynamics" for row in rows):
        lines += _thermodynamic_means(base)
    return lines


def _thermodynamic_means(base: Path) -> list[str]:
    """The production's thermodynamic means as the Overview gives them, for
    a study with no thermodynamics analysis: the summary named none while
    the Overview and the report gave each."""
    from fastmdxplora.gui.overview_view import THERMODYNAMICS, overview_payload
    from fastmdxplora.statistics import noted_beside

    try:
        means = (overview_payload(base).get("thermodynamics") or {}).get("means") or {}
    except Exception:  # noqa: BLE001 - the rest of the answer stands
        return []
    lines = []
    for key, label, _unit in THERMODYNAMICS:
        said = means.get(key)
        if not isinstance(said, dict) or not said.get("said"):
            continue
        noted = noted_beside(said.get("noted"))
        if said.get("determined"):
            samples = said.get("samples")
            more = f", {samples:.0f} independent samples" if isinstance(samples, (int, float)) else ""
            lines.append(f"{label}: {said['said']}{more}{noted}.")
        else:
            why = _first_clause(said.get("why"))
            lines.append(f"{label}: {said['said']}, not determined" + (f"; {why}" if why else "")
                         + noted + ".")
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
    from fastmdxplora.gui.telemetry import between_rounds

    if between_rounds(base) is not None:
        # Its study extends it in its next round: a piece run by hand beside
        # that would be one the study did not plan, or stop its own.
        return (ask.as_text() + " Its study extends this run itself, in its next round "
                "(simulation.stop_when); nothing needs running by hand.", "")
    from fastmdxplora.batch.explorer import campaign_of
    from fastmdxplora.simulation.resume import being_carried_on

    if being_carried_on(campaign_of(base) or base):
        # Still going, here or on another machine: an extension now is
        # refused (`resume.extend_study`), so none is offered while it goes.
        return (ask.as_text() + " It is still going; extend it once it has ended.", "")
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
    """Why no figure says how much more production the means need: for the
    means withheld as drifting between a joined run's segments, by name, as
    the Overview's lead says them (:func:`overview_view.drifting_said`);
    for one still drifting over the frames it averages, that no figure says
    how much longer; for any other withheld, that no figure was recorded.
    Told apart by each record's code, not its words: one drifting between
    segments was told to analyse again, which records no figure for it."""
    from fastmdxplora.gui.analysis_overview import overview_of
    from fastmdxplora.gui.overview_view import drifting_said
    from fastmdxplora.statistics import DRIFTING_CODES

    try:
        rows = overview_of(base).get("rows") or []
    except Exception:  # noqa: BLE001 - the plainer answer stands
        return NO_FIGURE
    between: list[str] = []
    within = other = False
    for row in rows:
        for q in row.get("quantities") or []:
            if not isinstance(q, dict) or q.get("determined") or q.get("value") is None:
                continue
            if q.get("drifting"):
                between.append(str(q.get("label") or row.get("title") or row.get("analysis")))
            elif q.get("withheld_code") in DRIFTING_CODES:
                within = True
            else:
                other = True
    said = [drifting_said(between)] if between else []
    if within:
        said.append(STILL_DRIFTING)
    if other or not said:
        said.append(NO_FIGURE)
    return " ".join(said)


def _drifting_between(base: Path) -> str:
    """The means withheld as drifting between a joined run's segments, by
    name, with their remedy, as the Overview's lead says them; empty for
    none. Beside what the means withheld for want of sampling need, which
    is not what these need."""
    from fastmdxplora.gui.analysis_overview import overview_of
    from fastmdxplora.gui.overview_view import drifting_said

    try:
        rows = overview_of(base).get("rows") or []
    except Exception:  # noqa: BLE001 - the rest of the answer stands
        return ""
    return drifting_said([str(q.get("label") or row.get("title") or row.get("analysis"))
                          for row in rows for q in row.get("quantities") or []
                          if isinstance(q, dict) and q.get("drifting")])


def _withheld_since(base: Path) -> str:
    """The means this version withholds that an earlier version called
    determined, by name, as the Overview counts them (``withheld_since``),
    and what records this version's verdict on them; empty for none. "Every
    mean withheld counted" left them out, and never said to analyse again."""
    from fastmdxplora.gui.overview_view import overview_payload

    try:
        named = list(overview_payload(base).get("withheld_since") or [])
    except Exception:  # noqa: BLE001 - the rest of the answer stands
        return ""
    if not named:
        return ""
    listed = named[0] if len(named) == 1 else ", ".join(named[:-1]) + " and " + named[-1]
    return (f"{listed}: called determined by an earlier version and withheld by this one; "
            "Analyze again to record this version's verdict.")


def _determined(supports: str) -> bool:
    """Whether the count (:func:`_supports`) says every mean is determined:
    the Overview's counts (:class:`_Counted`), or else the report's line
    that every observable equilibrated and holds enough independent samples
    to average."""
    if isinstance(supports, _Counted):
        return supports.determined
    return (supports.startswith(("All ", "Its mean is determined", "The production's"))
            and not any(said in supports for said in ("too few", "not determined", "not read")))


def _not_analysed(base: Path) -> str:
    """For a study with no analysis: that nothing has judged it yet, and a
    run that ended short is carried on first. It was told its analyses
    "recorded no figure" and to analyse again, and sent to a report it did
    not have."""
    said = ("Its records do not say yet: it has not been analysed, so no analysis "
            "has judged whether its series equilibrated.")
    try:
        # The study's state, as every page reads it (`workspace.study_state`,
        # its rule's round first), not its live record's.
        from fastmdxplora.gui.workspace import study_state

        status = str(study_state(base) or "").lower()
    except Exception:  # noqa: BLE001 - the plainer answer stands
        status = ""
    if status in ("stopped", "failed", "interrupted"):
        # What to do is the fix card's: carried on where a checkpoint was
        # written, run again or changed where none was or the run failed.
        # For a stop asked for, the card is titled "To carry it on".
        card = "To carry it on" if status == "stopped" else "What would fix it"
        said += (f" It ended before it finished: {card}, on the Overview, "
                 "says what to do first.")
    return said


def _long_enough(base: Path) -> str:
    parts: list[str] = []
    if not (base / "analysis").is_dir():
        return "\n\n".join([_not_analysed(base), TRAPPED])
    supports = _supports(base)
    asked = _ask(base)
    image = _image(base)
    if asked is not None:
        text, command = asked
        parts.append(" ".join(piece for piece in ("Not for all of its means.", supports)
                              if piece))
        # Every mean withheld, the quantities beside the report's
        # observables among them: the line above counts the observables.
        since = _withheld_since(base)
        drifting = _drifting_between(base)
        parts.append("What they need, every mean withheld counted: " + text
                     + (f" {since}" if since else "") + (f" {drifting}" if drifting else ""))
        if command:
            parts.append(f"To run it, extending the study in place:\n\n`{command}`")
        parts.extend(_not_a_longer_run_alone(base))
        parts.extend(image.noted)
    elif _determined(supports):
        beside = _the_image_beside(image) + image.noted
        # Never an unqualified yes before a paragraph saying the means may
        # be biased: the length answered, and the bias no length lifts.
        # Counted as the sentence below it counts them: "its means, though
        # they may be" was said where 1 of 3 was noted.
        them = ("its mean, though it may be" if image.means == 1
                else "its means, though they may be"
                if _the_image_beside(image) or image.count >= image.means
                else f"its means, though {image.count} of them may be")
        yes = (f"By its records, long enough to determine {them} biased by the "
               "solute's own image, which no length of run lifts. "
               if beside else "By its records, yes. ")
        parts.append(yes + supports)
        parts.extend(beside)
    elif supports:
        parts.append("Not by its records. " + supports)
        since = _withheld_since(base)
        if since:
            parts.append(since)
        parts.append(_no_figure(base))
        parts.extend(_not_a_longer_run_alone(base))
        parts.extend(image.noted)
    else:
        parts.append("Its records do not say yet: no analysis has judged whether its "
                     "series equilibrated.")
    # The seed sweep TRAPPED advises is in the same box, which samples the
    # same image: replicas are advised in a larger one first.
    parts.append(("Then replicas in a larger box. " if image.larger else "") + TRAPPED)
    return "\n\n".join(parts)


class _Image:
    """What the image check and the means' notes say of a run, read once
    for an answer: the check's finding, how many of the means the Overview
    counts carry a note (``noted`` of ``means``), the sentence saying so
    (:func:`fastmdxplora.image_note.noted_said`, in a list, empty where
    there is none) and whether replicas belong in a larger box."""

    def __init__(self, finding: Any, noted: int, means: int, counted: int | None = None) -> None:
        from fastmdxplora.image_note import in_a_larger_box, noted_said

        self.finding, self.means, self.count = finding, means, noted
        # Every mean the answer counts (:func:`_counted`), the production's
        # thermodynamic means with the analyses'.
        self.counted = means if counted is None else counted
        # No mean's note is beside an answer: it says where each is.
        said = noted_said(finding, noted, means, beside=False)
        self.noted = [said] if said else []
        self.larger = in_a_larger_box(finding, noted)


def _image(base: Path) -> _Image:
    """The run's :class:`_Image`, its counts the Overview's (its lead says
    "All 3 means are determined" of the same means)."""
    from fastmdxplora.gui.overview_view import overview_payload
    from fastmdxplora.image_note import recorded_finding

    try:
        payload = overview_payload(base)
    except Exception:  # noqa: BLE001 - the counts are said as none
        payload = {}
    counts = [payload.get(key) for key in ("noted", "means")]
    noted, means = (value if isinstance(value, int) and not isinstance(value, bool) else 0
                    for value in counts)
    return _Image(recorded_finding(base), noted, means,
                  means + len(_thermodynamic_counted(base, payload)))


def _not_a_longer_run_alone(base: Path) -> list[str]:
    """Where the solute met its own image in every frame, that the longer
    run asked for does not lift that bias, as the report says it
    (:func:`fastmdxplora.image_note.longer_run_said`)."""
    from fastmdxplora.image_note import longer_run_said, recorded_finding

    lifted = longer_run_said(recorded_finding(base))
    return [lifted] if lifted else []


def _the_image_beside(image: _Image) -> list[str]:
    """Beside means all determined, where the solute met its own image in
    every frame, that they may still be biased by it, and what lifts it
    (:func:`fastmdxplora.image_note.determined_said`, with how many means
    there are, so one is "its mean"): never "yes" alone."""
    from fastmdxplora.image_note import determined_said

    said = determined_said(image.finding, image.means or None)
    return [said] if said else []


def _strengthen(base: Path) -> str:
    from fastmdxplora.gui.exploration import study_a_run_belongs_to

    parts: list[str] = []
    supports = _supports(base)
    asked = _ask(base)
    image = _image(base)
    if asked is not None:
        text, command = asked
        parts.append("A longer run first: the means it withheld have to be given before "
                     "replicas can be compared on them.")
        drifting = _drifting_between(base)
        parts.append("What they need: " + text + (f" {drifting}" if drifting else ""))
        if command:
            parts.append(f"`{command}`")
        parts.extend(_not_a_longer_run_alone(base))
        parts.extend(image.noted)
    elif _determined(supports):
        # "Its means are determined" beside "Its mean may be biased" of a
        # run with one.
        parts.append(("Its mean is determined" if image.counted == 1 else
                      "Its means are determined")
                     + ", so a longer run of this one adds precision but not a check.")
        parts.extend(_the_image_beside(image) + image.noted)
    elif supports:
        parts.append("A longer run first. " + supports)
        parts.append(_no_figure(base))
        parts.extend(_not_a_longer_run_alone(base))
        parts.extend(image.noted)
    of = study_a_run_belongs_to(base)
    if of:
        parts.append(f"This run is one of the study {of.get('study')}. Its Report page "
                     "sets the runs' spread against each run's own error, which is the "
                     "check a single run cannot make.")
    else:
        # A mean noted, or the solute in contact with its image in every
        # frame: replicas in the same box sample the same image.
        larger = " in a larger box" if image.larger else ""
        parts.append(f"Then replicas{larger}. " + TRAPPED)
    return "\n\n".join(parts)


def _several_runs(base: Path) -> str:
    from fastmdxplora.gui.exploration import runs_of_a_study

    runs = runs_of_a_study(base) or []
    done = sum(1 for run in runs if run.get("state") == "completed")
    between = sum(1 for run in runs if run.get("so_far"))
    rounds = (f", {between} still extended in rounds until what it asks is determined"
              if between else "")
    parts = [f"This is a study of {len(runs)} runs, {done} completed{rounds}. Each run keeps its "
             "own records: view one from Runs in the sidebar and ask about it, or read the "
             "comparison across them on the Report page."]
    from fastmdxplora.simulation.stopping import round_ended

    ended = round_ended(base)
    if ended is not None:
        # Its runs each completed and the study did not: said first, as
        # every page says it (`stopping.round_ended`).
        parts.insert(0, f"{ended.said} {ended.carry_on_on_a_page}")
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
