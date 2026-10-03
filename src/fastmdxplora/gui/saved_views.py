"""Views of the Viewer saved with the study.

A figure of a trajectory is a camera, a frame and choices of how the
molecule is shown, made by turning and clicking, and lost with the page: a
second picture of the same view for a revised figure meant making it again
by eye. A view is saved under a name in the study (``viewer_views.json``),
and showing it again sets every one of those choices and the camera as they
were. Only what the Viewer can set is kept, each value checked, so the file
holds nothing a page could not have chosen.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from pathlib import Path
from typing import Any

__all__ = ["MOST_VIEWS", "VIEWS_FILE", "delete_view", "save_view", "views_of"]

VIEWS_FILE = "viewer_views.json"
MOST_VIEWS = 50

_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,60}$")
_WORD = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
# A colouring is a word, or one of the study's results ("result:rmsf").
_COLOURING = re.compile(r"^(result:)?[A-Za-z0-9_-]{1,40}$")
_SHOWN = ("protein", "ligand", "pocket", "water", "ions", "hydrogens", "box")
_LOCK = threading.Lock()


def views_of(root: Path | str) -> dict[str, Any]:
    """The views saved with the study, in the order they were saved."""
    try:
        data = json.loads((Path(root) / VIEWS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    views = data.get("views") if isinstance(data, dict) else None
    kept = []
    for view in views if isinstance(views, list) else []:
        clean = _checked(view)
        if clean is not None and isinstance(view, dict) and _NAME.match(str(view.get("name"))):
            kept.append({"name": str(view["name"]), **clean})
    return {"ok": True, "views": kept}


def save_view(root: Path | str, name: Any, view: Any) -> dict[str, Any]:
    """A view saved under ``name``, in place of one of that name."""
    name = str(name or "").strip()
    if not _NAME.match(name):
        return {"ok": False, "reason": "A view is named in 1 to 60 characters."}
    clean = _checked(view)
    if clean is None:
        return {"ok": False, "reason": "That is not a view the Viewer can show."}
    with _LOCK:
        views = [v for v in views_of(root)["views"] if v["name"] != name]
        if len(views) >= MOST_VIEWS:
            return {"ok": False, "reason": f"A study keeps at most {MOST_VIEWS} views; "
                                           "forget one first."}
        views.append({"name": name, **clean})
        _write(Path(root), views)
    return {"ok": True, "views": views}


def delete_view(root: Path | str, name: Any) -> dict[str, Any]:
    """The view of that name forgotten."""
    with _LOCK:
        views = views_of(root)["views"]
        kept = [v for v in views if v["name"] != str(name)]
        if len(kept) == len(views):
            return {"ok": False, "reason": "There is no view of that name."}
        _write(Path(root), kept)
    return {"ok": True, "views": kept}


def _write(root: Path, views: list[dict[str, Any]]) -> None:
    target = root / VIEWS_FILE
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps({"views": views}, indent=2), encoding="utf-8")
    temporary.replace(target)


def _numbers(value: Any, count: int) -> list[float] | None:
    if not isinstance(value, list) or len(value) != count:
        return None
    try:
        numbers = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    return numbers if all(math.isfinite(v) and abs(v) < 1e7 for v in numbers) else None


def _checked(view: Any) -> dict[str, Any] | None:
    """The parts of a view the Viewer can set, each checked; None if its
    camera is not one."""
    if not isinstance(view, dict):
        return None
    camera = view.get("camera")
    if not isinstance(camera, dict):
        return None
    position, target, up = (_numbers(camera.get(key), 3) for key in ("position", "target", "up"))
    if position is None or target is None or up is None:
        return None
    kept_camera: dict[str, Any] = {"position": position, "target": target, "up": up}
    for key in ("radius", "fov", "radiusMax"):
        number = _numbers([camera.get(key)], 1) if camera.get(key) is not None else None
        if number is not None and number[0] > 0:
            kept_camera[key] = number[0]
    if camera.get("mode") in ("perspective", "orthographic"):
        kept_camera["mode"] = camera["mode"]
    clean: dict[str, Any] = {"camera": kept_camera}
    frame = view.get("frame")
    if isinstance(frame, int) and not isinstance(frame, bool) and 0 <= frame < 10_000_000:
        clean["frame"] = frame
    for key, word in (("representation", _WORD), ("colour", _COLOURING)):
        if isinstance(view.get(key), str) and word.match(view[key]):
            clean[key] = view[key]
    shown = view.get("shown")
    if isinstance(shown, dict):
        clean["shown"] = {key: bool(shown[key]) for key in _SHOWN
                          if isinstance(shown.get(key), bool)}
    if view.get("superposed") in ("none", "backbone", "pocket"):
        clean["superposed"] = view["superposed"]
    cutoff = view.get("pocket_cutoff")
    if isinstance(cutoff, (int, float)) and not isinstance(cutoff, bool) and 3 <= cutoff <= 15:
        clean["pocket_cutoff"] = float(cutoff)
    if isinstance(view.get("publication"), bool):
        clean["publication"] = view["publication"]
    if view.get("ground") in ("dark", "white"):
        clean["ground"] = view["ground"]
    return clean
