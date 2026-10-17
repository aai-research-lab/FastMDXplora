"""What the Agent can look at with the software's own tools, before it answers.

The Agent wrote YAML and answered from what it was given. What it could not
do was ask: how big a box setup will build from this structure, how long the
study takes here, whether a config it is about to hand over would be refused,
which chains and ligands a structure holds, how many atoms a selection
matches. The software answers every one of those, and an AI model left to guess
them states sizes, times and chain names it made up.

So the Agent may look before it answers. A reply whose first line is
``USE: <tool>``, with the tool's arguments as YAML on the lines after it, is
not an answer: the tool is run and the AI model is asked again with what it
said. The tools only look. Nothing is run, written or started by one, and
what each says is the software's own words, quoted to the AI model and shown to
the person under the answer as what the Agent checked.

The rule the Agent was built on holds: the AI model never judges convergence or
chemistry. A tool says what the software measured or refused; the AI model may
repeat it and may not overrule it.

Tools can be added from outside without changing this file: an installed
package names them under the ``fastmdxplora.agent_tools`` entry point, or a
program hands them to :class:`Toolbox` as ``extra``. Each is an
:class:`AgentTool`, held to the same contract as the tools here: it only
looks. One cannot take the name of a tool already here, and one that fails
to load is left out with a warning rather than stopping the Agent.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from fastmdxplora.offered_tools import AGENT as _AGENT
from fastmdxplora.offered_tools import arguments_said as _arguments_said
from fastmdxplora.offered_tools import offered_by as _offered_by
from fastmdxplora.offered_tools import offered_named as _offered_named
from fastmdxplora.offered_tools import schema_for as _schema_for
from fastmdxplora.refusals import CodedError

__all__ = ["AgentTool", "ENTRY_POINT_GROUP", "LOOK_WORDS", "Look", "ToolRefused", "Toolbox",
           "current_view_tool", "look_said", "plugged_in", "use_in", "MOST_LOOKS"]

logger = logging.getLogger("fastmdx.agent.tools")

#: Where an installed package names tools for the Agent.
ENTRY_POINT_GROUP = "fastmdxplora.agent_tools"

#: A tool's name: lower case, as ``USE:`` is read, and short.
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_warned: set[str] = set()


def _warn_once(message: str, *args: Any) -> None:
    """A tool left out is said once, not at every reply that lists the tools."""
    said = message % args
    if said not in _warned:
        _warned.add(said)
        logger.warning(said)

#: Looks the AI model may take before one answer. Enough to inspect a
#: structure, preview a setup and check the config; beyond that an AI model is
#: exploring rather than answering, and each look is a round trip.
MOST_LOOKS = 4

#: The most of a tool's answer given to the AI model and shown to the person.
MOST_SAID = 4000

# Hints describe what the browser is showing. They are deliberately smaller
# than evidence, and the current-view tool sends facts to the existing tools.
MOST_VIEW_HINTS = 12
MOST_VIEW_HINT_TEXT = 512
_VIEW_HINT_KEYS = frozenset({
    "analysis", "colour", "expression", "field", "file", "frame", "page",
    "representation", "section", "selection", "study", "superposed", "system",
})

#: A structure a tool may read: a PDB identifier or a structure file. Only
#: these, so a tool asked for a path reads coordinates and nothing else.
_PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")
_STRUCTURE_SUFFIXES = (".pdb", ".ent", ".cif", ".mmcif", ".pdbx")


@dataclass(frozen=True)
class Look:
    """One tool used: what was asked, and what the software said."""

    tool: str
    asked: dict[str, Any]
    said: str
    ok: bool

    def as_record(self) -> dict[str, Any]:
        return {"tool": self.tool, "asked": _brief(self.asked), "said": self.said,
                "ok": self.ok, "label": look_said(self.tool, self.asked),
                "found": self.found()}

    def found(self) -> str:
        """What the look found, in one line: the first of what the software
        said, or why it was refused."""
        first = next((line.strip() for line in self.said.splitlines() if line.strip()), "")
        first = first if len(first) <= 160 else first[:157].rstrip() + "..."
        return first if self.ok else f"Refused: {first}"


#: What each look is called where it is shown (the Agent page, `fastmdx
#: agent`), done and while it is being done, and the argument it names.
#: The page showed the tool's own name for any look not listed here
#: (`find_structure`, `list_studies`, ...), which reads as code. From the
#: one list of tools (:mod:`fastmdxplora.offered_tools`).
LOOK_WORDS: dict[str, tuple[str, str, str]] = {
    tool.name: tool.look_words for tool in _offered_by(_AGENT) if tool.look_words}


def look_said(tool: str, asked: dict[str, Any] | None, *, doing: bool = False) -> str:
    """A look as the person reads it: "Looked up "trp-cage" in the PDB",
    "Inspecting 1L2Y"; a tool from outside by its name."""
    words = LOOK_WORDS.get(tool)
    if words is None:
        return f"{'Using' if doing else 'Used'} {tool}"
    done, going, named = words
    text = going if doing else done
    if "{}" not in text:
        return text
    if tool == "read_docs":
        # A question, else a section or a page read whole; nothing asked, the
        # pages. A look the software refuses is said as refused, from the
        # same reading of what was asked.
        try:
            query, page, section, _ = _docs_call(asked or {})
        except _Refused:
            return "Reading the docs" if doing else "Read the docs"
        value = query or section or page or ""
        if not value:
            return "Listing the docs' pages" if doing else "Listed the docs' pages"
    else:
        value = " ".join(str((asked or {}).get(named) or "").split())
    if tool == "inspect_structure":
        value = Path(value).name if value else "the structure"
    else:
        value = f"\u201c{value[:60]}\u201d" if value else "a name"
    return text.format(value)


@dataclass(frozen=True)
class AgentTool:
    """A tool for the Agent from outside this module.

    ``look`` is called with the :class:`Toolbox` (for its ``path_for``) and
    the arguments the AI model gave, and returns what the software found, in
    words. It may raise :class:`ToolRefused` to decline; any other error is
    said to the AI model as the tool's failure. It must only look: nothing
    run, written or started.
    """

    name: str
    arguments: str
    what: str
    look: Callable[[Toolbox, dict[str, Any]], str]


@dataclass
class Toolbox:
    """The tools, and what they may reach.

    ``path_for`` is the server's rule for a path a request names (inside the
    workspace, when hosted), the same rule the builder's preview is held to;
    ``None`` from it means refused. ``extra`` adds tools for this toolbox
    only, after those here and those installed packages provide.
    ``workspace`` is the folder of studies, for list_studies.
    """

    path_for: Callable[[Any], str | None] | None = None
    looks: list[Look] = field(default_factory=list)
    extra: tuple[AgentTool, ...] = ()
    #: The folder of studies list_studies lists (a
    #: :class:`fastmdxplora.mcp.workspace.Workspace`), where there is one.
    workspace: Any = None
    #: The study open on the page, where there is one: read_study_file reads
    #: a file of it, or of a study in the workspace, and of no other folder.
    open_study: Any = None
    #: Used by an AI app's server (`fastmdx mcp`) rather than by the Agent:
    #: what a look says next names the server's tools, and a path is
    #: refused naming the workspace.
    for_ai_app: bool = False
    #: Whether studies may be started from here (an AI app's server may
    #: be read-only), for what a look says can be done next.
    runs: bool = True
    #: A hosted GUI (MDXplora): other machines are the host's, and not
    #: reached from it, so the looks at them are not offered.
    hosted: bool = False

    def _table(self) -> dict[str, tuple[str, str, Callable[[Toolbox, dict[str, Any]], str]]]:
        """Every tool this toolbox has: those here, then installed, then extra."""
        table = dict(_TOOLS)
        if self.hosted:
            for name in _NOT_HOSTED:
                table.pop(name, None)
        for tool in (*plugged_in(), *_checked(self.extra, "given as extra")):
            if tool.name in table:
                _warn_once("Agent tool %r is already taken; the one given later "
                           "is left out.", tool.name)
                continue
            table[tool.name] = (tool.arguments, tool.what, tool.look)
        return table

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._table())

    def describe(self) -> str:
        """The tools as the text protocol's prompt states them."""
        lines = [
            "## Looking before you answer",
            "You can look with the software's own tools before you reply. Reply "
            "with a first line `USE: <tool>` and, on the lines after it, the "
            "tool's arguments as YAML, and nothing else. The software runs the "
            "tool and asks you again with what it said, under \"What the "
            f"software said\". At most {MOST_LOOKS} looks before each reply. "
            "The tools only look: nothing is run, written or started by one.",
            "",
            _LOOK_RATHER_THAN_GUESS,
            "",
        ]
        for name, (arguments, what, _) in self._table().items():
            lines.append(f"- `{name}`: {what} Arguments: {arguments}")
        return "\n".join(lines) + "\n"

    def guidance(self) -> str:
        """How to look, for an AI model the tools are declared to: the same
        rule as :meth:`describe` gives, without the text protocol's format."""
        return ("## Looking before you answer\n"
                "You can look with the software's own tools before you reply; the "
                "software runs each and gives you what it said. At most "
                f"{MOST_LOOKS} looks before each reply. The tools only look: "
                "nothing is run, written or started by one.\n\n"
                + _LOOK_RATHER_THAN_GUESS + "\n")

    def specs(self) -> list[Any]:
        """Each tool as it is declared to the AI model, its arguments as a
        JSON schema: the ones here by their own schema, one from outside
        by the arguments it describes in words."""
        from fastmdxplora.agent.turns import ToolSpec

        found = []
        for name, (arguments, what, _) in self._table().items():
            schema = _SCHEMAS.get(name) or {
                "type": "object", "additionalProperties": True,
                "description": f"Arguments: {arguments}"}
            found.append(ToolSpec(name, f"The software tells you {what}", schema))
        return found

    def use(self, name: str, asked: dict[str, Any]) -> Look:
        """Run one tool and keep what it said. Never raises: a tool that
        fails says so, and that is what the AI model is told."""
        cut = _cut
        table = self._table()
        entry = table.get(name)
        if entry is None:
            look = Look(name, asked, f"There is no tool called {name!r}. The tools are: "
                        + ", ".join(table) + ".", False)
        else:
            try:
                said = entry[2](self, dict(asked or {}))
                look = Look(name, asked, cut(said), True)
            except _Refused as exc:
                look = Look(name, asked, cut(str(exc)), False)
            except Exception as exc:  # noqa: BLE001 - a tool's failure is what it says
                from fastmdxplora.refusals import refusal_of

                look = Look(name, asked, cut(refusal_of(exc).message or str(exc)), False)
        self.looks.append(look)
        return look

    def said_so_far(self) -> str:
        """The looks taken, as the next prompt carries them."""
        if not self.looks:
            return ""
        parts = ["## What the software said"]
        for number, look in enumerate(self.looks, start=1):
            asked = json.dumps(_brief(look.asked), default=str)
            parts.append(f"{number}. `USE: {look.tool}` with {asked}"
                         + ("" if look.ok else " (refused)") + f":\n{look.said}")
        if len(self.looks) >= MOST_LOOKS:
            parts.append("That is all the looking there is for this reply. Answer now, "
                         "from what the software said.")
        return "\n\n".join(parts) + "\n\n"


_LOOK_RATHER_THAN_GUESS = (
    "Look rather than guess. Find a structure named in words before you write "
    "its PDB identifier; preview before you state a system's size or "
    "a study's time; check a config you are unsure of before you hand it "
    "over; inspect a structure before you choose its chains, its ligand "
    "or a residue's state; check a selection before you write one into a "
    "config. What a tool says is the software's own finding: quote it as "
    "it is, and never contradict it with a number of your own. A tool that "
    "refused says why; do not look again with the same arguments. A question "
    "about this software itself (a page, a button or what a mark on it does, "
    "a command or flag, what a setting or a refusal means, what it can and "
    "cannot do) is answered from its docs: read_docs first, answer as they "
    "say, and name the page; where they do not say, say that rather than "
    "guess.")


class _Refused(CodedError, Exception):
    """A tool that declines what it was asked, in words for the AI model."""

    default_code = "agent.tool.refused"


#: The name a tool from outside raises to decline.
ToolRefused = _Refused


def _checked(tools: Any, where: str) -> list[AgentTool]:
    """The tools that are well formed, each one that is not named in a warning."""
    kept = []
    for tool in tools or ():
        if not isinstance(tool, AgentTool):
            _warn_once("Agent tool %s is not an AgentTool (%r); left out.", where, tool)
        elif not isinstance(tool.name, str) or not _TOOL_NAME.match(tool.name):
            _warn_once("Agent tool %r %s is not a lower-case name of letters, digits "
                       "and underscores; left out.", tool.name, where)
        elif not callable(tool.look) or not str(tool.what).strip():
            _warn_once("Agent tool %r %s needs a look to call and a line saying "
                       "what it tells; left out.", tool.name, where)
        else:
            kept.append(tool)
    return kept


@lru_cache(maxsize=1)
def plugged_in() -> tuple[AgentTool, ...]:
    """The tools installed packages provide, read once per process.

    Each entry point in :data:`ENTRY_POINT_GROUP` loads to an
    :class:`AgentTool`, a list of them, or a function returning either. One
    that fails to load is named in a warning and left out: a plug-in that
    breaks must not take the Agent with it.
    """
    from importlib.metadata import entry_points

    found: list[AgentTool] = []
    try:
        points = list(entry_points(group=ENTRY_POINT_GROUP))
    except Exception as exc:  # noqa: BLE001 - a broken install is not the Agent's
        logger.warning("Agent tools from installed packages could not be listed: %s", exc)
        return ()
    for point in sorted(points, key=lambda p: p.name):
        try:
            given = point.load()
            if callable(given) and not isinstance(given, AgentTool):
                given = given()
            if isinstance(given, AgentTool):
                given = [given]
            found.extend(_checked(list(given), f"from {point.value}"))
        except Exception as exc:  # noqa: BLE001 - said, and left out
            logger.warning("Agent tools from %s could not be loaded, so they are left "
                           "out: %s", point.value, exc)
    return tuple(found)


def use_in(raw: str) -> tuple[str, dict[str, Any]] | None:
    """A tool asked for, if the reply is one: a first line ``USE: <tool>``,
    and its arguments as YAML (or JSON) after it or on the same line."""
    import yaml

    lines = (raw or "").splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.upper().startswith("USE:"):
            return None
        rest = stripped[4:].strip()
        name, _, inline = rest.partition(" ")
        body = inline.strip() or "\n".join(lines[index + 1:])
        body = "\n".join(line for line in body.splitlines()
                         if not line.strip().startswith("```"))
        try:
            asked = yaml.safe_load(body) if body.strip() else {}
        except yaml.YAMLError:
            asked = {"unreadable": body[:200]}
        if not isinstance(asked, dict):
            asked = {"value": asked}
        return name.strip().strip("`").lower(), asked
    return None


def _residue_hint(row: Any) -> dict[str, Any]:
    """A residue the page has selected: its chain, number, insertion code
    and name, each bounded."""
    if not isinstance(row, dict):
        raise _Refused("A selected residue must be a mapping.")
    kept: dict[str, Any] = {}
    for key, most in (("chain", 32), ("icode", 1), ("resn", 8), ("atom", 8)):
        if isinstance(row.get(key), str) and len(row[key]) <= most:
            kept[key] = row[key]
    resi = row.get("resi")
    if isinstance(resi, int) and not isinstance(resi, bool) and -100_000 < resi < 10_000_000:
        kept["resi"] = resi
    return kept


def _bounded_view_hints(value: Any) -> dict[str, Any]:
    """Keep browser-supplied current-view hints small and non-factual."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _Refused("Current-view hints must be a mapping.")
    if len(value) > MOST_VIEW_HINTS:
        raise _Refused(f"Current-view hints are bounded to {MOST_VIEW_HINTS} fields.")
    kept: dict[str, Any] = {}
    for key, item in value.items():
        if key not in _VIEW_HINT_KEYS:
            continue
        if isinstance(item, bool):
            kept[key] = item
        elif isinstance(item, int) and not isinstance(item, bool):
            if not 0 <= item < 10_000_000:
                raise _Refused("Current-view frame hints must be a bounded frame number.")
            kept[key] = item
        elif isinstance(item, str) and len(item) <= MOST_VIEW_HINT_TEXT:
            kept[key] = item
        elif isinstance(item, list) and key == "selection" and len(item) <= 20:
            kept[key] = [_residue_hint(row) for row in item]
        else:
            raise _Refused("Current-view hints must contain bounded text or numbers.")
    return kept


def _same_path(left: Any, right: Any) -> bool:
    try:
        return Path(str(left)).expanduser().resolve() == Path(str(right)).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def _read_with(box: Toolbox, name: str, asked: dict[str, Any]) -> str:
    """What one of the tools here says, read as part of another look: one
    look in all, as the AI model asked for one."""
    try:
        return str(box._table()[name][2](box, asked))
    except _Refused as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - a tool's failure is what it says
        from fastmdxplora.refusals import refusal_of

        return f"{name} could not answer: {refusal_of(exc).message}"


def _current_view_look(box: Toolbox, asked: dict[str, Any], request_hints: Any,
                       active_root: Any, path_for: Any) -> str:
    # The page's own hints, never ones the AI model writes: it asks what the
    # page shows, and cannot point the tool at another study by asking.
    hints = _bounded_view_hints(request_hints)
    if not hints:
        return ("The current view supplied no bounded hints. Hints are navigation only; "
                "ask an existing evidence tool for facts.")

    study = hints.get("study")
    named_study = study
    if study and path_for is not None:
        named_study = path_for(study)
    if study and named_study is None:
        raise _Refused("The current-view study is outside the workspace.")
    if study and active_root is not None and not _same_path(named_study, active_root):
        raise _Refused("The current view no longer names the active study.")

    parts = ["Current-view hints are navigation only: " +
             json.dumps(hints, sort_keys=True, default=str)]
    expression = hints.get("expression")
    selection = hints.get("selection")
    if isinstance(selection, str) and not expression:
        expression = selection
    system = hints.get("system")
    page = hints.get("page")
    if expression and system:
        parts.append("Factual evidence from check_selection:\n" + _read_with(
            box, "check_selection", {"system": system, "expression": expression}))
    elif system:
        parts.append("Factual evidence from inspect_structure:\n" + _read_with(
            box, "inspect_structure", {"system": system}))
    elif named_study:
        section = hints.get("section")
        where = {"report": "the report", "analysis": "the Analysis page"}.get(str(page))
        if where and section:
            parts.append(f"The person is reading {where} at its section {section!r}; "
                         "answer about that section unless they say otherwise.")
        if hints.get("file"):
            # The file open in the side panel's reader: read as
            # read_study_file reads it, inside the study and nowhere else;
            # the study's record as well, as the message may be about it.
            # The study is the server's open one, checked above.
            from dataclasses import replace

            reader = replace(box, open_study=named_study) if active_root is not None else box
            parts.append(f"The person has {hints['file']} open in the file reader. Factual "
                         "evidence from read_study_file:\n" + _read_with(
                             reader, "read_study_file",
                             {"study": named_study, "file": hints["file"]}))
        # The analysis the page is on, read first: an `analysis` hint where
        # a page sends one, the Report's section, or the analysis whose file
        # is open. A look is cut at its length, and the record of a short
        # run, each analysis not determined saying why, was cut before it.
        named: dict[str, Any] = {"study": named_study}
        from fastmdxplora.gui.agent_panel import _analysis_named

        file = str(hints.get("file") or "")
        on_page = (_analysis_named(named_study, hints.get("analysis"))
                   or _analysis_named(named_study, section)
                   or (_analysis_named(named_study, file.split("/")[1])
                       if file.startswith("analysis/") and file.count("/") >= 2 else None))
        if on_page:
            named["analysis"] = on_page
        parts.append("Factual evidence from read_study:\n" + _read_with(
            box, "read_study", named))
    else:
        parts.append("No study or structure hint was supplied for an existing evidence tool.")
    return "\n".join(parts)


def current_view_tool(hints: Any = None, *, active_root: Any = None,
                      path_for: Any = None) -> AgentTool:
    """Make a read-only current-view tool for one Agent request.

    The browser hints can identify a page, frame, selection, study or
    structure. They are not evidence. Factual work is delegated to the
    existing tools, with the same workspace boundary as the request.
    """
    told = _offered_named("current_view")
    return AgentTool(
        "current_view", _arguments_said(told), told.what,
        lambda box, asked: _current_view_look(box, asked, hints, active_root, path_for),
    )


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------
def _structure(box: Toolbox, given: Any) -> Path:
    """The file a tool reads for ``given``: a PDB identifier, fetched once
    and kept as the builder's preview keeps it, or a structure file."""
    from fastmdxplora.gui.preview import structure_file

    text = str(given or "").strip()
    if not text:
        raise _Refused("Name a structure: a PDB identifier such as 1UBQ, or the path "
                       "to a PDB or mmCIF file.")
    if not _PDB_ID.match(text) and not text.lower().endswith(_STRUCTURE_SUFFIXES):
        raise _Refused(f"{text} is not a PDB identifier or a structure file "
                       f"({', '.join(_STRUCTURE_SUFFIXES)}), so it is not read.")
    return structure_file(text, box.path_for)


def _inspect_structure(box: Toolbox, asked: dict[str, Any]) -> str:
    """Chains, residues, ligands, ions, water and metals, and the residues
    whose protonation state a study may set."""
    from fastmdxplora.advisories import advise
    from fastmdxplora.gui.preview import _as_given, titratable_residues
    from fastmdxplora.setup.estimate import estimate_system
    from fastmdxplora.structure_info import count_structure

    path = _structure(box, asked.get("system"))
    counted = count_structure(path)
    if not counted.get("valid", True):
        raise _Refused(f"The structure could not be read ({counted.get('reason')}).")
    lines = [f"{asked.get('system')}: {counted.get('atoms', 0):,} atoms in "
             f"{counted.get('models', 1)} model(s)"]
    chains = counted.get("all_chains") or counted.get("chains") or []
    lines.append(f"chains: {', '.join(map(str, chains)) or 'none named'}; "
                 f"{counted.get('protein_residues', 0)} protein residues")
    ligands = counted.get("ligand_resnames") or []
    lines.append("ligands (by residue name): " + (", ".join(map(str, ligands)) or "none"))
    ions = counted.get("ion_resnames") or []
    lines.append("ions: " + (", ".join(map(str, ions)) or "none"))
    lines.append(f"water molecules in the file: {counted.get('water_residues', 0)}")
    extents = counted.get("extents_angstrom")
    if extents:
        lines.append("extent: " + " x ".join(f"{e:g}" for e in extents) + " Angstrom")
    try:
        atoms = estimate_system(path, {}).atoms
        titratable = titratable_residues(atoms)
    except Exception:  # noqa: BLE001 - the counts stand without it
        atoms, titratable = [], []
    # Setup builds the biological assembly the file declares, which may be
    # more chains than the file holds (1HHO: A and B in the file, four in
    # the assembly). The counts above are the file's; what follows is what
    # setup builds, and said so, or an AI model reads 287 residues beside 38
    # histidines and one of them as wrong.
    built = list(dict.fromkeys(str(a.chain) for a in atoms))
    if len(built) > len(chains):
        lines.append(f"setup builds the biological assembly the file declares: chains "
                     f"{', '.join(built)}; the residues below are the assembly's")
    if titratable:
        by_kind: dict[str, list[str]] = {}
        for residue in titratable:
            by_kind.setdefault(residue["resname"], []).append(residue["key"])
        lines.append("residues that can take another protonation state "
                     "(setup.residue_states): " + "; ".join(
                         f"{kind} {len(keys)} ({', '.join(keys[:12])}"
                         f"{', ...' if len(keys) > 12 else ''})"
                         for kind, keys in by_kind.items()))
        near = [f"{r['key']} {r['resname']}: {r['near']}" for r in titratable if r.get("near")]
        if near:
            lines.append("side chains by a structural metal: " + "; ".join(near))
    lines += _what_setup_does_with_the_heterogens(path, asked.get("system"))
    for advisory in advise(_as_given(counted, asked.get("system")), {}):
        lines.append(f"worth knowing ({advisory.setting}): {advisory.summary}")
    return "\n".join(lines)


def _what_setup_does_with_the_heterogens(path: Path, given: Any) -> list[str]:
    """Each non-standard residue and what `heterogens: auto` does with it,
    as setup decides it. The Agent was told 181L's benzene had no chemistry,
    asked for a file setup would have fetched itself, and asked whether to
    leave out an additive setup discards by itself."""
    from fastmdxplora.gui.preview import given_by_identifier
    from fastmdxplora.setup.heterogens import Action, resolve

    try:
        decisions = resolve(str(path), keep_water=False)
    except Exception as exc:  # noqa: BLE001 - setup says the same, and why
        return [f"what setup does with the heterogens (heterogens: auto): it stops here: {exc}"]
    entry = given_by_identifier(given)
    said = []
    for decision in decisions:
        if decision.resname in ("HOH", "WAT") or not decision.instances:
            continue
        count = f" x{len(decision.instances)}" if len(decision.instances) > 1 else ""
        line = f"{decision.action.value} {decision.resname}{count}: {decision.reason}"
        if decision.action is Action.SIMULATE and not all(
                len(h.atoms) == 1 for h in decision.instances):
            line += (f"; setup fetches its chemistry from the {entry} entry, so no ligand "
                     "file is needed, with a force field that takes small molecules "
                     "(the default, amber-openff, does)" if entry else
                     "; a file has no entry to fetch its chemistry from, so give it as "
                     "an SDF or MOL2 in `ligand`")
        said.append(line)
    if not said:
        return []
    return ["what setup does with each heterogen (heterogens: auto): " + "; ".join(said)]


def _preview_setup(box: Toolbox, asked: dict[str, Any]) -> str:
    """What setup builds from a config and how long the study takes here."""
    from fastmdxplora.gui.preview import preview_of_config

    config = _config_asked(asked)
    answer = preview_of_config(config, path_for=box.path_for)
    if not answer.get("ok"):
        raise _Refused(str(answer.get("reason") or "No preview could be made."))
    e = answer["estimate"]
    lines = [
        f"about {int(e['particles']):,} particles in a {e['box_shape']} "
        f"{float(e['width_nm']):.2f} nm from face to face "
        f"({float(e['volume_nm3']):.0f} nm^3)",
        f"solute: {int(e['solute_atoms']):,} atoms with hydrogens, "
        f"{e['residues']} residues, net charge {int(e['net_charge']):+d}",
        f"water: {int(e['waters']):,} {str(e.get('water_model') or '').upper()}; ions: "
        f"{e['ions_positive']} {e['positive_ion']} and {e['ions_negative']} {e['negative_ion']}",
    ]
    if e.get("ligands"):
        lines.append("ligands kept: " + ", ".join(map(str, e["ligands"])))
    if e.get("grows"):
        lines.append(f"the padding is grown from {float(e['padding_nm']):g} to "
                     f"{float(e['padding_used_nm']):.2f} nm for the cutoff")
    if e.get("refuses"):
        lines.append("setup will refuse this padding for the cutoff")
    for note in e.get("notes") or []:
        lines.append(f"note: {note}")
    time = answer.get("time") or {}
    lines.append("time here: " + (
        f"{time['text']}" + (f" on {time['platform']}" if time.get("platform") else "")
        if time.get("ok") and time.get("text")
        else str(time.get("reason") or "not known")))
    for advisory in answer.get("advisories") or []:
        lines.append(f"worth knowing ({advisory['setting']}): {advisory['summary']}")
    lines.append("This is an estimate from the structure and the settings; setup's own "
                 "numbers replace it once it has run.")
    return "\n".join(lines)


def plan_lines(config: dict[str, Any]) -> list[str]:
    """The plan as the person reads it, defaults marked."""
    from fastmdxplora.gui.plan import plan_of

    return [f"  {line['label']}: {line['value']}" + (" (default)" if line.get("default") else "")
            for line in plan_of(config)]


def refused_said(exc: BaseException) -> str:
    """A refusal and what would fix it, as the builder says them."""
    from fastmdxplora.refusals import refusal_of
    from fastmdxplora.remedies import remedy_for

    found = refusal_of(exc)
    said = f"Refused ({found.code}): {found.message}"
    try:
        fix = remedy_for(found, where="the config")
        said += f"\nWhat would fix it: {fix.fix}"
    except Exception:  # noqa: BLE001 - the refusal stands without its fix
        pass
    return said


def carried_on(box: Toolbox, config: dict[str, Any]) -> Path | None:
    """The study a config carries on in place (`simulation.resume_from`),
    held to the server's rule for paths; None where it carries on none, or
    names no study."""
    from fastmdxplora.gui.browse import is_study

    simulation = config.get("simulation")
    named = simulation.get("resume_from") if isinstance(simulation, dict) else None
    if not named or not isinstance(named, str):
        return None
    if box.path_for is not None:
        found = box.path_for(named)
        if found is None:
            raise _Refused(f"resume_from names {named}, outside the workspace.")
        folder = Path(found)
    elif box.workspace is not None and not Path(named).expanduser().is_absolute():
        folder = box.workspace.root / named
    else:
        folder = Path(named).expanduser()
    return folder if folder.is_dir() and is_study(folder) else None


def _has_run(box: Toolbox, config: dict[str, Any], file: Path | None = None) -> Path | None:
    """The study a config's `output` names, where it has run already."""
    from fastmdxplora.config.recorded import RECORDED
    from fastmdxplora.workspace_studies import defaults_start

    if not config.get("output"):
        return None
    start = defaults_start(config, file=file, root=(
        box.workspace.root if box.workspace is not None else None))
    if start is None or (box.path_for is not None and box.path_for(str(start)) is None):
        return None
    return start if (start / RECORDED).is_file() else None


def _defaults_there(start: Path) -> Path | None:
    """The file of defaults a study in ``start`` would be given, if any."""
    from fastmdxplora.config.defaults_file import find_defaults

    return find_defaults(start)


def your_defaults_for(box: Toolbox, config: dict[str, Any], file: Path | None = None) -> Any:
    """Your defaults (fastmdx-defaults.yml) for ``config``, looked for as
    the command line looks: from the study's folder up, else from the
    config file's, else from the folder of studies. A file that is there
    and wrong is said, never passed over."""
    from fastmdxplora.config.defaults_file import defaults_for
    from fastmdxplora.config.loader import ConfigError
    from fastmdxplora.workspace_studies import defaults_start

    start = defaults_start(config, file=file, root=(
        box.workspace.root if box.workspace is not None else None))
    if start is None:
        # No folder of studies here (started in the home folder): from
        # where FastMDXplora was started, as the command line looks.
        start = Path.cwd()
    if box.path_for is not None and box.path_for(str(start)) is None:
        # Held to the server's rule, as every path a config names: a file
        # of defaults outside it is not read, nor said to be there.
        if config.get("output"):
            raise _Refused(f"`output` names {config.get('output')}, outside the workspace.")
        return None
    if _has_run(box, config, file) is not None:
        # A study that has run, given a config for a phase run again: its
        # record holds every value it ran with, and yours are not laid over.
        return None
    try:
        return defaults_for(start)
    except ConfigError as exc:
        raise _Refused(str(exc)) from None


def with_your_defaults(box: Toolbox, config: dict[str, Any],
                       file: Path | None = None) -> tuple[dict[str, Any], list[str], Any]:
    """The config as it will run: your defaults filling what it leaves
    unset, the settings they filled, and the defaults themselves. A config
    the validator accepts that your defaults make it refuse is refused
    here, naming the file, not once it runs."""
    from fastmdxplora.config.defaults_file import refused_with, with_defaults

    defaults = your_defaults_for(box, config, file)
    filled, names = with_defaults(config, defaults)
    refusal = refused_with(config, filled, defaults)
    if refusal is not None:
        raise _Refused(refused_said(refusal))
    return filled, names, defaults


def validated(box: Toolbox, config: dict[str, Any]) -> Path | None:
    """Validate ``config`` as a run would, with the study it carries on, if
    any; that study. Raises the refusal and what would fix it."""
    import copy

    from fastmdxplora.config.loader import ConfigError, validate_config

    continuing = carried_on(box, config)
    try:
        validate_config(copy.deepcopy(config), require_systems=continuing is None)
    except ConfigError as exc:
        raise _Refused(refused_said(exc)) from None
    return continuing


def checked_config(box: Toolbox, config: dict[str, Any],
                   file: Path | None = None) -> tuple[list[str], dict[str, Any]]:
    """Whether the validator accepts ``config``, as a run would take it: the
    study it carries on, if any, then your defaults filling what it leaves
    unset, the plan, and whether this machine can run it. One check for the
    Agent and an AI app; the lines said, and the config as it will run.
    Raises the refusal and what would fix it."""
    continuing = validated(box, config)
    filled, names, defaults = with_your_defaults(box, config, file)
    lines = ["Accepted by the validator. The plan:", *plan_lines(filled)]
    if names:
        lines.append(f"Your defaults ({defaults.path.name}) fill what it leaves unset: "
                     + ", ".join(names) + ".")
    ran = _has_run(box, config, file)
    if ran is not None and _defaults_there(ran) is not None:
        lines.append(f"Your defaults are not filled in: {_shown(box, ran)} has run, and its "
                     "record holds the values it ran with.")
    if continuing is not None:
        lines.append(f"It continues the study at {_shown(box, continuing)} in place: "
                     "its next segment, joined to the others, with the analyses rerun "
                     "over the whole.")
    lacking = _cannot_run_here(filled)
    if lacking:
        lines += ["", f"This machine cannot run it yet: {lacking}"]
    return lines, filled


def _cannot_run_here(config: dict[str, Any]) -> str | None:
    """What this machine lacks to run the config, if anything."""
    from fastmdxplora.gui.exploration import exploration_environment_error

    try:
        return exploration_environment_error(config)
    except Exception:  # noqa: BLE001 - a check, not the result
        return None


def _check_config(box: Toolbox, asked: dict[str, Any]) -> str:
    """Whether the validator accepts a config, and the plan if it does."""
    lines, _ = checked_config(box, _config_asked(asked))
    return "\n".join(lines)


def _check_selection(box: Toolbox, asked: dict[str, Any]) -> str:
    """What an MDTraj selection matches in a structure, as `fastmdx select`."""
    import mdtraj as md

    expression = str(asked.get("expression") or "").strip()
    if not expression:
        raise _Refused("Give the selection as `expression`, in MDTraj's language.")
    path = _structure(box, asked.get("system"))
    topology = md.load(str(path)).topology
    try:
        indices = topology.select(expression)
    except Exception as exc:  # noqa: BLE001 - MDTraj's parser raises several types
        raise _Refused(f"{expression!r} is not a valid selection: {exc}") from None
    if len(indices) == 0:
        return (f"{expression!r} matches none of the {topology.n_atoms:,} atoms. Residues "
                "named from a paper or the PDB are `resSeq`; `resid` counts from zero "
                "in file order.")
    residues: list[Any] = []
    for index in indices:
        residue = topology.atom(int(index)).residue
        if residue not in residues:
            residues.append(residue)
    named = [f"{r.name}{r.resSeq} (chain {r.chain.chain_id or r.chain.index})"
             for r in residues[:20]]
    more = f", and {len(residues) - 20} more" if len(residues) > 20 else ""
    return (f"{expression!r} matches {len(indices):,} of {topology.n_atoms:,} atoms in "
            f"{len(residues)} residue{'' if len(residues) == 1 else 's'}: "
            f"{', '.join(named)}{more}")


def _a_study(box: Toolbox, given: str, argument: str) -> Path:
    """The study folder ``given`` names, held to the server's rule for paths."""
    from fastmdxplora.gui.browse import is_study

    if not given:
        raise _Refused(f"Name the study as `{argument}`: its folder, as given to --output.")
    if box.path_for is not None:
        named = box.path_for(given)
    elif box.workspace is not None and not Path(given).expanduser().is_absolute():
        # As list_studies names them: relative to the folder of studies,
        # wherever FastMDXplora was started.
        named = str(box.workspace.root / given)
    else:
        named = given
    if named is None:
        raise _Refused(f"{given} is outside the workspace"
                       + (f" ({box.workspace.root})." if box.for_ai_app else "."))
    folder = Path(named).expanduser()
    # An AI app's workspace is a folder of studies, not one: list_studies
    # names those in it.
    root = box.for_ai_app and box.workspace is not None and _same_path(
        folder, box.workspace.root)
    if root or not folder.is_dir() or not is_study(folder):
        raise _Refused(f"{given} is not a study folder (one holding a manifest, a "
                       "resolved config, or simulation, analysis or report)."
                       + (" list_studies names the studies here." if box.workspace
                          is not None else ""))
    if box.workspace is not None and box.workspace.inside(folder) is not None:
        from fastmdxplora.workspace_studies import links_out

        leaving = links_out(box.workspace, folder.resolve())
        if leaving is not None:
            raise _Refused(f"{box.workspace.shown(leaving)} links out of the workspace, "
                           f"so {given} is not read.")
    return folder


def _list_studies(box: Toolbox, asked: dict[str, Any]) -> str:
    """The studies in the workspace, newest first, as an AI app lists them."""
    from fastmdxplora.workspace_studies import listed

    if box.workspace is None:
        raise _Refused("There is no folder of studies here to list: FastMDXplora was "
                       "started in the home folder or at the top of the file system. "
                       "Name a study's folder to read_study instead.")
    return listed(box.workspace, str(asked.get("tag") or "") or None)


def _compare_studies(box: Toolbox, asked: dict[str, Any]) -> str:
    """How two studies differ, in settings and in what each recorded."""
    from fastmdxplora.workspace_studies import compared

    first = _a_study(box, str(asked.get("first") or "").strip(), "first")
    second = _a_study(box, str(asked.get("second") or "").strip(), "second")
    shown = box.workspace.shown if box.workspace is not None else str
    said, could = compared(first, second, shown)
    if not could:
        raise _Refused(said)
    return said


def _read_study(box: Toolbox, asked: dict[str, Any]) -> str:
    """A study's record, as the Agent and an AI app both read it
    (:func:`fastmdxplora.workspace_studies.study_record`): where it stands,
    its config, what its analyses found, the checks it was held to, how
    long it ran and why, and what would fix it. With ``analysis``, the
    analysis asked about is said first, so a look cut at its length still
    holds it."""
    from fastmdxplora.gui.agent_panel import _analysis_named
    from fastmdxplora.workspace_studies import study_record

    given = str(asked.get("study") or "").strip()
    folder = _a_study(box, given, "study")
    asked_about = asked.get("analysis")
    first = _analysis_named(folder, asked_about)
    parts = [f"The study at {_shown(box, folder)}"]
    if asked_about and first is None:
        parts.append(f"It holds no analysis {str(asked_about)[:80]!r}.")
    # The Agent's look is cut at its length: the config is said in part, so
    # what the analyses found is not what is cut.
    parts.append(study_record(folder, for_the_agent=not box.for_ai_app, first=first,
                              most_config=None if box.for_ai_app else 1500))
    return "\n".join(parts)


#: A file in a study larger than this is not read: a trajectory, a
#: checkpoint. What it holds is read from the study's record instead.
MOST_FILE_BYTES = 4_000_000


#: What only a study's own folder holds: a folder holding an `analysis` or
#: a `report` folder alone (a home folder, a repository) is not read from.
_A_STUDY_S_RECORD = ("manifest.json", "resolved_config.yml", "exploration.yml",
                     "batch_manifest.json")


def _study_file(box: Toolbox, given: str, file: Any) -> Path:
    """The file ``file`` names inside the study ``given`` names, held to the
    study's folder (no absolute path, no ``..``, no hidden file, no link out
    of it) and to the server's rule for paths. Where the server sets no
    rule (on the person's own computer), the study is the one open or one in
    the workspace, and a folder that holds no study's record is not one."""
    folder = _a_study(box, given, "study")
    if not any((folder / name).is_file() for name in _A_STUDY_S_RECORD):
        raise _Refused(f"{given} holds no study's record (a manifest or a resolved "
                       "config), so its files are not read.")
    if box.path_for is None:
        open_study = box.open_study
        mine = open_study is not None and _same_path(folder, open_study)
        inside = box.workspace is not None and box.workspace.inside(folder) is not None
        if not (mine or inside):
            raise _Refused(f"{given} is neither the study open nor one in the workspace, "
                           "so its files are not read.")
    named = str(file or "").strip()
    if not named:
        raise _Refused("Name the file as `file`: its path inside the study, such as "
                       "analysis/rmsd/options.json.")
    relative = Path(named)
    if relative.is_absolute() or ".." in relative.parts:
        raise _Refused(f"{named} is not a path inside the study. Name the file by its "
                       "path inside the study's folder, such as analysis/rmsd/options.json.")
    if any(part.startswith(".") for part in relative.parts):
        raise _Refused(f"{named} is a hidden file or inside a hidden folder, so it is "
                       "not read.")
    target = folder / relative
    try:
        landed = target.resolve().relative_to(folder.resolve())
    except (OSError, RuntimeError, ValueError):
        raise _Refused(f"{named} leads out of the study, so it is not read.") from None
    if any(part.startswith(".") for part in landed.parts):
        # A link inside the study that leads to a hidden file of it.
        raise _Refused(f"{named} leads to a hidden file, so it is not read.")
    if box.path_for is not None and box.path_for(str(target)) is None:
        raise _Refused(f"{named} is outside the workspace.")
    if not target.is_file():
        raise _Refused(f"{given} holds no file {named}.")
    return target


def _read_study_file(box: Toolbox, asked: dict[str, Any]) -> str:
    """One text file of a study, as the Files page shows it: its head and
    tail where it is long. A binary file is not read."""
    given = str(asked.get("study") or "").strip()
    named = str(asked.get("file") or "").strip()
    target = _study_file(box, given, named)
    size = target.stat().st_size
    if size > MOST_FILE_BYTES:
        raise _Refused(f"{named} is {size:,} bytes, too large to read here; read what the "
                       "study recorded from it with read_study.")
    data = target.read_bytes()
    if b"\0" in data[:8192]:
        raise _Refused(f"{named} is not text (a trajectory, a checkpoint or an image); "
                       "read what the study recorded from it with read_study.")
    text = data.decode("utf-8", errors="replace")
    head, tail = MOST_SAID - 1200, 800
    if len(text) > head + tail:
        text = text[:head].rstrip() + "\n... (the middle was cut) ...\n" + text[-tail:].lstrip()
    return f"{named} in the study at {given} ({size:,} bytes):\n{text}"


def _methods_of_study(box: Toolbox, asked: dict[str, Any]) -> str:
    """A study's methods paragraphs, as its report gives them."""
    from fastmdxplora.report.document import methods_prose

    given = str(asked.get("study") or "").strip()
    folder = _a_study(box, given, "study")
    shown = _shown(box, folder)
    if (folder / "batch_manifest.json").is_file():
        raise _Refused(f"{shown} is a study of several runs; each run's report gives its "
                       "own methods. Name one of its runs (list_studies names them).")
    may_read = (None if box.path_for is None
                else lambda path: box.path_for(str(path)) is not None)
    try:
        prose = methods_prose(folder, may_read=may_read)
    except PermissionError as exc:
        raise _Refused(f"{shown}: {exc}.") from None
    if not prose:
        return f"{shown} has recorded nothing a methods section could say yet."
    return (f"The methods of {shown}, as its report gives them: quote them as they are "
            f"(a gap they name was not recorded).\n\n{prose}")


def _docs_asked(asked: dict[str, Any]) -> dict[str, Any]:
    """What ``read_docs`` was asked, as the look reads it: the text
    protocol's ``value`` as the query, and a bare question YAML read as a
    mapping put back together."""
    asked = dict(asked)
    if "value" in asked and "query" not in asked:
        # The text protocol's ``USE: read_docs how do I resume``, which YAML
        # may also read as a list ("- what is a sweep").
        value = asked.pop("value")
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            value = " ".join(value)
        asked["query"] = value
    taken = {"query", "page", "section", "part"}
    stray = [key for key in asked if key not in taken]
    if (len(stray) == 1 and not taken & set(asked) and isinstance(stray[0], str)
            and isinstance(asked[stray[0]], (str, type(None)))):
        # A bare question with a colon in it ("what does resume: true do"),
        # which YAML reads as a mapping: the question, put back together.
        key = stray[0]
        asked = {"query": f"{key}: {asked[key] or ''}".strip()}
    return asked


def _docs_call(asked: dict[str, Any]) -> tuple[str | None, str | None, str | None, int | None]:
    """The query, page, section and part ``read_docs`` was asked for, as the
    look reads them; raises :class:`_Refused` for what it does not take
    (another key, a value that is not words, a part that is not a whole
    number of 1 or more, a section with no page, a later part of no
    section)."""
    asked = _docs_asked(asked)
    taken = {"query", "page", "section", "part"}
    unknown = sorted(str(key) for key in asked if key not in taken)
    if unknown:
        raise _Refused("read_docs takes `query`, or `page` with `section` and `part`; not "
                       + ", ".join(f"`{k}`" for k in unknown) + ".")

    def text(key: str) -> str | None:
        value = asked.get(key)
        if value is None:
            return None
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise _Refused(f"`{key}` is words, not {type(value).__name__}.")
        if isinstance(value, int) and value.bit_length() > 1000:
            raise _Refused(f"`{key}` is words, not a number of {value.bit_length()} bits.")
        return " ".join(str(value).split())[:300] or None

    query, page, section = text("query"), text("page"), text("section")
    part = asked.get("part")
    if part is not None:
        whole = isinstance(part, int) and not isinstance(part, bool)
        if isinstance(part, float) and math.isfinite(part) and part.is_integer():
            part, whole = int(part), True
        if not whole or part < 1:
            raise _Refused("`part` is a whole number, 1 or more.")
    if section and not page:
        raise _Refused("Name the page a section is on, as `page`.")
    if part not in (None, 1) and not section:
        raise _Refused("`part` is for a section read in parts: name it with `page` and "
                       "`section`.")
    return query, page, section, part


def _read_docs(box: Toolbox, asked: dict[str, Any]) -> str:
    """What the docs of this installed version say (:mod:`fastmdxplora.software_docs`)."""
    from fastmdxplora.software_docs import DocsNotFound, read_docs

    query, page, section, part = _docs_call(asked)
    try:
        return read_docs(query=query, page=page, section=section, part=part)
    except DocsNotFound as exc:
        raise _Refused(str(exc)) from None


def _find_structure(box: Toolbox, asked: dict[str, Any]) -> str:
    """The PDB entries a name answers to, by protein, and AlphaFold DB's
    models where the PDB holds none or where asked."""
    from fastmdxplora.structure_search import SearchUnreachable, find_structures, said

    query = " ".join(str(asked.get("query") or "").split())
    if not query:
        raise _Refused("Name the structure as `query`: a molecule's name, such as "
                       "lysozyme, or a PDB identifier.")
    try:
        most = int(asked.get("most") or 5)
    except (TypeError, ValueError):
        raise _Refused("`most` is a whole number of entries, 1 to 10.") from None
    try:
        found = find_structures(query, organism=str(asked.get("organism") or ""),
                                most=most, predicted=asked.get("predicted") is True)
    except SearchUnreachable as exc:
        raise _Refused(f"{exc} Nothing is guessed in its place: ask the person for the "
                       "PDB identifier or a structure file.") from None
    return said(found)


def _config_asked(asked: dict[str, Any]) -> dict[str, Any]:
    config = asked.get("config", asked)
    if isinstance(config, str):
        import yaml

        try:
            config = yaml.safe_load(config)
        except yaml.YAMLError:
            config = None
    if not isinstance(config, dict) or not config:
        raise _Refused("Give the config as `config`, a mapping as it would be written "
                       "to a file.")
    return config


# ---------------------------------------------------------------------------
# Looks an AI app's server had first: a study's saved views, the machines a
# study can be sent to, and how a study sent to one is doing
# ---------------------------------------------------------------------------
def _shown(box: Toolbox, path: Any) -> str:
    """A path as the person reads it: in the workspace, relative to it."""
    if box.workspace is not None and box.workspace.inside(path) is not None:
        return box.workspace.shown(path)
    return str(path)


def _view_said(view: dict[str, Any]) -> str:
    said = []
    if view.get("frame") is not None:
        said.append(f"frame {view['frame']}")
    for key in ("representation", "colour"):
        if view.get(key):
            said.append(f"{key} {view[key]}")
    if view.get("superposed") and view["superposed"] != "none":
        said.append(f"superposed on {view['superposed']}, fitted to "
                    f"{view.get('superposed_to') or 'first'}"
                    + (f", smoothed over {view['smoothed_over']}"
                       if (view.get("smoothed_over") or 1) > 1 else ""))
    if view.get("publication"):
        said.append("publication look")
    return ", ".join(said) or "the structure as it opens"


def _views_of_study(box: Toolbox, asked: dict[str, Any]) -> str:
    """The views the person saved with a study, the selections they named,
    and the scenes written with it."""
    from fastmdxplora.gui.saved_views import views_of
    from fastmdxplora.gui.viewer_selections import selections_of
    from fastmdxplora.scenes import SCENES_DIR, scenes_of

    given = str(asked.get("study") or "").strip()
    folder = _a_study(box, given, "study")
    shown = _shown(box, folder)
    views = views_of(folder)["views"]
    scenes = scenes_of(folder)["scenes"]
    selections = selections_of(folder)["selections"]
    starts = (" (write_scene starts from one by its name)." if box.for_ai_app
              else " (the person opens one in the Viewer by its name).")
    lines = [f"Views saved with {shown} in the GUI: {len(views)}"
             + (starts if views else ".")]
    # A note is the person's words about the view, said as theirs.
    lines += [f"- {view['name']}: {_view_said(view)}"
              + (f". The person's note: {json.dumps(view['note'])}" if view.get("note") else "")
              for view in views]
    lines.append(f"Selections the person named: {len(selections)}"
                 + (" (every scene shows them)." if selections else "."))
    for selection in selections:
        what = selection.get("expression") or f"{len(selection.get('residues') or [])} residues"
        lines.append(f"- {selection.get('name')}: {what}")
    lines.append(f"Scenes written: {len(scenes)}" + ("." if not scenes else ":"))
    lines += [f"- {scene['name']}: {shown}/{SCENES_DIR}/{scene['name']}.mvsx"
              for scene in scenes]
    return "\n".join(lines)


def _jobs_here(box: Toolbox) -> list[Any]:
    """The jobs sent from this computer: those whose results come back into
    the workspace, where there is one."""
    from fastmdxplora.remote import api

    return api.jobs(under=box.workspace.root if box.workspace is not None else None)


def job_here(box: Toolbox, given: str) -> Any:
    """The job named, if its results come back into the workspace."""
    for job in _jobs_here(box):
        if job.name == given:
            return job
    raise _Refused(f"No job called {given!r} was sent with its results coming back "
                   "into this workspace. list_machines lists the jobs here.",
                   code="remote.job.unknown")


def job_line(job: Any, shown: Callable[[Any], str]) -> str:
    """One job sent to another machine, as both interfaces say it."""
    detail = f", {job.detail}" if job.detail else ""
    fetched = ("fetched" if job.fetched_at else "not fetched")
    return (f"  {job.name} on {job.machine}: {job.state}{detail} (results to "
            f"{shown(job.local_output)}, {fetched}; sent "
            f"{job.submitted_at.replace('T', ' ').replace('Z', ' UTC')})")


#: Looks at other machines: the host's in a hosted GUI, which reaches none
#: of them, as its own Remote routes do not.
_NOT_HOSTED = ("list_machines", "remote_status")


def _not_hosted(box: Toolbox) -> None:
    """Other machines are the host's in a hosted GUI, and not reached from
    it, as its own Remote routes are not."""
    if box.hosted:
        raise _Refused("Other machines are not reached from a hosted GUI: a study runs "
                       "where the GUI is hosted.")


def _list_machines(box: Toolbox, asked: dict[str, Any]) -> str:
    """The machines inspected from here, as recorded, and the jobs sent."""
    from fastmdxplora.remote import api

    _not_hosted(box)
    found = api.machines()
    lines = ["Machines a study can be sent to, as last inspected (nothing was asked "
             "of them now):"]
    if not found:
        lines = ["No machine has been inspected from this computer. The person adds "
                 "one at a terminal: `fastmdx remote --machine <alias from "
                 "~/.ssh/config>`."]
    for machine in found:
        state = "ready" if machine.ready else "not ready"
        gpus = machine.gpus or ("no GPU on the login node" if machine.kind == "slurm"
                                else "no GPU found")
        when = machine.inspected_at.replace("T", " ").replace("Z", " UTC")
        lines.append(f"  {machine.name} ({machine.kind}, {gpus[:200]}): {state}: "
                     f"{machine.summary[:300]} (inspected {when})")
        if not machine.ready:
            lines.append(f"    The person makes it ready at a terminal: `fastmdx remote "
                         f"install --machine {machine.name}`.")
    jobs = _jobs_here(box)
    if jobs:
        lines += ["", "Jobs sent with their results coming back here (as last asked; "
                      "remote_status asks again):"]
        lines += [job_line(job, lambda path: _shown(box, path)) for job in jobs]
    if found and box.for_ai_app and box.runs:
        lines += ["", "start_study with `machine` sends a checked study to a ready "
                      "machine, once the person agrees."]
    elif found and not box.for_ai_app:
        lines += ["", "The person sends a study to a ready machine from the Config "
                      "Builder's Run on, or with `fastmdx remote send`."]
    return "\n".join(lines)


def _remote_status(box: Toolbox, asked: dict[str, Any]) -> str:
    """How each job sent from here is doing, asked of its machine."""
    from fastmdxplora.remote import api
    from fastmdxplora.remote.jobs import FINISHED
    from fastmdxplora.refusals import refusal_of

    _not_hosted(box)
    given = str(asked.get("job") or "").strip()
    if given:
        names = [job_here(box, given).name]
    else:
        names = [job.name for job in _jobs_here(box) if job.state not in FINISHED]
        if not names:
            return ("No job sent from here is waiting or running. list_machines lists "
                    "every job whose results come back into this workspace.")
    lines = []
    for name in names:
        try:
            job = api.status(name)
        except Exception as exc:  # noqa: BLE001 - one machine down, the rest said
            lines.append(f"  {name}: could not be asked: {refusal_of(exc).message or exc}")
            continue
        lines.append(job_line(job, lambda path: _shown(box, path)))
        lines += [f"      {line}" for line in job.extra.get("log_tail", [])[-4:]]
        if job.state == "done" and not job.fetched_at:
            lines.append("    fetch_study brings its results here."
                         if box.for_ai_app and box.runs
                         else "    The person brings its results here with `fastmdx "
                              "remote fetch`, or from the GUI.")
    return "\n".join(lines)


def look_named(name: str) -> Callable[[Toolbox, dict[str, Any]], str]:
    """The function that does the Agent's look ``name``, for an AI app's
    server, which runs the same function in its workspace."""
    return _TOOLS[name][2]


def _the_agent_s() -> tuple[dict[str, tuple[str, str, Callable[[Toolbox, dict[str, Any]], str]]],
                            dict[str, dict[str, Any]]]:
    """The Agent's looks, from the one list of tools
    (:mod:`fastmdxplora.offered_tools`): each one's arguments in words, what
    it tells, and the function here that does it; and each one's arguments
    as a JSON schema. `current_view` is made for each request from the
    page's hints (:func:`current_view_tool`), so only its schema is here."""
    tools, schemas = {}, {}
    for tool in _offered_by(_AGENT):
        schemas[tool.name] = _schema_for(tool, _AGENT)
        if tool.name == "current_view":
            continue
        module, _, name = tool.does.partition(":")
        if module != __name__:  # pragma: no cover - held by the list's test
            # A fault in the list itself, not a refusal of anything asked.
            raise AssertionError(f"The Agent's {tool.name} is done in {module}, not here.")
        tools[tool.name] = (_arguments_said(tool), tool.told_to(_AGENT), globals()[name])
    return tools, schemas


_TOOLS, _SCHEMAS = _the_agent_s()


def _brief(asked: dict[str, Any]) -> dict[str, Any]:
    """The arguments as shown: a config is named, not repeated whole."""
    shown = dict(asked or {})
    if isinstance(shown.get("config"), dict):
        systems = shown["config"].get("systems") or []
        first = systems[0] if systems and isinstance(systems[0], dict) else {}
        shown["config"] = f"a study of {first.get('system') or 'no system'}"
    return shown


def _cut(said: str) -> str:
    said = str(said or "").strip()
    return said if len(said) <= MOST_SAID else said[:MOST_SAID].rstrip() + " ... (cut)"
