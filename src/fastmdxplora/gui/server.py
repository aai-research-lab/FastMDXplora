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
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from fastmdxplora.gui.browse import is_study
from fastmdxplora.gui.hosting import ACCOUNT_HEADER, SECRET_HEADER, Hosting
from fastmdxplora.gui.exploration import (
    _NO_CURRENT_RUN,
    DashboardRuntime,
)
from fastmdxplora.ligand_detection import detect_ligands, normalise_ligand_resname
from fastmdxplora.gui.live_frames import live_frame_exists, read_live_frame_index
from fastmdxplora.gui.viewed_structure import (
    display_structure_bytes as _display_structure_bytes,
)
from fastmdxplora.gui.viewed_structure import structure_file as _structure_file
from fastmdxplora.gui.viewed_structure import viewer_structure as _viewer_structure
from fastmdxplora.gui.protein_preview import (
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
    read_study_status,
    run_phases,
    run_stages,
    status_as_it_stands,
)
from fastmdxplora.refusals import StudyError
from fastmdxplora.refusals import BackendUnavailable

logger = logging.getLogger("fastmdxplora.gui.server")


def _imported_by_the_routes() -> tuple[str, ...]:
    """Every module of the package the routes can reach (route_imports.py),
    imported once by the thread that builds the server, before any request
    is served: two requests importing into the package's circular imports
    at once is refused by Python as a deadlock, and the loser answered 500."""
    from fastmdxplora.gui.route_imports import modules_reached_from

    return modules_reached_from(__name__)


def _import_what_the_routes_import() -> None:
    import importlib

    for name in _imported_by_the_routes():
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 -- an optional dependency: the route says so
            logger.debug("dashboard: %s not imported ahead: %s", name, exc)


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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _import_what_the_routes_import()
        super().__init__(*args, **kwargs)

    #: Set when the server is shutting down, so an open event stream ends
    #: rather than holding `server_close` on its thread.
    closing = False

    def shutdown(self) -> None:
        self.closing = True
        super().shutdown()

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

#: The POST routes a dashboard bound beyond loopback still answers; every
#: other POST is refused there, a route added later included. There is no
#: login, so the bind address is the whole of the trust model, and a list of
#: what to refuse fails open for whichever route nobody remembered to put on
#: it. The other routes start runs, change which folder is served, read files
#: the caller names, and store or spend an API key. Building a config from
#: the posted form reads no file and changes nothing, so it alone is open.
POSTS_ANSWERED_BEYOND_LOOPBACK = frozenset({"/api/config"})

#: The GET routes a dashboard bound beyond loopback still answers, listed for
#: the same reason as the POSTs: a route added later is refused there until
#: somebody decides otherwise. What is listed is what watching a run needs,
#: the page, its assets, and the run's status, results and files. Walking the
#: disk, opening a folder and reading the agent's conversations are not.
GETS_ANSWERED_BEYOND_LOOPBACK = frozenset({
    "/", "/index", "/results", "/live",
    "/api/app-state", "/api/explore/state", "/api/schema",
    "/api/status", "/api/metrics", "/api/events", "/api/report", "/api/methods",
    "/api/artifacts", "/api/files", "/api/files-page", "/api/results", "/api/analyses",
    "/api/file-text", "/api/protein-preview", "/api/structure-info",
    "/api/ligands", "/api/live-frame-index", "/api/live-coordinates",
    "/api/series", "/api/runs-compared", "/api/selection",
    "/api/analysis-overview", "/api/convergence", "/api/overview", "/api/figure-data",
    "/api/measure-over-frames", "/api/residue-values", "/api/secondary-structure",
    "/api/frames-info", "/api/frames-superposed", "/api/interactions-over-frames",
    "/api/frames-pieces", "/structure/frames-piece.xtc",
    "/api/chain-contacts", "/api/occupancy", "/api/water-sites", "/api/motion",
    "/api/states", "/api/state-difference", "/api/backbone-angles",
    "/api/contact-map", "/api/contact-pair", "/api/pocket-volume", "/structure/pocket.dx",
    "/api/views", "/api/viewer-atoms", "/api/viewer-selections", "/api/scenes",
    "/api/stopping", "/api/stream",
    "/analysis-figures-svg.zip",
    "/structure/topology.pdb", "/structure/live-frame.pdb", "/structure/live-frame.dcd",
    "/structure/frames.dcd", "/structure/frames-topology.pdb", "/structure/occupancy.dx",
})
GET_PREFIXES_ANSWERED_BEYOND_LOOPBACK = ("/static/", "/artifacts/", "/scenes/")

#: What the Viewer reads of the run it plays: for a study of several runs,
#: the first with a trajectory, the others rendered beside it
#: (runs_together.py); for a study of one run, the study.
_READ_FROM_THE_RUN_SHOWN = frozenset({
    "/api/selection", "/api/residue-states", "/api/measure-over-frames",
    "/api/secondary-structure", "/api/structure-info", "/api/ligands",
    "/api/viewer-atoms", "/api/chain-contacts", "/api/interactions-over-frames",
    "/api/frames-superposed", "/structure/topology.pdb", "/structure/frames.dcd",
    "/structure/frames-topology.pdb", "/api/occupancy", "/api/water-sites",
    "/structure/occupancy.dx", "/api/motion", "/api/states", "/api/state-difference",
    "/api/beside", "/structure/beside.pdb", "/structure/beside.dcd",
    "/api/frames-pieces", "/structure/frames-piece.xtc", "/api/backbone-angles",
    "/api/contact-map", "/api/contact-pair", "/api/pocket-volume", "/structure/pocket.dx",
})


#: How a document from a study is served: in a sandbox of its own origin,
#: so a script in it, the report dashboard's or anybody's, runs without the
#: GUI's standing. Its forms, popups and navigation of the page are off.
ARTIFACT_SANDBOX = "sandbox allow-scripts"


def _can_run_script(content_type: str) -> bool:
    """HTML, SVG and XML documents can carry script when opened as pages."""
    kind = content_type.split(";")[0].strip().lower()
    return "html" in kind or "xml" in kind


def _get_answered_beyond_loopback(path: str) -> bool:
    return (path in GETS_ANSWERED_BEYOND_LOOPBACK
            or path.startswith(GET_PREFIXES_ANSWERED_BEYOND_LOOPBACK))


def _private(parts: tuple[str, ...]) -> bool:
    """A path inside a run that is not one of its results.

    Hidden files and folders, and the agent's conversations, which hold what
    somebody typed and the content of files they attached. They are kept in
    the study, so the file list offered them and, beyond loopback,
    `/artifacts/` and `/api/file-text` handed out what the conversation
    routes refuse. Never listed, and never served beyond loopback.
    """
    from fastmdxplora.gui.agent_panel import CONVERSATIONS_SUBDIR

    if any(part.startswith(".") for part in parts):
        return True
    store = CONVERSATIONS_SUBDIR.parts
    return any(parts[i:i + len(store)] == store for i in range(len(parts)))


def _served_beyond_loopback(root: Path, target: Path) -> bool:
    """Whether a public bind may send this file: inside the watched run once
    links are followed, and one of its results."""
    try:
        parts = target.resolve().relative_to(root.resolve()).parts
    except (OSError, ValueError):
        return False
    return not _private(parts)



#: How much of a file is read and sent at a time.
SENT_AT_A_TIME = 1 << 20


def byte_range(asked: str, size: int) -> tuple[int, int] | tuple[()] | None:
    """The bytes a `Range` header asks for, first and last inclusive; ``()``
    where it asks for none this server answers in part (several ranges, or
    words it does not read), so the whole file is sent; None where it asks
    only for bytes the file does not have (416), as RFC 9110 reads it."""
    unit, _, spans = asked.partition("=")
    if unit.strip().lower() != "bytes" or "," in spans:
        return ()
    first_said, dash, last_said = spans.strip().partition("-")
    # Digits only: int() also reads "+5" and "1_0", which no range is.
    if not dash or not (first_said or last_said) \
            or not all(part.isdigit() and part.isascii() for part in (first_said, last_said) if part):
        return ()
    if not first_said:
        # The last n bytes; the last none is no bytes at all.
        count = int(last_said)
        if count == 0 or size == 0:
            return None
        return max(0, size - count), size - 1
    first = int(first_said)
    last = int(last_said) if last_said else size - 1
    if first >= size:
        return None
    if last < first:
        return ()
    return first, min(last, size - 1)


def _names_the_tag(header: str | None, tag: str) -> bool:
    """Whether `If-None-Match` names this version of the file."""
    if not header:
        return False
    said = [part.strip() for part in header.split(",")]
    return "*" in said or tag in said or f"W/{tag}" in said


def attachment_named(name: str) -> str:
    """`Content-Disposition` for a download of a file called ``name``, the
    name kept as it is (RFC 6266) where it is not plain ASCII."""
    plain = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in name)
    if plain == name:
        return f'attachment; filename="{plain}"'
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"


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
#: The most rows of the live record the charts are sent: the whole run,
#: thinned evenly (overview_view.thinned_metrics).
MOST_CHART_SAMPLES = 600
#: The AAi Research Lab's logo (its site's, scaled to 128 px), the avatar
#: at the foot of the sidebar; the standalone dashboard inlines it.
LAB_LOGO = "lab-logo.png"
#: FastMDXplora's mark, white on black, as the tab's icon (made by
#: scripts/make_mark.py). Over the folded sidebar it is written inline, in the
#: accent (``sidebar_icons.ICONS["mark"]``); the standalone dashboard
#: inlines the icon as well.
PRODUCT_MARK = "fastmdx-mark.svg"


@dataclass
class DashboardConfig:
    """Per-dashboard-server knobs supplied by the CLI."""

    ligand_resname: str | None = None
    include_cofactors: bool = False
    binding_pocket_cutoff_A: float = 5.0
    #: The most frames of the trajectory the viewer is sent
    #: (`trajectory_frames.MOST_FRAMES`); fewer where they would pass ten
    #: million atoms times frames.
    max_browser_frames: int = 2000
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
    hosting: Hosting | None = None,
) -> type[BaseHTTPRequestHandler]:
    """The request handler. With `hosting`, the GUI is served to someone
    else through a proxy; see :mod:`fastmdxplora.gui.hosting`."""
    if hosting is not None:
        # The proxy's secret and the listed names are the trust; the bind
        # address is not, since the proxy reaches it over a network.
        allow_control = True
    root = Path(project_root).resolve()
    app_runtime = runtime or DashboardRuntime(
        workspace_root=root,
        exploration_root=root.parent,
        active_root=root,
    )
    cfg = config or DashboardConfig()
    html = template_html if template_html is not None else _load_template()
    from fastmdxplora.gui.sidebar_icons import with_icons

    html = with_icons(html)
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
        __copyright__,
        __doi__,
        __expansion__,
        __version__,
    )

    html = html.replace("__FASTMDX_CITATION__", _escape(__citation__))
    html = html.replace("__FASTMDX_DOI__", _escape(__doi__))
    html = html.replace("__FASTMDX_VERSION__", _escape(__version__))
    html = html.replace("__FASTMDX_BIBTEX__", _escape(__bibtex__))
    html = html.replace("__FASTMDX_COPYRIGHT__", _escape(__copyright__))
    # Hosted behind a service, the service's name and line are shown where
    # this software's would be; its name without a line of its own has none,
    # not this software's (the citation and the links to it stay). The
    # person is named per request, below.
    product = hosting.product_name if hosting is not None else ""
    tagline = hosting.product_tagline if hosting is not None else ""
    if not tagline and not product:
        tagline = __expansion__
    # The person's placeholders become random marks first, and the names are
    # put in with one pass each, so no text put in (a name holding a
    # placeholder's spelling) is ever read as a placeholder.
    import re as _re
    import secrets as _secrets

    mark = _secrets.token_hex(8)
    person_marks = {f"__FASTMDX_NAME_{mark}__": "name",
                    f"__FASTMDX_INITIALS_{mark}__": "initials"}
    for spot, which in person_marks.items():
        html = html.replace(f"__FASTMDX_ACCOUNT_{which.upper()}__", spot)
    # The sidebar's line under the name is a service's own only: the
    # product's expansion is on the Cite page and the loading screen.
    brand_line = hosting.product_tagline if hosting is not None else ""
    shown = {"__FASTMDX_TITLE__": product or "FastMDXplora GUI",
             "__FASTMDX_PRODUCT__": product or "FastMDXplora",
             "__FASTMDX_TAGLINE__": tagline,
             "__FASTMDX_BRAND_LINE__": brand_line,
             "__FASTMDX_EXPANSION__": __expansion__}
    html = _re.sub("|".join(shown), lambda m: _escape(shown[m.group(0)]), html)
    # The settings menu at the foot of the sidebar opens with the way back
    # to the service's own page for the person; the GUI has no other.
    account_item = ""
    if hosting is not None and hosting.account_url:
        account_item = (
            '<div class="settings-section">Account</div>'
            f'<a href="{_escape(hosting.account_url)}" class="settings-item" '
            'id="settings-account-link">Your account '
            '<span class="mono settings-hint">runs, files, sign out</span></a>'
            '<div class="settings-divider"></div>')
    html = html.replace("<!--__FASTMDX_ACCOUNT_ITEM__-->", account_item)
    # FastMDXplora's mark as the tab's icon and over the folded sidebar, and
    # the lab's logo as the avatar at the foot of the sidebar; a hosted
    # service's own logo (--product-logo) is all three. The avatar is the
    # person's initials where the proxy names somebody, so the logo goes in
    # per request, below.
    from fastmdxplora.gui.sidebar_icons import icon as _icon

    own_logo = hosting.product_logo if hosting is not None else ""
    html = html.replace("<!--__FASTMDX_LAB_ICON__-->", (
        f'<link rel="icon" href="{_escape(own_logo)}">' if own_logo
        else f'<link rel="icon" type="image/svg+xml" href="/static/{PRODUCT_MARK}">'))
    html = html.replace("<!--__FASTMDX_STRIP_MARK__-->", (
        f'<img class="strip-mark" src="{_escape(own_logo)}" alt="">' if own_logo
        else _icon("mark", "strip-mark")))
    html = html.replace("<!--__FASTMDX_PHONE_MARK__-->", (
        f'<img class="brand-mark-tile" src="{_escape(own_logo)}" alt="">' if own_logo
        else f'<img class="brand-mark-tile" src="/static/{PRODUCT_MARK}" alt="">'))
    avatar = own_logo or f"/static/{LAB_LOGO}"
    logo_mark = f'<img class="sidebar-account-logo" src="{_escape(avatar)}" alt="">'
    logo_spot = f"__FASTMDX_LOGO_{_secrets.token_hex(8)}__"
    html = html.replace("<!--__FASTMDX_LAB_MARK__-->", logo_spot)

    # And, when the service runs studies on its own compute, a way to send
    # one there: the service's page opens with the config, for the person to
    # confirm. Never started from here.
    elsewhere = as_it_is_elsewhere = ""
    if hosting is not None and hosting.runs_url:
        runs = _escape(hosting.runs_url)
        elsewhere = (
            f'<button type="button" class="ghost-btn" id="run-elsewhere" data-runs-url="{runs}" '
            'disabled title="Save the config in your workspace, then open the page that runs '
            'it on a GPU">Run on a GPU</button>')
        as_it_is_elsewhere = (
            f'<button type="button" class="ghost-btn" id="run-as-is-elsewhere" '
            f'data-runs-url="{runs}" disabled title="Open the page that runs this config on '
            'a GPU">Run it on a GPU</button>')
    html = html.replace("<!--__FASTMDX_RUN_ELSEWHERE__-->", elsewhere)
    html = html.replace("<!--__FASTMDX_RUN_AS_IS_ELSEWHERE__-->", as_it_is_elsewhere)
    person_spots = _re.compile("|".join(person_marks))

    def page_for(account_header: str | None) -> str:
        """The page with the person named at the foot of the sidebar: the
        name the proxy sent, with its initials, hosted; the product's name
        (this software's on a person's own machine) and no initials
        otherwise."""
        from fastmdxplora.gui.hosting import Hosting as _Hosting
        from fastmdxplora.gui.hosting import initials

        name = _Hosting.account_name(account_header) if hosting is not None else ""
        said = {"name": name or product or "FastMDXplora", "initials": initials(name)}
        page = person_spots.sub(lambda m: _escape(said[person_marks[m.group(0)]]), html)
        return page.replace(logo_spot, "" if said["initials"] else logo_mark)

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
                logger.debug("dashboard route %s failed", self.path, exc_info=True)
                self.send_error(500, "Dashboard internal error")

        def do_HEAD(self) -> None:  # noqa: N802 - stdlib API
            """A study's file's size and type without the file, which a
            download manager asks before it fetches in pieces. Only
            `/artifacts/`: every other route's answer is made as it is
            asked for, and making it to send none of it is waste."""
            if not urlparse(self.path).path.startswith("/artifacts/"):
                self.send_error(405, "Only a study's files answer HEAD")
                return
            self.do_GET()

        def do_POST(self) -> None:  # noqa: N802 - stdlib API
            try:
                self._dispatch_post()
            except ConnectionError:
                logger.debug("dashboard caller %s hung up mid-request", self.client_address)
            except Exception as exc:  # noqa: BLE001 - exploration errors stay local
                logger.warning("dashboard POST route %s failed: %s", self.path, exc)
                logger.debug("dashboard route %s failed", self.path, exc_info=True)
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
            if self._refused_as_not_through_the_proxy():
                return
            if self._refused_as_from_elsewhere(api=path.startswith("/api/")):
                return
            root = app_runtime.data_root()
            if not allow_control:
                if not _get_answered_beyond_loopback(path):
                    # Refused unless listed as open; see the list for why.
                    self._refuse_beyond_loopback()
                    return
                if not is_study(root):
                    # `--output` can name any folder, a home folder
                    # included. Beyond loopback one FastMDXplora did not
                    # write is served as no run at all.
                    root = app_runtime.workspace_root / _NO_CURRENT_RUN
            study_root = root
            if path in _READ_FROM_THE_RUN_SHOWN:
                from fastmdxplora.gui.runs_together import run_shown

                if path in ("/structure/frames.dcd", "/structure/frames-topology.pdb") \
                        and "run" in parse_qs(parsed.query):
                    self._send_run_frames(root, parse_qs(parsed.query)["run"][0],
                                          path.endswith(".pdb"))
                    return
                root = run_shown(root) or root
            if path in {"/", "/index", "/results", "/live"}:
                self._send_html(page_for(self.headers.get(ACCOUNT_HEADER)))
                return
            if path == "/api/stream":
                self._stream_changes()
                return
            if path == "/api/app-state" or path == "/api/explore/state":
                self._send_json(app_runtime.snapshot())
                return
            if path == "/api/agent/conversation":
                from fastmdxplora.gui.agent_panel import read_conversation

                self._send_json(read_conversation(app_runtime))
                return
            if path == "/api/agent/receipt":
                # What the AI model was sent for one reply, kept beside the
                # conversation; private as the conversations are.
                from fastmdxplora.gui.agent_panel import receipt_endpoint

                self._send_json(receipt_endpoint(
                    app_runtime, (parse_qs(parsed.query).get("sha256") or [""])[0]))
                return
            if path == "/api/file-text":
                # A run's own text file, for the Files tab's preview.
                # Confined to the run root, over the same list of types
                # the Agent's + accepts.
                from fastmdxplora.gui.agent_panel import read_text_file

                # The run being watched, as `/artifacts/` reads it. The
                # workspace was the fallback, and with no run open that is
                # wherever `fastmdx gui` was typed, often a home folder.
                wanted = (parse_qs(parsed.query).get("path") or [""])[0]
                target = root / wanted
                if not allow_control and not _served_beyond_loopback(root, target):
                    self._send_json({"ok": False,
                                     "error": "That file is outside this study."})
                    return
                answer = read_text_file(target, within=root)
                if answer.get("ok") and answer.get("suffix") in ("md", "markdown"):
                    from fastmdxplora.gui.report_page import render_markdown

                    answer["html"], answer["rendered"] = render_markdown(answer["text"])
                # A file's contents are shown as they are on disk.
                self._send_json(answer, verbatim=("text", "html"))
                return
            if path == "/api/agent/conversations":
                from fastmdxplora.gui.agent_panel import list_conversations

                self._send_json(list_conversations(app_runtime))
                return
            if path == "/api/browse":
                # Typing a path is a poor ask, and a browser cannot offer a
                # dialog for a folder the server will read: the one it has
                # uploads files to a page. So the walking happens here.
                from fastmdxplora.gui.browse import browse

                query = parse_qs(parsed.query)
                where = query.get("path", [""])[0]
                kind = query.get("kind", [""])[0]
                if hosting is None:
                    self._send_json(browse(where or None, kind or None))
                    return
                inside = self._inside_or_refuse(where)
                if inside is None:
                    return
                listing = browse(inside, kind or None)
                if listing.get("ok"):
                    # The workspace is the top: nothing above it to go to.
                    here = Path(listing["path"])
                    if here == hosting.workspace:
                        listing["parent"] = None
                    listing["home"] = str(hosting.workspace)
                self._send_json(listing)
                return
            if path in ("/api/studies", "/api/studies-compared", "/api/study-thumbnail"):
                # The studies in the workspace, two of them compared, and a
                # figure for a card: read from the studies' records, inside
                # the workspace when hosted, loopback otherwise, as browsing
                # folders is.
                from fastmdxplora.gui import workspace

                query = parse_qs(parsed.query)

                def one(key: str) -> str:
                    return (query.get(key) or [""])[0]

                if path == "/api/studies":
                    named = self._path_for(one("path") or str(app_runtime.exploration_root))
                    if named is None:
                        return
                    self._send_json(workspace.studies_in(named))
                    return
                if path == "/api/studies-compared":
                    first, second = self._path_for(one("a")), self._path_for(one("b"))
                    if first is None or second is None:
                        return
                    self._send_json(workspace.studies_compared(first, second))
                    return
                named = self._path_for(one("path"))
                if named is None:
                    return
                if one("series"):
                    # The numbers of the card's series, for the card to plot
                    # in the page's colours.
                    self._send_json(workspace.thumbnail_series(named) if is_study(Path(named))
                                    else {"ok": False, "reason": "not a study"})
                    return
                # `is_study` is the module's: imported here, it would be a
                # local of the whole handler, unbound on every other route.
                figure = workspace.thumbnail_of(named) if is_study(Path(named)) else None
                kind = "image/png"
                if figure is not None:
                    data = figure.read_bytes()
                else:
                    # No figure yet: a picture of the study's backbone.
                    from fastmdxplora.gui.backbone_picture import backbone_svg

                    picture = backbone_svg(named, one("scheme") or "light") \
                        if is_study(Path(named)) else None
                    if picture is None:
                        self.send_error(404, "Not found")
                        return
                    data, kind = picture.encode("utf-8"), "image/svg+xml"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                if kind == "image/svg+xml":
                    self.send_header("Content-Security-Policy", "default-src 'none'")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
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
                if hosting is not None:
                    inside = self._inside_or_refuse(target)
                    if inside is None:
                        return
                    target = str(inside)
                self._send_json(inspect_directory(target))
                return
            if path == "/api/schema":
                # Every setting the software accepts, described well enough
                # for the page to draw a control for it. The form used to be
                # written by hand and offered eleven of eighty-three.
                from fastmdxplora.config.loader import ConfigError
                from fastmdxplora.gui.agent_panel import your_defaults
                from fastmdxplora.gui.schema_payload import schema_payload

                # The workspace's defaults offered as the form's: a setting
                # left at one runs with it. A defaults file that is wrong is
                # said, and the form offers FastMDXplora's own meanwhile.
                try:
                    payload = schema_payload(your_defaults(app_runtime))
                except ConfigError as exc:
                    payload = schema_payload()
                    payload["defaults_refused"] = str(exc)
                # Where a results folder named rather than pathed will land.
                # The page can then say it instead of leaving somebody to
                # guess which directory "analysis_output" is relative to.
                payload["workspace"] = str(app_runtime.exploration_root)
                self._send_json(payload)
                return
            if path == "/api/status":
                # A run that ended without saying so is said to have.
                status = status_as_it_stands(root)
                metrics = read_metrics(root)
                from fastmdxplora.gui.simulated_time import simulated_times

                payload = {
                    "status": status,
                    # Production first, equilibration beside it: the live
                    # record's own time has every stage in it.
                    "times": simulated_times(root, status),
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
                # The whole run, thinned evenly, for the charts: the newest
                # 500 samples alone left a long run's charts its last
                # stretch, without its equilibration or most of production.
                # As many as the person's setting keeps (Chart history).
                from fastmdxplora.gui.overview_view import thinned_metrics

                try:
                    most = int((parse_qs(parsed.query).get("most") or [""])[0])
                except ValueError:
                    most = MOST_CHART_SAMPLES
                most = max(60, min(most, 5000))
                self._send_json({"metrics": thinned_metrics(
                    read_metrics(root, limit=None), most)})
                return
            if path == "/api/figure-data":
                # The numbers behind an analysis's other figures, to plot
                # them in the page's colours (figure_data.py).
                from fastmdxplora.gui.figure_data import figure_payload

                wanted = (parse_qs(parsed.query).get("figure") or [""])[0]
                self._send_json(figure_payload(root, wanted))
                return
            if path == "/api/overview":
                # What the Overview leads with (overview_view.py).
                from fastmdxplora.gui.overview_view import overview_payload

                self._send_json(overview_payload(root))
                return
            if path == "/api/events":
                self._send_json({"events": read_events(root)})
                return
            if path == "/api/report":
                from fastmdxplora.gui.report_page import report_payload

                self._send_json(report_payload(root))
                return
            if path == "/api/methods":
                from fastmdxplora.gui.report_page import methods_payload

                self._send_json(methods_payload(
                    root, may_read=(None if hosting is None
                                    else lambda path: hosting.inside(str(path)) is not None)))
                return
            if path == "/api/files-page":
                # The Files page, rendered (`gui/files_page.py`).
                view = (parse_qs(parsed.query).get("view") or ["phases"])[0]
                self._send_files_page(root, "folders" if view == "folders" else "phases")
                return
            if path == "/api/files/deposit":
                # What a data deposit of the study would carry, and the
                # README it would be written with (`fastmdxplora.deposit`).
                from fastmdxplora.deposit import deposit_plan

                query = parse_qs(parsed.query)
                sets = query.get("sets")
                self._send_json(deposit_plan(
                    root, _artifact_records(root),
                    figures=(query.get("figures") or ["svg"])[0],
                    sets=None if sets is None else [k for k in ",".join(sets).split(",") if k]),
                    verbatim=("readme",))
                return
            if path == "/api/files/sha256":
                # A file's SHA-256, for a record or a deposit: read once a
                # version of the file. Loopback, like every route not
                # listed: reading a 10 GB trajectory through is work.
                target = self._a_file_of_the_study(
                    root, (parse_qs(parsed.query).get("path") or [""])[0])
                if target is None:
                    return
                from fastmdxplora.deposit import sha256_of

                self._send_json({"ok": True, "path": target.relative_to(root.resolve()).as_posix(),
                                 "sha256": sha256_of(target)})
                return
            if path == "/api/files/zip":
                # Files of the study in one zip: a trajectory and its
                # topology, an analysis's folder. Loopback, like every route
                # not listed: it is work the server does, and a zip of a
                # trajectory is as large as the trajectory.
                query = parse_qs(parsed.query)
                self._send_zip(root, query.get("path") or [],
                               (query.get("name") or ["files"])[0])
                return
            if path == "/api/artifacts" or path == "/api/files":
                self._send_json({"artifacts": self._where_each_is(root, _artifact_records(root))})
                return
            if path == "/api/results" or path == "/api/analyses":
                payload = _results_payload(root)
                self._where_each_is(root, payload.get("artifacts") or [])
                self._send_json(payload)
                return
            if path == "/api/series":
                from fastmdxplora.gui.series import series_over_time, series_payload

                query = parse_qs(parsed.query)
                name = (query.get("analysis") or [""])[0]
                run = (query.get("run") or [""])[0]
                if not name and not run:
                    # Which series there are, for the Viewer's.
                    self._send_json(series_over_time(root))
                    return
                if run:
                    # One run of a study of several, named by its id: only
                    # a run the study records, never a path.
                    from fastmdxplora.gui.runs_compared import run_folder

                    folder = run_folder(root, run)
                    self._send_json(series_payload(folder, name) if folder is not None
                                    else {"ok": False, "reason": "no such run in this study"})
                    return
                self._send_json(series_payload(root, name))
                return
            if path == "/api/analysis-overview":
                # What every analysis recorded, read together.
                from fastmdxplora.gui.analysis_overview import overview_of

                self._send_json(overview_of(root))
                return
            if path == "/api/convergence":
                from fastmdxplora.gui.analysis_overview import convergence_payload

                name = (parse_qs(parsed.query).get("analysis") or [""])[0]
                self._send_json(convergence_payload(root, name))
                return
            if path == "/api/selection":
                from fastmdxplora.gui.selection import selection_for

                query = parse_qs(parsed.query)

                def one(key: str) -> str:
                    return (query.get(key) or [""])[0]

                self._send_json(selection_for(root, chain=one("chain"), resseq=one("resseq"),
                                              resname=one("resname"), atom=one("atom"),
                                              frames_atom=one("frames_atom") or None))
                return
            if path == "/api/residue-states":
                # A clicked residue's states, for a new study of the same
                # structure with that residue set.
                from fastmdxplora.gui.selection import states_for

                query = parse_qs(parsed.query)
                self._send_json(states_for(root, chain=(query.get("chain") or [""])[0],
                                           resseq=(query.get("resseq") or [""])[0],
                                           resname=(query.get("resname") or [""])[0]))
                return
            if path == "/api/measure-over-frames":
                from fastmdxplora.gui.measure import over_frames

                query = parse_qs(parsed.query)
                self._send_json(over_frames(root, (query.get("a") or [""])[0],
                                            (query.get("b") or [""])[0]))
                return
            if path == "/api/residue-values":
                # The per-residue results the viewer colours the structure by.
                from fastmdxplora.gui.by_residue import values_by_residue

                self._send_json(values_by_residue(root))
                return
            if path == "/api/secondary-structure":
                # DSSP for the structure, live frame or frames the viewer was
                # sent, so its cartoon is the study's assignment.
                from fastmdxplora.gui.by_residue import secondary_structure

                of = (parse_qs(parsed.query).get("of") or ["structure"])[0]
                self._send_json(secondary_structure(root, of))
                return
            if path == "/api/stopping":
                from fastmdxplora.gui.stopping_view import stopping_payload

                self._send_json(stopping_payload(root))
                return
            if path == "/api/again":
                # What can be run again on the study on screen, and why not,
                # with the analyses it ran last and those this release has.
                # Not answered beyond loopback, like the POST it offers.
                from fastmdxplora.gui.again_view import again_payload

                self._send_json(again_payload(root))
                return
            if path == "/api/fixes":
                # What would fix the study on screen, its command and its
                # price. Not answered beyond loopback: it names the
                # commands the POST below runs.
                from fastmdxplora.gui.fixes_view import fixes_payload

                self._send_json(fixes_payload(root))
                return
            if path == "/api/runs-compared":
                from fastmdxplora.gui.runs_compared import runs_compared

                self._send_json(runs_compared(root))
                return
            if path == "/api/protein-preview":
                # Beyond loopback a viewer reads what the run has; it does
                # not set this machine redoing work on request.
                regenerate = allow_control and parsed.query == "regenerate=1"
                self._send_json(protein_preview_payload(root, regenerate=regenerate))
                return
            if path == "/api/structure-info":
                self._send_json(_structure_info_payload(root, cfg))
                return
            if path == "/api/ligands":
                self._send_json(_ligands_payload(root, cfg))
                return
            if path == "/api/live-frame-index":
                self._send_json(_live_frame_index(root))
                return
            if path == "/api/live-coordinates":
                self._send_json(_live_coordinates_payload(root))
                return
            if path == "/api/frames-info":
                # The trajectory as binary frames: a topology and a DCD.
                from fastmdxplora.gui.runs_together import runs_together
                from fastmdxplora.gui.trajectory_frames import frames_info

                together = runs_together(study_root, most_frames=cfg.max_browser_frames,
                                         force=allow_control and "force=1" in parsed.query)
                if together is not None:
                    self._send_json(together)
                    return
                sim_manifest = _load_json(root / "simulation" / "simulation_parameters.json")
                self._send_json(frames_info(
                    root, most_frames=cfg.max_browser_frames,
                    simulation_time_ns_total=sim_manifest.get("duration_ns_actual"),
                    force=allow_control and "force=1" in parsed.query))
                return
            if path == "/api/open-output":
                if hosting is not None:
                    # A file manager would open on the server, not in front
                    # of the person; the Files tab is how they reach it.
                    self._send_json({"opened": False, "path": str(root),
                                     "detail": "Not available in a hosted GUI."})
                    return
                if _no_study(root):
                    self._send_json({"opened": False, "path": "",
                                     "detail": "No study is open."})
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
            if path == "/structure/live-frame.dcd":
                self._send_live_coordinates(root)
                return
            if path in ("/structure/frames.dcd", "/structure/frames-topology.pdb"):
                self._send_frames(root, path.rsplit("/", 1)[1], parse_qs(parsed.query))
                return
            if path == "/api/frames-pieces":
                from fastmdxplora.gui.trajectory_frames import frames_pieces

                self._send_json(frames_pieces(root))
                return
            if path == "/structure/frames-piece.xtc":
                from fastmdxplora.gui.trajectory_frames import PIECES

                k = (parse_qs(parsed.query).get("k") or [""])[0]
                target = root / "simulation" / PIECES / f"piece_{k}.xtc"
                if not k.isdigit() or not target.is_file():
                    self.send_error(404, "No such piece of the frames")
                    return
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/views":
                from fastmdxplora.gui.saved_views import views_of

                self._send_json(views_of(root))
                return
            if path == "/api/viewer-atoms":
                # The atoms a typed selection names in what the Viewer renders.
                from fastmdxplora.gui.viewer_selections import atoms_selected

                query = parse_qs(parsed.query)
                one = lambda key: (query.get(key) or [""])[0]  # noqa: E731
                pdb, key = _viewer_structure(root, one("of"),
                                             with_solvent=one("solvent").lower() in {"1", "true"})
                self._send_json(atoms_selected(pdb, one("expression"), key=key))
                return
            if path == "/api/viewer-selections":
                from fastmdxplora.gui.viewer_selections import selections_of

                self._send_json(selections_of(root))
                return
            if path == "/api/scenes":
                from fastmdxplora.scenes import scenes_of

                self._send_json(scenes_of(root))
                return
            if path == "/api/movies":
                # What a movie is made with on this computer, and the movies
                # made. Not answered beyond loopback: it names this
                # computer's ffmpeg, and making one writes the study.
                from fastmdxplora.movies import encoding, movies_of

                self._send_json({**encoding(), "movies": movies_of(root)["movies"]})
                return
            if path.startswith("/scenes/"):
                # One file of a scene written with the study, so a viewer
                # given the scene's address finds the files it names.
                self._send_scene_member(root, path.removeprefix("/scenes/"))
                return
            if path == "/api/chain-contacts":
                from fastmdxplora.gui.chain_contacts import chain_contacts

                self._send_json(chain_contacts(root))
                return
            if path == "/api/interactions-over-frames":
                from fastmdxplora.gui.interactions_over_frames import interactions_over_frames

                self._send_json(interactions_over_frames(root))
                return
            if path == "/api/frames-superposed":
                self._send_json(_frames_superposed_payload(root, parse_qs(parsed.query)))
                return
            if path == "/api/occupancy":
                self._send_json(_occupancy_payload(root, parse_qs(parsed.query)))
                return
            if path == "/structure/occupancy.dx":
                self._send_occupancy(root, parse_qs(parsed.query))
                return
            if path == "/api/motion":
                from fastmdxplora.gui.motion import motion

                query = parse_qs(parsed.query)
                self._send_json(motion(root, (query.get("mode") or ["1"])[0],
                                       (query.get("scale") or ["1"])[0]))
                return
            if path == "/api/beside":
                # Another study's frames beside this one's. Not answered
                # beyond loopback: it reads a study the request names.
                from fastmdxplora.gui.beside import beside

                named = self._path_for((parse_qs(parsed.query).get("path") or [""])[0])
                if named is None:
                    return
                if not named:
                    self._send_json({"ok": False, "reason": "No study was named."})
                    return
                self._send_json(beside(root, named, most_frames=cfg.max_browser_frames))
                return
            if path in ("/structure/beside.pdb", "/structure/beside.dcd"):
                from fastmdxplora.gui.beside import BESIDE, beside_file

                name = beside_file((parse_qs(parsed.query).get("key") or [""])[0],
                                   path[-4:])
                target = root / BESIDE / name if name else None
                if target is None or not target.is_file():
                    self.send_error(404, "No such study beside this one")
                    return
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream"
                                 if path.endswith(".dcd") else "chemical/x-pdb; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path in ("/api/pocket-volume", "/structure/pocket.dx"):
                from fastmdxplora.gui.pocket_volume import pocket_points, pocket_volume

                asked = parse_qs(parsed.query)
                one = lambda key: (asked.get(key) or [None])[0]  # noqa: E731
                if path == "/api/pocket-volume":
                    self._send_json(pocket_volume(root, one("ligand"), one("cutoff") or 5.0))
                    return
                text, reason = pocket_points(root, one("ligand"), one("cutoff") or 5.0,
                                             one("frame"))
                if text is None:
                    self.send_error(404, reason)
                    return
                data = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path in ("/api/contact-map", "/api/contact-pair"):
                from fastmdxplora.gui.contact_map import contact_map, contact_pair

                asked = parse_qs(parsed.query)
                one = lambda key: (asked.get(key) or [None])[0]  # noqa: E731
                self._send_json(contact_pair(root, one("a"), one("b"))
                                if path == "/api/contact-pair"
                                else contact_map(root, one("first"), one("second"),
                                                 one("method")))
                return
            if path == "/api/backbone-angles":
                from fastmdxplora.gui.backbone_angles import backbone_angles

                self._send_json(backbone_angles(root))
                return
            if path == "/api/states":
                from fastmdxplora.gui.states import states_of

                self._send_json(states_of(root, (parse_qs(parsed.query).get("method")
                                                 or [None])[0]))
                return
            if path == "/api/state-difference":
                self._send_json(_state_difference_payload(root, parse_qs(parsed.query)))
                return
            if path == "/api/water-sites":
                from fastmdxplora.gui.occupancy import water_sites_placed

                self._send_json(water_sites_placed(root))
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
            if self._refused_as_not_through_the_proxy(posting=True):
                return
            if self._refused_as_from_elsewhere(api=True, posting=True):
                return
            if not allow_control and path not in POSTS_ANSWERED_BEYOND_LOOPBACK:
                # Refused unless listed as open; see the list for why.
                self._refuse_beyond_loopback()
                return
            if path.startswith("/api/movies/") and path.endswith("/frame"):
                # A frame of a movie, a PNG as the body rather than JSON.
                self._add_movie_frame(path.removeprefix("/api/movies/").removesuffix("/frame"))
                return
            payload = self._read_json_body()
            if path == "/api/files/reveal":
                # A file of the study shown in the file manager, selected:
                # on the person's own computer only, where a window opening
                # is in front of them.
                if hosting is not None:
                    self._send_json({"ok": False, "error": "Not available in a hosted GUI."})
                    return
                target = self._a_file_of_the_study(app_runtime.data_root(),
                                                   str((payload or {}).get("path") or ""))
                if target is None:
                    return
                shown, detail = _reveal_local_path(target)
                self._send_json({"ok": shown, "error": "" if shown else detail})
                return
            if path == "/api/files/deposit":
                # The deposit written into the study's deposit/ folder: it
                # writes on this machine, so loopback, as every route not
                # listed, or a hosted GUI's own study.
                from fastmdxplora.deposit import write_deposit

                root = app_runtime.data_root()
                if _no_study(root):
                    self._send_json({"ok": False, "error": "No study is open."})
                    return
                if app_runtime.snapshot().get("process_running"):
                    self._send_json({"ok": False, "error": "The study is running: a deposit of "
                                     "it now would carry what is still being written."})
                    return
                asked = payload if isinstance(payload, dict) else {}
                said = write_deposit(root, _artifact_records(root),
                                     sets=[str(k) for k in asked.get("sets") or []],
                                     figures=str(asked.get("figures") or "svg"))
                if said.get("ok"):
                    said["href"] = f"/artifacts/{quote(said['path'])}?download=1"
                self._send_json(said)
                return
            if path == "/api/files/clear-scratch":
                # The Viewer's and the live view's scratch removed, after the
                # page has asked (`dry` says what would go). On the person's
                # own computer only: it deletes files.
                if hosting is not None:
                    self._send_json({"ok": False, "error": "Not available in a hosted GUI."})
                    return
                from fastmdxplora.gui.files_page import clear_scratch

                root = app_runtime.data_root()
                if _no_study(root):
                    self._send_json({"ok": False, "error": "No study is open."})
                    return
                self._send_json(clear_scratch(
                    root, _artifact_records(root),
                    running=bool(app_runtime.snapshot().get("process_running")),
                    dry=bool((payload or {}).get("dry"))))
                return
            if path == "/api/study-tags":
                # A study's tags and note, as the person set them on its card,
                # kept in its folder beside its records (study_tags.py).
                from fastmdxplora.study_tags import set_tags

                named = self._path_for(str(payload.get("path") or ""))
                if named is None:
                    return
                if not is_study(Path(named)):
                    self._send_json({"ok": False, "reason": f"{named} is not a study."})
                    return
                self._send_json(set_tags(named, payload.get("tags"), payload.get("note")))
                return
            if path == "/api/movies":
                # A movie of the study's frames started: ffmpeg on this
                # computer encodes the frames the Viewer sends (movies.py).
                from fastmdxplora.movies import start_movie

                study = app_runtime.data_root()
                if not is_study(study):
                    self._send_json({"ok": False, "reason": "No study is open to save it in."})
                    return
                self._send_json(start_movie(study, payload.get("name"), fps=payload.get("fps"),
                                            width=payload.get("width"),
                                            height=payload.get("height"),
                                            about=str(payload.get("about") or "")))
                return
            if path.startswith("/api/movies/"):
                from fastmdxplora.movies import cancel_movie, finish_movie

                key, _, action = path.removeprefix("/api/movies/").partition("/")
                if action == "finish":
                    self._send_json(finish_movie(key))
                elif action == "cancel":
                    self._send_json(cancel_movie(key))
                else:
                    self.send_error(404, "Not found")
                return
            if path == "/api/scenes":
                # A view of the Viewer written with the study as a scene.
                from fastmdxplora.gui.viewer_selections import selections_of
                from fastmdxplora.scenes import write_scene

                study = app_runtime.data_root()
                if not is_study(study):
                    self._send_json({"ok": False, "reason": "No study is open to save it in."})
                    return
                ligands = payload.get("ligands")
                selections = (selections_of(study)["selections"]
                              if payload.get("selections", True) else [])
                highlight = payload.get("highlight")
                if isinstance(highlight, str) and highlight.strip():
                    # Atoms to show, as a scene the Agent proposed names them.
                    from fastmdxplora.scenes import highlighted

                    selections = [*selections, highlighted(highlight.strip()[:500],
                                                           payload.get("labels") is True)]
                # A study of several runs: the run the Viewer plays, alone.
                from fastmdxplora.gui.runs_together import run_shown

                played = run_shown(study)
                said = write_scene(
                    study, payload.get("name"), payload.get("view"),
                    selections=selections or None,
                    ligands=[str(n) for n in ligands][:20] if isinstance(ligands, list)
                    else None, source=played)
                if played is not None and said.get("ok"):
                    said["notes"] = [*said.get("notes", []),
                                     f"The scene is of the run played ({played.name}) alone: "
                                     "the other runs are not part of it."]
                self._send_json(said)
                return
            if path == "/api/viewer-selections":
                # A selection of the Viewer named in the study, or forgotten.
                from fastmdxplora.gui.viewer_selections import delete_selection, save_selection

                study = app_runtime.data_root()
                if not is_study(study):
                    self._send_json({"ok": False, "reason": "No study is open to save it in."})
                    return
                if payload.get("action") == "delete":
                    self._send_json(delete_selection(study, payload.get("name")))
                else:
                    self._send_json(save_selection(study, payload.get("name"),
                                                   payload.get("selection")))
                return
            if path == "/api/views":
                # A view of the Viewer saved with the study, or forgotten.
                from fastmdxplora.gui.saved_views import delete_view, save_view

                study = app_runtime.data_root()
                if not is_study(study):
                    self._send_json({"ok": False, "reason": "No study is open to save it in."})
                    return
                if payload.get("action") == "delete":
                    self._send_json(delete_view(study, payload.get("name")))
                else:
                    self._send_json(save_view(study, payload.get("name"), payload.get("view")))
                return
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
                # Reading and setting which AI model to ask. The key is
                # accepted here and stored by `save_choice`, which puts it
                # in a file of its own; it is never echoed back, never put
                # in a config, and never logged.
                from fastmdxplora.gui.agent_panel import model_endpoint

                self._send_json(model_endpoint(payload or {}))
                return
            if path == "/api/agent/run":
                # Starting a study, so it needs the machine's trust -- which
                # it has only on loopback, like every route not listed open.
                from fastmdxplora.gui.agent_panel import run_endpoint

                self._send_json(run_endpoint(
                    payload or {}, app_runtime,
                    dashboard_url=self.headers.get("Origin")))
                return
            if path == "/api/agent/run-summary":
                # What a run the Agent started found, from its records.
                from fastmdxplora.gui.agent_panel import run_summary_endpoint

                self._send_json(run_summary_endpoint(
                    payload or {}, app_runtime,
                    path_for=hosting.inside if hosting is not None else None))
                return
            if path == "/api/agent/propose":
                # A sentence in, a config out -- through the same
                # `propose_config` the CLI uses and the same validator a
                # hand-written config goes through. Nothing here decides
                # whether a config is acceptable.
                from fastmdxplora.gui.agent_panel import propose_endpoint

                self._send_json(propose_endpoint(
                    payload or {}, app_runtime,
                    path_for=hosting.inside if hosting is not None else None))
                return
            if path == "/api/agent/propose-stream":
                # The same, sent on as the AI model writes it, and stopped when
                # the page stops reading.
                self._stream_proposal(payload or {})
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
                    named = self._path_for(request.get("path"))
                    if named is None:
                        return
                    self._send_json(load_config_into_state(named))
                return
            if path == "/api/run-config":
                # Running a config exactly as it stands, which is a different
                # act from running what the form currently describes.
                request = payload or {}
                named = self._path_for(request.get("path"))
                if named is None:
                    return
                self._send_json(
                    app_runtime.launch_existing_config(
                        named,
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

                named = self._path_for((payload or {}).get("path"))
                if named is None:
                    return
                self._send_json(check_config_file(named))
                return
            if path == "/api/preview-system":
                # What the form's study will build and cost, while the form
                # is still open: the box, the particle count, the time on
                # this machine and what is worth knowing about the structure.
                from fastmdxplora.gui.preview import system_preview

                self._send_json(system_preview(
                    payload or {},
                    path_for=hosting.inside if hosting is not None else None))
                return
            if path == "/api/structure-facts":
                # What a structure in the form's list of systems holds, for
                # its row: read, never prepared. The same path rule as the
                # preview, so a hosted form reads only its workspace.
                from fastmdxplora.gui.preview import structure_facts

                self._send_json(structure_facts(
                    (payload or {}).get("system"),
                    path_for=hosting.inside if hosting is not None else None))
                return
            if path == "/api/save-config":
                # Hosted, for the service's page that runs a study on its
                # own compute (--runs-url): the builder's config saved in
                # the workspace, for that page to open with. Offered only
                # when the service said where that page is.
                if hosting is None or not hosting.runs_url:
                    self._send_json({"ok": False, "error": "Not offered here."}, status=404)
                    return
                self._send_json(app_runtime.save_config_for_elsewhere(payload or {}))
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
            if path == "/api/fix":
                # Running a fix starts work on this machine, so it needs the
                # machine's trust, which it has only on loopback, like every
                # route not listed open. The page asks the person first.
                asked = payload or {}
                if "windows" in asked:
                    # Windows the person named, at the values they named.
                    self._send_json(app_runtime.run_windows_again(
                        asked.get("windows"), force_constant=asked.get("force_constant"),
                        duration_ns=asked.get("duration_ns"),
                        dashboard_url=self.headers.get("Origin")))
                    return
                self._send_json(app_runtime.run_a_fix(
                    asked.get("index"), dashboard_url=self.headers.get("Origin")))
                return
            if path == "/api/again":
                # A study's analysis or report run again in its folder
                # (`fastmdxplora.again`). It starts work on this machine, so
                # loopback only, like every route not listed open; the page
                # asks the person first.
                asked = payload if isinstance(payload, dict) else {}
                self._send_json(app_runtime.run_again(
                    asked.get("phases") or [], asked.get("analyses"),
                    dashboard_url=self.headers.get("Origin")))
                return
            if path == "/api/report/write":
                # The report phase alone on the study open, from its records.
                # It starts work on this machine, so it needs the machine's
                # trust, which it has only on loopback, like every route not
                # listed open. The page asks the person first.
                self._send_json(app_runtime.write_the_report_again(
                    dashboard_url=self.headers.get("Origin")))
                return
            if path == "/api/agent/conversation":
                from fastmdxplora.gui.agent_panel import write_conversation

                asked = payload or {}
                # Named, the conversation is written where it lives, a chat
                # of no study (study null) included; else where the GUI is.
                if "seen" in asked:
                    # The page's save, merged into what is kept: its entries,
                    # the ones it has held (`merge_conversation`).
                    from fastmdxplora.gui.agent_panel import _HERE, merge_conversation

                    study = _HERE
                    if (asked.get("id") or asked.get("key")) and "study" in asked:
                        study = self._optional_path_for(asked.get("study"))
                        if study is False:
                            return
                    self._send_json(merge_conversation(
                        app_runtime, asked.get("entries"), asked.get("seen"),
                        asked.get("id") or None, study, key=asked.get("key"),
                        current=asked.get("current") is not False,
                        append=asked.get("append") is True,
                        dropped=asked.get("cut") if isinstance(asked.get("cut"), list) else None,
                        keep=asked.get("keep") if isinstance(asked.get("keep"), str) else None,
                        keep_current=asked.get("keep_current") is True))
                    return
                if asked.get("id") and "study" in asked:
                    study = self._optional_path_for(asked.get("study"))
                    if study is False:
                        return
                    self._send_json(write_conversation(
                        app_runtime, asked.get("entries"), asked.get("id"), study))
                    return
                self._send_json(write_conversation(app_runtime, asked.get("entries")))
                return
            if path == "/api/agent/conversation/clear":
                from fastmdxplora.gui.agent_panel import clear_conversation

                self._send_json(clear_conversation(app_runtime))
                return
            if path == "/api/agent/conversation/new":
                from fastmdxplora.gui.agent_panel import new_conversation

                asked = payload or {}
                if "study" in asked:
                    study = self._optional_path_for(asked.get("study"))
                    if study is False:
                        return
                    self._send_json(new_conversation(app_runtime, study,
                                                     key=asked.get("key")))
                    return
                self._send_json(new_conversation(app_runtime, key=asked.get("key")))
                return
            if path == "/api/agent/conversation/rename":
                from fastmdxplora.gui.agent_panel import rename_conversation

                study = self._optional_path_for((payload or {}).get("study"))
                if study is False:
                    return
                self._send_json(rename_conversation(app_runtime, (payload or {}).get("id"),
                                                    study, (payload or {}).get("title")))
                return
            if path == "/api/agent/conversation/open":
                from fastmdxplora.gui.agent_panel import open_conversation

                study = self._optional_path_for((payload or {}).get("study"))
                if study is False:
                    return
                self._send_json(open_conversation(app_runtime,
                                                  (payload or {}).get("id"),
                                                  study))
                return
            if path == "/api/agent/attachment":
                from fastmdxplora.gui.agent_panel import read_attachment

                named = self._path_for((payload or {}).get("path"))
                if named is None:
                    return
                self._send_json(read_attachment(named), verbatim=("text",))
                return
            if path == "/api/agent/conversation/attach":
                from fastmdxplora.gui.agent_panel import attach_conversation

                study = self._optional_path_for((payload or {}).get("study"))
                from_study = self._optional_path_for((payload or {}).get("from_study"))
                if study is False or from_study is False:
                    return
                self._send_json(attach_conversation(app_runtime, study,
                                                    (payload or {}).get("id"),
                                                    from_study))
                return
            if path == "/api/agent/conversation/delete":
                from fastmdxplora.gui.agent_panel import delete_conversation

                study = self._optional_path_for((payload or {}).get("study"))
                if study is False:
                    return
                self._send_json(delete_conversation(app_runtime,
                                                    (payload or {}).get("id"),
                                                    study, key=(payload or {}).get("key")))
                return
            if path == "/api/demo":
                # The demo study, copied into the workspace and opened
                # (fastmdxplora.demo); loopback only, as switching is.
                self._send_json(app_runtime.open_the_demo())
                return
            if path == "/api/open-shared":
                # A shared study, from its DOI, its Zenodo address or a zip
                # on this computer, checked and opened (fastmdxplora.sharing);
                # loopback only, as switching is.
                source = str((payload or {}).get("source") or "").strip()
                if not source:
                    self._send_json({"ok": False, "error": "Give a DOI, a Zenodo address "
                                     "or the path of a zip."}, status=400)
                    return
                self._send_json(app_runtime.open_shared_study(source))
                return
            if path == "/api/explore/switch":
                folder = str((payload or {}).get("folder") or "").strip()
                if not folder:
                    self._send_json({"ok": False, "error": "No folder given."},
                                    status=400)
                    return
                named = self._path_for(folder)
                if named is None:
                    return
                self._send_json(app_runtime.switch_to(named))
                return
            self.send_error(404, "Not found")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _refused_as_from_elsewhere(self, *, api: bool, posting: bool = False) -> bool:
            """Refuse what a page on another site asked a browser to send.

            A browser sends a request to 127.0.0.1 for any page it has open,
            so on loopback "only this machine" meant "any website visited
            while the GUI runs": a form posted as text/plain switched the
            served folder, stored an AI model and key, or started a run, and a
            name that resolves to 127.0.0.1 (DNS rebinding) let a page read
            the answers too. On loopback the server answers only to a
            loopback name, and anywhere it refuses a POST whose Origin is
            not its own, and an API request a browser marks as cross-site.
            A script or `curl` sends no Origin and is answered as before.
            """
            host = self.headers.get("Host") or ""
            if hosting is not None and not hosting.answers_to(host):
                why = "This server does not answer to that name."
            elif hosting is None and allow_control and host and not _names_this_machine(host):
                why = "This server answers only to localhost."
            else:
                origin = self.headers.get("Origin")
                fetched_from = (self.headers.get("Sec-Fetch-Site") or "").lower()
                crossing = fetched_from in {"cross-site", "same-site"}
                if posting and origin is not None:
                    own = (hosting.own_origin(origin) if hosting is not None
                           else origin.lower() == f"http://{host.lower()}")
                    crossing = crossing or not own
                if not (crossing and (api or posting)):
                    return False
                why = "A request from another site is refused."
            if posting:
                self._drain_request_body()
            self._send_json({"ok": False, "error": why}, status=403)
            return True

        def _refused_as_not_through_the_proxy(self, *, posting: bool = False) -> bool:
            """Hosted, refuse whatever did not come through the proxy.

            The proxy signs the person in and adds the secret; a caller who
            reaches the port some other way has neither. Refused before the
            request is looked at, with nothing that says what is served.
            """
            if hosting is None or hosting.admits(self.headers.get(SECRET_HEADER)):
                return False
            if posting:
                self._drain_request_body()
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True

        def _inside_or_refuse(self, given: Any) -> Path | None:
            """Hosted, the path a request named, inside the workspace; or
            None, after refusing it."""
            assert hosting is not None
            inside = hosting.inside(given)
            if inside is None:
                self._send_json({"ok": False,
                                 "error": "That is outside your workspace."},
                                status=403)
            return inside

        def _path_for(self, given: Any) -> str | None:
            """A path a request named, as the route reads it: as given on a
            person's own machine; hosted, inside the workspace or refused."""
            if hosting is None:
                return str(given or "")
            inside = self._inside_or_refuse(given)
            return None if inside is None else str(inside)

        def _optional_path_for(self, given: Any) -> Any:
            """As `_path_for`, for a path that may be absent (None stays
            None). False means it was refused and answered."""
            if hosting is None or not given:
                return given
            named = self._path_for(given)
            return False if named is None else named

        # ---- Generic response helpers ----
        def _refuse_beyond_loopback(self) -> None:
            # The body is drained before the refusal, not after: an unread
            # body turns the close into an RST and the caller loses the 403
            # it explains itself with. See `_drain_request_body`.
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

        def _add_movie_frame(self, key: str) -> None:
            from fastmdxplora.movies import MOST_A_FRAME_MAY_BE, add_frame

            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                length = -1
            if not 0 < length <= MOST_A_FRAME_MAY_BE:
                # Not read: the answer is sent and the connection closed,
                # with whatever body there is left on it.
                self.close_connection = True
                self._body_was_read = True
                self._send_json({"ok": False, "reason": "A frame is a PNG of at most "
                                 f"{MOST_A_FRAME_MAY_BE // 1_000_000} MB."}, status=413
                                if length > MOST_A_FRAME_MAY_BE else 400)
                return
            self._body_was_read = True
            data = self.rfile.read(length)
            self._send_json(add_frame(key, data))

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

        def _send_json(self, payload: dict[str, Any], *, status: int = 200,
                       verbatim: tuple[str, ...] = ()) -> None:
            if hosting is not None:
                # Every answer, so a route added later cannot show a path
                # on the server by forgetting to hide it. The fields named
                # in `verbatim` are a file's own contents, shown as on disk.
                kept = {key: payload[key] for key in verbatim if key in payload}
                payload = {**hosting.scrub(payload), **kept}
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream_changes(self) -> None:
            """`GET /api/stream`: one event each time the study the page
            shows changes (`fastmdxplora.gui.stream`)."""
            from fastmdxplora.gui.stream import changes, signature

            def look() -> tuple:
                state = app_runtime.snapshot()
                # With no study open the data root is the workspace, often
                # a home folder: nothing there for the page, and not a
                # place to walk twice a second.
                shown = app_runtime.data_root() if state.get("active_run") else None
                if shown is not None and not allow_control and not is_study(shown):
                    shown = None
                return signature(shown, state)

            def write(chunk: bytes) -> None:
                self.wfile.write(chunk)
                self.wfile.flush()

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            # A proxy that buffers would hold every event until the stream
            # ended, which is what the stream exists to avoid.
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            changes(write, look, closing=lambda: bool(getattr(self.server, "closing", False)))
            self.close_connection = True

        def _stream_proposal(self, payload: dict[str, Any]) -> None:
            """`POST /api/agent/propose-stream`: the Agent's reply as it is
            written, one JSON event a line, ending with the answer
            `/api/agent/propose` gives. The page stopping the request is
            the reply stopping: the next write fails, and the AI model's
            request is closed with it."""
            from fastmdxplora.gui.agent_panel import propose_endpoint

            def emit(event: dict[str, Any]) -> None:
                self.wfile.write((json.dumps(event, default=str) + "\n").encode("utf-8"))
                self.wfile.flush()

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            try:
                answer = propose_endpoint(
                    payload, app_runtime,
                    path_for=hosting.inside if hosting is not None else None, emit=emit)
                emit({"type": "done", "answer": answer})
            except (BrokenPipeError, ConnectionResetError):
                logger.debug("the page stopped reading the Agent's reply")
            except Exception as exc:  # noqa: BLE001 - said in the stream, which has begun
                from fastmdxplora.refusals import refusal_of

                found = refusal_of(exc)
                try:
                    emit({"type": "done", "answer": {"ok": False, "error": found.message,
                                                     "code": found.code}})
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _send_html(self, html_text: str) -> None:
            body = html_text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_scene_member(self, root: Path, rest: str) -> None:
            from fastmdxplora.scenes import SCENES_DIR, read_scene

            name, _, member = unquote(rest).partition("/")
            archive = root / SCENES_DIR / f"{name}.mvsx"
            data = read_scene(root, name, "index.mvsj" if member == "view"
                              else member or "index.mvsj")
            if data is None or (not allow_control
                                and not _served_beyond_loopback(root, archive)):
                self.send_error(404, "Scene not found")
                return
            if member == "view":
                from html import escape

                # The scene on a page of its own, as Mol* shows it.
                page = (Path(__file__).with_name("templates") / "scene.html").read_text(
                    encoding="utf-8")
                self._send_html(page
                                .replace("__SCENE_TITLE__", escape(name))
                                .replace("__SCENE_NAME__", escape(name, quote=True))
                                .replace("__SCENE_ARCHIVE__", escape(
                                    f"/artifacts/{SCENES_DIR}/{quote(name)}.mvsx?download=1",
                                    quote=True)))
                return
            content_type = ("application/json" if member.endswith((".mvsj", ".json"))
                            else "chemical/x-pdb" if member.endswith(".pdb")
                            else "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _files_links(self) -> Any:
            from fastmdxplora.gui.files_page import Links, reveal_word

            own = allow_control and hosting is None
            return Links(can={"zip": allow_control, "reveal": own, "sha": allow_control,
                              "clear": own, "deposit": allow_control},
                         reveal_word=reveal_word())

        def _send_files_page(self, root: Path, view: str) -> None:
            from fastmdxplora.gui.files_page import files_model, render

            links = self._files_links()
            records = self._where_each_is(root, _artifact_records(root))
            model = files_model(root, records, values=_determined_values(root, records))
            payload = {"ok": True, "html": render(model, links, view=view), "view": view,
                       "can": links.can, "reveal_word": links.reveal_word,
                       "runs": model["runs"], "files": len(model["files"])}
            body = json.dumps(payload).encode("utf-8")
            if hosting is not None:
                self._send_json(payload)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            if "gzip" in (self.headers.get("Accept-Encoding") or "") and len(body) > 16384:
                import gzip

                # 435 files are about 700 KB of rows, and 30 KB compressed:
                # the difference, on every change, over an SSH tunnel.
                body = gzip.compress(body, compresslevel=5, mtime=0)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _a_file_of_the_study(self, root: Path, rel: str) -> Path | None:
            """A file of the study a request names by its path in it, or
            None with the refusal sent: never one outside it, a hidden one
            or the Agent's conversations. The path as the request gave it,
            decoded once."""
            try:
                target = (root / rel).resolve()
                inside = target.relative_to(root.resolve())
            except (OSError, ValueError):
                self._send_json({"ok": False, "error": "That file is outside this study."},
                                status=403)
                return None
            if not rel or not target.is_file() or _private(inside.parts):
                self._send_json({"ok": False, "error": "That is not a file of this study."},
                                status=404)
                return None
            return target

        def _send_zip(self, root: Path, paths: list[str], name: str) -> None:
            """Several of the study's files in one zip, written as it is sent:
            no copy of a trajectory is made on the disk or in memory."""
            from fastmdxplora.gui.files_page import zip_entries

            found = []
            # The paths as `parse_qs` decoded them, once: decoded again, a
            # file called `a%41.dat` was asked for as `aA.dat`. The
            # refusals name no path: one carrying a line break would have
            # been a header of the answer.
            for rel in dict.fromkeys(paths):
                try:
                    target = (root / rel).resolve()
                    inside = target.relative_to(root.resolve())
                except (OSError, ValueError):
                    self.send_error(403, "A file named is outside the study")
                    return
                if not target.is_file() or _private(inside.parts):
                    self.send_error(404, "A file named is not a file of this study")
                    return
                found.append((target, inside.as_posix()))
            if not found:
                self.send_error(404, "No file was named")
                return
            # Each file opened before the answer starts: one that cannot be
            # read is refused, not found out halfway through the zip.
            for target, _ in found:
                try:
                    with target.open("rb"):
                        pass
                except OSError:
                    self.send_error(404, "A file named cannot be read")
                    return
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:80] or "files"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Disposition", attachment_named(f"{safe}.zip"))
            self.end_headers()
            # HTTP/1.0: the end of the zip is the end of the connection.
            self.close_connection = True
            try:
                zip_entries(self.wfile, found)
            except ConnectionError:
                raise
            except Exception:  # noqa: BLE001 - said to the browser as a failed download
                # A file gone or failing halfway. The headers are sent, so
                # no error can be: the connection is reset instead of
                # closed, which a browser reports as a failed download
                # rather than a finished one of a broken zip.
                logger.warning("a zip of %d files stopped partway", len(found), exc_info=True)
                import socket
                import struct

                try:
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                               struct.pack("ii", 1, 0))
                    # Closed here, both its files first, so the close is
                    # the socket's own and not the polite one after.
                    self.rfile.close()
                    self.wfile.close()
                    self.connection.close()
                except OSError:
                    pass

        def _where_each_is(self, root: Path,
                           records: list[dict[str, str]]) -> list[dict[str, str]]:
            """Each file's full path on this computer, which Copy path
            copies. It copied the path inside the study (`simulation/
            production.dcd`), of no use in a terminal or a script opened
            elsewhere. Only where the GUI is on the person's own computer:
            beyond loopback and hosted, a path on the server is not theirs
            to know, and the path inside the study is what is said."""
            if not allow_control or hosting is not None:
                return records
            for record in records:
                record["absolute_path"] = str(root / record["path"])
            return records

        def _send_artifact(
            self,
            root: Path,
            raw_rel: str,
            *,
            download: bool = False,
        ) -> None:
            """A study's file, sent from the disk in pieces.

            It was read whole into memory and sent with `no-store`: a 10 GB
            trajectory took 10 GB of the server's memory, a download cut
            short started again from nothing, and a figure seen twice was
            sent twice. Now it is sent a megabyte at a time, a range of it
            on request (`Range`, so a download resumes), and a copy the
            browser holds is checked rather than sent again (`ETag`)."""
            try:
                target = (root / unquote(raw_rel)).resolve()
                target.relative_to(root)
            except ValueError:
                self.send_error(403, "Artifact path is outside the output directory")
                return
            if not target.is_file() or (
                    not allow_control and not _served_beyond_loopback(root, target)):
                self.send_error(404, "Artifact not found")
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            try:
                handle = target.open("rb")
            except OSError:
                self.send_error(404, "Artifact not found")
                return
            with handle:
                info = os.fstat(handle.fileno())
                size = info.st_size
                tag = f'"{info.st_mtime_ns:x}-{size:x}"'
                modified = formatdate(info.st_mtime, usegmt=True)
                if _names_the_tag(self.headers.get("If-None-Match"), tag):
                    self.send_response(304)
                    self.send_header("ETag", tag)
                    self.send_header("Cache-Control", "private, no-cache")
                    self.end_headers()
                    return
                first, last, status = 0, size - 1, 200
                asked = self.headers.get("Range")
                kept = self.headers.get("If-Range")
                if asked and (kept is None or kept.strip() in (tag, modified)):
                    span = byte_range(asked, size)
                    if span is None:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    if span:
                        first, last = span
                        status = 206
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "private, no-cache")
                self.send_header("ETag", tag)
                self.send_header("Last-Modified", modified)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("X-Content-Type-Options", "nosniff")
                if _can_run_script(content_type):
                    # A study's own page, or one somebody put in it, runs as a
                    # page from nowhere: its scripts work, and it can neither
                    # read this server's answers nor act as the GUI does.
                    self.send_header("Content-Security-Policy", ARTIFACT_SANDBOX)
                if download:
                    self.send_header("Content-Disposition", attachment_named(target.name))
                if status == 206:
                    self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
                self.send_header("Content-Length", str(max(0, last - first + 1)))
                self.end_headers()
                if self.command == "HEAD":
                    return
                handle.seek(first)
                left = last - first + 1
                while left > 0:
                    piece = handle.read(min(SENT_AT_A_TIME, left))
                    if not piece:
                        # Shorter now than when it was looked at: what was
                        # promised cannot be sent, so the connection ends.
                        self.close_connection = True
                        break
                    self.wfile.write(piece)
                    left -= len(piece)

        def _send_svg_bundle(self, root: Path) -> None:
            """Download every generated SVG analysis/report figure as one ZIP."""
            svg_paths = [path for path in _svg_figure_paths(root)
                         if allow_control or _served_beyond_loopback(root, path)]
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
            target = _structure_file(root)
            if target is None:
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
            from fastmdxplora.gui.live_frames import live_frame_text

            # Made whole, so a bound ligand is shown in its pocket rather
            # than in the copy of the box the snapshot wrapped it into.
            text = live_frame_text(_live_dir(root))
            if text is None:
                self.send_error(404, "Live frame not available")
                return
            data = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "chemical/x-pdb; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_live_coordinates(self, root: Path) -> None:
            from fastmdxplora.gui.live_frames import live_frame_coordinates

            said = live_frame_coordinates(_live_dir(root))
            if said is None:
                self.send_error(404, "Live frame not available")
                return
            data = said["data"]
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-FastMDX-Atoms", str(said["atoms"]))
            # Percent-encoded: a header's leading spaces are not kept, and the
            # fingerprint is columns of a PDB line, spaces and all.
            self.send_header("X-FastMDX-Fingerprint", quote(said["fingerprint"]))
            self.end_headers()
            self.wfile.write(data)

        def _send_frames(self, root: Path, name: str,
                         query: dict[str, list[str]] | None = None) -> None:
            from fastmdxplora.gui.trajectory_frames import (FRAMES_FILE, FRAMES_TOPOLOGY,
                                                            frames_info, superposed_frames,
                                                            superposed_name)

            target = root / "simulation" / (FRAMES_FILE if name.endswith(".dcd")
                                            else FRAMES_TOPOLOGY)
            if not target.is_file():
                frames_info(root, most_frames=cfg.max_browser_frames)
            on = ((query or {}).get("superposed") or [""])[0]
            if on and name.endswith(".dcd"):
                # The frames superposed, by a name made from the request's
                # words, written if they are not yet.
                one = lambda key: ((query or {}).get(key) or [""])[0]  # noqa: E731
                to, smooth = one("to") or "first", one("smooth") or "1"
                file, _, reason = superposed_name(on, one("ligand"), one("cutoff") or 5.0, to,
                                                  smooth)
                if file is None:
                    self.send_error(404, reason)
                    return
                if not (root / "simulation" / file).is_file():
                    said = superposed_frames(root, on, ligand=one("ligand"),
                                             cutoff_angstrom=one("cutoff") or 5.0, to=to,
                                             smooth=smooth)
                    if not said.get("ok"):
                        self.send_error(404, said.get("reason"))
                        return
                target = root / "simulation" / file
            between = ((query or {}).get("between") or [""])[0]
            if between and name.endswith(".dcd"):
                # A movie's frames with frames in between them.
                from fastmdxplora.gui.trajectory_frames import frames_between

                tweened, reason = frames_between(
                    target, ((query or {}).get("span") or [""])[0], between)
                if tweened is None:
                    self.send_error(404, reason)
                    return
                target = tweened
            if ((query or {}).get("as") or [""])[0] == "xtc" and name.endswith(".dcd"):
                # As the frames were loaded in pieces: XTC, read in place of
                # them without loading them again.
                from fastmdxplora.gui.trajectory_frames import as_xtc

                target = as_xtc(target)
            try:
                data = target.read_bytes()
            except OSError:
                self.send_error(404, "Frames not available")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream" if name.endswith(".dcd")
                             else "chemical/x-pdb; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_occupancy(self, root: Path, query: dict[str, list[str]]) -> None:
            """A map written beside the frames, named by the request's words."""
            from fastmdxplora.gui.occupancy import occupancy_file

            one = lambda key: (query.get(key) or [""])[0]  # noqa: E731
            name, reason = occupancy_file(one("of"), one("ligand"), one("cutoff") or 5.0)
            target = root / "simulation" / name if name else None
            if target is None or not target.is_file():
                self.send_error(404, reason or "No such map yet")
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_run_frames(self, root: Path, run: str, topology: bool = False) -> None:
            """Another run's frames, fitted to the run shown's first frame,
            or the atoms of a run still running: named by its place in the
            study, never a path."""
            from fastmdxplora.gui.runs_together import TOGETHER

            target = root / TOGETHER / f"run_{run}.{'pdb' if topology else 'dcd'}"
            if not run.isdigit() or not target.is_file():
                self.send_error(404, "No such run's frames")
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "chemical/x-pdb; charset=utf-8" if topology
                             else "application/octet-stream")
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
            # Python 3.10's table has no woff2.
            content_type = ("font/woff2" if target.suffix == ".woff2"
                            else mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            data = target.read_bytes()
            vendored = target.relative_to(static_root.resolve()).parts[0] in ("molstar", "fonts")
            compressed = None
            if "gzip" in (self.headers.get("Accept-Encoding") or "") and len(data) > 65536 \
                    and content_type.startswith(("text/", "application/javascript")):
                compressed = _gzipped(target, data)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            # The vendored Mol*, 5 MB, is named by its version in the page
            # (`molstar.js?v=5.12.0`), as the fonts are in theme.css, so a
            # browser keeps them until the version changes; FastMDXplora's
            # own scripts are asked for afresh each time.
            self.send_header("Cache-Control", "public, max-age=31536000, immutable"
                             if vendored else "no-store")
            if compressed is not None:
                data = compressed
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return LiveDashboardHandler


_GZIPPED: dict[tuple[str, int, int], bytes] = {}
_GZIPPED_LOCK = threading.Lock()


def _gzipped(path: Path, data: bytes) -> bytes:
    """A static file compressed once for each version of it: the vendored
    Mol* is 5.2 MB as sent and 1.5 MB compressed."""
    import gzip

    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _GZIPPED_LOCK:
        kept = _GZIPPED.get(key)
        if kept is None:
            kept = gzip.compress(data, compresslevel=6, mtime=0)
            for old in [k for k in _GZIPPED if k[0] == key[0]]:
                del _GZIPPED[old]
            _GZIPPED[key] = kept
        return kept


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
    hosting: Hosting | None = None,
) -> None:
    session = start_dashboard_session(
        output=output,
        host=host,
        port=port,
        config=config,
        home_mode=home_mode,
        exploration_root=exploration_root,
        hosting=hosting,
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
        # On its own line: the terminal has just echoed ^C, and without a
        # newline the shell's next prompt starts where it left off.
        print("\nGUI stopped.")
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
    hosting: Hosting | None = None,
) -> DashboardSession:
    """Start the dashboard server in a background thread.

    With `hosting`, it is served to someone else through a proxy, and the
    workspace is the one folder it reads and writes: new studies go there,
    and nothing outside it is opened.
    """
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if hosting is not None:
        exploration_root = hosting.workspace
    runtime = DashboardRuntime(
        workspace_root=root,
        exploration_root=Path(exploration_root).resolve() if exploration_root is not None else root.parent,
        active_root=None if home_mode else root,
    )
    runtime.hosting = hosting
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
                hosting=hosting,
            )
            server = _DashboardServer((host, int(candidate)), handler)
        except OSError as exc:
            last_error = exc
            continue
        if hosting is not None:
            logger.info(
                "Hosted: answering only requests that carry the proxy's secret, "
                "under %s, inside %s.",
                ", ".join(sorted(hosting.allowed_hosts)), hosting.workspace)
        elif not _is_loopback_host(host):
            # Said out loud, because the alternative is that somebody
            # discovers it afterwards. There is no login: `allow_control`
            # leaves only the routes listed as open, and what is left is
            # still a live view of this run and its results to anyone who
            # can reach the port.
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


def _determined_values(root: Path, records: list[dict[str, str]]) -> dict[str, str]:
    """Each analysis's recorded mean, where its error was determined, as the
    Analysis page's table says it: beside the analysis on the Files page.
    None for a study with no analysis, and none where it cannot be read."""
    if not any(record["path"].startswith("analysis/") for record in records):
        return {}
    try:
        from fastmdxplora.gui.analysis_overview import overview_of

        rows = overview_of(root).get("rows") or []
    except Exception:  # noqa: BLE001 - a value beside a file is not load-bearing
        logger.debug("the analyses' values could not be read for the Files page", exc_info=True)
        return {}
    said = {}
    for row in rows:
        mean = row.get("mean") if isinstance(row, dict) else None
        if isinstance(mean, dict) and mean.get("determined") and mean.get("said"):
            said[str(row.get("analysis"))] = str(mean["said"])
    return said


def _artifact_records(root: Path) -> list[dict[str, str]]:
    """Every file a run has written, for the Files page.

    Read while the run is still writing. The live-frame history keeps the
    last two hundred frames, so each new frame deletes the oldest, and every
    atomic write passes through a `.tmp` file renamed into place. A file
    that vanished between the walk and its `stat` raised out of here and
    failed the whole listing -- "dashboard route failed: No such file"
    scattered through a production run, each one a refresh that landed in
    the gap. Now each file
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
    real_root = Path(os.path.realpath(root))
    for path in sorted(found):
        if path.suffix == ".tmp" or "__pycache__" in path.parts:
            continue
        try:
            relative = path.relative_to(root)
            info = path.stat()
            inside = Path(os.path.realpath(path)).relative_to(real_root)
        except (OSError, ValueError):
            continue
        # Nothing offered that `/artifacts/` would refuse: a link that
        # leads out of the run, or a file that is not one of its results.
        if _private(relative.parts) or _private(inside.parts):
            continue
        rel = relative.as_posix()
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
    _pair_the_trajectories(records)
    return records


def trajectory_topology(simulation: str, have: set[str]) -> str | None:
    """The topology a trajectory in ``simulation`` (a study's folder, as
    the listing says it) is read with: the atoms it saved where it saved
    some (`trajectory_topology.pdb`), else the whole system's."""
    for name in ("trajectory_topology.pdb", "topology.pdb"):
        if f"{simulation}/{name}" in have:
            return f"{simulation}/{name}"
    return None


def _pair_the_trajectories(records: list[dict[str, str]]) -> None:
    """Each trajectory names the topology it is read with.

    A run saves the trajectory without its water by default, so
    `production.dcd` holds the protein, its ligand and ions, and only
    `trajectory_topology.pdb` describes it. The list offered
    `topology.pdb` beside it, the solvated system of 18,320 atoms against
    the trajectory's 3,257, and put the one that loads it unlabelled in the
    folded record."""
    have = {record["path"] for record in records}
    by_path = {record["path"]: record for record in records}
    for record in records:
        path = PurePosixPath(record["path"])
        if path.name != "production.dcd" or path.parent.name not in ("simulation", "joined"):
            continue
        simulation = (path.parent.parent / "simulation").as_posix() \
            if path.parent.name == "joined" else path.parent.as_posix()
        if simulation.startswith("./"):
            simulation = simulation[2:]
        topology = trajectory_topology(simulation, have)
        if topology is None:
            continue
        record["opens_with"] = topology
        record["label"] = f"{record['label']}, read with {PurePosixPath(topology).name}"
        whole = by_path.get(f"{simulation}/topology.pdb")
        if topology.endswith("/trajectory_topology.pdb") and whole is not None \
                and "not the trajectory's" not in whole["label"]:
            whole["label"] = f"{whole['label']}: not the trajectory's topology"


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
        from fastmdxplora.gui.telemetry import read_study_status

        phases = _phase_rows(manifest, read_study_status(root))
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
                "theme": section.theme,
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

    try:
        from fastmdxplora.gui.figure_provenance import figure_provenance

        provenance = figure_provenance(root)
    except Exception:  # noqa: BLE001 - a chip must never break the dashboard
        logger.debug("figure provenance unavailable", exc_info=True)
        provenance = {}
    return {
        "summary_cards": [asdict(card) for card in cards],
        "metric_rows": [asdict(row) for row in metrics],
        "phase_rows": [asdict(row) for row in phases],
        "analysis_sections": sections,
        "quick_actions": quick_actions,
        # What made each analysis's figures, and the command that makes
        # them again, for the chip on each figure.
        "figure_provenance": provenance,
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
    from fastmdxplora.simulation.resume import extended_production

    # An extended study's length is its pieces', not its first piece's.
    extended = extended_production(root)
    summary = _summary_records(root, manifest, analysis_manifest, sim_manifest,
                               extended=extended)
    setup_manifest = _load_json(_setup_of(root) / "setup_parameters.json")
    system = _system_info(root, manifest, analysis_manifest, sim_manifest)
    return {
        "refreshed_at": _iso_now(),
        "has_analysis": any(record["path"].startswith("analysis/") for record in artifacts),
        "has_report": any(record["path"].startswith("report/") for record in artifacts),
        # No folder and no title where no study is open: the root is then a
        # name that is never created, and it was shown as the study's.
        "output_dir": "" if _no_study(root) else str(root),
        "run_title": "" if _no_study(root) else _run_title(root, manifest),
        "summary": summary,
        "system": system,
        "setup": _setup_details(setup_manifest),
        "simulation": _simulation_details(sim_manifest, read_study_status(root),
                                          extended=extended),
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
    # A system setup wrote: any ligand in it had its chemistry found or given.
    from fastmdxplora.gui.protein_preview import SYSTEM_CANDIDATES

    info["prepared"] = any(structure_path == root / rel for rel in SYSTEM_CANDIDATES)
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


def _frames_superposed_payload(root: Path, query: dict[str, list[str]]) -> dict[str, Any]:
    """The frames superposed as asked, written if they are not yet, and the
    address the viewer loads them from."""
    from urllib.parse import urlencode

    from fastmdxplora.gui.trajectory_frames import superposed_frames

    def one(key: str) -> str:
        return (query.get(key) or [""])[0]

    on, ligand, cutoff = one("on"), one("ligand"), one("cutoff") or "5"
    to = one("to") or "first"
    smooth = one("smooth") or "1"
    said = superposed_frames(root, on, ligand=ligand or None, cutoff_angstrom=cutoff, to=to,
                             smooth=smooth)
    if not said.get("ok"):
        return said
    # Asked for again under a new address once written again.
    version = (root / "simulation" / said["file"]).stat().st_mtime_ns
    asked = {"superposed": on, "v": version}
    if to != "first":
        asked["to"] = to
    if said["smooth"] > 1:
        asked["smooth"] = said["smooth"]
    if on == "pocket":
        asked.update(ligand=ligand, cutoff=cutoff)
    return {"ok": True, "said": said["said"], "atoms": said["atoms"], "to": to,
            "smooth": said["smooth"],
            "url": "/structure/frames.dcd?" + urlencode(asked)}


def _state_difference_payload(root: Path, query: dict[str, list[str]]) -> dict[str, Any]:
    """Two states' representatives compared, the second placed on the first
    as the Viewer shows the frames: superposed as its words say, or not."""
    from fastmdxplora.gui.states import state_difference
    from fastmdxplora.gui.trajectory_frames import superposed_frames, superposed_name

    def one(key: str) -> str:
        return (query.get(key) or [""])[0]

    on = one("on") or "none"
    frames_file = None
    if on != "none":
        to, smooth = one("to") or "first", one("smooth") or "1"
        frames_file, _, reason = superposed_name(on, one("ligand"), one("cutoff") or 5.0, to,
                                                 smooth)
        if frames_file is None:
            return {"ok": False, "reason": reason}
        if not (root / "simulation" / frames_file).is_file():
            said = superposed_frames(root, on, ligand=one("ligand"),
                                     cutoff_angstrom=one("cutoff") or 5.0, to=to, smooth=smooth)
            if not said.get("ok"):
                return said
    return state_difference(root, one("a"), one("b"), frames_file)


def _occupancy_payload(root: Path, query: dict[str, list[str]]) -> dict[str, Any]:
    """A map of the ligand or the water over the frames played, written if
    it is not yet, and the address the Viewer reads it from."""
    from urllib.parse import urlencode

    from fastmdxplora.gui.occupancy import occupancy

    def one(key: str) -> str:
        return (query.get(key) or [""])[0]

    said = occupancy(root, one("of"), ligand=one("ligand"), cutoff_angstrom=one("cutoff") or 5.0)
    if not said.get("ok"):
        return said
    version = (root / "simulation" / said["file"]).stat().st_mtime_ns
    asked = {"of": said["of"], "ligand": said["ligand"], "cutoff": f"{said['cutoff']:g}",
             "v": version}
    return {**said, "url": "/structure/occupancy.dx?" + urlencode(asked)}


def _live_dir(root: Path) -> Path:
    """Where the study's newest live frame is: its newest piece's, where it
    was carried on (the Viewer showed the first piece's stop beside an
    Overview saying Completed), else its own."""
    from fastmdxplora.gui.telemetry import _pieces_of

    for piece in reversed(_pieces_of(Path(root))):
        if live_frame_exists(piece / "simulation"):
            return piece / "simulation"
    return Path(root) / "simulation"


def _live_frame_index(root: Path) -> dict[str, Any]:
    """The live frame's index, its step and time on the study's clock
    where it is a piece's (a piece counts from its own start)."""
    sim_dir = _live_dir(root)
    index = read_live_frame_index(sim_dir)
    if sim_dir.parent == Path(root) or not index.get("live_frame_available"):
        return index
    from fastmdxplora.gui.telemetry import _offsets_of, _pieces_of, read_status

    try:
        pieces = _pieces_of(Path(root))
        offsets = _offsets_of(Path(root), read_status(root), pieces[:pieces.index(sim_dir.parent) + 1])
        timestep = read_status(sim_dir.parent).get("timestep_fs")
    except Exception:  # noqa: BLE001 - the piece's own clock stands
        return index
    if not offsets:
        return index
    before = offsets[-1][0]
    step = index.get("live_frame_index")
    if isinstance(step, (int, float)):
        index = {**index, "live_frame_index": int(step) + before}
        if isinstance(timestep, (int, float)):
            index["simulation_time_ns"] = (int(step) + before) * float(timestep) / 1e6
    return index


def _live_coordinates_payload(root: Path) -> dict[str, Any]:
    sim_dir = _live_dir(root)
    index = _live_frame_index(root)
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
    *,
    extended: tuple[float, int] | None = None,
) -> list[dict[str, str]]:
    phase_statuses = [
        str(item.get("status") or "unknown").lower()
        for item in manifest.get("phases", [])
        if isinstance(item, dict)
    ]
    if phase_statuses and all(value in {"ok", "completed", "skipped"} for value in phase_statuses):
        status = "completed"
    elif any(value in {"error", "failed"} for value in phase_statuses):
        # A phase asked to stop is said as stopped, as the study's card says.
        from fastmdxplora.simulation.runner import STOPPED_CODE

        stopped = any((item.get("refusal") or {}).get("code") == STOPPED_CODE
                      for item in manifest.get("phases", []) if isinstance(item, dict))
        status = "stopped" if stopped else "failed"
    elif phase_statuses:
        status = "in progress"
    elif read_status(root):
        # No manifest yet, but telemetry says a run is going. The manifest is
        # written when the run ends, so its absence is not a fact about the
        # run. Unless the run ended without saying so.
        status = ("interrupted" if status_as_it_stands(root).get("status") == "interrupted"
                  else "in progress")
    else:
        status = "not run"
    frames = _first_present(
        analysis_manifest.get("n_frames"),
        sim_manifest.get("n_production_frames"),
    )
    atoms = analysis_manifest.get("n_atoms")
    sim_time = sim_manifest.get("duration_ns_actual")
    if extended:
        # The pieces' production, not the first piece's record of its own.
        sim_time = extended[0]
    from fastmdxplora.gui.simulated_time import say_length
    live_status = status_as_it_stands(root)
    temperature = _first_present(
        live_status.get("target_temperature_K"),
        _last_metric_value(root, "temperature"),
    )
    latest = _first_present(live_status.get("status"), status)
    records = [
        {"label": "Project status", "value": status},
        {"label": "Latest status", "value": str(latest or "—")},
        {"label": "Frames", "value": _display_value(frames)},
        {"label": "Atoms", "value": _display_value(atoms)},
        {
            # The production, which the analyses average; the live record's
            # simulated time has equilibration in it.
            "label": "Production",
            "value": say_length(float(sim_time)) if sim_time is not None else "—",
        },
        {
            "label": "Temperature",
            "value": f"{temperature} K" if temperature not in (None, "") else "—",
        },
    ]
    if extended:
        records[4]["value"] += f" in {extended[1]} pieces"
    return records


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
    "hydrogen_bond_ligand_donor": "hbonds",
    "hydrogen_bond_protein_donor": "hbonds",
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
    ("previous", "Set aside by --rerun"),
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
    "simulation/trajectory_topology.pdb": ("Topology of the trajectory: the atoms it saved",
                                           "simulation"),
    "joined/production.dcd": ("The whole trajectory, its segments joined", "simulation"),
    "simulation/state_final.xml": ("Final state: positions, velocities, box", "simulation"),
    "simulation/state_minimized.xml": ("State after minimisation", "simulation"),
    "simulation/checkpoint.chk": ("Checkpoint: positions and velocities, for recovery by hand", "simulation"),
    "simulation/energy.csv": ("Energy log written by OpenMM", "simulation"),
    "simulation/frames.dcd": ("Trajectory frames prepared for the viewer", "record"),
    "simulation/frames_topology.pdb": ("Topology of the frames prepared for the viewer", "record"),
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
    if path.parts[:1] in (("previous",), ("superseded",)) and len(path.parts) > 1:
        # What `--rerun` replaced (`previous/<phase>`) and the windows a
        # study of windows ran again (`superseded/`): kept, and apart. They
        # were listed with the analyses and figures they were replaced by,
        # two of each title and nothing to say which was which.
        inner = Path(*path.parts[1:]).as_posix()
        if path.parts[0] == "superseded":
            inner = Path(*path.parts[2:]).as_posix() if len(path.parts) > 2 else inner
        said, _ = _artifact_label(inner)
        return (f"{said}, set aside" if path.parts[0] == "previous"
                else f"{said}, of a window run again", "previous")
    if path.parts[:1] == ("viewer_beside",):
        return (f"Another study's frames fitted beside this one's for the Viewer: {path.name}",
                "record")
    if path.parts[:1] == ("viewer_runs",):
        return ((f"Frames of a run fitted for the Viewer: {path.name}" if path.suffix == ".dcd"
                 else "Which runs the Viewer plays together"), "record")
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
    source = _setup_of(root) / "input.pdb"
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


def _setup_of(root: Path) -> Path:
    """The setup record of the system a run simulated: its own ``setup/``,
    or, for a run given `setup_from`, the named system's. A run that
    prepared nothing showed no setup details and no ligand positions."""
    from fastmdxplora.simulation.pipeline import setup_records_of

    try:
        return setup_records_of(root) or root / "setup"
    except Exception:  # noqa: BLE001 - a page to draw, not a record to write
        return root / "setup"


def _system_name(root: Path, manifest: dict[str, Any]) -> str:
    """What this run is of, from whichever record has it yet."""
    named = manifest.get("system")
    if named:
        return str(named)
    setup_manifest = _load_json(_setup_of(root) / "setup_parameters.json")
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
    live_status = read_study_status(root)
    params = sim_manifest.get("parameters")
    if not isinstance(params, dict):
        params = sim_manifest
    return {
        # The setup phase records the system before simulation starts, so a
        # run in progress has had a name from its first minute. Reading only
        # the manifest, which is written when the run ends, the top bar spent
        # the whole run showing the browser's placeholder label instead.
        "system": _system_name(root, manifest),
        "output_folder": "" if _no_study(root) else root.as_posix(),
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
    *,
    extended: tuple[float, int] | None = None,
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
        "duration_ns_actual": (extended[0] if extended else _first_present(
            sim_manifest.get("duration_ns_actual"),
            params.get("duration_ns"),
        )),
        "pieces": extended[1] if extended else 1,
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


def _no_study(root: Path) -> bool:
    """Whether ``root`` is the name the runtime gives when no study is open."""
    return Path(root).name == _NO_CURRENT_RUN


def _open_local_path(path: Path) -> tuple[bool, str]:
    """Open ``path`` in the host file manager on a best-effort basis."""

    # macOS's `open` said "The file ... does not exist." in the terminal
    # the GUI runs in, for a folder that was never there.
    if not os.path.exists(path):
        return False, f"{path} does not exist."

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


def _reveal_local_path(path: Path) -> tuple[bool, str]:
    """Show a file in the host's file manager, selected where the file
    manager can be asked to: Finder, Explorer, or one that answers the
    freedesktop call; else its folder opened."""
    path = Path(path)
    if not path.exists():
        return False, f"{path} does not exist."
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        elif os.name == "nt":
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            import shutil

            sender = shutil.which("dbus-send")
            if sender:
                asked = subprocess.run(
                    [sender, "--session", "--print-reply", "--dest=org.freedesktop.FileManager1",
                     "--type=method_call", "/org/freedesktop/FileManager1",
                     "org.freedesktop.FileManager1.ShowItems",
                     f"array:string:{path.as_uri()}", "string:"],
                    capture_output=True, timeout=5, check=False)
                if asked.returncode == 0:
                    return True, "shown"
            return _open_local_path(path.parent)
    except Exception as exc:  # noqa: BLE001 - dashboard helper only
        return False, str(exc)
    return True, "shown"


def _is_loopback_host(host: str) -> bool:
    normalized = str(host).strip().lower().strip("[]")
    return normalized in {"127.0.0.1", "localhost", "::1"}


def _names_this_machine(host_header: str) -> bool:
    """Whether a Host header names this machine by loopback, port aside."""
    import ipaddress

    value = host_header.strip().lower()
    if value.startswith("["):
        name = value[1:value.find("]")] if "]" in value else value[1:]
    else:
        name = value.rsplit(":", 1)[0] if value.count(":") == 1 else value
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False
