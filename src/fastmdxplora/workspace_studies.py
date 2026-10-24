"""The studies in a workspace, listed, compared and read, in one set of words.

The Agent and an AI app (`fastmdx mcp`) are both asked which studies there
are, how two of them differ, what one recorded, and whether a config would
run. Answered twice, the two answers drift: one says a mean with its error
and the other without, one leaves out a study reached through a link and
the other lists it, one gives a study's progress and the other does not,
one fills in your defaults and the other does not. So both say what is
written here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

__all__ = ["CARD_READS", "compared", "defaults_start", "folder_of_studies", "links_out",
           "listed", "mean_said", "studies_here", "study_record", "why_not", "with_error"]

#: What a study's card is read from: checked for links out before a study
#: is listed, as the whole study is before it is read. The tags and the
#: person's note are read too: a `study_tags.json` linked out of the
#: workspace had its text listed.
CARD_READS = ("resolved_config.yml", "exploration.yml", "manifest.json",
              "batch_manifest.json", "analysis", "pmf.json", "study_tags.json",
              "simulation", "report")


def folder_of_studies(folder: Any) -> Any:
    """``folder`` as a workspace the Agent lists, or None where it is not one
    (missing, the home folder, or the top of the file system)."""
    from fastmdxplora.mcp.workspace import Workspace
    from fastmdxplora.refusals import StudyError

    if not folder:
        return None
    try:
        return Workspace.at(folder)
    except StudyError:
        return None


def links_out(workspace: Any, folder: Path, *, whole: bool = True) -> Path | None:
    """A link inside a study that leads out of the workspace, if any: what
    is read through it would be read from outside. ``whole`` false looks
    only at what a listing reads, which is quick for a study of many runs."""
    if not whole:
        named = [folder / name for name in CARD_READS]
        named += list((folder / "analysis").glob("*/options.json"))
        named += list((folder / "analysis").glob("*"))
        for inside in ("simulation", "report"):
            if (folder / inside).is_dir() and not (folder / inside).is_symlink():
                named += list((folder / inside).glob("*"))
        return next((p for p in named if p.is_symlink() and workspace.inside(p) is None),
                    None)
    for here, folders, files in os.walk(folder, followlinks=False):
        for name in (*folders, *files):
            path = Path(here) / name
            if path.is_symlink() and workspace.inside(path) is None:
                return path
    return None


def studies_here(workspace: Any) -> tuple[list[dict[str, Any]], bool]:
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
                    and links_out(workspace, folder, whole=False) is None:
                cards.append(card)
    cards.sort(key=lambda card: card.get("when") or "", reverse=True)
    return cards, more


def with_error(value: float, error: float | None, *, sign: bool = False) -> str:
    """A value and its standard error as the report gives them
    (:func:`fastmdxplora.statistics.with_its_error`)."""
    from fastmdxplora.statistics import with_its_error

    return with_its_error(value, error, sign=sign)


def mean_said(side: dict[str, Any] | None) -> str:
    """One recorded mean, its error and unit, or why there is none."""
    if not side:
        return "not recorded"
    if side.get("withheld"):
        noted = f"; {side['noted']}" if side.get("noted") else ""
        return f"not determined ({side['withheld']}){noted}"
    mean, error, unit = side.get("mean"), side.get("error"), side.get("unit") or ""
    if mean is None:
        return "not recorded"
    # Its image note beside it, as every page says it (`image_note`).
    noted = f"; {side['noted']}" if side.get("noted") else ""
    return f"{with_error(mean, error)} {unit}".strip() + noted


def why_not(row: dict[str, Any]) -> str:
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


def listed(workspace: Any, tag: str | None = None) -> str:
    """The studies in the workspace, newest first, each with its system,
    kind, state, length, force field, tags, note and recorded means; then
    the YAML files at its top. ``tag`` keeps those tagged so."""
    cards, more = studies_here(workspace)
    wanted = " ".join(str(tag or "").split()).casefold()
    if wanted:
        cards = [c for c in cards if any(t.casefold() == wanted for t in c.get("tags") or [])]
    lines = [f"{len(cards)} stud{'y' if len(cards) == 1 else 'ies'} in {workspace.root}"
             + (f" tagged {tag!r}" if wanted else "")
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
        lines.append(f"- {workspace.shown(card['path'])}: " + ", ".join(s for s in said if s))
        if card.get("tags"):
            lines.append("    tagged: " + ", ".join(card["tags"]))
        if card.get("note"):
            lines.append(f"    the person's note: {card['note']}")
        for mean in card.get("means") or []:
            lines.append(f"    {mean.get('label') or mean['analysis']}: {mean_said(mean)}")
    if more:
        lines.append("(There are more; only the first are listed.)")
    try:
        configs = sorted(p.name for p in workspace.root.iterdir()
                         if p.is_file() and p.suffix.lower() in (".yml", ".yaml"))
    except OSError:
        configs = []
    if configs:
        lines += ["", "YAML files at the top of the workspace: " + ", ".join(configs)]
    return "\n".join(lines)


def compared(first: Path, second: Path,
             shown: Callable[[Path], str] = str) -> tuple[str, bool]:
    """How two studies differ: the settings each asks for, and what each
    recorded, a difference called resolved only where it is more than
    the stated multiple of its combined standard error; and whether they
    could be compared at all. Where they could not, what is said is why,
    for the asker to refuse with as its own (the Agent's, an AI app's)."""
    from fastmdxplora.gui.workspace import studies_compared

    found = studies_compared(first, second)
    if not found.get("ok"):
        return str(found.get("reason") or "They could not be compared."), False
    lines = [f"{shown(first)} against {shown(second)}"]
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
            said = (f"  {row.get('label')}: {mean_said(row.get('first'))} | "
                    f"{mean_said(row.get('second'))}")
            versus = row.get("versus")
            if versus and versus.get("error"):
                said += (f"; second minus first "
                         f"{with_error(versus['difference'], versus['error'], sign=True)} "
                         f"{row.get('unit') or ''}".rstrip()
                         + (", resolved" if versus.get("resolved") else ", not resolved"))
            else:
                said += "; the difference is not assessed: " + why_not(row)
            lines.append(said)
    return "\n".join(lines), True


class _Viewed:
    """A study as the Agent panel's summary reads a run: where, and its state."""

    def __init__(self, root: Path, state: str) -> None:
        self.active_root, self._state = root, state

    def snapshot(self) -> dict[str, Any]:
        return {"active_run": str(self.active_root), "status": self._state,
                "process_running": self._state == "running"}


#: The record's words for the Agent in the GUI, and what they mean to an AI app.
_SAID_TO_AN_AI_APP = (
    ("one marked [runs here] is what `DO: run the fix` runs, the first of them",
     "one marked [runs here] can be run on this machine"),
    ("To continue it, answer with this config.",
     "To continue it, save this config with save_study and run it."),
    ("To run it, answer with this config;",
     "To run it, save this config with save_study and run it;"),
)


def study_record(root: Path, *, for_the_agent: bool = False, first: str | None = None,
                 most_config: int | None = None) -> str:
    """Everything a study recorded that an AI model can read: where it
    stands (running, with its step and time left, or finished), its config,
    what its analyses found, its checks, why it stopped and what would fix
    it. The Agent's own reading of a run, for the Agent and an AI app
    alike; to an AI app, the Agent's words for the GUI said as they apply to
    it. ``first`` is the analysis asked about, said first among what the
    analyses found; ``most_config`` the most of the config said, for a
    reader whose look is cut at a length."""
    from fastmdxplora.gui.agent_panel import (
        _checks_summary,
        _config_the_run_used,
        _results_summary,
        _run_status,
    )
    from fastmdxplora.gui.workspace import card_of

    state = str(card_of(root).get("state") or "unknown")
    said = _run_status(_Viewed(root, state), first=first,
                       most_config=most_config) or f"status: {state}"
    if not (_config_the_run_used(root) or _results_summary(root) or _checks_summary(root)):
        said += "\n\nIt has recorded nothing yet: no config, findings or checks."
    if not for_the_agent:
        for theirs, ours in _SAID_TO_AN_AI_APP:
            said = said.replace(theirs, ours)
    return said


def defaults_start(config: dict[str, Any], *, file: Path | None = None,
                   root: Path | None = None) -> Path | None:
    """Where your defaults (fastmdx-defaults.yml) are looked for from, for
    ``config``: as the command line looks, from the study's folder up, else
    from the config file's folder, else from ``root`` (the folder of
    studies). A relative ``output`` is the folder of studies' (else the
    config file's folder, else the current folder). A nearer file
    applies to a run; read from the folder of studies alone, it was left
    out of the plan checked before the run."""
    output = config.get("output")
    if isinstance(output, str) and output.strip():
        start = Path(output.strip()).expanduser()
        if not start.is_absolute():
            start = (Path(root) if root is not None
                     else Path(file).parent if file is not None else Path.cwd()) / start
        return start
    if file is not None:
        return Path(file).parent
    return Path(root) if root is not None else None
