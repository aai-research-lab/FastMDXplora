"""Tags and a note a person gives a study, kept in the study's folder.

A study's records say what it is and what it found; nothing said what it
means to the person: "wild type", "JCIM Fig. 4", "redo with Ca²⁺". Tags are
free words, each up to 40 characters, at most 20 a study, the same tag
written once whatever its case; the note is one line of up to 200. Both are
kept in ``study_tags.json`` beside the study's records, never inside them,
so they travel with the study when it is moved, copied or shared, and no
record the software reads is changed. The GUI's All studies page sets them
on a card; an AI app through `fastmdx mcp` reads them and may add tags, and
removing a tag or writing the note stays with the person.

A study the person archived is kept there too (``archived``, when): All
studies leaves it out but in its Archived view. Nothing of the study is
moved or deleted, so its records and the paths in them stay as they were.
"""

from __future__ import annotations

import json
import os
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["MOST_TAGS", "NOTE_LENGTH", "TAG_LENGTH", "TAGS_FILE", "add_tags", "change_tags",
           "set_tags", "tags_of"]

TAGS_FILE = "study_tags.json"
#: Why a run of a study of several is not archived alone: All studies
#: lists its runs under the study, and leaves out the study it archives.
A_RUN_IS_ARCHIVED_WITH_ITS_STUDY = ("A run of a study is archived with its study: "
                                    "archive the study.")
TAG_LENGTH = 40
MOST_TAGS = 20
NOTE_LENGTH = 200


def _clean(text: Any, most: int) -> str | None:
    """One line of words: spaces collapsed, no control characters; None
    where it is not text or is too long."""
    if not isinstance(text, str):
        return None
    kept = "".join(" " if unicodedata.category(c).startswith("C") else c for c in text)
    kept = " ".join(kept.split())
    return kept if len(kept) <= most else None


def _kept(folder: str | Path) -> dict[str, Any]:
    # Through `record_access`: held to the study where the read is (All
    # studies' cards), a file linked out of it is not read.
    from fastmdxplora import record_access

    try:
        kept = json.loads(record_access.read_text(Path(folder) / TAGS_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    return kept if isinstance(kept, dict) else {}


def _not_changed_here(folder: Path) -> str | None:
    """Why a study's tags are not changed, where its ``study_tags.json`` is
    a link its held read refuses (out of the study, or to a hidden file of
    it): a change is made to the tags as kept, so they would be read from
    there, and copied into the study. None where they can be changed."""
    from fastmdxplora import record_access
    from fastmdxplora.refusals import OutsideWorkspace

    try:
        record_access.read_text(folder / TAGS_FILE, encoding="utf-8")
    except OutsideWorkspace:
        # Said as the study's row says it (`series.refused_said`).
        from fastmdxplora.gui.series import refused_said

        return f"Its tags were not changed: {refused_said(folder, folder / TAGS_FILE)}."
    except (OSError, ValueError):
        pass
    return None


def _archived_at(kept: dict[str, Any]) -> str | None:
    when = kept.get("archived")
    return when if isinstance(when, str) and when else None


def tags_of(folder: str | Path) -> dict[str, Any]:
    """A study's tags and note, as kept, none where none were given; and
    ``archived`` True where the person archived it."""
    kept = _kept(folder)
    tags = [t for t in (kept.get("tags") or []) if _clean(t, TAG_LENGTH)]
    note = _clean(kept.get("note") or "", NOTE_LENGTH) or ""
    said: dict[str, Any] = {"tags": tags[:MOST_TAGS], "note": note}
    if _archived_at(kept) is not None:
        said["archived"] = True
    return said


def _checked_tags(given: Any) -> tuple[list[str] | None, str | None]:
    if not isinstance(given, list):
        return None, "Tags are given as a list of words."
    tags: list[str] = []
    seen: set[str] = set()
    for tag in given:
        cleaned = _clean(tag, TAG_LENGTH)
        if cleaned is None:
            return None, f"A tag is up to {TAG_LENGTH} characters of text on one line."
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            tags.append(cleaned)
    if len(tags) > MOST_TAGS:
        return None, f"A study has {MOST_TAGS} tags at most."
    return tags, None


def _write(folder: Path, tags: list[str], note: str,
           archived: bool | None = None) -> dict[str, Any]:
    """Kept, the study archived as it was unless ``archived`` says."""
    from fastmdxplora.gui.browse import is_study

    if not folder.is_dir() or not is_study(folder):
        return {"ok": False, "reason": f"{folder} is not a study."}
    if archived is True and folder.parent.name == "runs" and is_study(folder.parent.parent):
        return {"ok": False, "reason": A_RUN_IS_ARCHIVED_WITH_ITS_STUDY}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record: dict[str, Any] = {"tags": tags, "note": note, "updated": now}
    when = _archived_at(_kept(folder))
    if archived is not None:
        when = (when or now) if archived else None
    if when:
        record["archived"] = when
    try:
        handle, temporary = tempfile.mkstemp(prefix=".study_tags.", suffix=".tmp", dir=folder)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(record, out, ensure_ascii=False, indent=2)
        os.replace(temporary, folder / TAGS_FILE)
    except OSError as exc:
        return {"ok": False, "reason": f"The study's tags could not be saved in its folder: "
                                       f"{exc.strerror or exc}."}
    said: dict[str, Any] = {"ok": True, "tags": tags, "note": note}
    if when:
        said["archived"] = True
    return said


def set_tags(folder: str | Path, tags: Any, note: Any = None,
             archived: Any = None) -> dict[str, Any]:
    """A study's tags as given, replacing those it had, its note where one
    is given (an empty one clears it), and archived or not where that is
    given (True or False; anything else leaves it as it was)."""
    base = Path(folder)
    refused = _not_changed_here(base)
    if refused:
        return {"ok": False, "reason": refused}
    checked, reason = _checked_tags(tags)
    if checked is None:
        return {"ok": False, "reason": reason}
    if note is None:
        kept_note = tags_of(base)["note"]
    else:
        kept_note = _clean(note, NOTE_LENGTH)
        if kept_note is None:
            return {"ok": False, "reason": f"A note is one line of up to {NOTE_LENGTH} "
                                           "characters."}
    return _write(base, checked, kept_note, archived if isinstance(archived, bool) else None)


def change_tags(folder: str | Path, add: Any = None, remove: Any = None, note: Any = None,
                archived: Any = None) -> dict[str, Any]:
    """A change to a study's tags, applied to the tags as kept now: those
    in ``remove`` taken off (whatever their case), those in ``add`` put on;
    its note where one is given; archived or not where that is given (True
    or False). A tag another writer kept since the page read the study (an
    AI app through `fastmdx mcp`, another window) stays."""
    base = Path(folder)
    refused = _not_changed_here(base)
    if refused:
        return {"ok": False, "reason": refused}
    adding, reason = _checked_tags([] if add is None else add)
    if adding is None:
        return {"ok": False, "reason": reason}
    taking, reason = _checked_tags([] if remove is None else remove)
    if taking is None:
        return {"ok": False, "reason": reason}
    now = tags_of(base)
    off = {t.casefold() for t in taking}
    merged, why = _checked_tags([t for t in now["tags"] if t.casefold() not in off] + adding)
    if merged is None:
        return {"ok": False, "reason": why}
    if note is None:
        kept_note = now["note"]
    else:
        kept_note = _clean(note, NOTE_LENGTH)
        if kept_note is None:
            return {"ok": False, "reason": f"A note is one line of up to {NOTE_LENGTH} "
                                           "characters."}
    return _write(base, merged, kept_note, archived if isinstance(archived, bool) else None)


def add_tags(folder: str | Path, tags: Any) -> dict[str, Any]:
    """Tags added to those a study has, the note left as it is."""
    base = Path(folder)
    refused = _not_changed_here(base)
    if refused:
        return {"ok": False, "reason": refused}
    checked, reason = _checked_tags(tags)
    if checked is None:
        return {"ok": False, "reason": reason}
    now = tags_of(base)
    merged, why = _checked_tags(now["tags"] + checked)
    if merged is None:
        return {"ok": False, "reason": why}
    said = _write(base, merged, now["note"])
    if said.get("ok"):
        known = {t.casefold() for t in now["tags"]}
        said["added"] = [t for t in checked if t.casefold() not in known]
    return said
