"""A line about a study's record is written in that study's log alone.

A record written with both `analysis.include` and `analysis.exclude` is
read as the analyses that ran, and said so in the study's log. The line was
held for the folder the record names (`output`), which the command line had
dropped, so it went into whichever log was attached then or next: another
study's in one process, the first run's of a study of runs (and no other's),
and, for a copied study read through `phase_settings`, into none, held for
the folder the study was copied from (review of `image-said`, 11-01). It is
said for the folder the study is in now, on the console at once, and in that
study's log, or each of its runs' logs, once attached.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from fastmdxplora.config.loader import validate_config
from fastmdxplora.config.recorded import RECORDED, phase_settings, study_config
from fastmdxplora.utils.logging import attach_file_logger, detach_file_logger

SAID = "lists the analyses that ran and those left out (image_check)"


def _a_study(folder: Path, recorded_at: str | None = None) -> Path:
    folder.mkdir(parents=True)
    record = {"systems": [{"system": "1UBQ"}], "analysis": {"include": ["rmsd"], "exclude": ["image_check"]}}
    if recorded_at:
        record["output"] = recorded_at
    (folder / RECORDED).write_text(yaml.safe_dump(record), encoding="utf-8")
    return folder


def _read_as_the_command_line_reads_it(study: Path) -> None:
    """`fastmdx analyze --output STUDY`: the record read, then checked."""
    config = study_config(study, ["analysis"])
    validate_config(config, as_record=True)
    assert "exclude" not in config["analysis"]


def _logged_in(folder: Path) -> str:
    log = folder / "fastmdxplora.log"
    attach_file_logger(log)
    detach_file_logger(log)
    return log.read_text(encoding="utf-8")


def test_two_studies_in_one_process(tmp_path) -> None:
    one, other = _a_study(tmp_path / "one"), _a_study(tmp_path / "other")
    attach_file_logger(other / "fastmdxplora.log")
    try:
        _read_as_the_command_line_reads_it(one)
    finally:
        detach_file_logger(other / "fastmdxplora.log")
    assert SAID not in (other / "fastmdxplora.log").read_text(encoding="utf-8")
    assert _logged_in(one).count(SAID) == 1


def test_a_held_line_is_not_held_for_ever(tmp_path, caplog) -> None:
    one = _a_study(tmp_path / "one")
    logger = logging.getLogger("fastmdx")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="fastmdx"):
            _read_as_the_command_line_reads_it(one)
    finally:
        logger.removeHandler(caplog.handler)
    assert any(SAID in r.getMessage() for r in caplog.records)
    # The next study's log attached is not its own: not written there, and
    # no longer held (the study reads its record again before it runs).
    assert SAID not in _logged_in(_a_study(tmp_path / "third"))
    assert SAID not in _logged_in(one)
    _read_as_the_command_line_reads_it(one)
    assert _logged_in(one).count(SAID) == 1


def test_a_study_of_runs(tmp_path) -> None:
    root = _a_study(tmp_path / "sweep")
    _read_as_the_command_line_reads_it(root)
    first, second = (_logged_in(root / "runs" / run) for run in ("r1", "r2"))
    for said in (first, second):
        assert said.count(f"The record of the study this run is one of ({root}) {SAID}") == 1
    assert SAID not in _logged_in(tmp_path / "elsewhere")


def test_a_copied_study_whose_record_names_its_old_folder(tmp_path, caplog) -> None:
    before = tmp_path / "before"
    copy = _a_study(tmp_path / "copy", recorded_at=str(before))
    logger = logging.getLogger("fastmdx")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="fastmdx"):
            assert phase_settings(copy / RECORDED, "analysis")["include"] == ["rmsd"]
    finally:
        logger.removeHandler(caplog.handler)
    # On the console at once, and in the copy's own log once attached.
    assert any(SAID in r.getMessage() for r in caplog.records)
    assert _logged_in(copy).count(SAID) == 1
    assert SAID not in _logged_in(before)


def test_a_study_not_known_is_said_on_the_console_alone(tmp_path) -> None:
    attached = _a_study(tmp_path / "attached")
    attach_file_logger(attached / "fastmdxplora.log")
    try:
        validate_config({"analysis": {"include": ["rmsd"], "exclude": ["image_check"]}},
                        as_record=True)
    finally:
        detach_file_logger(attached / "fastmdxplora.log")
    assert SAID not in (attached / "fastmdxplora.log").read_text(encoding="utf-8")
    assert SAID not in _logged_in(_a_study(tmp_path / "next"))
