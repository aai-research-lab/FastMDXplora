"""Find a structure by its name: the PDB's entries, and AlphaFold DB's models.

A study names a structure by its PDB identifier, and a person names it in
words: "lysozyme", "trp-cage", "the β2 adrenergic receptor". The Agent was
given a short list of names written into its instructions, and an AI model
left to the rest recalls identifiers from memory, which is how a study of the
wrong molecule validates perfectly. This asks the PDB instead.

**Grouped by protein, most studied first.** A name matches many entries:
"lysozyme" names 2,468 polymer entities in the PDB, from hen egg white (1,398
of them), phage T4 (617), human (215) and others. The entries are counted by
the UniProt accession of their protein, the proteins the PDB holds most
structures of come first, and each is offered by its best-resolved entry of
the protein alone (no mutation, no fusion, no other protein in it), and by
the first such entry determined where that is another: the first is often
the one a field simulates (1VII for the villin headpiece). Where the name
matches fewer proteins than are asked for (a designed peptide such as
trp-cage has no UniProt protein), the other entries it matches are offered,
the first determined first: 1L2Y before its variants.

**The name or the title, then any word.** A name is matched against the
names the entries give their molecules and their titles (1L2Y names its
molecule "TC5b" and is titled a Trp-cage); where that finds nothing,
against any text of the entry.

**A model where nothing was determined.** Where the PDB holds no structure,
or where asked, the AlphaFold DB models of the UniProt entries the name
matches are offered, each with its mean pLDDT and the file to fetch: a
model is a prediction, said as one.

**Never a guess.** Nothing here is written into a study, and a search that
cannot reach the services, or is answered with an error, says so, for the
person to name the structure; an error is never read as "no entry". One
search takes at most a minute in all. Answers are kept for seven days, so a
conversation does not ask twice.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import CodedError

__all__ = ["Entry", "Found", "Model", "SearchUnreachable", "find_structures", "said"]

SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
DATA = "https://data.rcsb.org/graphql"
UNIPROT = "https://rest.uniprot.org/uniprotkb/search"
ALPHAFOLD = "https://alphafold.ebi.ac.uk/api/prediction/"

#: How long an answer is kept, in seconds.
KEPT_FOR = 7 * 24 * 3600
#: How long one request may take, and one search in all, in seconds.
TIMEOUT = 20.0
BUDGET = 60.0
#: The most one answer may hold, in bytes.
MOST_BYTES = 8_000_000
MOST = 10
_AGENT = "FastMDXplora (https://github.com/aai-research-lab/FastMDXplora)"
_PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")
_UNIPROT_ACCESSION = re.compile(
    r"^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})$")
_ACCESSION = ("rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers"
              ".database_accession")
_ORGANISM = "rcsb_entity_source_organism.scientific_name"


class SearchUnreachable(CodedError):
    """The services could not be reached, or answered in a shape not read."""

    default_code = "environment.service.unreachable"


@dataclass(frozen=True)
class Entry:
    """A PDB entry, as a person chooses between them."""

    pdb_id: str
    title: str = ""
    method: str = ""
    resolution_A: float | None = None
    year: str = ""
    organisms: tuple[str, ...] = ()
    #: Each protein or nucleic acid chain: (chain IDs, residues, what it is).
    chains: tuple[tuple[str, int | None, str], ...] = ()
    #: Each ligand or ion: (its code, its name).
    ligands: tuple[tuple[str, str], ...] = ()
    #: The UniProt accession it was grouped by, and how many of the molecules
    #: the name matched are of it.
    accession: str = ""
    entries_of_it: int | None = None
    #: Whether it holds the protein alone (unmutated, unfused, no other
    #: protein); False where the protein has no such entry the name matches.
    alone: bool | None = None
    #: The first such entry determined, where it is another.
    first: Entry | None = None
    #: Every UniProt accession its molecules are of.
    accessions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Model:
    """An AlphaFold DB model: a prediction, not a determined structure."""

    model_id: str
    accession: str
    protein: str = ""
    organism: str = ""
    mean_plddt: float | None = None
    residues: int | None = None
    pdb_url: str = ""


@dataclass(frozen=True)
class Found:
    """What a search found, and how it was looked for."""

    query: str
    organism: str = ""
    entries: tuple[Entry, ...] = ()
    models: tuple[Model, ...] = ()
    #: Polymer entities the name matched in all, before grouping.
    matched: int = 0
    #: How the name was matched: by the molecules' names, or by any text.
    matched_by: str = ""
    #: Why AlphaFold DB's models could not be fetched, where they were asked
    #: for and the PDB's entries were found.
    models_missing: str = ""


# ---------------------------------------------------------------------------
# Asking, kept
# ---------------------------------------------------------------------------
#: When the search under way must end, by the monotonic clock.
_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "structure_search_deadline", default=None)


def _cache_dir() -> Path:
    override = os.environ.get("FASTMDXPLORA_CACHE_DIR")
    base = Path(override) if override else Path.home() / ".cache" / "fastmdxplora"
    return base / "structure_search"


def _asked(method: str, url: str, body: Any = None) -> Any:
    """One request, answered from the cache where it was asked within a
    week. JSON in, JSON out; None for an answer with no content (the
    search's way of saying nothing matched). An error the service answers
    with is raised, never kept."""
    key = hashlib.sha256(json.dumps([method, url, body], sort_keys=True).encode()).hexdigest()
    kept = _cache_dir() / f"{key[:32]}.json"
    try:
        if time.time() - kept.stat().st_mtime < KEPT_FOR:
            return json.loads(kept.read_text(encoding="utf-8"))["answer"]
    except (OSError, ValueError, KeyError):
        pass
    timeout = TIMEOUT
    deadline = _deadline.get()
    if deadline is not None:
        timeout = min(TIMEOUT, deadline - time.monotonic())
        if timeout <= 0:
            raise SearchUnreachable(
                f"The search took longer than {BUDGET:g} s in all, waiting on "
                f"{urllib.parse.urlsplit(url).netloc}.", url=url)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"User-Agent": _AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MOST_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            detail = exc.read(300).decode("utf-8", "replace")
            raise SearchUnreachable(f"{url} refused the search ({exc.code}): {detail}",
                                    url=url) from None
        raw = b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SearchUnreachable(f"{urllib.parse.urlsplit(url).netloc} could not be "
                                f"reached: {reason}", url=url) from None
    if len(raw) > MOST_BYTES:
        raise SearchUnreachable(f"{urllib.parse.urlsplit(url).netloc} answered with more "
                                f"than {MOST_BYTES // 1_000_000} MB.",
                                code="environment.service.unusable_response", url=url)
    try:
        answer = json.loads(raw) if raw.strip() else None
    except ValueError:
        raise SearchUnreachable(f"{urllib.parse.urlsplit(url).netloc} answered in a shape "
                                "that could not be read.",
                                code="environment.service.unusable_response",
                                url=url) from None
    if isinstance(answer, dict) and answer.get("errors"):
        # The PDB's data service answers a query it cannot run with 200 and
        # `errors`: read as no entry, 1L2Y would be "not in the PDB" for a week.
        first = answer["errors"][0] if isinstance(answer["errors"], list) else answer["errors"]
        message = first.get("message") if isinstance(first, dict) else first
        raise SearchUnreachable(f"{urllib.parse.urlsplit(url).netloc} answered with an "
                                f"error: {str(message)[:200]}",
                                code="environment.service.unusable_response", url=url)
    try:
        kept.parent.mkdir(parents=True, exist_ok=True)
        # Written beside and moved into place, so two searches at once never
        # read a half-written answer.
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=kept.parent,
                                         suffix=".part", delete=False) as part:
            json.dump({"url": url, "answer": answer}, part)
        os.replace(part.name, kept)
    except OSError:  # pragma: no cover - a cache that cannot be written is skipped
        pass
    return answer


# ---------------------------------------------------------------------------
# The PDB
# ---------------------------------------------------------------------------
def _terminal(attribute: str, operator: str, value: Any) -> dict[str, Any]:
    return {"type": "terminal", "service": "text",
            "parameters": {"attribute": attribute, "operator": operator, "value": value}}


def _matching(query: str, organism: str, by: str) -> dict[str, Any]:
    """What a name matches: ``name``, the names entries give their molecules;
    ``title``, those or the entries' titles; ``text``, any text of them."""
    named_so = _terminal("rcsb_polymer_entity.pdbx_description", "contains_phrase", query)
    if by == "name":
        named: dict[str, Any] = named_so
    elif by == "title":
        named = {"type": "group", "logical_operator": "or", "nodes": [
            named_so, _terminal("struct.title", "contains_phrase", query)]}
    else:
        named = {"type": "terminal", "service": "full_text", "parameters": {"value": query}}
    if not organism:
        return named
    return {"type": "group", "logical_operator": "and",
            "nodes": [named, _terminal(_ORGANISM, "exact_match", organism)]}


def _search(query: dict[str, Any], *, rows: int, sort: str = "score",
            ascending: bool = False, facet: bool = False,
            return_type: str = "polymer_entity") -> dict[str, Any] | None:
    options: dict[str, Any] = {
        "paginate": {"start": 0, "rows": rows},
        "results_content_type": ["experimental"],
        "sort": [{"sort_by": sort, "direction": "asc" if ascending else "desc"}],
    }
    if facet:
        options["facets"] = [{"name": "by_protein", "aggregation_type": "terms",
                              "attribute": _ACCESSION, "max_num_intervals": MOST}]
    return _asked("POST", SEARCH, {"query": query, "return_type": return_type,
                                   "request_options": options})


def _first_hit(nodes: list[dict[str, Any]], sort: str) -> str | None:
    answer = _search({"type": "group", "logical_operator": "and", "nodes": nodes},
                     rows=1, sort=sort, ascending=True)
    hits = (answer or {}).get("result_set") or []
    return str(hits[0]["identifier"]).split("_")[0].upper() if hits else None


def _representative(accession: str,
                    matching: dict[str, Any]) -> tuple[str, bool, str | None] | None:
    """The best-resolved entry of a protein that the name names, alone,
    unmutated and not fused to another, and the first such entry determined;
    failing those, its best-resolved entry the name names, said not to be
    alone. The name is held to as well as the protein: the best-resolved
    entry of the β2 adrenergic receptor's accession is a fragment of its
    tail bound to another protein."""
    accession_is = _terminal(_ACCESSION, "exact_match", accession)
    alone = [accession_is, matching,
             _terminal("entity_poly.rcsb_mutation_count", "equals", 0),
             _terminal("rcsb_entry_info.polymer_entity_count_protein", "equals", 1),
             _terminal("rcsb_polymer_entity.rcsb_source_part_count", "equals", 1)]
    best = _first_hit(alone, "rcsb_entry_info.resolution_combined")
    if best is not None:
        first = _first_hit(alone, "rcsb_accession_info.initial_release_date")
        return best, True, (first if first != best else None)
    best = _first_hit([accession_is, matching], "rcsb_entry_info.resolution_combined")
    return (best, False, None) if best is not None else None


_DETAILS = """
query($ids: [String!]!) { entries(entry_ids: $ids) {
  rcsb_id struct { title } exptl { method }
  rcsb_entry_info { resolution_combined }
  rcsb_accession_info { initial_release_date }
  polymer_entities {
    rcsb_polymer_entity { pdbx_description }
    entity_poly { rcsb_sample_sequence_length pdbx_strand_id }
    rcsb_entity_source_organism { scientific_name }
    rcsb_polymer_entity_container_identifiers {
      reference_sequence_identifiers { database_accession database_name } } }
  nonpolymer_entities { nonpolymer_comp { chem_comp { id name } } }
} }"""


def _as_named(organism: str) -> str:
    """A species as it is written: older entries give it in capitals."""
    if organism.isupper():
        return organism[:1] + organism[1:].lower()
    return organism


def _details(pdb_ids: list[str]) -> dict[str, Entry]:
    """Each entry as a person reads it, from the PDB's data service."""
    if not pdb_ids:
        return {}
    answer = _asked("POST", DATA, {"query": _DETAILS, "variables": {"ids": pdb_ids}})
    found: dict[str, Entry] = {}
    for raw in ((answer or {}).get("data") or {}).get("entries") or []:
        if not raw:
            continue
        info = raw.get("rcsb_entry_info") or {}
        resolution = info.get("resolution_combined") or []
        organisms: list[str] = []
        accessions: list[str] = []
        chains = []
        for entity in raw.get("polymer_entities") or []:
            for source in entity.get("rcsb_entity_source_organism") or []:
                name = _as_named(str(source.get("scientific_name") or ""))
                if name and name not in organisms:
                    organisms.append(name)
            for ref in ((entity.get("rcsb_polymer_entity_container_identifiers") or {})
                        .get("reference_sequence_identifiers") or []):
                accession = str(ref.get("database_accession") or "")
                if (accession and str(ref.get("database_name") or "UniProt") == "UniProt"
                        and accession not in accessions):
                    accessions.append(accession)
            poly = entity.get("entity_poly") or {}
            chains.append((str(poly.get("pdbx_strand_id") or ""),
                           poly.get("rcsb_sample_sequence_length"),
                           str((entity.get("rcsb_polymer_entity") or {})
                               .get("pdbx_description") or "")))
        ligands = []
        for entity in raw.get("nonpolymer_entities") or []:
            comp = ((entity.get("nonpolymer_comp") or {}).get("chem_comp") or {})
            if comp.get("id"):
                ligands.append((str(comp["id"]), str(comp.get("name") or "")))
        methods = [str(m.get("method") or "") for m in raw.get("exptl") or []]
        found[str(raw.get("rcsb_id")).upper()] = Entry(
            pdb_id=str(raw.get("rcsb_id")).upper(),
            title=str((raw.get("struct") or {}).get("title") or ""),
            method=", ".join(m for m in methods if m),
            resolution_A=float(resolution[0]) if resolution else None,
            year=str((raw.get("rcsb_accession_info") or {})
                     .get("initial_release_date") or "")[:4],
            organisms=tuple(organisms), chains=tuple(chains), ligands=tuple(ligands),
            accessions=tuple(accessions))
    return found


def _entries(query: str, organism: str, most: int) -> tuple[list[Entry], int, str]:
    """The entries offered for a name, the molecules it matched, and how.

    Proteins are counted among the molecules named so, not among every
    molecule of an entry whose title has the name: a Trp-cage chimera's
    title would make exendin-4 a protein the name answers to."""
    for by in ("name", "title", "text"):
        matching = _matching(query, organism, by)
        answer = _search(matching, rows=1, facet=True)
        if answer and answer.get("total_count"):
            break
    else:
        return [], 0, ""
    matched = int(answer.get("total_count") or 0)
    buckets = []
    if by == "name":
        for facet in answer.get("facets") or []:
            buckets += [(str(b["label"]), int(b.get("population") or 0))
                        for b in facet.get("buckets") or [] if b.get("label")]
    # (pdb id, accession, entries of it, alone, the first determined)
    chosen: list[tuple[str, str, int | None, bool | None, str | None]] = []
    # Each protein's entries looked for among those whose molecule or title
    # names it: 1VII, the villin headpiece most simulated, names its molecule
    # "villin".
    titled = _matching(query, organism, "title") if by == "name" else matching
    for accession, count in buckets[:most]:
        found = _representative(accession, titled)
        if found and found[0] not in {c[0] for c in chosen}:
            chosen.append((found[0], accession, count, found[1], found[2]))
    # Fewer proteins than asked for (a designed peptide has none): the other
    # entries the name matches in their molecules or their titles (1L2Y names
    # its molecule TC5b), the first determined first, leaving out one of a
    # protein already offered.
    others: list[str] = []
    if len(chosen) < most:
        earliest = _search(titled, rows=3 * most, return_type="entry",
                           sort="rcsb_accession_info.initial_release_date", ascending=True)
        offered = {c[0] for c in chosen}
        others = [str(h["identifier"]).upper() for h in (earliest or {}).get("result_set") or []
                  if str(h["identifier"]).upper() not in offered]
    details = _details(list(dict.fromkeys(
        [c[0] for c in chosen] + [c[4] for c in chosen if c[4]] + others)))
    entries = []
    for pdb_id, accession, count, alone, first in chosen:
        entry = details.get(pdb_id) or Entry(pdb_id)
        entries.append(Entry(**{**entry.__dict__, "accession": accession,
                                "entries_of_it": count, "alone": alone,
                                "first": details.get(first) if first else None}))
    grouped = {c[1] for c in chosen}
    for pdb_id in others:
        if len(entries) >= most:
            break
        entry = details.get(pdb_id) or Entry(pdb_id)
        if grouped & set(entry.accessions):
            continue
        entries.append(entry)
    return entries, matched, by


# ---------------------------------------------------------------------------
# AlphaFold DB
# ---------------------------------------------------------------------------
def _phrase(text: str) -> str:
    """Text as one phrase of UniProt's query language: its brackets, colons
    and quotes are the name's, not the language's."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _models(query: str, organism: str, most: int) -> list[Model]:
    """AlphaFold DB's models of the UniProt entries the name matches,
    reviewed entries first."""
    if _UNIPROT_ACCESSION.match(query.strip()):
        accessions = [query.strip()]
    else:
        search = _phrase(query) + (f" AND organism_name:{_phrase(organism)}" if organism else "")
        rows: list[dict[str, Any]] = []
        # Reviewed entries (Swiss-Prot) first; the unreviewed only where
        # there is none: a name's first unreviewed match is often a fragment.
        for kept in (" AND reviewed:true", ""):
            params = urllib.parse.urlencode({
                "query": search + kept, "fields": "accession,protein_name,organism_name",
                "format": "json", "size": str(most)})
            rows = (_asked("GET", f"{UNIPROT}?{params}") or {}).get("results") or []
            if rows:
                break
        accessions = [str(r.get("primaryAccession")) for r in rows if r.get("primaryAccession")]
    models = []
    for accession in accessions[:most]:
        for raw in _asked("GET", ALPHAFOLD + urllib.parse.quote(accession)) or []:
            start, end = raw.get("uniprotStart"), raw.get("uniprotEnd")
            models.append(Model(
                model_id=str(raw.get("entryId") or raw.get("modelEntityId") or ""),
                accession=str(raw.get("uniprotAccession") or accession),
                protein=str(raw.get("uniprotDescription") or ""),
                organism=str(raw.get("organismScientificName") or ""),
                mean_plddt=raw.get("globalMetricValue"),
                residues=(end - start + 1) if isinstance(start, int)
                and isinstance(end, int) else None,
                pdb_url=str(raw.get("pdbUrl") or "")))
            break
    return models


# ---------------------------------------------------------------------------
# The one door
# ---------------------------------------------------------------------------
def find_structures(query: str, *, organism: str = "", most: int = 5,
                    predicted: bool = False) -> Found:
    """The structures a name or a PDB identifier answers to. Raises
    SearchUnreachable where the PDB cannot be reached or answers with an
    error, or the whole takes longer than :data:`BUDGET`; nothing is
    guessed in its place."""
    query = " ".join(str(query or "").split())
    organism = " ".join(str(organism or "").split())
    most = max(1, min(int(most or 5), MOST))
    if not query:
        return Found(query)
    token = _deadline.set(time.monotonic() + BUDGET)
    try:
        if _PDB_ID.match(query):
            entry = _details([query.upper()]).get(query.upper())
            return Found(query, organism, (entry,) if entry else (),
                         matched=int(bool(entry)), matched_by="identifier")
        entries, matched, by = _entries(query, organism, most)
        models: list[Model] = []
        missing = ""
        if predicted or not entries:
            try:
                models = _models(query, organism, most)
            except SearchUnreachable as exc:
                if not entries:
                    raise
                missing = str(exc)
        return Found(query, organism, tuple(entries), tuple(models), matched, by, missing)
    finally:
        _deadline.reset(token)


def _resolution(entry: Entry) -> str:
    return f"{entry.resolution_A:g} Å" if entry.resolution_A is not None else ""


#: Fewer molecules than this matched by name, and another name is suggested.
FEW = 5
_FEW = ("Few entries name their molecule so: the PDB may name it otherwise (BPTI "
        "as pancreatic trypsin inhibitor, beta2 as beta-2), so look again by "
        "another name before you take one of these. ")


def _in_order(entries: tuple[Entry, ...]) -> str:
    grouped = [e for e in entries if e.entries_of_it is not None]
    if not grouped:
        return "The entries the name matches, the first determined first:"
    return ("By protein, the most studied first, each by its best-resolved entry of the "
            "protein alone (unmutated, unfused, with no other protein), and the first "
            "such entry determined where that is another; one marked not alone has no "
            "such entry"
            + (", then other entries the name matches, the first determined first:"
               if len(grouped) < len(entries) else ":"))


def _short(text: str, most: int) -> str:
    return text if len(text) <= most else text[:most - 3].rstrip() + "..."


def _counted(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


#: How many chains or ligands an entry is described by before the rest are
#: counted: a ribosome has dozens of each.
_SHOWN = 8


def _at_most(items: list[str], joined_by: str, rest: str) -> str:
    if len(items) <= _SHOWN:
        return joined_by.join(items)
    return joined_by.join(items[:_SHOWN]) + f"{joined_by}and {len(items) - _SHOWN} {rest}"


def _facts(entry: Entry) -> str:
    facts = [f for f in (entry.method.title() if entry.method else "", _resolution(entry),
                         entry.year) if f]
    return ", ".join(facts)


def said(found: Found) -> str:
    """What a search found, as the Agent and an AI app are told it, and as a
    person reads it under the answer."""
    lines: list[str] = []
    where = f" in {found.organism}" if found.organism else ""
    if found.matched_by == "identifier":
        if not found.entries:
            return f"The PDB has no entry {found.query.upper()}."
        lines.append(f"PDB entry {found.query.upper()}:")
    elif found.entries:
        how = {"name": "by the names the entries give their molecules",
               "title": "by the names the entries give their molecules or their titles"
               }.get(found.matched_by, "by any text of the entry")
        molecules = _counted(found.matched, "molecule", "molecules")
        lines.append(f"\"{found.query}\"{where} matches {molecules} in the PDB, "
                     f"{how}. Where more than one could be meant, ask the person which, "
                     "naming these; name the identifier chosen back to them. "
                     + (_FEW if found.matched_by == "name" and found.matched < FEW else "")
                     + _in_order(found.entries))
    elif not found.models:
        return (f"The PDB and AlphaFold DB hold nothing that \"{found.query}\"{where} "
                "names. Ask the person for the PDB identifier or a structure file.")
    for entry in found.entries:
        facts = _facts(entry)
        head = f"- {entry.pdb_id}: {_short(entry.title.rstrip('.'), 140)}." + (
            f" {facts}." if facts else "")
        if entry.alone is False:
            head += (" Not alone: the PDB has no entry of this protein alone, unmutated "
                     "and unfused that the name matches, so this is its best-resolved "
                     "entry of any kind.")
        if entry.organisms:
            head += f" {', '.join(entry.organisms)}."
        chains = _at_most([
            f"{ids or '?'} ({length} residues, {what})" if length else f"{ids or '?'} ({what})"
            for ids, length, what in entry.chains], "; ", "chains")
        if chains:
            head += f" Chains: {chains}."
        head += (" Ligands and ions: " + _at_most(
            [f"{code} ({_short(name.lower(), 60)})" for code, name in entry.ligands],
            ", ", "more") + "."
                 if entry.ligands else " No ligands or ions.")
        if entry.accession:
            head += (f" UniProt {entry.accession}"
                     + (f", {_counted(entry.entries_of_it, 'molecule', 'molecules')} "
                        "named so" if entry.entries_of_it else "")
                     + ".")
        elif entry.accessions:
            head += f" UniProt {', '.join(entry.accessions)}."
        if entry.first is not None:
            first = _facts(entry.first)
            head += (f" The first entry of it alone: {entry.first.pdb_id}, "
                     f"{_short(entry.first.title.rstrip('.'), 100)}"
                     + (f" ({first})" if first else "") + ".")
        lines.append(head)
    if found.models_missing:
        lines.append(f"AlphaFold DB's models could not be fetched: {found.models_missing}")
    if found.models:
        lines.append("" if found.entries else
                     f"The PDB holds no structure that \"{found.query}\"{where} names.")
        lines.append("AlphaFold DB's predicted models (a prediction, not a determined "
                     "structure; mean pLDDT out of 100: above 90 very high confidence, "
                     "70 to 90 confident, 50 to 70 low, below 50 very low):")
        for model in found.models:
            plddt = f"{model.mean_plddt:.1f}" if isinstance(model.mean_plddt, (int, float)) else "?"
            lines.append(
                f"- {model.model_id}: {model.protein}, {model.organism}"
                + (f", {model.residues} residues" if model.residues else "")
                + f", mean pLDDT {plddt}. The file, for the person to download into the "
                  f"workspace and name as the system: {model.pdb_url}")
    return "\n".join(lines)
