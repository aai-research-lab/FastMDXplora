"""The Report page's outline, and each number in the report traced to its record.

The report was a column of text: to find a section a person scrolled, and
a number in it (a mean, a length, a count of atoms) was taken on trust.
An outline is beside it now, its sections numbered as the PDF numbers
them, the one being read marked; and a number given with a unit, an
error, a percent or what it counts, or with a decimal point, opens the
record it was traced to: the file, the place in it, the value as
recorded and, where the report gives it in other units, as it gives it.
A number no record was traced to is said so, one more than one record
holds names them all, and a record whose value a number was traced to
when the report was first read, changed since, is named: a report written
before its analyses were run again says numbers they no longer hold.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from tests.test_the_frame_holds_the_study import _a_finished_study

REPORT = """# Report

## Summary

The prepared system contained 7,439 atoms and was simulated for 20 ps at 26.85 \u00b0C.

## Results

### RMSD

- Mean over the equilibrated part of the run: 0.799 \u00c5, from 8 independent samples.

### RG

- Mean over the equilibrated part of the run: 1.235 nm.
- A mean of 0.0799 nm is the RMSD's, not this section's.

## Convergence

| observable | mean |
|---|---|
| RMSD | 0.07990 |
| Rg | 1.3 |

At least 50 ps more production would determine the RMSD. The run lasted 50 ps.
"""


def _findings(mean: float, samples: float, more_ns: float | None = None) -> dict:
    found = {"unit": "nm", "mean": mean, "standard_error": float("nan"), "n_frames": 100,
             "discard": 10, "effective_samples": samples}
    if more_ns is not None:
        found["shortfall"] = {"more_ns": more_ns, "target_independent": 25.0}
    return {"options": {"selection": "protein"}, "findings": {"mean": found}}


@pytest.fixture()
def study(tmp_path) -> Path:
    root = tmp_path / "study"
    shutil.copytree(_a_finished_study(), root)
    (root / "report" / "report.md").write_text(REPORT, encoding="utf-8")
    (root / "analysis" / "rmsd" / "options.json").write_text(
        json.dumps(_findings(0.0799, 7.9, more_ns=0.05)), encoding="utf-8")
    (root / "analysis" / "rg" / "options.json").write_text(
        json.dumps(_findings(1.2345, 22.0)), encoding="utf-8")
    (root / "setup").mkdir()
    (root / "setup" / "setup_parameters.json").write_text(
        json.dumps({"n_atoms_solvated": 7439}), encoding="utf-8")
    (root / "resolved_config.yml").write_text(
        "simulation:\n  duration_ns: 0.02\n  temperature_K: 300.0\n", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _own_cache(monkeypatch, tmp_path):
    # What a report's numbers traced to when it was first read is kept in
    # the user's cache folder.
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))


def _analysed_again(study, mean=1.3):
    """The Rg analysed again after the report was first read."""
    (study / "analysis" / "rg" / "options.json").write_text(
        json.dumps(_findings(mean, 22.0)), encoding="utf-8")


def _payload(study):
    from fastmdxplora.gui.report_page import report_payload

    said = report_payload(study)
    if said.get("rendered") != "html":
        pytest.skip("the Markdown library is not installed")
    return said


def _by_said(numbers):
    found: dict[str, list[dict]] = {}
    for number in numbers:
        found.setdefault(number["said"], []).append(number)
    return found


def test_each_number_is_traced_to_its_record_in_the_unit_the_report_gives(study):
    said = _payload(study)
    numbers = _by_said(said["numbers"])
    atoms = numbers["7,439"][0]
    assert atoms["traced"] and atoms["record"]["file"] == "setup/setup_parameters.json"
    assert atoms["record"]["key"] == "n_atoms_solvated"
    length = numbers["20 ps"][0]["record"]
    assert (length["file"], length["key"], length["unit"]) == ("resolved_config.yml",
                                                                "simulation.duration_ns", "ns")
    assert length["as_said"] == pytest.approx(20.0)
    # Kelvin recorded, Celsius said.
    assert numbers["26.85 \u00b0C"][0]["record"]["key"] == "simulation.temperature_K"
    # nm recorded, angstrom said, in its own analysis's section.
    rmsd = numbers["0.799 \u00c5"][0]
    assert rmsd["traced"] and rmsd["record"]["file"] == "analysis/rmsd/options.json"
    assert rmsd["record"]["key"] == "findings.mean.mean"
    assert numbers["8"][0]["record"]["key"] == "findings.mean.effective_samples"
    assert numbers["1.235 nm"][0]["record"]["file"] == "analysis/rg/options.json"
    # A table's row says which analysis, its column which field.
    assert numbers["0.07990"][0]["record"]["file"] == "analysis/rmsd/options.json"


def test_a_number_no_record_holds_is_said_so(study):
    said = _payload(study)
    numbers = _by_said(said["numbers"])
    # The RMSD's mean, said under the Rg: not the Rg's record, and no other
    # analysis's.
    elsewhere = numbers["0.0799 nm"][0]
    assert elsewhere["traced"] is False and not elsewhere.get("candidates")
    invented = numbers["1.3"][0]
    assert invented["traced"] is False and not invented.get("candidates")
    # What a run still needs is a record of what is missing: taken for "50
    # ps more", not for the 50 ps a run lasted.
    more, lasted = numbers["50 ps"]
    assert more["traced"] and more["record"]["key"] == "findings.mean.shortfall.more_ns"
    assert lasted["traced"] is False
    assert said["records_changed"] == []
    _analysed_again(study)
    said = _payload(study)
    assert said["records_changed"] == ["analysis/rg/options.json"]
    rg = _by_said(said["numbers"])["1.235 nm"][0]
    assert rg["traced"] is False
    assert rg["changed"] == {"file": "analysis/rg/options.json", "key": "findings.mean.mean",
                             "then": 1.2345, "now": 1.3}
    # The marks are around the numbers alone, and nothing else changed; the
    # report as rendered is kept for the standalone page.
    assert 'data-traced="true" role="button" tabindex="0" aria-expanded="false">7,439</span> atoms' \
        in said["marked"]
    assert "report-number" not in said["html"]
    assert re.sub(r'<span class="report-number"[^>]*>([^<]*)</span>', r"\1", said["marked"]) \
        == said["html"]


def test_a_record_linked_out_of_the_study_is_not_read_to_trace_a_number(study, tmp_path):
    """`/api/report` is read held to the study, but the numbers were traced
    with plain reads: a setup record linked out of the study gave its value
    to the report's atom count, and the outside file's time put it among
    the records changed. It is not read now, its number said as not traced
    because it was not read, and it is named as not read; also when an
    earlier trace, read without the hold, is kept."""
    from fastmdxplora.gui.report_page import report_payload
    from fastmdxplora.gui.server import _confined, _said_not_read
    from tests.test_the_study_pages_read_only_the_study import _link

    _payload(study)
    record = study / "setup" / "setup_parameters.json"
    outside = tmp_path / "outside" / "setup_parameters.json"
    outside.parent.mkdir()
    outside.write_text(json.dumps({"n_atoms_solvated": 7439}), encoding="utf-8")
    later = (study / "report" / "report.md").stat().st_mtime + 120
    os.utime(outside, (later, later))
    record.unlink()
    _link(record, outside)

    # Read without the hold first (the standalone page): it follows the link.
    plain = _by_said(report_payload(study)["numbers"])["7,439"][0]
    assert plain["traced"] and plain["record"]["file"] == "setup/setup_parameters.json"
    for _ in range(2):
        said = _said_not_read(_confined(study, report_payload))
        atoms = _by_said(said["numbers"])["7,439"][0]
        assert atoms["traced"] is False
        assert atoms["not_read"] == ["setup/setup_parameters.json"]
        assert all((n.get("record") or {}).get("file")
                   != "setup/setup_parameters.json" for n in said["numbers"])
        assert "setup/setup_parameters.json" not in said["records_changed"]
        assert said["unavailable"] == ["setup/setup_parameters.json"]
        assert said["not_read"].startswith("setup/setup_parameters.json was not read")
        # What was read is still traced.
        assert _by_said(said["numbers"])["0.799 \u00c5"][0]["traced"]


def test_the_pdf_numbers_its_sections_as_the_outline_does():
    from fastmdxplora.report.pdf import STYLESHEET

    assert 'h2::before { content: counter(section) "  "; }' in STYLESHEET
    assert 'h3::before { content: counter(section) "." counter(subsection) "  "; }' in STYLESHEET


LOOK = """() => ({
    outline: [...document.querySelectorAll('#report-outline .outline-item')]
        .map((b) => b.querySelector('.outline-number').textContent + ' '
             + b.querySelector('.outline-words').textContent),
    numbered: [...document.querySelectorAll('#report-document h2, #report-document h3')]
        .map((h) => h.dataset.section),
    current: (document.querySelector('#report-outline .outline-item[aria-current]') || {}).textContent,
    said: document.getElementById('report-numbers-said').textContent,
    card: (document.querySelector('.report-number-card') || {}).textContent || '',
    marked: document.getElementById('report-document').dataset.marked || '',
    beside: getComputedStyle(document.getElementById('report-layout')).display,
    folded: getComputedStyle(document.querySelector('#report-outline .outline-body')).display,
})"""


def test_the_outline_is_beside_the_report_and_a_number_opens_its_record(study):
    pytest.importorskip("playwright.sync_api")
    _payload(study)
    _analysed_again(study)
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 700})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#report", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.wait_for_selector("#report-outline .outline-item", state="attached")
            # The side panel folded: the page's column is then wide enough
            # for the outline beside the report.
            page.evaluate("() => window.FastMDXFrame.setCollapsed(true)")
            page.wait_for_function("() => getComputedStyle(document.getElementById("
                                   "'report-layout')).display === 'grid'")
            first = page.evaluate(LOOK)
            page.click('#report-outline .outline-item:has-text("Convergence")')
            page.wait_for_function("() => (document.querySelector('#report-outline "
                                   ".outline-item[aria-current]') || {}).textContent === '3Convergence'")
            went = page.evaluate(LOOK)
            page.click('#report-document .report-number:text-is("0.799")')
            page.wait_for_selector(".report-number-card")
            traced = page.evaluate(LOOK)
            page.keyboard.press("Escape")
            page.click('#report-document .report-number:text-is("1.235")')
            page.wait_for_selector('.report-number-card[data-traced="false"]')
            missing = page.evaluate(LOOK)
            page.keyboard.press("Escape")
            page.click("#report-outline .outline-mark")
            marked = page.evaluate(LOOK)
            page.set_viewport_size({"width": 1000, "height": 700})
            page.wait_for_function("() => getComputedStyle(document.getElementById("
                                   "'report-layout')).display === 'block'")
            narrow = page.evaluate(LOOK)
            page.click("#report-outline .outline-toggle")
            opened = page.evaluate(LOOK)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert first["outline"] == ["1 Summary", "2 Results", "2.1 RMSD", "2.2 RG", "3 Convergence"]
    assert first["numbered"] == ["1", "2", "2.1", "2.2", "3"]
    assert first["beside"] == "grid" and first["folded"] == "block"
    assert first["said"].startswith("8 of 11 numbers traced to a record")
    assert ("1 record changed since this report was first read here: analysis/rg/options.json"
            in first["said"])
    assert went["current"] == "3Convergence"
    assert "analysis/rmsd/options.json" in traced["card"] and "findings.mean.mean = 0.0799 nm" in traced["card"]
    assert "That is 0.799 \u00c5, as the report gives it." in traced["card"]
    assert "No record traced." in missing["card"]
    assert ("When this report was first read here, analysis/rg/options.json held "
            "findings.mean.mean = 1.2345; it holds 1.3 now." in missing["card"])
    assert marked["marked"] == "true"
    assert (narrow["beside"], narrow["folded"]) == ("block", "none")
    assert opened["folded"] == "block"


def test_the_page_says_a_number_of_a_record_not_read_is_not_traced(study, tmp_path):
    pytest.importorskip("playwright.sync_api")
    _payload(study)
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_study_pages_read_only_the_study import _link

    record = study / "setup" / "setup_parameters.json"
    outside = tmp_path / "outside" / "setup_parameters.json"
    outside.parent.mkdir()
    outside.write_text(json.dumps({"n_atoms_solvated": 7439}), encoding="utf-8")
    record.unlink()
    _link(record, outside)
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#report", wait_until="domcontentloaded")
            page.wait_for_selector("#report-outline .outline-item", state="attached")
            page.click('#report-document .report-number:text-is("7,439")')
            page.wait_for_selector('.report-number-card[data-traced="false"]')
            said = page.evaluate(LOOK)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert said["card"].startswith("7,439×Not traced: setup/setup_parameters.json was not read, "
                                   "so whether it holds it is not known.")
    assert "No record traced." not in said["card"]
    assert ("setup/setup_parameters.json was not read, so 4 numbers are not traced: it may hold them."
            in said["said"])


#: Reports the software wrote, with the records they were written from: a
#: tri-alanine run of all 13 analyses, one of RMSD and thermodynamics, and a
#: comparison of two runs of a sweep (`tests/data/report_traced`).
WRITTEN = Path(__file__).resolve().parent / "data" / "report_traced"


def _written(tmp_path, name):
    root = tmp_path / name
    shutil.copytree(WRITTEN / name, root)
    return root


def _traced_in(root, report="report/report.md"):
    from fastmdxplora.gui.report_numbers import traced_html
    from fastmdxplora.gui.report_page import render_markdown

    html, rendered = render_markdown((root / report).read_text(encoding="utf-8"))
    if rendered != "html":
        pytest.skip("the Markdown library is not installed")
    return traced_html(html, root)[1]


def _where(number):
    record = number.get("record") or {}
    return record.get("file"), record.get("key")


def test_a_written_report_s_numbers_are_traced_only_to_records_that_hold_them(tmp_path):
    """On reports the pipeline wrote, numbers were traced to records that
    do not hold them: a 4 ps run to an atom index, 1.40 nm to a box
    vector's part, 2 ps to an analysis's count of rules, a count of samples
    that is a method's threshold to an analysis's, and a potential energy's
    samples to the kinetic energy's; the record holding the run's length was
    not seen (ns in the middle of `duration_ns_actual`)."""
    tri = _by_said(_traced_in(_written(tmp_path, "tri")))
    assert _where(tri["4 ps"][0]) == ("simulation/simulation_parameters.json", "duration_ns_actual")
    assert _where(tri["1.40 nm"][0])[1] == "setup.solvent_padding_nm"
    assert _where(tri["1.20 nm"][0])[1].endswith("solvent_padding_nm")
    assert _where(tri["0.04 ps"][0]) == ("analysis/analysis_manifest.json",
                                         "load_kwargs.saving_interval_ps")
    # A threshold of the method ("at least 10 independent samples") and
    # "34 frames hold ..." are no record's.
    assert [n["traced"] for n in tri["10"]] == [False]
    assert [n["traced"] for n in tri["34"]] == [False, False, False]
    assert not any(n.get("candidates") for n in tri["10"] + tri["34"])
    # In a section, its own analysis's record.
    assert [_where(n)[0] for n in tri["66"]][:2] == ["analysis/rmsd/options.json",
                                                     "analysis/rg/options.json"]
    # Where more than one record holds it, each is said and none picked.
    frames = tri["100"][1]
    assert frames["traced"] is False and len(frames["candidates"]) == 2
    # One setting kept as asked and as used is one record.
    pressure = tri["1.0 bar"][0]
    assert pressure["traced"] and _where(pressure)[1].endswith("pressure_bar"), pressure
    assert pressure["also"] == ["simulation/simulation_parameters.json"]
    clean = _by_said(_traced_in(_written(tmp_path, "clean-tri-alanine")))
    assert _where(clean["40 ps"][0])[1] == "duration_ns_actual"
    assert _where(clean["2 ps"][0]) == ("analysis/thermodynamics/options.json",
                                        "findings.thermodynamics.total_energy.shortfall.more_ns")
    assert "kinetic_energy" not in str(_where(clean["13"][0])[1])
    for said in (tri, clean):
        for number in (n for found in said.values() for n in found if n["traced"]):
            record = number["record"]
            # No list item (a box vector's part, an atom pair), and a number
            # said with a unit only from a record that says one.
            assert "[" not in record["key"], number
            if number["said"].split(" ", 1)[1:] and not number["counts"]:
                assert record["unit"], number


def test_a_study_of_runs_traces_each_run_s_numbers_to_that_run(tmp_path):
    """Run folders have hyphens (`s1__temperature-K-310`), the names rows
    gave had not: no row named its run, and the 310 K run's mean was
    traced to the 300 K run's record."""
    numbers = _traced_in(_written(tmp_path, "sweep"), "comparison/comparison_report.md")
    rows = [n for n in numbers if any(s.startswith("s1__") for s in n["subjects"])]
    assert len(rows) == 10
    for number in rows:
        run = next(s for s in number["subjects"] if s.startswith("s1__"))
        if number["said"] in ("300", "310"):
            continue
        assert number["traced"], number
        assert number["record"]["file"].startswith(
            "runs/" + run.replace("_k_", "-K-").replace("temperature_", "temperature-") + "/"), number
    # Said of the change between them, naming no run: no run's record.
    for said in ("0.16001", "0.16034 nm", "0.00011 nm", "1.20001 nm"):
        number = _by_said(numbers)[said][0]
        assert number["traced"] is False and not number.get("candidates"), number


def test_a_number_across_the_replicas_is_no_one_run_s(tmp_path):
    """A study of three replicas, as the pipeline compares them: the mean of
    their means (0.008) and the error each run estimated on average (0.0011)
    were traced to the one run whose own value rounds to them."""
    numbers = _traced_in(_written(tmp_path, "seeds"), "comparison/comparison_report.md")
    across = [n for n in numbers if n["subjects"] and not any(
        s.startswith("s1__") for s in n["subjects"])]
    assert {n["said"] for n in across} >= {"0.008", "0.0011", "0.6827", "0.0024"}
    for number in across:
        assert number["traced"] is False and not number.get("candidates"), number
    # A run's row is still that run's.
    row = [n for n in numbers if "s1__random_seed_2" in n["subjects"]]
    assert [n["said"] for n in row] == ["0.0080", "0.0011"]
    assert all(n["record"]["file"].startswith("runs/s1__random-seed-2/") for n in row)


def test_a_record_the_run_writes_again_unchanged_is_not_said_changed(tmp_path):
    """The pipeline writes the manifest and the resolved Config after the
    report, so every finished study said both had changed since, and Write
    it again could not clear it. Only a value a number was traced to,
    changed since the report was first read, is a change."""
    from fastmdxplora.gui.report_page import report_payload

    root = _written(tmp_path, "tri")
    first = report_payload(root)
    if first.get("rendered") != "html":
        pytest.skip("the Markdown library is not installed")
    for name in ("manifest.json", "resolved_config.yml"):
        path = root / name
        path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        later = (root / "report" / "report.md").stat().st_mtime + 3
        os.utime(path, (later, later))
    assert first["records_changed"] == []
    assert report_payload(root)["records_changed"] == []
    options = root / "analysis" / "rmsd" / "options.json"
    record = json.loads(options.read_text(encoding="utf-8"))
    record["findings"]["mean"]["mean"] = 0.0081
    options.write_text(json.dumps(record), encoding="utf-8")
    again = report_payload(root)
    assert again["records_changed"] == ["analysis/rmsd/options.json"]
    assert _by_said(again["numbers"])["0.0078 nm"][0]["changed"]["now"] == 0.0081


KEYS = """() => {
    const active = document.activeElement;
    const doc = document.getElementById('report-document');
    const card = document.querySelector('.report-number-card');
    const box = card ? card.getBoundingClientRect() : null;
    const cut = [...doc.querySelectorAll('table')].filter((t) => {
        const r = t.getBoundingClientRect(), d = doc.getBoundingClientRect();
        return r.right > d.right + 1 || t.scrollWidth > t.clientWidth + 1;
    }).length;
    return {
        inCard: !!(card && card.contains(active)),
        active: active ? (active.className || active.tagName) + ':' + (active.textContent || '').slice(0, 20) : '',
        cardInView: box ? box.top >= 0 && box.bottom <= innerHeight : null, cardBox: box ? [box.top, box.bottom, innerHeight] : null,
        mark: (document.querySelector('#report-outline .outline-mark') || {}).textContent || '',
        pressed: (document.querySelector('#report-outline .outline-mark') || {
            getAttribute: () => ''}).getAttribute('aria-pressed'),
        marked: doc.dataset.marked || '',
        cut, docWidth: doc.clientWidth,
        layout: getComputedStyle(document.getElementById('report-layout')).display,
        states: [...doc.querySelectorAll('.report-number')].reduce((a, n) => {
            a[n.dataset.state] = (a[n.dataset.state] || 0) + 1; return a; }, {}),
    };
}"""


def test_a_number_s_card_and_the_outline_are_reached_by_the_keyboard(tmp_path):
    """A number's card opened by Enter left focus on the number and sat at
    the report's end: Tab went on to the next numbers. Opened near the
    window's foot it opened out of sight. A section chosen from the folded
    outline left focus nowhere. Mark counted numbers whose record was not
    read as no record's, and lost its state when the report was drawn
    again. With the outline beside it the report's tables were cut."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_study_pages_read_only_the_study import _link

    root = _written(tmp_path, "tri")
    _traced_in(root)
    # One record linked out: its numbers are not traced, and not marked.
    record = root / "setup" / "setup_parameters.json"
    outside = tmp_path / "outside" / "setup_parameters.json"
    outside.parent.mkdir()
    shutil.copy(record, outside)
    record.unlink()
    _link(record, outside)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 700})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#report", wait_until="domcontentloaded")
            page.wait_for_selector("#report-outline .outline-item", state="attached")
            page.evaluate("() => window.FastMDXFrame.setCollapsed(true)")
            page.wait_for_function("() => getComputedStyle(document.getElementById("
                                   "'report-layout')).display === 'grid'")
            # The card is brought into view once, as it opens: a page still
            # being laid out, or figures above it still loading, moved it out
            # again on a slow machine.
            page.wait_for_selector("body.state-ready")
            page.wait_for_function("() => [...document.querySelectorAll('#report-document img')]"
                                   ".every(i => i.complete)")
            wide = page.evaluate(KEYS)
            # A number near the window's foot, by the keyboard.
            number = page.locator('#report-document .report-number:text-is("4.7592")')
            number.evaluate("(n) => window.scrollBy(0, n.getBoundingClientRect().bottom - innerHeight + 4)")
            number.focus()
            page.keyboard.press("Enter")
            page.wait_for_selector(".report-number-card")
            page.wait_for_function("""() => { const r = document.querySelector('.report-number-card')
                .getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }""",
                                   timeout=15000)
            opened = page.evaluate(KEYS)
            page.keyboard.press("Tab")
            tabbed = page.evaluate(KEYS)
            page.keyboard.press("Escape")
            back = page.evaluate(KEYS)
            page.click("#report-outline .outline-mark")
            # The report drawn again, its records changed: Mark keeps its state.
            page.evaluate("""async () => {
                const data = await (await fetch('/api/report')).json();
                window.FastMDXReportOutline.render(document.getElementById('report-document'),
                    {...data, records_changed: ['analysis/rmsd/options.json']});
            }""")
            redrawn = page.evaluate(KEYS)
            page.set_viewport_size({"width": 390, "height": 800})
            page.wait_for_function("() => getComputedStyle(document.getElementById("
                                   "'report-layout')).display === 'block'")
            page.click("#report-outline .outline-toggle")
            page.locator('#report-outline .outline-item:has-text("Convergence")').focus()
            page.keyboard.press("Enter")
            section = page.evaluate("() => document.activeElement.tagName + ' ' + "
                                    "document.activeElement.textContent")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert (wide["layout"], wide["cut"]) == ("grid", 0) and wide["docWidth"] >= 840, repr(wide)
    assert opened["inCard"] and opened["cardInView"], opened
    assert tabbed["inCard"] and tabbed["active"].startswith("line-btn number-close"), tabbed
    assert back["active"].startswith("report-number:4.7592"), back
    # Not the numbers of the record not read.
    marked = int(re.search(r"Mark the (\d+) not traced", wide["mark"]).group(1))
    states = wide["states"]
    assert states.get("unread", 0) > 0
    assert marked == states.get("none", 0) + states.get("several", 0), wide
    assert (redrawn["marked"], redrawn["pressed"], redrawn["mark"]) == ("true", "true", "Unmark them")
    assert section.startswith("H2") and "Convergence" in section


def test_a_temperature_s_error_in_degrees_c_is_its_error_in_kelvin():
    """An error is a difference, so 2.4 K is 2.4 degrees C; the trace added
    273.15 to it as to a temperature."""
    from fastmdxplora.gui.report_numbers import trace_number

    records = [{"file": "analysis/thermodynamics/options.json",
                "key": "findings.thermodynamics.temperature.mean", "value": 298.04, "unit": "K"},
               {"file": "analysis/thermodynamics/options.json",
                "key": "findings.thermodynamics.temperature.standard_error", "value": 2.4, "unit": "K"}]
    mean = trace_number("24.89", "°C", records, subjects=("thermodynamics",))
    error = trace_number("2.4", "°C", records, subjects=("thermodynamics",), error=True)
    assert mean["traced"] and mean["record"]["key"].endswith("temperature.mean")
    assert error["traced"] and error["record"]["key"].endswith("standard_error")


def test_write_it_again_clears_a_change_even_where_the_report_reads_the_same(tmp_path):
    """The first read was kept by the report's words: a record changed by
    less than the report prints, and the report written again the same,
    was said changed until the cache was deleted."""
    from fastmdxplora.gui.report_page import report_payload

    root = _written(tmp_path, "tri")
    if report_payload(root).get("rendered") != "html":
        pytest.skip("the Markdown library is not installed")
    options = root / "analysis" / "rmsd" / "options.json"
    record = json.loads(options.read_text(encoding="utf-8"))
    record["findings"]["mean"]["mean"] += 1e-9
    options.write_text(json.dumps(record), encoding="utf-8")
    assert report_payload(root)["records_changed"] == ["analysis/rmsd/options.json"]
    # Written again: the same words, a new file.
    report = root / "report" / "report.md"
    report.write_text(report.read_text(encoding="utf-8"), encoding="utf-8")
    later = report.stat().st_mtime + 5
    os.utime(report, (later, later))
    assert report_payload(root)["records_changed"] == []


def test_the_first_reads_kept_are_bounded(tmp_path, monkeypatch):
    from fastmdxplora.gui import report_page

    folder = tmp_path / "cache" / "report_traces"
    folder.mkdir(parents=True)
    for k in range(report_page._FIRST_READS_KEPT + 20):
        path = folder / f"{k:032d}.json"
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (1_000_000 + k, 1_000_000 + k) if k < 10 else None)
    root = _written(tmp_path, "tri")
    report_page.report_payload(root)
    left = list(folder.glob("*.json"))
    assert len(left) <= report_page._FIRST_READS_KEPT
    # The oldest (long past the days kept) gone first.
    assert not any(p.name == f"{0:032d}.json" for p in left)


def test_a_first_read_in_use_is_kept_however_old(tmp_path):
    """The cache let go of first reads by when they were written: a study
    read on and off for three months lost its first read to another study's,
    and after a restart said its changed records were not changed."""
    from fastmdxplora.gui import report_page

    a, b = _written(tmp_path, "tri"), _written(tmp_path, "clean-tri-alanine")
    if report_page.report_payload(a).get("rendered") != "html":
        pytest.skip("the Markdown library is not installed")
    folder = tmp_path / "cache" / "report_traces"
    (stored,) = list(folder.glob("*.json"))
    old = stored.stat().st_mtime - 91 * 86400
    os.utime(stored, (old, old))
    options = a / "analysis" / "rmsd" / "options.json"
    record = json.loads(options.read_text(encoding="utf-8"))
    record["findings"]["mean"]["mean"] += 1e-9
    options.write_text(json.dumps(record), encoding="utf-8")
    assert report_page.report_payload(a)["records_changed"] == ["analysis/rmsd/options.json"]
    report_page.report_payload(b)            # another study's first read prunes
    report_page._FIRST_READ.clear()          # the server started again
    report_page._TRACED.clear()
    assert stored.exists()
    assert report_page.report_payload(a)["records_changed"] == ["analysis/rmsd/options.json"]
    # Held in memory: adding one past the bound lets the oldest go.
    for k in range(report_page._FIRST_READS_KEPT + 5):
        report_page._kept(f"k{k}", {"numbers": {}})
    assert len(report_page._FIRST_READ) == report_page._FIRST_READS_KEPT


def test_a_section_and_a_record_opened_by_the_keyboard_are_in_sight(tmp_path):
    """A heading gone to from the outline sat under the page's title bar; a
    card's Open the record left the card open, cut by the panel it opened;
    Tab past the card's last button left it open over the report."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _written(tmp_path, "tri")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#report", wait_until="domcontentloaded")
            page.wait_for_selector("#report-outline .outline-item", state="attached")
            page.evaluate("() => window.FastMDXFrame.setCollapsed(true)")
            page.wait_for_function("() => getComputedStyle(document.getElementById("
                                   "'report-layout')).display === 'grid'")
            page.locator('#report-outline .outline-item:has-text("Thermodynamics")').focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            sight = page.evaluate("""() => {
                const h = document.activeElement, bar = document.querySelector(
                    '.page[data-page="report"] .page-header');
                return [h.tagName, h.getBoundingClientRect().top, bar.getBoundingClientRect().bottom];
            }""")
            number = page.locator('#report-document .report-number:text-is("1,409")').first
            number.focus()
            page.keyboard.press("Enter")
            page.wait_for_selector(".report-number-card")
            for _ in range(4):
                page.keyboard.press("Tab")
            tabbed = page.evaluate("() => !!document.querySelector('.report-number-card')")
            number.focus()
            page.keyboard.press("Enter")
            page.wait_for_selector(".report-number-card")
            page.click('.report-number-card .plot-tool:text("Open the record")')
            opened = page.evaluate("""() => [!!document.querySelector('.report-number-card'),
                document.activeElement.textContent]""")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert sight[0] == "H3" and sight[1] >= sight[2] - 1, sight
    assert tabbed is False
    assert opened == [False, "1,409"]
