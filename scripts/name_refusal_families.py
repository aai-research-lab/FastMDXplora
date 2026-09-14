"""Name refusals by the family they belong to, not one at a time.

The per-site tool is right when a rule applies once. Across a package it
is the wrong shape: `analysis/` alone holds eleven separate raise sites
saying a selection matched no atoms, in eleven modules, and writing
eleven rules to say the same thing invites ten of them to drift.

So this matches on the phrasing the family shares. A pattern claims a
site only if exactly one pattern claims it; a site two patterns want is
reported and skipped, because an ambiguous classification is worse than
an absent one -- the first is wrong and looks decided.

Bare `ValueError` becomes `StudyError`, which subclasses it, so every
`except ValueError` keeps working. Other builtins are left alone: a
`RuntimeError` cannot become a `ValueError` without changing what catches
it, and that is a decision per site rather than a sweep.
"""
from __future__ import annotations

import ast
import pathlib
import re

#: (scope, pattern, code). `scope` is the subpackage directory the pattern
#: was written against, or "" for anywhere. Scoping is not tidiness: the
#: phrasing for a selection that matched nothing is identical in `analysis`
#: and `simulation`, and the right code differs, so an unscoped pattern
#: claims sites in the wrong phase and the ambiguity guard then throws away
#: both. Order is irrelevant -- a site matched by
#: more than one pattern is skipped rather than resolved by precedence,
#: so adding a pattern can only ever reduce coverage, never silently
#: reclassify what an earlier one had.
FAMILIES: list[tuple[str, str, str]] = [
    # A selection that found nothing. The single most repeated refusal in
    # the package, and the one a caller most needs to distinguish from a
    # crash: the study is fine, the expression does not fit this system.
    ("analysis", r"matched zero atoms|matched no atoms|No atoms match(ed)? |"
     r"matched no atoms, so", "analysis.selection.empty"),

    # A selection that found the wrong number.
    ("analysis", r"matched \{len\(align_ids\)\}|An angle is three atoms|"
     r"A torsion is four atoms", "analysis.selection.arity"),

    # A frame index past the end.
    ("analysis", r"is out of range for trajectory|Invalid frame slice",
     "analysis.option.out_of_range"),

    # A value outside a declared set.
    ("analysis", r"Unknown clustering method|Unknown clustering features|"
     r"Unknown dihedral\(s\)|Unknown dimred method|Unknown interaction type|"
     r"Unknown analysis scope|HBonds method must be|SASA mode must be|"
     r"scheme must be one of|xunit must be one of|"
     r"figure_colours does not accept", "analysis.option.not_permitted"),

    # A required companion setting, absent.
    ("analysis", r"requires `ligand_resname`|requires `ligand_resname`|"
     r"requires \`ligand_resname\`|scope='ligand' requires",
     "analysis.option.missing_companion"),

    # A setting with no meaning where it was given.
    ("analysis", r"works out its own atoms|apply to \"\s*\"baker_hubbard only|"
     r"apply to |has no setting called|Specify either `include` or `exclude`",
     "analysis.option.inapplicable"),

    # What the analysis reads was never produced.
    ("analysis", r"beside this run|holds no rows|holds no usable rows|holds no surface|"
     r"holds no pull to draw|record holds no", "analysis.data.absent"),

    # Not enough to compute with.
    ("analysis", r"at least two frames|needs at least n_clusters|"
     r"Only \{len\(rows\)\} residues|No residue pairs satisfy|"
     r"No backbone dihedrals could be computed|"
     r"No backbone amide|No alpha carbons in the selection|"
     r"no residue in this|No water was found",
     "analysis.sampling.too_few_frames"),

    # Nothing varies, so there is nothing to decompose.
    ("analysis", r"nothing to decompose", "analysis.sampling.no_variance"),

    # The machine, not the study.
    ("analysis", r"is required for dendrogram|umap-learn package is not|"
     r"requires pdbfixer and openmm|requires OpenMM",
     "environment.backend.missing"),

    # -- simulation: collective variables and biasing ---------------------
    ("simulation", r"Unknown collective variable|Unknown nonbonded_method|"
     r"Unknown constraints option|unknown policy", "simulation.cv.unknown"),

    ("simulation", r"does not take a selection|does not say|has \"\s*\"nothing to na",
     "simulation.cv.inapplicable_setting"),

    ("simulation", r"needs a `sigma`|needs `axis_selection`|needs a `selection`|"
     r"needs a `site_selec|needs \{name|needs `\{name|"
     r"A `walls` block needs|needs a ligand|and \"\s*\"`site_selec",
     "simulation.cv.missing_companion"),

    ("simulation", r"selection \{expression!r\} matched no atoms|"
     r"The \{label\} selection", "simulation.cv.selection_empty"),

    # Only the sites that report a count. "needs a `selection` matching
    # exactly three atoms" fires when there is no selection at all, which
    # is a missing companion, and both phrasings name the number.
    ("simulation", r"An angle is three atoms;|A torsion is four atoms;",
     "simulation.cv.selection_arity"),

    ("simulation", r"without a wall or a funnel", "simulation.cv.unbounded"),

    ("simulation", r"holds no hills|deposited \"\s*\"nothing", "simulation.bias.no_deposit"),

    ("simulation", r"holds \{hills.n_dims\}|The hills hold|names \{len\(centre_at\)\}|"
     r"bias points have|one column per|must be one-dimensional and",
     "simulation.bias.dimension_mismatch"),

    ("simulation", r"has no native contacts|is measured against a reference structure",
     "simulation.reference.unusable"),

    # -- simulation: restraints, windows, seeds ---------------------------
    ("simulation", r"Unknown restraint kind|Unknown integrator|"
     r"Unknown umbrella setting", "simulation.cv.unknown"),
    ("simulation", r"restraint needs a `force_constant`|needs a `centre`|"
     r"window needs a `force_constant`|needs a `collective_variable`|"
     r"Umbrella sampling needs a `force_constant`|needs a `to`|"
     r"needs a positive number of `steps`|needs `from`|"
     r"no 'script' was provided|are given either as `centres`",
     "simulation.bias.parameter_missing"),
    ("simulation", r"restraint selection .* matched no|"
     r"No atom matches|No site selection reached|No ligand name reached|"
     r"is not a selection this can", "simulation.cv.selection_empty"),
    ("simulation", r"restraint holds two atoms|restraint holds three atoms|"
     r"restraint holds four atoms|could not be \"\s*\"read",
     "simulation.restraint.selection_arity"),
    ("simulation", r"needs at least two windows|at least two windows with",
     "simulation.windows.too_few"),
    ("simulation", r"produced no sampling|No window contributed",
     "simulation.windows.no_sampling"),
    ("simulation", r"has a potential \"\s*\"energy|seed has a potential energy|"
     r"particles \"\s*\"and the|was not written, so window",
     "simulation.seed.unusable"),
    ("simulation", r"can be steered, biased with metadynamics|"
     r"can be steered or biased with metadynamics, not both",
     "config.option.conflicting"),
    ("simulation", r"No usable OpenMM platform", "environment.platform.unavailable"),
    ("simulation", r"requires the 'openmm-plumed'|requires OpenMM",
     "environment.backend.missing"),
    ("simulation", r"script file not found", "environment.path.not_found"),
    ("simulation", r"must be a number, got|which is text\. It is a number|"
     r"it must be above 0 and|was given as \{len\(force\)\} values|"
     r"was given a force constant of|exactly tw",
     "config.option.wrong_type"),
    ("simulation", r"was accepted and never applied",
     "config.option.inapplicable"),

    # -- batch, report, gui -----------------------------------------------
    ("batch", r"Unknown sweep|sweep .* must be|is not a list|holds no runs|"
     r"produces no runs|must be a mapping", "batch.sweep.invalid"),
    ("report", r"is not installed|requires |could not be imported",
     "report.format.unavailable"),
    ("gui", r"outside the|not under|is not a directory|does not exist",
     "environment.path.not_found"),

    # -- setup: inputs, chains, force fields ------------------------------
    ("setup", r"Could not classify system input|Unknown input_form",
     "setup.input.unrecognised"),
    ("setup", r"requires a system input|Specify either `forcefield`|"
     r"raw `force_field` XML list", "config.option.conflicting"),
    ("setup", r"No structure at |Input PDB not found|Prepared PDB not found",
     "environment.path.not_found"),
    ("setup", r"No chain named|hold no polymer atoms|has no \"\s*\"chains to",
     "setup.structure.chain_unknown"),
    ("setup", r"auto identified components", "setup.structure.undetermined"),
    ("setup", r"does not support ligands", "setup.forcefield.incompatible"),
    ("setup", r"Could not fetch .* from RCSB", "environment.service.unreachable"),
    ("setup", r"heterogens: unknown policy|Unknown nonbonded_method",
     "config.option.not_permitted"),
    ("setup", r"nm across for ", "setup.structure.implausible_extent"),
    ("setup", r"requires pdbfixer and openmm", "environment.backend.missing"),
    ("setup", r"structure was asked for, but|looks like a residue",
     "setup.ligand.pose_unavailable"),

    # -- simulation: pipeline and seeds -----------------------------------
    ("simulation", r"setup outputs are missing|points at |not found: ",
     "analysis.data.absent"),
    ("simulation", r"No topology beside", "analysis.data.absent"),
    ("simulation", r"collective variable recomputed here has median|"
     r"has \{trajectory.n_frames\} frame", "simulation.seed.unusable"),
    ("simulation", r"funnel bounds where a ligand goes|"
     r"funnel restraint is built around|"
     r"fraction of a reference structure's native",
     "simulation.cv.missing_companion"),

    # -- report -----------------------------------------------------------
    # Not a bare "region_highlights": every message in that module names
    # the setting, including the one reporting that the RMSF output it
    # reads was never produced, which belongs to the data family.
    ("report", r"region_highlights\[|region_highlights must be",
     "report.region.invalid"),
    ("report", r"No report to convert|require existing RMSF output|"
     r"contains no residue values|must have at least two columns",
     "analysis.data.absent"),

    # -- batch and gui ----------------------------------------------------
    ("batch", r"requires `config`|requires either `systems`",
     "config.option.missing_companion"),
    ("batch", r"already", "environment.path.exists"),
    ("batch", r"could not be prepared|did not finish", "analysis.data.absent"),
    ("batch", r"by resolving the", "simulation.cv.selection_empty"),
    ("gui", r"must be between|must be a number|is too large|must be an object",
     "config.option.wrong_type"),
    ("gui", r"data is empty|must be an n x 4|must include",
     "analysis.data.absent"),
    ("gui", r"did not write a preview|No dashboard ports",
     "environment.platform.unavailable"),

    # -- top level: orchestrator and uncertainty --------------------------
    ("", r"Pass either `system=`|Specify either `include` or `exclude`",
     "config.option.conflicting"),
    ("", r"requires either a `system` input|needs an output directory",
     "config.option.missing_companion"),
    ("", r"Unknown phase", "config.phase.unknown"),
    ("", r"already holds output from", "environment.path.exists"),
    ("", r"needs at least one non-empty series|needs at least one array|"
     r"needs non-empty arrays", "analysis.sampling.too_few_frames"),
    ("", r"paired arrays must be the same length", "config.option.wrong_type"),

    # -- the remainder, by family ------------------------------------------
    ("analysis", r"matched \{len\(|matched zero|has \{len\(ligand_hydrogens",
     "analysis.selection.arity"),
    ("analysis", r"count_multiplier must be|must be a dict, got",
     "analysis.option.wrong_type"),
    ("analysis", r"hydrogen\(s\) in this selection are bonded|"
     r"carries no unit cell", "analysis.system.inapplicable"),
    ("analysis", r"net charge of \{chemistry.resname",
     "setup.chemistry.charge_undetermined"),
    ("analysis", r"chemistry of \{resname!r\} could not be established",
     "setup.chemistry.uninterpretable"),
    ("analysis", r"dimensions; this|says it is two-dimensional|"
     r"hills hold \{hills.n_dims\}|values against|holds \{columns.shape",
     "simulation.bias.dimension_mismatch"),
    ("analysis", r"is already registered to", "analysis.option.not_permitted"),
    ("analysis", r"Unknown analysis:|Unknown analyses in|No colour role named",
     "analysis.unknown"),
    ("analysis", r"returned unwritten frames", "analysis.data.absent"),
    ("analysis", r"No deposited structure with B-factors",
     "analysis.data.absent"),

    ("setup", r"same residue name|ligand names given for|"
     r"ligand charges given for|a single ligand name",
     "config.option.conflicting"),
    ("setup", r"clashes with the protein", "setup.ligand.clash"),
    ("setup", r"Unknown constraints option", "config.option.not_permitted"),
    ("setup", r"requires OpenMM|needs openmmforcefields",
     "environment.backend.missing"),
    ("setup", r"is not a lipid OpenMM can build",
     "setup.membrane.lipid_unparameterized"),
    ("setup", r"StudyError\(problem\)", "setup.membrane.orientation_unchecked"),
    ("setup", r"exceeds \"\s*\"half|Nonbonded cutoff", "config.option.wrong_type"),

    ("simulation", r"could not be \"\s*\"resolved|restraint selection",
     "simulation.cv.selection_empty"),
    ("simulation", r"prepared system has|has a potential \"",
     "simulation.seed.unusable"),
    ("simulation", r"holds one system at many positions",
     "config.option.conflicting"),

    ("batch", r"ALREADY_HOLD_RESULTS", "environment.path.exists"),
    ("validation", r"holds no data rows", "analysis.data.absent"),
    ("validation", r"needs MDAnalysis and ProLIF", "environment.backend.missing"),

    # -- the cone work that landed on main while this branch was open ------
    ("simulation", r"cone was never measured", "simulation.cone.unmeasured"),
    ("simulation", r"only fits in a cone of", "simulation.cone.too_narrow"),
    ("simulation", r"would start outside a cone", "simulation.cone.windows_outside"),
    ("simulation", r"cone's axis selection .* matched no|"
     r"cone's axis selection .* matches no|"
     r"cone's `axis_selection` .* matched no|"
     r"No alpha carbon in this topology|"
     r"alpha carbon\(s\) lie between", "simulation.cv.selection_empty"),
    ("simulation", r"matched \{frame_atoms.size\} atom", "simulation.cv.selection_arity"),
    ("simulation", r"frame\(s\) of this pull have the ligand",
     "analysis.sampling.too_few_frames"),
    ("simulation", r"`cone` is the angular wall|A cone record needs|"
     r"A cone restrains the angle", "simulation.bias.parameter_missing"),
    ("batch", r"names \{named!r\}, and there is", "analysis.data.absent"),

    # -- the cone's own settings, which are config rather than science -----
    ("simulation", r"`half_angle_deg` is the angle|needs a positive `force_constant`|"
     r"`keep` is the percentile|`margin` opens it wider|"
     r"`gate_used` is the fraction", "config.option.wrong_type"),
    ("simulation", r"`cone` takes `auto`|A cone takes `half_angle_deg`|"
     r"`cone` is \{spec!r\}", "config.option.not_permitted"),
    ("simulation", r"`axis_atoms` names the group|"
     r"angle is measured against a group of atoms, and", "simulation.cv.selection_empty"),
    ("simulation", r"axis selection \{cone.axis_selection!r\}", "simulation.cv.selection_empty"),
    ("simulation", r"A path is an array of unit vectors", "simulation.bias.dimension_mismatch"),
    ("simulation", r"needs a path to measure|needs at least three",
     "analysis.sampling.too_few_frames"),
    ("simulation", r"did not all run under the same cone", "simulation.cone.windows_outside"),
]

SKIP_TYPES = frozenset({
    "AssertionError", "NotImplementedError", "AttributeError",
    "StopIteration", "SystemExit",
})

#: Builtins it is safe to widen into, and what to widen them into. A
#: RuntimeError is deliberately absent.
PROMOTE = {
    "ValueError": "StudyError",
        "FileExistsError": "OutputExistsError",
    "FileNotFoundError": "MissingResultError",
    "ImportError": "BackendUnavailable",
}


def migrate(path: pathlib.Path, *, dry_run: bool = False) -> tuple[int, int, list[str]]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.split("\n")

    planned: list[tuple[int, int, str, str | None]] = []
    skipped: list[str] = []
    total = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        name = (getattr(node.exc.func, "id", None)
                or getattr(node.exc.func, "attr", None))
        if name is None or name in SKIP_TYPES:
            continue
        total += 1
        if any(k.arg == "code" for k in node.exc.keywords):
            continue
        seg = ast.get_source_segment(src, node.exc) or ""
        parts = set(path.parts)
        hits = {code for scope, pattern, code in FAMILIES
                if (not scope or scope in parts) and re.search(pattern, seg)}
        if not hits:
            continue
        if len(hits) > 1:
            skipped.append(f"{path.name}:{node.lineno} ambiguous {sorted(hits)}")
            continue
        if name in PROMOTE:
            promote_to = PROMOTE[name]
        elif name in ("FileNotFoundError", "RuntimeError", "KeyError",
                      "ImportError", "OSError"):
            # Widening these would change what catches them. Left for a
            # per-site decision.
            skipped.append(f"{path.name}:{node.lineno} {name} needs a decision")
            continue
        else:
            promote_to = None  # already a CodedError subclass
        planned.append((node.exc.end_lineno, node.exc.end_col_offset,
                        hits.pop(), promote_to))

    for lineno, col, code, promote_to in sorted(planned, reverse=True):
        line = lines[lineno - 1]
        close = line.rfind(")", 0, col)
        if close < 0:
            skipped.append(f"{path.name}:{lineno} no closing paren")
            continue
        lines[lineno - 1] = line[:close] + f', code="{code}"' + line[close:]

    if planned and not dry_run:
        out = "\n".join(lines)
        for old, new in PROMOTE.items():
            out = re.sub(rf"\braise {old}\(", f"raise {new}(", out)
        for name in ("StudyError", "OutputExistsError", "MissingResultError",
                     "BackendUnavailable"):
            if f"{name}(" not in out or f"import {name}" in out:
                continue
            if any(l.startswith("from fastmdxplora.refusals") and name in l
                   for l in out.split("\n")):
                continue
            body = ast.parse(out).body
            end = max((n.end_lineno for n in body
                       if isinstance(n, (ast.Import, ast.ImportFrom))), default=0)
            out_lines = out.split("\n")
            out_lines.insert(end,
                             f"from fastmdxplora.refusals import {name}")
            out = "\n".join(out_lines)
        ast.parse(out)
        path.write_text(out, encoding="utf-8")

    return len(planned), total, skipped


if __name__ == "__main__":
    import sys
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    named = counted = 0
    notes: list[str] = []
    for f in sorted(root.rglob("*.py")):
        n, t, s = migrate(f, dry_run="--dry-run" in sys.argv)
        named += n
        counted += t
        notes += s
        if n:
            print(f"  {n:3d}/{t:3d}  {f}")
    print(f"\nnamed {named} of {counted}")
    if notes:
        print(f"\nleft for a decision ({len(notes)}):")
        for note in notes:
            print("  ", note)
