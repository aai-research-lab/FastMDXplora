"""An extended study's resolved config is the study's, at its whole length.

Analysing the joined trajectory ran the study's analysis and report from the
last piece's config, and that run wrote it over the study's
`resolved_config.yml`: the piece's `resume_from`, its own length, no
minimisation or equilibration, and the joined trajectory's path. The file
kept so a study can be run again described its last extension; `fastmdx
diff` and the All studies comparison read the last piece's length as the
study's; and a study run from the file would have analysed the old study's
joined trajectory. Now the study's own config is put back with `duration_ns`
the production of every piece, and an extended study analysed again in place
finds its joined trajectory from its own folder.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import yaml

from tests.test_a_study_is_extended_in_place import _study
from tests.test_an_extended_study_goes_on_from_its_last_segment import _segment


def _config(root: Path) -> dict:
    return yaml.safe_load((root / "resolved_config.yml").read_text(encoding="utf-8"))


def _join(root: Path, monkeypatch, segment: Path) -> dict:
    """The join as an extension runs it, with the joining and the analysis
    stood in for: the analysis run writes the study's config from the last
    piece's, as the real one does."""
    import fastmdxplora
    from fastmdxplora.analysis import joining
    from fastmdxplora.simulation import resume

    def joined(base, out, **_):
        Path(out).write_bytes(b"joined frames")
        return {"segments": [0, 1, 2], "frames": 30,
                "topology": str(base / "simulation" / "topology.pdb")}

    class Analysed:
        def __init__(self, *, config_data, output_dir):
            self.config, self.out = config_data, Path(output_dir)

        def explore(self, force=False):
            last = {k: v for k, v in self.config.items() if k != "include_phase"}
            (self.out / "resolved_config.yml").write_text(
                "# what one analysis run wrote\n" + yaml.safe_dump(last), encoding="utf-8")
            return []

    monkeypatch.setattr(joining, "join_segments", joined)
    monkeypatch.setattr(fastmdxplora, "FastMDXplora", Analysed)
    piece = yaml.safe_load((segment / "resolved_config.yml").read_text(encoding="utf-8"))
    return resume._join_and_analyse(root, piece, segment=segment, keep_frames=None)


def _extended(monkeypatch):
    # The first run stopped 0.1 ns into a 0.5 ns plan and was extended twice
    # by 0.1 ns: 0.3 ns of production in three pieces.
    root = _study(done_steps=50_000, finished=False)
    _segment(root, 1)
    second = _segment(root, 2)
    resolved = root / "resolved_config.yml"
    resolved.write_text("# as the study wrote it\n" + resolved.read_text(encoding="utf-8"),
                        encoding="utf-8")
    first = _config(root)
    answer = _join(root, monkeypatch, second)
    assert answer["ok"]
    return root, first


def test_the_config_is_the_study_s_at_its_whole_length(monkeypatch):
    root, first = _extended(monkeypatch)
    after = _config(root)
    assert after["simulation"]["duration_ns"] == 0.3
    assert after["simulation"]["nvt_steps"] == first["simulation"]["nvt_steps"] == 50000
    assert "resume_from" not in after["simulation"]
    assert "trajectory" not in (after.get("analysis") or {})
    assert {k: v for k, v in after.items() if k != "simulation"} == {
        k: v for k, v in first.items() if k != "simulation"}
    assert (root / "resolved_config.yml").read_text(encoding="utf-8").startswith(
        "# as the study wrote it\n")


def test_the_next_extension_is_counted_from_every_piece(monkeypatch):
    from fastmdxplora.simulation.resume import extension_of, production_done_ns

    root, _ = _extended(monkeypatch)
    assert abs(production_done_ns(root) - 0.3) < 1e-9
    asked = extension_of(root, total_ns=0.5)
    assert asked.possible and abs(asked.production_done_ns - 0.3) < 1e-9
    assert abs(asked.config["simulation"]["duration_ns"] - 0.2) < 1e-9
    # Its own plan is what ran, so nothing of it remains, and it says how
    # to go past it.
    assert "can still be extended" in (extension_of(root).refusal or "")


def test_an_extended_study_analysed_in_place_reads_its_joined_trajectory(tmp_path):
    from fastmdxplora.analysis.analyze import study_trajectory

    root = tmp_path / "study"
    (root / "simulation").mkdir(parents=True)
    (root / "joined").mkdir()
    own = root / "simulation" / "production.dcd"
    own.write_bytes(b"first piece")
    assert study_trajectory(root) == (None, None)
    topology = root / "simulation" / "trajectory_topology.pdb"
    topology.write_text("END\n", encoding="utf-8")
    joined = root / "joined" / "production.dcd"
    joined.write_bytes(b"every piece")
    (root / "joined" / "joined.json").write_text(json.dumps({"topology": str(topology)}),
                                                 encoding="utf-8")
    later = time.time() + 5
    os.utime(joined, (later, later))
    assert study_trajectory(root) == (joined, topology)
    # Simulated again in place after the join: the join is not its trajectory.
    os.utime(own, (later + 5, later + 5))
    assert study_trajectory(root) == (None, None)


def _joined_study(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "study"
    (root / "simulation").mkdir(parents=True)
    (root / "joined").mkdir()
    (root / "analysis").mkdir()
    (root / "simulation" / "production.dcd").write_bytes(b"first piece")
    topology = root / "simulation" / "trajectory_topology.pdb"
    topology.write_text("END\n", encoding="utf-8")
    joined = root / "joined" / "production.dcd"
    joined.write_bytes(b"every piece")
    (root / "joined" / "joined.json").write_text(json.dumps({"topology": str(topology)}),
                                                 encoding="utf-8")
    later = time.time() + 5
    os.utime(joined, (later, later))
    return root, joined, topology


def test_the_analysis_phase_reads_the_joined_trajectory_unless_given_one(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fastmdxplora.analysis import analyze

    class Read(Exception):
        pass

    class Orchestrator:
        def __init__(self, *, trajectory, topology, **_):
            raise Read(trajectory, topology)

    monkeypatch.setattr(analyze, "AnalysisOrchestrator", Orchestrator)
    root, joined, topology = _joined_study(tmp_path)
    study = SimpleNamespace(output_dir=root)
    with pytest.raises(Read) as read:
        analyze.run(orchestrator=study, output_dir=root / "analysis")
    assert read.value.args == (str(joined), str(topology))
    own = root / "simulation" / "production.dcd"
    with pytest.raises(Read) as read:
        analyze.run(orchestrator=study, output_dir=root / "analysis", trajectory=str(own))
    assert read.value.args[0] == str(own)


def test_a_join_record_that_cannot_be_read_is_no_joined_trajectory(tmp_path):
    from fastmdxplora.analysis.analyze import study_trajectory

    root, _, _ = _joined_study(tmp_path)
    (root / "joined" / "joined.json").write_text("{not json", encoding="utf-8")
    assert study_trajectory(root) == (None, None)


def test_a_config_that_cannot_be_read_is_put_back_as_it_was(tmp_path):
    from fastmdxplora.simulation.resume import _keep_the_study_s_config

    root = tmp_path / "study"
    root.mkdir()
    resolved = root / "resolved_config.yml"
    resolved.write_text("what one analysis run wrote\n", encoding="utf-8")
    _keep_the_study_s_config(root, None)
    assert resolved.read_text(encoding="utf-8") == "what one analysis run wrote\n"
    for kept in ("simulation: [unclosed\n", "- a list, not a study\n"):
        _keep_the_study_s_config(root, kept)
        assert resolved.read_text(encoding="utf-8") == kept


def test_a_config_that_cannot_be_written_is_said(tmp_path, caplog):
    from fastmdxplora.simulation.resume import _keep_the_study_s_config

    root = tmp_path / "study"
    (root / "resolved_config.yml").mkdir(parents=True)
    with caplog.at_level("WARNING"):
        _keep_the_study_s_config(root, "simulation: {duration_ns: 1}\n")
    assert "could not be written back" in caplog.text
