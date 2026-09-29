"""The builder asks what a study is for, not YAML in a box.

`simulation.stop_when` was offered as a mapping box, with a placeholder of
"measures: [object Object]". It is a form now: measures chosen from the
analyses that record a mean, each with the error it must reach in its own
unit or as a percentage of the mean; the most production any run may reach;
and whether replicas must agree, with a way to add them where the study has
none. What it writes is the rule the config takes, and the validator the run
uses accepts it.
"""

from __future__ import annotations

import pytest
import yaml

from fastmdxplora.gui.schema_payload import schema_payload


def test_the_form_is_told_the_measures_and_their_units():
    field = next(f for f in schema_payload()["phases"]["simulation"]["fields"]
                 if f["name"] == "stop_when")
    assert field["control"] == "stopping"
    found = {m["analysis"]: m for m in field["measures"]}
    from fastmdxplora.simulation.stopping import judgeable_analyses

    assert sorted(found) == judgeable_analyses()
    assert (found["rmsd"]["label"], found["rmsd"]["unit"]) == ("RMSD", "nm")
    assert found["sasa"]["unit"] == "nm²" and found["area_per_lipid"]["unit"] == "nm²"
    assert found["moments_of_inertia"]["unit"] == "amu nm²"


def test_each_unit_known_by_name_is_the_one_the_analysis_records():
    """The names' table is a fall-back for studies analysed before units
    were recorded; it has to say what the analyses' own axes say."""
    import fastmdxplora.analysis.analyze  # noqa: F401
    from fastmdxplora.analysis.orchestrator import _REGISTRY
    from fastmdxplora.gui.report_dashboard import _UNITS

    for name in ("end_to_end", "ligand_rmsd", "area_per_lipid", "bilayer_thickness",
                 "moments_of_inertia"):
        cls = _REGISTRY[name]
        analysis = cls.__new__(cls)
        analysis.__dict__.update({"mode": "com", "options": {}})
        with pytest.MonkeyPatch.context():
            label = cls.default_ylabel(analysis)
        import re

        from fastmdxplora.analysis.base import _unit_in

        unit = re.sub(r"\b(nm|m)([23])\b",
                      lambda m: m.group(1) + "²³"[int(m.group(2)) - 2], _unit_in(label))
        assert _UNITS[name] == unit, name


def test_the_page_writes_the_rule(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.config.loader import validate_config
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(tmp_path / "ws"), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url, wait_until="domcontentloaded")
            page.wait_for_function(
                "window.FastMDXRun && window.FastMDXRun.state && window.FastMDXRun.state.schema")
            page.evaluate("() => window.FastMDXRun.applyLoadedState({"
                          " system: '1UAO', start: 'structure',"
                          " include_phase: ['setup', 'simulation', 'analysis'], phases: {}})")
            page.evaluate("() => window.FastMDXDashboard.navigate('run')")
            heads = page.locator("#run-settings .run-section-head")
            heads.filter(has_text="Simulate").first.click()
            field = page.locator('[data-setting="stop_when"]')
            field.locator(".builder-stopping-add").click()
            row = field.locator(".builder-stopping-row").first
            row.locator(".builder-stopping-analysis").select_option("rmsd")
            kind_label = row.locator(".builder-stopping-kind option").first.text_content()
            # A row being written is kept through the redraw its change causes.
            field.locator(".builder-stopping-add").click()
            second = field.locator(".builder-stopping-row").nth(1)
            second.locator(".builder-stopping-analysis").select_option("rg")
            second.locator(".builder-stopping-kind").select_option("relative")
            second.locator(".builder-stopping-amount").fill("2")
            second.locator(".builder-stopping-amount").dispatch_event("change")
            still = field.locator(".builder-stopping-row").count()
            first = field.locator(".builder-stopping-row").first
            first.locator(".builder-stopping-amount").fill("0.01")
            first.locator(".builder-stopping-amount").dispatch_event("change")
            field.locator(".builder-stopping-ceiling").fill("50")
            field.locator(".builder-stopping-ceiling").dispatch_event("change")
            offered = field.locator(".builder-stopping-seeds").count()
            field.locator(".builder-stopping-seeds").click()
            after = field.locator(".builder-stopping-seeds").count()
            built = page.evaluate("() => window.FastMDXRun.fetchConfig()")
            # Asked to accept one run's precision, it says what that leaves.
            field.locator(".builder-stopping-replicas input").uncheck()
            said = field.locator(".builder-stopping-note").text_content()
            single = page.evaluate("() => window.FastMDXRun.fetchConfig()")
            browser.close()
    finally:
        session.server.shutdown()
    assert kind_label == "to ± nm" and still == 2
    assert offered == 1 and after == 0
    config = yaml.safe_load(built["yaml"])
    assert config["simulation"]["stop_when"] == {
        "measures": [{"analysis": "rmsd", "standard_error": 0.01},
                     {"analysis": "rg", "relative_error": 0.02}],
        "max_duration_ns": 50}
    assert config["sweep"] == {"simulation.random_seed": [1, 2, 3]}
    validate_config(config, require_systems=True)
    assert "not checked against independent starts" in said
    assert yaml.safe_load(single["yaml"])["simulation"]["stop_when"]["independent_starts"] == (
        "not_required")
    assert errors == []
