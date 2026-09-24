"""A checkout has uncommitted changes when its package does, not its root.

A study file saved beside the checkout's README marked every run made from
that checkout as made from uncommitted changes, and stopped ``fastmdx
remote`` from sending a study although the code was exactly its commit.
These build a real repository shaped like this one and ask git.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from fastmdxplora.provenance import code_changed

pytestmark = pytest.mark.skipif(shutil.which("git") is None,
                                reason="needs git")


@pytest.fixture
def checkout(tmp_path) -> tuple[Path, Path]:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)

    package = tmp_path / "src" / "fastmdxplora"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("x = 1\n")
    (tmp_path / "README.md").write_text("readme\n")
    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "first")
    return tmp_path, package


def test_a_clean_checkout_is_clean(checkout):
    root, package = checkout
    assert code_changed(root, package) is False


def test_a_study_file_in_the_root_is_not_a_code_change(checkout):
    root, package = checkout
    (root / "trial.yml").write_text("systems: [1L2Y]\n")
    (root / "README.md").write_text("edited\n")
    assert code_changed(root, package) is False


def test_an_edited_module_is(checkout):
    root, package = checkout
    (package / "__init__.py").write_text("x = 2\n")
    assert code_changed(root, package) is True


def test_a_new_module_not_yet_added_is(checkout):
    root, package = checkout
    (package / "new.py").write_text("y = 1\n")
    assert code_changed(root, package) is True


def test_where_git_cannot_answer_it_is_unknown(tmp_path):
    assert code_changed(tmp_path, tmp_path / "src") is None
