"""Packaged software knowledge, plus the installed refusal registry."""
from __future__ import annotations

from importlib.resources import files


def error_reference() -> str:
    """All registered errors; generated from the same runtime definitions."""
    from fastmdxplora.refusals import CODES

    rows = ["# Registered FastMDXplora errors", "",
            "Generated from fastmdxplora.refusals.CODES. All entries are included.",
            "External/unclassified errors require their actual diagnostic; do not invent a cause.", ""]
    for code in CODES:
        rows.append(f"- `{code.id}` ({code.kind}; disclosure={code.disclosure}; "
                    f"retryable={str(code.retryable).lower()}): {code.summary}")
    return "\n".join(rows) + "\n"


def dashboard_knowledge() -> str:
    """Load installed Markdown; use the runtime registry to avoid stale codes."""
    reference = files("fastmdxplora.agent").joinpath("knowledge/fastmdxplora.md").read_text(encoding="utf-8")
    return reference + "\n\n" + error_reference()
