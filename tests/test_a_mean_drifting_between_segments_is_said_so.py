"""A mean withheld as drifting between a joined run's segments is said so.

The report called such a mean equilibrated ("yes", the check passed, no
word of drift) because it recognised a withheld mean as still moving by the
words "still drifting", which the joined refusal does not use; it now reads
the record's code. The test fits each segment mean against where it sits in
time, not its index, so a drift steady in time over segments of 500, 500 and
4,000 frames is a straight line and is called. And the Overview's card leads
with the verdict, its p and its remedy, while its lead names the mean.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.statistics import mean_record, summarise_segments


def _ar1(n: int, phi: float, rng: np.random.Generator) -> np.ndarray:
    noise = rng.normal(0.0, np.sqrt(1.0 - phi * phi), n)
    x = np.empty(n)
    x[0] = rng.normal()
    for i in range(1, n):
        x[i] = phi * x[i - 1] + noise[i]
    return x


def _climbing(pieces: int, length: int = 400, step: float = 0.4, seed: int = 3):
    """A joined run whose pieces each sit ``step`` frame SDs above the last,
    stationary within each: the shape of an energy climbing piece by piece
    under a weak thermostat (Reviewer 2's tri-alanine study, 100 rows a
    piece, -22,360, -22,321, -22,259, ... kJ/mol). Small against the
    frames' spread, so the thirds of the frames averaged do not move by
    twice it."""
    rng = np.random.default_rng(seed)
    values = -22300.0 + 20.0 * (_ar1(pieces * length, 0.5, rng)
                                + np.repeat(step * np.arange(pieces), length))
    joins = [length * i for i in range(1, pieces)]
    return values, joins


def _saved(record: dict) -> dict:
    """The record as an analysis saves it and a reader loads it."""
    return json.loads(json.dumps(record))


def _study(tmp_path: Path, values: np.ndarray, joins: list[int]) -> Path:
    root = tmp_path / "study"
    (root / "simulation").mkdir(parents=True)
    with (root / "simulation" / "energy.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Potential Energy (kJ/mole)", "Temperature (K)"])
        for value in values:
            writer.writerow([f"{value:.4f}", "300.0"])
    record = _saved(mean_record(values, joins=joins))
    folder = root / "analysis" / "thermodynamics"
    folder.mkdir(parents=True)
    (folder / "options.json").write_text(json.dumps({"analysis": "thermodynamics", "findings": {
        "thermodynamics": {"samples": int(values.size),
                           "potential_energy": {**record, "units": "kJ/mol"}}}}),
        encoding="utf-8")
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "load_kwargs": {"stride": None, "first": None, "saving_interval_ps": 0.2},
        "results": {"thermodynamics": {"status": "ok"}}}), encoding="utf-8")
    return root


@pytest.mark.parametrize("pieces", [4, 6])
def test_the_report_does_not_call_a_mean_drifting_between_segments_equilibrated(
        tmp_path, pieces) -> None:
    from fastmdxplora.report.document import _convergence_section

    values, joins = _climbing(pieces)
    record = _saved(mean_record(values, joins=joins))
    # The record is withheld as drifting between the segments (three or four
    # by the slope test, five or more by the order test).
    assert record["drift_test"] == ("slope_against_errors_and_scatter" if pieces < 5
                                    else "permutation")
    said = _convergence_section(_study(tmp_path, values, joins))
    row = next(line for line in said.splitlines() if line.startswith("| potential energy |"))
    assert row.rstrip().endswith("| no |"), row
    assert ("| Each observable equilibrates before it is averaged | **failed** | "
            "still moving: potential energy |") in said
    finding = next((line for line in said.splitlines()
                    if line.startswith("- Not equilibrated at the scale of the whole run: "
                                       "potential energy.")), "")
    assert "Drifting between its joined segments (p " in finding, said
    assert "the remedy is a longer run" in finding
    assert "Still moving in one direction" not in said


def test_a_record_saved_before_its_code_was_is_read_by_its_drift_test() -> None:
    from fastmdxplora.report.convergence import assess_run

    values, joins = _climbing(4)
    record = _saved(mean_record(values, joins=joins))
    record.pop("withheld_code", None)
    assessed = assess_run({"potential_energy": values},
                          records={"potential_energy": record})
    assert assessed["observables"]["potential_energy"]["equilibrated"] is False
    check = next(c for c in assessed["checks"] if c["check"] == "equilibrated")
    assert check["passed"] is False
    assert any(line.startswith("Not equilibrated at the scale of the whole run")
               for line in assessed["findings"])


def test_a_drift_steady_in_time_over_unequal_segments_is_called() -> None:
    # Segments of 500, 500 and 4,000 frames and a ramp linear in time: the
    # three means sit at the centres of what each averages, far off a
    # straight line in segment index, so that fit was refused and the drift
    # passed; in time they lie on one.
    lengths = [500, 500, 4000]
    joins = [500, 1000]
    n = sum(lengths)
    called = 0
    runs = 60
    for seed in range(runs):
        rng = np.random.default_rng(seed)
        x = _ar1(n, 0.8, rng) + 2.4 * np.linspace(0.0, 1.0, n)
        _, why = summarise_segments(x, joins)
        called += getattr(getattr(why, "refusal", None), "code", "") == "analysis.sampling.drifting"
    # Fitted against index: 0 of 60. Against time: 9 of 60 (counted; the
    # rest are mostly withheld for the long segment's own trend).
    assert called >= 6, called


def test_the_line_s_change_is_said_over_its_span_in_time() -> None:
    # Segment means on a line in time over unequal segments (0.2, 0.6 and
    # 2.4 frame SDs at the centres of 500, 500 and 4,000 frames): the line's
    # change is its slope over the span of the centres, 2.2, as the first to
    # last mean says, not its slope a segment times two.
    lengths = [500, 500, 4000]
    rng = np.random.default_rng(11)
    x = _ar1(sum(lengths), 0.8, rng)
    x += np.repeat(np.array([250.0, 750.0, 3000.0]) * 4.0 / 5000.0, lengths)
    _, why = summarise_segments(x, [500, 1000])
    assert why is not None and why.refusal.code == "analysis.sampling.drifting", why
    said = str(why)
    change = float(said.split("changes by ")[1].split(" ")[0])
    span = float(said.split("first to last mean ")[1].split(")")[0])
    assert change == pytest.approx(2.2, rel=0.1)
    assert change == pytest.approx(span, rel=0.05)


def test_the_refusal_leads_with_its_verdict_p_and_remedy() -> None:
    from fastmdxplora.gui.overview_view import _in_a_line

    values, joins = _climbing(4)
    record = _saved(mean_record(values, joins=joins))
    line = _in_a_line(record["not_a_measurement"])
    assert line.startswith("Drifting between its joined segments (p ")
    assert line.endswith("so the remedy is a longer run.")
    # Two lines of the card's width at 390 px hold it.
    assert len(line) <= 100, line


def test_the_overview_s_lead_names_the_mean_drifting(tmp_path) -> None:
    from fastmdxplora.gui.overview_view import overview_payload

    values, joins = _climbing(4)
    payload = overview_payload(_study(tmp_path, values, joins))
    tile = next(t for t in payload["tiles"] if t["label"] == "Potential energy")
    assert tile["determined"] is False
    assert tile["reason"].startswith("Drifting between its joined segments (p ")
    assert ("Potential energy is drifting between the joined run's segments, so the "
            "remedy is a longer run") in payload["lead"]["said"], payload["lead"]


@pytest.mark.parametrize("scatter, count, plain", [
    (2.5, 3, True),   # Q 5.0 on 2 degrees of freedom: p 0.08
    (3.2, 3, False),  # Q 6.4: p 0.04
    (2.5, 4, True),   # Q 7.5 on 3: p 0.06
])
def test_the_stronger_qualification_is_said_only_beyond_chance(scatter, count, plain) -> None:
    # Equal segment errors and offsets in no order whose Cochran's Q is
    # ``scatter`` times count - 1: a ratio above 2 arises by chance in 1 of 7
    # stationary runs of three segments, so "This does not establish
    # stationarity" is said only where Q stands against its chi-square.
    from fastmdxplora.statistics import Equilibrated, heterogeneity_ratio

    shape = np.array([1.0, -1.0, 0.0, 0.0][:count]) if count == 3 else \
        np.array([1.0, -1.0, -1.0, 1.0])
    shape = shape - shape.mean()
    error = 0.01
    weights = np.full(count, 1.0 / error**2)
    shape *= np.sqrt(scatter / heterogeneity_ratio(shape, weights))
    assert heterogeneity_ratio(shape, weights) == pytest.approx(scatter)
    pieces = [Equilibrated(discard=0, inefficiency=1.0, effective_samples=400.0,
                           mean=float(m), standard_error=error, standard_deviation=0.2)
              for m in shape]
    from unittest import mock

    with mock.patch("fastmdxplora.statistics.summarise",
                    side_effect=[(p, None) for p in pieces]):
        pooled, why = summarise_segments(np.zeros(400 * count), [400 * i for i in range(1, count)])
    assert why is None and pooled is not None
    assert pooled.qualification.startswith(f"The segment means scatter {scatter:.1f} times")
    assert ("This does not establish stationarity" in pooled.qualification) is not plain


def test_two_segments_that_disagree_are_not_said_to_be_free_of_drift() -> None:
    # Two means have no order to test: 177.8 then 185.5 K was said "That is
    # not drift".
    rng = np.random.default_rng(7)
    values = np.concatenate([_ar1(2000, 0.5, rng), _ar1(2000, 0.5, rng) + 1.5])
    pooled, why = summarise_segments(values, [2000])
    assert why is None and pooled is not None and pooled.qualification
    assert "not drift" not in pooled.qualification
    assert "two segments cannot tell a drift from offsets between them" in pooled.qualification


#: The smallest steady drift, in pooled errors first segment to last, the
#: rule calls in 80% of runs of equal segments (counted by
#: tests/_short_segment_drift_check.py).
UNSEEN = {3: 41, 4: 16}


def _unseen_said(values, joins, pieces):
    """The note's sentence for a run of equal segments all tested, its
    amount the counted X times the error of the segments' inverse-variance
    pooled mean, scaled by their scatter about the line where it exceeds
    what their errors allow."""
    from fastmdxplora.statistics import summarise

    edges = [0, *joins, len(values)]
    pieces_ = [summarise(values[a:b], minimum_effective_samples=0.0)[0]
               for a, b in zip(edges, edges[1:])]
    means = np.array([p.mean for p in pieces_])
    w = 1 / np.array([p.standard_error for p in pieces_]) ** 2
    x = np.array([a + p.discard + (b - a - p.discard - 1) / 2
                  for (a, b), p in zip(zip(edges, edges[1:]), pieces_)])
    fit = np.polyfit(x, means, 1, w=np.sqrt(w))
    plain = means - np.polyval(np.polyfit(x, means, 1), x)
    # The larger of the weighted line's chi-square and the unweighted line's
    # scatter over the means' mean error variance, per degree of freedom.
    ratio = max(float(w @ (means - np.polyval(fit, x)) ** 2),
                float(plain @ plain) / float(np.mean(1 / w))) / (pieces - 2)
    error = 1 / np.sqrt(w.sum())
    amount = UNSEEN[pieces] * error * np.sqrt(max(1.0, ratio))
    errors = 1 / np.sqrt(w)
    unequal = ("" if errors.max() - errors.min() <= 0.1 * errors.max()
               else ", and a larger one with segment errors as unequal as these")

    def two(v):
        r = float(f"{v:.2g}")
        return f"{r:,.{max(0, 1 - int(np.floor(np.log10(r))))}f}"

    basis = f"{UNSEEN[pieces]} times the error of their pooled mean, {two(error)} kJ/mol"
    if ratio > 1 and f"{np.sqrt(ratio):.1f}" != "1.0":
        basis += (f", times {np.sqrt(ratio):.1f} for their scatter about a straight line "
                  f"beyond that error, a scale estimated from only {pieces} means")
    return (f"With {pieces} segments, a steady drift smaller than about {two(amount)} kJ/mol "
            f"from the first segment's mean to the last's could go unseen{unequal} ({basis})")


@pytest.mark.parametrize("pieces", [3, 4])
def test_three_or_four_segments_not_called_drifting_say_how_large_a_drift_could_go_unseen(
        tmp_path, pieces) -> None:
    # The rule holds its 5% false rate at three and four segments, so it
    # calls only a large drift there: a mean it does not call is said with
    # how large a steady drift that few could miss, on every reader of it.
    from fastmdxplora.gui.analysis_overview import overview_of
    from fastmdxplora.gui.overview_view import overview_payload
    from fastmdxplora.gui.records_answer import answer_from_the_records
    from fastmdxplora.report.document import _convergence_section

    values, joins = _climbing(pieces, step=0.0)
    record = _saved(mean_record(values, joins=joins))
    assert not record.get("not_a_measurement")
    said = _unseen_said(values, joins, pieces)
    root = _study(tmp_path, values, joins)
    tile = next(t for t in overview_payload(root)["tiles"] if t["label"] == "Potential energy")
    row = next(q for r in overview_of(root)["rows"] for q in r["quantities"]
               if q.get("label") == "Potential energy")
    assert tile["determined"] and tile["unseen"] == said, (tile, said)
    # The same text on the Overview's card and the Analysis page.
    assert row["unseen"] == said and row["noted"] == said
    assert f"Potential energy: {said}." in _convergence_section(root)
    assert said in answer_from_the_records(root, "found")


@pytest.mark.parametrize("pieces, step", [(5, 0.0), (4, 0.4), (2, 0.0)])
def test_no_unseen_drift_is_said_at_five_where_drift_is_called_or_at_two(
        tmp_path, pieces, step) -> None:
    from fastmdxplora.gui.overview_view import overview_payload

    values, joins = _climbing(pieces, step=step)
    root = _study(tmp_path, values, joins)
    tile = next(t for t in overview_payload(root)["tiles"] if t["label"] == "Potential energy")
    assert "could go unseen" not in (tile["noted"] or "")
    assert "drift_unseen" not in _saved(mean_record(values, joins=joins))
    # The control: at four segments held level, it is said.
    values, joins = _climbing(4, step=0.0)
    assert "drift_unseen" in _saved(mean_record(values, joins=joins))


def test_the_unseen_drift_is_the_counted_one() -> None:
    # One derivation: the constant the note says is the helper's count.
    from fastmdxplora import statistics
    from tests._short_segment_drift_check import unseen_in_pooled_errors

    for pieces in (3, 4):
        counted = unseen_in_pooled_errors([1] * pieces)["in_pooled_errors"]
        assert counted == UNSEEN[pieces]
        assert getattr(statistics, "DRIFT_UNSEEN_IN_POOLED_ERRORS", {}).get(pieces) == counted


@pytest.mark.parametrize("question", ["long_enough", "strengthen"])
def test_the_records_answer_names_a_mean_drifting_between_segments(tmp_path, question) -> None:
    # Told apart by its record's code, not its words: the answer said no
    # figure was recorded and that analysing again records it, which it
    # does not for a drift between segments.
    from fastmdxplora.gui.records_answer import answer_from_the_records

    values, joins = _climbing(4)
    said = answer_from_the_records(_study(tmp_path, values, joins), question)
    assert ("Potential energy is drifting between the joined run's segments, so the "
            "remedy is a longer run; no record says how much longer.") in said, said
    assert "Analysing it again with this release records it" not in said


def test_what_would_support_it_names_a_mean_drifting_between_segments(tmp_path) -> None:
    # It named only the means withheld for want of sampling; a drifting one
    # had its remedy only in its finding line above.
    from fastmdxplora.report.document import _convergence_section

    values, joins = _climbing(4)
    said = _convergence_section(_study(tmp_path, values, joins))
    support = next((line for line in said.splitlines()
                    if line.startswith("**What would support it.**")
                    or line.startswith("Potential energy is drifting")), "")
    assert ("Potential energy is drifting between the joined run's segments, so the "
            "remedy is a longer run; no record says how much longer.") in support, said


def test_the_report_s_first_paragraph_gives_an_extended_study_s_joined_length() -> None:
    # It read the first piece's record alone: "simulated for 100 ps" of 300.
    import json

    from fastmdxplora.report.document import _study_in_one_paragraph
    from tests.test_an_extended_study_is_said_at_its_length import _extended

    root = _extended()
    record = root / "simulation" / "simulation_parameters.json"
    sim = json.loads(record.read_text(encoding="utf-8"))
    sim["parameters"]["duration_ns"] = 0.1
    record.write_text(json.dumps(sim), encoding="utf-8")
    said = _study_in_one_paragraph(root)
    assert "simulated for 300 ps in 3 pieces at 300 K" in said, said
    assert "for 100 ps" not in said


def _real(name: str):
    """A real joined run's series and joins (``tests/data/drift_short_real_runs.json``):
    a stationary tri-alanine study's temperature at 4 pieces of 20 ps, and
    a drifting one's (its thermostat at 300 K, friction 0.002/ps)
    temperature and RMSD at 3 pieces."""
    data = json.loads((Path(__file__).parent / "data" / "drift_short_real_runs.json")
                      .read_text(encoding="utf-8"))[name]
    return np.array(data["values"], dtype=float), data["joins"]


def test_a_determined_mean_whose_joined_summary_was_withheld_says_what_was_tested() -> None:
    # A real stationary run at 4 pieces: its first piece holds 15 independent
    # samples, too few to resolve its correlation, so the joined summary is
    # withheld; the drift test ran on the other 3 and did not call drift, and
    # the whole series' mean stands determined, 301.0 +/- 1.4 K. It said
    # nothing of what the test could miss.
    from fastmdxplora.statistics import withheld_reason

    values, joins = _real("stationary_4_pieces_temperature_K")
    record = _saved(mean_record(values, joins=joins))
    assert withheld_reason(record) is None
    said = _unseen_of(record, "K")
    assert said.startswith(
        "With 3 of its 4 segments tested (1 left out, numbered from zero: 0, its correlation "
        "not resolved), a steady drift smaller than about 32 K from the first segment's mean "
        "to the last's could go unseen, and a larger one with segment errors as unequal as "
        "these (41 times the error of their pooled mean, 0.79 K)"), said


def test_a_determined_mean_with_too_few_segments_tested_says_drift_was_not_tested(
        tmp_path) -> None:
    # A real drifting run at 3 pieces: RMSD 0.0065, 0.0159, 0.0208 nm by
    # piece, its middle piece unresolved, so 2 could be tested; the mean
    # stood determined, 0.02087 +/- 0.00068 nm, with no word of drift.
    from fastmdxplora.gui.analysis_overview import overview_of

    values, joins = _real("drifting_3_pieces_rmsd_nm")
    record = _saved(mean_record(values, joins=joins))
    said = _unseen_of(record, "nm")
    assert said == ("Drift between its 3 segments was not tested: only 2 could be (1 left out, "
                    "numbered from zero: 1, its correlation not resolved); longer segments "
                    "would let it be"), said
    root = tmp_path / "study"
    (root / "analysis" / "rmsd").mkdir(parents=True)
    (root / "analysis" / "rmsd" / "options.json").write_text(json.dumps(
        {"analysis": "rmsd", "findings": {"mean": {**record, "unit": "nm"}}}), encoding="utf-8")
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps(
        {"results": {"rmsd": {"status": "ok"}}}), encoding="utf-8")
    row = next(q for r in overview_of(root)["rows"] for q in r["quantities"])
    assert row["determined"] and row["unseen"] == said


def test_the_figure_is_scaled_where_the_means_scatter_beyond_their_errors() -> None:
    # The real drifting run's temperature at 3 pieces, 177.79, 185.45 and
    # 186.61 K over errors of 0.6 to 0.7 K: their scatter about the line is
    # 3.4 times their errors (Q 11.9 on 1 degree of freedom), and there the
    # rule is decided by that scatter. Said "15 K" from errors that do not
    # hold; the rule calls four in five drifts only of about 54 K, and the
    # scale rests on 3 means. Errors of 0.60 to 0.68 K differ by more than a
    # tenth.
    values, joins = _real("drifting_3_pieces_temperature_K")
    record = _saved(mean_record(values, joins=joins))
    said = _unseen_of(record, "K")
    assert said == ("With 3 segments, a steady drift smaller than about 54 K from the first "
                    "segment's mean to the last's could go unseen, and a larger one with segment "
                    "errors as unequal as these (41 times the error of their pooled mean, 0.37 K, "
                    "times 3.5 for their scatter about a straight line beyond that error, a "
                    "scale estimated from only 3 means)"), said


def _unseen_of(record, unit):
    """The note through the one reader every page uses, found by name so a
    version without it says nothing rather than failing to import."""
    from fastmdxplora import statistics

    reader = getattr(statistics, "drift_unseen_said", None)
    return reader(record, unit) if reader else ""


def _with_an_image_note(record: dict) -> dict:
    return {**record, "solute_image": {"frames_used": 1200, "frames_under_cutoff": 300,
                                       "cutoff_nm": 1.0, "counted_over": "frames"}}


def test_the_card_says_the_drift_note_whole_beside_an_image_note(tmp_path) -> None:
    # The card's note took two lines and cut the drift note before "could go
    # unseen", and an image note beside it would have hidden it whole.
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    values, joins = _climbing(3, step=0.0)
    root = _study(tmp_path, values, joins)
    options = root / "analysis" / "thermodynamics" / "options.json"
    document = json.loads(options.read_text(encoding="utf-8"))
    energy = document["findings"]["thermodynamics"]["potential_energy"]
    document["findings"]["thermodynamics"]["potential_energy"] = _with_an_image_note(energy)
    options.write_text(json.dumps(document), encoding="utf-8")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    seen = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            for width in (1440, 390):
                for scheme in ("light", "dark"):
                    page = browser.new_page(viewport={"width": width, "height": 900},
                                            color_scheme=scheme)
                    page.set_default_timeout(60000)
                    page.goto(session.url + "#overview", wait_until="domcontentloaded")
                    page.wait_for_selector("#overview-tiles .overview-tile")
                    seen.append((width, scheme, page.evaluate("""() => {
                      const tile = [...document.querySelectorAll('#overview-tiles .overview-tile')]
                        .find((t) => t.textContent.includes('Potential energy'));
                      const note = tile && tile.querySelector('.tile-unseen');
                      const image = tile && tile.querySelector('.tile-noted');
                      const whole = (el) => !!el && el.offsetParent !== null
                        && el.scrollHeight <= el.clientHeight + 1 && el.scrollWidth <= el.clientWidth + 1;
                      return {note: note ? note.innerText : '', whole: whole(note),
                              image: image ? image.innerText : ''};
                    }""")))
                    page.close()
            browser.close()
    finally:
        session.server.shutdown()
    for width, scheme, found in seen:
        assert found["whole"] and "could go unseen" in found["note"] and \
            "(41 times the error of their pooled mean" in found["note"] and \
            found["note"].endswith(")."), (width, scheme, found)
        assert found["image"].startswith("may be biased"), (width, scheme, found)


def _rmsd_study(tmp_path: Path, record: dict) -> Path:
    root = tmp_path / "study"
    (root / "analysis" / "rmsd").mkdir(parents=True)
    (root / "analysis" / "rmsd" / "options.json").write_text(json.dumps(
        {"analysis": "rmsd", "findings": {"mean": {**record, "unit": "nm"}}}), encoding="utf-8")
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps(
        {"results": {"rmsd": {"status": "ok"}}}), encoding="utf-8")
    return root


def test_the_agent_s_context_its_chips_and_compare_carry_the_note(tmp_path) -> None:
    # The Agent's own context for the AI model, its citation chips and
    # Compare gave a mean of three segments as determined with the image
    # note alone.
    from fastmdxplora.gui.agent_panel import _results_summary
    from fastmdxplora.gui.citations import cited_findings
    from fastmdxplora.gui.runs_compared import _measure

    values, joins = _climbing(3, step=0.0)
    values = 0.2 + (values + 22300.0) / 2000.0
    record = {**_saved(mean_record(values, joins=joins)), "unit": "nm"}
    said = _unseen_of(record, "nm")
    assert "could go unseen" in said
    root = _rmsd_study(tmp_path, record)
    assert said in _results_summary(root)
    chips = cited_findings("The RMSD is near 0.2 nm.", root)
    assert chips and said in chips[0]["noted"], chips
    measure = _measure("rmsd", [{"run_id": "a"}], {"a": {"rmsd": {"mean": record}}},
                       False, {"a": "a"})
    assert measure["cells"][0].get("unseen") == said, measure["cells"][0]


def test_the_first_paragraph_does_not_give_the_first_piece_s_length_for_a_piece_linked_out(
        monkeypatch) -> None:
    # A piece outside the study's folder is not read; the paragraph gave the
    # first piece's length as the study's.
    from fastmdxplora.refusals import OutsideWorkspace
    from fastmdxplora.report.document import _study_in_one_paragraph
    from fastmdxplora.simulation import resume
    from tests.test_an_extended_study_is_said_at_its_length import _extended

    root = _extended()
    record = root / "simulation" / "simulation_parameters.json"
    sim = json.loads(record.read_text(encoding="utf-8"))
    sim["parameters"]["duration_ns"] = 0.1
    record.write_text(json.dumps(sim), encoding="utf-8")

    def outside(_study):
        raise OutsideWorkspace("A piece of this study is outside the workspace.")

    monkeypatch.setattr(resume, "extended_production", outside)
    said = _study_in_one_paragraph(root)
    assert "for 100 ps" not in said, said
    assert "one of them outside the study's folder, so how long is not said here" in said


def _a_record(change: float, error: float, errors: list, scatter: float) -> dict:
    """A determined mean whose drift test ran on 3 equal pieces and did not
    call drift, as its record saves it."""
    return {"mean": 1.0, "standard_error": 0.1, "effective_samples": 400.0,
            "n_frames": 300, "rules": 5, "unit": "nm",
            "drift_unseen": {"segments": 3, "tested": 3, "left_out": "",
                             "counted_in_pooled_errors": 41, "scatter_about_line": scatter,
                             "in_pooled_errors": change / error, "pooled_error": error,
                             "change": change, "equal_segments": max(errors) - min(errors)
                             <= 0.1 * max(errors)}}


def test_equal_pieces_whose_errors_differ_say_a_larger_drift_could_go_unseen() -> None:
    # Three pieces of equal length, the middle one's error twice the others'
    # (its correlation time longer): the rule's 80% point is 61 pooled
    # errors, not 41, and the record judged them equal by their lengths. And
    # the scale is the scatter the rule decides on: an imprecise middle mean
    # off the line carries little weight in the weighted line's chi-square.
    from fastmdxplora.statistics import _drift_unseen, _line_scatter

    errors = np.array([1.0, 2.0, 1.0])
    means = np.array([0.0, 6.0, 1.0])
    centres = np.array([50.0, 150.0, 250.0])
    scatter = _line_scatter(means, 1 / errors**2, centres)
    # The unweighted line's residual variance over the means' mean error
    # variance, which the rule's own-scatter half judges the slope against,
    # and the weighted line's chi-square per degree of freedom, smaller.
    plain = means - np.polyval(np.polyfit(centres, means, 1), centres)
    weighted = means - np.polyval(np.polyfit(centres, means, 1, w=1 / errors), centres)
    assert scatter == pytest.approx(float(plain @ plain) / np.mean(errors**2))
    assert scatter > float(weighted @ (weighted / errors**2))
    unseen = _drift_unseen({"segments": 3, "tested": 3, "left_out": "", "called": False,
                            "pooled_error": 0.5, "line_scatter": scatter,
                            "lengths": [100, 100, 100], "errors": errors.tolist()})
    assert unseen["equal_segments"] is False


def test_a_scale_that_reads_1_point_0_is_not_said_and_one_said_says_its_means() -> None:
    from fastmdxplora.statistics import drift_unseen_said

    plain = drift_unseen_said(_a_record(41 * 1.04 * 0.01, 0.01, [1, 1, 1], 1.08), "nm")
    assert "times 1.0" not in plain and plain.endswith("(41 times the error of their pooled "
                                                       "mean, 0.010 nm)"), plain
    scaled = drift_unseen_said(_a_record(41 * 2.0 * 0.01, 0.01, [1, 1, 1], 4.0), "nm")
    assert scaled.endswith("times 2.0 for their scatter about a straight line beyond that "
                           "error, a scale estimated from only 3 means)"), scaled
