"""A Config with no `systems:` list says what it has instead.

`system:` at the top level is the natural thing to write for one structure,
and `explore` answered it with "explore requires a system", which reads as
though the file had not been read at all. A per-phase command answered it
with "`systems` must be a non-empty list of mappings". Both now show the
value given in the shape the key wants.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmdxplora.cli.main import main


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "study.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_explore_shows_the_key_it_wants(tmp_path, capsys) -> None:
    config = _config(tmp_path, "system: 1afo.pdb\nsetup:\n  membrane: DMPC\n")
    assert main(["explore", "-c", str(config), "--output", str(tmp_path / "run")]) == 2
    said = capsys.readouterr().err
    assert "top-level `system:`" in said
    assert "  systems:\n    - system: 1afo.pdb" in said
    assert "explore requires a system" not in said


def test_a_phase_command_shows_it_too(tmp_path, capsys) -> None:
    config = _config(tmp_path, "system: 1afo.pdb\n")
    with pytest.raises(SystemExit) as stopped:
        main(["setup", "-c", str(config), "--output", str(tmp_path / "run")])
    assert "    - system: 1afo.pdb" in str(stopped.value)


def test_a_config_with_neither_says_so(tmp_path) -> None:
    config = _config(tmp_path, "setup:\n  membrane: DMPC\n")
    with pytest.raises(SystemExit) as stopped:
        main(["setup", "-c", str(config), "--output", str(tmp_path / "run")])
    assert "has no `systems:` list" in str(stopped.value)
