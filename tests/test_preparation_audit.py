import hashlib
import json

import numpy as np

from fastmdxplora.gui.preparation_audit import audit_payload, comparison_payload, selection_evidence


def structure(points):
    return (
        "".join(
            f"ATOM  {i:5d} {name:^4s} ALA A   1    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00           C\n"
            for i, (name, (x, y, z)) in enumerate(zip(("N", "CA", "C", "CB"), points), 1)
        )
        + "END\n"
    )


def study(tmp_path):
    folder = tmp_path / "setup"
    folder.mkdir()
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    (folder / "input.pdb").write_text(structure(points), encoding="utf-8")
    (folder / "prepared.pdb").write_text(structure(points + [4, 7, 2]), encoding="utf-8")
    return folder, points


def test_historical_inventory_does_not_invent_operations_and_alignment_changes_no_files(tmp_path):
    folder, points = study(tmp_path)
    original = {path.name: path.read_bytes() for path in folder.iterdir()}
    payload = audit_payload(tmp_path)
    assert not payload["recorded"]
    assert payload["sources"]["input"]["counts"]["total"] == 4
    assert {"view-input", "view-prepared"} <= {row["id"] for row in payload["changes"]}
    alignment = comparison_payload(tmp_path, "input", "prepared")
    assert alignment["ok"] and alignment["matched_atoms"] == 4
    np.testing.assert_allclose(
        (points + [4, 7, 2]) @ alignment["rotation"] + alignment["translation"], points, atol=1e-10
    )
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == original
    found = selection_evidence(
        tmp_path, "input", {"chain": "A", "resseq": 1, "resname": "ALA", "atom": "CA"}
    )
    assert found["available"] and len(found["atoms"]) == 1
    assert not selection_evidence(
        tmp_path, "input", {"chain": "A", "resseq": 999, "resname": "ALA"}
    )["available"]


def test_duplicate_identity_refuses_alignment_and_selection(tmp_path):
    folder, _ = study(tmp_path)
    with (folder / "input.pdb").open("a") as stream:
        stream.write((folder / "input.pdb").read_text().splitlines()[0] + "\n")
    assert not comparison_payload(tmp_path, "input", "prepared")["ok"]
    assert not selection_evidence(tmp_path, "input", {"chain": "A", "resseq": 1, "resname": "ALA"})[
        "available"
    ]


def test_ligand_inventory_requires_saved_parameterization_names(tmp_path):
    folder, _ = study(tmp_path)
    text = (folder / "prepared.pdb").read_text().replace("ALA", "BEN")
    (folder / "prepared.pdb").write_text(text)
    payload = audit_payload(tmp_path)
    assert payload["sources"]["prepared"]["counts"]["ligand"] == 0
    assert payload["sources"]["prepared"]["counts"]["other"] == 4
    (folder / "setup_parameters.json").write_text('{"resolved_forcefield":{"ligand":{"name":"BEN"}}}')
    payload = audit_payload(tmp_path)
    assert payload["sources"]["prepared"]["counts"]["ligand"] == 4
    assert payload["sources"]["prepared"]["counts"]["other"] == 0


def test_recorded_stage_track_preserves_insertion_identity_and_reason(tmp_path):
    folder, _ = study(tmp_path)
    before = folder / "audit/run/before.pdb"
    after = folder / "audit/run/after.pdb"
    before.parent.mkdir(parents=True)
    before.write_text((folder / "input.pdb").read_text())
    # Remove one atom in the saved after stage; do not label it a repair cause.
    after.write_text("\n".join(before.read_text().splitlines()[1:]) + "\n")
    journal = {"version": 1, "status": "complete", "sources": {
        "before": {"label": "Before", "snapshot": "setup/audit/run/before.pdb", "sha256": hashlib.sha256(before.read_bytes()).hexdigest()},
        "after": {"label": "After", "snapshot": "setup/audit/run/after.pdb", "sha256": hashlib.sha256(after.read_bytes()).hexdigest()}},
        "events": [{"id": "repair", "operation": "missing_atom_repair", "before": "before", "after": "after",
                    "details": {"requested": {"ph": 6.5}}, "reason": "Fixture recorded reason", "evidence": "Recorded at operation"}]}
    (folder / "preparation_audit.json").write_text(json.dumps(journal))
    payload = audit_payload(tmp_path)
    assert payload["decisions"][0]["requested"] == {"ph": 6.5}
    assert payload["decisions"][0]["reason"] == "Fixture recorded reason"
    affected = payload["affected_residues"][0]
    assert affected["category"] == "Repair stage" and not affected["ambiguous"]
    assert affected["selection"]["chain"] == "A"
    assert "difference alone does not establish its cause" in affected["evidence"]
    assert affected["event_id"] in {row["id"] for row in payload["changes"]}


def test_snapshot_checksums_and_corrupt_journal_are_visible(tmp_path):
    folder, _ = study(tmp_path)
    snapshot = folder / "audit" / "run" / "source.pdb"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes((folder / "input.pdb").read_bytes())
    journal = {
        "version": 1,
        "status": "partial",
        "events": [],
        "sources": {
            "source-0001": {
                "label": "Before repair",
                "snapshot": "setup/audit/run/source.pdb",
                "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            }
        },
    }
    (folder / "preparation_audit.json").write_text(json.dumps(journal))
    assert audit_payload(tmp_path)["sources"]["source-0001"].get("url")
    snapshot.write_text("END\n")
    assert "checksum" in audit_payload(tmp_path)["sources"]["source-0001"]["unavailable"]
    (folder / "preparation_audit.json").write_text("{broken")
    payload = audit_payload(tmp_path)
    assert not payload["recorded"] and payload["warnings"]


def test_snapshot_paths_cannot_escape_study(tmp_path):
    folder, _ = study(tmp_path)
    journal = {
        "version": 1,
        "events": [],
        "sources": {"escape": {"snapshot": "setup/audit/../../../../secret.pdb"}},
    }
    (folder / "preparation_audit.json").write_text(json.dumps(journal))
    assert not audit_payload(tmp_path)["sources"]["escape"].get("url")


def test_agent_context_and_bookmark_identity_use_the_actual_audit_stage(tmp_path):
    from fastmdxplora.gui.research import clean_view, context_for
    from fastmdxplora.gui.research_sources import compatible, source_for

    folder, _ = study(tmp_path)
    view = clean_view(
        {
            "page": "overview",
            "audit_event": "view-input",
            "audit_source": "input",
            "audit_selection": {"chain": "A", "resname": "ALA", "resseq": 1, "atom": "CA"},
            "audit_display": {"before": "input", "after": "prepared", "overlay": False},
        }
    )
    text = context_for(tmp_path, view)
    assert '"recorded_operations": false' in text
    assert '"available": true' in text and "saved preparation stage" in text
    row = {"view": view, "source": source_for(tmp_path, view)}
    assert compatible(tmp_path, row)[0]
    (folder / "prepared.pdb").write_text("END\n")
    assert not compatible(tmp_path, row)[0]


def test_browser_restores_audit_selection_without_modifying_structures(tmp_path):
    import pytest

    from fastmdxplora.gui.server import start_test_server

    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    folder, _ = study(tmp_path)
    (tmp_path / "manifest.json").write_text('{"phases": []}')
    original = {path.name: path.read_bytes() for path in folder.iterdir()}
    server, url = start_test_server(tmp_path)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url + "/#overview")
            page.wait_for_function("window.FastMDXDashboard?.state.appState.active_run")
            page.locator("#preparation-audit-panel > summary").click()
            page.wait_for_function(
                "document.querySelector('#preparation-after-title').textContent "
                "=== 'Prepared solute'"
            )
            page.locator("#preparation-overlay").check()
            assert page.locator(".preparation-component").count() == 10
            assert "Force field" in page.locator("#preparation-decision-rows").inner_text()
            page.wait_for_function(
                "document.querySelector('#preparation-alignment').textContent"
                ".includes('4 exact heavy-atom matches')"
            )
            page.locator("#preparation-event").select_option("view-prepared")
            page.wait_for_function("FastMDXResearch.capture().audit_source === 'prepared'")
            page.locator("#preparation-ask").click()
            assert (
                "Explain this saved preparation change"
                in page.locator("#agent-request").input_value()
            )
            page.evaluate("""async () => {
                await FastMDXResearch.restore({page:'overview',audit_event:'view-input',
                  audit_source:'input',audit_selection:{chain:'A',resseq:1,resname:'ALA',atom:'CA'}});
            }""")
            view = page.evaluate("FastMDXResearch.capture()")
            assert view["audit_source"] == "input" and view["audit_selection"]["atom"] == "CA"
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == original
