"""An install with no terminal runs only the plan a person said yes to.

At a terminal a person reads the plan and types yes. A window that shows the
plan instead hands back a yes bound to that machine and to every command in
the plan, used once and gone after ten minutes; the machine is inspected
again before anything runs, and a plan that changed in between is not run.
"""

from __future__ import annotations

import pytest

from fastmdxplora.refusals import refusal_of
from fastmdxplora.remote.describe import plan_for
from fastmdxplora.remote.identity import CodeIdentity
from fastmdxplora.remote.installer import (
    PLAN_YES_S,
    confirmed_by,
    install,
    plan_digest,
    plan_yes,
)
from fastmdxplora.remote.survey import inspect_machine
from tests import test_a_study_travels_and_comes_back as travels

machine = travels.machine
machine_path = travels.machine_path

WANTED = CodeIdentity("1.0.dev2", commit="bbbbbbbbbbbb", dirty=False)


@pytest.fixture
def behind(machine, tmp_path):
    """The machine holding a checkout one commit behind this computer's,
    whose plan is two git commands."""
    env = machine.home / ".conda" / "envs" / "fastmdx-1.0"
    travels._tool(env / "bin" / "python",
                  f"echo '1.0.dev1|aaaaaaaaaaaa|no|{tmp_path}/checkout'")
    machine.env_python = env / "bin" / "python"
    return machine


def _shown(here):
    found = inspect_machine("box", transport=here.transport(), code=WANTED)
    plan = plan_for(found, WANTED)
    assert plan.route == "checkout"
    return plan


def _install(here, token, name="box"):
    return install(name, confirm=confirmed_by(name, token),
                   transport=here.transport(), code=WANTED)


def _ran_git(here) -> bool:
    return any("git -C" in c for c in here.commands)


def _refused_install(here, token, name="box") -> str:
    with pytest.raises(ValueError) as caught:
        _install(here, token, name)
    found = refusal_of(caught.value)
    assert found.code == "remote.install.unconfirmed"
    assert not _ran_git(here)
    return found.message


def test_the_plan_said_yes_to_runs(behind):
    token = plan_yes("box", _shown(behind))
    outcome = _install(behind, token)
    # Its first step ran (and failed: there is no checkout there).
    assert outcome.failed_step.startswith("git -C")
    assert _ran_git(behind)


def test_a_yes_is_used_once(behind):
    token = plan_yes("box", _shown(behind))
    _install(behind, token)
    behind.commands.clear()
    assert "used already" in _refused_install(behind, token)


def test_a_yes_never_given_runs_nothing(behind):
    _shown(behind)
    _refused_install(behind, "made-up")


def test_a_yes_expires(behind, monkeypatch):
    token = plan_yes("box", _shown(behind))
    import time

    later = time.monotonic() + PLAN_YES_S + 1
    monkeypatch.setattr("fastmdxplora.remote.installer.time.monotonic", lambda: later)
    assert "expired" in _refused_install(behind, token)


def test_a_yes_is_for_its_machine_only(behind):
    from fastmdxplora.remote.transport import Transport

    token = plan_yes("other", _shown(behind))
    with pytest.raises(ValueError) as caught:
        install("box", confirm=confirmed_by("box", token),
                transport=Transport("box", runner=behind.ssh, interactive=False),
                code=WANTED)
    assert "was for other" in refusal_of(caught.value).message
    assert not _ran_git(behind)


def test_a_plan_changed_since_it_was_shown_is_not_run(behind, tmp_path):
    token = plan_yes("box", _shown(behind))
    # The machine's checkout moved between showing the plan and the yes.
    travels._tool(behind.env_python,
                  f"echo '1.0.dev1|aaaaaaaaaaaa|no|{tmp_path}/moved'")
    assert "plan changed since it was shown" in _refused_install(behind, token)


def test_the_digest_covers_every_command_and_where_it_runs(behind):
    plan = _shown(behind)
    digest = plan_digest(plan, "box")
    assert plan_digest(plan, "other") != digest
    first = plan.steps[0]
    plan.steps[0] = type(first)("here", first.command)
    assert plan_digest(plan, "box") != digest


def test_a_plan_with_no_route_has_no_yes(machine):
    from fastmdxplora.remote.plan import InstallPlan

    with pytest.raises(ValueError) as caught:
        plan_yes("box", InstallPlan(version="1.0", blocked="nothing to do it with"))
    assert refusal_of(caught.value).code == "remote.machine.not_ready"


def test_a_step_here_reaches_the_machine_without_a_prompt(behind, monkeypatch):
    from fastmdxplora.remote.plan import InstallPlan, Step

    shown = InstallPlan(version="1.0.dev2", route="image",
                        steps=[Step("here", "true")])
    monkeypatch.setattr("fastmdxplora.remote.installer.plan_for",
                        lambda machine, code: shown)
    seen: list[dict] = []

    def here(command, **kwargs):
        seen.append(kwargs.get("env") or {})
        return behind.local(command)

    install("box", confirm=confirmed_by("box", plan_yes("box", shown)),
            transport=behind.transport(), code=WANTED, local_runner=here)
    assert "BatchMode=yes" in seen[0]["RSYNC_RSH"]
    assert "ControlPath=" in seen[0]["RSYNC_RSH"]


def test_an_image_is_copied_into_the_home_the_machine_has():
    from fastmdxplora.remote.identity import CodeIdentity
    from fastmdxplora.remote.plan import install_plan
    from fastmdxplora.remote.probe import Inspection

    plan = install_plan(Inspection(os="Linux", arch="x86_64", internet="no",
                                   container="/usr/bin/apptainer"),
                        CodeIdentity("2.5.6"), "box")
    copy = next(s.command for s in plan.steps if s.command.startswith("rsync"))
    assert copy.endswith(" box:.fastmdxplora/images/")
