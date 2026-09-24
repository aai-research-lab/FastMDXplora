"""What each phase method accepts, written from the schema.

The command line and the GUI are generated from the schema: every setting
becomes a flag, and a form field, with its type, default and help. The
Python phase methods took ``**kwargs`` under a one-line docstring, so
``help(FastMDXplora.simulate)`` named no setting and an editor offered
none. From the same schema:

* each phase method's docstring, built when the package loads, so it
  cannot drift from what the validator accepts;
* ``phase_settings_types.py``, one ``TypedDict`` per phase, which the
  methods are annotated with for editors and type checkers. It is a
  generated file; ``python -m fastmdxplora.config.phase_settings`` writes it
  again, and a test fails while it is out of date.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

#: The phase methods and the schema section each takes its settings from.
PHASES = {"setup": "setup", "simulate": "simulation", "analyze": "analysis", "report": "report"}

TYPES_FILE = Path(__file__).with_name("phase_settings_types.py")


def _schema(section: str) -> Any:
    from fastmdxplora.config.schema import all_schemas

    return all_schemas()[section]


def _type_text(field: Any) -> str:
    """How a setting's type reads, in the docstring and in the TypedDict."""
    choices = getattr(field, "choices", None)
    kinds = field.type if isinstance(field.type, tuple) else (field.type,)
    if choices and all(isinstance(c, str) for c in choices):
        # The choices are what the setting holds, or, for a list, its items.
        one = "Literal[" + ", ".join(repr(c) for c in choices) + "]"
        forms = [f"list[{one}]" if kind is list else one for kind in kinds]
        return " | ".join(dict.fromkeys(forms))
    names = []
    for kind in kinds:
        name = {str: "str", bool: "bool", int: "int", float: "float",
                list: "list[Any]", dict: "dict[str, Any]"}.get(kind, "Any")
        if name not in names:
            names.append(name)
    # int where float is accepted is float to a type checker already.
    if names == ["int", "float"]:
        return "float"
    return " | ".join(names)


def _bounds(field: Any) -> str:
    low, high = getattr(field, "minimum", None), getattr(field, "maximum", None)
    if low is not None and high is not None:
        return f", from {low} to {high}"
    if low is not None:
        return f", at least {low}"
    if high is not None:
        return f", at most {high}"
    return ""


def docstring(method: str) -> str:
    """The docstring of one phase method: what it does, then every setting."""
    section = PHASES[method]
    intro = ("Every setting is keyword-only and optional, the same settings a "
             f"configuration's ``{section}:`` block takes, and the validator checks them "
             "before the phase runs.")
    lines = [f"Run only the {section} phase.", "", *textwrap.wrap(intro, width=76), "",
             "Settings", "--------"]
    for field in _schema(section).fields:
        default = "" if field.default is None else f", default {field.default!r}"
        lines.append(f"{field.name} : {_type_text(field)}{_bounds(field)}{default}")
        help_text = " ".join(str(field.help or "").split())
        if help_text:
            lines.extend(textwrap.wrap(help_text, width=72, initial_indent="    ",
                                       subsequent_indent="    "))
    return "\n".join(lines)


def _annotation(field: Any) -> list[str]:
    """One TypedDict line, or a Literal set out across lines where it is long."""
    text = f"    {field.name}: {_type_text(field)}"
    if len(text) <= 100 or "Literal[" not in text:
        return [text]
    head, body = text.split("Literal[", 1)
    closing = "]" * (len(body) - len(body.rstrip("]")))
    if " | " in body or closing != ("]]" if head.endswith("list[") else "]"):
        return [text + "  # noqa: E501"]
    lines, line = [head + "Literal["], "        "
    for choice in body[:-len(closing)].split(", "):
        piece = choice + ", "
        if len(line) + len(piece) > 100:
            lines.append(line.rstrip())
            line = "        "
        line += piece
    lines.append(line.rstrip().rstrip(","))
    lines.append("    " + closing)
    return lines


def types_source() -> str:
    """The text of ``phase_settings_types.py``, as it should stand."""
    out = ['"""One TypedDict per phase method, generated from the schema.',
           "",
           "Written by ``python -m fastmdxplora.config.phase_settings``; not edited by",
           'hand. A test fails while it disagrees with the schema."""',
           "",
           "from __future__ import annotations",
           "",
           "from typing import Any, Literal, TypedDict",
           "",
           '__all__ = ["' + '", "'.join(f"{m.capitalize()}Settings" for m in PHASES) + '"]',
           ""]
    for method, section in PHASES.items():
        out += ["", f"class {method.capitalize()}Settings(TypedDict, total=False):",
                f'    """What ``{method}()`` accepts: the ``{section}:`` settings."""', ""]
        for field in _schema(section).fields:
            out.extend(_annotation(field))
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def write_types() -> Path:
    TYPES_FILE.write_text(types_source(), encoding="utf-8")
    return TYPES_FILE


if __name__ == "__main__":
    print(f"Wrote {write_types()}")
