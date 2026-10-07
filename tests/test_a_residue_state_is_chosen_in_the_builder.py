"""A residue's protonation state is chosen in the builder, from the structure.

`setup.residue_states` (1121) gives a named residue the state asked for in
place of setup's own choice, and the builder offered it as a box for YAML.
The person had to know the chain and number of the histidine they meant, and
the three names a histidine's states go by. The preview now lists every
residue of the structure that can take another state, with the states each
takes and, where a structural metal is within reach of its side chain, which
atom and how far; the builder offers them as rows, and a histidine clicked in
the picture of the system is added to them.

The distance is a fact about the structure. Which state follows from it is
the person's to decide, and nothing here chooses one.
"""

from __future__ import annotations

import pytest

from fastmdxplora.gui import preview
from fastmdxplora.gui.preview import system_preview, titratable_residues
from fastmdxplora.setup.estimate import estimate_system

ATOM = ("{:6s}{:5d} {:<4s} {:>3s} {}{:4d}    {:8.3f}{:8.3f}{:8.3f}  1.00  0.00"
        "          {:>2s}\n")

RESIDUES = [
    ("GLY", [("N", 0, 0), ("CA", 1.4, 0), ("C", 2.4, 1), ("O", 2.4, 2.2)]),
    ("HIS", [("N", 3.6, .5), ("CA", 5, .5), ("C", 6, 1.5), ("O", 6, 2.7), ("CB", 5.3, -1),
             ("CG", 5.3, -2.4), ("ND1", 4.3, -3.3), ("CE1", 4.9, -4.5), ("NE2", 6.1, -4.4),
             ("CD2", 6.4, -3)]),
    ("ASP", [("N", 7.2, 1), ("CA", 8.5, 1), ("C", 9.5, 2), ("O", 9.5, 3.2), ("CB", 8.8, -.5),
             ("CG", 8.8, -2), ("OD1", 8, -2.8), ("OD2", 9.8, -2.5)]),
    ("LYS", [("N", 10.7, 1.5), ("CA", 12, 1.5), ("C", 13, 2.5), ("O", 13, 3.7),
             ("CB", 12.3, 0), ("CG", 12.3, -1.5), ("CD", 12.3, -3), ("CE", 12.3, -4.5),
             ("NZ", 12.3, -6)]),
]


def _structure() -> str:
    lines, serial = [], 1
    for number, (resname, atoms) in enumerate(RESIDUES, start=1):
        for name, x, y in atoms:
            lines.append(ATOM.format("ATOM", serial, f" {name}" if len(name) < 4 else name,
                                     resname, "A", number, x, y, 0.0, name[0]))
            serial += 1
    # A zinc 2.4 Angstrom from the histidine's NE2.
    lines.append(ATOM.format("HETATM", serial, "ZN", "ZN", "A", 401, 7.6, -6.3, 0.0, "ZN"))
    return "".join(lines) + "END\n"


@pytest.fixture
def structure(tmp_path):
    path = tmp_path / "site.pdb"
    path.write_text(_structure(), encoding="utf-8")
    return path


class TestWhatThePreviewLists:
    def test_each_residue_that_can_take_another_state(self, structure):
        listed = titratable_residues(estimate_system(structure, {}).atoms)
        assert [(r["key"], r["resname"], r["states"]) for r in listed] == [
            ("A:2", "HIS", ["HID", "HIE", "HIP"]),
            ("A:3", "ASP", ["ASH", "ASP"]),
            ("A:4", "LYS", ["LYN", "LYS"]),
        ]

    def test_a_metal_within_reach_is_said_as_a_distance(self, structure):
        his, asp, lys = titratable_residues(estimate_system(structure, {}).atoms)
        assert his["near"] == "NE2 is 2.4 Å from ZN A:401"
        # The aspartate's oxygens are 4.4 Angstrom away: past a first shell.
        assert asp["near"] is None and lys["near"] is None

    def test_it_reaches_the_builder_with_the_estimate(self, structure, monkeypatch, tmp_path):
        monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setattr(preview, "_platform", lambda requested, precision: ("CPU", precision))
        answer = system_preview({"system": str(structure),
                                 "include_phase": ["setup", "simulation"]})
        assert answer["ok"], answer
        assert [r["key"] for r in answer["titratable"]] == ["A:2", "A:3", "A:4"]

    def test_the_form_is_told_the_states_and_what_each_is(self):
        from fastmdxplora.gui.schema_payload import schema_payload

        (field,) = [f for f in schema_payload()["phases"]["setup"]["fields"]
                    if f["name"] == "residue_states"]
        assert field["control"] == "residues"
        assert field["states"]["HIS"] == ["HID", "HIE", "HIP"]
        assert field["meaning"]["HIE"] == "neutral, hydrogen on NE2"
        assert field["meaning"]["HIP"] == "charged, hydrogens on both"


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    sync_api = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    root = tmp_path_factory.mktemp("residues")
    (root / "site.pdb").write_text(_structure(), encoding="utf-8")
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
            page.wait_for_selector("#run-start [data-start='structure']", state="attached")
            page.evaluate("() => window.FastMDXRun.setStart('structure')")
            page.fill("#run-system", str(root / "site.pdb"))
            page.wait_for_function(
                "() => /particles/.test(document.getElementById("
                "'run-system-preview-body').textContent)", timeout=60000)
            page.errors = errors
            yield page
            browser.close()
    finally:
        session.server.shutdown()
        mp.undo()


class TestInTheBuilder:
    def _config(self, page) -> str:
        built = page.evaluate("() => window.FastMDXRun.fetchConfig()")
        assert built["ok"], built
        return built["yaml"]

    def test_the_histidine_is_marked_in_the_picture(self, page):
        page.wait_for_selector('#run-system-preview-view[data-residues-marked="1"]')

    def test_a_residue_is_chosen_from_the_structure(self, page):
        page.evaluate("() => window.FastMDXRun.pickResidue('A:2')")
        row = '#run-settings .builder-residue-row[data-residue="A:2"]'
        page.wait_for_selector(row)
        assert page.text_content(f"{row} .builder-residue-name") == "A:2 HIS"
        assert page.text_content(f"{row} .builder-residue-near") == \
            "NE2 is 2.4 Å from ZN A:401"
        options = page.eval_on_selector_all(f"{row} select option", "os => os.map(o => o.value)")
        assert options == ["", "HID", "HIE", "HIP"]
        # Focused, and nothing chosen for the person: not yet in the config.
        assert page.evaluate(
            "() => document.activeElement.closest('.builder-residue-row')?.dataset.residue") \
            == "A:2"
        assert "residue_states" not in self._config(page)
        page.select_option(f"{row} select", "HID")
        page.wait_for_function(
            "() => window.FastMDXRun.state.values.setup?.residue_states?.['A:2'] === 'HID'")
        assert "residue_states:\n    A:2: HID" in self._config(page)

    def test_the_others_are_offered_by_kind(self, page):
        groups = page.eval_on_selector_all(
            "#run-settings .builder-residue-pick optgroup", "gs => gs.map(g => g.label)")
        assert groups == ["ASP", "LYS"]
        page.select_option("#run-settings .builder-residue-pick", "A:4")
        row = '#run-settings .builder-residue-row[data-residue="A:4"]'
        page.wait_for_selector(row)
        page.select_option(f"{row} select", "LYN")
        page.wait_for_function(
            "() => window.FastMDXRun.state.values.setup?.residue_states?.['A:4'] === 'LYN'")
        # The picture marks what was set, and says it.
        page.wait_for_function(
            "() => document.getElementById('run-system-preview-view')"
            ".dataset.residuesMarked === '2'", timeout=60000)

    def test_a_residue_left_to_setup_again(self, page):
        page.click('#run-settings .builder-residue-row[data-residue="A:4"] .run-sweep-remove')
        page.wait_for_function(
            "() => !('A:4' in (window.FastMDXRun.state.values.setup?.residue_states || {}))")
        assert "A:4" not in self._config(page) and "A:2: HID" in self._config(page)
        assert page.errors == []
