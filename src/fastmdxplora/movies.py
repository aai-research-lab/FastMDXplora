"""Movies of a study's frames, encoded by ffmpeg on this computer.

The Viewer renders each frame of the movie as a picture (PNG), as the
camera button does, and sends them here one by one; ffmpeg, run on the
computer the GUI runs on, encodes them as they arrive into
``movies/<name>.mp4`` in the study. Nothing is bundled: ffmpeg is the one
found on the PATH (or named by ``FASTMDXPLORA_FFMPEG``), and the encoder is
the first of these that encodes a test movie on this computer:

- H.264 in MP4, which slides, web pages and every player open: libx264
  (constant quality, CRF 18), then macOS's own (VideoToolbox) and
  OpenH264;
- VP9 or VP8 in WebM, where no H.264 encoder is there (an LGPL build of
  ffmpeg has none), which browsers and VLC open.

An encoder that ffmpeg lists is tried before it is chosen, since one that
needs hardware (VideoToolbox in a virtual machine) is listed and fails.
The pictures' RGB is turned into the BT.709 YUV that HD video is read as,
and the movie says so, so a player does not shift the colours of a
result's scale.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Callable

__all__ = ["FRAME_RATES", "MOVIES_DIR", "add_frame", "cancel_movie", "encoding",
           "find_ffmpeg", "finish_movie", "movies_of", "start_movie"]

MOVIES_DIR = "movies"
FRAME_RATES = (10, 15, 24, 25, 30, 60)
#: The largest movie: 4K UHD, 3840 by 2160 pixels, in either orientation.
MOST_PIXELS = 3840 * 2160
MOST_FRAMES = 20_000
#: The largest picture a frame may be. A 4K frame of a molecule on a plain
#: ground is a few megabytes as a PNG.
MOST_A_FRAME_MAY_BE = 64_000_000
#: A movie not added to for this long is given up (a tab closed mid-way).
IDLE_SECONDS = 300
#: Movies being made at once, across the GUI's tabs.
MOST_AT_ONCE = 2

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}$")
_ID = re.compile(r"^[0-9a-f]{16}$")
_PNG = b"\x89PNG\r\n\x1a\n"
_INSTALL = ("Install ffmpeg on this computer (conda install -c conda-forge ffmpeg, "
            "brew install ffmpeg, or your system's package) and start the GUI again, "
            "or name it with FASTMDXPLORA_FFMPEG.")


def _bit_rate(width: int, height: int, fps: int) -> str:
    """A bit rate for the encoders without a constant-quality mode: 0.2 bits
    a pixel, which keeps thin bonds and labels sharp."""
    return str(max(2_000_000, int(width * height * fps * 0.2)))


@dataclass(frozen=True)
class Encoder:
    name: str
    container: str
    codec: str
    options: Callable[[int, int, int], list[str]] = field(compare=False)

    @property
    def said(self) -> str:
        return f"{self.container.upper()} ({self.codec})"


_ENCODERS = (
    Encoder("libx264", "mp4", "H.264",
            lambda w, h, fps: ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]),
    Encoder("h264_videotoolbox", "mp4", "H.264",
            lambda w, h, fps: ["-c:v", "h264_videotoolbox", "-b:v", _bit_rate(w, h, fps)]),
    Encoder("libopenh264", "mp4", "H.264",
            lambda w, h, fps: ["-c:v", "libopenh264", "-b:v", _bit_rate(w, h, fps)]),
    Encoder("libvpx-vp9", "webm", "VP9",
            lambda w, h, fps: ["-c:v", "libvpx-vp9", "-crf", "24", "-b:v", "0",
                               "-row-mt", "1", "-deadline", "good", "-cpu-used", "2"]),
    Encoder("libvpx", "webm", "VP8",
            lambda w, h, fps: ["-c:v", "libvpx", "-crf", "8", "-b:v", _bit_rate(w, h, fps)]),
)

#: Where ffmpeg is put by Homebrew and by hand, for a GUI started from
#: somewhere other than a shell (whose PATH is the system's alone).
_ELSEWHERE = ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg")


def find_ffmpeg() -> str | None:
    """The ffmpeg to run: the one ``FASTMDXPLORA_FFMPEG`` names, else the
    PATH's, else Homebrew's."""
    named = os.environ.get("FASTMDXPLORA_FFMPEG", "").strip()
    if named:
        return named if os.path.isfile(named) and os.access(named, os.X_OK) else None
    found = shutil.which("ffmpeg")
    if found:
        return found
    return next((p for p in _ELSEWHERE if os.path.isfile(p) and os.access(p, os.X_OK)), None)


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A picture of one colour, as the Viewer sends them."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    row = b"\x00" + bytes(rgb) * width
    return (_PNG + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))


def _command(ffmpeg: str, encoder: Encoder, fps: int, width: int, height: int,
             out: Path, metadata: dict[str, str] | None = None) -> list[str]:
    command = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
               "-f", "image2pipe", "-framerate", str(fps), "-c:v", "png", "-i", "-",
               # RGB as BT.709 YUV at video range, and the movie tagged so.
               "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
               *encoder.options(width, height, fps),
               "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
               "-color_range", "tv", "-r", str(fps), "-g", str(2 * fps)]
    for key, value in (metadata or {}).items():
        command += ["-metadata", f"{key}={value}"]
    if encoder.container == "mp4":
        # The index first, so a page or a slide plays it before it has all.
        command += ["-movflags", "+faststart"]
    return [*command, "-f", encoder.container, str(out)]


def _encodes(ffmpeg: str, encoder: Encoder) -> bool:
    """Whether this encoder makes a movie on this computer: two frames of
    the size a movie's frames are kept to, through the same command."""
    with tempfile.TemporaryDirectory(prefix="fastmdx-movie-") as folder:
        out = Path(folder) / f"probe.{encoder.container}"
        frame = _png(64, 64, (128, 128, 128))
        try:
            done = subprocess.run(_command(ffmpeg, encoder, 10, 64, 64, out),
                                  input=frame * 2, capture_output=True, timeout=30,
                                  check=False)
        except (OSError, subprocess.SubprocessError):
            return False
        return done.returncode == 0 and out.is_file() and out.stat().st_size > 0


_CHOSEN: dict[tuple[str, float], tuple[Encoder | None, str]] = {}
_CHOOSING = threading.Lock()


def _listed(ffmpeg: str) -> tuple[set[str], str]:
    try:
        listing = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
                                 text=True, timeout=30, check=False).stdout
        banner = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True,
                                timeout=30, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return set(), ""
    names = {line.split()[1] for line in listing.splitlines()
             if len(line.split()) > 1 and line.split()[0].startswith("V")}
    version = re.match(r"ffmpeg version (\S+)", banner)
    return names, version.group(1) if version else ""


def _chosen(ffmpeg: str) -> tuple[Encoder | None, str]:
    """The encoder for movies with this ffmpeg, and its version: chosen once
    for each ffmpeg (as it stands on disk), since trying one takes a second."""
    try:
        key = (ffmpeg, os.stat(ffmpeg).st_mtime)
    except OSError:
        return None, ""
    with _CHOOSING:
        if key not in _CHOSEN:
            names, version = _listed(ffmpeg)
            encoder = next((e for e in _ENCODERS if e.name in names and _encodes(ffmpeg, e)),
                           None)
            _CHOSEN[key] = (encoder, version)
        return _CHOSEN[key]


def encoding() -> dict[str, Any]:
    """What a movie is made with here, or why one cannot be."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return {"ok": False, "reason": f"ffmpeg was not found on this computer. {_INSTALL}"}
    encoder, version = _chosen(ffmpeg)
    if encoder is None:
        return {"ok": False, "ffmpeg": ffmpeg, "version": version,
                "reason": f"The ffmpeg found ({ffmpeg}) encodes neither H.264 nor VP9 or VP8 "
                          f"on this computer. {_INSTALL}"}
    by = f"ffmpeg {version}" if version else "ffmpeg"
    return {"ok": True, "ffmpeg": ffmpeg, "version": version, "encoder": encoder.name,
            "format": encoder.container, "codec": encoder.codec,
            "said": f"{encoder.said}, by {by} on this computer"}


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or not data.startswith(_PNG) or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


@dataclass
class _Movie:
    root: Path
    name: str
    fps: int
    width: int
    height: int
    encoder: Encoder
    process: subprocess.Popen
    errors: IO[bytes]
    partial: Path
    target: Path
    frames: int = 0
    touched: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def said_by_ffmpeg(self) -> str:
        try:
            self.errors.seek(0)
            lines = self.errors.read().decode("utf-8", "replace").strip().splitlines()
        except (OSError, ValueError):
            return ""
        return " ".join(lines[-3:])

    def end(self) -> None:
        """Stops ffmpeg, if it still runs, and forgets the movie's pieces."""
        try:
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
        except OSError:
            pass
        if self.process.poll() is None:
            self.process.kill()
        try:
            self.process.wait(timeout=30)
        except subprocess.SubprocessError:
            pass
        self.errors.close()
        self.partial.unlink(missing_ok=True)


_MOVIES: dict[str, _Movie] = {}
_MOVIES_LOCK = threading.Lock()


def _give_up_the_idle() -> None:
    now = time.monotonic()
    with _MOVIES_LOCK:
        idle = [key for key, movie in _MOVIES.items() if now - movie.touched > IDLE_SECONDS]
        ended = [_MOVIES.pop(key) for key in idle]
    for movie in ended:
        movie.end()


def _whole(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if float(value).is_integer() else None


def start_movie(root: str | Path, name: Any, *, fps: Any, width: Any, height: Any,
                about: str = "") -> dict[str, Any]:
    """Starts ffmpeg on a movie of ``width`` by ``height`` pixels at ``fps``
    frames a second, written as ``movies/<name>.<format>`` in the study once
    finished. ``about`` is kept in the movie as its comment."""
    _give_up_the_idle()
    root = Path(root)
    name = str(name or "").strip()
    if not _NAME.match(name):
        return {"ok": False, "reason": "A movie is named in 1 to 60 letters, digits, spaces, "
                                       "dots, dashes and underscores, starting with a letter "
                                       "or digit."}
    fps, width, height = _whole(fps), _whole(width), _whole(height)
    if fps not in FRAME_RATES:
        return {"ok": False, "reason": "A movie plays at "
                + ", ".join(str(r) for r in FRAME_RATES) + " frames a second."}
    if (width is None or height is None or width < 16 or height < 16
            or width % 2 or height % 2 or width * height > MOST_PIXELS):
        return {"ok": False, "reason": "A movie's frame is an even number of pixels each way, "
                                       "16 at least, and 3840 by 2160 at most."}
    known = encoding()
    if not known["ok"]:
        return known
    encoder = next(e for e in _ENCODERS if e.name == known["encoder"])
    with _MOVIES_LOCK:
        if len(_MOVIES) >= MOST_AT_ONCE:
            return {"ok": False, "reason": f"{MOST_AT_ONCE} movies are being made already; "
                                           "one can be made once they are."}
    folder = root / MOVIES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{name}.{encoder.container}"
    # Hidden until it is whole, so the file list and a public bind skip it.
    partial = folder / f".{name}.{secrets.token_hex(4)}.partial.{encoder.container}"
    from fastmdxplora import __version__

    said = " ".join(str(about or "").split())[:500]
    metadata = {"title": name,
                "comment": f"Made with FastMDXplora {__version__}" + (f": {said}" if said else "")}
    errors = tempfile.TemporaryFile(prefix="fastmdx-movie-")
    try:
        process = subprocess.Popen(
            _command(known["ffmpeg"], encoder, fps, width, height, partial, metadata),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
    except OSError as exc:
        errors.close()
        return {"ok": False, "reason": f"ffmpeg could not be started: {exc}"}
    movie = _Movie(root, name, fps, width, height, encoder, process, errors, partial, target)
    key = secrets.token_hex(8)
    with _MOVIES_LOCK:
        _MOVIES[key] = movie
    return {"ok": True, "id": key, "format": encoder.container, "codec": encoder.codec,
            "encoder": encoder.name, "file": f"{MOVIES_DIR}/{target.name}"}


def _movie(key: Any) -> _Movie | None:
    _give_up_the_idle()
    if not isinstance(key, str) or not _ID.match(key):
        return None
    with _MOVIES_LOCK:
        return _MOVIES.get(key)


def _forget(key: str) -> _Movie | None:
    with _MOVIES_LOCK:
        return _MOVIES.pop(key, None)


_NOT_MADE = {"ok": False, "reason": "No movie is being made under that id: it was finished, "
                                    "cancelled, or given up after five minutes without a "
                                    "frame."}


def add_frame(key: Any, data: bytes) -> dict[str, Any]:
    """Gives ffmpeg the movie's next frame, a PNG of the movie's size."""
    movie = _movie(key)
    if movie is None:
        return dict(_NOT_MADE)
    size = _png_size(data)
    if size is None:
        return {"ok": False, "reason": "A frame is sent as a PNG."}
    if size != (movie.width, movie.height):
        return {"ok": False, "reason": f"This frame is {size[0]} by {size[1]} pixels; "
                                       f"the movie's are {movie.width} by {movie.height}."}
    with movie.lock:
        if movie.frames >= MOST_FRAMES:
            return {"ok": False, "reason": f"A movie has {MOST_FRAMES} frames at most."}
        try:
            movie.process.stdin.write(data)
            movie.process.stdin.flush()
        except (OSError, ValueError):
            _forget(key)
            said = movie.said_by_ffmpeg()
            movie.end()
            return {"ok": False, "reason": "ffmpeg stopped while encoding the movie"
                                           + (f": {said}" if said else ".")}
        movie.frames += 1
        movie.touched = time.monotonic()
        return {"ok": True, "frames": movie.frames}


def finish_movie(key: Any) -> dict[str, Any]:
    """Ends the movie: ffmpeg finishes the file, which takes the movie's
    name in the study."""
    movie = _movie(key)
    if movie is None or _forget(key) is None:
        return dict(_NOT_MADE)
    with movie.lock:
        if movie.frames == 0:
            movie.end()
            return {"ok": False, "reason": "The movie was given no frames."}
        try:
            movie.process.stdin.close()
            code = movie.process.wait(timeout=600)
        except (OSError, subprocess.SubprocessError):
            code = None
        if code != 0 or not movie.partial.is_file():
            said = movie.said_by_ffmpeg()
            movie.end()
            return {"ok": False, "reason": "ffmpeg did not finish the movie"
                                           + (f": {said}" if said else ".")}
        os.replace(movie.partial, movie.target)
        movie.end()
    return {"ok": True, "file": f"{MOVIES_DIR}/{movie.target.name}",
            "bytes": movie.target.stat().st_size, "frames": movie.frames, "fps": movie.fps,
            "seconds": round(movie.frames / movie.fps, 2), "width": movie.width,
            "height": movie.height, "format": movie.encoder.container,
            "codec": movie.encoder.codec}


def cancel_movie(key: Any) -> dict[str, Any]:
    """Stops making the movie and leaves nothing of it."""
    movie = _movie(key)
    if movie is None or _forget(key) is None:
        return dict(_NOT_MADE)
    with movie.lock:
        movie.end()
    return {"ok": True}


def movies_of(root: str | Path) -> dict[str, Any]:
    """The movies made of a study, newest first."""
    folder = Path(root) / MOVIES_DIR
    found = [p for p in folder.glob("*") if p.is_file() and not p.name.startswith(".")
             and p.suffix in {".mp4", ".webm"} and _NAME.match(p.stem)] \
        if folder.is_dir() else []
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return {"ok": True, "movies": [{"name": p.stem, "file": f"{MOVIES_DIR}/{p.name}",
                                    "bytes": p.stat().st_size} for p in found]}
