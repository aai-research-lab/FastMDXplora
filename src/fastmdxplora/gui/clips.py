"""Encode browser-rendered frames, leaving scientific artifacts untouched."""
from __future__ import annotations

import base64
import io
import json
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from PIL import Image

from fastmdxplora.gui.research import clean_view
from fastmdxplora.gui.research_sources import source_for

_LOCK = threading.RLock()


def clip_endpoint(runtime, payload: dict, *, path_for=None) -> dict:
    """One bounded upload per runtime; stale studies and playback are refused."""
    from fastmdxplora.gui.agent_panel import _is_study

    with _LOCK:
        session = getattr(runtime, "_clip_upload", None)
        if session and time.monotonic() - session["started"] > 600:
            session["temp"].cleanup()
            runtime._clip_upload = session = None
        try:
            root = getattr(runtime, "active_root", None)
            if not root or getattr(runtime, "data_stale", False) or not _is_study(root):
                raise ValueError("Load a current study before exporting a clip.")
            root = Path(root).resolve()
            supplied = payload.get("study")
            if path_for and supplied:
                supplied = path_for(supplied)
            if not supplied or Path(supplied).resolve() != root:
                raise ValueError("The study changed during export.")
            action = payload.get("action")
            if action == "start":
                if session:
                    raise ValueError("An export is already in progress. Cancel it first.")
                fmt = payload.get("format")
                if fmt not in {"gif", "mp4", "both"}:
                    raise ValueError("Choose GIF, MP4 or both.")
                fps = payload.get("fps")
                frames = payload.get("frames")
                if not isinstance(fps, (int, float)) or isinstance(fps, bool) or not 1 <= fps <= 30:
                    raise ValueError("Playback rate must be 1–30 frames per second.")
                if (not isinstance(frames, list) or not 2 <= len(frames) <= 120
                        or any(type(i) is not int or i < 0 for i in frames)
                        or any(a >= b for a, b in zip(frames, frames[1:]))):
                    raise ValueError("Choose 2–120 increasing browser frame indices.")
                index_path = root / "simulation/playback_index.json"
                if not index_path.resolve().is_relative_to(root) or index_path.stat().st_size > 2_000_000:
                    raise ValueError("Playback index must stay inside the study.")
                index = json.loads(index_path.read_text(encoding="utf-8"))
                if (not index.get("source_signature") or index["source_signature"] != payload.get("signature")
                        or frames[-1] >= index.get("n_frames_browser", 0)):
                    raise ValueError("Playback changed. Reload the trajectory before export.")
                if fmt in {"mp4", "both"} and not shutil.which("ffmpeg"):
                    raise ValueError("MP4 requires ffmpeg on PATH. GIF is available without it.")
                view = clean_view(payload.get("view", {}))
                view.update(page="viewer", mode="playback", frame=frames[0], playback_signature=index["source_signature"])
                view.pop("live_step", None)
                source = source_for(root, view)
                if not source["evidence"].get("playback_coordinates"):
                    raise ValueError("Saved playback coordinates are unavailable.")
                labels = payload.get("labels", {})
                if not isinstance(labels, dict) or any(type(v) is not bool for v in labels.values()) or set(labels) - {"residues", "atoms", "frame", "time"}:
                    raise ValueError("Choose valid label checkboxes.")
                rotation = payload.get("rotation", 0)
                if type(rotation) not in {int, float} or not -360 <= rotation <= 360:
                    raise ValueError("Camera rotation must be within −360 to 360 degrees.")
                scope = payload.get("label_scope", "selected")
                if scope not in {"selected", "protein"}:
                    raise ValueError("Choose selected-residue or protein label scope.")
                session = {"id": uuid.uuid4().hex, "root": root, "temp": tempfile.TemporaryDirectory(prefix="fastmdx-clip-"),
                    "started": time.monotonic(), "format": fmt, "fps": fps, "frames": frames,
                    "view": view, "source": source, "rotation": rotation, "label_scope": scope, "count": 0, "bytes": 0,
                    "signature": index["source_signature"],
                    "source_frames": [index.get("frame_indices", [])[i] for i in frames],
                    "times_ns": [index.get("frame_times_ns", [])[i] if i < len(index.get("frame_times_ns", [])) else None for i in frames],
                    "labels": labels}
                runtime._clip_upload = session
                return {"ok": True, "id": session["id"]}
            if not session or payload.get("id") != session["id"] or session["root"] != root:
                raise ValueError("This export session is unavailable.")
            if action == "cancel":
                session["temp"].cleanup()
                runtime._clip_upload = None
                return {"ok": True}
            folder = Path(session["temp"].name)
            if action == "frame":
                if payload.get("index") != session["count"] or session["count"] >= len(session["frames"]):
                    raise ValueError("Frames must be uploaded once, in order.")
                png = payload.get("png", "")
                if not isinstance(png, str) or len(png) > 940_000:
                    raise ValueError("Clip images exceed the export size limit.")
                raw = base64.b64decode(png, validate=True)
                if len(raw) > 700_000 or session["bytes"] + len(raw) > 64_000_000:
                    raise ValueError("Clip images exceed the export size limit.")
                image = Image.open(io.BytesIO(raw))
                if image.format != "PNG" or image.width > 1280 or image.height > 960 or image.width < 2 or image.height < 2:
                    raise ValueError("Clip frames must be PNG, up to 1280 × 960.")
                if session.get("size", image.size) != image.size:
                    raise ValueError("All frames must have the same dimensions.")
                session["size"] = image.size
                image.convert("RGB").save(folder / f"{session['count']:04d}.png")
                session["count"] += 1
                session["bytes"] += len(raw)
                return {"ok": True, "count": session["count"]}
            if action != "finish" or session["count"] != len(session["frames"]):
                raise ValueError("Upload all frames before finishing the clip.")
            if source_for(root, session["view"]) != session["source"]:
                raise ValueError("The trajectory changed during export. Reload playback and try again.")
            # Never overwrite a scientific file or a previous export.
            destination = root / "exports/clips" / session["id"]
            if not destination.resolve().is_relative_to(root):
                raise ValueError("Clip exports must stay inside the study.")
            images = sorted(folder.glob("*.png"))
            formats = ["gif", "mp4"] if session["format"] == "both" else [session["format"]]
            encoders = {}
            for fmt in formats:
                encoded = folder / ("trajectory." + fmt)
                if fmt == "gif":
                    opened = [Image.open(path).convert("RGB").quantize(colors=256) for path in images]
                    duration = max(10, round(1000 / session["fps"] / 10) * 10)
                    try:
                        opened[0].save(encoded, save_all=True, append_images=opened[1:],
                            duration=duration, loop=0, disposal=2)
                    finally:
                        for image in opened:
                            image.close()
                    encoders[fmt] = {"name": "Pillow", "frame_duration_ms": duration,
                                     "effective_fps": 1000 / duration}
                else:
                    result = subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-loglevel", "error",
                        "-framerate", str(session["fps"]), "-i", str(folder / "%04d.png"),
                        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(encoded)],
                        capture_output=True, timeout=90, check=False)
                    if result.returncode != 0:
                        raise ValueError("ffmpeg could not encode this clip. Try GIF or check your ffmpeg installation.")
                    encoders[fmt] = {"name": "ffmpeg/libx264", "pixel_format": "yuv420p", "effective_fps": session["fps"]}
            destination.mkdir(parents=True, exist_ok=False)
            for fmt in formats:
                shutil.copyfile(folder / ("trajectory." + fmt), destination / ("trajectory." + fmt))
            record = {key: session[key] for key in ("format", "fps", "frames", "source_frames", "times_ns", "signature", "source", "view", "labels", "rotation", "label_scope")}
            record["encoders"] = encoders
            record["environment_overlay"] = "Solvent, ions and unit-cell overlays displayed by the browser may be static reference geometry. This clip does not represent their full trajectory dynamics."
            record["camera_path"] = {"axis": "y", "total_degrees": session["rotation"],
                                     "fractions": [i / (len(session["frames"]) - 1) for i in range(len(session["frames"]))],
                                     "base_view": session["view"].get("camera")}
            record["notice"] = "Browser visualization of saved playback frames, which may be sampled and solvent-stripped. No coordinate interpolation. fps is presentation speed, not simulation time. Rendered images supplied by the local browser; metadata identifies its selected source."
            (destination / "view.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            session["temp"].cleanup()
            runtime._clip_upload = None
            relative = destination.relative_to(root).as_posix()
            urls = {fmt: f"/artifacts/{relative}/trajectory.{fmt}" for fmt in formats}
            return {"ok": True, "url": urls[formats[0]], "urls": urls, "metadata_url": f"/artifacts/{relative}/view.json"}
        except (ValueError, OSError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired, Image.DecompressionBombError):
            # Detailed validation errors above are safe, but decoder/system errors
            # can contain user paths. Keep those out of browser diagnostics.
            import sys
            error = sys.exc_info()[1]
            message = str(error) if type(error) is ValueError else "Export failed; check the study, image data and encoder availability."
            if session and payload.get("id") == session["id"]:
                session["temp"].cleanup()
                runtime._clip_upload = None
            return {"ok": False, "error": message}
