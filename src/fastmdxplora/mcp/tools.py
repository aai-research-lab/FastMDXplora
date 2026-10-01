"""The tools an assistant can call.

Each says what the software found, in the software's words, as the Agent's
own tools do; none judges chemistry or convergence of its own. A tool that
cannot do what it was asked says why, and what would fix it, as a tool
result marked as an error, so the model can put it right.

Studies, configs and structures are read and written inside the workspace
only (:mod:`fastmdxplora.mcp.workspace`).
"""

from __future__ import annotations

import copy
import hashlib
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
    """What a tool could not do, said to the model so it can put it right."""

    default_code = "assistant.tool.refused"


@dataclass
class Context:
    """What the tools reach: the workspace, the call being served, the
    person's model for the Agent, and whether studies may be run."""

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
    text = given.strip()
    return "\n" not in text and text.lower().endswith((".yml", ".yaml"))


def _config_from(ctx: Context, given: str) -> tuple[dict[str, Any], Path | None]:
    """A config named by its file, or given as YAML; and its file, if any."""
    import yaml

    file: Path | None = None
    if _is_a_path(given):
        file = ctx.workspace.inside(given)
        if file is None:
            raise ToolError(f"{given} is outside the workspace ({ctx.workspace.root}).")
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


def _accepted(ctx: Context, config: dict[str, Any]) -> Path | None:
    """Validate as a run would, raising the refusal as a tool error; the
    study continued, where the config continues one."""
    from fastmdxplora.config.loader import ConfigError, validate_config

    continuing = _continued(ctx, config)
    try:
        validate_config(copy.deepcopy(config), require_systems=continuing is None)
    except ConfigError as exc:
        raise ToolError(_refused(exc)) from None
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


def _study(ctx: Context, given: str) -> Path:
    from fastmdxplora.gui.browse import is_study

    folder = ctx.workspace.inside(given)
    if folder is None:
        raise ToolError(f"{given} is outside the workspace ({ctx.workspace.root}).")
    if not folder.is_dir() or not is_study(folder):
        raise ToolError(f"{given} is not a study folder: one holding a manifest, a "
                        "resolved config, or simulation, analysis or report. "
                        "list_studies names the studies here.")
    return folder


def _mean(side: dict[str, Any] | None) -> str:
    if not side:
        return "not recorded"
    if side.get("withheld"):
        return f"not determined ({side['withheld']})"
    mean, error, unit = side.get("mean"), side.get("error"), side.get("unit") or ""
    if mean is None:
        return "not recorded"
    said = f"{mean:.4g}"
    if error is not None:
        said += f" ± {error:.2g}"
    return f"{said} {unit}".strip()


# ---------------------------------------------------------------------------
# The Agent
# ---------------------------------------------------------------------------
#: The phases the Agent is told about unless asked for others: as the GUI
#: and `fastmdx agent` tell it, a smaller space to go wrong in.
_AGENT_PHASES = ["setup", "simulation"]

#: The Agent's instructions that are done in FastMDXplora's own window.
_IN_THE_WINDOW = ("open viewer", "open overview", "open report", "open builder",
                  "show config", "download config")


def _ask_agent(ctx: Context, args: dict[str, Any]) -> str:
    """The FastMDXplora Agent, as in the GUI: the person's model, the
    software's tools to look with, and the validator as the judge."""
    from fastmdxplora.agent import propose_config
    from fastmdxplora.agent.tools import Toolbox
    from fastmdxplora.refusals import StudyError, refusal_of

    if ctx.complete_for is None:
        raise ToolError("The Agent has no model here.")
    try:
        complete = ctx.complete_for()
    except StudyError as exc:
        raise ToolError(f"{refusal_of(exc).message}\nThe Agent writes with a model you choose "
                        "once, in a terminal: `fastmdx agent set`. Every other tool here "
                        "works without one.") from None
    current = None
    if args.get("config"):
        given = args["config"]
        if _is_a_path(given):
            _, file = _config_from(ctx, given)
            current = file.read_text(encoding="utf-8") if file is not None else None
        else:
            current = given
    status = study_record(_study(ctx, args["study"])) if args.get("study") else None

    box = Toolbox(path_for=ctx.workspace.path_for)
    told = ctx.call.progress if ctx.call is not None else (lambda message: None)
    used = box.use

    def use(name: str, asked: dict[str, Any]) -> Any:
        told(f"The Agent looks: {name}")
        return used(name, asked)

    box.use = use  # type: ignore[method-assign]

    def written(prompt: str) -> str:
        told("The Agent is writing")
        return complete(prompt)

    try:
        proposal = propose_config(args["request"], written,
                                  phases=args.get("phases") or list(_AGENT_PHASES),
                                  current_config=current, run_status=status, tools=box)
    except StudyError as exc:
        raise ToolError(refusal_of(exc).message) from None

    checked = [f"  - {look.tool}{'' if look.ok else ' (refused)'}: "
               f"{(look.said.splitlines() or [''])[0]}" for look in proposal.looks]
    after = (["", "What the Agent checked with the software:", *checked] if checked else [])
    if proposal.question:
        return "\n".join([f"The Agent asks: {proposal.question}",
                          "Answer it in a new request, with what it asks for.", *after])
    if proposal.answer:
        return "\n".join([f"The Agent says: {proposal.answer}", *after])
    if proposal.action:
        if proposal.action in _IN_THE_WINDOW:
            next_step = ("That is done in FastMDXplora's own window, which `fastmdx gui` "
                         "opens.")
        elif proposal.action == "stop":
            next_step = "stop_study stops a running study, once the person has agreed."
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
    return _proposed(ctx, args, proposal, corrected, after)


def _proposed(ctx: Context, args: dict[str, Any], proposal: Any, corrected: list[str],
              after: list[str]) -> str:
    """An accepted study: recorded as the Agent's, saved, and its plan said."""
    import yaml

    from fastmdxplora.agent import load_choice
    from fastmdxplora.naming import default_output_name, system_of

    config = dict(proposal.config)
    # Whose study this is, as `fastmdx agent` records it: a model wrote it,
    # and which one.
    config["agent"] = "assisted"
    chosen = load_choice()
    if chosen is not None:
        config["agent_model"] = f"{chosen.provider}/{chosen.model}"
    name = default_output_name(system_of(config))
    if _continued(ctx, config) is None and not config.get("output"):
        config["output"] = name
    text = yaml.safe_dump(config, sort_keys=False)
    tries = proposal.cycles
    lines = [f"The FastMDXplora Agent wrote a study, and the validator accepted it "
             f"{'first time' if tries == 1 else f'after {tries} attempts'}."]
    if corrected:
        lines += ["Refused on the way, and corrected:", *corrected]
    if args.get("save", True):
        asked = " ".join(str(args["request"]).split())[:400]
        target = _new_file(ctx.workspace.root, name)
        target.write_text(f"# Written by the FastMDXplora Agent, asked: {asked}\n" + text,
                          encoding="utf-8")
        lines += [f"Saved to {ctx.workspace.shown(target)}. Nothing has been run.", "",
                  "The plan:", *_plan_lines(config), "",
                  f"plan_id: {plan_id_of(target)} (for start_study, once the person has "
                  "agreed to this plan)"]
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
def _inspect_structure(ctx: Context, args: dict[str, Any]) -> str:
    return _looked(ctx, "inspect_structure", {"system": args["system"]})


def _check_selection(ctx: Context, args: dict[str, Any]) -> str:
    return _looked(ctx, "check_selection",
                   {"system": args["system"], "expression": args["expression"]})


def _preview_setup(ctx: Context, args: dict[str, Any]) -> str:
    config, _ = _config_from(ctx, args["config"])
    return _looked(ctx, "preview_setup", {"config": config})


def _check_study(ctx: Context, args: dict[str, Any]) -> str:
    config, file = _config_from(ctx, args["config"])
    continuing = _accepted(ctx, config)
    lines = ["Accepted by the validator. The plan:", *_plan_lines(config)]
    if continuing is not None:
        lines.append(f"It continues the study at {ctx.workspace.shown(continuing)} in place: "
                     "its next segment, joined to the others, with the analyses rerun "
                     "over the whole.")
    lacking = _cannot_run_here(config)
    if lacking:
        lines += ["", f"This machine cannot run it yet: {lacking}"]
    if file is not None:
        lines += ["", f"plan_id: {plan_id_of(file)} (for start_study; it changes if "
                      f"{ctx.workspace.shown(file)} does)"]
    else:
        lines += ["", "To run it, save it first with save_study."]
    return "\n".join(lines)


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
    target = ctx.workspace.root / f"{stem}.yml"
    try:
        with target.open("x", encoding="utf-8") as out:
            out.write(text if text.endswith("\n") else text + "\n")
    except FileExistsError:
        raise ToolError(f"{target.name} is in the workspace already, and is never written "
                        "over. Give a new name.") from None
    return (f"Saved to {ctx.workspace.shown(target)}; accepted by the validator. Nothing "
            f"has been run.\nplan_id: {plan_id_of(target)}")


# ---------------------------------------------------------------------------
# Reading studies
# ---------------------------------------------------------------------------
def _list_studies(ctx: Context, args: dict[str, Any]) -> str:
    from fastmdxplora.gui.workspace import studies_in

    found = studies_in(ctx.workspace.root)
    cards = found.get("studies") or []
    lines = [f"{len(cards)} stud{'y' if len(cards) == 1 else 'ies'} in {ctx.workspace.root}"
             + (", newest first:" if cards else ".")]
    for card in cards:
        said = [str(card.get("system") or "no system"), str(card.get("kind") or "study"),
                str(card.get("state") or "")]
        if card.get("production_ns") is not None:
            said.append(f"{card['production_ns']} ns production")
        if card.get("forcefield"):
            said.append(str(card["forcefield"]))
        if card.get("when"):
            said.append(str(card["when"])[:10])
        lines.append(f"- {ctx.workspace.shown(card['path'])}: " + ", ".join(s for s in said if s))
        for mean in card.get("means") or []:
            lines.append(f"    {mean.get('label') or mean['analysis']}: {_mean(mean)}")
    if found.get("more"):
        lines.append("(There are more; only the first are listed.)")
    configs = sorted(p.name for p in ctx.workspace.root.iterdir()
                     if p.is_file() and p.suffix.lower() in (".yml", ".yaml"))
    if configs:
        lines += ["", "Config files here: " + ", ".join(configs)]
    return "\n".join(lines)


class _Viewed:
    """A study as the Agent panel's summary reads a run: where, and its state."""

    def __init__(self, root: Path, state: str) -> None:
        self.active_root, self._state = root, state

    def snapshot(self) -> dict[str, Any]:
        return {"active_run": str(self.active_root), "status": self._state,
                "process_running": self._state == "running"}


def study_record(root: Path) -> str:
    """Everything a study recorded that a model can read: where it stands,
    its config, what its analyses found, its checks, why it stopped and
    what would fix it. The Agent's own reading of a run."""
    from fastmdxplora.gui.agent_panel import _run_status
    from fastmdxplora.gui.workspace import card_of

    state = str(card_of(root).get("state") or "unknown")
    return _run_status(_Viewed(root, state)) or f"status: {state}"


def _read_study(ctx: Context, args: dict[str, Any]) -> str:
    folder = _study(ctx, args["study"])
    return f"The study at {ctx.workspace.shown(folder)}\n" + study_record(folder)


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
            if versus:
                said += (f"; second minus first {versus['difference']:+.4g} ± "
                         f"{versus['error']:.2g} {row.get('unit') or ''}".rstrip()
                         + (", resolved" if versus.get("resolved") else
                            ", not resolved"))
            lines.append(said)
    return "\n".join(lines)


_LOOKS = {"readOnlyHint": True, "openWorldHint": True}
_READS = {"readOnlyHint": True, "openWorldHint": False}

#: In the order they are listed, which is the order to reach for them.
TOOLS: tuple[Tool, ...] = (
    Tool("ask_agent", "Ask the FastMDXplora Agent",
         "Write or change a study from a description, or ask about one. The Agent looks "
         "with the software's own tools before it answers, and a study it writes is "
         "accepted by the validator before it is returned, saved in the workspace with "
         "its plan and plan_id; it also answers questions and asks when the request is "
         "short of something only the person can say. Nothing is run. Uses the model "
         "chosen with `fastmdx agent set`.",
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
         "A file is never written over: a changed study is saved under a new name.",
         {"name": {"type": "string", "description": "The file's name, such as ubq_300K."},
          "config": {"type": "string", "description": "The config, as YAML."}},
         ("name", "config"),
         {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
          "openWorldHint": False}, _save_study),
    Tool("list_studies", "List the studies here",
         "The studies in the workspace, newest first, each with its system, state and the "
         "means it recorded with their errors; and the config files not yet run.",
         {}, (), _READS, _list_studies),
    Tool("read_study", "Read a study",
         "Where a study stands (running, with its step and time left, or finished) and "
         "what it recorded: its config, what its analyses found with errors and units, "
         "the checks it was held to, why it stopped and what would fix it.",
         {"study": _STUDY}, ("study",), _READS, _read_study),
    Tool("compare_studies", "Compare two studies",
         "The settings two studies differ in, and the means each recorded, with each "
         "difference marked resolved only where it exceeds its combined error.",
         {"first": _STUDY, "second": _STUDY}, ("first", "second"), _READS, _compare_studies),
)
