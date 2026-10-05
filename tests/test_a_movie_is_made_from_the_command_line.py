"""A movie of a study made without opening the GUI: `fastmdx movie` and the
`make_movie` tool of `fastmdx mcp`.

The movie is the one the Viewer's Movie section makes: the GUI is started
for the study on this computer, a browser with no window opens its Viewer,
and the Viewer's own movie code renders each frame and has ffmpeg encode it
into the study's movies/ folder (movie_maker.py).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mdtraj")


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    root = tmp_path_factory.mktemp("cli-movie") / "work"
    root.mkdir()
    return _helical_study(root / "haemoglobin", analyse=False)


def test_what_cannot_be_made_is_said(study, tmp_path, monkeypatch):
    from fastmdxplora import movie_maker
    from fastmdxplora.gui.saved_views import save_view
    from fastmdxplora.movie_maker import BROWSER_INSTALL, make_movie

    assert make_movie(tmp_path)["reason"] == f"{tmp_path} is not a study."
    assert make_movie(study, size="640x480")["reason"] == (
        "A movie is made at 1280x720, 1920x1080, 3840x2160.")
    assert make_movie(study, fps=12)["reason"].startswith("A movie plays at 10, 15, 24")
    assert make_movie(study, between=2)["reason"] == (
        "A movie puts 1, 3 or 7 frames in between two frames played, or none.")
    assert make_movie(study, every=0)["reason"] == (
        "A movie's frames are counted from 0, every 1 or more.")
    assert make_movie(study, first=-1)["reason"] == (
        "A movie's frames are counted from 0, every 1 or more.")
    assert make_movie(study, view="close")["reason"] == (
        "The study has no view named 'close'; its views: none.")
    assert make_movie(study, changes={"camera": 1})["reason"] == (
        "A view is changed by frame, representation, colour, superposed, superposed_to, "
        "smoothed_over, not camera.")
    monkeypatch.setenv("FASTMDXPLORA_FFMPEG", str(tmp_path / "no-ffmpeg"))
    assert make_movie(study)["reason"].startswith("ffmpeg was not found on this computer.")
    monkeypatch.delenv("FASTMDXPLORA_FFMPEG")
    if not shutil.which("ffmpeg"):
        return
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    assert make_movie(study)["reason"] == BROWSER_INSTALL
    monkeypatch.undo()
    pytest.importorskip("playwright.sync_api")
    # A view saved in the GUI is found, and reaches the browser, which here
    # cannot be started.
    camera = {"position": [0, 0, 100], "target": [0, 0, 0], "up": [0, 1, 0]}
    assert save_view(study, "close", {"camera": camera, "frame": 2})["ok"]
    monkeypatch.setattr(movie_maker, "_browser", lambda pw: (None, "no browser here"))
    said = make_movie(study, view="close", changes={"representation": "spacefill"})
    assert said["reason"] == f"{BROWSER_INSTALL} (no browser here)"


def test_fastmdx_movie_makes_the_viewer_s_movie(study, capsys):
    pytest.importorskip("playwright.sync_api")
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg is not on this computer")
    from fastmdxplora.cli.main import main

    code = main(["movie", str(study), "--to", "2", "--between", "1", "--size", "1280x720",
                 "--representation", "spacefill", "--fps", "10", "--name", "from the cli"])
    out = capsys.readouterr()
    if "cannot render WebGL" in out.err:
        pytest.skip("this browser has no WebGL, so the viewer cannot render")
    assert code == 0, out.err
    made = study / "movies" / "from the cli.mp4"
    assert made.is_file()
    lines = out.out.strip().splitlines()
    # After the banner, how far it has got, then what was made.
    told = lines.index("Opening the study's Viewer…")
    assert lines[told + 1:told + 3] == ["Showing the view…", "Rendering the frames…"]
    assert len(lines) == told + 5
    assert lines[-2] == (f"Made {made}: 5 frames, 0.5 s, 1280 x 720, MP4 (H.264), "
                         f"{made.stat().st_size / 1e6:.1f} MB, 1 frame in between each two "
                         "played, interpolated.")
    assert lines[-1].startswith("  Rendered by the Viewer in chromium; encoded as MP4 (H.264), "
                                "by ffmpeg")
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of",
         "json", str(made)], capture_output=True, text=True, check=True).stdout)
    stream = probe["streams"][0]
    assert (stream["width"], stream["height"], stream["nb_read_frames"]) == (1280, 720, "5")
    assert "frames 0 to 2, 1 frame in between each two" in probe["format"]["tags"]["comment"]
    # Nothing the GUI wrote is left running or half made.
    assert not [p for p in (study / "movies").iterdir() if p.name.startswith(".")]

    from fastmdxplora.movie_maker import make_movie

    # A view changed: shown as asked, and the movie made of it.
    made = make_movie(study, changes={"representation": "spacefill", "colour": "element",
                                      "superposed": "backbone"}, first=0, last=1,
                      size="1280x720", name="spheres")
    assert made["ok"] and made["frames"] == 2
    assert made["shown"] == {"representation": "spacefill", "colour": "element",
                             "superposed": "backbone"}

    assert main(["movie", str(study), "--view", "far"]) == 1
    assert "The study has no view named 'far'" in capsys.readouterr().err


def test_an_ai_app_makes_one(study, tmp_path, monkeypatch):
    from fastmdxplora import movie_maker
    from fastmdxplora.mcp.app import App
    from fastmdxplora.mcp.tools import Context, ToolError, _make_movie
    from fastmdxplora.mcp.workspace import Workspace

    workspace = Workspace.at(study.parent)
    assert "make_movie" in [t.name for t in App(workspace).tools]
    # A read-only server starts nothing on this computer.
    assert "make_movie" not in [t.name for t in App(workspace, runs=False).tools]
    asked = {}

    def made(folder, **kwargs):
        asked.update(kwargs, folder=folder)
        return {"ok": True, "file": "movies/m.mp4", "path": str(folder / "movies" / "m.mp4"),
                "frames": 9, "seconds": 0.38, "fps": 24, "width": 1920, "height": 1080,
                "format": "mp4", "codec": "H.264", "bytes": 2_100_000, "between": 3,
                "browser": "chromium", "encoder": "MP4 (H.264), by ffmpeg 7 on this computer"}

    monkeypatch.setattr(movie_maker, "make_movie", made)
    said = _make_movie(Context(workspace), {"study": "haemoglobin", "from": 1, "to": 3,
                                            "between": 3, "colour": "result:rmsf",
                                            "time": False})
    assert asked["folder"] == study.resolve() and asked["first"] == 1 and asked["last"] == 3
    assert asked["changes"] == {"colour": "result:rmsf"} and asked["time"] is False
    assert asked["between"] == 3 and asked["size"] == "1920x1080" and asked["every"] == 1
    assert said.splitlines()[0] == ("Made the movie haemoglobin/movies/m.mp4: 9 frames, 0.38 s "
                                    "at 24 frames a second, 1920 x 1080, MP4 (H.264), 2.1 MB. "
                                    "Tell the person where it is.")
    assert "not more simulation" in said
    monkeypatch.setattr(movie_maker, "make_movie",
                        lambda folder, **kwargs: {"ok": False, "reason": "No frames here."})
    with pytest.raises(ToolError, match="No frames here."):
        _make_movie(Context(workspace), {"study": "haemoglobin"})
