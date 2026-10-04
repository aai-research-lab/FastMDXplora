"""A contributor signs one agreement, with one sentence, checked one way.

The Contributor License Agreement (`CLA.md`) is signed by a comment on a pull
request, which a workflow recognises and records. The sentence is written in
four places: the agreement, the contributing guide, and twice in the
workflow (the condition that runs it and the comment the action accepts). A
sentence changed in one and not the others is a signature the check never
sees. The workflow runs with write permissions on pull requests from forks,
so it must never check out their code, and its action is pinned by commit.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SENTENCE = "I have read the FastMDXplora Contributor License Agreement and I hereby sign it"


def _workflow() -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / "cla.yml").read_text(
        encoding="utf-8"))


def test_the_sentence_is_the_same_everywhere():
    step = _workflow()["jobs"]["cla"]["steps"][0]
    assert step["with"]["custom-pr-sign-comment"] == SENTENCE
    assert f"github.event.comment.body == '{SENTENCE}'" in step["if"]
    assert f"> {SENTENCE}\n" in (ROOT / "CLA.md").read_text(encoding="utf-8")
    assert f"`{SENTENCE}`" in (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")


def test_the_check_reads_comments_and_never_runs_a_pull_request():
    workflow = _workflow()
    steps = workflow["jobs"]["cla"]["steps"]
    assert len(steps) == 1
    assert re.fullmatch(r"contributor-assistant/github-action@[0-9a-f]{40}", steps[0]["uses"])
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert set(triggers) == {"issue_comment", "pull_request_target"}
    with_ = steps[0]["with"]
    assert with_["branch"] == "cla-signatures"
    assert with_["path-to-document"].endswith("/blob/main/CLA.md")


def test_the_agreement_names_a_person_and_covers_what_was_asked():
    text = (ROOT / "CLA.md").read_text(encoding="utf-8")
    assert "**Adekunle Aina** (the \"Maintainer\")" in text
    for part in ("## 2. Copyright licence", "## 3. Patent licence", "## 4. What stays yours",
                 "## 6. Contributors under 18", "## 8. Transfer", "before or after You sign"):
        assert part in text, part
    assert "\u2014" not in text and "\u2013" not in text
