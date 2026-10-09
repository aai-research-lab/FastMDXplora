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
    "analysis", "colour", "expression", "field", "frame", "page", "representation",
    "selection", "study", "superposed", "system",
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
#: (`find_structure`, `list_studies`, ...), which reads as code.
LOOK_WORDS: dict[str, tuple[str, str, str]] = {
    "read_docs": ("Read the docs on {}", "Reading the docs on {}", "query"),
    "find_structure": ("Looked up {} in the PDB", "Looking up {} in the PDB", "query"),
    "inspect_structure": ("Inspected {}", "Inspecting {}", "system"),
    "preview_setup": ("Previewed what setup builds", "Previewing what setup builds", ""),
    "check_config": ("Checked the config", "Checking the config", ""),
    "check_selection": ("Checked a selection", "Checking a selection", ""),
    "read_study": ("Read a study's record", "Reading a study's record", ""),
    "list_studies": ("Listed the studies here", "Listing the studies here", ""),
    "compare_studies": ("Compared two studies", "Comparing two studies", ""),
    "methods_of_study": ("Read a study's methods", "Reading a study's methods", ""),
    "current_view": ("Checked what the page shows", "Checking what the page shows", ""),
}


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
        # pages. Only words are said: anything else is refused by the look.
        value = ""
        for key in ("query", "value", "section", "page"):
            given = (asked or {}).get(key)
            if (isinstance(given, (str, int, float)) and not isinstance(given, bool)
                    and not (isinstance(given, int) and given.bit_length() > 1000)):
                value = " ".join(str(given).split())
                if value:
                    break
        if not value and asked:
            # Asked something the look cannot read: it is refused, not a list.
            return "Reading the docs" if doing else "Read the docs"
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

    def _table(self) -> dict[str, tuple[str, str, Callable[[Toolbox, dict[str, Any]], str]]]:
        """Every tool this toolbox has: those here, then installed, then extra."""
        table = dict(_TOOLS)
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
        table = self._table()
        entry = table.get(name)
        if entry is None:
            look = Look(name, asked, f"There is no tool called {name!r}. The tools are: "
                        + ", ".join(table) + ".", False)
        else:
            try:
                said = entry[2](self, dict(asked or {}))
                look = Look(name, asked, _cut(said), True)
            except _Refused as exc:
                look = Look(name, asked, _cut(str(exc)), False)
            except Exception as exc:  # noqa: BLE001 - a tool's failure is what it says
                from fastmdxplora.refusals import refusal_of

                look = Look(name, asked, _cut(refusal_of(exc).message or str(exc)), False)
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
    for key, most in (("chain", 32), ("icode", 1), ("resn", 8)):
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
    if expression and system:
        parts.append("Factual evidence from check_selection:\n" + _read_with(
            box, "check_selection", {"system": system, "expression": expression}))
    elif system:
        parts.append("Factual evidence from inspect_structure:\n" + _read_with(
            box, "inspect_structure", {"system": system}))
    elif named_study:
        parts.append("Factual evidence from read_study:\n" + _read_with(
            box, "read_study", {"study": named_study}))
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
    return AgentTool(
        "current_view",
        "none: what the page shows is given to the tool by the page.",
        "what the page shows (the study, frame, view and residues chosen), as "
        "where to look and not as what is so, then the facts from the study's "
        "records or the structure, read by the tools that read them.",
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


def _check_config(box: Toolbox, asked: dict[str, Any]) -> str:
    """Whether the validator accepts a config, and the plan if it does."""
    from fastmdxplora.config.loader import ConfigError, validate_config
    from fastmdxplora.gui.plan import plan_of
    from fastmdxplora.refusals import refusal_of
    from fastmdxplora.remedies import remedy_for

    config = _config_asked(asked)
    try:
        validate_config(config, require_systems=True)
    except ConfigError as exc:
        found = refusal_of(exc)
        fix = remedy_for(found, where="the config")
        raise _Refused(f"Refused ({found.code}): {found.message}\n"
                       f"What would fix it: {fix.fix}") from None
    lines = ["Accepted. The plan, as the person will read it:"]
    for line in plan_of(config):
        lines.append(f"  {line['label']}: {line['value']}"
                     + (" (default)" if line.get("default") else ""))
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
        raise _Refused(f"{given} is outside the workspace.")
    folder = Path(named).expanduser()
    if not folder.is_dir() or not is_study(folder):
        raise _Refused(f"{given} is not a study folder (one holding a manifest, a "
                       "resolved config, or simulation, analysis or report).")
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
    """Another study's record: its config, what its analyses found, the
    checks it was held to, how long it ran and why, and what would fix it."""
    from fastmdxplora.gui.agent_panel import (
        _checks_summary,
        _config_the_run_used,
        _remedies_summary,
        _results_summary,
        _stopping_summary,
    )

    given = str(asked.get("study") or "").strip()
    folder = _a_study(box, given, "study")
    config = _config_the_run_used(folder)
    parts = [f"the study at {given}"]
    if config:
        parts.append("its config (the short form):\n" + config[:1500])
    for said in (_results_summary(folder), _checks_summary(folder),
                 _stopping_summary(folder), _remedies_summary(folder)):
        if said:
            parts.append(said)
    if len(parts) == 1:
        parts.append("It has recorded nothing yet: no config, findings or checks.")
    return "\n\n".join(parts)


def _methods_of_study(box: Toolbox, asked: dict[str, Any]) -> str:
    """A study's methods paragraphs, as its report gives them."""
    from fastmdxplora.report.document import methods_prose

    given = str(asked.get("study") or "").strip()
    folder = _a_study(box, given, "study")
    if (folder / "batch_manifest.json").is_file():
        raise _Refused(f"{given} is a study of several runs; each run's report gives its "
                       "own methods. Name one of its runs.")
    may_read = (None if box.path_for is None
                else lambda path: box.path_for(str(path)) is not None)
    try:
        prose = methods_prose(folder, may_read=may_read)
    except PermissionError as exc:
        raise _Refused(f"{given}: {exc}.") from None
    if not prose:
        return f"{given} has recorded nothing a methods section could say yet."
    return (f"The methods of {given}, as its report gives them; quote them as they are "
            f"(a gap they name was not recorded):\n\n{prose}")


def _read_docs(box: Toolbox, asked: dict[str, Any]) -> str:
    """What the docs of this installed version say (:mod:`fastmdxplora.software_docs`)."""
    from fastmdxplora.software_docs import DocsNotFound, read_docs

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

    part = asked.get("part")
    if part is not None:
        whole = isinstance(part, int) and not isinstance(part, bool)
        if isinstance(part, float) and math.isfinite(part) and part.is_integer():
            part, whole = int(part), True
        if not whole or part < 1:
            raise _Refused("`part` is a whole number, 1 or more.")
    try:
        return read_docs(query=text("query"), page=text("page"), section=text("section"),
                         part=part)
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


#: name -> (its arguments, what it tells, the function)
_TOOLS: dict[str, tuple[str, str, Callable[[Toolbox, dict[str, Any]], str]]] = {
    "find_structure": (
        "`query` (a molecule's name or a PDB identifier), and optionally `organism` "
        "(a species as the PDB names it, such as Homo sapiens), `most` (entries, 1 to "
        "10) and `predicted` (true for AlphaFold DB's models as well).",
        "the PDB entries a name answers to, grouped by protein, the most studied "
        "first, each with its method, resolution, chains and ligands; and AlphaFold "
        "DB's predicted models where the PDB holds no structure or where asked. "
        "Look here before you write a PDB identifier for a system named in words.",
        _find_structure),
    "inspect_structure": (
        "`system` (a PDB identifier or a structure file's path).",
        "the chains, protein residues, ligands, ions and water a structure holds, "
        "the residues whose protonation state a study may set, any side chain by "
        "a structural metal, and what is worth knowing about it.",
        _inspect_structure),
    "preview_setup": (
        "`config` (the study, as you would write it).",
        "what setup will build (particles, box, solute, water, ions, a padding "
        "grown for the cutoff) and how long the whole study takes on this "
        "machine, where it has been timed.",
        _preview_setup),
    "check_config": (
        "`config` (the study, as you would write it).",
        "whether the validator accepts it, and if not why and what would fix "
        "it; if so, the plan the person will read, defaults marked.",
        _check_config),
    "read_study": (
        "`study` (a study's folder).",
        "another study's record, not the one on screen: its config, what its "
        "analyses found (means, errors, units), the checks it was held to, how "
        "long it ran and why, and what would fix it if it stopped.",
        _read_study),
    "methods_of_study": (
        "`study` (a study's folder).",
        "that study's methods paragraphs as its report gives them, written from "
        "what it recorded: preparation, protocol, how each mean and its error "
        "were determined, any rule it ran until, an AI model's part, the software. "
        "Look here before you write or answer about a methods section.",
        _methods_of_study),
    "list_studies": (
        "optionally `tag` (only the studies tagged so).",
        "the studies in the workspace, newest first: each one's system, kind, "
        "state, length, force field, tags, the person's note and what its "
        "analyses recorded (means with their errors); then the YAML files at "
        "its top.",
        _list_studies),
    "compare_studies": (
        "`first` and `second` (two studies' folders).",
        "how two studies differ: each setting one asks for that the other does not, "
        "and what each recorded side by side, a difference called resolved only "
        "where it is more than the stated multiple of its combined standard error. "
        "Quote it rather than comparing means yourself.",
        _compare_studies),
    "check_selection": (
        "`system` and `expression` (an MDTraj selection).",
        "how many atoms and which residues the selection matches in that "
        "structure.",
        _check_selection),
    "read_docs": (
        "`query` (a question in words), with `page` to ask one page only; or "
        "`page` (a page's name, as listed) and optionally `section` (a heading on "
        "it) and `part`; nothing, for the list of pages.",
        "what the docs of this installed version of FastMDXplora say: the "
        "passages that answer a question, each with its page and section, or a "
        "page's sections, or one section whole. Look here before you answer how "
        "the software, its pages, its buttons or its commands work.",
        _read_docs),
}


_A_STUDY = {"type": "object", "description": "A study config, as the file holds it."}
_A_STRUCTURE = {"type": "string",
                "description": "A PDB identifier such as 1UBQ, or the path to a PDB or "
                               "mmCIF file."}
_A_FOLDER = {"type": "string", "description": "A study's folder, as given to --output."}

#: Each tool's arguments as the AI model is told them, by name.
_SCHEMAS: dict[str, dict[str, Any]] = {
    "find_structure": {"type": "object", "properties": {
        "query": {"type": "string",
                  "description": "A molecule's name, such as lysozyme or trp-cage, or a "
                                 "PDB identifier."},
        "organism": {"type": "string",
                     "description": "A species as the PDB names it, such as Homo sapiens."},
        "most": {"type": "integer", "minimum": 1, "maximum": 10,
                 "description": "How many entries to offer; 5 if not given."},
        "predicted": {"type": "boolean",
                      "description": "AlphaFold DB's models as well as the PDB's entries."}},
        "required": ["query"]},
    "inspect_structure": {"type": "object", "properties": {"system": _A_STRUCTURE},
                          "required": ["system"]},
    "preview_setup": {"type": "object", "properties": {"config": _A_STUDY},
                      "required": ["config"]},
    "check_config": {"type": "object", "properties": {"config": _A_STUDY},
                     "required": ["config"]},
    "read_study": {"type": "object", "properties": {"study": _A_FOLDER},
                   "required": ["study"]},
    "methods_of_study": {"type": "object", "properties": {"study": _A_FOLDER},
                         "required": ["study"]},
    "list_studies": {"type": "object", "properties": {
        "tag": {"type": "string", "description": "Only the studies tagged so."}}},
    "compare_studies": {"type": "object", "properties": {
        "first": _A_FOLDER, "second": _A_FOLDER}, "required": ["first", "second"]},
    "current_view": {"type": "object", "properties": {}, "additionalProperties": False},
    "check_selection": {"type": "object", "properties": {
        "system": _A_STRUCTURE,
        "expression": {"type": "string", "description": "An MDTraj selection."}},
        "required": ["system", "expression"]},
    "read_docs": {"type": "object", "properties": {
        "query": {"type": "string",
                  "description": "What to find in the docs, in words, such as what the "
                                 "Useful and Wrong buttons do."},
        "page": {"type": "string",
                 "description": "A page's name as the list of pages gives it, such as "
                                "agent or gui: with `query`, the passages from that page "
                                "alone; without, its sections."},
        "section": {"type": "string",
                    "description": "A heading on that page, to read that section whole; "
                                   "where a heading is on the page twice, the headings "
                                   "above it too, as `A > B`."},
        "part": {"type": "integer", "minimum": 1,
                 "description": "Which part of a long section; 1 if not given."}},
        "additionalProperties": False},
}


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
