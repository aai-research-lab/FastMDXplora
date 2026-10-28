"""A fix is run only as the page showed it, read from the study's own records.

`/api/fix` ran a fix read without a held read, where the page's fixes
(`/api/fixes`) were read in one, and then failed on every study (a name the
route never set). It now runs the fix shown, and refuses one that differs
from it or was not shown. What would fix a study is read from its own
records: a Manifest, batch manifest or free energy record linked out of it
was read and its words shown as the remedy's; such a record is now named
and the remedy says it was not read, with nothing from it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from fastmdxplora.gui.exploration import DashboardRuntime
from tests.test_a_remedy_is_not_priced_from_a_record_not_read import (
    NOT_READ,
    _linked_out,
    _stopped,
    _thin,
)
from tests.test_the_study_pages_read_only_the_study import _OPENED, _WATCHED, _watch

MARK = "OUTSIDE-FIX-not-for-any-page"
REFUSED = "That fix is not the one the page showed"


def _asked(runtime: DashboardRuntime, *, fix: int | None = None) -> tuple[dict, dict | None, list]:
    """The page's fixes, and what running fix ``fix`` answered, on loopback;
    with the commands that would have been started."""
    from tests.test_a_public_dashboard_answers_only_what_it_lists import _serving

    with _serving(runtime, allow_control=True) as url, \
            mock.patch.object(DashboardRuntime, "_spawn",
                              return_value={"launched": True}) as spawn:
        with urllib.request.urlopen(url + "/api/fixes", timeout=30) as response:
            page = json.loads(response.read())
        ran = None
        if fix is not None:
            request = urllib.request.Request(
                url + "/api/fix", data=json.dumps({"index": fix}).encode(), method="POST",
                headers={"Content-Type": "application/json", "Origin": url})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    ran = json.loads(response.read())
            except urllib.error.HTTPError as exc:
                ran = {"status": exc.code}
    return page, ran, [call.args[0] for call in spawn.call_args_list]


def _runtime(root: Path) -> DashboardRuntime:
    return DashboardRuntime(workspace_root=root, exploration_root=root.parent, active_root=root)


def _said_outside(target: Path) -> None:
    """The record outside the study says something else, marked."""
    record = json.loads(target.read_text(encoding="utf-8"))
    for phase in record.get("phases") or []:
        if phase.get("status") == "error":
            phase["refusal"]["message"] = phase["message"] = f"{MARK} stopped at step 999,999."
    target.write_text(json.dumps(record), encoding="utf-8")


def test_the_fix_shown_is_run(tmp_path):
    root = _stopped(tmp_path)
    page, ran, started = _asked(_runtime(root), fix=0)
    (fix,) = page["fixes"]
    assert fix["runnable"] and fix["code"] == "simulation.run.stopped"
    assert ran["ok"] is True
    assert [command[-2:] for command in started] == [["resume", str(root.resolve())]]


def test_a_fix_that_is_not_the_one_shown_is_refused(tmp_path):
    """The Manifest linked out: the page shows that it was not read, and the
    resume that the record read through the link would give is not run.
    The study is walked first (`server._fix_walked`), so it is refused by
    the link, before the fix shown is compared; a study with no link out is
    refused as not the fix shown (below)."""
    root = _stopped(tmp_path)
    _linked_out(root, tmp_path, "manifest.json")
    _said_outside(tmp_path / "outside" / "manifest.json")

    page, ran, started = _asked(_runtime(root), fix=0)
    assert MARK not in json.dumps(page)
    assert page["unavailable"] == ["manifest.json"]
    (fix,) = page["fixes"]
    assert not fix["runnable"] and fix["command"] == ""
    assert ran["ok"] is False and ran["error"].startswith(
        "manifest.json is a link out of the study"), ran
    assert MARK not in json.dumps(ran) and started == []


def test_a_fix_not_shown_is_refused(tmp_path):
    """The page shows no study (the open one's data set aside): a fix of
    it the page never showed is not run."""
    root = _stopped(tmp_path)
    runtime = _runtime(root)
    runtime.data_stale = True
    page, ran, started = _asked(runtime, fix=0)
    assert page.get("fixes") in (None, [])
    assert ran["ok"] is False and ran["error"].startswith(REFUSED)
    assert started == []


@pytest.mark.parametrize("record", ["manifest.json"])
def test_a_manifest_linked_out_is_named_and_not_read(tmp_path, record):
    root = _stopped(tmp_path)
    _linked_out(root, tmp_path, record)
    _said_outside(tmp_path / "outside" / record)
    _watch(tmp_path / "outside")
    try:
        page, _, _ = _asked(_runtime(root))
        opened = list(_OPENED)
    finally:
        _WATCHED.clear()
    assert opened == []
    assert MARK not in json.dumps(page) and "999,999" not in json.dumps(page)
    assert page["unavailable"] == [record]
    (fix,) = page["fixes"]
    assert fix["code"] == "environment.workspace.outside" and fix["where"] == "the study"
    assert fix["why"] == f"{record} {NOT_READ}"
    assert fix["fix"] == ("What would fix it is not given: what stopped the study is said "
                          "in that record.")


def test_a_batch_manifest_linked_out_is_named_and_not_read(tmp_path):
    root = _thin(tmp_path)
    _linked_out(root, tmp_path, "batch_manifest.json")
    page, _, _ = _asked(_runtime(root))
    assert page["unavailable"] == ["batch_manifest.json"]
    (fix,) = page["fixes"]
    assert fix["why"] == f"batch_manifest.json {NOT_READ}" and fix["command"] == ""


def test_a_free_energy_record_linked_out_is_named_and_not_read(tmp_path):
    root = _thin(tmp_path)
    _linked_out(root, tmp_path, "pmf.json")
    record = tmp_path / "outside" / "pmf.json"
    record.write_text(json.dumps({**json.loads(record.read_text(encoding="utf-8")),
                                  "refused": f"{MARK} refused."}), encoding="utf-8")
    page, _, _ = _asked(_runtime(root))
    assert MARK not in json.dumps(page)
    assert page["unavailable"] == ["pmf.json"]
    (fix,) = page["fixes"]
    assert fix["where"] == "the free energy" and fix["why"] == f"pmf.json {NOT_READ}"
    assert fix["command"] == "" and fix["price"] is None


@pytest.mark.parametrize("record", ["manifest.json", "batch_manifest.json", "pmf.json"])
def test_outside_a_held_read_the_remedies_are_as_before(tmp_path, record):
    """The command line follows a link as it always did."""
    from fastmdxplora.remedies import remedies_of

    root = _stopped(tmp_path) if record == "manifest.json" else _thin(tmp_path)
    before = [remedy.as_text() for remedy in remedies_of(root)]
    _linked_out(root, tmp_path, record)
    assert [remedy.as_text() for remedy in remedies_of(root)] == before
