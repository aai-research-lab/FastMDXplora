"""What FastMDXplora offers an assistant, and the server offering it."""

from __future__ import annotations

import logging
from typing import Any, Callable

from fastmdxplora.mcp.protocol import (
    INVALID_PARAMS,
    Call,
    Cancelled,
    InputRequired,
    Method,
    ProtocolError,
    Server,
)
from fastmdxplora.mcp.tools import TOOLS, Context, Tool, ToolError
from fastmdxplora.mcp.workspace import Workspace

__all__ = ["App", "server_info"]

logger = logging.getLogger("fastmdx.mcp")

#: How long a listing of what never changes while the server runs keeps.
_AN_HOUR_MS = 3_600_000


def server_info() -> dict[str, str]:
    from fastmdxplora import __version__

    return {"name": "fastmdxplora", "title": "FastMDXplora", "version": str(__version__),
            "websiteUrl": "https://github.com/aai-research-lab/FastMDXplora"}


def _instructions(workspace: Workspace, runs: bool) -> str:
    lines = [
        "FastMDXplora designs, checks, runs and reads molecular dynamics studies on this "
        f"machine, in the workspace {workspace.root}.",
        "- To write or change a study from a description, or to ask about one, use "
        "ask_agent first. It is FastMDXplora's own Agent: it looks with the software's "
        "tools, says what it checked, and a study it writes is accepted by the "
        "validator before you see it.",
        "- What a tool says is the software's own finding. Quote its numbers with their "
        "errors and units as given; never overrule a refusal or a check with a number "
        "or judgement of your own.",
        "- Look rather than guess: inspect_structure before choosing chains, ligands or "
        "a residue's state; preview_setup before stating a size or a time; "
        "check_selection before writing a selection.",
        "- check_study before anything runs: its plan is what the person should read.",
        "- The resource fastmdxplora://guide/working-with-studies says what the "
        "software's words mean (a mean, 'not determined', 'resolved', a refusal).",
    ]
    if runs:
        lines.append("- start_study only when the person has agreed to that plan: a study "
                     "can take hours of this machine's GPU.")
    else:
        lines.append("- This server only reads and checks: studies are started from "
                     "FastMDXplora itself, not from here.")
    return "\n".join(lines)


class App:
    """The tools, offered over the protocol.

    ``complete_for`` gives the model the Agent writes with (the person's
    own, as `fastmdx agent model` chose it); ``runs`` false leaves out every
    tool that starts or stops work.
    """

    def __init__(self, workspace: Workspace, *, runs: bool = True,
                 complete_for: Callable[[], Any] | None = None) -> None:
        self.workspace = workspace
        self.runs = runs
        self.complete_for = complete_for
        self.tools: tuple[Tool, ...] = tuple(t for t in TOOLS if runs or not t.acts)

    def server(self, *, workers: int = 4) -> Server:
        from fastmdxplora.mcp import content

        place = self.workspace

        def listed(call: Call, items: Any, key: str) -> dict[str, Any]:
            _no_cursor(call)
            return {key: items}

        methods = {
            "tools/list": Method(self._list_tools, ttl_ms=_AN_HOUR_MS),
            "tools/call": Method(self._call_tool),
            "prompts/list": Method(lambda call: listed(
                call, [p.listed() for p in content.PROMPTS], "prompts"), ttl_ms=_AN_HOUR_MS),
            "prompts/get": Method(lambda call: content.prompt_messages(
                place, call.params.get("name"), call.params.get("arguments"))),
            "resources/list": Method(lambda call: listed(
                call, content.resources(place), "resources"),
                ttl_ms=content.STUDY_TTL_MS, scope="private"),
            "resources/templates/list": Method(lambda call: listed(
                call, content.TEMPLATES, "resourceTemplates"), ttl_ms=_AN_HOUR_MS),
            "resources/read": Method(lambda call: content.read_resource(
                place, call.params.get("uri"), call.era),
                ttl_ms=content.STUDY_TTL_MS, scope="private"),
        }
        return Server(methods, info=server_info(),
                      instructions=_instructions(self.workspace, self.runs),
                      capabilities={"tools": {}, "prompts": {}, "resources": {}},
                      workers=workers)

    def _list_tools(self, call: Call) -> dict[str, Any]:
        _no_cursor(call)
        return {"tools": [tool.listed() for tool in self.tools]}

    def _call_tool(self, call: Call) -> dict[str, Any]:
        name = call.params.get("name")
        tool = next((t for t in self.tools if t.name == name), None)
        if tool is None:
            raise ProtocolError(INVALID_PARAMS, f"Unknown tool: {name}")
        context = Context(self.workspace, call, self.complete_for, self.runs)
        try:
            said = tool.run(context, tool.checked(call.params.get("arguments")))
            failed = False
        except ToolError as exc:
            said, failed = str(exc), True
        except (InputRequired, Cancelled):
            raise  # the person is asked first, or nobody is waiting any more
        except Exception as exc:  # noqa: BLE001 - said to the model, not a dead call
            from fastmdxplora.refusals import refusal_of

            logger.debug("tool %s failed", name, exc_info=True)
            said, failed = refusal_of(exc).message or f"{type(exc).__name__}: {exc}", True
        return {"content": [{"type": "text", "text": said}], "isError": failed}


def _no_cursor(call: Call) -> None:
    """Every list fits one page, so no cursor was ever given out."""
    if call.params.get("cursor") is not None:
        raise ProtocolError(INVALID_PARAMS, "Unknown cursor: every list here is one page.")
