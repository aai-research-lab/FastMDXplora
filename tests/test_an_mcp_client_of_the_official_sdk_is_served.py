"""The protocol's own client library is served, in both eras.

The server is written without the MCP SDK, so what it says it speaks is
checked against the SDK's client where that is installed: `fastmdx mcp` is
started as a subprocess, as an assistant starts it, and driven once as a
modern client (it probes `server/discover`) and once as a legacy one (it
opens with `initialize`), the person being asked before a study starts in
each, and the app's own model lent to the Agent in each. Skipped where the
SDK is not installed; it is not a dependency.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from fastmdxplora.gui.exploration import exploration_environment_error
from tests.test_an_assistant_reads_and_checks_studies import _structure, _study

pytest.importorskip("mcp.client.stdio", reason="the MCP SDK is not installed")


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    _structure(root / "ghg.pdb")
    (root / "ghg.yml").write_text("systems:\n  - system: ghg.pdb\nsimulation:\n"
                                  "  duration_ns: 5\n")
    _study(root / "ubq", duration=10, means={"rmsd": (0.1234, 0.0056)},
           started="2026-09-02T10:00:00+00:00")
    return root


async def _drive(workspace, mode: str) -> dict:
    import mcp_types as types
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    asked: list[str] = []

    async def elicit(context, params):
        asked.append(params.message)
        return types.ElicitResult(action="decline")

    lent: list[str] = []

    async def sample(context, params):
        lent.append(params.messages[0].content.text)
        return types.CreateMessageResult(
            role="assistant", model="sdk-model", content=types.TextContent(
                type="text", text="systems:\n  - system: ghg.pdb\nsimulation:\n"
                                  "  duration_ns: 5\n"))

    server = StdioServerParameters(
        command=sys.executable, args=["-m", "fastmdxplora", "mcp", "--workspace", str(workspace)],
        env={"FASTMDXPLORA_CONFIG_DIR": str(workspace.parent / "settings"),
             "FASTMDXPLORA_CACHE_DIR": str(workspace.parent / "cache")})
    seen: dict = {"asked": asked, "lent": lent}
    async with Client(server, mode=mode, elicitation_callback=elicit,
                      sampling_callback=sample) as client:
        seen["version"] = client.protocol_version
        seen["name"] = client.server_info.name
        seen["tools"] = [tool.name for tool in (await client.list_tools()).tools]
        checked = await client.call_tool("check_study", {"config": "ghg.yml"})
        text = checked.content[0].text
        plan_id = text.split("plan_id: ")[1].split()[0]
        started = await client.call_tool("start_study", {"config": "ghg.yml",
                                                         "plan_id": plan_id})
        seen["started"] = (started.is_error, started.content[0].text)
        read = await client.call_tool("read_study", {"study": "ubq"})
        seen["read"] = read.content[0].text
        refused = await client.call_tool("check_study", {"config": "nothing.yml"})
        seen["refused"] = (refused.is_error, refused.content[0].text)
        seen["prompts"] = [p.name for p in (await client.list_prompts()).prompts]
        resources = (await client.list_resources()).resources
        seen["resources"] = [str(r.uri) for r in resources]
        guide = await client.read_resource("fastmdxplora://guide/working-with-studies")
        seen["guide"] = guide.contents[0].text
        agent = await client.call_tool("ask_agent", {"request": "Five nanoseconds of ghg.pdb",
                                                     "save": False})
        seen["agent"] = (agent.is_error, agent.content[0].text)
    return seen


@pytest.mark.parametrize("mode, version", [("auto", "2026-07-28"), ("legacy", "2025-11-25")])
def test_the_sdk_client_is_served(workspace, mode, version):
    seen = asyncio.run(_drive(workspace, mode))
    assert seen["version"] == version and seen["name"] == "fastmdxplora"
    # The assistant writes and the validator judges; the Agent, optional, comes last.
    assert seen["tools"][0] == "inspect_structure" and seen["tools"][-1] == "ask_agent"
    assert "start_study" in seen["tools"]
    if exploration_environment_error({"systems": [{"system": "ghg.pdb"}]}):
        # Without what a run needs, it is refused before anybody is asked.
        assert seen["started"][0] is True
        assert seen["started"][1].startswith("This machine cannot run it yet: ")
        assert seen["asked"] == []
    else:
        assert seen["started"] == (False, "Not started: the person did not go ahead.")
        assert len(seen["asked"]) == 1
        assert seen["asked"][0].startswith("Start the study in ghg.yml on this machine?")
    assert seen["read"].startswith("The study at ubq\nstatus: completed")
    assert seen["refused"] == (True, "There is no config at nothing.yml in the workspace.")
    assert seen["prompts"][0] == "design_a_study"
    assert seen["resources"][-1] == "fastmdxplora://study/ubq"
    assert seen["guide"].startswith("# Working with FastMDXplora studies")
    # The Agent wrote with the app's model, asked once, and no key of the person's.
    assert len(seen["lent"]) == 1 and "Five nanoseconds of ghg.pdb" in seen["lent"][0]
    assert seen["agent"][0] is False
    assert "own model (sdk-model), lent through the protocol" in seen["agent"][1]
    # Named as the app names itself, not as "the app".
    assert seen["agent"][1].count("Written with the app's own model") == 0
    # Nothing was run, so nothing was written.
    assert sorted(p.name for p in workspace.iterdir()) == ["ghg.pdb", "ghg.yml", "ubq"]
