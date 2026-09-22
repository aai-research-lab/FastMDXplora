"""The settings, described well enough for a browser to draw them.

The dashboard's form was written by hand, one control at a time, which is why
it offered eleven of the eighty-three settings that exist: every new field had
to be noticed and added, and a field nobody noticed was simply unreachable from
the browser. Whole phases were missing -- there was no way to configure an
analysis or a report at all.

So the form is not written any more. This turns the schema into something a
browser can render: a name, a type, what it means, what values it accepts, and
what it does if left alone. Adding a field to the schema puts a control in the
dashboard, and nothing has to be kept in step by hand.

The per-analysis settings are read the same way, from the analyses themselves,
via ``analysis.describe``. Those need the chemistry stack, so they are asked
for separately and their absence is reported rather than raised: a dashboard
that cannot offer clustering options is worth more than one that will not load.
"""

from __future__ import annotations

from typing import Any

from fastmdxplora.config.schema import EXECUTION, PHASE_SCHEMAS, TOP_LEVEL

__all__ = ["schema_payload", "field_payload"]


#: How a value is best asked for. The schema says what a field *is*; this says
#: what a browser should put on the screen for it. Kept here rather than in the
#: schema because it is a fact about drawing, not about configuring.
_CONTROL_FOR_TYPE = {
    bool: "checkbox",
    int: "number",
    float: "number",
    str: "text",
    list: "list",
    dict: "mapping",
}


def _control(field: Any) -> str:
    if field.name == "plumed":
        # One script with an on-switch, not a mapping of settings. Drawn as
        # a mapping it asked for `script: |` with block-scalar indentation
        # around a working .dat file -- YAML inside a box, where one stray
        # tab changes a PLUMED input -- and the setting that exists to name
        # a file on the machine that runs was the one path in the form
        # without the Browse control every other path has.
        return "script"
    if field.choices:
        # A list of choices is not a choice. `include` and `exclude` take
        # several analyses, and offered as a single select the form could
        # express only one of them -- so they were left as free text, where a
        # misspelling was found only when the run did not do what was asked.
        return "multiselect" if field.type is list else "select"
    return _CONTROL_FOR_TYPE.get(field.type, "text")


def _jsonable(value: Any) -> Any:
    """Values reach the browser as JSON, and tuples are not JSON."""
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, dict)):
        return value
    return str(value)


def field_payload(field: Any) -> dict[str, Any]:
    """One setting, as much as a browser needs to offer it."""
    return {
        "name": field.name,
        "type": getattr(field.type, "__name__", str(field.type)),
        "control": _control(field),
        "help": field.help,
        "default": _jsonable(field.default),
        "choices": _jsonable(field.choices),
        "example": _jsonable(field.example),
        "required": bool(getattr(field, "required", False)),
    }


def _analysis_options() -> dict[str, Any]:
    """What each analysis can be told, read from the analyses themselves.

    Importing them needs MDTraj. Where it is absent the dashboard should still
    load and still offer everything else, so the failure is described rather
    than raised.
    """
    try:
        import fastmdxplora.analysis  # noqa: F401  (populates the registry)
        from fastmdxplora.analysis.describe import (
            describe_all, explain_analysis,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "reason": (
                "Per-analysis settings need the analysis stack, which is not "
                f"installed here ({type(exc).__name__}). Everything else on "
                "this page still works."
            ),
            "analyses": {},
            "explanations": {},
        }

    analyses: dict[str, Any] = {}
    #: What each analysis is about, so fifteen of them can be read as a few
    #: groups rather than a list. An analysis appearing in none of these is
    #: shown under "other" rather than hidden: a grouping that silently drops
    #: things is worse than no grouping.
    groups = {
        "Shape and size": ("rmsd", "rg", "sasa", "ss"),
        "Flexibility": ("rmsf", "dihedrals"),
        "Conformations": ("cluster", "dimred"),
        "Folding": ("qvalue", "hbonds"),
        "The ligand": ("ligand_rmsd", "ligand_rmsf"),
        "Protein and ligand together": ("pl_contacts", "pl_hbonds",
                                        "pl_interactions"),
    }
    of_group = {name: title for title, names in groups.items() for name in names}

    from fastmdxplora.analysis.orchestrator import get_analysis_class

    explanations: dict[str, Any] = {}
    categories: dict[str, str] = {}
    for name, options in describe_all().items():
        # An analysis that works out its own atoms has nothing to apply a
        # general selection to, and offering a box that does nothing is worse
        # than offering none: it looks as though it worked.
        if not getattr(get_analysis_class(name), "honours_selection", True):
            options = tuple(o for o in options if o.name != "selection")
        explanations[name] = explain_analysis(name)
        categories[name] = of_group.get(name, "Other")
        analyses[name] = [
            {
                "name": option.name,
                # An option whose default is a list takes several of its
                # accepted values, not one. Offering a single select would
                # make the two clustering methods that run by default
                # unreachable together; offering a text box asks somebody to
                # type "kmeans, hierarchical" and get the spelling right.
                "control": (
                    ("multiselect"
                     if isinstance(option.default, (list, tuple))
                     else "select")
                    if option.choices
                    else _CONTROL_FOR_TYPE.get(type(option.default), "text")
                ),
                "help": option.help,
                "default": _jsonable(option.default),
                "choices": _jsonable(option.choices),
                "type_text": option.type_text,
                # Settings every analysis shares come from the base class and
                # are worth grouping apart from the ones that make an analysis
                # what it is.
                "shared": option.owner == "Analysis",
                # Something the run works out for itself. Asking for it
                # invites somebody to type a ligand name that does not match
                # the one detected, and the analysis would then find nothing
                # and say the ligand was absent.
                "supplied_by_the_run": bool(
                    option.help and "orchestrator" in option.help.lower()
                ),
                # A path, so the page can offer a picker rather than a box to
                # type one into.
                "is_path": option.name.endswith(("_file", "_chemistry", "_path")),
            }
            for option in options
        ]
    return {
        "available": True,
        "reason": None,
        "analyses": analyses,
        "explanations": explanations,
        "categories": categories,
        "category_order": list(groups) + ["Other"],
    }


#: Top-level settings the form already has a place for, so they are not drawn
#: again as generic fields. Each is structural rather than a preference:
#: where the run goes, and which phases run.
_STRUCTURAL_TOP_LEVEL = {
    "output": "the output directory has its own field with a file picker",
    "include_phase": "the phase checkboxes",
    "exclude_phase": "the phase checkboxes",
}


def schema_payload() -> dict[str, Any]:
    """Every setting the software accepts, ready to be drawn."""
    from fastmdxplora.config.schema import grouped_fields

    phases = {
        phase: {
            "fields": [field_payload(f) for f in group.fields],
            "description": getattr(group, "description", None),
            # The same settings, in named groups. Thirty-odd controls in one
            # grid is a list to read rather than a form to fill in, and the
            # schema is the one place that knows what each setting is about.
            "groups": [
                {"title": title,
                 "why": why,
                 "fields": [field_payload(f) for f in fields]}
                for title, why, fields in grouped_fields(phase)
            ],
        }
        for phase, group in PHASE_SCHEMAS.items()
    }
    # Top-level settings that are preferences rather than structure. These
    # reached the command line and the config file and not the form, so the
    # browser was the one interface that could not turn them on or off.
    run_options = [
        field_payload(field) for field in TOP_LEVEL.fields
        if field.name not in _STRUCTURAL_TOP_LEVEL
    ]
    # How the runs are scheduled. Not a phase -- it is not in the plan and
    # cannot be included or excluded -- so it is offered beside the run
    # options rather than as a fifth tab. It reached the config file and
    # nothing else: no flag, no control, which is the same gap the comment
    # above records closing for the top-level settings.
    execution_options = [field_payload(field) for field in EXECUTION.fields]
    # Every setting a sweep can vary, by its dotted name: any phase setting
    # that takes one value. A block of settings is not one value.
    sweep_axes = [f"{phase}.{field.name}" for phase, group in PHASE_SCHEMAS.items()
                  for field in group.fields if field.type is not dict]
    return {
        "phases": phases,
        "run_options": run_options,
        "execution_options": execution_options,
        "sweep_axes": sweep_axes,
        "analysis_options": _analysis_options(),
    }
