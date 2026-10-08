"""The Config Builder's **From a paper**: a paper read, its MD studies
listed with how each setting came from it, and the chosen ones opened in
the builder or downloaded as configs (:mod:`fastmdxplora.paper`).

On the person's own computer only: reading asks their AI model and may
fetch the paper, and a hosted GUI does neither for a visitor."""

from __future__ import annotations

import io
import zipfile
from typing import Any

__all__ = ["read_paper_studies", "configs_download"]


def read_paper_studies(payload: dict[str, Any], *, hosted: bool = False) -> dict[str, Any]:
    """``{source, si, until_determined}`` read: the paper's title, DOI and
    the AI model that read it, and each study as a plan (its state, its
    choices with the paper's words, its config)."""
    from fastmdxplora.paper.studies import length_said, plans_for, studies_in
    from fastmdxplora.refusals import CodedError

    if hosted:
        return {"ok": False, "error": "A paper is read only in a GUI on your own computer, "
                "with your own AI model: fastmdx gui, or fastmdx config --paper."}
    source = str(payload.get("source") or "").strip()
    if not source:
        return {"ok": False, "error": "Give the paper: a PDF on this computer, or the DOI of "
                "an open-access paper."}
    si = payload.get("si") or []
    si = [str(item).strip() for item in (si if isinstance(si, list) else [si]) if str(item).strip()]
    said: list[str] = []
    try:
        paper, reading = studies_in(source, si, said=said.append)
    except CodedError as exc:
        return {"ok": False, "error": str(exc), "said": said}
    except OSError as exc:
        return {"ok": False, "error": f"The paper could not be read: {exc}", "said": said}
    plans = plans_for(reading, until_determined=bool(payload.get("until_determined")))
    for plan in plans:
        plan["length"] = length_said(plan)
    return {
        "ok": True,
        "title": reading.get("title") or paper.title or source,
        "doi": reading.get("doi") or "",
        "route": reading.get("route") or "",
        "model": reading.get("model") or "",
        "left_out": reading.get("left_out") or [],
        "said": said,
        "plans": plans,
    }


def configs_download(payload: dict[str, Any]) -> tuple[bytes, str, str] | None:
    """The configs given (``{"configs": [{"id", "config"}]}``) as one YAML
    file, or several in a zip: its bytes, name and media type. None where
    none was given."""
    from fastmdxplora.paper.mapping import distinct, slug
    from fastmdxplora.paper.studies import config_text

    entries = [entry for entry in payload.get("configs") or []
               if isinstance(entry, dict) and isinstance(entry.get("config"), dict)]
    if not entries:
        return None
    names = distinct([slug(str(entry.get("id") or "study"), 12) for entry in entries])
    texts = [(name,
              config_text({"id": entry.get("id") or "study",
                           "label": entry.get("label") or "",
                           "state": entry.get("state") or "ready",
                           "choices": entry.get("choices") or [],
                           "config": entry["config"]}))
             for name, entry in zip(names, entries)]
    if len(texts) == 1:
        name, text = texts[0]
        return text.encode("utf-8"), f"paper-{name}.yml", "application/x-yaml"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in texts:
            archive.writestr(f"paper-{name}.yml", text)
    return buffer.getvalue(), "paper-studies.zip", "application/zip"
