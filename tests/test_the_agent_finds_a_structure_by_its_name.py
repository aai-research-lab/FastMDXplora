"""The Agent finds a structure named in words, never recalls one.

The Agent was given six names in its instructions (trp-cage is 1L2Y, ...)
and an AI model left to recall the rest: "trpcage" was once written as
1UAO, which is chignolin, and the study validated perfectly. Now it looks:
`find_structure` asks the PDB, groups the entries a name matches by
protein, offers each by its best-resolved entry with what it holds, and
offers AlphaFold DB's models where asked or where the PDB has none. It is
the Agent's tool and `fastmdx mcp`'s.

The services are a stand-in here, answering with replies recorded from the
real ones (tests/data/structure_search/replies.json): no test reaches the
network.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import fastmdxplora.structure_search as search

_REPLIES = Path(__file__).parent / "data" / "structure_search" / "replies.json"


def _key(method: str, path: str, query: str, body) -> str:
    return json.dumps([method, path, query, body], sort_keys=True)


class _StandIn(BaseHTTPRequestHandler):
    replies: dict[str, object] = {}
    asked: list[tuple[str, str, object]] = []

    def _answer(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        parts = urllib.parse.urlsplit(self.path)
        self.asked.append((method, parts.path, body))
        key = _key(method, parts.path, parts.query, body)
        if key not in self.replies:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"not recorded: " + key.encode()[:400])
            return
        answer = self.replies[key]
        if answer is None:
            self.send_response(204)
            self.end_headers()
            return
        raw = json.dumps(answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802 - the handler's name
        self._answer("GET")

    def do_POST(self):  # noqa: N802
        self._answer("POST")

    def log_message(self, *args):  # pragma: no cover - quiet
        pass


@pytest.fixture
def services(monkeypatch, tmp_path):
    """The four services, answered by a stand-in from recorded replies."""
    replies = {}
    for one in json.loads(_REPLIES.read_text(encoding="utf-8")):
        path = f"/{one['host']}{one['path']}"
        replies[_key(one["method"], path, one["query"], one["body"])] = one["answer"]
    _StandIn.replies, _StandIn.asked = replies, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StandIn)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(search, "SEARCH", f"{base}/search.rcsb.org/rcsbsearch/v2/query")
    monkeypatch.setattr(search, "DATA", f"{base}/data.rcsb.org/graphql")
    monkeypatch.setattr(search, "UNIPROT", f"{base}/rest.uniprot.org/uniprotkb/search")
    monkeypatch.setattr(search, "ALPHAFOLD", f"{base}/alphafold.ebi.ac.uk/api/prediction/")
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    yield _StandIn
    server.shutdown()
    server.server_close()


def test_a_name_is_offered_by_protein_the_most_studied_first(services):
    said = search.said(search.find_structures("lysozyme", most=2))
    assert said.startswith('"lysozyme" matches 2,468 molecules in the PDB')
    hen, phage = said.index("- 2VB1:"), said.index("- 3FA0:")
    assert hen < phage
    assert "Gallus gallus" in said and "Enterobacteria phage T4" in said
    assert "UniProt P00698, 1,398 molecules named so." in said
    # The first determined of each, where it is another: often the one simulated.
    assert "The first entry of it alone: 1LYZ" in said
    assert "The first entry of it alone: 2LZM" in said
    assert "0.65 Å" in said
    assert "ask the person which, naming these" in said


def test_each_protein_is_offered_by_an_entry_of_it_alone(services):
    search.find_structures("lysozyme", most=2)
    asked = [json.dumps(body) for method, _, body in services.asked if body]
    alone = [a for a in asked if "rcsb_source_part_count" in a]
    assert alone, "the representative is asked for unfused"
    assert all("rcsb_mutation_count" in a and "polymer_entity_count_protein" in a
               and "pdbx_description" in a for a in alone)


def test_a_designed_peptide_is_offered_first_determined_first(services):
    # 1L2Y names its molecule "TC5b": matched by the name alone it was never
    # offered, and a variant of another sequence (2JOF) was.
    said = search.said(search.find_structures("trp-cage", most=2))
    assert "The entries the name matches, the first determined first:" in said
    assert "By protein" not in said
    assert said.index("- 1L2Y:") < said.index("- 1RIJ:")
    assert "2JOF" not in said


def test_the_entry_a_field_simulates_is_offered_beside_the_best_resolved(services):
    # 1VII names its molecule "villin": its protein's entries are looked for
    # by title too, and the first determined is named.
    said = search.said(search.find_structures("villin headpiece", most=1))
    assert "- 2RJY:" in said
    assert "The first entry of it alone: 1VII" in said


def test_an_entry_not_of_the_protein_alone_is_said_so(services):
    said = search.said(search.find_structures(
        "GTPase KRas", organism="Homo sapiens", most=2, predicted=True))
    line = next(x for x in said.splitlines() if x.startswith("- 9GLZ:"))
    assert "Not alone: the PDB has no entry of this protein alone" in line
    six = next(x for x in said.splitlines() if x.startswith("- 6P0Z:"))
    assert "Not alone" not in six


def test_an_identifier_is_described_not_searched(services):
    said = search.said(search.find_structures("1l2y"))
    assert said.startswith("PDB entry 1L2Y:")
    assert "Solution Nmr" in said and "20 residues" in said


def test_a_predicted_model_is_said_as_one(services):
    said = search.said(search.find_structures(
        "GTPase KRas", organism="Homo sapiens", most=2, predicted=True))
    assert "- 6P0Z:" in said
    assert "a prediction, not a determined structure" in said
    assert "AF-P01116-F1" in said and "mean pLDDT 91.5" in said
    assert "for the person to download" in said


def test_nothing_found_is_said_and_nothing_offered(services):
    said = search.said(search.find_structures("zzqx not a molecule"))
    assert "hold nothing" in said
    assert "Ask the person for the PDB identifier or a structure file." in said


def test_an_answer_is_kept_and_not_asked_again(services):
    first = search.said(search.find_structures("trp-cage", most=2))
    count = len(services.asked)
    assert not list((search._cache_dir()).glob("*.part"))
    assert search.said(search.find_structures("trp-cage", most=2)) == first
    assert len(services.asked) == count


def test_unreachable_is_said_and_nothing_is_guessed(monkeypatch, tmp_path):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(search, "SEARCH", "http://127.0.0.1:9/nowhere")
    with pytest.raises(search.SearchUnreachable) as raised:
        search.find_structures("lysozyme")
    assert raised.value.code == "environment.service.unreachable"
    from fastmdxplora.agent.tools import Toolbox

    look = Toolbox().use("find_structure", {"query": "lysozyme"})
    assert not look.ok
    assert "could not be reached" in look.said
    assert "Nothing is guessed in its place" in look.said
    assert "1AKI" not in look.said


def test_the_agent_looks_with_it(services):
    from fastmdxplora.agent.tools import Toolbox

    box = Toolbox()
    assert "find_structure" in box.names
    spec = next(s for s in box.specs() if s.name == "find_structure")
    assert spec.parameters["required"] == ["query"]
    look = box.use("find_structure", {"query": "trp-cage", "most": 2})
    assert look.ok and "- 1L2Y:" in look.said
    refused = box.use("find_structure", {"query": ""})
    assert not refused.ok and "Name the structure as `query`" in refused.said
    assert "Find a structure named in words before you write" in box.guidance()


def test_an_ai_app_looks_with_it():
    from fastmdxplora.mcp.tools import TOOLS

    tool = next(t for t in TOOLS if t.name == "find_structure")
    assert tool.required == ("query",)
    assert tool.annotations.get("readOnlyHint") is True


def test_the_agent_is_told_to_look_not_recall():
    from fastmdxplora.agent.propose import prompt_for

    prompt = prompt_for("simulate trpcage")
    assert "trp-cage is 1L2Y" not in prompt
    assert "never one\nrecalled" in prompt or "never one recalled" in prompt


def test_few_matches_by_name_suggest_another_name():
    found = search.Found("BPTI", entries=(search.Entry("1CBW", title="chymotrypsin"),),
                         matched=1, matched_by="name")
    assert "look again by another name" in search.said(found)
    many = search.Found("lysozyme", entries=(search.Entry("2VB1", title="HEWL"),),
                        matched=2468, matched_by="name")
    assert "another name" not in search.said(many)


def test_long_lists_are_counted_and_capitals_written_as_a_species():
    ligands = tuple((f"L{n:02d}", "a ligand") for n in range(20))
    found = search.Found("ribosome", entries=(search.Entry("4V9D", title="A ribosome",
                                                           ligands=ligands),),
                         matched=40, matched_by="name")
    assert "and 12 more." in search.said(found)
    assert search._as_named("GALLUS GALLUS") == "Gallus gallus"
    assert search._as_named("Homo sapiens") == "Homo sapiens"


# ---------------------------------------------------------------------------
# What a service says when it cannot answer
# ---------------------------------------------------------------------------
class _Answers(BaseHTTPRequestHandler):
    """A service answering every request with one reply."""

    status = 200
    body = b"{}"
    delay = 0.0

    def _answer(self):
        import time

        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        time.sleep(self.delay)
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            self.wfile.write(self.body)
        except OSError:  # pragma: no cover - the asker gave up
            pass

    do_GET = do_POST = _answer  # noqa: N815

    def log_message(self, *args):  # pragma: no cover - quiet
        pass


@pytest.fixture
def one_answer(monkeypatch, tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Answers)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    for name in ("SEARCH", "DATA", "UNIPROT", "ALPHAFOLD"):
        monkeypatch.setattr(search, name, f"{base}/{name.lower()}/")
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    yield _Answers
    _Answers.status, _Answers.body, _Answers.delay = 200, b"{}", 0.0
    server.shutdown()
    server.server_close()


def test_an_error_from_the_data_service_is_not_read_as_no_entry(one_answer):
    # The PDB's data service answers a query it cannot run with 200 and
    # `errors`; read as no entries, 1L2Y was "not in the PDB" for a week.
    one_answer.body = json.dumps({"errors": [{"message": "Field 'x' is undefined"}],
                                  "data": None}).encode()
    for _ in range(2):
        with pytest.raises(search.SearchUnreachable) as raised:
            search.find_structures("1L2Y")
        assert raised.value.code == "environment.service.unusable_response"
        assert "answered with an error: Field 'x' is undefined" in str(raised.value)
    assert not list(search._cache_dir().glob("*.json"))


def test_an_answer_too_large_is_refused(one_answer, monkeypatch):
    monkeypatch.setattr(search, "MOST_BYTES", 100)
    one_answer.body = json.dumps({"data": {"entries": []}, "pad": "x" * 500}).encode()
    with pytest.raises(search.SearchUnreachable, match="more than"):
        search.find_structures("1L2Y")


def test_a_search_ends_within_its_budget(one_answer, monkeypatch):
    import time

    monkeypatch.setattr(search, "BUDGET", 0.6)
    one_answer.delay = 0.4
    one_answer.body = json.dumps({"total_count": 0}).encode()
    started = time.monotonic()
    with pytest.raises(search.SearchUnreachable):
        search.find_structures("lysozyme")
    assert time.monotonic() - started < 2.0


def test_a_name_in_uniprot_s_language_is_one_phrase():
    assert search._phrase('HIV-1 protease [mutant]') == '"HIV-1 protease [mutant]"'
    assert search._phrase('say "hi" \\ there') == '"say \\"hi\\" \\\\ there"'


def test_entries_found_are_kept_when_the_models_cannot_be_fetched(monkeypatch):
    entry = search.Entry("2QD7", title="HIV-1 protease", accession="P03367", entries_of_it=27,
                         alone=True)
    monkeypatch.setattr(search, "_entries", lambda *a: ([entry], 69, "name"))

    def unreachable(*a):
        raise search.SearchUnreachable("rest.uniprot.org refused the search (400).")

    monkeypatch.setattr(search, "_models", unreachable)
    found = search.find_structures("HIV-1 protease [mutant]", predicted=True)
    assert [e.pdb_id for e in found.entries] == ["2QD7"]
    said = search.said(found)
    assert "- 2QD7:" in said
    assert "AlphaFold DB's models could not be fetched: rest.uniprot.org refused" in said
    monkeypatch.setattr(search, "_entries", lambda *a: ([], 0, ""))
    with pytest.raises(search.SearchUnreachable):
        search.find_structures("HIV-1 protease [mutant]")
