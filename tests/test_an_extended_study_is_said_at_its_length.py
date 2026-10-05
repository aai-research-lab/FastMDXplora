"""An extended study is said at the length it ran, not its first piece's.

An extension writes its own folder inside the study and leaves the study's
own simulation record as the first piece wrote it. The methods, the list of
settings, the GUI's summary and the dashboard all read that record, so a study
run until it knew, extended from 10 ns to 40 ns, said "Production dynamics
were run for 10 ns" beside analyses that averaged 40.
"""

from __future__ import annotations

import json

from fastmdxplora.report.context import PhaseContext
from fastmdxplora.simulation.resume import extended_production
from tests.test_a_study_is_extended_in_place import _study
from tests.test_an_extended_study_goes_on_from_its_last_segment import _segment


def _extended():
    # The first run stopped 0.1 ns into its plan and was extended twice by
    # 0.1 ns: 0.3 ns of production in three pieces.
    root = _study(done_steps=50_000, finished=False)
    _segment(root, 1)
    _segment(root, 2)
    (root / "simulation" / "simulation_parameters.json").write_text(json.dumps({
        "parameters": {"production_steps": 50_000, "timestep_fs": 2.0,
                       "integrator": "langevin_middle", "temperature_K": 300.0}}),
        encoding="utf-8")
    return root


def test_the_pieces_are_counted_and_their_production_summed():
    total, pieces = extended_production(_extended())
    assert pieces == 3 and abs(total - 0.3) < 1e-9


def test_a_study_in_one_piece_is_left_to_its_record():
    assert extended_production(_study()) is None


def test_the_methods_give_the_whole_production():
    from fastmdxplora.report.document import _methods_section

    text = _methods_section(_extended(), PhaseContext(simulation_present=True))
    assert "Production dynamics were run for 300 ps in the NPT ensemble, in 3 pieces, " \
           "each continuing from the checkpoint of the one before." in text
    assert "run for 100 ps" not in text
    # The list of settings says whose they are.
    assert "These are the first piece's. 2 more pieces extended the study" in text
    assert "to 0.3 ns of production in all" in text


def test_the_methods_of_a_study_in_one_piece_are_unchanged(tmp_path):
    from fastmdxplora.report.methods import methods_paragraphs

    sim = {"parameters": {"production_steps": 50_000, "timestep_fs": 2.0}}
    text = methods_paragraphs(tmp_path, {}, sim)
    assert "Production dynamics were run for 100 ps in the NPT ensemble." in text


def test_the_gui_summary_gives_the_whole_production():
    from fastmdxplora.gui.server import _results_payload

    payload = _results_payload(_extended())
    said = {row["label"]: row["value"] for row in payload["summary"]}
    assert said["Production"] == "300 ps in 3 pieces"
    assert payload["simulation"]["pieces"] == 3


def test_the_dashboard_card_gives_the_whole_production():
    from fastmdxplora.gui.report_dashboard import _summary_cards

    root = _extended()
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "simulation", "status": "ok"}]}), encoding="utf-8")
    cards = _summary_cards(project_root=root, manifest={"phases": [
        {"name": "simulation", "status": "ok"}]}, analysis_manifest={},
        sim_manifest={"parameters": {"duration_ns": 0.1}})
    [card] = [c for c in cards if c.label == "Production"]
    assert (card.value, card.detail) == ("300 ps", "in 3 pieces")


def test_its_card_gives_the_whole_production():
    """The card an AI app's list of studies reads, from the pieces."""
    from fastmdxplora.gui.workspace import card_of

    card = card_of(_extended())
    assert (card["production_ns"], card["pieces"]) == (0.3, 3)
    assert "pieces" not in card_of(_study())


def test_its_times_are_the_pieces_production():
    """The Overview's strip, the Agent and the Viewer read these: a study of
    three pieces said 100 ps, its first piece's, beside a card saying 300."""
    from fastmdxplora.gui.simulated_time import equilibration_said, simulated_times

    times = simulated_times(_extended(), {"status": "completed", "stage": "report"})
    assert abs(times["production_ns"] - 0.3) < 1e-9 and times["pieces"] == 3
    assert equilibration_said(times) == "in 3 pieces"


def test_its_wall_time_has_every_piece_in_it():
    """Each piece is a run with its own Manifest; the study's own has the
    first piece alone."""
    from fastmdxplora.gui.simulated_time import phases_wall_seconds, simulated_times

    root = _extended()
    for folder, minutes in ((root, 10), (root / "segment-001", 7), (root / "segment-002", 5)):
        (folder / "manifest.json").write_text(json.dumps({"phases": [
            {"name": "simulation", "status": "ok", "started_at": "2026-10-05T10:00:00Z",
             "finished_at": f"2026-10-05T10:{minutes:02d}:00Z"}]}), encoding="utf-8")
    assert phases_wall_seconds(root) == 22 * 60
    assert simulated_times(root, {"status": "completed"})["wall_s"] == 22 * 60
    assert simulated_times(root, {"status": "running"})["wall_s"] is None
