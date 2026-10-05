"""Movies of a study made without opening the GUI: `fastmdx movie` and the
`make_movie` tool of `fastmdx mcp`.

A movie is rendered by the Viewer itself, so it is the movie the Viewer's
Movie section makes: the GUI is started for the study on this computer,
reachable from it alone; a browser with no window opens its Viewer, shows a
view the person saved (or the Viewer as it opens), changed as asked; and
the Viewer's own movie code renders each frame and has ffmpeg encode it
into the study's ``movies/`` folder. The browser is Playwright's Chromium,
or Chrome or Edge where they are installed; nothing is bundled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

__all__ = ["BROWSER_INSTALL", "SIZES", "make_movie"]

#: The sizes a movie is made at, as the Viewer's Movie section offers them.
SIZES = ("1280x720", "1920x1080", "3840x2160")
BROWSER_INSTALL = ("Movies are rendered by the Viewer in a browser with no window: install "
                   "Playwright (pip install \"fastmdxplora[movies]\") and its Chromium "
                   "(playwright install chromium), or have Chrome or Edge installed.")
#: What a view may be changed by, as `write_scene` takes them.
_OVERRIDES = ("frame", "representation", "colour", "superposed", "superposed_to",
              "smoothed_over")


def _browser(pw: Any) -> tuple[Any, str]:
    """A browser with no window that renders WebGL: Playwright's Chromium,
    else Chrome, else Edge."""
    from playwright.sync_api import Error

    reasons = []
    for channel in (None, "chrome", "msedge"):
        try:
            browser = pw.chromium.launch(
                channel=channel, args=["--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
            return browser, channel or "chromium"
        except Error as exc:
            reasons.append(str(exc).splitlines()[0])
    return None, "; ".join(reasons)


def make_movie(study: str | Path, *, name: str = "movie", view: str | None = None,
               changes: dict[str, Any] | None = None, first: int | None = None,
               last: int | None = None, every: int = 1, between: int = 0, fps: int = 24,
               size: str = "1920x1080", turn: bool = False, time: bool = True,
               said: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Makes a movie of a study as its Viewer makes one, and returns what was
    made (``file``, ``frames``, ``seconds`` and the rest, as the Movie
    section says them) or why nothing was (``ok`` false, ``reason``).

    ``view`` names a view the person saved in the GUI; ``changes`` changes it
    as `write_scene` does (frame, representation, colour, superposed,
    superposed_to, smoothed_over). ``first``, ``last`` and ``every`` choose
    the frames played (all of them by default), ``between`` (1, 3 or 7) puts
    frames in between each two, ``turn`` turns the camera once over the
    movie, and ``time`` stamps each frame's simulated time. ``said`` is told
    how far it has got."""
    from fastmdxplora.gui.browse import is_study
    from fastmdxplora.gui.saved_views import views_of
    from fastmdxplora.movies import FRAME_RATES, encoding

    tell = said or (lambda text: None)
    folder = Path(study).expanduser()
    if not folder.is_dir() or not is_study(folder):
        return {"ok": False, "reason": f"{folder} is not a study."}
    if size not in SIZES:
        return {"ok": False, "reason": "A movie is made at " + ", ".join(SIZES) + "."}
    if fps not in FRAME_RATES:
        return {"ok": False, "reason": "A movie plays at "
                + ", ".join(str(r) for r in FRAME_RATES) + " frames a second."}
    if between not in (0, 1, 3, 7):
        return {"ok": False, "reason": "A movie puts 1, 3 or 7 frames in between two frames "
                                       "played, or none."}
    if every < 1 or (first is not None and first < 0) or (last is not None and last < 0):
        return {"ok": False, "reason": "A movie's frames are counted from 0, every 1 or more."}
    shown: dict[str, Any] | None = None
    if view:
        saved = views_of(folder)["views"]
        found = [v for v in saved if v.get("name") == view]
        if not found:
            return {"ok": False, "reason": f"The study has no view named {view!r}; its views: "
                                           + (", ".join(v["name"] for v in saved) or "none")
                                           + "."}
        shown = dict(found[0])
    unknown = sorted(set(changes or {}) - set(_OVERRIDES))
    if unknown:
        return {"ok": False, "reason": "A view is changed by " + ", ".join(_OVERRIDES)
                                       + f", not {', '.join(unknown)}."}
    if changes:
        shown = {**(shown or {}), **{k: v for k, v in changes.items() if v is not None}}
    known = encoding()
    if not known["ok"]:
        return known
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "reason": BROWSER_INSTALL}
    from fastmdxplora.gui.server import start_dashboard_session

    width, height = (int(part) for part in size.split("x"))
    asked = {"name": name, "fps": fps, "between": between, "turn": turn, "time": time,
             "every": every, "size": size, "keep": True}
    if first is not None:
        asked["from"] = first
    if last is not None:
        asked["to"] = last
    session = start_dashboard_session(output=str(folder), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, by = _browser(pw)
            if browser is None:
                return {"ok": False, "reason": f"{BROWSER_INSTALL} ({by})"}
            try:
                page = browser.new_page(viewport={"width": max(1280, width // 2 + 480),
                                                  "height": 900})
                page.set_default_timeout(300_000)
                tell("Opening the study's Viewer…")
                page.goto(session.url + "#viewer", wait_until="domcontentloaded")
                if not page.evaluate(
                        "() => !!document.createElement('canvas').getContext('webgl2')"
                        " || !!document.createElement('canvas').getContext('webgl')"):
                    return {"ok": False, "reason": f"The browser ({by}) cannot render WebGL "
                                                   "here, so the Viewer cannot render."}
                page.wait_for_function(
                    "() => { const s = window.FastMDXMoleculeViewer"
                    " && window.FastMDXMoleculeViewer.STATE;"
                    " return !!(s && s.engine && (s.model || s.framesRendered)); }")
                if shown is not None:
                    tell("Showing the view…")
                    # A view changed without a camera of its own keeps the
                    # camera the Viewer opened with.
                    if not page.evaluate(
                            "(view) => { const v = window.FastMDXMoleculeViewer;"
                            " return v.showView({camera: v.STATE.engine.cameraSnapshot(),"
                            " ...view}); }", shown):
                        return {"ok": False, "reason": "The Viewer could not show the view."}
                as_shown = page.evaluate(
                    "() => { const s = window.FastMDXMoleculeViewer.STATE;"
                    " return {representation: s.representation, colour: s.colorMode,"
                    " superposed: s.superposed}; }")
                page.evaluate("() => window.FastMDXViewerMovie.encoding()")
                tell("Rendering the frames…")
                made = page.evaluate("(asked) => window.FastMDXViewerMovie.make(asked)", asked)
            finally:
                browser.close()
    finally:
        session.server.shutdown()
        session.server.server_close()
    if not made:
        return {"ok": False, "reason": "The Viewer made no movie and said nothing of why."}
    if made.get("ok"):
        made["path"] = str(folder / made["file"])
        made["browser"] = by
        made["shown"] = as_shown
        made["encoder"] = known["said"]
    return made
