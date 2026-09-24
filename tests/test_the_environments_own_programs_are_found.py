"""AmberTools installed beside this Python is found without activating it.

The OpenFF toolkit looks for ``antechamber`` on ``PATH`` once, when it is
first imported. A conda environment's Python run by its full path (as a
script, ``ssh host 'env/bin/fastmdx ...'`` or a batch job does) has the
environment's ``bin`` off ``PATH``, so AmberTools was installed and a
ligand still failed after solvation for want of AM1-BCC charges.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastmdxplora import own_programs


def _prefix_with_bin(tmp_path: Path, monkeypatch) -> Path:
    prefix = tmp_path / "env"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "Scripts").mkdir()
    monkeypatch.setattr(sys, "prefix", str(prefix))
    return prefix / ("Scripts" if os.name == "nt" else "bin")


def test_the_environments_programs_are_put_on_the_path(tmp_path, monkeypatch):
    own = _prefix_with_bin(tmp_path, monkeypatch)
    env = {"PATH": os.pathsep.join(["/usr/bin", "/bin"])}
    assert own_programs.put_own_programs_on_path(env)
    assert env["PATH"].split(os.pathsep)[-1] == str(own)


def test_what_was_already_on_the_path_keeps_its_place(tmp_path, monkeypatch):
    own = _prefix_with_bin(tmp_path, monkeypatch)
    env = {"PATH": os.pathsep.join(["/opt/other/bin", "/usr/bin"])}
    own_programs.put_own_programs_on_path(env)
    assert env["PATH"].split(os.pathsep) == ["/opt/other/bin", "/usr/bin", str(own)]


def test_an_environment_already_active_is_left_alone(tmp_path, monkeypatch):
    own = _prefix_with_bin(tmp_path, monkeypatch)
    env = {"PATH": os.pathsep.join([str(own), "/usr/bin"])}
    assert not own_programs.put_own_programs_on_path(env)
    assert env["PATH"] == os.pathsep.join([str(own), "/usr/bin"])


def test_nothing_is_added_where_there_is_no_program_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "nowhere"))
    env = {"PATH": "/usr/bin"}
    assert not own_programs.put_own_programs_on_path(env)
    assert env["PATH"] == "/usr/bin"


def test_importing_the_package_does_it_before_openff_can_look(tmp_path):
    """A child Python with PATH stripped of its own bin finds it again after
    ``import fastmdxplora`` -- the order that matters, since the OpenFF
    toolkit decides on import."""
    own = str(Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin"))
    stripped = os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep)
        if p and Path(p).resolve() != Path(own).resolve()) or os.defpath
    src = str(Path(__file__).resolve().parents[1] / "src")
    code = ("import os, sys; import fastmdxplora; "
            "from pathlib import Path; "
            "own = Path(sys.prefix) / ('Scripts' if os.name == 'nt' else 'bin'); "
            "print(any(Path(p).resolve() == own.resolve() "
            "for p in os.environ['PATH'].split(os.pathsep) if p))")
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env={**os.environ, "PATH": stripped,
             "PYTHONPATH": os.pathsep.join(
                 [src, os.environ.get("PYTHONPATH", "")])},
        timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"
