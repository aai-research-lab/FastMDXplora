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

import contextlib
import difflib
import math
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

    from fastmdxplora import record_access

    p = Path(path)
    # Through `record_access`: held to a study where the read is, as pathlib
    # elsewhere.
    if not record_access.exists(p):
        raise ConfigError(f"Config file not found: {p}",
                          code="config.file.missing", path=str(p))
    try:
        with record_access.open_text(p, encoding="utf-8") as fh:
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

    canonical_phase_keys(data)
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



def _check_bounds(value: Any, fld: Any, *, key: str, context: str) -> None:
    """Refuse a number the world does not have.

    `choices` refuses a name the software does not know. This refuses a
    value outside what the quantity can be: a pH of 25, a negative
    duration, a concentration below zero. Those are not strict readings of
    a setting, they are readings of nothing.

    Only where the bound is a fact. A 10 fs timestep is unstable for almost
    every system and is not impossible, so it has no upper bound -- a
    guessed one would refuse somebody's legitimate coarse-grained run, and
    the runner already refuses an integration that blows up, which is the
    honest place for that judgement.

    The message says what the bound is and why, because "out of range" with
    no range is a refusal a caller cannot act on.
    """
    low = getattr(fld, "minimum", None)
    high = getattr(fld, "maximum", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    if not math.isfinite(value):
        # `.nan` and `.inf` are numbers to YAML and to every comparison
        # below, which a NaN passes whatever the bound: `ph: .nan` was
        # prepared at a pH of NaN. No setting is any of them.
        raise ConfigError(
            f"{context} option '{key}' is {value}, which is not a value it "
            "can have: give a finite number.",
            code="config.option.out_of_range",
            option=key, context=context, given=str(value), minimum=low,
            maximum=high,
        )
    if low is None and high is None:
        return

    if low is not None and getattr(fld, "exclusive_minimum", False) and value <= low:
        raise ConfigError(
            f"{context} option '{key}' is {value}; it must be above {low}.",
            code="config.option.out_of_range",
            option=key, context=context, given=value, minimum=low, maximum=high,
        )
    if low is not None and value < low:
        raise ConfigError(
            f"{context} option '{key}' is {value}, below the smallest value "
            f"it can have ({low}).",
            code="config.option.out_of_range",
            option=key, context=context, given=value, minimum=low, maximum=high,
        )
    if high is not None and value > high:
        raise ConfigError(
            f"{context} option '{key}' is {value}, above the largest value "
            f"it can have ({high}).",
            code="config.option.out_of_range",
            option=key, context=context, given=value, minimum=low, maximum=high,
        )


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
        _check_bounds(value, fld, key=key, context=context)


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
        if block not in (None, False) and not isinstance(block, dict):
            # Named, rather than read on as if it were a mapping -- which
            # listed a string's characters as unknown settings.
            raise ConfigError(
                f"`simulation.{name}` is a block of settings and was given "
                f"{type(block).__name__} {str(block)[:60]!r}. Write it as a "
                "mapping: in a config, its settings indented under it; on the "
                f"command line, --simulate-{name} "
                "'{collective_variable: distance, from: 0.3, to: 1.5}'.",
                code="config.option.wrong_type")
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

    for field in ("include_phase", "exclude_phase", "include", "exclude"):
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


def validate_config(data: dict[str, Any], *, require_systems: bool = False,
                    as_record: bool = False) -> None:
    """Strictly validate a parsed config dict against the schema.

    Parameters
    ----------
    data : dict
        The parsed config.
    require_systems : bool, default False
        If True, the config must define a non-empty ``systems`` list.
        The full pipeline (CLI / BatchExplorer) sets this; unit tests that
        validate fragments leave it False.
    as_record : bool, default False
        The config is a study's record, read to report or analyse what ran
        rather than to run it: a choice refused since (an integrator with no
        thermostat) describes the study and is not refused again.

    Raises
    ------
    ConfigError
        On unknown top-level keys, unknown per-phase keys, type
        mismatches, mutually-exclusive include/exclude, a missing
        ``systems`` list (when required), or a malformed execution block.
    """
    canonical_phase_keys(data)
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

    if data.get("decisions") is not None:
        _check_decisions(data["decisions"])

    # include / exclude mutual exclusion
    if data.get("include_phase") and data.get("exclude_phase"):
        raise ConfigError(
            "Config sets both 'include_phase' and 'exclude_phase' (written "
            "earlier as 'include' and 'exclude') at the top level; they are "
            "mutually exclusive: give the phases to run, or the phases to "
            "leave out.",
            code="config.option.conflicting",
            options=["include_phase", "exclude_phase"], context="top-level",
        )
    _validate_phase_list(data.get("include_phase"), field_name="include_phase")
    _validate_phase_list(data.get("exclude_phase"), field_name="exclude_phase")

    # `systems` is the canonical (and required) way to specify input.
    if require_systems and not data.get("systems"):
        raise ConfigError(
            "Config must define a `systems:` list (the canonical way to "
            "specify input). Each entry needs a `system` that is a PDB "
            "identifier to fetch or a path to a structure file that "
            "exists -- taken from the request, not invented. An `id` is "
            "optional and is numbered if absent:\n"
            "  systems:\n"
            "    - {system: <PDB id or path>}\n"
            "If the request names no structure, there is nothing to put "
            "here and the study cannot be written.",
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
        # The structure is right; now what each entry and each swept value
        # says, held to the same schema as the top-level blocks. Neither was
        # read by anything but the run: `simulation: {temprature_K: 350}` in
        # an entry ran at 300 K, and a swept pH of 25 was prepared at 25.
        if isinstance(data.get("systems"), list):
            _check_system_entries(data["systems"])
        if isinstance(data.get("sweep"), dict):
            _check_sweep_values(data["sweep"], data.get("systems"), data)

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
        _check_phase_values(phase, block, context=phase)

    # The umbrella block is a mapping inside the simulation block, so the
    # phase schema sees one key and looks no further. Everything inside it
    # went unchecked: `minimum_ovelap: 0.15` was accepted, ignored, and the
    # study stitched at the default while its author believed otherwise.
    simulation = data.get("simulation")
    # An integrator that cannot hold the temperature every stage asks for,
    # or Brownian dynamics too weakly damped for its step, refused with the
    # config rather than at the first step of a run: for each run the study
    # expands to, and only where the simulation phase runs, so a record of
    # an older study can still be read and reported.
    if not as_record:
        _check_integrators(data)
    if isinstance(simulation, dict):
        _check_restraints(simulation, where="")
    if isinstance(simulation, dict) and isinstance(simulation.get("umbrella"), dict):
        from fastmdxplora.simulation.umbrella import check_umbrella_keys

        check_umbrella_keys(simulation["umbrella"])

    # analysis include/exclude mutual exclusion (nested)
    analysis = data.get("analysis", {})
    if (as_record and isinstance(analysis, dict) and analysis.get("include")
            and analysis.get("exclude")):
        # A study's record written with both: the analyses left out by name
        # and, beside them, the plan they resolved to. That plan is what ran,
        # so it is read as the record's and the study is not refused; said
        # in the log, since a run it starts runs the plan, which the image
        # check joins, its exclusion not kept.
        left = analysis.pop("exclude")
        named = [str(name) for name in left] if isinstance(left, list) else [str(left)]
        from fastmdxplora.utils.logging import get_logger

        get_logger("config").info(
            "The study's record lists the analyses that ran and those left out (%s); "
            "it is read as the analyses that ran%s.", ", ".join(named),
            ", which the image check joins, its exclusion not kept"
            if "image_check" in named else "")
    if isinstance(analysis, dict) and analysis.get("include") and analysis.get("exclude"):
        raise ConfigError(
            "The 'analysis' block sets both 'include' and 'exclude'; "
            "they are mutually exclusive.",
            code="config.option.conflicting",
            options=["include", "exclude"], context="analysis",
        )
    if isinstance(analysis, dict):
        _check_analysis_names(analysis)
        _check_analysis_options(analysis, context="analysis")

    # A stopping rule the study cannot keep is refused with the rest of the
    # config, so `fastmdx check-config`, the GUI and the Agent's repair loop
    # see it; the batch layer checks it again with the phases a call asks for.
    if isinstance(simulation, dict) and simulation.get("stop_when") is not None:
        from fastmdxplora.refusals import StudyError
        from fastmdxplora.simulation.stopping import check_study, targets_of

        try:
            if require_systems:
                check_study(data)
            elif isinstance(simulation["stop_when"], dict):
                targets_of(simulation["stop_when"])
        except ConfigError:
            raise
        except StudyError as exc:
            raise ConfigError(str(exc), **_rewrapped(exc)) from exc

    # Last: what holds between settings, for each run as it will be made,
    # its system's block and its sweep values over the top-level blocks.
    _check_each_run(data)


#: The keys a `systems` entry may hold: its structure, its name, and a
#: block for each phase that overrides the top-level one for it alone.
SYSTEM_ENTRY_KEYS = ("system", "id", *PHASE_KEYS)

#: Settings that answer one question between them, by their dotted names.
#: Given both, the runner reads one and drops the other without a word, so a
#: study, a system entry or your defaults that gives one has given the
#: setting, whichever spelling it used.
SAME_SETTING: tuple[frozenset[str], ...] = (
    frozenset({"simulation.pressure_bar", "simulation.pressure_atm"}),
    frozenset({"simulation.nvt_steps", "simulation.nvt_duration_ns"}),
    frozenset({"simulation.npt_steps", "simulation.npt_duration_ns"}),
    frozenset({"simulation.production_steps", "simulation.duration_ns"}),
    frozenset({"setup.forcefield", "setup.force_field"}),
    frozenset({"setup.heterogens", "setup.keep_heterogens"}),
)


#: The pairs of `SAME_SETTING` whose losing spelling a record writes as
#: `null` when it was not given, so the other given beside it is a second
#: value the run never reads. (`forcefield: auto` is written beside a
#: `force_field` list, and `heterogens` beside `keep_heterogens`, by every
#: record, so those two pairs are judged where they are read.)
ONE_SPELLING_READ: tuple[frozenset[str], ...] = SAME_SETTING[:4]

#: Of each pair the runner reads, the spelling it reads when both are given.
SPELLING_READ = {
    "simulation.pressure_bar": "simulation.pressure_atm",
    "simulation.nvt_steps": "simulation.nvt_duration_ns",
    "simulation.npt_steps": "simulation.npt_duration_ns",
    "simulation.production_steps": "simulation.duration_ns",
}


def same_setting(name: str) -> frozenset[str]:
    """Every dotted name that sets what ``name`` sets, ``name`` among them."""
    for group in SAME_SETTING:
        if name in group:
            return group
    return frozenset({name})


#: The bond constraints setup builds, as it spells them. Setup reads them in
#: any letter case, so the check does too.
CONSTRAINTS = ("None", "HBonds", "AllBonds", "HAngles")


def _check_phase_values(phase: str, block: dict[str, Any], *, context: str) -> None:
    """What a phase block's schema cannot say about a value on its own."""
    if phase == "setup":
        value = block.get("constraints")
        if value is not None and str(value).lower() not in {
                c.lower() for c in CONSTRAINTS}:
            raise ConfigError(
                f"{context} option 'constraints' does not accept {value!r}"
                f"{_suggest(str(value), set(CONSTRAINTS))}. Accepted values: "
                f"{', '.join(CONSTRAINTS)}.",
                code="config.option.not_permitted", option="constraints",
                context=context, given=value, permitted=list(CONSTRAINTS),
                suggestion=_suggestion_only(str(value), set(CONSTRAINTS)))
    if phase == "simulation" and isinstance(block.get("umbrella"), dict):
        from fastmdxplora.simulation.umbrella import check_umbrella_keys

        check_umbrella_keys(block["umbrella"])


def _check_system_entries(entries: list[Any]) -> None:
    """Each `systems` entry held to what a top-level block is held to."""
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue  # refused by the batch layer before this
        where = f"systems[{i}]"
        if entry.get("id") is not None:
            where = f"systems[{i}] ({entry['id']})"
        for key in entry:
            if key not in SYSTEM_ENTRY_KEYS:
                raise ConfigError(
                    f"Unknown key '{key}' in {where}"
                    f"{_suggest(str(key), set(SYSTEM_ENTRY_KEYS))}. An entry holds "
                    "`system`, an optional `id`, and a block for any phase "
                    f"({', '.join(PHASE_KEYS)}) that overrides the top-level "
                    "one for this system.",
                    code="config.option.unknown", option=str(key), context=where,
                    permitted=list(SYSTEM_ENTRY_KEYS),
                    suggestion=_suggestion_only(str(key), set(SYSTEM_ENTRY_KEYS)))
        for phase in PHASE_KEYS:
            if phase not in entry or entry[phase] is None:
                continue
            block = entry[phase]
            if not isinstance(block, dict):
                raise ConfigError(
                    f"The '{phase}' block of {where} must be a mapping of "
                    f"options, got {type(block).__name__}.",
                    code="config.option.wrong_type", option=phase, context=where,
                    expected_type="mapping", found_type=type(block).__name__)
            context = f"{where} {phase}"
            _validate_block(block, PHASE_SCHEMAS[phase], context=context)
            _check_phase_values(phase, block, context=context)
            if phase == "analysis":
                for field in ("include", "exclude"):
                    if field in block:
                        block[field] = _split_on_commas(block[field])
                if block.get("include") and block.get("exclude"):
                    raise ConfigError(
                        f"The 'analysis' block of {where} sets both 'include' "
                        "and 'exclude'; they are mutually exclusive.",
                        code="config.option.conflicting",
                        options=["include", "exclude"], context=where)
                _check_analysis_names(block)
                _check_analysis_options(block, context=context)


def _same_value(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    return a == b


def _check_sweep_values(sweep: dict[str, Any], systems: Any,
                        data: dict[str, Any] | None = None) -> None:
    """Each value an axis takes held to its setting's type, choices and
    bounds; no value given twice; no axis a system entry also sets; no axis
    whose other spelling the study also gives, which one of the two would
    leave unread."""
    for axis in sweep:
        others = next((group - {str(axis)} for group in ONE_SPELLING_READ
                       if str(axis) in group), frozenset())
        for other in sorted(others):
            block_name, _, key = other.partition(".")
            block = (data or {}).get(block_name)
            given_there = isinstance(block, dict) and block.get(key) is not None
            if not given_there and other not in sweep:
                continue
            read = other if SPELLING_READ.get(other) == str(axis) else str(axis)
            where = f"sweep.{other}" if other in sweep else other
            raise ConfigError(
                f"The study sweeps {axis} and also gives {where}; they set one "
                f"thing, and given both a run reads only {read}, so "
                + ("every run would be the same run under different names"
                   if read != str(axis) else f"{where} would never be used")
                + ". Sweep one of them and leave the other out.",
                code="config.option.conflicting",
                options=[f"sweep.{axis}", where], context="sweep")
    for axis, given in sweep.items():
        phase, _, option = str(axis).partition(".")
        fld = PHASE_SCHEMAS[phase].get(option)
        values = list(given) if isinstance(given, (list, tuple)) else [given]
        context = f"swept {phase}"
        for value in values:
            if value is None:
                continue
            with _named_as(f"sweep.{axis}"):
                _validate_block({option: value}, PHASE_SCHEMAS[phase], context=context)
                _check_phase_values(phase, {option: value}, context=context)
                if phase == "analysis" and option in ("include", "exclude"):
                    _check_analysis_names({option: _split_on_commas(value)})
                if phase == "analysis" and option == "options":
                    _check_analysis_options({"options": value}, context=context)
        for i, value in enumerate(values):
            if any(_same_value(value, earlier) for earlier in values[:i]):
                raise ConfigError(
                    f"Sweep axis '{axis}' gives {value!r} more than once, so "
                    "two runs would be the same run under two names. Give each "
                    "value once; for repeated runs of one setting, sweep "
                    "`simulation.random_seed` over different seeds.",
                    code="batch.sweep.invalid", option=str(axis),
                    reason="repeated value")
        if fld is None or not isinstance(systems, list):
            continue
        names = same_setting(str(axis))
        for i, entry in enumerate(systems):
            if not isinstance(entry, dict) or _is_an_umbrella_window(entry):
                continue
            for name in names:
                block_name, _, key = name.partition(".")
                block = entry.get(block_name)
                if isinstance(block, dict) and block.get(key) is not None:
                    label = entry.get("id") or f"systems[{i}]"
                    raise ConfigError(
                        f"System '{label}' sets {name} to {block[key]!r} and the "
                        f"study sweeps {axis}; the sweep's values replace the "
                        "system's for every run of it, so its own is never used. "
                        "Leave it to the sweep, or move that system to a study "
                        "of its own.",
                        code="config.option.conflicting",
                        options=[f"systems[{i}].{name}", f"sweep.{axis}"],
                        context="sweep")


@contextlib.contextmanager
def _named_as(path: str):
    """A refusal of a swept value named by the place it was given, so its
    fix says `sweep.setup.ph` rather than `ph`, which the study does not set."""
    try:
        yield
    except ConfigError as exc:
        refusal = getattr(exc, "refusal", None)
        details = dict(refusal.details) if refusal is not None else {}
        details.pop("options", None)
        details.update(option=path, context="sweep")
        raise ConfigError(str(exc), code=refusal.code if refusal is not None
                          else "config.option.out_of_range", **details) from exc


def _is_an_umbrella_window(entry: dict[str, Any]) -> bool:
    """An entry the umbrella expansion made, which carries every top-level
    simulation setting as its own: a sweep over one of them is the study's,
    not a system's value set aside."""
    simulation = entry.get("simulation")
    umbrella = simulation.get("umbrella") if isinstance(simulation, dict) else None
    return isinstance(umbrella, dict) and "index" in umbrella and "centre" in umbrella


def _check_analysis_options(analysis: dict[str, Any], *, context: str) -> None:
    """Refuse options for an analysis that does not exist, and an option an
    analysis has no name for. Checked when the analysis phase starts as
    well; here, so a misspelling is said before the simulation it would
    otherwise wait for."""
    options = analysis.get("options")
    if not isinstance(options, dict):
        return
    try:
        import fastmdxplora.analysis.analyze  # noqa: F401,PLC0415
        from fastmdxplora.analysis.orchestrator import (  # noqa: PLC0415
            _REGISTRY,
            AnalysisOrchestrator,
            get_analysis_class,
        )
    except ImportError:  # pragma: no cover - a trimmed install
        return
    known = set(_REGISTRY)
    for name, given in options.items():
        if str(name) not in known:
            raise ConfigError(
                f"{context} option 'options' names {str(name)!r}, which is no "
                f"analysis{_suggest(str(name), known)}, so its settings would "
                f"be read by nothing. The analyses are: {', '.join(sorted(known))}.",
                code="analysis.unknown", option=f"{context}.options",
                given=name, permitted=sorted(known),
                suggestion=_suggestion_only(str(name), known))
        if not isinstance(given, dict):
            raise ConfigError(
                f"{context} options[{name!r}] must be a mapping of settings, "
                f"got {type(given).__name__}.",
                code="analysis.option.wrong_type", analysis=str(name),
                option=str(name), expected_type="mapping",
                found_type=type(given).__name__)
        accepted = AnalysisOrchestrator._accepted_options(
            get_analysis_class(str(name)))
        for key in given:
            if key in accepted:
                _refuse_non_finite(given[key], where=f"{context} options[{name!r}]",
                                   key=str(key))
                continue
            raise ConfigError(
                f"{context} options[{name!r}] has no setting called {key!r}"
                f"{_suggest(str(key), accepted)}. {name} accepts: "
                f"{', '.join(sorted(accepted))}.",
                code="analysis.option.inapplicable", analysis=str(name),
                option=str(key), applies_to=sorted(accepted))


def _refuse_non_finite(value: Any, *, where: str, key: str) -> None:
    """NaN and infinity are numbers to YAML, inside a list or a mapping too,
    and no analysis setting is any of them."""
    if isinstance(value, bool):
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigError(
            f"{where} sets {key!r} to {value}, which is not a value it can "
            "have: give a finite number.",
            code="config.option.out_of_range", option=key, context=where,
            given=str(value))
    if isinstance(value, (list, tuple)):
        for item in value:
            _refuse_non_finite(item, where=where, key=key)
    elif isinstance(value, dict):
        for name, item in value.items():
            _refuse_non_finite(item, where=where, key=f"{key}.{name}")


def _check_each_run(data: dict[str, Any]) -> None:
    """What holds between settings, for each run the study makes.

    A setting can come from the top-level block, a system's block or a sweep
    value, so a pair is judged where the run has them both.
    """
    from fastmdxplora.batch.sweep import (
        SweepError,
        expand_runs,
        normalize_sweep,
        normalize_systems,
    )

    base = phase_options(data)
    try:
        systems = (normalize_systems(data["systems"])
                   if data.get("systems") else None)
        sweep = normalize_sweep(data.get("sweep")) if data.get("sweep") else None
        runs = expand_runs(systems=systems, sweep=sweep, base_options=base,
                           base_system="")
    except SweepError:  # pragma: no cover - refused above
        return
    several = len(runs) > 1
    for run in runs:
        where = f" for run '{run.run_id}'" if several else ""
        _check_one_run(run.options, where=where)


def _check_one_run(options: dict[str, dict[str, Any]], *, where: str) -> None:
    setup = options.get("setup") or {}
    named = setup.get("forcefield")
    if named is not None and str(named).strip().lower() == "auto":
        # The default, written beside the list by every record of a study
        # that gave one: it names no force field.
        named = None
    if named is not None and setup.get("force_field"):
        from fastmdxplora.setup.pipeline import _the_list_is_the_name, _the_list_was_the_name

        # The list a name resolved to in an earlier release is the record of
        # a study prepared then, carried on with the files it was prepared with.
        if not (_the_list_is_the_name(setup["forcefield"], setup["force_field"])
                or _the_list_was_the_name(setup["forcefield"], setup["force_field"])):
            raise ConfigError(
                f"The setup gives both `forcefield` ({setup['forcefield']!r}, a "
                "named force field) and `force_field` (a list of OpenMM XML "
                f"files){where}; give one. The named one is recommended: it also "
                "decides the cutoff and how a ligand is parameterized.",
                code="config.option.conflicting",
                options=["setup.forcefield", "setup.force_field"])

    simulation = options.get("simulation") or {}
    _check_lengths_agree(simulation, where=where)
    # A system's or a sweep's restraints, read as the study's are above.
    _check_restraints(simulation, where=where)
    interval = simulation.get("trajectory_interval_steps")
    if (isinstance(interval, int) and not isinstance(interval, bool)
            and not simulation.get("resume_from")):
        from fastmdxplora.simulation.lengths import DEFAULT_TIMESTEP_FS
        from fastmdxplora.simulation.runner import plan_stages

        timestep = simulation.get("timestep_fs") or DEFAULT_TIMESTEP_FS
        try:
            production = plan_stages(
                duration_ns=simulation.get("duration_ns"), timestep_fs=timestep,
                nvt_steps=simulation.get("nvt_steps"),
                npt_steps=simulation.get("npt_steps"),
                production_steps=simulation.get("production_steps"),
            )["production_steps"]
        except (TypeError, ValueError, ZeroDivisionError):  # pragma: no cover
            production = None
        if production and interval > production:
            raise ConfigError(
                f"simulation option 'trajectory_interval_steps' is {interval:,} "
                f"steps{where}, longer than the {production:,} steps of "
                "production, so not one frame would be written and nothing "
                "could be analysed. Give an interval of at most the production "
                "length (a tenth of it or less gives ten frames or more), or "
                "leave it out for about 2,000 frames.",
                code="config.option.conflicting",
                options=["simulation.trajectory_interval_steps",
                         "simulation.production_steps"])

    analysis = options.get("analysis") or {}
    first, last = analysis.get("first"), analysis.get("last")
    if first is None:
        first = 0  # the first frame, as the analysis reads an unset `first`
    if (isinstance(first, int) and isinstance(last, int)
            and not isinstance(first, bool) and not isinstance(last, bool)
            and last <= first):
        raise ConfigError(
            f"analysis options 'first' ({first}) and 'last' ({last}){where} "
            "leave no frame to analyse: `last` is the frame after the last one "
            "read, so it must be above `first`.",
            code="config.option.conflicting",
            options=["analysis.first", "analysis.last"])


def _check_restraints(simulation: dict[str, Any], *, where: str) -> None:
    """The long form of `restrain` read as the run will read it, and its
    release ladder, so a block with an unknown kind or no force constant is
    refused with the config rather than after setup has run."""
    if simulation.get("restrain") in (None, ""):
        return
    from fastmdxplora.refusals import StudyError
    from fastmdxplora.simulation.restraints import ReleaseSchedule, parse_restraints

    def said(exc: Exception) -> str:
        return f"The restraints{where}: {exc}" if where else str(exc)

    try:
        parse_restraints(simulation["restrain"])
    except StudyError as exc:
        raise ConfigError(said(exc), **_rewrapped(exc)) from exc
    except (TypeError, ValueError, AttributeError) as exc:
        raise ConfigError(
            f"simulation option 'restrain'{where} could not be read: {exc}",
            code="config.option.wrong_type", option="restrain",
            context="simulation") from exc
    if simulation.get("restraint_release"):
        try:
            steps = tuple(float(x) for x in simulation["restraint_release"])
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"simulation option 'restraint_release'{where} could not be read "
                f"as force constants: {exc}",
                code="config.option.wrong_type", option="restraint_release",
                context="simulation") from exc
        # The default ladder, written out, asks for nothing the kept
        # restraints would not do; any other is a ladder that would be
        # ignored.
        if simulation.get("restrain_production") and steps != ReleaseSchedule().steps:
            raise ConfigError(
                f"`restraint_release` and `restrain_production` were both given{where}. "
                "Restraints kept through production are held at "
                "their full force constants from the first step, so the "
                "ladder would not be used. To hold production at a weaker "
                "restraint, give that restraint the force constant wanted "
                "and leave out `restraint_release`.",
                code="config.option.conflicting",
                options=["simulation.restraint_release",
                         "simulation.restrain_production"])
        try:
            ReleaseSchedule(steps=steps).check()
        except StudyError as exc:
            raise ConfigError(said(exc), **_rewrapped(exc)) from exc


def _check_lengths_agree(simulation: dict[str, Any], *, where: str) -> None:
    """Refuse a stage given as steps and as a duration that disagree.

    Given both, the steps are run and the duration is read by nothing, so a
    study that says `duration_ns: 10` beside `production_steps: 300` runs
    300 steps. The two are accepted together where they say the same length,
    as a record that states both does.
    """
    from fastmdxplora.simulation.lengths import DEFAULT_TIMESTEP_FS, steps_in

    timestep = simulation.get("timestep_fs") or DEFAULT_TIMESTEP_FS
    for steps_key, ns_key in (("production_steps", "duration_ns"),
                              ("nvt_steps", "nvt_duration_ns"),
                              ("npt_steps", "npt_duration_ns")):
        steps, ns = simulation.get(steps_key), simulation.get(ns_key)
        if steps is None or ns is None:
            continue
        try:
            says = steps_in(ns, timestep)
        except (TypeError, ValueError, ZeroDivisionError):  # refused above
            continue
        if int(steps) == says:
            continue
        raise ConfigError(
            f"simulation gives '{steps_key}' ({int(steps):,} steps) and "
            f"'{ns_key}' ({ns} ns, {says:,} steps at {timestep} fs){where}; "
            f"given both, the run takes the steps and reads nothing of the "
            f"duration. Give one of them.",
            code="config.option.conflicting",
            options=[f"simulation.{steps_key}", f"simulation.{ns_key}"])


def _check_integrators(data: dict[str, Any]) -> None:
    """`check_integrator` on every run's simulation settings, defaults filled."""
    from fastmdxplora.config.schema import SIMULATION
    from fastmdxplora.refusals import StudyError
    from fastmdxplora.simulation.runner import check_integrator
    from fastmdxplora.simulation.stopping import phases_of

    if "simulation" not in phases_of(data):
        return
    top = data.get("simulation") if isinstance(data.get("simulation"), dict) else {}
    blocks: list[tuple[str | None, dict[str, Any]]] = []
    if data.get("systems"):
        from fastmdxplora.batch.sweep import expand_runs, normalize_sweep, normalize_systems

        try:
            specs = expand_runs(
                systems=normalize_systems(data["systems"]),
                sweep=normalize_sweep(data["sweep"]) if data.get("sweep") else None,
                base_options=phase_options(data))
        except Exception:  # noqa: BLE001 - the batch check above says what is wrong
            specs = []
        blocks = [(spec.run_id, dict(spec.options.get("simulation") or {}))
                  for spec in specs]
    if not blocks:
        blocks = [(None, dict(top))]
    defaults = SIMULATION.defaults()
    for run_id, block in blocks:
        if block.get("integrator") is None:
            continue
        given = {name: defaults.get(name) if block.get(name) is None else block[name]
                 for name in ("temperature_K", "friction_per_ps", "timestep_fs")}
        try:
            check_integrator(str(block["integrator"]),
                             temperature_K=float(given["temperature_K"]),
                             friction_per_ps=float(given["friction_per_ps"]),
                             timestep_fs=float(given["timestep_fs"]))
        except (TypeError, ValueError) as exc:
            if not isinstance(exc, StudyError):
                continue  # a value of the wrong type is refused by its own check
            # The run is named where the settings the refusal rests on are
            # its own rather than the top-level ones: the integrator alone for
            # one with no thermostat, all four for Brownian dynamics.
            rests_on = (("integrator",) if exc.refusal.code == "config.option.not_permitted"
                        else ("integrator", "friction_per_ps", "timestep_fs",
                              "temperature_K"))
            own = run_id is not None and any(block.get(k) != top.get(k) for k in rests_on)
            where = (f"Run {run_id} (its own settings, from `systems` or "
                     f"`sweep`): ") if own else ""
            raise ConfigError(where + str(exc), **_rewrapped(exc)) from exc


#: What a decision may say about the setting it is recorded for.
DECISION_KEYS = ("why", "source", "alternatives")


def settings_named() -> set[str]:
    """Every setting a decision may be about, by its dotted name: each
    phase's as `phase.name`, the scheduling block's as `execution.name`,
    and the top level's by its own name, `systems` and `sweep` among them:
    which structure a study simulates, and how many replicas, are the
    choices a reader most wants the reason for."""
    from fastmdxplora.config.schema import EXECUTION

    names = {f"{phase}.{field.name}" for phase, schema in PHASE_SCHEMAS.items()
             for field in schema.fields}
    names |= {f"execution.{field.name}" for field in EXECUTION.fields}
    names |= {field.name for field in TOP_LEVEL.fields if field.name != "decisions"}
    names |= {"systems", "sweep"}
    return names


def _check_decisions(decisions: Any) -> None:
    """Refuse a decision about a setting that does not exist, or one that
    does not say why: the record is read in place of the reasoning, so a
    misspelled name would attach a reason to nothing."""
    if not isinstance(decisions, dict):
        raise ConfigError(
            "`decisions` is a mapping from a setting's dotted name to why it "
            "has its value, for example `decisions: {setup.ph: {why: ...}}`.",
            code="config.option.wrong_type", option="decisions",
            context="top-level", expected_type="mapping",
            found_type=type(decisions).__name__)
    known = settings_named()
    for name, said in decisions.items():
        if str(name) not in known:
            raise ConfigError(
                f"`decisions` names '{name}', which is not a setting"
                f"{_suggest(str(name), known)}. A decision is recorded for a "
                "setting by its dotted name, such as `setup.ph`.",
                code="config.option.unknown", option=str(name),
                context="decisions", permitted=sorted(known),
                suggestion=_suggestion_only(str(name), known))
        if not isinstance(said, dict):
            raise ConfigError(
                f"The decision for `{name}` is a mapping with `why`, and "
                "optionally `source` and `alternatives`.",
                code="config.option.wrong_type", option=str(name),
                context="decisions", expected_type="mapping",
                found_type=type(said).__name__)
        for key in said:
            if key not in DECISION_KEYS:
                raise ConfigError(
                    f"The decision for `{name}` has '{key}'"
                    f"{_suggest(str(key), set(DECISION_KEYS))}. A decision says "
                    "`why`, and optionally `source` and `alternatives`.",
                    code="config.option.unknown", option=str(key),
                    context=f"decisions.{name}", permitted=list(DECISION_KEYS),
                    suggestion=_suggestion_only(str(key), set(DECISION_KEYS)))
        why = said.get("why")
        if not isinstance(why, str) or not why.strip():
            raise ConfigError(
                f"The decision for `{name}` says no `why`: a decision is its "
                "reason, in a sentence.",
                code="config.option.missing_companion", option=str(name),
                context="decisions", requires=["why"])
        if said.get("source") is not None and not isinstance(said["source"], str):
            raise ConfigError(
                f"The decision for `{name}` gives its `source` as "
                f"{type(said['source']).__name__}; it is text: `person`, "
                "`agent`, or a reference.",
                code="config.option.wrong_type", option="source",
                context=f"decisions.{name}", expected_type="str",
                found_type=type(said["source"]).__name__)
        alternatives = said.get("alternatives")
        if alternatives is not None and not isinstance(alternatives, list):
            raise ConfigError(
                f"The decision for `{name}` gives its `alternatives` as "
                f"{type(alternatives).__name__}; it is a list of the values "
                "set aside.",
                code="config.option.wrong_type", option="alternatives",
                context=f"decisions.{name}", expected_type="list",
                found_type=type(alternatives).__name__)


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


#: Top-level settings that describe the study rather than a phase, and so
#: have to travel beside :func:`phase_options` rather than inside it.
#:
#: Both are provenance. `agent` at the top level is the study's answer to
#: "who wrote this", and each phase may differ from it -- which is the
#: whole point of having two levels, and is why the study value cannot be
#: folded into the phase blocks: `resolve_agent_modes` compares the two to
#: find the departures, and a phase that agreed with a study value it could
#: not see was reported as departing from it.
STUDY_LEVEL_KEYS = ("agent", "agent_model", "budget_hours", "decisions")


def study_options(data: dict[str, Any]) -> dict[str, Any]:
    """Extract the study-level settings from a validated config.

    The companion to :func:`phase_options`, which keeps only the four phase
    blocks and therefore drops these. They reach a run through
    :class:`~fastmdxplora.batch.sweep.RunSpec` and are recorded in the
    manifest and the resolved config.
    """
    return {
        key: data[key]
        for key in STUDY_LEVEL_KEYS
        if data.get(key) is not None
    }


def canonical_phase_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Settle the top-level phase lists on their current names, in place.

    They were `include` and `exclude`, which is also what the analysis
    block calls the list of analyses to run: a config reading
    ``exclude: [setup]`` above ``analysis.exclude: [dimred]`` invites the
    reader to think the two are the same kind of thing.

    The earlier spellings are converted rather than refused. Every config
    written before this uses them, and a rename that breaks all of them on
    the day it lands is one nobody can adopt; the same courtesy
    `prepared_from` gets for `setup_from`. They go in the release that
    breaks compatibility deliberately.

    In place, and returning the same dict: `validate_config` settles
    umbrella windows and other normalisation by mutating what it is given,
    so handing back a copy would throw all of that away.
    """
    for earlier, current in (("include", "include_phase"),
                             ("exclude", "exclude_phase")):
        if earlier in data:
            if (current in data and data[current] is not None
                    and data[earlier] is not None
                    and _split_on_commas(data[current]) != _split_on_commas(data[earlier])):
                # Both spellings, saying different things: one would be
                # dropped without a word, so neither is chosen for the author.
                raise ConfigError(
                    f"Config sets both '{earlier}' ({data[earlier]!r}) and "
                    f"'{current}' ({data[current]!r}). They are one setting, "
                    f"'{earlier}' its earlier name; give it once, as "
                    f"'{current}'.",
                    code="config.option.conflicting",
                    options=[earlier, current], context="top-level")
            value = data.pop(earlier)
            # Both spellings present and agreeing: the current one stays, and
            # the earlier is dropped rather than left for something that
            # still reads it.
            data.setdefault(current, value)
    return data
