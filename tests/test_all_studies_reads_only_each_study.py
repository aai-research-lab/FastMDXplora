"""All studies reads each study only, as the study's own pages do.

A study's card (`workspace.card_of`, behind `/api/studies`) read its
records through any link: a downloaded study holding a link to a file
outside it had that file read and shown as its means, tags, state and
molecule, and `/api/study-thumbnail` sent a figure it linked to. Each card
is now read held to its study (`server._confined`, `record_access`): a
record linked out of it, or a file of it that is not one of its results
(a hidden one), is not opened, and the card names it and says what it
leaves unknown, never a value taken from its absence. Checked by an audit
of the opens, on loopback and on a public bind.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests.test_the_study_pages_read_only_the_study import _OPENED, _WATCHED, _link, _watch

#: A Cα trace of ten residues, 3.8 Å apart.
PDB = "".join(
    f"ATOM  {i:5d}  CA  ALA A{i:4d}    {3.8 * i:8.3f}{1.5 * (i % 2):8.3f}{0.5 * (i % 3):8.3f}"
    "  1.00  0.00           C\n" for i in range(1, 11)) + "END\n"
PIXEL = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5f0000000049454e44ae426082")
#: What a study's records say once they are linked out: none of it may
#: reach a card.
LEAKED = ("0.4321", "2.2222", "leaked tag", "leaked note", "Leaked windows")
OUT = dict(rmsd=0.4321, rg=2.2222, tag="leaked tag", note="leaked note", pmf="Leaked windows")


def _write(root: Path, relative: str, value: object) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return path


def _mean(name: str, value: float) -> dict:
    return {"analysis": name, "findings": {"mean": {
        "mean": value, "standard_error": 0.002, "unit": "nm", "effective_samples": 40}}}


def _a_study(root: Path, *, rmsd=0.1234, rg=1.0567, tag="wild type", note="kept with care",
             pmf="Too few windows") -> Path:
    """A study run and analysed, with every record a card reads."""
    _write(root, "manifest.json", {"phases": [
        {"name": "setup", "status": "ok", "started_at": "2026-09-01T10:00:00+00:00"},
        {"name": "simulation", "status": "ok"}, {"name": "analysis", "status": "ok"}]})
    _write(root, "resolved_config.yml", yaml.safe_dump({
        "systems": [{"system": "1UBQ"}], "simulation": {"duration_ns": 1.0}}))
    _write(root, "analysis/analysis_manifest.json", {"plan": ["rmsd", "rg"]})
    _write(root, "analysis/rmsd/options.json", _mean("rmsd", rmsd))
    _write(root, "analysis/rg/options.json", _mean("rg", rg))
    _write(root, "analysis/rmsd/rmsd.png", PIXEL)
    _write(root, "analysis/rmsd/rmsd.dat", "".join(f"{0.1 + 0.001 * i:.4f}\n" for i in range(20)))
    _write(root, "simulation/live_status.json", {
        "status": "completed", "stage": "production", "timestep_fs": 2.0,
        "nvt_steps_planned": 0, "npt_steps_planned": 0,
        "production_steps_planned": 500000, "current_step": 500000,
        "total_planned_steps": 500000})
    _write(root, "setup/prepared.pdb", PDB)
    _write(root, "study_tags.json", {"tags": [tag], "note": note,
                                     "updated": "2026-09-02T10:00:00+00:00"})
    _write(root, "pmf.json", {"refused": f"{pmf}. More are needed."})
    return root


def _linked_out(root: Path, outside: Path) -> None:
    """Each regular file of the study moved outside it, a link left in its
    place: what the link leads to is the study's own record."""
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            target = outside / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)
            _link(path, target)


@pytest.fixture
def workspace(tmp_path) -> tuple[Path, Path]:
    space, outside = tmp_path / "ws", tmp_path / "outside"
    _a_study(space / "own")
    _linked_out(_a_study(space / "linked", **OUT), outside / "linked")
    # A record linked to a hidden file of the study: not one of its results.
    hidden = _a_study(space / "hidden", rg=3.3333)
    (hidden / "analysis" / "rg" / "options.json").rename(hidden / ".rg.json")
    _link(hidden / "analysis" / "rg" / "options.json", Path("../../.rg.json"))
    # Replicas whose comparison is linked out, and one of whose runs is.
    reps = space / "reps"
    planned = [{"run_id": f"seed{n}", "system": "1UAO", "options": {},
                "sweep_values": {"simulation.random_seed": n}} for n in (1, 2)]
    _write(reps, "batch_manifest.json", {
        "n_runs": 2, "planned": planned, "sweep": {"simulation.random_seed": [1, 2]},
        "systems": [{"id": "s", "system": "1UAO"}],
        "runs": [{"run_id": p["run_id"], "status": "ok"} for p in planned]})
    _write(reps, "resolved_config.yml", yaml.safe_dump({"systems": [{"system": "1UAO"}]}))
    _a_study(reps / "runs" / "seed1")
    _a_study(outside / "seed2", **OUT)
    _link(reps / "runs" / "seed2", outside / "seed2")
    _write(outside, "members.json", {"replicas": True, "analyses": {"rmsd": {
        "mean_of_means": 0.4321, "spread_of_means": 0.01,
        "members": [{"member": "seed1", "mean": 0.12}, {"member": "seed2", "mean": 0.13}]}}})
    _link(reps / "members.json", outside / "members.json")
    return space, outside


def _opened_outside(outside: Path, read) -> tuple[object, list[str]]:
    """``read()``, and every path under ``outside`` opened or listed while
    it ran: as the audit hook sees them, and the reads `record_access`
    makes relative to a folder it holds open (``dir_fd``), by where they
    lead."""
    import os

    real = os.open
    target = str(outside.resolve()) + "/"
    seen: list[str] = []

    def opened_at(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and not os.path.isabs(os.fsdecode(path)):
            try:
                where = os.path.realpath(os.path.join(
                    os.readlink(f"/proc/self/fd/{dir_fd}"), os.fsdecode(path)))
            except OSError:
                where = ""
            if where.startswith(target):
                seen.append(where)
        return real(path, flags, mode, dir_fd=dir_fd)

    _watch(outside)
    os.open = opened_at
    os.supports_dir_fd.add(opened_at)
    try:
        answer = read()
        return answer, sorted(set(_OPENED) | set(seen))
    finally:
        os.open = real
        os.supports_dir_fd.discard(opened_at)
        _WATCHED.clear()


def test_a_card_reads_nothing_through_a_link_and_says_what_it_did_not_read(workspace):
    from fastmdxplora.gui.workspace import studies_in

    space, outside = workspace
    found, opened = _opened_outside(outside, lambda: studies_in(space))
    cards = {card["name"]: card for card in found["studies"]}
    runs, opened_runs = _opened_outside(outside, lambda: studies_in(space / "reps" / "runs"))
    assert opened == [] and opened_runs == []
    # The study read whole: all of it, nothing said not read.
    own = cards["own"]
    assert own["state"] == "completed" and own["kind"] == "one run"
    assert [m["mean"] for m in own["means"]] == [0.1234, 1.0567]
    assert own["tags"] == ["wild type"] and own["simulated_ns"] == 1.0
    assert own["molecule"]["residues"] == 10 and own["free_energy"]
    assert "unavailable" not in own and "unread" not in own and "not_read" not in own
    # Every record linked out: none of it on the card, each named.
    linked = cards["linked"]
    text = json.dumps(linked)
    for value in LEAKED:
        assert value not in text, value
    assert linked["state"] == "not known" and linked["kind"] == ""
    assert linked["means"] == [] and linked["tags"] == [] and linked["molecule"] is None
    assert "simulated_ns" not in linked and linked["last_active"] == ""
    assert set(linked["unread"]) == {"state", "length", "means", "molecule"}
    assert {"manifest.json", "analysis/rmsd/options.json", "analysis/rg/options.json",
            "study_tags.json", "simulation/live_status.json",
            "setup/prepared.pdb"} <= set(linked["unavailable"])
    assert "analysis/rg/options.json" in linked["not_read"]
    assert "a link to a file outside the study" in linked["not_read"]
    # A record linked to a hidden file of the study: not read, the rest is.
    hidden = cards["hidden"]
    assert [m["analysis"] for m in hidden["means"]] == ["rmsd"]
    assert hidden["unread"] == ["means"] and hidden["unavailable"] == ["analysis/rg/options.json"]
    assert "not one of its results" in hidden["not_read"]
    assert hidden["state"] == "completed" and "3.3333" not in json.dumps(hidden)
    # Replicas: their comparison linked out is not their mean, and a run
    # linked out is not counted in how long they ran.
    reps = cards["reps"]
    assert reps["means"] == [] and "means" in reps["unread"]
    assert "simulated_ns" not in reps and "length" in reps["unread"]
    assert reps["state"] == "not known"
    assert "0.4321" not in json.dumps(reps)
    named = {card["name"]: card for card in runs["studies"]}
    assert named["seed1"]["means"] and "unavailable" not in named["seed1"]
    assert named["seed2"]["state"] == "not known" and named["seed2"]["means"] == []
    assert named["seed2"]["not_read"] == ("runs/seed2 was not read: "
                                          "it is a link to a folder outside the study.")


def test_a_start_outside_the_study_is_not_drawn(tmp_path):
    from fastmdxplora.gui.workspace import card_of

    start = _write(tmp_path / "structures", "start.pdb", PDB)
    study = tmp_path / "ws" / "waiting"
    _write(study, "resolved_config.yml", yaml.safe_dump({"systems": [{"system": str(start)}]}))
    _write(study, "manifest.json", {"phases": []})
    card, opened = _opened_outside(tmp_path / "structures", lambda: card_of(study))
    assert opened == []
    assert card["molecule"] is None and card["molecule_outside"] == "start.pdb"
    assert card["state"] == "not started"
    # Where it is in the study, it is drawn.
    _write(study, "start.pdb", PDB)
    (study / "resolved_config.yml").write_text(
        yaml.safe_dump({"systems": [{"system": "start.pdb"}]}), encoding="utf-8")
    assert card_of(study)["molecule"]["from"] == "start"


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_the_routes_read_nothing_through_a_link(workspace, public):
    import urllib.error
    import urllib.request
    from urllib.parse import urlencode

    from fastmdxplora.gui.exploration import DashboardRuntime
    from tests.test_a_public_dashboard_answers_only_what_it_lists import _serving

    space, outside = workspace
    # A study whose only structure is linked out: no figure, no backbone.
    drawn = _a_study(space / "drawn")
    (drawn / "analysis" / "rmsd" / "rmsd.png").unlink()
    (outside / "drawn").mkdir()
    (drawn / "setup" / "prepared.pdb").rename(outside / "drawn" / "prepared.pdb")
    _link(drawn / "setup" / "prepared.pdb", outside / "drawn" / "prepared.pdb")
    runtime = DashboardRuntime(workspace_root=space / "own", exploration_root=space,
                               active_root=space / "own")
    asked = {"studies": f"/api/studies?{urlencode({'path': str(space)})}",
             "runs": f"/api/studies?{urlencode({'path': str(space / 'reps' / 'runs')})}",
             "figure": f"/api/study-thumbnail?{urlencode({'path': str(space / 'linked')})}",
             "series": f"/api/study-thumbnail?{urlencode({'path': str(space / 'linked'), 'series': 1})}",
             "backbone": f"/api/study-thumbnail?{urlencode({'path': str(drawn)})}",
             "own": f"/api/study-thumbnail?{urlencode({'path': str(space / 'own')})}"}
    said: dict[str, tuple[int, bytes]] = {}

    def ask_all() -> None:
        with _serving(runtime, allow_control=not public) as url:
            for key, route in asked.items():
                try:
                    with urllib.request.urlopen(url + route, timeout=60) as response:
                        said[key] = (response.status, response.read())
                except urllib.error.HTTPError as error:
                    said[key] = (error.code, error.read())

    _, opened = _opened_outside(outside, ask_all)
    assert opened == []
    for key, (_, body) in said.items():
        if key == "own":
            continue
        for value in LEAKED:
            assert value.encode() not in body, (key, value)
        assert PIXEL not in body and b"<svg" not in body, key
    if public:
        # Beyond loopback none of these is answered.
        assert {status for status, _ in said.values()} == {403}
        return
    assert said["own"] == (200, PIXEL)
    cards = {card["name"]: card for card in json.loads(said["studies"][1])["studies"]}
    assert cards["linked"]["state"] == "not known" and cards["own"]["means"]
    assert said["figure"][0] == 404
    assert "analysis/rmsd/rmsd.png" in json.loads(said["figure"][1])["unavailable"]
    assert said["backbone"][0] == 404
    assert json.loads(said["backbone"][1])["unavailable"] == ["setup/prepared.pdb"]
    series = json.loads(said["series"][1])
    assert series["ok"] is False and "unavailable" in series


def test_the_page_says_what_a_card_did_not_read(workspace):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    space, outside = workspace
    _runs_linked_out(space, outside)
    session = start_dashboard_session(output=str(space / "own"), host="127.0.0.1", port=0)
    look = """(name) => {
        const row = document.querySelector(`#studies-table tr.studies-row[data-path$="/${name}"]`);
        const frame = row.querySelector('.studies-mol-frame');
        return {state: row.querySelector('.studies-state-line').textContent.trim(),
                length: row.querySelector('.studies-length').textContent.trim(),
                means: row.querySelector('.studies-means-cell').textContent.trim(),
                said: (row.querySelector('.studies-not-read') || {}).textContent || '',
                why: (row.querySelector('.studies-not-read') || {}).title || '',
                molecule: frame ? frame.title : ''};
    }"""
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.evaluate(f"() => window.FastMDXStudies.load({str(space)!r})")
            page.wait_for_selector('#studies-table tr.studies-row[data-path$="/linked"]')
            linked = page.evaluate(look, "linked")
            # Every record not read, and why, opened from the row.
            page.click('#studies-table tr.studies-row[data-path$="/linked"] .studies-not-read')
            linked["why"] = page.inner_text(".studies-means-pop")
            page.keyboard.press("Escape")
            hidden = page.evaluate(look, "hidden")
            own = page.evaluate(look, "own")
            # Its runs unfolded: not read where its `runs` leads out of it.
            page.click('#studies-table tr.studies-row[data-path$="/reps_out"] .studies-fold')
            page.wait_for_selector(".studies-child-wait:has-text('runs was not read')")
            unfolded = page.inner_text(".studies-child-wait")
            # A run linked out of its study: named, and nothing offered on
            # it that would open, tag or choose it.
            page.click('#studies-table tr.studies-row[data-path$="/reps"] .studies-fold')
            page.wait_for_selector('#studies-table tr.studies-child[data-path$="/seed2"]')
            offered = page.evaluate("""() => Object.fromEntries(['seed1', 'seed2'].map((run) => {
                const row = document.querySelector(`#studies-table tr.studies-child[data-path$="/${run}"]`);
                return [run, ['.study-tag-edit', '.study-open', '.study-files',
                              'input[type=checkbox]', 'button.studies-name-text']
                  .filter((s) => row.querySelector(s))];
            }))""")
            tag_offered = page.evaluate("""() => ['linked', 'own'].map((name) => !!document.querySelector(
                `#studies-table tr.studies-row[data-path$="/${name}"] .study-tag-edit`))""")
            page.click('[data-studies-view="cards"]')
            card = page.inner_text('.study-card[data-path$="/linked"]')
            tag_offered += [bool(page.query_selector('.study-card[data-path$="/linked"] .study-tag-edit'))]
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert linked["state"] == "not known"
    assert linked["length"] == "not read" and linked["means"] == "not read"
    assert linked["said"].startswith("Not read: ") and "manifest.json" in linked["said"]
    assert "a link to a file outside the study" in linked["why"]
    assert linked["molecule"].startswith("Not rendered: its structure was not read")
    assert "Rg" not in hidden["means"] and "not read" in hidden["means"]
    assert own["said"] == "" and "not read" not in own["means"]
    assert "Not read: " in card
    # Its tags not read: Tag not offered on its row or card, as a change
    # to them is refused.
    assert tag_offered == [False, True, False]
    assert offered == {"seed1": [".study-tag-edit", ".study-open", ".study-files",
                                 "input[type=checkbox]", "button.studies-name-text"],
                       "seed2": []}
    assert unfolded == "runs was not read: it is a link to a folder outside the study."


def _runs_linked_out(space: Path, outside: Path) -> Path:
    """A study of two replicas whose ``runs`` is a link out of it, and a
    folder of studies linked into the workspace from outside it."""
    study = space / "reps_out"
    planned = [{"run_id": f"seed{n}", "system": "1UAO", "options": {}} for n in (1, 2)]
    _write(study, "batch_manifest.json", {
        "n_runs": 2, "planned": planned, "sweep": {"simulation.random_seed": [1, 2]},
        "runs": [{"run_id": p["run_id"], "status": "ok"} for p in planned]})
    for n in (1, 2):
        _a_study(outside / "runs_dir" / f"seed{n}", **OUT)
    _link(study / "runs", outside / "runs_dir")
    _a_study(outside / "folder_of_studies" / "elsewhere", **OUT)
    _link(space / "linked_folder", outside / "folder_of_studies")
    return study


def test_runs_linked_out_and_a_folder_linked_in_are_not_read(workspace):
    """A study whose ``runs`` is a link out of it: unfolding it read every
    run where the link led and showed their means. A run linked out of its
    study had its figure served. A folder linked into the workspace from
    outside it was walked, and its studies listed."""
    from fastmdxplora.gui.workspace import card_of, studies_in, study_thumbnail

    space, outside = workspace
    study = _runs_linked_out(space, outside)
    runs, opened_runs = _opened_outside(outside, lambda: studies_in(study / "runs"))
    run, opened_run = _opened_outside(outside, lambda: card_of(study / "runs" / "seed1"))
    picture, opened_picture = _opened_outside(
        outside, lambda: study_thumbnail(space / "reps" / "runs" / "seed2"))
    listed, opened_listed = _opened_outside(outside, lambda: studies_in(space))
    assert opened_runs == [] and opened_run == [] and opened_picture == []
    assert opened_listed == []
    assert runs["studies"] == [] and runs["root"] == str(study / "runs")
    assert runs["not_read"] == "runs was not read: it is a link to a folder outside the study."
    assert run["state"] == "not known" and run["unavailable"] == ["runs"]
    assert picture["ok"] is False and picture["unavailable"] == ["runs/seed2"]
    names = {card["name"] for card in listed["studies"]}
    assert "elsewhere" not in names and "reps_out" in names
    for value in LEAKED:
        assert value not in json.dumps([runs, run, picture]), value


def _post_tags(url: str, body: dict) -> dict:
    import urllib.request

    request = urllib.request.Request(url + "/api/study-tags", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def test_tags_linked_out_and_a_run_linked_out_are_not_changed(workspace):
    """Archive, the bar's Tag and a card's Save change the tags as kept:
    the change read a ``study_tags.json`` linked out of the study, copied
    what it held into the study and showed it; for a run linked out of its
    study, it wrote the outside folder."""
    import os

    from fastmdxplora.gui.server import start_dashboard_session

    space, outside = workspace
    tagged = _a_study(space / "tagged")
    (tagged / "study_tags.json").unlink()
    _write(outside, "tags.json", {"tags": ["leaked tag"], "note": "leaked note",
                                  "archived": "2026-01-01T00:00:00+00:00"})
    _link(tagged / "study_tags.json", outside / "tags.json")
    before = sorted(str(p) for p in outside.rglob("*"))
    held = (outside / "seed2" / "study_tags.json").read_bytes()
    session = start_dashboard_session(output=str(space / "own"), host="127.0.0.1", port=0)
    said: dict[str, dict] = {}

    def post_all() -> None:
        said["archive"] = _post_tags(session.url, {"path": str(tagged), "archived": True})
        said["tag"] = _post_tags(session.url, {"path": str(tagged), "add": ["mine"],
                                               "remove": []})
        said["save"] = _post_tags(session.url, {"path": str(tagged), "tags": ["mine"],
                                                "note": ""})
        said["run"] = _post_tags(session.url, {"path": str(space / "reps" / "runs" / "seed2"),
                                               "add": ["mine"], "remove": [], "note": ""})
        said["own"] = _post_tags(session.url, {"path": str(space / "own"), "add": ["mine"]})

    try:
        _, opened = _opened_outside(outside, post_all)
    finally:
        session.server.shutdown()
    assert opened == []
    for key in ("archive", "tag", "save"):
        assert said[key]["ok"] is False, key
        assert said[key]["reason"] == ("Its tags were not changed: study_tags.json was not "
                                       "read: it is a link to a file outside the study.")
    assert said["run"] == {"ok": False, "reason": (
        "runs/seed2 is a link to a folder outside the study: it is not read, and its tags "
        "are not changed.")}
    assert said["own"]["ok"] is True and said["own"]["tags"] == ["wild type", "mine"]
    for value in LEAKED:
        assert value not in json.dumps(said), value
    # Nothing written outside, and the link left as it was.
    assert os.path.islink(tagged / "study_tags.json")
    assert sorted(str(p) for p in outside.rglob("*")) == before
    assert (outside / "seed2" / "study_tags.json").read_bytes() == held


def test_a_run_asked_by_its_own_path_through_a_link_is_not_read(workspace):
    """``/api/studies?path=<study>/runs/<run>``, the run linked out of its
    study (or under ``runs`` linked out): the path was resolved first, and
    the outside run read as a study of its own, its folder the answer's
    ``root``."""
    from fastmdxplora.gui.workspace import studies_in

    space, outside = workspace
    study = _runs_linked_out(space, outside)
    asked = {"run": space / "reps" / "runs" / "seed2",
             "under_runs": study / "runs" / "seed1",
             "inside_it": space / "reps" / "runs" / "seed2" / "analysis"}
    said = {}
    for key, path in asked.items():
        said[key], opened = _opened_outside(outside, lambda path=path: studies_in(path))
        assert opened == [], key
        assert str(outside.resolve()) not in json.dumps(said[key]), key
        assert said[key]["root"] == str(path), key
    (run,) = said["run"]["studies"]
    assert run["outside"] is True and run["state"] == "not known"
    assert run["unavailable"] == ["runs/seed2"] and run["path"] == str(asked["run"])
    (under,) = said["under_runs"]["studies"]
    assert under["outside"] is True and under["unavailable"] == ["runs"]
    assert said["inside_it"]["studies"] == []
    assert said["inside_it"]["not_read"] == ("runs/seed2 was not read: it is a link to a "
                                             "folder outside the study.")
    for value in LEAKED:
        assert value not in json.dumps(said), value


def test_a_study_linked_into_the_workspace_is_listed_and_marked(tmp_path):
    """A study kept on another disk and linked into the workspace (as on a
    cluster's scratch) was listed before cards were held to their studies,
    then dropped from All studies without a word. It is listed, marked a
    link, and read as its own pages read it, held to itself: a record it
    links further out is not read. Hosted, where every route refuses a
    path outside the workspace, it is named and not read. A folder linked
    in that is not a study is still not walked."""
    from fastmdxplora.gui.hosting import Hosting
    from fastmdxplora.gui.workspace import studies_in
    from tests.test_a_hosted_gui_answers_only_its_proxy import NAME, SECRET, _ask, _json
    from tests.test_a_hosted_gui_answers_only_its_proxy import _serving as _hosted

    space, elsewhere, further = tmp_path / "ws", tmp_path / "scratch", tmp_path / "further"
    _a_study(space / "own")
    kept = _a_study(elsewhere / "kept_on_scratch", rg=2.0)
    (kept / "analysis" / "rg" / "options.json").unlink()
    _write(further, "rg.json", _mean("rg", 2.2222))
    _link(kept / "analysis" / "rg" / "options.json", further / "rg.json")
    _link(space / "scratch_study", kept)
    _a_study(elsewhere / "folder" / "inner", **OUT)
    _link(space / "linked_folder", elsewhere / "folder")

    listed, opened = _opened_outside(further, lambda: studies_in(space))
    assert opened == []
    cards = {card["name"]: card for card in listed["studies"]}
    assert set(cards) == {"own", "scratch_study"}
    linked = cards["scratch_study"]
    assert linked["linked_in"] == str(kept.resolve()) and linked["path"] == str(space / "scratch_study")
    assert linked["state"] == "completed"
    assert [m["analysis"] for m in linked["means"]] == ["rmsd"]
    assert "analysis/rg/options.json" in linked["unavailable"]
    assert "linked_in" not in cards["own"]

    hosting = Hosting(workspace=space.resolve(), allowed_hosts=frozenset({NAME}), secret=SECRET)

    def ask() -> tuple[int, bytes]:
        with _hosted(hosting) as (address, _):
            return _ask(address, "/api/studies?path=" + str(space.resolve()),
                        headers={"Host": NAME})

    (status, body), opened = _opened_outside(elsewhere, ask)
    assert status == 200 and opened == []
    cards = {card["name"]: card for card in _json(body)["studies"]}
    assert set(cards) == {"own", "scratch_study"}
    assert cards["scratch_study"]["outside"] is True and cards["scratch_study"]["linked_in"] == ""
    assert cards["scratch_study"]["not_read"] == (
        "scratch_study was not read: it is a link to a folder outside the workspace, which is "
        "not read here.")
    assert str(elsewhere) not in body.decode() and "2.2222" not in body.decode()


def test_a_run_linked_out_is_named_not_an_unavailable_record(tmp_path):
    """Replicas one of whose runs is linked out of the study, its Manifest
    recording where it ran (a folder that is there, outside the study):
    after ``runs/<run>`` was refused the recorded path was tried, refused
    too, and the row's "Not read" began "an unavailable record", which
    names nothing. The run is named; one not placed comes last."""
    from fastmdxplora.gui.workspace import _named_first, card_of

    space, outside = tmp_path / "ws", tmp_path / "outside"
    reps = space / "reps"
    planned = [{"run_id": f"seed{n}", "system": "1UAO", "options": {},
                "sweep_values": {"simulation.random_seed": n}} for n in (1, 2, 3)]
    _write(reps, "batch_manifest.json", {
        "n_runs": 3, "planned": planned, "sweep": {"simulation.random_seed": [1, 2, 3]},
        "systems": [{"id": "s", "system": "1UAO"}],
        "runs": [{"run_id": p["run_id"], "status": "ok",
                  "output_dir": str(outside / "old" / "runs" / p["run_id"])} for p in planned]})
    _write(reps, "resolved_config.yml", yaml.safe_dump({"systems": [{"system": "1UAO"}]}))
    for n in (1, 3):
        _a_study(reps / "runs" / f"seed{n}")
    _a_study(outside / "old" / "runs" / "seed2", **OUT)
    _a_study(outside / "seed2", **OUT)
    _link(reps / "runs" / "seed2", outside / "seed2")
    card, opened = _opened_outside(outside, lambda: card_of(reps))
    assert opened == []
    assert "an unavailable record" not in card["unavailable"]
    # A run's folder linked out is named as a folder, with its "/".
    assert card["unavailable"][0] == "runs/seed2/"
    assert card["not_read"].startswith("runs/seed2/") and "unavailable" not in card["not_read"]
    assert _named_first(["an unavailable record", "runs/seed2", "a non-regular record",
                         "members.json"]) == ["members.json", "runs/seed2",
                                              "a non-regular record", "an unavailable record"]


def test_a_held_read_finds_where_an_address_leads_once(tmp_path, monkeypatch):
    """The first list of a hundred studies took 1.5 times the base's: each
    record a card asked of was resolved again at every ask (four or five
    times for one ``is_file``: its check, its admission, its folder's). A
    held read finds where an address leads once; a link retargeted out of
    the study after that is still not read through, its parts opened one
    at a time without following a link."""
    import os
    import posixpath

    from fastmdxplora import record_access
    from fastmdxplora.gui import workspace

    study = _a_study(tmp_path / "study")
    real = posixpath.realpath
    asked: list[str] = []

    def counted(path, *args, **kwargs):
        asked.append(os.fspath(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(posixpath, "realpath", counted)
    monkeypatch.setattr(os.path, "realpath", counted)
    workspace._CARDS_KEPT.clear()
    workspace.card_of(study)
    assert len(asked) < 60, len(asked)
    monkeypatch.setattr(posixpath, "realpath", real)
    monkeypatch.setattr(os.path, "realpath", real)

    _write(tmp_path / "outside", "rmsd/options.json", _mean("rmsd", 0.4321))
    options = study / "analysis" / "rmsd" / "options.json"
    with record_access.confined_records(study, lambda path: True):
        assert "0.1234" in record_access.read_text(options)
        moved = study / "analysis" / "rmsd_moved"
        (study / "analysis" / "rmsd").rename(moved)
        _link(study / "analysis" / "rmsd", tmp_path / "outside" / "rmsd")
        with pytest.raises(OSError):
            record_access.read_text(options)
        assert record_access.is_file(options) is False
        assert "analysis/rmsd/options.json" in record_access.refused()


def test_tags_linked_to_a_hidden_file_are_refused_as_the_row_says(tmp_path):
    """A ``study_tags.json`` linked to a hidden file of the study: the
    change was refused as "a link out of the study's own records" while the
    row said it leads to a file of the study that is not one of its
    results. One wording for both."""
    from fastmdxplora.gui.server import _confined
    from fastmdxplora.gui.workspace import card_of
    from fastmdxplora.study_tags import change_tags

    study = _a_study(tmp_path / "hidden_tags")
    (study / "study_tags.json").rename(study / ".tags.json")
    _link(study / "study_tags.json", Path(".tags.json"))
    card = card_of(study)
    said = _confined(study, lambda inside: change_tags(inside, add=["mine"]))
    assert said["ok"] is False
    assert "study_tags.json was not read: it leads to a file of the study" in card["not_read"]
    assert said["reason"] == ("Its tags were not changed: study_tags.json was not read: it "
                              "leads to a file of the study that is not one of its results.")
