"""The tools an AI app can call.

Each says what the software found, in the software's words, as the Agent's
own tools do; none judges chemistry or convergence of its own. A tool that
cannot do what it was asked says why, and what would fix it, as a tool
result marked as an error, so the AI model can put it right.

Studies, configs and structures are read and written inside the workspace
only (:mod:`fastmdxplora.mcp.workspace`).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastmdxplora.mcp.workspace import Workspace
from fastmdxplora.refusals import CodedError

__all__ = ["Tool", "ToolError", "Context", "TOOLS", "plan_id_of"]

#: The tools say a structure as the Agent's tools do: a PDB identifier, or a file.
_STRUCTURE = {"type": "string", "description": (
    "A PDB identifier such as 1UBQ, or a PDB or mmCIF file in the workspace.")}
_CONFIG = {"type": "string", "description": (
    "A study config: the path of a YAML file in the workspace, or the YAML itself.")}
_STUDY = {"type": "string", "description": "A study's folder in the workspace."}


class ToolError(CodedError, Exception):
    """What a tool could not do, said to the AI model so it can put it right."""

    default_code = "mcp.tool.refused"


@dataclass
class Context:
    """What the tools reach: the workspace, the call being served, the
    person's AI model for the Agent, and whether studies may be run."""

    workspace: Workspace
    call: Any = None
    complete_for: Callable[[], Any] | None = None
    runs: bool = True


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    properties: dict[str, Any]
    required: tuple[str, ...]
    annotations: dict[str, bool]
    run: Callable[[Context, dict[str, Any]], str]
    #: Started or stopped work on this machine: left out of a read-only server.
    acts: bool = False

    def listed(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": "object", "properties": self.properties,
                                  "additionalProperties": False}
        if self.required:
            schema["required"] = list(self.required)
        return {"name": self.name, "title": self.title, "description": self.description,
                "inputSchema": schema, "annotations": {"title": self.title, **self.annotations}}

    def checked(self, arguments: Any) -> dict[str, Any]:
        """The arguments, held to the schema the tool lists."""
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ToolError("The arguments are an object, named as the tool lists them.")
        unknown = sorted(set(arguments) - set(self.properties))
        if unknown:
            raise ToolError(f"{self.name} takes no {', '.join(unknown)}. It takes: "
                            f"{', '.join(self.properties) or 'nothing'}.")
        for name in self.required:
            if arguments.get(name) in (None, ""):
                raise ToolError(f"{self.name} needs `{name}`: "
                                f"{self.properties[name].get('description', '')}")
        for name, value in arguments.items():
            kind = self.properties[name].get("type")
            if kind == "string" and not isinstance(value, str):
                raise ToolError(f"`{name}` is text.")
            if kind == "boolean" and not isinstance(value, bool):
                raise ToolError(f"`{name}` is true or false.")
            if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                raise ToolError(f"`{name}` is a whole number.")
            allowed_values = self.properties[name].get("enum")
            if kind in ("string", "integer") and allowed_values and value not in allowed_values:
                raise ToolError(f"`{name}` is one of: "
                                f"{', '.join(str(v) for v in allowed_values)}.")
            if kind == "array":
                allowed = self.properties[name].get("items", {}).get("enum")
                if not isinstance(value, list) or (
                        allowed and any(v not in allowed for v in value)):
                    raise ToolError(f"`{name}` is a list of: {', '.join(allowed or [])}.")
        return arguments


# ---------------------------------------------------------------------------
# Shared readings
# ---------------------------------------------------------------------------
def plan_id_of(path: Path) -> str:
    """What a config file says, in twelve characters: the plan checked is
    the plan run only while the file is unchanged."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _is_a_path(given: str) -> bool:
    """A file named, rather than YAML given: one line, not a setting."""
    text = given.strip()
    return "\n" not in text and ": " not in text and not text.endswith(":") \
        and not text.startswith(("{", "["))


def _config_from(ctx: Context, given: str) -> tuple[dict[str, Any], Path | None]:
    """A config named by its file, or given as YAML; and its file, if any."""
    import yaml

    file: Path | None = None
    if _is_a_path(given):
        file = ctx.workspace.inside(given)
        if file is None:
            raise ToolError(f"{given} is outside the workspace ({ctx.workspace.root}).")
        if file.suffix.lower() not in (".yml", ".yaml"):
            raise ToolError(f"{given} is not a config file: a config is YAML, in a .yml "
                            "or .yaml file, or the YAML itself.")
        if not file.is_file():
            raise ToolError(f"There is no config at {given} in the workspace.")
        text = file.read_text(encoding="utf-8")
    else:
        text = given
    try:
        config = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ToolError(f"That is not YAML a config can be read from: {exc}") from None
    if not isinstance(config, dict) or not config:
        raise ToolError("A config is a mapping of settings, as written to a file.")
    return config, file


def _continued(ctx: Context, config: dict[str, Any]) -> Path | None:
    """The study a config extends in place, read in the workspace."""
    from fastmdxplora.gui.browse import is_study

    simulation = config.get("simulation")
    named = simulation.get("resume_from") if isinstance(simulation, dict) else None
    if not named:
        return None
    folder = ctx.workspace.inside(named)
    if folder is None:
        raise ToolError(f"resume_from names {named}, outside the workspace.")
    return folder if folder.is_dir() and is_study(folder) else None


def _refused(exc: BaseException) -> str:
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


def _confined(ctx: Context, config: Any, where: str = "") -> None:
    """Refuse a config naming a file or folder outside the workspace.

    Read from the values rather than from a list of the settings that take
    a path, as a study sent to another machine gathers its files: a setting
    added later that takes one is held to the workspace without anyone
    remembering to add it here. A value is taken for a path where it names
    something that exists, is absolute, starts from a home folder or climbs
    out with ``..``; a PDB identifier or a selection is none of those.
    """
    if isinstance(config, dict):
        for key, value in config.items():
            _confined(ctx, value, f"{where}.{key}" if where else str(key))
        return
    if isinstance(config, list):
        for index, value in enumerate(config):
            _confined(ctx, value, f"{where}[{index}]")
        return
    if not isinstance(config, str) or not config.strip():
        return
    # As a reader of the value would take it: stripped, and with `//` and
    # `/./` gone, which is what pathlib makes of them anyway. A value still
    # holding a line break after that is text, not a file name.
    text = os.path.normpath(config.strip())
    if "\n" in text:
        return
    try:
        named = Path(text).expanduser()
        exists = (named if named.is_absolute() else ctx.workspace.root / named).exists()
    except (OSError, RuntimeError, ValueError):
        named, exists = Path(text), False
    if (exists or named.is_absolute() or text.startswith("~") or ".." in Path(text).parts) \
            and ctx.workspace.inside(text) is None:
        raise ToolError(f"`{where}` names {text}, outside the workspace "
                        f"({ctx.workspace.root}). Copy it into the workspace and name it "
                        "there.")


def _accepted(ctx: Context, config: dict[str, Any]) -> Path | None:
    """Validate as a run would, raising the refusal as a tool error; the
    study continued, where the config continues one. Every path it names
    is held to the workspace too."""
    from fastmdxplora.config.loader import ConfigError, validate_config

    continuing = _continued(ctx, config)
    try:
        validate_config(copy.deepcopy(config), require_systems=continuing is None)
    except ConfigError as exc:
        raise ToolError(_refused(exc)) from None
    # After the validator, so a setting it refuses is refused for what it is.
    _confined(ctx, config)
    return continuing


def _plan_lines(config: dict[str, Any]) -> list[str]:
    from fastmdxplora.gui.plan import plan_of

    return [f"  {line['label']}: {line['value']}" + (" (default)" if line.get("default") else "")
            for line in plan_of(config)]


def _cannot_run_here(config: dict[str, Any]) -> str | None:
    """What this machine lacks to run the config, if anything."""
    from fastmdxplora.gui.exploration import exploration_environment_error

    try:
        return exploration_environment_error(config)
    except Exception:  # noqa: BLE001 - a check, not the result
        return None


def _looked(ctx: Context, tool: str, asked: dict[str, Any]) -> str:
    """One of the Agent's own tools, as the Agent would use it."""
    from fastmdxplora.agent.tools import Toolbox

    look = Toolbox(path_for=ctx.workspace.path_for).use(tool, asked)
    if not look.ok:
        raise ToolError(look.said)
    return look.said


#: What a study's card is read from: checked for links out before a study
#: is listed, as the whole study is before it is read.
_CARD_READS = ("resolved_config.yml", "exploration.yml", "manifest.json",
               "batch_manifest.json", "analysis", "pmf.json")


def _links_out(workspace: Workspace, folder: Path, *, whole: bool = True) -> Path | None:
    """A link inside a study that leads out of the workspace, if any: what
    is read through it would be read from outside. ``whole`` false looks
    only at what a listing reads, which is quick for a study of many runs."""
    if not whole:
        named = [folder / name for name in _CARD_READS]
        named += list((folder / "analysis").glob("*/options.json"))
        named += list((folder / "analysis").glob("*"))
        return next((p for p in named if p.is_symlink() and workspace.inside(p) is None),
                    None)
    for here, folders, files in os.walk(folder, followlinks=False):
        for name in (*folders, *files):
            path = Path(here) / name
            if path.is_symlink() and workspace.inside(path) is None:
                return path
    return None


def _study(ctx: Context, given: str) -> Path:
    from fastmdxplora.gui.browse import is_study

    folder = ctx.workspace.inside(given)
    if folder is None:
        raise ToolError(f"{given} is outside the workspace ({ctx.workspace.root}).")
    if folder == ctx.workspace.root or not folder.is_dir() or not is_study(folder):
        raise ToolError(f"{given} is not a study folder: one holding a manifest, a "
                        "resolved config, or simulation, analysis or report. "
                        "list_studies names the studies here.")
    leaving = _links_out(ctx.workspace, folder)
    if leaving is not None:
        raise ToolError(f"{ctx.workspace.shown(leaving)} links out of the workspace, so "
                        f"{given} is not read.")
    return folder


def studies_here(workspace: Workspace) -> tuple[list[dict[str, Any]], bool]:
    """The studies in the workspace as cards, newest first, and whether
    there are more than were looked at.

    Each folder at the top is looked in, whatever its name: one called
    `runs` at the top is a folder of studies, not a study's own runs. A
    study reached through a link out of the workspace is left out."""
    from fastmdxplora.gui.workspace import studies_in

    cards: list[dict[str, Any]] = []
    more = False
    try:
        tops = sorted(p for p in workspace.root.iterdir()
                      if p.is_dir() and not p.is_symlink() and not p.name.startswith("."))
    except OSError:
        tops = []
    for top in tops:
        found = studies_in(top)
        more = more or bool(found.get("more"))
        for card in found.get("studies") or []:
            folder = workspace.inside(card["path"])
            if folder is not None and folder != workspace.root \
                    and _links_out(workspace, folder, whole=False) is None:
                cards.append(card)
    cards.sort(key=lambda card: card.get("when") or "", reverse=True)
    return cards, more


def with_error(value: float, error: float | None, *, sign: bool = False) -> str:
    """A value and its standard error as the report gives them
    (:func:`fastmdxplora.statistics.with_its_error`)."""
    from fastmdxplora.statistics import with_its_error

    return with_its_error(value, error, sign=sign)


def _mean(side: dict[str, Any] | None) -> str:
    if not side:
        return "not recorded"
    if side.get("withheld"):
        return f"not determined ({side['withheld']})"
    mean, error, unit = side.get("mean"), side.get("error"), side.get("unit") or ""
    if mean is None:
        return "not recorded"
    return f"{with_error(mean, error)} {unit}".strip()


# ---------------------------------------------------------------------------
# The Agent
# ---------------------------------------------------------------------------
#: The phases the Agent is told about unless asked for others: as the GUI
#: and `fastmdx agent` tell it, a smaller space to go wrong in.
_AGENT_PHASES = ["setup", "simulation"]

#: The Agent's instructions that are done in FastMDXplora's own window.
_IN_THE_WINDOW = ("open viewer", "open overview", "open report", "open builder",
                  "show config", "download config")


#: As much as the Agent's AI model is given to write a reply in.
AGENT_MAX_TOKENS = 4000


def _ask_agent(ctx: Context, args: dict[str, Any]) -> str:
    """The FastMDXplora Agent, as in the GUI: an AI model, the software's
    tools to look with, and the validator as the judge. The AI model is the
    AI app's own where it lends it, so nothing is paid twice; else the
    person's, on their own key."""
    from fastmdxplora.agent import propose_config
    from fastmdxplora.agent.tools import Look, Toolbox
    from fastmdxplora.mcp.protocol import NotLent
    from fastmdxplora.refusals import StudyError, refusal_of

    lent = ctx.call is not None and ctx.call.can_sample()
    replied: list[str] = []
    if lent:
        # Bound to what was asked, so a reply lent for one request is never
        # given back to another.
        bound_to = "ask_agent:" + hashlib.sha256(json.dumps(
            args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

        def complete(prompt: str) -> str:
            text, model = ctx.call.sample(prompt, bound_to=bound_to,
                                          max_tokens=AGENT_MAX_TOKENS)
            replied.append(model)
            return text
    elif ctx.complete_for is None:
        raise ToolError("The Agent has no AI model here.")
    else:
        try:
            complete = ctx.complete_for()
        except StudyError as exc:
            raise ToolError(f"{refusal_of(exc).message}\nThis AI app does not lend its "
                            "model, so the Agent writes with an AI model you choose once, "
                            "in a terminal: `fastmdx agent model`. Every other tool here "
                            "works without one.") from None
    def keep(key: str, make: Callable[[], Any]) -> Any:
        # Lent in rounds, each round works the prompts out again: from what
        # the first round read, so a running study's record or a look that
        # has moved on since does not change them.
        if lent and ctx.call is not None:
            return ctx.call.kept(key, make, bound_to=bound_to)
        return make()

    def given_config() -> str | None:
        given = args["config"]
        if _is_a_path(given):
            _, file = _config_from(ctx, given)
            return file.read_text(encoding="utf-8") if file is not None else None
        return given

    current = keep("config", given_config) if args.get("config") else None
    status = (keep("study", lambda: study_record(_study(ctx, args["study"]),
                                                 for_the_agent=True))
              if args.get("study") else None)

    box = Toolbox(path_for=ctx.workspace.path_for)
    told = ctx.call.progress if ctx.call is not None else (lambda message: None)
    used = box.use

    def use(name: str, asked: dict[str, Any]) -> Any:
        told(f"The Agent looks: {name}")

        def looked() -> dict[str, Any]:
            look = used(name, asked)
            box.looks.pop()  # kept below, the same way every round
            return {"tool": look.tool, "asked": look.asked, "said": look.said,
                    "ok": look.ok}

        said = keep(f"look-{len(box.looks)}", looked)
        look = Look(said["tool"], said["asked"], said["said"], said["ok"])
        box.looks.append(look)
        return look

    box.use = use  # type: ignore[method-assign]

    def written(prompt: str) -> str:
        told("The Agent is writing")
        return complete(prompt)

    # The person's own AI model replies by tool calls where it takes them.
    # A model the AI app lends is asked in text: sampling is a prompt and a
    # reply, and its rounds keep what each read.
    turn = None if lent else getattr(complete, "turn", None)
    if callable(turn):
        def turned(system: str, messages: list[Any], tools: list[Any]) -> Any:
            told("The Agent is writing")
            return turn(system, messages, tools)
        written.turn = turned  # type: ignore[attr-defined]
        written.turned_away = getattr(complete, "turned_away", None)  # type: ignore[attr-defined]

    try:
        proposal = propose_config(args["request"], written,
                                  phases=args.get("phases") or list(_AGENT_PHASES),
                                  current_config=current, run_status=status, tools=box,
                                  defaults=_your_defaults(ctx))
    except StudyError as exc:
        raise ToolError(refusal_of(exc).message) from None
    except NotLent as exc:
        raise ToolError(f"{exc} Nothing was written. Ask again to have the AI app lend "
                        "it; your own API key is not used in its place.") from None
    recorded, said_whose = _whose(ctx, lent, replied)

    checked = [f"  - {look.tool}{'' if look.ok else ' (refused)'}: "
               f"{(look.said.splitlines() or [''])[0]}" for look in proposal.looks]
    after = (["", said_whose]
             + (["", "What the Agent checked with the software:", *checked] if checked else []))
    if proposal.question:
        choices = list(getattr(proposal, "choices", ()) or ())
        return "\n".join([f"The Agent asks: {proposal.question}",
                          *([f"Candidates: {'; '.join(choices)}."] if choices else []),
                          "Answer it in a new request, with what it asks for.", *after])
    if proposal.answer:
        scene = getattr(proposal, "scene", None)
        # The same parts write_scene takes; nothing is written until asked.
        shown = ([f"It proposes a scene: {json.dumps(scene, sort_keys=True)}. write_scene "
                  "writes it, once the person agrees."] if scene else [])
        return "\n".join([f"The Agent says: {proposal.answer}", *shown, *after])
    if proposal.action:
        if proposal.action in _IN_THE_WINDOW:
            next_step = ("That is done in FastMDXplora's own window, which `fastmdx gui` "
                         "opens.")
        elif not ctx.runs:
            next_step = ("This server does not start or stop studies: that is done in "
                         "FastMDXplora itself, with `fastmdx explore` or the GUI.")
        elif proposal.action == "stop":
            next_step = "stop_study stops a running study, once the person has agreed."
        elif proposal.action in ("analyze again", "write the report again"):
            named = (proposal.arguments or {}).get("analyses")
            next_step = ("run_phases_again runs it on the study, once the person has agreed"
                         + (f", with the analyses {', '.join(named)}." if named else "."))
        else:
            next_step = ("check_study shows the plan; start_study runs it once the person "
                         "has agreed to that plan.")
        return "\n".join([f"The Agent read this as an instruction: {proposal.action}.",
                          next_step, *after])
    corrected = [f"  - {a.refusal.code}: {a.refusal.message}"
                 for a in proposal.attempts if a.refusal is not None]
    if not proposal.accepted:
        last = proposal.refusal
        raise ToolError("\n".join([
            f"The Agent gave up after {proposal.cycles} attempt"
            f"{'' if proposal.cycles == 1 else 's'}; the validator refused each.",
            *(["Refused:", *corrected] if corrected else []),
            *([f"Last: {last.message}"] if last is not None and not corrected else []),
            "Say more of what the study is for, or check a config by hand with check_study.",
            *after]))
    return _proposed(ctx, args, proposal, corrected, after, recorded)


def _whose(ctx: Context, lent: bool, replied: list[str]) -> tuple[str | None, str]:
    """Which AI model the Agent wrote with, as a study records it
    (``agent_model``), and as it is said to the person."""
    from fastmdxplora.agent import load_choice

    if lent:
        models = list(dict.fromkeys(replied))
        app = (ctx.call.client_name if ctx.call is not None else None) or "the AI app"
        named = ", ".join(models) or "none"
        return (", ".join(f"{app}/{m}" for m in models) or None,
                f"Written with {app}'s model ({named}), lent through the protocol: your "
                "own API key was not used.")
    chosen = load_choice()
    if chosen is None:
        return None, "Written with the AI model chosen with `fastmdx agent model`."
    return (f"{chosen.provider}/{chosen.model}",
            f"Written with your AI model ({chosen.provider}/{chosen.model}), chosen with "
            "`fastmdx agent model`, on your own API key.")


def _proposed(ctx: Context, args: dict[str, Any], proposal: Any, corrected: list[str],
              after: list[str], recorded: str | None) -> str:
    """An accepted study: recorded as the Agent's, saved, and its plan said."""
    import yaml

    from fastmdxplora.naming import default_output_name, system_of

    config = dict(proposal.config)
    _confined(ctx, config)
    # Whose study this is, as `fastmdx agent` records it: an AI model wrote
    # it, and which one, as the AI app named it where it lent its own.
    config["agent"] = "assisted"
    if recorded is not None:
        config["agent_model"] = recorded
    else:
        config.pop("agent_model", None)
    name = default_output_name(system_of(config))
    if _continued(ctx, config) is None and not config.get("output"):
        config["output"] = name
    text = yaml.safe_dump(config, sort_keys=False)
    tries = proposal.cycles
    lines = [f"The FastMDXplora Agent wrote a study, and the validator accepted it "
             f"{'first time' if tries == 1 else f'after {tries} attempts'}."]
    if corrected:
        lines += ["Refused on the way, and corrected:", *corrected]
    if getattr(proposal, "note", None):
        lines.append(f"The Agent says: {proposal.note}")
    if args.get("save", True):
        asked = " ".join(str(args["request"]).split())[:400]
        target = _new_file(ctx.workspace.root, name)
        target.write_text(f"# Written by the FastMDXplora Agent, asked: {asked}\n" + text,
                          encoding="utf-8")
        lines += [f"Saved to {ctx.workspace.shown(target)}. Nothing has been run.", "",
                  "The plan:", *_plan_lines(config), "", _plan_id_line(ctx, target)]
    else:
        lines += ["Not saved; save_study writes it.", "", "The plan:", *_plan_lines(config)]
    return "\n".join([*lines, *after, "", "The config:", text.rstrip()])


def _new_file(folder: Path, stem: str) -> Path:
    """``stem.yml``, or ``stem-2.yml`` and so on: never one already there."""
    for n in range(1, 1000):
        target = folder / (f"{stem}.yml" if n == 1 else f"{stem}-{n}.yml")
        try:
            target.open("x", encoding="utf-8").close()
        except FileExistsError:
            continue
        return target
    raise ToolError(f"Too many files named {stem} in the workspace.")


# ---------------------------------------------------------------------------
# Looking
# ---------------------------------------------------------------------------
def _find_structure(ctx: Context, args: dict[str, Any]) -> str:
    return _looked(ctx, "find_structure", {
        key: args[key] for key in ("query", "organism", "most", "predicted") if key in args})


def _inspect_structure(ctx: Context, args: dict[str, Any]) -> str:
    return _looked(ctx, "inspect_structure", {"system": args["system"]})


def _check_selection(ctx: Context, args: dict[str, Any]) -> str:
    return _looked(ctx, "check_selection",
                   {"system": args["system"], "expression": args["expression"]})


def _preview_setup(ctx: Context, args: dict[str, Any]) -> str:
    config, _ = _config_from(ctx, args["config"])
    return _looked(ctx, "preview_setup", {"config": config})


def _your_defaults(ctx: Context) -> Any:
    """The workspace's fastmdx-defaults.yml, read and checked, or None; a
    file that is there and wrong is said, never passed over."""
    from fastmdxplora.config.defaults_file import defaults_for
    from fastmdxplora.config.loader import ConfigError

    try:
        return defaults_for(ctx.workspace.root)
    except ConfigError as exc:
        raise ToolError(str(exc)) from None


def _filled(ctx: Context, config: dict[str, Any]) -> tuple[dict[str, Any], list[str], Any]:
    """The config as it will run: your defaults filling what it leaves
    unset, the settings they filled, and the defaults themselves. Checked
    as it will run: a config the validator accepts that your defaults make
    it refuse is refused here, naming the file, not once it is saved."""
    from fastmdxplora.config.defaults_file import refused_with, with_defaults

    defaults = _your_defaults(ctx)
    filled, names = with_defaults(config, defaults)
    refusal = refused_with(config, filled, defaults)
    if refusal is not None:
        raise ToolError(_refused(refusal))
    return filled, names, defaults


def _check_study(ctx: Context, args: dict[str, Any]) -> str:
    config, file = _config_from(ctx, args["config"])
    continuing = _accepted(ctx, config)
    # The plan of what will run: the workspace's defaults fill what the
    # config leaves unset when it is saved and run.
    config, filled, defaults = _filled(ctx, config)
    lines = ["Accepted by the validator. The plan:", *_plan_lines(config)]
    if filled:
        lines.append(f"Your defaults ({defaults.path.name}) fill what it leaves unset: "
                     + ", ".join(filled) + ".")
    if continuing is not None:
        lines.append(f"It continues the study at {ctx.workspace.shown(continuing)} in place: "
                     "its next segment, joined to the others, with the analyses rerun "
                     "over the whole.")
    lacking = _cannot_run_here(config)
    if lacking:
        lines += ["", f"This machine cannot run it yet: {lacking}"]
    if file is not None:
        lines += ["", _plan_id_line(ctx, file)]
    else:
        lines += ["", "To run it, save it first with save_study."]
    return "\n".join(lines)


def _plan_id_line(ctx: Context, file: Path) -> str:
    """What runs a checked file: start_study with its plan_id, or, on a
    server that does not run studies, FastMDXplora itself."""
    if ctx.runs:
        return (f"plan_id: {plan_id_of(file)} (for start_study, once the person has agreed "
                f"to this plan; it changes if {ctx.workspace.shown(file)} does)")
    return (f"To run it: `fastmdx explore --config {ctx.workspace.shown(file)}`, or the GUI. "
            "This server does not start studies.")


def _save_study(ctx: Context, args: dict[str, Any]) -> str:
    text = args["config"]
    if _is_a_path(text):
        raise ToolError("Give the config itself as YAML; save_study writes a new file.")
    config, _ = _config_from(ctx, text)
    _accepted(ctx, config)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", args["name"].strip()).strip("._-")
    stem = re.sub(r"\.(ya?ml)$", "", stem, flags=re.IGNORECASE)[:96]
    if not stem:
        raise ToolError("Name the file with letters or digits.")
    if f"{stem}.yml".lower() in _MARKERS:
        raise ToolError(f"{stem}.yml is the name a study gives its own config, and would "
                        "make the workspace look like a study. Give another name.")
    by_hand = bool(args.get("by_hand"))
    said_how = config.get("agent")
    if not by_hand:
        app = ctx.call.client_name if ctx.call is not None else None
        text, config = _as_assisted(text, config, app)
    # Saved as it will run, your defaults filled in and each recorded in
    # `decisions`: the plan agreed to is the file, and the file is what runs.
    # Added as lines, so the comments and order written stay as written.
    before = config
    config, filled, _defaults = _filled(ctx, config)
    if filled:
        _accepted(ctx, config)
        text = _with_lines_for(text, before, config, filled)
    target = ctx.workspace.root / f"{stem}.yml"
    try:
        with target.open("x", encoding="utf-8") as out:
            out.write(text if text.endswith("\n") else text + "\n")
    except FileExistsError:
        raise ToolError(f"{target.name} is in the workspace already, and is never written "
                        "over. Give a new name.") from None
    if said_how is not None:
        whose = f"Recorded as it says it was written (agent: {said_how})."
    elif by_hand:
        whose = "Recorded as the person's own."
    else:
        whose = ("Recorded as written in an AI app (agent: assisted"
                 + (f", in {config['agent_model']}" if config.get("agent_model") else "")
                 + ").")
    return "\n".join([f"Saved to {ctx.workspace.shown(target)}; accepted by the validator. "
                      f"{whose} Nothing has been run.", "The plan:", *_plan_lines(config), "",
                      _plan_id_line(ctx, target)])


def _with_lines_for(text: str, before: dict[str, Any], config: dict[str, Any],
                    filled: list[str]) -> str:
    """``text`` with the settings your defaults filled, and their
    decisions, added under their blocks (or as blocks of their own at the
    end), so what was written stays as written; where that would not read
    back as ``config``, the whole is written out again."""
    import yaml

    added: dict[str, dict[str, Any]] = {}
    for name in filled:
        block, key = name.split(".", 1)
        added.setdefault(block, {})[key] = config[block][key]
    decided = {name: config["decisions"][name] for name in filled
               if name in (config.get("decisions") or {})
               and name not in (before.get("decisions") or {})}
    if decided:
        added["decisions"] = decided
    lines = text.rstrip("\n").splitlines()
    for block, settings in added.items():
        written = yaml.safe_dump(settings, sort_keys=False).splitlines()
        at = next((n for n, line in enumerate(lines)
                   if re.match(rf"^{re.escape(block)}:\s*(#.*)?$", line)), None)
        if at is None:
            lines += [f"{block}:", *("  " + line for line in written)]
            continue
        indent = next((len(line) - len(line.lstrip()) for line in lines[at + 1:]
                       if line.strip() and not line.lstrip().startswith("#")), 0)
        if indent == 0:
            lines = []
            break
        lines[at + 1:at + 1] = [" " * indent + line for line in written]
    out = "\n".join(lines) + "\n"
    try:
        if lines and yaml.safe_load(out) == config:
            return out
    except yaml.YAMLError:
        pass
    return yaml.safe_dump(config, sort_keys=False)


def _as_assisted(text: str, config: dict[str, Any],
                 app: str | None) -> tuple[str, dict[str, Any]]:
    """A config an AI app's model wrote, recorded as one: ``agent:
    assisted`` where it says nothing of how it was written, as the Agent's
    own are, and in ``agent_model`` the AI app it was written in, where the
    config names no AI model and the AI app named itself (the AI app does
    not say which AI model it runs, so none is claimed). A study with no
    ``agent`` says a person wrote it, which an AI model's draft is not. Added as lines above
    the rest, so the comments and order written stay as written; where
    that would not read back as the same settings, the whole is written
    out again."""
    import yaml

    if "agent" in config:
        return text, config
    said: dict[str, Any] = {"agent": "assisted"}
    if app and not config.get("agent_model"):
        said["agent_model"] = f"{app} (its own AI model, which the AI app does not name)"
    marked = {**config, **said}
    added = yaml.safe_dump(said, sort_keys=False) + text
    try:
        if yaml.safe_load(added) == marked:
            return added, marked
    except yaml.YAMLError:
        pass
    return yaml.safe_dump({**said, **config}, sort_keys=False), marked


#: The names a study gives files of its own, which mark a folder as a study.
_MARKERS = frozenset({"exploration.yml", "resolved_config.yml"})


# ---------------------------------------------------------------------------
# Reading studies
# ---------------------------------------------------------------------------

def _write_scene(ctx: Context, args: dict[str, Any]) -> str:
    from fastmdxplora.gui.saved_views import views_of
    from fastmdxplora.gui.viewer_selections import selections_of
    from fastmdxplora.scenes import SCENES_DIR, highlighted, write_scene

    folder = _study(ctx, args["study"])
    view: dict[str, Any] = {}
    if args.get("view"):
        saved = views_of(folder)["views"]
        found = [v for v in saved if v["name"] == args["view"]]
        if not found:
            raise ToolError(f"{ctx.workspace.shown(folder)} has no view named {args['view']!r}; "
                            f"its views: {', '.join(v['name'] for v in saved) or 'none'}.")
        view = {k: v for k, v in found[0].items() if k != "name"}
    for key in ("frame", "representation", "colour", "superposed", "superposed_to",
                "smoothed_over"):
        if args.get(key) is not None:
            view[key] = args[key]
    selections = selections_of(folder)["selections"]
    if args.get("highlight"):
        selections.append(highlighted(args["highlight"], bool(args.get("labels"))))
    from fastmdxplora.gui.runs_together import run_shown

    # A study of several runs: the run the GUI plays, as its Viewer shows it.
    played = run_shown(folder)
    said = write_scene(folder, args["name"], view, selections=selections, source=played)
    if not said.get("ok"):
        raise ToolError(said.get("reason") or "The scene could not be made.")
    where = ctx.workspace.shown(Path(said["path"]))
    lines = [f"Wrote the scene {said['name']} at {where}, "
             + (f"frame {said['frame']} of the frames the GUI plays."
                if said.get("frame") is not None else "the study's structure."),
             "It is MolViewSpec (.mvsx): it opens as written in any viewer built on Mol*, "
             "in the FastMDXplora GUI (Viewer, Saved views, Scenes) or dropped on "
             "molstar.org. Tell the person where it is.",
             f"The study's scenes are in its {SCENES_DIR}/ folder."]
    if played is not None:
        lines.append(f"The study is of several runs: the scene is of {played.name}, the run "
                     "the GUI plays, alone.")
    lines += [f"- {note}" for note in said.get("notes") or []]
    return "\n".join(lines)


def _make_movie(ctx: Context, args: dict[str, Any]) -> str:
    from fastmdxplora.movie_maker import make_movie

    folder = _study(ctx, args["study"])
    changes = {key: args[key] for key in ("representation", "colour", "superposed")
               if args.get(key) is not None}
    made = make_movie(folder, name=args.get("name") or "movie", view=args.get("view"),
                      changes=changes or None, first=args.get("from"), last=args.get("to"),
                      every=args.get("every") or 1, between=args.get("between") or 0,
                      fps=args.get("fps") or 24, size=args.get("size") or "1920x1080",
                      turn=bool(args.get("turn")), time=args.get("time", True) is not False)
    if not made.get("ok"):
        raise ToolError(made.get("reason") or "The movie could not be made.")
    where = ctx.workspace.shown(Path(made["path"]))
    lines = [f"Made the movie {where}: {made['frames']} frames, {made['seconds']} s at "
             f"{made['fps']} frames a second, {made['width']} x {made['height']}, "
             f"{made['format'].upper()} ({made['codec']}), {made['bytes'] / 1e6:.1f} MB. "
             "Tell the person where it is.",
             f"Rendered by the study's Viewer in a browser with no window ({made['browser']}); "
             f"encoded as {made['encoder']}."]
    if made.get("between"):
        lines.append(f"{made['between']} frame{'s were' if made['between'] > 1 else ' was'} "
                     "put in between each two frames played, "
                     "each atom moved in a straight line: a smoother movie, not more "
                     "simulation. Say so where the movie is shown.")
    return "\n".join(lines)


def _tag_study(ctx: Context, args: dict[str, Any]) -> str:
    from fastmdxplora.study_tags import add_tags

    folder = _study(ctx, args["study"])
    said = add_tags(folder, args.get("tags"))
    if not said.get("ok"):
        raise ToolError(said.get("reason") or "The tags could not be added.")
    where = ctx.workspace.shown(folder)
    added = said.get("added") or []
    return "\n".join([
        (f"Tagged {where} " + ", ".join(repr(t) for t in added) + "." if added
         else f"{where} had those tags already."),
        "Its tags now: " + ", ".join(said["tags"]) + ".",
        "Kept in its study_tags.json; the person removes a tag, or writes the study's note, "
        "on its card in the GUI's All studies."])


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


def _views_of_study(ctx: Context, args: dict[str, Any]) -> str:
    from fastmdxplora.gui.saved_views import views_of
    from fastmdxplora.gui.viewer_selections import selections_of
    from fastmdxplora.scenes import SCENES_DIR, scenes_of

    folder = _study(ctx, args["study"])
    shown = ctx.workspace.shown(folder)
    views = views_of(folder)["views"]
    scenes = scenes_of(folder)["scenes"]
    selections = selections_of(folder)["selections"]
    lines = [f"Views saved with {shown} in the GUI: {len(views)}"
             + (" (write_scene starts from one by its name)." if views else ".")]
    lines += [f"- {view['name']}: {_view_said(view)}" for view in views]
    lines.append(f"Selections the person named: {len(selections)}"
                 + (" (every scene shows them)." if selections else "."))
    for selection in selections:
        what = selection.get("expression") or f"{len(selection.get('residues') or [])} residues"
        lines.append(f"- {selection.get('name')}: {what}")
    lines.append(f"Scenes written: {len(scenes)}" + ("." if not scenes else ":"))
    lines += [f"- {scene['name']}: {shown}/{SCENES_DIR}/{scene['name']}.mvsx"
              for scene in scenes]
    return "\n".join(lines)


def _list_studies(ctx: Context, args: dict[str, Any]) -> str:
    cards, more = studies_here(ctx.workspace)
    wanted = " ".join(str(args.get("tag") or "").split()).casefold()
    if wanted:
        cards = [c for c in cards if any(t.casefold() == wanted for t in c.get("tags") or [])]
    lines = [f"{len(cards)} stud{'y' if len(cards) == 1 else 'ies'} in {ctx.workspace.root}"
             + (f" tagged {args['tag']!r}" if wanted else "")
             + (", newest first:" if cards else ".")]
    for card in cards:
        said = [str(card.get("system") or "no system"), str(card.get("kind") or "study"),
                str(card.get("state") or "")]
        if card.get("production_ns") is not None:
            said.append(f"{card['production_ns']} ns production"
                        + (f" in {card['pieces']} pieces" if card.get("pieces") else ""))
        if card.get("forcefield"):
            said.append(str(card["forcefield"]))
        if card.get("when"):
            said.append(str(card["when"])[:10])
        lines.append(f"- {ctx.workspace.shown(card['path'])}: " + ", ".join(s for s in said if s))
        if card.get("tags"):
            lines.append("    tagged: " + ", ".join(card["tags"]))
        if card.get("note"):
            lines.append(f"    the person's note: {card['note']}")
        for mean in card.get("means") or []:
            lines.append(f"    {mean.get('label') or mean['analysis']}: {_mean(mean)}")
    if more:
        lines.append("(There are more; only the first are listed.)")
    configs = sorted(p.name for p in ctx.workspace.root.iterdir()
                     if p.is_file() and p.suffix.lower() in (".yml", ".yaml"))
    if configs:
        lines += ["", "YAML files at the top of the workspace: " + ", ".join(configs)]
    return "\n".join(lines)


class _Viewed:
    """A study as the Agent panel's summary reads a run: where, and its state."""

    def __init__(self, root: Path, state: str) -> None:
        self.active_root, self._state = root, state

    def snapshot(self) -> dict[str, Any]:
        return {"active_run": str(self.active_root), "status": self._state,
                "process_running": self._state == "running"}


#: The record's words for the Agent in the GUI, and what they mean here.
_SAID_HERE = (
    ("one marked [runs here] is what `DO: run the fix` runs, the first of them",
     "one marked [runs here] can be run on this machine"),
    ("To continue it, answer with this config.",
     "To continue it, save this config with save_study and run it."),
    ("To run it, answer with this config;",
     "To run it, save this config with save_study and run it;"),
)


def study_record(root: Path, *, for_the_agent: bool = False) -> str:
    """Everything a study recorded that an AI model can read: where it
    stands, its config, what its analyses found, its checks, why it stopped
    and what would fix it. The Agent's own reading of a run; for an AI app,
    its words for the GUI's Agent said as they apply here."""
    from fastmdxplora.gui.agent_panel import _run_status
    from fastmdxplora.gui.workspace import card_of

    state = str(card_of(root).get("state") or "unknown")
    said = _run_status(_Viewed(root, state)) or f"status: {state}"
    if not for_the_agent:
        for theirs, ours in _SAID_HERE:
            said = said.replace(theirs, ours)
    return said


def _read_study(ctx: Context, args: dict[str, Any]) -> str:
    folder = _study(ctx, args["study"])
    return f"The study at {ctx.workspace.shown(folder)}\n" + study_record(folder)


def _methods_of_study(ctx: Context, args: dict[str, Any]) -> str:
    from fastmdxplora.report.document import methods_prose

    folder = _study(ctx, args["study"])
    shown = ctx.workspace.shown(folder)
    if (folder / "batch_manifest.json").is_file():
        raise ToolError(f"{shown} is a study of several runs; each run's report gives its "
                        "own methods. Name one of its runs (list_studies names them).")
    try:
        prose = methods_prose(folder, may_read=lambda path: ctx.workspace.inside(
            str(path)) is not None)
    except PermissionError as exc:
        raise ToolError(f"{shown}: {exc}.") from None
    if not prose:
        return f"{shown} has recorded nothing a methods section could say yet."
    return f"The methods of {shown}, as its report gives them:\n\n{prose}"


def _compare_studies(ctx: Context, args: dict[str, Any]) -> str:
    from fastmdxplora.gui.workspace import studies_compared

    first, second = _study(ctx, args["first"]), _study(ctx, args["second"])
    found = studies_compared(first, second)
    if not found.get("ok"):
        raise ToolError(str(found.get("reason") or "They could not be compared."))
    lines = [f"{ctx.workspace.shown(first)} against {ctx.workspace.shown(second)}"]
    settings = found.get("settings") or []
    if settings:
        lines.append(f"{len(settings)} setting{'s differ' if len(settings) != 1 else ' differs'}:")
        for d in settings:
            lines.append(f"  {d['setting']}: "
                         f"{d['first'] if d.get('in_first') else '(not set)'} -> "
                         f"{d['second'] if d.get('in_second') else '(not set)'}")
    else:
        lines.append("They ask for the same study.")
    measures = found.get("measures") or []
    if measures:
        k = found.get("resolved_at")
        lines.append(f"What each recorded (a difference is resolved where it is more than "
                     f"{k:g} times its combined standard error):")
        for row in measures:
            said = f"  {row.get('label')}: {_mean(row.get('first'))} | {_mean(row.get('second'))}"
            versus = row.get("versus")
            if versus and versus.get("error"):
                said += (f"; second minus first "
                         f"{with_error(versus['difference'], versus['error'], sign=True)} "
                         f"{row.get('unit') or ''}".rstrip()
                         + (", resolved" if versus.get("resolved") else ", not resolved"))
            else:
                said += "; the difference is not assessed: " + _why_not(row)
            lines.append(said)
    return "\n".join(lines)


def _why_not(row: dict[str, Any]) -> str:
    """Why two means were not compared, so an AI model does not compare them."""
    for side, name in ((row.get("first"), "the first"), (row.get("second"), "the second")):
        if not side or side.get("mean") is None:
            return f"{name} recorded no mean"
        if side.get("withheld"):
            return f"{name} mean is not determined"
        if side.get("error") is None:
            return f"{name} recorded no standard error"
        if side.get("error") == 0:
            return f"{name} recorded a standard error of zero"
    return "no standard error to judge it by"


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------
#: What the person is asked before a study starts or stops: a box to tick,
#: so that going ahead is something they did rather than a default.
_GO_AHEAD = {"type": "object", "required": ["go"], "properties": {"go": {
    "type": "boolean", "title": "Go ahead",
    "description": "Tick to go ahead; leave it to change nothing."}}}


def _went_ahead(ctx: Context, key: str, message: str, bound_to: str) -> bool | None:
    """True where the person agreed, False where they did not, None where
    the AI app cannot ask (its own approval of the call is then the gate)."""
    if ctx.call is None:
        return None
    answer = ctx.call.confirm(key, message, _GO_AHEAD, bound_to=bound_to)
    if answer is None:
        return None
    content = answer.get("content") if isinstance(answer.get("content"), dict) else {}
    return answer.get("action") == "accept" and content.get("go") is True


class _Inside:
    """The workspace as the GUI's runtime reads a hosted one: where a
    results folder may be."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace.root
        self._place = workspace

    def inside(self, given: Any) -> Path | None:
        return self._place.inside(given)

    def shown(self, path: Any) -> str:
        return self._place.shown(path)


def _running_here(ctx: Context) -> list[str]:
    """The studies running in the workspace: those started there from the
    GUI or by an AI app, and any found by the record each run keeps
    while it runs, wherever the study is (`fastmdxplora.runs_here`)."""
    from fastmdxplora.runs_here import running_in

    return [ctx.workspace.shown(folder) for folder, _ in running_in(ctx.workspace.root)]


def _time_here(ctx: Context, config: dict[str, Any]) -> str | None:
    """The preview's time for the study on this machine, where known."""
    from fastmdxplora.agent.tools import Toolbox

    look = Toolbox(path_for=ctx.workspace.path_for).use("preview_setup", {"config": config})
    if not look.ok:
        return None
    return next((line for line in look.said.splitlines() if line.startswith("time here:")),
                None)


def _none_running(ctx: Context) -> None:
    from fastmdxplora.runs_here import running_in, said_going

    going = running_in(ctx.workspace.root)
    if going:
        raise ToolError(said_going(ctx.workspace.root, going,
                                   then="stop_study stops it, once the person agrees."),
                        code="environment.workspace.run_going")


def _unused(ctx: Context, folder: Path) -> None:
    if folder == ctx.workspace.root or (folder.exists() and (
            not folder.is_dir() or any(folder.iterdir()))):
        raise ToolError(f"{ctx.workspace.shown(folder)} is in use already, and a run is "
                        "never written over anything. Set `output` in the config to a new "
                        "folder, save it under a new name and check it again.")


def _start_study(ctx: Context, args: dict[str, Any]) -> str:
    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.runs_here import StartRefused, starting_in

    given = args["config"]
    if not _is_a_path(given):
        raise ToolError("start_study runs a config file in the workspace; save it first "
                        "with save_study, then check it with check_study.")
    config, file = _config_from(ctx, given)
    if file is None:  # pragma: no cover - a path always names its file
        raise ToolError("start_study runs a config file in the workspace.")
    now = plan_id_of(file)
    if args["plan_id"] != now:
        raise ToolError(f"{ctx.workspace.shown(file)} is not the file that was checked: its "
                        f"plan_id is {now} now. check_study it again and show the person "
                        "that plan.")
    continuing = _accepted(ctx, config)
    if config.get("agent") == "autonomous":
        # Its record would say it ran without being shown to anyone, and
        # here it is shown before it runs.
        raise ToolError(f"{ctx.workspace.shown(file)} says `agent: autonomous`: that it runs "
                        "without being shown to anyone. Here its plan is shown before it "
                        "runs, so its record would be wrong. Save it with `agent: assisted` "
                        "to run it here, or run it unseen with `fastmdx explore --config "
                        f"{ctx.workspace.shown(file)}`.")
    lacking = _cannot_run_here(config)
    if lacking:
        raise ToolError(f"This machine cannot run it yet: {lacking}")
    _none_running(ctx)
    if continuing is not None:
        where = continuing
    else:
        # A config with no `output` writes beside itself, named after it, so
        # the folder the person agrees to is the folder written.
        requested = str(config.get("output") or file.with_suffix(""))
        found = ctx.workspace.inside(requested)
        if found is None:
            raise ToolError(f"The results folder {requested} is outside the workspace.")
        _unused(ctx, found)
        config = {**config, "output": str(found)}
        where = found

    shown = ctx.workspace.shown(where)
    message = "\n".join([
        f"Start the study in {ctx.workspace.shown(file)} on this machine?",
        *(_plan_lines(config)),
        *([f"It continues {shown} in place."] if continuing is not None
          else [f"Results: {shown}"]),
        *([line] if (line := _time_here(ctx, config)) else []),
    ])
    agreed = _went_ahead(ctx, "start", message, f"start_study:{file}:{now}:{where}")
    if agreed is False:
        return "Not started: the person did not go ahead."

    # The workspace's starting lock, which the GUI's Run holds too: held
    # from the check that nothing is running to the start, so no two
    # starters, here or in a window, both find the workspace free.
    try:
        with starting_in(ctx.workspace.root):
            # Asked again now: the person may have taken minutes to answer.
            if ctx.call is not None and ctx.call.cancelled:
                return "Not started: the call was cancelled."
            _none_running(ctx)
            if continuing is None:
                _unused(ctx, where)
            runtime = DashboardRuntime(workspace_root=ctx.workspace.root,
                                       exploration_root=ctx.workspace.root,
                                       hosting=_Inside(ctx.workspace),
                                       started_by="by an AI app")
            started = runtime.launch_from_config(None, config=config)
    except StartRefused as exc:
        raise ToolError(str(exc), code=exc.code) from None
    if not started.get("ok"):
        raise ToolError(str(started.get("error") or "It could not be started."),
                        code=str(started.get("code") or ToolError.default_code))
    folder, pid = Path(started["output"]).resolve(), int(started["pid"])
    _recorded(ctx, folder, pid)
    return (f"Started {shown} (process {pid}). It runs on its own: closing "
            "the AI app does not stop it. read_study says how far it has got; "
            "stop_study stops it. Its log is "
            f"{ctx.workspace.shown(folder / 'exploration.log')}.")


def _run_phases_again(ctx: Context, args: dict[str, Any]) -> str:
    """A study's analysis or report run again in its folder, by the phase
    command with ``--rerun`` (`fastmdxplora.again` names it), once the
    person agrees."""
    from fastmdxplora import again
    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.refusals import refusal_of
    from fastmdxplora.runs_here import StartRefused, starting_in

    folder = _study(ctx, args["study"])
    try:
        planned = again.plan(folder, args.get("phases") or [], args.get("analyses"))
    except Exception as exc:  # noqa: BLE001 - a refusal, said as one
        found = refusal_of(exc)
        raise ToolError(found.message.replace(str(folder), ctx.workspace.shown(folder)),
                        code=found.code) from None
    _none_running(ctx)
    shown = ctx.workspace.shown(folder)
    message = f"Run {' and '.join(planned.phases)} again on {shown}? " + planned.said()
    bound = f"run_phases_again:{folder}:{','.join(planned.phases)}:" + ",".join(
        planned.analyses or ())
    agreed = _went_ahead(ctx, "rerun", message, bound)
    if agreed is False:
        return "Not run: the person did not go ahead."
    try:
        with starting_in(ctx.workspace.root):
            if ctx.call is not None and ctx.call.cancelled:
                return "Not run: the call was cancelled."
            _none_running(ctx)
            runtime = DashboardRuntime(workspace_root=ctx.workspace.root,
                                       exploration_root=ctx.workspace.root,
                                       hosting=_Inside(ctx.workspace),
                                       started_by="by an AI app")
            runtime.active_root = folder
            started = runtime.run_again(planned.phases, planned.analyses)
    except StartRefused as exc:
        raise ToolError(str(exc), code=exc.code) from None
    if not started.get("ok"):
        raise ToolError(str(started.get("error") or "It could not be started."),
                        code=str(started.get("code") or ToolError.default_code))
    ended = _ended_within(runtime.process, RECORDED_WITHIN_S)
    if ended is None:
        return (f"Started on {shown} (process {started['pid']}): {planned.said()} It runs on "
                "its own: closing the AI app does not stop it. read_study says what it "
                f"found once it ends; its log is {ctx.workspace.shown(folder / 'exploration.log')}.")
    if ended != 0:
        try:
            lines = (folder / "exploration.log").read_text(
                encoding="utf-8", errors="replace").strip().splitlines()
        except OSError:
            lines = []
        raise ToolError(f"Running it again on {shown} failed. The end of its log:\n"
                        + ("\n".join(lines[-12:]) or "It wrote nothing to its log."))
    return (f"Done on {shown}: {' and '.join(planned.phases)} run again. "
            f"What it replaced is in {ctx.workspace.shown(folder / again.PREVIOUS)}. "
            "read_study says what the analyses found now.")


def _ended_within(process: Any, seconds: float) -> int | None:
    """The exit code of a run that ends within ``seconds``, or None."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        code = process.poll() if process is not None else 0
        if code is not None:
            return code
        time.sleep(0.25)
    return None


#: How long a run started here is watched for a record of itself, which it
#: writes once its program has loaded (a study of several runs, and a
#: continuation, in the folder of the run going): a run that ends before
#: then failed to start, and is said to have.
RECORDED_WITHIN_S = 20.0

def _recorded(ctx: Context, folder: Path, pid: int) -> None:
    """Wait for the run to record itself; a run that ends first failed to
    start, and says why from its log."""
    import time

    from fastmdxplora.gui.exploration import _process_alive
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    def recorded() -> bool:
        for here, folders, files in os.walk(folder, followlinks=False):
            if RUN_PROCESS_FILE in files:
                return True
            if len(Path(here).relative_to(folder).parts) >= 3:
                folders[:] = []
        return False

    deadline = time.monotonic() + RECORDED_WITHIN_S
    while time.monotonic() < deadline:
        if recorded():
            return
        if not _process_alive(pid):
            if recorded():
                return
            try:
                lines = (folder / "exploration.log").read_text(
                    encoding="utf-8", errors="replace").strip().splitlines()
            except OSError:
                lines = []
            tail = "\n".join(lines[-12:]) or "It wrote nothing to its log."
            raise ToolError(f"The run of {ctx.workspace.shown(folder)} ended as it "
                            f"started. The end of its log:\n{tail}")
        time.sleep(0.5)


def _stop_study(ctx: Context, args: dict[str, Any]) -> str:
    import json

    from fastmdxplora.gui.exploration import _AdoptedProcess, _identify_run
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE, record_is_from_elsewhere
    from fastmdxplora.simulation.runner import stop_grace_seconds
    from fastmdxplora.stop_after import see_it_stops

    folder = _study(ctx, args["study"])
    shown = ctx.workspace.shown(folder)
    try:
        record = json.loads((folder / RUN_PROCESS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    pid = record.get("pid") if isinstance(record, dict) else None
    if (not isinstance(pid, int) or pid <= 0 or record_is_from_elsewhere(record)
            or _identify_run(pid, folder, record.get("argv")) is not True):
        raise ToolError(f"No run of {shown} is going on this machine.")
    agreed = _went_ahead(ctx, "stop", (
        f"Stop the study {shown}? A run in production stops at its next frame with a "
        "checkpoint there, and can be carried on from it."), f"stop_study:{folder}:{pid}")
    if agreed is False:
        return "Not stopped: the person did not go ahead."
    # Identified again before each signal: the person may have taken
    # minutes to answer, and a process number is reused once its run ends.
    if ctx.call is not None and ctx.call.cancelled:
        return "Not stopped: the call was cancelled."
    if _identify_run(pid, folder, record.get("argv")) is not True:
        return f"{shown} has stopped already."
    _AdoptedProcess(pid, folder).terminate()
    # As the GUI's Stop: time to reach the next frame and checkpoint, then
    # an end that cannot be ignored. Watched from a process of its own, so
    # an AI app closed in the meantime does not take the watching with it.
    try:
        see_it_stops(pid, folder, record.get("argv"), stop_grace_seconds() + 10)
    except OSError as exc:
        return (f"Asked {shown} to stop. Ending it if it does not could not be arranged "
                f"({exc}): if read_study still finds it running in a minute, stop it "
                "again.")
    return (f"Asked {shown} to stop. A run in production stops at its next frame with a "
            "checkpoint there; read_study says when it has, and gives the config that "
            "continues it. One that has not stopped "
            f"{stop_grace_seconds() + 10:g} s from now is ended, whether or not this "
            "AI app is still open.")


_LOOKS = {"readOnlyHint": True, "openWorldHint": True}
_READS = {"readOnlyHint": True, "openWorldHint": False}

#: In the order they are listed, which is the order to reach for them; the
#: Agent last, as it is optional and calls an AI model of the person's own.
TOOLS: tuple[Tool, ...] = (
    Tool("find_structure", "Find a structure by its name",
         "The PDB entries a molecule's name answers to, grouped by protein, the most "
         "studied first, each by its best-resolved entry with its method, resolution, "
         "chains and ligands; and AlphaFold DB's predicted models where the PDB holds no "
         "structure or where asked. Look here before writing a PDB identifier for a "
         "system named in words, and where more than one could be meant, ask the person "
         "which. Offline, it says it cannot reach the PDB and offers nothing.",
         {"query": {"type": "string",
                    "description": "A molecule's name, such as lysozyme or trp-cage, or "
                                   "a PDB identifier."},
          "organism": {"type": "string",
                       "description": "A species as the PDB names it, such as Homo sapiens."},
          "most": {"type": "integer", "minimum": 1, "maximum": 10,
                   "description": "How many entries to offer; 5 if not given."},
          "predicted": {"type": "boolean",
                        "description": "AlphaFold DB's models as well as the PDB's entries."}},
         ("query",), _LOOKS, _find_structure),
    Tool("inspect_structure", "Inspect a structure",
         "What a structure holds: its chains, protein residues, ligands, ions and "
         "water, the residues whose protonation state a study may set, any side chain "
         "by a structural metal, and what is worth knowing before simulating it.",
         {"system": _STRUCTURE}, ("system",), _LOOKS, _inspect_structure),
    Tool("check_selection", "Check an atom selection",
         "How many atoms and which residues an MDTraj selection matches in a structure. "
         "Residue numbers from a paper or the PDB are `resSeq`; `resid` counts from zero.",
         {"system": _STRUCTURE,
          "expression": {"type": "string", "description": "An MDTraj selection."}},
         ("system", "expression"), _LOOKS, _check_selection),
    Tool("preview_setup", "Preview what setup builds",
         "What setup will build from a config (particles, box, solute, water, ions) and "
         "how long the whole study takes on this machine where it has been timed. An "
         "estimate; setup's own numbers replace it once it has run.",
         {"config": _CONFIG}, ("config",), _LOOKS, _preview_setup),
    Tool("check_study", "Check a study before it runs",
         "Whether the validator accepts a config, and if not why and what would fix it; "
         "if so, the plan to show the person, defaults marked, whether this machine can "
         "run it, and the plan_id start_study needs.",
         {"config": _CONFIG}, ("config",), _READS, _check_study),
    Tool("save_study", "Save a study config",
         "Write a config into the workspace as a new file, once the validator accepts it. "
         "A file is never written over: a changed study is saved under a new name. A "
         "config you wrote is recorded as written in an AI app (agent: assisted).",
         {"name": {"type": "string", "description": "The file's name, such as ubq_300K."},
          "config": {"type": "string", "description": "The config, as YAML."},
          "by_hand": {"type": "boolean", "description": (
              "True only when the person wrote this config themselves and asked only for "
              "it to be saved: it is then recorded as theirs. Default false.")}},
         ("name", "config"),
         {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
          "openWorldHint": False}, _save_study),
    Tool("start_study", "Start a study",
         "Run a checked config on this machine, in the workspace, once the person has "
         "agreed to its plan. Needs the plan_id check_study gave for the file as it is "
         "now. Where the AI app can ask, the person is asked here too. Results go to the "
         "config's `output`, or a folder named after the file beside it, never one in "
         "use. The run goes on after the AI app closes; one study runs at a time.",
         {"config": {"type": "string", "description": "A config file in the workspace."},
          "plan_id": {"type": "string", "description": "From check_study, for this file."}},
         ("config", "plan_id"),
         {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
          "openWorldHint": True}, _start_study, acts=True),
    Tool("run_phases_again", "Run a study's analysis or report again",
         "Run a study's analysis, its report or both again in its folder, from the "
         "settings it recorded, simulating nothing, once the person agrees: to add an "
         "analysis, or to analyse its frames with this release. An analysis run again "
         "writes the report again too where the study has one. What is replaced is kept "
         "in the study's previous/ folder. A study of several runs is run again run by "
         "run, and the comparison of its runs built again. Setup and simulation are not "
         "run again in place: a new study with simulation.setup_from or "
         "simulation.resume_from does that.",
         {"study": _STUDY,
          "phases": {"type": "array", "items": {"type": "string",
                                                "enum": ["analysis", "report"]},
                     "description": "analysis, report, or both."},
          "analyses": {"type": "array", "items": {"type": "string"}, "description": (
              "The analyses to run, by name, such as rmsd, rg or pl_interactions "
              "(default: those the study ran last).")}},
         ("study", "phases"),
         {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False,
          "openWorldHint": False}, _run_phases_again, acts=True),
    Tool("stop_study", "Stop a study",
         "Stop a study running on this machine. A run in production stops at its next "
         "frame with a checkpoint there, so it can be carried on.",
         {"study": _STUDY}, ("study",),
         {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True,
          "openWorldHint": False}, _stop_study, acts=True),
    Tool("list_studies", "List the studies here",
         "The studies in the workspace, newest first, each with its system, state, the "
         "means it recorded with their errors, and the tags and note the person gave it; "
         "or only those with a tag. And the YAML files at its top.",
         {"tag": {"type": "string", "description": (
             "Only the studies with this tag, whatever its case.")}},
         (), _READS, _list_studies),
    Tool("tag_study", "Tag a study",
         "Add tags to a study, as the person asks: short words of their own such as wild "
         "type or JCIM Fig. 4, kept in the study's folder beside its records and shown on "
         "its card in the GUI. Tags are only added; the person removes one, or writes the "
         "study's note, in the GUI.",
         {"study": _STUDY,
          "tags": {"type": "array", "items": {"type": "string"}, "description": (
              "The tags to add, each up to 40 characters on one line.")}},
         ("study", "tags"),
         {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
          "openWorldHint": False}, _tag_study, acts=True),
    Tool("read_study", "Read a study",
         "Where a study stands (running, with its step and time left, or finished) and "
         "what it recorded: its config, what its analyses found with errors and units, "
         "the checks it was held to, why it stopped and what would fix it.",
         {"study": _STUDY}, ("study",), _READS, _read_study),
    Tool("methods_of_study", "The methods of a study",
         "A study's methods paragraphs as its report gives them, written from what it "
         "recorded: its preparation and protocol, how each mean and its error were "
         "determined, any rule it ran until, an AI model's part, and the software. "
         "Quote them as they are; a gap they name was not recorded.",
         {"study": _STUDY}, ("study",), _READS, _methods_of_study),
    Tool("compare_studies", "Compare two studies",
         "The settings two studies differ in, and the means each recorded, each "
         "difference marked resolved only where it is more than twice its combined "
         "standard error.",
         {"first": _STUDY, "second": _STUDY}, ("first", "second"), _READS, _compare_studies),
    Tool("views_of_study", "The views and scenes of a study",
         "The views the person saved with a study in the GUI (each a frame, a "
         "representation and colouring, a superposition), the selections they named, "
         "and the scenes written with it. A view's name starts write_scene from it.",
         {"study": _STUDY}, ("study",), _READS, _views_of_study),
    Tool("write_scene", "Write a scene of a study",
         "Write a view of a study as a scene file (MolViewSpec, .mvsx) in its scenes "
         "folder, for the person to open: the atoms at a frame, the study's secondary "
         "structure, a representation and colouring, the selections they named in the GUI, "
         "and atoms to highlight. Use it to show what an answer is about, such as the "
         "residues that move most coloured by RMSF, or the pocket at a frame. A scene of "
         "the same name is replaced.",
         {"study": _STUDY,
          "name": {"type": "string", "description": (
              "The scene's name: letters, digits, spaces, dots, dashes, underscores.")},
          "view": {"type": "string", "description": (
              "A view the person saved with the study in the GUI, to start from.")},
          "frame": {"type": "integer", "description": (
              "A frame of the trajectory as the GUI plays it, from 0.")},
          "representation": {"type": "string", "enum": [
              "cartoon", "backbone", "sticks", "ballAndStick", "lines", "surface",
              "spacefill"], "description": "How the protein is represented."},
          "colour": {"type": "string", "description": (
              "chain, spectrum, residue, element, secondary_structure, monochrome, or one "
              "of the study's per-residue results as result:<analysis>, such as "
              "result:rmsf.")},
          "superposed": {"type": "string", "enum": ["none", "backbone", "pocket"],
                         "description": "Frames fitted to a reference, on this."},
          "superposed_to": {"type": "string", "enum": ["first", "start", "deposited"],
                            "description": (
                                "What the frames are fitted to: the first frame (default), "
                                "the structure the run started from, or the deposited "
                                "structure the study was given.")},
          "smoothed_over": {"type": "integer", "enum": [1, 3, 5, 9, 15], "description": (
              "Each atom's fitted position averaged over this many frames centred on the "
              "one shown (default 1, not smoothed); only with `superposed`.")},
          "highlight": {"type": "string", "description": (
              "An MDTraj selection to show as orange sticks, such as resSeq 189 to 195.")},
          "labels": {"type": "boolean", "description": (
              "Label each highlighted residue (default false).")}},
         ("study", "name"),
         {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
          "openWorldHint": False}, _write_scene),
    Tool("make_movie", "Make a movie of a study",
         "Make a movie of a study's frames as its Viewer in the GUI makes one, into its "
         "movies folder: each frame rendered as a view the person saved shows it (or as "
         "the Viewer opens), changed as asked, with its simulated time, and encoded by "
         "ffmpeg on this computer. It takes a minute or more: a browser with no window "
         "renders every frame. A study without frames gives its structure turned once. "
         "A movie of the same name is replaced.",
         {"study": _STUDY,
          "name": {"type": "string", "description": (
              "The movie's name: letters, digits, spaces, dots, dashes, underscores "
              "(default movie).")},
          "view": {"type": "string", "description": (
              "A view the person saved with the study in the GUI, to show.")},
          "representation": {"type": "string", "enum": [
              "cartoon", "backbone", "sticks", "ballAndStick", "lines", "surface",
              "spacefill"], "description": "How the protein is represented."},
          "colour": {"type": "string", "description": (
              "chain, spectrum, residue, element, secondary_structure, monochrome, or one "
              "of the study's per-residue results as result:<analysis>.")},
          "superposed": {"type": "string", "enum": ["none", "backbone", "pocket"],
                         "description": "Frames fitted to the first frame, on this."},
          "from": {"type": "integer", "description": "The first frame, from 0 (default 0)."},
          "to": {"type": "integer", "description": (
              "The last frame (default the last); before `from`, the movie plays "
              "backwards.")},
          "every": {"type": "integer", "description": "Every Nth frame (default 1)."},
          "between": {"type": "integer", "enum": [0, 1, 3, 7], "description": (
              "Frames put in between each two, each atom moved in a straight line: "
              "smoother, not more simulation (default 0).")},
          "fps": {"type": "integer", "enum": [10, 15, 24, 25, 30, 60],
                  "description": "Frames a second (default 24)."},
          "size": {"type": "string", "enum": ["1280x720", "1920x1080", "3840x2160"],
                   "description": "Width x height in pixels (default 1920x1080)."},
          "turn": {"type": "boolean", "description": (
              "Turn the camera once about the screen's vertical over the movie.")},
          "time": {"type": "boolean", "description": (
              "Stamp each frame's simulated time (default true).")}},
         ("study",),
         {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
          "openWorldHint": False}, _make_movie, acts=True),
    Tool("ask_agent", "Ask the FastMDXplora Agent (optional; may use your API key)",
         "Optional: only when the person asks for FastMDXplora's own Agent. It writes "
         "with this AI app's model where the AI app lends it (the AI app may ask the "
         "person first); otherwise with a second AI model, the one the person chose with "
         "`fastmdx agent model`, on their own API key, each call paid for on top of this "
         "conversation. Without it, write the config yourself and give it to "
         "check_study; the validator judges it either way. The Agent writes or changes a "
         "study from a description, or answers about one; a study it writes is accepted "
         "by the validator before it is returned, saved in the workspace with its plan "
         "and plan_id, and recorded as its AI model's; the answer says which AI model "
         "wrote and whose. Nothing is run.",
         {"request": {"type": "string", "description": (
             "What the study should do or what to ask, in the person's words.")},
          "config": {"type": "string", "description": (
              "The study config being changed, if any: a file in the workspace or the "
              "YAML. The Agent returns the whole config with the change.")},
          "study": {"type": "string", "description": (
              "A study folder the request is about, if any: its record (where it stands, "
              "what it found, why it stopped) is given to the Agent.")},
          "phases": {"type": "array", "items": {"type": "string", "enum": [
              "setup", "simulation", "analysis", "report"]}, "description": (
              "The phases whose settings the Agent is told about. Default: setup and "
              "simulation; add analysis or report to have it set those.")},
          "save": {"type": "boolean", "description": (
              "Save an accepted study as a new file (default true).")}},
         ("request",),
         {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
          "openWorldHint": True}, _ask_agent),
)
