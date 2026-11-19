"""The public config route and existing file/image boundaries reach the output."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import mdtraj as md
import numpy as np
import pytest
import yaml

import fastmdxplora
from fastmdxplora.analysis.dimred import DimRed
from fastmdxplora.analysis.loading import load_trajectory
from fastmdxplora.batch.sweep import expand_runs, normalize_sweep, normalize_systems
from fastmdxplora.config.defaults_file import read_defaults
from fastmdxplora.config.loader import ConfigError, load_config_file, phase_options, validate_config
from fastmdxplora.mcp.tools import ToolError, _confined
from fastmdxplora.refusals import StudyError
from fastmdxplora.remote.inputs import gather_inputs


def _write_ensembles(folder: Path, *, offset: float = 1.0) -> tuple[Path, Path, Path, Path]:
    rng = np.random.default_rng(102)
    base = rng.normal(size=(8, 3)).astype(np.float32)
    base -= base.mean(axis=0)
    mode = rng.normal(size=(8, 3)).astype(np.float32)
    mode -= mode.mean(axis=0)
    mode /= np.linalg.norm(mode)
    mode2 = rng.normal(size=(8, 3)).astype(np.float32)
    mode2 -= mode2.mean(axis=0)
    mode2 -= np.sum(mode * mode2) * mode
    mode2 /= np.linalg.norm(mode2)
    frames = [base + q * mode + r * mode2
              for q, r in zip(np.linspace(-0.5, 0.5, 12), np.sin(np.arange(12)) * 0.2)]
    topology = md.Topology()
    chain = topology.add_chain("A")
    for i in range(8):
        topology.add_atom("CA", md.element.carbon,
                          topology.add_residue("ALA", chain, resSeq=i + 1))
    reference = md.Trajectory(np.asarray(frames, dtype=np.float32), topology)
    target = md.Trajectory(np.asarray(frames, dtype=np.float32) + offset * mode, topology)
    wt_pdb, lig_pdb = folder / "wt.pdb", folder / "ligand.pdb"
    wt_xtc, lig_xtc = folder / "wt.xtc", folder / "ligand.xtc"
    reference[0].save_pdb(str(wt_pdb))
    target[0].save_pdb(str(lig_pdb))
    reference.save_xtc(str(wt_xtc))
    target.save_xtc(str(lig_xtc))
    return wt_pdb, wt_xtc, lig_pdb, lig_xtc


def _run_cli(args, cwd):
    env = dict(os.environ, PYTHONPATH=str(Path(fastmdxplora.__file__).resolve().parents[1]))
    completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False, env=env)
    if os.name == "nt" and completed.returncode and "os.fchmod" in completed.stderr:
        pytest.skip("native Windows os.fchmod crash precedes analysis")
    return completed


def _check_target_files(root, trajectory, topology):
    record = json.loads((root / "analysis" / "dimred" / "options.json").read_text())
    comparison = record["findings"]["comparison"]
    assert comparison["histogrammed_frames"] == [12, 12]
    source, = comparison["target_sources"]
    for key, expected in (("trajectory", trajectory), ("topology", topology)):
        assert Path(source[key]["path"]) == expected.resolve()
        assert source[key]["sha256"] == hashlib.sha256(expected.read_bytes()).hexdigest()


@pytest.mark.parametrize("origin", ["file", "sweep", "flag"])
def test_documented_config_runs_and_existing_views_find_a_static_figure(
        tmp_path, monkeypatch, origin):
    project, launch = tmp_path / "project", tmp_path / "launch"
    project.mkdir()
    launch.mkdir()
    wt_pdb, wt_xtc, lig_pdb, lig_xtc = _write_ensembles(project)
    _, _, decoy_pdb, decoy_xtc = _write_ensembles(launch, offset=-1.0)
    config = project / "common.yml"
    config.write_text(
        f"""systems:
  - system: {wt_pdb.as_posix()}
output: common-study
include_phase: [analysis]
analysis:
  trajectory: {wt_xtc.as_posix()}
  topology: {wt_pdb.as_posix()}
  include: [dimred]
  options:
    dimred:
      methods: [pca]
      n_components: 2
      selection: protein and name CA
      landscape_bins: 12
      comparison:
        reference: {{label: WT, temperature_K: 300, sampling: unknown}}
        targets:
          - label: ligand
            trajectory: ligand.xtc
            topology: ligand.pdb
            first: 0
            stride: 1
            temperature_K: 300
            sampling: unknown
""", encoding="utf-8")
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    options = raw["analysis"]["options"]
    args = [sys.executable, "-m", "fastmdxplora", "analyze", "--config", str(config)]
    if origin != "file":
        raw["analysis"].pop("options")
        other = {"dimred": dict(options["dimred"], landscape_bins=13)}
        raw["sweep"] = {"analysis.options": [options, other]}
        config.write_text(yaml.safe_dump(raw), encoding="utf-8")
        if origin == "flag":
            args[3] = "explore"
            args.extend(["--sweep", f"analysis.options={json.dumps([options, other])}"])
    completed = _run_cli(args, launch)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    root = launch / "common-study"
    trajectory, topology = (decoy_xtc, decoy_pdb) if origin == "flag" else (lig_xtc, lig_pdb)
    if origin != "file":
        runs = sorted(path.parents[2] for path in root.rglob("analysis/dimred/options.json"))
        assert [run.name for run in runs] == [
            "s1__options-comparison-1", "s1__options-comparison-2",
        ]
        for run in runs:
            _check_target_files(run, trajectory, topology)
        root = runs[0]
    comparison = root / "analysis" / "dimred" / "common_pca_landscapes.png"
    assert comparison.is_file()
    assert comparison.stat().st_size > 10_000
    manifest = json.loads((root / "analysis" / "analysis_manifest.json").read_text())
    assert manifest["results"]["dimred"]["status"] == "ok"
    _check_target_files(root, trajectory, topology)
    assert lig_xtc.read_bytes() != decoy_xtc.read_bytes()
    assert lig_pdb.read_bytes() != decoy_pdb.read_bytes()

    monkeypatch.chdir(launch)
    reference = load_trajectory(wt_xtc, wt_pdb)
    options = options["dimred"]
    direct = DimRed(**options).compute(reference)
    options["comparison"]["targets"][0].update(trajectory=str(lig_xtc), topology=str(lig_pdb))
    absolute = DimRed(**options).compute(reference)
    table = np.loadtxt(comparison.with_name("common_pca_projections.dat"), delimiter=",",
                       skiprows=1, usecols=(4, 5))
    expected, decoy = (direct, absolute) if origin == "flag" else (absolute, direct)
    np.testing.assert_allclose(table, np.concatenate(expected["projections"]), atol=2e-5)
    assert not np.allclose(table[12:], decoy["projections"][1], atol=2e-5)
    if origin != "file":
        return
    from fastmdxplora.gui.figure_data import _projection
    from fastmdxplora.gui.report_dashboard import build_dashboard

    assert _projection(root, "pca") is None  # no reference-only frame links
    build_dashboard(orchestrator=SimpleNamespace(output_dir=root, system="fixture"),
                    output_dir=root / "report", title="Common comparison")
    assert "analysis/dimred/common_pca_landscapes.png" in (
        root / "report" / "dashboard.html").read_text(encoding="utf-8")


def _relative_study(folder):
    wt_pdb, wt_xtc, lig_pdb, lig_xtc = _write_ensembles(folder)
    targets = [{"label": "ligand", "trajectory": lig_xtc.name, "topology": lig_pdb.name}]
    options = {"dimred": {"comparison": {"reference": {"label": "WT"}, "targets": targets}}}
    return {
        "systems": [{"system": str(wt_pdb)}], "include_phase": ["analysis"],
        "analysis": {"trajectory": str(wt_xtc), "topology": str(wt_pdb),
                     "include": ["dimred"], "options": options},
    }


def _reopen_in_builder(source, saved):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(saved.parent), host="127.0.0.1", port=0)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(session.url, wait_until="domcontentloaded")
            page.wait_for_function(
                "window.FastMDXRun && window.FastMDXRun.state.schema", timeout=30_000)

            def opened(path):
                return page.evaluate("""async (path) => {
                    const response = await fetch('/api/load-config', {method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({path})});
                    const loaded = await response.json();
                    if (!loaded.ok) return loaded;
                    const run = window.FastMDXRun;
                    run.applyLoadedState(loaded.state, {from: path});
                    return {built: await run.fetchConfig(), state: run.currentState()};
                }""", str(path))

            built = opened(source)["built"]
            assert built["ok"], built
            saved.write_text(built["yaml"], encoding="utf-8")
            reopened = opened(saved)
            assert reopened["built"]["ok"], reopened
            browser.close()
            return reopened["state"]
    finally:
        session.server.shutdown()


@pytest.mark.parametrize("entry", ["mcp", "builder", "inline"])
def test_emitted_run_keeps_the_target_origin(tmp_path, entry):
    from fastmdxplora.gui.run_from_config import prepare_run
    from fastmdxplora.mcp.tools import Context, _check_study, _config_from
    from fastmdxplora.mcp.workspace import Workspace

    project, launch = tmp_path / "project" / "nested", tmp_path / "launch"
    project.mkdir(parents=True)
    launch.mkdir()
    raw = _relative_study(project)
    _write_ensembles(launch, offset=-1.0)
    source = project / "common.yml"
    source.write_text(yaml.safe_dump(raw), encoding="utf-8")
    context = Context(Workspace.at(tmp_path))
    output = launch / "results"
    if entry == "builder":
        prepared = prepare_run(_reopen_in_builder(source, launch / "saved.yml"), output,
                               cwd=launch)
    else:
        name = source.relative_to(tmp_path).as_posix()
        if entry == "mcp":
            assert "plan_id:" in _check_study(context, {"config": name})
            config, actual = _config_from(context, name)
            assert actual == source
            _confined(context, config)
        else:
            config, actual = _config_from(context, source.read_text(encoding="utf-8"))
            assert actual is None and config == raw
        prepared = prepare_run(None, output, config=config, cwd=launch)
    assert prepared["ok"], prepared
    ran = _run_cli(prepared["command"], launch)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    target_folder = launch if entry == "inline" else project
    _check_target_files(output, target_folder / "ligand.xtc", target_folder / "ligand.pdb")
    if entry == "mcp":
        raw["analysis"]["options"]["dimred"]["comparison"]["targets"][0][
            "trajectory"] = "../../../outside.xtc"
        source.write_text(yaml.safe_dump(raw), encoding="utf-8")
        with pytest.raises(ToolError, match="outside the workspace"):
            _check_study(context, {"config": name})


def test_folder_defaults_refuse_comparison_but_an_explicit_study_runs(tmp_path):
    study = _relative_study(tmp_path)
    options = study["analysis"].pop("options")
    defaults = tmp_path / "fastmdx-defaults.yml"
    defaults.write_text(yaml.safe_dump({"analysis": {"options": options}}), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"analysis\.options\.dimred\.comparison"):
        read_defaults(defaults)

    source = tmp_path / "study.yml"
    study["output"] = "refused"
    source.write_text(yaml.safe_dump(study), encoding="utf-8")
    args = [sys.executable, "-m", "fastmdxplora", "analyze", "--config", str(source)]
    refused = _run_cli(args, tmp_path)
    assert refused.returncode != 0
    assert "analysis.options.dimred.comparison" in refused.stdout + refused.stderr
    assert not list((tmp_path / "refused").rglob("common_pca_projections.dat"))

    defaults.write_text("analysis: {figure_width: single_column}\n", encoding="utf-8")
    study["analysis"]["options"] = options
    study["output"] = "explicit"
    source.write_text(yaml.safe_dump(study), encoding="utf-8")
    allowed = _run_cli(args, tmp_path)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    _check_target_files(tmp_path / "explicit", tmp_path / "ligand.xtc", tmp_path / "ligand.pdb")


@pytest.mark.parametrize("placement", ["study", "system", "sweep"])
def test_nested_comparison_paths_keep_existing_confinement_and_gathering(placement):
    worktree = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix=".common_inputs_", dir=worktree) as scratch:
        root = Path(scratch).resolve()
        project = root / "project"
        project.mkdir()
        wt_pdb, wt_xtc, ligand_pdb, ligand_xtc = _write_ensembles(project)
        target = {"label": "ligand", "trajectory": ligand_xtc.name,
                  "topology": f"~/{ligand_pdb.name}"}
        options = {"dimred": {"comparison": {"reference": {"label": "WT"},
                                             "targets": [target]}}}
        config = {
            "systems": [{"system": str(wt_pdb)}],
            "analysis": {"trajectory": str(wt_xtc), "topology": str(wt_pdb)},
        }
        if placement == "sweep":
            config["sweep"] = {"analysis.options": options}  # a one-point axis
        else:
            block = config["systems"][0] if placement == "system" else config
            block.setdefault("analysis", {})["options"] = options
        path = project / "common.yml"

        def loaded_config():
            path.write_text(yaml.safe_dump(config), encoding="utf-8")
            return load_config_file(path)

        loaded = loaded_config()
        if placement == "sweep":
            loaded_options = loaded["sweep"]["analysis.options"]
        else:
            loaded_block = loaded["systems"][0] if placement == "system" else loaded
            loaded_options = loaded_block["analysis"]["options"]
        loaded_targets = loaded_options["dimred"]["comparison"]["targets"]
        assert loaded_targets[0]["topology"] == str(Path.home() / ligand_pdb.name)
        target["topology"] = str(ligand_pdb)

        def inside(value):
            path = Path(value)
            path = path if path.is_absolute() else root / path
            try:
                return path.resolve().relative_to(root)
            except ValueError:
                return None

        context = SimpleNamespace(workspace=SimpleNamespace(root=root, inside=inside))
        loaded = loaded_config()
        validate_config(loaded)
        _confined(context, loaded)
        gathered = gather_inputs(loaded, root)
        assert set(gathered.files.values()) == {wt_pdb, wt_xtc, ligand_pdb, ligand_xtc}
        run, = expand_runs(systems=normalize_systems(loaded["systems"]),
                           sweep=normalize_sweep(loaded.get("sweep")),
                           base_options=phase_options(loaded))
        recorded = root / "recorded.yml"
        recorded.write_text(yaml.safe_dump(run.options), encoding="utf-8")
        saved = load_config_file(recorded)["analysis"]["options"]["dimred"]["comparison"]
        assert saved["targets"][0] == {
            "label": "ligand", "trajectory": str(ligand_xtc), "topology": str(ligand_pdb),
        }

        outside = root.parent / f"{root.name}-outside.xtc"
        outside.write_bytes(b"outside")
        try:
            rejected = [f"../../{outside.name}"]
            link = project / "escape.xtc"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                if os.name != "nt" or getattr(exc, "winerror", None) != 1314:
                    raise
            else:
                rejected.append(link.name)
            for escape in rejected:
                target["trajectory"] = escape
                loaded = loaded_config()
                with pytest.raises(ToolError, match="outside the workspace"):
                    _confined(context, loaded)
                with pytest.raises(StudyError, match="outside"):
                    gather_inputs(loaded, root)
        finally:
            outside.unlink(missing_ok=True)
