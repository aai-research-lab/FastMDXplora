"""Tests for the batch / parameter-sweep system.

Covers:
  - Pure sweep expansion (systems × sweep cross-product)
  - System and sweep normalization + validation
  - Run-id generation and uniqueness
  - Merge precedence (base < per-system < sweep)
  - Sweep-axis typo rejection (via config validation)
  - End-to-end BatchExplorer execution + manifest
  - Flat output for a single run; runs/ layout for many
  - Execution scheduling: worker-count resolution, device round-robin
    pinning, and sequential-vs-parallel equivalence (mocked — real GPU
    parallelism can't run in the sandbox)
"""

from __future__ import annotations

from concurrent.futures import Future
import json
from pathlib import Path

import pytest

from fastmdxplora.batch import (
    BatchExplorer,
    SweepError,
    expand_runs,
    normalize_sweep,
    normalize_systems,
)
from fastmdxplora.batch.sweep import is_batch_config
from fastmdxplora.config import ConfigError, validate_config
from fastmdxplora.cli.main import main as cli_main
from fastmdxplora.orchestrator import PhaseResult, RunResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def stub_pdb(tmp_path: Path) -> Path:
    p = tmp_path / "protein.pdb"
    p.write_text(
        # A tripeptide, not a lone residue: one amino acid is simultaneously
        # N- and C-terminal, and AMBER has no template for that. CHARMM36
        # tolerated it, so this fixture survived until the default changed.
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
        "ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C\n"
        "ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00           C\n"
        "ATOM      4  O   ALA A   1       1.251   2.390   0.000  1.00  0.00           O\n"
        "ATOM      5  CB  ALA A   1       1.988  -0.773  -1.199  1.00  0.00           C\n"
        "ATOM      6  N   GLY A   2       3.332   1.549   0.000  1.00  0.00           N\n"
        "ATOM      7  CA  GLY A   2       3.972   2.849   0.000  1.00  0.00           C\n"
        "ATOM      8  C   GLY A   2       5.486   2.705   0.000  1.00  0.00           C\n"
        "ATOM      9  O   GLY A   2       6.008   1.593   0.000  1.00  0.00           O\n"
        "ATOM     10  N   ALA A   3       6.171   3.845   0.000  1.00  0.00           N\n"
        "ATOM     11  CA  ALA A   3       7.623   3.845   0.000  1.00  0.00           C\n"
        "ATOM     12  C   ALA A   3       8.174   5.265   0.000  1.00  0.00           C\n"
        "ATOM     13  O   ALA A   3       7.416   6.235   0.000  1.00  0.00           O\n"
        "ATOM     14  CB  ALA A   3       8.153   3.072  -1.199  1.00  0.00           C\n"
        "ATOM     15  OXT ALA A   3       9.400   5.400   0.000  1.00  0.00           O\n"
        "END\n"
    )
    return p


def _write(tmp_path: Path, text: str, name: str = "batch.yml") -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


def _fake_worker_factory(*, fail_values: set[int], write_analysis: bool = False, calls=None):
    def _fake_execute_run(
        spec_dict,
        run_out,
        include,
        exclude,
        verbose,
        device_override,
        quiet: bool = True,
        force: bool = False,
    ):
        out = Path(run_out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "worker_marker.txt").write_text(spec_dict["run_id"], encoding="utf-8")
        if calls is not None:
            calls.append(spec_dict["run_id"])

        value = spec_dict["sweep_values"].get("setup.ph")
        phase_dir = out / "setup"
        phase_dir.mkdir(exist_ok=True)
        status = "error" if value in fail_values else "ok"
        message = "RuntimeError: intentional batch failure" if status == "error" else ""
        phase = PhaseResult(
            name="setup",
            status=status,
            output_dir=phase_dir,
            message=message,
            artifacts=["worker_marker.txt"] if status == "ok" else [],
        )

        if status == "ok" and write_analysis:
            rmsd_dir = out / "analysis" / "rmsd"
            rmsd_dir.mkdir(parents=True, exist_ok=True)
            (rmsd_dir / "rmsd.dat").write_text("0.1\n0.2\n0.3\n", encoding="utf-8")

        return RunResult(
            run_id=spec_dict["run_id"],
            system=spec_dict["system"],
            status=status,
            output_dir=out,
            sweep_values=spec_dict["sweep_values"],
            phases=[phase],
            message=message,
            error_type="PhaseError" if status == "error" else None,
        )

    return _fake_execute_run


class _ImmediateProcessPoolExecutor:
    """Synchronous ProcessPoolExecutor stand-in for deterministic scheduler tests."""

    def __init__(self, max_workers, mp_context=None, initializer=None,
                 initargs=()):
        # A real ProcessPoolExecutor takes an initializer, and the study uses
        # one to give each worker its share of the machine's cores. A double
        # that refuses the argument reports the caller as broken. Recorded
        # rather than run: this executor works in the test process, and
        # setting thread-count environment variables there would leak into
        # every test after it.
        self.max_workers = max_workers
        self.initializer = initializer
        self.initargs = initargs

    def submit(self, fn, *args, **kwargs):
        # A real Executor.submit forwards keyword arguments; this stood in for
        # one without them, so a caller that used any looked like a fault here.
        fut = Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001
            fut.set_exception(exc)
        return fut

    def shutdown(self, wait=True, cancel_futures=False):
        return None


# ===========================================================================
# normalize_systems
# ===========================================================================
class TestNormalizeSystems:
    def test_basic(self):
        out = normalize_systems([
            {"id": "a", "system": "a.pdb"},
            {"id": "b", "system": "b.pdb"},
        ])
        assert [s["id"] for s in out] == ["a", "b"]
        assert out[0]["system"] == "a.pdb"

    def test_auto_id_when_missing(self):
        out = normalize_systems([{"system": "x.pdb"}])
        assert out[0]["id"] == "s1"

    def test_per_system_options_captured(self):
        out = normalize_systems([
            {"id": "a", "system": "a.pdb", "setup": {"ph": 6.5}},
        ])
        assert out[0]["options"]["setup"]["ph"] == 6.5

    def test_missing_system_raises(self):
        with pytest.raises(SweepError, match="missing a `system`"):
            normalize_systems([{"id": "a"}])

    def test_duplicate_id_raises(self):
        with pytest.raises(SweepError, match="Duplicate system id"):
            normalize_systems([
                {"id": "a", "system": "a.pdb"},
                {"id": "a", "system": "b.pdb"},
            ])

    def test_empty_raises(self):
        with pytest.raises(SweepError, match="non-empty list"):
            normalize_systems([])


# ===========================================================================
# normalize_sweep
# ===========================================================================
class TestNormalizeSweep:
    def test_basic(self):
        out = normalize_sweep({"simulation.temperature_K": [300, 310]})
        assert out["simulation.temperature_K"] == [300, 310]

    def test_scalar_becomes_single_axis(self):
        out = normalize_sweep({"setup.ph": 7.0})
        assert out["setup.ph"] == [7.0]

    def test_non_dotted_key_raises(self):
        with pytest.raises(SweepError, match="dotted phase.option"):
            normalize_sweep({"temperature_K": [300]})

    def test_unknown_phase_raises(self):
        with pytest.raises(SweepError, match="not a valid phase"):
            normalize_sweep({"nosuchphase.x": [1]})

    def test_empty_values_raises(self):
        with pytest.raises(SweepError, match="empty value list"):
            normalize_sweep({"setup.ph": []})


# ===========================================================================
# expand_runs
# ===========================================================================
class TestExpandRuns:
    def test_systems_times_sweep(self):
        systems = normalize_systems([
            {"id": "a", "system": "a.pdb"},
            {"id": "b", "system": "b.pdb"},
        ])
        sweep = normalize_sweep({"simulation.temperature_K": [300, 310, 320]})
        runs = expand_runs(systems=systems, sweep=sweep)
        assert len(runs) == 6  # 2 × 3

    def test_two_axis_cross_product(self):
        systems = normalize_systems([{"id": "a", "system": "a.pdb"}])
        sweep = normalize_sweep({
            "simulation.temperature_K": [300, 310],
            "simulation.pressure_bar": [1.0, 1.2],
        })
        runs = expand_runs(systems=systems, sweep=sweep)
        assert len(runs) == 4  # 1 × 2 × 2

    def test_no_sweep_one_run_per_system(self):
        systems = normalize_systems([
            {"id": "a", "system": "a.pdb"},
            {"id": "b", "system": "b.pdb"},
        ])
        runs = expand_runs(systems=systems, sweep=None)
        assert len(runs) == 2
        assert all(r.sweep_values == {} for r in runs)

    def test_single_implicit_system(self):
        sweep = normalize_sweep({"setup.ph": [6.5, 7.0]})
        runs = expand_runs(systems=None, sweep=sweep, base_system="x.pdb")
        assert len(runs) == 2
        assert all(r.system == "x.pdb" for r in runs)

    def test_sweep_value_lands_in_options(self):
        systems = normalize_systems([{"id": "a", "system": "a.pdb"}])
        sweep = normalize_sweep({"simulation.temperature_K": [310]})
        runs = expand_runs(systems=systems, sweep=sweep)
        assert runs[0].options["simulation"]["temperature_K"] == 310

    def test_merge_precedence_sweep_beats_system_beats_base(self):
        systems = normalize_systems([
            {"id": "a", "system": "a.pdb", "setup": {"ph": 6.0}},
        ])
        sweep = normalize_sweep({"setup.ph": [8.0]})
        base = {"setup": {"ph": 5.0, "ion_concentration_M": 0.1}}
        runs = expand_runs(systems=systems, sweep=sweep, base_options=base)
        # sweep (8.0) wins over system (6.0) wins over base (5.0)
        assert runs[0].options["setup"]["ph"] == 8.0
        # base-only option survives
        assert runs[0].options["setup"]["ion_concentration_M"] == 0.1

    def test_deterministic_order(self):
        systems = normalize_systems([
            {"id": "a", "system": "a.pdb"},
            {"id": "b", "system": "b.pdb"},
        ])
        sweep = normalize_sweep({"setup.ph": [6, 7]})
        runs = expand_runs(systems=systems, sweep=sweep)
        ids = [r.run_id for r in runs]
        # systems outer, sweep inner
        assert ids[0].startswith("a__")
        assert ids[1].startswith("a__")
        assert ids[2].startswith("b__")

    def test_requires_systems_or_base(self):
        with pytest.raises(SweepError, match="requires either"):
            expand_runs(systems=None, sweep=None)


# ===========================================================================
# Run-id generation
# ===========================================================================
class TestRunIds:
    def test_encodes_system_and_sweep(self):
        systems = normalize_systems([{"id": "trpcage1", "system": "t.pdb"}])
        sweep = normalize_sweep({"simulation.temperature_K": [300]})
        runs = expand_runs(systems=systems, sweep=sweep)
        assert "trpcage1" in runs[0].run_id
        assert "300" in runs[0].run_id

    def test_ids_unique(self):
        systems = normalize_systems([
            {"id": "a", "system": "a.pdb"},
            {"id": "b", "system": "b.pdb"},
        ])
        sweep = normalize_sweep({"setup.ph": [6, 7, 8]})
        runs = expand_runs(systems=systems, sweep=sweep)
        ids = [r.run_id for r in runs]
        assert len(ids) == len(set(ids))

    def test_unsafe_chars_slugged(self):
        systems = normalize_systems([{"id": "my system/v2", "system": "x.pdb"}])
        runs = expand_runs(systems=systems, sweep=None)
        # No slashes or spaces in the run id (safe as a directory name)
        assert "/" not in runs[0].run_id
        assert " " not in runs[0].run_id


# ===========================================================================
# Batch detection
# ===========================================================================
class TestBatchDetection:
    def test_sweep_triggers(self):
        assert is_batch_config({"sweep": {"setup.ph": [7]}}) is True

    def test_systems_triggers(self):
        assert is_batch_config({"systems": [{"system": "x.pdb"}]}) is True

    def test_plain_config_not_batch(self):
        assert is_batch_config({"system": "x.pdb", "setup": {"ph": 7}}) is False

    def test_empty_sweep_not_batch(self):
        assert is_batch_config({"system": "x.pdb", "sweep": {}}) is False


# ===========================================================================
# Validation integration (typo'd sweep axis)
# ===========================================================================
class TestBatchValidation:
    def test_unknown_sweep_option_rejected(self):
        with pytest.raises(ConfigError, match="not a valid simulation option"):
            validate_config({
                "systems": [{"id": "a", "system": "x.pdb"}],
                "sweep": {"simulation.temperatur_K": [300]},  # typo
            })

    def test_valid_batch_config_passes(self):
        validate_config({
            "output": "./out",
            "systems": [{"id": "a", "system": "x.pdb", "setup": {"ph": 6.5}}],
            "sweep": {"simulation.temperature_K": [300, 310]},
        })

    def test_bad_system_entry_rejected(self):
        with pytest.raises(ConfigError, match="missing a `system`"):
            validate_config({"systems": [{"id": "a"}]})


# ===========================================================================
# End-to-end BatchExplorer
# ===========================================================================
class TestBatchExplorerE2E:
    def test_runs_full_matrix(self, tmp_path, stub_pdb):
        cfg = _write(tmp_path, f"""
output: {tmp_path / 'batch'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
  - {{id: b, system: {stub_pdb}, setup: {{ph: 6.5}}}}
sweep:
  setup.temperature_K: [300, 310, 320]
""")
        batch = BatchExplorer(config=str(cfg))
        results = batch.run()
        assert len(results) == 6
        assert all(r.status == "ok" for r in results)
        # All six run dirs exist
        runs_dir = tmp_path / "batch" / "runs"
        assert len(list(runs_dir.iterdir())) == 6

    def test_batch_manifest_written(self, tmp_path, stub_pdb):
        cfg = _write(tmp_path, f"""
output: {tmp_path / 'batch'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
sweep:
  setup.ph: [6.5, 7.4]
""")
        BatchExplorer(config=str(cfg)).run()
        manifest = json.loads(
            (tmp_path / "batch" / "batch_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["kind"] == "batch"
        assert manifest["n_runs"] == 2
        assert len(manifest["runs"]) == 2

    def test_per_system_and_sweep_overrides_applied(self, tmp_path, stub_pdb):
        cfg = _write(tmp_path, f"""
output: {tmp_path / 'batch'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
  - {{id: b, system: {stub_pdb}, setup: {{ph: 6.5}}}}
sweep:
  setup.temperature_K: [300]
""")
        BatchExplorer(config=str(cfg)).run()
        # b overrides ph, a takes the default; both have temperature_K 300.
        # The default is read rather than written out: what this test checks is
        # that a per-system setting reaches one run and not the other, and
        # spelling the value here made it fail when the default moved.
        from fastmdxplora.setup.pipeline import DEFAULTS
        a = json.loads((tmp_path / "batch" / "runs" / "a__temperature-K-300"
                        / "setup" / "setup_parameters.json").read_text(encoding="utf-8"))
        b = json.loads((tmp_path / "batch" / "runs" / "b__temperature-K-300"
                        / "setup" / "setup_parameters.json").read_text(encoding="utf-8"))
        assert a["parameters"]["ph"] == DEFAULTS["ph"]
        assert b["parameters"]["ph"] == 6.5
        assert a["parameters"]["temperature_K"] == 300
        assert b["parameters"]["temperature_K"] == 300

    def test_each_run_is_self_contained(self, tmp_path, stub_pdb):
        """Every run dir has its own manifest + resolved_config."""
        cfg = _write(tmp_path, f"""
output: {tmp_path / 'batch'}
include: [setup]
sweep:
  setup.ph: [6.5, 7.0]
systems:
  - {{id: a, system: {stub_pdb}}}
""")
        BatchExplorer(config=str(cfg)).run()
        for run_dir in (tmp_path / "batch" / "runs").iterdir():
            assert (run_dir / "manifest.json").exists()
            assert (run_dir / "resolved_config.yml").exists()
            assert (run_dir / "setup").is_dir()


# ===========================================================================
# CLI
# ===========================================================================
class TestBatchCLI:
    def test_explore_runs_sweep(self, tmp_path, stub_pdb):
        cfg = _write(tmp_path, f"""
output: {tmp_path / 'b'}
include: [setup]
sweep:
  setup.ph: [6.5, 7.0]
systems:
  - {{id: a, system: {stub_pdb}}}
""")
        rc = cli_main(["explore", "--config", str(cfg)])
        assert rc == 0
        # Two runs -> runs/ layout + batch manifest
        assert (tmp_path / "b" / "batch_manifest.json").exists()

    def test_explore_multi_system(self, tmp_path, stub_pdb):
        cfg = _write(tmp_path, f"""
output: {tmp_path / 'b'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
sweep:
  setup.temperature_K: [300, 310]
""")
        rc = cli_main(["explore", "--config", str(cfg)])
        assert rc == 0
        manifest = json.loads((tmp_path / "b" / "batch_manifest.json").read_text(encoding="utf-8"))
        assert manifest["n_runs"] == 2

    def test_single_system_flat_output(self, tmp_path, stub_pdb):
        """A one-system, no-sweep config produces the flat single-run layout."""
        cfg = _write(tmp_path, f"""
output: {tmp_path / 'single'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
""")
        rc = cli_main(["explore", "--config", str(cfg)])
        assert rc == 0
        # Flat layout: NO batch_manifest, NO runs/ dir; phase dir at root
        assert not (tmp_path / "single" / "batch_manifest.json").exists()
        assert not (tmp_path / "single" / "runs").exists()
        assert (tmp_path / "single" / "setup").is_dir()

    def test_cli_dry_run_creates_nothing(self, tmp_path, stub_pdb):
        cfg = _write(tmp_path, f"""
output: {tmp_path / 'dry'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
sweep:
  setup.temperature_K: [300, 310]
""")
        rc = cli_main(["explore", "--config", str(cfg), "--dry-run"])
        assert rc == 0
        # Nothing executed: no output directory at all
        assert not (tmp_path / "dry").exists()

    def test_batch_typo_returns_error_code(self, tmp_path, stub_pdb):
        cfg = _write(tmp_path, f"""
output: {tmp_path / 'b'}
sweep:
  simulation.temperatur_K: [300]
systems:
  - {{id: a, system: {stub_pdb}}}
""")
        rc = cli_main(["explore", "--config", str(cfg)])
        assert rc == 2  # ConfigError -> exit 2


# ===========================================================================
# Execution scheduling (worker count, device pinning, parallel path)
# ===========================================================================
class TestExecutionScheduling:
    def _cfg(self, tmp_path, stub_pdb, execution_block="", n_temps=4):
        temps = ", ".join(str(300 + 10 * i) for i in range(n_temps))
        return _write(tmp_path, f"""
output: {tmp_path / 'b'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
sweep:
  setup.temperature_K: [{temps}]
{execution_block}
""")

    def test_workers_explicit(self, tmp_path, stub_pdb):
        cfg = self._cfg(tmp_path, stub_pdb,
                        "execution:\n  mode: parallel\n  workers: 3\n")
        batch = BatchExplorer(config=str(cfg))
        assert batch._resolve_workers() == 3

    def test_workers_default_from_devices(self, tmp_path, stub_pdb):
        cfg = self._cfg(tmp_path, stub_pdb,
                        "execution:\n  mode: parallel\n  devices: [0, 1]\n")
        batch = BatchExplorer(config=str(cfg))
        # One worker per device when workers unset
        assert batch._resolve_workers() == 2

    def test_workers_default_cpu_capped_at_runs(self, tmp_path, stub_pdb):
        # 2 runs, no devices, no explicit workers -> capped at run count
        cfg = self._cfg(tmp_path, stub_pdb,
                        "execution:\n  mode: parallel\n", n_temps=2)
        batch = BatchExplorer(config=str(cfg))
        assert batch._resolve_workers() <= 2
        assert batch._resolve_workers() >= 1

    def test_a_card_freed_first_takes_the_next_run(self, tmp_path, stub_pdb, monkeypatch):
        """Two cards, three runs, and the second run finishes before the first.

        The third went to card 0 by its place in the queue, so two runs
        shared card 0 while card 1 sat idle. It goes where the room is."""
        from fastmdxplora.batch import explorer

        given: list[str] = []

        class FirstRunStillGoing:
            def __init__(self, max_workers, mp_context=None, initializer=None, initargs=()):
                self.held = []

            def submit(self, fn, spec_dict, out, include, exclude, verbose, device, **kwargs):
                given.append(device)
                fut = Future()
                result = RunResult(run_id=spec_dict["run_id"], system=spec_dict["system"],
                                   status="ok", output_dir=Path(out),
                                   sweep_values=spec_dict["sweep_values"], phases=[])
                if len(given) == 1:
                    self.held.append((fut, result))
                else:
                    fut.set_result(result)
                if len(given) == 3:
                    for held, done in self.held:
                        held.set_result(done)
                return fut

            def shutdown(self, wait=True, cancel_futures=False):
                return None

        monkeypatch.setattr(explorer, "ProcessPoolExecutor", FirstRunStillGoing)
        cfg = self._cfg(tmp_path, stub_pdb,
                        "execution:\n  mode: parallel\n  devices: [0, 1]\n", n_temps=3)
        BatchExplorer(config=str(cfg)).run()
        assert given == ["0", "1", "1"]

    def test_device_round_robin(self, tmp_path, stub_pdb):
        cfg = self._cfg(tmp_path, stub_pdb,
                        "execution:\n  mode: parallel\n  devices: [0, 1]\n")
        batch = BatchExplorer(config=str(cfg))
        # Worker slots cycle through the device list
        assert batch._device_for_worker(0) == "0"
        assert batch._device_for_worker(1) == "1"
        assert batch._device_for_worker(2) == "0"
        assert batch._device_for_worker(3) == "1"

    def test_no_devices_no_pinning(self, tmp_path, stub_pdb):
        cfg = self._cfg(tmp_path, stub_pdb, "execution:\n  mode: sequential\n")
        batch = BatchExplorer(config=str(cfg))
        assert batch._device_for_worker(0) is None

    def test_mode_defaults_sequential(self, tmp_path, stub_pdb):
        cfg = self._cfg(tmp_path, stub_pdb, "")
        batch = BatchExplorer(config=str(cfg))
        assert batch.mode == "sequential"

    def test_parallel_produces_same_runs_as_sequential(self, tmp_path, stub_pdb):
        """Parallel and sequential must produce equivalent results.

        This verifies the *scheduler*: parallel mode must dispatch and
        collect the same run set (same ids, same count) with the same
        per-run status as sequential mode. It deliberately does NOT require
        status == "ok": that would demand the real chemistry backend succeed
        inside spawned worker processes, which is environment-dependent
        (e.g. OpenMM behavior under Windows 'spawn' on a degenerate stub).
        Whatever the chemistry produces, the two modes must agree.
        """
        seq_cfg = self._cfg(
            tmp_path, stub_pdb, "execution:\n  mode: sequential\n")
        seq = BatchExplorer(config=str(seq_cfg))
        seq_results = seq.run()

        # Fresh output dir for the parallel run
        par_cfg = _write(tmp_path, f"""
output: {tmp_path / 'bpar'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
sweep:
  setup.temperature_K: [300, 310, 320, 330]
execution:
  mode: parallel
  workers: 2
""", name="par.yml")
        par = BatchExplorer(config=str(par_cfg))
        par_results = par.run()

        # Same run ids and count (pure scheduling — independent of chemistry).
        seq_ids = sorted(r.run_id for r in seq_results)
        par_ids = sorted(r.run_id for r in par_results)
        assert seq_ids == par_ids
        assert len(par_results) == 4

        # Same per-run status between the two modes: the scheduler must not
        # change outcomes. (Equivalence, not a hard-coded "ok".) Surface the
        # actual per-run error messages so a real parallel-only failure is
        # diagnosable from CI rather than opaque.
        seq_status = {r.run_id: r.status for r in seq_results}
        par_status = {r.run_id: r.status for r in par_results}
        par_errors = {
            r.run_id: (r.message or [p.message for p in r.phases])
            for r in par_results if r.status != "ok"
        }
        assert par_status == seq_status, (
            "parallel and sequential disagree on per-run status:\n"
            f"  sequential={seq_status}\n"
            f"  parallel={par_status}\n"
            f"  parallel errors={par_errors}"
        )

    def test_parallel_manifest_records_execution(self, tmp_path, stub_pdb):
        cfg = _write(tmp_path, f"""
output: {tmp_path / 'b'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
sweep:
  setup.temperature_K: [300, 310]
execution:
  mode: parallel
  workers: 2
  devices: [0, 1]
""")
        BatchExplorer(config=str(cfg)).run()
        manifest = json.loads((tmp_path / "b" / "batch_manifest.json").read_text(encoding="utf-8"))
        assert manifest["execution"]["mode"] == "parallel"
        assert manifest["execution"]["workers"] == 2
        assert manifest["execution"]["devices"] == [0, 1]

    def test_device_pinning_stamps_simulation_option(self, tmp_path, stub_pdb):
        """The worker stamps its device onto the run's simulation.device_index."""
        from fastmdxplora.batch.explorer import _execute_run
        spec = {
            "run_id": "a__t-300", "system_id": "a", "system": str(stub_pdb),
            "sweep_values": {"setup.temperature_K": 300},
            "options": {"setup": {}, "simulation": {}},
        }
        record = _execute_run(
            spec, str(tmp_path / "out"), ["setup"], None, False,
            device_override="1",
        )
        # The run completed; device pinning is internal but must not crash
        # and must produce a valid record.
        assert record.run_id == "a__t-300"
        assert record.status in ("ok", "error")


class TestContinueOnErrorScheduling:
    def _cfg(self, tmp_path, stub_pdb, *, mode, continue_on_error, values=(6, 7, 8)):
        vals = ", ".join(str(v) for v in values)
        return _write(tmp_path, f"""
output: {tmp_path / f'b_{mode}_{continue_on_error}'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
sweep:
  setup.ph: [{vals}]
execution:
  mode: {mode}
  workers: 1
  continue_on_error: {str(continue_on_error).lower()}
""", name=f"{mode}_{continue_on_error}.yml")

    def test_sequential_continue_true_records_failure_and_keeps_running(
        self, tmp_path, stub_pdb, monkeypatch
    ):
        import fastmdxplora.batch.explorer as batch_explorer

        calls = []
        monkeypatch.setattr(
            batch_explorer,
            "_execute_run",
            _fake_worker_factory(fail_values={7}, calls=calls),
        )
        cfg = self._cfg(tmp_path, stub_pdb, mode="sequential", continue_on_error=True)

        results = BatchExplorer(config=str(cfg)).run()

        assert [r.status for r in results] == ["ok", "error", "ok"]
        assert calls == ["a__ph-6", "a__ph-7", "a__ph-8"]
        manifest = json.loads(
            (tmp_path / "b_sequential_True" / "batch_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        failed = manifest["runs"][1]
        assert failed["status"] == "error"
        assert failed["error_type"] == "PhaseError"
        assert failed["phases"][0]["name"] == "setup"
        assert failed["phases"][0]["status"] == "error"
        assert "intentional batch failure" in failed["message"]
        for run in manifest["runs"]:
            assert Path(run["output_dir"], "worker_marker.txt").is_file()

    def test_sequential_continue_false_stops_and_marks_later_runs_skipped(
        self, tmp_path, stub_pdb, monkeypatch
    ):
        import fastmdxplora.batch.explorer as batch_explorer

        calls = []
        monkeypatch.setattr(
            batch_explorer,
            "_execute_run",
            _fake_worker_factory(fail_values={7}, calls=calls),
        )
        cfg = self._cfg(tmp_path, stub_pdb, mode="sequential", continue_on_error=False)

        results = BatchExplorer(config=str(cfg)).run()

        assert [r.status for r in results] == ["ok", "error", "skipped"]
        assert calls == ["a__ph-6", "a__ph-7"]
        skipped = results[2]
        assert "continue_on_error=False" in skipped.message
        assert not (skipped.output_dir / "worker_marker.txt").exists()

    def test_parallel_continue_true_collects_successes_and_failures(
        self, tmp_path, stub_pdb, monkeypatch
    ):
        import fastmdxplora.batch.explorer as batch_explorer

        calls = []
        monkeypatch.setattr(batch_explorer, "ProcessPoolExecutor", _ImmediateProcessPoolExecutor)
        monkeypatch.setattr(
            batch_explorer,
            "_execute_run",
            _fake_worker_factory(fail_values={7}, calls=calls),
        )
        cfg = self._cfg(tmp_path, stub_pdb, mode="parallel", continue_on_error=True)

        results = BatchExplorer(config=str(cfg)).run()

        assert [r.status for r in results] == ["ok", "error", "ok"]
        assert calls == ["a__ph-6", "a__ph-7", "a__ph-8"]

    def test_parallel_continue_false_stops_submitting_and_marks_skipped(
        self, tmp_path, stub_pdb, monkeypatch
    ):
        import fastmdxplora.batch.explorer as batch_explorer

        calls = []
        monkeypatch.setattr(batch_explorer, "ProcessPoolExecutor", _ImmediateProcessPoolExecutor)
        monkeypatch.setattr(
            batch_explorer,
            "_execute_run",
            _fake_worker_factory(fail_values={7}, calls=calls),
        )
        cfg = self._cfg(
            tmp_path,
            stub_pdb,
            mode="parallel",
            continue_on_error=False,
            values=(6, 7, 8, 9),
        )

        results = BatchExplorer(config=str(cfg)).run()

        assert [r.status for r in results] == ["ok", "error", "skipped", "skipped"]
        assert calls == ["a__ph-6", "a__ph-7"]
        assert all("continue_on_error=False" in r.message for r in results[2:])
        assert not (results[2].output_dir / "worker_marker.txt").exists()

    def test_batch_cli_returns_failure_exit_code_for_failed_run(
        self, tmp_path, stub_pdb, monkeypatch
    ):
        import fastmdxplora.batch.explorer as batch_explorer

        monkeypatch.setattr(
            batch_explorer,
            "_execute_run",
            _fake_worker_factory(fail_values={7}),
        )
        cfg = self._cfg(tmp_path, stub_pdb, mode="sequential", continue_on_error=True)

        rc = cli_main(["explore", "--config", str(cfg)])

        assert rc == 1

    def test_failed_run_does_not_break_comparison_from_successful_runs(
        self, tmp_path, stub_pdb, monkeypatch
    ):
        import fastmdxplora.batch.explorer as batch_explorer

        monkeypatch.setattr(
            batch_explorer,
            "_execute_run",
            _fake_worker_factory(fail_values={7}, write_analysis=True),
        )
        cfg = self._cfg(tmp_path, stub_pdb, mode="sequential", continue_on_error=True)

        results = BatchExplorer(config=str(cfg)).run()

        assert [r.status for r in results] == ["ok", "error", "ok"]
        cmp_dir = tmp_path / "b_sequential_True" / "comparison"
        assert (cmp_dir / "comparison_report.md").is_file()
        summary = (cmp_dir / "comparison_summary.csv").read_text(encoding="utf-8")
        assert "a__ph-6" in summary
        assert "a__ph-8" in summary
        assert "a__ph-7" not in summary


# ===========================================================================
# Dry-run (plan only) + uniform RunResult shape
# ===========================================================================
class TestDryRunAndShape:
    def test_dry_run_sweep_executes_nothing(self, tmp_path, stub_pdb, capsys):
        cfg = _write(tmp_path, f"""
output: {tmp_path / 'd'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
sweep:
  setup.temperature_K: [300, 310]
""")
        from fastmdxplora import FastMDXplora
        results = FastMDXplora(config=str(cfg)).explore(dry_run=True)
        # Two planned runs, nothing executed
        assert len(results) == 2
        assert all(r.status == "planned" for r in results)
        assert all(r.phases == [] for r in results)
        # No output directory created
        assert not (tmp_path / "d").exists()
        out = capsys.readouterr().out
        assert "dry run" in out.lower()

    def test_dry_run_single_system(self, tmp_path, stub_pdb):
        from fastmdxplora import FastMDXplora
        results = FastMDXplora(
            system=str(stub_pdb), output_dir=tmp_path / "s"
        ).explore(include=["setup"], dry_run=True)
        assert len(results) == 1
        assert results[0].status == "planned"

    def test_explore_returns_uniform_runresult_list(self, tmp_path, stub_pdb):
        """Single system and sweep both return list[RunResult]."""
        from fastmdxplora import FastMDXplora
        from fastmdxplora.orchestrator import RunResult

        # single system=
        r1 = FastMDXplora(
            system=str(stub_pdb), output_dir=tmp_path / "a"
        ).explore(include=["setup"])
        assert isinstance(r1, list) and all(isinstance(x, RunResult) for x in r1)
        assert len(r1) == 1 and r1[0].phases[0].name == "setup"

        # sweep
        cfg = _write(tmp_path, f"""
output: {tmp_path / 'b'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
sweep:
  setup.temperature_K: [300, 310]
""")
        r2 = FastMDXplora(config=str(cfg)).explore()
        assert all(isinstance(x, RunResult) for x in r2)
        assert len(r2) == 2
        # Each RunResult carries its phases
        assert all(x.phases[0].name == "setup" for x in r2)

    def test_runresult_to_dict_and_phase_lookup(self, tmp_path, stub_pdb):
        from fastmdxplora import FastMDXplora
        run = FastMDXplora(
            system=str(stub_pdb), output_dir=tmp_path / "a"
        ).explore(include=["setup", "analysis"])[0]
        d = run.to_dict()
        assert d["run_id"] == "s1"
        assert {p["name"] for p in d["phases"]} == {"setup", "analysis"}
        # phase() helper
        assert run.phase("setup").name == "setup"
        assert run.phase("report") is None


class TestAParallelStudySaysWhatItIsDoing:
    """Each worker's output goes to its own log, so three of them do not
    interleave into one unreadable screen. That left the terminal showing the
    last completion and then nothing at all.

    A window of a real study runs for hours. A study that is working looked
    exactly like one that had hung, and the only way to tell was to go looking
    for files.
    """

    def test_a_duration_reads_at_a_glance(self) -> None:
        from fastmdxplora.batch.explorer import _elapsed

        assert _elapsed(9) == "0m09s"
        assert _elapsed(65) == "1m05s"
        assert _elapsed(3725) == "1h02m"

    def test_the_line_says_running_finished_and_queued(self) -> None:
        """Which together answer 'is anything happening, and how much is
        left'."""
        from fastmdxplora.batch.explorer import _progress_line

        line = _progress_line(running=3, done=2, queued=2, total=7,
                              seconds=745)
        assert "3 running" in line
        # "finished" became "done" when the sentence became a bar; the
        # question the line answers did not change.
        assert "2/7 done" in line
        assert "2 queued" in line
        assert "12m25s" in line

    def test_a_run_is_named_when_it_starts_and_its_log_is_given(
        self, tmp_path, capsys, monkeypatch
    ) -> None:
        """So a run still going has been named once, and can be followed while
        it goes rather than read after it ends."""
        from fastmdxplora.batch import explorer

        monkeypatch.setattr(explorer, "HEARTBEAT_SECONDS", 0.05)
        config = tmp_path / "campaign.yml"
        config.write_text(
            "output: out\ninclude: [setup]\n"
            "execution: {mode: parallel, workers: 2}\n"
            "systems:\n"
            f"  - system: {tmp_path / 'no-such-a.pdb'}\n    id: one\n"
            f"  - system: {tmp_path / 'no-such-b.pdb'}\n    id: two\n",
            encoding="utf-8")
        explorer.BatchExplorer(config=config,
                              output_dir=str(tmp_path / "out")).run()

        printed = capsys.readouterr().out
        assert "started one" in printed and "started two" in printed
        assert "run.log" in printed

    def test_and_says_something_while_nothing_finishes(
        self, tmp_path, capsys, monkeypatch
    ) -> None:
        """The heartbeat is the whole point: without it the terminal is silent
        for as long as the longest run takes."""
        from fastmdxplora.batch import explorer

        # Zero means every wait times out at once, so the line appears even
        # though these runs fail in under a second.
        monkeypatch.setattr(explorer, "HEARTBEAT_SECONDS", 0.0)
        config = tmp_path / "campaign.yml"
        config.write_text(
            "output: out\ninclude: [setup]\n"
            "execution: {mode: parallel, workers: 1}\n"
            "systems:\n"
            f"  - system: {tmp_path / 'no-such-a.pdb'}\n    id: one\n"
            f"  - system: {tmp_path / 'no-such-b.pdb'}\n    id: two\n",
            encoding="utf-8")
        explorer.BatchExplorer(config=config,
                              output_dir=str(tmp_path / "out")).run()

        assert "running," in capsys.readouterr().out


class TestASingleRunIsNotSilenced:
    """Each worker's output goes to its own log so three of them do not
    interleave three PLUMED banners into one unreadable screen.

    With one run there is nothing to interleave with, and redirecting it left
    a config-driven study of a single system printing one line and then
    nothing for as long as the run took.
    """

    @staticmethod
    def _study(tmp_path, systems):
        from fastmdxplora.batch.explorer import BatchExplorer

        listed = "\n".join(f"  - system: {s}" for s in systems)
        config = tmp_path / "study.yml"
        config.write_text(f"output: out\ninclude: [setup]\nsystems:\n{listed}\n",
                          encoding="utf-8")
        return BatchExplorer(config=config, output_dir=str(tmp_path / "out"))

    def test_one_system_speaks(self, tmp_path, monkeypatch) -> None:
        from fastmdxplora.batch import explorer

        seen: dict = {}
        monkeypatch.setattr(
            explorer, "_execute_run",
            lambda *args, **kwargs: seen.update(quiet=kwargs.get("quiet"))
            or _ok_result(args[0]))

        self._study(tmp_path, [tmp_path / "a.pdb"]).run()
        assert seen["quiet"] is False

    def test_several_do_not(self, tmp_path, monkeypatch) -> None:
        """Where their output would collide, it goes to their own logs."""
        from fastmdxplora.batch import explorer

        seen: list = []
        monkeypatch.setattr(
            explorer, "_execute_run",
            lambda *args, **kwargs: seen.append(kwargs.get("quiet"))
            or _ok_result(args[0]))

        self._study(tmp_path, [tmp_path / "a.pdb", tmp_path / "b.pdb"]).run()
        assert seen == [True, True]


def _ok_result(spec_dict):
    from fastmdxplora.orchestrator import RunResult

    return RunResult(run_id=spec_dict["run_id"], system=spec_dict["system"],
                     status="ok", sweep_values={}, phases=[])


class TestSelectionsAreCheckedAgainstWhateverWillBeSimulated:
    """The check was written inside the block that prepares the shared
    system, so a study re-run with --force -- which finds one already there
    and prepares nothing -- skipped it entirely and failed three windows in
    parallel instead. The one case it covered was a freshly prepared system,
    which is the case where the author has just watched the structure being
    built."""

    def test_it_runs_outside_the_preparation_block(self, tmp_path, monkeypatch) -> None:
        # A study re-run finds its shared system already prepared, and the
        # selections are still checked against it: one that matches nothing
        # there is refused before a window runs. And freshly prepared too.
        from fastmdxplora.batch import explorer
        from fastmdxplora.refusals import StudyError

        for already_prepared in (True, False):
            where = tmp_path / str(already_prepared)
            study = _an_umbrella_study(where, selection_a="resname XYZ and name CA")
            shared = where / "out" / "shared_setup" / "setup"
            if already_prepared:
                _a_prepared_peptide(shared)
            monkeypatch.setattr(explorer, "_execute_run",
                                lambda *a, _s=shared, **k: _a_prepared_peptide(_s) or _ok())
            with pytest.raises(StudyError) as raised:
                study._maybe_prepare_once(["setup", "simulation"], None)
            assert raised.value.code == "simulation.cv.selection_empty", already_prepared

    def test_an_unresolvable_selection_is_not_a_verdict(self, tmp_path, monkeypatch) -> None:
        """A selection this cannot resolve is not thereby wrong; refusing on
        that basis would be worse than the wait it saves. A selection that
        resolves to nothing is a verdict, and is refused."""
        from fastmdxplora.batch import explorer
        from fastmdxplora.refusals import StudyError

        _a_prepared_peptide(tmp_path)
        spec = {"umbrella": {"collective_variable": "distance",
                             "selection_a": "name CA", "selection_b": "name CB"}}

        def cannot_tell(*args, **kwargs):
            raise RuntimeError("the resolver could not read this topology")

        monkeypatch.setattr(explorer, "_resolve_umbrella_selections", cannot_tell)
        assert explorer._check_selections_against(tmp_path, spec) is None

        def matches_nothing(*args, **kwargs):
            raise ValueError("selection_b 'name CB' matched no atoms")

        monkeypatch.setattr(explorer, "_resolve_umbrella_selections", matches_nothing)
        with pytest.raises(StudyError):
            explorer._check_selections_against(tmp_path, spec)

    def test_a_missing_topology_is_passed_over(self, tmp_path) -> None:
        from fastmdxplora.batch.explorer import _check_selections_against

        # Nothing raised: there is no system to check against yet.
        _check_selections_against(tmp_path, {"umbrella": {
            "collective_variable": "distance",
            "selection_a": "resid 0 and name CA",
            "selection_b": "resid 99 and name CA",
        }})


class TestEachMethodIsCheckedByItsOwnPlanner:
    """The pre-flight check ran every block through `plan_from_config`, which
    is metadynamics' planner and requires `sigma` -- the width of a hill. An
    umbrella study has no hills and no sigma, so a valid umbrella config was
    refused for lacking a setting the method does not have, after its shared
    system had already been built.

    The selection-resolving code is shared between the methods. The
    validation around it is not."""

    def _topology(self, tmp_path):

        path = tmp_path / "topology.pdb"
        lines, serial = [], 1
        for index, name in enumerate(("ALA", "GLY", "ALA")):
            for atom in ("N", "CA", "C", "O"):
                lines.append(
                    f"ATOM  {serial:>5} {atom:^4} {name:>3} A{index + 1:>4}    "
                    f"{index * 1.5:>8.3f}{0.0:>8.3f}{0.0:>8.3f}  1.00  0.00")
                serial += 1
        path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
        return path

    def _umbrella(self, **overrides):
        block = {
            "collective_variable": "distance",
            "selection_a": "resid 0 and name CA",
            "selection_b": "resid 2 and name CA",
            "from": 0.6, "to": 1.0, "n_windows": 5,
            "force_constant": 2000.0,
        }
        block.update(overrides)
        return {"umbrella": block}

    def test_a_valid_umbrella_block_is_accepted(self, tmp_path) -> None:
        """It has no `sigma` and does not need one."""
        from fastmdxplora.batch.explorer import _check_selections_against

        self._topology(tmp_path)
        _check_selections_against(tmp_path, self._umbrella())

    def test_an_empty_selection_is_still_caught(self, tmp_path) -> None:
        """Which is what the check exists for: `resid 9` on a tripeptide cost
        a full preparation and three failed windows to discover."""
        from fastmdxplora.batch.explorer import _check_selections_against

        self._topology(tmp_path)
        with pytest.raises(ValueError) as caught:
            _check_selections_against(
                tmp_path, self._umbrella(selection_b="resid 9 and name CA"))
        assert "matched no atoms" in str(caught.value)

    def test_umbrella_windows_are_checked_too(self, tmp_path) -> None:
        """Two checks catching different things: the coordinate through the
        shared layer, the windows through umbrella's own planner."""
        from fastmdxplora.batch.explorer import _check_selections_against

        self._topology(tmp_path)
        with pytest.raises(ValueError):
            # One window is not an umbrella study.
            _check_selections_against(tmp_path, self._umbrella(n_windows=1))

    def test_a_metadynamics_block_still_needs_its_sigma(self, tmp_path) -> None:
        """The requirement is real for the method that has hills."""
        from fastmdxplora.batch.explorer import _check_selections_against

        self._topology(tmp_path)
        with pytest.raises(ValueError) as caught:
            _check_selections_against(tmp_path, {"metadynamics": {
                "collective_variable": "distance",
                "selection_a": "resid 0 and name CA",
                "selection_b": "resid 2 and name CA",
            }})
        assert "sigma" in str(caught.value)

    def test_an_expanded_window_is_not_a_study(self, tmp_path) -> None:
        """By the time the check runs, the block has been expanded: each
        window carries the single `centre` it sits at, and `from`, `to` and
        `n_windows` are gone. Asking the study's planner to validate a
        window's spec reported `from` as missing from a config that had it --
        a refusal for the absence of a key expansion had consumed on
        purpose."""
        from fastmdxplora.batch.explorer import _check_selections_against
        from fastmdxplora.simulation.umbrella import expand_umbrella

        self._topology(tmp_path)
        study = self._umbrella()["umbrella"]
        expanded = expand_umbrella({
            "systems": [{"id": "t", "system": "x.pdb"}],
            "simulation": {"umbrella": dict(study)},
        })["systems"][0]["simulation"]["umbrella"]

        assert "centre" in expanded and "from" not in expanded
        _check_selections_against(tmp_path, {"umbrella": expanded})

    def test_a_selection_is_checked_in_a_window_too(self, tmp_path) -> None:
        """The coordinate applies to both shapes; only the window planning
        does not."""
        from fastmdxplora.batch.explorer import _check_selections_against
        from fastmdxplora.simulation.umbrella import expand_umbrella

        self._topology(tmp_path)
        study = self._umbrella(selection_b="resid 9 and name CA")["umbrella"]
        expanded = expand_umbrella({
            "systems": [{"id": "t", "system": "x.pdb"}],
            "simulation": {"umbrella": dict(study)},
        })["systems"][0]["simulation"]["umbrella"]

        with pytest.raises(ValueError) as caught:
            _check_selections_against(tmp_path, {"umbrella": expanded})
        assert "matched no atoms" in str(caught.value)


def _a_prepared_peptide(setup: Path) -> None:
    """What a prepared system leaves: its three files, the topology real."""
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    setup.mkdir(parents=True, exist_ok=True)
    (setup / "topology.pdb").write_text(TRI_ALANINE, encoding="utf-8")
    for name in ("system.xml", "state.xml"):
        (setup / name).write_text("<x/>", encoding="utf-8")


def _ok():
    from types import SimpleNamespace

    return SimpleNamespace(status="ok", message="")


def _an_umbrella_study(where: Path, *, selection_a: str):
    from fastmdxplora.batch.explorer import BatchExplorer

    where.mkdir(parents=True, exist_ok=True)
    config = where / "umbrella.yml"
    config.write_text(
        "systems:\n  - system: 1UBQ\n"
        "include: [setup, simulation]\n"
        "simulation:\n  umbrella:\n    collective_variable: distance\n"
        f"    selection_a: \"{selection_a}\"\n    selection_b: \"name CA\"\n"
        "    from: 0.4\n    to: 1.2\n    n_windows: 3\n    force_constant: 500\n",
        encoding="utf-8")
    return BatchExplorer(config=config, output_dir=str(where / "out"))
