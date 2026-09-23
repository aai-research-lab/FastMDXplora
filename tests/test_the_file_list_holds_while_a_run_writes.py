"""The Files page's list holds while a run is writing the folder it lists.

The live-frame history keeps the last two hundred frames, deleting the
oldest as each arrives, and every atomic write passes through a `.tmp`
file renamed into place. A file that vanished between the walk and its
`stat` raised out of the listing and failed the whole request, logged as
"dashboard route failed: No such file or directory" through a real
production run, without saying which route. Each race is reproduced here.
"""

from __future__ import annotations

import logging
import pathlib
from types import SimpleNamespace

from fastmdxplora.gui.server import _artifact_records


def _a_run_being_written(root: pathlib.Path) -> pathlib.Path:
    frames = root / "simulation" / "live_frames"
    frames.mkdir(parents=True)
    for n in range(1, 4):
        (frames / f"frame_{n:06d}_production.pdb").write_text("ATOM\n", encoding="utf-8")
    (root / "simulation" / "playback.pdb.tmp").write_text("half written", encoding="utf-8")
    (root / "simulation" / "energy.csv").write_text("step,energy\n", encoding="utf-8")
    return frames


def _listed(root):
    return sorted(record["path"] for record in _artifact_records(root))


def test_a_frame_deleted_after_the_walk_is_left_out(tmp_path, monkeypatch) -> None:
    # The oldest frame is deleted the moment the walk has seen it, as the
    # run does when the next frame arrives: the list is still made.
    frames = _a_run_being_written(tmp_path)
    oldest = frames / "frame_000001_production.pdb"
    real_rglob = pathlib.Path.rglob

    def walk_then_delete(self, pattern):
        for path in real_rglob(self, pattern):
            yield path
            if path == oldest:
                oldest.unlink()

    monkeypatch.setattr(pathlib.Path, "rglob", walk_then_delete)
    listed = _listed(tmp_path)
    assert "simulation/live_frames/frame_000001_production.pdb" not in listed
    assert "simulation/live_frames/frame_000003_production.pdb" in listed
    assert "simulation/energy.csv" in listed


def test_a_half_written_file_is_never_listed(tmp_path) -> None:
    _a_run_being_written(tmp_path)
    assert not [path for path in _listed(tmp_path) if path.endswith(".tmp")]


def test_a_file_is_read_once_so_its_fields_agree(tmp_path, monkeypatch) -> None:
    # A file rewritten while it is listed: every field comes from one
    # reading, so the link's version, the time and the size agree.
    _a_run_being_written(tmp_path)
    real_stat = pathlib.Path.stat
    readings: dict[str, int] = {}

    def changing(self, *args, **kwargs):
        info = real_stat(self, *args, **kwargs)
        if self.name != "energy.csv":
            return info
        readings[self.name] = readings.get(self.name, 0) + 1
        return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size + readings[self.name],
                               st_mtime=1_000_000.0 + readings[self.name])

    monkeypatch.setattr(pathlib.Path, "stat", changing)
    record = next(r for r in _artifact_records(tmp_path) if r["name"] == "energy.csv")
    version = int(float(record["mtime"]))
    assert record["href"].endswith(f"?v={version}")
    assert record["download_href"].endswith(f"&v={version}")
    assert readings["energy.csv"] == 1


def test_a_folder_removed_mid_walk_leaves_what_was_reached(tmp_path, monkeypatch) -> None:
    _a_run_being_written(tmp_path)
    real_rglob = pathlib.Path.rglob

    def walk_until_a_folder_goes(self, pattern):
        for path in real_rglob(self, pattern):
            yield path
            if path.name == "energy.csv":
                raise FileNotFoundError(2, "No such file or directory", str(self / "gone"))

    monkeypatch.setattr(pathlib.Path, "rglob", walk_until_a_folder_goes)
    assert "simulation/energy.csv" in _listed(tmp_path)


def test_a_route_that_fails_is_named(tmp_path, monkeypatch, caplog) -> None:
    # The warning said what went wrong and not where, which made this one
    # harder to trace than it needed to be.
    import urllib.error
    import urllib.request

    from fastmdxplora.gui import server

    def fails(root):
        raise RuntimeError("the listing failed")

    monkeypatch.setattr(server, "_artifact_records", fails)
    session = server.start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    try:
        with caplog.at_level(logging.WARNING, logger="fastmdxplora.gui.server"):
            try:
                urllib.request.urlopen(f"{session.url.rstrip('/')}/api/files", timeout=20)
            except urllib.error.HTTPError as error:
                assert error.code == 500
    finally:
        session.server.shutdown()
    assert "dashboard route /api/files failed: the listing failed" in caplog.text
