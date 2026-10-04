"""Tags and a note a person gives a study, kept in its folder.

A study's records say what it is and what it found; nothing said what it is
to the person ("wild type", "JCIM Fig. 4", "redo with Ca²⁺"). Tags and a
one-line note are kept in `study_tags.json` beside the records, never in
them: set on a card of the All studies page, which filters by a tag and
offers the tags used as one is typed; read by an AI app through `fastmdx
mcp`, which may list the studies with a tag and add tags, but not remove one
or write the note.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_the_workspace_says_its_studies import _replicas, _study


@pytest.fixture
def workspace(tmp_path) -> Path:
    _study(tmp_path / "wild_type", started="2026-09-02T10:00:00+00:00")
    _study(tmp_path / "mutant", system="1UBQ", started="2026-09-03T10:00:00+00:00")
    _replicas(tmp_path / "chignolin")
    return tmp_path


def test_tags_and_a_note_are_kept_beside_the_records(workspace):
    from fastmdxplora.study_tags import TAGS_FILE, add_tags, set_tags, tags_of

    study = workspace / "wild_type"
    records = {p.name: p.read_bytes() for p in study.iterdir() if p.is_file()}
    assert tags_of(study) == {"tags": [], "note": ""}
    said = set_tags(study, ["  wild   type ", "JCIM Fig. 4", "Wild Type", ""],
                    "Ca²⁺ missing,\nrerun before quoting")
    # Spaces collapsed, one tag whatever its case, no empty tag, one line.
    assert said == {"ok": True, "tags": ["wild type", "JCIM Fig. 4"],
                    "note": "Ca²⁺ missing, rerun before quoting"}
    assert tags_of(study) == {"tags": ["wild type", "JCIM Fig. 4"],
                              "note": "Ca²⁺ missing, rerun before quoting"}
    kept = json.loads((study / TAGS_FILE).read_text(encoding="utf-8"))
    assert kept["tags"] == ["wild type", "JCIM Fig. 4"] and kept["updated"].endswith("+00:00")
    # No record the software reads was touched, and nothing else was left.
    assert {p.name: p.read_bytes() for p in study.iterdir()
            if p.is_file() and p.name != TAGS_FILE} == records
    assert not [p for p in study.iterdir() if p.name.startswith(".study_tags")]
    # Added to: the note kept, a tag it has not added again.
    added = add_tags(study, ["redo with Ca²⁺", "WILD TYPE"])
    assert added["tags"] == ["wild type", "JCIM Fig. 4", "redo with Ca²⁺"]
    assert added["added"] == ["redo with Ca²⁺"] and added["note"].startswith("Ca²⁺ missing")
    # Set again without a note: the note kept; with an empty one: cleared.
    assert set_tags(study, ["wild type"])["note"].startswith("Ca²⁺ missing")
    assert set_tags(study, ["wild type"], "")["note"] == ""


def test_what_cannot_be_kept_is_said(workspace, monkeypatch):
    from fastmdxplora import study_tags
    from fastmdxplora.study_tags import add_tags, set_tags, tags_of

    study = workspace / "wild_type"
    assert set_tags(study, "wild type")["reason"] == "Tags are given as a list of words."
    assert set_tags(study, ["x" * 41])["reason"] == (
        "A tag is up to 40 characters of text on one line.")
    assert set_tags(study, [3])["reason"] == "A tag is up to 40 characters of text on one line."
    assert set_tags(study, [f"t{n}" for n in range(21)])["reason"] == (
        "A study has 20 tags at most.")
    assert set_tags(study, ["a"], "n" * 201)["reason"] == (
        "A note is one line of up to 200 characters.")
    set_tags(study, [f"t{n}" for n in range(20)])
    assert add_tags(study, ["one more"])["reason"] == "A study has 20 tags at most."
    assert set_tags(workspace, ["a"])["reason"] == f"{workspace} is not a study."

    def refused(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(study_tags.os, "replace", refused)
    assert set_tags(study, ["a"])["reason"] == (
        "The study's tags could not be kept in its folder: Permission denied.")
    monkeypatch.undo()
    # A file written by hand that is not as kept is read as none.
    (study / "study_tags.json").write_text("[1, 2]", encoding="utf-8")
    assert tags_of(study) == {"tags": [], "note": ""}


def test_the_cards_carry_them_and_the_tags_used(workspace):
    from fastmdxplora.gui.workspace import studies_in
    from fastmdxplora.study_tags import set_tags

    set_tags(workspace / "wild_type", ["ubiquitin", "JCIM Fig. 4"], "the reference")
    set_tags(workspace / "mutant", ["Ubiquitin", "L67S"])
    found = studies_in(workspace)
    cards = {card["name"]: card for card in found["studies"]}
    assert cards["wild_type"]["tags"] == ["ubiquitin", "JCIM Fig. 4"]
    assert cards["wild_type"]["note"] == "the reference"
    assert cards["chignolin"]["tags"] == [] and cards["chignolin"]["note"] == ""
    # The most used first, as first written.
    assert found["tags_used"] == [{"tag": "Ubiquitin", "studies": 2},
                                  {"tag": "JCIM Fig. 4", "studies": 1},
                                  {"tag": "L67S", "studies": 1}]


def test_the_server_keeps_them_from_this_computer_alone(workspace):
    import urllib.request

    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.study_tags import tags_of

    session = start_dashboard_session(output=str(workspace / "wild_type"), host="127.0.0.1",
                                      port=0)

    def post(body):
        request = urllib.request.Request(session.url + "/api/study-tags",
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    try:
        said = post({"path": str(workspace / "mutant"), "tags": ["L67S"], "note": "the mutant"})
        assert said == {"ok": True, "tags": ["L67S"], "note": "the mutant"}
        assert tags_of(workspace / "mutant")["tags"] == ["L67S"]
        assert post({"path": str(workspace), "tags": []})["reason"] == (
            f"{workspace} is not a study.")
        assert post({"path": str(workspace / "mutant"), "tags": "x"})["reason"] == (
            "Tags are given as a list of words.")
    finally:
        session.server.shutdown()
    from fastmdxplora.gui.server import POSTS_ANSWERED_BEYOND_LOOPBACK

    assert "/api/study-tags" not in POSTS_ANSWERED_BEYOND_LOOPBACK


def test_an_ai_app_reads_them_and_adds_tags(workspace):
    from fastmdxplora.mcp.app import App
    from fastmdxplora.mcp.tools import Context, ToolError, _list_studies, _tag_study
    from fastmdxplora.mcp.workspace import Workspace
    from fastmdxplora.study_tags import set_tags, tags_of

    set_tags(workspace / "wild_type", ["ubiquitin"], "the reference")
    ws = Workspace.at(workspace)
    context = Context(ws)
    listed = _list_studies(context, {})
    assert "    tagged: ubiquitin" in listed
    assert "    the person's note: the reference" in listed
    only = _list_studies(context, {"tag": "UBIQUITIN"})
    assert only.splitlines()[0] == (f"1 study in {ws.root} tagged 'UBIQUITIN', newest first:")
    assert "wild_type" in only and "mutant" not in only
    said = _tag_study(context, {"study": "mutant", "tags": ["ubiquitin", "L67S"]})
    assert said.splitlines()[0] == "Tagged mutant 'ubiquitin', 'L67S'."
    assert tags_of(workspace / "mutant") == {"tags": ["ubiquitin", "L67S"], "note": ""}
    assert _tag_study(context, {"study": "mutant", "tags": ["l67s"]}).startswith(
        "mutant had those tags already.")
    with pytest.raises(ToolError, match="A tag is up to 40 characters"):
        _tag_study(context, {"study": "mutant", "tags": ["x" * 50]})
    # Removing a tag and the note stay with the person; a read-only server
    # writes nothing.
    assert tags_of(workspace / "wild_type")["note"] == "the reference"
    assert "tag_study" in [t.name for t in App(ws).tools]
    assert "tag_study" not in [t.name for t in App(ws, runs=False).tools]


def test_the_page_tags_a_card_and_filters_by_a_tag(workspace):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.study_tags import set_tags, tags_of

    set_tags(workspace / "mutant", ["ubiquitin", "L67S"])
    session = start_dashboard_session(output=str(workspace / "wild_type"), host="127.0.0.1",
                                      port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.evaluate(f"() => window.FastMDXStudies.load({str(workspace)!r})")
            page.wait_for_selector(".study-card")
            offered = page.eval_on_selector_all("#studies-tags-used option",
                                                "o => o.map((x) => x.value)")
            card = '.study-card[data-path$="wild_type"]'
            # "Tag" on a card without any, "Tags" on one that has some.
            buttons = (page.text_content(f"{card} .study-tag-edit"),
                       page.text_content('.study-card[data-path$="mutant"] .study-tag-edit'))
            page.click(f"{card} .study-tag-edit")
            page.fill(f"{card} .study-tag-input", "ubiquitin")
            page.press(f"{card} .study-tag-input", "Enter")
            page.fill(f"{card} .study-tag-input", "reference")
            page.click(f"{card} .study-tag-editor button:has-text('Add')")
            page.click(f"{card} .study-tag-drop[aria-label='Remove the tag reference']")
            page.fill(f"{card} .study-note-input", "the wild type, 300 K")
            page.click(f"{card} .study-tag-editor button:has-text('Save')")
            page.wait_for_selector(f"{card} .study-note")
            shown = page.eval_on_selector_all(f"{card} .study-tag", "t => t.map((x) => x.textContent)")
            note = page.text_content(f"{card} .study-note")
            # A tag clicked narrows the cards to the studies with it.
            page.click(f"{card} .study-tag:has-text('ubiquitin')")
            page.wait_for_selector("#studies-tag-filter:not([hidden])")
            narrowed = sorted(page.eval_on_selector_all(
                ".study-card", "c => c.map((x) => x.dataset.path.split('/').pop())"))
            said = page.text_content("#studies-tag-filter-said")
            page.click("#studies-tag-filter-clear")
            every = page.locator(".study-card").count()
            # Searched by a note's words.
            page.fill("#studies-search", "300 K")
            searched = page.locator(".study-card").count()
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert buttons == ("Tag", "Tags")
    assert offered == ["L67S", "ubiquitin"] or offered == ["ubiquitin", "L67S"]
    assert shown == ["ubiquitin"] and note == "the wild type, 300 K"
    assert tags_of(workspace / "wild_type") == {"tags": ["ubiquitin"],
                                                "note": "the wild type, 300 K"}
    assert narrowed == ["mutant", "wild_type"] and said == "Tagged ubiquitin"
    assert every == 3 and searched == 1
