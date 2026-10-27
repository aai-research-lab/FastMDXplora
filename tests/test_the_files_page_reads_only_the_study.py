"""The Files page reads only the study's own records.

Its tiles say how many frames the trajectory holds over how long, from the
run's parameters, and each analysis row its status from the analysis
manifest. Both were read by name, so a record linked out of the study had
the page say what the file outside said, on a public bind too. Each is now
read held to the study (`record_access`): a record linked out of it is not
opened, its tile or row says it was not read, and the answer names it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_the_study_pages_read_only_the_study import _OPENED, _WATCHED, _ask, _link, _watch

pytest.importorskip("mdtraj")

#: Each record the page reads, what the study's own says, what the file
#: outside it says, and what the page would show of that.
RECORDS = {
    "simulation/simulation_parameters.json": (
        {"n_production_frames": 12, "duration_ns_actual": 0.012},
        {"n_production_frames": 4242, "duration_ns_actual": 77.7},
        ("4,242 frames", "77.7 ns")),
    "analysis/analysis_manifest.json": (
        {"phase": "analysis", "results": {"rmsf": {"status": "ok"}}},
        {"phase": "analysis", "results": {"rmsf": {"status": "failed",
                                                   "message": "MARKER-OUTSIDE-777"}}},
        ("MARKER-OUTSIDE-777",)),
}


def _study(tmp_path: Path) -> Path:
    from tests.test_every_route_reads_only_the_study import _study as written

    root = written(tmp_path)
    for relative, (own, _, _) in RECORDS.items():
        (root / relative).write_text(json.dumps(own), encoding="utf-8")
    return root


def _page(root: Path, tmp_path: Path, public: bool) -> dict:
    status, body = _ask(root, tmp_path, "/api/files-page", public=public)
    assert status == 200, body[:300]
    return json.loads(body)


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("linked", [*sorted(RECORDS), "both"])
def test_a_record_linked_out_is_said_not_read_on_the_files_page(tmp_path, linked, public):
    root = _study(tmp_path)
    page = _page(root, tmp_path, public)
    assert "12 frames" in page["html"] and "not_read" not in page, page.get("not_read")
    outside = tmp_path / "outside"
    names = sorted(RECORDS) if linked == "both" else [linked]
    for relative in names:
        target = outside / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(RECORDS[relative][1]), encoding="utf-8")
        (root / relative).unlink()
        _link(root / relative, target)

    _watch(outside)
    try:
        page = _page(root, tmp_path, public)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()

    assert opened == []
    for relative in names:
        for marker in RECORDS[relative][2]:
            assert marker not in page["html"], marker
        # A tile says the record not read; an analysis a short "not read"
        # pill, which says on hover that its status, not the value beside
        # it, was not read, and why, as `not_read` says it.
        if relative.endswith("manifest.json"):
            assert ('data-state="not read" title="Its status was not read: '
                    f'{relative} was not read, it is a link to a file outside the study.'
                    in page["html"]), page["html"]
        else:
            assert f"{Path(relative).name} not read" in page["html"]
        assert f"{relative} was not read" in page["not_read"], page["not_read"]
    assert str(outside) not in json.dumps(page)
    if "simulation/simulation_parameters.json" not in names:
        assert "12 frames" in page["html"]


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_manifest_linked_to_a_hidden_file_of_the_study_is_said_so(tmp_path, public):
    """The pill's reason is the refusal's own (G13 L3): a link to a hidden
    file of the study is not said to lead outside it."""
    root = _study(tmp_path)
    manifest = root / "analysis" / "analysis_manifest.json"
    hidden = root / ".hidden" / "m.json"
    hidden.parent.mkdir()
    manifest.rename(hidden)
    _link(manifest, hidden)

    page = _page(root, tmp_path, public)

    because = "it leads to a file of the study that is not one of its results"
    assert f"analysis/analysis_manifest.json was not read: {because}" in page["not_read"]
    assert f"analysis/analysis_manifest.json was not read, {because}." in page["html"]
    assert "leads outside the study" not in page["html"]


def test_the_status_not_read_fits_a_narrow_page_and_is_said_without_a_pointer(tmp_path):
    """The "not read" pill of an analysis with a long title sits under its
    name, not clipped with it at 390 pixels, and what it means (its status,
    not the value beside it, was not read) is in the page for a keyboard or
    a screen reader, not only on hover (G15 L1, L2, G16 L1)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _study(tmp_path)
    (root / "analysis" / "rmsf").rename(root / "analysis" / "order_parameters")
    manifest = root / "analysis" / "analysis_manifest.json"
    outside = tmp_path / "outside" / "analysis_manifest.json"
    outside.parent.mkdir()
    outside.write_text(json.dumps({"results": {"order_parameters": {"status": "ok"}}}),
                       encoding="utf-8")
    manifest.unlink()
    _link(manifest, outside)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 390, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#files", wait_until="domcontentloaded")
            page.wait_for_selector(".files-state", state="attached")
            pill = page.evaluate("""() => {
                const e = document.querySelector('.files-state');
                const name = e.closest('.files-name');
                const cut = name ? e.getBoundingClientRect().right
                    > name.getBoundingClientRect().right + 1 : false;
                const said = document.getElementById(e.getAttribute('aria-describedby') || '');
                return {cut: cut || e.scrollWidth > e.clientWidth + 1, inName: !!name,
                        focusable: e.tabIndex >= 0, said: said ? said.textContent : '',
                        wide: document.documentElement.scrollWidth};
            }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert pill["inName"] is False and pill["cut"] is False and pill["wide"] <= 390, pill
    assert pill["focusable"] and pill["said"].startswith("Its status was not read"), pill
