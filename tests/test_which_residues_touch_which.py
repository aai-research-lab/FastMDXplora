"""Which residues of the protein touch which over the frames played.

The Viewer's Contact map gives each pair of residues the share of frames
played they were in contact in (any heavy atoms within 4.5 Å, MDTraj's
closest-heavy contact; residues of one chain fewer than three apart left
out), compares two states the cluster analysis found pair by pair, and
follows one pair in the structure frame by frame.

Trypsin whose residues 16 to 65 sit 4 Å along x in the second half of the
run (the States tests' study): the pairs between those residues and the
rest are the ones that change between the two states.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

md = pytest.importorskip("mdtraj")

VIEWER = "window.FastMDXMoleculeViewer"
CMAP = "window.FastMDXContactMap"


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    from tests.test_the_states_a_study_visited import _two_states

    return _two_states(tmp_path_factory.mktemp("contacts") / "study")


def _frames(study: Path):
    return md.load_dcd(str(study / "simulation" / "frames.dcd"),
                       top=str(study / "simulation" / "frames_topology.pdb"))


def _shares(said):
    n = len(said["residues"])
    return {(i, j): v for i, j, v in zip(said["pairs"]["i"], said["pairs"]["j"],
                                         said["pairs"]["v"])}, n


def test_each_pair_s_share_is_mdtraj_s_closest_heavy_contact(study):
    from fastmdxplora.gui.contact_map import MAP_FILE, contact_map

    said = contact_map(study)
    assert said["ok"] and said["cutoff_angstrom"] == 4.5 and said["frames"] == 30
    shares, n = _shares(said)
    frames = _frames(study)
    residues = [r for r in frames.topology.residues if r.is_protein]
    assert n == len(residues) and said["residues"][0] == ["A", 16, "", "ILE"]
    # The pairs of a frame are MDTraj's own, in a frame of each state.
    from fastmdxplora.gui.contact_map import _counts

    for frame in (3, 20):
        distances, pairs = md.compute_contacts(frames[frame], contacts="all",
                                               scheme="closest-heavy")
        theirs = {(int(i), int(j)) for (i, j), d in zip(pairs, distances[0]) if d <= 0.45}
        keys, counts = _counts(frames, [frame])
        assert {(int(k // n), int(k % n)) for k in keys} == theirs
        assert set(counts.tolist()) == {1}
    # Over every frame, each pair's share is its frames in contact.
    keys, counts = _counts(frames, range(frames.n_frames))
    assert shares == {(int(k // n), int(k % n)): round(c / 30, 4) for k, c in zip(keys, counts)}
    # Nothing is counted that MDTraj would not count: neighbours in a chain.
    assert all(j - i >= 3 for i, j in shares)
    assert json.loads((study / "simulation" / MAP_FILE).read_text())["pairs"] == said["pairs"]
    assert contact_map(study) == said


def test_two_states_compared_pair_by_pair(study):
    from fastmdxplora.gui.contact_map import contact_map

    said = contact_map(study, first=0, second=1, method="kmeans")
    assert said["ok"] and said["compared"] == [0, 1] and said["frames_of"] == [15, 15]
    differences, n = _shares(said)
    moving = {k for k, r in enumerate(said["residues"]) if 16 <= r[1] <= 65}
    # A pair whose closest atoms sit at the cutoff flickers with the frames'
    # noise; one that changed with the state changed in most of its frames.
    changed = {pair: d for pair, d in differences.items() if abs(d) > 0.8}
    assert len(changed) > 20
    # What changed is between the residues that moved and the rest.
    assert all((i in moving) != (j in moving) for i, j in changed)
    assert said["said"].startswith("How much more often each pair of residues was in contact "
                                   "in state 1 (15 frames played) than in state 0 (15)")
    assert said["said"].endswith("red where more often in state 1, blue where more often in "
                                 "state 0.")


def test_one_pair_followed_frame_by_frame(study):
    from fastmdxplora.gui.contact_map import contact_map, contact_pair

    shares, _ = _shares(contact_map(study))
    pair = next(p for p, v in shares.items() if 0.3 < v < 0.7)
    said = contact_pair(study, *pair)
    assert said["ok"] and said["share"] == pytest.approx(shares[pair], abs=1e-4)
    frames = _frames(study)
    for frame in (0, 29):
        a, b = said["atoms"][frame]
        assert frames.topology.atom(a).residue.index != frames.topology.atom(b).residue.index
        distance = md.compute_distances(frames[frame], [[a, b]], periodic=False)[0, 0] * 10
        assert said["angstrom"][frame] == pytest.approx(distance, abs=0.01)


def test_what_cannot_be_mapped_is_said(study, tmp_path):
    from fastmdxplora.gui.contact_map import contact_map, contact_pair

    assert contact_map(tmp_path)["reason"] == "There are no frames to find contacts in yet."
    assert contact_map(study, first="a", second=1)["reason"] == (
        "Two states are compared by their numbers.")
    assert contact_map(study, first=0, second=7)["reason"] == "No frame played is in state 7."
    assert contact_pair(study, 3, 3)["reason"].startswith("Two residues of the map, 0 to ")
    assert contact_pair(study, "x", 3)["reason"] == (
        "Two residues are named by their places in the map.")


def test_the_viewer_maps_them_and_follows_a_pair(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(120000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => {VIEWER} && {VIEWER}.STATE.engine"
                                   f" && {VIEWER}.STATE.playbackPayload")
            page.evaluate(f"async () => {{ await {VIEWER}.movie.frames(); }}")
            page.wait_for_function("() => !document.getElementById('side-cmap').hidden")
            page.click("#side-cmap > summary")
            page.wait_for_function(f"() => {CMAP}.state.data")
            page.wait_for_function("() => !document.getElementById('cmap-states-row').hidden")
            # Large enough that a cell is several pixels across.
            page.evaluate("() => { const c = document.getElementById('cmap-canvas');"
                          " c.style.maxWidth = 'none'; c.style.width = '1000px';"
                          " window.dispatchEvent(new Event('resize')); }")
            page.locator("#cmap-canvas").scroll_into_view_if_needed()
            # The cell of a pair, pointed at and clicked.
            pair = page.evaluate(f"""() => {{
                const d = {CMAP}.state.data;
                const k = d.pairs.v.findIndex((v) => v > 0.3 && v < 0.7);
                return [d.pairs.i[k], d.pairs.j[k], d.pairs.v[k]];
            }}""")
            at = page.evaluate(f"""(pair) => {{
                const canvas = document.getElementById('cmap-canvas');
                const box = canvas.getBoundingClientRect();
                const n = {CMAP}.state.data.residues.length;
                const size = canvas.width;
                const left = Math.round(size * 0.12), top = Math.round(size * 0.03);
                const cell = (size - left - top) / n;
                const scale = box.width / canvas.width;
                return [box.left + (left + (pair[1] + 0.5) * cell) * scale,
                        box.top + (top + (pair[0] + 0.5) * cell) * scale];
            }}""", pair)
            # Sent to the map itself: a canvas this wide runs out of the
            # panel, where a pointer would reach what is over it.
            send = ("([kind, x, y]) => document.getElementById('cmap-canvas').dispatchEvent("
                    "new MouseEvent(kind, {clientX: x, clientY: y, bubbles: true}))")
            page.evaluate(send, ["mousemove", *at])
            hover = page.text_content("#cmap-hover")
            page.evaluate(send, ["click", *at])
            page.wait_for_function(f"() => {CMAP}.state.pairData")
            page.wait_for_function(f"() => {VIEWER}.STATE.engine"
                                   ".interactionsHeld('contact-pair') === 1")
            selected = page.evaluate(f"() => {VIEWER}.STATE.selection.residues.length")
            note = page.text_content("#cmap-note")
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {frame: 25}}))")
            page.wait_for_function("() => document.getElementById('cmap-note')"
                                   ".textContent.endsWith('in frame 25.')")
            page.click("#cmap-clear")
            page.wait_for_function(f"() => {VIEWER}.STATE.engine"
                                   ".interactionsHeld('contact-pair') === 0")
            # Two states compared.
            page.select_option("#cmap-second", "1")
            page.select_option("#cmap-first", "0")
            page.click("#cmap-compare")
            page.wait_for_function(f"() => {CMAP}.state.data && {CMAP}.state.data.compared")
            compared = page.evaluate(f"() => {CMAP}.state.data.compared")
            every = page.is_visible("#cmap-all")
            key_compared = (page.text_content("#cmap-key-low"),
                            page.text_content("#cmap-key-high"))
            labels = page.eval_on_selector_all("#cmap-states-row label",
                                               "l => l.map((x) => x.firstChild.textContent.trim())")
            page.click("#cmap-all")
            page.wait_for_function(f"() => {CMAP}.state.data && !{CMAP}.state.data.compared")
            key_every = (page.text_content("#cmap-key-low"), page.text_content("#cmap-key-high"))
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert hover.endswith(f"in contact in {round(pair[2] * 100)}% of frames")
    assert selected == 2
    assert "in contact in" in note and note.endswith("in frame 0.")
    assert compared == [0, 1] and every
    # "State 1 vs 0", and a key that says which colour is which state.
    assert labels == ["State", "vs"]
    assert key_compared == ("More often in state 0", "More often in state 1")
    assert key_every == ("Never in contact", "In contact in every frame")
