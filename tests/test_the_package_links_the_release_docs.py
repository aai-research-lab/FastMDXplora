"""The package's documentation link is the release's.

PyPI shows the link on the release's page, and `/en/latest/` is `main`: a
person installing 2.5.8 was sent to documentation of settings 2.5.8 does not
have. `/en/stable/` is built from the newest release tag.
"""

from __future__ import annotations

from pathlib import Path

from tests._toml import load_toml

ROOT = Path(__file__).resolve().parents[1]


def test_the_documentation_link_is_the_stable_build() -> None:
    urls = load_toml(ROOT / "pyproject.toml")["project"]["urls"]
    assert urls["Documentation"] == "https://fastmdxplora.readthedocs.io/en/stable/"
