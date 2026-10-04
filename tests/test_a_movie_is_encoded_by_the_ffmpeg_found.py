"""A movie is encoded by whichever ffmpeg is found, checked with a stand-in.

The encoding of a movie (`movies.py`) runs the ffmpeg of the computer the GUI
runs on, and the tests that encode a real movie skip where there is none,
which was every job coverage was measured on: the pipeline read as untested
there. These run it against a stand-in, a small program named by
`FASTMDXPLORA_FFMPEG` that answers as ffmpeg does where it matters: it lists
encoders, says its version, reads the frames from its input and writes a
file, or fails as it is told to. So every job checks which encoder is
chosen, the command ffmpeg is given, and what is said when it fails.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fastmdxplora import movies
from fastmdxplora.movies import add_frame, cancel_movie, encoding, finish_movie, start_movie

STAND_IN = r'''#!{python}
import os
import sys

args = sys.argv[1:]
if "-encoders" in args:
    for name in os.environ.get("STAND_IN_ENCODERS", "libx264 libvpx-vp9").split():
        print(f" V....D {{name:20s}} a stand-in encoder")
    sys.exit(0)
if "-version" in args:
    print("ffmpeg version 9.9-stand-in Copyright (c) the stand-in")
    sys.exit(0)
out = args[-1]
encoder = [args[i + 1] for i, a in enumerate(args) if a == "-c:v"][-1]
probe = os.path.basename(out).startswith("probe.")
mode = os.environ.get("STAND_IN_MODE", "ok")
if encoder in os.environ.get("STAND_IN_BROKEN", "").split():
    sys.stderr.write(f"Error while opening encoder {{encoder}}\n")
    sys.exit(1)
if not probe and mode == "leave":
    sys.stderr.write("the stand-in left early\n")
    sys.exit(1)
data = sys.stdin.buffer.read()
if not probe and mode == "refuse":
    sys.stderr.write("first line\nsecond line\nthe stand-in refused the frames\n")
    sys.exit(1)
log = os.environ.get("STAND_IN_LOG")
if log and not probe:
    with open(log, "w", encoding="utf-8") as handle:
        handle.write("\n".join(args))
with open(out, "wb") as handle:
    handle.write(b"STAND-IN " + encoder.encode() + b" frames=%d" % data.count(b"\x89PNG"))
'''


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    program = tmp_path / "bin" / "ffmpeg"
    program.parent.mkdir()
    program.write_text(STAND_IN.format(python=sys.executable), encoding="utf-8")
    program.chmod(0o755)
    monkeypatch.setenv("FASTMDXPLORA_FFMPEG", str(program))
    monkeypatch.setenv("STAND_IN_LOG", str(tmp_path / "command.txt"))
    monkeypatch.setattr(movies, "_CHOSEN", {})
    yield program
    for key in list(movies._MOVIES):
        cancel_movie(key)


def _frames(n: int, size=(64, 48)) -> list[bytes]:
    return [movies._png(*size, (10 * i, 20, 30)) for i in range(n)]


def test_the_first_encoder_that_works_is_chosen(stand_in, monkeypatch):
    said = encoding()
    assert said == {"ok": True, "ffmpeg": str(stand_in), "version": "9.9-stand-in",
                    "encoder": "libx264", "format": "mp4", "codec": "H.264",
                    "said": "MP4 (H.264), by ffmpeg 9.9-stand-in on this computer"}
    # Listed and broken, as VideoToolbox is in a virtual machine: the next.
    monkeypatch.setattr(movies, "_CHOSEN", {})
    monkeypatch.setenv("STAND_IN_BROKEN", "libx264")
    assert encoding()["encoder"] == "libvpx-vp9"
    monkeypatch.setattr(movies, "_CHOSEN", {})
    monkeypatch.setenv("STAND_IN_ENCODERS", "libopenh264 h264_videotoolbox")
    monkeypatch.setenv("STAND_IN_BROKEN", "")
    assert encoding()["encoder"] == "h264_videotoolbox"
    monkeypatch.setattr(movies, "_CHOSEN", {})
    monkeypatch.setenv("STAND_IN_ENCODERS", "mpeg4 libtheora")
    refused = encoding()
    assert not refused["ok"] and "encodes neither H.264 nor VP9 or VP8" in refused["reason"]


def test_a_movie_is_made_through_the_command_given(stand_in, tmp_path):
    started = start_movie(tmp_path, "made", fps=30, width=64, height=48,
                          about="frames 0 to 2,\n30 frames a second")
    assert started["ok"] and started["file"] == "movies/made.mp4"
    for frame in _frames(3):
        assert add_frame(started["id"], frame)["ok"]
    done = finish_movie(started["id"])
    assert done["ok"] and (done["frames"], done["seconds"], done["codec"]) == (3, 0.1, "H.264")
    assert (tmp_path / done["file"]).read_bytes() == b"STAND-IN libx264 frames=3"
    command = (tmp_path / "command.txt").read_text(encoding="utf-8").splitlines()

    def given(flag: str, start: int = 0) -> str:
        return command[command.index(flag, start) + 1]

    # The frames as they arrive, then the encoding of the movie.
    assert (given("-f"), given("-framerate"), given("-c:v")) == ("image2pipe", "30", "png")
    encoded = command.index("-i")
    for flag, value in (("-c:v", "libx264"), ("-crf", "18"), ("-colorspace", "bt709"),
                        ("-color_range", "tv"), ("-r", "30"), ("-movflags", "+faststart"),
                        ("-f", "mp4")):
        assert given(flag, encoded) == value, flag
    assert "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p" in command
    assert "title=made" in command
    assert any(arg.startswith("comment=Made with FastMDXplora ")
               and arg.endswith(": frames 0 to 2, 30 frames a second") for arg in command)
    assert [p.name for p in (tmp_path / "movies").iterdir()] == ["made.mp4"]


def test_what_ffmpeg_says_when_it_fails_is_passed_on(stand_in, tmp_path, monkeypatch):
    monkeypatch.setenv("STAND_IN_MODE", "refuse")
    started = start_movie(tmp_path, "x", fps=24, width=64, height=48)
    add_frame(started["id"], _frames(1)[0])
    said = finish_movie(started["id"])
    assert said == {"ok": False, "reason": "ffmpeg did not finish the movie: first line "
                    "second line the stand-in refused the frames"}
    assert not list((tmp_path / "movies").iterdir())
    monkeypatch.setenv("STAND_IN_MODE", "leave")
    started = start_movie(tmp_path, "y", fps=24, width=64, height=48)
    movies._MOVIES[started["id"]].process.wait()
    said = add_frame(started["id"], _frames(1)[0] * 4000)
    assert said == {"ok": False, "reason": "ffmpeg stopped while encoding the movie: "
                    "the stand-in left early"}
    assert not list((tmp_path / "movies").iterdir())


def test_cancelled_given_up_or_too_many(stand_in, tmp_path, monkeypatch):
    first = start_movie(tmp_path, "a", fps=24, width=64, height=48)
    second = start_movie(tmp_path, "b", fps=24, width=64, height=48)
    assert start_movie(tmp_path, "c", fps=24, width=64, height=48)["reason"].startswith(
        "2 movies are being made already")
    assert cancel_movie(first["id"]) == {"ok": True}
    monkeypatch.setattr(movies, "IDLE_SECONDS", -1)
    assert "No movie is being made" in finish_movie(second["id"])["reason"]
    assert not movies._MOVIES and not list((tmp_path / "movies").iterdir())


def test_the_ffmpeg_named_is_checked_once_until_it_changes(stand_in, monkeypatch):
    import os

    calls = []
    real = movies._listed
    monkeypatch.setattr(movies, "_listed", lambda path: calls.append(path) or real(path))
    encoding()
    encoding()
    assert calls == [str(stand_in)]
    later = os.stat(stand_in).st_mtime_ns + 5_000_000_000
    os.utime(stand_in, ns=(later, later))
    encoding()
    assert calls == [str(stand_in)] * 2


def test_the_stand_in_is_a_program(stand_in):
    assert Path(movies.find_ffmpeg()) == stand_in
