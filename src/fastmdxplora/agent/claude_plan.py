"""Official Claude client bridge: separate login, explicit input and zero tools.

The provider client owns its credentials. This module never reads or copies a
credential file. No study directory is given to the client, and no shell is used.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastmdxplora.agent.openai_plan import ConnectionError

MAX_OUTPUT = 8_000_000
_SYSTEM_ENV = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
               "LANG", "LC_ALL", "LC_CTYPE", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
               "SSL_CERT_FILE", "SSL_CERT_DIR"}


def unmanaged_host() -> None:
    """Managed hooks outrank CLI flags; fail closed rather than run them."""
    if os.environ.get("WSL_DISTRO_NAME"):
        raise ConnectionError("The Claude bridge needs a native host; WSL policy isolation is not verified.")
    if os.name == "nt":
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Policies\ClaudeCode"):
                    raise ConnectionError("Managed Claude policy is present; tool-free client isolation cannot be guaranteed.")
            except FileNotFoundError:
                pass
            except PermissionError:
                raise ConnectionError("Claude policy could not be checked; the bridge remains disabled.") from None
        base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ClaudeCode"
    elif sys.platform == "darwin":
        # MDM policy can be delivered without a file. Do not claim native macOS
        # isolation until the managed-preferences mechanism is covered.
        raise ConnectionError("The Claude subscription bridge is not yet verified for macOS policy isolation.")
    else:
        base = Path("/etc/claude-code")
    if any((base / name).exists() for name in ("managed-settings.json", "managed-settings.d", "managed-mcp.json")):
        raise ConnectionError("Managed Claude policy is present; tool-free client isolation cannot be guaranteed.")


def executable() -> str:
    """Resolve a native client; never invoke npm's command-shell wrapper."""
    located = shutil.which("claude")
    if located:
        path = Path(located)
        if os.name != "nt" or path.suffix.lower() == ".exe":
            return str(path.resolve())
        native = path.parent / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
        if native.is_file():
            return str(native.resolve())
    raise ConnectionError("Install the official Claude Code client to connect a Claude subscription.")


class ClaudePlan:
    def __init__(self, root: Path, *, client=None):
        self.root = Path(root).resolve()
        self.client = client

    def environment(self) -> dict[str, str]:
        # Do not inherit API keys, OAuth tokens, NODE_OPTIONS, alternate endpoints
        # or another client's profile. The official client creates a fresh login.
        env = {key: value for key, value in os.environ.items() if key.upper() in _SYSTEM_ENV}
        home = self.root / "home"
        config = self.root / "config"
        for path in (home, config, self.root / "work", home / "AppData/Roaming", home / "AppData/Local"):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "AppData/Roaming"),
                   LOCALAPPDATA=str(home / "AppData/Local"), XDG_CONFIG_HOME=str(home / ".config"),
                   CLAUDE_CONFIG_DIR=str(config), CLAUDE_CODE_SAFE_MODE="1",
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
        return env

    def command(self, args):
        return [self.client or executable(), *args]

    def spawn(self, args):
        unmanaged_host()
        env = self.environment()
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            return subprocess.Popen(self.command(args), cwd=self.root / "work", env=env,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, shell=False, **kwargs)
        except OSError:
            raise ConnectionError("The official Claude client could not be started.") from None

    @staticmethod
    def collect(process, *, text="", timeout=120, cancelled=None):
        """Bound output and time, with no provider diagnostics/URLs in logs."""
        if not isinstance(text, str) or len(text.encode("utf-8")) > 1_000_000:
            process.kill()
            raise ConnectionError("The explanation context exceeds the provider input limit.")
        chunks = queue.Queue(maxsize=16)
        stopping = threading.Event()

        def read():
            try:
                while not stopping.is_set():
                    piece = process.stdout.read1(65_536)
                    if not piece:
                        break
                    while not stopping.is_set():
                        try:
                            chunks.put(piece, timeout=0.1)
                            break
                        except queue.Full:
                            pass
            finally:
                while not stopping.is_set():
                    try:
                        chunks.put(None, timeout=0.1)
                        break
                    except queue.Full:
                        pass

        def write():
            try:
                if text:
                    process.stdin.write(text.encode("utf-8"))
                process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

        threading.Thread(target=read, daemon=True).start()
        threading.Thread(target=write, daemon=True).start()
        result = bytearray()
        deadline = time.monotonic() + timeout
        try:
            while True:
                if cancelled and cancelled.is_set():
                    raise ConnectionError("Claude sign-in or explanation was cancelled.")
                if time.monotonic() >= deadline:
                    raise ConnectionError("The Claude client timed out. Retry or reconnect in Settings.")
                try:
                    piece = chunks.get(timeout=0.1)
                except queue.Empty:
                    continue
                if piece is None:
                    break
                result.extend(piece)
                if len(result) > MAX_OUTPUT:
                    raise ConnectionError("The Claude client exceeded the response size limit.")
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
            return process.returncode, result.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            raise ConnectionError("The Claude client timed out.") from None
        finally:
            stopping.set()
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    def identity(self):
        code, output = self.collect(self.spawn(["auth", "status", "--json"]), timeout=20)
        try:
            record = json.loads(output)
        except (ValueError, TypeError):
            raise ConnectionError("Claude could not confirm the app's subscription login. Reconnect in Settings.") from None
        if (code != 0 or not isinstance(record, dict) or record.get("loggedIn") is not True
                or record.get("authMethod") != "claude.ai" or record.get("apiProvider") != "firstParty"
                or record.get("subscriptionType") not in {"pro", "max"}):
            raise ConnectionError("Sign in with a Claude subscription for this app; API billing is not used here.")
        # Status JSON is provider-owned metadata; expose only bounded identity.
        email = record.get("email")
        if not isinstance(email, str) or len(email) > 320 or any(ord(c) < 32 for c in email):
            raise ConnectionError("Claude did not return a usable account identity.")
        return {"email": email, "subject": email.casefold()}

    def login(self, cancelled):
        code, _ = self.collect(self.spawn(["auth", "login", "--claudeai"]), timeout=600, cancelled=cancelled)
        if code != 0:
            raise ConnectionError("Claude browser sign-in did not complete. Retry in Settings.")
        return self.identity()

    def logout(self):
        code, _ = self.collect(self.spawn(["auth", "logout"]), timeout=20)
        if code != 0:
            raise ConnectionError("Claude could not confirm logout. Use the official client's account controls.")

    def models(self, expected_subject):
        """Initialize the official client without sending a user/inference turn."""
        if self.identity()["subject"] != expected_subject:
            raise ConnectionError("The Claude account changed. Reconnect before choosing a model.")
        args = self.explanation_args()
        args[args.index("--output-format") + 1] = "stream-json"
        args += ["--input-format", "stream-json", "--verbose"]
        request = {"type": "control_request", "request_id": "fastmdx-models",
                   "request": {"subtype": "initialize", "hooks": {}, "skills": []}}
        code, output = self.collect(self.spawn(args), text=json.dumps(request) + "\n", timeout=60)
        try:
            rows = [json.loads(line) for line in output.splitlines() if line.strip()]
            response = next(row["response"] for row in rows if row.get("type") == "control_response"
                            and row.get("response", {}).get("request_id") == "fastmdx-models")
            models = response["response"]["models"]
        except (ValueError, KeyError, StopIteration, TypeError):
            raise ConnectionError("Claude did not return its model catalog. Update the official client and reconnect.") from None
        choices = []
        if code != 0 or not isinstance(models, list):
            raise ConnectionError("Claude's model catalog could not be verified.")
        for row in models[:100]:
            model = row.get("value") if isinstance(row, dict) else None
            # Fable/best can spend usage credits without consent in print mode.
            if (isinstance(model, str) and re.fullmatch(r"(default|opus|sonnet|haiku)(\[1m\])?|claude-(opus|sonnet|haiku)-[a-z0-9.-]{1,100}(\[1m\])?", model)
                    and not row.get("disabled") and not row.get("requiresUsageCredits")):
                reported = row.get("supportedEffortLevels", [])
                efforts = [level for level in ("low", "medium", "high", "xhigh", "max")
                           if row.get("supportsEffort") and isinstance(reported, list) and level in reported]
                choices.append({"id": "provider-default" if model == "default" else model,
                                "label": str(row.get("displayName") or model)[:200], "reasoning_levels": efforts})
        if not choices:
            raise ConnectionError("Claude returned no subscription models for this client. Update it and reconnect.")
        if self.identity()["subject"] != expected_subject:
            raise ConnectionError("The Claude account changed while loading models.")
        return choices

    @staticmethod
    def explanation_args():
        return ["--safe-mode", "--print", "--output-format", "json", "--tools", "",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--disable-slash-commands", "--no-chrome", "--no-session-persistence",
                "--permission-mode", "dontAsk", "--setting-sources", "",
                "--settings", '{"disableAllHooks":true,"forceLoginMethod":"claudeai"}']

    def complete(self, prompt, expected_subject, *, cancelled=None, model="provider-default", reasoning=None):
        if self.identity()["subject"] != expected_subject:
            raise ConnectionError("The Claude account changed. Reconnect and select it before asking.")
        from .reasoning import validate
        effort = None
        args = self.explanation_args()
        if model != "provider-default" or reasoning not in {None, "default"}:
            choices = self.models(expected_subject)
            if model not in {row["id"] for row in choices}:
                raise ConnectionError("Choose a current Claude subscription model in Settings.")
            effort = validate(reasoning, next(row["reasoning_levels"] for row in choices if row["id"] == model))
        if model != "provider-default":
            args += ["--model", model]
        if effort:
            args += ["--effort", effort]
        code, output = self.collect(self.spawn(args), text=prompt, timeout=180, cancelled=cancelled)
        try:
            reply = json.loads(output)
        except (ValueError, TypeError):
            raise ConnectionError("Claude returned an incomplete explanation. Retry in Settings.") from None
        if (code != 0 or not isinstance(reply, dict) or reply.get("type") != "result"
                or reply.get("subtype") != "success" or reply.get("is_error") is not False
                or reply.get("permission_denials") or not isinstance(reply.get("result"), str)
                or not reply["result"].strip()):
            raise ConnectionError("Claude could not complete a tool-free explanation. Check login and plan usage.")
        if self.identity()["subject"] != expected_subject:
            raise ConnectionError("The Claude account changed during the explanation. Send a new request.")
        return reply["result"]
