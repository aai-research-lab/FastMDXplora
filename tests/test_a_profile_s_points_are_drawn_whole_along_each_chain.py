"""A residue profile is drawn whole, chain by chain.

Backbone order parameters of two chains, A:1 to A:5 and B:1 to B:5, with
measured values at both ends: every point and measured square lies wholly
inside the axes (the squares are drawn in the plot's clipped layer, and the
axes reach half a residue past each end), and the line goes along each
chain without joining A:5 to B:1.
"""

from __future__ import annotations

import pytest

PAYLOAD = {
    "ok": True, "analysis": "s2", "label": "S²", "unit": "", "kind": "residue",
    "x_label": "Residue", "x": list(range(1, 11)),
    "y": [0.92, 0.85, 0.88, 0.9, 0.8, 0.91, 0.86, 0.87, 0.89, 0.6],
    "reference": [0.95, None, 0.8, None, 0.99, 0.93, None, None, 0.9, 1.0],
    "labels": [f"{chain}:{n}" for chain in "AB" for n in range(1, 6)],
    "residues": [{"chain": chain, "resi": n} for chain in "AB" for n in range(1, 6)],
    "entities": [None] * 10, "linked": False, "mean": None, "y_bounds": [0.0, 1.05],
}


@pytest.mark.parametrize("width", [1440, 390])
def test_measured_squares_are_whole_and_the_line_goes_along_each_chain(tmp_path, width):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = tmp_path / "study"
    root.mkdir()
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.goto(session.url, wait_until="domcontentloaded")
            page.wait_for_function("() => !!window.FastMDXSeries")
            got = page.evaluate("""(data) => {
              const host = document.createElement('div');
              host.style.width = Math.min(560, innerWidth - 32) + 'px';
              document.body.appendChild(host);
              window.FastMDXSeries.draw(host, data);
              const clip = host.querySelector('clipPath rect');
              const box = {left: +clip.getAttribute('x'), top: +clip.getAttribute('y')};
              box.right = box.left + +clip.getAttribute('width');
              box.bottom = box.top + +clip.getAttribute('height');
              const clipped = host.querySelector('g[clip-path]');
              const squares = [...host.querySelectorAll('.series-reference')].map((r) => ({
                inLayer: clipped.contains(r),
                left: +r.getAttribute('x'), top: +r.getAttribute('y'),
                right: +r.getAttribute('x') + +r.getAttribute('width'),
                bottom: +r.getAttribute('y') + +r.getAttribute('height')}));
              const points = [...clipped.querySelectorAll('circle')].map((c) => ({
                left: +c.getAttribute('cx') - +c.getAttribute('r'),
                right: +c.getAttribute('cx') + +c.getAttribute('r')}));
              const line = host.querySelector('.series-line').getAttribute('d');
              return {box, squares, points, moves: (line.match(/M/g) || []).length};
            }""", PAYLOAD)
            browser.close()
    finally:
        session.server.shutdown()
    box = got["box"]
    assert len(got["squares"]) == 6
    for square in got["squares"]:
        assert square["inLayer"], square
        assert box["left"] <= square["left"] and square["right"] <= box["right"], (square, box)
        assert box["top"] <= square["top"] and square["bottom"] <= box["bottom"], (square, box)
    for point in got["points"]:
        assert box["left"] <= point["left"] and point["right"] <= box["right"], (point, box)
    # One line along chain A, one along chain B.
    assert got["moves"] == 2
