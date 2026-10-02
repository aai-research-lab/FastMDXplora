import json
import os
import subprocess
import sys
import threading

import pytest

from fastmdxplora.agent.claude_plan import ClaudePlan
from fastmdxplora.agent.openai_plan import ConnectionError


def status(**changes):
    record = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
              "subscriptionType": "pro", "email": "Fixture@example.test"}
    record.update(changes)
    return 0, json.dumps(record)


def test_environment_excludes_billing_credentials_and_foreign_configuration(tmp_path, monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "NODE_OPTIONS", "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "ANTHROPIC_PROFILE"):
        monkeypatch.setenv(key, "fixture-do-not-inherit")
    adapter = ClaudePlan(tmp_path)
    environment = adapter.environment()
    assert "fixture-do-not-inherit" not in json.dumps(environment)
    assert environment["CLAUDE_CODE_SAFE_MODE"] == "1"
    assert environment["CLAUDE_CONFIG_DIR"] == str(tmp_path / "config")
    assert environment["HOME"] == environment["USERPROFILE"] == str(tmp_path / "home")
    assert (tmp_path / "work").is_dir()


@pytest.mark.parametrize("changes", [{"loggedIn": False}, {"authMethod": "api_key"},
                                    {"apiProvider": "bedrock"}, {"subscriptionType": "team"},
                                    {"subscriptionType": "enterprise"}, {"subscriptionType": None}])
def test_wrong_authentication_or_managed_plan_fails_closed(tmp_path, monkeypatch, changes):
    adapter = ClaudePlan(tmp_path)
    monkeypatch.setattr(adapter, "spawn", lambda args: object())
    monkeypatch.setattr(adapter, "collect", lambda process, **kwargs: status(**changes))
    with pytest.raises(ConnectionError, match="subscription"):
        adapter.identity()


def test_completed_explanation_has_no_tools_shell_mcp_hooks_or_persistence(tmp_path, monkeypatch):
    adapter = ClaudePlan(tmp_path)
    commands, inputs = [], []
    def spawn(args):
        commands.append(args)
        return args
    def collect(args, **kwargs):
        inputs.append(kwargs.get("text"))
        if args[:2] == ["auth", "status"]:
            return status()
        return 0, json.dumps({"type": "result", "subtype": "success", "is_error": False,
                              "permission_denials": [], "result": "Grounded fixture explanation."})
    monkeypatch.setattr(adapter, "spawn", spawn)
    monkeypatch.setattr(adapter, "collect", collect)
    prompt = 'Explain this range; $(untrusted-command) --dangerously-skip-permissions'
    assert adapter.complete(prompt, "fixture@example.test") == "Grounded fixture explanation."
    args = commands[1]
    assert args[args.index("--tools") + 1] == ""
    assert json.loads(args[args.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert json.loads(args[args.index("--settings") + 1])["disableAllHooks"] is True
    assert "--safe-mode" in args and "--no-session-persistence" in args and "--strict-mcp-config" in args
    assert "--disable-slash-commands" in args and "--no-chrome" in args
    assert prompt not in args and prompt in inputs


@pytest.mark.parametrize("response", [{"subtype": "error_during_execution"}, {"is_error": True},
                                      {"permission_denials": [{"tool_name": "Bash"}]}, {"result": ""}])
def test_unsuccessful_or_tool_attempted_reply_is_not_accepted(tmp_path, monkeypatch, response):
    adapter = ClaudePlan(tmp_path)
    monkeypatch.setattr(adapter, "identity", lambda: {"subject": "fixture"})
    monkeypatch.setattr(adapter, "spawn", lambda args: object())
    record = {"type": "result", "subtype": "success", "is_error": False, "result": "text"}
    record.update(response)
    monkeypatch.setattr(adapter, "collect", lambda *args, **kwargs: (0, json.dumps(record)))
    with pytest.raises(ConnectionError, match="tool-free"):
        adapter.complete("question", "fixture")


def test_provider_process_reads_stdin_and_preserves_empty_tool_argument():
    process = subprocess.Popen([sys.executable, "-c", "import sys,json; print(json.dumps([sys.argv[1:],sys.stdin.read()]))",
                                "--tools", ""], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    code, text = ClaudePlan.collect(process, text="fixture prompt", timeout=5)
    assert code == 0 and json.loads(text) == [["--tools", ""], "fixture prompt"]


def test_cancelled_process_is_terminated_without_returning_raw_diagnostics():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(ConnectionError, match="cancelled"):
        ClaudePlan.collect(process, timeout=5, cancelled=cancelled)
    assert process.poll() is not None


def test_oversized_provider_output_is_refused(monkeypatch):
    monkeypatch.setattr("fastmdxplora.agent.claude_plan.MAX_OUTPUT", 100)
    process = subprocess.Popen([sys.executable, "-c", "print('x'*1000)"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    with pytest.raises(ConnectionError, match="size limit"):
        ClaudePlan.collect(process, timeout=5)
    assert process.poll() is not None


@pytest.mark.skipif(os.name != "nt", reason="Windows policy source")
def test_managed_policy_is_refused_before_provider_process(tmp_path, monkeypatch):
    policy = tmp_path / "ClaudeCode/managed-settings.d"
    policy.mkdir(parents=True)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    adapter = ClaudePlan(tmp_path / "app", client=sys.executable)
    with pytest.raises(ConnectionError, match="Managed Claude policy"):
        adapter.spawn(["--help"])
