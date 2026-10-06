"""A study's system is shown as four capital characters.

Asked for (10-06): a study was shown under a PDB entry in whatever case it
was typed, a file's name with its suffix (``topology.pdb``), or a path. Its
system is its PDB ID where it was given one, otherwise the first four
letters or digits of its structure file's name, in capitals; the person
can give it another, for display only.
"""

from __future__ import annotations

import pytest

from fastmdxplora.system_id import system_id


@pytest.mark.parametrize("system,shown", [
    ("1l2y", "1L2Y"),
    ("3PTB", "3PTB"),
    ("topology.pdb", "TOPO"),
    ("/data/trp_cage.pdb.gz", "TRPC"),
    ("C:\\\\data\\\\ubq.cif", "UBQ"),
    ("my-protein.gro", "MYPR"),
    ("2 systems", "2 systems"),
    ("", ""),
    (None, ""),
])
def test_the_rule(system, shown) -> None:
    assert system_id(system) == shown


def test_the_page_names_it_as_the_server_does(tmp_path) -> None:
    """The Viewer's title is named in the page, the cards on the server:
    both by one rule."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    systems = ["1l2y", "topology.pdb", "/data/trp_cage.pdb.gz", "my-protein.gro", "ab.pdb"]
    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(session.url, wait_until="domcontentloaded")
            page.wait_for_function("() => window.FastMDXDashboard && window.FastMDXDashboard.systemId")
            named = page.evaluate("(all) => all.map((s) => window.FastMDXDashboard.systemId(s))",
                                  systems)
            browser.close()
    finally:
        session.server.shutdown()
    assert named == [system_id(s) for s in systems]
