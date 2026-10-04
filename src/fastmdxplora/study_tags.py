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
"""

from __future__ import annotations

import json
import os
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["MOST_TAGS", "NOTE_LENGTH", "TAG_LENGTH", "TAGS_FILE", "add_tags", "set_tags",
           "tags_of"]

TAGS_FILE = "study_tags.json"
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


def tags_of(folder: str | Path) -> dict[str, Any]:
    """A study's tags and note, as kept; none where none were given."""
    try:
        kept = json.loads((Path(folder) / TAGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    if not isinstance(kept, dict):
        kept = {}
    tags = [t for t in (kept.get("tags") or []) if _clean(t, TAG_LENGTH)]
    note = _clean(kept.get("note") or "", NOTE_LENGTH) or ""
    return {"tags": tags[:MOST_TAGS], "note": note}


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


def _write(folder: Path, tags: list[str], note: str) -> dict[str, Any]:
    from fastmdxplora.gui.browse import is_study

    if not folder.is_dir() or not is_study(folder):
        return {"ok": False, "reason": f"{folder} is not a study."}
    record = {"tags": tags, "note": note,
              "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        handle, temporary = tempfile.mkstemp(prefix=".study_tags.", suffix=".tmp", dir=folder)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(record, out, ensure_ascii=False, indent=2)
        os.replace(temporary, folder / TAGS_FILE)
    except OSError as exc:
        return {"ok": False, "reason": f"The study's tags could not be kept in its folder: "
                                       f"{exc.strerror or exc}."}
    return {"ok": True, "tags": tags, "note": note}


def set_tags(folder: str | Path, tags: Any, note: Any = None) -> dict[str, Any]:
    """A study's tags as given, replacing those it had, and its note where
    one is given (an empty one clears it)."""
    base = Path(folder)
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
    return _write(base, checked, kept_note)


def add_tags(folder: str | Path, tags: Any) -> dict[str, Any]:
    """Tags added to those a study has, the note left as it is."""
    base = Path(folder)
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
