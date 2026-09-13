"""Config file loading, validation, and merging.

Loads a YAML configuration, validates it strictly against the schema
registry (:mod:`fastmdxplora.config.schema`), and produces a normalized
structure ready to drive :class:`fastmdxplora.FastMDXplora`.

Validation is strict by design: unknown keys raise
:class:`ConfigError` with a did-you-mean suggestion, and values whose
type doesn't match the schema raise with a clear message. A typo'd
config that silently runs with defaults is the worst failure mode in
science (you think you set ``ph: 7.4``, you actually ran the default,
and your results are subtly wrong with no indication why) — so nothing is ever
silently ignore.

Override precedence (highest wins):

  1. Explicit flags / kwargs supplied at call time
  2. Values in the config file
  3. Built-in phase defaults

This module implements (1) beating (2). The phase ``run()`` functions
implement (2) beating (3) via their own ``DEFAULTS`` tables.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from fastmdxplora.config.schema import (
    PHASE_KEYS,
    PHASE_SCHEMAS,
    TOP_LEVEL,
    TOP_LEVEL_KEYS,
    PhaseSchema,
)
from fastmdxplora.refusals import CodedError

VALID_PHASES = set(PHASE_KEYS)


class ConfigError(CodedError, ValueError):
    """Raised for any problem loading or validating a config file."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_config_file(path: str | Path) -> dict[str, Any]:
    """Read and parse a YAML config file. Returns the raw dict.

    Raises
    ------
    ConfigError
        If the file is missing, unreadable, not valid YAML, or doesn't
        parse to a mapping at the top level.
    """
    import yaml

    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}",
                          code="config.file.missing", path=str(p))
    try:
        with p.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML in {p}: {exc}",
                          code="config.file.unparseable", path=str(p)) from exc

    if data is None:
        # Empty file — treat as empty config (valid; everything defaults)
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file {p} must contain a YAML mapping at the top level, "
            f"got {type(data).__name__}.",
            code="config.file.not_a_mapping",
            path=str(p), found_type=type(data).__name__,
        )

    # Settled here as well as in `validate_config`, so a caller that loads a
    # file and reads it without validating still sees the expanded windows,
    # as it always did. `normalise_config` is idempotent -- `expand_umbrella`
    # lifts the block out of `simulation` as it expands, so a second pass is
    # a no-op -- which is what makes calling it twice safe rather than
    # merely convenient.
    return normalise_config(data)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _suggest(key: str, valid: set[str]) -> str:
    """Return a ' (did you mean X?)' suffix if a close match exists.

    Case-insensitive: a pure case mismatch (``pH`` vs ``ph``) is one of
    the most common config typos and short keys fall below difflib's
    default ratio when case differs, so case-folded matches are checked
    first, then fall back to fuzzy matching.
    """
    # Exact case-insensitive match first (handles pH -> ph, PH -> ph, etc.)
    lower_map = {v.lower(): v for v in valid}
    if key.lower() in lower_map and lower_map[key.lower()] != key:
        return f" (did you mean '{lower_map[key.lower()]}'?)"
    # Fuzzy match on the case-folded forms
    matches = difflib.get_close_matches(
        key.lower(), list(lower_map.keys()), n=1, cutoff=0.6
    )
    if matches:
        return f" (did you mean '{lower_map[matches[0]]}'?)"
    return ""


def _rewrapped(exc: Exception) -> dict[str, Any]:
    """Keyword arguments that carry an inner refusal's identity outward.

    Four places here catch a more specific error -- from the umbrella
    expansion, the alias settling -- and re-raise it as a ``ConfigError``
    so a caller has one thing to catch. The message survives that;
    without this the code would not, and the outer refusal would say only
    that something in the config was wrong.

    Where the inner exception carries no code, the result is empty and the
    outer refusal falls to its own default, which is what it did before.
    """
    from fastmdxplora.refusals import Refusal

    inner = getattr(exc, "refusal", None)
    if not isinstance(inner, Refusal) or inner.code == "unclassified":
        return {}
    return {"code": inner.code, **inner.details}


def _suggestion_only(key: str, valid: set[str]) -> str | None:
    """The nearest valid name, without the sentence `_suggest` wraps it in.

    `_suggest` returns " (did you mean 'ph'?)" because that is what belongs
    in the middle of a message. A caller reading the refusal's details wants
    the name alone: it is going to put it in a config file, not in a
    sentence. Both read the same matcher, so they cannot disagree about
    what the nearest name is.
    """
    suffix = _suggest(key, valid)
    if not suffix:
        return None
    return suffix.split("'")[1]


def _type_name(t: type | tuple[type, ...]) -> str:
    if isinstance(t, tuple):
        return " or ".join(_friendly_type(x) for x in t)
    return _friendly_type(t)


def _friendly_type(t: type) -> str:
    return {
        str: "string",
        int: "integer",
        float: "number",
        bool: "true/false",
        list: "list",
        dict: "mapping",
    }.get(t, t.__name__)


def _check_type(value: Any, expected: type | tuple[type, ...]) -> bool:
    """Type check with YAML-friendly coercions.

    YAML parses ``1`` as int and ``1.0`` as float. A field declared
    ``float`` should accept an int (``temperature_K: 300`` is fine), so
    int is accepted wherever float is allowed. bool is rejected where
    int/float is expected (YAML ``true`` is a Python bool, which is an
    int subclass — without this guard ``ph: true`` would pass an int
    check).
    """
    # bool is a subclass of int — handle it explicitly so numeric fields
    # don't silently accept booleans.
    allowed = expected if isinstance(expected, tuple) else (expected,)
    if isinstance(value, bool):
        return bool in allowed
    # int acceptable wherever float is expected
    if isinstance(value, int) and (float in allowed or int in allowed):
        return True
    if isinstance(value, float) and float in allowed:
        return True
    return isinstance(value, allowed)


#: Fields whose values a later, more specific check refuses better than a
#: generic one can. Kept as a list rather than a flag on the field, because
#: it is a fact about this module's ordering and not about the schema.
_CHECKED_MORE_FULLY_LATER = frozenset({
    ("analysis", "include"),
    ("analysis", "exclude"),
})


def _check_choices(value: Any, fld: Any, *, key: str, context: str) -> None:
    """Refuse a value the schema does not list, the way the parser does.

    The schema names the accepted values for nine settings and argparse
    rejects anything else on all nine. This checked the field's *name* and
    its *type* and stopped, so `box_shape: dodecahedran` validated here,
    survived PDBFixer and force-field construction, and died inside OpenMM's
    `addSolvent` several minutes later with a message about neither the
    setting nor the typo.

    Which is the failure this module's own docstring names: "a typo'd config
    that silently runs with defaults is the worst failure mode in science...
    so nothing is silently ignored". It was true of unknown keys and not of
    known keys carrying unknown values.

    A list field is checked element by element -- `analysis.include` names
    the twenty-three analyses, and one misspelling in a list of six should
    say which.
    """
    if not getattr(fld, "choices", None):
        return
    if (context, key) in _CHECKED_MORE_FULLY_LATER:
        # `analysis.include` and `analysis.exclude` have a refusal further
        # down that names the analysis, offers the three nearest, and says
        # what the list is for. A generic "does not accept" firing first
        # would replace a better message with a worse one.
        return
    offered = set(fld.choices)
    given = value if fld.type is list and isinstance(value, list) else [value]
    for item in given:
        if item in offered:
            continue
        where = f"{context} option '{key}'"
        raise ConfigError(
            f"{where} does not accept {item!r}"
            f"{_suggest(str(item), offered)}. "
            f"Accepted values: {', '.join(str(c) for c in fld.choices)}.",
            code="config.option.not_permitted",
            option=key, context=context, given=item,
            permitted=list(fld.choices),
            suggestion=_suggestion_only(str(item), offered),
        )


def _validate_block(
    block: dict[str, Any],
    schema: PhaseSchema,
    *,
    context: str,
) -> None:
    """Validate one phase block (or the top-level scalars) against a schema."""
    valid = schema.field_names()
    for key, value in block.items():
        if key not in valid:
            raise ConfigError(
                f"Unknown {context} option '{key}'{_suggest(key, valid)}. "
                f"Valid options: {', '.join(sorted(valid))}.",
                code="config.option.unknown",
                option=key, context=context, permitted=sorted(valid),
                suggestion=_suggestion_only(key, valid),
            )
        fld = schema.get(key)
        assert fld is not None  # guaranteed by the membership check
        if value is None:
            # Explicit null is allowed — means "use the default".
            continue
        if not _check_type(value, fld.type):
            raise ConfigError(
                f"{context} option '{key}' should be {_type_name(fld.type)}, "
                f"got {type(value).__name__} ({value!r}).",
                code="config.option.wrong_type",
                option=key, context=context,
                expected_type=_type_name(fld.type),
                found_type=type(value).__name__,
            )
        _check_choices(value, fld, key=key, context=context)


def _validate_phase_list(value: Any, *, field_name: str) -> None:
    """Validate top-level include/exclude phase names after type checking."""
    if value is None:
        return
    unknown = [phase for phase in value if not isinstance(phase, str) or phase not in VALID_PHASES]
    if unknown:
        valid = ", ".join(PHASE_KEYS)
        raise ConfigError(
            f"Top-level '{field_name}' contains unknown phase(s): "
            f"{', '.join(repr(p) for p in unknown)}. Valid phases: {valid}.",
            code="config.phase.unknown",
            option=field_name, given=list(unknown), permitted=list(PHASE_KEYS),
        )


def _split_on_commas(value: Any) -> Any:
    """A comma-separated list written where a list was expected.

    `--include setup,simulation` and `--analyses rmsd,rmsf` are how most
    command-line tools take a list, and argparse takes these as one string.
    No phase or analysis name contains a comma, so there is nothing ambiguous
    to resolve: the alternative is refusing a spelling whose meaning is plain,
    which is a puzzle rather than a safeguard.
    """
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list):
        return value
    if not any(isinstance(item, str) and "," in item for item in items):
        return value

    out: list[Any] = []
    for item in items:
        if isinstance(item, str) and "," in item:
            out.extend(part.strip() for part in item.split(",") if part.strip())
        else:
            out.append(item)
    return out



#: Every group of spellings that name one thing, in one place. A second
#: list of them would be a second place to forget one, which is the defect
#: this whole mechanism exists to end.
#:
#: `tests/test_the_config_asks_for_less.py` reads this and checks the
#: invariant a sweep of the source found broken exactly once: any filter
#: that names one member of a group must name all of them. A reader that
#: knows one spelling finds nothing; a *filter* that knows one spelling
#: leaves the other behind, which is the same defect wearing a mirror.
#:
#: `select_atoms` is absent because the name it settles to depends on which
#: variable is being biased; that pairing is resolved below through
#: `with_general_selection_names`, which owns the mapping.
ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"prepared_from", "setup_from"}),
    frozenset({"ligand_name", "ligand_resname"}),
    frozenset({"centres", "centers"}),
    frozenset({"selection_a", "select_atoms_a"}),
    frozenset({"selection_b", "select_atoms_b"}),
)


#: Spellings that name one thing. Each entry is (established, general): the
#: first is what the code inside this package has always called it, the
#: second is the word a user is likely to arrive knowing. Given both and
#: they differ, the established name wins -- which is what
#: `docs/selections.md` already promises for the role-specific selection
#: names, applied here to every pair rather than to one of them.
_ONE_THING_TWO_WORDS: dict[str, tuple[tuple[str, str], ...]] = {
    "simulation": (("prepared_from", "setup_from"),),
    "setup": (("ligand_name", "ligand_resname"),),
    "analysis": (("selection", "select_atoms"),),
}

#: Pairs settled inside a biasing block, whichever block it is.
_ONE_THING_TWO_WORDS_IN_A_BIASING_BLOCK: tuple[tuple[str, str], ...] = (
    ("ligand_resname", "ligand_name"),
    # British and American, and both were already accepted -- by every
    # reader except one filter in the runner, which stripped `centres`
    # from the spec it forwards and left `centers` behind to be carried
    # into a collective-variable plan that has no such key.
    ("centres", "centers"),
    ("selection_a", "select_atoms_a"),
    ("selection_b", "select_atoms_b"),
)

#: The biasing blocks, which additionally carry a selection whose role name
#: depends on which variable is being biased.
_BIASING_BLOCKS = ("umbrella", "steered", "metadynamics")


def _mirror(block: dict[str, Any], established: str, general: str) -> None:
    """Leave both spellings present and equal, or leave both absent.

    Resolving to one name would only move the problem: every reader would
    then have to know which one won. Writing both means a reader cannot
    pick the absent one, because there is no absent one.
    """
    if established in block and block[established] is not None:
        block.setdefault(general, block[established])
        block[general] = block[established]
    elif general in block and block[general] is not None:
        block[established] = block[general]


def _settle_the_words_that_mean_one_thing(data: dict[str, Any]) -> None:
    """Make every alias pair agree, once, before anything reads them.

    Three defects in one week came from a block with two spellings and two
    readers that each knew one. The reader accepted a spelling the validator
    did not; a rename left two readers behind; and the seeding hand-off read
    `site_selection` off a block that said `select_atoms`, handed MDTraj an
    empty string, and lost a two-and-a-half-hour steered pull to a parser
    error naming column one.

    Each was fixed where it was found, which is how the same defect happened
    three times. The translation existed -- `with_general_selection_names` --
    but it ran inside the PLUMED builder, so whichever readers happened to go
    through that path were correct and the rest were not.

    Settled here instead, for the reason the umbrella expansion below is
    settled here: every route into the software gets it, rather than each
    reader remembering to ask.
    """
    # `_THE_ONE_SELECTION` is imported rather than restated. Duplicating the
    # variable-to-role mapping would create a second place for it to be
    # wrong, which is the defect this function exists to stop.
    from fastmdxplora.simulation.metadynamics import (
        _THE_ONE_SELECTION,
        with_general_selection_names,
    )

    for phase, pairs in _ONE_THING_TWO_WORDS.items():
        block = data.get(phase)
        if isinstance(block, dict):
            for established, general in pairs:
                _mirror(block, established, general)

    simulation = data.get("simulation")
    if not isinstance(simulation, dict):
        return
    for name in _BIASING_BLOCKS:
        block = simulation.get(name)
        if not isinstance(block, dict):
            continue
        for established, general in _ONE_THING_TWO_WORDS_IN_A_BIASING_BLOCK:
            _mirror(block, established, general)
        variable = str(block.get("collective_variable", "")).lower()
        if not variable or "select_atoms" not in block:
            continue
        try:
            resolved = with_general_selection_names(dict(block), variable)
        except ValueError as exc:
            # A two-group variable given a bare `select_atoms`, or a
            # variable that takes no selection at all. Both are real
            # mistakes, and refusing them while reading the file is far
            # better than refusing them after a preparation has run.
            raise ConfigError(str(exc), **_rewrapped(exc)) from exc
        block.update(resolved)
        # And back the other way, so the general word carries the value that
        # won rather than the one it lost with. Given both spellings the
        # role name wins, which `docs/selections.md` promises; leaving
        # `select_atoms` holding the loser would keep a reader of the
        # general word reading something the study did not mean.
        role = _THE_ONE_SELECTION.get(variable)
        if role and block.get(role) is not None:
            block["select_atoms"] = block[role]


def normalise_config(data: dict[str, Any]) -> dict[str, Any]:
    """Settle spellings that have one meaning, before anything is checked.

    Three kinds. Alias pairs are made to agree, so no reader can pick the
    spelling that happens to be absent. Empty phase blocks and comma-joined
    lists are given the shape the validator expects. An umbrella block is
    expanded into its windows. Each was found by writing a config by hand and
    having it refused, or -- in the seeding case -- accepted and then failed
    on hours later. None is a judgement about what the author meant; each is
    a single unambiguous reading.
    """
    # A phase block present but empty. `analysis:` with nothing under it means
    # "run the analysis phase with its defaults" -- which is exactly what
    # leaving the key out entirely does, so refusing one and accepting the
    # other made two spellings of the same intent behave differently. An
    # option set to null is already read as "use the default"; a block is the
    # same statement one level up. A block of the wrong *type* -- a number, a
    # list -- is still a real mistake and still refused.
    for phase in PHASE_KEYS:
        if phase in data and data[phase] is None:
            data[phase] = {}

    for field in ("include", "exclude"):
        if field in data:
            data[field] = _split_on_commas(data[field])

    analysis = data.get("analysis")
    if isinstance(analysis, dict):
        for field in ("include", "exclude"):
            if field in analysis:
                analysis[field] = _split_on_commas(analysis[field])

    _settle_the_words_that_mean_one_thing(data)

    # An umbrella block describes a set of windows; everything downstream runs
    # one system at a time, so the block becomes one `systems` entry per
    # window. This lived in `load_config_file`, under a comment saying the
    # point was that "every route into the software -- command line, Python,
    # the GUI -- gets the same expansion rather than each remembering to ask
    # for it". It was in the one route that reads a file. `config_data=` is
    # the CLI's own path and the documented Python-API shape, and there a
    # five-window study became one run with the block unexpanded, and
    # `continue_on_error` silently flipped from False to True.
    #
    # Here instead, because `validate_config` calls this and both routes call
    # that -- and in place, because a caller holding the dict keeps the dict
    # it passed in.
    from fastmdxplora.simulation.umbrella import expand_umbrella

    try:
        expanded = expand_umbrella(data)
    except ValueError as exc:
        raise ConfigError(str(exc), **_rewrapped(exc)) from exc
    if expanded is not data:
        data.clear()
        data.update(expanded)

    return data


def validate_config(data: dict[str, Any], *, require_systems: bool = False) -> None:
    """Strictly validate a parsed config dict against the schema.

    Parameters
    ----------
    data : dict
        The parsed config.
    require_systems : bool, default False
        If True, the config must define a non-empty ``systems`` list.
        The full pipeline (CLI / BatchExplorer) sets this; unit tests that
        validate fragments leave it False.

    Raises
    ------
    ConfigError
        On unknown top-level keys, unknown per-phase keys, type
        mismatches, mutually-exclusive include/exclude, a missing
        ``systems`` list (when required), or a malformed execution block.
    """
    # Spellings with one meaning are settled first, so the checks below judge
    # what the author meant rather than how they typed it.
    normalise_config(data)

    # Top-level keys: scalar fields + phase block names
    for key in data:
        if key not in TOP_LEVEL_KEYS:
            raise ConfigError(
                f"Unknown top-level key '{key}'{_suggest(key, TOP_LEVEL_KEYS)}. "
                f"Valid: {', '.join(sorted(TOP_LEVEL_KEYS))}.",
                code="config.option.unknown",
                option=key, context="top-level",
                permitted=sorted(TOP_LEVEL_KEYS),
                suggestion=_suggestion_only(key, set(TOP_LEVEL_KEYS)),
            )

    # Validate the top-level scalar fields (output, verbose, include,
    # exclude). Phase blocks and batch keys (systems/sweep/execution)
    # have their own structure and are validated separately below.
    from fastmdxplora.config.schema import BATCH_KEYS, EXECUTION

    top_scalars = {
        k: v for k, v in data.items()
        if k not in PHASE_KEYS and k not in BATCH_KEYS
    }
    _validate_block(top_scalars, TOP_LEVEL, context="top-level")

    # include / exclude mutual exclusion
    if data.get("include") and data.get("exclude"):
        raise ConfigError(
            "Config sets both 'include' and 'exclude' at the top level; "
            "they are mutually exclusive.",
            code="config.option.conflicting",
            options=["include", "exclude"], context="top-level",
        )
    _validate_phase_list(data.get("include"), field_name="include")
    _validate_phase_list(data.get("exclude"), field_name="exclude")

    # `systems` is the canonical (and required) way to specify input.
    if require_systems and not data.get("systems"):
        raise ConfigError(
            "Config must define a `systems:` list (the canonical way to "
            "specify input). Even a single system goes in the list, e.g.\n"
            "  systems:\n"
            "    - {id: protein1, system: protein.pdb}",
            code="config.option.missing_companion",
            option="systems", requires=["systems"],
        )

    # Validate batch keys (systems / sweep) via the batch layer.
    if data.get("systems") is not None or data.get("sweep") is not None:
        from fastmdxplora.batch.sweep import (
            SweepError,
            normalize_summary_for_validation,
        )
        try:
            normalize_summary_for_validation(data)
        except SweepError as exc:
            raise ConfigError(str(exc), **_rewrapped(exc)) from exc

    # Validate the execution block (parallelism settings)
    if data.get("execution") is not None:
        execution = data["execution"]
        if not isinstance(execution, dict):
            raise ConfigError(
                f"The 'execution' block must be a mapping, "
                f"got {type(execution).__name__}.",
                code="config.option.wrong_type",
                option="execution", context="top-level",
                expected_type="mapping", found_type=type(execution).__name__,
            )
        # `mode` used to be checked against a hand-written pair here, beside
        # a schema that named the same pair in prose and declared no
        # `choices`. It has the tuple now, so `_check_choices` refuses it on
        # the same terms as every other setting -- and argparse offers the
        # same two, from the same tuple.
        _validate_block(execution, EXECUTION, context="execution")

    # Validate each per-phase block
    for phase in PHASE_KEYS:
        if phase not in data:
            continue
        block = data[phase]
        if not isinstance(block, dict):
            raise ConfigError(
                f"The '{phase}' block must be a mapping of options, "
                f"got {type(block).__name__}.",
                code="config.option.wrong_type",
                option=phase, context="top-level",
                expected_type="mapping", found_type=type(block).__name__,
            )
        _validate_block(block, PHASE_SCHEMAS[phase], context=f"{phase}")

    # The umbrella block is a mapping inside the simulation block, so the
    # phase schema sees one key and looks no further. Everything inside it
    # went unchecked: `minimum_ovelap: 0.15` was accepted, ignored, and the
    # study stitched at the default while its author believed otherwise.
    simulation = data.get("simulation")
    if isinstance(simulation, dict) and isinstance(simulation.get("umbrella"), dict):
        from fastmdxplora.simulation.umbrella import check_umbrella_keys

        check_umbrella_keys(simulation["umbrella"])

    # analysis include/exclude mutual exclusion (nested)
    analysis = data.get("analysis", {})
    if isinstance(analysis, dict) and analysis.get("include") and analysis.get("exclude"):
        raise ConfigError(
            "The 'analysis' block sets both 'include' and 'exclude'; "
            "they are mutually exclusive.",
            code="config.option.conflicting",
            options=["include", "exclude"], context="analysis",
        )
    if isinstance(analysis, dict):
        _check_analysis_names(analysis)


def _check_analysis_names(analysis: dict[str, Any]) -> None:
    """Refuse a name no analysis has.

    A misspelling was accepted and then quietly dropped: `contacts` for
    `pl_contacts` validated, ran nothing, and produced a report missing the
    measurement the config asked for. A config that cannot do what it says is
    a config to refuse, and the nearest name is usually the intended one.
    """
    try:
        import fastmdxplora.analysis.analyze  # noqa: F401,PLC0415
        from fastmdxplora.analysis.orchestrator import _REGISTRY  # noqa: PLC0415
    except ImportError:  # pragma: no cover - a trimmed install
        return

    known = set(_REGISTRY)
    for key in ("include", "exclude"):
        names = analysis.get(key)
        if not isinstance(names, (list, tuple)):
            continue
        for name in names:
            if str(name) in known:
                continue
            import difflib  # noqa: PLC0415

            near = difflib.get_close_matches(str(name), sorted(known), n=3,
                                             cutoff=0.5)
            suggestion = (f" Did you mean {', '.join(near)}?" if near else "")
            raise ConfigError(
                f"No analysis is called {str(name)!r}, so "
                f"analysis.{key} asks for something that cannot run."
                f"{suggestion} The full list is: {', '.join(sorted(known))}.",
                code="analysis.unknown",
                option=f"analysis.{key}", given=name,
                permitted=sorted(known),
                suggestion=_suggestion_only(str(name), set(known)),
            )


# ---------------------------------------------------------------------------
# Phase-option extraction
# ---------------------------------------------------------------------------
def phase_options(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract the per-phase option blocks from a validated config.

    Returns ``{phase: {option: value, ...}}`` with ``None`` values
    dropped so phase ``DEFAULTS`` apply. Used by :class:`BatchExplorer`
    to assemble the base options shared by every run.
    """
    options: dict[str, dict[str, Any]] = {}
    for phase in PHASE_KEYS:
        if phase in data and isinstance(data[phase], dict):
            block = {k: v for k, v in data[phase].items() if v is not None}
            if block:
                options[phase] = block
    return options
