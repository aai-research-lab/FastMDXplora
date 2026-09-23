"""Dependency-free localhost dashboard server.

The HTML shell, CSS, and JavaScript live in
:mod:`fastmdxplora.gui.templates` and :mod:`fastmdxplora.gui.static`.
``_dashboard_shell`` reads ``dashboard.html`` from disk on first request
and caches the result.
"""

from __future__ import annotations

import io
import csv
import json
import logging
import mimetypes
import os
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from functools import lru_cache
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from fastmdxplora.gui.exploration import (
    DashboardRuntime,
)
from fastmdxplora.ligand_detection import detect_ligands, normalise_ligand_resname
from fastmdxplora.gui.live_frames import live_frame_exists, read_live_frame_index
from fastmdxplora.gui.protein_preview import (
    find_structure,
    find_system,
    protein_preview_payload,
)
from fastmdxplora.structure_info import (
    _MAX_PDB_BYTES_FOR_SYSTEM_SCAN,
    count_structure,
    ligand_atom_counts,
)
from fastmdxplora.gui.telemetry import (
    analyze_health,
    read_events,
    read_metrics,
    read_status,
    run_phases,
    run_stages,
)
from fastmdxplora.gui.trajectory_playback import playback_info
from fastmdxplora.refusals import StudyError
from fastmdxplora.refusals import BackendUnavailable

logger = logging.getLogger("fastmdxplora.gui.server")

class _DashboardServer(ThreadingHTTPServer):
    """A server that does not shout when a caller hangs up.

    `socketserver` prints a traceback for any exception a handler lets
    escape, and a caller that goes away mid-request -- a browser tab closed,
    a page reloaded, a `curl` interrupted -- makes the handler's next write
    raise `BrokenPipeError`, or `ConnectionResetError`, or on Windows
    `ConnectionAbortedError`. On a network that is weather, not a fault, and
    a traceback for each one buries the faults that are.

    It is the same lesson as the refusal that never arrived: this server is
    reachable from somewhere else now, so the ordinary behaviour of somebody
    else's socket has to be ordinary here too.
    """

    def handle_error(self, request: Any, client_address: Any) -> None:
        raised = sys.exc_info()[0]
        if raised is not None and issubclass(raised, ConnectionError):
            logger.debug("dashboard caller %s hung up mid-request",
                         client_address)
            return
        super().handle_error(request, client_address)


#: The largest request body this server will read, whether to parse it or to
#: discard it before a refusal. One number for both, so a body small enough to
#: be answered is always small enough to be drained first.
MOST_A_BODY_MAY_BE = 1_000_000

PLOT_TITLE_ALIASES = {
    "rmsd": "RMSD",
    "rmsf": "RMSF",
    "rg": "Radius of gyration",
    "hbonds": "Hydrogen bonds",
    "hbond": "Hydrogen bonds",
    "sasa": "SASA",
    "ss": "Secondary structure",
    "secondary_structure": "Secondary structure",
    "dimred_pca": "PCA",
    "pca": "PCA",
    "dimred_mds": "MDS",
    "mds": "MDS",
    "dimred_tsne": "t-SNE",
    "tsne": "t-SNE",
    "cluster_kmeans": "KMeans trajectory scatter",
    "kmeans_trajectory": "KMeans trajectory scatter",
    "kmeans_population": "KMeans population",
    "cluster_kmeans_counts": "KMeans population",
    "cluster_hierarchical": "Hierarchical trajectory scatter",
    "hierarchical_trajectory": "Hierarchical trajectory scatter",
    "hierarchical_population": "Hierarchical population",
    "cluster_hierarchical_counts": "Hierarchical population",
    "cluster_hierarchical_dendrogram": "Hierarchical dendrogram",
    "hierarchical_dendrogram": "Hierarchical dendrogram",
    "cluster_dbscan": "DBSCAN trajectory scatter",
    "dbscan_trajectory": "DBSCAN trajectory scatter",
    "dbscan_population": "DBSCAN population",
    "cluster_dbscan_counts": "DBSCAN population",
    "qvalue": "Native contacts",
    "native_contacts": "Native contacts",
    "dihedrals": "Dihedrals",
    "ramachandran": "Ramachandran plot",
}

DASHBOARD_ASSET_TITLES = {
    "rmsd_dashboard": "RMSD",
    "rmsf_dashboard": "RMSF",
    "rg_dashboard": "Radius of gyration",
    "hbonds_dashboard": "Hydrogen bonds",
    "sasa_dashboard": "SASA",
    "pca_dashboard": "PCA",
    "mds_dashboard": "MDS",
    "tsne_dashboard": "t-SNE",
    "kmeans_trajectory_dashboard": "KMeans trajectory scatter",
    "kmeans_population_dashboard": "KMeans population",
    "hierarchical_trajectory_dashboard": "Hierarchical trajectory scatter",
    "hierarchical_population_dashboard": "Hierarchical population",
    "hierarchical_dendrogram_dashboard": "Hierarchical dendrogram",
    "cluster_hierarchical_dendrogram_dashboard": "Hierarchical dendrogram",
    "dbscan_trajectory_dashboard": "DBSCAN trajectory scatter",
    "dbscan_population_dashboard": "DBSCAN population",
    "ss_dashboard": "Secondary structure",
    "qvalue_dashboard": "Native contacts",
    "dihedrals_dashboard": "Dihedrals",
}

PLOT_CATEGORY_BY_TITLE = {
    "RMSD": "Core Metrics",
    "RMSF": "Core Metrics",
    "Radius of gyration": "Core Metrics",
    "Hydrogen bonds": "Core Metrics",
    "KMeans trajectory scatter": "Clustering",
    "KMeans population": "Clustering",
    "Hierarchical trajectory scatter": "Clustering",
    "Hierarchical population": "Clustering",
    "Hierarchical dendrogram": "Clustering",
    "DBSCAN trajectory scatter": "Clustering",
    "DBSCAN population": "Clustering",
    "PCA": "Additional Analysis",
    "MDS": "Additional Analysis",
    "t-SNE": "Additional Analysis",
    "SASA": "Additional Analysis",
    "Secondary structure": "Additional Analysis",
    "Native contacts": "Additional Analysis",
    "Dihedrals": "Additional Analysis",
    "Ramachandran plot": "Additional Analysis",
}

KEY_PLOT_TITLES = {"RMSD", "RMSF", "Radius of gyration", "Hydrogen bonds", "PCA", "SASA"}

DASHBOARD_TEMPLATE_PATH = Path(__file__).with_name("templates") / "dashboard.html"


@dataclass
class DashboardConfig:
    """Per-dashboard-server knobs supplied by the CLI."""

    ligand_resname: str | None = None
    include_cofactors: bool = False
    binding_pocket_cutoff_A: float = 5.0
    max_browser_frames: int = 200
    refresh_seconds: float = 3.0

    @property
    def binding_pocket_cutoff_m(self) -> float:
        return float(self.binding_pocket_cutoff_A)


@dataclass
class DashboardSession:
    """Background local dashboard server session."""

    server: ThreadingHTTPServer
    thread: threading.Thread
    root: Path
    host: str
    port: int
    requested_port: int
    config: DashboardConfig | None = None
    runtime: DashboardRuntime | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def port_was_changed(self) -> bool:
        # Port 0 explicitly asks the operating system for any free port; that
        # is not a fallback caused by a conflict and should not be reported as
        # one to the user.
        return self.requested_port != 0 and self.port != self.requested_port

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def wait_forever(self) -> None:
        while self.thread.is_alive():
            time.sleep(0.2)



def make_handler(
    project_root: str | Path,
    config: DashboardConfig | None = None,
    template_html: str | None = None,
    runtime: DashboardRuntime | None = None,
    allow_control: bool = True,
) -> type[BaseHTTPRequestHandler]:
    root = Path(project_root).resolve()
    app_runtime = runtime or DashboardRuntime(
        workspace_root=root,
        exploration_root=root.parent,
        active_root=root,
    )
    cfg = config or DashboardConfig()
    html = template_html if template_html is not None else _load_template()
    refresh_seconds = min(60.0, max(1.0, float(cfg.refresh_seconds or 3.0)))
    html = html.replace("__FASTMDX_REFRESH_SECONDS__", f"{refresh_seconds:g}")
    # Injected rather than written into the template, so the citation cannot
    # drift from the one the report, the slides and `fastmdx info` print. The
    # GUI carried none at all: the interface the documentation sends a new
    # user to first was the one that never said how to cite the software.
    from html import escape as _escape

    from fastmdxplora import (
        __bibtex__,
        __citation__,
        __doi__,
        __version__,
    )

    html = html.replace("__FASTMDX_CITATION__", _escape(__citation__))
    html = html.replace("__FASTMDX_DOI__", _escape(__doi__))
    html = html.replace("__FASTMDX_VERSION__", _escape(__version__))
    html = html.replace("__FASTMDX_BIBTEX__", _escape(__bibtex__))

    class LiveDashboardHandler(BaseHTTPRequestHandler):
        server_version = "FastMDXLive/1.0"

        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            try:
                self._dispatch()
            except ConnectionError:
                # The caller hung up: a tab closed, a page reloaded. Not a
                # fault, and nobody is left to answer -- writing a 500 to the
                # closed socket only fails a second time. Same policy as the
                # server's own handle_error, which never saw these because
                # the route caught them first.
                logger.debug("dashboard caller %s hung up mid-request", self.client_address)
            except Exception as exc:  # noqa: BLE001 — dashboard must never crash the sim
                logger.warning("dashboard route %s failed: %s", self.path, exc)
                self.send_error(500, "Dashboard internal error")

        def do_POST(self) -> None:  # noqa: N802 - stdlib API
            try:
                self._dispatch_post()
            except ConnectionError:
                logger.debug("dashboard caller %s hung up mid-request", self.client_address)
            except Exception as exc:  # noqa: BLE001 - exploration errors stay local
                logger.warning("dashboard POST route %s failed: %s", self.path, exc)
                # Whatever went wrong, the body may not have been read -- and
                # an unread body costs the caller this message.
                self._drain_request_body()
                self._send_json(
                    {"ok": False, "error": "Dashboard request failed."},
                    status=500,
                )

        def _dispatch(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            root = app_runtime.data_root()
            if path in {"/", "/index", "/results", "/live"}:
                self._send_html(html)
                return
            if path == "/api/app-state" or path == "/api/explore/state":
                self._send_json(app_runtime.snapshot())
                return
            if path == "/api/agent/conversation":
                from fastmdxplora.gui.agent_panel import read_conversation

                self._send_json(read_conversation(app_runtime))
                return
            if path == "/api/file-text":
                # A run's own text file, for the Files tab's preview.
                # Confined to the run root, over the same list of types
                # the Agent's + accepts.
                from fastmdxplora.gui.agent_panel import read_text_file

                wanted = (parse_qs(parsed.query).get("path") or [""])[0]
                root = app_runtime.active_root or app_runtime.workspace_root
                answer = read_text_file(
                    Path(str(root)) / wanted if not Path(str(wanted)).is_absolute() else wanted,
                    within=root)
                if answer.get("ok") and answer.get("suffix") in ("md", "markdown"):
                    from fastmdxplora.gui.report_page import render_markdown

                    answer["html"], answer["rendered"] = render_markdown(answer["text"])
                self._send_json(answer)
                return
            if path == "/api/agent/conversations":
                from fastmdxplora.gui.agent_panel import list_conversations

                self._send_json(list_conversations(app_runtime))
                return
            if path in {"/api/browse", "/api/inspect-directory"} and not allow_control:
                # These walk the filesystem for a folder picker, which is a
                # reasonable thing for a tool on your own machine and not for
                # one reachable over a network. They were outside the gate:
                # bound to a non-loopback address the workflow-control
                # endpoints returned 403 while `/api/browse?path=/etc` listed
                # any directory on the host to anyone who asked, unauthenticated.
                self._send_json(
                    {
                        "ok": False,
                        "error": (
                            "Browsing the server's filesystem is disabled "
                            "when the dashboard is bound to a non-loopback "
                            "address."
                        ),
                    },
                    status=403,
                )
                return
            if path == "/api/browse":
                # Typing a path is a poor ask, and a browser cannot offer a
                # dialog for a folder the server will read: the one it has
                # uploads files to a page. So the walking happens here.
                from fastmdxplora.gui.browse import browse

                query = parse_qs(parsed.query)
                where = query.get("path", [""])[0]
                kind = query.get("kind", [""])[0]
                self._send_json(browse(where or None, kind or None))
                return
            if path == "/api/inspect-directory":
                # Someone with a trajectory already should be able to point at
                # the folder and be offered the analyses, rather than being
                # asked to reproduce the simulation first.
                from fastmdxplora.gui.directory_inspect import inspect_directory

                target = parse_qs(parsed.query).get("path", [""])[0]
                if not target:
                    self._send_json(
                        {"ok": False, "error": "No directory given."}, status=400
                    )
                    return
                self._send_json(inspect_directory(target))
                return
            if path == "/api/schema":
                # Every setting the software accepts, described well enough
                # for the page to draw a control for it. The form used to be
                # written by hand and offered eleven of eighty-three.
                from fastmdxplora.gui.schema_payload import schema_payload

                payload = schema_payload()
                # Where a results folder named rather than pathed will land.
                # The page can then say it instead of leaving somebody to
                # guess which directory "analysis_output" is relative to.
                payload["workspace"] = str(app_runtime.exploration_root)
                self._send_json(payload)
                return
            if path == "/api/status":
                status = read_status(root)
                metrics = read_metrics(root)
                payload = {
                    "status": status,
                    "health": analyze_health(status, metrics, root=root),
                    # Which stages this run can actually reach. An
                    # analysis-only run has no minimization to wait for, and a
                    # stage greyed out forever reads as a run that stalled.
                    "stages": run_stages(root),
                    "phases": run_phases(root),
                }
                self._send_json(payload)
                return
            if path == "/api/metrics":
                self._send_json({"metrics": read_metrics(root)})
                return
            if path == "/api/events":
                self._send_json({"events": read_events(root)})
                return
            if path == "/api/report":
                from fastmdxplora.gui.report_page import report_payload

                self._send_json(report_payload(root))
                return
            if path == "/api/artifacts" or path == "/api/files":
                self._send_json({"artifacts": _artifact_records(root)})
                return
            if path == "/api/results" or path == "/api/analyses":
                self._send_json(_results_payload(root))
                return
            if path == "/api/protein-preview":
                regenerate = parsed.query == "regenerate=1"
                self._send_json(protein_preview_payload(root, regenerate=regenerate))
                return
            if path == "/api/structure-info":
                self._send_json(_structure_info_payload(root, cfg))
                return
            if path == "/api/ligands":
                self._send_json(_ligands_payload(root, cfg))
                return
            if path == "/api/live-frame-index":
                sim_dir = root / "simulation"
                self._send_json(read_live_frame_index(sim_dir))
                return
            if path == "/api/live-coordinates":
                self._send_json(_live_coordinates_payload(root))
                return
            if path == "/api/playback-info":
                max_frames = cfg.max_browser_frames
                if "max=" in parsed.query:
                    try:
                        max_frames = int(parsed.query.split("max=", 1)[1].split("&")[0])
                    except ValueError:
                        pass
                force = "force=1" in parsed.query
                sim_manifest = _load_json(root / "simulation" / "simulation_parameters.json")
                duration_ns = sim_manifest.get("duration_ns_actual")
                self._send_json(playback_info(
                    root,
                    max_browser_frames=max_frames,
                    simulation_time_ns_total=duration_ns,
                    force=force,
                ))
                return
            if path == "/api/open-output":
                if not allow_control:
                    self._send_json(
                        {
                            "opened": False,
                            "path": str(root),
                            "detail": "Opening local folders is disabled for remote dashboards.",
                        },
                        status=403,
                    )
                    return
                opened, detail = _open_local_path(root)
                self._send_json({
                    "opened": opened,
                    "path": str(root),
                    "detail": detail,
                })
                return
            if path == "/analysis-figures-svg.zip":
                self._send_svg_bundle(root)
                return
            if path == "/structure/topology.pdb":
                wanted = parse_qs(parsed.query).get("solvent", ["0"])[0]
                self._send_structure(
                    root, with_solvent=wanted.lower() in {"1", "true", "yes"}
                )
                return
            if path == "/structure/live-frame.pdb":
                self._send_live_frame(root)
                return
            if path == "/structure/playback.pdb":
                self._send_playback(root)
                return
            if path.startswith("/static/"):
                self._send_static_asset(path.removeprefix("/static/"))
                return
            if path.startswith("/artifacts/"):
                self._send_artifact(
                    root,
                    path.removeprefix("/artifacts/"),
                    download="download=1" in parsed.query,
                )
                return
            self.send_error(404, "Not found")

        def _dispatch_post(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if not allow_control and path in {
                "/api/explore/stop",
                "/api/run",
                "/api/run-config",
                # These two read a file the caller names and report what went
                # wrong with it, and a parse error quotes the line it failed
                # on. Over a network that is a file-content oracle: a planted
                # token and an AWS key were both recovered through it, and a
                # valid-mapping file echoes its first key. They belong with
                # the endpoints that need the machine's trust.
                "/api/load-config",
                "/api/check-config",
                # One stores an API key and the other spends it. Over a
                # network, unauthenticated, that is somebody else setting
                # where this machine's requests go, or burning the credit
                # on the key already stored. Neither is a study, so neither
                # reads as dangerous at a glance -- which is exactly why
                # they belong on a list rather than in a judgement.
                "/api/agent/model",
                "/api/agent/propose",
                "/api/agent/run",
                "/api/agent/conversation",
                "/api/agent/conversation/clear",
                "/api/agent/conversation/new",
                "/api/agent/conversation/open",
                "/api/agent/conversation/delete",
                "/api/agent/conversation/attach",
                "/api/agent/attachment",
            }:
                # Before the refusal, not after: an unread body turns the
                # close into an RST and the caller loses the 403 it explains
                # itself with. See `_drain_request_body`.
                self._drain_request_body()
                self._send_json(
                    {
                        "ok": False,
                        "error": (
                            "This is disabled when the dashboard is bound to a "
                            "non-loopback address."
                        ),
                    },
                    status=403,
                )
                return
            payload = self._read_json_body()
            if path == "/api/run":
                # Runs what the config describes rather than what a form was
                # wired for, which is how an analysis of an existing
                # trajectory can be started at all.
                self._send_json(
                    app_runtime.launch_from_config(
                        payload or {},
                        dashboard_url=self.headers.get("Origin"),
                    )
                )
                return
            if path == "/api/agent/model":
                # Reading and setting which model to ask. The key is
                # accepted here and stored by `save_choice`, which puts it
                # in a file of its own; it is never echoed back, never put
                # in a config, and never logged.
                from fastmdxplora.gui.agent_panel import model_endpoint

                self._send_json(model_endpoint(payload or {}))
                return
            if path == "/api/agent/run":
                # Starting a study, so it belongs with the endpoints that
                # need the machine's trust -- listed above with the others.
                from fastmdxplora.gui.agent_panel import run_endpoint

                self._send_json(run_endpoint(
                    payload or {}, app_runtime,
                    dashboard_url=self.headers.get("Origin")))
                return
            if path == "/api/agent/propose":
                # A sentence in, a config out -- through the same
                # `propose_config` the CLI uses and the same validator a
                # hand-written config goes through. Nothing here decides
                # whether a config is acceptable.
                from fastmdxplora.gui.agent_panel import propose_endpoint

                self._send_json(propose_endpoint(payload or {}, app_runtime))
                return
            if path == "/api/load-config":
                # Bringing a config into the form so it can be changed. The
                # file is read and never written: anything altered is saved as
                # a new one.
                #
                # Either a path or the config itself. The agent panel holds
                # one as a mapping and never as a path, and the mapping from
                # a config to form state is here rather than in the browser
                # -- it decides the starting point from whether an analysis
                # names a trajectory, strips the keys the form owns, and
                # splits a comma-joined analysis list. A second copy of that
                # in JavaScript is where the two would drift.
                from fastmdxplora.gui.config_builder import (
                    load_config_into_state,
                    state_from_config,
                )

                request = payload or {}
                given = request.get("config")
                if isinstance(given, dict):
                    self._send_json(state_from_config(given))
                else:
                    self._send_json(
                        load_config_into_state(str(request.get("path") or ""))
                    )
                return
            if path == "/api/run-config":
                # Running a config exactly as it stands, which is a different
                # act from running what the form currently describes.
                request = payload or {}
                self._send_json(
                    app_runtime.launch_existing_config(
                        str(request.get("path") or ""),
                        output=request.get("output"),
                        dashboard_url=self.headers.get("Origin"),
                    )
                )
                return
            if path == "/api/check-config":
                # A config that has been elsewhere -- edited by hand, carried
                # to a cluster and back -- should be able to say whether it
                # still runs before an hour of compute queues behind it.
                from fastmdxplora.gui.config_builder import check_config_file

                self._send_json(
                    check_config_file(str((payload or {}).get("path") or ""))
                )
                return
            if path == "/api/config":
                # The file the page would run, handed back instead. A laptop
                # is a poor place to run fifty nanoseconds and a cluster is a
                # poor place to decide what to run, so the decisions are made
                # here and the file goes where the compute is. It is put
                # through the command line's own validator first, so a
                # configuration that would fail there fails here, while the
                # form that produced it is still on the screen.
                from fastmdxplora.gui.config_builder import config_yaml

                request = payload or {}
                self._send_json(
                    config_yaml(request, full=bool(request.get("full")))
                )
                return
            if path == "/api/explore/stop":
                self._send_json(app_runtime.stop())
                return
            if path == "/api/agent/conversation":
                from fastmdxplora.gui.agent_panel import write_conversation

                self._send_json(write_conversation(
                    app_runtime, (payload or {}).get("entries")))
                return
            if path == "/api/agent/conversation/clear":
                from fastmdxplora.gui.agent_panel import clear_conversation

                self._send_json(clear_conversation(app_runtime))
                return
            if path == "/api/agent/conversation/new":
                from fastmdxplora.gui.agent_panel import new_conversation

                self._send_json(new_conversation(app_runtime))
                return
            if path == "/api/agent/conversation/open":
                from fastmdxplora.gui.agent_panel import open_conversation

                self._send_json(open_conversation(app_runtime,
                                                  (payload or {}).get("id"),
                                                  (payload or {}).get("study")))
                return
            if path == "/api/agent/attachment":
                from fastmdxplora.gui.agent_panel import read_attachment

                self._send_json(read_attachment((payload or {}).get("path")))
                return
            if path == "/api/agent/conversation/attach":
                from fastmdxplora.gui.agent_panel import attach_conversation

                self._send_json(attach_conversation(app_runtime,
                                                    (payload or {}).get("study"),
                                                    (payload or {}).get("id"),
                                                    (payload or {}).get("from_study")))
                return
            if path == "/api/agent/conversation/delete":
                from fastmdxplora.gui.agent_panel import delete_conversation

                self._send_json(delete_conversation(app_runtime,
                                                    (payload or {}).get("id"),
                                                    (payload or {}).get("study")))
                return
            if path == "/api/explore/switch":
                folder = str((payload or {}).get("folder") or "").strip()
                if not folder:
                    self._send_json({"ok": False, "error": "No folder given."},
                                    status=400)
                    return
                self._send_json(app_runtime.switch_to(folder))
                return
            self.send_error(404, "Not found")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        # ---- Generic response helpers ----
        def _drain_request_body(self) -> None:
            """Read and discard what the caller sent, before refusing it.

            A handler that answers without reading the request body leaves
            that body in the socket's receive buffer, and closing a socket
            with unread data sends RST rather than FIN. The peer then loses
            the response that was already on the wire.

            That is not a theoretical tidiness. These refusals answer 403
            with a sentence saying *why* the endpoint is closed, and a
            refusal nobody receives is indistinguishable from a broken
            server. On Windows the client raises `ConnectionAbortedError:
            [WinError 10053]` for a twenty-byte body and never sees the
            explanation; on Linux the same request is refused cleanly until
            the body outgrows the socket buffer, at which point it becomes
            `BrokenPipeError` there too. The Windows CI run found it first,
            intermittently, which is how a race presents.

            Bounded by the same limit the parser enforces. Past that the
            body is one this server would refuse to parse in any case, so
            reading it to be polite about the refusal would be reading an
            unbounded amount from a caller already being told no.

            Idempotent, and that is not a nicety. `rfile.read(n)` blocks
            until it has `n` bytes or the peer closes, so draining a body
            that was already read waits for bytes nobody is going to send --
            the whole server stops answering. The first version of this
            hung the dashboard tests that way, from the error path, where
            the body had usually been read before the error.
            """
            if getattr(self, "_body_was_read", False):
                return
            self._body_was_read = True
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                return
            remaining = min(max(length, 0), MOST_A_BODY_MAY_BE)
            while remaining > 0:
                try:
                    chunk = self.rfile.read(min(remaining, 65536))
                except OSError:
                    return
                if not chunk:
                    return
                remaining -= len(chunk)

        def _read_json_body(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                self._body_was_read = True
                return {}
            if length > MOST_A_BODY_MAY_BE:
                raise StudyError("Request body is too large",
                                 code="config.option.wrong_type")
            # Marked before the read, not after: a read that fails partway
            # has still taken bytes off the socket, and a drain that then
            # asked for the whole length again would block on bytes that
            # are gone.
            self._body_was_read = True
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise StudyError("JSON body must be an object", code="config.option.wrong_type")
            return data

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html_text: str) -> None:
            body = html_text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_artifact(
            self,
            root: Path,
            raw_rel: str,
            *,
            download: bool = False,
        ) -> None:
            try:
                target = (root / unquote(raw_rel)).resolve()
                target.relative_to(root)
            except ValueError:
                self.send_error(403, "Artifact path is outside the output directory")
                return
            if not target.is_file():
                self.send_error(404, "Artifact not found")
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            if download:
                safe_name = target.name.replace('"', "")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{safe_name}"',
                )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_svg_bundle(self, root: Path) -> None:
            """Download every generated SVG analysis/report figure as one ZIP."""
            svg_paths = _svg_figure_paths(root)
            if not svg_paths:
                self.send_error(404, "No SVG figures are available yet")
                return
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                for source in svg_paths:
                    try:
                        relative = source.relative_to(root).as_posix()
                        archive.write(source, arcname=relative)
                    except (OSError, ValueError):
                        continue
            data = buffer.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Disposition",
                'attachment; filename="fastmdxplora_svg_figures.zip"',
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_structure(self, root: Path, *, with_solvent: bool = False) -> None:
            # Draw the system that was simulated, with bulk solvent stripped
            # -- the same filter the live frames already go through. Serving
            # the prepared solute kept the file small but omitted the ligand,
            # which is put back only when the system is built, so a ligand run
            # drew a bare protein and said nothing about the absence.
            target = find_system(root)
            if target is None or not target.is_file():
                target = find_structure(root)
            if target is None or not target.is_file():
                self.send_error(404, "Structure not found")
                return
            # Water and ions on request only. The viewer's Water and Ions
            # toggles had nothing to act on, because the copy sent to the
            # browser has bulk solvent stripped -- and before that it was the
            # prepared solute, which has none either. So the controls have
            # never shown anything. Sending the whole box every time would
            # cost every reader the download for a view most of them never
            # open; sending it when asked costs only them.
            data = (
                target.read_bytes() if with_solvent else _display_structure_bytes(target)
            )
            self.send_response(200)
            self.send_header("Content-Type", "chemical/x-pdb; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_live_frame(self, root: Path) -> None:
            sim_dir = root / "simulation"
            live_path = sim_dir / "live_frame.pdb"
            if not live_path.is_file():
                self.send_error(404, "Live frame not available")
                return
            try:
                data = live_path.read_bytes()
            except OSError:
                self.send_error(404, "Live frame not available")
                return
            self.send_response(200)
            self.send_header("Content-Type", "chemical/x-pdb; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_playback(self, root: Path) -> None:
            sim_dir = root / "simulation"
            playback_path = sim_dir / "playback.pdb"
            if not playback_path.is_file():
                # Auto-generate on demand so the dashboard never needs
                # to know whether the simulation phase has finished.
                playback_info(root, max_browser_frames=cfg.max_browser_frames)
            if not playback_path.is_file():
                self.send_error(404, "Playback not available")
                return
            try:
                data = playback_path.read_bytes()
            except OSError:
                self.send_error(404, "Playback not available")
                return
            self.send_response(200)
            self.send_header("Content-Type", "chemical/x-pdb; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_static_asset(self, raw_name: str) -> None:
            static_root = Path(__file__).with_name("static")
            target = (static_root / unquote(raw_name)).resolve()
            try:
                target.relative_to(static_root.resolve())
            except ValueError:
                self.send_error(404, "Static asset not found")
                return
            if not target.is_file():
                self.send_error(404, "Static asset not found")
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return LiveDashboardHandler


def _load_template() -> str:
    try:
        return DASHBOARD_TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError:
        return (
            "<!doctype html><html><head><meta charset=\"utf-8\"><title>"
            "FastMDXplora Live Dashboard</title></head><body>"
            "<h1>Dashboard template unavailable</h1>"
            "<p>The dashboard HTML template could not be loaded. "
            "Reinstall the fastmdxplora package or run from the editable checkout.</p>"
            "</body></html>"
        )


def serve_dashboard(
    *,
    output: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    config: DashboardConfig | None = None,
    home_mode: bool = False,
    exploration_root: str | Path | None = None,
    on_ready: Callable[[str], None] | None = None,
) -> None:
    session = start_dashboard_session(
        output=output,
        host=host,
        port=port,
        config=config,
        home_mode=home_mode,
        exploration_root=exploration_root,
    )
    print(f"FastMDXplora GUI running at {session.url}")
    if on_ready is not None:
        # After the socket answers, not before. One request that returns
        # confirms the handler is serving, and only then is the browser
        # pointed at it, so the first page it asks for is one the server
        # can give -- a completed-run folder has more to read before the
        # first response, which is where the race showed.
        import threading
        import time
        import urllib.request

        def _open_when_ready(url: str) -> None:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(url, timeout=0.5) as response:
                        response.read(1)
                    break
                except Exception:  # noqa: BLE001 - keep polling
                    time.sleep(0.1)
            on_ready(url)

        threading.Thread(target=_open_when_ready, args=(session.url,),
                         daemon=True).start()
    if session.port_was_changed:
        print(
            f"Requested port {session.requested_port} was busy, "
            f"so FastMDXplora used {session.port}."
        )
    print(f"Watching: {session.root}")
    print("Press Ctrl+C to stop.")
    try:
        session.wait_forever()
    except KeyboardInterrupt:
        pass
    finally:
        session.stop()
        # The run is its own session and is not stopped by stopping the
        # server. Say so, and say where, so nobody assumes it died with
        # the terminal or wonders where it went.
        runtime = getattr(session, "runtime", None)
        proc = getattr(runtime, "process", None)
        if proc is not None and proc.poll() is None:
            where = getattr(runtime, "running_root", None) or getattr(runtime, "active_root", None)
            print(
                f"\nThe study in {where} is still running (pid {proc.pid}).\n"
                f"Reopen the GUI on it to watch, or stop it with:  kill {proc.pid}"
            )


def start_dashboard_session(
    *,
    output: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    max_port_tries: int = 50,
    config: DashboardConfig | None = None,
    home_mode: bool = False,
    exploration_root: str | Path | None = None,
) -> DashboardSession:
    """Start the local dashboard server in a background thread."""
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    runtime = DashboardRuntime(
        workspace_root=root,
        exploration_root=Path(exploration_root).resolve() if exploration_root is not None else root.parent,
        active_root=None if home_mode else root,
    )
    requested_port = int(port)
    candidates = [0] if requested_port == 0 else range(requested_port, requested_port + max_port_tries)
    last_error: OSError | None = None
    for candidate in candidates:
        try:
            handler = make_handler(
                root,
                config=config,
                runtime=runtime,
                allow_control=_is_loopback_host(host),
            )
            server = _DashboardServer((host, int(candidate)), handler)
        except OSError as exc:
            last_error = exc
            continue
        if not _is_loopback_host(host):
            # Said out loud, because the alternative is that somebody
            # discovers it afterwards. There is no login: `allow_control`
            # turns off the endpoints that browse the filesystem, read a
            # config or start a run, and what is left is still a live view
            # of this run to anyone who can reach the port.
            logger.warning(
                "Serving on %s, which is not loopback. There is no login. "
                "Browsing, config reading and run control are disabled, and "
                "anyone who can reach this port can still watch this run. "
                "Prefer an SSH tunnel: ssh -L %s:localhost:%s <this host>",
                host, int(candidate) or "PORT", int(candidate) or "PORT")
        actual_port = int(server.server_address[1])
        thread = threading.Thread(
            target=server.serve_forever,
            name=f"FastMDXLiveDashboard:{actual_port}",
            daemon=True,
        )
        thread.start()
        return DashboardSession(
            server=server,
            thread=thread,
            root=root,
            host=host,
            port=actual_port,
            requested_port=requested_port,
            config=config,
            runtime=runtime,
        )
    if last_error is not None:
        raise last_error
    raise BackendUnavailable("No dashboard ports were available", code="environment.platform.unavailable")


def start_test_server(
    project_root: str | Path,
    *,
    config: DashboardConfig | None = None,
    home_mode: bool = False,
) -> tuple[ThreadingHTTPServer, str]:
    root = Path(project_root).resolve()
    runtime = DashboardRuntime(
        workspace_root=root,
        exploration_root=root.parent,
        active_root=None if home_mode else root,
    )
    handler = make_handler(root, config=config, runtime=runtime)
    server = _DashboardServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _artifact_records(root: Path) -> list[dict[str, str]]:
    """Every file a run has written, for the Files page.

    Read while the run is still writing. The live-frame history keeps the
    last two hundred frames, so each new frame deletes the oldest, and every
    atomic write passes through a `.tmp` file renamed into place. A file
    that vanished between the walk and its `stat` raised out of here and
    failed the whole listing -- "dashboard route failed: No such file"
    scattered through a production run, each one a refresh that landed in
    the gap. The playback beside it already allowed for this. Now each file
    is read once, a file gone since the walk is left out, and a `.tmp` is
    never listed: a half-written file is nothing to show or download. One
    reading also keeps `href`, `mtime` and `size` from disagreeing.
    """
    import stat as _stat

    records: list[dict[str, str]] = []
    if not root.exists():
        return records
    found: list[Path] = []
    try:
        for path in root.rglob("*"):
            found.append(path)
    except OSError:
        # A folder removed while it was walked: list what was reached.
        pass
    for path in sorted(found):
        if path.suffix == ".tmp" or "__pycache__" in path.parts:
            continue
        try:
            rel = path.relative_to(root).as_posix()
            info = path.stat()
        except (OSError, ValueError):
            continue
        if not _stat.S_ISREG(info.st_mode):
            continue
        label, group = _artifact_label(rel)
        version = int(info.st_mtime)
        records.append(
            {
                "path": rel,
                "name": path.name,
                "href": f"/artifacts/{rel}?v={version}",
                "download_href": f"/artifacts/{rel}?download=1&v={version}",
                "size": str(info.st_size),
                "mtime": str(info.st_mtime),
                "display_path": _compact_path(rel),
                "label": label,
                "group": group,
            }
        )
    return records
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if "__pycache__" in path.parts:
            continue
        label, group = _artifact_label(rel)
        records.append(
            {
                "path": rel,
                "name": path.name,
                "href": f"/artifacts/{rel}?v={int(path.stat().st_mtime)}",
                "download_href": (
                    f"/artifacts/{rel}?download=1&v={int(path.stat().st_mtime)}"
                ),
                "size": str(path.stat().st_size),
                "mtime": str(path.stat().st_mtime),
                "display_path": _compact_path(rel),
                "label": label,
                "group": group,
            }
        )
    return records


def _compact_path(path: str, *, keep: int = 2) -> str:
    parts = Path(path).parts
    if len(parts) <= keep:
        return path
    return ".../" + "/".join(parts[-keep:])



def _report_panels(root: Path) -> dict[str, Any]:
    """Summary cards and metric statistics, shared with the static report.

    These are computed by :mod:`fastmdxplora.gui.report_dashboard`, which the
    report phase already uses, so the browser and the generated report show
    the same numbers rather than two independent calculations.
    """
    from dataclasses import asdict

    from fastmdxplora.gui.report_dashboard import (
        _metric_rows,
        _phase_rows,
        _summary_cards,
    )

    manifest = _load_json(root / "manifest.json")
    analysis_manifest = _load_json(root / "analysis" / "analysis_manifest.json")
    sim_manifest = _load_json(root / "simulation" / "simulation_parameters.json")
    try:
        cards = _summary_cards(
            project_root=root,
            manifest=manifest,
            analysis_manifest=analysis_manifest,
            sim_manifest=sim_manifest,
        )
        metrics = _metric_rows(root, analysis_manifest)
        # The live status too: during a run the manifest holds nothing, and
        # a phase with no record is not one that did not happen.
        from fastmdxplora.gui.telemetry import read_status

        phases = _phase_rows(manifest, read_status(root))
    except Exception:  # noqa: BLE001 - a panel must never break the dashboard
        logger.debug("report panels unavailable", exc_info=True)
        return {"summary_cards": [], "metric_rows": [], "phase_rows": []}
    sections: list[dict[str, Any]] = []
    quick_actions: list[dict[str, Any]] = []
    try:
        from fastmdxplora.gui.report_dashboard import (
            analysis_sections_for,
            quick_actions_for,
        )

        built = analysis_sections_for(root)
        sections = [
            {
                "title": section.title,
                "anchor": section.anchor,
                "panels": [
                    {
                        "title": panel.title,
                        "category": panel.category,
                        "summary": panel.summary,
                        "mode": panel.mode,
                        "source": panel.source,
                        "href": f"/artifacts/{panel.source}",
                        "original_source": panel.original_source,
                        "original_href": f"/artifacts/{panel.original_source}",
                    }
                    for panel in section.panels
                ],
            }
            for section in built
            if section.panels
        ]
        quick_actions = [
            {"label": link.label, "href": f"/artifacts/{link.href}", "detail": link.detail}
            for link in quick_actions_for(root, built)
        ]
    except Exception:  # noqa: BLE001 - a panel must never break the dashboard
        logger.debug("analysis sections unavailable", exc_info=True)

    return {
        "summary_cards": [asdict(card) for card in cards],
        "metric_rows": [asdict(row) for row in metrics],
        "phase_rows": [asdict(row) for row in phases],
        "analysis_sections": sections,
        "quick_actions": quick_actions,
    }


def _results_payload(root: Path) -> dict[str, Any]:
    artifacts = _artifact_records(root)
    by_path = {record["path"]: record for record in artifacts}
    manifest = _load_json(root / "manifest.json")
    analysis_manifest = _load_json(root / "analysis" / "analysis_manifest.json")
    sim_manifest = _load_json(root / "simulation" / "simulation_parameters.json")
    plots = _plot_records(artifacts)
    report_suffixes = {".md", ".html", ".htm", ".pdf", ".pptx", ".zip"}
    reports = [
        record
        for record in artifacts
        if record["path"].startswith("report/")
        and len(Path(record["path"]).parts) == 2
        and Path(record["path"]).suffix.lower() in report_suffixes
    ]
    reports.sort(key=lambda record: _report_order(record["path"]))
    dashboard = by_path.get("report/dashboard.html")
    summary = _summary_records(root, manifest, analysis_manifest, sim_manifest)
    setup_manifest = _load_json(root / "setup" / "setup_parameters.json")
    system = _system_info(root, manifest, analysis_manifest, sim_manifest)
    return {
        "refreshed_at": _iso_now(),
        "has_analysis": any(record["path"].startswith("analysis/") for record in artifacts),
        "has_report": any(record["path"].startswith("report/") for record in artifacts),
        "output_dir": str(root),
        "run_title": _run_title(root, manifest),
        "summary": summary,
        "system": system,
        "setup": _setup_details(setup_manifest),
        "simulation": _simulation_details(sim_manifest, read_status(root)),
        "phases": _phase_records(manifest),
        "analyses": _analysis_records(analysis_manifest, artifacts, plots),
        "dashboard": dashboard,
        "plots": plots,
        "key_plots": [plot for plot in plots if plot["title"] in KEY_PLOT_TITLES][:6],
        "svg_figure_count": len(_svg_figure_paths(root)),
        "svg_bundle_href": "/analysis-figures-svg.zip",
        "reports": reports,
        "artifacts": artifacts,
        **_report_panels(root),
    }


@lru_cache(maxsize=4)
def _display_structure_cached(path_string: str, _mtime_ns: int, _size: int) -> bytes:
    from fastmdxplora.gui.live_frames import dashboard_display_pdb

    target = Path(path_string)
    text = target.read_text(encoding="utf-8", errors="ignore")
    filtered = dashboard_display_pdb(text)
    # An empty filter result means nothing matched the solute test -- an
    # unusual file rather than a solvent box. Send it as written rather than
    # sending nothing.
    return filtered.encode("utf-8") if filtered.strip() else target.read_bytes()


def _display_structure_bytes(target: Path) -> bytes:
    """The structure as the browser should draw it: solute only.

    Cached per file version, because a solvated topology is read in full to
    filter it and the viewer asks on every page load.
    """
    try:
        stat = target.stat()
        return _display_structure_cached(
            str(target.resolve()), int(stat.st_mtime_ns), int(stat.st_size)
        )
    except OSError:
        return target.read_bytes()


def _settings_for_advice(config: DashboardConfig) -> dict[str, Any]:
    """The settings an advisory reads, flattened out of the config.

    Only what is set: an advisory about a value nobody has chosen would fire
    on an empty form, which is where people are least able to act on it.
    """
    resolved = getattr(config, "resolved", None) or {}
    flat: dict[str, Any] = {}
    for phase in ("setup", "simulation"):
        block = resolved.get(phase)
        if isinstance(block, dict):
            flat.update(block)
    return flat


def _structure_info_payload(
    root: Path, config: DashboardConfig
) -> dict[str, Any]:
    # What the system contains, not what is drawn.
    structure_path = find_system(root)
    if structure_path is None:
        return {
            "valid": False,
            "reason": "missing",
            "ligand_resnames": [],
            "ligand_instances": [],
        }
    info = count_structure(structure_path, max_bytes=_MAX_PDB_BYTES_FOR_SYSTEM_SCAN)
    info = dict(info)
    info["interactions"] = _ligand_interactions(root)
    info["crystal_positions"] = _crystal_positions(root)
    if not info.get("valid"):
        return dict(
            info,
            structure_available=True,
            structure_url="/structure/topology.pdb",
            ligand_resnames=[],
            ligand_instances=[],
        )
    info["atoms_by_resname"] = ligand_atom_counts(structure_path)
    info["explicit_ligand"] = config.ligand_resname

    # What is worth knowing before the run, beside the settings rather than
    # in the log afterwards. A metal that this force field will not hold in
    # its site is a thing to say while somebody is still choosing, not once
    # they have stopped watching.
    from fastmdxplora.advisories import advise

    info["advisories"] = [
        {"setting": a.setting, "summary": a.summary,
         "detail": a.detail, "remedy": a.remedy}
        for a in advise(info, _settings_for_advice(config))
    ]
    try:
        info["structure_path"] = structure_path.relative_to(root).as_posix()
    except ValueError:
        info["structure_path"] = structure_path.as_posix()
    info["structure_url"] = (
        f"/structure/topology.pdb?v={int(structure_path.stat().st_mtime_ns)}"
    )
    info["structure_available"] = True
    return info


def _ligands_payload(
    root: Path, config: DashboardConfig
) -> dict[str, Any]:
    # Preparation strips the ligand and puts it back with small-molecule
    # parameters, so the prepared structure is the one file that never has it.
    structure_path = find_system(root)
    if structure_path is None or not structure_path.is_file():
        return {"ligands": [], "explicit": normalise_ligand_resname(config.ligand_resname), "valid": False}
    info = count_structure(structure_path, max_bytes=_MAX_PDB_BYTES_FOR_SYSTEM_SCAN)
    instances = []
    if info.get("valid"):
        explicit = [config.ligand_resname] if config.ligand_resname else None
        detected = detect_ligands(
            # Reconstruct (chain, resname, resi) keys from info — a
            # already collected them in count_structure. To avoid a
            # second PDB walk is avoided, so callers that want full
            # ligand IDs receive them via /api/structure-info.
            (
                (str(ins.get("chain", "A")), str(ins.get("resname", "")), str(ins.get("resi", "")))
                for ins in info.get("ligand_instances", [])
                if isinstance(ins, dict)
            ),
            explicit=explicit,
            include_cofactors=config.include_cofactors,
        )
        instances = [
            {
                "chain": inst["chain"],
                "resname": inst["resname"],
                "resi": inst["resi"],
                "explicit": inst["explicit"],
            }
            for inst in detected["instances"]
        ]
    return {
        "ligands": instances,
        "resnames": sorted({inst["resname"] for inst in instances}),
        "explicit": normalise_ligand_resname(config.ligand_resname),
        "include_cofactors": config.include_cofactors,
        "valid": True,
    }


def _live_coordinates_payload(root: Path) -> dict[str, Any]:
    sim_dir = root / "simulation"
    index = read_live_frame_index(sim_dir)
    return {
        "live_frame_exists": live_frame_exists(sim_dir),
        "index": index,
        "available": bool(index.get("live_frame_available")),
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _summary_records(
    root: Path,
    manifest: dict[str, Any],
    analysis_manifest: dict[str, Any],
    sim_manifest: dict[str, Any],
) -> list[dict[str, str]]:
    phase_statuses = [
        str(item.get("status") or "unknown").lower()
        for item in manifest.get("phases", [])
        if isinstance(item, dict)
    ]
    if phase_statuses and all(value in {"ok", "completed", "skipped"} for value in phase_statuses):
        status = "completed"
    elif any(value in {"error", "failed"} for value in phase_statuses):
        status = "failed"
    elif phase_statuses:
        status = "in progress"
    elif read_status(root):
        # No manifest yet, but telemetry says a run is going. The manifest is
        # written when the run ends, so its absence is not a fact about the
        # run.
        status = "in progress"
    else:
        status = "not run"
    frames = _first_present(
        analysis_manifest.get("n_frames"),
        sim_manifest.get("n_production_frames"),
    )
    atoms = analysis_manifest.get("n_atoms")
    sim_time = sim_manifest.get("duration_ns_actual")
    live_status = read_status(root)
    temperature = _first_present(
        live_status.get("target_temperature_K"),
        _last_metric_value(root, "temperature"),
    )
    latest = _first_present(live_status.get("status"), status)
    return [
        {"label": "Project status", "value": status},
        {"label": "Latest status", "value": str(latest or "—")},
        {"label": "Frames", "value": _display_value(frames)},
        {"label": "Atoms", "value": _display_value(atoms)},
        {
            "label": "Simulation time",
            "value": f"{sim_time} ns" if sim_time is not None else "—",
        },
        {
            "label": "Temperature",
            "value": f"{temperature} K" if temperature not in (None, "") else "—",
        },
    ]


def _last_metric_value(root: Path, field: str) -> str | None:
    metrics = read_metrics(root, limit=1)
    if not metrics:
        return None
    value = metrics[-1].get(field)
    return str(value) if value not in (None, "") else None


#: The three interaction kinds the ligand panel has rows for, and the label
#: each row carries. `pl_interactions` records eight; these are the three a
#: reader is shown without opening the analysis.
_PANEL_INTERACTION_KINDS = {
    "hydrogen_bond": "hbonds",
    "hydrophobic": "hydrophobic",
    "salt_bridge": "salt_bridges",
}


def _ligand_interactions(root: Path) -> dict[str, Any]:
    """What holds the ligand, from the analysis that measured it.

    The panel used to print "Requires analysis output" in all three rows,
    unconditionally -- including for a run that had produced exactly that
    output. It also could not distinguish a contact type that was looked for
    and not found from one that was never looked for, which are different
    things to tell somebody about a binding site.
    """
    table = root / "analysis" / "pl_interactions" / "pl_interactions.dat"
    if not table.is_file():
        return {"analysed": False, "kinds": {}}

    # Counted by residue, not by row. Each row is one ligand-atom /
    # protein-atom pair, so a twelve-atom benzene against six residues
    # produces scores of rows -- and reporting that count read as though the
    # ligand were held by eighty-four separate things. What a binding site is
    # described by is which residues touch it.
    seen: dict[str, dict[str, Any]] = {
        label: {"residues": {}, "pairs": 0} for label in _PANEL_INTERACTION_KINDS.values()
    }
    try:
        with table.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                label = _PANEL_INTERACTION_KINDS.get(str(row.get("kind", "")).strip())
                if label is None:
                    continue
                entry = seen[label]
                entry["pairs"] += 1
                residue = str(row.get("residue", "")).strip() or "?"
                record = entry["residues"].setdefault(
                    residue, {"occupancy": None, "well_sampled": False}
                )
                occupancy = _safe_float_value(row.get("occupancy"))
                if occupancy is not None:
                    best = record["occupancy"]
                    record["occupancy"] = occupancy if best is None else max(best, occupancy)
                if str(row.get("well_sampled", "")).strip().lower() in {"true", "1"}:
                    record["well_sampled"] = True
    except (OSError, csv.Error):
        return {"analysed": False, "kinds": {}}

    # Every residue that met an interaction criterion, whichever kind, best
    # observed first. Distinct from the viewer's pocket button, which takes
    # everything within a distance cutoff: these are contacts that were
    # measured, not neighbours that were counted.
    contacts: dict[str, float] = {}
    for entry in seen.values():
        for residue, record in entry["residues"].items():
            occupancy = record["occupancy"] or 0.0
            contacts[residue] = max(contacts.get(residue, 0.0), occupancy)
    ranked_contacts = [
        name for name, _ in sorted(contacts.items(), key=lambda item: -item[1])
    ]

    kinds: dict[str, Any] = {}
    for label, entry in seen.items():
        residues = entry["residues"]
        ranked = sorted(
            residues.items(),
            key=lambda item: (item[1]["occupancy"] is None, -(item[1]["occupancy"] or 0.0)),
        )
        best_name, best = (ranked[0] if ranked else (None, {}))
        kinds[label] = {
            "residues": len(residues),
            "pairs": entry["pairs"],
            "best_residue": best_name,
            "best_occupancy": best.get("occupancy") if best else None,
            # A residue whose every contact is thinly observed is a weaker
            # claim than the count alone suggests.
            "thinly_sampled": sum(
                1 for record in residues.values() if not record["well_sampled"]
            ),
        }
    return {"analysed": True, "kinds": kinds, "contact_residues": ranked_contacts}


def _safe_float_value(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


#: What a reader is looking for when they open the Report tab, in the order
#: they are likely to want it. Grouping by top-level directory instead put the
#: trajectory beside the dashboard's own scratch, and sorted everything by
#: filename -- so finding `production.dcd` meant knowing it was called that.
ARTIFACT_GROUPS = (
    ("deliverables", "Reports and deliverables"),
    ("simulation", "Simulation data"),
    ("analysis", "Analysis data"),
    ("figures", "Figures"),
    ("record", "Run record"),
)

#: Files whose purpose is not guessable from the name.
_ARTIFACT_LABELS = {
    "report/report.pdf": ("Report (PDF)", "deliverables"),
    "report/report.md": ("Report (Markdown)", "deliverables"),
    "report/slides.pptx": ("Slide deck", "deliverables"),
    "report/slides_outline.md": ("Slide outline", "deliverables"),
    "report/dashboard.html": ("Standalone dashboard", "deliverables"),
    "report/project_bundle.zip": ("Everything, zipped", "deliverables"),
    "simulation/production.dcd": ("Production trajectory", "simulation"),
    "simulation/topology.pdb": ("Simulated system, solvated", "simulation"),
    "simulation/state_final.xml": ("Final state: positions, velocities, box", "simulation"),
    "simulation/state_minimized.xml": ("State after minimisation", "simulation"),
    "simulation/checkpoint.chk": ("Checkpoint: positions and velocities, for recovery by hand", "simulation"),
    "simulation/energy.csv": ("Energy log written by OpenMM", "simulation"),
    "simulation/playback.pdb": ("Trajectory prepared for the viewer", "record"),
    "simulation/simulation.log": ("Simulation log", "record"),
    "simulation/simulation_parameters.json": ("Settings this simulation used", "record"),
    "analysis/analysis_manifest.json": ("What each analysis produced", "record"),
    "manifest.json": ("What each phase produced", "record"),
    "resolved_config.yml": ("The configuration this run resolved to", "record"),
}

#: Where a name says enough on its own, with the extension deciding the group.
_ARTIFACT_SUFFIXES = {
    ".png": ("figure (PNG)", "figures"),
    ".svg": ("figure (SVG)", "figures"),
    ".dat": ("data", "analysis"),
    ".csv": ("data (CSV)", "analysis"),
    ".npy": ("array", "analysis"),
}


def _artifact_label(rel: str) -> tuple[str, str]:
    """A description a reader can act on, and the group it belongs in."""
    named = _ARTIFACT_LABELS.get(rel)
    if named:
        return named

    path = Path(rel)
    if "live_frames" in path.parts or path.name.startswith("live_"):
        return (f"Live viewer scratch: {path.name}", "record")
    if path.name == "options.json":
        # The analysis it belongs to is its parent directory, and sixteen of
        # these listed by name were indistinguishable.
        return (f"{path.parent.name}: options used", "record")

    described, group = _ARTIFACT_SUFFIXES.get(path.suffix.lower(), ("", ""))
    if described:
        stem = path.stem.replace("_", " ")
        return (f"{stem}: {described}", group)
    return (path.name, "record")


def _crystal_positions(root: Path) -> dict[str, list[str]]:
    """Where each ligand sits in the structure the run started from.

    The chain and residue number the panel shows otherwise are OpenMM's, from
    the solvated system it built -- `X 0` for a benzene the PDB entry calls
    `BNZ A400`. The crystal numbering is the durable one: it is what the entry
    says, what a reader would check, and what any other tool will expect.

    Read from `setup/input.pdb`, the source structure placed there unaltered,
    rather than plumbed through the setup pipeline -- the file is already in
    every run directory.
    """
    source = root / "setup" / "input.pdb"
    if not source.is_file():
        return {}
    info = count_structure(source)
    if not info.get("valid"):
        return {}
    positions: dict[str, list[str]] = {}
    for instance in info.get("ligand_instances", []):
        resname = str(instance.get("resname") or "").strip()
        chain = str(instance.get("chain") or "").strip()
        resi = str(instance.get("resi") or "").strip()
        if not resname or not (chain or resi):
            continue
        positions.setdefault(resname, []).append(f"{chain}{resi}".strip())
    return positions


def _system_name(root: Path, manifest: dict[str, Any]) -> str:
    """What this run is of, from whichever record has it yet."""
    named = manifest.get("system")
    if named:
        return str(named)
    setup_manifest = _load_json(root / "setup" / "setup_parameters.json")
    recorded = setup_manifest.get("input")
    if isinstance(recorded, dict) and recorded.get("system"):
        return str(recorded["system"])
    # Empty rather than a dash: with no name anywhere, the browser's own
    # fallback label is better than a dash overriding it.
    return ""


def _system_info(
    root: Path,
    manifest: dict[str, Any],
    analysis_manifest: dict[str, Any],
    sim_manifest: dict[str, Any],
) -> dict[str, str]:
    live_status = read_status(root)
    params = sim_manifest.get("parameters")
    if not isinstance(params, dict):
        params = sim_manifest
    return {
        # The setup phase records the system before simulation starts, so a
        # run in progress has had a name from its first minute. Reading only
        # the manifest, which is written when the run ends, the top bar spent
        # the whole run showing the browser's placeholder label instead.
        "system": _system_name(root, manifest),
        "output_folder": root.as_posix(),
        "atoms": _display_value(analysis_manifest.get("n_atoms")),
        "frames": _display_value(
            _first_present(
                analysis_manifest.get("n_frames"),
                sim_manifest.get("n_production_frames"),
            )
        ),
        "platform": _display_value(
            _first_present(
                live_status.get("platform"),
                sim_manifest.get("platform_used"),
                sim_manifest.get("platform"),
                params.get("platform"),
            )
        ),
        "timestep_fs": _display_value(
            _first_present(live_status.get("timestep_fs"), params.get("timestep_fs"))
        ),
        "checkpoint": _display_value(
            _first_present(
                live_status.get("current_checkpoint_path"),
                live_status.get("checkpoint_path"),
            )
        ),
    }


def _run_title(root: Path, manifest: dict[str, Any]) -> str:
    options = manifest.get("options") if isinstance(manifest.get("options"), dict) else {}
    report_options = options.get("report") if isinstance(options.get("report"), dict) else {}
    title = (
        report_options.get("title")
        or report_options.get("report_title")
        or manifest.get("title")
    )
    if title:
        return str(title)
    system = manifest.get("system")
    if system:
        # Just the system. The product name is already in the sidebar.
        return Path(str(system)).stem
    return root.name or "FastMDXplora Live"


def _setup_details(setup_manifest: dict[str, Any]) -> dict[str, Any]:
    params = setup_manifest.get("parameters")
    if not isinstance(params, dict):
        params = setup_manifest
    forcefield = setup_manifest.get("resolved_forcefield")
    if not isinstance(forcefield, dict):
        forcefield = {}
    ligand = forcefield.get("ligand") or params.get("ligand")
    if isinstance(ligand, dict):
        ligand = ligand.get("name") or ligand.get("files")
    return {
        "ph": _first_present(params.get("ph"), params.get("pH")),
        "ion_concentration_M": _first_present(
            params.get("ion_concentration_M"),
            params.get("ion_concentration_m"),
        ),
        "force_field": _first_present(
            forcefield.get("name"),
            forcefield.get("protein_forcefield"),
            forcefield.get("forcefield"),
            params.get("forcefield"),
            params.get("force_field"),
        ),
        "water_model": _first_present(
            forcefield.get("water_model"),
            params.get("water_model"),
        ),
        "ligand": ligand,
    }


def _simulation_details(
    sim_manifest: dict[str, Any],
    live_status: dict[str, Any],
) -> dict[str, Any]:
    params = sim_manifest.get("parameters")
    if not isinstance(params, dict):
        params = sim_manifest
    return {
        "platform": _first_present(
            live_status.get("platform"),
            sim_manifest.get("platform_used"),
            sim_manifest.get("platform"),
            params.get("platform"),
        ),
        "precision": _first_present(
            live_status.get("precision"),
            params.get("precision"),
        ),
        "temperature_K": _first_present(
            live_status.get("target_temperature_K"),
            params.get("temperature_K"),
        ),
        "timestep_fs": _first_present(
            live_status.get("timestep_fs"),
            params.get("timestep_fs"),
        ),
        "friction_per_ps": params.get("friction_per_ps"),
        "pressure_bar": _first_present(
            params.get("pressure_bar"),
            params.get("pressure_atm"),
        ),
        "duration_ns_actual": _first_present(
            sim_manifest.get("duration_ns_actual"),
            params.get("duration_ns"),
        ),
        "n_production_frames": _first_present(
            sim_manifest.get("n_production_frames"),
            live_status.get("current_frame_count"),
        ),
    }


def _analysis_records(
    analysis_manifest: dict[str, Any],
    artifacts: list[dict[str, str]],
    plots: list[dict[str, str]],
) -> list[dict[str, Any]]:
    results = analysis_manifest.get("results")
    if not isinstance(results, dict):
        if analysis_manifest.get("status"):
            return [
                {
                    "name": "analysis",
                    "title": "Analysis",
                    "status": str(analysis_manifest.get("status")),
                    "message": str(analysis_manifest.get("note") or ""),
                    "artifacts": [],
                    "plot": None,
                }
            ]
        return []

    records: list[dict[str, Any]] = []
    for name, raw in results.items():
        data = raw if isinstance(raw, dict) else {}
        prefix = f"analysis/{name}/"
        related = [record for record in artifacts if record["path"].startswith(prefix)]
        plot = next(
            (
                item
                for item in plots
                if item["path"].startswith(prefix)
                or _normalise_analysis_name(item["title"]) == _normalise_analysis_name(str(name))
            ),
            None,
        )
        records.append(
            {
                "name": str(name),
                "title": PLOT_TITLE_ALIASES.get(str(name).lower(), _humanize_stem(str(name))),
                "status": str(data.get("status") or "unknown"),
                "message": str(data.get("message") or ""),
                "started_at": data.get("started_at"),
                "finished_at": data.get("finished_at"),
                "artifacts": related,
                "plot": plot,
            }
        )
    return records


def _normalise_analysis_name(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _report_order(path: str) -> tuple[int, str]:
    preferred = {
        "report/dashboard.html": 0,
        "report/report.md": 1,
        "report/slides.pptx": 2,
        "report/project_bundle.zip": 3,
    }
    return preferred.get(path, 99), path


def _first_present(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "")), None)


def _display_value(value: Any) -> str:
    """A value, or a dash where there is not one yet.

    This used to return the string "not available", which is a verdict rather
    than a value: it travelled into the results payload and was rendered under
    labels promising a system name, an atom count, a platform. A run in
    progress has no manifest to read those from, and saying so with a dash is
    the same convention the rest of the page uses for "not yet".
    """
    return "—" if value in (None, "") else str(value)


def _phase_records(manifest: dict[str, Any]) -> list[dict[str, str]]:
    seen: dict[str, str] = {}
    for phase in manifest.get("phases", []):
        if isinstance(phase, dict):
            name = str(phase.get("name") or "").lower()
            if name:
                seen[name] = str(phase.get("status") or "unknown")
    records = []
    for name in ("setup", "simulation", "analysis", "report"):
        status = seen.get(name, "not run")
        records.append({"name": name.title() if name != "simulation" else "Simulation", "status": status})
    return records


def _plot_records(artifacts: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return one display record per scientific figure, paired with SVG.

    PNG is preferred for fast browser previews while a matching true-vector
    SVG is exposed for publication download.  Pairing by normalized title also
    handles report/dashboard_assets copies and per-analysis artifact names.
    """
    image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".svg"}
    candidates = [
        record for record in artifacts
        if Path(record["path"]).suffix.lower() in image_suffixes
        and (
            record["path"].startswith("report/dashboard_assets/")
            or record["path"].startswith("analysis/")
        )
    ]
    grouped: dict[str, list[dict[str, str]]] = {}
    for record in candidates:
        title = _plot_title(record["path"])
        if title:
            grouped.setdefault(title, []).append(record)

    results: list[dict[str, str]] = []
    for title, records in grouped.items():
        display = min(records, key=lambda item: _plot_record_priority(item["path"]))
        svg_candidates = [item for item in records if Path(item["path"]).suffix.lower() == ".svg"]
        svg = min(svg_candidates, key=lambda item: _plot_priority(item["path"])) if svg_candidates else None
        # Prefer what the analysis wrote over the report's restyled copy, so
        # every surface shows the same figure.
        analysis_first = [
            item for item in records
            if not item["path"].startswith("report/dashboard_assets/")
        ]
        if analysis_first:
            display = min(analysis_first, key=lambda item: _plot_record_priority(item["path"]))
        mode = "analysis figure"
        enriched = dict(display)
        enriched.update(
            {
                "title": title,
                "category": PLOT_CATEGORY_BY_TITLE.get(title, _plot_category(display["path"])),
                "mode": mode,
            }
        )
        if svg is not None:
            enriched.update(
                {
                    "svg_path": svg["path"],
                    "svg_href": svg["href"],
                    "svg_download_href": svg["download_href"],
                }
            )
        results.append(enriched)
    return sorted(results, key=lambda item: (_category_order(item["category"]), item["title"]))


def _plot_record_priority(path: str) -> tuple[int, int]:
    suffix = Path(path).suffix.lower()
    suffix_priority = {".png": 0, ".jpg": 1, ".jpeg": 1, ".gif": 2, ".svg": 3}
    return _plot_priority(path), suffix_priority.get(suffix, 9)


def _plot_priority(path: str) -> int:
    # dashboard_assets/ now holds only the viewer's protein preview, so it
    # should never be chosen ahead of a figure an analysis produced.
    return 1 if path.startswith("report/dashboard_assets/") else 0


def _plot_title(path: str) -> str | None:
    stem = Path(path).stem
    if stem in DASHBOARD_ASSET_TITLES:
        return DASHBOARD_ASSET_TITLES[stem]
    if stem in PLOT_TITLE_ALIASES:
        return PLOT_TITLE_ALIASES[stem]
    lower = stem.lower()
    for key, title in PLOT_TITLE_ALIASES.items():
        if lower == key or lower.endswith(f"_{key}") or key in lower:
            return title
    return _humanize_stem(stem)


def _humanize_stem(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").strip().title()


def _plot_category(path: str) -> str:
    parts = Path(path).parts
    if "cluster" in parts or "dashboard_assets" in parts and "cluster" in path:
        return "Clustering"
    if any(part in {"rmsd", "rmsf", "rg", "hbonds", "hbond"} for part in parts):
        return "Core Metrics"
    if any(part in {"sasa", "ss", "dimred", "dihedrals", "qvalue"} for part in parts):
        return "Additional Analysis"
    return "Other"


def _category_order(category: str) -> int:
    order = {"Core Metrics": 0, "Additional Analysis": 1, "Clustering": 2, "Other": 3}
    return order.get(category, 99)


def _svg_figure_paths(root: Path) -> list[Path]:
    """Return generated SVG figures, excluding branding/static assets."""
    paths: list[Path] = []
    for base in (root / "analysis", root / "report" / "dashboard_assets", root / "report"):
        if not base.is_dir():
            continue
        for path in base.rglob("*.svg"):
            if path.is_file() and path not in paths:
                paths.append(path)
    return sorted(paths)


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _open_local_path(path: Path) -> tuple[bool, str]:
    """Open ``path`` in the host file manager on a best-effort basis."""

    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:  # noqa: BLE001 - dashboard helper only
        return False, str(exc)
    return True, "opened"


def _is_loopback_host(host: str) -> bool:
    normalized = str(host).strip().lower().strip("[]")
    return normalized in {"127.0.0.1", "localhost", "::1"}
