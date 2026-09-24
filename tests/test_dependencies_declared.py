"""Every module the package imports must be declared somewhere.

Three dependencies have reached users undeclared: scipy, arriving through
scikit-learn; netCDF4, which mdtraj declares but conda-forge does not install;
and Pillow, arriving through python-pptx. Each worked until it didn't, and
each was found by accident rather than by a check.

This test is that check. It compares what the source actually imports against
what pyproject.toml and the conda recipe declare, so an undeclared dependency
fails here rather than in somebody's environment.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

# The check reads source files, so its verdict is the same on every Python.
# `sys.stdlib_module_names` arrived in 3.10; rather than carry a duplicate
# list of standard-library modules for 3.9, run the check where the
# interpreter can answer the question itself.
pytestmark = pytest.mark.skipif(
    not hasattr(sys, "stdlib_module_names"),
    reason="needs sys.stdlib_module_names (Python 3.10+); result is version-independent",
)

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "src" / "fastmdxplora"

#: Import name -> distribution name, where they differ.
DISTRIBUTION = {
    "yaml": "pyyaml",
    "sklearn": "scikit-learn",
    "pptx": "python-pptx",
    "PIL": "pillow",
    "openff": "openff-toolkit",
    "openmmplumed": "openmm-plumed",
    "umap": "umap-learn",
    "netCDF4": "netcdf4",
}

#: Imports guarded by try/except with an actionable message, and reachable
#: only when the user asks for the feature. These need not be installed.
OPTIONAL = {
    "umap-learn",      # one of three dimensionality-reduction methods
    # The cross-tool comparison's references. Absent from the conda recipe
    # on purpose: a comparison is only independent while the reference is
    # something the user already had or installed separately, and a run
    # dependency would put both on every machine and let them drift
    # together. Reachable as the `validation` extra.
    "MDAnalysis",
    "prolif",
}

#: Imported, and deliberately absent from pyproject.toml because pip cannot
#: install them at all. Naming one in an extra does not make it reachable; it
#: makes the whole extra fail, which is what `pip install
#: "fastmdxplora[ligand]"` did. They are declared in the conda recipe, which
#: is where they can be had, and the import raises with that instruction.
CONDA_ONLY = {
    "openff-toolkit": "no PyPI distribution; conda-forge only",
    "openmm-plumed": "no PyPI distribution; conda-forge only",
}


def _is_type_checking(test: ast.expr) -> bool:
    return ((isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
            or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"))


def _imported_distributions() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # An import under `if TYPE_CHECKING:` is read by type checkers and
        # never run, so nothing needs installing for it. Its `else:` runs.
        never_run = {id(inner) for block in ast.walk(tree)
                     if isinstance(block, ast.If) and _is_type_checking(block.test)
                     for statement in block.body for inner in ast.walk(statement)}
        for node in ast.walk(tree):
            if id(node) in never_run:
                continue
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                if name in sys.stdlib_module_names or name == "fastmdxplora":
                    continue
                dist = DISTRIBUTION.get(name, name)
                found.setdefault(dist, set()).add(path.name)
    return found


def _requirement_names(entries) -> set[str]:
    """The distribution names in a list of requirement strings."""
    return {
        re.split(r"[><=!~;\s\[]", str(entry), maxsplit=1)[0].strip().lower()
        for entry in entries
        if str(entry).strip()
    }


def _pyproject() -> dict:
    from tests._toml import load_toml

    return load_toml(REPO / "pyproject.toml")


def _core_dependencies() -> set[str]:
    """Only `[project] dependencies` -- what a bare install actually gets."""
    return _requirement_names(_pyproject()["project"]["dependencies"])


def _declared_in_pyproject() -> set[str]:
    """Every distribution named anywhere: core plus every extra.

    Parsed as TOML. It used to slice the file from `dependencies = [` to
    `[project.urls]`, which spans the whole `[project.optional-dependencies]`
    table -- so an import satisfied only by an extra counted as declared, and
    `rdkit`, imported unguarded in `analysis/ligand_chemistry.py` and present
    only in `[ligand]` and `[test]`, passed this check while raising
    ModuleNotFoundError on a plain install. The distinction is available now
    through `_core_dependencies`.
    """
    project = _pyproject()["project"]
    names = _requirement_names(project["dependencies"])
    for entries in project.get("optional-dependencies", {}).values():
        names |= _requirement_names(entries)
    return {name.split("[")[0] for name in names if not name.startswith("fastmdxplora")}


def test_every_import_is_declared_in_pyproject() -> None:
    declared = _declared_in_pyproject()
    missing = {
        dist: sorted(files)
        for dist, files in _imported_distributions().items()
        if dist.lower() not in declared
        and dist not in OPTIONAL
        and dist not in CONDA_ONLY
    }
    assert not missing, (
        "these are imported but declared nowhere in pyproject.toml: "
        f"{missing}. Add them to dependencies, or to an extra if the import "
        "is guarded and the feature is optional."
    )


def test_the_conda_recipe_matches_pyproject() -> None:
    """A conda install must be able to do what a pip install can.

    The conda package exists so that one command gives a working four-phase
    pipeline, so anything the source imports unguarded belongs in it.
    """
    recipe_path = REPO / "recipes" / "fastmdxplora" / "recipe.yaml"
    if not recipe_path.is_file():  # pragma: no cover - recipe is optional
        return
    recipe = recipe_path.read_text(encoding="utf-8")
    run = re.search(r"  run:\n(.*?)\n\ntests:", recipe, re.S)
    assert run, "could not read the run requirements from the recipe"
    declared = {
        line.strip().lstrip("- ").split()[0].lower()
        for line in run.group(1).splitlines()
        if line.strip().startswith("- ")
    }
    # conda splits matplotlib to avoid pulling a GUI toolkit.
    aliases = {"matplotlib": "matplotlib-base"}

    missing = sorted(
        dist for dist in _imported_distributions()
        if dist not in OPTIONAL
        and aliases.get(dist, dist).lower() not in declared
    )
    assert not missing, (
        f"imported but absent from the conda recipe: {missing}. A conda "
        "install would fail at the point of use."
    )


def test_the_recipe_says_where_the_package_is_actually_built() -> None:
    """The recipe in this repository is a reference copy; the conda-forge
    feedstock builds the package.

    A dependency added to one and not the other gives a conda install that
    fails at the point of use -- which is what happened when WeasyPrint was
    added to the PyPI extras and nowhere else. The relationship is written in
    the recipe so the next release does not have to rediscover it.
    """
    from pathlib import Path

    recipe = Path(__file__).resolve().parents[1] / "recipes" / "fastmdxplora" / "recipe.yaml"
    if not recipe.is_file():  # pragma: no cover - the recipe is optional
        return
    text = recipe.read_text(encoding="utf-8")
    assert "feedstock" in text, (
        "the recipe should say that the feedstock is what builds the package"
    )


def test_a_conda_only_dependency_is_still_in_the_recipe() -> None:
    """Something pip cannot install has to be gettable somewhere.

    `pip install "fastmdxplora[ligand]"` named openff-toolkit, which has no
    PyPI distribution, so the command failed outright rather than installing
    what pip could. It is out of the extra and in the recipe, and this checks
    it did not simply vanish.
    """
    recipe_path = REPO / "recipes" / "fastmdxplora" / "recipe.yaml"
    if not recipe_path.is_file():  # pragma: no cover - the recipe is optional
        return
    recipe = recipe_path.read_text(encoding="utf-8")
    for name in CONDA_ONLY:
        assert name in recipe, (
            f"{name} cannot be installed by pip and is not in the recipe "
            "either, so there is nowhere to get it"
        )


def test_the_reason_is_recorded_for_each() -> None:
    """So the list cannot become a place to hide an undeclared import."""
    assert CONDA_ONLY
    for name, reason in CONDA_ONLY.items():
        assert reason and len(reason) > 10, name


def test_the_alias_package_carries_the_released_version() -> None:
    """`fastmdx` is a thin alias for `fastmdxplora`, and its version is
    written in a file where the main package takes its own from the git tag.

    A hand-written version beside a derived one drifts, and this one did: the
    release workflow refused a 2.3.0 tag because the alias still said 2.2.0.
    That check fires after tagging, which is too late to be comfortable, so
    this one fires in the ordinary test run -- against the changelog, which is
    the thing a release is cut from.
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    shim = repo / "shim-package" / "pyproject.toml"
    if not shim.is_file():  # pragma: no cover - the alias is optional
        return

    text = shim.read_text(encoding="utf-8")

    # It takes the tag now, like the package it aliases, so there is no
    # written version left to drift. What is worth checking is that the
    # wiring is there: a literal creeping back in is the fault this test was
    # written for, and it would be silent until a release refused a tag.
    written = re.search(r'^version = "([^"]+)"', text, re.M)
    assert written is None, (
        f"the alias has gone back to a written version ({written.group(1)}). "
        "It derives from the git tag; a literal beside a derived one drifts, "
        "and the release workflow is where that gets noticed."
    )
    assert 'dynamic = ["version"]' in text
    assert "[tool.setuptools_scm]" in text
    assert 'root = ".."' in text, (
        "the tag lives in the repository above this directory"
    )


def test_the_conda_recipe_declares_the_version_being_released() -> None:
    """The reference recipe carried 2.3.0 while the changelog said 2.4.0.

    It is the copy a dependency is added to before the feedstock, so somebody
    reading it to check what 2.4.0 requires would be reading a file that says
    it is a different release. The same drift the alias check exists to catch,
    one file over -- so it gets the same check, against the same source of
    truth.

    The feedstock's own version is bumped by the bot from the PyPI upload;
    this is only about the copy that lives here.
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    recipe = repo / "recipes" / "fastmdxplora" / "recipe.yaml"
    if not recipe.is_file():  # pragma: no cover - the recipe is optional
        return

    written = re.search(r'^  version: "([^"]+)"',
                        recipe.read_text(encoding="utf-8"), re.M)
    assert written, "the recipe declares no version"

    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    released = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M)
    assert released, "the changelog names no released version"

    assert written.group(1) == released.group(1), (
        f"the recipe says {written.group(1)} and the changelog's latest "
        f"release is {released.group(1)}. Bump "
        "recipes/fastmdxplora/recipe.yaml before tagging."
    )


def test_info_reports_every_backend_the_software_reaches_for() -> None:
    """`fastmdx info` exists to say what will work on this machine.

    It listed two backends where the software reaches for eight, so a PyPI
    install reported both present and said nothing about the OpenFF toolkit --
    which a protein-ligand setup needs and which pip cannot install. Somebody
    reading it would conclude their install was complete and find out
    otherwise three phases later.
    """
    from fastmdxplora.cli.main import _BACKENDS

    reported = {
        import_name
        for _group, backends in _BACKENDS
        for _display, import_name, _hint in backends
    }
    for needed in ("openmm", "pdbfixer", "openff.toolkit", "openmmforcefields",
                   "rdkit", "propka", "am1bcc", "weasyprint", "markdown"):
        assert needed in reported, f"info does not mention {needed}"


def test_each_backend_says_where_to_get_it() -> None:
    """And says something that works: naming a pip extra for the OpenFF
    toolkit sent people to a command that could not succeed."""
    from fastmdxplora.cli.main import _BACKENDS

    for _group, backends in _BACKENDS:
        for display, import_name, hint in backends:
            assert hint, display
            if import_name == "openff.toolkit":
                assert "conda" in hint and "pip" not in hint, (
                    "the toolkit is not on PyPI, so a pip hint cannot work"
                )
            if import_name == "am1bcc":
                assert hint == "conda install -c conda-forge ambertools", (
                    "AmberTools computes the charges and is not on PyPI"
                )


def test_backends_are_grouped_by_what_they_are_for() -> None:
    """A trajectory analysis does not care that OpenMM is absent, and saying
    so unqualified reads as a broken install."""
    from fastmdxplora.cli.main import _BACKENDS

    groups = {group for group, _backends in _BACKENDS}
    assert len(groups) >= 3
    assert all(group and group[0].islower() for group in groups), (
        "the groups read as a phrase after the heading"
    )


def test_a_backend_is_probed_in_a_process_of_its_own(tmp_path) -> None:
    """Importing is the only honest test of whether a backend will load, and
    the failure is not always quiet or catchable.

    WeasyPrint without Pango raises from the dynamic loader and prints five
    lines about its installation guide on the way. Redirecting Python's stderr
    did not stop them and neither did redirecting the file descriptor -- and
    the test written for that fix passed only because it faked the message
    with a print, which checks the assumption rather than the behaviour.

    A subprocess ends the argument, and a backend that fails hard cannot take
    this command with it. Two that end the interpreter on import, one by
    exiting and one by segfaulting as a C extension built against the wrong
    ABI does: each is named and reported broken, the backends after them are
    still probed, and the command lives. Probed from a child process of its
    own here, so that if it did not live, this is one failing test rather
    than a test run that stops.
    """
    import json
    import os
    import subprocess
    import sys

    (tmp_path / "pretend_exits.py").write_text("import os\nos._exit(3)\n", encoding="utf-8")
    (tmp_path / "pretend_crashes.py").write_text(
        "import os, signal\nos.kill(os.getpid(), signal.SIGSEGV)\n", encoding="utf-8")
    names = ["json", "pretend_exits", "os", "no_such_module_at_all"]
    if hasattr(__import__("signal"), "SIGSEGV") and sys.platform != "win32":
        names.insert(3, "pretend_crashes")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), str(Path(__file__).resolve().parents[1] / "src"),
         environment.get("PYTHONPATH", "")])
    finished = subprocess.run(
        [sys.executable, "-c",
         "import json, sys\n"
         "from fastmdxplora.cli.main import _probe_backends\n"
         f"print(json.dumps(_probe_backends(tuple({names!r}))))\n"],
        capture_output=True, text=True, env=environment, timeout=300)
    assert finished.returncode == 0, (
        f"a backend took the command with it: exit code {finished.returncode}, "
        f"{finished.stderr[-300:]!r}")
    found = json.loads(finished.stdout.strip().splitlines()[-1])
    assert found["json"][0] == "installed" and found["os"][0] == "installed"
    assert found["no_such_module_at_all"][0] == "missing"
    assert found["pretend_exits"] == ["broken", "ended the interpreter on import (exit code 3)"]
    if "pretend_crashes" in found:
        assert found["pretend_crashes"][0] == "broken"
        assert "SIGSEGV" in found["pretend_crashes"][1]

def test_it_reports_what_is_there_and_what_is_not() -> None:
    from fastmdxplora.cli.main import _probe_backends

    found = _probe_backends(("json", "no_such_module_at_all"))
    assert found["json"][0] == "installed"
    assert found["no_such_module_at_all"][0] == "missing"


def test_a_backend_that_raises_on_import_is_reported_broken(tmp_path) -> None:
    """The case a real machine hits: installed, and will not load."""
    import os

    from fastmdxplora.cli.main import _probe_backends

    (tmp_path / "pretend_backend.py").write_text(
        "import sys\n"
        "sys.stderr.write('a library complaining loudly\\n')\n"
        "raise OSError(\"cannot load library 'libgobject-2.0-0'\")\n",
        encoding="utf-8")

    original = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path)] + ([original] if original else []))
    try:
        state, detail = _probe_backends(("pretend_backend",))["pretend_backend"]
    finally:
        if original:
            os.environ["PYTHONPATH"] = original
        else:
            os.environ.pop("PYTHONPATH", None)

    assert state == "broken"
    assert "libgobject" in detail


def test_the_probe_cannot_be_interrupted_by_what_it_probes(capfd) -> None:
    """The point of the subprocess: whatever the backend writes, and however
    far down it writes it, stays out of this command's output."""

    from fastmdxplora.cli.main import _probe_backends

    _probe_backends(("json",))
    captured = capfd.readouterr()
    assert "complaining" not in captured.err



def test_a_probe_that_cannot_run_still_answers(capsys) -> None:
    """A table of "unknown" is worse than a noisy answer.

    The subprocess probe failed on a real machine and reported nothing about
    every backend, because the fallback swallowed the reason -- which is the
    defect this software exists to avoid, in the code written to avoid it. It
    now says why the subprocess could not answer and checks in this process
    instead.
    """
    import importlib
    import sys

    module = importlib.import_module("fastmdxplora.cli.main")
    real = sys.executable
    sys.executable = "/nonexistent/python"
    try:
        found = module._probe_backends(("json", "no_such_module_at_all"))
    finally:
        sys.executable = real

    assert found["json"][0] == "installed"
    assert found["no_such_module_at_all"][0] == "missing"
    assert "checked in this process" in capsys.readouterr().out


def test_the_reason_the_subprocess_failed_is_named(capsys) -> None:
    """Reporting that something could not be checked, without saying why,
    leaves nothing to act on."""
    import importlib
    import sys

    module = importlib.import_module("fastmdxplora.cli.main")
    real = sys.executable
    sys.executable = "/nonexistent/python"
    try:
        module._probe_backends(("json",))
    finally:
        sys.executable = real

    printed = capsys.readouterr().out
    assert "FileNotFoundError" in printed or "No such file" in printed


def test_a_backend_writing_to_stdout_cannot_corrupt_the_answer(tmp_path) -> None:
    """WeasyPrint writes its five-line complaint to stdout, not stderr.

    That took four attempts to notice. Redirecting Python's stderr did not
    quiet it, redirecting the file descriptor did not either, and moving the
    probe to a subprocess then failed to parse its own output -- because the
    answer was coming back on the channel the message was using. It travels by
    file now, so nothing a backend prints, on any stream, can reach it.
    """
    import importlib
    import os

    module = importlib.import_module("fastmdxplora.cli.main")

    (tmp_path / "loud_backend.py").write_text(
        "import sys\n"
        "sys.stdout.write('\\n-----\\nsomething loud on stdout\\n-----\\n')\n"
        "sys.stdout.flush()\n"
        "raise OSError(\"cannot load library 'libgobject-2.0-0'\")\n",
        encoding="utf-8")

    original = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path)] + ([original] if original else []))
    try:
        found = module._probe_backends(("loud_backend", "json"))
    finally:
        if original:
            os.environ["PYTHONPATH"] = original
        else:
            os.environ.pop("PYTHONPATH", None)

    assert found["loud_backend"][0] == "broken"
    assert "libgobject" in found["loud_backend"][1]
    assert found["json"][0] == "installed", (
        "the noise must not take the other answers with it"
    )


def test_the_answer_does_not_come_back_on_stdout(tmp_path, monkeypatch) -> None:
    """Which is the channel a failing backend is most likely to use. A
    backend that prints a forged answer there, claiming json is missing,
    changes nothing about what the probe reports."""
    from fastmdxplora.cli.main import _probe_backends

    (tmp_path / "pretend_liar.py").write_text(
        "import sys\n"
        "sys.stdout.write('{\"json\": [\"missing\", \"forged\"]}\\n')\n"
        "sys.stdout.flush()\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    found = _probe_backends(("pretend_liar", "json"))
    assert found == {"pretend_liar": ("installed", ""), "json": ("installed", "")}

class TestAPhaseSaysWhetherItWillRun:
    """`fastmdx info` reported setup and simulation as "available" three lines
    above OpenMM and PDBFixer as "missing".

    "Available" meant only that the module imported and had a callable `run`,
    which is true of a phase that cannot run at all. So one screen contradicted
    itself, and somebody reading the first block would conclude their install
    was complete -- the same defect this command was fixed for one block down,
    left in the block above it.

    Found on a real PyPI install of 2.4.0, which is the environment the
    question is asked in.
    """

    @staticmethod
    def _probed(**state):
        from fastmdxplora.cli.main import _BACKENDS

        return {import_name: (state.get(import_name, "missing"), "")
                for _group, backends in _BACKENDS
                for _display, import_name, _hint in backends}

    @staticmethod
    def _everything():
        from fastmdxplora.cli.main import _BACKENDS

        return {import_name: ("installed", "")
                for _group, backends in _BACKENDS
                for _display, import_name, _hint in backends}

    def test_a_pip_install_says_what_the_md_phases_need(self) -> None:
        from fastmdxplora.cli.main import _phase_status

        nothing = self._probed()
        assert _phase_status("setup", nothing) == "needs OpenMM, PDBFixer"
        assert _phase_status("simulation", nothing) == "needs OpenMM, PDBFixer"

    def test_and_that_the_other_two_still_run(self) -> None:
        """Which is what the PyPI package is for: analysis and reporting on a
        trajectory you already have."""
        from fastmdxplora.cli.main import _phase_status

        nothing = self._probed()
        assert _phase_status("analysis", nothing) == "ready"
        assert _phase_status("report", nothing).startswith("ready")

    def test_a_full_install_says_ready_without_qualification(self) -> None:
        from fastmdxplora.cli.main import _phase_status

        everything = self._everything()
        for phase in ("setup", "simulation", "analysis", "report"):
            assert _phase_status(phase, everything) == "ready", phase

    def test_setup_runs_for_a_protein_without_the_ligand_stack(self) -> None:
        """Reporting it unavailable would send somebody installing four
        packages for a run that would have worked."""
        from fastmdxplora.cli.main import _phase_status

        md_only = self._probed(openmm="installed", pdbfixer="installed")
        assert _phase_status("setup", md_only) == "ready (proteins only)"
        assert _phase_status("simulation", md_only) == "ready"

    def test_the_report_says_which_format_it_cannot_write(self) -> None:
        """It still runs -- the PDF is one format of several."""
        from fastmdxplora.cli.main import _phase_status

        no_pdf = self._everything()
        no_pdf["weasyprint"] = ("missing", "")
        assert _phase_status("report", no_pdf) == "ready (no PDF)"

    def test_installed_but_broken_counts_as_needed(self) -> None:
        """A backend that will not load is not one you have."""
        from fastmdxplora.cli.main import _phase_status

        broken = self._everything()
        broken["openmm"] = ("broken", "libOpenMM.so")
        assert _phase_status("simulation", broken) == "needs OpenMM"

    def test_the_two_blocks_cannot_disagree(self) -> None:
        """They read one probe, so a phase reported ready has every backend
        the block below it reports installed. The first version compared
        against 'present' where the probe says 'installed', which reported
        every backend absent -- the same contradiction pointing the other
        way, and it got as far as being run."""
        from fastmdxplora.cli.main import _BACKENDS, _PHASE_NEEDS, _phase_status

        groups = {group for group, _backends in _BACKENDS}
        for phase, (required, conditional) in _PHASE_NEEDS.items():
            for group in required + tuple(g for g, _note in conditional):
                assert group in groups, (phase, group)

        everything = self._everything()
        assert all(_phase_status(phase, everything) == "ready"
                   for phase in _PHASE_NEEDS)


class TestARunSaysWhichCodeMadeIt:
    """A manifest recorded the version string and nothing else.

    setuptools-scm writes that string at *install* time, so an editable
    install carries whatever the version was when `pip install -e .` was last
    run and drifts from then on. A real umbrella study came back stamped
    2.3.0 for seven windows that used shared setup -- a feature 2.3.0 did not
    have -- so the manifest named a version in which the run could not have
    happened, and it is the number the report's reproducibility section
    prints.
    """

    def test_the_checkout_is_found_from_the_package(self) -> None:
        """Not from the working directory. A run started inside another
        repository would otherwise record that repository's commit, which is
        worse than recording nothing: a precise claim about the wrong code.
        Asked from inside another repository."""
        import os
        import subprocess
        import tempfile
        from pathlib import Path

        from fastmdxplora import provenance

        before = provenance.source_checkout()
        here = os.getcwd()
        with tempfile.TemporaryDirectory() as other:
            subprocess.run(["git", "init", "-q", other], check=True)
            os.chdir(other)
            try:
                from_elsewhere = provenance.source_checkout()
            finally:
                os.chdir(here)
        assert from_elsewhere == before
        assert from_elsewhere is None or Path(other).resolve() not in (
            from_elsewhere, *from_elsewhere.parents)

        found = provenance.source_checkout()
        if found is not None:
            # Whatever it found, it is an ancestor of the package holding a
            # .git -- rather than a fixed number of levels up, which the
            # layout is free to change.
            assert (found / ".git").exists()
            assert found in Path(provenance.__file__).resolve().parents

    def test_what_it_records_from_a_checkout(self) -> None:
        from fastmdxplora.provenance import source_checkout, source_provenance

        record = source_provenance()
        if source_checkout() is None:  # pragma: no cover - installed copy
            assert record is None
            return

        assert set(record) <= {"commit", "dirty", "branch"}
        assert len(record["commit"]) == 12
        assert record["dirty"] in (True, False, None)

    def test_an_installed_copy_records_nothing_rather_than_guessing(
        self, monkeypatch
    ) -> None:
        """There is no checkout to ask, and the version string is the whole
        answer because the distribution was built from a tag."""
        from fastmdxplora import provenance

        provenance.source_provenance.cache_clear()
        monkeypatch.setattr(provenance, "source_checkout", lambda: None)
        try:
            assert provenance.source_provenance() is None
        finally:
            provenance.source_provenance.cache_clear()

    def test_a_dirty_tree_is_reported_rather_than_refused(self) -> None:
        """Refusing would block the runs developers make all day. A commit
        beside uncommitted changes does not describe the code that ran, so
        saying so is what keeps the commit from being decorative."""
        from fastmdxplora.provenance import described

        dirty = described({"commit": "abcdef123456", "dirty": True})
        assert "abcdef123456" in dirty
        assert "uncommitted changes" in dirty

        clean = described({"commit": "abcdef123456", "dirty": False})
        assert clean == "abcdef123456"

    def test_an_unknown_state_is_not_reported_as_clean(self) -> None:
        """A failure to ask is not an answer of no."""
        from fastmdxplora.provenance import described

        unknown = described({"commit": "abcdef123456", "dirty": None})
        assert "could not be determined" in unknown

    def test_the_manifest_carries_it(self, tmp_path, monkeypatch) -> None:
        # Written, then read back: the commit a run was made from is in its
        # manifest. In pytest's own folder rather than a TemporaryDirectory:
        # the study keeps its log open while it is the current one, and
        # Windows will not delete an open file when the block ends.
        import json

        from fastmdxplora.orchestrator import FastMDXplora

        record = {"commit": "0123456789abcdef", "dirty": False, "branch": "main"}
        monkeypatch.setattr("fastmdxplora.provenance.source_provenance", lambda: record)
        FastMDXplora(system="1UBQ", output_dir=tmp_path / "study")._write_manifest()
        written = json.loads((tmp_path / "study" / "manifest.json").read_text(encoding="utf-8"))
        assert written["source"] == record

    def test_and_the_reproducibility_section_prints_it(self) -> None:
        """It is where a reader looks, so a manifest holding it and a report
        not showing it would only move the problem. And a copy installed from
        a release, with no checkout, says nothing rather than something."""
        import tempfile
        from types import SimpleNamespace
        from unittest import mock

        from fastmdxplora.report.context import load_phase_context
        from fastmdxplora.report.document import _reproducibility_section

        record = {"commit": "0123456789abcdef", "dirty": True, "branch": "main"}
        with tempfile.TemporaryDirectory() as tmp:
            study = SimpleNamespace(system="1UBQ", output_dir=Path(tmp))
            with mock.patch("fastmdxplora.provenance.source_provenance", return_value=record):
                from_a_checkout = _reproducibility_section(study, load_phase_context(Path(tmp)))
            with mock.patch("fastmdxplora.provenance.source_provenance", return_value=None):
                installed = _reproducibility_section(study, load_phase_context(Path(tmp)))
        line = next(row for row in from_a_checkout.splitlines() if "Source commit" in row)
        assert "0123456" in line and "main" in line
        assert "Source commit" not in installed

def test_the_reference_recipe_carries_every_conda_only_backend() -> None:
    """The conda-forge distribution exists so that one install command makes
    all four phases work; the PyPI package covers analysis and reporting
    alone. Every backend that pip cannot supply must therefore be a run
    dependency here.

    Checked because the feedstock's own recipe is bumped by a bot that changes
    only the version and the checksum. A dependency added here does not travel
    with that bump, so the feedstock sat three versions back with eight fewer
    run dependencies than this file, and merging the bump alone would have
    shipped a conda package without the ligand stack, PDF rendering or PLUMED.
    """
    import re
    from pathlib import Path

    recipe = (Path(__file__).resolve().parents[1]
              / "recipes" / "fastmdxplora" / "recipe.yaml")
    if not recipe.is_file():  # pragma: no cover - the recipe is optional
        return

    text = recipe.read_text(encoding="utf-8")
    run = text[text.index("  run:"):text.index("tests:")]
    declared = set(re.findall(r"^\s+- ([a-z0-9_.-]+)", run, re.M))

    # Everything `fastmdx info` reports and pip cannot install.
    for backend in ("openmm", "pdbfixer", "openff-toolkit", "ambertools",
                    "openmmforcefields", "rdkit", "propka", "weasyprint",
                    "markdown", "openmm-plumed"):
        assert backend in declared, (
            f"{backend} is a conda-only backend and is not a run dependency "
            "of the reference recipe")

    # Declaring a dependency is not the same as it resolving, so the ligand
    # stack is imported by the recipe's own tests: a broken solve then fails
    # the build rather than the first protein-ligand run somebody attempts.
    # Established in the feedstock during review and brought back here.
    for imported in ("openff.toolkit", "openmmforcefields", "rdkit", "propka"):
        assert imported in text, (
            f"the recipe does not import {imported} in its tests, so a solve "
            "that resolves the name and not the package would pass the build")

    # AmberTools installs Python components whose metadata declares numpy<2
    # against a numpy 2.x environment. The reason is recorded beside the
    # setting, because a regeneration that restored the check without it
    # would reintroduce a build failure somebody had already diagnosed.
    assert "pip_check: false" in text
    assert "numpy<2" in text
