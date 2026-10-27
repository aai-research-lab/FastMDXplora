"""A record the study pages refuse is not read, which is not missing.

A reader that falls back where a record is missing must not fall back where
a held read refused it (a link out of the study, or to a file of it that is
not one of its results). A carried-on study whose record of the join was
refused read as a study of one run: the Overview gave its first piece's
means as the production's and called them not determined. Each such
record now reads as not read, named, and no stand-in is given for it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_the_study_pages_read_only_the_study import (
    _a_study_joined,
    _ask,
    _link,
    _study,
    _write,
)

PUBLIC = pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])


def _energy(temperature: float, rows: int = 40) -> str:
    """A state record as OpenMM writes it, about ``temperature``."""
    return ('#"Step","Time (ps)","Potential Energy (kJ/mole)","Temperature (K)"\n'
            + "".join(f"{100 * i},{0.2 * i:.1f},{-1000.0 + i % 3},{temperature + 0.5 * (i % 4)}\n"
                      for i in range(1, rows + 1)))


def _joined(tmp_path: Path) -> Path:
    """A study carried on in two pieces of 40 samples each and joined: a
    mean over one piece is over 40, the study's over 80."""
    root = _a_study_joined(tmp_path)
    _write(root, "simulation/energy.csv", _energy(300.0))
    _write(root, "segment-001/simulation/energy.csv", _energy(300.0))
    return root


def _linked_out(root: Path, tmp_path: Path, *names: str) -> None:
    for name in names:
        path = root / name
        target = tmp_path / "outside" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
        _link(path, target)


def _overview(root: Path, tmp_path: Path, *, public: bool) -> dict:
    status, body = _ask(root, tmp_path, "/api/overview", public=public)
    assert status == 200
    return json.loads(body)


def _thermodynamics_record(root: Path) -> None:
    """The thermodynamics analysis's record of the whole production, as it
    writes it (`findings.thermodynamics`)."""
    _write(root, "analysis/thermodynamics/options.json", {"findings": {"thermodynamics": {
        "temperature": {"units": "K", "mean": 300.4, "standard_error": 0.2,
                        "effective_samples": 30.0}}}})


@PUBLIC
def test_a_joined_study_s_means_are_over_both_pieces(tmp_path, public):
    """Every record read: the means are over both pieces, not one."""
    answer = _overview(_joined(tmp_path), tmp_path, public=public)
    assert answer["thermodynamics"]["means"]["temperature"]["of"] == 80
    assert "not_read" not in answer["thermodynamics"]


@PUBLIC
@pytest.mark.parametrize("refused", [
    ("joined/joined.json",),
    ("joined/joined.json", "analysis/thermodynamics/options.json"),
], ids=["join-record", "join-record-and-thermodynamics"])
def test_a_join_record_not_read_gives_no_one_piece_means(tmp_path, refused, public):
    """The record of the join refused (and the thermodynamics analysis's
    record with it): the production's means are not read, the join's
    record is named, and no piece's means stand in for the study's."""
    root = _joined(tmp_path)
    _thermodynamics_record(root)
    _linked_out(root, tmp_path, *refused)

    answer = _overview(root, tmp_path, public=public)
    thermo = answer["thermodynamics"]
    if len(refused) == 1:
        # The analysis's own record of the whole production still read.
        assert thermo["means"]["temperature"]["mean"] == pytest.approx(300.4)
        assert thermo["means"]["potential_energy"] is None
    else:
        assert thermo["means"]["temperature"] is None
    # No mean over the 40 samples of the piece read.
    assert all(mean is None or mean["of"] != 40 for mean in thermo["means"].values())
    # Each record refused that the means rest on is named (df981c83): the
    # thermodynamics analysis's own as well as the join's.
    assert sorted(thermo["not_read"]) == sorted(refused)
    assert set(refused) <= set(answer["unavailable"])
    assert answer["unavailable_why"]["joined/joined.json"] == "outside"

    status, body = _ask(root, tmp_path, "/api/results", public=public)
    cards = {card["label"]: card for card in json.loads(body)["summary_cards"]}
    assert cards["Temperature"]["value"] == "Not read"
    assert cards["Temperature"]["detail"] == (
        "joined/joined.json was not read: it is a link to a file outside the study.")


@PUBLIC
@pytest.mark.parametrize("refused", [
    "joined/production.dcd", "simulation/production.dcd",
    "segment-001/simulation/energy.csv", "segment-001/simulation/checkpoint.chk.json",
])
def test_a_piece_s_record_not_read_is_named_not_missing(tmp_path, refused, public):
    """A joined trajectory, a piece's trajectory, state record or
    checkpoint's record refused: the means are not read and the record is
    named, not a study of one run, nor a piece without its record."""
    root = _joined(tmp_path)
    _linked_out(root, tmp_path, refused)

    thermo = _overview(root, tmp_path, public=public)["thermodynamics"]
    assert thermo["not_read"] == [refused]
    assert all(mean is None for mean in thermo["means"].values())


def _a_run_of_one_piece(tmp_path: Path) -> Path:
    """A study of one run whose live record sampled about 250 K and whose
    state record (`energy.csv`) about 300 K."""
    root = _study(tmp_path)
    _write(root, "simulation/live_status.json", {
        "status": "completed", "stage": "production", "current_step": 4000,
        "total_planned_steps": 4000, "last_update_timestamp": "2026-10-01T00:00:00+00:00"})
    _write(root, "simulation/live_metrics.csv", "stage,simulation_time_ns,temperature\n" + "".join(
        f"production,{0.001 * i:.3f},{250.0 + i % 3}\n" for i in range(1, 41)))
    _write(root, "simulation/energy.csv", _energy(300.0))
    return root


@PUBLIC
def test_a_state_record_not_read_is_not_stood_in_for_by_the_live_record(tmp_path, public):
    """The run's `energy.csv` refused: its means are not read, rather than
    taken from the live record's coarser samples."""
    read = _a_run_of_one_piece(tmp_path / "read")
    mean = _overview(read, tmp_path / "read", public=public)["thermodynamics"]["means"]
    assert mean["temperature"]["mean"] == pytest.approx(300.75, abs=0.5)

    root = _a_run_of_one_piece(tmp_path / "refused")
    _linked_out(root, tmp_path, "simulation/energy.csv")
    thermo = _overview(root, tmp_path / "refused", public=public)["thermodynamics"]
    assert thermo["means"]["temperature"] is None
    assert thermo["not_read"] == ["simulation/energy.csv"]


def test_outside_a_held_read_a_joined_study_is_its_pieces(tmp_path):
    """Outside a held read (the analysis phase, the command line) nothing
    is refused: a link is read as before."""
    from fastmdxplora.analysis.thermodynamics import state_record_pieces

    root = _joined(tmp_path)
    _linked_out(root, tmp_path, "joined/joined.json", "segment-001/simulation/energy.csv")
    assert len(state_record_pieces(root)) == 2


def _timed(name: str, minutes: int) -> dict:
    return {"name": name, "status": "ok", "started_at": "2026-10-01T00:00:00+00:00",
            "finished_at": f"2026-10-01T00:{minutes:02d}:00+00:00"}


@PUBLIC
@pytest.mark.parametrize("refused", ["segment-001/manifest.json", "segment-001"])
def test_a_piece_s_phases_not_read_add_no_wall_time(tmp_path, refused, public):
    """A carried-on study's wall time is its phases' and its pieces': with a
    piece's manifest (or the piece) refused, the others' times added are
    not the study's, and the run card is given no wall time."""
    from fastmdxplora.gui.simulated_time import phases_wall_seconds

    def timed(where: Path) -> Path:
        root = _a_study_joined(where)
        _write(root, "manifest.json", {"phases": [_timed("simulation", 10)]})
        _write(root, "segment-001/manifest.json", {"phases": [_timed("simulation", 20)]})
        return root

    def wall(root: Path, where: Path) -> object:
        status, body = _ask(root, where, "/api/status", public=public)
        assert status == 200
        return json.loads(body)["times"]["wall_s"]

    read = timed(tmp_path / "read")
    assert wall(read, tmp_path / "read") == 30 * 60

    root = timed(tmp_path / "refused")
    if refused == "segment-001":
        (root / "segment-001").rename(tmp_path / "outside-piece")
        _link(root / "segment-001", tmp_path / "outside-piece")
    else:
        _linked_out(root, tmp_path, refused)
    assert wall(root, tmp_path / "refused") is None
    # Outside a held read, nothing is refused.
    assert phases_wall_seconds(root) == 30 * 60


#: A piece's config as the run resolves it, on a timestep of ``dt`` fs.
_CONFIG = ("systems: [{{system: ala3.pdb}}]\nsimulation:\n  integrator: langevin_middle\n"
           "  ensemble: npt\n  timestep_fs: {dt}\n  nvt_steps: 0\n  npt_steps: 0\n"
           "  production_steps: 10000\n  duration_ns: 0.02\n  trajectory_interval_steps: 100\n")

#: What the pages may say of the production, for each route.
_PRODUCTION_ROUTES = ("/api/status", "/api/overview", "/api/results", "/api/analyses")


def _carried_on_from_checkpoints(tmp_path: Path) -> Path:
    """A study run once and carried on in `segment-001`, each piece 10,000
    steps of 2 fs from its own checkpoint: 0.04 ns of production in all."""
    from tests.test_the_study_pages_read_only_the_study import _a_study_carried_on

    root = _a_study_carried_on(tmp_path)
    for piece in ("", "segment-001/"):
        _write(root, f"{piece}simulation/checkpoint.chk", "checkpoint")
        _write(root, f"{piece}resolved_config.yml", _CONFIG.format(dt=2.0))
    return root


def _numbers(value: object):
    if isinstance(value, dict):
        for inner in value.values():
            yield from _numbers(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _numbers(inner)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield value


@PUBLIC
def test_a_piece_s_config_not_read_gives_no_production(tmp_path, public):
    """A piece's config linked out of the study, its timestep changed: it is
    not opened, its timestep sets no production a page shows, and each page
    says the production was not read, naming the config."""
    from fastmdxplora.simulation.resume import production_done_ns
    from tests.test_the_study_pages_read_only_the_study import _OPENED, _WATCHED, _watch

    root = _carried_on_from_checkpoints(tmp_path)
    assert production_done_ns(root) == pytest.approx(0.04)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "resolved_config.yml").write_text(_CONFIG.format(dt=3.7), encoding="utf-8")
    (root / "segment-001" / "resolved_config.yml").unlink()
    _link(root / "segment-001" / "resolved_config.yml", outside / "resolved_config.yml")
    # Read through, the outside config's timestep gives 0.057 ns.
    assert production_done_ns(root) == pytest.approx(0.057)

    _watch(outside)
    try:
        answers = {}
        for route in _PRODUCTION_ROUTES:
            status, body = _ask(root, tmp_path, route, public=public)
            assert status == 200, route
            answers[route] = json.loads(body)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    assert opened == []
    said = ("segment-001/resolved_config.yml was not read: it is a link to a file "
            "outside the study")
    for route, answer in answers.items():
        assert all(abs(number - 0.057) > 1e-9 for number in _numbers(answer)), route
        assert "segment-001/resolved_config.yml" in answer["unavailable"], route
    times = answers["/api/status"]["times"]
    assert times["production_ns"] is None
    assert times["production_not_read"] == said
    for route in ("/api/results", "/api/analyses"):
        answer = answers[route]
        assert answer["simulation"]["duration_ns_actual"] is None
        assert answer["simulation"]["pieces"] is None
        assert answer["simulation"]["production_not_read"] == said
        summary = {row["label"]: row["value"] for row in answer["summary"]}
        assert summary["Production"] == "not read"
        cards = {card["label"]: card for card in answer["summary_cards"]}
        assert cards["Production"]["value"] == "Not read"
        assert cards["Production"]["detail"] == said + "."


@PUBLIC
@pytest.mark.parametrize("refused", ["segment-001/simulation/production.dcd", "segment-001"])
def test_a_piece_not_read_is_not_left_out_of_the_production(tmp_path, refused, public):
    """A piece's trajectory, or the piece, linked out of the study: the
    production and the pieces it ran in are not read, rather than the one
    piece left given as all of it."""
    root = _carried_on_from_checkpoints(tmp_path)
    target = tmp_path / "outside" / refused
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / refused).rename(target)
    _link(root / refused, target)

    status, body = _ask(root, tmp_path, "/api/results", public=public)
    simulation = json.loads(body)["simulation"]
    assert simulation["pieces"] is None
    assert simulation["duration_ns_actual"] is None
    # A folder linked out is said as a folder (`series.FOLDER_OUT`).
    assert simulation["production_not_read"].startswith(
        (f"{refused} was not read", f"{refused}/ is a link out of the study"))


@PUBLIC
@pytest.mark.parametrize("refused", [
    ("analysis/rmsd/options.json",), ("manifest.json",),
    ("manifest.json", "analysis/rmsd/options.json")], ids=["options", "manifest", "both"])
def test_a_figure_s_record_not_read_gives_no_other_command(tmp_path, refused, public):
    """The analysis's options.json not read: no command is given to make the
    figure again (one without its selection and options makes another
    figure), and the chip says why. The run's manifest not read: who made
    the figure is said not read, not as a release that did not say."""
    root = _study(tmp_path)
    _write(root, "analysis/analysis_manifest.json", {
        "phase": "analysis", "n_frames": 40,
        "resolved": {"trajectory": str(root / "simulation" / "production.dcd"),
                     "topology": str(root / "simulation" / "topology.pdb")},
        "results": {"rmsd": {"status": "ok"}, "rg": {"status": "ok"}}})
    _write(root, "analysis/rmsd/options.json", {
        "analysis": "rmsd", "selection": "protein and name CA",
        "options": {"ref": 0, "align": True}})
    _write(root, "manifest.json", {"phases": [{"name": "analysis", "status": "ok",
                                               "produced_by": {"version": "9.9.9"}}]})
    read = json.loads(_ask(root, tmp_path, "/api/analyses", public=public)[1])
    made = read["figure_provenance"]["rmsd"]
    assert "--analyze-options" in made["command"] and made["version"] == "9.9.9"
    assert "not_read" not in made

    _linked_out(root, tmp_path, *refused)
    made = json.loads(_ask(root, tmp_path, "/api/analyses", public=public)[1])[
        "figure_provenance"]["rmsd"]
    assert made["not_read"] == list(refused)
    for name in refused:
        assert f"{name} was not read: it is a link to a file outside the study." in (
            made["not_read_said"])
    if "analysis/rmsd/options.json" in refused:
        assert made["command"] is None and made["config"] is None
        assert made["at_widths"] == {} and made["selection"] is None
        assert "command that makes the figure again is not written" in made["not_read_said"]
    else:
        assert "--analyze-options" in made["command"]
        assert made["version"] is None


@PUBLIC
@pytest.mark.parametrize("refused,project,latest", [
    (("manifest.json",), "not read", "completed"),
    (("manifest.json", "simulation/live_status.json"), "not read", "not read"),
    (("simulation/live_status.json",), "completed", "not read"),
], ids=["manifest", "manifest-and-live-record", "live-record"])
def test_a_run_s_status_not_read_is_said_not_read(tmp_path, refused, project, latest, public):
    """The run's manifest (or its live record) not read: its status is said
    not read, not "in progress" or "not run" as for a run that wrote none."""
    root = _a_run_of_one_piece(tmp_path)
    _linked_out(root, tmp_path, *refused)
    status, body = _ask(root, tmp_path, "/api/results", public=public)
    summary = {row["label"]: row["value"] for row in json.loads(body)["summary"]}
    assert (summary["Project status"], summary["Latest status"]) == (project, latest)


@PUBLIC
def test_a_piece_s_cost_not_read_gives_no_other_speed(tmp_path, public):
    """The last piece's cost record not read: the Overview gives no speed,
    not the study's first run's (or its live record's) in its place."""
    from tests.test_the_study_pages_read_only_the_study import _a_study_carried_on

    def carried_on(where: Path) -> Path:
        root = _a_study_carried_on(where)
        # The piece ran at half the first run's speed: 0.02 ns in 200 s.
        _write(root, "segment-001/simulation/cost.json", {
            "particles": 1403, "steps": 10000, "seconds": 200.0, "platform": "CPU",
            "timestep_fs": 2.0})
        return root

    read = carried_on(tmp_path / "read")
    assert _overview(read, tmp_path / "read", public=public)["speed_ns_per_day"] == (
        pytest.approx(8.64))

    root = carried_on(tmp_path / "refused")
    _linked_out(root, tmp_path, "segment-001/simulation/cost.json")
    answer = _overview(root, tmp_path / "refused", public=public)
    assert answer["speed_ns_per_day"] is None
    assert "segment-001/simulation/cost.json" in answer["unavailable"]


@PUBLIC
def test_a_join_record_and_trajectory_not_read_name_the_record(tmp_path, public):
    """The record of the join and the joined trajectory both not read: the
    means are said not read for the record of the join, which says what the
    join is, not for its trajectory."""
    root = _joined(tmp_path)
    _linked_out(root, tmp_path, "joined/production.dcd", "joined/joined.json")
    thermo = _overview(root, tmp_path, public=public)["thermodynamics"]
    assert thermo["not_read"] == ["joined/joined.json"]


@PUBLIC
def test_a_setup_record_not_read_is_not_another_system_s(tmp_path, public):
    """A run simulated from a system prepared elsewhere in its folder, an
    older `setup/` of its own beside it: with the record of the system it
    simulated not read, no setup is given, not the older system's."""
    root = _study(tmp_path)
    _write(root, "manifest.json", {"phases": [
        {"name": "setup", "status": "skipped", "taken_from": str(root / "prepared")},
        {"name": "analysis", "status": "ok"}]})
    _write(root, "prepared/setup_parameters.json", {"phase": "setup", "parameters": {"ph": 6.5}})
    _write(root, "setup/setup_parameters.json", {"phase": "setup", "parameters": {"ph": 8.1}})
    read = json.loads(_ask(root, tmp_path, "/api/results", public=public)[1])
    assert read["setup"]["ph"] == 6.5

    _linked_out(root, tmp_path, "prepared/setup_parameters.json")
    answer = json.loads(_ask(root, tmp_path, "/api/results", public=public)[1])
    assert answer["setup"]["ph"] is None
    assert "prepared/setup_parameters.json" in answer["unavailable"]
