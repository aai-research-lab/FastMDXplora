"""A run directory's log accumulates, and says where each run starts.

Appending is right. A run directory is the record of what happened in it, and
a second attempt that truncated the file would erase the evidence of why the
first was made -- which is what somebody reading it is looking for. A run that
failed naming a wrong CUDA version, followed by the corrected one, belongs in
the same file in that order.

What appending needed and did not have is a mark. Without one, the second
run's first line follows the first run's traceback with nothing between them,
and reading backwards there is no way to tell how far back this run goes.
"""

from __future__ import annotations

import re
from pathlib import Path


from fastmdxplora.utils.logging import _mark_a_new_run


class TestItMarksTheBoundary:
    def test_a_banner_is_written(self, tmp_path: Path) -> None:
        log = tmp_path / "fastmdxplora.log"
        log.write_text("first run\n", encoding="utf-8")
        _mark_a_new_run(log)
        assert "=== new run" in log.read_text(encoding="utf-8")

    def test_what_came_before_is_kept(self, tmp_path: Path) -> None:
        """The point of appending. The failed attempt is the evidence."""
        log = tmp_path / "fastmdxplora.log"
        log.write_text("ERROR - Phase 'simulation' failed: ...\n",
                       encoding="utf-8")
        _mark_a_new_run(log)
        assert "Phase 'simulation' failed" in log.read_text(encoding="utf-8")

    def test_it_names_the_time(self, tmp_path: Path) -> None:
        log = tmp_path / "fastmdxplora.log"
        log.write_text("x\n", encoding="utf-8")
        _mark_a_new_run(log)
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
                         log.read_text(encoding="utf-8"))

    def test_it_names_the_version(self, tmp_path: Path) -> None:
        """Two runs of a directory can be made by two versions, and a result
        computed by one with a known defect should be identifiable."""
        log = tmp_path / "fastmdxplora.log"
        log.write_text("x\n", encoding="utf-8")
        _mark_a_new_run(log)
        assert "fastmdxplora" in log.read_text(encoding="utf-8")

    def test_several_runs_leave_several_marks(self, tmp_path: Path) -> None:
        log = tmp_path / "fastmdxplora.log"
        log.write_text("x\n", encoding="utf-8")
        for _ in range(3):
            _mark_a_new_run(log)
        assert log.read_text(encoding="utf-8").count("=== new run") == 3


class TestItNeverStopsARun:
    def test_a_directory_that_cannot_be_written_to(self, tmp_path: Path) -> None:
        """Losing a run over where a separator went would be the worse
        failure."""
        _mark_a_new_run(tmp_path / "no" / "such" / "place.log")

    def test_the_first_run_gets_no_banner(self, tmp_path: Path) -> None:
        """An empty file needs no separator: there is nothing to separate it
        from, and a banner at the top of every log is noise. A log attached
        where there is none, or where the file is empty, starts with no
        banner; one attached to a log with a run in it gets one."""
        from fastmdxplora.utils.logging import attach_file_logger

        new, empty, used = tmp_path / "new.log", tmp_path / "empty.log", tmp_path / "used.log"
        empty.write_text("", encoding="utf-8")
        used.write_text("the run before\n", encoding="utf-8")
        for log in (new, empty, used):
            attach_file_logger(log)
        assert "=== new run" not in new.read_text(encoding="utf-8")
        assert "=== new run" not in empty.read_text(encoding="utf-8")
        text = used.read_text(encoding="utf-8")
        assert text.startswith("the run before\n") and "=== new run" in text

