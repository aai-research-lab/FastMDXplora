"""The frames arrive in pieces, as XTC, and play from the first.

The Viewer fetched the frames whole, as a DCD of up to 120 MB, before it
showed one: over a tunnel, or from a hosted GUI, a large system waited for
all of it. The frames are now sent as XTC, about a third of a DCD's size at
a hundredth of an angstrom, in pieces the Viewer plays from the first while
the rest arrive.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    from fastmdxplora.gui.trajectory_frames import frames_info
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    root = _helical_study(tmp_path_factory.mktemp("pieces") / "study", analyse=False)
    assert frames_info(root)["available"]
    return root


@pytest.fixture
def small_pieces(monkeypatch, study):
    """Pieces of two frames each, so a small study has several."""
    from fastmdxplora.gui import trajectory_frames

    atoms = md.load_topology(str(study / "simulation" / "frames_topology.pdb")).n_atoms
    monkeypatch.setattr(trajectory_frames, "PIECE_ATOM_FRAMES", 2 * atoms)
    folder = study / "simulation" / "frames_pieces"
    if (folder / "index.json").is_file():
        (folder / "index.json").unlink()
    return atoms


def test_the_pieces_are_the_frames(study, small_pieces):
    from fastmdxplora.gui.trajectory_frames import frames_pieces

    said = frames_pieces(study)
    assert said["ok"] and said["total"] == 6 and said["atoms"] == small_pieces
    assert [(p["start"], p["frames"]) for p in said["pieces"]] == [(0, 2), (2, 2), (4, 2)]
    assert said["pieces"][1]["url"] == "/structure/frames-piece.xtc?k=1"
    assert said["bytes"] < said["dcd_bytes"] / 2
    simulation = study / "simulation"
    whole = md.load_dcd(str(simulation / "frames.dcd"), top=str(simulation / "frames_topology.pdb"))
    pieces = md.join([md.load_xtc(str(simulation / "frames_pieces" / f"piece_{k}.xtc"),
                                  top=str(simulation / "frames_topology.pdb")) for k in range(3)])
    # To a thousandth of a nanometre, as XTC keeps them.
    assert np.abs(pieces.xyz - whole.xyz).max() <= 0.0006
    stamp = (simulation / "frames_pieces" / "piece_0.xtc").stat().st_mtime_ns
    assert frames_pieces(study) == said
    assert (simulation / "frames_pieces" / "piece_0.xtc").stat().st_mtime_ns == stamp


def test_no_frames_no_pieces(tmp_path):
    from fastmdxplora.gui.trajectory_frames import frames_pieces

    assert frames_pieces(tmp_path) == {"ok": False, "reason": "There are no frames yet."}


def test_the_server_sends_them(study, small_pieces):
    import urllib.error
    import urllib.request

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)

    def get(path):
        try:
            with urllib.request.urlopen(session.url + path, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, b""

    try:
        said = json.loads(get("/api/frames-pieces")[1])
        assert said["ok"] and len(said["pieces"]) == 3
        status, piece = get(said["pieces"][2]["url"])
        assert status == 200
        assert piece == (study / "simulation" / "frames_pieces" / "piece_2.xtc").read_bytes()
        assert get("/structure/frames-piece.xtc?k=9")[0] == 404
        assert get("/structure/frames-piece.xtc?k=..%2F..%2Fx")[0] == 404
        status, xtc = get("/structure/frames.dcd?as=xtc")
        assert status == 200
        assert xtc == (study / "simulation" / "frames.xtc").read_bytes()
        superposed = json.loads(get("/api/frames-superposed?on=backbone")[1])
        status, fitted = get(superposed["url"] + "&as=xtc")
        assert status == 200 and fitted == (
            study / "simulation" / "frames_superposed_backbone.xtc").read_bytes()
    finally:
        session.server.shutdown()


def test_the_viewer_plays_from_the_first_piece(study, small_pieces):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            # The Playback starts closed; these drive what is in it.
            page.add_init_script("try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
            page.set_default_timeout(120000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => {state}.model")
            played = page.evaluate("async () => window.FastMDXMoleculeViewer.loadPlayback("
                                   "await (await fetch('/api/frames-info')).json())")
            assert played is True, errors
            page.wait_for_function(f"() => {state}.framesPieces"
                                   f" && {state}.framesPieces.loaded === 6"
                                   f" && {state}.engine.frameCount() === 6")
            pieces = page.evaluate(f"() => {{ const p = {state}.framesPieces;"
                                   " return {total:p.total, loaded:p.loaded, pieces:p.pieces}; }")
            loaded = page.get_attribute("#trajectory-row", "data-frames-loaded")
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {frame: 5}}))")
            page.wait_for_function(f"() => {state}.engine.frame() === 5")
            # Superposed: the fitted frames read as XTC in place of these.
            page.select_option("#traj-superpose", "backbone")
            page.wait_for_function(f"() => {state}.superposedUrl")
            after = page.evaluate(f"() => [{state}.engine.frameCount(), {state}.engine.frame(),"
                                  f" {state}.playbackFrames]")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert pieces == {"total": 6, "loaded": 6, "pieces": 3}
    assert loaded == "6"
    assert after == [6, 5, 6]


#: The pieces after the first held until the test lets each come.
_HELD_PIECES = """(() => {
    const real = window.fetch;
    const held = {};
    const one = (k) => held[k] || (held[k] = (() => {
        let let_it; const coming = new Promise((done) => { let_it = done; });
        return {coming, let_it};
    })());
    window.letThePieceCome = (k) => one(String(k)).let_it();
    window.fetch = async (input, init) => {
        const asked = String((input && input.url) || input).match(/frames-piece\\.xtc\\?k=(\\d+)/);
        if (asked && asked[1] !== "0") await one(asked[1]).coming;
        return real(input, init);
    };
})();"""

#: Each piece appended, in the engine's turn, only once a frame has been
#: asked for after it began: that frame is then on its way, queued behind
#: the piece, as the piece ends. `window.appending` counts the pieces begun.
_APPENDED_ONCE_A_FRAME_IS_ASKED = """() => {
    const state = window.FastMDXMoleculeViewer.STATE;
    const engine = state.engine;
    const real = Object.getPrototypeOf(engine).appendFrames;
    window.appending = 0;
    engine.appendFrames = async function (bytes) {
        window.appending += 1;
        const asked = state.playbackFrameAsked;
        const until = Date.now() + 60000;
        while (state.playbackFrameAsked === asked && Date.now() < until) {
            await new Promise((done) => setTimeout(done, 20));
        }
        return real.call(this, bytes);
    };
}"""


def _a_page(pw, session):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.add_init_script("try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
    page.add_init_script(_HELD_PIECES)
    page.set_default_timeout(120000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(session.url + "#viewer", wait_until="domcontentloaded")
    if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
        pytest.skip("this browser has no WebGL, so the viewer cannot render")
    page.wait_for_function("() => window.FastMDXMoleculeViewer.STATE.model"
                           " && window.FastMDXMoleculeViewer.STATE.engine")
    return browser, page, errors


_PLAY = ("async () => window.FastMDXMoleculeViewer.loadPlayback("
         "await (await fetch('/api/frames-info')).json())")


def test_a_frame_asked_for_while_a_piece_arrives_is_the_frame_shown(study, small_pieces):
    """A frame asked for while a piece of the frames was appended: the piece
    then showed the frame again, and asked for the one before the frame
    asked for over it, as the frame asked for was not yet shown. The frame
    asked for stayed unshown."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _a_page(pw, session)
            played = page.evaluate(_PLAY)
            first = page.evaluate(f"() => [{state}.engine.frameCount(), {state}.engine.frame()]")
            page.evaluate(_APPENDED_ONCE_A_FRAME_IS_ASKED)
            page.evaluate("() => window.letThePieceCome(1)")
            page.wait_for_function("() => window.appending === 1")
            # Its frame queued behind the piece being appended.
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {frame: 1}}))")
            # Said once the piece has shown its frame again.
            page.wait_for_selector('#trajectory-row[data-frames-loaded="4"]', state="attached")
            shown = page.evaluate(f"() => [{state}.engine.frameCount(), {state}.engine.frame(),"
                                  " document.getElementById('traj-slider').value]")
            # The last piece appended as it comes.
            page.evaluate(f"() => {{ delete {state}.engine.appendFrames; }}")
            page.evaluate("() => window.letThePieceCome(2)")
            page.wait_for_selector('#trajectory-row[data-frames-loaded="6"]', state="attached")
            last = page.evaluate(f"() => [{state}.engine.frameCount(), {state}.engine.frame()]")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert played is True
    assert first == [2, 0]
    assert shown == [4, 1, "1"]
    assert last == [6, 1]


def test_the_first_frame_is_shown_while_a_piece_arrives(study, small_pieces):
    """The load's first frame asked for while the second piece was appended:
    the piece asked over it, and the load said it could not show it (CI's
    120 s wait for frames that had all arrived)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _a_page(pw, session)
            page.evaluate(_APPENDED_ONCE_A_FRAME_IS_ASKED)
            # The load's last call to the engine before its first frame is
            # the frames' cartoon: there the second piece is let come, and
            # the cartoon given once its append is queued. The load then
            # asks for its first frame while that piece is appended.
            page.evaluate(f"""() => {{
                const s = {state};
                const engine = s.engine;
                const real = Object.getPrototypeOf(engine).setSecondaryStructure;
                window.cartoons = 0;
                engine.setSecondaryStructure = async function (...given) {{
                    if (s.framesPieces && !window.cartoons++) {{
                        window.letThePieceCome(1);
                        const until = Date.now() + 60000;
                        while (s.framesPieces.baseParts.length < 2 && Date.now() < until) {{
                            await new Promise((done) => setTimeout(done, 20));
                        }}
                    }}
                    return real.apply(this, given);
                }};
            }}""")
            played = page.evaluate(_PLAY)
            page.wait_for_selector('#trajectory-row[data-frames-loaded="4"]', state="attached")
            # The last piece appended as it comes.
            page.evaluate(f"() => {{ delete {state}.engine.appendFrames; }}")
            page.evaluate("() => window.letThePieceCome(2)")
            page.wait_for_selector('#trajectory-row[data-frames-loaded="6"]', state="attached")
            after = page.evaluate(f"() => [{state}.playbackLoaded, {state}.engine.frameCount(),"
                                  f" {state}.engine.frame(), window.cartoons > 0]")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert played is True
    assert after == [True, 6, 0, True]


def test_frames_superposed_while_a_piece_waits_keep_their_count(study, small_pieces):
    """The frames superposed (given whole) while a piece waited for a frame
    asked for: the piece then wrote its own count, 4, over the 6 frames
    now there, and the last two could not be reached until a reload."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _a_page(pw, session)
            played = page.evaluate(_PLAY)
            page.evaluate(_APPENDED_ONCE_A_FRAME_IS_ASKED)
            # Said once the superposed frames are queued for the engine.
            page.evaluate(f"""() => {{
                const engine = {state}.engine;
                const real = Object.getPrototypeOf(engine).setFramesCoordinates;
                window.superposing = 0;
                Object.defineProperty(engine, "setFramesCoordinates", {{
                    configurable: true,
                    get() {{
                        window.superposing += 1;
                        return real;
                    }},
                }});
            }}""")
            page.evaluate("() => window.letThePieceCome(1)")
            page.wait_for_function("() => window.appending === 1")
            page.select_option("#traj-superpose", "backbone")
            page.wait_for_function("() => window.superposing === 1")
            # Queued behind them: the piece waits for it once appended.
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {frame: 1}}))")
            page.wait_for_function(f"() => {state}.superposedUrl")
            page.evaluate(f"() => {{ delete {state}.engine.appendFrames; }}")
            page.evaluate("() => window.letThePieceCome(2)")
            page.wait_for_function(f"() => {state}.framesArriving === null")
            after = page.evaluate(f"""() => [{state}.engine.frameCount(), {state}.playbackFrames,
                {state}.framesPieces.loaded,
                document.getElementById('trajectory-row').dataset.framesLoaded,
                document.getElementById('traj-slider').max, {state}.engine.frame()]""")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert played is True
    assert after == [6, 6, 6, "6", "5", 1]


def test_frames_asked_for_before_the_structure_arrives_keep_the_canvas(study):
    """Play pressed while the structure was on its way: the structure
    arrived after the frames and was rendered in their place."""
    pytest.importorskip("playwright.sync_api")
    import time

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            # The Playback starts closed; these drive what is in it.
            page.add_init_script("try { localStorage.setItem('fmx.viewerPlaybackOpen', '1'); } catch (e) {}")
            page.set_default_timeout(120000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def slowly(route):
                time.sleep(4)
                route.continue_()

            page.route("**/structure/topology.pdb*", slowly)
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function("() => window.FastMDXMoleculeViewer"
                                   f" && {state}.structureInfo")
            page.evaluate("async () => window.FastMDXMoleculeViewer.loadPlayback("
                          "await (await fetch('/api/frames-info')).json())")
            page.wait_for_function(f"() => {state}.structurePdb", timeout=60000)
            page.wait_for_timeout(1500)
            after = page.evaluate(f"() => [{state}.mode, {state}.engine.frameCount()]")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert after == ["playback", 6]
