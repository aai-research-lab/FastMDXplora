"""The MD studies in a paper, as FastMDXplora reads them and checks them.

FastMDXplora reads three things, with the AI model chosen with `fastmdx
agent model`, each time showing it the paper's text with its parts labelled: which MD studies the paper reports (one
system under one protocol each, with what tells them apart), each
protocol's settings, and the results each study reports. Long answers come
in rounds. Each answer is JSON, every value with the paper's words it was
read from.

What comes back is not used as it is. Each value's words are looked for in
the paper (:class:`~fastmdxplora.paper.quotes.QuoteIndex`) and the value is
read from them here (:mod:`fastmdxplora.paper.values`); a value whose words
are not the paper's, or which its words do not hold, is kept as it was read
and marked so, and never used. A study's settings are its own
where it states them, else its protocol's.

A reading is kept under the paper's digest, the AI model it was read with
and the version of the questions, so choosing other studies of the same
paper reads nothing again.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmdxplora.paper import PaperRefused
from fastmdxplora.paper.fields import (
    BY_NAME,
    CLAIM_QUANTITIES,
    ERROR_KINDS,
    FIELDS,
    METHODS,
    PROTOCOL_FIELDS,
    STUDY_FIELDS,
)
from fastmdxplora.paper.quotes import QuoteIndex, squash
from fastmdxplora.paper.text import PaperText
from fastmdxplora.paper.values import CANONICAL, WORD_NUMBERS, numbers_in, plain, read_value

__all__ = ["read_studies", "check_reading", "PROMPT_VERSION", "BUDGET_CHARS",
           "kept_reading", "cache_root", "STATUSES"]

#: The questions' version: a reading kept from other questions is not reused.
PROMPT_VERSION = "3"

#: How much of the paper the AI model is shown, in characters: about 50,000
#: tokens, which holds a paper and its methods supplement.
BUDGET_CHARS = 200_000

#: Pages of studies or results asked for, at most. Each page is one reply.
MOST_PAGES = 12

#: What a setting read from a paper can be:
#:
#: ``stated``        the paper says it, in the words kept, and its value is read from them
#: ``not_stated``    the paper does not say it
#: ``by_reference``  the paper says it is as in another paper
#: ``in_si``         the paper says it is in its supporting information, not given here
#: ``not_found``     the AI model gave words the paper does not contain
#: ``unread``        the words are the paper's, and do not hold the value given
STATUSES = ("stated", "not_stated", "by_reference", "in_si", "not_found", "unread")

Complete = Callable[[str], str]


def cache_root() -> Path:
    override = os.environ.get("FASTMDXPLORA_CACHE_DIR")
    base = Path(override) if override else Path.home() / ".cache" / "fastmdxplora"
    return base / "papers"


def _key(paper: PaperText, model: str) -> Path:
    digest = paper.sha256()
    tag = hashlib.sha256(f"{model}\0{PROMPT_VERSION}".encode()).hexdigest()[:12]
    return cache_root() / digest[:24] / f"reading-{tag}.json"


def kept_reading(paper: PaperText, model: str) -> dict[str, Any] | None:
    """A reading of this paper by this AI model kept from before, or None."""
    path = _key(paper, model)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") == PROMPT_VERSION else None


# ---------------------------------------------------------------------------
# The questions
# ---------------------------------------------------------------------------
_RULES = """\
Rules for every answer:
- Answer with one JSON object and nothing else.
- Every value comes with "quote": the paper's own words it is read from,
  copied exactly as the text above has them (at most about 200 characters,
  no paraphrase, no ellipsis unless the words are apart). A value the paper
  does not state is left out; never infer, assume or compute a value.
- A number is given as the paper writes it, with its unit as written, in
  "value" and "unit". A total over several runs is not a per-run value.
- Where the paper says a setting is as in another paper, give
  {"status": "by_reference", "quote": "..."}; where it says the setting is
  in its supporting information and the text above does not have it, give
  {"status": "in_si", "quote": "..."}.
- The text is the paper's parts, each under its label in [[ ]]."""

_STUDY_RULES = """\
What a study is:
- One MD study is one starting system (its structure, mutations, ligands,
  membrane) under one force field and water model, at one temperature, by
  one method. Independent repeats of the same are ONE study, with
  "replicas" their number.
- Simulations the authors took from another paper are not studies of this
  paper. Runs that only prepare or seed a method (steered pulls seeding
  umbrella windows, equilibration) belong to that method's study.
- A paper applying one protocol to many systems (a dataset) is one study
  per protocol, its "system" saying which entries.
- Coarse-grained and all-atom runs are different studies."""


def _fields_list(names: tuple[str, ...]) -> str:
    return "\n".join(f'- "{name}": {BY_NAME[name].asks}' for name in names)


def _studies_prompt(text: str, listed: list[str]) -> str:
    more = ("" if not listed else
            "\nThe studies already listed are: " + ", ".join(listed) +
            ". List the ones after them only, numbering on.\n")
    return f"""You are reading a scientific paper to list the molecular dynamics (MD) studies it reports, so that each can be reproduced.

{_STUDY_RULES}

{_RULES}

For each study give:
  "id": "S1", "S2", ... in the order the paper introduces them;
  "label": a few words naming it;
  "protocol": "P1", "P2", ...: studies sharing one set of simulation settings share a protocol;
  "fields": an object of the settings that tell this study apart, each
    {{"value": ..., "unit": "...", "quote": "..."}}, from:
{_fields_list(STUDY_FIELDS)}
    "method" is one of {", ".join(METHODS)}.
Also give "protocols": [{{"id": "P1", "label": "what the protocol is"}}], and
"more": true if there are more studies than fit in this answer (list at most
5 per answer), else false.
{more}
Answer as {{"title": "...", "studies": [...], "protocols": [...], "more": false}}.

The paper:

{text}"""


def _protocol_prompt(text: str, protocol: dict[str, Any], studies: list[str],
                     names: tuple[str, ...] = PROTOCOL_FIELDS) -> str:
    return f"""You are reading a scientific paper to find the simulation settings of one of its MD protocols, so that its studies can be reproduced.

The protocol: {protocol.get("id")}, "{protocol.get("label", "")}", used by the studies {", ".join(studies)}.

{_RULES}

Give "fields": an object of the settings the paper states for this protocol,
each {{"value": ..., "unit": "...", "quote": "..."}}, from:
{_fields_list(names)}

Settings that differ between studies of the protocol are left out here.
Answer as {{"fields": {{...}}}}.

The paper:

{text}"""


def _claims_prompt(text: str, studies: list[dict[str, Any]], listed: int) -> str:
    named = "\n".join(f'- {s.get("id")}: {s.get("label", "")}' for s in studies)
    more = "" if not listed else f"\n{listed} results are already listed: give the ones after them.\n"
    return f"""You are reading a scientific paper to list the numerical results its MD studies report, so that a reproduction can be compared with them.

The studies:
{named}

{_RULES}

List each result stated in the text or a table (not one only seen in a
figure; not an experimental value), as
  {{"study": "S1" (or a list of ids), "quantity": one of {", ".join(CLAIM_QUANTITIES)},
   "what": what exactly (which atoms, which state, which part of the run),
   "value": the number as written, "error": the ± number or null,
   "error_kind": one of {", ".join(ERROR_KINDS)} (as the paper defines its ±),
   "n": the number of runs or blocks the error is over, or null,
   "unit": as written, "quote": "..."}}.
List at most 12 per answer; "more": true if there are more.
{more}
Answer as {{"claims": [...], "more": false}}.

The paper:

{text}"""


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------
#: What each answer's key holds: an answer whose key holds anything else
#: ("fields": [] or null) is not the answer asked for.
#: A list answered as null is an empty list; settings must be an object.
_SHAPE: dict[str, tuple[type, ...]] = {"fields": (dict,), "studies": (list, type(None)),
                                       "claims": (list, type(None))}


def _json_from(reply: str, wanted: str) -> dict[str, Any]:
    """The object the AI model was asked for: the first in ``reply`` that has
    the key ``wanted``. A reply cut off part way parses, if at all, only as
    one of the objects inside it, which never has that key, so a cut reply
    is refused rather than read as one with nothing in it."""
    text = str(reply or "")
    start = text.find("{")
    while start >= 0:
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(value, dict) and wanted in value \
                and isinstance(value[wanted], _SHAPE.get(wanted, (object,))):
            return value
        start = text.find("{", start + 1)
    raise PaperRefused(
        "FastMDXplora could not read the paper: its reading came back cut off, or "
        "not in the form it reads, so nothing was taken from it. Try again, or "
        "choose another AI model with `fastmdx agent model`.",
        code="environment.service.unusable_response")


def _ask(complete: Complete, prompt: str, said: Callable[[str], None], what: str,
         wanted: str, *, paged: bool = True) -> dict[str, Any]:
    """The answer to ``prompt``, asked once more, said plainly, where the
    reply is not the JSON asked for: a reply cut off or wrapped in prose is
    common. Only an answer given in pages (``paged``) is told it may give
    fewer items; one that is not would leave settings out unsaid."""
    said(what)
    reply = complete(prompt)
    try:
        return _json_from(reply, wanted)
    except PaperRefused:
        again = "\n\nAnswer with the JSON object only, whole."
        if paged:
            again += " If it does not fit, give fewer items and \"more\": true."
        return _json_from(complete(prompt + again), wanted)


def _protocol_settings(complete: Complete, text: str, protocol: dict[str, Any],
                       users: list[str], said: Callable[[str], None]) -> dict[str, Any]:
    """A protocol's settings, asked for all at once and, where the reply is
    not whole (most often cut off at what an AI model may answer at a time),
    in two halves, each asked whole: an answer told to give fewer would
    leave settings out, and each left out would read as one the paper does
    not state."""
    pid = protocol.get("id")
    said(f"Reading the settings of protocol {pid}...")
    try:
        answer = _json_from(complete(_protocol_prompt(text, protocol, users)), "fields")
    except PaperRefused:
        half = len(PROTOCOL_FIELDS) // 2
        fields: dict[str, Any] = {}
        for number, part in enumerate((PROTOCOL_FIELDS[:half], PROTOCOL_FIELDS[half:]), 1):
            got = _ask(complete, _protocol_prompt(text, protocol, users, part), said,
                       f"The settings of protocol {pid} did not come whole at once: "
                       f"reading them in two parts ({number} of 2)...", "fields",
                       paged=False).get("fields")
            if isinstance(got, dict):
                fields.update({name: value for name, value in got.items() if name in part})
        return fields
    fields = answer.get("fields")
    return fields if isinstance(fields, dict) else {}


def _claim_key(claim: dict[str, Any]) -> tuple[str, str, str, str]:
    return (str(claim.get("study")), str(claim.get("quantity")), str(claim.get("value")),
            squash(str(claim.get("quote") or ""))[0])


def read_studies(paper: PaperText, complete: Complete, *, model: str = "",
                 said: Callable[[str], None] | None = None,
                 use_kept: bool = True) -> dict[str, Any]:
    """The MD studies of ``paper`` as ``complete`` (an AI model: prompt in,
    text out) reads them, checked here (:func:`check_reading`), and kept.

    ``model`` names the AI model, for the record and for the kept reading's
    key."""
    tell = said or (lambda _message: None)
    if use_kept:
        kept = kept_reading(paper, model)
        if kept is not None:
            tell("Using the reading of this paper kept from before.")
            return kept
    text, left_out = paper.shown(BUDGET_CHARS)
    if left_out:
        tell(f"Too long to show whole: left out {', '.join(left_out[:8])}"
             + (" and more" if len(left_out) > 8 else "") + ".")

    studies: list[dict[str, Any]] = []
    protocols: dict[str, dict[str, Any]] = {}
    title = ""
    for page in range(MOST_PAGES):
        answer = _ask(complete, _studies_prompt(text, [str(s.get("id")) for s in studies]),
                      tell, "Reading which MD studies the paper reports"
                      + (f" (round {page + 1})" if page else "") + "...", "studies")
        title = title or str(answer.get("title") or "")
        known = {str(s.get("id")) for s in studies}
        new = [s for s in answer.get("studies") or []
               if isinstance(s, dict) and str(s.get("id")) not in known]
        studies.extend(new)
        for protocol in answer.get("protocols") or []:
            if isinstance(protocol, dict) and protocol.get("id"):
                protocols.setdefault(str(protocol["id"]), protocol)
        if answer.get("more") and not new:
            tell(f"Stopped at {len(studies)} stud{'y' if len(studies) == 1 else 'ies'}: "
                 "more were said to follow, but no new ones came.")
        if not answer.get("more") or not new:
            break
    else:
        tell(f"Stopped after {MOST_PAGES} rounds of listing studies; the paper may have "
             f"more than the {len(studies)} listed.")
    for study in studies:
        pid = str(study.get("protocol") or "P1")
        protocols.setdefault(pid, {"id": pid, "label": ""})

    protocol_fields: dict[str, dict[str, Any]] = {}
    for pid, protocol in protocols.items():
        users = [str(s.get("id")) for s in studies if str(s.get("protocol") or "P1") == pid]
        if not users:
            continue
        protocol_fields[pid] = _protocol_settings(complete, text, protocol, users, tell)

    claims: list[dict[str, Any]] = []
    if studies:
        for page in range(MOST_PAGES):
            answer = _ask(complete, _claims_prompt(text, studies, len(claims)), tell,
                          "Reading the results each study reports"
                          + (f" (round {page + 1})" if page else "") + "...", "claims")
            seen = {_claim_key(c) for c in claims}
            new = [c for c in answer.get("claims") or []
                   if isinstance(c, dict) and _claim_key(c) not in seen]
            claims.extend(new)
            if not answer.get("more") or not new:
                break
        else:
            tell(f"Stopped after {MOST_PAGES} rounds of listing results; the paper may "
                 "report more.")

    raw = {"title": title, "studies": studies, "protocols": list(protocols.values()),
           "protocol_fields": protocol_fields, "claims": claims}
    reading = check_reading(paper, raw, model=model, left_out=left_out)
    path = _key(paper, model)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".part")
        temporary.write_text(json.dumps(reading, indent=1), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass
    return reading


def read_by_fastmdxplora(model: str) -> str:
    """Who read a paper, as its record says it: FastMDXplora, with the AI
    model or the AI app it read with where one is named."""
    return f"FastMDXplora with {model}" if model else "FastMDXplora"


def who_read(reading: dict[str, Any]) -> str:
    """Who read ``reading``: as it says, or, for one kept from before it
    said, FastMDXplora with the AI model it was read with."""
    return str(reading.get("read_by") or read_by_fastmdxplora(str(reading.get("model") or "")))


# ---------------------------------------------------------------------------
# Checking what the AI model said
# ---------------------------------------------------------------------------
def check_reading(paper: PaperText, raw: dict[str, Any], *, model: str = "",
                  left_out: list[str] | None = None) -> dict[str, Any]:
    """The AI model's answers (``studies``, ``protocols``,
    ``protocol_fields``, ``claims``) checked against the paper: each
    study's settings as :func:`check_field` finds them, its protocol's
    where it states none of its own, and its results as
    :func:`check_claim` finds them. Also what an AI app hands
    :mod:`fastmdxplora.mcp`, which is checked the same way.

    ``model`` is what FastMDXplora read with: the AI model, or the AI app
    that gave the reading; the record says FastMDXplora with it."""
    index = QuoteIndex(paper)
    protocol_fields = raw.get("protocol_fields") if isinstance(raw.get("protocol_fields"), dict) else {}
    checked_protocols = {
        str(pid): {name: check_field(index, name, value)
                   for name, value in (fields or {}).items() if name in BY_NAME}
        for pid, fields in protocol_fields.items() if isinstance(fields, dict)}
    studies_out = []
    seen_ids: set[str] = set()
    for number, study in enumerate(raw.get("studies") or [], start=1):
        if not isinstance(study, dict):
            continue
        sid = str(study.get("id") or f"S{number}")
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        pid = str(study.get("protocol") or "P1")
        own = {name: check_field(index, name, value)
               for name, value in (study.get("fields") or {}).items()
               if name in BY_NAME} if isinstance(study.get("fields"), dict) else {}
        fields: dict[str, Any] = {}
        for field in FIELDS:
            mine = own.get(field.name)
            theirs = checked_protocols.get(pid, {}).get(field.name)
            chosen = mine if mine and mine["status"] == "stated" else (
                theirs if theirs and theirs["status"] == "stated" else (mine or theirs))
            if chosen is not None:
                fields[field.name] = {**chosen, "from": "study" if chosen is mine else "protocol"}
        studies_out.append({"id": sid, "label": str(study.get("label") or sid)[:160],
                            "protocol": pid, "fields": fields, "claims": []})
    by_id = {study["id"]: study for study in studies_out}
    for claim in raw.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        checked = check_claim(index, claim)
        targets = claim.get("study")
        ids = [str(t) for t in targets] if isinstance(targets, list) else [str(targets)]
        for sid in ids:
            if sid in by_id:
                by_id[sid]["claims"].append(checked)
    return {
        "version": PROMPT_VERSION,
        "title": str(raw.get("title") or paper.title or ""),
        "doi": paper.doi,
        "source": paper.source,
        "route": paper.route,
        "licence": paper.licence,
        "paper_sha256": paper.sha256(),
        "model": model,
        "read_by": read_by_fastmdxplora(model),
        "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "left_out": list(left_out or []),
        "protocols": [{"id": str(p.get("id")), "label": str(p.get("label") or "")[:200]}
                      for p in raw.get("protocols") or [] if isinstance(p, dict)],
        "studies": studies_out,
    }


def check_field(index: QuoteIndex, name: str, given: Any) -> dict[str, Any]:
    """One setting as the AI model gave it, checked: its words found in the
    paper and its value read from them. Always a record with ``status``
    (:data:`STATUSES`); ``said`` keeps what the AI model gave."""
    field = BY_NAME[name]
    if not isinstance(given, dict):
        given = {"value": given}
    status = str(given.get("status") or "").strip().lower()
    quote = str(given.get("quote") or "").strip()
    said = {key: given.get(key) for key in ("value", "unit") if given.get(key) is not None}
    if status in ("not_stated", "absent", "none"):
        return {"status": "not_stated"}
    if status in ("by_reference", "in_si"):
        found = index.find(quote) if quote else None
        out: dict[str, Any] = {"status": status if found else "not_found"}
        if found:
            out.update(quote=found.words, where=found.label)
        elif quote:
            out["said"] = {"quote": quote[:300]}
        return out
    if given.get("value") in (None, "", [], {}) and not quote:
        return {"status": "not_stated"}
    found = index.find(quote) if quote else None
    if found is None:
        return {"status": "not_found", "said": {**said, "quote": quote[:300]}}
    record: dict[str, Any] = {"quote": found.words, "where": found.label,
                              "in": found.kind}
    if field.kind in CANONICAL:
        if field.kind == "count":
            value = _count_read(found.words, name, given.get("value"))
        else:
            value = read_value(found.words, field.kind, given.get("value"), given.get("unit"))
        if value is None:
            return {"status": "unread", **record, "said": said}
        if field.kind == "count":
            value = int(value)
        return {"status": "stated", "value": value, "unit": CANONICAL[field.kind], **record}
    if field.kind == "flag":
        value = given.get("value")
        if isinstance(value, str):
            value = value.strip().lower() in ("true", "yes", "y", "1")
        words = _flag_read(found.words, name)
        if words is None or words != bool(value):
            return {"status": "unread", **record, "said": said}
        return {"status": "stated", "value": words, **record}
    values = given.get("value")
    items = [str(v) for v in values] if isinstance(values, list) else [str(values or "")]
    items = [item.strip() for item in items if item and item.strip()]
    held = [item for item in items if _held_by(item, found.words, name)]
    if not held:
        return {"status": "unread", **record, "said": said}
    value_out: Any = held if field.kind == "names" else held[0]
    return {"status": "stated", "value": value_out, **record}


#: What a count is of, which its number must sit beside to be its count:
#: "2" in "with a 2 fs time step" is no number of replicas.
_COUNTED = {
    "replicas": r"replica|repeat|repetition|run|simulation|trajector|independent|seed"
                r"|cop(?:y|ies)|duplicate|triplicate|quadruplicate|quintuplicate|times",
    "n": r"replica|repeat|run|simulation|trajector|independent|seed|block|cop(?:y|ies)",
    "copies": r"cop(?:y|ies)|molecule|monomer|chain|peptide|protein|protomer|subunit"
              r"|dimer|trimer|oligomer|unit",
}

#: How near, in characters, a count's number must be to what it counts.
_COUNTED_WITHIN = 40


def _count_read(words: str, name: str, given: Any) -> float | None:
    """The count of ``name`` the words hold: a number or number word beside
    what is counted ("three independent runs", "in triplicate", "3 x 100
    ns"), the AI model's where it is one of them, else the only one."""
    lowered = plain(words).lower()
    counted = _COUNTED.get(name)
    found: list[float] = []
    numbers = [(m.start(), m.end(), float(m.group(1)))
               for m in re.finditer(r"(?<![\w.])(\d+)(?![\w.]|\.\d)", lowered)]
    numbers += [(m.start(), m.end(), float(WORD_NUMBERS[m.group(1)]))
                for m in re.finditer(r"\b(" + "|".join(WORD_NUMBERS) + r")\b", lowered)]
    for start, end, value in numbers:
        if re.search(r"(?:table|fig\.?|figure|panel|eq\.?|ref\.?|section|chain|residue|"
                     r"model)\s*$", lowered[:start]):
            continue  # a label's number: Table 3, Fig. 2
        if re.match(r"\s*-?\s*(?:frames?|snapshots?|steps?|structures|conformations|"
                    r"configurations|[pnfum]s\b|k\b|bar\b|nm\b|a\b|%|degrees?)",
                    lowered[end:]):
            continue  # a number of frames, or a quantity: no count of runs
        # What it counts follows it, at most two words on ("three independent
        # runs"), or names it just before ("replicas: 3", "n = 3 runs").
        after = re.match(r"(?:\W+[\w-]+){0,2}?\W+(?:" + (counted or ".") + r")", lowered[end:])
        before = re.search(r"(?:" + (counted or ".") + r")\w*\W{1,6}(?:n\s*=\s*)?$",
                           lowered[max(0, start - _COUNTED_WITHIN):start])
        times = name == "replicas" and re.match(r"\s*x\s*\d", lowered[end:])
        if counted is None or times or after or before \
                or re.search(r"plicate", lowered[start:end]):
            found.append(value)
    if not found:
        return None
    try:
        wanted = float(given) if given not in (None, "") and not isinstance(given, bool) else None
    except (TypeError, ValueError):
        wanted = None
    if wanted is not None:
        return wanted if wanted in found else None
    return found[0] if len(set(found)) == 1 else None


#: The words a flag is read from, and those that make it false.
_FLAG_WORDS = {"neutralized": r"neutrali[sz]|neutral\b|counter-?ions?|zero net charge"}
#: A denial of the flag: a negation at most three words before its word.
_NEGATED = (r"(?:\b(?:not|no|without|never)\b|n't)(?:\W+\w+){0,3}?\W+(?:neutrali|counter)"
            r"|(?:neutrali|counter-?ion)\w*(?:\W+\w+){0,2}?\W+(?:not|never)\b")


def _flag_read(words: str, name: str) -> bool | None:
    """What the words say of a yes-or-no setting: None where they do not
    speak of it, else whether they say it was done."""
    lowered = plain(words).lower()
    pattern = _FLAG_WORDS.get(name)
    if pattern is None or not re.search(pattern, lowered):
        return None
    return not re.search(_NEGATED, lowered)


_THREE = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q",
          "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K",
          "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
          "TYR": "Y", "VAL": "V"}
_ONE = {one: three for three, one in _THREE.items()}


def mutation_forms(mutation: str) -> set[str]:
    """The ways a point mutation is written: D189N, Asp189Asn, ASP-189-ASN."""
    text = plain(mutation).strip()
    match = re.fullmatch(r"([A-Za-z]{1,3})[\s-]*(\d+)[\s-]*([A-Za-z]{1,3})", text)
    if not match:
        return {text}
    before, number, after = match.groups()
    def letters(code: str) -> str | None:
        upper = code.upper()
        if len(upper) == 1 and upper in _ONE:
            return upper
        return _THREE.get(upper)
    first, last = letters(before), letters(after)
    if not first or not last:
        return {text}
    return {f"{first}{number}{last}", f"{_ONE[first]}{number}{_ONE[last]}"}


def _held_by(item: str, words: str, name: str) -> bool:
    """Whether a name the AI model gave is in the words it was read from.

    A choice among known names (a force field, a water model, a thermostat,
    a box) is held where every one the AI model's value names is named by
    the words too, however each spells it. A description (the system, how
    it was minimised) is held where its words are the paper's, which
    :func:`check_field` has found: the description is the AI model's, and
    is never set as a value."""
    from fastmdxplora.paper.mapping import method_said_by, recognized

    hay = squash(words)[0]
    if name == "mutations":
        return any(squash(form)[0] in hay for form in mutation_forms(item))
    if name == "method":
        # Plain MD is what a study is unless its words say otherwise.
        return item.strip().lower() == "plain" or method_said_by(item, words)
    if name == "pdb_id":
        return bool(re.fullmatch(r"[0-9][A-Za-z0-9]{3}", item.strip())) and \
            item.strip().lower() in hay
    if name == "chains":
        return all(letter.lower() in hay for letter in re.findall(r"\b[A-Za-z]\b", item)) \
            and bool(re.findall(r"\b[A-Za-z]\b", item))
    said = recognized(name, item)
    if said is None:
        return True
    if not said:
        reduced = squash(item)[0]
        return bool(reduced) and reduced in hay
    return said <= (recognized(name, words) or set())


def check_claim(index: QuoteIndex, claim: dict[str, Any]) -> dict[str, Any]:
    """A result the AI model gave, checked: its words found in the paper,
    and its value and error among the numbers they hold. A result whose
    value its words do not hold is kept as said and not compared."""
    quote = str(claim.get("quote") or "").strip()
    quantity = str(claim.get("quantity") or "other").strip().lower()
    if quantity not in CLAIM_QUANTITIES:
        quantity = "other"
    kind = str(claim.get("error_kind") or "unstated").strip().lower()
    if kind not in ERROR_KINDS:
        kind = "unstated"
    out: dict[str, Any] = {
        "quantity": quantity,
        "analysis": CLAIM_QUANTITIES[quantity],
        "what": str(claim.get("what") or "")[:300],
        "unit": str(claim.get("unit") or "")[:40],
        "error_kind": kind,
        "n": claim.get("n") if isinstance(claim.get("n"), int) and claim.get("n") > 0 else None,
    }
    found = index.find(quote) if quote else None
    value = _number(claim.get("value"))
    error = _number(claim.get("error"))
    if found is None:
        return {**out, "status": "not_found", "said": {"value": claim.get("value"),
                                                       "error": claim.get("error"),
                                                       "quote": quote[:300]}}
    held = numbers_in(found.words)
    if value is None or not any(_close(value, number) for number in held):
        return {**out, "status": "unread", "quote": found.words, "where": found.label,
                "said": {"value": claim.get("value"), "error": claim.get("error")}}
    if error is not None and not any(_close(abs(error), abs(number)) for number in held):
        error = None
        out["error_kind"] = "unstated"
        out["error_unread"] = True
    # What the error is, and over how many runs, decide the verdict: each is
    # kept only where the part the result is in says it. A spread whose
    # kind is not said is compared as a spread, never as an error of a mean.
    where_said = found.context or found.words
    if out["error_kind"] in _ERROR_WORDS and not re.search(
            _ERROR_WORDS[out["error_kind"]], plain(where_said).lower()):
        out["error_kind"] = "unstated"
    if out["n"] is not None and _count_read(where_said, "n", out["n"]) is None:
        out["n"] = None
    # A unit is often in a table's head, not its row: it is looked for in
    # the part the words are in. A unit found nowhere is not trusted, and
    # the result is not compared, since a nanometre read as an angstrom is
    # ten times off.
    unit = out["unit"]
    unit_found = (not unit or _unit_key(unit) in _DIMENSIONLESS
                  or _unit_in(unit, found.context or found.words))
    return {**out, "status": "stated" if unit_found else "unread",
            **({} if unit_found else {"unit_unread": True}),
            "value": value, "error": error, "quote": found.words, "where": found.label}


#: Units that are no unit: a count or a share. A share's scale is told
#: from its value where it is compared.
_DIMENSIONLESS = {"", "%", "percent", "count", "counts", "fraction", "number", "none"}


def _unit_key(text: str) -> str:
    """A unit as it is written, reduced so that ``kcal mol-1`` and
    ``kcal/mol`` are one: lower case, accents and spaces set aside."""
    reduced = plain(text).lower()
    reduced = re.sub(r"\s*(mol|s|nm|m|l)\s*\^?\s*-\s*1\b", r"/\1", reduced)
    reduced = re.sub(r"\s*/\s*", "/", reduced)
    return re.sub(r"[\s^·*]+", "", reduced)


def _unit_in(unit: str, text: str) -> bool:
    """Whether ``unit`` is written in ``text`` as a unit: whole, not as the
    letter it shares with a word (``Å``, set aside as ``A``, is in every
    sentence)."""
    key = _unit_key(unit)
    if not key:
        return True
    if len(key) == 1:
        # A one-letter unit (Å, K, M) is looked for as written, never as the
        # letter: "L50A" holds no ångström.
        import unicodedata

        written = unicodedata.normalize("NFC", str(unit).strip())
        forms = {written}
        if key == "a":
            forms |= {"\u00c5", "A\u02da", "angstrom", "Angstrom"}
        source = unicodedata.normalize("NFC", text)
        return any(re.search(r"(?<![A-Za-z])" + re.escape(form) + r"(?![A-Za-z])", source)
                   for form in forms)
    hay = plain(text).lower()
    hay = re.sub(r"\s*(mol|s|nm|m|l)\s*\^?\s*-\s*1\b", r"/\1", hay)
    hay = re.sub(r"\s*/\s*", "/", hay)
    hay = re.sub(r"[\^·*]", "", hay)
    pattern = r"(?<![a-z])" + r"\s*".join(re.escape(char) for char in key) + r"(?![a-z])"
    return re.search(pattern, hay) is not None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        numbers = [n for n in numbers_in(value) if math.isfinite(n)]
        return numbers[0] if len(numbers) == 1 else None
    return None


#: The words that say what a result's error is.
_ERROR_WORDS = {
    "standard_error": r"standard errors?|\bs\.e\.(?:m\.?)?|\bsem\b|error of the mean",
    "standard_deviation": r"standard deviations?|\bs\.d\.|\bsd\b|\bstd\b",
    "confidence_95": r"confidence|95\s*%\s*(?:c\.?i\b|interval)",
}


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 + 1e-6 * max(abs(a), abs(b))
