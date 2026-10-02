"""Isolated official Gemini authentication and tool-free content generation."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from fastmdxplora.agent.claude_plan import ClaudePlan, _SYSTEM_ENV
from fastmdxplora.agent.openai_plan import ConnectionError


def executable():
    node = shutil.which("node")
    located = shutil.which("gemini")
    candidates = [Path(located).parent / "node_modules/@google/gemini-cli/bundle/gemini.js"] if located else []
    candidates.append(Path.home() / ".fastmdxplora/provider-tools/gemini/node_modules/@google/gemini-cli/bundle/gemini.js")
    for entry in candidates:
        if node and entry.is_file():
            return [str(Path(node).resolve()), str(entry.resolve())]
    raise ConnectionError("Install the official Gemini CLI 0.62.0 to connect a Google account.")


class GeminiPlan:
    def __init__(self, root, *, client=None):
        self.root = Path(root).resolve()
        self.client = client

    def environment(self):
        self.root.mkdir(parents=True, exist_ok=True)
        work = self.root / "work"
        work.mkdir(exist_ok=True)
        if os.name != "nt":
            self.root.chmod(0o700)
        env = {key: value for key, value in os.environ.items() if key.upper() in _SYSTEM_ENV}
        env.update(HOME=str(self.root), USERPROFILE=str(self.root), GEMINI_CLI_HOME=str(self.root),
                   APPDATA=str(self.root / "roaming"), LOCALAPPDATA=str(self.root / "local"),
                   XDG_CONFIG_HOME=str(self.root / "config"), XDG_CACHE_HOME=str(self.root / "cache"),
                   GEMINI_CLI_NO_RELAUNCH="true", GEMINI_FORCE_FILE_STORAGE="true",
                   GEMINI_FORCE_ENCRYPTED_FILE_STORAGE="true")
        return env

    def invoke(self, action, payload=None, *, cancelled=None):
        env = self.environment()
        node, entry = self.client or executable()
        bridge = Path(__file__).with_name("gemini_bridge.mjs")
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            process = subprocess.Popen([node, str(bridge), entry, action], cwd=self.root / "work",
                                       env=env, shell=False, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kwargs)
            code, output = ClaudePlan.collect(process, text=json.dumps(payload) if payload else "",
                                             timeout=600 if action == "login" else 180,
                                             cancelled=cancelled)
            result = json.loads(output.strip().splitlines()[-1])
        except (OSError, ValueError, IndexError, ConnectionError):
            raise ConnectionError("The Gemini connection was cancelled or could not complete. Check the official client and account.") from None
        if code != 0 or not isinstance(result, dict) or result.get("ok") is not True:
            raise ConnectionError("Gemini could not verify this isolated account request. Check login, Code Assist enrollment, client version and account quota.")
        return result

    def identity(self):
        return self.invoke("identity")

    def login(self, cancelled):
        return self.invoke("login", cancelled=cancelled)

    def logout(self):
        if self.invoke("logout").get("logged_out") is not True:
            raise ConnectionError("Gemini could not confirm local logout. Check the provider account controls.")

    def models(self, expected_subject):
        result = self.invoke("models")
        if result.get("identity", {}).get("subject") != expected_subject:
            raise ConnectionError("The Google account changed. Reconnect and select it before asking.")
        return result["models"]

    def complete(self, prompt, expected_subject, model, *, cancelled=None):
        result = self.invoke("complete", {"prompt": prompt, "subject": expected_subject, "model": model}, cancelled=cancelled)
        if result.get("identity", {}).get("subject") != expected_subject or result.get("model") != model:
            raise ConnectionError("The Gemini account or model changed during the explanation.")
        text = result.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ConnectionError("Gemini returned no complete explanation.")
        return text
