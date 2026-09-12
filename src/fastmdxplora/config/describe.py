"""The config language, described to something that has not read the docs.

The GUI's form, the CLI's flags and the Python API all come from
:mod:`fastmdxplora.config.schema`, so none of them can drift from the
others or from what validation accepts. This is the fourth reader of that
same declaration, and it exists because a language model proposing a
config needs to be told what the language is, and being told by hand is
how the four become five things that disagree.

Natural language interfaces are old -- the database ones date to the
seventies -- and their standing failure is the plausible query: well
formed, semantically wrong, silently answered. The target language is
what fixes that, not the parser. A description generated from the schema
cannot offer a setting that does not exist, and a config built from it
still has to survive :func:`~fastmdxplora.config.loader.validate_config`
before anything reaches a GPU.

So this renders, and it does not parse. What comes back from a model is
a config like any other, and it goes through the same door.

Nothing here imports a model client, and nothing here holds a key. The
caller supplies whatever it wants to talk to; this package's business is
saying what a study may contain.
"""

from __future__ import annotations

from typing import Any, Iterable

from fastmdxplora.config.schema import (
    ANALYSIS,
    PHASE_SCHEMAS,
    REPORT,
    SETUP,
    SIMULATION,
    TOP_LEVEL,
    Field,
    PhaseSchema,
)

__all__ = [
    "describe_schema",
    "describe_field",
    "schema_as_json",
]


def _type_name(declared: type | tuple[type, ...]) -> str:
    """The type, in words a reader uses rather than Python's."""
    if isinstance(declared, tuple):
        return " or ".join(_type_name(one) for one in declared)
    return {
        bool: "true/false",
        int: "whole number",
        float: "number",
        str: "text",
        list: "list",
        dict: "mapping",
    }.get(declared, getattr(declared, "__name__", str(declared)))


def describe_field(field: Field, *, verbose: bool = True) -> str:
    """One setting, as a line.

    The help text is used whole rather than truncated. It is the most
    valuable thing in the registry for this purpose and it was written
    for a reader who does not already know the answer, which is exactly
    the audience here. ``membrane_orientation_checked`` explains that the
    bilayer is built in the xy plane, that a deposited entry is rarely
    aligned to z, and that setup refuses rather than embedding a protein
    sideways. A model that has read that will not propose a membrane
    study without it; one given only the field's name will.
    """
    parts = [f"{field.name} ({_type_name(field.type)})"]
    if field.choices:
        parts.append("one of: " + ", ".join(str(c) for c in field.choices))
    if field.default is not None:
        parts.append(f"default {field.default!r}")
    head = "  - " + "; ".join(parts)
    if not verbose or not field.help:
        return head
    body = " ".join(str(field.help).split())
    return f"{head}\n      {body}"


def _phase_section(name: str, schema: PhaseSchema, *, verbose: bool) -> str:
    lines = [f"## {name}"]
    if getattr(schema, "help", None):
        lines.append(" ".join(str(schema.help).split()))
    for field in schema.fields:
        lines.append(describe_field(field, verbose=verbose))
    return "\n".join(lines)


def describe_schema(
    *,
    phases: Iterable[str] | None = None,
    verbose: bool = True,
) -> str:
    """The whole config language, as text to put in front of a model.

    Parameters
    ----------
    phases
        Which phase blocks to include. All of them by default. A caller
        working on setup alone can leave the other three out, which is
        worth doing: a shorter description is a cheaper call and a model
        cannot propose a setting it was never shown.
    verbose
        Include each setting's help text. On by default, because the help
        is where the refusals are explained and a model that has read them
        proposes fewer studies that will be refused.

    Notes
    -----
    Generated, never written. Adding a field to
    :mod:`fastmdxplora.config.schema` adds it here, to the GUI form, to
    the CLI and to validation in the same commit, and there is no fifth
    place that has to be remembered.
    """
    wanted = list(phases) if phases is not None else list(PHASE_SCHEMAS)
    sections = [
        "A FastMDXplora study is a YAML mapping. Top-level keys are below, "
        "and each phase takes a mapping of its own settings.",
        "",
        "Every setting is optional unless a phase says otherwise, and an "
        "unknown key is refused rather than ignored -- so a misspelling "
        "stops the study instead of silently running with a default.",
        "",
        _phase_section("top level", TOP_LEVEL, verbose=verbose),
    ]
    for phase in wanted:
        schema = PHASE_SCHEMAS.get(phase)
        if schema is not None:
            sections.append("")
            sections.append(_phase_section(phase, schema, verbose=verbose))
    return "\n".join(sections)


def schema_as_json(*, phases: Iterable[str] | None = None) -> dict[str, Any]:
    """The same declaration, as data rather than prose.

    For a caller building a tool schema, a JSON-schema document, or a
    structured-output constraint. The point of returning this rather than
    only the text is that the constraint and the description then come
    from one source: a model told about a setting is a model permitted to
    set it, and neither list can gain an entry the other lacks.
    """
    def block(schema: PhaseSchema) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field in schema.fields:
            entry: dict[str, Any] = {"type": _type_name(field.type)}
            if field.choices:
                entry["enum"] = list(field.choices)
            if field.default is not None:
                entry["default"] = field.default
            if field.help:
                entry["description"] = " ".join(str(field.help).split())
            out[field.name] = entry
        return out

    wanted = list(phases) if phases is not None else list(PHASE_SCHEMAS)
    document = {"top_level": block(TOP_LEVEL)}
    for phase in wanted:
        schema = PHASE_SCHEMAS.get(phase)
        if schema is not None:
            document[phase] = block(schema)
    return document
