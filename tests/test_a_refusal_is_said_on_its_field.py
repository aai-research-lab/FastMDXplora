"""A refused setting is said on its own field, with what would fix it.

The builder puts its config through the command line's validator, and a
refusal came back as one line under the buttons: `setup option 'ph' is 25,
above the largest value it can have (14)`, with the setting itself folded
away in a closed section further up the page. The refusal now carries the
setting it is about and its fix (`fastmdxplora.remedies`), and the builder
says both on that setting's field, with its section opened and the field
pointed at. A spelling the schema holds near what was given is offered as a
button; nothing changes until it is pressed.
"""

from __future__ import annotations

import pytest

from fastmdxplora.gui.config_builder import config_yaml, render_config
from fastmdxplora.gui.run_from_config import prepare_run


def _state(**setup) -> dict:
    return {"system": "1L2Y", "include_phase": ["setup", "simulation"], "setup": setup}


class TestTheRefusalCarriesItsField:
    def test_a_value_outside_what_it_can_be(self):
        built = config_yaml(_state(ph=25))
        assert not built["ok"]
        assert built["refusal"]["code"] == "config.option.out_of_range"
        assert built["remedy"]["settings"] == ["setup.ph"]
        assert built["remedy"]["fix"].startswith("Change `setup.ph`.")
        assert built["remedy"]["suggestion"] is None

    def test_a_value_the_schema_does_not_list_with_the_nearest_it_does(self):
        built = render_config({"systems": [{"system": "1L2Y"}],
                               "setup": {"box_shape": "dodecahedran"}})
        remedy = built["remedy"]
        assert remedy["settings"] == ["setup.box_shape"]
        assert remedy["permitted"] == ["cube", "dodecahedron", "octahedron"]
        assert remedy["suggestion"] == "dodecahedron"

    def test_a_run_refused_before_it_starts_says_it_too(self, tmp_path):
        prepared = prepare_run(_state(ph=-1), tmp_path / "study")
        assert not prepared["ok"] and prepared["remedy"]["settings"] == ["setup.ph"]


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui import preview
    from fastmdxplora.gui.server import start_dashboard_session

    root = tmp_path_factory.mktemp("refused")
    atom = "ATOM  {:5d}  {:<3s} GLY A{:4d}    {:8.3f}{:8.3f}{:8.3f}  1.00  0.00           {}\n"
    (root / "peptide.pdb").write_text("".join(
        atom.format(4 * i + k + 1, name, i + 1, 3.6 * i + 0.4 * k, 0.0, 0.0, name[0])
        for i in range(3) for k, name in enumerate(("N", "CA", "C", "O"))) + "END\n",
        encoding="utf-8")
    mp = pytest.MonkeyPatch()
    mp.setenv("FASTMDXPLORA_CACHE_DIR", str(root / "cache"))
    mp.setenv("FASTMDXPLORA_CONFIG_DIR", str(root / "config"))
    mp.setattr(preview, "_platform", lambda requested, precision: ("CPU", precision))
    session = start_dashboard_session(output=str(root / "study"), host="127.0.0.1", port=0)
    try:
        with sync_api.sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url.rstrip("/") + "/#run", wait_until="domcontentloaded")
            page.wait_for_selector("#run-start option[value='structure']", state="attached")
            page.select_option("#run-start", "structure")
            page.fill("#run-system", str(root / "peptide.pdb"))
            page.errors = errors
            yield page
            browser.close()
    finally:
        session.server.shutdown()
        mp.undo()


def _ask(page) -> dict:
    return page.evaluate("() => window.FastMDXRun.fetchConfig()")


class TestOnTheField:
    def test_it_is_said_on_the_field_with_the_section_opened(self, page):
        page.evaluate("""() => {
            const s = window.FastMDXRun.state;
            s.values.setup = Object.assign({}, s.values.setup, {ph: 25});
        }""")
        assert not _ask(page)["ok"]
        field = '#run-settings .builder-field.is-refused[data-setting="ph"]'
        page.wait_for_selector(field)
        assert page.text_content(f"{field} .builder-refusal-why") == (
            "setup option 'ph' is 25.0, above the largest value it can have (14.0).")
        # The setting's name set as code, as the refusal writes it.
        assert page.text_content(f"{field} .builder-refusal-fix").startswith(
            "Change setup.ph.")
        assert page.text_content(f"{field} .builder-refusal-fix code") == "setup.ph"
        assert page.evaluate(
            "() => document.activeElement.closest('.builder-field')?.dataset.setting") == "ph"
        assert page.locator("#run-settings .builder-field.is-refused").count() == 1

    def test_a_change_to_it_takes_the_refusal_away(self, page):
        page.fill('#run-settings [data-setting="ph"] input', "7")
        page.dispatch_event('#run-settings [data-setting="ph"] input', "change")
        page.wait_for_function(
            "() => !document.querySelector('#run-settings .builder-field.is-refused')")
        assert _ask(page)["ok"]

    def test_the_nearest_spelling_is_offered_and_applied_only_when_pressed(self, page):
        page.evaluate("""() => {
            const s = window.FastMDXRun.state;
            s.values.setup = Object.assign({}, s.values.setup, {box_shape: 'dodecahedran'});
        }""")
        assert not _ask(page)["ok"]
        use = '#run-settings [data-setting="box_shape"] .builder-refusal-use'
        page.wait_for_selector(use)
        assert page.text_content(use) == "Use dodecahedron"
        assert page.evaluate("() => window.FastMDXRun.state.values.setup.box_shape") == \
            "dodecahedran"
        page.click(use)
        page.wait_for_function(
            "() => window.FastMDXRun.state.values.setup.box_shape === 'dodecahedron'")
        assert page.locator("#run-settings .builder-field.is-refused").count() == 0
        assert _ask(page)["ok"]
        assert page.errors == []
