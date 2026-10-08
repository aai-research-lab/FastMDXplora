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

from fastmdxplora.config.schema import (
    ESSENTIAL, EXECUTION, PHASE_SCHEMAS, TOP_LEVEL, UNSET_MEANS,
)

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


#: Units written at the end of a setting's name, and how a page says them.
#: The longest first, so `_per_ps` is not read as `_ps`.
_UNITS = (
    ("_kjmol_per_nm", "kJ/mol/nm"),
    ("_per_ps", "1/ps"),
    ("_hours", "GPU hours"),
    ("_steps", "steps"),
    ("_amu", "amu"),
    ("_bar", "bar"),
    ("_atm", "atm"),
    ("_nm", "nm"),
    ("_ns", "ns"),
    ("_ps", "ps"),
    ("_fs", "fs"),
    ("_K", "K"),
    ("_M", "M"),
    ("_A", "\u00c5"),
)

#: Words a name spells in lower case that are written otherwise.
_WORDS = {
    "ph": "pH", "pdb": "PDB", "nvt": "NVT", "npt": "NPT", "cm": "centre-of-mass",
    "id": "ID", "ai": "AI", "pdf": "PDF", "cpu": "CPU", "gpu": "GPU",
    "forcefield": "force field", "pka": "pKa", "dcd": "DCD", "sdf": "SDF",
    "rmsd": "RMSD", "rmsf": "RMSF", "pca": "PCA", "dssp": "DSSP", "sasa": "SASA",
    "tica": "tICA", "tsne": "t-SNE", "dbscan": "DBSCAN", "cv": "CV",
}

#: Where the words of a name do not make the best label.
_LABELS = {
    "solvent_padding_nm": "Padding",
    "ion_concentration_M": "Salt",
    "force_field": "Force field files",
    "fixed_pdb": "Prepared PDB",
    "keep_water": "Retain crystal water",
    "remove_cm_motion": "Remove centre-of-mass motion",
    "duration_ns": "Production",
    "nvt_duration_ns": "NVT equilibration",
    "npt_duration_ns": "NPT equilibration",
    "stop_when": "Run until determined",
    "trajectory_interval_steps": "Frames every",
    "state_interval_steps": "State every",
    "checkpoint_interval_steps": "Checkpoint every",
    "save_selection": "Atoms in the frames",
    "budget_hours": "Spending ceiling",
    "agent_model": "AI model",
    "agent": "Written by",
    "select_atoms": "Atoms analysed",
    "first": "First frame",
    "last": "Last frame",
    "figure_colours": "Figure colours",
    "include_methods": "Methods section",
    "include_reproducibility": "Reproducibility section",
    "document": "Report (Markdown)",
    "pdf": "PDF",
    "slides": "Slides (PPTX)",
    "bundle": "Bundle (zip)",
    "continue_on_error": "Go on after a run fails",
    "device_index": "Device",
    "nvt_steps": "NVT equilibration",
    "npt_steps": "NPT equilibration",
    "production_steps": "Production",
}


#: What a setting is to the form, where the form treats it apart: a record
#: the software records rather than a choice made in the form, the length a
#: study runs for, a block of enhanced sampling, the model of an ensemble,
#: the analyses' own settings. Said here, beside the labels, so the page
#: names no setting and nothing on it can drift from the schema.
_ROLES = {
    "agent": "provenance",
    "agent_model": "provenance",
    "decisions": "provenance",
    "paper": "provenance",
    "setup.agent": "provenance",
    "simulation.agent": "provenance",
    "analysis.agent": "provenance",
    "report.agent": "provenance",
    "simulation.duration_ns": "production-length",
    "simulation.umbrella": "biased-sampling",
    "simulation.steered": "biased-sampling",
    "simulation.metadynamics": "biased-sampling",
    "simulation.plumed": "biased-sampling",
    "setup.model": "ensemble-member",
    "analysis.options": "per-analysis",
}


def label_and_unit(name: str) -> tuple[str, str]:
    """A setting's name as a page says it, and its unit, from the name.

    The unit is the name's own suffix, so a setting gains one by being
    named as the schema names things, and nothing here is held in step.
    """
    unit = ""
    stem = name
    for suffix, said in _UNITS:
        if name.endswith(suffix) and len(name) > len(suffix):
            unit, stem = said, name[: -len(suffix)]
            break
    if name in _LABELS:
        return _LABELS[name], unit
    words = [_WORDS.get(word, word) for word in stem.split("_") if word]
    text = " ".join(words)
    first = text[:1]
    if first.islower() and not text.startswith("pH") and not text.startswith("pKa") \
            and not text.startswith("tICA") and not text.startswith("t-SNE"):
        text = first.upper() + text[1:]
    return text, unit


def _summary(help_text: str | None) -> str:
    """The first sentence of a setting's help, which is what a page shows
    before More."""
    text = (help_text or "").strip()
    if not text:
        return ""
    import re

    # A sentence ends at a full stop followed by a space and a capital, so
    # "e.g. 300" and "0.15 M" do not end one.
    found = re.search(r"(?<!e\.g)(?<!i\.e)(?<!\d)\.\s+(?=[A-Z(`'\"])", text)
    return text[: found.start() + 1] if found else text


def _number_type(kind: Any) -> bool:
    kinds = kind if isinstance(kind, tuple) else (kind,)
    return bool(kinds) and all(k in (int, float) for k in kinds)


def _control(field: Any) -> str:
    if field.name == "residue_states":
        # Residues of this structure, each with a state it can take: rows
        # to choose from, and a histidine clicked in the picture.
        return "residues"
    if field.name == "stop_when":
        # Measures, each with the error it must reach, a ceiling and
        # whether replicas must agree: a form, not YAML in a box.
        return "stopping"
    if field.name == "plumed":
        # One script with an on-switch, not a mapping of settings. Drawn as
        # a mapping it asked for `script: |` with block-scalar indentation
        # around a working .dat file -- YAML inside a box, where one stray
        # tab changes a PLUMED input -- and the setting that exists to name
        # a file on the machine that runs was the one path in the form
        # without the Browse control every other path has.
        return "script"
    if field.type is bool and field.default is None:
        # Three answers, not two: on, off, or as the force field is
        # developed. A checkbox drawn unticked said "off" for a setting the
        # run would switch on, and ticking it and back wrote an explicit
        # false the person never meant.
        return "tristate"
    if _number_type(field.type):
        # A temperature is written 300 or 300.0, so its type is "int or
        # float", which the table below did not know: every such setting
        # (temperature, production length, timestep) was a text box.
        return "number"
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


def _measures() -> list[dict[str, str]]:
    """The measures a stopping rule may name, each with its name on the
    page and its unit, from the analyses that record a mean."""
    from fastmdxplora.gui.report_dashboard import unit_of
    from fastmdxplora.gui.series import SERIES

    try:
        from fastmdxplora.simulation.stopping import judgeable_analyses

        names = judgeable_analyses()
    except Exception:  # noqa: BLE001 - the analysis stack is optional here
        return []
    return [{"analysis": name, "label": SERIES.get(name, (name.replace("_", " "), ""))[0],
             "unit": unit_of(name)} for name in names]


def field_payload(field: Any, phase: str | None = None) -> dict[str, Any]:
    """One setting, as much as a browser needs to offer it.

    ``phase`` is the block it belongs to (``None`` for the top level), so
    what it does when unset and whether it is shown first can be looked up
    by its dotted name.
    """
    if field.name == "stop_when":
        return {**_field_payload(field, phase), "measures": _measures()}
    if field.name == "residue_states":
        from fastmdxplora.setup.pdbfix import RESIDUE_STATE_MEANING, RESIDUE_STATES

        return {**_field_payload(field, phase),
                "states": {name: list(states) for name, states in RESIDUE_STATES.items()},
                "meaning": dict(RESIDUE_STATE_MEANING)}
    return _field_payload(field, phase)


def _field_payload(field: Any, phase: str | None = None) -> dict[str, Any]:
    dotted = f"{phase}.{field.name}" if phase else field.name
    label, unit = label_and_unit(field.name)
    return {
        "name": field.name,
        "type": getattr(field.type, "__name__", str(field.type)),
        "control": _control(field),
        "label": label,
        "unit": unit,
        "help": field.help,
        "summary": _summary(field.help),
        "default": _jsonable(field.default),
        "unset": UNSET_MEANS.get(dotted),
        "choices": _jsonable(field.choices),
        "example": _jsonable(field.example),
        "minimum": field.minimum,
        "maximum": field.maximum,
        "essential": field.name in ESSENTIAL.get(phase or "(top-level)", ()),
        "role": _ROLES.get(dotted),
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
    # The themes are the Analysis page's own, so an analysis is found under
    # the same heading where it is chosen and where its result is read. The
    # form had a grouping of its own that put sixteen of thirty under
    # "Other", and named them differently from the page they open on.
    from fastmdxplora.gui.report_dashboard import ANALYSIS_THEMES

    of_group = {name: theme for theme, members in ANALYSIS_THEMES
                for name, _label in members}
    names_of = {name: label for _theme, members in ANALYSIS_THEMES
                for name, label in members}

    from fastmdxplora.analysis.orchestrator import get_analysis_class

    explanations: dict[str, Any] = {}
    categories: dict[str, str] = {}
    needs: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    for name, options in describe_all().items():
        # An analysis that works out its own atoms has nothing to apply a
        # general selection to, and offering a box that does nothing is worse
        # than offering none: it looks as though it worked.
        if not getattr(get_analysis_class(name), "honours_selection", True):
            options = tuple(o for o in options if o.name != "selection")
        explanations[name] = explain_analysis(name)
        categories[name] = of_group.get(name, "Other")
        cls = get_analysis_class(name)
        # What the analysis needs of the system to apply, as its class
        # declares it (the orchestrator reads the same attributes to plan
        # the default run), so the form can say why one will not run.
        needs[name] = sorted(
            attribute[len("requires_"):] for attribute in dir(cls)
            if attribute.startswith("requires_") and getattr(cls, attribute, False) is True)
        labels[name] = names_of.get(name, label_and_unit(name)[0])
        analyses[name] = [
            {
                "name": option.name,
                "label": label_and_unit(option.name)[0],
                "summary": _summary(option.help),
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
        "category_order": [theme for theme, _members in ANALYSIS_THEMES] + ["Other"],
        "needs": needs,
        # The reason an analysis the study must name gives until it is
        # chosen; the form takes it away once it is (gui/applicable.py).
        "when_chosen": _when_chosen(),
        "labels": labels,
    }


#: Top-level settings the form already has a place for, so they are not drawn
#: again as generic fields. Each is structural rather than a preference:
#: where the run goes, and which phases run.
_STRUCTURAL_TOP_LEVEL = {
    "output": "the output directory has its own field with a file picker",
    "include_phase": "the phase checkboxes",
    "exclude_phase": "the phase checkboxes",
}


def _checks() -> list[str]:
    from fastmdxplora.report.convergence import CHECKS

    return [said for _key, said, _short in CHECKS]


def schema_payload(defaults: Any = None) -> dict[str, Any]:
    """Every setting the software accepts, ready to be drawn.

    With ``defaults`` (the workspace's fastmdx-defaults.yml), each setting
    it gives is offered at that value as its default, marked as yours
    (``default_from``), since a study that leaves it unset runs with it."""
    from fastmdxplora.config.schema import grouped_fields

    phases = {
        phase: {
            "fields": [field_payload(f, phase) for f in group.fields],
            "description": getattr(group, "description", None),
            # The same settings, in named groups. Thirty-odd controls in one
            # grid is a list to read rather than a form to fill in, and the
            # schema is the one place that knows what each setting is about.
            "groups": [
                {"title": title,
                 "why": why,
                 "fields": [field_payload(f, phase) for f in fields]}
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
    execution_options = [field_payload(field, "execution") for field in EXECUTION.fields]
    # Every setting a sweep can vary, by its dotted name: any phase setting
    # that takes one value. A block of settings is not one value.
    sweep_axes = [f"{phase}.{field.name}" for phase, group in PHASE_SCHEMAS.items()
                  for field in group.fields if field.type is not dict]
    from fastmdxplora.gui.starters import starters_payload

    payload = {
        "phases": phases,
        "run_options": run_options,
        "execution_options": execution_options,
        "sweep_axes": sweep_axes,
        "analysis_options": _analysis_options(),
        # Complete studies to start from, each with its plan (starters.py).
        "starters": starters_payload(),
        # What a run is held to after it ends, from the one list the report
        # ticks and the Agent's plan states, for the form's review.
        "checks": _checks(),
    }
    return payload if defaults is None else _with_your_defaults(payload, defaults)


def _with_your_defaults(payload: dict[str, Any], defaults: Any) -> dict[str, Any]:
    """The payload with each setting your defaults give offered at their
    value, marked with the file it comes from."""
    given = dict(defaults.settings())
    name = defaults.path.name

    def mark(field: dict[str, Any], block: str) -> None:
        dotted = f"{block}.{field['name']}"
        if dotted in given:
            field["default"] = _jsonable(given[dotted])
            field["default_from"] = name

    for block, group in payload["phases"].items():
        for field in group["fields"]:
            mark(field, block)
        for section in group["groups"]:
            for field in section["fields"]:
                mark(field, block)
    for field in payload["execution_options"]:
        mark(field, "execution")
    payload["defaults_from"] = name
    return payload


def _when_chosen() -> str:
    from fastmdxplora.gui.applicable import SAID

    return SAID["naming"]
