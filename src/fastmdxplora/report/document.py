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


def _load_json_safely(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        logger.warning("Could not parse JSON manifest at %s", path)
        return None


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
    def flattened(path: Path) -> dict:
        """A manifest with its nested `parameters` lifted to the top.

        Values live at either level depending on whether they were requested
        or measured, and reading only the top missed the duration on manifests
        that record it under `parameters` -- so the summary fell back to the
        generic sentence for a run that had one.
        """
        record = _load_json_safely(path) or {}
        merged = dict(record)
        nested = record.get("parameters")
        if isinstance(nested, dict):
            for key, value in nested.items():
                if merged.get(key) is None:
                    merged[key] = value
        return merged

    setup = flattened(project_root / "setup" / "setup_parameters.json")
    sim = flattened(project_root / "simulation" / "simulation_parameters.json")

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
                "the system rather than this run; see Convergence.")

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
            + "; see Convergence below before using any average from this run.")


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


def _methods_section(project_root: Path, phase_context: PhaseContext) -> str:
    setup = _load_json_safely(project_root / "setup" / "setup_parameters.json") or {}
    sim = _load_json_safely(project_root / "simulation" / "simulation_parameters.json") or {}
    setup_params = setup.get("parameters", {})
    sim_params = sim.get("parameters", {})

    lines = ["## Methods", ""]

    # The paragraph a journal asks for, before the list of every setting. The
    # list is what the software knows; this is what a reader needs, and they
    # are not the same document. Written against the checklists published by
    # JCIM (Soares et al. 2023) and Communications Biology (2023), from values
    # already recorded -- nothing here is invented, and anything missing is
    # named rather than filled in with what is usual.
    from fastmdxplora.report.methods import methods_paragraphs

    # The whole manifests, not just their `parameters`: the system is under
    # `input`, and the force field the run resolved to sits beside them. The
    # first version passed `parameters` alone and produced a methods section
    # saying the coordinates came from "the input structure".
    prose = methods_paragraphs(
        project_root, setup, sim,
        system_name=(setup.get("input") or {}).get("system"),
        versions=_software_versions(),
    )
    if prose:
        lines.append(prose)
        lines.append("")
        lines.append("### Every setting used")
        lines.append("")
        lines.append(
            "The paragraph above states what a methods section must; this is "
            "the complete record, for anyone repeating the run exactly."
        )
        lines.append("")

    lines.append("### System preparation")
    if setup_params:
        lines.append("")
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
        lines.append(
            "Production MD was performed with the following simulation parameters:"
        )
        lines.append("")
        for k, v in sim_params.items():
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


def _results_section(project_root: Path) -> str:
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
    if n_frames is not None and n_residues is not None:
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
        lines.append(reweighted_section(project_root, reweighted_record))
        lines.append("")

    summary_fig = project_root / "report" / "analysis_summary.png"
    summary_manifest = project_root / "report" / "analysis_summary_manifest.json"
    if summary_fig.is_file():
        lines.append("### Analysis Summary Figure")
        lines.append("")
        lines.append("![Analysis summary](analysis_summary.png)")
        lines.append("")
        if summary_manifest.is_file():
            lines.append(
                "_Panel inclusion and skipped optional source figures are recorded "
                "in `analysis_summary_manifest.json`._"
            )
            lines.append("")

    region_fig = project_root / "report" / "region_highlight_summary.png"
    region_manifest = project_root / "report" / "region_highlight_manifest.json"
    if region_fig.is_file():
        lines.append("### Region Highlight Figure")
        lines.append("")
        lines.append(
            "User-configured residue regions are highlighted on the RMSF "
            "profile. These labels are user-provided annotations."
        )
        lines.append("")
        lines.append("![Region highlights](region_highlight_summary.png)")
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

        if opts or selection:
            lines.append("**Parameters:**")
            if selection:
                lines.append(f"- `selection`: `{_code_text(selection)}`")
            for k, v in opts.items():
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
                # Markdown/HTML image links require forward slashes on every
                # OS; str(WindowsPath) would emit backslashes and break them.
                rel = fig.relative_to(project_root).as_posix()
                caption = _md_text(f"{analysis} — {fig.stem}")
                lines.append(f"![{caption}]({_link_target(rel)})")
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
            # A header line, which tells us the file has one and that the
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

    setup = _load_json_safely(project_root / "setup" / "setup_parameters.json") or {}
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
        "observations is set by how quickly each measure forgets where it "
        "was, not by how often frames were written. The uncertainties below "
        "count the former."
    )
    lines.append("")
    lines.append(
        "| measure | frames | discarded | independent | mean | uncertainty "
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

    if assessed["findings"]:
        lines.append("### What this run cannot support")
        lines.append("")
        for finding in assessed["findings"]:
            lines.append(f"- {finding}")
        lines.append("")
    else:
        lines.append(
            "Every measure equilibrated and carries enough independent observation "
            "to average. That is a statement about sampling, not about whether "
            "the force field describes the system."
        )
        lines.append("")
    return "\n".join(lines)


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
        error = measured.get("standard_error")
        value = (f"{measured['mean']:.4g} ± {error:.3g}"
                 if isinstance(error, (int, float)) and error == error
                 else f"{measured['mean']:.4g}")
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
                "The ligand's charge was ambiguous, so the measures that are "
                "claims about charge were not made."
            )

    refused = findings.get("not_measured")
    if isinstance(refused, dict) and refused:
        notes.append(
            "Not measured: " + ", ".join(sorted(refused))
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

    lines.append(f"- **FastMDXplora version**: `{__version__}`")
    # A version string is written at install time, so from a source checkout
    # it can name a release the run could not have been made with. The commit
    # says what the version cannot, and the dirty flag says when the commit
    # does not describe the code either.
    from_source = described(source_provenance())
    if from_source:
        lines.append(f"- **Source commit**: `{from_source}`")
    lines.append(f"- **Python**: `{sys.version.split()[0]}`")
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
        lines.append(
            "**What rerunning the configuration does and does not reproduce.** "
            "Running `resolved_config.yml` again gives an equivalent study and "
            "not an identical one: solvation places water by a procedure that "
            "cannot be seeded, so the same configuration produces a system "
            "with a slightly different atom count each time, and a fixed "
            "`random_seed` fixes the dynamics rather than the solvent. To "
            "repeat this study exactly, simulate from the system it prepared "
            "by pointing `simulation.prepared_from` at its `setup/` "
            "directory."
        )
    else:
        lines.append(
            "The complete session manifest is at `manifest.json` at the project root."
        )
    return "\n".join(lines)


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
    header.append("_Tool: FastMDXplora_  ")
    header.append("_Dashboard: [dashboard.html](dashboard.html)_")
    sections.append("\n".join(header))

    sections.append(_summary_section(phase_context, project_root))

    if include_methods:
        sections.append(_methods_section(project_root, phase_context))

    sections.append(_results_section(project_root))
    convergence = _convergence_section(project_root)
    if convergence:
        sections.append(convergence)

    sections.append(
        "## Discussion\n\n"
        "_Everything above is measurement: what was built, what was run, what "
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
    for label, module in (("OpenMM", "openmm"), ("MDTraj", "mdtraj"),
                          ("OpenFF Toolkit", "openff.toolkit"),
                          ("PDBFixer", "pdbfixer"), ("RDKit", "rdkit")):
        try:
            import importlib

            found = importlib.import_module(module)
            version = getattr(found, "__version__", None)
            if version:
                versions[label] = str(version)
        except Exception:  # noqa: BLE001 - a tool not installed did no work
            continue
    return versions
