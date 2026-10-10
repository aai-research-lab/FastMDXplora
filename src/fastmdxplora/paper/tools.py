"""A paper's MD studies for the Agent and for an AI app.

The Agent's ``studies_in_paper`` reads a paper as the command line and the
Config Builder do, and says its studies, and
one study's config when asked. An AI app reads the paper itself: its
``read_paper`` gives the paper's text a page at a time with what to read
and how to answer, and its ``check_paper_studies`` takes the AI app's
reading and checks it here as any reading is checked, every value against
the paper's own words; the AI app writes, and the software judges.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmdxplora.paper import PaperRefused
from fastmdxplora.paper.text import PaperText

__all__ = ["open_given", "page_of", "how_to_read", "checked_said", "studies_said",
           "study_config_said", "PAGE_CHARS"]

#: Characters of the paper given in one page.
PAGE_CHARS = 40_000


def open_given(source: str, si: str | None = None, *,
               inside: Callable[[str], Path | None] | None = None) -> PaperText:
    """The paper ``source`` names, a file (held by ``inside`` where given:
    the workspace's rule) or an identifier, with ``si`` read as its
    supporting information."""
    from fastmdxplora.paper.fetch import identify, open_paper

    def held(given: str) -> str:
        if inside is None:
            return given
        found = inside(given)
        if found is None:
            raise PaperRefused(f"{given} is outside the workspace, so it is not read.",
                               code="environment.workspace.outside")
        return str(found)

    kind, identifier = identify(source)
    paper_source = held(identifier) if kind == "file" else source
    extras = [held(si)] if si else []
    return open_paper(paper_source, extras)


def page_of(paper: PaperText, page: int) -> str:
    """Page ``page`` (from 1) of the paper's text as the AI model is shown it."""
    from fastmdxplora.paper.extract import BUDGET_CHARS

    text, left_out = paper.shown(BUDGET_CHARS)
    pages = max(1, -(-len(text) // PAGE_CHARS))
    if page < 1 or page > pages:
        raise PaperRefused(f"The paper's text is {pages} page(s); ask for 1 to {pages}.",
                           code="config.option.out_of_range")
    chunk = text[(page - 1) * PAGE_CHARS: page * PAGE_CHARS]
    head = (f"{paper.title or 'The paper'}" + (f" (doi:{paper.doi})" if paper.doi else "")
            + f": page {page} of {pages}.")
    if left_out:
        head += " Left out as too long: " + ", ".join(left_out[:8]) + "."
    return head + "\n\n" + chunk


def how_to_read() -> str:
    """What check_paper_studies takes: the studies, each protocol's
    settings and the results, every value with the paper's words."""
    from fastmdxplora.paper.fields import (
        CLAIM_QUANTITIES,
        ERROR_KINDS,
        FIELDS,
        METHODS,
    )

    fields = "\n".join(f"- {field.name}: {field.asks}" for field in FIELDS)
    return (
        "Read the whole paper (every page), then give check_paper_studies a JSON object:\n"
        '{"title": "...", "studies": [{"id": "S1", "label": "...", "protocol": "P1", '
        '"fields": {...}}], "protocol_fields": {"P1": {...}}, "claims": [{"study": "S1", '
        '"quantity": "...", "what": "...", "value": 1.42, "error": 0.02, "error_kind": '
        '"standard_error", "n": 3, "unit": "nm", "quote": "..."}]}\n\n'
        "One study is one starting system under one force field and water model, at one "
        "temperature, by one method; independent repeats are one study with `replicas`. "
        "Each field is {\"value\": ..., \"unit\": \"...\", \"quote\": \"the paper's own words, "
        "copied exactly\"}, or {\"status\": \"by_reference\" or \"in_si\", \"quote\": \"...\"}; "
        "a setting the paper does not state is left out. Numbers as the paper writes them, "
        "never computed. A study's own fields override its protocol's.\n\n"
        f"The fields:\n{fields}\n\n`method` is one of {', '.join(METHODS)}. A claim's "
        f"quantity is one of {', '.join(CLAIM_QUANTITIES)}; its error_kind one of "
        f"{', '.join(ERROR_KINDS)}. Only results in the text or a table, not figures.")


def checked_said(paper: PaperText, reading_json: str, *, model: str = "",
                 until_determined: bool = False) -> str:
    """An AI app's reading of ``paper``, checked here: each study's state,
    what was not found in the paper's words, what it needs, and each
    runnable study's config as YAML."""
    from fastmdxplora.paper.extract import check_reading
    from fastmdxplora.paper.studies import plans_for

    try:
        raw = json.loads(reading_json)
    except ValueError as exc:
        raise PaperRefused(f"The reading is not JSON: {exc}",
                           code="config.option.wrong_type") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("studies"), list):
        raise PaperRefused("The reading is a JSON object with `studies`, as read_paper "
                           "says.", code="config.option.wrong_type")
    reading = check_reading(paper, raw, model=model or "an AI app")
    plans = plans_for(reading, until_determined=until_determined)
    lines = [studies_said(plans, reading), ""]
    for study, plan in zip(reading["studies"], plans):
        refused = [f"{name} ({record['status'].replace('_', ' ')})"
                   for name, record in study["fields"].items()
                   if record.get("status") in ("not_found", "unread")]
        if refused:
            lines.append(f"{plan['id']}: not used, its words not the paper's or not holding "
                         f"the value: {', '.join(refused)}. Quote the paper exactly.")
    for plan in plans:
        if plan.get("config") is not None:
            lines += ["", study_config_said(plan)]
    return "\n".join(lines).rstrip()


def studies_said(plans: list[dict[str, Any]], reading: dict[str, Any]) -> str:
    from fastmdxplora.paper.studies import said_of

    title = reading.get("title") or "The paper"
    lines = [f"{title}" + (f" (doi:{reading['doi']})" if reading.get("doi") else "")
             + f": {len(plans)} MD stud{'y' if len(plans) == 1 else 'ies'}, every value "
             "checked against the paper's own words."]
    for plan in plans:
        lines.append(f"- {said_of(plan)}")
        for choice in plan.get("choices") or []:
            if choice["label"] in ("needs_you", "not_possible"):
                lines.append(f"    {choice['label'].replace('_', ' ')}: {choice['why']}")
    return "\n".join(lines)


def study_config_said(plan: dict[str, Any]) -> str:
    """One study's config as the file it is written as, with what differs."""
    from fastmdxplora.paper.studies import config_text

    differs = [f"- {choice['field']}: {choice['why']}" for choice in plan.get("choices") or []
               if choice["label"] == "differs"]
    head = f"{plan['id']}'s config" + (", with these differences from the paper:\n"
                                        + "\n".join(differs) if differs else ":")
    return head + "\n```yaml\n" + config_text(plan) + "```"
