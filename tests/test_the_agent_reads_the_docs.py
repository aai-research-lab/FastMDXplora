"""The Agent reads the software's own docs, and Useful and Wrong are icons.

Asked on its page what **Useful** and **Wrong** under its replies do, the
Agent answered that it could not say: its instructions carried the config
language and its rules, no look read the docs, and an installed copy had no
docs to read, since the package did not ship them (user, 10-09: "why the
agent doesn't have access to the software docs?"; "if that is the
professinal practice implement it!!"). Now `setup.py` copies `docs/*.md`
into the wheel as `fastmdxplora/_docs`, a source checkout reads `docs/`,
`fastmdxplora.software_docs` finds the passages that answer a question, and
the look `read_docs` gives them to the AI model, which is told to answer a
question about the software from them and name the page. And the two marks
under each reply are line icons (user, 10-09: "and those buttons should be
line icons").
"""

from __future__ import annotations

import importlib.util
import json
import runpy
import tempfile
import urllib.request
from pathlib import Path

import pytest

from fastmdxplora import software_docs
from fastmdxplora.agent.tools import LOOK_WORDS, MOST_SAID, Toolbox, look_said
from fastmdxplora.software_docs import DocsNotFound, read_docs

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


# ---------------------------------------------------------------------------
# Where the docs are
# ---------------------------------------------------------------------------
def test_a_checkout_reads_its_own_docs_folder() -> None:
    assert software_docs.docs_folder() == DOCS
    assert [name for name, _ in software_docs.pages()] == sorted(
        p.stem for p in DOCS.glob("*.md"))


def test_what_the_docs_do_not_have_is_a_refusal_of_the_look() -> None:
    refused = DocsNotFound("no such page")
    assert refused.code == "agent.tool.refused" and isinstance(refused, LookupError)
    assert str(refused) == "no such page"


def test_the_docs_inside_the_package_come_first(tmp_path, monkeypatch) -> None:
    packaged = tmp_path / "packaged"
    packaged.mkdir()
    (packaged / "index.md").write_text("# Packaged\n\nThe wheel's own.\n")
    (packaged / "agent.md").write_text("# The Agent\n\n## Marks\n\nA packaged passage.\n")
    monkeypatch.setattr(software_docs, "_PACKAGED", str(packaged))
    assert software_docs.docs_folder() == packaged
    assert "A packaged passage." in read_docs("packaged passage")


def test_with_no_docs_the_look_says_so_and_guesses_nothing(monkeypatch) -> None:
    monkeypatch.setattr(software_docs, "docs_folder", lambda: None)
    with pytest.raises(DocsNotFound, match="not installed"):
        read_docs("anything")
    look = Toolbox().use("read_docs", {"query": "what does Useful do"})
    assert not look.ok and "not installed" in look.said


# ---------------------------------------------------------------------------
# What it says
# ---------------------------------------------------------------------------
def test_the_question_asked_on_the_page_is_answered_from_the_agent_page() -> None:
    said = read_docs("what do the Useful and Wrong buttons under a reply do")
    blocks = said.split("\n\n")[1:]
    assert blocks[0].startswith(("`agent`: The FastMDXplora Agent", "`gui`: The FastMDXplora GUI"))
    assert "Useful" in blocks[0]
    assert "**Useful or Wrong** under each reply marks it" in said


@pytest.mark.parametrize("question, page", [
    ("can I run a study on another machine", ("remote", "mcp", "api")),
    ("how do I resume a study that stopped", ("refusals", "cli", "production")),
    ("where does the API key for the AI model live", ("agent", "gui")),
    ("residue_states protonation by hand", ("config", "gui")),
])
def test_each_answer_names_its_page_and_section(question, page) -> None:
    said = read_docs(question)
    assert said.startswith(f"From the docs of {software_docs._version()}, installed here")
    named = [block.split(":", 1)[0].strip("`") for block in said.split("\n\n")[1:]]
    assert named and any(name in page for name in named)


def test_what_the_docs_do_not_hold_is_said_with_the_pages() -> None:
    said = read_docs("xylophone zeppelin")
    assert said.startswith(f"Nothing in the docs of {software_docs._version()}")
    assert "rather than guess" in said and "- `agent`:" in said
    # Words in another script leave no letters behind that match anything.
    assert read_docs("\u00dcn\u00efc\u00f6d\u00e9 \u65e5\u672c\u8a9e \u2603").startswith(
        "Nothing in the docs")


def test_the_version_said_is_the_one_installed(monkeypatch) -> None:
    import sys
    import types

    monkeypatch.setitem(sys.modules, "fastmdxplora._version", None)
    assert software_docs._version() == "this copy of FastMDXplora"
    monkeypatch.setitem(sys.modules, "fastmdxplora._version",
                        types.SimpleNamespace(version="9.8.7"))
    assert software_docs._version() == "FastMDXplora 9.8.7"
    assert read_docs().startswith("The docs of FastMDXplora 9.8.7, installed here.")


def test_nothing_asked_lists_the_pages() -> None:
    said = read_docs()
    for name, title in software_docs.pages():
        assert f"- `{name}`: {title}" in said


def test_a_page_says_its_sections_and_how_it_opens() -> None:
    said = read_docs(page="agent")
    assert said.startswith("The page `agent` (The FastMDXplora Agent)")
    assert "- Connecting an AI model" in said and "It opens:" in said
    assert read_docs(page="agent.md") == said
    assert read_docs(page="The FastMDXplora Agent") == said


def _read_in_parts(page: str, section: str) -> list[str]:
    parts, number = [], 1
    while True:
        said = read_docs(page=page, section=section, part=number)
        body = said.split("\n\n", 1)[1]
        if "\n\n(Part " in body:
            body = body.rsplit("\n\n(Part ", 1)[0]
        parts.append(body)
        if f"`part` {number + 1}" not in said:
            return parts
        number += 1


def _section(page: str, heading: str):
    return next(s for s in software_docs._docs().sections
                if s.page == page and s.heading == heading)


def test_a_section_is_read_whole_in_parts_that_join_up() -> None:
    first = read_docs(page="agent", section="From the GUI")
    assert first.startswith(f"From the docs of {software_docs._version()}, ")
    assert "`agent`: The FastMDXplora Agent > The three channels > From the GUI." in first
    section = next(s for s in software_docs._docs().sections
                   if s.page == "agent" and s.heading == "From the GUI")
    parts, number = [], 1
    while True:
        said = read_docs(page="agent", section="from the gui", part=number)
        body = said.split("\n\n", 1)[1]
        if "\n\n(Part " in body:
            body = body.rsplit("\n\n(Part ", 1)[0]
        parts.append(body)
        if f"`part` {number + 1}" not in said:
            break
        number += 1
    assert number > 1
    assert "\n\n".join(parts).replace("\n", "") == section.text.replace("\n", "")


@pytest.mark.parametrize("page, heading", [
    ("refusals", "Common refusals"),    # one table of 8,000 characters
    ("gui", "The pages"),               # a table whose rows run to 18,000
    ("remote", "Sending a study"),      # blocks of code across parts
    ("mcp", "Tools"),
])
def test_a_long_section_is_cut_only_where_it_reads(page, heading) -> None:
    section = _section(page, heading)
    parts = _read_in_parts(page, heading)
    assert len(parts) > 1
    words = set(section.text.split())
    for part in parts:
        # No word cut in two, nothing that is not the section's.
        assert set(part.split()) <= words | {"..."}
        assert part.count("```") % 2 == 0 and part.count("~~~") % 2 == 0
        lines = part.lstrip("\n").splitlines()
        assert part == part.strip("\n")
        if lines[0].startswith("|"):
            # A table carried on: its head above it again.
            assert lines[1].startswith("|---") or lines[1].startswith("| ---")
    # Every word of the section, in its order, is in the parts (a long row's
    # cells read as one run of words).
    said = iter(w for w in " ".join(parts).split() if w != "|")
    assert all(word in said for word in section.text.split() if word != "|")


def test_a_heading_on_a_page_twice_is_read_by_the_headings_above_it() -> None:
    said = read_docs(page="config_reference", section="How this phase was written")
    assert "other sections of this name" in said
    named = said.split("other sections of this name", 1)[1].split("\n\n", 1)[0]
    others = [line[2:] for line in named.splitlines() if line.startswith("- ")]
    assert len(others) == 3
    for trail in others:
        heading = trail.split(" > ")[-1]
        assert heading == "How this phase was written"
        read = read_docs(page="config_reference", section=trail)
        assert f"> {trail}." in read and "other sections of this name" not in read
    texts = {read_docs(page="config_reference", section=t) for t in others}
    assert len(texts) == 3


def test_a_page_or_section_not_there_is_refused_with_what_is() -> None:
    with pytest.raises(DocsNotFound, match="The pages are: agent, analyses"):
        read_docs(page="nowhere")
    with pytest.raises(DocsNotFound, match="Its sections are: .*Connecting an AI model"):
        read_docs(page="agent", section="zeppelin")
    with pytest.raises(DocsNotFound, match="Name the page"):
        read_docs(section="Connecting an AI model")
    # A letter or two is no heading, though some heading holds it.
    for few in ("a", "e"):
        with pytest.raises(DocsNotFound, match="has no section"):
            read_docs(page="agent", section=few)


def test_a_question_with_a_page_is_answered_from_that_page() -> None:
    said = read_docs("Useful and Wrong", page="agent")
    assert said.startswith("From the page `agent` of the docs")
    named = [block.split(":", 1)[0].strip("`") for block in said.split("\n\n")[1:]]
    assert named and set(named) == {"agent"}
    assert "**Useful or Wrong**" in said


def test_every_answer_fits_what_a_look_may_say() -> None:
    docs = software_docs._docs()
    asked = [read_docs(), *(read_docs(page=name) for name, _ in software_docs.pages())]
    for section in docs.sections:
        said = read_docs(page=section.page, section=section.heading)
        asked.append(said)
        if "`part` 2" in said:
            asked.append(read_docs(page=section.page, section=section.heading, part=2))
    for heading in {s.heading for s in docs.sections}:
        asked.append(read_docs(heading))
    assert max(len(said) for said in asked) <= MOST_SAID


def test_sphinx_s_own_blocks_comments_and_targets_are_not_read(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "conf.py").write_text("")
    (folder / "index.md").write_text(
        "# Home\n\n```{toctree}\n:maxdepth: 1\nagent\n```\n\n(home-target)=\n"
        "<!-- a comment saying zeppelin -->\nThe opening.\n\n"
        "```{eval-rst}\n.. automodule:: zeppelin\n```\n")
    (folder / "agent.md").write_text(
        "# The Agent\n\n## Code\n\n```yaml\n# not a heading\nkey: value\n```\n\nAfter it.\n")
    monkeypatch.setattr(software_docs, "docs_folder", lambda: folder)
    assert read_docs("zeppelin").startswith("Nothing in the docs")
    assert "toctree" not in read_docs(page="index")
    assert "The opening." in read_docs(page="index")
    code = read_docs(page="agent", section="Code")
    assert "# not a heading\nkey: value" in code and "After it." in code
    assert [s.heading for s in software_docs._docs().sections if s.page == "agent"] == [
        "The Agent", "Code"]


def test_fences_close_only_on_their_own_kind_and_text_before_a_heading_is_kept(
        tmp_path, monkeypatch) -> None:
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "conf.py").write_text("")
    (folder / "index.md").write_text("Before any heading, quokka.\n\n# Home\n\nThe opening.\n")
    (folder / "agent.md").write_text(
        "# The Agent\n\n## One\n\n````md\n```\n# also not a heading\n~~~\n```\n````\n\n"
        "## Two\n\n~~~\n```\n## nor this\n~~~\n\n## Three\n\nThe end.\n")
    monkeypatch.setattr(software_docs, "docs_folder", lambda: folder)
    headings = [s.heading for s in software_docs._docs().sections if s.page == "agent"]
    assert headings == ["The Agent", "One", "Two", "Three"]
    assert "Before any heading, quokka." in read_docs(page="index")
    assert "Before any heading, quokka." in read_docs("quokka")
    assert "The end." in read_docs(page="agent", section="Three")


def test_a_line_across_the_page_is_no_passage() -> None:
    docs = software_docs._docs()
    assert all(software_docs._words(p.text) for p in docs.passages)
    assert not [p for p in docs.passages if p.text.strip() in ("---", "***", "___")]
    said = read_docs("what is a Manifest")
    named = [block.split(":", 1)[0].strip("`") for block in said.split("\n\n")[1:]]
    assert named.count("manifest") >= 3 and "\n---" not in said


@pytest.mark.parametrize("written, asked", [
    ("refused", "refusal"), ("refuses", "refuse"), ("stored", "store"),
    ("computed", "compute"), ("sharing", "share"), ("running", "run"),
    ("settings", "setting"), ("analyses", "analysis"), ("analysed", "analyse"),
    ("boxes", "box"), ("stopped", "stop"), ("minimize", "minimisation"),
    ("minimization", "minimise"), ("analyzed", "analysed"), ("parameterized", "parameterises"),
    ("equilibrate", "equilibration"), ("equilibrated", "equilibrating"), ("seeded", "seed"),
    ("needed", "need"), ("separated", "separation"),
    # -ation at any length, y and i, and the words the endings get wrong.
    ("rotation", "rotate"), ("creation", "creates"), ("durations", "duration"),
    ("applies", "apply"), ("applied", "applies"), ("studies", "studying"),
    ("used", "use"), ("using", "usage"), ("uses", "use"), ("gpus", "GPU".lower()),
    ("cpus", "cpu"), ("menus", "menu"), ("usages", "use"),
])
def test_a_word_is_found_in_any_of_its_forms(written, asked) -> None:
    assert software_docs._stem(written) == software_docs._stem(asked)
    assert software_docs._stem("status") == "status"
    assert software_docs._stem("process") == "process"


@pytest.mark.parametrize("one, other", [
    ("duration", "during"), ("status", "state"), ("key", "ki"), ("use", "us"),
    # A short word ending in -ation is a word of its own.
    ("cation", "cat"), ("station", "state"), ("ration", "rate"),
])
def test_words_that_only_look_alike_stay_apart(one, other) -> None:
    assert software_docs._stem(one) != software_docs._stem(other)


def test_two_words_are_matched_with_one_only_where_both_are_written_as_words(
        tmp_path, monkeypatch) -> None:
    _synthetic(tmp_path, monkeypatch,
               "# A\n\n## Errors\n\nA script catches `StudyFailed` and reads its phase.\n\n"
               "## Kept\n\nA study that failed keeps its log.\n\n"
               "## Integrator\n\n| Setting | Default |\n|---|---|\n| `timestep_fs` | 2.0 |\n\n"
               "## Prose\n\nThe timestep is chosen for you.\n\n"
               "## Apart\n\nEach time a step is taken it is kept.\n")
    found = [p.trail[-1] for p in software_docs.search("study failed")]
    # Two words glued into a name at a capital may be a name of its own:
    # what writes the two words comes first.
    assert found[0] == "Kept"
    found = [p.trail[-1] for p in software_docs.search("the time step")]
    # One word in prose or in a name of code, for the two asked, as well as
    # the two written apart.
    assert set(found) >= {"Integrator", "Prose", "Apart"}
    found = [p.trail[-1] for p in software_docs.search("the timestep")]
    assert set(found) >= {"Integrator", "Prose"}


def test_two_words_of_prose_asked_as_one_are_found() -> None:
    assert "force field" in read_docs("which forcefield is used")
    assert "`timestep_fs`" in read_docs("what time step is used")


@pytest.mark.parametrize("question, row", [
    ("what box shape", "`box_shape`"), ("which water model", "`water_model`"),
    ("what does ligand forcefield do", "`ligand_forcefield`"),
    ("what does ligand pose do", "`ligand_pose`"),
])
def test_a_setting_s_name_asked_in_words_finds_its_row(question, row) -> None:
    # ``box_shape`` is "box shape" written as one name.
    assert row in read_docs(question)


def test_pairs_are_read_inside_names_and_never_across_code() -> None:
    joined = [j for j, _ in software_docs._joined("set `box_shape` up, re-run --read-only")]
    assert {"boxshap", "rerun", "readonli"} <= set(joined)
    # A span of code between two words parts them; the words of a span of
    # code pair with each other.
    assert "timestep" not in [j for j, _ in software_docs._joined("each time `x` step")]
    assert "timestep" in [j for j, _ in software_docs._joined("each time step")]
    assert "fastmdxsetup" in [j for j, _ in software_docs._joined("run `fastmdx setup` first")]
    # A run of words ends at a sentence's end and at a table's cell.
    joined = [j for j, _ in software_docs._joined(
        "The run stops. Time step `x`\n| Setting | Type |")]
    assert "stopstim" not in joined and "settingtyp" not in joined
    # A block of code parts the prose around it.
    assert "timestep" not in [j for j, _ in software_docs._joined(
        "the time\n```\nx = 1\n```\nstep is two")]
    # The second word may be a particle ("set up", "read only"), no other
    # short word; two short words make no pair.
    joined = [j for j, _ in software_docs._joined("use the GUI to set up a run that stopped")]
    assert "setup" in joined and not {"useth", "guito", "runthat"} & set(joined)
    assert software_docs._joined("`in_out`") == []
    # Two words glued at a capital are a name, not a pair, in prose or code.
    assert not any("studyfail" in j for j, _ in software_docs._joined("a StudyFailed error"))
    assert software_docs._name_parts("fooBar_baz and box_shape") == [["box", "shape"]]
    assert software_docs._written_as_one("StudyFailed timestep_fs") == (
        ["timestep", "fs"], ["studyfailed"])


def test_a_short_word_after_another_is_part_of_the_pair() -> None:
    assert ("setup", ("set", "up")) in software_docs._joined("how do I set up a cluster")
    assert "`clusters`" in read_docs("how do I setup a cluster")
    # "set up" is the verb, found only where the docs write it apart; the
    # phase written as one (``setup_from``) is not what was asked.
    first = software_docs.search("how to set up umbrella sampling")[0]
    assert "Umbrella sampling" in first.trail
    found = software_docs.search("How do I set up a study?")
    assert not any("`setup_from`" in p.text or "setup.prepared" in p.text for p in found)
    assert any(p.trail[-1] == "Designing a study" for p in found)


def test_a_command_s_name_finds_what_it_does() -> None:
    for command, does in (("simulate", "Run dynamics on a prepared system"),
                          ("setup", "Prepare a system and stop")):
        assert does in read_docs(f"What does fastmdx {command} do?")
    first = software_docs.search("What does fastmdx explore do?")[0]
    assert "`fastmdx explore` runs all four" in first.text


def test_a_name_glued_at_a_capital_is_found_by_its_two_words(tmp_path, monkeypatch) -> None:
    _synthetic(tmp_path, monkeypatch,
               "# A\n\n## Install\n\nInstall AmberTools first, before anything.\n\n"
               "## Other\n\nAmber is a colour, and so are others.\n")
    assert software_docs.search("amber tools")[0].trail[-1] == "Install"


def test_a_page_s_opening_is_lifted_not_a_long_row_s(tmp_path, monkeypatch) -> None:
    # Many other passages, so the page's name is a word few of them say.
    others = "".join(f"## Part {n}\n\nSomething else, number {n}.\n\n" for n in range(120))
    _synthetic(tmp_path, monkeypatch, f"# A\n\n{others}")
    filler = " ".join(["The row goes on about other things."] * 30)
    (tmp_path / "docs" / "quokkas.md").write_text(
        "# Quokkas\n\nA page of wombats.\n\n## Table\n\n| Name | What |\n|---|---|\n"
        f"| `q` | Wombats. {filler} |\n\n## Prose\n\nWombats here. "
        + " ".join(["Other words go on."] * 8) + "\n")
    found = software_docs.search("quokkas wombats")
    # The page's own opening is lifted by the name; a long row's opening is
    # lifted as any row's is, not by the page's name.
    assert found[0].trail == ("Quokkas",)
    row = next(p for p in found if p.trail[-1] == "Table")
    assert row.opening and found.index(row) > found.index(next(
        p for p in found if p.trail[-1] == "Prose"))


def test_a_page_is_lifted_only_by_a_word_that_names_it() -> None:
    assert software_docs.search("show me the examples")[0].page == "examples"
    # "study" is said on nearly every page: it lifts no page's opening.
    assert software_docs._page_word("studies") == software_docs._stem("study")
    found = software_docs.search("How do I name my study?")
    assert not any(p.page == "studies" and p.opening and not p.trail[1:] for p in found)


def test_a_short_passage_is_scored_as_part_of_something_longer() -> None:
    docs = software_docs._docs()
    # Its words and pairs of words; not the same words again as one.
    counts = [sum(n for key, n in c.items() if not key.startswith(("~", "^")))
              for c in docs.terms]
    assert docs.average == pytest.approx(sum(counts) / len(counts))
    assert list(docs.lengths) == [max(n, software_docs._SHORTEST * docs.average)
                                  for n in counts]
    assert any(length > count for length, count in zip(docs.lengths, counts))


def test_one_table_takes_at_most_two_places_unless_nothing_else_answers(
        tmp_path, monkeypatch) -> None:
    filler = " ".join(["The row goes on about other things."] * 5)
    rows = "\n".join(f"| `row{n}` | Quokka wombat quokka. {filler} |" for n in range(4))
    table = f"| Name | What |\n|---|---|\n{rows}"
    other = "A quokka and a wombat, and the rest of it said at length here, number nine."
    _synthetic(tmp_path, monkeypatch, f"# A\n\n## Many\n\n{table}\n\n## Other\n\n{other}\n")
    found = [p.trail[-1] for p in software_docs.search("quokka wombat")]
    assert found == ["Many", "Many", "Other", "Many"], found
    (tmp_path / "alone").mkdir()
    _synthetic(tmp_path / "alone", monkeypatch,
               f"# A\n\n## Many\n\n{table}\n\n## Other\n\nNothing.\n")
    found = [p.trail[-1] for p in software_docs.search("quokka wombat")]
    assert found == ["Many"] * 4
    # A third row is put back, not dropped: before what answers less than
    # half as well.
    weak = "A wombat, and only a wombat, with a great deal more said about other things here."
    (tmp_path / "weak").mkdir()
    _synthetic(tmp_path / "weak", monkeypatch,
               f"# A\n\n## Many\n\n{table}\n\n## Weak\n\n{weak}\n")
    found = [p.trail[-1] for p in software_docs.search("quokka wombat")]
    assert found == ["Many"] * 4, found
    # Paragraphs of one section that each answer are each kept: the cap is
    # for the rows of one table, or the windows of one long block.
    paragraphs = "\n\n".join(f"Quokka wombat quokka, number {n}." for n in range(4))
    (tmp_path / "paragraphs").mkdir()
    _synthetic(tmp_path / "paragraphs", monkeypatch,
               f"# A\n\n## Many\n\n{paragraphs}\n\n## Other\n\n{other}\n")
    assert [p.trail[-1] for p in software_docs.search("quokka wombat")] == ["Many"] * 4


def test_the_rows_of_one_table_that_answer_are_not_dropped_for_others() -> None:
    said = read_docs("how long is NVT equilibration")
    assert "`nvt_duration_ns`" in said.split("\n\n", 2)[1]


def test_each_window_of_a_long_row_is_led_by_its_first_cell() -> None:
    assert software_docs._lead_cell("| **Viewer** | The molecule | in 3D |") == \
        ("| **Viewer** |", " The molecule   in 3D ")
    windows = [p for p in software_docs._docs().passages
               if p.page == "gui" and "| **Viewer** |" in p.text]
    assert len(windows) > 2
    for passage in windows:
        row = passage.text.splitlines()[-1]
        assert row.startswith("| **Viewer** |"), row[:40]
        assert len(passage.text) <= software_docs.LONG_BLOCK


@pytest.mark.parametrize("question, found", [
    ("Why was my config refused?", "`refusals`"),
    ("What does the Movie button in the Viewer do?", "**Movie** makes"),
    ("What does Name it do in the Viewer?", "**Name it**"),
    # Two words in the question, one in the docs, and the other way round.
    ("How do I change the time step?", "timestep"),
    ("how is the forcefield chosen", "force field"),
    # The American spelling, where the docs write the British.
    ("How is energy minimization done?", "minimis"),
    ("How is the ligand parameterized?", "parameteris"),
])
def test_a_question_finds_its_answer_wherever_it_stands(question, found) -> None:
    assert found in read_docs(question)


def test_every_passage_but_code_is_short_enough_to_be_said_whole() -> None:
    for passage in software_docs._docs().passages:
        if "```" not in passage.text and "~~~" not in passage.text:
            assert len(passage.text) <= software_docs.MOST_PASSAGE, passage.trail


def test_a_thing_the_question_names_is_found_by_where_it_is_said_first() -> None:
    # The Viewer's row of the table of pages runs to 18,000 characters; its
    # first window says what the page is, the rest what each control does.
    said = read_docs("What is the Viewer page for?")
    first = said.split("\n\n")[1]
    assert "| **Viewer** | The molecule in 3D" in first
    assert "| **Viewer** | ..." not in first
    assert any("The molecule in 3D" in p.text and p.opening
               for p in software_docs._docs().passages)
    # A page's opening says what the page is for.
    opening = read_docs("What is the MCP server for?")
    assert "`mcp`: FastMDXplora from your AI app (MCP server)\nAn AI app that speaks" in opening


def test_a_lead_in_goes_with_what_it_introduces() -> None:
    for passage in software_docs._docs().passages:
        lines = passage.text.rstrip().splitlines()
        if not software_docs._is_list(lines):
            assert not (len(lines) == 1 and lines[0].endswith(":")), passage


def test_a_bold_name_is_a_name_and_a_word_alone_is_not() -> None:
    labels = software_docs._labels_of
    assert labels("**Run on\nthis machine** runs it") == frozenset({" run on this machine "})
    assert labels("**0** on a match **1**") == frozenset()
    assert labels("**not** and **why**") == frozenset()
    assert labels("**Name it**") == frozenset({" name it "})


def test_words_cut_to_nothing_still_end() -> None:
    assert software_docs._windows("abc def", 0) == ["abc def"]
    assert software_docs._windows("abc def", -5) == ["abc def"]
    head = "| " + " | ".join(f"column {n}" for n in range(500)) + " |"
    rule = "|" + "---|" * 500
    rows = ["| " + " | ".join(f"value {n}" for n in range(500)) + " |"] * 3
    parts = software_docs._parts("\n".join([head, rule, *rows]), 3500)
    assert parts and max(len(p) for p in parts) <= 3500


def _synthetic(tmp_path, monkeypatch, agent: str) -> None:
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "conf.py").write_text("")
    (folder / "index.md").write_text("# Home\n\nThe opening.\n")
    (folder / "agent.md").write_text(agent)
    monkeypatch.setattr(software_docs, "docs_folder", lambda: folder)


def test_long_code_lists_and_rows_keep_their_shape_in_parts(tmp_path, monkeypatch) -> None:
    code = "\n".join(f"echo {n} {'x' * 40}" for n in range(200))
    items = "\n".join(f"- item {n}: {'word ' * 30}" for n in range(60))
    row = "| **Lead** | " + " ".join(f"cell{n}" for n in range(3000)) + " |"
    _synthetic(tmp_path, monkeypatch,
               f"# A\n\n## Code\n\n```bash\n{code}\n```\n\n## List\n\n{items}\n\n## Row\n\n"
               f"| Name | What |\n|---|---|\n{row}\n\n## Unclosed\n\n```bash\n{code}\n")
    for section in ("Code", "List", "Row"):
        parts = _read_in_parts("agent", section)
        assert len(parts) > 2, section
        for part in parts:
            if section == "Code":
                assert part.startswith("```bash\n") and part.endswith("\n```")
            if section == "List":
                assert all(line.startswith("- item ") or line.startswith("  ")
                           for line in part.splitlines()), part[:200]
            if section == "Row":
                assert part.startswith("| Name | What |\n|---|---|\n| **Lead** |")
    unclosed = read_docs(page="agent", section="Unclosed", part=2)
    assert unclosed.count("```") % 2 == 0


def test_a_code_passage_cut_short_is_closed(tmp_path, monkeypatch) -> None:
    code = "\n".join(f"quokka {n} {'x' * 40}" for n in range(60))
    _synthetic(tmp_path, monkeypatch, f"# A\n\n## Code\n\n```bash\n{code}\n```\n\nAfter.\n")
    said = read_docs("quokka")
    block = said.split("\n\n", 1)[1]
    assert block.count("```") % 2 == 0 and block.rstrip().endswith("```")


def test_an_answer_of_long_passages_stays_within_what_a_look_may_say(
        tmp_path, monkeypatch) -> None:
    code = "\n".join(f"quokka {n} {'x' * 40}" for n in range(60))
    blocks = "\n\n".join(f"## Code {k}\n\n```bash\n{code}\n```" for k in range(6))
    _synthetic(tmp_path, monkeypatch, f"# A\n\n{blocks}\n")
    said = read_docs("quokka")
    assert len(said) <= software_docs.MOST_ANSWER and said.count("`agent`: A > Code") >= 3


def test_a_page_saved_with_a_byte_order_mark_keeps_its_title(tmp_path, monkeypatch) -> None:
    _synthetic(tmp_path, monkeypatch, "﻿# The Agent\n\n## Inside\n\nText.\n")
    assert ("agent", "The Agent") in software_docs.pages()


def test_a_section_is_found_by_the_headings_above_it_too(tmp_path, monkeypatch) -> None:
    for asked in ("simulation how this phase was written",
                  "simulation > How this phase was written"):
        said = read_docs(page="config_reference", section=asked)
        assert "> `simulation` \u2014 45 settings > How this phase was written." in said
        assert "other sections of this name" in said
    _synthetic(tmp_path, monkeypatch,
               "# A\n\n## Setup\n\n### Notes\n\nFirst notes.\n\n## Run\n\n### Notes\n\n"
               "Second notes.\n\n## Again\n\n### Example\n\nOne.\n\n### Example\n\nTwo.\n")
    assert "Second notes." in read_docs(page="agent", section="run notes")
    said = read_docs(page="agent", section="Example")
    assert "One." in said and "- Again > Example (2)" in said
    assert "Two." in read_docs(page="agent", section="Again > Example (2)")


def test_a_part_that_is_not_there_is_refused() -> None:
    parts = len(_read_in_parts("gui", "The pages"))
    with pytest.raises(DocsNotFound, match=f"read in {parts} parts; ask with `part` 1 to"):
        read_docs(page="gui", section="The pages", part=parts + 1)
    with pytest.raises(DocsNotFound, match="`part` is for a section"):
        read_docs(page="gui", part=2)
    for part in ("2", 2.5, True, 0):
        with pytest.raises(DocsNotFound, match="whole number"):
            read_docs(page="gui", section="The pages", part=part)


def test_a_page_s_opening_has_no_line_across_it() -> None:
    assert "\n---" not in read_docs(page="results")


def test_a_block_of_code_left_open_is_closed(tmp_path, monkeypatch) -> None:
    _synthetic(tmp_path, monkeypatch,
               "# A\n\nStart it with:\n\n```bash\nfastmdx gui\n")
    said = read_docs(page="agent")
    assert said.count("```") == 2 and said.rstrip().endswith("```")
    said = read_docs("fastmdx gui")
    assert said.count("```") == 2 and said.rstrip().endswith("```")
    (tmp_path / "section").mkdir()
    _synthetic(tmp_path / "section", monkeypatch, "# A\n\n## S\n\nRun:\n\n```bash\nfastmdx gui\n")
    said = read_docs(page="agent", section="S")
    assert said.count("```") == 2 and said.rstrip().endswith("```")


def test_a_lead_in_shares_the_number_of_the_block_it_introduces() -> None:
    passages = software_docs._passages_of(
        "First:\n\nPara one.\n\nThe table:\n\n| a | b |\n|---|---|\n| r0 | " + "word " * 80
        + " |\n| r1 | " + "word " * 80 + " |\n\nLast lead-in:")
    assert [block for _, _, block in passages] == [1, 3, 3, 4]


def test_a_cut_on_a_fence_and_code_within_a_line_read_as_they_are() -> None:
    assert software_docs._cut("```\ncode\n```\n\nmore words here", 12) == "```\ncode\n```\n..."
    # Cut at a block's opening, the opening is left off; with nothing before
    # it, the opening is said, closed.
    assert software_docs._cut("intro words\n```bash\ncode", 18) == "intro words ..."
    assert software_docs._cut("```" + "p" * 20 + "\ncode", 10).startswith("```" + "p" * 7)
    # Three backticks with a backtick after them on the line are code within
    # the line, not a fence.
    assert software_docs._closed("```x``` is inline") == "```x``` is inline"
    assert software_docs._fence_of("```bash") == "```"
    assert software_docs._fence_of("~~~ `x`") == "~~~"


def test_a_table_head_too_wide_to_repeat_is_left_off_its_row_s_windows() -> None:
    head = "| " + " | ".join(f"column {n}" for n in range(60)) + " |\n|" + "---|" * 60
    row = "| `name` | " + "Words go on here. " * 120 + "|"
    pieces = software_docs._row_pieces(head, row)
    assert len(pieces) < 10
    assert all(text.startswith("| `name` |") and len(text) <= software_docs.LONG_BLOCK
               for text, _ in pieces)
    # A first cell too long to repeat is cut, so each window keeps its room.
    head = "| h | " + "x" * 290 + " |\n|---|---|"
    row = "| " + "y " * 190 + " | " + "Words go on. " * 90 + "|"
    pieces = software_docs._row_pieces(head, row)
    assert all(len(text) <= software_docs.LONG_BLOCK for text, _ in pieces)
    assert all(" ... |" in text.splitlines()[2] for text, _ in pieces)


def test_a_heading_with_nothing_under_it_says_so(tmp_path, monkeypatch) -> None:
    _synthetic(tmp_path, monkeypatch, "# A\n\n## The API\n\n## Next\n\nText.\n")
    said = read_docs(page="agent", section="The API")
    assert "Nothing is written under this heading" in said
    assert "rather than guess" in said
    with pytest.raises(DocsNotFound, match="read in 1 part"):
        read_docs(page="agent", section="The API", part=7)


def test_a_long_table_is_found_by_its_row() -> None:
    said = read_docs("methods_of_study methods paragraphs report")
    row = next(block for block in said.split("\n\n")[1:] if "methods_of_study" in block)
    # The row comes under its table's head, not the whole table.
    assert row.count("\n|") <= 3


# ---------------------------------------------------------------------------
# The look
# ---------------------------------------------------------------------------
def test_the_look_is_offered_described_and_declared() -> None:
    box = Toolbox()
    assert "read_docs" in box.names
    assert "`read_docs`: The software tells you" not in box.describe()
    assert "- `read_docs`: what the docs of this installed version" in box.describe()
    spec = next(s for s in box.specs() if s.name == "read_docs")
    assert set(spec.parameters["properties"]) == {"query", "page", "section", "part"}
    assert spec.parameters["additionalProperties"] is False


def test_the_agent_is_told_to_answer_about_the_software_from_its_docs() -> None:
    from fastmdxplora.agent.conversation import system_prompt

    rule = "is answered from its docs: read_docs first"
    assert rule in Toolbox().describe() and rule in Toolbox().guidance()
    assert rule in system_prompt(phases=None, verbose=False, tools=Toolbox())


def test_the_look_reads_what_it_is_asked_and_refuses_what_is_not_words() -> None:
    box = Toolbox()
    found = box.use("read_docs", {"query": "Useful and Wrong under each reply"})
    assert found.ok and "**Useful or Wrong**" in found.said
    whole = box.use("read_docs", {"page": "agent", "section": "Connecting an AI model"})
    assert whole.ok and "fastmdx agent model" in whole.said
    assert not box.use("read_docs", {"page": "nowhere"}).ok
    assert not box.use("read_docs", {"query": ["a", "list"]}).ok
    assert not box.use("read_docs", {"query": "x", "part": 0}).ok
    assert not box.use("read_docs", {"page": "agent", "part": "two"}).ok
    assert box.use("read_docs", {}).said.startswith(f"The docs of {software_docs._version()}")


@pytest.mark.parametrize("part", [True, 2.7, float("inf"), float("nan"), -1, 0, "2"])
def test_a_part_is_a_whole_number(part) -> None:
    look = Toolbox().use("read_docs", {"page": "gui", "section": "The pages", "part": part})
    assert not look.ok and look.said.endswith("`part` is a whole number, 1 or more.")


def test_a_part_given_as_a_whole_float_is_read() -> None:
    look = Toolbox().use("read_docs", {"page": "gui", "section": "The pages", "part": 2.0})
    assert look.ok and "(Part 2 of " in look.said


def test_what_the_look_does_not_take_is_refused_and_a_bare_question_is_a_query() -> None:
    box = Toolbox()
    look = box.use("read_docs", {"question": "how do I resume", "page": "cli"})
    assert not look.ok and "`question`" in look.said
    # A bare question YAML read as a mapping, or as a list, is the question.
    for asked in ({"what does resume": "true do"}, {"value": ["what is a sweep"]}):
        look = box.use("read_docs", asked)
        assert look.ok and look.said.startswith("From the docs"), asked
    asked = box.use("read_docs", {"value": "Useful and Wrong under each reply"})
    assert asked.ok and "**Useful or Wrong**" in asked.said
    huge = box.use("read_docs", {"query": 10 ** 5000})
    assert not huge.ok and "is words, not a number of" in huge.said
    assert look_said("read_docs", {"query": 10 ** 5000}) == "Read the docs"


def test_the_look_is_said_as_the_person_reads_it() -> None:
    assert "read_docs" in LOOK_WORDS
    assert look_said("read_docs", {"query": "Useful buttons"}) == \
        "Read the docs on “Useful buttons”"
    assert look_said("read_docs", {"query": "x"}, doing=True) == "Reading the docs on “x”"
    assert look_said("read_docs", {"page": "agent", "section": "From the GUI"}) == \
        "Read the docs on “From the GUI”"
    assert look_said("read_docs", {"page": "gui"}) == "Read the docs on “gui”"
    assert look_said("read_docs", {}) == "Listed the docs' pages"
    assert look_said("read_docs", {}, doing=True) == "Listing the docs' pages"
    assert look_said("read_docs", {"page": 0}) == "Read the docs on “0”"
    # A value the look does not take is refused, and said so.
    assert look_said("read_docs", {"query": True, "page": "gui"}) == "Read the docs"
    assert look_said("read_docs", {"value": "resume"}) == "Read the docs on “resume”"
    # Something it cannot read is refused, not a list of the pages.
    assert look_said("read_docs", {"query": ["x"]}) == "Read the docs"
    # As the look reads it: a question YAML split at its colon, and nothing
    # asked but blanks, which lists the pages.
    assert look_said("read_docs", {"what does resume": "true do"}) == \
        "Read the docs on “what does resume: true do”"
    for blank in ({"query": ""}, {"query": "  ", "page": None}, {"value": ""}, {"part": 1},
                  {"part": 1.0}, {"query": "  ", "part": 1}):
        assert look_said("read_docs", blank) == "Listed the docs' pages", blank
        assert Toolbox().use("read_docs", blank).said.startswith("The docs of"), blank
    # What the look refuses is said as refused, never as a listing or a
    # reading: a key it does not take, a section with no page, a part alone.
    for refused in ({"query": "  ", "bogus": None}, {"page": None, "bogus": None},
                    {(1, 2): None}, {"part": 2}, {"section": "x"}, {"query": "x", "foo": "y"},
                    {"query": "x", "part": 2}, {"query": "resume", "part": 0},
                    {"query": "x", "page": ["agent"]}, {"page": "agent", "value": False},
                    {"page": "gui", "part": 2}):
        assert look_said("read_docs", refused) == "Read the docs", refused
        assert not Toolbox().use("read_docs", refused).ok, refused


def test_a_question_is_read_to_its_first_300_characters() -> None:
    box = Toolbox()
    assert "From the docs" in box.use("read_docs", {"query": "zzz " * 60 + "resume"}).said
    cut = box.use("read_docs", {"query": "zzz " * 80 + "resume"})
    assert cut.ok and cut.said.startswith("Nothing in the docs")


def test_the_agent_looks_in_the_docs_and_answers_from_them() -> None:
    from fastmdxplora.agent.conversation import propose_with_tools
    from fastmdxplora.agent.turns import ToolCall, Turn, Usage

    replies = [
        Turn("", (ToolCall("call_1", "read_docs",
                           {"query": "what do Useful and Wrong under a reply do"}),),
             Usage(calls=1, input_tokens=100, output_tokens=10)),
        Turn("SAY: Useful or Wrong marks a reply, kept with the conversation "
             "(the agent page, From the GUI).", (), Usage(calls=1, input_tokens=100,
                                                         output_tokens=10)),
    ]
    asked: list[dict] = []

    def turn(system, messages, tools):
        asked.append({"system": system, "messages": [dict(m) for m in messages],
                      "tools": [t.name for t in tools]})
        return replies.pop(0)

    proposal = propose_with_tools(
        "what do the useful and wrong buttons under your responses do?", turn,
        phases=["setup", "simulation"], max_cycles=3, verbose_schema=False, history=None,
        current_config=None, run_status=None, attachments=None, tools=Toolbox())
    assert "read_docs" in asked[0]["tools"]
    assert "read_docs first" in asked[0]["system"]
    result = asked[1]["messages"][-1]["results"][0]
    assert result["name"] == "read_docs" and not result["is_error"]
    assert "**Useful or Wrong** under each reply marks it" in result["content"]
    assert [look.tool for look in proposal.looks] == ["read_docs"]
    assert proposal.answer.startswith("Useful or Wrong marks a reply")


# ---------------------------------------------------------------------------
# The build puts the docs in the wheel
# ---------------------------------------------------------------------------
def _setup_module():
    spec = importlib.util.spec_from_file_location("fastmdxplora_setup", ROOT / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_build_copies_every_page_and_nothing_stale(tmp_path) -> None:
    built = _setup_module()
    target = tmp_path / "build" / "fastmdxplora" / "_docs"
    target.mkdir(parents=True)
    (target / "stale.md").write_text("from an earlier build")
    copied = built.copy_docs(DOCS, target)
    assert sorted(p.name for p in target.iterdir()) == sorted(p.name for p in DOCS.glob("*.md"))
    assert [p.name for p in copied] == sorted(p.name for p in DOCS.glob("*.md"))
    assert (target / "agent.md").read_bytes() == (DOCS / "agent.md").read_bytes()
    assert built.copy_docs(tmp_path / "none", target) == [] and not target.exists()


def test_the_build_step_is_the_one_setuptools_runs(tmp_path, monkeypatch) -> None:
    import setuptools
    from setuptools.command.build_py import build_py
    from setuptools.dist import Distribution

    given: dict = {}
    monkeypatch.setattr(setuptools, "setup", lambda **kw: given.update(kw))
    runpy.run_path(str(ROOT / "setup.py"), run_name="__main__")
    command = given["cmdclass"]["build_py"]
    assert issubclass(command, build_py)

    monkeypatch.setattr(build_py, "run", lambda self: None)
    step = command(Distribution())
    step.build_lib = str(tmp_path / "lib")
    step.editable_mode = False
    step.run()
    assert (tmp_path / "lib" / "fastmdxplora" / "_docs" / "agent.md").is_file()
    step.editable_mode = True
    (tmp_path / "lib2").mkdir()
    step.build_lib = str(tmp_path / "lib2")
    step.run()
    assert not (tmp_path / "lib2" / "fastmdxplora").exists()


def test_a_build_with_no_docs_says_so(tmp_path, monkeypatch) -> None:
    from setuptools.command.build_py import build_py
    from setuptools.dist import Distribution

    built = _setup_module()
    monkeypatch.setattr(build_py, "run", lambda self: None)
    monkeypatch.setattr(built, "DOCS", tmp_path / "nowhere")
    step = built.BuildWithDocs(Distribution())
    step.build_lib = str(tmp_path / "lib")
    step.editable_mode = False
    warned: list[str] = []
    monkeypatch.setattr(step, "warn", warned.append)
    step.run()
    assert len(warned) == 1 and "carries no docs" in warned[0]


# ---------------------------------------------------------------------------
# Useful and Wrong as line icons
# ---------------------------------------------------------------------------
def test_the_marks_are_drawn_from_the_page_s_one_icon_set() -> None:
    from fastmdxplora.gui.sidebar_icons import ICONS

    assert ICONS["useful"].count("<path") == 2
    assert ICONS["wrong"].startswith('<g transform="rotate(180 12 12)">')
    script = (ROOT / "src" / "fastmdxplora" / "gui" / "static" / "agent-panel.js").read_text()
    assert 'window.FastMDXIcons.button(kind, FEEDBACK[kind], "agent-feedback")' in script
    assert 'b.textContent = kind === "useful"' not in script


def test_the_marks_are_found_in_the_docs_by_what_they_look_like() -> None:
    said = read_docs("what does the thumbs up under a reply do")
    assert "a thumb up (**Useful**) and a thumb down (**Wrong**)" in said


HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    from fastmdxplora.gui.server import start_dashboard_session

    study = tmp_path / "workspace" / "study"
    (study / "analysis").mkdir(parents=True)
    started = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield started
    started.server.shutdown()


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="needs Playwright")
def test_useful_and_wrong_are_line_icons_named_and_filled_when_pressed(session) -> None:
    from playwright.sync_api import sync_playwright

    reply = {"ok": False, "answer": "It has 76 residues.", "cites": [], "attempts": [],
             "usage": {"calls": 1, "input_tokens": 93, "cache_read_tokens": 0,
                       "cache_write_tokens": 0, "output_tokens": 49}}
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(30000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.wait_for_selector("#agent-request", state="visible")
            page.route("**/api/agent/propose*", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(reply)))
            page.fill("#agent-request", "How many residues?")
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-answer:has-text('76 residues')")
            marks = """() => [...document.querySelectorAll(
                '#agent-thread .agent-meta [data-feedback]')].slice(-2).map(b => ({
                kind: b.dataset.feedback, label: b.getAttribute('aria-label'),
                title: b.title || (window.FastMDXTooltips
                                   ? window.FastMDXTooltips.titleOf(b) : ''),
                text: b.textContent.trim(), icon: b.classList.contains('line-btn'),
                drawn: !!b.querySelector('svg.line-icon path'),
                pressed: b.getAttribute('aria-pressed'),
                fill: b.querySelector('svg').getAttribute('fill'),
                colour: getComputedStyle(b).color,
                wide: Math.round(b.getBoundingClientRect().width)}))"""
            before = page.evaluate(marks)
            page.locator("#agent-thread .agent-meta").last.locator(
                "[data-feedback=useful]").click()
            useful = page.evaluate(marks)
            page.locator("#agent-thread .agent-meta").last.locator(
                "[data-feedback=wrong]").click()
            wrong = page.evaluate(marks)
            page.locator("#agent-thread .agent-meta").last.locator(
                "[data-feedback=wrong]").click()
            neither = page.evaluate(marks)
            page.locator("#agent-thread .agent-meta").last.locator(
                "[data-feedback=wrong]").click()
            page.wait_for_timeout(600)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#agent-thread .agent-meta [data-feedback=wrong]")
            kept = page.evaluate(marks)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(300)
            phone = page.evaluate("""() => ({
                marks: [...document.querySelectorAll('#agent-thread .agent-meta [data-feedback]')]
                    .slice(-2).map(b => { const r = b.getBoundingClientRect();
                        return [Math.round(r.width), Math.round(r.height),
                                r.left >= 0 && r.right <= window.innerWidth]; }),
                sideways: document.documentElement.scrollWidth > window.innerWidth})""")
        finally:
            browser.close()
    assert [(m["kind"], m["label"], m["title"], m["text"], m["icon"], m["drawn"])
            for m in before] == [("useful", "Useful", "Useful", "", True, True),
                                 ("wrong", "Wrong", "Wrong", "", True, True)]
    assert [(m["pressed"], m["fill"]) for m in before] == [("false", "none")] * 2
    assert [(m["pressed"], m["fill"]) for m in useful] == [("true", "currentColor"),
                                                           ("false", "none")]
    assert [(m["pressed"], m["fill"]) for m in wrong] == [("false", "none"),
                                                          ("true", "currentColor")]
    assert wrong[1]["colour"] != wrong[0]["colour"] and useful[0]["colour"] != before[0]["colour"]
    assert [(m["pressed"], m["fill"]) for m in neither] == [("false", "none")] * 2
    assert [(m["pressed"], m["fill"]) for m in kept] == [("false", "none"),
                                                         ("true", "currentColor")]
    assert all(m["wide"] == 28 for m in before)
    assert phone == {"marks": [[28, 28, True], [28, 28, True]], "sideways": False}
    assert errors == []
    assert json.loads(urllib.request.urlopen(session.url + "/api/agent/conversation",
                                             timeout=10).read())["entries"][-1][
        "feedback"] == "wrong"
