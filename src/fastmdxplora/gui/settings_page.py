"""This folder's defaults, as GUI Settings shows and changes them (IMDE 2.9).

The workspace's ``fastmdx-defaults.yml`` fills what a new study leaves unset,
from every interface alike (`config/defaults_file.py`); the GUI showed its
values on New study and could not change them. Settings' This folder lists
every setting the file may hold, with FastMDXplora's own default beside it,
the file's value and its reason where it gives one; a value set there is
written into the file, checked as any defaults file is, and Use
FastMDXplora's takes it out. The file is the one the GUI reads for new
studies: the nearest above where they are put, else a new one there.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

# The settings of one study, which a defaults file refuses: not offered.
from fastmdxplora.config.defaults_file import _ONE_STUDY_S

__all__ = ["defaults_changed", "defaults_said", "has_comments"]

#: The controls This folder draws; a setting drawn another way (a list of
#: residues, a stopping rule, a script, a mapping) is shown as the file
#: holds it and changed by hand.
_DRAWN = {"select", "number", "checkbox", "tristate", "text", "list", "multiselect"}

#: One change at a time: each request is answered in its own thread, and
#: two changes read, changed and written over each other lost the first.
_WRITING = threading.Lock()


def has_comments(text: str) -> bool:
    """Whether YAML ``text`` has a comment: a ``#`` that starts a line or
    follows a space, outside a quoted string (a colour, ``'#4E79A7'``, is
    not one). Written again from its values, the file would lose them."""
    for line in text.splitlines():
        quoted = ""
        before = " "
        for char in line:
            if quoted:
                if char == quoted:
                    quoted = ""
            elif char in "'\"" and before in " \t:-[{,":
                quoted = char
            elif char == "#" and before in " \t":
                return True
            before = char
    return False


def _where(root: Path) -> Path:
    from fastmdxplora.config.defaults_file import DEFAULTS_FILE, find_defaults

    return find_defaults(root) or (Path(root).expanduser() / DEFAULTS_FILE)


def _items(field: dict[str, Any]) -> str:
    """What a list setting holds, as This folder writes it from words split
    at commas: "number", "text", or "" where its items are not words (a
    list of mappings, as restraints written as blocks)."""
    example = field.get("example")
    if isinstance(example, list) and example:
        if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in example):
            return "number"
        return "text" if all(isinstance(x, str) for x in example) else ""
    said = str(field.get("unset") or "")
    try:
        if said and all(float(x) == float(x) for x in said.split(",")):
            return "number"
    except ValueError:
        pass
    return "text"


def _fields() -> list[dict[str, Any]]:
    from fastmdxplora.gui.schema_payload import schema_payload

    # FastMDXplora's own defaults: the payload without the file's.
    payload = schema_payload()
    out: list[dict[str, Any]] = []
    blocks = list(payload["phases"].items()) + [("execution", {"fields": payload["execution_options"]})]
    for block, group in blocks:
        for field in group["fields"]:
            if (field.get("role") == "provenance"
                    or f"{block}.{field['name']}" in _ONE_STUDY_S):
                continue
            control = field["control"] if field["control"] in _DRAWN else ""
            items = _items(field) if control == "list" else ""
            if control == "list" and not items:
                control = ""
            example = field.get("example")
            out.append({
                "name": f"{block}.{field['name']}", "block": block,
                "label": field["label"], "unit": field["unit"], "summary": field["summary"],
                "help": field.get("help") or field["summary"],
                "control": control, "items": items,
                "example": ", ".join(str(x) for x in example) if isinstance(example, list) and items
                else ("" if example is None or isinstance(example, (list, dict)) else str(example)),
                "type": field["type"], "choices": field["choices"],
                "minimum": field["minimum"], "maximum": field["maximum"],
                "exclusive_minimum": bool(field.get("exclusive_minimum")),
                "default": field["default"], "unset": field["unset"],
                "essential": field["essential"],
            })
    return out


def _of_the_file(reason: str, path: Path) -> str:
    """A refusal said beside the file's own name: not its path again."""
    if reason.startswith(f"{path}: "):
        reason = reason[len(f"{path}: "):]
    elif reason.startswith(f"{path} "):
        reason = "It " + reason[len(f"{path} "):]
    return reason[:1].upper() + reason[1:]


def defaults_said(root: str | Path) -> dict[str, Any]:
    """The defaults file for studies put in ``root``: where it is, whether it
    is there and can be written by Settings, the settings it gives with
    their reasons, and every setting it may hold."""
    from fastmdxplora.config.defaults_file import read_defaults
    from fastmdxplora.config.loader import ConfigError

    path = _where(Path(root))
    said: dict[str, Any] = {"ok": True, "path": str(path), "exists": path.is_file(),
                            "values": {}, "whys": {}, "refused": None, "comments": False,
                            "fields": _fields()}
    if not path.is_file():
        return said
    try:
        said["comments"] = has_comments(path.read_text(encoding="utf-8"))
        found = read_defaults(path)
    except (OSError, ConfigError) as exc:
        said["refused"] = _of_the_file(str(exc), path)
        return said
    said["values"] = dict(found.settings())
    said["whys"] = dict(found.whys)
    return said


def defaults_changed(root: str | Path, body: Any) -> dict[str, Any]:
    """The file written as Settings asked (``set``: {name: value}, ``unset``:
    [name], ``why``: {name: reason}), and said again; or why not."""
    from fastmdxplora.config.defaults_file import write_defaults
    from fastmdxplora.config.loader import ConfigError

    asked = body if isinstance(body, dict) else {}
    given = asked.get("set") if isinstance(asked.get("set"), dict) else {}
    unset = [str(n) for n in asked.get("unset") or [] if isinstance(n, str)]
    whys = asked.get("why") if isinstance(asked.get("why"), dict) else {}
    path = _where(Path(root))
    with _WRITING:
        try:
            if path.is_file() and has_comments(path.read_text(encoding="utf-8")):
                said = defaults_said(root)
                said.update({"ok": False, "reason": (
                    "The file has comments in it, which writing it again would lose. Change "
                    "it by hand, or take its comments out to change it here.")})
                return said
            write_defaults(path, given=given, unset=unset, whys=whys)
        except ConfigError as exc:
            # Said beside the setting, under the file's own name.
            said = defaults_said(root)
            said.update({"ok": False, "reason": _of_the_file(str(exc), path)})
            return said
        except OSError as exc:
            said = defaults_said(root)
            said.update({"ok": False, "reason": _of_the_file(
                f"{path} could not be written: {exc.strerror or exc}.", path)})
            return said
        return defaults_said(root)
