"""One study in several places: what must match, and what must not."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from fastmdxplora.validation.environments import (
    compare_environments,
    digest_of,
)


def _run(root: Path, name: str, *, config: dict, digest: str | None = "abc123",
         output_dir: str | None = None) -> Path:
    run = root / name
    run.mkdir(parents=True, exist_ok=True)
    payload = dict(config)
    payload["output_dir"] = output_dir or f"/scratch/{name}/run"
    (run / "resolved_config.yml").write_text(
        yaml.safe_dump(payload), encoding="utf-8")
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    if digest:
        (run / "setup").mkdir(exist_ok=True)
        record = {"input": {"structure": {"sha256": digest}}}
        (run / "setup" / "setup_parameters.json").write_text(
            json.dumps(record), encoding="utf-8")
    return run


STUDY = {
    "setup": {"ph": 7.0, "heterogens": "auto"},
    "simulation": {"duration_ns": 20, "temperature_K": 300.0},
}


class TestWhatMustMatch:
    def test_the_same_study_in_two_places_agrees(self, tmp_path):
        runs = {
            "container": _run(tmp_path, "container", config=STUDY),
            "conda": _run(tmp_path, "conda", config=STUDY),
        }
        result = compare_environments(runs)

        assert result["agreed"] is True
        assert result["study_differences"] == {}
        assert result["input_digest"]["agree"] is True

    def test_a_default_resolving_differently_is_the_failure_it_catches(
            self, tmp_path):
        """The study said one thing and two machines heard two.

        This is the whole reason for comparing configurations rather than
        results: a difference here is unambiguous, where a difference in a
        trajectory could be the thermostat.
        """
        other = {"setup": {"ph": 7.0, "heterogens": "auto"},
                 "simulation": {"duration_ns": 20, "temperature_K": 310.0}}
        result = compare_environments({
            "container": _run(tmp_path, "container", config=STUDY),
            "wheel": _run(tmp_path, "wheel", config=other),
        })

        assert result["agreed"] is False
        assert "simulation.temperature_K" in result["study_differences"]
        assert result["study_differences"][
            "simulation.temperature_K"]["wheel"] == 310.0

    def test_different_inputs_are_not_the_same_study(self, tmp_path):
        """Whatever the configurations say.

        Two runs that began from different bytes are two studies, and a
        matching configuration makes that more dangerous rather than less.
        """
        result = compare_environments({
            "a": _run(tmp_path, "a", config=STUDY, digest="aaa"),
            "b": _run(tmp_path, "b", config=STUDY, digest="bbb"),
        })

        assert result["agreed"] is False
        assert result["input_digest"]["agree"] is False


class TestWhatMustNotBeReportedAsADifference:
    def test_paths_and_platforms_are_environmental(self, tmp_path):
        """They differ by construction.

        Reporting them alongside real discrepancies would bury the ones
        that matter under a list of directories.
        """
        result = compare_environments({
            "container": _run(tmp_path, "container", config=STUDY,
                              output_dir="/mnt/scratch/run"),
            "laptop": _run(tmp_path, "laptop", config=STUDY,
                           output_dir="/Users/a/run"),
        })

        assert result["agreed"] is True
        assert "output_dir" in result["environmental_differences"]
        assert result["study_differences"] == {}

    def test_results_are_declared_out_of_scope_with_the_reason(self, tmp_path):
        """The preparation is seeded; the dynamics diverge across platforms.

        Calling that a failure would make an honest limitation look like a
        defect, and leaving it unsaid would let a real difference hide
        behind it.
        """
        result = compare_environments({
            "a": _run(tmp_path, "a", config=STUDY),
            "b": _run(tmp_path, "b", config=STUDY),
        })
        note = result["what_is_not_compared"]

        assert "the dynamics seed does not fix" not in note
        assert "seeded by setup.random_seed" in note
        assert "prepare the same atoms again" in note
        assert "simulation.random_seed" in note
        assert "within the spread between replicas" in note


class TestWhatItRefuses:
    def test_one_environment_is_not_a_comparison(self, tmp_path):
        result = compare_environments(
            {"only": _run(tmp_path, "only", config=STUDY)})
        assert "at least two" in result["refused"]

    def test_a_directory_that_is_not_a_run(self, tmp_path):
        (tmp_path / "empty").mkdir()
        result = compare_environments({
            "real": _run(tmp_path, "real", config=STUDY),
            "empty": tmp_path / "empty",
        })
        assert "not a run this can compare" in result["refused"]

    def test_a_missing_digest_is_noted_rather_than_assumed_to_agree(
            self, tmp_path):
        result = compare_environments({
            "a": _run(tmp_path, "a", config=STUDY, digest="aaa"),
            "b": _run(tmp_path, "b", config=STUDY, digest=None),
        })
        assert result["input_digest"]["not_recorded_for"] == ["b"]


class TestTheDigestItself:
    def test_it_is_read_from_the_record_setup_writes(self, tmp_path):
        """Setup's own record, written by setup's own writer, is checked.

        The digest sits in ``setup/setup_parameters.json``; the run's
        ``manifest.json`` does not carry it, so a comparison reading only
        the manifest never checked this row.
        """
        from types import SimpleNamespace

        from fastmdxplora.provenance import structure_provenance
        from fastmdxplora.setup.pipeline import _write_manifest

        runs = {}
        for name, body in (("a", b"ATOM 1\n"), ("b", b"ATOM 2\n")):
            run = tmp_path / name
            (run / "setup").mkdir(parents=True)
            pdb = run / "in.pdb"
            pdb.write_bytes(body)
            orchestrator = SimpleNamespace(
                system=str(pdb),
                _structure_provenance=structure_provenance(
                    str(pdb), "file", pdb))
            _write_manifest(run / "setup", orchestrator, "file", {}, [], [])
            (run / "resolved_config.yml").write_text(
                yaml.safe_dump(STUDY), encoding="utf-8")
            (run / "manifest.json").write_text("{}", encoding="utf-8")
            runs[name] = run

        result = compare_environments(runs)
        digests = result["input_digest"]["digests"]

        assert digests["a"] == hashlib.sha256(b"ATOM 1\n").hexdigest()
        assert digests["b"] == hashlib.sha256(b"ATOM 2\n").hexdigest()
        assert result["input_digest"]["not_recorded_for"] == []
        assert result["input_digest"]["agree"] is False
        assert result["agreed"] is False

    def test_it_reads_the_file_not_its_name(self, tmp_path):
        path = tmp_path / "structure.pdb"
        path.write_bytes(b"ATOM\n")
        assert digest_of(path) == hashlib.sha256(b"ATOM\n").hexdigest()
