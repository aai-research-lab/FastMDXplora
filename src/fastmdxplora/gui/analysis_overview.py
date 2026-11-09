"""What a study's analyses determined, read together, and how each series
converged.

The Analysis page showed each analysis as a figure with a caption under it,
so the question a reader opens the page with -- what did this study find,
and which of it holds -- needed every card read in turn, and a mean the run
was too short to determine looked, at a glance, like any other. This reads
what each analysis recorded beside its figure (`findings.mean` in its
`options.json`, or the reweighted mean a biased run recovered) and says it
as one table: the mean to the place its error allows, the frames it rests
on, and whether it was determined or why not. Nothing is computed again; a
number here is the number the report gives.

The second half is a series' convergence (:func:`convergence_payload`): the
same series the figure plots, read by
:func:`fastmdxplora.statistics.convergence_of`, the package's own estimator,
so the running mean, the block averages and the correlation shown are the
ones the recorded error rests on. Where the recorded mean started from a
point found elsewhere (replicas share their start) the view says so rather
than show a second start as if it were the one used.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from fastmdxplora import record_access
from fastmdxplora.gui.report_dashboard import (
    ANALYSIS_SECTION_BY_FOLDER,
    SECTION_ANCHORS,
    SECTION_ORDER,
    SECTION_THEME,
    unit_of,
)
from fastmdxplora.gui.series import (
    NAME,
    NOT_READ_BECAUSE,
    analysed_axis,
    refused_said,
    series_column,
    why_not_read,
)

#: The manifest's status words, as the page says them.
_STATUS_SAID = {"ok": "done", "completed": "done", "complete": "done",
                "failed": "failed", "error": "failed", "skipped": "skipped"}


def overview_of(root: str | Path, *, include_time: bool = True,
                include_data: bool = True) -> dict[str, Any]:
    """Every analysis of the study, in the page's order, with what it
    determined: ``{"ok", "rows", "biased", "complete"}``, ``complete`` once
    the analysis phase has recorded every analysis it ran.

    A row is ``analysis``, ``title``, ``theme``, ``anchor``, ``status``
    (done, failed, skipped), ``message`` (a failure's reason), ``kind``
    (``mean`` where it recorded a mean over its frames, else ``result``),
    ``mean`` (its main quantity's, or None) and ``quantities``: the main
    mean first, labelled by the analysis, then any other quantity it
    recorded a mean of over its frames (the helix fraction beside the
    secondary structure, the asphericity beside the moments of inertia),
    each ``key``, ``label`` and ``value``, ``error``, ``unit``, ``said``
    (the mean to the place its error allows, with its unit),
    ``determined``, ``why`` (where it is not), ``samples`` (independent
    samples, a whole number as every page says them), ``from_frame``, ``of_frames``, ``from_ns`` and
    ``reweighted`` and ``judged_earlier`` (determined by an earlier
    version's rules), ``withheld_since`` (called determined by an earlier
    version's rules, withheld by this version's), ``noted`` and ``noted_why`` (its image note beside
    the verdict, which it does not change, and the analysis's paragraph of
    it: :mod:`fastmdxplora.image_note`); and ``data``, its data files with
    their addresses. The image check's row gives its ``finding`` in a
    sentence, ``finding_why`` its paragraph and ``atoms_said`` the atoms it
    checks.
    ``judged_earlier`` beside the rows names the analyses holding such a
    mean.

    ``include_time=False`` omits the trajectory clock lookup, and
    ``include_data=False`` omits discovery of downloadable data files.
    Both leave the recorded means and their scientific verdicts unchanged.
    """
    root = Path(root)
    folder = root / "analysis"
    manifest = _json(folder / "analysis_manifest.json")
    # A manifest a confined read refused (`record_access`) says nothing of
    # which analyses are still running.
    refused = not record_access.can_read(folder / "analysis_manifest.json")
    results = manifest.get("results") if isinstance(manifest.get("results"), dict) else {}
    names = [str(name) for name in results]
    try:
        # An analysis's folder, or its record, that a confined read refused
        # is listed too, to say its record was not read.
        names += sorted(p.name for p in record_access.iterdir(folder)
                        if NAME.match(p.name) and p.name not in results
                        and (not record_access.can_read(p / "options.json")
                             or (record_access.is_dir(p)
                                 and record_access.is_file(p / "options.json"))))
    except OSError:
        pass

    from fastmdxplora.report.reweighted import load_reweighted

    biased = load_reweighted(root)
    corrected = {item.get("analysis"): item
                 for item in ((biased or {}).get("quantities") or [])
                 if isinstance(item, dict)}

    # The manifest's results are written once every analysis has run
    # (analysis/orchestrator.py); until then a folder holds an analysis
    # still computing or one finished, told apart by what it recorded.
    complete = bool(results)
    rows = [_row(root, name, results.get(name), corrected, biased is not None,
                 complete or refused, include_time=include_time, include_data=include_data)
            for name in names]
    if refused:
        # Its status is in the manifest, which was not read: an analysis
        # with no result read is said not read, not done (nor failed).
        why = NOT_READ_BECAUSE[why_not_read(root, folder / "analysis_manifest.json")]
        for row in rows:
            if not row["quantities"]:
                row["status"] = "not read"
                row["message"] = ("Its status is in analysis/analysis_manifest.json, which "
                                  f"was not read: {why}.")
    order = {title: index for index, title in enumerate(SECTION_ORDER)}
    rows.sort(key=lambda row: (order.get(row["title"], len(order)), row["analysis"]))
    # The analyses a mean of which an earlier version called determined: a
    # study analysed before this version's rules reads its old verdicts
    # until it is analysed again, and the report written since says
    # otherwise of the same mean.
    earlier = list(dict.fromkeys(
        row["title"] for row in rows
        if any(q.get("judged_earlier") for q in row["quantities"])))
    return {"ok": True, "rows": rows, "biased": biased is not None, "complete": complete,
            "judged_earlier": earlier}


#: Where a report or the slides say a figure an earlier version plotted
#: with the error of a mean this version withholds, by the figure's path
#: from the study's root (:func:`plotted_since`).
SUMMARY_FIGURE = "report/analysis_summary.png"


def plotted_since(root: str | Path, overview: dict[str, Any] | None = None) -> dict[str, str]:
    """The figures an earlier version plotted with the error of a mean this
    version withholds (``withheld_since``), each with the sentence said
    under it, by its path from the study's root: an analysis's own figure
    (``rmsd.png``, its greyscale copy and SVG), which plots its mean with
    its error band, the thermodynamics' bars of its means' errors, and the
    summary figure's panels made from them. The report written by this
    version embedded ``rmsd.png`` with "0.0078 \u00b1 0.0010 nm" and its
    band in its legend, beside its own "not determined" of that mean.
    Analysing again plots them again; the report phase does not."""
    from fastmdxplora.statistics import MINIMUM_EFFECTIVE_SAMPLES

    root = Path(root)
    if overview is None:
        overview = overview_of(root, include_time=False, include_data=False)
    rule = (f"fewer than the {MINIMUM_EFFECTIVE_SAMPLES:g} independent samples it "
            "determines a mean on")
    again = "Analyze again to plot it again."
    plotted: dict[str, list[str]] = {}
    for row in overview.get("rows") or []:
        name = str(row.get("analysis") or "")
        # The figure plots the analysis's main mean with its error; the
        # thermodynamics' plots each of its means' errors as a bar.
        labels = [str(q.get("label") or name) for q in row.get("quantities") or []
                  if isinstance(q, dict) and q.get("withheld_since")
                  and (q.get("key") == "mean" or (name == "thermodynamics"
                                                  and str(q.get("key")).startswith(name)))]
        if not labels:
            continue
        for stem in (name, f"{name}_greyscale"):
            for suffix in (".png", ".svg"):
                path = f"analysis/{name}/{stem}{suffix}"
                if record_access.is_file(root / path):
                    plotted[path] = labels
    said = {path: (f"Plotted by an earlier version with {'an error' if len(labels) == 1 else 'errors'} "
                   f"this version withholds ({_listed(labels)}: {rule}). {again}")
            for path, labels in plotted.items()}
    if record_access.is_file(root / SUMMARY_FIGURE):
        manifest = _json(root / "report" / "analysis_summary_manifest.json")
        panels = [panel for panel in manifest.get("included") or []
                  if isinstance(panel, dict) and panel.get("source") in plotted]
        if panels:
            letters = _listed([str(panel.get("panel")) for panel in panels])
            labels = list(dict.fromkeys(label for panel in panels
                                        for label in plotted[panel["source"]]))
            said[SUMMARY_FIGURE] = (
                f"{'Panel' if len(panels) == 1 else 'Panels'} {letters} "
                f"{'was' if len(panels) == 1 else 'were'} plotted by an earlier version with "
                f"{'an error' if len(labels) == 1 else 'errors'} this version withholds "
                f"({_listed(labels)}: {rule}). {again}")
    return said


def _listed(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _row(root: Path, name: str, result: Any, corrected: dict[Any, Any],
         biased: bool, complete: bool = True, *, include_time: bool = True,
         include_data: bool = True) -> dict[str, Any]:
    title = ANALYSIS_SECTION_BY_FOLDER.get(name) or name.replace("_", " ").capitalize()
    entry = result if isinstance(result, dict) else {}
    status = str(entry.get("status") or "done").lower()
    if not entry and not complete and not _has_its_figure(root, name):
        # Its options are written before it computes and its figure after.
        status = "running"
    row: dict[str, Any] = {
        "analysis": name,
        "title": title,
        "theme": SECTION_THEME.get(title, "Other"),
        "anchor": SECTION_ANCHORS.get(title, ""),
        "status": _STATUS_SAID.get(status, status),
        "message": _said(name, entry.get("message")) if status not in _STATUS_SAID
        or _STATUS_SAID[status] != "done" else "",
        "kind": "result",
        "mean": None,
        "quantities": [],
        "data": _data_files(root, name) if include_data else [],
    }
    record_path = root / "analysis" / name / "options.json"
    record = _json(record_path)
    if not record_access.can_read(record_path):
        # Its record leads out of the study, or to a hidden file in it: not
        # read (the confined read refused it), and said so, rather than read
        # as an analysis that recorded no mean (a profile, map or table).
        row["quantities"].append({"key": "mean", "label": title, **_not_read(root, record_path)})
        row["kind"] = "mean"
        return row
    findings = record.get("findings") if isinstance(record.get("findings"), dict) else {}
    found = findings.get("mean") if isinstance(findings.get("mean"), dict) else None
    if name == "image_check":
        # Its finding as a sentence in its row, the paragraph one click away,
        # and the atoms it checks said as they are (not the analyses' scope).
        from fastmdxplora.image_note import finding_said, paragraph_of

        image = findings.get("solute_image")
        row["finding"] = finding_said(image)
        # The rest of its paragraph, one click away: the finding not again.
        paragraph = " ".join(paragraph_of(findings).split())
        row["finding_why"] = (paragraph[len(row["finding"]):].strip()
                              if row["finding"] and paragraph.startswith(row["finding"])
                              else paragraph)
        if isinstance(image, dict) and image.get("solute"):
            row["atoms_said"] = "heavy atoms of " + str(image["solute"]).split(";")[0]
    main = None
    if biased and name in corrected:
        main = _reweighted(name, corrected[name], found)
    elif found is not None:
        main = _recorded(root, name, found, biased, include_time=include_time,
                         findings=findings)
    if main is not None:
        row["mean"] = main
        row["quantities"].append({"key": "mean", "label": title, **main})
    for key, other in findings.items():
        if key != "mean" and _is_a_mean_record(other):
            row["quantities"].append({"key": key, "label": _label(key),
                                      **_recorded(root, name, other, biased,
                                                  include_time=include_time,
                                                  findings=findings)})
        elif key != "mean" and isinstance(other, dict):
            # A group of quantities read from another record than the
            # trajectory (thermodynamics: the run's energy file), each with
            # its `units` and over the group's `samples`, on that record's
            # own clock, so no trajectory time is given for its start.
            samples = _count(other.get("samples"))
            for inner, quantity in other.items():
                if isinstance(quantity, dict) and "mean" in quantity and (
                        "units" in quantity or "discard" in quantity):
                    record = {**quantity, "unit": quantity.get("units", quantity.get("unit")),
                              "n_frames": quantity.get("n_frames", samples)}
                    row["quantities"].append({
                        "key": f"{key}.{inner}", "label": _label(inner),
                        **_recorded(root, name, record, biased, timed=False,
                                    findings=findings)})
    if row["quantities"]:
        row["kind"] = "mean"
    elif name != "image_check" and _noted(findings, findings)["noted"]:
        # A profile, map or table (RMSF, the order parameters, the dihedrals,
        # clustering, the projections): no mean to say it beside, so its
        # image note is said on its row and its card.
        row.update(_noted(findings, findings))
    return row


#: Words a quantity's key spells in lower case that are said otherwise.
_SAID_AS = {"sasa": "SASA", "rmsd": "RMSD", "rmsf": "RMSF", "rg": "Rg", "pca": "PCA",
            "nm2": "", "nm": "", "ml": "mL"}


def _label(key: str) -> str:
    """A recorded quantity's key as a reader says it: hydrophobic_sasa is
    "Hydrophobic SASA", relative_shape_anisotropy "Relative shape anisotropy"."""
    words = [_SAID_AS.get(word, word) for word in key.split("_")]
    text = " ".join(word for word in words if word)
    return text[:1].upper() + text[1:]


#: What an analysis writes that a reader would open in another program.
_DATA_SUFFIXES = (".dat", ".csv", ".tsv")


def _data_files(root: Path, name: str) -> list[dict[str, str]]:
    """The analysis's data files, its own first, each with its address."""
    folder = root / "analysis" / name
    try:
        files = sorted((p for p in record_access.iterdir(folder)
                        if record_access.is_file(p) and p.suffix.lower() in _DATA_SUFFIXES),
                       key=lambda p: (p.stem != name, p.name))
    except OSError:
        return []
    return [{"name": p.name, "href": f"/artifacts/analysis/{name}/{p.name}"}
            for p in files[:12]]


def _has_its_figure(root: Path, name: str) -> bool:
    folder = root / "analysis" / name
    return any(record_access.is_file(folder / f"{name}{suffix}")
               for suffix in (".png", ".svg"))


def _said(name: str, message: Any) -> str:
    """A failure's message without the analysis's name the manifest puts
    before it, which the row already shows."""
    # Read on the page as plain text: the code marks of a setting to give
    # (`stride: 2`) are left out, as the record keeps them.
    text = str(message or "").strip().replace("`", "")
    prefix = f"{name}: "
    return text[len(prefix):] if text.startswith(prefix) else text


def _is_a_mean_record(value: Any) -> bool:
    """What `statistics.mean_record` writes: a mean over a number of frames,
    or the reason it withheld one."""
    return (isinstance(value, dict) and "n_frames" in value
            and ("mean" in value or "not_a_measurement" in value))


def _noted(found: dict[str, Any] | None, findings: dict[str, Any] | None, *,
           why: str | None = None, unit: str | None = None) -> dict[str, Any]:
    """A mean's notes, beside its verdict, which they do not change: its
    image note in a line, read from the record by the one reader every page
    and the Agent use (:mod:`fastmdxplora.image_note`; ``image_said``), and
    what its joined run's test for drift between segments could miss
    (:func:`fastmdxplora.statistics.drift_unseen_said`; ``unseen``, a
    sentence); both together for a reader that says them in one place
    (``noted``); the analysis's paragraph of the image note (``noted_why``);
    and whether the image note is among them (``image_noted``), for what
    counts the means it notes. Empty where neither is said."""
    from fastmdxplora.image_note import note_of, paragraph_beside, said_short
    from fastmdxplora.statistics import drift_unseen_said

    unseen = "" if why else drift_unseen_said(found, unit)
    note = note_of(found)
    short = said_short(note)
    if not short:
        return {"noted": unseen or None, "noted_why": None, "image_noted": False,
                "image_said": None, "unseen": unseen or None}
    # Said as the analysis's where it counts other frames than this mean's;
    # not again where the reason the mean was not determined says it.
    paragraph = paragraph_beside(note if found is not findings else None, findings)
    if paragraph and why and paragraph.split(". ")[0] in why:
        paragraph = ""
    return {"noted": f"{short}. {unseen}" if unseen else short,
            "noted_why": paragraph or None, "image_noted": True,
            "image_said": short, "unseen": unseen or None}


def too_few_by_these_rules(found: Any) -> str | None:
    """Why a mean an earlier version's rules called determined is not one by
    this version's (:func:`fastmdxplora.statistics.too_few_by_these_rules`),
    said here as it was; every reader of a verdict asks
    :func:`fastmdxplora.statistics.withheld_reason`, which reads it."""
    from fastmdxplora.statistics import too_few_by_these_rules as too_few

    return too_few(found)


def _recorded(root: Path, name: str, found: dict[str, Any], biased: bool,
              timed: bool = True, *, include_time: bool = True,
              findings: dict[str, Any] | None = None) -> dict[str, Any]:
    from fastmdxplora.statistics import (
        RESOLVED_SAMPLES,
        four_figures,
        independent_samples_said,
        judged_by_earlier_rules,
        too_few_by_these_rules,
        with_its_error,
        withheld_code,
        withheld_reason,
    )

    unit = unit_of(name, found)
    value = _finite(found.get("mean"))
    error = _finite(found.get("standard_error"))
    samples = _finite(found.get("effective_samples"))
    n = _count(found.get("n_frames"))
    discard = _count(found.get("discard")) or 0
    why = withheld_reason(found)
    if biased and timed:
        why = ("The run was biased, so this is an average over the ensemble the "
               "bias flattened, not the equilibrium one; no reweighted value was "
               "recovered for it.")
    elif value is None and why is None:
        why = "No mean was recorded."
    elif error is None and why is None:
        if samples is not None and samples < RESOLVED_SAMPLES:
            # Counted as every page counts them: 9.5 is "about 9", not 10.
            count = independent_samples_said(samples, about=True)
            why = (f"{count[:1].upper()}{count[1:]}, fewer than the "
                   f"{RESOLVED_SAMPLES:g} an error bar needs.")
        else:
            why = "No error bar was recorded."
    constant = (value is not None and _finite(found.get("standard_deviation")) == 0.0
                and error in (None, 0.0) and not biased)
    if constant:
        # The same in every frame (no strand formed): said as that, not as
        # "1 independent sample" of a mean not determined.
        why = ("The same in every frame analysed: there is no spread to estimate an "
               "error from, and a longer run may still change it.")
        samples = None
    determined = why is None and value is not None and error is not None
    suffix = f" {unit}" if unit else ""
    if constant:
        said = f"{four_figures(value)}{suffix} in every frame"
    elif value is None:
        said = "not determined"
    elif determined:
        said = with_its_error(value, error) + suffix
    else:
        said = four_figures(value) + suffix
    from_ns = None
    if constant:
        # The same in every frame: no start to say ("0 in every frame \u00b7
        # from 67 ps").
        discard = None
    if include_time and n and timed and discard is not None:
        _, x, label = analysed_axis(root, n)
        if label == "Time (ns)" and discard < len(x):
            from_ns = x[discard]
    return {
        # No error beside a mean not determined: the table's Download gave
        # one beside "Determined: no" for a mean this version withholds.
        "value": value, "error": error if determined else None, "unit": unit,
        "said": said, "determined": determined, "why": why,
        "samples": _samples(samples),
        "from_frame": discard, "of_frames": n, "from_ns": from_ns,
        # Frames after the discard without a value, so not averaged.
        "without_a_value": _count(found.get("without_a_value")) or 0,
        "reweighted": False, "constant": constant,
        # Withheld by this version from a mean an earlier version's rules
        # called determined (`statistics.too_few_by_these_rules`), which
        # analysing again records this version's verdict on, and whose
        # figure that version plotted with its error.
        "withheld_since": (not determined and not constant and not (biased and timed)
                           and too_few_by_these_rules(found) is not None),
        # Determined by the rules of the version that analysed it, which
        # may have called a mean still drifting determined.
        "judged_earlier": determined and judged_by_earlier_rules(found),
        # Withheld as drifting between a joined run's segments, by its
        # record's code: the Overview's lead names it with its remedy.
        "drifting": (not determined and not constant and not (biased and timed)
                     and withheld_code(found) == "analysis.sampling.drifting"),
        # The code it is withheld under, for a reader to branch on, not its
        # words.
        "withheld_code": ("" if determined or constant or (biased and timed)
                          else withheld_code(found)),
        **_noted(found, findings, why=why, unit=unit),
    }


def _samples(count: float | None) -> int | None:
    """Independent samples as the table's column gives them, by the one rule
    every page says them by (`statistics.independent_samples_said`): 9.5 was
    shown as 10 beside "not determined: 9.5 independent samples ... Below
    10", where every other page says 9."""
    from fastmdxplora.statistics import independent_samples_whole

    return independent_samples_whole(count) if count is not None else None


def _not_read(root: Path, path: Path) -> dict[str, Any]:
    """A mean whose record a confined read refused: no value, said so."""
    return {
        "value": None, "error": None, "unit": "", "said": "record unavailable",
        "determined": False, "why": refused_said(root, path) + ".",
        "samples": None, "from_frame": None, "of_frames": None, "from_ns": None,
        "reweighted": False, "constant": False, "judged_earlier": False,
        "withheld_since": False, "unavailable": True, "noted": None, "noted_why": None,
    }


def _reweighted(name: str, item: dict[str, Any], found: dict[str, Any] | None) -> dict[str, Any]:
    from fastmdxplora.statistics import four_figures, with_its_error

    unit = unit_of(name, found)
    value = _finite(item.get("reweighted_mean"))
    error = _finite(item.get("reweighted_standard_error"))
    samples = _finite(item.get("independent_samples"))
    suffix = f" {unit}" if unit else ""
    determined = value is not None and error is not None
    if value is None:
        said, why = "not determined", "The reweighting recovered no value."
    elif determined:
        said, why = with_its_error(value, error) + suffix, None
    else:
        said = four_figures(value) + suffix
        why = ("Reweighted from the biased run; too few independent samples "
               "behind the weights for an error bar.")
    return {
        "value": value, "error": error, "unit": unit, "said": said,
        "determined": determined, "why": why,
        "samples": _samples(samples),
        "from_frame": None, "of_frames": None, "from_ns": None, "reweighted": True,
        "constant": False, "judged_earlier": False, "withheld_since": False,
        # Over every frame, as the reweighting weighs them: its own note.
        **_noted(item, item),
    }


def convergence_payload(root: str | Path, analysis: str) -> dict[str, Any]:
    """How one analysis's series converged, or why it cannot be said.

    The series is the column the figure plots (:func:`series_column`), on
    the axis the analysis loaded it with; the result is
    :func:`fastmdxplora.statistics.convergence_of`'s, with ``label``,
    ``unit``, ``x_label`` and ``recorded``: the mean and error the analysis
    recorded, which are the ones the report gives, the reason it withheld
    the error where it did (an analysis can withhold for a reason the series
    cannot show, a chain reaching its own periodic image), and
    ``same_start`` saying whether it began where this view's does. The page
    states the record's mean and error, never the view's own.
    """
    from fastmdxplora.statistics import convergence_of

    if not NAME.match(analysis or ""):
        return {"ok": False, "reason": "not an analysis name"}
    root = Path(root)
    folder = root / "analysis" / analysis
    data = folder / f"{analysis}.dat"
    # Said by the name the page gives it, not its folder's.
    named = ANALYSIS_SECTION_BY_FOLDER.get(analysis, analysis)
    for path in (data, folder / "options.json"):
        if not record_access.can_read(path):
            return {"ok": False, "unavailable": [path.relative_to(root).as_posix()],
                    "reason": f"{refused_said(root, path)}, so {named} is not shown"}
    if not record_access.is_file(data):
        return {"ok": False, "reason": f"{named} wrote no data file"}
    record = _json(folder / "options.json")
    findings = record.get("findings") if isinstance(record.get("findings"), dict) else {}
    found = findings.get("mean") if isinstance(findings.get("mean"), dict) else None
    if found is None or _finite(found.get("mean")) is None:
        # A record holding only a reason (the moments of a molecule broken
        # across the box) has no series of its own: the last column of its
        # file was I3, which nothing recorded a mean of.
        return {"ok": False, "reason": f"{named} recorded no mean over its frames, "
                                       "so it has no series to converge"}
    if _columns(data) > 2:
        return {"ok": False, "reason": f"{named}'s data file holds several quantities "
                                       "a frame, and its mean is of none of them alone"}
    values = series_column(data)
    if not values:
        return {"ok": False, "reason": f"{named}'s data file holds no numbers"}
    frames, x, x_label = analysed_axis(root, len(values))
    timed = x_label == "Time (ns)"
    result = convergence_of(values, x if timed else None)
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason") or "too short a series"}
    recorded_start = _count(found.get("discard"))
    start = (result.get("equilibration") or {}).get("discard_frames")
    # The mean as the table above the panel gives it: its error only where
    # the record stands behind it. The panel gave "0.0881 \u00b1 0.00199 nm"
    # under the table's "0.08811 nm, Not determined", the record holding
    # both an error and the reason it is not one.
    from fastmdxplora.statistics import four_figures, with_its_error, withheld_reason

    mean, error = _finite(found.get("mean")), _finite(found.get("standard_error"))
    # Withheld by its record, or by this version's count of a record an
    # earlier version judged (:func:`too_few_by_these_rules`).
    withheld = withheld_reason(found)
    if withheld:
        error = None
    unit = unit_of(analysis, found)
    said = None
    if mean is not None:
        said = (with_its_error(mean, error) if error is not None else four_figures(mean))
        said = f"{said} {unit}".strip()
    result.update({
        "analysis": analysis,
        "label": ANALYSIS_SECTION_BY_FOLDER.get(analysis, analysis),
        "unit": unit_of(analysis, found),
        "x_label": x_label,
        "time_unit": "ns" if timed else None,
        "recorded": {
            "mean": mean,
            "standard_error": error,
            "said": said,
            "not_a_measurement": withheld or None,
            "error_withheld_because": found.get("error_withheld_because") or None,
            # Its notes, as the table above says them.
            **_noted(found, findings, unit=unit),
            "discard_frames": recorded_start,
            "same_start": recorded_start is None or recorded_start == start,
            "start_shared_with_replicas": bool(found.get("start_shared_with_replicas")),
        },
    })
    return result


def _columns(path: Path) -> int:
    """The most numbers on one data line of ``path``."""
    from fastmdxplora.gui.series import _rows

    return max((len(numbers) for _, numbers in _rows(path)), default=0)


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(record_access.read_text(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None
