"""What would fix the study on screen, for the Overview and the Agent.

`fastmdxplora.remedies` says what would fix each thing that stopped a study,
the command that does it and what it costs here. The Overview shows it, and
where the fix is a command this software runs (`fastmdx resume`, windows run
again with `--rerun-window`) it can be run from there, or by telling the
Agent, once the person has confirmed it with its price in view. Nothing
else is run: an install command, a setting to change and a choice only the
person can make are said, never done.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["study_of", "fixes_payload", "runnable", "windows_payload"]


def study_of(root: Any) -> Path | None:
    """The study to fix: a run of a campaign is fixed with its campaign,
    since resuming it alone would leave the comparison across the runs
    unbuilt."""
    if not root:
        return None
    here = Path(root)
    if here.parent.name == "runs" and (here.parent.parent / "batch_manifest.json").is_file():
        return here.parent.parent
    return here


def runnable(remedy: Any) -> bool:
    """Whether the GUI may run this fix for the person: a command of this
    software's, and not one waiting on a choice only the person can make."""
    return bool(getattr(remedy, "argv", ())) and not getattr(remedy, "decision", False)


def fixes_payload(root: Any) -> dict[str, Any]:
    """The fixes for the study, each with whether it can be run from here."""
    from fastmdxplora.remedies import remedies_of

    study = study_of(root)
    if study is None or not study.is_dir():
        return {"ok": False, "reason": "No study is open."}
    try:
        found = remedies_of(study)
    except Exception as exc:  # noqa: BLE001 - a card must never break the page
        return {"ok": False, "reason": f"What would fix it could not be read: {exc}"}
    fixes = []
    for index, remedy in enumerate(found):
        record = remedy.as_record()
        record["index"] = index
        record["runnable"] = runnable(remedy)
        record["price_said"] = remedy.price.as_text() if remedy.price else ""
        fixes.append(record)
    return {"ok": True, "study": str(study), "fixes": fixes}


def windows_payload(root: Any, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Umbrella windows the person named, as the page asks about them: what
    runs, its command and price, and the request that runs it; or why not."""
    from fastmdxplora.refusals import StudyError, refusal_of
    from fastmdxplora.remedies import windows_again

    asked = dict(arguments or {})
    study = study_of(root)
    if study is None or not study.is_dir():
        return {"ok": False, "reason": "No study is open."}
    try:
        remedy = windows_again(study, asked.get("windows"),
                               force_constant=asked.get("force_constant"),
                               duration_ns=asked.get("duration_ns"))
    except StudyError as exc:
        return {"ok": False, "reason": refusal_of(exc).message}
    record = remedy.as_record()
    record["price_said"] = remedy.price.as_text() if remedy.price else ""
    record["request"] = {key: asked.get(key)
                         for key in ("windows", "force_constant", "duration_ns")}
    return {"ok": True, "fix": record}
