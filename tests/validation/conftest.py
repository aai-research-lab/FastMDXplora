"""Preparing each structure once, so the assertions can be many and cheap.

Setup takes seconds to minutes per structure and fetches from RCSB, so it
runs once per session per structure and every test reads the same output.
That also means a failure in preparation is reported once, against the
structure, rather than repeated against each thing that wanted to look at it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from .structures import CORPUS, Expectation


@dataclass
class Prepared:
    """What a setup run left behind, or why it did not."""

    expectation: Expectation
    directory: Path
    returncode: int
    output: str

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0

    @property
    def prepared_pdb(self) -> Path:
        return self.directory / "setup" / "prepared.pdb"

    @property
    def solvated_pdb(self) -> Path:
        return self.directory / "setup" / "solvated.pdb"

    def refusal(self) -> str:
        """The message a failed run ended with, for tests about refusals."""
        return self.output


def _workspace() -> Path:
    """Where prepared structures go.

    Honours FASTMDXPLORA_VALIDATION_DIR so a developer can keep the outputs
    between runs and look at them, which is the point of the exercise: these
    tests exist because looking at the structure found what the suite did not.
    """
    override = os.environ.get("FASTMDXPLORA_VALIDATION_DIR")
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(tempfile.mkdtemp(prefix="fastmdx-validation-"))


@pytest.fixture(scope="session")
def workspace() -> Path:
    return _workspace()


def _run_setup(expectation: Expectation, workspace: Path) -> Prepared:
    directory = workspace / expectation.label.replace("/", "_")
    if directory.exists():
        shutil.rmtree(directory)

    command = [
        sys.executable, "-m", "fastmdxplora.cli.main", "explore",
        "--system", expectation.pdb_id,
        "--output", str(directory),
        "--include", "setup",
    ]
    if expectation.chains:
        command += ["--setup-chains", expectation.chains]
    if expectation.membrane:
        command += ["--setup-membrane", expectation.membrane,
                    "--setup-membrane-orient"]

    # Shorter than the 600 s pytest timeout in pytest.ini, and deliberately
    # so. It was 1800 -- three times the outer budget, which meant it could
    # never fire: pytest killed the test first, `_run_setup` never returned,
    # the cache below never filled, and the next test on the same structure
    # paid the full 600 s again. Seven tests share `multimer-glycoprotein`,
    # which is 70 minutes of a 90-minute job spent re-attempting one
    # preparation. Worse, killing the subprocess destroyed the output that
    # would have said what it was doing, so the cause stayed invisible for as
    # long as the arrangement lasted.
    # 1800, measured rather than guessed, and every rise has been read off a
    # log rather than doubled hopefully. 6B73 needed more than 420 s to
    # embed 192,906 atoms in a bilayer and build the System; it passes at
    # 900. 5WYZ needed two PDBFixer passes, one keeping heterogens for the
    # pKa complex and one with the ligand stripped for `prepared.pdb`, each
    # building 79 residues in five gaps of a 1,600-residue dimer: about
    # 475 s each on CI's threads, and 992 s each once setup ran on one
    # thread, so it stopped at 1800 in the second (2026-10-02). It is now
    # one pass, which gives both. That is a large structure, not a stall.
    # The corpus command raises pytest's own timeout to match.
    budget = int(os.environ.get("FASTMDXPLORA_SETUP_TIMEOUT_S", "1800"))
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=budget)
    except subprocess.TimeoutExpired as expired:
        def _text(stream):
            if stream is None:
                return ""
            return stream if isinstance(stream, str) else stream.decode(
                "utf-8", "replace")

        return Prepared(
            expectation=expectation,
            directory=directory,
            returncode=-1,
            output=(_text(expired.stdout) + _text(expired.stderr)
                    + f"\n\nPreparation did not finish within {budget} s and "
                      "was stopped. The output above is what it had written by "
                      "then, which is the evidence for where it was. Raise "
                      "FASTMDXPLORA_SETUP_TIMEOUT_S to give it longer."),
        )
    return Prepared(
        expectation=expectation,
        directory=directory,
        returncode=completed.returncode,
        output=completed.stdout + completed.stderr,
    )


@pytest.fixture(scope="session")
def prepared(workspace: Path):
    """Prepare a structure once, by kind, and remember the result."""
    cache: dict[str, Prepared] = {}

    def _for(kind: str) -> Prepared:
        if kind not in cache:
            expectation = next(e for e in CORPUS if e.kind == kind)
            # Cached whatever the outcome. A preparation that failed is a
            # result the other tests on this structure can read; re-running it
            # once per test tells nobody anything the first attempt did not.
            cache[kind] = _run_setup(expectation, workspace)
        return cache[kind]

    return _for


def load(path: Path):
    """Read a structure, or skip the test saying which file was missing."""
    import mdtraj as md

    if not path.is_file():
        pytest.fail(f"{path.name} was not written")
    return md.load(str(path))
