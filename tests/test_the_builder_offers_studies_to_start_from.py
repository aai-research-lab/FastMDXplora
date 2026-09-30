"""The builder offers complete studies to start from.

A first study began at an empty form. The builder now offers the kinds of
study the examples page has recipes for (a protein in water, a protein and
its ligand, a membrane protein, a study run until a quantity is determined,
a free energy along a distance), each a complete Config the validator
accepts, each tile saying what it is for and what it will run. Chosen, it is
loaded as any Config is, and what the builder then writes is that study.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from fastmdxplora.gui.starters import STARTERS, starters_payload

DOCS = Path(__file__).resolve().parents[1] / "docs"


@pytest.mark.parametrize("starter", STARTERS, ids=[s["id"] for s in STARTERS])
class TestEachStarter:
    def test_the_validator_accepts_it(self, starter):
        from fastmdxplora.config.loader import validate_config

        validate_config(copy.deepcopy(starter["config"]), require_systems=True)

    def test_its_structure_is_one_the_docs_use(self, starter):
        system = starter["config"]["systems"][0]["system"]
        assert any(system in page.read_text(encoding="utf-8")
                   for page in DOCS.glob("*.md")), system

    def test_it_says_what_it_is_and_what_to_change(self, starter):
        assert starter["title"] and starter["what"] and starter["change"]
        for said in (starter["title"], starter["what"], starter["change"]):
            assert chr(0x2014) not in said and chr(0x2013) not in said


def test_each_tile_is_given_its_plan() -> None:
    offered = {s["id"]: {line["label"]: line["value"] for line in s["plan"]}
               for s in starters_payload()}
    assert offered["protein"]["Production"] == "10 ns, 2 fs steps"
    assert offered["membrane"]["Membrane"] == "POPC bilayer"
    assert offered["determined"]["Replicas"] == "3, differing only by random seed"
    assert offered["umbrella"]["Sampling"] == (
        "umbrella sampling, 13 windows along ligand_distance from 0.3 to 1.5")
    # Planning a starter leaves the starter as it was.
    assert "umbrella" in STARTERS[-1]["config"]["simulation"]


def test_they_reach_the_page_with_the_schema() -> None:
    from fastmdxplora.gui.schema_payload import schema_payload

    assert [s["id"] for s in schema_payload()["starters"]] == [s["id"] for s in STARTERS]


def test_a_starter_chosen_is_the_study_the_builder_writes(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.config.loader import validate_config
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    written: dict[str, dict] = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#run", wait_until="domcontentloaded")
            page.wait_for_selector("#run-starters .starter")
            tiles = page.locator("#run-starters .starter").count()
            facts = page.text_content('#run-starters .starter[data-starter="membrane"] .starter-facts')
            for starter in STARTERS:
                page.click(f'#run-starters .starter[data-starter="{starter["id"]}"]')
                page.wait_for_function(
                    "(title) => document.getElementById('run-starters-note')"
                    ".textContent.includes(title)", arg=starter["title"])
                built = page.evaluate("() => window.FastMDXRun.fetchConfig()")
                assert built["ok"], built
                written[starter["id"]] = yaml.safe_load(built["yaml"])
            chosen = page.locator("#run-starters .starter.is-chosen").get_attribute("data-starter")
            note = page.text_content("#run-starters-note")
            system = page.input_value("#run-system")
            # Folded, it stays folded.
            page.click("#run-starters-card > summary")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#run-starters .starter", state="attached")
            folded = page.evaluate("() => !document.getElementById('run-starters-card').open")
            browser.close()
    finally:
        session.server.shutdown()
    assert tiles == len(STARTERS)
    assert facts == "1AFO · POPC bilayer · 20 ns, 2 fs steps"
    assert chosen == "umbrella" and system == "181L"
    assert note.startswith('Started from "A free energy along a distance". Change first: ')
    for starter in STARTERS:
        config = written[starter["id"]]
        validate_config(copy.deepcopy(config), require_systems=True)
        for phase, block in starter["config"].items():
            if phase in ("setup",):
                for key, value in block.items():
                    assert config["setup"][key] == value, (starter["id"], key)
    assert written["membrane"]["simulation"]["restrain"] == "protein and not element H"
    assert written["determined"]["simulation"]["stop_when"]["max_duration_ns"] == 50
    assert written["determined"]["sweep"] == {"simulation.random_seed": [1, 2, 3]}
    assert len(written["umbrella"]["systems"]) == 13
    assert folded
    assert errors == []
