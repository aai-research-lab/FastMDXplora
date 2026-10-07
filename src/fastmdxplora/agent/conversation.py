"""The Agent's loop when the AI model replies by tool calls.

The same loop as :func:`fastmdxplora.agent.propose.propose_config` runs in
the text protocol, with the same rules, and with the reply delivered as
data rather than read from a line by a pattern:

- **The validator is the only judge.** A config comes as the arguments of
  ``propose_config`` and is validated as one typed by hand. A refusal is
  the tool's result, in the same conversation, with what would fix it as
  far as the refusal registry lets that be said; a structural refusal is
  answered by a corrected call, a semantic one ends the loop. Attempts are
  counted and capped as before.
- **The tools only look.** A look is not an attempt, and at most
  :data:`~fastmdxplora.agent.tools.MOST_LOOKS` are taken per reply.
- **Code decides what is done.** ``act`` names an action and returns it to
  the caller, which carries it out through the button's door: a run the
  person's own words did not plainly ask for is confirmed, a stop always
  is. Nothing here starts, stops or writes anything.

What the tool path adds is what the text path could not hold: a reason
for each setting chosen (written into the study's ``decisions``, by who
decided it), a sentence beside a config, a question with its candidates,
and the conversation kept whole through a repair.
"""

from __future__ import annotations

import json
from typing import Any

from fastmdxplora.agent.turns import ToolCall, ToolSpec, Turn, Usage

__all__ = ["REPLY_TOOLS", "ACTS", "propose_with_tools", "reply_specs"]

#: The actions `act` may name: those the text protocol's `DO:` line takes,
#: and the three that carry arguments there.
ACTS = ("run", "stop", "run the fix", "open viewer", "open overview", "open report",
        "open builder", "show config", "download config", "rerun windows",
        "analyze again", "write the report again")

REPLY_TOOLS = ("propose_config", "ask_person", "act", "show_scene")


def reply_specs() -> list[ToolSpec]:
    """The tools a reply is made with, as they are declared to the AI model."""
    from fastmdxplora.agent.propose import _SHOWN_COLOURS, _SHOWN_REPRESENTATIONS

    representations = sorted({name for name in _SHOWN_REPRESENTATIONS
                              if name not in ("ballandstick", "spacefill")})
    return [
        ToolSpec("propose_config",
                 "Hand the person a study config. The software validates it: a refusal "
                 "comes back as this tool's result, with what would fix it where the "
                 "software can say, and you call this again with the config corrected.",
                 {"type": "object", "properties": {
                     "config": {"type": "object",
                                "description": "The study, as the config file holds it."},
                     "reasons": {
                         "type": "object",
                         "description": "For each setting you set, by its dotted name "
                                        "(`setup.ph`, `simulation.duration_ns`, `systems`): "
                                        "why it has that value.",
                         "additionalProperties": {
                             "type": "object", "properties": {
                                 "why": {"type": "string",
                                         "description": "The reason, in a sentence."},
                                 "alternatives": {"type": "array",
                                                  "description": "Values set aside."},
                                 "asked": {"type": "boolean",
                                           "description": "True where the request "
                                                          "stated the value."}},
                             "required": ["why"]}},
                     "note": {"type": "string",
                              "description": "A sentence or two to the person, beside "
                                             "the config."}},
                  "required": ["config"]}),
        ToolSpec("ask_person",
                 "Ask the person what only they can say, such as which structure, "
                 "instead of choosing it for them.",
                 {"type": "object", "properties": {
                     "question": {"type": "string"},
                     "choices": {"type": "array", "items": {"type": "string"},
                                 "maxItems": 6,
                                 "description": "The candidates you know, if any."}},
                  "required": ["question"]}),
        ToolSpec("act",
                 "Carry out what the person plainly told you to do, one action. The "
                 "software confirms a stop, and a run their message did not plainly "
                 "ask for, before anything happens.",
                 {"type": "object", "properties": {
                     "action": {"type": "string", "enum": list(ACTS)},
                     "windows": {"type": "array", "items": {"type": "integer"},
                                 "description": "For rerun windows: the windows, by number."},
                     "force_constant": {"type": "number",
                                        "description": "For rerun windows, as the person "
                                                       "gave it."},
                     "duration_ns": {"type": "number",
                                     "description": "For rerun windows, as the person "
                                                    "gave it."},
                     "analyses": {"type": "array", "items": {"type": "string"},
                                  "description": "For analyze again: every analysis to "
                                                 "run, by its name in the software."}},
                  "required": ["action"]}),
        ToolSpec("show_scene",
                 "Beside an answer about something in the open study that can be seen, "
                 "propose a scene that shows it. The person writes it with a button.",
                 {"type": "object", "properties": {
                     "frame": {"type": "integer", "minimum": 0},
                     "colour": {"type": "string",
                                "description": "One of " + ", ".join(_SHOWN_COLOURS)
                                               + ", or result:<analysis>."},
                     "representation": {"type": "string", "enum": representations},
                     "superposed": {"type": "string", "enum": ["backbone", "pocket"]},
                     "highlight": {"type": "string",
                                   "description": "An MDTraj selection."},
                     "labels": {"type": "boolean"},
                     "name": {"type": "string", "description": "A few words."}}}),
    ]


def system_prompt(*, phases: list[str] | None, verbose: bool, tools: Any) -> str:
    """What every message of every conversation starts with: the same text,
    so the provider reads it from its cache after the first."""
    from fastmdxplora.agent.propose import _instructions, _stopping_instructions
    from fastmdxplora.config.describe import describe_schema

    parts = [_instructions("tools"), "\n", _stopping_instructions()]
    if tools is not None:
        parts += [tools.guidance(), "\n"]
    parts.append(describe_schema(phases=phases, verbose=verbose))
    return "".join(parts)


def _history(history: list[dict[str, str]] | None) -> list[dict[str, Any]]:
    """The conversation so far as turns: the person's and the Agent's."""
    turns: list[dict[str, Any]] = []
    for said in (history or [])[-12:]:
        text = str(said.get("text") or "").strip()
        if not text:
            continue
        if said.get("role") == "user":
            turns.append({"role": "user", "text": text})
        else:
            turns.append({"role": "assistant", "text": text, "calls": ()})
    return turns


def _reasons_into(config: dict[str, Any], reasons: Any,
                  request: str = "") -> tuple[dict[str, Any], list[str]]:
    """The config with each reason written into its ``decisions``, and the
    names no reason could be written for.

    Only a setting's dotted name takes a reason (``setup.ph``, ``systems``):
    a reason about anything else is set aside and said, never allowed to
    turn a valid config into a refused one. A decision the config already
    holds as the person's is kept as it is. The source is the person only
    where the request states the value; the AI model's own word that it
    was asked for is not enough, since the record is read as who decided.
    A reason given only as a sentence is taken as its ``why``.
    """
    from fastmdxplora.config.loader import settings_named

    if not isinstance(reasons, dict) or not reasons:
        return config, []
    known = settings_named()
    decisions = dict(config.get("decisions") or {}) if isinstance(
        config.get("decisions"), dict) else {}
    dropped: list[str] = []
    for name, reason in reasons.items():
        name = str(name)
        if name not in known:
            dropped.append(name)
            continue
        held = decisions.get(name)
        if isinstance(held, dict) and held.get("source") == "person":
            continue
        if isinstance(reason, str):
            reason = {"why": reason}
        if not isinstance(reason, dict):
            continue
        why = " ".join(str(reason.get("why") or "").split())
        if not why:
            continue
        stated = reason.get("asked") is True and _stated_in(request, _value_at(config, name))
        entry: dict[str, Any] = {"why": why, "source": "person" if stated else "agent"}
        alternatives = reason.get("alternatives")
        if isinstance(alternatives, list) and alternatives:
            entry["alternatives"] = alternatives
        decisions[name] = entry
    if decisions:
        config = dict(config, decisions=decisions)
    return config, dropped


def _value_at(config: dict[str, Any], name: str) -> Any:
    """The value a dotted name points at in the config, or None."""
    held: Any = config
    for part in name.split("."):
        if not isinstance(held, dict) or part not in held:
            return None
        held = held[part]
    return held


def _stated_in(request: str, value: Any) -> bool:
    """Whether the request states this value: a number as a number in it, a
    word as a word in it, and for a list or a mapping (``systems``) any one
    of the words or numbers it holds."""
    import re

    said = " ".join(str(request or "").lower().split())
    if not said or value is None or isinstance(value, bool):
        return False
    if isinstance(value, dict):
        return any(_stated_in(request, v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_stated_in(request, v) for v in value)
    if isinstance(value, (int, float)):
        numbers = re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?:e[+-]?\d+)?", said)
        return any(float(n) == float(value) for n in numbers)
    word = str(value).strip().lower()
    return bool(word) and re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", said) is not None


def _config_of(arguments: dict[str, Any]) -> dict[str, Any] | None:
    """The config a call carries: a mapping, or YAML text an AI model sent
    as a string."""
    config = arguments.get("config")
    if isinstance(config, str):
        import yaml

        try:
            config = yaml.safe_load(config)
        except yaml.YAMLError:
            return None
    return config if isinstance(config, dict) and config else None


def _act_of(arguments: dict[str, Any]) -> tuple[str, dict[str, Any] | None] | str:
    """The action and its arguments as the text protocol's line gives them,
    or why the call is not one."""
    action = " ".join(str(arguments.get("action") or "").lower().split())
    if action not in ACTS:
        return (f"There is no action {action!r}. The actions are: "
                + ", ".join(ACTS) + ".")
    if action == "rerun windows":
        windows = arguments.get("windows")
        if (not isinstance(windows, list) or not windows
                or not all(isinstance(w, int) and not isinstance(w, bool) for w in windows)):
            return "Name the windows to run again, by number, as `windows`."
        def number(key: str) -> float | None:
            value = arguments.get(key)
            return float(value) if isinstance(value, (int, float)) and not isinstance(
                value, bool) else None
        return action, {"windows": sorted(set(windows)),
                        "force_constant": number("force_constant"),
                        "duration_ns": number("duration_ns")}
    if action == "analyze again":
        named = arguments.get("analyses")
        names = ([str(n).strip().lower() for n in named if str(n).strip()]
                 if isinstance(named, list) else [])
        return action, {"analyses": list(dict.fromkeys(names)) or None}
    if action == "write the report again":
        return action, {}
    return action, None


#: A setting's name as a config writes it at its top: one word, or dotted.
_A_NAME = r"[A-Za-z_][\w.\-]*"


def _a_written_config(text: str) -> dict[str, Any] | None:
    """A study config the reply wrote out as text, or None.

    Read as the text protocol reads one, and taken where it names what it
    simulates (``systems``) or where the reply is the YAML and little else:
    a misspelled block (``simulaton:``) is then validated and refused by
    name, not passed on as an answer nobody checked. An answer that shows a
    snippet of YAML among its sentences ("set it like this: ...") stays an
    answer.
    """
    import re

    from fastmdxplora.agent.propose import _parse, _yaml_like

    config = _parse(text)
    if not config or not all(isinstance(key, str) and re.fullmatch(_A_NAME, key)
                             for key in config):
        return None
    if "systems" in config:
        return config
    lines = [line for line in text.splitlines()
             if line.strip() and not line.strip().startswith("```")]
    written = sum(1 for line in lines if _yaml_like(line))
    return config if lines and written >= 0.8 * len(lines) else None


def _text_reply(text: str, scene_call: dict[str, Any] | None,
                plain: bool = False) -> dict[str, Any] | None:
    """What a reply in text alone is, where it says so as the text protocol
    does: an action on a line of its own (``DO: stop``, as the page's own
    record of an action reads, and so as an AI model may write one), a
    question (``ASK:``), or an answer (``SAY:``) with its scene. None for
    text with none of these, unless ``plain``, when it is the answer."""
    from fastmdxplora.agent.propose import (
        _action_in, _again_in, _answer_in, _question_in, _scene_from, _scene_in, _windows_in)

    act = _action_in(text)
    if act:
        return {"action": act}
    again = _windows_in(text)
    if again is not None:
        return {"action": "rerun windows", "arguments": again}
    asked_again = _again_in(text)
    if asked_again is not None:
        return {"action": asked_again[0], "arguments": asked_again[1]}
    asked = _question_in(text)
    if asked and scene_call is None:
        return {"question": asked}
    said = _answer_in(text) or (text if plain else None)
    if said is None:
        return None
    said, scene = _scene_in(said)
    if scene_call is not None:
        scene = _scene_from(scene_call)
    return {"answer": said, "scene": scene}


def propose_with_tools(request: str, turn: Any, *, phases: list[str] | None,
                       max_cycles: int, verbose_schema: bool,
                       history: list[dict[str, str]] | None, current_config: str | None,
                       run_status: str | None, attachments: list[dict[str, Any]] | None,
                       tools: Any) -> Any:
    """Ask by tool calls until a reply is made, or the attempts run out.

    ``turn`` is the completion's ``turn``: a system prompt, the conversation
    and the tools in, one :class:`~fastmdxplora.agent.turns.Turn` out. Raises
    :class:`~fastmdxplora.agent.turns.NoToolCalling` from the first turn if
    the server turns tools away, for the caller to ask in text instead; a
    server that took tools on the first turn and refuses them later has
    failed, and that is said.

    Every call the AI model makes is answered in the next turn, a look by
    what it found and any other by why it was not taken, as every provider
    requires; anything more said to it comes after.
    """
    import yaml

    from fastmdxplora.agent.propose import Attempt, Proposal, _this_message
    from fastmdxplora.agent.tools import MOST_LOOKS
    from fastmdxplora.agent.turns import NoToolCalling
    from fastmdxplora.config.loader import ConfigError, validate_config
    from fastmdxplora.refusals import Kind, Refusal, StudyError, refusal_of
    from fastmdxplora.remedies import remedy_for

    system = system_prompt(phases=phases, verbose=verbose_schema, tools=tools)
    specs = (tools.specs() if tools is not None else []) + reply_specs()
    looks_named = {spec.name for spec in specs} - set(REPLY_TOOLS)
    messages: list[dict[str, Any]] = _history(history) + [{
        "role": "user", "text": _this_message(request, current_config=current_config,
                                              run_status=run_status,
                                              attachments=attachments)}]
    usage = Usage()
    attempts: list[Attempt] = []
    refusal: Refusal | None = None
    looked = 0
    turns_taken = 0

    def looks() -> tuple[Any, ...]:
        return tuple(tools.looks) if tools is not None else ()

    def done(**said: Any) -> Any:
        return Proposal(attempts=tuple(attempts), looks=looks(), usage=usage.as_record(),
                        protocol="tools", **said)

    def missed(why: str, raw: str) -> None:
        nonlocal refusal
        refusal = Refusal(code="config.file.unparseable", message=why,
                          details={"attempt": len(attempts) + 1})
        attempts.append(Attempt(len(attempts) + 1, raw, None, refusal))

    def refused_by(exc: ConfigError, raw: str, config: dict[str, Any]) -> str | None:
        """The refusal recorded; what would fix it, or None where the
        refusal is semantic and ends the loop."""
        nonlocal refusal
        refusal = refusal_of(exc)
        attempts.append(Attempt(len(attempts) + 1, raw, config, refusal))
        if refusal.kind != Kind.STRUCTURAL:
            # Not answerable by rewriting the config: the software declined
            # to decide, and so does the loop.
            return None
        fix = remedy_for(refusal, where="the config")
        return f"Refused ({refusal.code}): {refusal.message}\nWhat would fix it: {fix.fix}"

    for _ in range(max_cycles + MOST_LOOKS + 2):
        if len(attempts) >= max_cycles:
            break
        try:
            reply: Turn = turn(system, messages, specs)
        except NoToolCalling as exc:
            if not turns_taken:
                raise
            raise StudyError(
                "The AI model took tool calls and then refused them in the same reply: "
                f"{exc}", code="environment.service.unusable_response") from None
        turns_taken += 1
        usage.add(reply.usage)
        messages.append({"role": "assistant", "text": reply.text, "calls": reply.calls})
        text = reply.text.strip()
        calls = list(reply.calls)
        results: list[dict[str, Any]] = []
        then: str | None = None

        def answer(call: ToolCall, content: str, is_error: bool = False,
                   results: list[dict[str, Any]] = results) -> None:
            results.append({"id": call.id, "name": call.name, "content": content,
                            "is_error": is_error})

        # Looks first: what they say is given back with the reply's other
        # results, and a look is not an attempt.
        looked_now = False
        for call in calls:
            if call.name in looks_named:
                looked_now = True
                if looked >= MOST_LOOKS:
                    answer(call, "The looks for this reply are used up; answer from what "
                                 "the software said.", True)
                    continue
                looked += 1
                look = tools.use(call.name, call.arguments)
                answer(call, look.said, not look.ok)
            elif call.name not in REPLY_TOOLS:
                # Counted as an attempt, so a reply that keeps calling what
                # is not there cannot go round for ever.
                looked_now = True
                missed(f"There is no tool called {call.name!r}.", json.dumps(call.arguments))
                answer(call, f"There is no tool called {call.name!r}.", True)

        proposals = [c for c in calls if c.name == "propose_config"]
        asks = [c for c in calls if c.name == "ask_person"]
        acts = [c for c in calls if c.name == "act"]
        scenes = [c for c in calls if c.name == "show_scene"]

        if proposals:
            first = proposals[0]
            for extra in proposals[1:]:
                answer(extra, "One config per reply; the first was taken.", True)
            config = _config_of(first.arguments)
            raw = (yaml.safe_dump(first.arguments.get("config"), sort_keys=False)
                   if isinstance(first.arguments.get("config"), dict)
                   else str(first.arguments.get("config") or ""))
            if config is None:
                missed("The config was not a mapping.", raw)
                answer(first, "Refused: `config` is the study as a mapping, as the "
                              "file holds it.", True)
            else:
                config, dropped = _reasons_into(config, first.arguments.get("reasons"),
                                                request)
                try:
                    # As in the text protocol: a proposed study is one
                    # somebody means to run, so it names what it simulates.
                    validate_config(config, require_systems=True)
                except ConfigError as exc:
                    said = refused_by(exc, raw, config)
                    if said is None:
                        return done(config=None, refusal=refusal)
                    if dropped:
                        said += ("\nReasons are kept by a setting's dotted name; none was "
                                 "kept for: " + ", ".join(dropped) + ".")
                    answer(first, said, True)
                else:
                    attempts.append(Attempt(len(attempts) + 1, raw, config, None))
                    note = str(first.arguments.get("note") or "").strip() or text or None
                    return done(config=config, note=note)
            for other in asks + acts + scenes:
                answer(other, "Not taken: the config comes first.", True)
        elif asks:
            question = str(asks[0].arguments.get("question") or "").strip()
            choices = asks[0].arguments.get("choices")
            if question:
                return done(config=None, question=question, choices=tuple(
                    str(c) for c in choices[:6] if str(c).strip())
                    if isinstance(choices, list) else ())
            missed("The question was empty.", json.dumps(asks[0].arguments))
            answer(asks[0], "Say the question in `question`.", True)
        elif acts:
            if len(acts) > 1:
                missed("Two actions in one reply.", json.dumps([a.arguments for a in acts]))
                for call in acts:
                    answer(call, "One action at a time: none was taken.", True)
            else:
                read = _act_of(acts[0].arguments)
                if isinstance(read, str):
                    missed(read, json.dumps(acts[0].arguments))
                    answer(acts[0], read, True)
                else:
                    return done(config=None, action=read[0], arguments=read[1])
        elif looked_now:
            # Text beside a look is the AI model saying what it is about to
            # do, not its reply: the reply comes once it has read the look.
            pass
        elif text:
            scene_call = scenes[0].arguments if scenes else None
            marked = _text_reply(text, scene_call)
            if marked is not None:
                return done(config=None, **marked)
            written = _a_written_config(text)
            if written is not None:
                # A config written as text, by an AI model that does not use
                # the tool for it: validated all the same, and never taken
                # as an answer that nothing checked.
                try:
                    validate_config(written, require_systems=True)
                except ConfigError as exc:
                    said = refused_by(exc, text, written)
                    if said is None:
                        return done(config=None, refusal=refusal)
                    then = f"That config was {said[0].lower()}{said[1:]}\nCall " \
                           "`propose_config` with it corrected."
                else:
                    attempts.append(Attempt(len(attempts) + 1, text, written, None))
                    return done(config=written)
            else:
                return done(config=None, **(_text_reply(text, scene_call, plain=True) or {}))
        elif scenes:
            missed("A scene without an answer.", json.dumps(scenes[0].arguments))
            for call in scenes:
                answer(call, "Say the answer in text, with the scene beside it.", True)
        else:
            missed("The reply was empty.", "")
            then = ("Reply to the person: answer in plain text, or call one of the "
                    "reply tools.")

        # Every call answered in the turn straight after it, whatever else
        # is said, as every provider requires.
        answered = {r["id"] for r in results}
        for call in calls:
            if call.id not in answered:
                answer(call, "Not taken.", True)
        if results:
            messages.append({"role": "results", "results": results})
        if then:
            messages.append({"role": "user", "text": then})

    return done(config=None, refusal=refusal or Refusal(
        code="config.file.unparseable", message="No reply was made.",
        details={"attempt": len(attempts)}))
