"""An umbrella study's shared system is reused only when it is checked.

The windows of an umbrella study share one prepared system, and a study run
again reuses it. Two ways through went unchecked:

- A shared system prepared before `prepared_for.json` was written was reused
  with a warning, whatever it had been prepared from. Its own setup record
  is checked instead, and one with no record at all is refused.
- The structure was identified by the path typed, so a structure file edited
  in place passed, and the same file named by another path did not. A file
  is now identified by its SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pytest

from fastmdxplora.batch.explorer import BatchExplorer
from fastmdxplora.refusals import StudyError

PEPTIDE = (
    "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
    "ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C\n"
    "END\n"
)


def _study(root: Path, system: str, *, ph: float | None = 7.4,
           force: bool = False) -> BatchExplorer:
    setup = f"setup:\n  ph: {ph}\n" if ph is not None else ""
    config = root / "study.yml"
    config.write_text(
        f"output: out\ninclude: [setup, simulation]\nsystems:\n  - system: {system}\n"
        f"{setup}"
        "simulation:\n  umbrella:\n    collective_variable: radius_of_gyration\n"
        '    selection: "protein and name CA"\n'
        "    from: 0.4\n    to: 0.6\n    n_windows: 3\n    force_constant: 5000\n",
        encoding="utf-8")
    return BatchExplorer(config=config, output_dir=str(root / "out"), force=force)


def _prepared(root: Path, *, record: dict | None = None,
              prepared_for: dict | None = None) -> Path:
    shared = root / "out" / "shared_setup"
    setup = shared / "setup"
    setup.mkdir(parents=True)
    for name in ("system.xml", "state.xml", "topology.pdb"):
        (setup / name).write_text("<x/>", encoding="utf-8")
    if record is not None:
        (setup / "setup_parameters.json").write_text(json.dumps(record), encoding="utf-8")
    if prepared_for is not None:
        (shared / "prepared_for.json").write_text(json.dumps(prepared_for), encoding="utf-8")
    return setup


def _reuse(study: BatchExplorer) -> Path | None:
    return study._maybe_prepare_once(["setup", "simulation"], None)


class TestPreparedBeforeItWasRecorded:

    def test_with_no_record_at_all_it_is_refused(self, tmp_path) -> None:
        _prepared(tmp_path)
        with pytest.raises(StudyError) as refused:
            _reuse(_study(tmp_path, "181L"))
        assert refused.value.code == "setup.prepared.unverifiable"
        assert "--force-overwrite" in str(refused.value)

    def test_with_force_it_is_prepared_again(self, tmp_path, monkeypatch) -> None:
        from types import SimpleNamespace

        from fastmdxplora.batch import explorer

        setup = _prepared(tmp_path)
        (setup / "stale").write_text("x", encoding="utf-8")

        def prepare(spec, output, *args, **kwargs):
            _prepared(Path(output).parent.parent, record={"input": {}, "parameters": {}})
            return SimpleNamespace(status="ok", message="")

        monkeypatch.setattr(explorer, "_execute_run", prepare)
        assert _reuse(_study(tmp_path, "181L", force=True)) == setup
        assert not (setup / "stale").exists()

    def test_a_setup_record_that_agrees_lets_it_be_reused(self, tmp_path, caplog) -> None:
        setup = _prepared(tmp_path, record={"input": {"system": "181L"},
                                            "parameters": {"ph": 7.4, "forcefield": None}})
        with caplog.at_level(logging.INFO, logger="fastmdx"):
            assert _reuse(_study(tmp_path, "181L")) == setup
        assert "its setup record agrees" in caplog.text
        written = json.loads((setup.parent / "prepared_for.json").read_text(encoding="utf-8"))
        assert written == {"system": "181L", "setup": {"ph": 7.4}}

    def test_a_setup_record_that_disagrees_is_refused(self, tmp_path) -> None:
        _prepared(tmp_path, record={"input": {"system": "181L"},
                                    "parameters": {"ph": 7.4}})
        with pytest.raises(StudyError) as refused:
            _reuse(_study(tmp_path, "181L", ph=5.0))
        assert refused.value.code == "setup.prepared.mismatch"
        assert "(ph)" in str(refused.value)

    def test_a_stated_setting_the_record_does_not_hold_is_not_assumed(self, tmp_path) -> None:
        _prepared(tmp_path, record={"input": {"system": "181L"}, "parameters": {}})
        with pytest.raises(StudyError, match=r"\(ph\)"):
            _reuse(_study(tmp_path, "181L"))

    def test_another_structure_in_the_record_is_refused(self, tmp_path) -> None:
        _prepared(tmp_path, record={"input": {"system": "1UBQ"},
                                    "parameters": {"ph": 7.4}})
        with pytest.raises(StudyError, match=r"\(system\)"):
            _reuse(_study(tmp_path, "181L"))


class TestAStructureFileIsItsContent:

    def _file(self, tmp_path: Path, name: str = "peptide.pdb") -> Path:
        path = tmp_path / name
        path.write_text(PEPTIDE, encoding="utf-8")
        return path

    def test_edited_in_place_it_is_another_structure(self, tmp_path) -> None:
        # Prepared from the file as it was, then the file changed.
        structure = self._file(tmp_path)
        _prepared(tmp_path, prepared_for={
            "system": str(structure), "setup": {"ph": 7.4},
            "structure_sha256": hashlib.sha256(PEPTIDE.encode()).hexdigest()})
        structure.write_text(PEPTIDE.replace("1.458", "1.460"), encoding="utf-8")
        with pytest.raises(StudyError, match=r"\(system\)"):
            _reuse(_study(tmp_path, str(structure)))

    def test_the_same_bytes_by_another_path_are_the_same_structure(self, tmp_path) -> None:
        moved = self._file(tmp_path, "moved.pdb")
        setup = _prepared(tmp_path, prepared_for={
            "system": "somewhere/else/peptide.pdb", "setup": {"ph": 7.4},
            "structure_sha256": hashlib.sha256(PEPTIDE.encode()).hexdigest()})
        assert _reuse(_study(tmp_path, str(moved))) == setup

    def test_a_record_from_before_the_digest_is_read_by_path(self, tmp_path) -> None:
        structure = self._file(tmp_path)
        setup = _prepared(tmp_path, prepared_for={"system": str(structure),
                                                  "setup": {"ph": 7.4}})
        assert _reuse(_study(tmp_path, str(structure))) == setup


def test_a_whole_number_and_its_float_are_one_setting(tmp_path) -> None:
    setup = _prepared(tmp_path, prepared_for={"system": "181L", "setup": {"ph": 7}})
    assert _reuse(_study(tmp_path, "181L", ph=7.0)) == setup
