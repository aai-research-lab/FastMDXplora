"""FastMDXplora's own docs, as the Agent reads them.

The Agent was asked what a button under its replies does and could not say:
its instructions carry the config language and its rules, and nothing of
the pages a person reads. The docs are those pages, the Markdown under
``docs/``. A wheel carries them as ``fastmdxplora/_docs`` (``setup.py``
copies them in at build); a source checkout, an editable install included,
reads ``docs/`` beside ``src/``. So the pages read are those of the version
installed, and nothing is fetched.

Each page is cut into sections at its headings, and each section into
passages (paragraphs, a table's rows, a list's items, a block of code). A
question is answered with the passages that match it best, scored by BM25
over their words and their headings, each said with its page and section.
A page, or one section of it, can be read whole.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fastmdxplora.refusals import CodedError

__all__ = ["DocsNotFound", "Passage", "Section", "docs_folder", "pages", "read_docs",
           "search"]

#: The most of an answer: under what a look may say (agent/tools.MOST_SAID).
MOST_ANSWER = 3800
#: The most of one passage in a search's answer.
MOST_PASSAGE = 900
#: How many passages a search gives.
MOST_PASSAGES = 4
#: A block longer than this is cut into its rows or items.
LONG_BLOCK = 700

_PACKAGED = "_docs"


class DocsNotFound(CodedError, LookupError):
    """A page or section the docs do not have, or no docs at all: what the
    Agent's look declines, said to the AI model as its refusal."""

    default_code = "agent.tool.refused"


@dataclass(frozen=True)
class Section:
    """One heading of a page and the text under it, its sections below it
    included."""

    page: str
    title: str
    heading: str
    level: int
    trail: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class Passage:
    """A paragraph, a table's row, a list's item or a block of code, with
    where it stands."""

    page: str
    trail: tuple[str, ...]
    text: str
    #: Whether it opens what it is part of: a page, or a long row of a table.
    opening: bool = False
    #: Which block of its section it is from: the windows of one long row or
    #: paragraph, the rows of one table and the items of one list share it.
    block: int = 0


def _is_docs(folder: Path) -> bool:
    return folder.is_dir() and (folder / "index.md").is_file() and (folder / "agent.md").is_file()


def docs_folder() -> Path | None:
    """Where the docs of this installation are: inside the package (a wheel),
    else beside ``src/`` (a source checkout). None where neither holds them."""
    here = Path(__file__).resolve().parent
    packaged = here / _PACKAGED
    if _is_docs(packaged):
        return packaged
    checkout = here.parent.parent / "docs"
    if _is_docs(checkout) and (checkout / "conf.py").is_file():
        return checkout
    return None


# ---------------------------------------------------------------------------
# Reading the pages
# ---------------------------------------------------------------------------
_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_TARGET = re.compile(r"^\([^)]+\)=\s*$")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
#: Sphinx's own blocks: a table of contents or reStructuredText for the API
#: pages, read by Sphinx and nothing a person reads as prose.
_SPHINX_ONLY = re.compile(r"^\s{0,3}(`{3,}|~{3,})\{(toctree|eval-rst)\}")
#: A line across the page (``---``), which says nothing.
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")


def _fence_of(line: str) -> str | None:
    """The fence a line opens a block of code with (its backticks or
    tildes), or None."""
    match = _FENCE.match(line)
    if not match:
        return None
    # A run of backticks with a backtick after it is code within a line
    # ("```x``` is inline"), not a fence (CommonMark).
    if match.group(1)[0] == "`" and "`" in line[match.end():]:
        return None
    return match.group(1)


def _closes(line: str, fence: str) -> bool:
    """Whether ``line`` closes the block of code ``fence`` opened: the same
    character, at least as many, and nothing after them (CommonMark)."""
    match = re.match(r"^\s{0,3}(`{3,}|~{3,})\s*$", line)
    return bool(match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence))


def _marked(lines: list[str]) -> list[tuple[str, str]]:
    """Each line with what it is: ``open`` or ``close`` (a block of code's
    fences), ``code`` (inside one) or ``text``."""
    marked: list[tuple[str, str]] = []
    fence: str | None = None
    for line in lines:
        if fence is None:
            opened = _fence_of(line)
            if opened:
                fence = opened
                marked.append((line, "open"))
            else:
                marked.append((line, "text"))
        elif _closes(line, fence):
            fence = None
            marked.append((line, "close"))
        else:
            marked.append((line, "code"))
    return marked


def _plain_lines(text: str) -> list[str]:
    """The page's lines, without comments, link targets and Sphinx's blocks."""
    lines = _COMMENT.sub("", text).splitlines()
    kept: list[str] = []
    skipping: str | None = None
    for line in lines:
        if skipping is not None:
            if _closes(line, skipping):
                skipping = None
            continue
        sphinx = _SPHINX_ONLY.match(line)
        if sphinx:
            skipping = sphinx.group(1)
            continue
        if _TARGET.match(line):
            continue
        kept.append(line.rstrip())
    return kept


def _sections_of(name: str, text: str) -> list[Section]:
    """A page's sections, in order, one per heading; text before the first
    heading is kept at the start of the first section."""
    lines = _plain_lines(text)
    title = name
    found: list[tuple[int, str, int]] = []  # level, heading, line index
    for index, (line, kind) in enumerate(_marked(lines)):
        if kind != "text":
            continue
        match = _HEADING.match(line)
        if match:
            level, heading = len(match.group(1)), match.group(2).strip()
            if level == 1 and title == name:
                title = heading
            found.append((level, heading, index))
    if not found:
        return [Section(name, title, title, 1, (), "\n".join(lines).strip())]
    before = "\n".join(lines[:found[0][2]]).strip()
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    for number, (level, heading, start) in enumerate(found):
        end = len(lines)
        for later_level, _, later_start in found[number + 1:]:
            if later_level <= level:
                end = later_start
                break
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        trail = tuple(h for lvl, h in stack if lvl > 1)
        body = "\n".join(lines[start + 1:end]).strip()
        if number == 0 and before:
            body = (before + "\n\n" + body).strip()
        sections.append(Section(name, title, heading, level, trail, body))
    return sections


def _chunks(text: str, *, to_heading: bool) -> list[str]:
    """``text`` as blocks: paragraphs, tables, lists, and each block of code
    whole. With ``to_heading``, only the text before the first heading."""
    blocks: list[str] = []
    current: list[str] = []
    for line, kind in _marked(text.splitlines()):
        if kind == "open":
            if current:
                blocks.append("\n".join(current))
            current = [line]
            continue
        if kind == "code":
            current.append(line)
            continue
        if kind == "close":
            current.append(line)
            blocks.append("\n".join(current))
            current = []
            continue
        if to_heading and _HEADING.match(line):
            break
        if not line.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return [b for b in blocks if b.strip()]


def _blocks(text: str) -> list[str]:
    """A section's own text (up to its first heading) as blocks, lines
    across the page left out."""
    return [b for b in _chunks(text, to_heading=True) if not _RULE.match(b)]


_ITEM = re.compile(r"^(\s{0,3})([-*+]|\d+[.)])\s")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
#: The narrowest a window is cut to, whatever is asked.
_NARROWEST = 40
#: The longest lead-in ("Four buttons at the bottom of the page:") carried
#: with what it introduces.
_LEAD_IN = 200
#: The least share of the average length a passage is scored as.
_SHORTEST = 0.75


def _is_table(lines: list[str]) -> bool:
    return len(lines) > 2 and all(line.lstrip().startswith("|") for line in lines)


def _is_list(lines: list[str]) -> bool:
    return bool(lines) and bool(_ITEM.match(lines[0]))


def _windows(text: str, most: int) -> list[str]:
    """``text`` in windows of whole sentences, of about one length and at
    most ``most`` characters; a longer sentence cut between words."""
    most = max(most, _NARROWEST)
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(" ".join(text.split())):
        while len(sentence) > most:
            cut = sentence.rfind(" ", 0, most)
            cut = cut if cut > 0 else most
            pieces.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if sentence:
            pieces.append(sentence)
    total = sum(len(p) + 1 for p in pieces)
    target = math.ceil(total / max(1, math.ceil(total / most)))
    windows: list[str] = []
    for piece in pieces:
        if windows and len(windows[-1]) < target and len(windows[-1]) + 1 + len(piece) <= most:
            windows[-1] += " " + piece
        else:
            windows.append(piece)
    return windows


def _lead_cell(row: str) -> tuple[str, str]:
    """A table row's first cell, which names what the row is, and the rest."""
    cells = row.strip().strip("|").split("|", 1)
    return "| " + cells[0].strip() + " |", (cells[1] if len(cells) > 1 else "").replace("|", " ")


def _row_pieces(head: str, row: str) -> list[tuple[str, bool]]:
    """A table's row under the table's head; a long row in windows, each led
    by the row's first cell, the first marked as opening the row."""
    if len(row) <= LONG_BLOCK:
        return [(head + "\n" + row, False)]
    if len(head) >= LONG_BLOCK // 2:
        # A head too wide to repeat above each window: each window under its
        # row's first cell alone.
        head = ""
    lead, rest = _lead_cell(row)
    # Each window within a long block with the head and lead above it; a
    # first cell too long to repeat is cut to leave a window its room.
    room = LONG_BLOCK - len(head) - 7 - 2 * _NARROWEST
    if len(lead) > room:
        lead = lead[:max(room - 6, 8)].rstrip() + " ... |"
    windows = _windows(rest, LONG_BLOCK - len(head) - len(lead) - 7)
    return [((f"{head}\n" if head else "") + (f"{lead} {w}" if i == 0 else f"{lead} ... {w}"),
             i == 0) for i, w in enumerate(windows)]


def _items(lines: list[str]) -> list[str]:
    items: list[list[str]] = []
    for line in lines:
        if _ITEM.match(line) or not items:
            items.append([line])
        else:
            items[-1].append(line)
    return ["\n".join(item) for item in items]


def _pieces(block: str) -> list[tuple[str, bool]]:
    """A long table as its rows, each under the table's head; a long list as
    its items; a long paragraph in windows of sentences; a block of code as
    it is. Each with whether it opens a long row."""
    if len(block) <= LONG_BLOCK or _fence_of(block.splitlines()[0]):
        return [(block, False)]
    lines = block.splitlines()
    if _is_table(lines):
        head = "\n".join(lines[:2])
        return [piece for row in lines[2:] for piece in _row_pieces(head, row)]
    if _is_list(lines):
        return [(window, False) for item in _items(lines)
                for window in _windows(item, LONG_BLOCK)]
    return [(window, False) for window in _windows(block, LONG_BLOCK)]


def _is_lead_in(block: str) -> bool:
    lines = block.splitlines()
    return (block.rstrip().endswith(":") and len(block) <= _LEAD_IN
            and not _fence_of(lines[0]) and not _is_table(lines) and not _is_list(lines))


def _passages_of(text: str) -> list[tuple[str, bool, int]]:
    """A section's own passages, each with the number of the block it is
    from. A lead-in ending in a colon goes with the start of what it
    introduces, not alone."""
    found: list[tuple[str, bool, int]] = []
    lead, led = "", 0
    for number, block in enumerate(_blocks(text)):
        if lead:
            (piece, opening), *rest = _pieces(block)
            found.append((lead + "\n" + piece, opening, number))
            found.extend((p, o, number) for p, o in rest)
            lead = ""
            continue
        if _is_lead_in(block):
            lead, led = block, number
            continue
        found.extend((p, o, number) for p, o in _pieces(block))
    if lead:
        found.append((lead, False, led))
    return found


@dataclass(frozen=True)
class _Docs:
    folder: Path
    sections: tuple[Section, ...]
    passages: tuple[Passage, ...]
    terms: tuple[Counter, ...]
    lengths: tuple[float, ...]
    frequency: Counter
    average: float
    labels: tuple[frozenset, ...]


def _signature(folder: Path) -> tuple:
    return tuple(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size)
                        for p in folder.glob("*.md")))


@lru_cache(maxsize=2)
def _read(folder: Path, signature: tuple) -> _Docs:
    sections: list[Section] = []
    for path in sorted(folder.glob("*.md")):
        sections.extend(_sections_of(path.stem, path.read_text(encoding="utf-8-sig",
                                                                errors="replace")))
    passages: list[Passage] = []
    for section in sections:
        trail = (section.title, *section.trail)
        for text, opening, block in _passages_of(section.text):
            if _words(text):
                passages.append(Passage(section.page, trail, text,
                                        opening or not section.trail, block))
    terms = tuple(_terms_of(p) for p in passages)
    frequency: Counter = Counter()
    for counted in terms:
        frequency.update(counted.keys())
    # Its words and its pairs of words, not the same words again as one.
    counts = [sum(n for key, n in c.items() if not key.startswith((_ONE, _GLUE))) for c in terms]
    average = (sum(counts) / len(counts)) if counts else 1.0
    # A passage is scored as at least this long: a short one (a window of a
    # long row, a line of a list) is part of something longer, and BM25's
    # favour for short documents would put it before the whole it is from.
    lengths = tuple(max(count, _SHORTEST * average) for count in counts)
    labels = tuple(_labels_of(p.text) for p in passages)
    return _Docs(folder, tuple(sections), tuple(passages), terms, lengths, frequency, average,
                 labels)


def _docs() -> _Docs:
    folder = docs_folder()
    if folder is None:
        raise DocsNotFound(
            "The docs are not installed with this copy of FastMDXplora, so they cannot "
            "be read here. Say so, and answer only from what you were given.")
    return _read(folder, _signature(folder))


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------
_WORD = re.compile(r"[a-z0-9][a-z0-9_.\-]*[a-z0-9]|[a-z0-9]")
#: Words that say nothing of what a passage is about.
_STOP = frozenset("""
a about after again all also am an and any are as at be because been before
being both but by can could did do does doing done each else for from had has
have having he her here hers him his how i if in into is it its itself just me
more most my no nor not now of off on once only or other our out over own same
she should so some such than that the their them then there these they this
those through to too under until up very was we were what when where which
while who whom why will with would you your yours
""".split())


#: Words the endings below would get wrong, as they are read.
_AS_READ = {"use": "use", "used": "use", "uses": "use", "using": "use", "usage": "use",
            "usages": "use",
            "gpus": "gpu", "cpus": "cpu", "menus": "menu"}


def _stem(word: str) -> str:
    """A word without the plural, tense or ending it was written in, and in
    the docs' spelling: "refused", "refuses" and "refusal" are one word, as
    are "stored" and "store", "minimize" and "minimisation", "equilibrate"
    and "equilibration", "rotate" and "rotation", "applies" and "apply"."""
    if len(word) <= 2 or not word.isalpha():
        return word
    if word in _AS_READ:
        return _AS_READ[word]
    # The docs write -ise and -yse; a question may be in either spelling.
    word = re.sub(r"([iy])z(e|ed|es|ing|ations?|ers?)$",
                  lambda m: m.group(1) + "s" + m.group(2), word)
    made = word.endswith(("ate", "ates", "ated", "ating", "ation", "ations"))
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("sis"):
        word = word[:-2]
    elif word.endswith("s") and word[-2] not in "su":
        word = word[:-1]
    if word.endswith("ation") and len(word) >= 8:
        # "-ation" read as "-ate" is: "rotation" as "rotate", while
        # "duration" stays apart from "during"; a short word ("cation",
        # "station") is a word of its own.
        word = word[:-3]
    else:
        for ending, least in (("ing", 3), ("ed", 2)):
            if (word.endswith(ending) and len(word) - len(ending) >= least
                    and not (ending == "ed" and word[-3] == "e")):
                word = word[:-len(ending)]
                if len(word) > 2 and word[-1] == word[-2] and word[-1] not in "aeioudlsz":
                    word = word[:-1]
                break
    if made and word.endswith("at") and len(word) >= 6:
        word = word[:-2]
    elif made and word.endswith("ate") and len(word) >= 7:
        word = word[:-3]
    if word.endswith("al") and len(word) >= 6:
        word = word[:-2]
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]
    if len(word) > 3 and word.endswith("y") and word[-2] not in "aeiou":
        # "apply", "applies" and "applied" as one.
        word = word[:-1] + "i"
    return word


#: The short words a pair may end in ("set up", "read only"): a verb's
#: particle, which the docs may write joined to it.
_PARTICLES = frozenset({"up", "out", "off", "on", "in", "over", "down", "only"})
_PARTICLE_STEMS = frozenset(_stem(word) for word in _PARTICLES)


def _words(text: str) -> list[str]:
    found: list[str] = []
    for word in _WORD.findall(text.lower()):
        parts = [word, *re.split(r"[_.\-]+", word)] if re.search(r"[_.\-]", word) else [word]
        for part in parts:
            # A letter alone says nothing (and is what is left of a word
            # in another script).
            if len(part) > 1 and part not in _STOP:
                found.append(_stem(part))
    return found


_CODE_SPAN = re.compile(r"`[^`\n]*`")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*[A-Za-z0-9]|[A-Za-z0-9]")
#: Two words glued into one name, the second's capital showing the seam
#: (``StudyFailed``, ``AmberTools``).
_GLUED = re.compile(r"[a-z][A-Z]")
_NAME_PARTS = re.compile(r"[_.\-]+")


#: Where a run of words ends: a sentence, a clause, a table's cell.
_RUN_END = re.compile(r"[|;:!?]|\.(?=\s|$)")


def _prose(text: str) -> list[str | None]:
    """The words of ``text`` in runs, in order, lower case, outside blocks of
    code: each a plain word ("Time", "step", "GPU", "up"), not a name of code
    (``StudyFailed``, ``nvt_duration_ns``), which stands as None between the
    words around it. A span of code is a run of its own (``fastmdx setup``),
    and a run ends at a sentence's end, a clause's or a table's cell."""
    # A block of code between two runs of prose parts them.
    lines = [line if kind == "text" else "|" for line, kind in _marked(text.splitlines())]
    words: list[str | None] = []
    joined = "\n".join(lines)
    runs: list[str] = []
    last = 0
    for span in _CODE_SPAN.finditer(joined):
        runs.extend(_RUN_END.split(joined[last:span.start()]))
        runs.append(span.group(0).strip("`"))
        last = span.end()
    runs.extend(_RUN_END.split(joined[last:]))
    for run in runs:
        for token in _TOKEN.findall(run):
            plain = token.isalpha() and (token[1:].islower() or token.isupper())
            words.append(token.lower() if plain else None)
        words.append(None)
    return words


def _name_parts(text: str) -> list[list[str]]:
    """Each name of words joined by ``_``, ``.`` or ``-`` (``box_shape``,
    ``--read-only``, "re-run") as its words, lower case; a name glued at a
    capital left out."""
    found: list[list[str]] = []
    for token in _TOKEN.findall(text):
        if _GLUED.search(token) or not _NAME_PARTS.search(token):
            continue
        parts = [part.lower() for part in _NAME_PARTS.split(token)]
        if all(part.isalpha() for part in parts):
            found.append(parts)
    return found


def _joined(text: str) -> list[tuple[str, tuple[str, str]]]:
    """Each two words that follow one another, as one word ("time step" as
    "timestep", "set up" as "setup", ``box_shape`` as "boxshape"), with the
    two they join: the docs write some of these as one word and some as two.
    Pairs come from runs of prose and from inside a name joined by ``_``,
    ``.`` or ``-``, never from a name glued at a capital; the second word may
    be a particle ("up", "only"), no other short word."""
    pairs: list[tuple[str, str]] = []
    words = _prose(text)
    pairs.extend(zip(words, words[1:]))
    for parts in _name_parts(text):
        pairs.extend(zip(parts, parts[1:]))
    return [(_stem(a + b), (_stem(a), _stem(b))) for a, b in pairs
            if a and b and len(a) > 1 and len(b) > 1 and a not in _STOP
            and (b not in _STOP or b in _PARTICLES)]


def _written_as_one(text: str) -> tuple[list[str], list[str]]:
    """The words ``text`` writes, lower case: each plain word and each word
    of a name joined by ``_``, ``.`` or ``-`` (``timestep_fs``); and apart,
    the names glued at a capital (``AmberTools``) as one word."""
    plain: list[str] = []
    glued: list[str] = []
    for token in _TOKEN.findall(text):
        if _GLUED.search(token):
            if token.isalpha():
                glued.append(token.lower())
            continue
        plain.extend(part.lower() for part in _NAME_PARTS.split(token)
                     if part.isalpha() and len(part) > 1 and part.lower() not in _STOP)
    return plain, glued


#: Keys of a passage's words as one word, for two words asked: ``+`` two
#: words of it side by side ("time step", ``box_shape``), ``~`` a word it
#: writes as one ("timestep", ``timestep_fs``), ``^`` a name it glues at a
#: capital (``AmberTools``), which may be two words or a name of its own.
_PAIR, _ONE, _GLUE = "+", "~", "^"


def _terms_of(passage: Passage) -> Counter:
    counted = Counter(_words(passage.text))
    for joined, _ in _joined(passage.text):
        counted[_PAIR + joined] += 1
    plain, glued = _written_as_one(passage.text)
    for word in plain:
        counted[_ONE + _stem(word)] += 1
    for word in glued:
        counted[_GLUE + _stem(word)] += 1
    # Where a passage stands says what it is about: its headings count twice.
    for heading in passage.trail[1:]:
        for word in _words(heading):
            counted[word] += 2
    for word in _words(passage.trail[0] if passage.trail else ""):
        counted[word] += 1
    return counted


def _phrase(text: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "


def _labels_of(text: str) -> frozenset:
    """The names a passage gives in bold: the GUI's buttons and pages, the
    things it defines (**Movie**, **Name it**, **Useful or Wrong**)."""
    labels = set()
    for bold in re.findall(r"\*\*(.+?)\*\*", text, re.S):
        label = _phrase(bold)
        if (3 <= len(bold) <= 60 and "\n\n" not in bold
                and any(len(w) > 2 and w not in _STOP for w in label.split())):
            labels.add(label)
    return frozenset(labels)


def _named(asked: str, labels: frozenset) -> int:
    """How many of a passage's bold names the question says, word for word."""
    return sum(1 for label in labels if label in asked)


def _page_word(page: str) -> str:
    """A page's name as a word asked is read ("clusters" as "cluster")."""
    return _stem(page)


def search(query: str, most: int = MOST_PASSAGES, page: str | None = None) -> list[Passage]:
    """The passages that answer ``query`` best, best first, from every page
    or from ``page``; none where no word of it is in them."""
    docs = _docs()
    asked = list(dict.fromkeys(_words(query)))
    if not asked:
        return []
    joins = [(j, pair) for j, pair in dict.fromkeys(_joined(query)) if j not in asked]
    # A page named by a word the docs use on few passages ("clusters", "the
    # examples"); "study" or "agent" is said of nearly every page.
    total = len(docs.passages)
    naming = {word for word in asked if docs.frequency[word] <= 0.05 * total}
    said = _phrase(query)
    k1, b = 1.2, 0.75

    def weight(word: str, tf: int, index: int) -> float:
        df = docs.frequency[word]
        idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
        norm = tf + k1 * (1 - b + b * docs.lengths[index] / docs.average)
        return idf * tf * (k1 + 1) / norm

    scored: list[tuple[float, int]] = []
    for index, counted in enumerate(docs.terms):
        passage = docs.passages[index]
        if page is not None and passage.page != page:
            continue
        score = 0.0
        covered: set[str] = set()
        for word in asked:
            tf = counted.get(word, 0)
            if tf:
                covered.add(word)
                score += weight(word, tf, index)
                continue
            # One word asked, written as two in prose ("timestep" for "time
            # step"): half a word's weight.
            tf = counted.get(_PAIR + word, 0)
            if tf:
                covered.add(word)
                score += 0.5 * weight(_PAIR + word, tf, index)
        for joined, pair in joins:
            # Two words asked, written by the passage side by side or as one
            # word: the weight of the two it stands for; half one word's
            # weight where it holds them apart as well (the phrase). A name
            # glued at a capital may be the two words or a name of its own
            # (``StudyFailed`` is not "study failed"): one word's weight.
            # A verb and its particle ("set up") is found only as two words
            # side by side: "setup" written as one is the phase, as in
            # ``setup_from``.
            keys = (((_PAIR + joined, 2.0),) if pair[1] in _PARTICLE_STEMS else
                    ((_PAIR + joined, 2.0), (_ONE + joined, 2.0), (_GLUE + joined, 1.0)))
            for key, whole in keys:
                tf = counted.get(key, 0)
                if tf:
                    apart = covered & set(pair)
                    covered.update(pair)
                    score += (0.5 if apart else whole) * weight(key, tf, index)
                    break
        matched = len(covered & set(asked))
        if matched:
            # A passage holding more of the words asked comes before one that
            # holds one of them many times; a thing the question names as the
            # docs name it in bold (a button, a page) is what it asks about;
            # a page's opening, or a long row's, says what the thing is, the
            # more so where the question names it.
            named = _named(said, docs.labels[index])
            # A page's opening where the question names the page ("a
            # cluster") says what it is, as a bold name does.
            paged = (passage.opening and not passage.trail[1:]
                     and _page_word(passage.page) in naming)
            lift = (2.0 if named or paged else 1.25) if passage.opening else 1.0
            scored.append((score * (0.5 + matched / len(asked)) * (1 + 0.5 * min(named, 2))
                           * lift, index))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [docs.passages[index] for index in _spread(scored, docs, max(1, most))]


def _spread(scored: list[tuple[float, int]], docs: _Docs, most: int) -> list[int]:
    """The best ``most``, at most two from one block (the rows of one table,
    the items of one list, the windows of one long row or paragraph) unless the others answer less
    than half as well: one long table's rows would otherwise take every
    place from the passage that says what was asked."""
    pool = scored[:max(8 * most, 32)]
    taken: Counter = Counter()
    chosen: list[int] = []
    while pool and len(chosen) < most:
        def standing(pair: tuple[float, int]) -> tuple[float, int]:
            passage = docs.passages[pair[1]]
            many = taken[(passage.page, passage.trail, passage.block)] >= 2
            return (pair[0] * (0.5 if many else 1.0), -pair[1])
        best = max(pool, key=standing)
        pool.remove(best)
        passage = docs.passages[best[1]]
        taken[(passage.page, passage.trail, passage.block)] += 1
        chosen.append(best[1])
    return chosen


# ---------------------------------------------------------------------------
# What a look says
# ---------------------------------------------------------------------------
def _version() -> str:
    """FastMDXplora and its version, where the version is known: a source
    checkout never built has none."""
    try:
        from fastmdxplora._version import version

        return f"FastMDXplora {version}"
    except Exception:  # noqa: BLE001 - a version is only said
        return "this copy of FastMDXplora"


def _cut(text: str, most: int) -> str:
    """``text`` cut to about ``most`` characters; a block of code cut is
    closed, so what follows it is not read as code."""
    text = text.strip()
    if len(text) <= most:
        return _closed(text)
    lines = text[:most].rstrip().splitlines()
    marked = _marked(lines)
    if marked and marked[-1][1] == "open":
        # A block of code cut at its opening: the opening left off.
        lines = lines[:-1]
        marked = marked[:-1]
    cut = "\n".join(lines).rstrip()
    if not cut:
        # Nothing before the block's opening: the opening is said, closed.
        return _closed(text[:most].rstrip() + "\n...")
    # Said on a line of its own where the cut ends on a block's closing.
    ending = "\n..." if marked and marked[-1][1] == "close" else " ..."
    return _closed(cut + ending)


def _closed(text: str) -> str:
    """``text`` with a block of code it leaves open closed, so what follows
    it is not read as code."""
    fence = None
    for line, kind in _marked(text.splitlines()):
        fence = _fence_of(line) if kind == "open" else (None if kind == "close" else fence)
    return text + (f"\n{fence}" if fence else "")


def pages() -> list[tuple[str, str]]:
    """Each page's name and title, in name order."""
    seen: dict[str, str] = {}
    for section in _docs().sections:
        seen.setdefault(section.page, section.title)
    return sorted(seen.items())


def _page_named(name: str) -> str:
    wanted = str(name or "").strip().lower()
    wanted = wanted[:-3] if wanted.endswith(".md") else wanted
    names = dict(pages())
    if wanted in names:
        return wanted
    for page, title in names.items():
        if title.lower() == wanted:
            return page
    raise DocsNotFound(f"The docs have no page {name!r}. The pages are: "
                       + ", ".join(names) + ".")


def _listing() -> str:
    lines = [f"The docs of {_version()}, installed here. Ask with `query` for the passages "
             "that answer a question, or with `page` (and `section`) to read one. The "
             "pages:"]
    for page, title in pages():
        lines.append(f"- `{page}`: {title}")
    return "\n".join(lines)


def _named_sections(page: str) -> list[tuple[Section, str]]:
    """A page's sections, each with the name it is read by: its headings
    from the page down (``A > B``), numbered where that is the same for two
    (``A > B (2)``)."""
    own = [s for s in _docs().sections if s.page == page]
    seen: Counter = Counter()
    named = []
    for section in own:
        trail = " > ".join(section.trail) or section.heading
        seen[trail] += 1
        named.append((section, trail if seen[trail] == 1 else f"{trail} ({seen[trail]})"))
    return named


def _plain(name: str) -> str:
    return " ".join(name.split()).lower().replace(" > ", ">")


def _section_in(page: str, wanted: str) -> tuple[Section, list[str]]:
    """The section of ``page`` that ``wanted`` names (by its name, its
    heading, or the most of its words in its heading and the headings above
    it), and the names of the others with its heading."""
    named = _named_sections(page)
    asked = " ".join(str(wanted).split())
    for section, name in named:
        if _plain(asked) in (_plain(name), _plain(f"{section.title} > {name}")):
            return section, []
    words = set(_words(asked))

    def score(section: Section) -> tuple[int, int]:
        heading = section.heading.lower()
        whole = int(heading == asked.lower()) * 2 + int(
            len(asked) >= 3 and asked.lower() in heading)
        above = set(_words(" ".join((section.title, *section.trail[:-1]))))
        return whole, 2 * len(words & set(_words(section.heading))) + len(words & above)

    best = max((section for section, _ in named), key=score, default=None)
    # Words that only the headings above match name no section.
    if best is None or not (score(best)[0] or words & set(_words(best.heading))):
        raise DocsNotFound(f"The page `{page}` has no section {wanted!r}. Its sections "
                           "are: " + "; ".join(s.heading for s, _ in named if s.level > 1)
                           + ".")
    others = [name for section, name in named
              if section.heading == best.heading and section is not best]
    return best, others


def _grouped(top: str, rows: list[str], bottom: str, size: int, room: int) -> list[str]:
    """``rows`` in groups, each between ``top`` and ``bottom`` (a table's
    head, a block of code's fences) and within ``size``; the first within
    ``room``."""
    rim = len(top) + len(bottom) + 2
    fitted: list[str] = []
    for row in rows:
        fitted.extend(_windows(row, size - rim - 1) if len(row) > size - rim else [row])
    groups: list[str] = []
    current: list[str] = []
    limit = room
    for row in fitted:
        body = len("\n".join(current + [row])) + rim
        if body > limit:
            if current:
                groups.append("\n".join(x for x in (top, *current, bottom) if x))
            elif limit < size:
                groups.append("")
            current, limit = [], size
        current.append(row)
    if current:
        groups.append("\n".join(x for x in (top, *current, bottom) if x))
    return groups


def _split(chunk: str, size: int, room: int) -> list[str]:
    """A block too long for one part, cut where it reads: a table between
    rows, its head above each piece, a long row in windows led by its first
    cell; code between lines, each piece fenced; a list between items; prose
    between sentences. The first piece fits in ``room`` (or is empty)."""
    lines = chunk.splitlines()
    fence = _fence_of(lines[0])
    if fence:
        closed = len(lines) > 1 and _closes(lines[-1], fence)
        return _grouped(lines[0], lines[1:-1] if closed else lines[1:],
                        lines[-1] if closed else fence, size, room)
    if _is_table(lines) and len(lines[0]) + len(lines[1]) < size // 2:
        top = "\n".join(lines[:2])
        rows: list[str] = []
        for row in lines[2:]:
            if len(top) + len(row) + 2 > size:
                lead, rest = _lead_cell(row)
                rows.extend(f"{lead} {w}" if i == 0 else f"{lead} ... {w}" for i, w in
                            enumerate(_windows(rest, size - len(top) - len(lead) - 10)))
            else:
                rows.append(row)
        return _grouped(top, rows, "", size, room)
    if _is_list(lines):
        return _grouped("", _items(lines), "", size, room)
    first = _windows(chunk, room)[0] if room >= _NARROWEST else ""
    rest = " ".join(chunk.split())[len(first):].strip()
    return ([first] if first else [""]) + _windows(rest, size)


def _parts(text: str, size: int) -> list[str]:
    """``text`` in parts of at most ``size`` characters, cut between blocks,
    and within a block only where it reads (see :func:`_split`)."""
    parts: list[str] = []
    current = ""
    for chunk in _chunks(text, to_heading=False):
        joined = chunk if not current else current + "\n\n" + chunk
        if len(joined) <= size:
            current = joined
            continue
        if len(chunk) <= size:
            parts.append(current)
            current = chunk
            continue
        room = size - len(current) - 2 if current else size
        pieces = _split(chunk, size, room)
        first = pieces.pop(0) if pieces else ""
        if current and first:
            parts.append(current + "\n\n" + first)
        elif current or first:
            parts.append(current or first)
        parts.extend(pieces[:-1])
        current = pieces[-1] if pieces else ""
    if current:
        parts.append(current)
    return [part for part in parts if part] or [""]


def _whole(page: str, wanted: str | None, part: int) -> str:
    docs = _docs()
    if not wanted:
        own = [s for s in docs.sections if s.page == page]
        top = own[0]
        heads = [("  " * (s.level - 2)) + "- " + s.heading for s in own if s.level > 1]
        head = (f"The page `{page}` ({top.title}) of the docs of {_version()}. Its "
                "sections; read one with `section`:\n" + "\n".join(heads))
        body = "\n\n".join(_blocks(top.text))
        text = head + ("\n\nIt opens:\n" + body if body else "")
        return _cut(text, MOST_ANSWER)
    section, others = _section_in(page, wanted)
    where = " > ".join((section.title, *section.trail))
    head = f"From the docs of {_version()}, `{page}`: {where}."
    if others:
        head += (" The page has other sections of this name; read one by naming it as "
                 "`section`:\n" + "\n".join(f"- {name}" for name in others[:6]))
    if not section.text.strip():
        if part > 1:
            raise DocsNotFound(f"`{page}`: {where} is read in 1 part; ask with `part` 1.")
        return (head + "\n\nNothing is written under this heading in the docs installed "
                "here (Sphinx may fill it from the code when the docs are built as a site). "
                "Say the docs do not say it, rather than guess.")
    size = MOST_ANSWER - len(head) - 120
    parts = _parts(section.text, size)
    if part > len(parts):
        raise DocsNotFound(f"`{page}`: {where} is read in {len(parts)} part"
                           f"{'s' if len(parts) > 1 else ''}; ask with `part` 1 to "
                           f"{len(parts)}.")
    tail = ""
    if len(parts) > 1:
        tail = (f"\n\n(Part {part} of {len(parts)}"
                + (f"; ask with `part` {part + 1} for the next.)" if part < len(parts)
                   else ".)"))
    return head + "\n\n" + _closed(parts[part - 1]) + tail


def read_docs(query: str | None = None, page: str | None = None,
              section: str | None = None, part: int | None = None) -> str:
    """What the docs say: the passages answering ``query`` (from ``page``
    alone where one is named); a ``page``'s sections and opening; one
    ``section`` of it whole, in ``part``s where it is long; with nothing
    asked, the pages. Raises :class:`DocsNotFound`."""
    if part is not None and (not isinstance(part, int) or isinstance(part, bool) or part < 1):
        raise DocsNotFound("`part` is a whole number, 1 or more.")
    asked = str(query or "").strip()
    name = _page_named(page) if page else None
    if section and not name:
        raise DocsNotFound("Name the page a section is on, as `page`.")
    if part not in (None, 1) and not section:
        raise DocsNotFound("`part` is for a section read in parts: name it with `page` and "
                           "`section`.")
    if name and (section or not asked):
        return _whole(name, section, part or 1)
    if not asked:
        return _listing()
    found = search(asked, page=name)
    where = f"the page `{name}` of the docs" if name else "the docs"
    if not found:
        return (f"Nothing in {where} of {_version()} answers {asked[:120]!r}. Say that "
                "the docs do not say, rather than guess.\n\n" + _listing())
    lines = [f"From {where} of {_version()}, installed here: the passages that answer it "
             "best, each with its page and section. Read a whole section with `page` and "
             "`section`."]
    budget = MOST_ANSWER - len(lines[0])
    for passage in found:
        trail = " > ".join(passage.trail)
        said = f"\n\n`{passage.page}`: {trail}\n{_cut(passage.text, MOST_PASSAGE)}"
        if len(said) > budget:
            break
        lines.append(said)
        budget -= len(said)
    return "".join(lines)
