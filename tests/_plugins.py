"""Entry points made up for a test, and the registry put back after it."""

from __future__ import annotations

from typing import Any

import pytest


class Offered:
    """An entry point, as :mod:`importlib.metadata` gives one."""

    def __init__(self, name: str, loads: Any, *, package: str = "a-plugin",
                 value: str = "a_plugin:thing") -> None:
        self.name = name
        self.value = value
        self._loads = loads
        self.dist = type("Dist", (), {"name": package})()

    def load(self) -> Any:
        if isinstance(self._loads, BaseException):
            raise self._loads
        return self._loads


@pytest.fixture
def plugged(monkeypatch):
    """Call it with ``{group: [Offered, ...]}`` to have those be what is
    installed. The registry of analyses and what was read before are put
    back afterwards, in the order they were."""
    pytest.importorskip("mdtraj")
    from fastmdxplora import plugins
    from fastmdxplora.analysis import orchestrator

    before = dict(orchestrator._REGISTRY)

    def install(by_group: dict[str, list[Offered]], *, without: tuple[str, ...] = ()) -> None:
        monkeypatch.setattr(
            plugins, "_entry_points",
            lambda group: sorted(by_group.get(group, []), key=lambda p: p.name))
        plugins.forget()
        for name in without:
            orchestrator._REGISTRY.pop(name, None)

    yield install
    monkeypatch.undo()
    plugins.forget()
    orchestrator._REGISTRY.clear()
    orchestrator._REGISTRY.update(before)
