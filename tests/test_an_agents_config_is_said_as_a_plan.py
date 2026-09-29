"""An Agent's config is said as a plan before it is run.

The reply showed the refusals the Agent worked through and a row of
buttons; what it had written was behind "Show the config", as YAML. Whether
it would run for 2 ns because no length was given, or build a dodecahedron,
was learned by reading the file. The reply now says, in lines, what will be
built, run and measured, with the values the run will take: the config's,
and the defaults, marked as defaults. Lengths are resolved by the runner's
own function and umbrella windows by the umbrella module's.
"""

from __future__ import annotations

import json
import tempfile

import pytest

from fastmdxplora.gui.plan import plan_of


def _lines(config: dict) -> dict:
    return {line["label"]: line for line in plan_of(config)}


class TestThePlan:

    def test_what_is_not_said_is_the_default_and_marked(self) -> None:
        plan = _lines({"systems": [{"system": "1UBQ"}]})
        assert plan["System"]["value"] == "1UBQ"
        assert plan["Production"] == {"label": "Production", "value": "2 ns, 2 fs steps",
                                      "default": True}
        assert plan["Equilibration"]["value"] == "NVT 500 ps, then NPT 1 ns"
        assert plan["Conditions"] == {"label": "Conditions", "value": "300 K, 1 bar",
                                      "default": True}
        assert plan["Solvent"]["value"] == (
            "dodecahedron box, 1 nm padding, 0.15 M Na+/Cl-")
        assert plan["Force field"]["value"] == "chosen for the structure"
        assert plan["Analyses"]["value"] == "the default set"

    def test_what_is_said_is_given(self) -> None:
        plan = _lines({
            "systems": [{"system": "1UBQ"}],
            "setup": {"forcefield": "charmm36", "water_model": "tip3p",
                      "solvent_padding_nm": 1.2, "box_shape": "cube"},
            "simulation": {"duration_ns": 50, "timestep_fs": 4, "temperature_K": 310,
                           "nvt_duration_ns": 0.1, "pressure_atm": 1},
            "analysis": {"include": ["rmsd", "rg"]},
        })
        assert plan["Force field"] == {"label": "Force field",
                                       "value": "charmm36, tip3p water", "default": False}
        assert plan["Solvent"]["value"].startswith("cube box, 1.2 nm padding")
        assert plan["Production"] == {"label": "Production", "value": "50 ns, 4 fs steps",
                                      "default": False}
        # The default NPT stage is a number of steps, so at 4 fs it is 2 ns:
        # what the runner will do, which is what the plan is for.
        assert plan["Equilibration"]["value"] == "NVT 100 ps, then NPT 2 ns"
        assert plan["Conditions"]["value"] == "310 K, 1 atm"
        assert plan["Analyses"]["value"] == "rmsd, rg"

    def test_umbrella_windows_as_the_umbrella_module_lays_them(self) -> None:
        plan = _lines({
            "systems": [{"system": "181L"}], "setup": {"ligand": "BNZ"},
            "simulation": {"duration_ns": 5, "umbrella": {
                "collective_variable": "ligand_distance", "from": 0.3, "to": 1.5,
                "n_windows": 13, "force_constant": 2000}},
        })
        assert plan["Sampling"]["value"] == (
            "umbrella sampling, 13 windows along ligand_distance from 0.3 to 1.5")
        assert plan["Production"]["value"] == "5 ns per window, 2 fs steps"
        assert plan["Ligand"]["value"] == "BNZ"

    def test_replicas_and_other_axes(self) -> None:
        plan = _lines({"systems": [{"system": "1UBQ"}], "sweep": {
            "simulation.random_seed": [1, 2, 3], "simulation.temperature_K": [300, 350]}})
        assert plan["Replicas"]["value"] == "3, differing only by random seed"
        assert plan["Varied"]["value"] == "simulation.temperature_K: 300, 350"

    def test_only_the_phases_that_run(self) -> None:
        plan = _lines({"systems": [{"system": "a.pdb"}], "include_phase": ["analysis", "report"],
                       "analysis": {"trajectory": "md.xtc"}})
        assert "Production" not in plan and "Solvent" not in plan
        assert "Analyses" in plan

    def test_a_long_run_is_said_in_microseconds(self) -> None:
        plan = _lines({"systems": [{"system": "1UBQ"}], "simulation": {"duration_ns": 2500}})
        assert plan["Production"]["value"].startswith("2.5 µs")

    def test_the_ceiling(self) -> None:
        plan = _lines({"systems": [{"system": "1UBQ"}], "budget_hours": 12})
        assert plan["Ceiling"]["value"].startswith("12 hours of compute")

    def test_force_field_files_named(self) -> None:
        plan = _lines({"systems": [{"system": "1UBQ"}],
                       "setup": {"force_field": "amber14-all.xml"}})
        assert plan["Force field"] == {"label": "Force field", "value": "amber14-all.xml",
                                       "default": False}

    @pytest.mark.parametrize("membrane,said", [({"lipid": "POPC"}, "POPC bilayer"),
                                               ("DMPC", "DMPC bilayer"), ({}, None)])
    def test_a_membrane(self, membrane, said) -> None:
        plan = _lines({"systems": [{"system": "1AFO"}], "setup": {"membrane": membrane}})
        assert (plan.get("Membrane") or {}).get("value") == said

    def test_analyses_named_in_one_string(self) -> None:
        plan = _lines({"systems": [{"system": "1UBQ"}], "analysis": {"include": "rmsd, rg"}})
        assert plan["Analyses"]["value"] == "rmsd, rg"

    @pytest.mark.parametrize("block,said", [
        ({"metadynamics": {"collective_variable": "phi"}}, "metadynamics along phi"),
        ({"metadynamics": {}}, "metadynamics"),
        ({"steered": {"collective_variable": "ligand_distance"}},
         "steered pulling along ligand_distance"),
        ({"plumed": "plumed.dat"}, "a PLUMED bias of your own"),
        ({"umbrella": {"collective_variable": "ligand_distance"}},
         "umbrella sampling along ligand_distance"),
    ])
    def test_every_bias(self, block, said) -> None:
        plan = _lines({"systems": [{"system": "181L"}], "simulation": block})
        assert plan["Sampling"]["value"] == said

    def test_equilibration_only(self) -> None:
        plan = _lines({"systems": [{"system": "1UBQ"}], "simulation": {"duration_ns": 0}})
        assert plan["Production"]["value"] == "none: the study equilibrates and stops"

    def test_lengths_that_cannot_be_worked_out_are_not_said(self) -> None:
        plan = _lines({"systems": [{"system": "1UBQ"}],
                       "simulation": {"timestep_fs": 0, "duration_ns": "ten"}})
        assert "Production" not in plan and "Equilibration" not in plan

    def test_what_it_cannot_read_it_gives_as_written(self) -> None:
        from fastmdxplora.gui.plan import _int, _number, _value

        assert _number("abc") == "abc" and _int("x") is None
        assert _value({}, "setup", "no_such_setting") == (None, False)

    def test_no_dash_in_what_it_says(self) -> None:
        plan = plan_of({"systems": [{"system": "1UBQ"}], "budget_hours": 3,
                        "sweep": {"simulation.random_seed": [1, 2]}})
        assert not any("—" in l["value"] or "–" in l["value"] for l in plan)


def test_the_reply_carries_it(monkeypatch) -> None:
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui import agent_panel

    reply = ("systems:\n  - {id: a, system: 1UBQ}\n"
             "simulation:\n  duration_ns: 10\n")
    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: (lambda prompt: reply))
    answer = agent_panel.propose_endpoint({"request": "ubiquitin for 10 ns"}, None)
    assert answer["ok"]
    production = next(l for l in answer["plan"] if l["label"] == "Production")
    assert production["value"] == "10 ns, 2 fs steps" and not production["default"]


def test_a_plan_that_cannot_be_said_does_not_lose_the_config(monkeypatch) -> None:
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui import agent_panel, plan

    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: (
        lambda prompt: "systems:\n  - {id: a, system: 1UBQ}\n"))
    monkeypatch.setattr(plan, "plan_of", lambda config: 1 / 0)
    answer = agent_panel.propose_endpoint({"request": "x"}, None)
    assert answer["ok"] and answer["plan"] == []


def test_the_page_shows_it_under_the_reply(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    import urllib.request

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    entries = [
        {"role": "user", "text": "ubiquitin for 10 ns"},
        {"role": "agent", "kind": "config", "yaml": "systems: []\n", "config": {},
         "cycles": 1, "attempts": [],
         "plan": [{"label": "Production", "value": "10 ns, 2 fs steps", "default": False},
                  {"label": "Conditions", "value": "300 K, 1 bar", "default": True}]},
    ]
    request = urllib.request.Request(
        session.url + "/api/agent/conversation", data=json.dumps({"entries": entries}).encode(),
        headers={"Content-Type": "application/json", "Origin": session.url}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => { const p = document.querySelector('#agent-thread .agent-plan');"
                " return p && !p.hidden; }", timeout=15000)
            terms = page.locator("#agent-thread .agent-plan dt").all_text_contents()
            values = page.locator("#agent-thread .agent-plan dd").all_text_contents()
            browser.close()
    finally:
        session.server.shutdown()
    assert terms == ["Production", "Conditions"]
    assert values[0] == "10 ns, 2 fs steps"
    assert values[1] == "300 K, 1 bar default"


def test_the_plan_ends_with_what_it_would_build_and_take(tmp_path) -> None:
    """Added by the page once the builder holds the proposal: the size of the
    system and the time here, as the builder's preview says them."""
    pytest.importorskip("playwright.sync_api")
    import urllib.request

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    structure = tmp_path / "peptide.pdb"
    structure.write_text("".join(
        f"ATOM  {4 * i + k + 1:5d}  {name:<3s} GLY A{i + 1:4d}    {3.6 * i + 0.4 * k:8.3f}"
        f"{0.0:8.3f}{0.0:8.3f}  1.00  0.00           {name[0]}\n"
        for i in range(3) for k, name in enumerate(("N", "CA", "C", "O"))) + "END\n",
        encoding="utf-8")
    config = {"systems": [{"id": "p", "system": str(structure)}],
              "simulation": {"duration_ns": 1}}
    session = start_dashboard_session(output=str(tmp_path / "study"), host="127.0.0.1", port=0)
    entries = [
        {"role": "user", "text": "this peptide for a nanosecond"},
        {"role": "agent", "kind": "config", "yaml": "systems: []\n", "config": config,
         "cycles": 1, "attempts": [],
         "plan": [{"label": "Production", "value": "1 ns, 2 fs steps", "default": False}]},
    ]
    request = urllib.request.Request(
        session.url + "/api/agent/conversation", data=json.dumps({"entries": entries}).encode(),
        headers={"Content-Type": "application/json", "Origin": session.url}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(60000)
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.wait_for_selector("#agent-thread .agent-plan dt.agent-plan-cost",
                                   state="attached")
            shown = page.is_visible("#agent-thread .agent-plan dt.agent-plan-cost")
            terms = page.locator("#agent-thread .agent-plan dt").all_text_contents()
            values = page.locator("#agent-thread .agent-plan dd").all_text_contents()
            browser.close()
    finally:
        session.server.shutdown()
    assert terms == ["Production", "System", "Time here"]
    assert shown
    assert values[1].startswith("about ") and "dodecahedron" in values[1]
    assert "padding grown to" in values[1]
    assert values[2] == "not known: this machine has not been timed"
