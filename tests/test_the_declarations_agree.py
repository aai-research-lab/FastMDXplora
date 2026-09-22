"""Three files declare this package's dependencies, and they drift.

`pyproject.toml`, `environment.yml` and `recipes/fastmdxplora/recipe.yaml`
each state a floor for the same packages, and each has at some point been
corrected on its own. The OpenMM floor is the recorded case: pyproject
explains at length why `>=8.0` is below the real one and corrects it to
`>=8.2`, and the other two kept saying 8.0 for four releases.

CI cannot catch a floor. All nine legs resolve the newest version of
everything, so no leg has ever run at a version this package claims to
support. That makes these declarations the only statement of the range,
and a wrong one is not visible until somebody outside the lab installs it.

Same species as the `cuda-version` lesson in the TODO: a constraint stated
in one place and enforced nowhere travels to the next environment as a
comment, not as a rule.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests._toml import load_toml

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
ENVIRONMENT = ROOT / "environment.yml"
RECIPE = ROOT / "recipes" / "fastmdxplora" / "recipe.yaml"


def _pyproject() -> dict:
    return load_toml(PYPROJECT)


def _floor(text: str, package: str) -> str | None:
    """The version floor declared for `package` anywhere in a text file."""
    # Covers a TOML list entry (`"numpy>=2.0",`), a conda list entry
    # (`  - numpy>=2.0`) and a recipe entry (`    - numpy >=2.0`).
    match = re.search(
        rf"""^\s*(?:-\s*)?["']?{re.escape(package)}\s*>=\s*([0-9][0-9.]*)""",
        text, re.MULTILINE)
    return match.group(1) if match else None


class TestTheExtrasTableIsClosed:
    """`[all]` may only name extras that exist."""

    def test_all_names_only_real_extras(self) -> None:
        extras = _pyproject()["project"]["optional-dependencies"]
        named: set[str] = set()
        for requirement in extras["all"]:
            for group in re.findall(r"\[([^\]]*)\]", requirement):
                named.update(part.strip() for part in group.split(","))

        undefined = sorted(named - set(extras))

        # pip prints "does not provide the extra 'plumed'" and installs
        # anyway, so this failed as a warning for as long as it existed.
        assert not undefined, (
            f"[all] names extras that do not exist: {undefined}. pip warns "
            f"and continues, so the install silently lacks them."
        )

    def test_plumed_is_not_offered_as_an_extra(self) -> None:
        """Stated as its own assertion because the comment above the table
        says so and the table did not."""
        extras = _pyproject()["project"]["optional-dependencies"]
        assert "plumed" not in extras
        assert not any("plumed" in req for req in extras["all"])


class TestNumpyIsDeclaredHighEnoughToRun:

    def test_the_floor_admits_np_trapezoid(self) -> None:
        """`np.trapezoid` is the NumPy 2.0 rename of `np.trapz`.

        `binding_free_energy()` calls it, so any NumPy the declarations admit
        must have it. The floor said 1.22 in all three files.
        """
        source = (ROOT / "src" / "fastmdxplora" / "simulation"
                  / "binding.py").read_text(encoding="utf-8")
        if "np.trapezoid" not in source:
            pytest.skip("binding.py no longer calls np.trapezoid")

        floor = _floor(PYPROJECT.read_text(encoding="utf-8"), "numpy")
        assert floor is not None
        assert int(floor.split(".")[0]) >= 2, (
            f"binding.py calls np.trapezoid, which needs NumPy 2.0, but the "
            f"declared floor is {floor}."
        )


class TestTheThreeFilesAgree:

    @pytest.mark.parametrize("package", ["numpy", "openmm"])
    def test_the_floors_match(self, package: str) -> None:
        floors = {
            "pyproject.toml": _floor(
                PYPROJECT.read_text(encoding="utf-8"), package),
            "environment.yml": _floor(
                ENVIRONMENT.read_text(encoding="utf-8"), package),
            "recipe.yaml": _floor(RECIPE.read_text(encoding="utf-8"), package),
        }
        stated = {k: v for k, v in floors.items() if v is not None}
        if len(stated) < 2:
            pytest.skip(f"{package} is declared in fewer than two files")

        assert len(set(stated.values())) == 1, (
            f"{package} floors disagree: {stated}. A floor corrected in one "
            f"file and not the others is how openmm stayed at 8.0 in two of "
            f"three for four releases."
        )


#: Standard-library modules added after 3.9, with the version that added
#: them. Short because few have been: this is the complete list of stdlib
#: modules introduced in 3.10 through 3.13 that anything here would
#: plausibly import. It is a table and not a scan because Python ships no
#: machine-readable record of when a module entered the standard library --
#: `sys.stdlib_module_names` says what exists on the running interpreter,
#: not since when.
STDLIB_ADDED_IN = {
    "tomllib": (3, 11),
    "dbm.sqlite3": (3, 13),
}


def _declared_python_floor() -> tuple[int, int]:
    """The lowest Python `requires-python` admits."""
    spec = _pyproject()["project"]["requires-python"]
    match = re.search(r">=\s*(\d+)\.(\d+)", spec)
    assert match is not None, f"cannot read a floor from requires-python: {spec!r}"
    return int(match.group(1)), int(match.group(2))


def _module_level_imports(path: Path) -> set[str]:
    """Top-level module names imported at module scope, unguarded.

    Module scope only, deliberately. An import inside a function or inside
    `try: ... except ImportError:` fails one test; an import at module
    scope on a version that lacks it is a *collection* error, and pytest
    abandons the entire run when it hits one.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:  # direct children only: not nested, not guarded
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


class TestTheDeclaredPythonFloorIsReal:
    """`requires-python` is a promise, and one import can break it silently.

    `tests/test_the_declarations_agree.py` imported `tomllib` at module
    scope. `tomllib` is stdlib from 3.11; the floor here is 3.10. Every
    local run and eight of nine CI legs were green, because they all run
    3.11 or newer. The 3.9 leg reported `3414/3519 tests collected ... 1
    error` and stopped -- one import, and 3,414 tests did not run.

    Same species as the openmm floor two classes up, and as the
    `cuda-version` lesson in the TODO: a constraint declared in one place
    and enforced nowhere travels to the next environment as a comment.
    """

    def test_no_module_scope_import_reaches_past_the_floor(self) -> None:
        floor = _declared_python_floor()
        reaching: dict[str, str] = {}
        for path in sorted((*(ROOT / "src").rglob("*.py"),
                            *(ROOT / "tests").rglob("*.py"))):
            for name in _module_level_imports(path):
                added = STDLIB_ADDED_IN.get(name)
                if added is not None and added > floor:
                    version = ".".join(str(part) for part in added)
                    reaching[str(path.relative_to(ROOT))] = f"{name} (added in {version})"

        floor_text = ".".join(str(part) for part in floor)
        assert not reaching, (
            f"requires-python declares >={floor_text}, but these modules "
            f"import a newer standard library at module scope, which is a "
            f"collection error rather than a test failure: {reaching}. "
            f"Import it inside the function, or via a compatibility module "
            f"with the older package declared -- see tests/_toml.py."
        )

    def test_the_toml_parser_is_chosen_in_one_place(self) -> None:
        """`tomllib` reached four modules before; it belongs in one."""
        importers = sorted(
            str(path.relative_to(ROOT))
            for path in (ROOT / "tests").rglob("*.py")
            if path.name != "_toml.py"
            and any(name.split(".")[0] in {"tomllib", "tomli"}
                    for name in _module_level_imports(path))
        )
        assert not importers, (
            f"these modules import a TOML parser directly: {importers}. "
            f"Use `from tests._toml import load_toml`, so the 3.11 split is "
            f"decided once."
        )

    def test_the_compatibility_module_parses(self) -> None:
        """The shim is exercised, not merely imported."""
        from tests._toml import load_toml

        assert load_toml(PYPROJECT)["project"]["name"] == "fastmdxplora"


class TestEveryDeclarationOfTheFloorAgrees:
    """The floor is said in six places, and they drifted: the floor moved
    to 3.10 because OpenMM stopped publishing wheels for 3.9, and each
    place had to be found by hand. Read from `requires-python`, checked
    everywhere else it is written."""

    def test_the_classifiers_start_at_the_floor(self) -> None:
        major, minor = _declared_python_floor()
        listed = sorted(
            tuple(int(x) for x in c.rsplit("::", 1)[1].strip().split("."))
            for c in _pyproject()["project"]["classifiers"]
            if c.startswith("Programming Language :: Python :: 3.")
        )
        assert listed[0] == (major, minor), listed

    def test_the_ci_matrix_starts_at_the_floor(self) -> None:
        import yaml

        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8"))
        versions = workflow["jobs"]["test"]["strategy"]["matrix"]["python-version"]
        lowest = min(tuple(int(x) for x in str(v).split(".")) for v in versions)
        assert lowest == _declared_python_floor(), versions

    def test_the_environment_file_and_the_docs_say_the_floor(self) -> None:
        major, minor = _declared_python_floor()
        floor = f"{major}.{minor}"
        assert f"python>={floor}" in (ROOT / "environment.yml").read_text(encoding="utf-8")
        assert f"python-{floor}%2B" in (ROOT / "README.md").read_text(encoding="utf-8")
        for doc in ("docs/installation.md", "docs/api.md"):
            assert f"Python {floor} to " in (ROOT / doc).read_text(encoding="utf-8"), doc
        assert f"Python ≥ {floor}." in (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
