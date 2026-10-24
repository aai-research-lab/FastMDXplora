"""The tools an AI app can call.

Each says what the software found, in the software's words, as the Agent's
own tools do; none judges chemistry or convergence of its own. A tool that
cannot do what it was asked says why, and what would fix it, as a tool
result marked as an error, so the AI model can put it right.

Studies, configs and structures are read and written inside the workspace
only (:mod:`fastmdxplora.mcp.workspace`).

The tools are those of the one list (:mod:`fastmdxplora.offered_tools`). A
tool the Agent offers too is the Agent's own look, run here in the
workspace and said whole; the rest are this server's own, defined below.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastmdxplora.mcp.workspace import Workspace
from fastmdxplora.refusals import CodedError
# One listing and one comparison for the Agent and an AI app; the names
# read from here before they were shared are kept.
from fastmdxplora.workspace_studies import studies_here  # noqa: F401
# The record's words for the Agent, and what they mean to an AI app.
from fastmdxplora.workspace_studies import _SAID_TO_AN_AI_APP as _SAID_HERE  # noqa: F401
from fastmdxplora.workspace_studies import links_out as _links_out
from fastmdxplora.workspace_studies import why_not as _why_not  # noqa: F401
from fastmdxplora.workspace_studies import with_error  # noqa: F401

__all__ = ["Tool", "ToolError", "Context", "TOOLS", "plan_id_of"]


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


def _box(ctx: Context) -> Any:
    """The Agent's toolbox, reaching this workspace and nothing outside it,
    as this server's tools use it."""
    from fastmdxplora.agent.tools import Toolbox

    return Toolbox(path_for=ctx.workspace.path_for, workspace=ctx.workspace,
                   for_ai_app=True, runs=ctx.runs)


def _as_tool_error(call: Callable[[], Any]) -> Any:
    """``call``, a refusal from the Agent's own code said as this server's."""
    from fastmdxplora.agent.tools import ToolRefused

    try:
        return call()
    except ToolRefused as exc:
        raise ToolError(str(exc), code=exc.code) from None


def _continued(ctx: Context, config: dict[str, Any]) -> Path | None:
    """The study a config extends in place, read in the workspace."""
    from fastmdxplora.agent.tools import carried_on

    return _as_tool_error(lambda: carried_on(_box(ctx), config))


def _refused(exc: BaseException) -> str:
    """A refusal and what would fix it, as the builder says them."""
    from fastmdxplora.agent.tools import refused_said

    return refused_said(exc)


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
    # holding a line break after that is text, not a file name. `..` is read
    # in the value as given: normpath folds `link/..` away without following
    # the link, where a reader of the path follows it first.
    given = config.strip()
    text = os.path.normpath(given)
    if "\n" in text:
        return
    try:
        named = Path(text).expanduser()
        exists = (named if named.is_absolute() else ctx.workspace.root / named).exists()
    except (OSError, RuntimeError, ValueError):
        named, exists = Path(text), False
    if (exists or named.is_absolute() or text.startswith("~") or ".." in Path(given).parts) \
            and (ctx.workspace.inside(text) is None or ctx.workspace.inside(given) is None):
        raise ToolError(f"`{where}` names {text}, outside the workspace "
                        f"({ctx.workspace.root}). Copy it into the workspace and name it "
                        "there.")


def _accepted(ctx: Context, config: dict[str, Any]) -> Path | None:
    """Validate as a run would, raising the refusal as a tool error; the
    study continued, where the config continues one. Every path it names
    is held to the workspace too."""
    from fastmdxplora.agent.tools import validated

    continuing = _as_tool_error(lambda: validated(_box(ctx), config))
    # After the validator, so a setting it refuses is refused for what it is.
    _confined(ctx, config)
    return continuing


def _seen_before_a_run(ctx: Context, given: dict[str, Any], filled: dict[str, Any],
                       defaults: Any) -> None:
    """Refuse what can be seen wrong before a run, as check_study does: a
    system setup cannot read, a selection that does not parse, naming your
    defaults where they gave it. Relative paths are read from the
    workspace, where a study from here starts."""
    from fastmdxplora.config.before_run import refused_before_a_run

    caught = refused_before_a_run(given, filled, defaults, base=Path(ctx.workspace.root))
    if caught is not None:
        from fastmdxplora.agent.tools import refused_said

        raise ToolError(refused_said(caught), code=caught.code) from None


def _plan_lines(config: dict[str, Any]) -> list[str]:
    from fastmdxplora.agent.tools import plan_lines

    return plan_lines(config)


def _cannot_run_here(config: dict[str, Any]) -> str | None:
    """What this machine lacks to run the config, if anything."""
    from fastmdxplora.agent.tools import _cannot_run_here as lacking

    return lacking(config)


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

    box = Toolbox(path_for=ctx.workspace.path_for, workspace=ctx.workspace)
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
def the_agent_s_look(name: str) -> Callable[[Context, dict[str, Any]], str]:
    """A tool the Agent offers too, as this server runs it: the Agent's own
    look in the workspace, said whole, a config given as a file or YAML
    read first."""
    def run(ctx: Context, args: dict[str, Any]) -> str:
        from fastmdxplora.agent.tools import look_named

        asked = dict(args)
        if isinstance(asked.get("config"), str):
            asked["config"], _ = _config_from(ctx, asked["config"])
        return _as_tool_error(lambda: look_named(name)(_box(ctx), asked))

    run.__name__ = run.__qualname__ = f"_{name}"
    return run


def _your_defaults(ctx: Context) -> Any:
    """Your defaults for the folder of studies, read and checked, or None; a
    file that is there and wrong is said, never passed over."""
    from fastmdxplora.agent.tools import your_defaults_for

    return _as_tool_error(lambda: your_defaults_for(_box(ctx), {}))


def _filled(ctx: Context, config: dict[str, Any],
            file: Path | None = None) -> tuple[dict[str, Any], list[str], Any]:
    """The config as it will run: your defaults filling what it leaves
    unset, looked for from the study's folder up as a run looks for them,
    the settings they filled, and the defaults themselves."""
    from fastmdxplora.agent.tools import with_your_defaults

    return _as_tool_error(lambda: with_your_defaults(_box(ctx), config, file))


def _check_study(ctx: Context, args: dict[str, Any]) -> str:
    """The Agent's check (:func:`fastmdxplora.agent.tools.checked_config`),
    every path the config names held to the workspace, and what runs it."""
    from fastmdxplora.agent.tools import checked_config

    config, file = _config_from(ctx, args["config"])
    # Every path the config names held to the workspace before anything it
    # names (the defaults beside its output) is read.
    _accepted(ctx, config)
    lines, filled = _as_tool_error(lambda: checked_config(_box(ctx), config, file))
    lines += _worth_knowing(filled)
    if file is not None:
        lines += ["", _plan_id_line(ctx, file)]
    else:
        lines += ["", "To run it, save it first with save_study."]
    return "\n".join(lines)


def _worth_knowing(config: dict[str, Any]) -> list[str]:
    """What the GUI's Check it says under Worth knowing for the same config,
    by the same function: a 4 fs timestep with no constraints was warned of
    there and not here, so an AI app passed on a study that fails in NVT."""
    from fastmdxplora.gui.config_builder import check_config

    try:
        knowing = check_config(json.loads(json.dumps(config))).get("worth_knowing") or []
    except Exception:  # noqa: BLE001 - advice, never a verdict
        return []
    return ["", "Worth knowing:", *(f"  {said}" for said in knowing)] if knowing else []


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
    continuing = _accepted(ctx, config)
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
    if continuing is None:
        # What check_study refuses before a run is not saved as a study that
        # will run: a structure that is not there was saved and given a plan.
        _seen_before_a_run(ctx, before, config, _defaults)
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
                      f"{whose} Nothing has been run.", "The plan:", *_plan_lines(config),
                      *_worth_knowing(config), "", _plan_id_line(ctx, target)])


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


def study_record(root: Path, *, for_the_agent: bool = False) -> str:
    """A study's record, as the Agent and an AI app both read it
    (:func:`fastmdxplora.workspace_studies.study_record`)."""
    from fastmdxplora.workspace_studies import study_record as record

    return record(root, for_the_agent=for_the_agent)


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

    look = Toolbox(path_for=ctx.workspace.path_for, workspace=ctx.workspace).use(
        "preview_setup", {"config": config})
    if not look.ok:
        return None
    return next((line for line in look.said.splitlines() if line.startswith("time here:")),
                None)


def _may_start(ctx: Context, config: dict[str, Any] | None,
               target: Path | None = None) -> list[str]:
    """Refuses a start that must wait for the studies running in the
    workspace (`fastmdxplora.runs_here.may_start`): never where a run going
    writes (``target``, the folder it would write); work on the CPU waits
    for the other such work; a study on a GPU of this computer goes where it
    fits. Returns what the person is told of the GPUs and of the computer
    being shared."""
    from fastmdxplora.runs_here import may_start

    start = may_start([ctx.workspace.root], config, ctx.workspace.root, walk=True,
                      target=target, then="stop_study stops it, once the person agrees.")
    if start.refused is not None:
        raise ToolError(str(start.refused["error"]), code=str(start.refused["code"]))
    return list(start.notes)


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
    if args.get("machine"):
        from fastmdxplora.mcp.remote_tools import start_on_machine

        return start_on_machine(ctx, file, config, now, args["machine"])
    lacking = _cannot_run_here(config)
    if lacking:
        raise ToolError(f"This machine cannot run it yet: {lacking}")
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
    sharing = _may_start(ctx, config, where)

    shown = ctx.workspace.shown(where)
    message = "\n".join([
        f"Start the study in {ctx.workspace.shown(file)} on this machine?",
        *(_plan_lines(config)),
        *([f"It continues {shown} in place."] if continuing is not None
          else [f"Results: {shown}"]),
        *([line] if (line := _time_here(ctx, config)) else []),
        *sharing,
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
            _may_start(ctx, config, where)
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
    # As it started: the GPU is chosen again then, and may not be the one
    # the person was told of.
    shared = [str(line) for line in started.get("shared") or []]
    return (f"Started {shown} (process {pid}). It runs on its own: closing "
            "the AI app does not stop it. read_study says how far it has got; "
            "stop_study stops it. Its log is "
            f"{ctx.workspace.shown(folder / 'exploration.log')}."
            + ("\nAs it started:\n" + "\n".join(shared) if shared else ""))


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
    _may_start(ctx, None, folder)
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
            _may_start(ctx, None, folder)
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
    from fastmdxplora.gui.exploration import (_AdoptedProcess, _identify_run,
                                              _process_record_held)
    from fastmdxplora.orchestrator import record_is_from_elsewhere
    from fastmdxplora.simulation.runner import stop_grace_seconds
    from fastmdxplora.stop_after import see_it_stops

    folder = _study(ctx, args["study"])
    shown = ctx.workspace.shown(folder)
    # Held to the study: a process record linked out of it names no run of it.
    record = _process_record_held(folder)
    pid = record.get("pid")
    if (not isinstance(pid, int) or pid <= 0 or record_is_from_elsewhere(record)
            or _identify_run(pid, folder, record) is not True):
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
    if _identify_run(pid, folder, record) is not True:
        return f"{shown} has stopped already."
    _AdoptedProcess(pid, folder, record).terminate()
    # As the GUI's Stop: time to reach the next frame and checkpoint, then
    # an end that cannot be ignored. Watched from a process of its own, so
    # an AI app closed in the meantime does not take the watching with it.
    try:
        see_it_stops(pid, folder, record, stop_grace_seconds() + 10)
    except OSError as exc:
        return (f"Asked {shown} to stop. Ending it if it does not could not be arranged "
                f"({exc}): if read_study still finds it running in a minute, stop it "
                "again.")
    return (f"Asked {shown} to stop. A run in production stops at its next frame with a "
            "checkpoint there; read_study says when it has, and gives the config that "
            "continues it. One that has not stopped "
            f"{stop_grace_seconds() + 10:g} s from now is ended, whether or not this "
            "AI app is still open.")


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------
def _does(tool: Any) -> Callable[[Context, dict[str, Any]], str]:
    """The function an AI app's call to ``tool`` runs: the Agent's own look
    for one the Agent offers too, else this server's own."""
    from fastmdxplora.offered_tools import AGENT

    if AGENT in tool.offered and not tool.ai_app_does:
        return the_agent_s_look(tool.name)
    module, _, name = (tool.ai_app_does or tool.does).partition(":")
    if module == __name__:
        return globals()[name]
    from importlib import import_module

    return getattr(import_module(module), name)


def _listed() -> tuple[Tool, ...]:
    """Every tool an AI app is offered, from the one list
    (:mod:`fastmdxplora.offered_tools`), in its order."""
    from fastmdxplora.offered_tools import AI_APP, offered_by, schema_for

    return tuple(
        Tool(tool.name_for(AI_APP), tool.title, tool.told_to(AI_APP),
             schema_for(tool, AI_APP)["properties"], tool.required, dict(tool.annotations),
             _does(tool), acts=tool.acts)
        for tool in offered_by(AI_APP))


# Here, once every helper they use is defined: other machines, over
# `fastmdxplora.remote.api`.
import fastmdxplora.mcp.remote_tools  # noqa: E402,F401

TOOLS: tuple[Tool, ...] = _listed()

# The Agent's own looks under the names this server's tests and callers knew.
_list_studies = the_agent_s_look("list_studies")
_read_study = the_agent_s_look("read_study")
