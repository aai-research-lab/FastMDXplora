"""FastMDXplora supports Linux and macOS, and says so everywhere it is asked.

AmberTools, which gives every ligand its AM1-BCC charges, has no Windows
build, so a Windows install cannot prepare a ligand and the project stopped
supporting it. What a person or an index reads -- the test matrix, the
package classifiers, the installation guide and `fastmdx info` -- has to say
the same, and a classifier still reading "OS Independent" would say the
opposite to everyone who finds the package on PyPI. Nothing refuses to run
on Windows: this is about what is claimed, not what is blocked.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import yaml

from tests._toml import load_toml

ROOT = Path(__file__).resolve().parents[1]


class TestWhatIsClaimed:

    def test_the_test_matrix_is_linux_and_macos(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "tests.yml").read_text(
                encoding="utf-8"))
        systems = workflow["jobs"]["test"]["strategy"]["matrix"]["os"]
        assert sorted(systems) == ["macos-latest", "ubuntu-latest"]

    def test_both_packages_name_the_two_platforms(self) -> None:
        for path in (ROOT / "pyproject.toml",
                     ROOT / "shim-package" / "pyproject.toml"):
            classifiers = load_toml(path)["project"]["classifiers"]
            systems = {c for c in classifiers if c.startswith("Operating System")}
            assert systems == {"Operating System :: POSIX :: Linux",
                               "Operating System :: MacOS"}, path

    def test_the_installation_guide_says_it(self) -> None:
        page = (ROOT / "docs" / "installation.md").read_text(encoding="utf-8")
        assert "**Windows is not supported.**" in page
        assert "## Windows" not in page
        assert "WSL" not in page


class TestInfoSaysWhichPlatformItIsOn:

    @staticmethod
    def _info(monkeypatch, capsys, *, as_json: bool) -> str:
        # The module, not the `main` function the package re-exports.
        cli = importlib.import_module("fastmdxplora.cli.main")
        monkeypatch.setattr(
            cli, "_probe_backends",
            lambda names: {name: ("installed", "") for name in names})
        monkeypatch.setattr("fastmdxplora.provenance.source_provenance",
                            lambda: None)
        assert cli._cmd_info(argparse.Namespace(json=as_json)) == 0
        return capsys.readouterr().out

    @staticmethod
    def _on_windows(monkeypatch) -> None:
        import platform

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(platform, "machine", lambda: "AMD64")

    def test_on_windows_it_says_plainly_that_it_is_not_supported(
            self, monkeypatch, capsys) -> None:
        self._on_windows(monkeypatch)
        printed = self._info(monkeypatch, capsys, as_json=False)
        assert "Platform: Windows AMD64" in printed
        assert "Windows is not supported" in printed
        assert "Linux and macOS" in printed

    def test_and_the_json_carries_the_same(self, monkeypatch, capsys) -> None:
        self._on_windows(monkeypatch)
        record = json.loads(self._info(monkeypatch, capsys, as_json=True))
        assert record["platform"] == {"name": "Windows", "machine": "AMD64",
                                      "supported": False}

    def test_macos_is_named_as_people_know_it(self, monkeypatch, capsys) -> None:
        import platform

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(platform, "machine", lambda: "arm64")
        printed = self._info(monkeypatch, capsys, as_json=False)
        assert "Platform: macOS arm64" in printed
        assert "not supported" not in printed

    def test_a_supported_platform_is_only_named(self, monkeypatch, capsys) -> None:
        import platform

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform, "machine", lambda: "x86_64")
        record = json.loads(self._info(monkeypatch, capsys, as_json=True))
        assert record["platform"]["supported"] is True
