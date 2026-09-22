"""A study opened in the form keeps how it was to run.

The form offers controls for every run option and for how the runs are
scheduled, and a loaded study filled none of them: a study with
`execution.mode: parallel` opened here and started again ran sequentially,
and `verbose: true` came back off. And the form refused a bare name where
the analysis lists take a list, which the config file accepts, so a study
the command line opens failed to open here.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mdtraj")


def _loaded(tmp_path, config):
    import yaml

    from fastmdxplora.gui.config_builder import load_config_into_state

    path = tmp_path / "study.yml"
    path.write_text(yaml.safe_dump({"systems": [{"system": "1UBQ"}], **config}, sort_keys=False),
                    encoding="utf-8")
    return load_config_into_state(str(path))


def _built(state):
    from fastmdxplora.gui.config_builder import build_config

    # As the page submits it: the study and execution blocks under the
    # sentinel keys the run-option and execution controls read from.
    form = {"system": "1UBQ", "include_phase": state["include_phase"],
            "__run__": state.get("study") or {}, "__execution__": state.get("execution") or {}}
    form.update(state.get("phases") or {})
    return build_config(form)


def test_the_run_options_are_filled(tmp_path):
    loaded = _loaded(tmp_path, {"verbose": True, "explain": False, "budget_hours": 0.5})
    assert loaded["ok"]
    assert loaded["state"]["study"] == {"budget_hours": 0.5, "explain": False, "verbose": True}
    built = _built(loaded["state"])
    assert (built["verbose"], built["explain"], built["budget_hours"]) == (True, False, 0.5)


def test_how_the_runs_are_scheduled_is_filled(tmp_path):
    scheduling = {"mode": "parallel", "workers": 2, "continue_on_error": False}
    loaded = _loaded(tmp_path, {"execution": scheduling})
    assert loaded["ok"]
    assert loaded["state"]["execution"] == scheduling
    assert _built(loaded["state"])["execution"] == scheduling


@pytest.mark.parametrize("given, meant", [
    ("rmsd", ["rmsd"]),
    ("rmsd, rg", ["rmsd", "rg"]),
    (["rmsd", "rg"], ["rmsd", "rg"]),
])
def test_a_bare_name_for_an_analysis_list_opens(tmp_path, given, meant):
    from fastmdxplora.gui.config_builder import check_config

    loaded = _loaded(tmp_path, {"analysis": {"include": given}})
    assert loaded["ok"], loaded.get("error")
    assert loaded["state"]["analyses"] == meant
    assert check_config({"systems": [{"system": "1UBQ"}], "analysis": {"exclude": given}})["ok"]
