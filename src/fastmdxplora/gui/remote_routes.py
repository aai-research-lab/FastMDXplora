"""The GUI's way to your machines: the routes behind **Run on** and
**Remote jobs**.

What ``fastmdx remote`` does at a terminal, for the page, under the rules
every interface keeps (`docs/remote.md`):

- machines only from the records made at a terminal; nothing here inspects a
  machine or installs on one;
- a config, the files that travel and the results folder all inside the
  GUI's workspace, and only the files in the config's folder travel;
- nothing sent until the person agrees to the plan the page showed: a plan
  is kept here under a token for ten minutes and used once, and the send
  goes ahead only where what would travel now is what was shown, each file's
  contents included (its digest, as an AI app's yes is bound to it);
- a fetch only of the size the person was shown;
- refused in a hosted GUI, whose runs are its service's.

The server's gate holds for every route here as for the others: loopback
only, ``Host`` and ``Origin`` checked.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import refusal_of

__all__ = ["RemoteDesk", "PLAN_KEPT_S"]

#: How long a plan shown on the page may be sent.
PLAN_KEPT_S = 600

#: Plans kept at once; the oldest goes first.
_MOST_PLANS = 32


@dataclass(frozen=True)
class _Plan:
    config: Path
    machine: str
    output: Path
    digest: str
    made_at: float


def _refused(error: str, code: str = "", **more: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, **({"code": code} if code else {}), **more}


#: The routes, so another path under ``/api/remote/`` is not found.
_GETS = frozenset({"/api/remote/machines", "/api/remote/job", "/api/remote/fetch-sizes"})
_POSTS = frozenset({"/api/remote/plan", "/api/remote/send", "/api/remote/fetch",
                    "/api/remote/cancel"})

_HOSTED = _refused("Not available in a hosted GUI: its studies run on its service.")


def _said(exc: BaseException) -> dict[str, Any]:
    found = refusal_of(exc)
    return _refused(found.message, found.code)


class RemoteDesk:
    """The remote routes of one GUI server, held to its workspace: the
    folder the window was opened on and, given the window's runtime, the
    folder it puts new studies in."""

    def __init__(self, where: Any, *, hosted: bool = False) -> None:
        self.runtime = where if hasattr(where, "workspace_root") else None
        given = ([where.workspace_root, where.exploration_root] if self.runtime is not None
                 else [where])
        self.roots = list(dict.fromkeys(Path(root).expanduser().resolve() for root in given))
        self.root = self.roots[0]
        self.hosted = hosted
        self._plans: dict[str, _Plan] = {}
        self._lock = threading.Lock()

    # -- paths --------------------------------------------------------------
    def inside(self, given: Any) -> Path | None:
        """A path as named, read inside the workspace, or None: a relative
        path is the first folder's; an absolute one may be in either."""
        from fastmdxplora.mcp.workspace import Workspace

        try:
            absolute = Path(str(given)).expanduser().is_absolute()
        except (TypeError, ValueError, RuntimeError):
            return None
        for root in self.roots if absolute else self.roots[:1]:
            found = Workspace(root).inside(given)
            if found is not None:
                return found
        return None

    def shown(self, path: Any) -> str:
        from fastmdxplora.mcp.workspace import Workspace

        for root in self.roots:
            try:
                Path(str(path)).resolve().relative_to(root)
            except (ValueError, OSError, RuntimeError):
                continue
            return Workspace(root).shown(path)
        return Workspace(self.root).shown(path)

    def _jobs(self) -> list[Any]:
        """The jobs whose results come back into the workspace, each once."""
        from fastmdxplora.remote import api

        found: dict[str, Any] = {}
        for root in self.roots:
            for job in api.jobs(under=root):
                found.setdefault(job.name, job)
        return list(found.values())

    # -- the routes -----------------------------------------------------------
    def get(self, path: str, query: dict[str, list[str]]) -> dict[str, Any] | None:
        """The answer to a GET of ``path``, or None where it is not one of
        these routes."""
        if path not in _GETS:
            return None
        if self.hosted:
            return dict(_HOSTED)
        name = (query.get("job") or [""])[0]
        if path == "/api/remote/machines":
            return self.machines()
        if path == "/api/remote/job":
            return self.job(name)
        if path == "/api/remote/fetch-sizes":
            return self.fetch_sizes(name)
        return None

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        """The answer to a POST of ``path``, or None where it is not one of
        these routes."""
        if path not in _POSTS:
            return None
        if self.hosted:
            return dict(_HOSTED)
        if path == "/api/remote/plan":
            return self.plan(body.get("config"), body.get("machine"),
                             state=body.get("state"))
        if path == "/api/remote/send":
            return self.send(body.get("plan"))
        if path == "/api/remote/fetch":
            return self.fetch(body.get("job"), bool(body.get("with_trajectory")),
                              body.get("bringing"))
        if path == "/api/remote/cancel":
            return self.cancel(body.get("job"))
        return None

    # -- machines and jobs ------------------------------------------------------
    def machines(self) -> dict[str, Any]:
        """The machines as recorded at a terminal, and the jobs whose results
        come back into this workspace. Nothing is asked of any machine."""
        from fastmdxplora.remote import api

        try:
            targets = [{"name": t.name, "kind": t.kind, "ready": t.ready,
                        "summary": t.summary, "gpus": list(t.gpus),
                        "inspected_at": t.inspected_at}
                       for t in api.machines()]
            jobs = [self._job_view(job) for job in self._jobs()]
        except Exception as exc:  # noqa: BLE001 - a refusal, said as one
            return _said(exc)
        return {"ok": True, "machines": targets, "jobs": jobs}

    def _job_here(self, given: Any):
        name = str(given or "")
        for job in self._jobs():
            if job.name == name:
                return job
        return None

    def _job_view(self, job: Any) -> dict[str, Any]:
        return {"name": job.name, "machine": job.machine, "state": job.state,
                "detail": job.detail, "scheduler": job.scheduler,
                "submitted_at": job.submitted_at, "fetched_at": job.fetched_at,
                "results": self.shown(job.local_output), "remote_dir": job.remote_dir,
                "log_tail": list(job.extra.get("log_tail") or [])[-8:]}

    def job(self, name: Any) -> dict[str, Any]:
        """A job's state, asked of its machine at most every 30 s."""
        from fastmdxplora.remote import api

        job = self._job_here(name)
        if job is None:
            return _refused(f"No job called {name!r} sends its results into this "
                            "workspace.", "remote.job.unknown")
        try:
            job = api.status(job.name, max_age_s=api.STATUS_KEPT_S)
        except Exception as exc:  # noqa: BLE001 - a refusal, said as one
            return _said(exc)
        return {"ok": True, "job": self._job_view(job)}

    # -- sending ----------------------------------------------------------------
    def _planned(self, config: Path, machine: str, output: Path):
        """The send planned, refused (as a page answer) where it may not go:
        an input outside the workspace, no room, a name or folder taken."""
        from fastmdxplora.remote import api
        from fastmdxplora.remote.jobs import job_names

        sending = api.plan_send(config, machine, output=output)
        outside = [source for source in sending.inputs.files.values()
                   if self.inside(source) is None]
        if outside:
            return None, _refused(
                f"{outside[0]} would be sent with the study, and it is outside the "
                f"workspace ({self.root}); nothing outside it is sent from here. Copy it "
                "into the config's folder and name it there.", "remote.input.outside")
        if sending.no_room:
            return None, _refused(sending.no_room, "remote.machine.no_room")
        if sending.job_name in job_names():
            return None, _refused(
                f"A job called {sending.job_name} was sent from this computer before, and "
                "a job's name is its results folder's. Set `output` in the config to a "
                "new folder name and plan it again.", "environment.path.exists")
        if output in self.roots or (output.exists() and (
                not output.is_dir() or any(output.iterdir()))):
            return None, _refused(
                f"{self.shown(output)} is in use already, and results are never written "
                "over anything. Set `output` in the config to a new folder name and plan "
                "it again.", "environment.path.exists")
        return sending, None

    def _what(self, config: Any) -> tuple[Path | None, Path | None, dict[str, Any] | None]:
        """The config file and its results folder, both in the workspace."""
        import yaml

        file = self.inside(config) if config else None
        if file is None or not file.is_file() or file.suffix.lower() not in (".yml", ".yaml"):
            return None, None, _refused(
                f"{config!r} is not a config file in the workspace ({self.root}).")
        try:
            raw = yaml.safe_load(file.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return None, None, _refused(f"{self.shown(file)} could not be read: {exc}")
        requested = str((raw or {}).get("output") or file.with_suffix("")) if isinstance(
            raw, dict) else str(file.with_suffix(""))
        output = self.inside(requested)
        if output is None:
            return None, None, _refused(f"The results folder {requested} is outside the "
                                        "workspace.")
        return file, output, None

    def _saved(self, state: dict[str, Any]) -> tuple[Path | None, dict[str, Any] | None]:
        """The Config Builder's study saved as a config file beside the
        results folder it names, where **Run on this machine** would write
        them: ``<results>.yml``, or ``-2``, ``-3`` and so on beside an
        earlier one; the same text planned again is the same file."""
        from fastmdxplora.gui.config_builder import config_yaml
        from fastmdxplora.naming import default_output_name, system_of

        source = dict(state)
        requested = str(source.get("output") or "").strip()
        if not requested:
            requested = default_output_name(system_of(source))
        if self.runtime is not None:
            output = self.runtime._output_folder(requested)
        else:
            output = self.inside(requested)
        if output is None or self.inside(str(output)) is None or output in self.roots:
            return None, _refused(f"The results folder {requested} must be a new folder "
                                  "inside the workspace.")
        source["output"] = str(output)
        try:
            built = config_yaml(source, full=bool(source.get("full")))
        except Exception as exc:  # noqa: BLE001 - a refusal, said as one
            return None, _said(exc)
        if not built.get("ok"):
            return None, _refused(str(built.get("error") or "The study could not be "
                                      "written as a config."), str(built.get("code") or ""))
        text = str(built["yaml"])
        for n in range(1, 1000):
            target = output.parent / (f"{output.name}.yml" if n == 1
                                      else f"{output.name}-{n}.yml")
            try:
                if target.is_file() and target.read_text(encoding="utf-8") == text:
                    return target, None
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("x", encoding="utf-8") as out:
                    out.write(text)
            except FileExistsError:
                continue
            except (OSError, ValueError) as exc:
                return None, _refused(f"The config could not be saved beside "
                                      f"{self.shown(output)}: {exc}")
            return target, None
        return None, _refused("Too many configs of that name; give another results folder.")

    def plan(self, config: Any, machine: Any, *, state: Any = None) -> dict[str, Any]:
        """What a send of ``config`` to ``machine`` would do, as the page
        shows it before the person agrees; with a token the send is made by.
        Given the Config Builder's ``state`` in place of a file, the study is
        saved as one first (:meth:`_saved`)."""
        from fastmdxplora.remote.send import room_said, sent_digest, travelling

        if not config and isinstance(state, dict):
            saved, refused = self._saved(state)
            if refused is not None:
                return refused
            config = str(saved)
        file, output, refused = self._what(config)
        if refused is not None:
            return refused
        name = str(machine or "")
        try:
            sending, refused = self._planned(file, name, output)
        except Exception as exc:  # noqa: BLE001 - a refusal, said as one
            return _said(exc)
        if refused is not None:
            return refused
        prints = travelling(sending)
        token = secrets.token_hex(16)
        with self._lock:
            self._forget_old()
            while len(self._plans) >= _MOST_PLANS:
                self._plans.pop(min(self._plans, key=lambda t: self._plans[t].made_at))
            self._plans[token] = _Plan(file, name, output, sent_digest(sending, prints),
                                        time.monotonic())
        return {
            "ok": True, "plan": token, "kept_s": PLAN_KEPT_S,
            "config": self.shown(file), "machine": name, "job": sending.job_name,
            "runs_in": sending.installation.path,
            "scheduler": "SLURM" if sending.scheduler == "slurm" else "a detached process",
            "folder": sending.remote_dir, "results": self.shown(output),
            "travels": [{"name": f"inputs/{travelled}", "from": self.shown(source),
                         "bytes": prints[travelled][0]}
                        for travelled, source in sending.inputs.files.items()],
            "fetched_there": list(sending.inputs.fetched),
            "room": room_said(sending), "notes": list(sending.notes),
            "script": sending.script,
        }

    def _forget_old(self) -> None:
        now = time.monotonic()
        for token in [t for t, p in self._plans.items() if now - p.made_at > PLAN_KEPT_S]:
            self._plans.pop(token, None)

    def send(self, token: Any) -> dict[str, Any]:
        """Send the plan the person agreed to, once, where what would travel
        now is what they were shown."""
        from fastmdxplora.remote import api
        from fastmdxplora.remote.send import sent_digest

        with self._lock:
            self._forget_old()
            plan = self._plans.pop(str(token or ""), None)
        if plan is None:
            return _refused("That plan is not one this page was shown in the last ten "
                            "minutes, or it was sent already. Plan the send again.",
                            "remote.send.unconfirmed")
        try:
            sending, refused = self._planned(plan.config, plan.machine, plan.output)
        except Exception as exc:  # noqa: BLE001 - a refusal, said as one
            return _said(exc)
        if refused is not None:
            return refused
        if sent_digest(sending) != plan.digest:
            return _refused("What would be sent changed after the plan was shown (the "
                            "config, a file that travels, or the machine). Plan the send "
                            "again and look at it.", "remote.send.unconfirmed")
        try:
            job = api.send_planned(sending)
        except Exception as exc:  # noqa: BLE001 - a refusal, said as one
            return _said(exc)
        return {"ok": True, "job": self._job_view(job)}

    # -- fetching and stopping ------------------------------------------------
    def fetch_sizes(self, name: Any) -> dict[str, Any]:
        """What a fetch of an ended job would bring, each part's size."""
        from fastmdxplora.remote import api

        job = self._job_here(name)
        if job is None:
            return _refused(f"No job called {name!r} sends its results into this "
                            "workspace.", "remote.job.unknown")
        try:
            job = api.status(job.name, max_age_s=api.STATUS_KEPT_S)
            if job.state in ("ready", "running"):
                return _refused(f"{job.name} is still {job.state}; it is fetched once it "
                                "has ended.", "remote.job.unfinished")
            sizes = api.fetch_sizes(job.name, max_age_s=api.STATUS_KEPT_S)
        except Exception as exc:  # noqa: BLE001 - a refusal, said as one
            return _said(exc)
        return {"ok": True, "job": self._job_view(job), "results_bytes": sizes.results,
                "trajectory_bytes": sizes.trajectory,
                "trajectory_files": sizes.trajectory_files,
                "run_written": sizes.run_written,
                "bringing": {"without": sizes.bringing(False), "with": sizes.bringing(True)}}

    def fetch(self, name: Any, with_trajectory: bool, bringing: Any) -> dict[str, Any]:
        """Fetch a job's results, where what it would bring, asked of the
        machine now, is still what the person was shown (``bringing``, in
        bytes). The copy then brings no file larger than that, and a margin."""
        from fastmdxplora.remote import api

        job = self._job_here(name)
        if job is None:
            return _refused(f"No job called {name!r} sends its results into this "
                            "workspace.", "remote.job.unknown")
        try:
            sizes = api.fetch_sizes(job.name)
        except Exception as exc:  # noqa: BLE001 - a refusal, said as one
            return _said(exc)
        now = sizes.bringing(with_trajectory)
        if not (isinstance(bringing, int) and not isinstance(bringing, bool)
                and bringing == now):
            return _refused("The fetch would bring another size than the one shown. Look "
                            "at the sizes again.", "remote.fetch.unconfirmed",
                            bringing=now)
        try:
            # No file larger than the whole was said to be, and a margin.
            job, warnings = api.fetch(job.name, with_trajectory=with_trajectory,
                                      most_bytes=now + now // 10 + 1_000_000)
        except Exception as exc:  # noqa: BLE001 - a refusal, said as one
            return _said(exc)
        return {"ok": True, "job": self._job_view(job), "warnings": list(warnings)}

    def cancel(self, name: Any) -> dict[str, Any]:
        """Stop a job, as `fastmdx remote cancel` does (the page asks first)."""
        from fastmdxplora.remote import api
        from fastmdxplora.remote.send import left_stop_done

        job = self._job_here(name)
        if job is None:
            return _refused(f"No job called {name!r} sends its results into this "
                            "workspace.", "remote.job.unknown")
        asked_at = time.time()
        try:
            job = api.cancel(job.name)
        except Exception as exc:  # noqa: BLE001 - a refusal, said as one
            return _said(exc)
        return {"ok": True, "job": self._job_view(job),
                "stopped": job.state == "abandoned" or left_stop_done(job, asked_at) in (
                    "sent", "now")}
