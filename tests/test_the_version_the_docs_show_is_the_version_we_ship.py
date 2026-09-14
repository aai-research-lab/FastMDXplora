"""The first number anyone reads about this project should be a true one.

`fastmdxplora.readthedocs.io` served `FastMDXplora 0.1.1.dev50+g52cfb8616` in
the title of every page while PyPI and conda-forge both carried 2.5.5, and the
`g52cfb86` in that string is a real commit on `main` -- so the docs build had
the repository and simply could not see a tag in it.

That is the whole mechanism. Read the Docs clones shallow and without tags,
setuptools-scm finds no `v2.5.5` to resolve against, falls back, and stamps
the guess into the built pages. A reader evaluating the software sees version
0.1 and concludes it is a pre-1.0 experiment, which is the opposite of eleven
releases over a year.

Neither half of the fix is visible from inside the package -- the build that
goes wrong happens on someone else's machine, and no import can detect it --
so what is checked here is the configuration that prevents it. Same species
as `test_the_conda_file_installs_what_it_claims`: a promise that lives in a
file gets a test that reads that file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
READTHEDOCS = ROOT / ".readthedocs.yaml"
PYPROJECT = ROOT / "pyproject.toml"


def load(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestTheDocsBuildCanSeeATag:

    def test_the_config_is_there_at_all(self) -> None:
        assert READTHEDOCS.is_file(), (
            "Without .readthedocs.yaml the docs build takes defaults, and the "
            "default is a shallow clone with no tags.")

    def test_it_fetches_tags_after_checkout(self) -> None:
        """The one step that makes the version real.

        `setuptools-scm` derives the version from the nearest tag. A clone
        with no tags cannot have one, so every built page carries a guess.
        """
        jobs = ((load(READTHEDOCS).get("build") or {}).get("jobs") or {})
        after = " ".join(jobs.get("post_checkout") or [])

        assert "--tags" in after, (
            "The docs build does not fetch tags, so setuptools-scm has no "
            "version to find and the pages will advertise a guess. Add a "
            "`build.jobs.post_checkout` step running `git fetch --tags "
            "--force`.")

    def test_it_deepens_the_shallow_clone(self) -> None:
        """Tags alone are not enough: the distance to the nearest tag is
        counted in commits, and a shallow clone does not have them."""
        jobs = ((load(READTHEDOCS).get("build") or {}).get("jobs") or {})
        after = " ".join(jobs.get("post_checkout") or [])

        assert "--unshallow" in after, (
            "The clone is still shallow, so even with tags fetched the "
            "commit distance -- and therefore the version -- can be wrong.")


class TestTheFallbackDoesNotImpersonateARelease:

    def read_fallback(self) -> str:
        try:
            import tomllib
        except ModuleNotFoundError:  # Python 3.9/3.10
            import tomli as tomllib  # type: ignore[no-redef]

        settings = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        return str(settings["tool"]["setuptools_scm"].get(
            "fallback_version", ""))

    def test_there_is_one(self) -> None:
        assert self.read_fallback()

    def test_it_says_it_does_not_know(self) -> None:
        """A build that cannot determine its version should say so rather
        than name a number.

        `0.1.0` sat here while the project shipped 2.5.5. A fallback that
        looks like a release makes a build that failed to find its version
        indistinguishable from one that found it -- which is how a docs site
        came to advertise 0.1 in good faith.
        """
        fallback = self.read_fallback()
        from packaging.version import Version

        parsed = Version(fallback)
        assert parsed.local, (
            f"`fallback_version = {fallback!r}` is shaped like a release. "
            "Give it a local segment -- `0.0.0+unknown-version` -- so a "
            "build that could not find its version is recognisable as one.")

    def test_it_is_a_version_python_packaging_accepts(self) -> None:
        """Recognisable as unknown, and still parseable: a fallback that
        cannot be parsed breaks the build it was meant to rescue."""
        from packaging.version import InvalidVersion, Version

        try:
            Version(self.read_fallback())
        except InvalidVersion as bad:  # pragma: no cover - the failure text
            pytest.fail(f"fallback_version is not PEP 440: {bad}")

    def test_it_is_below_every_release(self) -> None:
        """So that anything comparing versions treats an unknown build as
        older than the real ones rather than newer."""
        from packaging.version import Version

        assert Version(self.read_fallback()) < Version("2.0.0")
