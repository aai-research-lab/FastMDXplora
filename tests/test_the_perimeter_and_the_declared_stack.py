"""What a non-loopback dashboard will answer, and what the env file installs.

Two findings that are not about a number being wrong. The dashboard's
workflow-control endpoints were gated on a non-loopback bind and the ones
that walk the filesystem and read a named file were not, while `docs/gui.md`
said there was no network exposure at all. And `environment.yml` -- the
documented development setup -- omitted eight packages while the page
describing it said a clone set up that way "has the whole stack".
"""

from __future__ import annotations

from pathlib import Path

from urllib.parse import urlparse

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class TestARemoteDashboardWillNotWalkTheDisk:
    """Asked of a running server rather than read off its source.

    `_is_loopback_host` is monkeypatched to False so the handler is built the
    way a `--dashboard-host 0.0.0.0` bind builds it, while the socket stays
    on localhost and the test stays local.
    """

    @pytest.fixture()
    def remote(self, tmp_path, monkeypatch):
        import json
        import urllib.error
        import urllib.request

        from fastmdxplora.gui import server

        monkeypatch.setattr(server, "_is_loopback_host", lambda host: False)
        session = server.start_dashboard_session(
            output=tmp_path, host="127.0.0.1", port=0)
        try:
            def ask(path, body=None):
                url = f"{session.url.rstrip('/')}{path}"
                data = json.dumps(body).encode() if body is not None else None
                request = urllib.request.Request(
                    url, data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST" if data is not None else "GET")
                try:
                    with urllib.request.urlopen(request, timeout=10) as reply:
                        return reply.status, reply.read().decode()
                except urllib.error.HTTPError as refused:
                    return refused.code, refused.read().decode()
            yield ask
        finally:
            session.server.shutdown()
            session.server.server_close()

    @pytest.mark.parametrize("path", [
        "/api/browse?path=/etc",
        "/api/inspect-directory?path=/etc",
    ])
    def test_it_will_not_list_a_directory(self, remote, path: str) -> None:
        status, body = remote(path)
        assert status == 403, (
            f"{path} answered {status} to a remote caller: {body[:300]}"
        )

    @pytest.mark.parametrize("path", ["/api/load-config", "/api/check-config"])
    def test_it_will_not_read_a_named_file(self, remote, path, tmp_path) -> None:
        """These quote the file back on a parse failure, which over a network
        is a file-content oracle: a planted token and an AWS key were both
        recovered through it."""
        secret = tmp_path / "creds"
        secret.write_text("AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")

        status, body = remote(path, {"path": str(secret)})

        assert status == 403
        assert "AKIA" not in body

    def test_the_control_endpoints_are_still_refused(self, remote) -> None:
        status, _ = remote("/api/run", {"system": "1UBQ"})
        assert status == 403

    def test_the_run_is_still_watchable(self, remote) -> None:
        """The narrowing has to leave what a colleague watching a job needs,
        or the flag has no use left."""
        status, _ = remote("/api/status")
        assert status == 200

    def test_the_documentation_no_longer_claims_immunity(self) -> None:
        text = (ROOT / "docs" / "gui.md").read_text(encoding="utf-8")
        assert "There is no authentication because there is no network" not in text
        assert "--dashboard-host" in text


class TestARefusalReachesTheCallerItRefuses:
    """A 403 nobody receives is indistinguishable from a broken server.

    These endpoints answer a remote caller with a sentence saying why they
    are closed. The handler sent that sentence and returned without reading
    the request body -- and closing a socket with unread data in its receive
    buffer sends RST rather than FIN, which discards the response already on
    the wire.

    Windows CI found it first, and intermittently, which is how a race
    presents: `ConnectionAbortedError: [WinError 10053]` on a twenty-byte
    body, on one of the three tests that post one. On Linux the same request
    is refused cleanly until the body outgrows the socket buffer, so the
    reproduction here sets a small send buffer and posts enough to fill it.
    Without the drain this raises `BrokenPipeError` at 200 kB; with it the
    refusal arrives at every size the server would have parsed.
    """

    @pytest.fixture()
    def address(self, tmp_path, monkeypatch):
        from fastmdxplora.gui import server

        monkeypatch.setattr(server, "_is_loopback_host", lambda host: False)
        session = server.start_dashboard_session(
            output=tmp_path, host="127.0.0.1", port=0)
        parsed = urlparse(session.url)
        try:
            yield parsed.hostname, parsed.port
        finally:
            session.server.shutdown()
            session.server.server_close()

    def post(self, address, path: str, size: int) -> str:
        """The first line of the reply, over a socket too small to hold the
        body -- so an undrained body shows up as a broken pipe rather than
        as nothing at all."""
        import json
        import socket

        body = json.dumps({"system": "1UBQ",
                           "pad": "x" * max(0, size - 30)}).encode()
        head = (f"POST {path} HTTP/1.0\r\nHost: x\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n").encode()
        connection = socket.create_connection(address, timeout=20)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        try:
            connection.sendall(head + body)
            return connection.recv(400).decode(errors="replace").splitlines()[0]
        finally:
            connection.close()

    @pytest.mark.parametrize("path", [
        "/api/run", "/api/run-config", "/api/load-config", "/api/check-config",
        "/api/explore/validate", "/api/explore/start", "/api/explore/stop",
    ])
    def test_every_refused_endpoint_answers_a_body_it_will_not_read(
            self, address, path: str) -> None:
        assert "403" in self.post(address, path, 200_000), (
            f"{path} did not get its refusal back to a caller that sent a "
            "body. The handler answered without reading it, so the close "
            "was an RST and took the response with it.")

    @pytest.mark.parametrize("size", [20, 200_000, 900_000])
    def test_it_holds_at_every_size_the_server_would_parse(
            self, address, size: int) -> None:
        """Twenty bytes is the case Windows failed on; 900 kB is the largest
        body the parser accepts, and the drain is bounded by that same
        limit so the two cannot disagree."""
        assert "403" in self.post(address, "/api/run", size)

    def test_the_drain_is_bounded_by_what_the_parser_accepts(self) -> None:
        """Reading an unbounded body to be polite about refusing it would be
        reading an unbounded amount from a caller already being told no."""
        from fastmdxplora.gui.server import MOST_A_BODY_MAY_BE

        assert MOST_A_BODY_MAY_BE == 1_000_000


class TestTheEnvironmentFileCarriesWhatThePageClaims:
    """`docs/installation.md` says a clone set up from it has the whole
    stack. That was false for four releases, and nothing checked it: the
    dependency test reads the conda recipe, not this file."""

    def _declared(self) -> set[str]:
        loaded = yaml.safe_load((ROOT / "environment.yml").read_text(
            encoding="utf-8"))
        names: set[str] = set()
        for entry in loaded["dependencies"]:
            if isinstance(entry, str):
                names.add(entry.split(">")[0].split("=")[0].split("<")[0].strip())
        return names

    @pytest.mark.parametrize("package", [
        "rdkit",            # ligand perception
        "propka",           # pKa assignment
        "openff-toolkit",   # ligand parameterisation
        "openmm-plumed",    # every enhanced-sampling method
        "scipy",
        "pillow",
        "netcdf4",          # Amber trajectories
        "weasyprint",       # the PDF report
    ])
    def test_the_stack_is_actually_there(self, package: str) -> None:
        assert package in self._declared(), (
            f"environment.yml does not install {package}, so the documented "
            f"development setup lacks it while docs/installation.md says "
            f"otherwise"
        )

    def test_the_conda_only_ones_are_the_reason_this_file_exists(self) -> None:
        """openmm-plumed has no PyPI distribution at all, which is why there
        is deliberately no `plumed` pip extra. A developer set up any other
        way has no umbrella, metadynamics or steered support."""
        assert "openmm-plumed" in self._declared()

    def test_the_floors_match_pyproject(self) -> None:
        """Covered generally in `test_the_declarations_agree`; asserted here
        for the two that have moved, so this file fails on its own terms."""
        text = (ROOT / "environment.yml").read_text(encoding="utf-8")
        assert "numpy>=2.0" in text
        assert "openmm>=8.2" in text


class TestTheCorpusHasSomewhereToRun:
    """AUD31. 105 network-marked tests -- the structure corpus and the
    method-produces-its-result suite -- ran in no environment at all:
    deselected by pytest.ini, absent from CI, unmentioned in CONTRIBUTING."""

    def _workflow(self) -> dict:
        return yaml.safe_load(
            (ROOT / ".github" / "workflows" / "tests.yml").read_text(
                encoding="utf-8"))

    def test_there_is_a_job_for_it(self) -> None:
        assert "corpus" in self._workflow()["jobs"]

    def test_it_runs_the_marked_tests(self) -> None:
        steps = self._workflow()["jobs"]["corpus"]["steps"]
        run = " ".join(str(step.get("run", "")) for step in steps)
        assert "-m network" in run
        assert "tests/validation" in run

    def test_it_installs_the_extras_no_job_ever_has(self) -> None:
        """`[validation]` brings MDAnalysis and ProLIF. Without it
        `cross_tool.py` -- 853 lines of independent comparison against
        pre-registered tolerances -- skips, which is how it has never run.

        Asserted over the whole job rather than over its `run:` lines,
        because these arrive from conda now and a conda dependency is named
        in a `with:` block. What has to stay true is that the job gets them,
        not which installer fetched them.
        """
        job = yaml.safe_dump(self._workflow()["jobs"]["corpus"]).lower()
        assert "mdanalysis" in job
        assert "prolif" in job

    def test_it_is_built_from_the_environment_file(self) -> None:
        """openff-toolkit and openmm-plumed have no wheel at all, so a job
        that installs by pip runs this corpus with no ligand chemistry and no
        enhanced sampling. On 2026-09-06 that is what happened: `test_it_runs`
        refused for umbrella, metadynamics and steered, the ligand structure
        refused too, and four correct refusals read as four defects. Only
        `environment.yml` describes an environment the physics can run in.
        """
        job = yaml.safe_dump(self._workflow()["jobs"]["corpus"])
        assert "environment.yml" in job

        declared = (ROOT / "environment.yml").read_text(encoding="utf-8")
        assert "openff-toolkit" in declared
        assert "openmm-plumed" in declared

    def test_it_is_scheduled_and_can_be_asked_for(self) -> None:
        workflow = self._workflow()
        triggers = workflow[True] if True in workflow else workflow["on"]
        assert "schedule" in triggers
        assert "workflow_dispatch" in triggers

    def test_it_does_not_gate_a_pull_request(self) -> None:
        """A corpus that fetches from RCSB will go red for reasons that are
        not the contributor's, and a required check that does that gets
        ignored or removed."""
        condition = self._workflow()["jobs"]["corpus"]["if"]
        assert "schedule" in condition or "workflow_dispatch" in condition

    def test_contributing_says_how_to_run_it(self) -> None:
        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        assert "-m network" in text


class TestTheLinterHasAJobAndAStatedScope:
    """AUD35. CONTRIBUTING required it before there was a job for it."""

    def test_there_is_a_lint_step(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "tests.yml").read_text(
                encoding="utf-8"))
        names = [s.get("name") for s in workflow["jobs"]["test"]["steps"]]
        assert "Lint" in names

    def test_the_rules_it_gates_on_are_clean(self) -> None:
        """The gate is F and B -- the rules that find bugs. Holding those
        hostage to a backlog of import order and line length is how a lint
        job ends up permanently disabled.

        Skipped where ruff is absent rather than erroring. The first version
        of this called `subprocess.run(["ruff", ...])` unguarded, and ruff
        was in the `[dev]` extra and not in `[test]` -- so it raised
        FileNotFoundError in every environment installed the documented way,
        including CI's own test job. A test that shells out to a tool has to
        say what it needs. It is in `[test]` now as well, so the skip should
        be rare; it stays because an environment predating that change is
        not a broken one.
        """
        import shutil
        import subprocess

        if shutil.which("ruff") is None:
            pytest.skip("ruff is not installed; `pip install -e '.[test]'`")

        result = subprocess.run(
            ["ruff", "check", "src", "tests", "--select", "F,B"],
            cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout[-2000:]

    def test_contributing_says_which_subset_must_pass(self) -> None:
        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        assert "--select F,B" in text


class TestThreeTestsThatHadNeverRun:
    """Found by the F811 rule the lint gate now enforces: a function defined
    twice in one file, so the earlier one is replaced before pytest collects
    it. Not in the 39 findings -- this is what turning the rule on found."""

    @pytest.mark.parametrize("path", [
        "tests/test_live_status.py",
        "tests/test_concrete_analyses.py",
    ])
    def test_no_test_is_defined_twice(self, path: str) -> None:
        import ast
        from collections import Counter

        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        repeated: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            names = Counter(
                child.name for child in node.body
                if isinstance(child, ast.FunctionDef)
                and child.name.startswith("test_"))
            repeated += [f"{node.name}.{n}" for n, c in names.items() if c > 1]

        assert not repeated, (
            f"defined twice, so the first never runs: {repeated}"
        )


class TestAnUnguardedImportIsACoreDependency:
    """AUD18, and the reason it was invisible.

    `analysis/ligand_chemistry.py` imported RDKit at the top of `_perceive`
    with no guard, and RDKit is in `[ligand]` and `[test]` and not in the
    core dependency list. So on a plain `pip install fastmdxplora` the call
    raised ModuleNotFoundError, escaping `resolve_ligand_chemistry` and its
    carefully written refusal -- the one that names every route tried and
    says to supply an SDF. The user got a traceback instead of a next step,
    on the single path where "it refuses rather than guesses" was not true.

    `test_dependencies_declared` was supposed to catch exactly this and could
    not: it sliced pyproject from `dependencies = [` to `[project.urls]`,
    which spans the whole optional-dependencies table, so anything named in
    any extra counted as a core declaration. That slice is a TOML parse now,
    and the distinction it lost is what this class asserts.
    """

    def _core(self) -> set[str]:
        from tests._toml import load_toml

        project = load_toml(ROOT / "pyproject.toml")["project"]
        import re as _re
        return {_re.split(r"[><=!~;\s\[]", entry, maxsplit=1)[0].strip().lower()
                for entry in project["dependencies"]}

    def test_rdkit_is_not_a_core_dependency(self) -> None:
        """Stated so the test below means something. RDKit belongs in the
        ligand extra -- the point is that the code must cope without it."""
        assert "rdkit" not in self._core()

    def test_perception_returns_none_rather_than_raising(self, monkeypatch) -> None:
        """The behaviour that lets the refusal downstream do its job."""
        import builtins

        from fastmdxplora.analysis import ligand_chemistry

        real_import = builtins.__import__

        def without_rdkit(name, *args, **kwargs):
            if name.startswith("rdkit"):
                raise ImportError("No module named 'rdkit'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", without_rdkit)
        assert ligand_chemistry._perceive(None, [], "LIG", None) is None

    def test_the_refusal_says_rdkit_is_missing(self, monkeypatch) -> None:
        """And names the install line, rather than reading as though
        perception had been tried and had not worked."""
        from fastmdxplora.analysis import ligand_chemistry

        monkeypatch.setattr(ligand_chemistry, "_rdkit_is_absent", lambda: True)
        monkeypatch.setattr(ligand_chemistry, "_perceive",
                            lambda *a, **k: None)

        with pytest.raises(ValueError) as refusal:
            ligand_chemistry.resolve_ligand_chemistry(
                traj=None, atom_indices=[], resname="LIG")

        message = str(refusal.value)
        assert "RDKit is not installed" in message
        assert "conda install" in message
        assert "Supply an SDF" in message
