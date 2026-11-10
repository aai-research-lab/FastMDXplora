"""The GUI's Analyze again offers a biased study's analyses alone.

A metadynamics study's analysis manifest lists the reweighting pass
(`reweighted`) among its results. Analyze again offered the results ticked,
and confirming was refused ("'reweighted' is not an analysis this release
has"); the Agent's offer named it too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from test_a_biased_study_reads_its_own_record import biased  # noqa: E402,F401


def test_the_page_offers_the_analyses_ticked_and_they_run(biased):  # noqa: F811
    from fastmdxplora import again
    from fastmdxplora.gui.server import _again_answer

    manifest = json.loads((biased / "analysis" / "analysis_manifest.json").read_text())
    assert "reweighted" in manifest["results"]
    offered = _again_answer(biased)
    assert offered["recorded"] == ["rmsd", "rg"], offered
    planned = again.plan(biased, ["analysis"], offered["recorded"])
    assert planned.analyses == ("rmsd", "rg")


def test_the_agent_s_offer_names_the_analyses_alone(biased):  # noqa: F811
    from fastmdxplora.gui.again_view import again_fix

    said = again_fix(biased, "analyze again", {})
    assert "fix" in said, said
    assert said["fix"]["fix"].startswith("Analyze this study again with rmsd, rg,")
    assert "reweighted" not in said["fix"]["fix"]


def test_a_record_saved_on_main_offers_them_too(biased):  # noqa: F811
    """No manifest read: the analyses the record asked for, as main saved
    it with the pass among them."""
    from fastmdxplora import again

    path = biased / "resolved_config.yml"
    saved = yaml.safe_load(path.read_text())
    saved["analysis"]["include"] = ["rmsd", "rg", "reweighted"]
    path.write_text(yaml.safe_dump(saved, sort_keys=False))
    (biased / "analysis" / "analysis_manifest.json").unlink()
    assert again.recorded_analyses(biased) == ["rmsd", "rg"]


@pytest.mark.parametrize("plan", [["rmsd", "reweighted"], ["rmsd"]])
def test_the_workspace_says_the_analyses_that_ran_alone(tmp_path, plan):
    from fastmdxplora.gui.workspace import _ran_analyses

    (tmp_path / "analysis").mkdir()
    (tmp_path / "analysis" / "analysis_manifest.json").write_text(json.dumps({"plan": plan}))
    (tmp_path / "manifest.json").write_text(json.dumps({"phases": [{"name": "analysis"}]}))
    assert _ran_analyses(tmp_path) == ["rmsd"]
