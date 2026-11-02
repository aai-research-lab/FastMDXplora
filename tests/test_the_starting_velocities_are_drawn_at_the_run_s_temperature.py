"""The starting velocities are drawn at the run's own temperature.

A folder's defaults at 310 K reached `simulation.temperature_K` and not
`setup.temperature_K`, which stayed at 300 K: a study run at 310 K started
from velocities drawn at 300 K. Unset, the setup temperature is now each
run's simulation temperature, and the resolved config says so.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from fastmdxplora import FastMDXplora
from fastmdxplora.batch.sweep import expand_runs, normalize_sweep, normalize_systems
from fastmdxplora.config.defaults_file import YourDefaults, with_defaults
from fastmdxplora.config.loader import phase_options


def _setup_of(study: dict, tmp_path: Path) -> list[tuple[dict, dict, str]]:
    """Each run's setup options and resolved config, with setup stubbed."""
    runs = expand_runs(
        systems=normalize_systems(study["systems"]),
        sweep=normalize_sweep(study["sweep"]) if study.get("sweep") else None,
        base_options=phase_options(study))
    seen: list[tuple[dict, dict, str]] = []
    for run in runs:
        given: dict = {}

        def fake(*, orchestrator, output_dir, given=given, **options):
            given.update(options)
            return []

        out = tmp_path / run.run_id
        with patch("fastmdxplora.setup.pipeline.run", side_effect=fake):
            FastMDXplora(system="1L2Y", output_dir=out, options=run.options
                         ).explore(include_phase=["setup"], report=False)
        text = (out / "resolved_config.yml").read_text(encoding="utf-8")
        seen.append((given, yaml.safe_load(text), text))
    return seen


def test_a_folder_s_defaults_at_310_K_start_the_run_at_310_K(tmp_path):
    defaults = YourDefaults(Path("/lab/fastmdx-defaults.yml"),
                            {"simulation": {"temperature_K": 310}})
    study, _ = with_defaults({"systems": [{"system": "1L2Y"}]}, defaults)
    [(given, resolved, text)] = _setup_of(study, tmp_path)
    assert given["temperature_K"] == 310.0
    assert resolved["setup"]["temperature_K"] == 310.0
    assert "setup.temperature_K: 310.0, taken from simulation.temperature_K" in text


def test_a_plain_study_at_310_K_starts_at_310_K(tmp_path):
    study = {"systems": [{"system": "1L2Y"}], "simulation": {"temperature_K": 310}}
    [(given, resolved, _)] = _setup_of(study, tmp_path)
    assert given["temperature_K"] == 310.0
    assert resolved["setup"]["temperature_K"] == 310.0


def test_each_run_of_a_temperature_sweep_starts_at_its_own(tmp_path):
    study = {"systems": [{"system": "1L2Y"}],
             "sweep": {"simulation.temperature_K": [300, 330]}}
    seen = _setup_of(study, tmp_path)
    assert [g["temperature_K"] for g, _, _ in seen] == [300.0, 330.0]
    assert [r["setup"]["temperature_K"] for _, r, _ in seen] == [300.0, 330.0]


def test_a_system_entry_s_own_temperature_reaches_its_setup(tmp_path):
    study = {"systems": [{"id": "a", "system": "1L2Y"},
                         {"id": "b", "system": "1L2Y",
                          "simulation": {"temperature_K": 320}}],
             "simulation": {"temperature_K": 310}}
    seen = _setup_of(study, tmp_path)
    assert [g["temperature_K"] for g, _, _ in seen] == [310.0, 320.0]


def test_a_setup_temperature_the_study_sets_is_kept(tmp_path):
    study = {"systems": [{"system": "1L2Y"}], "setup": {"temperature_K": 290},
             "simulation": {"temperature_K": 310}}
    [(given, resolved, text)] = _setup_of(study, tmp_path)
    assert given["temperature_K"] == 290
    assert resolved["setup"]["temperature_K"] == 290
    assert "taken from" not in text


def test_setup_called_directly_falls_back_and_says_so(caplog):
    from types import SimpleNamespace

    from fastmdxplora.setup.pipeline import _starting_temperature

    with caplog.at_level("INFO"):
        assert _starting_temperature(
            {"temperature_K": None},
            SimpleNamespace(options={"simulation": {"temperature_K": 315}})) == 315.0
        assert _starting_temperature(
            {"temperature_K": None}, SimpleNamespace(options={})) == 300.0
    assert "315.0 K, the simulation temperature" in caplog.text
    assert "neither setup.temperature_K nor simulation.temperature_K" in caplog.text
