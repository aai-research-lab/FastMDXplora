"""An exact source frame, and the chart points that lead to one, are given only
when they can be.

``test_a_graph_opens_exact_source_frame.py`` follows a chart click to the
coordinates of a frame the browser never sampled. These are the refusals and
the less common routes beside it: a frame whose source is no longer the
production trajectory, an alignment that cannot be made, a frame that cannot
be read or written, the DCD-only route for an engine that cannot read XTC, a
source-frame scene asked for smoothed, and profile and figure rows whose
residue or atom cannot be named uniquely in the topology the analysis read.
"""

from __future__ import annotations

import json
import types
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from tests.test_a_graph_opens_exact_source_frame import _study  # noqa: E402


@pytest.fixture
def study(tmp_path):
    from fastmdxplora.gui.trajectory_frames import frames_info

    root = _study(tmp_path / "study")
    info = frames_info(root, most_frames=3)
    assert info["playback_available"] and info["frame_indices"] == [0, 5, 10]
    return root, info["source_signature"]


def _deposit(root: Path) -> None:
    (root / "setup").mkdir(exist_ok=True)
    (root / "setup" / "input.pdb").write_bytes(
        (root / "simulation" / "trajectory_topology.pdb").read_bytes())


def _read_dcd(root: Path, body: bytes, tmp_path: Path):
    target = tmp_path / "answer.dcd"
    target.write_bytes(body)
    return md.load_dcd(str(target), top=str(root / "simulation" / "frames_topology.pdb"))


# -- what an exact frame request names ---------------------------------------


def test_an_exact_frame_is_only_from_the_recorded_production_trajectory(
        monkeypatch, study):
    from fastmdxplora.gui import trajectory_frames

    root, signature = study
    playable = {"playback_available": True, "source_kind": "production-dcd",
                "source_signature": signature, "n_frames_total": 11,
                "frame_indices": [0, 5, 10], "frame_times_ns": [None] * 3}
    monkeypatch.setattr(trajectory_frames, "frames_info", lambda *_a, **_kw: dict(playable))
    monkeypatch.setattr(trajectory_frames, "_source", lambda _out: {"kind": "history"})
    assert trajectory_frames.exact_frame_info(root, 7) == {
        "ok": False, "reason": "The study's source trajectory is unavailable.",
        "code": "analysis.data.absent"}
    monkeypatch.setattr(trajectory_frames, "_source", lambda _out: None)
    assert trajectory_frames.exact_frame_info(root, 7)["ok"] is False
    playable["source_kind"] = "live-history"
    answer = trajectory_frames.exact_frame_info(root, 7)
    assert answer == {"ok": False, "reason": "An exact frame is available only from this "
                      "study's production trajectory.", "code": "analysis.data.absent"}


@pytest.mark.parametrize("frame", [True, 7.0, "7", None])
def test_only_an_integer_names_a_source_frame(study, frame):
    from fastmdxplora.gui.trajectory_frames import exact_frame_info

    root, _signature = study
    assert exact_frame_info(root, frame) == {
        "ok": False, "reason": "A source frame is a non-negative integer.",
        "code": "config.option.wrong_type"}


@pytest.mark.parametrize("on,ligand,cutoff,to,reason", [
    ("pocket", None, 5.0, "first", "no ligand was named"),
    ("pocket", "LIG", 40, "first", "cutoff is 1 to 20"),
    ("sidechains", None, 5.0, "first", "Frames are superposed on"),
    ("backbone", None, 5.0, "elsewhere", "Frames are fitted to"),
])
def test_an_alignment_the_viewer_cannot_make_is_refused_before_loading(
        study, on, ligand, cutoff, to, reason):
    from fastmdxplora.gui.trajectory_frames import exact_frame, exact_frame_info

    root, signature = study
    answer = exact_frame_info(root, 7, most_frames=3, source_signature=signature, on=on,
                              ligand=ligand, cutoff_angstrom=cutoff, to=to)
    assert answer["ok"] is False and reason in answer["reason"]
    assert answer["code"] == "config.option.not_permitted"
    assert exact_frame(root, 7, most_frames=3, on=on, ligand=ligand,
                       cutoff_angstrom=cutoff, to=to) == (None, answer)


def test_a_pocket_request_names_its_ligand_and_cutoff_in_both_routes(study):
    from urllib.parse import parse_qs, urlsplit

    from fastmdxplora.gui.trajectory_frames import exact_frame_info

    root, signature = study
    answer = exact_frame_info(root, 7, most_frames=3, on="pocket", ligand="LIG",
                              cutoff_angstrom="6.50", to="start")
    assert answer["ok"] is True
    exact = parse_qs(urlsplit(answer["exact_url"]).query)
    dcd = parse_qs(urlsplit(answer["dcd_url"]).query)
    assert exact == {"frame": ["7"], "signature": [signature], "on": ["pocket"],
                     "to": ["start"], "ligand": ["LIG"], "cutoff": ["6.5"]}
    assert dcd == {"exact_frame": ["7"], "signature": [signature], "on": ["pocket"],
                   "to": ["start"], "ligand": ["LIG"], "cutoff": ["6.5"]}


# -- preparing the frame ----------------------------------------------------


def test_a_frame_that_cannot_be_read_is_reported_by_each_route(monkeypatch, study):
    from fastmdxplora.gui import trajectory_frames

    root, signature = study

    def unreadable(*_args):
        raise OSError("[Errno 5] Input/output error: '/somewhere/private/production.dcd'")

    monkeypatch.setattr(trajectory_frames, "_source_frame", unreadable)
    # The reason names no path: the library's message is logged, not sent.
    expected = {"ok": False, "reason": "Frame 7 could not be prepared.",
                "code": "analysis.trajectory.unreadable"}
    assert trajectory_frames.exact_frame(root, 7, most_frames=3,
                                         source_signature=signature) == (None, expected)
    assert trajectory_frames.exact_frame_xtc(root, 7, most_frames=3,
                                             source_signature=signature) == (None, expected)
    assert trajectory_frames.exact_frames_dcd(root, 7, most_frames=3,
                                              source_signature=signature) == (None, expected)


def test_a_frame_that_cannot_be_written_as_xtc_is_reported(monkeypatch, study):
    from fastmdxplora.gui import trajectory_frames

    root, signature = study

    class Unwritable:
        def save_xtc(self, _path):
            raise OSError("disk full")

    monkeypatch.setattr(trajectory_frames, "exact_frame",
                        lambda *_a, **_kw: (Unwritable(), {"ok": True}))
    assert trajectory_frames.exact_frame_xtc(root, 7, source_signature=signature) == (
        None, {"ok": False, "reason": "Frame 7 could not be prepared.",
               "code": "analysis.trajectory.unreadable"})


def test_a_refused_request_prepares_nothing(study):
    from fastmdxplora.gui.trajectory_frames import exact_frame_xtc, exact_frames_dcd

    root, _signature = study
    stale = {"ok": False, "reason": "The trajectory has changed since this frame was named; "
             "reload its charts before opening it.", "code": "analysis.frame.changed"}
    assert exact_frame_xtc(root, 7, most_frames=3, source_signature="old") == (None, stale)
    assert exact_frames_dcd(root, 7, most_frames=3, source_signature="old") == (None, stale)


def test_the_dcd_route_fits_the_exact_frame_with_the_backbone_playback(tmp_path, study):
    from fastmdxplora.gui.trajectory_frames import exact_frames_dcd, superposed_frames

    root, signature = study
    body, details = exact_frames_dcd(root, 7, most_frames=3, source_signature=signature,
                                     on="backbone")
    assert details["ok"] is True and body
    joined = _read_dcd(root, body, tmp_path)
    fitted = superposed_frames(root, "backbone", smooth=1)
    playback = md.load_dcd(str(root / "simulation" / fitted["file"]),
                           top=str(root / "simulation" / "frames_topology.pdb"))
    assert joined.n_frames == playback.n_frames + 1
    assert np.allclose(joined.xyz[:-1], playback.xyz, atol=1e-4)
    # Fitted onto the first frame, the exact frame lies where frame 0 does.
    assert np.allclose(joined.xyz[-1], playback.xyz[0], atol=1e-3)


def test_the_dcd_route_reports_a_pocket_alignment_the_frames_cannot_make(study):
    from fastmdxplora.gui.trajectory_frames import exact_frames_dcd

    root, signature = study
    body, details = exact_frames_dcd(root, 7, most_frames=3, source_signature=signature,
                                     on="pocket", ligand="LIG")
    assert body is None and details["ok"] is False
    assert "LIG" in details["reason"]


def test_a_source_frame_needs_a_trajectory_and_shows_a_water_only_system_whole(tmp_path):
    from fastmdxplora.gui.trajectory_frames import _source_frame
    from fastmdxplora.refusals import MissingResultError

    with pytest.raises(MissingResultError, match="source trajectory is unavailable"):
        _source_frame(tmp_path, None, 0)
    with pytest.raises(MissingResultError, match="source trajectory is unavailable"):
        _source_frame(tmp_path, {"kind": "history"}, 0)
    topology = md.Topology()
    chain = topology.add_chain()
    for _ in range(2):
        residue = topology.add_residue("HOH", chain)
        topology.add_atom("O", md.element.oxygen, residue)
    water = md.Trajectory(np.arange(12, dtype=np.float32).reshape(2, 2, 3) / 10.0, topology)
    water[0].save_pdb(str(tmp_path / "water.pdb"))
    water.save_dcd(str(tmp_path / "water.dcd"))
    source = {"kind": "trajectory", "topology": tmp_path / "water.pdb",
              "trajectory": tmp_path / "water.dcd"}
    frame = _source_frame(tmp_path, source, 1)
    assert frame.n_atoms == 2 and frame.unitcell_lengths is None
    assert np.allclose(frame.xyz[0], water.xyz[1], atol=1e-4)
    with pytest.raises(MissingResultError, match="could not be read"):
        _source_frame(tmp_path, source, 2)


def test_an_exact_frame_is_fitted_to_the_deposited_structure(tmp_path, study):
    from fastmdxplora.gui.trajectory_frames import exact_frame_xtc

    root, signature = study
    _deposit(root)
    body, details = exact_frame_xtc(root, 7, most_frames=3, source_signature=signature,
                                    on="backbone", to="deposited")
    assert details["ok"] is True
    target = tmp_path / "deposited.xtc"
    target.write_bytes(body)
    exact = md.load_xtc(str(target), top=str(root / "simulation" / "frames_topology.pdb"))
    deposited = md.load_pdb(str(root / "setup" / "input.pdb"))
    backbone = deposited.topology.select("backbone")
    assert np.allclose(exact.xyz[0][backbone], deposited.xyz[0][backbone], atol=1e-3)


def test_fitting_without_a_deposited_structure_or_enough_atoms_is_refused(study):
    from fastmdxplora.gui.trajectory_frames import _fit_exact_frame, _source, _source_frame
    from fastmdxplora.refusals import StudyError

    root, _signature = study
    exact = _source_frame(root, _source(root), 7)
    with pytest.raises(StudyError, match="no deposited structure"):
        _fit_exact_frame(root, exact, on="backbone", ligand=None, cutoff_angstrom=5.0,
                         to="deposited")
    with pytest.raises(StudyError, match="fewer than three matching backbone atoms") as caught:
        _fit_exact_frame(root, exact, on="pocket", ligand="LIG", cutoff_angstrom=5.0,
                         to="first")
    assert caught.value.code == "analysis.system.inapplicable"


def test_a_fitted_frame_keeps_its_box_when_the_fit_drops_it(monkeypatch, study):
    from fastmdxplora.analysis import base
    from fastmdxplora.gui.trajectory_frames import _fit_exact_frame, _source, _source_frame

    root, _signature = study
    exact = _source_frame(root, _source(root), 7)
    exact.unitcell_lengths = np.array([[3.0, 3.0, 3.0]], dtype=np.float32)
    exact.unitcell_angles = np.array([[90.0, 90.0, 90.0]], dtype=np.float32)

    def fit_without_box(trajectory, **_kwargs):
        fitted = trajectory[:]
        fitted.unitcell_vectors = None
        return fitted

    monkeypatch.setattr(base, "superposed", fit_without_box)
    fitted = _fit_exact_frame(root, exact, on="backbone", ligand=None,
                              cutoff_angstrom=5.0, to="first")
    assert np.allclose(fitted.unitcell_lengths, [[3.0, 3.0, 3.0]])
    assert np.allclose(fitted.unitcell_angles, [[90.0, 90.0, 90.0]])


def test_a_refused_replace_is_retried_only_on_windows(monkeypatch, tmp_path):
    from fastmdxplora.gui import trajectory_frames

    target = tmp_path / "frames_index.json"
    replace = Path.replace
    refusals = {"left": 0}

    def sometimes_refused(self, other):
        if refusals["left"]:
            refusals["left"] -= 1
            raise PermissionError("sharing violation")
        return replace(self, other)

    monkeypatch.setattr(Path, "replace", sometimes_refused)
    waits = []
    monkeypatch.setattr(trajectory_frames.time, "sleep", waits.append)

    refusals["left"] = 1
    with pytest.raises(PermissionError):
        trajectory_frames._write_text(target, "posix")
    assert not target.exists() and list(tmp_path.iterdir()) == [] and waits == []

    monkeypatch.setattr(trajectory_frames, "os", types.SimpleNamespace(
        name="nt", getpid=trajectory_frames.os.getpid))
    refusals["left"] = 2
    trajectory_frames._write_text(target, "settled")
    assert target.read_text(encoding="utf-8") == "settled"
    assert waits == pytest.approx([0.01, 0.02])

    refusals["left"] = 6
    with pytest.raises(PermissionError):
        trajectory_frames._write_text(target, "never")
    assert target.read_text(encoding="utf-8") == "settled"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["frames_index.json"]


def test_a_hosted_page_is_not_told_where_a_frame_could_not_be_read(monkeypatch, tmp_path):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.parse import quote

    from fastmdxplora.gui import trajectory_frames
    from fastmdxplora.gui.hosting import Hosting
    from fastmdxplora.gui.server import DashboardConfig, DashboardRuntime, make_handler

    workspace = tmp_path.resolve() / "person"
    workspace.mkdir()
    root = _study(workspace / "study")
    signature = trajectory_frames.frames_info(root, most_frames=3)["source_signature"]

    def broken(_out, source, _frame):
        raise OSError(f"[Errno 5] Input/output error: '{source['trajectory']}' \u00e9t\u00e9 \u2192")

    monkeypatch.setattr(trajectory_frames, "_source_frame", broken)
    secret = "s" * 40
    hosting = Hosting(workspace=workspace, allowed_hosts=frozenset({"app.example.org"}),
                      secret=secret)
    runtime = DashboardRuntime(workspace_root=root, exploration_root=workspace,
                               active_root=root)
    runtime.hosting = hosting
    handler = make_handler(root, config=DashboardConfig(max_browser_frames=3), runtime=runtime,
                           allow_control=False, hosting=hosting)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for path in (f"/structure/exact-frame.xtc?frame=7&signature={quote(signature)}",
                     f"/structure/frames.dcd?exact_frame=7&signature={quote(signature)}"):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1],
                                                    timeout=60)
            connection.request("GET", path, headers={"Host": "app.example.org",
                                                     "X-FastMDX-Proxy-Secret": secret})
            response = connection.getresponse()
            body = response.read().decode("latin-1")
            assert response.status == 404, path
            assert response.reason == "Exact frame unavailable"
            assert str(workspace) not in body and "Errno" not in body
    finally:
        server.shutdown()


def test_frames_are_prepared_two_at_a_time_and_a_long_wait_is_refused(monkeypatch, study):
    import threading
    import time

    from fastmdxplora.gui import trajectory_frames

    root, signature = study
    real = trajectory_frames._source_frame
    lock = threading.Lock()
    at_once = {"now": 0, "most": 0}

    def slow(*args):
        with lock:
            at_once["now"] += 1
            at_once["most"] = max(at_once["most"], at_once["now"])
        try:
            time.sleep(0.2)
            return real(*args)
        finally:
            with lock:
                at_once["now"] -= 1

    monkeypatch.setattr(trajectory_frames, "_source_frame", slow)
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(trajectory_frames.exact_frame_xtc(
        root, 7, most_frames=3, source_signature=signature)[1]["ok"])) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert answers == [True] * 6 and at_once["most"] == 2

    monkeypatch.setattr(trajectory_frames, "_PREPARING_WAIT_S", 0.05)
    held = [trajectory_frames._PREPARING.acquire(), trajectory_frames._PREPARING.acquire()]
    try:
        body, details = trajectory_frames.exact_frame_xtc(root, 7, most_frames=3,
                                                          source_signature=signature)
    finally:
        for _ in held:
            trajectory_frames._PREPARING.release()
    assert body is None and details["code"] == "environment.service.busy"


def test_a_frame_opened_again_reads_its_topologies_once(monkeypatch, study):
    from fastmdxplora.gui import trajectory_frames
    from fastmdxplora.utils import native_output

    root, signature = study
    trajectory_frames._READ_CACHE.clear()
    read: list[str] = []
    topology_quietly = native_output.topology_quietly

    def counted_topology(path, *args, **kwargs):
        read.append(Path(path).name)
        return topology_quietly(path, *args, **kwargs)

    monkeypatch.setattr(native_output, "topology_quietly", counted_topology)
    for frame in (7, 8, 7):
        body, details = trajectory_frames.exact_frame_xtc(
            root, frame, most_frames=3, source_signature=signature, on="backbone")
        assert details["ok"] and body
    # The source's topology and the fit's, each once for the three frames;
    # the browser's frames are not loaded whole for the fit.
    assert sorted(read) == ["frames_topology.pdb", "trajectory_topology.pdb"]


def test_a_file_read_for_two_requests_at_once_is_read_once(tmp_path):
    import os
    import threading
    import time

    from fastmdxplora.gui import trajectory_frames

    trajectory_frames._READ_CACHE.clear()
    path = tmp_path / "topology.pdb"
    path.write_text("one", encoding="utf-8")
    made = []

    def make():
        made.append(1)
        time.sleep(0.2)
        return path.read_text(encoding="utf-8")

    threads = [threading.Thread(target=trajectory_frames._cached, args=("t", path, make))
               for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(made) == 1
    # Another file put in its place, of the same size and time: read again.
    stat = path.stat()
    other = tmp_path / "other.pdb"
    other.write_text("two", encoding="utf-8")
    os.utime(other, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    other.replace(path)
    assert trajectory_frames._cached("t", path, make) == "two" and len(made) == 2


def test_the_dcd_route_over_http_is_one_more_frame_or_a_404(tmp_path, study):
    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session

    root, signature = study
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                      config=DashboardConfig(max_browser_frames=3))
    try:
        base = session.url.rstrip("/")
        with urllib.request.urlopen(
                base + f"/structure/frames.dcd?exact_frame=7&signature={signature}",
                timeout=60) as response:
            body = response.read()
            assert response.status == 200
            assert response.headers["Content-Type"] == "application/octet-stream"
            assert response.headers["Cache-Control"] == "no-store"
            assert int(response.headers["Content-Length"]) == len(body)
        joined = _read_dcd(root, body, tmp_path)
        assert joined.n_frames == 4
        assert joined.xyz[-1, 1, 0] == pytest.approx(0.7, abs=1e-4)
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(base + "/structure/frames.dcd?exact_frame=7&signature=old")
        assert caught.value.code == 404
    finally:
        session.server.shutdown()


def test_a_source_frame_scene_refuses_smoothed_coordinates(study):
    from fastmdxplora.refusals import StudyError
    from fastmdxplora.scenes import build_scene

    root, _signature = study
    (root / "setup").mkdir(exist_ok=True)
    (root / "setup" / "topology.pdb").write_bytes(
        (root / "simulation" / "trajectory_topology.pdb").read_bytes())
    try:
        answer = build_scene(root, {"frame": 7, "frame_source": True, "smoothed_over": 3})
    except StudyError as exc:
        assert "needs unsmoothed coordinates" in str(exc)
        assert exc.code == "config.option.not_permitted"
    else:
        assert answer["ok"] is False
        assert "needs unsmoothed coordinates" in json.dumps(answer)


def _with_setup(root: Path) -> None:
    (root / "setup").mkdir(exist_ok=True)
    (root / "setup" / "topology.pdb").write_bytes(
        (root / "simulation" / "trajectory_topology.pdb").read_bytes())


def test_a_source_frame_named_in_another_trajectory_is_not_shown(study):
    from fastmdxplora.gui.saved_views import _checked
    from fastmdxplora.refusals import StudyError
    from fastmdxplora.scenes import build_scene

    root, signature = study
    _with_setup(root)
    camera = {"position": [0, 0, 10], "target": [0, 0, 0], "up": [0, 1, 0]}
    view = {"camera": camera, "frame": 7, "frame_source": True, "frame_signature": signature}
    # A saved view keeps the trajectory its frame was named in.
    assert _checked(view)["frame_signature"] == signature
    # The same trajectory, sent to the browser as more frames: still it.
    same = signature.replace(":most=3", ":most=2000")
    assert build_scene(root, {**view, "frame_signature": same}, most_frames=3)["ok"]
    with pytest.raises(StudyError) as caught:
        build_scene(root, {**view, "frame_signature": "simulation/production.dcd:1:2:most=3"},
                    most_frames=3)
    assert caught.value.code == "analysis.frame.changed"
    assert "has changed since this frame was named" in str(caught.value)


def test_a_scene_reads_the_frames_as_the_viewer_was_sent_them(study):
    from fastmdxplora.gui.server import DashboardConfig, start_dashboard_session

    root, signature = study
    _with_setup(root)
    index = root / "simulation" / "frames_index.json"
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                      config=DashboardConfig(max_browser_frames=3))
    try:
        request = urllib.request.Request(
            session.url.rstrip("/") + "/api/scenes", method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"name": "exact", "view": {
                "frame": 7, "frame_source": True, "frame_signature": signature}}).encode())
        with urllib.request.urlopen(request, timeout=120) as response:
            assert json.loads(response.read())["ok"] is True
    finally:
        session.server.shutdown()
    # The browser's three frames were not written again as two thousand.
    assert json.loads(index.read_text())["frame_indices"] == [0, 5, 10]
    # Nor by a scene written with no server's setting: the frames sent are read.
    from fastmdxplora.scenes import write_scene

    assert write_scene(root, "again", {"frame": 7, "frame_source": True})["ok"]
    assert json.loads(index.read_text())["frame_indices"] == [0, 5, 10]


# -- profile and figure rows ------------------------------------------------


def _analysis(root: Path, name: str, text: str, *, options=None, selection=None) -> None:
    from tests.test_an_analysis_is_drawn_from_its_numbers import _analysis as write

    write(root, name, text, options=options, selection=selection)


def test_atom_rows_name_an_atom_only_where_its_serial_is_unique(study):
    from fastmdxplora.gui.series import series_payload

    root, _signature = study
    _analysis(root, "rmsf", "2 0.20\n99 0.30\n", options={"per_residue": False})
    data = series_payload(root, "rmsf")
    assert data["kind"] == "atom" and data["labels"] == ["Atom 2", "Atom 99"]
    assert data["entities"] == [
        {"kind": "atom", "chain": "A", "resi": 1, "icode": "", "resn": "ALA", "atom": "CA"},
        None]


def test_a_chain_word_before_a_residue_number_chooses_the_chain(study):
    from fastmdxplora.gui.series import series_payload

    root, _signature = study
    _analysis(root, "rmsf", "A 1 0.20\nB 1 0.30\n", options={"per_residue": True})
    entities = series_payload(root, "rmsf")["entities"]
    assert entities == [{"kind": "residue", "chain": "A", "resi": 1, "icode": "",
                         "resn": "ALA"}, None]


def test_profile_rows_stay_value_only_where_the_topology_cannot_name_them(study):
    from fastmdxplora.gui.series import series_payload

    root, _signature = study
    _analysis(root, "rmsf", "1 0.20\n", options={"per_residue": True},
              selection="this is not a selection")
    assert series_payload(root, "rmsf")["entities"] == [None]
    (root / "simulation" / "trajectory_topology.pdb").rename(root / "elsewhere.pdb")
    _analysis(root, "rmsf", "1 0.20\n", options={"per_residue": True})
    assert series_payload(root, "rmsf")["entities"] == [None]


def test_a_comma_table_whose_rows_do_not_match_the_profile_names_none(monkeypatch, study):
    from fastmdxplora.gui import series

    root, _signature = study
    _analysis(root, "rmsf", "chain,residue,rmsf_nm\nA,1,0.20\nA,1,0.30\n",
              options={"per_residue": True})
    monkeypatch.setattr(series, "_profile_table_rows",
                        lambda _path: [{"chain": "A", "residue": "1", "rmsf_nm": "0.2"}])
    assert series.series_payload(root, "rmsf")["entities"] == [None, None]


def test_only_a_readable_identity_table_is_read_as_one(tmp_path):
    from fastmdxplora.gui.series import _profile_table_rows

    assert _profile_table_rows(tmp_path / "missing.csv") == []
    unnamed = tmp_path / "unnamed.csv"
    unnamed.write_text("chain,number,value\nA,1,0.2\n", encoding="utf-8")
    assert _profile_table_rows(unnamed) == []
    oversized = tmp_path / "oversized.csv"
    oversized.write_text("chain,residue,s2\nA,1," + "9" * 200_000 + "\n", encoding="utf-8")
    assert _profile_table_rows(oversized) == []
    named = tmp_path / "named.csv"
    named.write_text("# S2\nChain, Residue ,S2\nA, 1 ,0.8\n", encoding="utf-8")
    assert _profile_table_rows(named) == [{"chain": "A", "residue": "1", "s2": "0.8"}]


def test_order_parameter_rows_keep_only_finite_whole_residues(tmp_path):
    from fastmdxplora.gui.series import _order_parameter_rows

    path = tmp_path / "order_parameters.dat"
    path.write_text("# residue S2 measured\n1 0.80 0.75\nresidue two 0.5\n2.5 0.70\n"
                    "3 nan\ninf 0.9\n4 0.60 bad\n5\n", encoding="utf-8")
    rows, reference = _order_parameter_rows(path)
    assert rows == [([], [1.0, 0.8]), ([], [4.0, 0.6])]
    assert reference == [0.75, None]
    assert _order_parameter_rows(tmp_path / "missing.dat") == ([], [])


def test_figure_residue_labels_are_named_only_from_a_readable_topology(monkeypatch, study):
    from fastmdxplora.gui.figure_data import _residue_entities
    from fastmdxplora.utils import native_output

    root, _signature = study
    assert _residue_entities(root, []) == []
    assert _residue_entities(root, ["ALA1", "GLY9"]) == [
        {"kind": "residue", "chain": "A", "resi": 1, "icode": "", "resn": "ALA"}, None]

    def unreadable(_path):
        raise OSError("synthetic unreadable topology")

    monkeypatch.setattr(native_output, "topology_quietly", unreadable)
    assert _residue_entities(root, ["ALA1", "ALA2"]) == [None, None]
