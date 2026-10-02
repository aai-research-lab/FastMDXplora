"""Bounded, metadata-free PNG thumbnails in private study-local storage."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

MAX_PNG_BYTES = 600_000
MAX_IMAGE_STORAGE = 64_000_000


def image_path(root: Path, image_id: Any) -> Path:
    if not isinstance(image_id, str) or not re.fullmatch(r"[a-f0-9]{64}", image_id):
        raise ValueError("Invalid bookmark screenshot identity.")
    path = root / ".research" / "screenshots" / (image_id + ".png")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Screenshot storage must stay inside the study.")
    return path


def normalize_image(value: Any) -> tuple[bytes, int, int]:
    if not isinstance(value, str) or not value.startswith("data:image/png;base64,") or len(value) > 810_000:
        raise ValueError("Use a PNG screenshot up to 600 KB.")
    try:
        raw = base64.b64decode(value.partition(",")[2], validate=True)
        if len(raw) > MAX_PNG_BYTES:
            raise ValueError("Screenshot exceeds 600 KB.")
        # Inspect dimensions before decoding pixels; refuse decompression bombs.
        with Image.open(io.BytesIO(raw)) as source:
            if source.format != "PNG" or not 0 < source.width <= 1600 or not 0 < source.height <= 1600:
                raise ValueError("Screenshot must be a PNG up to 1600 × 1600 pixels.")
            source.load()
            output = io.BytesIO()
            source.convert("RGB").save(output, format="PNG", optimize=True)
            pixels = output.getvalue()
            width, height = source.size
        if len(pixels) > MAX_PNG_BYTES:
            raise ValueError("Screenshot exceeds 600 KB after normalization.")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Screenshot is not a readable PNG.") from exc
    return pixels, width, height


def store_image(root: Path, value: Any) -> dict[str, Any]:
    pixels, width, height = normalize_image(value)
    image_id = hashlib.sha256(pixels).hexdigest()
    path = image_path(root, image_id)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        total = sum(p.stat().st_size for p in path.parent.glob("*.png"))
        if total + len(pixels) > MAX_IMAGE_STORAGE:
            raise ValueError("Study screenshot storage is full (64 MB). Save without a screenshot.")
        fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(pixels)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return {"id": image_id, "width": width, "height": height}


def read_image(root: Path, reference: Any) -> str:
    if not isinstance(reference, dict):
        raise ValueError("This bookmark has no screenshot.")
    path = image_path(root, reference.get("id"))
    if path.stat().st_size > MAX_PNG_BYTES:
        raise ValueError("Stored screenshot exceeds the size limit.")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference["id"]:
        raise ValueError("The stored screenshot changed or is damaged.")
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
