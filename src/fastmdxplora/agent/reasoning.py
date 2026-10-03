"""Documented model-specific inference controls, separate from scientific settings.

Official OpenAI model cards checked 2026-10-02. Unknown models retain defaults.
"""
from __future__ import annotations
import re

OPENAI_ORDER = ("gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna")
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def levels(provider, model):
    if provider == "openai-chatgpt":
        if model in {"gpt-6-astra", "gpt-6.1-sol"}:
            return list(EFFORTS)
        if model in {"gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}:
            return ["none", *EFFORTS]
        if model in {"gpt-5.5", "gpt-5.5-2026-04-23"}:
            return ["none", "low", "medium", "high", "xhigh"]
    if provider == "claude":
        if re.fullmatch(r"claude-(opus|sonnet)-(5(-5)?|4-[78])(-\d{8})?", model):
            return list(EFFORTS)
        if re.fullmatch(r"claude-(opus|sonnet)-4-6(-\d{8})?", model):
            return ["low", "medium", "high", "max"]
    if provider == "gemini":
        if re.fullmatch(r"gemini-3\.[78]-flash(-preview)?", model):
            return ["low", "medium", "high"]
        if re.fullmatch(r"gemini-3\.1-pro(-preview)?", model):
            return ["low", "medium", "high"]
        if re.fullmatch(r"gemini-3-pro(-preview)?", model):
            return ["low", "high"]
        if re.fullmatch(r"gemini-(3\.[56]-flash|3\.[15]-flash-lite|3-flash)(-preview)?", model):
            return ["minimal", "low", "medium", "high"]
        if re.fullmatch(r"gemini-2\.5-pro(-preview(-\d{2}-\d{2})?)?", model):
            return ["128", "1024", "4096", "8192", "16384", "32768"]
        if re.fullmatch(r"gemini-2\.5-flash(-lite)?(-preview(-\d{2}-\d{2})?)?", model):
            return ["0", "512", "1024", "4096", "8192", "16384", "24576"]
    return []


def decorate(provider, rows):
    output = [{**row, "reasoning_levels": row.get("reasoning_levels", levels(provider, row["id"]))}
              for row in rows]
    if provider == "openai-chatgpt":
        output.sort(key=lambda row: OPENAI_ORDER.index(row["id"]) if row["id"] in OPENAI_ORDER else len(OPENAI_ORDER))
    return output


def validate(effort, choices):
    from fastmdxplora.agent.openai_plan import ConnectionError
    if effort is None or effort == "default":
        return None
    if not isinstance(effort, str) or effort not in choices:
        raise ConnectionError("Choose a reasoning level supported by the selected model.")
    return effort
