"""Portable bookmark bundles with bounded validation and a two-step import."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmdxplora.gui.research import _LOCK, bookmarks_endpoint, clean_tags, clean_view, write_rows
from fastmdxplora.gui.research_images import MAX_PNG_BYTES, normalize_image, read_image, store_image
from fastmdxplora.gui.research_sources import clean_source, compatible

MAX_BUNDLE_BYTES = 32_000_000
MAX_METADATA_BYTES = MAX_BUNDLE_BYTES
_FORMAT = "fastmdxplora-research-bookmarks"


def _portable(row: dict) -> dict:
    view = clean_view(row.get("view", {}))
    view.pop("study", None)
    source = clean_source(row.get("source"))
    # Explicit fields exclude stored paths, model conversations and credentials.
    result = {key: row.get(key) for key in ("id", "title", "note", "created_at", "updated_at")}
    result.update(version=2, view=view, tags=clean_tags(row.get("tags", [])), source=source)
    if row.get("screenshot"):
        image_id = row["screenshot"].get("id")
        if not isinstance(image_id, str) or not re.fullmatch(r"[a-f0-9]{64}", image_id):
            raise ValueError("A stored screenshot reference is malformed.")
        result["screenshot"] = {key: row["screenshot"].get(key) for key in ("id", "width", "height")}
    return result


def export_bundle(runtime: Any, *, images: bool = True, bookmark_ids: list[str] | None = None) -> bytes:
    with _LOCK:
        answer = bookmarks_endpoint(runtime)
        if not answer.get("ok"):
            raise ValueError(answer.get("error", "No study is loaded."))
        root = Path(answer["study"])
        stored = answer["bookmarks"]
        if bookmark_ids is not None:
            if (not 1 <= len(bookmark_ids) <= 500 or any(not re.fullmatch(r"[a-f0-9]{32}", bid) for bid in bookmark_ids)
                    or not set(bookmark_ids).issubset({row["id"] for row in stored})):
                raise ValueError("The export selection changed or is invalid. Reload bookmarks and select a filter again.")
            stored = [row for row in stored if row["id"] in set(bookmark_ids)]
        rows = [_portable(row) for row in stored]
        assets: dict[str, bytes] = {}
        for row in rows:
            reference = row.get("screenshot")
            if reference and images:
                encoded = read_image(root, reference)
                assets["screenshots/" + reference["id"] + ".png"] = base64.b64decode(encoded.partition(",")[2])
            elif reference:
                row.pop("screenshot")
        metadata = json.dumps({"format": _FORMAT, "version": 2, "bookmarks": rows},
                              ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
        if len(metadata) > MAX_METADATA_BYTES or len(metadata) + sum(map(len, assets.values())) > MAX_BUNDLE_BYTES:
            raise ValueError("This export exceeds 32 MB. Export JSON or filter the bookmarks to export a smaller set.")
        if not images:
            return metadata
        output = io.BytesIO()
        # Already-compressed PNGs need no second compression; this also avoids
        # creating high compression-ratio archives that the importer refuses.
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("bookmarks.json", metadata)
            for name, pixels in assets.items():
                archive.writestr(name, pixels)
        raw = output.getvalue()
        if len(raw) > MAX_BUNDLE_BYTES:
            raise ValueError("This bundle exceeds 32 MB. Export JSON instead.")
        return raw


def _unpack(raw: bytes) -> tuple[dict, dict[str, str]]:
    if not raw or len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError("Import a JSON or ZIP bookmark file up to 32 MB.")
    assets = {}
    if raw.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                infos = archive.infolist()
                if len(infos) > 501 or len({info.filename for info in infos}) != len(infos):
                    raise ValueError("The bundle has too many or duplicate files.")
                total = 0
                for info in infos:
                    name = info.filename
                    if name != "bookmarks.json" and not re.fullmatch(r"screenshots/[a-f0-9]{64}\.png", name):
                        raise ValueError("The bundle contains an unsupported file or path.")
                    limit = MAX_METADATA_BYTES if name == "bookmarks.json" else MAX_PNG_BYTES
                    if info.flag_bits & 1 or info.file_size > limit or info.compress_size == 0 and info.file_size:
                        raise ValueError("An archive member is encrypted or exceeds the size limit.")
                    if info.file_size > max(1, info.compress_size) * 200:
                        raise ValueError("The bundle has an unsafe compression ratio.")
                    total += info.file_size
                    if total > MAX_BUNDLE_BYTES:
                        raise ValueError("The expanded bundle exceeds 32 MB.")
                metadata = archive.read("bookmarks.json")
                for info in infos:
                    if info.filename != "bookmarks.json":
                        pixels = archive.read(info.filename)
                        image_id = info.filename.removeprefix("screenshots/").removesuffix(".png")
                        if hashlib.sha256(pixels).hexdigest() != image_id:
                            raise ValueError("A screenshot does not match its recorded identity.")
                        encoded = "data:image/png;base64," + base64.b64encode(pixels).decode("ascii")
                        # Validate pixels before preview, without writing anything.
                        normalize_image(encoded)
                        assets[image_id] = encoded
        except (zipfile.BadZipFile, KeyError, RuntimeError) as exc:
            raise ValueError("The bookmark ZIP is damaged or incomplete.") from exc
    else:
        metadata = raw
    if len(metadata) > MAX_METADATA_BYTES:
        raise ValueError("Bookmark metadata exceeds 32 MB.")
    value = json.loads(metadata.decode("utf-8"))
    if not isinstance(value, dict) or value.get("version") not in {1, 2}:
        raise ValueError("Unsupported bookmark file version.")
    if value.get("format", _FORMAT) != _FORMAT:
        raise ValueError("This is not a FastMDXplora bookmark file.")
    return value, assets


def _import_row(row: Any, assets: dict[str, str]) -> dict:
    if not isinstance(row, dict):
        raise ValueError("Each imported bookmark must be an object.")
    title, note = row.get("title"), row.get("note", "")
    if not isinstance(title, str) or not title.strip() or len(title) > 160 or not isinstance(note, str) or len(note) > 8000:
        raise ValueError("An imported bookmark has an invalid title or note.")
    bid = row.get("id")
    if not isinstance(bid, str) or not re.fullmatch(r"[a-f0-9]{32}", bid):
        bid = uuid.uuid4().hex
    view = clean_view(row.get("view", {}))
    view.pop("study", None)
    now = datetime.now(timezone.utc).isoformat()
    result = {"id": bid, "title": title.strip(), "note": note, "version": 2,
              "view": view, "tags": clean_tags(row.get("tags", [])),
              "source": clean_source(row.get("source")),
              "created_at": row.get("created_at") if isinstance(row.get("created_at"), str) and len(row["created_at"]) <= 64 else now,
              "updated_at": now}
    screenshot = row.get("screenshot")
    if screenshot:
        if not isinstance(screenshot, dict) or screenshot.get("id") not in assets:
            raise ValueError("A screenshot is missing from the bundle. Use JSON-only export for notes without images.")
        result["screenshot_import"] = screenshot["id"]
    return result


def _revision(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, allow_nan=False).encode()).hexdigest()


def preview_import(runtime: Any, raw: bytes) -> dict:
    try:
        with _LOCK:
            timer = getattr(runtime, "_research_import_timer", None)
            if timer:
                timer.cancel()
            runtime._research_import = None
            answer = bookmarks_endpoint(runtime)
            if not answer.get("ok"):
                return answer
            root = Path(answer["study"])
            value, assets = _unpack(raw)
            incoming = value.get("bookmarks")
            if not isinstance(incoming, list) or not 1 <= len(incoming) <= 500:
                raise ValueError("Import between 1 and 500 bookmarks.")
            rows = [_import_row(row, assets) for row in incoming]
            if len({row["id"] for row in rows}) != len(rows):
                raise ValueError("The import contains duplicate bookmark IDs.")
            existing = {row["id"] for row in answer["bookmarks"]}
            previews = []
            for row in rows:
                try:
                    matches, reason = compatible(root, row)
                except (OSError, ValueError):
                    matches, reason = False, "The bookmarked source is unavailable in this study."
                previews.append({"title": row["title"], "tags": row["tags"],
                                 "duplicate": row["id"] in existing,
                                 "compatible": matches, "reason": reason,
                                 "screenshot": "screenshot_import" in row})
            token = uuid.uuid4().hex
            runtime._research_import = {"token": token, "expires": time.monotonic() + 600,
                "study": answer["study"], "revision": _revision(answer["bookmarks"]),
                "rows": rows, "assets": assets}
            def expire() -> None:
                with _LOCK:
                    pending = getattr(runtime, "_research_import", None)
                    if pending and pending["token"] == token:
                        runtime._research_import = None
            timer = threading.Timer(600, expire)
            timer.daemon = True
            runtime._research_import_timer = timer
            timer.start()
            return {"ok": True, "token": token, "study": answer["study"],
                    "bookmarks": previews, "count": len(rows),
                    "duplicates": sum(row["duplicate"] for row in previews),
                    "compatible": sum(row["compatible"] for row in previews)}
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        return {"ok": False, "error": str(exc)}


def apply_import(runtime: Any, payload: dict, *, path_for: Any = None) -> dict:
    try:
        with _LOCK:
            answer = bookmarks_endpoint(runtime)
            if not answer.get("ok"):
                return answer
            root = Path(answer["study"])
            pending = getattr(runtime, "_research_import", None)
            supplied = payload.get("study")
            if path_for and supplied:
                supplied = path_for(supplied)
                supplied = str(supplied) if supplied else None
            if (not pending or pending["token"] != payload.get("token")
                    or pending["expires"] < time.monotonic()):
                raise ValueError("Import preview expired. Choose the file again.")
            if supplied != answer["study"] or pending["study"] != answer["study"]:
                raise ValueError("The study changed. Preview the import again.")
            if payload.get("cancel"):
                runtime._research_import = None
                runtime._research_import_timer.cancel()
                return {"ok": True, "cancelled": True}
            if pending["revision"] != _revision(answer["bookmarks"]):
                raise ValueError("Bookmarks changed after preview. Preview the import again.")
            mode = payload.get("duplicates", "copy")
            if mode not in {"copy", "skip", "replace"}:
                raise ValueError("Choose keep both, skip existing, or replace matching IDs.")
            rows = list(answer["bookmarks"])
            existing = {row["id"] for row in rows}
            additions = []
            for imported in pending["rows"]:
                row = dict(imported)
                if row["id"] in existing:
                    if mode == "skip":
                        continue
                    if mode == "copy":
                        row["id"] = uuid.uuid4().hex
                additions.append(row)
            final_ids = (existing - {row["id"] for row in additions}) | {row["id"] for row in additions}
            if len(final_ids) > 500:
                raise ValueError("This import would exceed the study's 500-bookmark limit.")
            for row in additions:
                image_id = row.pop("screenshot_import", None)
                if image_id:
                    row["screenshot"] = store_image(root, pending["assets"][image_id])
            rows = [row for row in rows if row["id"] not in {new["id"] for new in additions}] + additions
            if Path(runtime.active_root).resolve() != root:
                raise ValueError("The study changed during import. Preview the file again.")
            write_rows(root, rows)
            runtime._research_import = None
            runtime._research_import_timer.cancel()
            return {"ok": True, "study": answer["study"], "bookmarks": rows, "imported": len(additions)}
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
