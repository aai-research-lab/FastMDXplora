"""A setup file that says "full dependency stack" has to install one.

`environment.yml` is the recommended route, because two packages the
simulation phases need -- the PLUMED--OpenMM coupling and the Open Force
Field toolkit -- have no PyPI distribution, and its first paragraph offers
the whole stack. It did not have one. `markdown` and `umap-learn` were both
absent, so anyone who set up from that file got a PDF report that refused to
render and a UMAP analysis that could not run, while the file said otherwise.

Both refusals are polite and name the missing package, which is the code
behaving correctly. That is exactly what makes the gap hard to notice from
the inside: nothing is broken, a documented setup route simply does not
deliver what it documents, and the person who followed it concludes the
feature does not work.

So the claim is checked rather than asserted. The requirements come from
`pyproject.toml`, which is where they are declared, and the file has to name
every one of them at a floor no lower. A new extra, or a floor raised in
`pyproject.toml`, fails here instead of reaching a user as a package that
turned out not to be installed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
ENVIRONMENT = ROOT / "environment.yml"

#: The extras `environment.yml` says it covers: `all` plus `pdf`.
#:
#: `validation` is deliberately not among them. MDAnalysis and ProLIF are
#: installed for the cross-tool comparison, and the point of that comparison
#: is that the two stacks are separate; putting them in the environment this
#: package runs from would undo it.
CLAIMED = ("pdf", "md", "ligand", "amber", "umap")

#: Requirements that reach the environment under another name, or not at all.
#: Kept deliberately short: a long table here would mean the two files have
#: drifted into different vocabularies.
ELSEWHERE: dict[str, str | None] = {}


def load_pyproject() -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.9/3.10
        import tomli as tomllib  # type: ignore[no-redef]
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def name_of(requirement: str) -> str:
    """The package a requirement names, lowercased.

    Conda-forge and PyPI agree on every name this project uses once case is
    taken out -- `NetCDF4` and `netcdf4`, `PyYAML` and `pyyaml` -- so there is
    no translation table to go stale.
    """
    return re.split(r"[<>=!~;\[]", requirement.strip(), maxsplit=1)[0].strip().lower()


def floor_of(requirement: str) -> str | None:
    """The lowest version a requirement allows, or None where it allows any."""
    found = re.search(r">=\s*([0-9][0-9A-Za-z.*+!-]*)", requirement)
    return found.group(1) if found else None


def at_least(given: str | None, needed: str | None) -> bool:
    """Whether `given` is a floor no lower than `needed`."""
    if needed is None:
        return True
    if given is None:
        return False
    from packaging.version import InvalidVersion, Version

    try:
        return Version(given) >= Version(needed)
    except InvalidVersion:  # pragma: no cover -- a floor nobody can compare
        return given == needed


def what_is_required() -> dict[str, str | None]:
    """Every package the claim covers, mapped to the floor it needs."""
    project = load_pyproject()["project"]
    requirements = list(project.get("dependencies", []))
    extras = project.get("optional-dependencies", {})
    for extra in CLAIMED:
        assert extra in extras, (
            f"`{extra}` is claimed by environment.yml and is not an extra in "
            "pyproject.toml, so the claim names something that no longer "
            "exists."
        )
        requirements += [r for r in extras[extra]
                         if not r.startswith("fastmdxplora")]

    needed: dict[str, str | None] = {}
    for requirement in requirements:
        # A marker such as `; python_version < "3.11"` makes a requirement
        # conditional, and a conda file cannot express the condition.
        if ";" in requirement:
            continue
        package = name_of(requirement)
        floor = floor_of(requirement)
        if package not in needed or at_least(floor, needed[package]):
            needed[package] = floor
    return needed


def what_is_installed() -> dict[str, str | None]:
    """Every package `environment.yml` names, mapped to its floor."""
    import yaml

    document = yaml.safe_load(ENVIRONMENT.read_text(encoding="utf-8"))
    installed: dict[str, str | None] = {}
    for entry in document.get("dependencies") or []:
        if isinstance(entry, str):
            installed[name_of(entry)] = floor_of(entry)
        elif isinstance(entry, dict):
            # A `pip:` block, which conda hands to pip verbatim.
            for nested in entry.get("pip") or []:
                installed[name_of(nested)] = floor_of(nested)
    return installed


class TestTheFileCoversWhatItSays:

    def test_every_claimed_requirement_is_named(self) -> None:
        needed = what_is_required()
        installed = what_is_installed()
        missing = sorted(
            package for package in needed
            if package not in installed
            and ELSEWHERE.get(package, package) is not None
            and ELSEWHERE.get(package, package) not in installed
        )
        assert not missing, (
            "environment.yml offers the core requirements and the "
            f"{', '.join(CLAIMED)} extras, and does not install: "
            f"{', '.join(missing)}. Add them, or narrow the claim -- a "
            "documented setup route that quietly leaves a feature "
            "uninstallable is worse than one that says what it omits."
        )

    @pytest.mark.parametrize("package", sorted(what_is_required()))
    def test_the_floors_are_no_lower_than_pyprojects(self, package) -> None:
        """A conda environment and a pip install should be the same software.

        Left unpinned, `rdkit` here and `rdkit>=2023.3` there are two
        different stacks wearing one name, and the difference surfaces as
        behaviour nobody can reproduce from the other route.
        """
        needed = what_is_required()[package]
        here = what_is_installed()
        installed = here.get(ELSEWHERE.get(package, package))
        if needed is None:
            pytest.skip(f"pyproject.toml sets no floor for {package}, so "
                        "there is nothing to hold this file to.")
        if ELSEWHERE.get(package, package) not in here:
            pytest.skip(f"{package} is absent altogether, which "
                        "test_every_claimed_requirement_is_named reports.")
        assert at_least(installed, needed), (
            f"pyproject.toml requires {package}>={needed} and "
            f"environment.yml asks for "
            f"{package + '>=' + installed if installed else package} — so a "
            "conda environment can satisfy this file with a version the "
            "package does not support."
        )


class TestTheClaimIsStillTrue:

    def test_the_two_conda_only_packages_are_there(self) -> None:
        """The reason this file exists at all: neither has a PyPI
        distribution, so no extra can express them and no pip install can
        supply them."""
        installed = what_is_installed()
        assert "openmm-plumed" in installed
        assert "openff-toolkit" in installed

    def test_the_cross_tool_stack_is_kept_out(self) -> None:
        """Not an omission. The comparison in the validation program is
        between two separately installed stacks, and it stops meaning
        anything if this environment carries both."""
        installed = what_is_installed()
        assert "mdanalysis" not in installed
        assert "prolif" not in installed

    def test_the_file_says_which_extras_it_covers(self) -> None:
        """So that a reader learns the scope from the file rather than from
        this test."""
        text = ENVIRONMENT.read_text(encoding="utf-8")
        for extra in CLAIMED:
            assert f"`{extra}`" in text or f" {extra}," in text or \
                f" {extra} " in text, (
                    f"environment.yml does not mention the `{extra}` extra it "
                    "is held to.")
        assert "validation" in text, (
            "environment.yml should say that the validation extra is left "
            "out on purpose, so the omission reads as a decision.")
