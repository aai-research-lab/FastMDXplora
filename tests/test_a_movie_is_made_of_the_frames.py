"""A movie is encoded by ffmpeg on this computer, as the frames arrive.

The Viewer sends each frame of a movie as a PNG; ffmpeg, found on the PATH,
encodes them into ``movies/<name>.mp4`` with the first encoder that makes a
test movie here: H.264 in MP4, else VP9 or VP8 in WebM. What is checked is
the movie as a player reads it (ffprobe): its codec, size, frame count, rate
and colour tags, and that a result's colours come back as they went in.
"""

from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

from fastmdxplora import movies
from fastmdxplora.movies import (
    Encoder,
    add_frame,
    cancel_movie,
    encoding,
    finish_movie,
    movies_of,
    start_movie,
)

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg is not on this computer")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv("FASTMDXPLORA_FFMPEG", raising=False)
    monkeypatch.setattr(movies, "_CHOSEN", {})
    yield
    for key in list(movies._MOVIES):
        cancel_movie(key)


def _probe(path: Path) -> dict:
    said = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                           "-of", "json", str(path)], capture_output=True, text=True,
                          check=True).stdout
    found = json.loads(said)
    return {**found["streams"][0], "tags": found["format"].get("tags", {})}


def _made(root: Path, colours, *, name="movie", fps=24, size=(64, 48), about=""):
    started = start_movie(root, name, fps=fps, width=size[0], height=size[1], about=about)
    assert started["ok"], started
    for colour in colours:
        assert add_frame(started["id"], movies._png(*size, colour))["ok"]
    return started, finish_movie(started["id"])


@needs_ffmpeg
def test_a_movie_is_an_mp4_a_player_reads(tmp_path):
    said = encoding()
    assert said["ok"] and said["said"].endswith("on this computer")
    started, done = _made(tmp_path, [(i * 20, 90, 200) for i in range(10)],
                          name="the first one", about="frames 0 to 9,\n24 frames a second")
    assert done["ok"], done
    assert done["file"] == f"movies/the first one.{started['format']}"
    effective_fps = 2 if started["format"] == "mp4" else 24
    assert (done["frames"], done["fps"], done["seconds"]) \
        == (10, effective_fps, round(10 / effective_fps, 2))
    found = _probe(tmp_path / done["file"])
    assert found["codec_name"] in {"h264", "vp9", "vp8"}
    assert (found["width"], found["height"], found["r_frame_rate"]) \
        == (64, 48, f"{effective_fps}/1")
    assert int(found.get("nb_frames") or 10) == 10
    assert found["pix_fmt"] == "yuv420p"
    assert found["color_space"] == "bt709"
    for key in ("color_primaries", "color_transfer"):
        if key in found:
            assert found[key] == "bt709"
    tags = {key.lower(): value for key, value in found["tags"].items()}
    assert tags["title"] == "the first one"
    assert tags["comment"].startswith("Made with FastMDXplora ")
    assert tags["comment"].endswith(": frames 0 to 9, 24 frames a second")
    # Nothing of its making is left beside it.
    kept = [p.name for p in (tmp_path / "movies").iterdir()]
    assert kept == [f"the first one.{started['format']}"]
    assert movies_of(tmp_path)["movies"] == [{"name": "the first one", "file": done["file"],
                                              "bytes": done["bytes"]}]


@needs_ffmpeg
def test_a_result_s_colours_come_back_as_they_went_in(tmp_path):
    """The Viewer's scale (blue, white, red) and its grey for no value: RGB
    is encoded as BT.709 and tagged so, which is how a player reads HD."""
    colours = [(44, 123, 182), (247, 247, 247), (215, 25, 28), (92, 92, 102)]
    _, done = _made(tmp_path, colours, size=(64, 64))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(tmp_path / done["file"]),
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    frame = 64 * 64 * 3
    middle = (32 * 64 + 32) * 3
    for index, colour in enumerate(colours):
        back = raw[index * frame + middle:index * frame + middle + 3]
        assert max(abs(a - b) for a, b in zip(back, colour)) <= 4, (colour, tuple(back))


@needs_ffmpeg
def test_an_encoder_listed_that_fails_is_passed_over(tmp_path, monkeypatch):
    """VideoToolbox in a virtual machine is listed and fails; the next that
    works is chosen, here VP9 in WebM, as where ffmpeg has no H.264."""
    listed, _ = movies._listed(shutil.which("ffmpeg"))
    if "libvpx-vp9" not in listed or "libx264" not in listed:
        pytest.skip("this ffmpeg has not both libx264 and libvpx-vp9")
    failing = Encoder("libx264", "mp4", "H.264",
                      lambda w, h, fps: ["-c:v", "libx264", "-preset", "no-such-preset"])
    vp9 = next(e for e in movies._ENCODERS if e.name == "libvpx-vp9")
    monkeypatch.setattr(movies, "_ENCODERS", (failing, vp9))
    said = encoding()
    assert (said["encoder"], said["format"], said["codec"]) == ("libvpx-vp9", "webm", "VP9")
    _, done = _made(tmp_path, [(10, 10, 10)] * 3)
    assert done["file"] == "movies/movie.webm" and done["codec"] == "VP9"
    assert _probe(tmp_path / done["file"])["codec_name"] == "vp9"
    monkeypatch.setattr(movies, "_CHOSEN", {})
    monkeypatch.setattr(movies, "_ENCODERS", (failing,))
    said = encoding()
    assert not said["ok"] and "encodes neither H.264 nor VP9 or VP8" in said["reason"]
    assert not start_movie(tmp_path, "x", fps=24, width=64, height=64)["ok"]


def test_without_ffmpeg_it_is_said_how_to_have_it(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_FFMPEG", str(tmp_path / "no-ffmpeg"))
    said = encoding()
    assert not said["ok"]
    assert "ffmpeg was not found" in said["reason"]
    assert "conda install -c conda-forge ffmpeg" in said["reason"]
    assert start_movie(tmp_path, "x", fps=24, width=64, height=64) == said


def test_the_ffmpeg_named_is_the_one_run(tmp_path, monkeypatch):
    named = tmp_path / "ffmpeg"
    named.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    named.chmod(0o755)
    monkeypatch.setenv("FASTMDXPLORA_FFMPEG", str(named))
    assert movies.find_ffmpeg() == str(named)
    assert not encoding()["ok"]
    monkeypatch.delenv("FASTMDXPLORA_FFMPEG")
    monkeypatch.setattr(movies.shutil, "which", lambda name: None)
    monkeypatch.setattr(movies, "_ELSEWHERE", (str(named),))
    assert movies.find_ffmpeg() == str(named)
    monkeypatch.setattr(movies, "_ELSEWHERE", ())
    assert movies.find_ffmpeg() is None


@pytest.mark.parametrize("asked, said", [
    ({"name": "../up"}, "A movie is named in 1 to 60"),
    ({"name": ""}, "A movie is named in 1 to 60"),
    ({"fps": 23}, "A movie plays at 10, 15, 24, 25, 30, 60 frames a second."),
    ({"fps": True}, "A movie plays at"),
    ({"fps": "24"}, "A movie plays at"),
    ({"width": 65}, "an even number of pixels each way"),
    ({"width": 8}, "an even number of pixels each way"),
    ({"width": 64.5}, "an even number of pixels each way"),
    ({"width": 7680, "height": 4320}, "3840 by 2160 at most"),
])
def test_what_is_not_a_movie_is_said(tmp_path, asked, said):
    settings = {"name": "x", "fps": 24, "width": 64, "height": 64, **asked}
    refused = start_movie(tmp_path, settings.pop("name"), **settings)
    assert not refused["ok"] and said in refused["reason"]


@needs_ffmpeg
def test_a_frame_that_is_not_the_movie_s_is_refused(tmp_path):
    started = start_movie(tmp_path, "x", fps=24, width=64, height=48)
    assert add_frame(started["id"], b"GIF89a" + b"\0" * 40)["reason"] == "A frame is sent as a PNG."
    assert add_frame(started["id"], movies._png(48, 64, (0, 0, 0)))["reason"] == \
        "This frame is 48 by 64 pixels; the movie's are 64 by 48."
    assert finish_movie(started["id"])["reason"] == "The movie was given no frames."
    assert not list((tmp_path / "movies").iterdir())
    for key in ("0123456789abcdef", "not an id", None):
        assert "No movie is being made under that id" in add_frame(key, b"")["reason"]
        assert not finish_movie(key)["ok"] and not cancel_movie(key)["ok"]


@needs_ffmpeg
def test_a_movie_cancelled_or_left_leaves_nothing(tmp_path, monkeypatch):
    started = start_movie(tmp_path, "x", fps=24, width=64, height=48)
    add_frame(started["id"], movies._png(64, 48, (1, 2, 3)))
    assert cancel_movie(started["id"]) == {"ok": True}
    assert not list((tmp_path / "movies").iterdir())
    # A tab closed mid-way: given up once idle, by the next movie asked for.
    left = start_movie(tmp_path, "left", fps=24, width=64, height=48)
    process = movies._MOVIES[left["id"]].process
    monkeypatch.setattr(movies, "IDLE_SECONDS", -1)
    assert "No movie is being made" in add_frame(left["id"], b"")["reason"]
    assert left["id"] not in movies._MOVIES and process.poll() is not None
    assert not list((tmp_path / "movies").iterdir())


@needs_ffmpeg
def test_two_movies_at_once_and_no_more(tmp_path):
    first = start_movie(tmp_path, "a", fps=24, width=64, height=48)
    second = start_movie(tmp_path, "b", fps=24, width=64, height=48)
    third = start_movie(tmp_path, "c", fps=24, width=64, height=48)
    assert first["ok"] and second["ok"]
    assert third == {"ok": False, "reason": "2 movies are being made already; one can be made "
                                            "once they are."}


@needs_ffmpeg
def test_ffmpeg_stopping_mid_way_is_said(tmp_path):
    started = start_movie(tmp_path, "x", fps=24, width=64, height=48)
    movie = movies._MOVIES[started["id"]]
    movie.process.kill()
    movie.process.wait()
    said = add_frame(started["id"], movies._png(64, 48, (1, 2, 3)) * 2000)
    assert not said["ok"] and said["reason"].startswith("ffmpeg stopped while encoding the movie")
    assert started["id"] not in movies._MOVIES
    # Killed after its frames, it does not finish the file.
    started = start_movie(tmp_path, "y", fps=24, width=64, height=48)
    add_frame(started["id"], movies._png(64, 48, (1, 2, 3)))
    movies._MOVIES[started["id"]].process.kill()
    said = finish_movie(started["id"])
    assert not said["ok"] and said["reason"].startswith("ffmpeg did not finish the movie")
    assert not list((tmp_path / "movies").iterdir())


def test_each_encoder_is_asked_for_what_it_takes():
    """Constant quality where the encoder has it; 0.2 bits a pixel where it
    has not, which is about 10 Mb/s for 1080p at 24 frames a second."""
    by_name = {e.name: e for e in movies._ENCODERS}
    assert [e.name for e in movies._ENCODERS] == ["libx264", "h264_videotoolbox", "libopenh264",
                                                  "libvpx-vp9", "libvpx"]
    command = movies._command("ffmpeg", by_name["libx264"], 24, 1920, 1080, Path("m.mp4"))
    assert command[command.index("-crf") + 1] == "18" and "-movflags" in command
    for name in ("h264_videotoolbox", "libopenh264", "libvpx"):
        command = movies._command("ffmpeg", by_name[name], 24, 1920, 1080, Path("m"))
        assert command[command.index("-b:v") + 1] == "9953280", name
    small = movies._command("ffmpeg", by_name["libvpx"], 10, 64, 64, Path("m.webm"))
    assert small[small.index("-b:v") + 1] == "2000000"
    assert "-movflags" not in small and small[-3:] == ["-f", "webm", "m.webm"]


def test_an_ffmpeg_that_cannot_be_run_chooses_nothing(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("not allowed")

    assert movies._chosen(str(tmp_path / "gone")) == (None, "")
    monkeypatch.setattr(movies.subprocess, "run", refuse)
    assert movies._listed("ffmpeg") == (set(), "")
    assert movies._encodes("ffmpeg", movies._ENCODERS[0]) is False


@needs_ffmpeg
def test_ffmpeg_not_starting_is_said(tmp_path, monkeypatch):
    encoding()

    def refuse(*args, **kwargs):
        raise OSError("no more processes")

    monkeypatch.setattr(movies.subprocess, "Popen", refuse)
    said = start_movie(tmp_path, "x", fps=24, width=64, height=48)
    assert said == {"ok": False, "reason": "ffmpeg could not be started: no more processes"}


@needs_ffmpeg
def test_a_movie_has_so_many_frames_at_most(tmp_path, monkeypatch):
    monkeypatch.setattr(movies, "MOST_FRAMES", 2)
    started = start_movie(tmp_path, "x", fps=24, width=64, height=48)
    frame = movies._png(64, 48, (1, 2, 3))
    assert add_frame(started["id"], frame)["ok"] and add_frame(started["id"], frame)["ok"]
    assert add_frame(started["id"], frame) == {"ok": False,
                                               "reason": "A movie has 2 frames at most."}
    assert finish_movie(started["id"])["frames"] == 2


@needs_ffmpeg
def test_an_ffmpeg_that_does_not_finish_is_said(tmp_path):
    started = start_movie(tmp_path, "x", fps=24, width=64, height=48)
    add_frame(started["id"], movies._png(64, 48, (1, 2, 3)))
    process = movies._MOVIES[started["id"]].process
    waited = process.wait

    def hangs(timeout=None):
        if timeout == 600:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return waited(timeout=timeout)

    process.wait = hangs
    said = finish_movie(started["id"])
    assert not said["ok"] and said["reason"].startswith("ffmpeg did not finish the movie")
    assert process.poll() is not None and not list((tmp_path / "movies").iterdir())


def test_only_the_movies_are_listed(tmp_path):
    folder = tmp_path / "movies"
    folder.mkdir()
    for name in ("one.mp4", "two.webm", ".three.ab12.partial.mp4", "notes.txt", "-bad.mp4"):
        (folder / name).write_bytes(b"x")
    assert sorted(m["name"] for m in movies_of(tmp_path)["movies"]) == ["one", "two"]
    assert movies_of(tmp_path / "nothing") == {"ok": True, "movies": []}


@needs_ffmpeg
def test_the_gui_makes_a_movie_with_the_study(tmp_path, monkeypatch):
    """The routes the Viewer uses, on loopback: what a movie is made with,
    a movie started, its frames sent as PNGs, finished and downloaded."""
    pytest.importorskip("mdtraj")
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    study = _write_study(tmp_path / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")

    def ask(path, body=None, kind="application/json"):
        data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
        request = urllib.request.Request(base + path, data=data, method="POST" if data is not None
                                         else "GET", headers={"content-type": kind,
                                                              "origin": base})
        with urllib.request.urlopen(request, timeout=60) as answer:
            return json.loads(answer.read())

    try:
        said = ask("/api/movies")
        assert said["ok"] and said["movies"] == []
        assert not ask("/api/movies", {"name": "x", "fps": 7, "width": 64, "height": 64})["ok"]
        started = ask("/api/movies", {"name": "made here", "fps": 15, "width": 64,
                                      "height": 48, "about": "frames 0 to 2"})
        assert started["ok"], started
        for shade in (0, 120, 240):
            sent = ask(f"/api/movies/{started['id']}/frame",
                       movies._png(64, 48, (shade, shade, shade)), "image/png")
            assert sent["ok"], sent
        done = ask(f"/api/movies/{started['id']}/finish", {})
        assert done["ok"] and done["frames"] == 3, done
        assert ask("/api/movies")["movies"][0]["name"] == "made here"
        with urllib.request.urlopen(base + "/artifacts/" + urllib.request.quote(done["file"])
                                    + "?download=1", timeout=60) as answer:
            assert answer.headers["Content-Disposition"].startswith("attachment")
            assert len(answer.read()) == done["bytes"]
        cancelled = ask("/api/movies", {"name": "gone", "fps": 15, "width": 64, "height": 48})
        assert ask(f"/api/movies/{cancelled['id']}/cancel", {}) == {"ok": True}
        with pytest.raises(urllib.error.HTTPError) as unknown:
            ask(f"/api/movies/{cancelled['id']}/rewind", {})
        assert unknown.value.code == 404
        # A frame larger than a frame may be is refused before it is read.
        host, port = base.removeprefix("http://").split(":")
        connection = http.client.HTTPConnection(host, int(port), timeout=60)
        connection.putrequest("POST", f"/api/movies/{started['id']}/frame")
        connection.putheader("Content-Type", "image/png")
        connection.putheader("Origin", base)
        connection.putheader("Content-Length", str(movies.MOST_A_FRAME_MAY_BE + 1))
        connection.endheaders()
        answer = connection.getresponse()
        assert answer.status == 413 and "at most 64 MB" in json.loads(answer.read())["reason"]
        connection.close()
        for length in ("0", "many"):
            connection = http.client.HTTPConnection(host, int(port), timeout=60)
            connection.putrequest("POST", f"/api/movies/{started['id']}/frame")
            connection.putheader("Origin", base)
            connection.putheader("Content-Length", length)
            connection.endheaders()
            answer = connection.getresponse()
            assert answer.status == 400, length
            connection.close()
    finally:
        session.server.shutdown()


def test_a_movie_is_not_made_without_a_study(tmp_path, monkeypatch):
    pytest.importorskip("mdtraj")
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    empty = tmp_path / "empty"
    empty.mkdir()
    session = start_dashboard_session(output=str(empty), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        request = urllib.request.Request(
            base + "/api/movies", data=json.dumps({"name": "x"}).encode(), method="POST",
            headers={"content-type": "application/json", "origin": base})
        with urllib.request.urlopen(request, timeout=60) as answer:
            assert json.loads(answer.read()) == {"ok": False,
                                                 "reason": "No study is open to save it in."}
    finally:
        session.server.shutdown()
