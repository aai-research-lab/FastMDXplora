"""Review binds the precise draft, builder baseline, study and purpose."""
from types import SimpleNamespace

import pytest

from fastmdxplora.gui.draft_review import review_endpoint, verify_run_review


@pytest.fixture
def draft(tmp_path):
    runtime = SimpleNamespace(active_root=tmp_path)
    payload = {"config": {"agent": "assisted", "systems": [{"id": "proposed", "system": "1UAO"}], "setup": {"ph": 6.5}},
               "builder_state": {"system": "1UAO", "setup": {"ph": 7.4}, "include_phase": ["setup", "simulation"]},
               "study": str(tmp_path)}
    return runtime, payload


def test_review_reports_field_differences_without_mutating_source(draft):
    runtime, payload = draft
    result = review_endpoint(payload, runtime)
    assert result["ok"]
    ph = next(row for row in result["changes"] if row["field"] == "setup.ph")
    assert ph["before"] == 7.4 and ph["after"] == 6.5
    assert ph["help"] and ph["has_schema"]
    assert "scientific suitability" in result["notice"]
    assert not list(runtime.active_root.iterdir())
    accepted = review_endpoint({**payload, "action": "accept", "confirmed": True, "review_token": result["review_token"]}, runtime)
    assert accepted["ok"] and accepted["state"]["phases"]["setup"]["ph"] == 6.5
    assert accepted["state"]["study"]["agent"] == "assisted"


@pytest.mark.parametrize("change", ["draft", "baseline", "study", "source", "confirmation", "purpose"])
def test_review_rejects_changed_state_or_missing_confirmation(draft, change):
    runtime, payload = draft
    receipt = review_endpoint(payload, runtime)
    accepted = {**payload, "action": "accept", "confirmed": True, "review_token": receipt["review_token"]}
    if change == "draft":
        payload["config"]["setup"]["ph"] = 8
    elif change == "baseline":
        payload["builder_state"]["setup"]["ph"] = 8
    elif change == "study":
        runtime.active_root = runtime.active_root / "other"
    elif change == "source":
        (runtime.active_root / "setup").mkdir()
        (runtime.active_root / "setup/setup_parameters.json").write_text('{"parameters":{"ph":8}}')
    elif change == "confirmation":
        accepted["confirmed"] = False
    else:
        accepted["purpose"] = "run"
    assert not review_endpoint(accepted, runtime)["ok"]


def test_final_agent_run_review_requires_exact_state_and_expires(draft, monkeypatch):
    import fastmdxplora.gui.draft_review as module

    runtime, _ = draft
    state = {"system": "1UAO", "include_phase": ["setup", "simulation"],
             "setup": {"ph": 6.5}, "study": {"agent": "assisted"}}
    assert verify_run_review(state, runtime)["code"] == "config.option.not_permitted"
    receipt = review_endpoint({"purpose": "run", "builder_state": state}, runtime)
    assert receipt["ok"]
    approved = {**state, "review_confirmed": True, "review_token": receipt["review_token"]}
    assert verify_run_review(approved, runtime) is None
    assert verify_run_review({**approved, "output": "different-output"}, runtime)
    stamp = int(receipt["review_token"].split(".")[0])
    monkeypatch.setattr(module.time, "time", lambda: stamp + 1)
    assert "expired" in verify_run_review(approved, runtime)["error"]
    assert verify_run_review({"system": "1UAO"}, runtime) is None
