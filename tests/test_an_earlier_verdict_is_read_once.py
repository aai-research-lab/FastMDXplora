"""A mean an earlier version called determined on too few samples is read
with one verdict on every page.

For a short study (use review of `ci-850`, 10-28) the Overview said its RMSD
mean "Determined", noting the record was from an earlier version, while the
RMSD card on the Analysis page said "too few to determine the mean": the
Overview read the record's verdict, the card applied this version's count of
independent samples to it. Where the record holds fewer than this version
determines a mean on, that is the verdict both read
(`analysis_overview.too_few_by_these_rules`), the error not given.
"""

from __future__ import annotations

from tests.test_a_run_s_overview_leads_with_what_it_needs import _a_run
from tests.test_a_study_of_runs_is_shown_as_one import _recorded

#: An RMSD mean as a version before `rules` recorded it: an error given on
#: 7.3 independent samples, and no reason withheld.
EARLIER = {"discard": 66, "statistical_inefficiency": 4.65, "effective_samples": 7.3,
           "mean": 0.0077659, "standard_error": 0.0010031, "standard_deviation": 0.004,
           "n_frames": 100, "unit": "nm"}

WHY = "7 independent samples, fewer than the 10 this version determines a mean on."


def test_the_overview_the_table_and_the_card_read_one_verdict(tmp_path) -> None:
    from fastmdxplora.gui.analysis_overview import overview_of
    from fastmdxplora.gui.overview_view import overview_payload
    from fastmdxplora.gui.report_dashboard import _dashboard_summaries

    root = _a_run(tmp_path, dict(EARLIER), _recorded(1.2))
    tile = next(t for t in overview_payload(root)["tiles"] if t.get("analysis") == "rmsd")
    assert (tile["determined"], tile["reason"]) == (False, WHY)
    assert tile["said"] == "0.007766 nm"
    row = next(r for r in overview_of(root)["rows"] if r["analysis"] == "rmsd")
    (quantity,) = row["quantities"]
    assert not quantity["determined"] and not quantity["judged_earlier"]
    assert quantity["why"].startswith(WHY + " Its record, from an earlier version, called it "
                                            "determined; Analyze again")
    assert _dashboard_summaries(root)["RMSD"].summary == (
        "mean 0.007766 nm, not determined: " + WHY.rstrip("."))
    # A record this version judged keeps its verdict.
    rg = next(r for r in overview_of(root)["rows"] if r["analysis"] == "rg")
    assert rg["quantities"][0]["determined"]
