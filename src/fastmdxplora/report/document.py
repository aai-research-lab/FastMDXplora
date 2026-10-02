"""Structured study report (Markdown).

Produces a publication-style report from the project state:

  - Header (title, authors, date)
  - Methods (auto-populated from setup + simulation parameter manifests)
  - Results (figures + summary tables from the analysis manifest)
  - Discussion (stub for the user to fill in)
  - Citation (FastMDXplora + JCC paper)
  - Reproducibility appendix (command-line invocation, software versions,
    parameter manifests, input hashes)

PDF rendering of this report is an optional add-on (requires extra
dependencies); the Markdown source is always produced.
"""

from __future__ import annotations

import json
import os
from fastmdxplora.utils.logging import get_logger
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from typing import TYPE_CHECKING
from urllib.parse import quote

from fastmdxplora.report.context import PhaseContext, load_phase_context
from fastmdxplora.report.reweighted import (
    load_reweighted,
    reweighted_line,
    reweighted_section,
)

if TYPE_CHECKING:
    from fastmdxplora.orchestrator import FastMDXplora

logger = get_logger("report.document")


def _one_line(value: object, *, limit: int = 1000) -> str:
    text = str(value)
    text = " ".join(text.replace("\t", " ").splitlines())
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "..."
    return text


def _md_text(value: object, *, limit: int = 1000) -> str:
    text = _one_line(value, limit=limit)
    for char in "\\`*_{}[]()#+-.!|<>":
        text = text.replace(char, f"\\{char}")
    return text


def _code_text(value: object, *, limit: int = 1000) -> str:
    return _one_line(value, limit=limit).replace("`", "'")


def _link_target(path: str) -> str:
    return quote(path, safe="/._-")


def _link_from(report_dir: Path, target: Path) -> str:
    """A link to `target` that works from the page written in `report_dir`.

    Markdown resolves a relative link against the file's own folder, and so
    does every reader of it: a viewer, the PDF renderer, the GUI. The report
    is written in report/ and the analyses in analysis/, so a figure is
    `../analysis/...` from there, not the `analysis/...` it is from the
    study's root.
    """
    return _link_target(Path(os.path.relpath(target, report_dir)).as_posix())


def _caption_of(analysis: str, stem: str) -> str:
    """`rmsd` for rmsd.png, `cluster: kmeans counts` for cluster_kmeans_counts.png."""
    rest = stem[len(analysis):] if stem.startswith(analysis) else stem
    rest = rest.strip("_").replace("_", " ")
    return f"{analysis}: {rest}" if rest else analysis


def _load_json_safely(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        logger.warning("Could not parse JSON manifest at %s", path)
        return None


def _setup_record(project_root: Path) -> dict:
    """The setup record of the system this run simulated.

    Its own, or, for a run given `setup_from`, the named system's: that run
    prepared nothing, and the system it simulated is described there.
    """
    from fastmdxplora.simulation.pipeline import setup_records_of

    records = setup_records_of(project_root)
    if records is None:
        return {}
    return _load_json_safely(records / "setup_parameters.json") or {}


def _study_in_one_paragraph(project_root: Path) -> str | None:
    """What was simulated, for how long, and what came of it.

    The summary said "This report was generated automatically by FastMDXplora
    from the outputs of an end-to-end molecular dynamics study" -- a statement
    about the software, in the first section a reader reads, of a document
    about their system. Everything needed to say something is recorded by the
    time this runs.

    Returns None where too little was recorded to say anything, because a
    sentence assembled from three absent values is worse than the generic one
    it replaces.
    """
    def flattened(record: dict) -> dict:
        """A manifest with its nested `parameters` lifted to the top.

        Values live at either level depending on whether they were requested
        or measured, and reading only the top missed the duration on manifests
        that record it under `parameters` -- so the summary fell back to the
        generic sentence for a run that had one.
        """
        merged = dict(record)
        nested = record.get("parameters")
        if isinstance(nested, dict):
            for key, value in nested.items():
                if merged.get(key) is None:
                    merged[key] = value
        return merged

    setup = flattened(_setup_record(project_root))
    sim = flattened(_load_json_safely(
        project_root / "simulation" / "simulation_parameters.json") or {})

    said: list[str] = []

    atoms = (setup or {}).get("n_atoms_solvated")
    if isinstance(atoms, int):
        said.append(f"The prepared system contained {atoms:,} atoms")

    duration = (sim or {}).get("duration_ns_actual") or (sim or {}).get("duration_ns")
    if isinstance(duration, (int, float)) and duration > 0:
        length = (f"{duration * 1000:.0f} ps" if duration < 1
                  else f"{duration:.3g} ns")
        # Recorded under `parameters`, with the top level kept as a fallback
        # for manifests written before that nesting.
        temperature = sim.get("temperature_K")
        at = f" at {temperature:g} K" if isinstance(temperature, (int, float)) else ""
        said.append(("and was simulated" if said else "The system was simulated")
                    + f" for {length}{at}")

    if not said:
        return None

    paragraph = " ".join(said).rstrip(".") + "."

    analyses = _load_json_safely(
        project_root / "analysis" / "analysis_manifest.json") or {}
    records = analyses.get("analyses") or analyses.get("results") or []
    if isinstance(records, list) and records:
        ran = sum(1 for r in records
                  if isinstance(r, dict) and r.get("status") == "ok")
        if ran:
            paragraph += f" {ran} analys{'is' if ran == 1 else 'es'} completed."

    return paragraph


def _what_the_run_supports(project_root: Path) -> str | None:
    """One line on how much of the run can be interpreted.

    The convergence section reports this per observable. Saying it once at the
    top is what stops a reader taking the results at face value and meeting
    the caveat six pages later.
    """
    assessed = _assess_this_run(project_root)
    if not assessed:
        return None

    records = list(assessed["observables"].values())
    if not records:
        return None

    # The three states are exclusive, so the counts add up. A first version
    # counted "equilibrated" and "could not be judged" from overlapping
    # conditions and reported three and six out of six.
    total = len(records)
    equilibrated = sum(
        1 for r in records
        if r.get("equilibrated", r.get("settled")) is True)
    drifting = sum(
        1 for r in records
        if r.get("equilibrated", r.get("settled")) is False)
    unjudged = total - equilibrated - drifting
    # Equilibrated and adequately sampled are different questions. A real
    # study reported all five observables equilibrated while two held six
    # and ten
    # independent samples -- at or under the point where a mean stops
    # describing the system -- and the summary said only the first.
    thin = sum(1 for r in records if not r.get("sampled_enough", True))

    what = "observable" if total == 1 else "observables"
    if equilibrated == total and not thin:
        return (f"All {total} {what} assessed had equilibrated and hold "
                "enough independent samples to average.")
    if equilibrated == total:
        return (f"All {total} {what} assessed had equilibrated, but {thin} "
                "hold too few independent samples for the mean to describe "
                "the system rather than this run; see [Convergence](#convergence).")

    parts = []
    if equilibrated:
        parts.append(f"{equilibrated} had equilibrated")
    if drifting:
        parts.append(f"{drifting} had not")
    if unjudged:
        parts.append(f"{unjudged} could not be judged from a run this length")
    if thin:
        parts.append(f"{thin} hold too few independent samples to average")
    return (f"Of {total} {what} assessed, " + ", ".join(parts)
            + "; see [Convergence](#convergence) before using any average from this run.")


def _summary_section(phase_context: PhaseContext, project_root: Path) -> str:
    """What this study was, before what the software is."""
    said: list[str] = []

    study = _study_in_one_paragraph(project_root)
    if study:
        said.append(study)
        supports = _what_the_run_supports(project_root)
        if supports:
            said.append(supports)

    if phase_context.is_analysis_from_existing_trajectory:
        # Kept as it was: this one is about the study rather than the
        # software, and a reader needs to know the trajectory was not produced
        # here before reading anything measured from it.
        said.append(
            "This report was generated from an existing trajectory. Setup and "
            "simulation were not run in this workflow.")
    elif not said:
        said.append(
            "This report summarizes the FastMDXplora outputs recorded for "
            "this workflow.")

    return "## Summary\n\n" + " ".join(said)


def _recorded_means(project_root: Path) -> dict[str, dict[str, Any]]:
    """Each analysis's mean record, by name, as the analysis wrote it."""
    means: dict[str, dict[str, Any]] = {}
    for path in sorted((project_root / "analysis").glob("*/options.json")):
        document = _load_json_safely(path)
        found = document.get("findings") if isinstance(document, dict) else None
        record = found.get("mean") if isinstance(found, dict) else None
        if isinstance(record, dict):
            means[path.parent.name] = record
    return means


def _conversations_kept(project_root: Path) -> int:
    """How many of the Agent's conversations about this study it keeps, of
    those that say anything: a conversation opened and left empty is not
    one in which the study was written."""
    from fastmdxplora.gui.agent_panel import CONVERSATIONS_SUBDIR

    kept = 0
    for path in sorted((project_root / CONVERSATIONS_SUBDIR).glob("conv-*.json")):
        document = _load_json_safely(path)
        if isinstance(document, dict) and document.get("entries"):
            kept += 1
    return kept


def _stopping_record_of(project_root: Path) -> dict[str, Any] | None:
    """The record of the rule this study ran under: its own, or, for one of a
    campaign's runs, the campaign's where it names this run."""
    own = _load_json_safely(project_root / "stopping.json")
    if isinstance(own, dict):
        return own
    if project_root.parent.name != "runs":
        return None
    campaign = _load_json_safely(project_root.parent.parent / "stopping.json")
    if isinstance(campaign, dict) and project_root.name in (campaign.get("runs") or []):
        return campaign
    return None


def methods_prose(project_root: Path, orchestrator: Any = None) -> str:
    """The methods paragraphs of a study, as its report gives them and the
    GUI shows them to copy.

    The paragraph a journal asks for, before the list of every setting. The
    list is what the software knows; this is what a reader needs, and they
    are not the same document. Written against the checklists published by
    JCIM (Soares et al. 2023) and Communications Biology (2023), from values
    already recorded -- nothing here is invented, and anything missing is
    named rather than filled in with what is usual.
    """
    from fastmdxplora.report.methods import methods_paragraphs
    from fastmdxplora.simulation.pipeline import setup_records_of
    from fastmdxplora.simulation.resume import extended_production

    prepared_in = setup_records_of(project_root)
    setup = (_load_json_safely(prepared_in / "setup_parameters.json") or {}
             if prepared_in is not None else {})
    sim = _load_json_safely(project_root / "simulation" / "simulation_parameters.json") or {}
    # The whole manifests, not just their `parameters`: the system is under
    # `input`, and the force field the run resolved to sits beside them. The
    # first version passed `parameters` alone and produced a methods section
    # saying the coordinates came from "the input structure".
    if orchestrator is None:
        from types import SimpleNamespace

        orchestrator = SimpleNamespace(output_dir=project_root, results=[])
    made_with, tools, recorded = _recorded_software(orchestrator)
    study_manifest = _load_json_safely(project_root / "manifest.json")
    return methods_paragraphs(
        project_root, setup, sim,
        system_name=(setup.get("input") or {}).get("system"),
        versions=tools, made_with=made_with, tools_recorded=recorded,
        extended=extended_production(project_root), means=_recorded_means(project_root),
        stopping=_stopping_record_of(project_root),
        written=study_manifest.get("agent") if isinstance(study_manifest, dict) else None,
        conversations=_conversations_kept(project_root),
    )


def _methods_section(project_root: Path, phase_context: PhaseContext,
                     orchestrator: Any = None) -> str:
    from fastmdxplora.simulation.pipeline import setup_records_of
    from fastmdxplora.simulation.resume import extended_production

    prepared_in = setup_records_of(project_root)
    setup = (_load_json_safely(prepared_in / "setup_parameters.json") or {}
             if prepared_in is not None else {})
    sim = _load_json_safely(project_root / "simulation" / "simulation_parameters.json") or {}
    setup_params = setup.get("parameters", {})
    sim_params = sim.get("parameters", {})
    extended = extended_production(project_root)

    lines = ["## Methods", ""]
    prose = methods_prose(project_root, orchestrator)
    if prose:
        lines.append(prose)
        lines.append("")
        lines.append("### Every setting used")
        lines.append("")
        lines.append(
            "The paragraphs above say what a methods section says; this is "
            "the complete record, for anyone repeating the run exactly."
        )
        lines.append("")

    lines.append("### System preparation")
    if setup_params:
        lines.append("")
        if prepared_in not in (None, project_root / "setup"):
            # Said, because the settings below are not this run's: it
            # simulated a system prepared elsewhere, and a reader repeating
            # it needs that system rather than these settings run again.
            lines.append(
                "This run simulated the system prepared in "
                f"`{_code_text(prepared_in)}`, named by "
                "`simulation.setup_from`, rather than preparing its own. "
                "That system was prepared using FastMDXplora's automated "
                "setup pipeline with the following parameters:"
            )
        else:
            lines.append(
                "The input system was prepared using FastMDXplora's automated "
                "setup pipeline with the following parameters:"
            )
        lines.append("")
        for k, v in setup_params.items():
            lines.append(f"- **{_md_text(k)}**: `{_code_text(v)}`")
    else:
        lines.append("")
        if phase_context.setup_present:
            lines.append("Setup ran in this workflow, but parameters were not recorded.")
        else:
            lines.append("Setup was not run in this workflow.")

    lines.append("")
    lines.append("### Molecular dynamics simulation")
    if sim_params:
        lines.append("")
        # Said of a run that ran none it was false: `duration_ns: 0`
        # equilibrates and stops.
        production = (sim.get("resolved") or {}).get("production_steps")
        lines.append(
            "No production was run; the simulation phase equilibrated with "
            "the following parameters:" if production == 0 else
            "Production MD was performed with the following simulation parameters:"
        )
        if extended and production != 0:
            # The list is the first piece's record; the study is its pieces.
            lines.append("")
            more = extended[1] - 1
            lines.append(
                f"These are the first piece's. {more} more piece"
                + ("s" if more != 1 else "")
                + f" extended the study, each from the checkpoint of the one "
                f"before, to {extended[0]:.3g} ns of production in all; each "
                "piece's own record is in its `segment-NNN/simulation` folder.")
        lines.append("")
        for k, v in _resolve_derived(dict(sim_params), record=sim).items():
            lines.append(f"- **{_md_text(k)}**: `{_code_text(v)}`")
    else:
        lines.append("")
        if phase_context.simulation_present:
            lines.append(
                "Simulation ran in this workflow, but parameters were not recorded."
            )
        elif phase_context.analysis_present:
            lines.append(
                "Simulation was not run in this workflow. Analysis was performed "
                "on externally provided or previously generated trajectory/topology "
                "files."
            )
        else:
            lines.append("Simulation was not run in this workflow.")

    return "\n".join(lines)


def _results_section(project_root: Path, report_dir: Path | None = None) -> str:
    report_dir = report_dir or project_root / "report"
    analysis_manifest = _load_json_safely(
        project_root / "analysis" / "analysis_manifest.json"
    ) or {}
    plan: list[str] = analysis_manifest.get("plan", [])
    results = analysis_manifest.get("results", {})

    lines = ["## Results", ""]
    if not plan:
        lines.append("No analyses were executed in this session.")
        return "\n".join(lines)

    n_frames = analysis_manifest.get("n_frames")
    n_residues = analysis_manifest.get("n_residues")
    n_protein = analysis_manifest.get("n_protein_residues")
    if n_frames is not None and n_protein is not None:
        # Ions are residues to MDTraj, and "not water" keeps them, so a
        # 20-residue peptide reads as 27. Say what the trajectory holds.
        others = int(n_residues) - int(n_protein) if n_residues is not None else 0
        tail = (f" and {others} other residue{'s' if others != 1 else ''} "
                f"(ions and any cofactors kept by the save selection)") if others else ""
        lines.append(
            f"Analysis was performed on a trajectory of {n_frames} frames: "
            f"{n_protein} protein residues{tail}.")
        lines.append("")
    elif n_frames is not None and n_residues is not None:
        lines.append(
            f"Analysis was performed on a trajectory of {n_frames} frames "
            f"and {n_residues} residues."
        )
    lines.append(f"Analyses performed: {', '.join(_md_text(a) for a in plan)}.")
    lines.append("")

    # The corrected averages come before the analyses they correct. A reader
    # who meets the RMSD heading first has already read the raw number by the
    # time they reach a section saying it was not a measurement.
    reweighted_record = load_reweighted(project_root)
    if reweighted_record:
        lines.append(reweighted_section(project_root, reweighted_record,
                                        report_dir=report_dir))
        lines.append("")

    summary_fig = report_dir / "analysis_summary.png"
    summary_manifest = report_dir / "analysis_summary_manifest.json"
    if summary_fig.is_file():
        lines.append("### Analysis Summary Figure")
        lines.append("")
        lines.append(f"![Analysis summary]({_link_from(report_dir, summary_fig)})")
        lines.append("")
        if summary_manifest.is_file():
            lines.append(
                "_Panel inclusion and skipped optional source figures are recorded "
                "in `analysis_summary_manifest.json`._"
            )
            lines.append("")

    region_fig = report_dir / "region_highlight_summary.png"
    region_manifest = report_dir / "region_highlight_manifest.json"
    if region_fig.is_file():
        lines.append("### Region Highlight Figure")
        lines.append("")
        lines.append(
            "User-configured residue regions are highlighted on the RMSF "
            "profile. These labels are user-provided annotations."
        )
        lines.append("")
        lines.append(f"![Region highlights]({_link_from(report_dir, region_fig)})")
        lines.append("")
        if region_manifest.is_file():
            lines.append(
                "_Generation details and any skipped optional structure panel "
                "are recorded in `region_highlight_manifest.json`._"
            )
            lines.append("")
            region_meta = _load_json_safely(region_manifest) or {}
            skipped = region_meta.get("skipped") or []
            for item in skipped:
                reason = item.get("reason")
                if reason:
                    lines.append(f"_Structure note: {_md_text(reason)}_")
                    lines.append("")
                    break

    for analysis in plan:
        # The reweighting pass is not an analysis of the trajectory and has
        # already been rendered as its own section above, with a table the
        # generic heading-and-figure treatment here could not produce.
        if analysis == "reweighted" and reweighted_record:
            continue
        # Pretty heading: uppercase short names, title-case longer ones
        heading = analysis.upper() if len(analysis) <= 4 else analysis.title()
        heading = _md_text(heading)
        lines.append(f"### {heading}")
        # What this analysis measures, in a sentence, before its parameters.
        # RMSF, secondary structure, dihedrals, thermodynamics and the
        # moments of inertia had a heading, a parameter list and a figure,
        # and nothing that said what the figure was of.
        try:
            from fastmdxplora.analysis.describe import explain_analysis

            about = explain_analysis(str(analysis)).get("summary") or ""
        except Exception:  # noqa: BLE001 - a missing description is no description
            about = ""
        if about:
            lines.append("")
            lines.append(about.strip())
        lines.append("")

        # Per-analysis result row from the analysis manifest
        result_meta = results.get(analysis, {})
        status = result_meta.get("status", "unknown")
        if status != "ok":
            lines.append(
                f"_This analysis did not complete successfully (status: "
                f"`{status}`)._"
            )
            if result_meta.get("message"):
                lines.append(f"Reason: {_md_text(result_meta['message'])}")
            lines.append("")
            continue

        # Where this analysis reports a per-frame quantity on a biased run,
        # its corrected value goes first: the section heading is where a
        # reader looking for that number actually goes.
        corrected = reweighted_line(reweighted_record, analysis)
        if corrected:
            lines.append(corrected)
            lines.append("")

        # Options come from each analysis's own options.json (more reliable
        # than the manifest because the per-analysis file records the
        # actual fully-resolved options after defaults are applied).
        opts_file = project_root / "analysis" / analysis / "options.json"
        per_analysis = _load_json_safely(opts_file) or {}
        opts = per_analysis.get("options", {})
        selection = per_analysis.get("selection")

        # A parameter left at its default reads as None, and "state_csv:
        # None" in the report says a file was not given, which is noise, not
        # a setting. Drop the empty ones; if nothing is left, drop the block.
        shown = [(k, v) for k, v in opts.items() if v is not None and v != ""]
        if selection or shown:
            lines.append("**Parameters:**")
            if selection:
                lines.append(f"- `selection`: `{_code_text(selection)}`")
            for k, v in shown:
                lines.append(f"- `{_code_text(k)}`: `{_code_text(v)}`")

        # What the analysis worked out, said in a sentence rather than
        # printed. The structures behind these run to pages of atom indices,
        # and a reader needs to know that the chemistry was resolved, not to
        # read the tuples.
        findings = per_analysis.get("findings") or {}
        notes = _findings_notes(findings)
        for note in notes:
            lines.append(f"- {note}")
        # A `for ... else` runs its else when the loop finishes without a
        # break, which is every time: the report listed an analysis's
        # parameters and then said it had run with the defaults.
        if not (opts or selection or notes):
            lines.append("_Ran with default options._")
        lines.append("")

        # Embed all figures in the analysis directory. Multi-method
        # analyses (cluster, dimred) emit several PNGs; sort them so the
        # report renders deterministically.
        figs_dir = project_root / "analysis" / analysis
        figures = sorted(figs_dir.glob("*.png")) if figs_dir.exists() else []
        if figures:
            for fig in figures:
                lines.append(f"![{_md_text(_caption_of(analysis, fig.stem))}]"
                             f"({_link_from(report_dir, fig)})")
                lines.append("")
        else:
            lines.append("_No figure was produced for this analysis._")
            lines.append("")

    return "\n".join(lines)



def _last_numeric_column(path: Path) -> list[float]:
    """The numbers in a data file, whatever shape the analysis wrote.

    An analysis returning an array writes bare numbers; one returning a table
    writes a header and several columns, of which the measurement is the last
    -- the earlier ones being the frame or residue it belongs to.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    values: list[float] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.replace(",", " ").split()
        try:
            values.append(float(fields[-1]))
        except (ValueError, IndexError):
            # A header line, which says the file has one and that the
            # numbers start below it.
            continue
    return values


def _assess_this_run(project_root: Path) -> dict[str, Any] | None:
    """Everything this run says about whether it can be interpreted.

    Gathered in one place because two sections need it: the summary states in
    one line how much of the run can be interpreted, and the convergence
    section reports it per observable. Two gatherings would be two answers
    the moment one of them learned to read a new file.
    """
    import csv

    from fastmdxplora.report.convergence import assess_run

    series: dict[str, Any] = {}

    energy_csv = project_root / "simulation" / "energy.csv"
    if energy_csv.is_file():
        wanted = {
            "Potential Energy (kJ/mole)": "potential_energy",
            "Temperature (K)": "temperature",
            "Density (g/mL)": "density",
        }
        collected: dict[str, list[float]] = {name: [] for name in wanted.values()}
        try:
            with energy_csv.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    for column, name in wanted.items():
                        value = row.get(column) or row.get(f"#\"{column}\"")
                        if value:
                            try:
                                collected[name].append(float(value))
                            except ValueError:
                                pass
        except OSError:
            collected = {}
        series.update({k: v for k, v in collected.items() if v})

    # The structural measures, from the analyses that computed them. An
    # analysis returning an array writes plain numbers and one returning a
    # frame writes a header, so both shapes are read rather than one assumed.
    for name in ("rmsd", "rg", "sasa"):
        data = project_root / "analysis" / name / f"{name}.dat"
        if not data.is_file():
            continue
        values = _last_numeric_column(data)
        if values:
            series[name] = values

    if not series:
        return None

    setup = _setup_record(project_root)
    sim = _load_json_safely(
        project_root / "simulation" / "simulation_parameters.json") or {}
    return assess_run(
        series,
        duration_ns=sim.get("duration_ns_actual"),
        n_atoms=setup.get("n_atoms_solvated"),
        target_temperature_K=(sim.get("parameters") or {}).get("temperature_K"),
    )


def _convergence_section(project_root: Path) -> str:
    """How much independent information the trajectory holds.

    Placed after the results because it is about them: every mean and error
    bar above rests on how many independent observations the run contains,
    and that is usually far fewer than the frame count suggests.
    """
    assessed = _assess_this_run(project_root)
    if assessed is None:
        return ""


    lines = ["## Convergence", ""]
    lines.append(
        "A frame is not an observation. Consecutive frames of a trajectory "
        "are nearly the same structure, so the number of independent "
        "observations is set by how quickly each observable forgets where it "
        "was, not by how often frames were written. The uncertainties below "
        "count the former."
    )
    lines.append("")
    lines.append(
        "| observable | frames | discarded | independent | mean | uncertainty "
        "| equilibrated |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for record in assessed["observables"].values():
        error = record["standard_error"]
        lines.append(
            f"| {record['observable']} | {record['frames']:,} | "
            f"{record.get('discard', 0):,} | "
            f"{record['effective_samples']:.1f} | {record['mean']:.4g} | "
            + (f"{error:.3g}" if error is not None else "not enough to say")
            + " | "
            + {True: "yes", False: "no", None: "too short to say"}[
                record.get("equilibrated", record.get("settled"))]
            + " |"
        )
    lines.append("")

    checks = assessed.get("checks") or []
    if checks:
        lines.append("### The checks this run was held to")
        lines.append("")
        lines.append("| check | result | on what |")
        lines.append("|---|---|---|")
        for check in checks:
            result = {True: "passed", False: "**failed**", None: "not judged"}[check["passed"]]
            lines.append(f"| {check['said'][0].upper()}{check['said'][1:]} | {result} "
                         f"| {check['detail']} |")
        lines.append("")

    asked = _what_more_sampling_needs(project_root)
    if assessed["findings"]:
        lines.append("### What this run cannot support")
        lines.append("")
        for finding in assessed["findings"]:
            lines.append(f"- {finding}")
        lines.append("")
        if asked:
            lines += [asked, ""]
    elif asked:
        lines += [asked, ""]
    else:
        lines.append(
            "Every observable equilibrated and carries enough independent observation "
            "to average. That is a statement about sampling, not about whether "
            "the force field describes the system."
        )
        lines.append("")
    return "\n".join(lines)


def _stopping_section(project_root: Path) -> str:
    """How long a study run until it knew ran, and why it stopped there."""
    try:
        from fastmdxplora.simulation.stopping import stopping_section

        return "\n".join(stopping_section(project_root)).rstrip()
    except Exception:  # noqa: BLE001 - a report section must never fail a report
        return ""


def _what_more_sampling_needs(project_root: Path) -> str:
    """The production the withheld means ask for, what it takes at this
    run's speed, and the config that runs it. The analyses said "the remedy
    is a longer run" and left the length to guess."""
    try:
        import yaml

        from fastmdxplora.simulation.sampling_ask import sampling_asked_for

        ask = sampling_asked_for(project_root)
    except Exception:  # noqa: BLE001 - a report section must never fail a report
        return ""
    if ask is None:
        return ""
    text = ask.as_text()
    config = yaml.safe_dump(ask.config(project_root), sort_keys=False,
                            default_flow_style=False).strip()
    return (f"**What would support it.** {text[0].upper()}{text[1:]} This extends "
            f"the study in place, joins the segments and reruns the analyses:\n\n"
            f"```yaml\n{config}\n```")


def _findings_notes(findings: dict[str, Any]) -> list[str]:
    """What an analysis worked out, in sentences.

    The findings themselves are structures -- lists of binding modes, matrices
    of transitions -- kept in ``options.json`` for anyone who wants them. What
    belongs in a document is what they amount to.
    """
    notes: list[str] = []

    # What the analysis measured. Recorded for every per-frame analysis and
    # shown nowhere: a results section carried a figure and the settings that
    # produced it, and no number.
    measured = findings.get("mean")
    if isinstance(measured, dict) and measured.get("mean") is not None:
        from fastmdxplora.statistics import with_its_error

        error = measured.get("standard_error")
        error = error if isinstance(error, (int, float)) and error > 0 else None
        value = with_its_error(measured["mean"], error)
        if measured.get("unit"):
            value += f" {measured['unit']}"
        independent = measured.get("effective_samples")
        said = f"Mean over the equilibrated part of the run: {value}"
        if isinstance(independent, (int, float)):
            said += f", from {independent:.0f} independent samples"
        discarded = measured.get("discard")
        if isinstance(discarded, int) and discarded:
            said += f" after discarding {discarded:,} frames"
        notes.append(said + ".")
    if isinstance(measured, dict) and measured.get("not_a_measurement"):
        notes.append(str(measured["not_a_measurement"]))

    chemistry = findings.get("ligand_chemistry")
    if isinstance(chemistry, dict):
        source = chemistry.get("source")
        where = {
            "run": "resolved during setup, at the pH simulated",
            "supplied": "read from the file you supplied",
            "ccd": "taken from the Chemical Component Dictionary",
            "perceived": "inferred from the coordinates, which is a guess",
        }.get(source, source)
        notes.append(f"Ligand chemistry: {where}.")
        if chemistry.get("charge_was_ambiguous"):
            notes.append(
                "The ligand's charge was ambiguous, so the analyses that "
                "depend on it were not run."
            )

    refused = findings.get("not_measured")
    if isinstance(refused, dict) and refused:
        notes.append(
            "Not computed: " + ", ".join(sorted(refused))
            + " — see `options.json` for why."
        )

    modes = findings.get("binding_modes")
    if isinstance(modes, list) and modes:
        notes.append(
            f"{len(modes)} binding mode(s) seen; the most common held "
            f"{modes[0].get('fraction', 0):.0%} of frames."
        )

    transitions = findings.get("mode_transitions")
    if isinstance(transitions, dict):
        seen = transitions.get("observed_transitions")
        if transitions.get("supported"):
            notes.append(
                f"{seen} changes of binding mode were seen, enough to give "
                "transition probabilities (in `options.json`)."
            )
        elif seen is not None:
            notes.append(
                f"{seen} change(s) of binding mode were seen, too few for a "
                "rate; the counts are in `options.json`."
            )
    return notes


def _citation_section() -> str:
    from fastmdxplora import __bibtex__, __citation__

    return "\n".join(
        [
            "## Citation",
            "",
            "If you use FastMDXplora in your work, please cite:",
            "",
            f"> {__citation__}",
            "",
            "BibTeX:",
            "",
            "```bibtex",
            __bibtex__,
            "```",
        ]
    )


def _reproducibility_section(
    orchestrator: "FastMDXplora",
    phase_context: PhaseContext,
) -> str:
    from fastmdxplora import __version__

    lines = ["## Reproducibility", ""]
    from fastmdxplora.provenance import described, source_provenance

    # By phase where they differ: a study simulated under one release, or on
    # another machine, and reported under this one printed this one alone.
    producers = _phase_producers(orchestrator)
    made_by = {phase: (str(produced.get("version") or "")
                       + (" or earlier" if produced.get("inferred") else ""))
               for phase, produced in producers.items()}
    ran_on = {phase: str((produced.get("environment") or {}).get("python") or "")
              for phase, produced in producers.items()
              if isinstance(produced.get("environment"), dict)}
    lines.append(_by_phase("FastMDXplora version", made_by, str(__version__)))
    # A version string is written at install time, so from a source checkout
    # it can name a release the run could not have been made with. The commit
    # says what the version cannot, and the dirty flag says when the commit
    # does not describe the code either.
    from_source = described(source_provenance())
    if from_source:
        lines.append(f"- **Source commit**: `{from_source}`")
    lines.append(_by_phase("Python", ran_on, sys.version.split()[0]))
    lines.append(f"- **Platform**: `{platform.platform()}`")
    lines.append(f"- **System input**: `{_code_text(orchestrator.system)}`")
    lines.append(f"- **Output directory**: `{_code_text(orchestrator.output_dir)}`")
    lines.append("")
    manifests: list[str] = []
    if phase_context.setup_present:
        manifests.append("`setup/setup_parameters.json`")
    if phase_context.simulation_present:
        manifests.append("`simulation/simulation_parameters.json`")
    if phase_context.analysis_present:
        manifests.append("`analysis/analysis_manifest.json`")
    if manifests:
        lines.append(
            "Per-phase parameter manifests for phases in this workflow are "
            f"preserved at {', '.join(manifests)}. The complete session manifest "
            "is at `manifest.json` at the project root."
        )

    if phase_context.setup_present:
        lines.append("")
        lines.append(_what_a_rerun_repeats(Path(orchestrator.output_dir)))
    else:
        lines.append(
            "The complete session manifest is at `manifest.json` at the project root."
        )
    return "\n".join(lines)


def _what_a_rerun_repeats(root: Path) -> str:
    """What running the study's configuration again gives, from its records.

    Setup places hydrogens and ions at random and records the seed it used
    (`setup.random_seed`, drawn where none was given), and minimises on one
    CPU thread, so the same structure and settings prepare the same atoms
    again; a bilayer is packed by OpenMM with a random stream of its own.
    This said once that solvation could not be seeded at all, which was the
    unseeded hydrogens and ions, and stayed wrong after they were seeded.
    The dynamics repeat their start only where `simulation.random_seed` was
    given, and step for step only as far as the platform's arithmetic does.
    """
    import yaml

    try:
        record = json.loads((root / "setup" / "setup_parameters.json")
                            .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    try:
        config = yaml.safe_load((root / "resolved_config.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        config = {}
    seeded = (record.get("random_seed") or {}) if isinstance(record, dict) else {}
    setup = config.get("setup") if isinstance(config.get("setup"), dict) else {}
    simulation = (config.get("simulation")
                  if isinstance(config.get("simulation"), dict) else {})
    bilayer = bool(setup.get("membrane")) or bool(
        isinstance(record, dict) and record.get("membrane"))

    if isinstance(seeded, dict) and seeded.get("seed") is not None:
        text = (
            "**What rerunning the configuration repeats.** Running "
            "`resolved_config.yml` again prepares the same system: it records "
            f"the random seed setup placed hydrogens and ions with "
            f"({int(seeded['seed'])}{', drawn for this study' if seeded.get('drawn') else ''}), "
            "and setup minimises on one CPU thread, so the same structure and "
            "settings give the same atoms in the same places"
            + (", except the bilayer, which OpenMM packs with a random stream of "
               "its own" if bilayer else "")
            + ".")
    else:
        text = (
            "**What rerunning the configuration does and does not repeat.** This "
            "study was prepared without a recorded random seed, so running "
            "`resolved_config.yml` again places hydrogens, ions and water afresh "
            "and can give a slightly different atom count.")
    seed = simulation.get("random_seed")
    if seed is not None and not isinstance(seed, list):
        text += (
            f" Its dynamics start from `simulation.random_seed` {seed}, so a rerun "
            "starts from the same velocities. Whether the trajectory then repeats "
            "step for step depends on the platform: OpenMM's GPU platforms can "
            "differ in the last bit of a force from one run to the next unless "
            "asked for deterministic forces, and dynamics this chaotic grow such a "
            "difference, so it is the statistics rather than the frames that a "
            "rerun is expected to reproduce.")
    else:
        text += (
            " No `simulation.random_seed` was given, so a rerun starts its "
            "dynamics from velocities drawn afresh: a new trajectory of the same "
            "system.")
    return text + (" To simulate this very system without preparing it again, "
                   "point `simulation.setup_from` at its `setup/` directory.")


def _by_phase(label: str, recorded: dict[str, str], here: str) -> str:
    """One line of the Reproducibility section, split by phase if it must be."""
    values = {value for value in recorded.values() if value}
    if not values or values == {here}:
        return f"- **{label}**: `{here}`"
    grouped: dict[str, list[str]] = {}
    for phase, value in recorded.items():
        if value:
            grouped.setdefault(value, []).append(phase)
    grouped.setdefault(here, []).append("this report")
    return f"- **{label}**: " + "; ".join(
        f"`{value}` ({', '.join(phases)})" for value, phases in grouped.items())


def build_document(
    *,
    orchestrator: "FastMDXplora",
    output_dir: Path,
    title: str,
    author: str | None,
    include_methods: bool,
    include_reproducibility: bool,
) -> list[str]:
    """Render the Markdown study report.

    Returns
    -------
    list of str
        Artifact paths relative to ``output_dir``.
    """
    project_root = orchestrator.output_dir
    phase_context = load_phase_context(project_root)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    sections: list[str] = []
    header = [f"# {_md_text(title, limit=200)}", ""]
    if author:
        header.append(f"_Author: {_md_text(author, limit=200)}_  ")
    header.append(f"_Generated: {now} (UTC)_  ")
    header.append("_Software: FastMDXplora_  ")
    header.append("_Dashboard: [dashboard.html](dashboard.html)_")
    sections.append("\n".join(header))

    sections.append(_summary_section(phase_context, project_root))

    if include_methods:
        sections.append(_methods_section(project_root, phase_context, orchestrator))

    sections.append(_results_section(project_root, output_dir))
    convergence = _convergence_section(project_root)
    if convergence:
        sections.append(convergence)
    stopping = _stopping_section(project_root)
    if stopping:
        sections.append(stopping)

    sections.append(
        "## Discussion\n\n"
        "_Everything above is a record: what was built, what was run, what "
        "was computed from it, and how much of it the run supports. What none "
        "of it says is what the system was doing, or whether that answers the "
        "question the study was for. That judgement is yours, and this "
        "section is where it goes._\n\n"
        "_Software can report that a mean rests on eight independent samples. "
        "It cannot tell you whether eight is enough for the claim you intend "
        "to make._"
    )

    sections.append(_citation_section())

    if include_reproducibility:
        sections.append(_reproducibility_section(orchestrator, phase_context))

    doc = "\n\n".join(sections) + "\n"
    md_path = output_dir / "report.md"
    md_path.write_text(doc, encoding="utf-8")

    return ["report.md"]


def _software_versions() -> dict[str, str]:
    """What did the work, and at what version.

    A methods section naming a tool without its version is naming a moving
    target: the defaults change, and a reader repeating the run gets different
    numbers with no way to know why.
    """
    versions: dict[str, str] = {}
    try:
        from fastmdxplora import __version__

        versions["FastMDXplora"] = str(__version__)
    except Exception:  # noqa: BLE001
        pass
    # The engine and the structure tools first; then the numerical and
    # plotting libraries every phase stands on. The list named five and
    # left out NumPy, SciPy, matplotlib, pandas and scikit-learn, which
    # do the arithmetic, the fitting, the figures, the tables and the
    # clustering respectively. A version of any of them can change a
    # number in this report.
    for label, module in (("OpenMM", "openmm"), ("MDTraj", "mdtraj"),
                          ("OpenFF Toolkit", "openff.toolkit"),
                          ("OpenMM force fields", "openmmforcefields"),
                          ("PDBFixer", "pdbfixer"), ("RDKit", "rdkit"),
                          ("NumPy", "numpy"), ("SciPy", "scipy"),
                          ("matplotlib", "matplotlib"), ("pandas", "pandas"),
                          ("scikit-learn", "sklearn")):
        try:
            import importlib

            found = importlib.import_module(module)
            version = getattr(found, "__version__", None)
            if version:
                versions[label] = str(version)
        except Exception:  # noqa: BLE001 - a tool not installed did no work
            continue
    return versions


#: How a Methods section names each package a phase records, in the order a
#: study reaches for them.
RECORDED_TOOLS: tuple[tuple[str, str], ...] = (
    ("openmm", "OpenMM"), ("pdbfixer", "PDBFixer"), ("propka", "PROPKA"),
    ("openff.toolkit", "OpenFF Toolkit"),
    ("openmmforcefields", "OpenMM force fields"), ("ambertools", "AmberTools"),
    ("rdkit", "RDKit"), ("openmmplumed", "OpenMM-PLUMED"), ("plumed", "PLUMED"),
    ("mdtraj", "MDTraj"), ("numpy", "NumPy"), ("scipy", "SciPy"),
    ("sklearn", "scikit-learn"), ("pandas", "pandas"),
    ("matplotlib", "matplotlib"),
)

#: The phases whose software a Methods section names.
WORK_PHASES = ("setup", "simulation", "analysis")


def _producers_in(manifest: Path) -> dict[str, dict[str, Any]]:
    """Each finished phase a manifest records, with what produced it."""
    found: dict[str, dict[str, Any]] = {}
    record = _load_json_safely(manifest) or {}
    phases = record.get("phases")
    for phase in phases if isinstance(phases, list) else []:
        if (isinstance(phase, dict) and phase.get("status") == "ok"
                and isinstance(phase.get("produced_by"), dict)):
            found[str(phase.get("name"))] = phase["produced_by"]
    return found


def _phase_producers(orchestrator: Any) -> dict[str, dict[str, Any]]:
    """What produced each phase whose outputs this report describes.

    The manifest on disk holds the phases of earlier sessions; this
    session's are on the orchestrator, since the manifest is written after
    the last phase. A system prepared by another study (`setup_from`) was
    produced by what that study's manifest says.
    """
    from fastmdxplora.simulation.pipeline import setup_records_of

    root = Path(orchestrator.output_dir)
    producers = _producers_in(root / "manifest.json")
    for result in getattr(orchestrator, "results", None) or []:
        if getattr(result, "status", None) == "ok" and getattr(result, "produced_by", None):
            producers[str(result.name)] = dict(result.produced_by)
    if "setup" not in producers:
        prepared_in = setup_records_of(root)
        if prepared_in is not None and prepared_in != root / "setup":
            elsewhere = _producers_in(prepared_in.parent / "manifest.json")
            if "setup" in elsewhere:
                producers["setup"] = elsewhere["setup"]
    return {phase: producers[phase] for phase in WORK_PHASES if phase in producers}


def _and(items: list[str]) -> str:
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _recorded_software(orchestrator: Any) -> tuple[list[tuple[str, list[str]]], dict[str, str], bool]:
    """What produced each phase, as a Methods section names it.

    Returns the FastMDXplora version with the phases it produced, the
    libraries by name with their versions, and whether those were recorded
    by the run. A report made on another machine, or by a later release,
    named its own installation: a study simulated under 2.5.4 and analysed
    again under 2.5.8 was said to have been set up and simulated with 2.5.8
    and whatever OpenMM the analysing machine had.
    """
    producers = _phase_producers(orchestrator)
    made_with: list[tuple[str, list[str]]] = []
    for phase, produced in producers.items():
        version = produced.get("version")
        if not version:
            continue
        shown = f"{version} or earlier" if produced.get("inferred") else str(version)
        for entry in made_with:
            if entry[0] == shown:
                entry[1].append(phase)
                break
        else:
            made_with.append((shown, [phase]))

    environments = {phase: produced["environment"] for phase, produced in producers.items()
                    if isinstance(produced.get("environment"), dict)}
    if not environments:
        return made_with, _software_versions(), False
    tools: dict[str, str] = {}
    for key, label in RECORDED_TOOLS:
        by_version: dict[str, list[str]] = {}
        for phase, environment in environments.items():
            version = environment.get(key)
            if version:
                by_version.setdefault(str(version), []).append(phase)
        if not by_version:
            continue
        named = {("" if version == "loaded" else version): phases
                 for version, phases in by_version.items()}
        if len(named) == 1:
            version = next(iter(named))
            tools[label] = version or "(version not recorded)"
        else:
            tools[label] = " and ".join(
                f"{version or 'unrecorded'} ({_and(phases)})"
                for version, phases in named.items())
    return made_with, tools, True


def _resolve_derived(params: dict[str, Any], *,
                     record: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fill in what the given settings determine.

    A run given `duration_ns` has a production step count; a run given
    `nvt_steps` has an NVT duration; a pressure in bar is a pressure in
    atm. The dump printed None for whichever form was not typed, and a
    reader of "every setting used" was left to do the arithmetic. These
    are the record of what the run used, so they are filled from the
    timestep and each other. Values already set are left as they are.

    ``record`` is the whole simulation record, which says what the
    parameters cannot: the ensemble production ran in, and the pressure
    the barostat held.
    """
    out = dict(params)
    dt_fs = out.get("timestep_fs")
    try:
        dt_ns = float(dt_fs) * 1e-6 if dt_fs is not None else None
    except (TypeError, ValueError):
        dt_ns = None

    def steps_from(ns: Any) -> int | None:
        try:
            return int(round(float(ns) / dt_ns)) if dt_ns else None
        except (TypeError, ValueError):
            return None

    def ns_from(steps: Any) -> float | None:
        try:
            return round(int(steps) * dt_ns, 6) if dt_ns else None
        except (TypeError, ValueError):
            return None

    for ns_key, steps_key in (("duration_ns", "production_steps"),
                              ("nvt_duration_ns", "nvt_steps"),
                              ("npt_duration_ns", "npt_steps")):
        if out.get(steps_key) is None and out.get(ns_key) is not None:
            out[steps_key] = steps_from(out[ns_key])
        elif out.get(ns_key) is None and out.get(steps_key) is not None:
            out[ns_key] = ns_from(out[steps_key])

    # The ensemble production ran in, as the run recorded it. Asked of
    # `npt_steps` here, a study leaving the stage to its default -- unset in
    # the parameters, a positive stage in the runner -- would be listed as
    # NVT when it ran at constant pressure.
    from fastmdxplora.simulation.ensembles import NPT, recorded_ensemble

    record = dict(record or {})
    record.setdefault("parameters", params)
    out["ensemble"] = recorded_ensemble(record)
    ran = record.get("resolved") if isinstance(record.get("resolved"), dict) else {}

    bar, atm = out.get("pressure_bar"), out.get("pressure_atm")
    stage = ran.get("npt_steps", out.get("npt_steps"))
    barostat = out["ensemble"] == NPT or stage is None or bool(stage)
    if bar is None and atm is None:
        # What the barostat ran at, as the runner recorded it; one bar
        # where the record predates that. Where nothing held a pressure --
        # no NPT stage and constant-volume production -- none is filled in,
        # because one listed here would read as production's.
        if barostat:
            used = (record.get("pressure_bar_used") or ran.get("pressure_bar")
                    or 1.0)
            out["pressure_bar"] = used
            out["pressure_atm"] = round(float(used) / 1.01325, 5)
    elif bar is None:
        try:
            out["pressure_bar"] = round(float(atm) * 1.01325, 5)
        except (TypeError, ValueError):
            pass
    elif atm is None:
        try:
            out["pressure_atm"] = round(float(bar) / 1.01325, 5)
        except (TypeError, ValueError):
            pass
    return out
