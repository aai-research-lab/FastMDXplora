"""A study that simulated another's prepared system finds it by content.

A run given `setup_from` recorded the prepared system by path alone, as it
was typed, relative to wherever the command ran. Move or copy the study, or
fetch it to another machine, and re-analysis, the report and replica
comparison found nothing; put a different prepared system at the old path
and it was used without a word, its ligand chemistry and atom count
describing atoms the run never simulated. The run now records the system as
given, as resolved, relative to itself and by the SHA-256 of its
``system.xml``, and accepts only a system whose content matches. A
campaign's manifest lists each member relative to the campaign too.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import pytest

from fastmdxplora.refusals import CodedError
from fastmdxplora.simulation.pipeline import (
    _write_manifest,
    prepared_system_reference,
    setup_records_of,
    system_digest,
)
from tests.test_a_run_given_setup_from_prepares_nothing import _placeholders


def _prepared(setup: Path, contents: str) -> Path:
    _placeholders(setup)
    (setup / "system.xml").write_text(contents, encoding="utf-8")
    return setup


def _a_study(root: Path) -> tuple[Path, Path]:
    """A prepared system and a run that simulated it, as the simulation
    phase records them."""
    setup = _prepared(root / "runs" / "prepared" / "setup", "<System atoms='1'/>")
    run = root / "runs" / "replica"
    (run / "simulation").mkdir(parents=True)
    _write_manifest(run / "simulation", {"setup_from": "runs/prepared"}, [], [],
                    platform_used="CPU",
                    prepared_system=prepared_system_reference(
                        setup, run, "runs/prepared"))
    return setup, run


class TestTheRecord:

    def test_it_says_where_and_what(self, tmp_path: Path) -> None:
        setup, run = _a_study(tmp_path)
        record = json.loads((run / "simulation" / "simulation_parameters.json")
                            .read_text(encoding="utf-8"))["prepared_system"]
        assert record == {
            "given": "runs/prepared",
            "resolved": str(setup.resolve()),
            "relative_to_run": str(Path("..") / "prepared" / "setup"),
            "system_xml_sha256": system_digest(setup),
        }

    def test_two_preparations_differ(self, tmp_path: Path) -> None:
        first = _prepared(tmp_path / "a", "<System atoms='1'/>")
        second = _prepared(tmp_path / "b", "<System atoms='2'/>")
        assert system_digest(first) != system_digest(second)


class TestMoved:

    def test_where_it_ran_it_is_found(self, tmp_path: Path) -> None:
        setup, run = _a_study(tmp_path)
        assert setup_records_of(run) == setup.resolve()

    def test_moved_whole_it_is_found_beside_the_run(self, tmp_path: Path) -> None:
        _a_study(tmp_path / "here")
        shutil.move(str(tmp_path / "here"), str(tmp_path / "there"))
        found = setup_records_of(tmp_path / "there" / "runs" / "replica")
        assert found == (tmp_path / "there" / "runs" / "prepared" / "setup").resolve()

    def test_the_run_alone_copied_still_finds_the_original(self, tmp_path: Path) -> None:
        setup, run = _a_study(tmp_path / "here")
        shutil.copytree(run, tmp_path / "elsewhere" / "replica")
        assert setup_records_of(tmp_path / "elsewhere" / "replica") == setup.resolve()

    def test_nothing_where_it_should_be_is_said(self, tmp_path: Path, caplog) -> None:
        _, run = _a_study(tmp_path / "here")
        shutil.copytree(run, tmp_path / "elsewhere" / "replica")
        shutil.rmtree(tmp_path / "here")
        with caplog.at_level(logging.WARNING, logger="fastmdx"):
            assert setup_records_of(tmp_path / "elsewhere" / "replica") is None
        assert "not where its record points" in caplog.text


class TestADifferentSystemAtThePath:

    def test_it_is_refused_rather_than_read(self, tmp_path: Path) -> None:
        setup, run = _a_study(tmp_path / "here")
        shutil.copytree(run, tmp_path / "elsewhere" / "replica")
        # Another preparation of the same molecule where the first one was.
        (setup / "system.xml").write_text("<System atoms='2'/>", encoding="utf-8")
        with pytest.raises(CodedError) as refused:
            setup_records_of(tmp_path / "elsewhere" / "replica")
        assert refused.value.code == "analysis.data.not_this_system"
        assert str(setup.resolve()) in str(refused.value)

    def test_the_right_one_beside_it_wins_over_a_wrong_one_elsewhere(
            self, tmp_path: Path) -> None:
        setup, _ = _a_study(tmp_path / "here")
        shutil.copytree(tmp_path / "here", tmp_path / "there")
        (setup / "system.xml").write_text("<System atoms='2'/>", encoding="utf-8")
        found = setup_records_of(tmp_path / "there" / "runs" / "replica")
        assert found == (tmp_path / "there" / "runs" / "prepared" / "setup").resolve()


def test_a_run_recorded_before_this_is_read_as_before(tmp_path: Path) -> None:
    """No `prepared_system` in its record: found by the path, as it was."""
    setup = _prepared(tmp_path / "prepared" / "setup", "<System/>")
    run = tmp_path / "replica"
    (run / "simulation").mkdir(parents=True)
    (run / "simulation" / "simulation_parameters.json").write_text(json.dumps(
        {"parameters": {"setup_from": str(tmp_path / "prepared")}}), encoding="utf-8")
    assert setup_records_of(run) == setup


class TestACampaignMoved:

    def _campaign(self, root: Path) -> Path:
        batch = root / "campaign"
        for run_id, mean in (("s1", 1.0), ("s2", 3.0)):
            options = batch / "runs" / run_id / "analysis" / "rg" / "options.json"
            options.parent.mkdir(parents=True)
            options.write_text(json.dumps({"analysis": "rg", "findings": {
                "mean": {"mean": mean, "standard_error": 0.1}}}), encoding="utf-8")
        from fastmdxplora.batch.explorer import BatchExplorer

        runs = []
        for run_id in ("s1", "s2"):
            record = {"run_id": run_id, "status": "ok",
                      "output_dir": str(batch / "runs" / run_id),
                      "sweep_values": {"simulation.random_seed": int(run_id[1])}}
            runs.append(BatchExplorer._with_relative_dir(
                type("E", (), {"output_dir": batch})(), record))
        (batch / "batch_manifest.json").write_text(json.dumps({
            "runs": runs, "sweep": {"simulation.random_seed": [1, 2]}}), encoding="utf-8")
        return batch

    def test_each_member_is_listed_relative_to_the_campaign(self, tmp_path: Path) -> None:
        manifest = json.loads((self._campaign(tmp_path) / "batch_manifest.json")
                              .read_text(encoding="utf-8"))
        assert [r["output_dir_relative"] for r in manifest["runs"]] == [
            str(Path("runs") / "s1"), str(Path("runs") / "s2")]

    def test_moved_it_reads_its_own_members(self, tmp_path: Path) -> None:
        from fastmdxplora.batch.aggregate import aggregate_members

        self._campaign(tmp_path / "here")
        shutil.move(str(tmp_path / "here"), str(tmp_path / "there"))
        # Another campaign at the old path would have been read instead.
        other = self._campaign(tmp_path / "here")
        for run_id in ("s1", "s2"):
            (other / "runs" / run_id / "analysis" / "rg" / "options.json").write_text(
                json.dumps({"analysis": "rg", "findings": {
                    "mean": {"mean": 100.0, "standard_error": 0.1}}}), encoding="utf-8")
        compared = aggregate_members(tmp_path / "there" / "campaign")
        means = sorted(m["mean"] for m in compared["analyses"]["rg"]["members"])
        assert means == [1.0, 3.0]


# ---------------------------------------------------------------------------
# A real run, copied to another folder and analysed again there
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def moved(tmp_path_factory):
    """One replica of a prepared complex, run, then its whole folder copied
    somewhere else: the prepared system and the run together."""
    pytest.importorskip("openmm")
    pytest.importorskip("rdkit")
    from fastmdxplora import FastMDXplora
    from tests.test_a_run_given_setup_from_prepares_nothing import _a_prepared_system

    base = tmp_path_factory.mktemp("travel")
    here = base / "here"
    here.mkdir()
    _a_prepared_system(here)
    config = {
        "output": "runs/one",
        "systems": [{"system": str(here / "complex.pdb"), "id": "bound"}],
        "exclude_phase": ["setup"],
        "simulation": {"setup_from": "runs/prepared", "duration_ns": 0.0001,
                       "nvt_steps": 20, "npt_steps": 20,
                       "minimize_max_iterations": 100, "platform": "CPU",
                       "trajectory_interval_steps": 10},
        "analysis": {"include": ["ligand_rmsd", "pl_interactions"]},
    }
    propagate = logging.getLogger("fastmdx").propagate
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(here)
        patch.setenv("OPENMM_CPU_THREADS", "1")
        results = FastMDXplora(config_data=config).explore()
    logging.getLogger("fastmdx").propagate = propagate
    assert [r.status for r in results] == ["ok"], results[0].message
    # Copied rather than moved: the run's log stays open where it ran.
    shutil.copytree(here, base / "there")
    return base


def _analyse(run: Path):
    from fastmdxplora import FastMDXplora

    propagate = logging.getLogger("fastmdx").propagate
    try:
        fmdx = FastMDXplora(system=str(run / "simulation" / "topology.pdb"),
                            output_dir=run)
        return fmdx.analyze(include=["pl_interactions"])
    finally:
        logging.getLogger("fastmdx").propagate = propagate


@pytest.mark.slow
class TestARealRunCopied:

    def test_its_record_names_the_system_by_content(self, moved) -> None:
        run = moved / "there" / "runs" / "one"
        record = json.loads((run / "simulation" / "simulation_parameters.json")
                            .read_text(encoding="utf-8"))["prepared_system"]
        assert record["relative_to_run"] == str(Path("..") / "prepared" / "setup")
        assert record["system_xml_sha256"] == system_digest(
            moved / "there" / "runs" / "prepared" / "setup")

    def test_analysed_again_it_reads_the_ligand_from_the_moved_system(self, moved) -> None:
        run = moved / "there" / "runs" / "one"
        assert _analyse(run).status == "ok"
        options = json.loads((run / "analysis" / "pl_interactions" / "options.json")
                             .read_text(encoding="utf-8"))
        chemistry = options["findings"]["ligand_chemistry"]
        assert chemistry["source"] == "run"
        assert chemistry["formal_charge"] == 1
        assert Path(chemistry["detail"]).resolve().is_relative_to(
            (moved / "there").resolve())

    def test_a_different_system_at_the_old_path_is_refused(self, moved) -> None:
        """The run copied away alone, and the old folder now holding another
        preparation of the same molecule."""
        run = moved / "alone" / "one"
        shutil.copytree(moved / "there" / "runs" / "one", run)
        old = moved / "here" / "runs" / "prepared" / "setup"
        with (old / "system.xml").open("a", encoding="utf-8") as handle:
            handle.write("<!-- another preparation -->\n")
        result = _analyse(run)
        assert result.status == "error"
        assert result.refusal["code"] == "analysis.data.not_this_system"
