"""``fastmdx`` command-line entry point.

Subcommands
-----------

  explore / xplore   Run the full pipeline (setup → simulation → analysis → report)
  setup              Run only the setup phase
  simulate           Run only the simulation phase
  analyze            Run only the analysis phase
  report             Run only the report phase
  info               Print environment and component info
  remote             Inspect other machines, reached over SSH

Each per-phase subcommand exposes the phase's own options (e.g.
``--ph`` for setup, ``--duration-ns`` for simulate). The ``explore``
subcommand additionally exposes those same options under per-phase
prefixes (``--setup-ph``, ``--simulate-duration-ns``) so a user can drive
the full pipeline from a single invocation.

Global flags:

  --version, -V
  --cite                 Print the citation and exit
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from fastmdxplora import (
    __author__,
    __citation__,
    __doi__,
    __expansion__,
    __version__,
)
from fastmdxplora.orchestrator import FastMDXplora
from fastmdxplora.utils.logging import get_logger

logger = get_logger("cli")


# ---------------------------------------------------------------------------
# Phase-option definitions
# ---------------------------------------------------------------------------
# Each entry: (cli_flag_suffix, kwarg_name, argparse-kwargs)
#
# The cli_flag_suffix is appended to "--" for the per-phase subcommands
# (e.g. setup's ``--ph``) and to "--<prefix>-" for the `explore`
# subcommand (e.g. ``--setup-ph``).
#
# The kwarg_name is what gets passed to the phase's run() function.

_SETUP_OPTIONS: list[tuple[str, str, dict[str, Any]]] = [
    ("ph", "ph", {"type": float, "help": "pH for hydrogen placement."}),
    ("protonation-margin", "protonation_margin", {"type": float, "metavar": "UNITS",
        "help": "How close a ligand's pKa may come to the pH before setup "
                "stops rather than choose a charge state. Narrow it only for "
                "a ligand whose protonation you already know."}),
    ("heterogens", "heterogens", {"type": str, "help": "How to treat non-standard residues: auto decides per "
                "component and stops where the structure does not determine "
                "what to simulate; drop removes them all; keep retains them."}),
    ("keep-heterogens", "keep_heterogens", {"action": "store_true", "default": None,
        "help": "Retain non-standard residues. Same as --setup-heterogens keep."}),
    ("keep-water", "keep_water", {"action": "store_true", "default": None,
        "help": "Retain crystallographic waters."}),
    ("fixed-pdb", "fixed_pdb", {"type": str, "metavar": "PATH",
        "help": "Use an already-fixed PDB and skip PDBFixer."}),
    ("forcefield", "forcefield", {"help": "Named force field, resolved to the right XMLs and water "
                "model. 'amber-openff' is the one that takes a ligand."}),
    ("force-field", "force_field", {"nargs": "+", "metavar": "XML",
        "help": "Raw OpenMM XML(s), overriding --forcefield (power users)."}),
    ("ligand", "ligand", {"nargs": "+", "metavar": "FILE",
        "help": "Ligand SDF/MOL2 file(s) (needs --setup-forcefield amber-openff)."}),
    ("ligand-forcefield", "ligand_forcefield", {"type": str, "metavar": "NAME",
        "help": "OpenFF small-molecule force field (e.g. openff-2.2.1)."}),
    ("ligand-name", "ligand_name", {"type": str, "metavar": "NAME",
        "help": "Residue/molecule name for the ligand."}),
    ("ligand-net-charge", "ligand_net_charge", {"type": int, "metavar": "INT",
        "help": "Ligand formal net charge (default: inferred from SDF)."}),
    ("no-ligand-clash-check", "check_ligand_clashes", {"action": "store_false",
        "default": None,
        "help": "Skip the ligand-protein clash check at setup."}),
    ("ligand-clash-threshold-nm", "ligand_clash_threshold_nm", {"type": float,
        "metavar": "NM",
        "help": "Min ligand-protein contact distance in nm."}),
    ("water-model", "water_model", {"type": str, "metavar": "NAME",
        "help": "Water model for Modeller (e.g. 'tip3p', 'tip4pew')."}),
    ("solvent-padding-nm", "solvent_padding_nm", {"type": float,
        "help": "Min distance between solute and box wall in nm."}),
    ("box-shape", "box_shape", {"help": "Periodic box geometry."}),
    ("nonbonded-method", "nonbonded_method", {"help": "Nonbonded method."}),
    ("ion-positive", "ion_positive", {"type": str, "metavar": "ION",
        "help": "Counter-ion cation."}),
    ("ion-negative", "ion_negative", {"type": str, "metavar": "ION",
        "help": "Counter-ion anion."}),
    ("ion-concentration-M", "ion_concentration_M", {"type": float,
        "help": "Target ionic strength in M."}),
    ("temperature-K", "temperature_K", {"type": float,
        "help": "Initial velocity temperature in K."}),
]

_SIMULATION_OPTIONS: list[tuple[str, str, dict[str, Any]]] = [
    ("duration-ns", "duration_ns", {"type": float,
        "help": "Production length in ns (standard MD convention; equilibration is independent)."}),
    ("nvt-duration-ns", "nvt_duration_ns", {"type": float,
        "help": "NVT equilibration in ns (default: fixed 500 ps regardless of production length)."}),
    ("npt-duration-ns", "npt_duration_ns", {"type": float,
        "help": "NPT equilibration in ns (default: fixed 1 ns regardless of production length)."}),
    ("nvt-steps", "nvt_steps", {"type": int,
        "help": "NVT step count (overrides --nvt-duration-ns)."}),
    ("npt-steps", "npt_steps", {"type": int,
        "help": "NPT step count (overrides --npt-duration-ns)."}),
    ("production-steps", "production_steps", {"type": int,
        "help": "Production step count (overrides --duration-ns)."}),
    ("timestep-fs", "timestep_fs", {"type": float,
        "help": "Integrator timestep in fs."}),
    ("integrator", "integrator", {"help": "Integrator."}),
    ("temperature-K", "temperature_K", {"type": float,
        "help": "Production temperature in K."}),
    ("pressure-bar", "pressure_bar", {"type": float,
        "help": "Barostat pressure in bar, OpenMM-native."}),
    ("pressure-atm", "pressure_atm", {"type": float,
        "help": "Barostat pressure in atm (converted to bar)."}),
    ("friction-per-ps", "friction_per_ps", {"type": float,
        "help": "Langevin friction in 1/ps."}),
    ("platform", "platform", {"help": "OpenMM compute platform. 'auto' tries CUDA, then OpenCL, "
                "then CPU."}),
    ("precision", "precision", {"help": "GPU precision."}),
    ("device-index", "device_index", {"type": str, "metavar": "IDX",
        "help": "GPU device index for multi-GPU machines (e.g. '0' or '0,1')."}),
    ("checkpoint-interval-steps", "checkpoint_interval_steps", {"type": int,
        "help": "Checkpoint (.chk) interval in steps; 0 disables."}),
    ("telemetry-interval", "telemetry_interval", {"type": int,
        "help": "Minimum step interval for live telemetry updates."}),
    ("trajectory-interval-steps", "trajectory_interval_steps", {"type": int,
        "help": "Trajectory (.dcd) frame interval in steps (default: adaptive, ~2000 frames)."}),
    ("random-seed", "random_seed", {"type": int,
        "help": "Integrator random seed (default: not set)."}),
    ("plumed-script", "plumed_script", {"type": str, "metavar": "PATH",
        "help": "Enable PLUMED enhanced sampling with this script (path to a "
                ".dat file or inline text). Requires openmm-plumed."}),
    ("no-minimize", "minimize", {"action": "store_false", "default": None,
        "help": "Skip the energy minimization stage."}),
]

_ANALYSIS_OPTIONS: list[tuple[str, str, dict[str, Any]]] = [
    ("trajectory", "trajectory", {"type": str, "metavar": "PATH",
        "help": "Trajectory file (default: simulation/production.dcd)."}),
    ("topology", "topology", {"type": str, "metavar": "PATH",
        "help": "Topology file (default: simulation/topology.pdb)."}),
    ("ligand-resname", "ligand_resname", {"type": str, "metavar": "NAME",
        "help": "Ligand residue name for ligand-aware analyses."}),
    ("analyses", "include", {"nargs": "+", "metavar": "NAME",
        "help": "Subset of analyses to run (e.g. rmsd rmsf rg). Default: all."}),
    ("exclude-analyses", "exclude", {"nargs": "+", "metavar": "NAME",
        "help": "Analyses to skip. Mutually exclusive with --analyses."}),
    ("selection", "selection", {"type": str, "metavar": "EXPR",
        "help": "Default MDTraj atom selection (e.g. 'name CA'). Overrides --scope."}),
    ("scope", "scope", {"help": "Atom scope for analyses. 'solute' is protein plus ligand."}),
    ("stride", "stride", {"type": int,
        "help": "Frame stride for trajectory loading (default 1)."}),
    ("first", "first", {"type": int,
        "help": "First frame index to include (default 0)."}),
    ("last", "last", {"type": int,
        "help": "Last frame index (exclusive). Default: full trajectory."}),
    ("dimred-methods", "dimred_methods", {"nargs": "+", "choices": ["pca", "tsne", "umap"],
        "metavar": "METHOD",
        "help": "Dimensionality-reduction methods (e.g. pca)."}),
    ("dimred-components", "dimred_components", {"type": int, "metavar": "N",
        "help": "Number of dimensionality-reduction components (default 2)."}),
    ("cluster-methods", "cluster_methods", {"nargs": "+",
        "choices": ["kmeans", "hierarchical", "dbscan"], "metavar": "METHOD",
        "help": "Clustering methods (e.g. hierarchical)."}),
    ("cluster-n-clusters", "cluster_n_clusters", {"type": int, "metavar": "N",
        "help": "Number of clusters for k-means/hierarchical clustering."}),
    ("cluster-features", "cluster_features", {"choices": ["rmsd", "coordinates"],
        "help": "What clustering compares frames in: rmsd superposes every "
                "pair optimally; coordinates superposes each onto the first "
                "and compares directly."}),
    ("cluster-linkage", "cluster_linkage",
        {"choices": ["ward", "complete", "average", "single"],
         "help": "Hierarchical clustering linkage method."}),
]

_REPORT_OPTIONS: list[tuple[str, str, dict[str, Any]]] = [
    ("title", "title", {"type": str, "metavar": "STR",
        "help": "Report title (default auto-generated from system name)."}),
    ("author", "author", {"type": str, "metavar": "NAME",
        "help": "Author name for the report metadata."}),
    ("no-document", "document", {"action": "store_false", "default": None,
        "help": "Skip the Markdown document."}),
    ("no-slides", "slides", {"action": "store_false", "default": None,
        "help": "Skip the PPTX slide deck."}),
    ("no-bundle", "bundle", {"action": "store_false", "default": None,
        "help": "Skip the project_bundle.zip artifact."}),
    ("no-methods", "include_methods", {"action": "store_false", "default": None,
        "help": "Skip the Methods section in the document."}),
    ("no-reproducibility", "include_reproducibility", {"action": "store_false", "default": None,
        "help": "Skip the Reproducibility section."}),
]


#: Settings the schema declares that deliberately get no flag of their own.
#: Each is reachable, by a flag with a different name or through a structured
#: block a command line cannot express, and the reason is recorded so this
#: does not become a place to hide a setting nobody wired up.
_NO_FLAG_OF_ITS_OWN: dict[str, str] = {
    # A mapping, and a list of blocks, were withheld as things a flag cannot
    # carry. A flag reads a mapping as YAML now, and each item of a list the
    # same way, so `--analyze-options '{cluster: {n_clusters: 5}}'` and
    # `--report-region-highlights '{label: helix, start: 3, end: 7}'` carry
    # them; the config file is still the readable place for either.
    # Named differently on the command line, for the reason given.
    "plumed": "reached by --simulate-plumed-script, which takes a path",
    "force_field": "reached by --setup-forcefield, which resolves a name",
    # Already flags on the dashboard command, without a phase prefix, because
    # that is where somebody sets them.
    "dashboard_ligand_resname": "reached by --dashboard-ligand-resname",
    "dashboard_binding_pocket_cutoff_A":
        "reached by --dashboard-binding-pocket-cutoff-A",
    "dashboard_max_playback_frames":
        "reached by --dashboard-max-playback-frames",
}


class _PercentSafeHelp(argparse.RawDescriptionHelpFormatter):
    """Render help without treating a literal percent as a format spec.

    argparse expands help strings with `%`, so `71% of a cube` in box_shape
    read as a `% o` octal conversion and raised at print_help() time while the
    parser still built. Escaping in the schema is wrong: config/generate.py
    and gui/schema_payload.py print the same text verbatim and would show
    `71%%`, and the interface-parity test requires the stored help to equal
    the schema's byte for byte. So the doubling happens here, at render, and
    nowhere earlier.
    """

    def _get_help_string(self, action):
        return (super()._get_help_string(action) or "").replace("%", "%%")


def _schema_for(phase: str):
    """The schema a CLI verb (or block name) refers to, or None.

    Three functions used to write `PHASE_SCHEMAS.get(_SCHEMA_KEY.get(...))`
    separately, which made `execution` invisible to the flag generator, the
    default reader and the choice reader in the same stroke -- it is a
    top-level block and not a phase, so it is in `all_schemas()` and not in
    `PHASE_SCHEMAS`. One lookup, and adding a block to `all_schemas()` is
    enough to reach all three.
    """
    from fastmdxplora.config.schema import all_schemas

    return all_schemas().get(_SCHEMA_KEY.get(phase, phase))


def _generated_options(phase: str, written: list[tuple]) -> list[tuple]:
    """A flag for every setting the schema declares.

    The flags were listed by hand beside a schema declaring the same settings,
    and the list drifted: twenty-four settings had no flag at all, and of the
    sixty that did, fifty-three carried help text that had fallen behind the
    schema's. Keeping the wording was the argument for the table, and the
    wording was the thing that had rotted -- ``--setup-ph`` said "pH for
    hydrogen placement" where the schema explains that it sets protonation
    states.

    So everything derivable is derived: the help, the type, the accepted
    values, whether it takes a list. What remains in the table is what cannot
    be: a flag deliberately named something other than its setting, and a
    convenience flag that has no setting of its own.
    """
    group = _schema_for(phase)
    if group is None:
        return written

    # The wording comes from the schema even where the table supplied some.
    # Fifty-three of its sixty entries had help that had fallen behind the
    # declaration, which is what a second copy of a sentence does. What the
    # table keeps is the flag name and the argparse mechanics -- how the value
    # is spelled on the command line, not what it means.
    refreshed: list[tuple] = []
    for flag, dest, options in written:
        field = group.get(dest)
        if field is not None and field.help:
            options = dict(options)
            if options.get("action") == "store_false":
                options["help"] = (
                    f"Do not: {field.help[0].lower()}{field.help[1:]}")
            else:
                options["help"] = field.help
        refreshed.append((flag, dest, options))
    written = refreshed

    already = {dest for _flag, dest, _kw in written}
    generated: list[tuple] = []
    for field in group.fields:
        if field.name in already or field.name in _NO_FLAG_OF_ITS_OWN:
            continue
        flag = field.name.replace("_", "-")
        help_text = field.help or f"Set {field.name}."

        if field.type is bool:
            # Both directions, because a setting defaulting to true needs a
            # way off and one defaulting to false needs a way on -- and which
            # it is can change without the flag having to.
            generated.append((flag, field.name, {
                "action": "store_true", "default": None, "help": help_text}))
            generated.append((f"no-{flag}", field.name, {
                "action": "store_false", "default": None,
                "help": f"Do not: {help_text[0].lower()}{help_text[1:]}"}))
            continue

        options: dict[str, Any] = {"help": help_text}
        if field.choices:
            options["choices"] = list(field.choices)
        if field.type is list:
            options["nargs"] = "+"
            options["metavar"] = "VALUE"
            # Each item as the config file would read it, so `--execution-devices
            # 0 1` is a list of numbers and `--setup-chains A B` of names. Read
            # as text before, so the devices arrived as the strings "0" and "1".
            options["type"] = item
        else:
            options["type"] = _parser_for(field.type)
            options["metavar"] = _METAVAR.get(options["type"], "VALUE")
        generated.append((flag, field.name, options))
    return list(written) + generated


from fastmdxplora.refusals import CodedError  # noqa: E402


def number(text: str) -> int | float:
    """A setting the schema lets be whole or not: whole where it is written
    whole. Read as text before, so `--setup-temperature-K 310` reached the
    study as the string "310"."""
    try:
        return int(text)
    except ValueError:
        return float(text)


class NotTheTypeItTakes(CodedError, argparse.ArgumentTypeError):
    """A flag's value that is not the type its setting takes. argparse reports
    it as the flag's error; the code says which refusal it is."""

    default_code = "config.option.wrong_type"


def item(text: str) -> Any:
    """One item of a list setting, read as the config file reads a value."""
    import yaml

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def mapping(text: str) -> dict[str, Any]:
    """A block of settings, written as a YAML mapping. Read as text before, so
    a block could not be given on the command line at all: what arrived was a
    string, and the validator named its characters as unknown settings."""
    import yaml

    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise NotTheTypeItTakes(
            f"not a mapping: {str(exc).splitlines()[0]}") from exc
    if not isinstance(value, dict):
        raise NotTheTypeItTakes(
            "expects a mapping of settings, as in "
            "'{collective_variable: distance, from: 0.3, to: 1.5}'")
    return value


def _parser_for(kind: Any) -> Any:
    """How the command line reads a value, from the type the schema declares."""
    if kind in (int, float, str):
        return kind
    if kind is dict:
        return mapping
    if isinstance(kind, tuple) and set(kind) <= {int, float}:
        return number
    return str


#: What to call the value in help, by how it is read. Cosmetic, and derived
#: rather than written per flag so a new setting does not need a decision.
_METAVAR = {int: "N", float: "X", str: "TEXT", number: "X", mapping: "YAML"}


# Map: phase -> (options-list, explore-prefix)
_PHASE_SPEC = {
    "setup":      (_SETUP_OPTIONS,      "setup"),
    "simulate":   (_SIMULATION_OPTIONS, "simulate"),
    "analyze":    (_ANALYSIS_OPTIONS,   "analyze"),
    "report":     (_REPORT_OPTIONS,     "report"),
}

# Phase-name aliases used when forwarding options to the orchestrator's
# `options=` dict. CLI says "simulate" / "analyze" but the orchestrator
# uses "simulation" / "analysis".
_PHASE_TO_ORCH = {
    "setup":    "setup",
    "simulate": "simulation",
    "analyze":  "analysis",
    "report":   "report",
}


#: The CLI names a phase for the verb, the schema for the noun.
_SCHEMA_KEY = {
    "setup": "setup", "simulate": "simulation",
    "analyze": "analysis", "report": "report",
}


# Filled in once the schema key is known: the hand-written flags above, plus
# one for every setting they do not cover. Done here rather than in the
# literal because the mapping from a CLI verb to a schema block is declared
# below it.
for _phase in list(_PHASE_SPEC):
    _table, _prefix = _PHASE_SPEC[_phase]
    _PHASE_SPEC[_phase] = (_generated_options(_phase, _table), _prefix)

#: `execution` is not a phase -- it has no subcommand and does not appear in
#: a plan -- so it is not in `_PHASE_SPEC`. It is still a block of the config
#: with four settings, and it had no flag and no form control: writing a file
#: was the only way to reach `mode`, `workers`, `devices` or
#: `continue_on_error`, against a README that says no interface is a subset
#: of another. Generated the same way, attached to the commands that schedule
#: more than one run.
_EXECUTION_OPTIONS = _generated_options("execution", [])


def _schema_defaults(phase: str) -> dict[str, Any]:
    """Every default for a phase, read from the one place they are declared."""
    group = _schema_for(phase)
    if group is None:
        return {}
    return {field.name: field.default for field in group.fields}


def _schema_choices(phase: str) -> dict[str, tuple[str, ...]]:
    """Every accepted-value list for a phase, from the schema that declares it."""
    group = _schema_for(phase)
    if group is None:
        return {}
    return {f.name: f.choices for f in group.fields if f.choices}


def _with_default(help_text: str, kwarg: str, defaults: dict[str, Any]) -> str:
    """Append the schema's default to a help string.

    Written out by hand, a default drifts: the help said pH 7.0 after it had
    moved to 7.4, and named charmm36 after the force field became auto. Someone
    reading --help has no way to tell and no reason to doubt it. So the value
    is taken from the schema, which is where it is decided.
    """
    if kwarg not in defaults:
        return help_text
    default = defaults[kwarg]
    if default is None or isinstance(default, bool):
        # A flag's default is carried by whether passing it turns something on.
        return help_text
    return f"{help_text} Default: {default}.".strip()


def _attach_phase_options(
    parser: argparse.ArgumentParser,
    options: list[tuple[str, str, dict[str, Any]]],
    *,
    prefix: str = "",
    dest_prefix: str = "",
    group_title: str = "options",
    phase: str = "",
) -> None:
    """Attach a phase's options to a parser under an argparse group.

    Parameters
    ----------
    parser : argparse.ArgumentParser
    options : list
        The per-phase option tuples (cli_suffix, kwarg, argparse_kwargs).
    prefix : str
        Prepended to the CLI flag with a dash (e.g. ``prefix="simulate"``
        → ``--simulate-duration-ns``). Empty for per-phase subcommands.
    dest_prefix : str
        Prepended to ``argparse.dest`` to avoid name collisions in
        ``explore``. Same convention as ``prefix`` but with underscores.
    group_title : str
        Title for the argument group (shown in --help).
    """
    # One group per set of related settings, so `--help` reads as sections
    # rather than as thirty-seven flags in declaration order. The grouping is
    # the schema's, which is where the browser gets it too -- two views of one
    # list, rather than two lists that agree until one is edited.
    from fastmdxplora.config.schema import SETTING_GROUPS

    schema_key = _SCHEMA_KEY.get(phase, phase)
    titles = {name: title
              for title, _why, names in SETTING_GROUPS.get(schema_key, ())
              for name in names}
    order = [title for title, _why, _names in SETTING_GROUPS.get(schema_key, ())]
    groups: dict[str, Any] = {}

    def _group_for(kwarg: str):
        """The argument group a setting belongs in, made when first needed."""
        title = titles.get(kwarg)
        if title is None:
            # A convenience flag with no setting of its own -- there is
            # nothing in the schema to have grouped it by.
            stem = (group_title or "").removesuffix(" options")
            heading = f"{stem}: other" if stem else "other"
            return groups.setdefault(heading, parser.add_argument_group(heading))
        # "simulate options" reads badly in front of a group name, and the
        # word carries nothing: every flag is an option.
        stem = (group_title or "").removesuffix(" options")
        heading = f"{stem}: {title.lower()}" if stem else title
        if title not in groups:
            groups[title] = parser.add_argument_group(heading)
        return groups[title]

    defaults = _schema_defaults(phase)
    choices = _schema_choices(phase)
    for cli_suffix, kwarg, argparse_kwargs in sorted(
        options, key=lambda o: (order.index(titles[o[1]])
                                if o[1] in titles else len(order))
    ):
        group = _group_for(kwarg)
        if prefix:
            flag = f"--{prefix}-{cli_suffix}"
            dest = f"{dest_prefix}__{kwarg}"
        else:
            flag = f"--{cli_suffix}"
            dest = kwarg
        argparse_kwargs = dict(argparse_kwargs)
        argparse_kwargs["help"] = _with_default(
            argparse_kwargs.get("help", ""), kwarg, defaults)
        # The accepted values come from the schema too. They were written out
        # here as well, and in the browser, and beside the code that validates
        # them -- four copies of one list, agreeing by nobody having touched
        # them.
        allowed = choices.get(kwarg)
        if allowed and "choices" not in argparse_kwargs:
            argparse_kwargs["choices"] = list(allowed)
        group.add_argument(flag, dest=dest, **argparse_kwargs)


def _harvest_phase_options(
    args: argparse.Namespace,
    options: list[tuple[str, str, dict[str, Any]]],
    *,
    dest_prefix: str = "",
) -> dict[str, Any]:
    """Pull phase options out of parsed args, dropping None values.

    Returns a kwargs-shaped dict ready to splat into the phase's run().
    None values are dropped so the phase falls back to its own DEFAULTS
    table — important because argparse uses None for unset args even
    when the phase's own default is something else.
    """
    out: dict[str, Any] = {}
    for _cli_suffix, kwarg, _ in options:
        dest = f"{dest_prefix}__{kwarg}" if dest_prefix else kwarg
        val = getattr(args, dest, None)
        if val is not None:
            out[kwarg] = val
    return out


#: Flags whose name does not become the option name by dropping the analysis
#: prefix. --analyze-dimred-components reads better than
#: --analyze-dimred-n-components, but the option is n_components, and stripping
#: the prefix produced "components", which no analysis has ever accepted. The
#: flag was therefore ignored in silence.
_OPTION_KEY_OVERRIDES = {
    ("dimred", "dimred_components"): "n_components",
}


def _option_key(analysis: str, flag_key: str) -> str:
    """The option name a CLI flag stands for."""
    override = _OPTION_KEY_OVERRIDES.get((analysis, flag_key))
    if override is not None:
        return override
    return flag_key[len(analysis) + 1:] if flag_key.startswith(f"{analysis}_") else flag_key


def _normalize_analysis_options(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Move CLI analysis-method flags into the nested ``options`` mapping.

    The analysis orchestrator accepts method-specific settings under
    ``options`` (for example ``options.cluster.n_clusters``), while argparse
    naturally harvests flat flags. Keeping this conversion at the CLI
    boundary makes the same options work for both ``analyze`` and prefixed
    ``explore`` flags without changing the Python API.
    """
    out = dict(kwargs)
    nested = {
        "dimred": {
            key: out.pop(key)
            for key in ("dimred_methods", "dimred_components")
            if key in out
        },
        "cluster": {
            key: out.pop(key)
            for key in ("cluster_methods", "cluster_n_clusters",
                        "cluster_linkage", "cluster_features")
            if key in out
        },
    }
    options = dict(out.get("options") or {})
    for name, values in nested.items():
        if values:
            current = dict(options.get(name) or {})
            current.update(
                {_option_key(name, key): value for key, value in values.items()}
            )
            options[name] = current
    if options:
        out["options"] = options
    elif "options" in out:
        out.pop("options")
    return out


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------
def _study_level_args(p: argparse.ArgumentParser) -> None:
    """The three study-level settings the config file carries and the study
    records: how it was written, by which model, and its budget. On
    `explore` only: a single-phase command attaches that phase's own
    `agent` field without a prefix, so `--agent` there means the phase's.
    Generated from the schema, as the phase settings are."""
    from fastmdxplora.config.loader import STUDY_LEVEL_KEYS
    from fastmdxplora.config.schema import TOP_LEVEL

    study = p.add_argument_group("study")
    for name in STUDY_LEVEL_KEYS:
        field = TOP_LEVEL.get(name)
        options: dict[str, Any] = {"dest": name, "default": None, "help": field.help}
        if field.choices:
            options["choices"] = list(field.choices)
        options["type"] = _parser_for(field.type)
        options["metavar"] = _METAVAR.get(options["type"], "VALUE")
        study.add_argument("--" + name.replace("_", "-"), **options)


def _common_input_args(p: argparse.ArgumentParser) -> None:
    """Arguments shared by all subcommands that accept a system input.

    The ``system`` flag accepts three forms: ``-s`` (GNU short option),
    ``-system`` (single-dash long, the GROMACS / AMBER / NAMD convention
    MD researchers expect), and ``--system`` (GNU double-dash long). All
    three are equivalent.

    The system value is auto-classified downstream: a path ending in
    ``.pdb`` / ``.cif`` is loaded from disk, a 4-character alphanumeric
    string is fetched from RCSB as a PDB ID, and a longer alphabetic
    string is treated as a one-letter sequence. There is therefore no
    separate ``--pdb-id`` flag — ``--system 1L2Y`` does the right thing.
    """
    src = p.add_argument_group("input")
    src.add_argument(
        "-s", "-system", "--system",
        dest="system",
        metavar="SYSTEM",
        help=(
            "System input: a PDB/CIF file path, a 4-character PDB ID "
            "(e.g. 1L2Y, fetched from RCSB), or a one-letter sequence. "
            "May instead be supplied via --config."
        ),
    )
    src.add_argument(
        "-c", "-config", "--config",
        dest="config",
        metavar="FILE",
        help=(
            "YAML config file capturing the whole run (system, output, "
            "phase selection, per-phase options). Command-line flags "
            "override values in the file. See `fastmdx init-config`."
        ),
    )
    src.add_argument(
        "--output",
        dest="output_dir",
        metavar="DIR",
        help=(
            "Output directory for project artifacts "
            "(default: ./fastmdxplora_<system>_study_<UTC-timestamp>)."
        ),
    )
    src.add_argument(
        "--verbose",
        action="store_true",
        help="Also stream debug logging to the terminal.",
    )
    # Top-level rather than per-phase, so it is written here rather than
    # generated from the schema alongside the phase settings.
    src.add_argument(
        "--no-explain",
        dest="explain",
        action="store_false",
        default=True,
        help=(
            "Do not say why each step happens. Explanations are on, because "
            "a pipeline that runs silently teaches nothing; turn them off "
            "once the steps are familiar."
        ),
    )
    dash = p.add_argument_group("dashboard")
    dash.add_argument(
        "--dashboard",
        "--live-dashboard",
        dest="dashboard",
        action="store_true",
        default=False,
        help=(
            "Open the local GUI for this output folder before "
            "the workflow starts. Implies live telemetry when simulation runs."
        ),
    )
    dash.add_argument(
        "--dashboard-host",
        default="127.0.0.1",
        help="Dashboard bind address (default: 127.0.0.1).",
    )
    dash.add_argument(
        "--dashboard-port",
        type=int,
        default=8765,
        help="Dashboard port (default: 8765; next free port is used if busy).",
    )
    dash.add_argument(
        "--dashboard-stop-on-complete",
        action="store_true",
        default=False,
        help="Stop the dashboard automatically when the command completes.",
    )
    dash.add_argument(
        "--dashboard-refresh-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Browser-side telemetry polling interval in seconds (default 3).",
    )
    dash.add_argument(
        "--dashboard-frame-interval",
        type=int,
        default=None,
        metavar="STEPS",
        help="Override simulation telemetry interval used by the live dashboard. "
             "Honored when the workflow is creating a telemetry writer; existing "
             "runs keep their stored value.",
    )
    dash.add_argument(
        "--dashboard-ligand-resname",
        type=str,
        default=None,
        metavar="RESNAME",
        help="Force a ligand residue name for the dashboard ligand tools pane. "
             "Auto-detection is used when omitted.",
    )
    dash.add_argument(
        "--dashboard-binding-pocket-cutoff-A",
        type=float,
        default=None,
        metavar="ANGSTROM",
        help="Default binding-pocket cutoff for the molecular viewer (default 5.0).",
    )
    dash.add_argument(
        "--dashboard-max-playback-frames",
        type=int,
        default=None,
        metavar="FRAMES",
        help="Maximum number of frames the molecular viewer will load for "
             "trajectory playback (default 200).",
    )
    dash.add_argument(
        "--dashboard-open-browser",
        action="store_true",
        default=False,
        help="Attempt to open the dashboard URL in the local browser. "
             "Disabled by default for headless / no-display environments.",
    )


class _Accumulate(argparse.Action):
    """Add to what is there rather than replacing it.

    With `nargs="+"` argparse replaces on a second use, so `--include
    analysis --include report` runs report alone -- half the request dropped,
    and nothing said. Both spellings work now: repeated flags and a single
    flag with several values mean the same thing, which is what somebody
    typing either of them intends.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        current = list(getattr(namespace, self.dest, None) or [])
        for value in values if isinstance(values, list) else [values]:
            if value not in current:
                current.append(value)
        setattr(namespace, self.dest, current)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fastmdx",
        description=(
            f"FastMDXplora: {__expansion__}\n\n"
            "Project-level orchestrator for end-to-end molecular dynamics "
            "studies: setup → simulate → analyze → report."
        ),
        formatter_class=_PercentSafeHelp,
        epilog=(
            "Examples:\n"
            "  fastmdx explore -system protein.pdb\n"
            "  fastmdx xplore --system 1L2Y --simulate-duration-ns 50.0\n"
            "  fastmdx setup -system protein.pdb --ph 6.5\n"
            "  fastmdx simulate --duration-ns 100.0 --platform CUDA\n"
            "  fastmdx analyze --output run_001 --analyses rmsd rmsf rg\n"
            "  fastmdx report --output run_001 --no-slides\n"
            "\n"
            f"Citation: {__citation__}\n"
        ),
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"fastmdx {__version__} (FastMDXplora)",
    )
    parser.add_argument(
        "--cite",
        action="store_true",
        help="Print the citation and exit.",
    )

    sub = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        title="commands",
    )

    # ---------- explore / xplore: full pipeline with prefixed flags ----------
    for verb in ("explore", "xplore"):
        ep = sub.add_parser(
            verb,
            help=(
                "Run the full pipeline (setup → simulate → analyze → report)."
                if verb == "explore"
                else "Alias of `explore` (matches the X branding)."
            ),
            description=(
                "Run the full FastMDXplora pipeline end-to-end on the given "
                "system. Use --include-phase or --exclude-phase to run a subset "
                "of phases. "
                "Each phase's flags are available under a per-phase prefix "
                "(--setup-*, --simulate-*, --analyze-*, --report-*)."
            ),
            formatter_class=_PercentSafeHelp,
        )
        _common_input_args(ep)
        _study_level_args(ep)
        ep.add_argument(
            "--sweep", action="append", dest="sweep", metavar="AXIS=VALUES",
            help=(
                "Run the study once for every value of a setting: "
                "--sweep simulation.temperature_K=300,310,320. Give it once "
                "per axis; the runs are every combination. Where a value "
                "holds a comma, write the list in brackets: "
                "--sweep 'setup.ligand=[\"a.sdf\", \"b.sdf\"]'."
            ),
        )
        ep.add_argument(
            "--force-overwrite",
            "--force",
            dest="force",
            action="store_true",
            help=(
                "Run into an output directory that already holds results, "
                "overwriting them. Without this a second run is refused."
            ),
        )
        # The setting is `include_phase`, so the flag is too. `--include`
        # and `--exclude` still work: they are what every script and every
        # set of notes already says, and breaking them to rename a flag
        # would cost more than the confusion it removes.
        ep.add_argument(
            "--include-phase", "--include",
            dest="include",
            nargs="+",
            action=_Accumulate,
            metavar="PHASE",
            help="Subset of phases to run: setup, simulation, analysis, report.",
        )
        ep.add_argument(
            "--exclude-phase", "--exclude",
            dest="exclude",
            nargs="+",
            action=_Accumulate,
            metavar="PHASE",
            help="Phases to skip (mutually exclusive with --include-phase).",
        )
        ep.add_argument(
            "--no-report",
            action="store_true",
            help="Skip the report phase even if it would otherwise run.",
        )
        ep.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the plan (runs, systems, swept values, output dirs, "
                 "phases) without running anything.",
        )

        # The execution block: how the runs a plan produces are scheduled.
        # Prefixed like the phases, because `--mode` and `--workers` on their
        # own say nothing about which part of the study they belong to.
        _attach_phase_options(
            ep, _EXECUTION_OPTIONS,
            prefix="execution",
            dest_prefix="execution",
            group_title="execution options",
            phase="execution",
        )

        # Per-phase options under per-phase prefix
        for phase, (opts, prefix) in _PHASE_SPEC.items():
            _attach_phase_options(
                ep, opts,
                prefix=prefix,
                dest_prefix=phase,
                group_title=f"{phase} options",
                phase=phase,
            )

    # ---------- per-phase subcommands: phase-specific flags only ----------
    for phase, (opts, _) in _PHASE_SPEC.items():
        pp = sub.add_parser(
            phase,
            help=f"Run only the {phase} phase.",
            description=f"Run only the {phase} phase of the FastMDXplora pipeline.",
            formatter_class=_PercentSafeHelp,
        )
        _common_input_args(pp)
        _attach_phase_options(pp, opts, group_title=f"{phase} options",
                              phase=phase)

    info = sub.add_parser(
        "info",
        help="Print FastMDXplora environment information.",
        description=(
            "Print the installed FastMDXplora version, the detected backends "
            "for each phase, and the citation."
        ),
    )
    info.add_argument(
        "--json",
        action="store_true",
        help=("Print the same information as JSON, for a program to read. "
              "This is how `fastmdx remote` learns what a machine has."),
    )

    sel = sub.add_parser(
        "select",
        help="Show which atoms and residues a selection matches.",
        description=(
            "Resolve a selection against a structure and print what it "
            "matches, before a run uses it. A selection that matches the "
            "wrong atoms is not an error and nothing downstream can detect "
            "it, so the way to know is to look."
        ),
    )
    sel.add_argument("expression",
                     help="The selection, e.g. 'resSeq 189 to 195 and name CA'.")
    sel.add_argument("--structure", "--topology", "-s", dest="structure",
                     required=True,
                     help="A PDB, CIF or trajectory topology to resolve against.")
    sel.add_argument("--limit", type=int, default=40,
                     help="How many atoms to list (default 40). 0 lists all.")
    sel.add_argument("--atoms", action="store_true",
                     help="List every matching atom rather than the residues.")

    gui = sub.add_parser(
        "gui",
        help="Open the FastMDXplora graphical interface in a browser.",
        description=(
            "Serve the local FastMDXplora GUI: build and save a study "
            "configuration, launch a run locally, watch live telemetry, view "
            "the structure and trajectory, and browse results. Binds to "
            "127.0.0.1 by default."
        ),
    )
    gui.add_argument(
        "--output",
        default=None,
        metavar="DIR",
        help=(
            "Output directory to open. Defaults to the current directory, "
            "which is the right choice when you are designing a new study."
        ),
    )
    gui.add_argument("--host", default="127.0.0.1",
                     help="Bind address (default: 127.0.0.1).")
    gui.add_argument("--port", type=int, default=8765,
                     help="Port to serve on (default: 8765).")
    gui.add_argument("--no-browser", action="store_true",
                     help="Do not open a browser window automatically.")
    gui.add_argument("--ligand-resname", type=str, default=None, metavar="RESNAME",
                     help="Force a ligand residue name for the ligand tools.")
    gui.add_argument("--binding-pocket-cutoff-A", type=float, default=None,
                     metavar="ANGSTROM",
                     help="Binding-pocket cutoff used by the viewer.")


    # ---------- agent: write a study from a sentence ------------------------
    ag = sub.add_parser(
        "agent",
        help="Write a study from a sentence, using a model you choose.",
        description=(
            "Describe a study in plain language and get a config. The "
            "config goes through the same validation as one written by "
            "hand, so a refusal here is the refusal you would have got "
            "anyway -- the agent cannot ask for something the software "
            "will not do. Run `fastmdx agent set` once to choose a model; "
            "nothing else in FastMDXplora needs one."
        ),
        formatter_class=_PercentSafeHelp,
    )
    ag.add_argument(
        "request",
        nargs="?",
        metavar="REQUEST",
        help=(
            "What the study should do, in plain language. The word `set` "
            "chooses a model instead. With neither, prints what is "
            "currently set."
        ),
    )
    ag.add_argument(
        "-f", "-file", "--file",
        dest="request_file",
        metavar="FILE",
        help=(
            "Read the request from a file instead. For anything longer "
            "than a shell quote comfortably holds."
        ),
    )
    ag.add_argument(
        "-o", "--output",
        dest="agent_output",
        metavar="FILE",
        help=(
            "Write the config here instead of printing it. The config is "
            "printed either way; this also saves it."
        ),
    )
    ag.add_argument(
        "--phases",
        metavar="PHASES",
        default="setup,simulation",
        help=(
            "Which phases to describe to the model (default: "
            "setup,simulation). Fewer is a cheaper call and a smaller "
            "space to go wrong in."
        ),
    )
    mode = ag.add_mutually_exclusive_group()
    mode.add_argument(
        "--assisted",
        dest="agent_mode",
        action="store_const",
        const="assisted",
        help=(
            "Draft the study and stop, so you see it before it runs. The "
            "default, and the only one that needs no further decision."
        ),
    )
    mode.add_argument(
        "--autonomous",
        dest="agent_mode",
        action="store_const",
        const="autonomous",
        help=(
            "Draft the study and run it without showing it to you first. "
            "Refused without a cost estimate: approving five days is a "
            "decision, approving an unknown duration is not."
        ),
    )
    mode.add_argument(
        "--unvalidated",
        dest="agent_mode",
        action="store_const",
        const="unvalidated",
        help=(
            "Work outside this schema, so nothing checks the result and "
            "every file says so. Specified and not yet built."
        ),
    )
    ag.set_defaults(agent_mode="assisted")
    ag.add_argument(
        "--budget-hours",
        type=float,
        metavar="HOURS",
        help=(
            "The most GPU time an unattended study may spend. Required by "
            "--autonomous, which runs without showing you the config: a "
            "budget is the only thing left that can stop it. Checked after "
            "setup, which is when the solvated particle count -- and so "
            "the cost -- is first known."
        ),
    )
    ag.add_argument("--host", default="127.0.0.1",
                    help=("Bind address for the panel (default: 127.0.0.1). "
                          "Anything else disables the endpoints that read "
                          "the filesystem or spend an API key, because "
                          "there is no login."))
    ag.add_argument("--port", type=int, default=8765,
                    help="Port to serve the panel on (default: 8765).")
    ag.add_argument("--no-browser", action="store_true",
                    help="Serve the panel without opening a browser.")
    ag.add_argument(
        "--attempts",
        type=int,
        metavar="N",
        default=3,
        help=(
            "How many times it may correct itself before giving up "
            "(default: 3). Each attempt is checked before anything runs, "
            "so they cost seconds rather than GPU time. Three is measured "
            "rather than guessed: claude-sonnet-4-6 took at most two on "
            "the evaluation set with the schema's help text, and at most "
            "three without it."
        ),
    )

    # ---------- remote: other machines, reached over SSH --------------------
    rm = sub.add_parser(
        "remote",
        help="Inspect other machines, reached over SSH, to run studies on.",
        description=(
            "Machines a study can run on, reached with your own ssh and "
            "~/.ssh/config, and the studies sent to them. With no arguments, "
            "lists the machines and jobs as last recorded, without "
            "connecting. With --machine alone, inspects the machine, "
            "records it, and says whether it is ready or how FastMDXplora "
            "would be installed there; inspecting changes nothing. A "
            "study's Config never names a machine: the records are kept "
            "with your other settings, not in any study."
        ),
        formatter_class=_PercentSafeHelp,
    )
    rm.add_argument(
        "--machine",
        metavar="NAME",
        help=("The machine: an alias from ~/.ssh/config, or user@host. "
              "Inspects it and records what it has."),
    )
    actions = rm.add_subparsers(dest="remote_action", metavar="<action>",
                                title="actions")

    def _on_machine(action: argparse.ArgumentParser) -> None:
        # Its own dest: a subparser's default would otherwise overwrite a
        # --machine given before the action word.
        action.add_argument(
            "--machine", dest="action_machine", metavar="NAME",
            help=("The machine. May be left out when only one has been "
                  "inspected."))

    install = actions.add_parser(
        "install",
        help="Install FastMDXplora on a machine, after you confirm the plan.",
        description=(
            "Inspect the machine again, show the install plan `--machine` "
            "prints, and run it once you answer yes. Nothing runs without "
            "that answer, and there is no flag to skip it. Everything goes "
            "inside your own account."),
    )
    _on_machine(install)

    send = actions.add_parser(
        "send",
        help="Run a study's Config on a machine.",
        description=(
            "Check the Config here, copy it and the files it names to the "
            "machine, and start `fastmdx explore` there in the installation "
            "that holds this computer's code. Refused if the machine is not "
            "ready. The job runs on without this terminal."),
        formatter_class=_PercentSafeHelp,
    )
    send.add_argument("-c", "-config", "--config", dest="config",
                      metavar="FILE", required=True,
                      help="The study's YAML Config.")
    _on_machine(send)
    send.add_argument("--output", dest="output_dir", metavar="DIR",
                      help=("Where fetch puts the run on this computer. Its "
                            "folder name is the job's name on both "
                            "computers. Default: the Config's `output`, "
                            "else a new study folder here."))
    send.add_argument("--dry-run", action="store_true",
                      help="Check and show what would be sent, and send nothing.")
    send.add_argument("--force-overwrite", dest="force", action="store_true",
                      help="Replace a job of the same name, here and there.")
    send.add_argument("--partition", metavar="NAME", default="",
                      help="SLURM partition. Ignored on a workstation.")
    send.add_argument("--time", dest="time_limit", metavar="LIMIT", default="",
                      help=("SLURM time limit, as sbatch reads it "
                            "(e.g. 24:00:00). Ignored on a workstation."))

    status = actions.add_parser(
        "status", help="How the jobs sent from here are doing.",
        description="Ask each job's machine how it is doing.")
    status.add_argument("job", nargs="?", metavar="JOB",
                        help="One job. Default: every job not yet finished.")

    fetch = actions.add_parser(
        "fetch", help="Bring a job's results back.",
        description=("Copy a job's run folder back to this computer, leaving "
                     "trajectories and checkpoints on the machine unless "
                     "asked for."))
    fetch.add_argument("job", metavar="JOB")
    fetch.add_argument("--with-trajectory", action="store_true",
                       help="Bring trajectories and checkpoints as well.")

    cancel = actions.add_parser(
        "cancel", help="Stop a job. Its folder on the machine stays.",
        description="Stop a running or waiting job.")
    cancel.add_argument("job", metavar="JOB")

    forget = actions.add_parser(
        "forget",
        help="Remove a machine's record. Nothing on the machine is touched.",
        description=("Remove the record of a machine from this computer. "
                     "Nothing on the machine itself is changed or deleted."),
    )
    forget.add_argument("forget_name", metavar="NAME",
                        help="The machine to forget.")

    ic = sub.add_parser(
        "init-config",
        help="Write a commented YAML config template to edit.",
        description=(
            "Generate a FastMDXplora config template. By default writes a "
            "comprehensive, fully-commented template with every option, its "
            "default, and a description. Edit it and run with "
            "`fastmdx explore --config <file>`."
        ),
    )
    ic.add_argument(
        "-o", "--output",
        dest="config_output",
        metavar="FILE",
        default="fastmdxplora.yml",
        help="Where to write the template (default: fastmdxplora.yml).",
    )
    ic.add_argument(
        "--minimal",
        action="store_true",
        help="Write a short starter template with only the essentials.",
    )
    ic.add_argument(
        "--force-overwrite",
        "--force",
        dest="force",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )

    return parser


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------
def _infer_system_from_output(output_dir: str | None) -> str | None:
    """Best-effort system inference for report/analyze reruns on existing output."""
    if not output_dir:
        return None

    import json

    root = Path(output_dir)
    manifest = root / "manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    system = data.get("system")
    if system:
        return str(system)

    # The topology beside the trajectory where one was written: with
    # `save_selection` leaving the solvent out by default, the prepared
    # system's topology describes more atoms than the trajectory holds.
    _saved = root / "simulation" / "trajectory_topology.pdb"
    topology = (_saved if _saved.is_file()
                else root / "simulation" / "topology.pdb")
    if topology.exists():
        return str(topology)
    return None


def _make_orchestrator(args: argparse.Namespace, *, phase: str | None = None) -> FastMDXplora:
    """Build a single-system orchestrator for the per-phase subcommands.

    The per-phase commands (setup/simulate/analyze/report) operate on one
    system directly, so they bypass the batch layer. `explore` always goes
    through BatchExplorer instead.
    """
    config = getattr(args, "config", None)
    inferred_system = (
        _infer_system_from_output(args.output_dir)
        if phase in {"analyze", "report"} else None
    )
    if not args.system and not config and not inferred_system:
        raise SystemExit(
            "fastmdx: this command requires a system input "
            "(-s / -system / --system) or a --config file."
        )
    # For per-phase commands with a config file, pull the first system out.
    if config and not args.system:
        from fastmdxplora.config import load_config_file
        from fastmdxplora.batch.sweep import normalize_systems

        raw = load_config_file(config)
        systems = normalize_systems(raw.get("systems") or [])
        system = systems[0]["system"]
    else:
        system = args.system or inferred_system
    return FastMDXplora(
        system=system,
        output_dir=args.output_dir,
        verbose=args.verbose,
    )


def _sweep_from_flags(given: list[str]) -> dict[str, list[Any]]:
    """`--sweep AXIS=VALUES`, once per axis, as the config's `sweep` block.

    VALUES is a comma-separated list, or a bracketed one where a value holds
    a comma. Each value is read the way the config file reads it, so 300 is
    a number and true is a truth value, and the axes are checked by the same
    rule the file's are.
    """
    from fastmdxplora.batch.sweep import SweepError, normalize_sweep, values_from_text

    axes: dict[str, list[Any]] = {}
    for item in given:
        axis, sep, values = item.partition("=")
        if not sep or not axis.strip() or not values.strip():
            raise SystemExit(
                "fastmdx: --sweep takes AXIS=VALUES, as in "
                f"--sweep simulation.temperature_K=300,310; got {item!r}.")
        try:
            axes[axis.strip()] = values_from_text(values)
        except SweepError as exc:
            raise SystemExit(f"fastmdx: {exc}") from exc
    try:
        return normalize_sweep(axes)
    except SweepError as exc:
        raise SystemExit(f"fastmdx: {exc}") from exc


def _build_explore_config(args: argparse.Namespace) -> dict[str, Any]:
    """Assemble the config dict that drives an `explore` run.

    Two sources, in priority order (flags win):
      1. A ``--config`` YAML file (if given).
      2. Command-line flags: ``-s/--system`` builds a one-element
         ``systems`` list; per-phase prefixed flags become phase blocks.

    The result always has a ``systems`` list, so it flows through
    BatchExplorer like any other config (a single system is a batch of
    one, written with the flat output layout).
    """
    from fastmdxplora.config import load_config_file

    # Start from the file, if any.
    if getattr(args, "config", None):
        config = load_config_file(args.config)
    else:
        config = {}

    # The execution block, on the same terms: what the flag says beats what
    # the file says, and an unset flag leaves the file alone.
    scheduling = _harvest_phase_options(
        args, _EXECUTION_OPTIONS, dest_prefix="execution")
    if scheduling:
        block = dict(config.get("execution") or {})
        block.update(scheduling)
        config["execution"] = block

    # Harvest per-phase option flags and merge them on top (flags win).
    for phase, (opts, _prefix) in _PHASE_SPEC.items():
        harvested = _harvest_phase_options(args, opts, dest_prefix=phase)
        if phase == "analyze":
            harvested = _normalize_analysis_options(harvested)
        if harvested:
            orch_phase = _PHASE_TO_ORCH[phase]
            block = dict(config.get(orch_phase, {}))
            block.update(harvested)
            config[orch_phase] = block

    # The flat --simulate-plumed-script flag maps to the nested `plumed` dict.
    sim_block = config.get("simulation")
    if isinstance(sim_block, dict) and "plumed_script" in sim_block:
        script = sim_block.pop("plumed_script")
        if script:
            sim_block["plumed"] = {"enabled": True, "script": script}

    # -s/--system builds (or replaces) a one-element systems list.
    if args.system:
        config["systems"] = [{"id": "s1", "system": args.system}]
    if getattr(args, "sweep", None):
        config["sweep"] = {**(config.get("sweep") or {}), **_sweep_from_flags(args.sweep)}

    # Top-level scalars from flags.
    if args.output_dir:
        config["output"] = args.output_dir
    if getattr(args, "verbose", False):
        config["verbose"] = True
    from fastmdxplora.config.loader import STUDY_LEVEL_KEYS

    for name in STUDY_LEVEL_KEYS:
        if getattr(args, name, None) is not None:
            config[name] = getattr(args, name)

    # The presenter exists before the arguments are read, so it is told
    # afterwards. A config file's `explain` is honoured where the flag was
    # not given.
    from fastmdxplora.utils.presenter import set_explain

    wanted = getattr(args, "explain", True)
    if wanted and config.get("explain") is False:
        wanted = False
    set_explain(wanted)
    # And into the study, as --verbose goes: the flag was acted on and then
    # dropped, so a command written with --no-explain built a config that
    # explained, and the resolved config recorded that it did.
    if not wanted:
        config["explain"] = False
    if args.include:
        config["include_phase"] = args.include
    if args.exclude:
        config["exclude_phase"] = args.exclude

    return config


def _dashboard_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "dashboard", False))


def _enable_dashboard_telemetry(
    config: dict[str, Any], args: argparse.Namespace | None = None
) -> None:
    simulation = dict(config.get("simulation", {}))
    simulation["live_telemetry"] = True
    if args is not None and getattr(args, "dashboard_frame_interval", None) is not None:
        simulation["telemetry_interval"] = int(args.dashboard_frame_interval)
    config["simulation"] = simulation


def _resolve_dashboard_output_dir(args: argparse.Namespace, config: dict[str, Any] | None = None) -> Path:
    raw_output = getattr(args, "output_dir", None)
    if not raw_output and config:
        raw_output = config.get("output")
    if not raw_output:
        from fastmdxplora.naming import default_output_name, system_of

        raw_output = default_output_name(system_of(config))
    return Path(raw_output).expanduser().resolve()


def _start_dashboard_for_command(args: argparse.Namespace, output_dir: Path):
    # The orchestrator uses this process-local marker to publish setup,
    # analysis, and report phase transitions to the same live timeline as
    # the OpenMM simulation sub-stages.
    os.environ["FASTMDX_DASHBOARD_ACTIVE"] = "1"
    os.environ["FASTMDX_DASHBOARD_OUTPUT"] = str(output_dir)

    from fastmdxplora.gui.server import (
        DashboardConfig,
        start_dashboard_session,
    )

    config = DashboardConfig(
        ligand_resname=getattr(args, "dashboard_ligand_resname", None),
        binding_pocket_cutoff_A=float(
            getattr(args, "dashboard_binding_pocket_cutoff_A", 5.0) or 5.0
        ),
        max_browser_frames=int(
            getattr(args, "dashboard_max_playback_frames", 200) or 200
        ),
        refresh_seconds=float(
            getattr(args, "dashboard_refresh_seconds", 3.0) or 3.0
        ),
    )
    session = start_dashboard_session(
        output=output_dir,
        host=args.dashboard_host,
        port=args.dashboard_port,
        config=config,
    )
    print(f"FastMDXplora GUI running at: {session.url}")
    if session.port_was_changed:
        print(
            f"Requested port {session.requested_port} was busy, "
            f"so FastMDXplora used {session.port}."
        )
    if args.dashboard_host == "0.0.0.0":
        print(
            "Warning: dashboard is bound to 0.0.0.0 and may be visible on your network."
        )
        print("Use --dashboard-host 127.0.0.1 for local-only access.")
    print(f"Watching output folder: {output_dir}")
    print("Open this URL in your browser to monitor the run.")
    if args.dashboard_stop_on_complete:
        print("The GUI stops automatically when the exploration completes.")
    else:
        print("Press Ctrl+C to stop the GUI after the exploration completes.")
    print()
    return session


def _finish_dashboard_for_command(session, args: argparse.Namespace) -> None:
    if session is None:
        return
    if args.dashboard_stop_on_complete:
        session.stop()
        return
    print()
    print(f"Exploration complete. The GUI is still running at: {session.url}")
    print("Press Ctrl+C to stop the GUI.")
    try:
        session.wait_forever()
    except KeyboardInterrupt:
        pass
    finally:
        session.stop()


def _cmd_explore(args: argparse.Namespace) -> int:
    from fastmdxplora import FastMDXplora

    if args.include and args.exclude:
        print("fastmdx: --include-phase and --exclude-phase are mutually "
                  "exclusive.", file=sys.stderr)
        return 2

    config = _build_explore_config(args)

    # `simulation.resume_from` naming a STUDY DIRECTORY is a continuation of
    # that study: its next segment, the join, and the analyses over the
    # joined trajectory. Naming a checkpoint file is the raw mechanism and
    # runs as an ordinary simulation, which is what a segmented campaign
    # wants. The study being continued supplies the systems and the phases,
    # which is why the usual "explore requires a system" check comes after
    # this rather than before it.
    simulation = config.get("simulation") or {}
    resume_from = simulation.get("resume_from")
    continuing = bool(resume_from) and Path(str(resume_from)).is_dir()
    if continuing and not getattr(args, "dry_run", False):
        from fastmdxplora.simulation.resume import extend_study

        answer = extend_study(resume_from,
                              total_ns=simulation.get("duration_ns"),
                              more_ns=simulation.get("extra_ns"))
        if not answer.get("ok"):
            print(f"fastmdx: {answer.get('error')}", file=sys.stderr)
            return 1
        joined = answer.get("joined") or {}
        print(f"Ran {Path(answer['segment']).name} and joined segments "
              f"{joined.get('segments') or '?'}.")
        if answer.get("analysed"):
            print(f"Analyses and report rerun over {answer['trajectory']}.")
        return 0

    if not config.get("systems"):
        print(
            "fastmdx: explore requires a system — pass -s/--system PATH, a "
            "--config file with a `systems:` list, or "
            "`simulation.resume_from` naming a study to carry on from.",
            file=sys.stderr,
        )
        return 2

    # --no-report removes report from the plan via exclude (unless the user
    # already constrained phases with include).
    if getattr(args, "no_report", False) and not config.get("include_phase"):
        existing = config.get("exclude_phase") or []
        if "report" not in existing:
            config["exclude_phase"] = [*existing, "report"]

    if _dashboard_requested(args):
        _enable_dashboard_telemetry(config, args)
    dashboard_output_dir: Path | None = None
    if _dashboard_requested(args):
        dashboard_output_dir = _resolve_dashboard_output_dir(args, config)
        config["output"] = str(dashboard_output_dir)

    # A study carrying a budget runs in stages: setup, then a price, then
    # the rest only if it fits. That is what `fastmdx agent --autonomous`
    # does in-process; a config the GUI hands to `explore --config` had no
    # way to ask for it until the budget became a config key. One reader
    # for the ceiling, whichever door the study came through.
    budget = config.get("budget_hours")
    if budget is not None and not getattr(args, "dry_run", False):
        from fastmdxplora.agent import run_in_stages

        from fastmdxplora.naming import default_output_name, system_of

        output = Path(config.get("output") or args.output_dir
                      or default_output_name(system_of(config)))
        staged = run_in_stages(config, output, budget_hours=float(budget))
        for note in staged.notes:
            print(f"  {note}")
        if staged.refusal is not None:
            print(f"\n  \u2717 {staged.refusal.message}")
            if staged.setup_done:
                print("\nSetup's output is kept, so a shorter study can reuse it.")
            return 2
        return 0

    fmdx = FastMDXplora(
        config_data=config,
        output_dir=args.output_dir,
        verbose=args.verbose,
    )
    session = None
    if _dashboard_requested(args) and not getattr(args, "dry_run", False):
        session = _start_dashboard_for_command(args, dashboard_output_dir)
    try:
        results = fmdx.explore(
            dry_run=getattr(args, "dry_run", False),
            force=getattr(args, "force", False),
        )
    except KeyboardInterrupt:
        if session is not None:
            session.stop()
        return 130
    except Exception:
        if session is not None:
            session.stop()
        raise

    # Dry run: the plan was printed; nothing executed.
    if getattr(args, "dry_run", False):
        return 0

    # Single run -> flat layout; point at the project manifest.
    if len(results) == 1:
        print()
        print(f"Project output: {fmdx.output_dir}")
        print(f"Manifest:       {fmdx.output_dir / 'manifest.json'}")
    rc = 0 if all(r.status == "ok" for r in results) else 1
    _finish_dashboard_for_command(session, args)
    return rc


def _cmd_phase(phase: str, args: argparse.Namespace) -> int:
    fmdx = _make_orchestrator(args, phase=phase)
    opts_list, _ = _PHASE_SPEC[phase]
    kwargs = _harvest_phase_options(args, opts_list)
    if phase == "analyze":
        kwargs = _normalize_analysis_options(kwargs)
    if _dashboard_requested(args) and phase == "simulate":
        kwargs["live_telemetry"] = True
        # Forward dashboard knobs when running live; ignored if the user
        # did not opt in to live telemetry.
        if getattr(args, "dashboard_frame_interval", None) is not None:
            kwargs["telemetry_interval"] = int(args.dashboard_frame_interval)
        if getattr(args, "dashboard_refresh_seconds", None) is not None:
            # The same value is embedded into the served dashboard HTML.
            print(
                f"  dashboard polling: every {args.dashboard_refresh_seconds}s"
            )

    method = {
        "setup":    fmdx.setup,
        "simulate": fmdx.simulate,
        "analyze":  fmdx.analyze,
        "report":   fmdx.report,
    }[phase]

    # Bracket the single-phase invocation with presenter output so the
    # user sees the same visual structure as during `fastmdx explore`.
    session = None
    if _dashboard_requested(args):
        output_dir = _resolve_dashboard_output_dir(args)
        if not getattr(args, "output_dir", None):
            output_dir = Path(fmdx.output_dir).expanduser().resolve()
        session = _start_dashboard_for_command(args, output_dir)
    try:
        fmdx._presenter.phase_start(phase)  # noqa: SLF001 -- internal hook
        result = method(**kwargs)
        fmdx.results.append(result)
        fmdx._presenter.phase_end(phase, status=result.status)
        fmdx._write_manifest()  # noqa: SLF001 -- single-phase still records
    except KeyboardInterrupt:
        if session is not None:
            session.stop()
        return 130
    except Exception:
        if session is not None:
            session.stop()
        raise
    if result.status != "ok" and result.message:
        # `explore` reports this through the orchestrator loop; a single-phase
        # run has no such loop, so without this the reason for a refusal or a
        # failure is discarded and the user sees only that it happened.
        logger.error("Phase '%s' failed: %s", phase, result.message)
    print()
    print(f"Project output: {fmdx.output_dir}")
    rc = 0 if result.status == "ok" else 1
    _finish_dashboard_for_command(session, args)
    return rc


#: What each phase reaches for, and where to get it. Grouped because a
#: missing backend matters only for what it is needed for: a trajectory
#: analysis does not care that OpenMM is absent, and saying so unqualified
#: reads as a broken install.
_BACKENDS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    ("to run a simulation", (
        ("OpenMM", "openmm", "conda install -c conda-forge openmm"),
        ("PDBFixer", "pdbfixer", "conda install -c conda-forge pdbfixer"),
    )),
    ("to prepare a ligand", (
        # openff-toolkit has no PyPI distribution, so no pip command reaches
        # it whatever extra is named. Saying "pip install the ligand extra"
        # here sent people to something that could not work.
        ("OpenFF toolkit", "openff.toolkit",
         "conda install -c conda-forge openff-toolkit"),
        ("OpenMM force fields", "openmmforcefields",
         "conda install -c conda-forge openmmforcefields"),
        ("RDKit", "rdkit", "conda install -c conda-forge rdkit"),
        ("PROPKA", "propka", "conda install -c conda-forge propka"),
    )),
    ("to write the report as a PDF", (
        ("WeasyPrint", "weasyprint", "conda install -c conda-forge weasyprint"),
        ("Markdown", "markdown", "conda install -c conda-forge markdown"),
    )),
    ("for optional extras", (
        ("UMAP", "umap", "conda install -c conda-forge umap-learn"),
        ("PLUMED", "openmmplumed", "conda install -c conda-forge openmm-plumed"),
    )),
)


def _probe_backends(import_names: tuple[str, ...]) -> dict[str, tuple[str, str]]:
    """Whether each backend will load, tested in a process of its own.

    Importing is the only honest test -- a package can be installed and still
    fail to load -- but the failure is not always quiet or catchable. Importing
    WeasyPrint without Pango raises from the dynamic loader, and prints five
    lines about its installation guide on the way. Redirecting Python's stderr
    did not stop them, and neither did redirecting the file descriptor: the
    first attempt at this passed its test only because the test faked the
    message with a print, which is the assumption being checked rather than
    the behaviour.

    A subprocess ends the argument. Its output is captured whatever writes it
    and however far down, and a backend that fails hard cannot take this
    command with it. One process probes them all, so the cost is a single
    interpreter start rather than one per backend.

    It reports as it goes: each name before it is imported, each answer
    after. A backend that ends the interpreter -- a C extension built against
    the wrong ABI segfaults on import -- is then known by name, reported as
    broken with how the interpreter ended, and the names after it are probed
    in a fresh process. The whole answer used to be written once at the end,
    so a crash lost it, and the fallback then imported every backend in this
    process: the one that had just killed an interpreter killed the command,
    silently, with its own exit code. A hang is treated the same way.
    Checking in this process is kept for when no subprocess can be started at
    all, which is what it was written for.
    """
    import json
    import subprocess
    import sys
    import tempfile

    script = (
        "import json, sys\n"
        "with open(sys.argv[2], 'a', encoding='utf-8') as out:\n"
        "    for name in json.loads(sys.argv[1]):\n"
        "        out.write(json.dumps({'trying': name}) + '\\n')\n"
        "        out.flush()\n"
        "        try:\n"
        "            __import__(name)\n"
        "            row = [name, 'installed', '']\n"
        "        except ImportError:\n"
        "            row = [name, 'missing', '']\n"
        "        except BaseException as exc:\n"
        "            row = [name, 'broken', str(exc).split(':')[0][:60]]\n"
        "        out.write(json.dumps({'result': row}) + '\\n')\n"
        "        out.flush()\n"
    )
    timeout_s = 120
    found: dict[str, tuple[str, str]] = {}
    remaining = list(import_names)
    detail = ""
    try:
        with tempfile.TemporaryDirectory() as work:
            for attempt in range(len(import_names) + 1):
                if not remaining:
                    break
                answer = Path(work) / f"backends-{attempt}.jsonl"
                try:
                    finished = subprocess.run(
                        [sys.executable, "-c", script, json.dumps(remaining), str(answer)],
                        capture_output=True, text=True, timeout=timeout_s, check=False,
                    )
                    ended = _how_the_probe_ended(finished.returncode)
                except subprocess.TimeoutExpired:
                    ended = f"did not finish loading within {timeout_s} s"
                trying = None
                lines = answer.read_text(encoding="utf-8").splitlines() if answer.is_file() else []
                for line in lines:
                    record = json.loads(line)
                    if "trying" in record:
                        trying = record["trying"]
                    else:
                        name, state, why = record["result"]
                        found[name] = (state, why)
                        trying = None
                if trying is None:
                    break
                found[trying] = ("broken", ended)
                remaining = [name for name in remaining if name not in found]
        if found or not import_names:
            # Anything a finished process never reached was not reached
            # because an earlier backend ended it past recovery; say so
            # rather than leave a gap the caller would read as a KeyError.
            return {name: found.get(name, ("broken", "not reached by the check"))
                    for name in import_names}
        detail = "the check answered nothing"
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"[:80]
    print(f"  (checked in this process: {detail})")
    checked: dict[str, tuple[str, str]] = {}
    for name in import_names:
        try:
            __import__(name)
            checked[name] = ("installed", "")
        except ImportError:
            checked[name] = ("missing", "")
        except BaseException as exc:  # noqa: BLE001 - any load failure counts
            checked[name] = ("broken", str(exc).split(":")[0][:60])
    return checked


def _how_the_probe_ended(returncode: int) -> str:
    """What a probe that stopped mid-import said on the way out."""
    if returncode < 0:
        import signal

        try:
            name = signal.Signals(-returncode).name
        except ValueError:
            name = f"signal {-returncode}"
        return f"crashed the interpreter on import ({name})"
    return f"ended the interpreter on import (exit code {returncode})"


#: What a phase cannot run without, and what it does less of without the rest.
#: Named by the group in ``_BACKENDS`` that already says what those packages
#: are for, so a backend added to a group is one the phases needing that group
#: are reported as needing.
#:
#: The distinction matters: setup prepares a protein with OpenMM and PDBFixer
#: alone, and only a *ligand* needs the chemistry stack. Reporting setup as
#: unavailable there would send somebody installing four packages for a run
#: that would have worked.
_PHASE_NEEDS: dict[str, tuple[tuple[str, ...], tuple[tuple[str, str], ...]]] = {
    "setup": (("to run a simulation",),
              (("to prepare a ligand", "proteins only"),)),
    "simulation": (("to run a simulation",), ()),
    # Analysis runs on a PyPI install alone, which is what the PyPI package is
    # for. The report does too; the PDF is one format of several.
    "analysis": ((), ()),
    "report": ((), (("to write the report as a PDF", "no PDF"),)),
}


def _backends_in(group_names: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """The (display name, import name) of every backend in those groups."""
    by_group = {group: backends for group, backends in _BACKENDS}
    return tuple(
        (display, import_name)
        for group in group_names
        for display, import_name, _hint in by_group.get(group, ())
    )


def _phase_status(phase: str, probed: dict[str, tuple[str, str]]) -> str:
    """Whether the phase will run here, and what it will not do if it does."""
    required, conditional = _PHASE_NEEDS[phase]

    def absent(groups: tuple[str, ...]) -> list[str]:
        # "installed" is the word the probe uses; comparing against any other
        # reports every backend missing, which is the same contradiction
        # pointing the other way.
        return [display for display, import_name in _backends_in(groups)
                if probed[import_name][0] != "installed"]

    missing = absent(required)
    if missing:
        return "needs " + ", ".join(missing)
    limits = [note for group, note in conditional if absent((group,))]
    return "ready" + (f" ({', '.join(limits)})" if limits else "")


def _cmd_select(args: argparse.Namespace) -> int:
    """Show what a selection matches, before a run depends on it.

    A selection that matches no atoms is refused when a study starts. A
    selection that matches the *wrong* atoms is not an error and cannot be
    made one -- `resid 189` and `resSeq 189` are both valid, both non-empty,
    and name different residues in any structure that is not numbered from
    one without gaps. This prints what the expression actually resolves to.
    """
    import mdtraj as md

    structure = Path(args.structure)
    if not structure.is_file():
        print(f"fastmdx: no such structure: {structure}", file=sys.stderr)
        return 2

    try:
        topology = md.load(str(structure)).topology
    except Exception as exc:  # mdtraj raises a variety of types
        print(f"fastmdx: could not read {structure}: {exc}", file=sys.stderr)
        return 2

    try:
        indices = topology.select(args.expression)
    except Exception as exc:
        print(f"fastmdx: {args.expression!r} is not a valid selection: {exc}",
              file=sys.stderr)
        return 2

    print(f"{args.expression!r} against {structure.name}")
    print(f"  {len(indices)} of {topology.n_atoms} atoms")

    if len(indices) == 0:
        # Said here rather than left for the reader to infer, because an
        # empty match is the one case where the next step is obvious.
        print()
        print("  Nothing matched. If you are naming residues from a paper, a")
        print("  figure or a PDB entry, the number in them is `resSeq`;")
        print("  `resid` counts residues from zero in file order.")
        return 1

    residues = []
    for index in indices:
        residue = topology.atom(int(index)).residue
        if not residues or residues[-1] is not residue:
            if residue not in residues:
                residues.append(residue)

    limit = None if args.limit == 0 else max(1, int(args.limit))
    if args.atoms:
        shown = [f"{topology.atom(int(i))}" for i in indices]
        label = "atoms"
    else:
        shown = [f"{r.name}{r.resSeq}" for r in residues]
        label = f"{len(residues)} residues"
    print(f"  {label}: ", end="")
    if limit is not None and len(shown) > limit:
        print(", ".join(shown[:limit]) + f", ... ({len(shown) - limit} more)")
    else:
        print(", ".join(shown))
    return 0


def _info_record() -> dict[str, Any]:
    """What `fastmdx info` reports, as data.

    One collection behind both forms, so the text a person reads and the
    JSON a program reads cannot disagree about what is installed.
    """
    import platform

    from fastmdxplora.provenance import source_provenance

    # A phase is not available because its module imports. It said so anyway:
    # a pip install reported setup and simulation "available" three lines
    # above OpenMM and PDBFixer "missing", which is the same screen
    # contradicting itself -- and the same defect this command was fixed for
    # one block down.
    probed = _probe_backends(tuple(
        import_name
        for _group, backends in _BACKENDS
        for _display, import_name, _hint in backends
    ))
    phases: dict[str, str] = {}
    for name in ("setup", "simulation", "analysis", "report"):
        try:
            module = __import__(f"fastmdxplora.{name}", fromlist=["run"])
            status = ("missing run()"
                      if not callable(getattr(module, "run", None))
                      else _phase_status(name, probed))
        except Exception as exc:  # noqa: BLE001
            status = f"import error: {exc}"
        phases[name] = status
    backends: dict[str, dict[str, str]] = {}
    for group, members in _BACKENDS:
        for display_name, import_name, install_hint in members:
            state, detail = probed[import_name]
            backends[import_name] = {
                "name": display_name, "needed": group, "state": state,
                "detail": detail, "install": install_hint,
            }
    return {
        "version": __version__,
        "source": source_provenance(),
        "python": platform.python_version(),
        "phases": phases,
        "backends": backends,
    }


def _cmd_info(args: argparse.Namespace | None = None) -> int:
    record = _info_record()
    if getattr(args, "json", False):
        import json

        print(json.dumps(record, indent=2))
        return 0
    print("FastMDXplora")
    print(f"  version: {__version__}")
    print(f"  Authors: {__author__}")
    print(f"  DOI:     {__doi__}")
    print()
    print("Molecular Dynamics Phases:")
    for name, status in record["phases"].items():
        print(f"  {name:<11} {status}")
    print()
    # Everything a phase reaches for at runtime, grouped by what it is for.
    # This listed two of six, so a PyPI install reported both of them present
    # and said nothing about the toolkit a protein-ligand setup needs -- which
    # is the one question this command exists to answer.
    print("Backends:")
    for group, backends in _BACKENDS:
        print(f"  {group}")
        for display_name, import_name, _hint in backends:
            entry = record["backends"][import_name]
            remedy = {
                "missing": entry["install"],
                "broken": f"installed but will not load ({entry['detail']})",
            }.get(entry["state"], entry["detail"])
            print(f"    {display_name:<22} {entry['state']:<10} {remedy}".rstrip())
    print()
    print(f"Citation: {__citation__}")
    return 0


def _remote_machine(args: argparse.Namespace) -> str:
    """The machine an action is about: named, or the only one there is."""
    from fastmdxplora.remote import machine_names
    from fastmdxplora.remote.machines import UnknownMachine

    named = getattr(args, "action_machine", None) or args.machine
    if named:
        return named
    known = machine_names()
    if len(known) == 1:
        return known[0]
    raise UnknownMachine(
        "Name the machine with --machine"
        + (f": {', '.join(known)} have been inspected." if known
           else ". None has been inspected yet: `fastmdx remote --machine <alias>`."),
        given="", permitted=known)


def _cmd_remote(args: argparse.Namespace) -> int:
    """`fastmdx remote`: machines, and the studies sent to them."""
    from fastmdxplora.remote import (
        forget_machine,
        inspect_machine,
        load_machine,
        machine_names,
        machines_dir,
        readiness,
        this_code,
    )
    from fastmdxplora.remote.describe import (
        describe_machine,
        describe_plan,
        describe_unloadable,
        overview,
        plan_for,
    )

    action = args.remote_action
    if action == "forget":
        forget_machine(args.forget_name)
        print(f"  \u2713 Forgot {args.forget_name}. Nothing on the machine "
              "was changed.")
        return 0
    if action == "install":
        return _remote_install(_remote_machine(args))
    if action == "send":
        return _remote_send(args, _remote_machine(args))
    if action in ("status", "fetch", "cancel"):
        return _remote_job(args)

    if args.machine:
        code = this_code()
        print(f"Inspecting {args.machine} over ssh...")
        machine = inspect_machine(args.machine, code=code)
        print()
        for line in describe_machine(machine, code):
            print(line)
        print()
        verdict = readiness(machine, code)
        if verdict.ready:
            print(f"  \u2713 Ready: {verdict.summary}.")
        else:
            print(f"  \u2717 Not ready: {verdict.summary}.")
            print()
            # An installation that holds the code and cannot load something
            # needs that something, not a second installation beside it;
            # plan_for makes that choice, and says it first.
            plan = plan_for(machine, code)
            for line in describe_unloadable(machine, verdict)[:-1]:
                print(line)
            for line in describe_plan(plan, machine.name):
                print(line)
            if plan.possible:
                print("Or have FastMDXplora run them, after you confirm:")
                print(f"  fastmdx remote install --machine {machine.name}")
        print()
        print(f"Recorded in {machines_dir() / (machine.name + '.json')}")
        return 0

    from fastmdxplora.remote.jobs import job_names, load_job
    from fastmdxplora.remote.send import job_line

    machines = [load_machine(name) for name in machine_names()]
    for line in overview(machines, this_code()):
        print(line)
    jobs = [load_job(name) for name in job_names()]
    if jobs:
        print()
        print("Jobs (as last checked; `fastmdx remote status` asks again)")
        for job in jobs:
            print(job_line(job))
    return 0


def _remote_install(name: str) -> int:
    from fastmdxplora.remote.describe import describe_plan
    from fastmdxplora.remote.installer import ask_a_person, install

    def confirm(plan) -> bool:
        print()
        for line in describe_plan(plan, name)[:-3]:
            print(line)
        return ask_a_person(plan)

    print(f"Inspecting {name} over ssh...")
    outcome = install(name, confirm=confirm)
    if outcome.ran == 0 and outcome.verdict is not None and outcome.verdict.ready:
        print(f"  \u2713 Already ready: {outcome.verdict.summary}.")
        return 0
    if outcome.failed_step:
        print(f"\n  \u2717 Stopped at: {outcome.failed_step}")
        print("  Steps before it were left as they are. See the output above.")
        return 1
    verdict = outcome.verdict
    if verdict is not None and verdict.ready:
        print(f"\n  \u2713 Ready: {verdict.summary}.")
        return 0
    print(f"\n  \u2717 Installed, and still not ready: "
          f"{verdict.summary if verdict else 'not re-inspected'}.")
    return 1


def _remote_send(args: argparse.Namespace, name: str) -> int:
    from fastmdxplora.remote.send import describe_sending, prepare, send

    print(f"Checking the Config, and {name} over ssh...")
    sending = prepare(args.config, name, output=args.output_dir,
                      force=args.force, partition=args.partition,
                      time_limit=args.time_limit)
    print()
    for line in describe_sending(sending):
        print(line)
    if args.dry_run:
        print("\nDry run: nothing was sent.")
        return 0
    job = send(sending)
    print(f"\n  \u2713 Started {job.name} on {job.machine} "
          f"({'SLURM job' if job.scheduler == 'slurm' else 'process'} {job.handle}).")
    print(f"  fastmdx remote status {job.name}")
    print(f"  fastmdx remote fetch {job.name}")
    return 0


def _remote_job(args: argparse.Namespace) -> int:
    from fastmdxplora.remote.jobs import FINISHED, job_names, load_job
    from fastmdxplora.remote.send import cancel, fetch, job_line, status

    action = args.remote_action
    if action == "status":
        names = [args.job] if args.job else [
            n for n in job_names() if load_job(n).state not in FINISHED]
        if not names:
            print("No jobs still running. `fastmdx remote` lists them all.")
            return 0
        for job_name in names:
            job = status(job_name)
            print(job_line(job))
            for line in job.extra.get("log_tail", [])[-4:]:
                print(f"      {line}")
        return 0
    if action == "cancel":
        job = cancel(args.job)
        print(f"  \u2713 {job.name}: {job.state}. Its folder on {job.machine} "
              f"is left at {job.remote_dir}.")
        return 0
    job, warnings = fetch(args.job, with_trajectory=args.with_trajectory)
    print(f"  \u2713 Fetched {job.name} to {job.local_output}")
    for warning in warnings:
        print(f"  {warning}")
    print(f"  Open it with: fastmdx gui --output {job.local_output}")
    return 0


def _cmd_init_config(args: argparse.Namespace) -> int:
    from fastmdxplora.config import generate_template

    out_path = Path(args.config_output)
    if out_path.exists() and not args.force:
        print(
            f"fastmdx: {out_path} already exists. Use --force-overwrite, "
            f"or -o to choose a different path.",
            file=sys.stderr,
        )
        return 2

    text = generate_template(minimal=args.minimal)
    out_path.write_text(text, encoding="utf-8")
    kind = "minimal" if args.minimal else "comprehensive"
    print(f"Wrote {kind} config template to {out_path}")
    print(f"Edit it, then run:  fastmdx explore --config {out_path}")
    return 0


def _cmd_gui(args: argparse.Namespace, *, panel: str = "") -> int:
    """Serve the full GUI: study builder, exploration, telemetry, and viewer.

    `panel` names where to land. `fastmdx agent` with no request passes
    "agent" and gets the same server on the same port, opened at that
    section -- a note in the URL fragment the page reads on load, not a
    second application. Two commands that started two browsers would be two
    things to learn for one thing to use.
    """
    from fastmdxplora.gui.server import DashboardConfig, serve_dashboard

    # Without --output there is no run to watch: the working directory is
    # merely where the command was typed. Treating it as an active run made
    # the GUI open on the overview of that run -- an overview of nothing.
    watching_a_run = bool(getattr(args, "output", None))
    output = Path(args.output) if watching_a_run else Path.cwd()
    config = DashboardConfig(
        ligand_resname=getattr(args, "ligand_resname", None),
        binding_pocket_cutoff_A=float(
            getattr(args, "binding_pocket_cutoff_A", 5.0) or 5.0
        ),
    )
    # Open the browser only once the server answers. It was opened first,
    # and on a completed-run folder -- more to read before the first
    # response -- the browser reached the port before it was listening and
    # showed "unable to connect" until a refresh. A short poll closes the
    # race; the browser still opens best-effort.
    on_ready = None
    if not getattr(args, "no_browser", False):
        import webbrowser

        fragment = f"#{panel}" if panel else ""

        def on_ready(url: str) -> None:
            try:
                webbrowser.open(f"{url}{fragment}", new=2)
            except Exception:  # noqa: BLE001 - best effort
                pass

    serve_dashboard(
        output=output,
        host=args.host,
        port=args.port,
        config=config,
        home_mode=not watching_a_run,
        on_ready=on_ready,
    )
    return 0


def _startup_dashboard_details(argv: Sequence[str]) -> tuple[str, bool]:
    """Resolve the GUI address shown by the startup wordmark."""
    host = "127.0.0.1"
    port = "8765"
    enabled = (
        "gui" in argv
        or "--dashboard" in argv
        or "--live-dashboard" in argv
        or os.getenv("FASTMDX_DASHBOARD_ACTIVE") == "1"
    )

    for index, token in enumerate(argv):
        if token in {"--dashboard-host", "--host"} and index + 1 < len(argv):
            host = str(argv[index + 1])
        elif token.startswith("--dashboard-host=") or token.startswith("--host="):
            host = token.split("=", 1)[1]

        if token in {"--dashboard-port", "--port"} and index + 1 < len(argv):
            port = str(argv[index + 1])
        elif token.startswith("--dashboard-port=") or token.startswith("--port="):
            port = token.split("=", 1)[1]

    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"

    url = os.getenv("FASTMDX_DASHBOARD_URL") or f"http://{host}:{port}"
    return url, enabled


def _cmd_dashboard_home() -> int:
    """Start the dashboard home screen for an empty CLI invocation."""
    from fastmdxplora.gui.server import serve_dashboard

    serve_dashboard(
        output=Path.cwd(),
        host="127.0.0.1",
        port=8765,
    )
    return 0


def _run_agent(args: Any) -> int:
    """`fastmdx agent` -- choose a model, or write a study from a sentence."""
    from pathlib import Path as _Path

    from fastmdxplora.agent import (
        completion_for, describe_choice, propose_config,
    )
    from fastmdxplora.refusals import StudyError, refusal_of

    request = args.request

    if request == "set":
        return _choose_model()

    if args.request_file:
        try:
            request = _Path(args.request_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            print(f"Could not read {args.request_file}: {exc}")
            return 1

    if not request:
        # No request is an invitation to converse, and conversing wants a
        # window. The same server `fastmdx gui` starts, landing on the
        # agent panel.
        print(describe_choice())
        print("\nOpening the agent panel. Ctrl-C to stop the server.")
        gui_args = argparse.Namespace(
            output=getattr(args, "agent_output", None),
            host=args.host,
            port=args.port,
            no_browser=args.no_browser,
            ligand_resname=None,
            binding_pocket_cutoff_A=5.0,
        )
        # Through the module attribute rather than the local name, so a
        # test can substitute it. `_cmd_gui` serves until interrupted, and
        # a test that called it for real would hang rather than fail.
        import sys as _sys

        return _sys.modules[__name__]._cmd_gui(gui_args, panel="agent")

    try:
        complete = completion_for()
    except StudyError as exc:
        print(refusal_of(exc).message)
        return 1

    if args.agent_mode == "unvalidated":
        # The mode itself -- an agent writing code outside the schema --
        # is still to come. What exists now is the marking that makes it
        # safe to offer, so the config records it and every figure from an
        # unchecked phase carries it. Writing a config in this mode is
        # therefore honest: it says what the study will be, and the
        # marking will hold whatever produces the output.
        print(
            "Writing a config marked `unvalidated`. Work in this mode goes "
            "outside the schema, so nothing checks the method and every "
            "figure from an unchecked phase is stamped. The agent cannot "
            "yet write code itself; the mode is recorded, and the marking "
            "holds for anything run under it."
        )

    print("Writing a config...")
    try:
        proposal = propose_config(
            request, complete,
            phases=[p.strip() for p in args.phases.split(",") if p.strip()],
            max_cycles=int(args.attempts))
    except StudyError as exc:
        print(refusal_of(exc).message)
        return 1

    # The corrections, shown rather than hidden. They are the only visible
    # sign that anything checked the config, and the count is worth seeing.
    for attempt in proposal.attempts:
        if attempt.refusal is not None:
            print(f"  ✗ {attempt.refusal.message}")

    if proposal.question:
        # Not a failure. The request is short of something only the person
        # can supply -- a structure, most often -- and guessing one would
        # produce a study of the wrong molecule that validates perfectly.
        print(f"\n  ? {proposal.question}")
        print("\nAdd that to the request and try again.")
        return 2
    if not proposal.accepted:
        print(f"\nGave up after {proposal.cycles} attempt(s). The last "
              "refusal is above.")
        return 1

    import yaml

    config = dict(proposal.config)
    config["agent"] = args.agent_mode
    # And which model, not only that one was used. `agent: assisted` says a
    # model was involved; this says which, so the record identifies the
    # software rather than the category.
    from fastmdxplora.agent import load_choice

    chosen = load_choice()
    if chosen is not None:
        config["agent_model"] = f"{chosen.provider}/{chosen.model}"
    text = yaml.safe_dump(config, sort_keys=False)
    print(f"  ✓ Accepted after {proposal.cycles} attempt(s)\n")
    print(text)
    if args.agent_mode == "autonomous":
        if args.budget_hours is None:
            print(
                "\n`--autonomous` runs the study without showing it to "
                "you, so a budget is the only thing left that can stop "
                "it. Give one with --budget-hours."
            )
            return 1
        return _run_staged(args, config)

    if args.agent_output:
        _Path(args.agent_output).write_text(text, encoding="utf-8")
        print(f"Written to {args.agent_output}")
        print(f"Run it with: fastmdx explore -config {args.agent_output}")
    else:
        print("Save it with -o FILE, then run "
              "`fastmdx explore -config FILE`.")
    return 0



def _run_staged(args: Any, config: dict) -> int:
    """`--autonomous`: setup, price it, then the rest if it fits."""
    from pathlib import Path as _Path

    from fastmdxplora.agent import run_in_stages

    from fastmdxplora.naming import default_output_name

    output = _Path(args.agent_output or default_output_name())
    print(f"\nRunning setup, which settles the particle count "
          f"({output})...")
    staged = run_in_stages(config, output,
                           budget_hours=float(args.budget_hours))

    for note in staged.notes:
        print(f"  {note}")

    if staged.refusal is not None:
        print(f"\n  ✗ {staged.refusal.message}")
        if staged.setup_done:
            print("\nSetup's output is kept, so a shorter study can reuse "
                  "it with `setup_from`.")
        return 1

    print("\n  ✓ Ran within the budget.")
    return 0


def _choose_model() -> int:
    """`fastmdx agent set` -- pick a provider and, optionally, store a key."""
    from fastmdxplora.agent import PROVIDERS, ModelChoice, save_choice

    names = list(PROVIDERS)
    print("Model:")
    for index, name in enumerate(names, 1):
        print(f"  [{index}] {PROVIDERS[name]['label']}")
    try:
        picked = names[int(input("> ").strip()) - 1]
    except (ValueError, IndexError, EOFError, KeyboardInterrupt):
        print("Nothing chosen.")
        return 1

    base_url = ""
    if picked == "compatible":
        print("\nAnything speaking the OpenAI chat shape. For example:")
        for label, url, model in PROVIDERS[picked].get("examples", ()):
            print(f"  {label:<16} {url:<34} model: {model}")
        base_url = input("\nBase URL: ").strip()
        if not base_url:
            print("A base URL is needed for an OpenAI-compatible server.")
            return 1

    default_model = str(PROVIDERS[picked]["default_model"])
    prompt = (f"Model [{default_model}]: " if default_model else "Model: ")
    model = input(prompt).strip() or default_model
    if not model:
        print("A model name is needed.")
        return 1

    env_name = str(PROVIDERS[picked]["env"])
    print(f"API key (leave blank to read {env_name} from the environment "
          "instead):")
    key = input("> ").strip()

    where = save_choice(ModelChoice(picked, model, base_url), key=key)
    print(f"\n  ✓ Saved to {where}")
    if key:
        print("  ✓ Key stored there, readable only by you. It is never "
              "written into a study.")
    else:
        print(f"  ✓ No key stored; {env_name} will be read at call time.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    # Ensure the CLI can emit its Unicode output (box-drawing banner, "→",
    # "—") regardless of the platform's locale. On machines whose default
    # stdio encoding is ASCII, printing these would otherwise raise
    # UnicodeEncodeError. reconfigure() is available on Python 3.7+ text
    # streams; guard for unusual stream types.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    # Initialize console logging on every CLI invocation. setup_console() is
    # idempotent (no duplicate handlers) and honors FASTMDX_LOG_STYLE /
    # FASTMDX_LOGLEVEL / NO_COLOR.
    from fastmdxplora.utils.logging import own_the_console, setup_console

    setup_console()
    # The CLI owns the terminal, so records are printed once by FastMDXplora's handler
    # rather than also by whatever the root logger has. This used to happen
    # inside setup_console, which meant the library path did it too and
    # cut a caller's logging off from FastMDXplora for the rest of the session.
    own_the_console()

    raw_argv = list(sys.argv[1:] if argv is None else argv)

    # Show the FastMDXplora identity as soon as the CLI starts. Keep version
    # and citation output machine-friendly; help and an empty invocation are
    # intentionally branded.
    if not any(flag in raw_argv for flag in ("--version", "-V", "--cite", "--json")):
        from fastmdxplora.utils.presenter import get_presenter

        dashboard_url, dashboard_enabled = _startup_dashboard_details(raw_argv)
        get_presenter().welcome(
            dashboard_url=dashboard_url,
            dashboard_enabled=dashboard_enabled,
        )

    parser = _build_parser()
    args = parser.parse_args(raw_argv)

    # Short-circuit cheap flags first so a missing chemistry backend never
    # *blocks* `--cite`, `--version`, or `--help`. These flags are how
    # users diagnose the install gap, so guarding them would defeat their
    # purpose.
    if args.cite:
        print(__citation__)
        return 0
    if args.command is None:
        return _cmd_dashboard_home()

    # No chemistry preflight here, deliberately. Setup and simulation handle
    # missing optional dependencies by recording the skipped work in their
    # manifests, and aborting here would prevent setup-only and config-only
    # workflows -- and stop the test matrix exercising that fallback at all.
    #
    # Two functions used to sit further down this file implementing the other
    # policy, `_needs_chemistry` and `_missing_chemistry_backends`. They had
    # no caller once this decision was taken, `_needs_chemistry` referenced a
    # `_CHEMISTRY_PHASES` that was never defined -- so calling it raised
    # NameError -- and `tests/conftest.py` carried an autouse fixture
    # neutralising the second of them for all 3,171 tests. All three are
    # removed. The decision is here, in one place, and nothing half-implements
    # its opposite. If the fail-fast policy is wanted instead, it is a change
    # to this comment and a call on the next line, not a resurrection.

    if args.command == "agent":
        return _run_agent(args)

    if args.command == "init-config":
        return _cmd_init_config(args)
    if args.command == "gui":
        return _cmd_gui(args)

    # Commands that build an orchestrator can hit config-file errors;
    # surface those cleanly rather than as a traceback.
    from fastmdxplora.config import ConfigError

    try:
        if args.command in ("explore", "xplore"):
            return _cmd_explore(args)
        if args.command in ("setup", "simulate", "analyze", "report"):
            return _cmd_phase(args.command, args)
        if args.command == "info":
            return _cmd_info(args)
        if args.command == "select":
            return _cmd_select(args)
        if args.command == "remote":
            return _cmd_remote(args)
    except ConfigError as exc:
        print(f"fastmdx: config error: {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, ValueError) as exc:
        # A study that stops before it starts says so once. These carry a
        # written explanation -- a selection matching no atoms, a system that
        # could not be prepared -- and the stack behind them names batch and
        # setup internals, which is forty lines telling the reader nothing
        # they can act on. The traceback is still available by raising the
        # logging level; what a user needs is the sentence.
        print(f"fastmdx: {exc}", file=sys.stderr)
        logger.debug("stopping after %s", type(exc).__name__, exc_info=True)
        return 1
    except FileExistsError as exc:
        # Refusing to overwrite a previous run is a decision, not a crash:
        # said plainly, with what to do about it, and no traceback.
        print(f"fastmdx: {exc}", file=sys.stderr)
        return 2

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
