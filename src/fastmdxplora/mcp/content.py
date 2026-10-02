"""What an assistant can read, and what the person can ask it to do.

Resources are for reading: two guides (how to work with studies here, and
the config language) and each study in the workspace, as its record.
Prompts are for the person to choose, as a slash command in most clients:
each starts a piece of work the way FastMDXplora would do it, with the
validator as the judge of what the assistant writes and nothing run without
the person's word.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from fastmdxplora.mcp.protocol import INVALID_PARAMS, ProtocolError
from fastmdxplora.mcp.workspace import Workspace

__all__ = ["PROMPTS", "prompt_messages", "resources", "TEMPLATES", "read_resource"]

_GUIDE = "fastmdxplora://guide/"
_STUDY = "fastmdxplora://study/"

#: A guide changes only with the software; a study's record as it runs.
GUIDE_TTL_MS = 3_600_000
STUDY_TTL_MS = 5_000

_WORKING = """\
# Working with FastMDXplora studies

FastMDXplora runs molecular dynamics studies end to end: setup, simulation,
analysis and a report, each step checked, with every setting and decision
recorded beside the results.

## The order of work

1. **Write the study as a config.** The config language
   (`fastmdxplora://guide/config-language`) lists every setting, what it
   does and its default; a key not listed there is refused. Look before
   choosing: `inspect_structure` for chains, ligands and residue states,
   `check_selection` for a selection, `preview_setup` for a size or a time.
   Ask the person what only they can say (the system, what the study is
   for, its conditions where they matter); do not choose those for them.
2. **Have the validator judge it.** `check_study` with the YAML. A refusal
   names its code, its reason and what would fix it: fix that and check
   again. Once it is accepted, `save_study` writes it as a new file,
   recorded as written with an assistant.
3. **Show the plan.** `check_study` on the file gives the plan the person
   should read, defaults marked, whether this machine can run it, and its
   `plan_id`.
4. **Run it only on the person's word.** `start_study` with that `plan_id`;
   a changed file needs checking again. A server started read-only offers
   no `start_study`: the person runs the file with
   `fastmdx explore --config FILE` or from the GUI.
5. **Read what it found.** `read_study` while it runs (step, time left,
   health) and after (what each analysis found, the checks, why it stopped,
   and the config that continues it).

`ask_agent` is optional. It is FastMDXplora's own Agent, a second model
the person chose and pays for on their own API key; use it only when the
person asks for it. What it writes is judged by the same validator.

## What the software's words mean

- A mean is given with its standard error and unit, the mean to the
  decimal place of the error's second significant figure; `read_study` also
  gives the number of independent samples behind it. Quote them as given.
- "Not determined" means the software withheld a mean and says why (too
  few independent samples, or a correlation time it could not resolve,
  for example). Say so, with its reason; do not estimate one.
- In `compare_studies`, a difference is "resolved" only where it exceeds
  twice its combined standard error. A difference that is not resolved is
  not evidence of a change.
- A refusal names its code, its reason and what would fix it. Fix the cause;
  do not work around a refusal.
- An estimate from `preview_setup` is replaced by setup's own numbers once
  setup has run.

## What not to do

- Do not save, run or call ready a config the validator has not accepted,
  and do not work around a refusal.
- Do not start, stop or continue a study the person has not agreed to.
- Do not state a system's size, a run's time, or a residue's state from
  memory: look with `preview_setup` or `inspect_structure`.
"""


def _guide_config() -> str:
    from fastmdxplora.config.describe import describe_schema

    return ("# The FastMDXplora config language\n\nEvery setting a study config can "
            "carry, with what it does and its default. A key not listed here is refused.\n\n"
            + describe_schema(verbose=True))


_GUIDES = {
    "working-with-studies": ("Working with FastMDXplora studies",
                             "The order of work, what the software's words mean, and what "
                             "not to do. Read this first.", lambda: _WORKING),
    "config-language": ("The FastMDXplora config language",
                        "Every setting a study config can carry, with what it does.",
                        _guide_config),
}


def resources(workspace: Workspace) -> list[dict[str, Any]]:
    """The guides, then every study in the workspace, newest first."""
    from fastmdxplora.mcp.tools import studies_here

    listed = [{"uri": _GUIDE + key, "name": key, "title": title, "description": what,
               "mimeType": "text/markdown"} for key, (title, what, _) in _GUIDES.items()]
    for card in studies_here(workspace)[0]:
        where = workspace.shown(card["path"])
        listed.append({"uri": _STUDY + quote(where, safe="/"), "name": where,
                       "title": f"{card.get('system') or 'Study'}: {where}",
                       "description": f"{card.get('kind') or 'study'}, {card.get('state')}",
                       "mimeType": "text/plain"})
    return listed


TEMPLATES = [{"uriTemplate": _STUDY + "{+path}", "name": "study",
              "title": "A study in the workspace",
              "description": "A study's record, by its folder in the workspace.",
              "mimeType": "text/plain"}]


def _a_study(workspace: Workspace, given: str) -> Path | None:
    """A study folder in the workspace, held to the tools' rules, or None."""
    from fastmdxplora.mcp.tools import Context, ToolError, _study

    try:
        return _study(Context(workspace), given)
    except ToolError:
        return None


#: Not found, as each era says it: 2025-11-25 and before used its own code.
NOT_FOUND = {"modern": INVALID_PARAMS, "legacy": -32002}


def read_resource(workspace: Workspace, uri: Any, era: str = "modern") -> dict[str, Any]:
    """The contents of a guide or a study's record; a URI naming neither
    is refused as the call's era says, with the URI."""
    from fastmdxplora.mcp.tools import study_record

    text = str(uri or "")
    if text.startswith(_GUIDE) and text[len(_GUIDE):] in _GUIDES:
        _, _, make = _GUIDES[text[len(_GUIDE):]]
        return {"contents": [{"uri": text, "mimeType": "text/markdown", "text": make()}],
                "ttlMs": GUIDE_TTL_MS, "cacheScope": "public"}
    if text.startswith(_STUDY):
        folder = _a_study(workspace, unquote(text[len(_STUDY):]))
        if folder is not None:
            return {"contents": [{"uri": text, "mimeType": "text/plain",
                                  "text": f"The study at {workspace.shown(folder)}\n"
                                          + study_record(folder)}]}
    raise ProtocolError(NOT_FOUND.get(era, INVALID_PARAMS), "Resource not found", {"uri": text})


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Prompt:
    name: str
    title: str
    description: str
    arguments: tuple[tuple[str, str, bool], ...]
    said: str
    #: The argument naming a study whose record goes with the prompt.
    study: str | None = None

    def listed(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "description": self.description,
                "arguments": [{"name": n, "description": d, "required": r}
                              for n, d, r in self.arguments]}


PROMPTS: tuple[Prompt, ...] = (
    Prompt("design_a_study", "Design a study",
           "Write a study from what you want to learn, judged by FastMDXplora's "
           "validator, and see its plan before anything runs.",
           (("goal", "What the study is for, in your words.", True),
            ("structure", "A PDB identifier or a structure file, if you have one.", False)),
           "I want a molecular dynamics study with FastMDXplora. {goal}{structure}\n\n"
           "Write it as a FastMDXplora config, from the config language guide, looking "
           "with inspect_structure and preview_setup where a choice depends on the "
           "structure or the size. Give it to check_study until the validator accepts it, "
           "and ask me anything only I can say. Then save it with save_study, show me the "
           "plan from check_study on the file and what you looked at, and do not start "
           "anything until I say so."),
    Prompt("explain_a_study", "Explain what a study found",
           "What a study found, from its own record, each number with its error and unit.",
           (("study", "The study's folder in the workspace.", True),),
           "Explain what the FastMDXplora study {study} found, from its record below. Give "
           "each number with its error and unit as recorded and the analysis it comes "
           "from; say which checks it passed and which it did not, and what was recorded "
           "as not determined. Make no judgement the record does not make.",
           study="study"),
    Prompt("why_did_it_stop", "Why did a study stop?",
           "Why a study stopped and what would fix it, from its own record.",
           (("study", "The study's folder in the workspace.", True),),
           "From the record of the FastMDXplora study {study} below, say why it stopped and "
           "what would fix it: name the fix, give its command or config exactly as written, "
           "and its cost where recorded. Ask me before starting anything.",
           study="study"),
    Prompt("continue_a_study", "Continue a study",
           "Extend a study in place by more production, joined and analysed as one.",
           (("study", "The study's folder in the workspace.", True),
            ("more", "How much more production, such as 50 ns.", True)),
           "Continue the FastMDXplora study {study} by {more} of production, as one study "
           "extended in place. Its record below gives the config that does it: use that "
           "config with `extra_ns` set to {more} in nanoseconds in place of `duration_ns`. "
           "If the record says it cannot be continued, tell me why instead. Check it with "
           "check_study, save it with save_study, show me its plan, and start it only when "
           "I say so.",
           study="study"),
)


def prompt_messages(workspace: Workspace, name: Any, arguments: Any) -> dict[str, Any]:
    """A prompt with its arguments filled in, and the study's record with it."""
    from fastmdxplora.mcp.tools import study_record

    prompt = next((p for p in PROMPTS if p.name == name), None)
    if prompt is None:
        raise ProtocolError(INVALID_PARAMS, f"Unknown prompt: {name}")
    given = arguments if isinstance(arguments, dict) else {}
    values = {}
    for argument, _, required in prompt.arguments:
        value = str(given.get(argument) or "").strip()
        if required and not value:
            raise ProtocolError(INVALID_PARAMS, f"{prompt.name} needs `{argument}`.")
        values[argument] = value
    if "structure" in values:
        values["structure"] = (f"\nThe structure: {values['structure']}."
                               if values["structure"] else "")
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": {"type": "text", "text": prompt.said.format(**values)}}]
    if prompt.study is not None:
        folder = _a_study(workspace, values[prompt.study])
        if folder is None:
            raise ProtocolError(INVALID_PARAMS,
                                f"{values[prompt.study]} is not a study in the workspace.")
        where = workspace.shown(folder)
        messages.append({"role": "user", "content": {"type": "resource", "resource": {
            "uri": _STUDY + quote(where, safe="/"), "mimeType": "text/plain",
            "text": f"The study at {where}\n" + study_record(folder)}}})
    return {"description": prompt.description, "messages": messages}
