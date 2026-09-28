"""Hosted behind a service that runs studies on its own compute, the builder
offers Run on a GPU: the config is saved in the workspace beside the folder
it will write, and the service's page opens with both. Nothing is started
by the GUI; the page is the service's, and so is the compute."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from tests.test_a_hosted_gui_answers_only_its_proxy import NAME, SECRET, _ask, _serving

from fastmdxplora.gui.hosting import Hosting, HostingError

ORIGIN = {"Host": NAME, "Origin": f"https://{NAME}"}
STATE = {"system": "1UBQ", "include_phase": ["setup", "simulation"], "output": "ub-run"}


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    folder = tmp_path / "person"
    folder.mkdir()
    return folder


def _hosting(workspace: Path, runs_url: str = "/_mdx/runs") -> Hosting:
    return Hosting(workspace=workspace.resolve(), allowed_hosts=frozenset({NAME}),
                   secret=SECRET, runs_url=runs_url)


def _save(hosting: Hosting, state: dict, headers: dict = ORIGIN) -> tuple[int, dict]:
    with _serving(hosting) as (address, _):
        status, body = _ask(address, "/api/save-config", body=state, headers=headers)
    return status, json.loads(body or b"{}")


class TestTheButtons:

    def test_they_are_offered_with_the_service_page(self, workspace: Path) -> None:
        with _serving(_hosting(workspace)) as (address, _):
            status, body = _ask(address, "/", headers={"Host": NAME})
        page = body.decode()
        assert status == 200
        assert 'id="run-elsewhere" data-runs-url="/_mdx/runs"' in page
        assert 'id="run-as-is-elsewhere" data-runs-url="/_mdx/runs"' in page
        assert "__FASTMDX_RUN_" not in page

    def test_without_it_there_are_none(self, workspace: Path) -> None:
        with _serving(_hosting(workspace, "")) as (address, _):
            _, body = _ask(address, "/", headers={"Host": NAME})
        page = body.decode()
        assert "run-elsewhere" not in page and "__FASTMDX_RUN_" not in page

    def test_the_script_wires_them(self) -> None:
        script = (Path(__file__).parents[1] / "src" / "fastmdxplora" / "gui" / "static"
                  / "run-builder.js").read_text()
        assert '"/api/save-config"' in script
        assert "window.location.assign(base" in script
        assert '"run-elsewhere"' in script and '"run-as-is-elsewhere"' in script


class TestSavingTheConfig:

    def test_it_lands_beside_the_folder_it_will_write(self, workspace: Path) -> None:
        status, saved = _save(_hosting(workspace), STATE)
        assert status == 200 and saved["ok"], saved
        assert saved["config"] == "~/ub-run.yml" and saved["output"] == "~/ub-run"
        written = yaml.safe_load((workspace / "ub-run.yml").read_text())
        assert written["systems"][0]["system"] == "1UBQ"
        assert not (workspace / "ub-run").exists(), "nothing is run or made here"

    def test_a_second_is_never_written_over_the_first(self, workspace: Path) -> None:
        _save(_hosting(workspace), STATE)
        (workspace / "ub-run.yml").write_text("# mine\n")
        status, saved = _save(_hosting(workspace), STATE)
        assert saved["ok"] and saved["config"] == "~/ub-run-2.yml"
        assert (workspace / "ub-run.yml").read_text() == "# mine\n"

    def test_without_a_folder_it_takes_the_usual_name(self, workspace: Path) -> None:
        status, saved = _save(_hosting(workspace), {**STATE, "output": ""})
        assert saved["ok"] and saved["output"].startswith("~/")
        assert saved["config"] == saved["output"] + ".yml"

    def test_into_a_folder(self, workspace: Path) -> None:
        status, saved = _save(_hosting(workspace), {**STATE, "output": "~/studies/ub-run"})
        assert saved["ok"] and saved["config"] == "~/studies/ub-run.yml"

    def test_a_results_folder_there_already_is_refused(self, workspace: Path) -> None:
        (workspace / "ub-run").mkdir()
        status, saved = _save(_hosting(workspace), STATE)
        assert not saved["ok"] and "is in your workspace already" in saved["error"]
        assert not (workspace / "ub-run.yml").exists()

    @pytest.mark.parametrize("output", ["../elsewhere", "/etc/elsewhere", "~/../elsewhere", "~"])
    def test_nothing_outside_the_workspace(self, workspace: Path, output: str) -> None:
        status, saved = _save(_hosting(workspace), {**STATE, "output": output})
        assert not saved["ok"]
        assert list(workspace.parent.glob("*.yml")) == []
        assert list(workspace.glob("*.yml")) == []

    def test_a_config_that_would_be_refused_is_not_saved(self, workspace: Path) -> None:
        state = {**STATE, "simulation": {"duration_ns": -5}}
        status, saved = _save(_hosting(workspace), state)
        assert not saved["ok"] and saved["error"]
        assert list(workspace.iterdir()) == []

    def test_not_offered_without_the_service_page(self, workspace: Path) -> None:
        status, saved = _save(_hosting(workspace, ""), STATE)
        assert status == 404 and not saved["ok"]
        assert list(workspace.iterdir()) == []

    def test_not_from_another_site(self, workspace: Path) -> None:
        status, _ = _save(_hosting(workspace), STATE,
                          headers={"Host": NAME, "Origin": "https://evil.example"})
        assert status == 403
        assert list(workspace.iterdir()) == []


class TestTheFlag:

    @pytest.mark.parametrize("url", ["//evil.example/", "https://evil.example/runs",
                                     "javascript:alert(1)", '/x"onclick="alert(1)', "runs"])
    def test_only_a_path_on_this_site(self, workspace: Path, monkeypatch, url: str) -> None:
        monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
        with pytest.raises(HostingError, match="--runs-url .* is not a path on this site"):
            Hosting.from_environment(workspace, [NAME], "", url)

    def test_a_path_is_kept(self, workspace: Path, monkeypatch) -> None:
        monkeypatch.setenv("FASTMDX_PROXY_SECRET", SECRET)
        assert Hosting.from_environment(workspace, [NAME], "/_mdx/", "/_mdx/runs"
                                        ).runs_url == "/_mdx/runs"

    def test_it_needs_hosted(self, tmp_path: Path) -> None:
        done = subprocess.run(
            [sys.executable, "-m", "fastmdxplora.cli.main", "gui", "--runs-url", "/_mdx/runs",
             "--no-browser", "--port", "0"],
            capture_output=True, text=True, timeout=120, cwd=tmp_path,
            env={**os.environ, "FASTMDX_PROXY_SECRET": ""})
        assert done.returncode == 2
        assert "--runs-url apply only with --hosted" in done.stderr

    def test_help_names_it(self) -> None:
        done = subprocess.run([sys.executable, "-m", "fastmdxplora.cli.main", "gui", "--help"],
                              capture_output=True, text=True, timeout=120)
        assert "--runs-url PATH" in done.stdout


class TestTheEdges:

    def test_only_a_hosted_gui_saves_one(self, workspace: Path) -> None:
        from fastmdxplora.gui.exploration import DashboardRuntime

        runtime = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
        saved = runtime.save_config_for_elsewhere(STATE)
        assert not saved["ok"] and "Only a hosted GUI" in saved["error"]
        assert list(workspace.iterdir()) == []

    def test_after_a_thousand_it_asks_for_another_folder(self, workspace: Path) -> None:
        (workspace / "ub-run.yml").write_text("# 1\n")
        for n in range(2, 1000):
            (workspace / f"ub-run-{n}.yml").write_text(f"# {n}\n")
        status, saved = _save(_hosting(workspace), STATE)
        assert not saved["ok"] and "give another folder" in saved["error"]

    def test_the_flag_needs_hosted_in_process(self, tmp_path: Path, monkeypatch,
                                              capsys) -> None:
        from fastmdxplora.cli.main import main

        monkeypatch.chdir(tmp_path)
        assert main(["gui", "--runs-url", "/_mdx/runs", "--no-browser", "--port", "0"]) == 2
        assert "--runs-url apply only with --hosted" in capsys.readouterr().err
