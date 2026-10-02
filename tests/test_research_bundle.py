"""Portable research views preserve evidence and require a reviewed import."""
from __future__ import annotations

import io
import json
import shutil
import zipfile
from types import SimpleNamespace

import pytest

from fastmdxplora.gui.research import bookmarks_endpoint, restore_endpoint, screenshot_endpoint
from fastmdxplora.gui.research_bundle import apply_import, export_bundle, preview_import
from tests.test_research_views import png_data, save


@pytest.fixture
def study(tmp_path):
    root = tmp_path / "original"
    (root / "analysis/rmsd").mkdir(parents=True)
    (root / "manifest.json").write_text('{"phases": []}')
    (root / "analysis/rmsd/rmsd.dat").write_text("0 0.1\n1 0.2\n2 0.3\n")
    return SimpleNamespace(active_root=root)


def graph(study, **changes):
    return save(study, view={"page": "analysis", "analysis": "rmsd", "range": [0, 2]},
                tags=["Graph", "Observation"], **changes)


def confirm(runtime, preview, **options):
    return apply_import(runtime, {"study": str(runtime.active_root), "token": preview["token"], **options})


def test_bundle_round_trip_at_a_different_path_is_read_only_until_confirmation(study, tmp_path):
    first = graph(study, screenshot=png_data())
    assert first["ok"]
    raw = export_bundle(study)
    target = tmp_path / "relocated"
    shutil.copytree(study.active_root, target, ignore=shutil.ignore_patterns(".research"))
    fresh = SimpleNamespace(active_root=target)
    preview = preview_import(fresh, raw)
    assert preview["ok"] and preview["compatible"] == 1
    assert preview["bookmarks"][0]["screenshot"]
    assert not (target / ".research").exists()
    answer = confirm(fresh, preview)
    assert answer["ok"] and answer["imported"] == 1
    row = answer["bookmarks"][0]
    assert row["title"] == first["bookmarks"][0]["title"]
    assert row["tags"] == ["Graph", "Observation"]
    assert screenshot_endpoint(fresh, row["id"])["ok"]
    restored = restore_endpoint(fresh, row["id"])
    assert restored["ok"] and restored["view"]["range"] == [0, 2]
    assert restored["view"]["study"] == str(target)
    assert not confirm(fresh, preview)["ok"]  # consumed preview cannot be replayed


def test_json_export_omits_screenshots_paths_and_unrelated_stored_fields(study):
    graph(study, screenshot=png_data())
    path = study.active_root / ".research/bookmarks.json"
    rows = json.loads(path.read_text())
    rows[0]["credentials"] = "must-never-be-exported"
    rows[0]["view"]["action"] = "run"
    rows[0]["view"]["study"] = str(study.active_root)
    path.write_text(json.dumps(rows))
    raw = export_bundle(study, images=False)
    assert b"must-never-be-exported" not in raw
    assert str(study.active_root).encode() not in raw
    value = json.loads(raw)
    assert "screenshot" not in value["bookmarks"][0]
    assert "action" not in value["bookmarks"][0]["view"]
    preview = preview_import(study, raw)
    assert preview["ok"] and not preview["bookmarks"][0]["screenshot"]
    assert confirm(study, preview, duplicates="copy")["imported"] == 1


def test_filtered_export_contains_only_selected_bookmarks(study):
    first = graph(study, title="First observation")["bookmarks"][0]
    graph(study, title="Second observation")
    exported = json.loads(export_bundle(study, images=False, bookmark_ids=[first["id"]]))
    assert [row["title"] for row in exported["bookmarks"]] == ["First observation"]
    with pytest.raises(ValueError, match="selection changed"):
        export_bundle(study, bookmark_ids=["0" * 32])


@pytest.mark.parametrize("mode,count,note", [("copy", 2, "Newer note"), ("skip", 1, "Newer note"), ("replace", 1, "Check this residue")])
def test_duplicate_choice_is_explicit_and_preserves_existing_notes_by_default(study, mode, count, note):
    row = graph(study)["bookmarks"][0]
    exported = export_bundle(study, images=False)
    graph(study, id=row["id"], note="Newer note")
    preview = preview_import(study, exported)
    assert preview["duplicates"] == 1
    assert bookmarks_endpoint(study)["bookmarks"][0]["note"] == "Newer note"
    imported = confirm(study, preview, duplicates=mode)
    assert imported["ok"] and len(imported["bookmarks"]) == count
    assert imported["bookmarks"][0]["note"] == note


def test_changed_data_refuses_restore_but_keeps_notes_and_images(study):
    row = graph(study, screenshot=png_data())["bookmarks"][0]
    assert restore_endpoint(study, row["id"])["ok"]
    (study.active_root / "analysis/rmsd/rmsd.dat").write_text("0 9.9\n1 8.8\n")
    result = restore_endpoint(study, row["id"])
    assert not result["ok"] and "differs" in result["error"]
    assert bookmarks_endpoint(study)["bookmarks"][0]["note"] == row["note"]
    assert screenshot_endpoint(study, row["id"])["ok"]


def test_original_trajectory_changes_invalidate_cached_playback_bookmarks(study):
    simulation = study.active_root / "simulation"
    simulation.mkdir()
    (simulation / "production.dcd").write_bytes(b"original trajectory")
    (simulation / "playback.pdb").write_bytes(b"cached displayed coordinates")
    index = {"source_kind": "production-dcd", "source_signature": "original",
             "frame_indices": [0, 10], "frame_times_ns": [0.1, 1.1]}
    (simulation / "playback_index.json").write_text(json.dumps(index))
    view = {"page": "viewer", "mode": "playback", "frame": 1, "playback_signature": "original"}
    first = save(study, view=view)
    row = first["bookmarks"][0]
    assert restore_endpoint(study, row["id"])["ok"]
    (simulation / "production.dcd").write_bytes(b"changed trajectory")
    assert not restore_endpoint(study, row["id"])["ok"]
    index["source_signature"] = "changed"
    (simulation / "playback_index.json").write_text(json.dumps(index))
    assert not save(study, view=view)["ok"]


def test_a_specific_figure_is_distinct_from_other_figures_of_the_same_analysis(study):
    folder = study.active_root / "analysis/rmsd"
    (folder / "comparison.svg").write_text("<svg>original figure</svg>")
    view = {"page": "report", "analysis": "rmsd", "figure": "analysis/rmsd/comparison.svg"}
    row = save(study, view=view)["bookmarks"][0]
    assert restore_endpoint(study, row["id"])["ok"]
    (folder / "comparison.svg").write_text("<svg>changed figure</svg>")
    assert not restore_endpoint(study, row["id"])["ok"]


def test_preview_is_invalidated_by_a_concurrent_edit_or_study_switch(study, tmp_path):
    row = graph(study)["bookmarks"][0]
    raw = export_bundle(study)
    preview = preview_import(study, raw)
    graph(study, id=row["id"], note="Changed after preview")
    assert "changed after preview" in confirm(study, preview)["error"]
    preview = preview_import(study, raw)
    other = tmp_path / "other"
    other.mkdir()
    (other / "manifest.json").write_text('{"phases": []}')
    study.active_root = other
    assert "study changed" in apply_import(study, {"study": preview["study"], "token": preview["token"]})["error"]
    assert not (other / ".research").exists()


def test_a_study_switch_during_image_import_cannot_write_into_the_new_study(study, tmp_path, monkeypatch):
    import fastmdxplora.gui.research_bundle as bundles

    graph(study, screenshot=png_data())
    raw = export_bundle(study)
    original = study.active_root
    before = bookmarks_endpoint(study)["bookmarks"]
    preview = preview_import(study, raw)
    other = tmp_path / "switched"
    other.mkdir()
    (other / "manifest.json").write_text('{"phases":[]}')
    store = bundles.store_image
    def switch(root, value):
        result = store(root, value)
        study.active_root = other
        return result
    monkeypatch.setattr(bundles, "store_image", switch)
    result = apply_import(study, {"study": str(original), "token": preview["token"]})
    assert not result["ok"] and "changed during import" in result["error"]
    assert not (other / ".research").exists()
    study.active_root = original
    assert bookmarks_endpoint(study)["bookmarks"] == before


def test_legacy_import_keeps_notes_without_claiming_source_compatibility(study):
    legacy = {"version": 1, "study": "C:/private/location", "bookmarks": [{"id": "a" * 32,
        "title": "Legacy observation", "note": "Useful note", "view": {"page": "viewer", "study": "C:/private/location", "action": "run"}}]}
    preview = preview_import(study, json.dumps(legacy).encode())
    assert preview["ok"] and preview["compatible"] == 0
    imported = confirm(study, preview)
    row = imported["bookmarks"][0]
    assert row["view"] == {"page": "viewer"}
    assert not restore_endpoint(study, row["id"])["ok"]


def test_cancel_releases_preview_without_changing_bookmarks(study):
    graph(study)
    before = bookmarks_endpoint(study)["bookmarks"]
    preview = preview_import(study, export_bundle(study))
    assert confirm(study, preview, cancel=True)["cancelled"]
    assert study._research_import is None
    assert bookmarks_endpoint(study)["bookmarks"] == before


@pytest.mark.parametrize("raw", [b"not JSON", b'{"version": 99}', b'{"version": 2, "bookmarks": []}', b'{"version":2,"bookmarks":[null]}'])
def test_malformed_import_does_not_write_anything(study, raw):
    assert not preview_import(study, raw)["ok"]
    assert not (study.active_root / ".research").exists()


@pytest.mark.parametrize("filename", ["../escape.png", "/absolute/path", "screenshots/../../../escape.png", "other.txt"])
def test_zip_paths_are_rejected_without_extraction(study, filename):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bookmarks.json", '{"version":2,"bookmarks":[]}')
        archive.writestr(filename, b"unsafe")
    assert not preview_import(study, buffer.getvalue())["ok"]
    assert not (study.active_root / ".research").exists()


def test_missing_and_tampered_screenshots_are_rejected(study):
    graph(study, screenshot=png_data())
    original = export_bundle(study)
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        metadata = archive.read("bookmarks.json")
        image = next(name for name in archive.namelist() if name.endswith(".png"))
    assert not preview_import(study, metadata)["ok"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bookmarks.json", metadata)
        archive.writestr(image, b"changed screenshot")
    assert not preview_import(study, buffer.getvalue())["ok"]


def test_expired_preview_and_oversized_input_are_refused(study, monkeypatch):
    import fastmdxplora.gui.research_bundle as bundles

    graph(study)
    preview = preview_import(study, export_bundle(study))
    study._research_import["expires"] = 0
    assert "expired" in confirm(study, preview)["error"]
    monkeypatch.setattr(bundles, "MAX_BUNDLE_BYTES", 100)
    assert not preview_import(study, b"x" * 101)["ok"]
