"""What the Agent is told about where the person is and what was said.

Three parts of a message, each given only where there is something in it,
and each pointing at a record rather than standing in for one:

- **What is named with @** (``@rmsd``, ``@study``, ``@frame 120``,
  ``@Trp6``, ``@docs``, ``@file analysis/rg/options.json``, a study's
  folder by its name): each resolved here, by the software, to what it
  names and the look that reads it. Nothing is pasted in: an analysis is
  named with its record, never with its numbers, and a name that matches
  nothing is said to match nothing, so it is asked about rather than
  guessed.
- **The turns before the last few**, kept as what was decided and what is
  still open, each line with the turn it came from. They were kept by
  their first sentence, so "Thanks, that helps. Use CHARMM36m." lost the
  force field twenty turns later. The person's own words are kept whole
  (they are short and they decide); a config the Agent wrote is kept as the
  settings it set or changed; a question as asked, and as still open where
  nothing after it answered it.
- **The plan** the Agent keeps across turns, as the person's checklist
  shows it, so the twentieth turn still knows which step comes next.

None of it is a fact the AI model may quote. It says where to look; the
looks say what is so.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

__all__ = ["MOST_MENTIONS", "MOST_PLAN_STEPS", "earlier_kept", "mentions_in", "named_in",
           "plan_of", "plan_said"]

#: Names with @ resolved in one message; more are left as typed.
MOST_MENTIONS = 8
#: Steps a plan keeps, and the words a step keeps.
MOST_PLAN_STEPS = 12
MOST_STEP_TEXT = 200
#: The person's words kept whole up to this; longer, their start.
MOST_PERSON_TEXT = 600
#: Lines of a config the Agent wrote, kept as its settings.
MOST_SETTINGS_SAID = 16

# -- what is named with @ --------------------------------------------------------

#: `@word` where `@` starts a word (not an address: a@b.org), `@frame 12`,
#: `@file some/path`.
_MENTION = re.compile(r"(?<![\w@.])@(frame\s+\d{1,7}|file\s+[^\s`]+|[A-Za-z0-9][\w.:+\-]{0,79})",
                      re.ASCII)
_TRAILING = ".,;:!?)]}\"'"
_AMINO = {"ala", "arg", "asn", "asp", "cys", "gln", "glu", "gly", "his", "ile", "leu",
          "lys", "met", "phe", "pro", "ser", "thr", "trp", "tyr", "val", "hid", "hie",
          "hip", "hsd", "hse", "hsp", "ash", "glh", "lyn", "cyx", "sec", "pyl"}
_RESIDUE = re.compile(r"^(?P<name>[A-Za-z]{3})(?P<number>-?\d{1,6})(?P<icode>[A-Za-z]?)$")
_CHAIN_RESIDUE = re.compile(r"^(?P<chain>[A-Za-z0-9]{1,4}):(?P<number>-?\d{1,6})$")


def mentions_in(text: str) -> list[str]:
    """What the message names with @, in order, each once, as typed
    without the @ (``rmsd``, ``frame 120``, ``file analysis/rg/options.json``)."""
    found: list[str] = []
    for match in _MENTION.finditer(str(text or "")):
        said = " ".join(match.group(1).split()).rstrip(_TRAILING)
        if said and said not in found:
            found.append(said)
        if len(found) >= MOST_MENTIONS:
            break
    return found


def _analyses_of(study: Path | None) -> list[str]:
    if study is None:
        return []
    try:
        return sorted(p.name for p in (study / "analysis").iterdir()
                      if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []


def _studies_here(tools: Any) -> list[Path]:
    """The studies in the workspace, listed once for a message."""
    workspace = getattr(tools, "workspace", None)
    if workspace is None:
        return []
    from fastmdxplora.workspace_studies import studies_here

    try:
        cards, _ = studies_here(workspace)
    except Exception:  # noqa: BLE001 - a listing that fails names nothing
        return []
    return [Path(str(card["path"])) for card in cards if card.get("path")]


def _studies_named(studies: list[Path], name: str) -> list[Path]:
    """The studies whose folder is called ``name``."""
    wanted = name.casefold()
    return [path for path in studies if path.name.casefold() == wanted]


def _shown(tools: Any, path: Path | str) -> str:
    workspace = getattr(tools, "workspace", None)
    if workspace is not None:
        try:
            return workspace.shown(path)
        except Exception:  # noqa: BLE001 - shown whole instead
            pass
    return str(path)


def _one(said: str, tools: Any, study: Path | None, listed: dict[str, list[Path]]) -> str:
    """What one name with @ names, and how it is read."""
    lowered = said.casefold()
    where = f"the study open ({_shown(tools, study)})" if study is not None else None
    if lowered in ("study", "this", "here"):
        if study is None:
            return "no study is open, so it names none; ask which study they mean."
        return f"{where}. Read it with read_study (study: {study})."
    if lowered == "docs":
        return ("the person asks for the software's docs: answer from read_docs, and "
                "name the page.")
    if lowered.startswith("frame "):
        frame = said.split()[1]
        return (f"frame {frame} of the open study's trajectory as the Viewer plays it, "
                "counted from 0" + ("" if study is not None else "; no study is open") + ".")
    if lowered.startswith("file "):
        named = said.split(None, 1)[1]
        if study is None:
            return f"the file {named}, but no study is open to read it in; ask which study."
        return (f"the file {named} in {where}. Read it with read_study_file "
                f"(study: {study}, file: {named}).")
    chain = _CHAIN_RESIDUE.match(said)
    residue = _RESIDUE.match(said)
    if chain or (residue and residue.group("name").casefold() in _AMINO):
        if chain:
            number = chain.group("number")
            what = f"residue {number} of chain {chain.group('chain')}"
            selection = f"resSeq {number}"
            note = (" MDTraj numbers chains from 0, so pick the chain from what "
                    "inspect_structure lists.")
        else:
            name = residue.group("name").upper()
            number = residue.group("number")
            what = f"residue {name} {number}" + (f" (insertion code {residue.group('icode')})"
                                                  if residue.group("icode") else "")
            selection = f"resname {name} and resSeq {number}"
            note = ""
        if number.startswith("-"):
            return (f"{what}. Use inspect_structure to identify the structure and chain. "
                    "Find the residue's exact zero-based topology index on that chain, "
                    "using the Viewer's Selection tab if available, and verify the "
                    "selection with check_selection against the same topology.")
        return (f"{what}; as an MDTraj selection, `{selection}`. Check it with "
                "check_selection on the study's structure before you rely on it." + note)
    analyses = _analyses_of(study)
    match = next((a for a in analyses if a.casefold() == lowered), None)
    if match is not None:
        return (f"the {match} analysis of {where}; its record is "
                f"analysis/{match}/options.json. Read it with read_study (study: {study}, "
                f"analysis: {match}), which gives it first, and quote its mean as recorded, "
                "with its error and unit.")
    if "studies" not in listed:
        listed["studies"] = _studies_here(tools)
    studies = _studies_named(listed["studies"], said)
    if len(studies) == 1:
        return (f"the study {_shown(tools, studies[0])} in this workspace. Read it with "
                f"read_study (study: {studies[0]}).")
    if len(studies) > 1:
        return ("more than one study in this workspace is called so ("
                + ", ".join(_shown(tools, s) for s in studies[:4])
                + "); ask the person which.")
    ran = (f" The open study's analyses are: {', '.join(analyses)}." if analyses else
           " No study is open, so it names no analysis." if study is None else "")
    return ("names nothing the software found: no analysis of the open study, no study in "
            "this workspace, no frame, residue or file. Where the message makes plain what "
            "it means (a PDB identifier, a setting's value), go on; otherwise ask the person "
            "what they meant rather than guess." + ran)


def named_in(request: str, tools: Any = None, study: Any = None) -> str:
    """The message's names with @, each resolved, as the message carries
    them; empty where it names none. ``study`` is the study open, where one
    is (the page's active study)."""
    mentions = mentions_in(request)
    if not mentions:
        return ""
    folder = Path(str(study)).expanduser() if study else None
    if folder is not None and not folder.is_dir():
        folder = None
    lines = ["## Named with @ in this message",
             "Each says where to look, not what is so: read the record with the look "
             "named before you answer from it."]
    listed: dict[str, list[Path]] = {}
    for said in mentions:
        lines.append(f"- @{said}: {_one(said, tools, folder, listed)}")
    return "\n".join(lines) + "\n"


# -- the turns before the last few ------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
_CANDIDATES = re.compile(r"\n\s*Candidates:\s*(?P<choices>.+?)\.?\s*$", re.S)
_WROTE = "Wrote a config:"
_STARTED = re.compile(r"^Started version \d+\.?$")


def _first_sentence(text: str, most: int = 200) -> str:
    text = " ".join(str(text or "").split())
    first = _SENTENCE_END.split(text, maxsplit=1)[0]
    return first if len(first) <= most else first[:most - 3].rstrip() + "..."


def _person_said(text: str) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= MOST_PERSON_TEXT:
        return text
    return text[:MOST_PERSON_TEXT - 3].rstrip() + "... (cut)"


def _flat(config: Any, prefix: str = "", depth: int = 0,
          out: dict[str, Any] | None = None) -> dict[str, Any]:
    """A config's settings by dotted name; ``systems`` by what each names.
    At most :data:`MOST_SETTINGS_READ`, :data:`MOST_DEPTH` blocks deep."""
    out = {} if out is None else out
    if not isinstance(config, dict) or depth > MOST_DEPTH:
        return out
    for key, value in config.items():
        if len(out) >= MOST_SETTINGS_READ:
            break
        name = f"{prefix}{key}"
        if not prefix and key in ("agent", "agent_model", "decisions"):
            continue
        if not prefix and key == "systems" and isinstance(value, list):
            out[name] = ", ".join(
                str(s.get("system") if isinstance(s, dict) else s) for s in value[:20])
        elif isinstance(value, dict):
            _flat(value, name + ".", depth + 1, out)
        else:
            out[name] = value
    return out


def _value(value: Any) -> str:
    said = " ".join(str(value).split())
    return said if len(said) <= 80 else said[:77] + "..."


#: The most of a config's text read back from the conversation, which the
#: browser sends: a config is a few kilobytes.
MOST_CONFIG_TEXT = 20_000
#: Settings said of one config, and how deep its blocks are read.
MOST_SETTINGS_READ = 400
MOST_DEPTH = 8


def _no_aliases() -> Any:
    """A YAML loader that follows no alias: a few hundred bytes of nested
    aliases unfold into billions of entries. One met is noted
    (``met_an_alias``), read as nothing, and the config is not read back."""
    import yaml

    class NoAliases(yaml.SafeLoader):
        met_an_alias = False

        def compose_node(self, parent: Any, index: Any) -> Any:
            if self.check_event(yaml.events.AliasEvent):
                self.get_event()
                self.met_an_alias = True
                return yaml.ScalarNode("tag:yaml.org,2002:null", "")
            return super().compose_node(parent, index)

    return NoAliases


def _config_in(text: str) -> tuple[str, dict[str, Any] | None]:
    """The note before a config the Agent wrote, and the config; None for a
    config too long, or written with aliases, to be read back here."""
    import yaml

    note, _, written = str(text).partition(_WROTE)
    if len(written) > MOST_CONFIG_TEXT:
        return note.strip(), None
    loader = _no_aliases()(written)
    try:
        config = loader.get_single_data()
    except (yaml.YAMLError, RecursionError):
        # Not YAML, or nested past what the parser can follow: not a config
        # to read back.
        config = None
    finally:
        loader.dispose()
    if loader.met_an_alias:
        config = None
    return note.strip(), config if isinstance(config, dict) else None


def _a_question(text: str) -> tuple[str, list[str]] | None:
    """The question an Agent's turn asked, and its candidates, or None."""
    text = str(text or "").strip()
    choices: list[str] = []
    found = _CANDIDATES.search(text)
    if found:
        choices = [c.strip() for c in found.group("choices").split(";") if c.strip()]
        text = text[:found.start()].strip()
    if not (choices or text.endswith("?")):
        return None
    return " ".join(text.split()), choices


def _answers(question: tuple[str, list[str]], reply: str) -> bool:
    """Whether a message of the person's answers the question: it names a
    candidate, or, for the next message, it is not a question of its own."""
    _, choices = question
    said = reply.casefold()
    if any(choice.casefold() in said for choice in choices):
        return True
    return not reply.strip().endswith("?")


def _still_open(question: tuple[str, list[str]], later: list[dict[str, str]]) -> bool:
    """Whether a question is still open: the person's next message asked
    something else, no later message of theirs names one of its candidates,
    and no config was written after it (which settles what it asked)."""
    replies = [str(t.get("text") or "") for t in later if t.get("role") == "user"]
    if not replies or _answers(question, replies[0]):
        return False
    _, choices = question
    if choices and any(any(c.casefold() in r.casefold() for c in choices) for r in replies):
        return False
    return not any(t.get("role") != "user" and _WROTE in str(t.get("text") or "")
                   for t in later)


def earlier_kept(history: list[dict[str, str]] | None, *, whole: int = 12,
                 most: int = 48) -> str:
    """The turns before the last ``whole``, kept as what was decided and
    what is still open, each with the turn it came from; or nothing where
    there are none. Turns are numbered from the first one kept."""
    turns = list(history or [])
    if len(turns) <= whole:
        return ""
    start = max(0, len(turns) - whole - most)
    decided: list[str] = []
    open_: list[str] = []
    version = 0
    previous: dict[str, Any] = {}
    last_config: tuple[int, int] | None = None
    last_run = run_before = run_named = -1
    for index, turn in enumerate(turns):
        number = index + 1
        text = str(turn.get("text") or "").strip()
        mine = turn.get("role") != "user"
        shown = index < len(turns) - whole and index >= start
        if not mine:
            if shown and text:
                decided.append(f"- Turn {number}, the person: {_person_said(text)}")
            continue
        if not text or text.startswith("(stopped before answering)"):
            continue
        if text.startswith("DO:"):
            action = " ".join(text[3:].split())
            if action == "run":
                # Named, and confirmed by the person where it had to be: a
                # "Not run." straight after it says it was not.
                last_run, run_before = index, last_run
                run_named = index
            if shown:
                decided.append(f"- Turn {number}, the action named: {action}.")
            continue
        if _STARTED.match(text):
            # The page's Run button, pressed: that version started.
            last_run, run_before = index, last_run
            if shown:
                decided.append(f"- Turn {number}, the page: {text}")
            continue
        if text == "Not run.":
            # The run named just before, declined: only where nothing but
            # the person's answer came between (a fix declined is not it).
            if run_named >= 0 and all(t.get("role") == "user"
                                      for t in turns[run_named + 1:index]):
                last_run = run_before
            run_named = -1
            if shown:
                decided.append(f"- Turn {number}, the page: Not run.")
            continue
        if _WROTE in text:
            note, config = _config_in(text)
            if config is None:
                continue
            version += 1
            last_config = (version, number)
            flat = _flat(config)
            if shown:
                if not previous:
                    settings = [f"{k} {_value(v)}" for k, v in flat.items()]
                    head = f"- Turn {number}, the Agent wrote config {version}: "
                else:
                    settings = [f"{k} {_value(previous[k]) if k in previous else 'unset'} "
                                f"to {_value(flat[k]) if k in flat else 'unset'}"
                                for k in sorted(set(flat) | set(previous))
                                if previous.get(k) != flat.get(k)]
                    head = f"- Turn {number}, the Agent wrote config {version}, changing "
                kept = settings[:MOST_SETTINGS_SAID]
                more = len(settings) - len(kept)
                body = "; ".join(kept) or "nothing"
                decided.append(head + body + (f"; and {more} more" if more > 0 else "") + ".")
            previous = flat
            continue
        question = _a_question(text)
        if question is not None:
            asked = question[0] + (f" Candidates: {'; '.join(question[1])}."
                                   if question[1] else "")
            if index >= start and _still_open(question, turns[index + 1:]):
                open_.append(f"- Turn {number}, the Agent asked: {asked} The person's next "
                             "message asked something else, so it may still be open.")
            elif shown:
                decided.append(f"- Turn {number}, the Agent asked: {asked}")
            continue
        if shown:
            decided.append(f"- Turn {number}, the Agent answered: {_first_sentence(text)}")
    if last_config is not None and last_run < last_config[1] - 1:
        open_.append(f"- Config {last_config[0]} (turn {last_config[1]}) is written "
                     "and not run.")
    lines = ["## Earlier in this conversation",
             f"The turns before the last {whole}, kept as what was decided and what is still "
             "open; the turns, and the configs written, are counted from the first turn "
             "kept, so a config's number here is not its version on the page. The "
             "person's words are theirs, whole unless cut; where something you need was "
             "cut or is not here, ask them rather than guess.",
             "", "### Decided"] + (decided or ["- Nothing yet."])
    if open_:
        lines += ["", "### Still open"] + open_
    return "\n".join(lines) + "\n"


# -- the plan -----------------------------------------------------------------------

def plan_of(value: Any) -> tuple[dict[str, Any], ...] | None:
    """A plan as kept: at most :data:`MOST_PLAN_STEPS` steps, each its
    words and whether it is done; None where there is none."""
    if isinstance(value, dict):
        value = value.get("steps")
    if not isinstance(value, (list, tuple)):
        return None
    steps: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            item = {"step": item}
        if not isinstance(item, dict):
            continue
        words = " ".join(str(item.get("step") or "").split())
        if not words:
            continue
        if len(words) > MOST_STEP_TEXT:
            words = words[:MOST_STEP_TEXT - 3].rstrip() + "..."
        steps.append({"step": words, "done": item.get("done") is True})
        if len(steps) >= MOST_PLAN_STEPS:
            break
    return tuple(steps) or None


def plan_said(plan: Any) -> str:
    """The plan so far as the message carries it; empty where there is none."""
    steps = plan_of(plan)
    if not steps:
        return ""
    lines = ["## Your plan so far",
             "Kept from your earlier replies; the person sees it as a checklist. Tick a "
             "step only once the conversation shows it done; change the steps only where "
             "the person changed what they want."]
    for number, step in enumerate(steps, start=1):
        lines.append(f"{number}. [{'x' if step['done'] else ' '}] {step['step']}")
    return "\n".join(lines) + "\n"
