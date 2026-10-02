"""Content identities for research views; never an instruction to load a path."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

_CACHE: OrderedDict[tuple, str] = OrderedDict()
_CACHE_LOCK = threading.RLock()


def _digest(root: Path, path: Path | None) -> str | None:
    if path is None or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        return None
    before = path.stat()
    key = (str(path.resolve()), before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    with _CACHE_LOCK:
        found = _CACHE.get(key)
        if found:
            _CACHE.move_to_end(key)
            return found
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("Research source changed during verification. Save the view again.")
    with _CACHE_LOCK:
        _CACHE[key] = digest
        while len(_CACHE) > 64:
            _CACHE.popitem(last=False)
    return digest


def _record(root: Path, relative: str) -> dict:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size > 2_000_000:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def source_for(root: Path, view: dict, *, verify_view: bool = True) -> dict:
    """Hash the exact saved artifacts relevant to a view, in bounded memory.

    File names are selected by the application, never an imported reference.
    Hashing may read a large trajectory once; unchanged files use a bounded cache.
    """
    from fastmdxplora.gui.protein_preview import find_structure, find_system
    from fastmdxplora.gui.selection import topology_the_analyses_read

    evidence: dict[str, Any] = {}
    def add(name: str, path: Path | None) -> None:
        digest = _digest(root, path)
        if digest:
            evidence[name] = digest
    analysis = view.get("analysis")
    figure = view.get("figure")
    if isinstance(figure, str) and re.fullmatch(r"analysis/[a-z][a-z0-9_]{0,63}/[A-Za-z0-9_.-]{1,128}\.(?:png|svg|jpg|jpeg)", figure):
        add("selected_figure", root / figure)
    if isinstance(analysis, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", analysis):
        folder = root / "analysis" / analysis
        add("analysis_data", folder / (analysis + ".dat"))
        add("analysis_options", folder / "options.json")
        add("analysis_figure", folder / (analysis + ".png"))
        # Only scientific sampling metadata, not absolute input paths.
        manifest = _record(root, "analysis/analysis_manifest.json")
        sampling = manifest.get("load_kwargs")
        if isinstance(sampling, dict):
            evidence["sampling"] = hashlib.sha256(json.dumps({k: sampling.get(k) for k in
                ("first", "last", "stride", "saving_interval_ps")}, sort_keys=True).encode()).hexdigest()
    if view.get("page") == "viewer" or view.get("selection"):
        add("display_topology", find_system(root) or find_structure(root))
        add("analysis_topology", topology_the_analyses_read(root))
        if view.get("frame") is not None:
            record = _record(root, "simulation/playback_index.json")
            if not record or view["frame"] >= len(record.get("frame_indices") or []):
                return {"version": 1, "evidence": {}}
            if verify_view and view.get("playback_signature") and view["playback_signature"] != record.get("source_signature"):
                raise ValueError("The trajectory changed since this view was displayed. Select the frame again.")
            add("playback_coordinates", root / "simulation/playback.pdb")
            joined = _record(root, "joined/joined.json")
            if joined and (root / "joined/production.dcd").is_file():
                add("trajectory", root / "joined/production.dcd")
                named = joined.get("topology")
                if isinstance(named, str) and named:
                    add("trajectory_topology", Path(named))
            elif record.get("source_kind") == "live-history":
                add("live_history", root / "simulation/live_frame_history.json")
            else:
                add("trajectory", root / "simulation/production.dcd")
                add("trajectory_topology", root / "simulation/trajectory_topology.pdb")
            if record:
                evidence["frame_mapping"] = hashlib.sha256(json.dumps({k: record.get(k) for k in
                    ("source_kind", "frame_indices", "frame_times_ns", "n_frames_total", "n_frames_browser")},
                    sort_keys=True).encode()).hexdigest()
        elif view.get("mode") == "live":
            record = _record(root, "simulation/live_frame_index.json")
            if record.get("live_frame_index") != view.get("live_step"):
                raise ValueError("The displayed live frame changed. Save its screenshot or select a trajectory frame.")
            add("live_coordinates", root / "simulation/live_frame.pdb")
    if view.get("field") or view.get("page") == "run":
        add("setup_settings", root / "setup/setup_parameters.json")
        add("resolved_settings", root / "resolved_config.yml")
        add("resolved_settings_yaml", root / "resolved_config.yaml")
    if view.get("audit_event"):
        add("preparation_audit", root / "setup/preparation_audit.json")
    if not evidence:
        add("study_manifest", root / "manifest.json")
    return {"version": 1, "evidence": evidence}


def clean_source(value: Any) -> dict:
    if not isinstance(value, dict) or value.get("version") != 1:
        return {}
    evidence = value.get("evidence")
    if not isinstance(evidence, dict) or len(evidence) > 24:
        raise ValueError("Invalid bookmark source identity.")
    if any(not isinstance(key, str) or not re.fullmatch(r"[a-z_]{1,40}", key)
           or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
           for key, digest in evidence.items()):
        raise ValueError("Invalid bookmark source fingerprint.")
    return {"version": 1, "evidence": evidence}


def compatible(root: Path, row: dict) -> tuple[bool, str]:
    saved = clean_source(row.get("source"))
    if not saved or not saved.get("evidence"):
        return False, "Source identity was not recorded. The note and screenshot remain available."
    current = source_for(root, row.get("view") or {}, verify_view=False)
    if current != saved:
        return False, "The bookmarked data or molecular source differs from this study. The note and screenshot remain available."
    return True, "The saved source matches this study."
