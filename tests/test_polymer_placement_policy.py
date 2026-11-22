"""Polymer lattice placement is a choice, independent of making bonds whole."""

from __future__ import annotations

import json

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis import loading


def _stored(n=2, *, split=False, ligand=False):
    top = md.Topology()
    xyz = []
    for i in range(n):
        residue = top.add_residue("ALA", top.add_chain())
        a = top.add_atom("CA", md.element.carbon, residue)
        b = top.add_atom("C", md.element.carbon, residue)
        top.add_bond(a, b)
        x = 0.0 if i == 0 else 9.8
        xyz.extend([[x, i * 0.01, 0], [x + 0.1, i * 0.01, 0]])
    if ligand:
        residue = top.add_residue("LIG", top.add_chain())
        a = top.add_atom("C1", md.element.carbon, residue)
        b = top.add_atom("C2", md.element.carbon, residue)
        top.add_bond(a, b)
        xyz.extend([[29.8, 0, 0], [29.9, 0, 0]])
        top.add_atom("NA", md.element.sodium, top.add_residue("NA", top.add_chain()))
        xyz.append([30, 0, 0])
    frames = np.repeat(np.asarray(xyz, dtype=np.float32)[None], 3, axis=0)
    if split:
        frames[1, 3, 0] -= 10
    result = md.Trajectory(frames, top)
    result.unitcell_vectors = np.repeat((np.eye(3) * 10)[None], 3, axis=0)
    return result


def _placement(trajectory):
    return getattr(trajectory, loading.POLYMER_PLACEMENT)


@pytest.mark.parametrize("n", [2, 33])
def test_default_relocates_ten_nm_and_records_only_the_lattice_move(n, caplog):
    original = _stored(n)
    expected = original.image_molecules(
        inplace=False, anchor_molecules=original.topology.find_molecules(), other_molecules=[])
    result = loading._made_whole(original[:], source_frames=[4, 8, 12])
    np.testing.assert_allclose(result.xyz, expected.xyz, atol=2e-6)
    relative_move = (result.xyz[:, 2] - result.xyz[:, 0]
                     - (original.xyz[:, 2] - original.xyz[:, 0]))
    np.testing.assert_allclose(relative_move, [[-10, 0, 0]] * 3, atol=2e-6)
    record = _placement(result)
    assert record["preserve_polymer_placement"] is False
    assert len(record["relocations"]) == n - 1
    assert record["relocations"][0]["source_frames"] == [4, 8, 12]
    assert record["relocations"][0]["relative_lattice_shift"] == [-1, 0, 0]
    assert record["relocations"][0]["chains"] == [1]
    assert caplog.text.count("Polymer assembly placement changed") == 1
    assert "--preserve-polymer-placement" in record["warning"]
    assert "does not establish" in record["warning"]


@pytest.mark.parametrize("n", [2, 33])
def test_preservation_keeps_relative_roots_but_repairs_split_bonds(n, caplog):
    original = _stored(n, split=True)
    result = loading._made_whole(original[:], preserve_polymer_placement=True)
    roots = np.arange(0, 2 * n, 2)
    np.testing.assert_allclose(
        result.xyz[:, roots] - result.xyz[:, :1],
        original.xyz[:, roots] - original.xyz[:, :1], atol=2e-6)
    assert np.linalg.norm(result.xyz[1, 3] - result.xyz[1, 2]) == pytest.approx(0.1, abs=2e-6)
    assert _placement(result)["relocations"] == []
    assert "assembly placement changed" not in caplog.text


@pytest.mark.parametrize("preserve", [False, True])
def test_ligand_and_ion_still_take_the_nearest_copy(preserve):
    result = loading._made_whole(_stored(ligand=True), preserve_polymer_placement=preserve)
    centre = result.xyz[:, :4].mean(axis=1)
    for indices in ([4, 5], [6]):
        offset = result.xyz[:, indices].mean(axis=1) - centre
        assert np.all(np.abs(offset) <= 5.00001)
    assert np.allclose(np.linalg.norm(result.xyz[:, 4] - result.xyz[:, 5], axis=1), 0.1,
                       atol=2e-6)


def test_no_cell_and_no_polymer_keep_the_existing_behavior():
    no_cell = _stored()
    no_cell.unitcell_vectors = None
    original = no_cell.xyz.copy()
    assert loading._made_whole(no_cell, preserve_polymer_placement=True) is no_cell
    np.testing.assert_array_equal(no_cell.xyz, original)
    for name in ("LIG", "HOH"):
        control = _stored()
        for residue in control.topology.residues:
            residue.name = name
        default = loading._made_whole(control[:])
        preserved = loading._made_whole(control[:], preserve_polymer_placement=True)
        np.testing.assert_array_equal(default.xyz, preserved.xyz)


def _study(root):
    simulation = root / "simulation"
    simulation.mkdir(parents=True)
    stored = _stored()
    stored[0].save_pdb(str(simulation / "trajectory_topology.pdb"))
    stored.save_dcd(str(simulation / "production.dcd"))
    (simulation / "live_status.json").write_text('{"status":"completed"}', encoding="utf-8")
    return root


def _policy(root, preserve):
    (root / "resolved_config.yml").write_text(
        f"analysis:\n  preserve_polymer_placement: {str(preserve).lower()}\n", encoding="utf-8")


def test_loading_trimming_copies_subsets_and_reference_keep_the_policy(tmp_path):
    from fastmdxplora.analysis.base import carry_wholeness

    root = _study(tmp_path / "study")
    result = loading.load_trajectory(
        root / "simulation/production.dcd", root / "simulation/trajectory_topology.pdb",
        preserve_polymer_placement=True, stride=2, first=1, last=2)
    assert result.n_frames == 1
    assert _placement(result)["preserve_polymer_placement"] is True
    subset = carry_wholeness(result, result.atom_slice([0, 1, 2, 3]))
    reference = loading.image_whole(subset, inplace=False)
    assert _placement(reference)["preserve_polymer_placement"] is True
    np.testing.assert_allclose(reference.xyz[:, 2] - reference.xyz[:, 0], [[9.8, .01, 0]],
                               atol=2e-6)


def test_playback_exact_frames_and_alternate_runs_invalidate_on_policy_change(tmp_path):
    from fastmdxplora.gui import runs_together
    from fastmdxplora.gui import trajectory_frames as tf

    root = _study(tmp_path / "study")
    before = tf.frames_info(root)
    assert before["polymer_placement"]["relocations"]
    _policy(root, True)
    after = tf.frames_info(root)
    assert before["signature"] != after["signature"]
    assert after["polymer_placement"]["preserve_polymer_placement"] is True
    exact = tf._source_frame(root, tf._source(root), 1)
    source = tf._source(root)
    names, _, _ = runs_together._shown_atoms(source["topology"])
    alternate, why = runs_together._frames_of(source, names, [0, 1])
    assert why is None
    for frames in (exact, alternate, md.load_dcd(
            str(root / "simulation/frames.dcd"), top=str(root / "simulation/frames_topology.pdb"))):
        np.testing.assert_allclose(frames.xyz[:, 2] - frames.xyz[:, 0],
                                   [[9.8, .01, 0]] * frames.n_frames, atol=2e-6)


def test_live_cache_and_history_use_the_current_policy(tmp_path):
    from fastmdxplora.gui import live_frames as lf
    from fastmdxplora.gui import trajectory_frames as tf

    root = _study(tmp_path / "study")
    simulation = root / "simulation"
    text = (simulation / "trajectory_topology.pdb").read_text(encoding="utf-8")
    lf.write_live_frame(simulation, pdb_text=text, archive=True, frame_index=1)
    lf.write_live_frame(simulation, pdb_text=text, archive=True, frame_index=2)
    before = lf.live_frame_text(simulation)
    _policy(root, True)
    after = lf.live_frame_text(simulation)
    assert before != after
    def xyz(pdb):
        return np.asarray([[float(line[k:k + 8]) for k in (30, 38, 46)]
                           for line in pdb.splitlines() if line.startswith("ATOM")])

    np.testing.assert_allclose(xyz(after)[2] - xyz(after)[0], [98, .1, 0], atol=.002)
    source = dict(tf._source(root), kind="live-history",
                  records=lf.read_live_frame_history(simulation)["frames"])
    history = tf._from_history(source, simulation, 20)
    assert history["polymer_placement"]["preserve_polymer_placement"] is True


def test_schema_cli_options_reach_the_real_loader(tmp_path, monkeypatch):
    import importlib
    from types import SimpleNamespace

    from fastmdxplora.analysis import analyze
    from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator, available_analyses

    cli = importlib.import_module("fastmdxplora.cli.main")

    root = _study(tmp_path / "study")
    received = []
    original = AnalysisOrchestrator.run

    def observe(self, **kwargs):
        received.append(self)
        return original(self, exclude=available_analyses())

    monkeypatch.setattr(AnalysisOrchestrator, "run", observe)

    def dispatch(args):
        # Keep the actual CLI's phase remapping and option harvesting;
        # replace only the outer batch runner (no setup/MD or providers).
        config = cli._build_explore_config(args)
        from fastmdxplora.config.loader import validate_config

        validate_config(config)
        analyze.run(orchestrator=SimpleNamespace(output_dir=root, presenter=None),
                    output_dir=root / "analysis", **config["analysis"])
        return 0

    monkeypatch.setattr(cli, "_cmd_explore", dispatch)
    for command, flag in (("analyze", "--preserve-polymer-placement"),
                          ("explore", "--analyze-preserve-polymer-placement")):
        assert cli.main([command, "--output", str(root), flag]) == 0
    assert len(received) == 2
    assert _placement(received[0].traj)["preserve_polymer_placement"] is True
    manifest = json.loads((root / "analysis/analysis_manifest.json").read_text(encoding="utf-8"))
    assert manifest["load_kwargs"]["preserve_polymer_placement"] is True
    assert manifest["resolved"]["preserve_polymer_placement"] is True


def test_an_analysis_records_the_policy_and_the_manifest_the_relocations(tmp_path):
    from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

    root = _study(tmp_path / "study")
    run = AnalysisOrchestrator(
        root / "simulation/production.dcd", root / "simulation/trajectory_topology.pdb",
        output_dir=root / "analysis")
    run.run(include=["rg"])
    options = json.loads((root / "analysis/rg/options.json").read_text(encoding="utf-8"))
    finding = options["findings"]["polymer_placement"]
    assert finding["preserve_polymer_placement"] is False
    assert "relocated" in finding["summary"]
    assert "warning" not in finding
    manifest = json.loads((root / "analysis/analysis_manifest.json").read_text(encoding="utf-8"))
    assert manifest["polymer_placement"]["relocations"][0]["relative_lattice_shift"] == [-1, 0, 0]


def test_chromium_observes_the_warning_and_policy_change(tmp_path):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _study(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page()
            page.goto(session.url.rstrip("/") + "/#viewer", wait_until="domcontentloaded")
            warning = page.locator("#dashboard-toast .dashboard-toast-said")
            warning.wait_for(state="visible", timeout=30000)
            assert "Polymer assembly placement changed" in warning.inner_text()
            assert "--preserve-polymer-placement" in warning.inner_text()
            before = page.evaluate("fetch('/api/frames-info').then(r => r.json())")
            _policy(root, True)
            after = page.evaluate("fetch('/api/frames-info').then(r => r.json())")
            assert before["source_signature"] != after["source_signature"]
            assert after["polymer_placement"]["relocations"] == []
            assert after["polymer_placement"]["preserve_polymer_placement"] is True
            browser.close()
    finally:
        session.server.shutdown()
