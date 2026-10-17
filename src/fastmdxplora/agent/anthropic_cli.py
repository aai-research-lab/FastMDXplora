"""Use Anthropic's documented local ``ant`` OAuth profile for API calls.

This is Anthropic Console OAuth for the Anthropic API. FastMDXplora gives the CLI
its own config directory and deliberately
passes no inherited API-key/profile environment variables, so an existing
Anthropic CLI session is neither inspected nor reused.
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import BackendUnavailable, CodedError
from fastmdxplora.user_dir import user_config_dir


class ConsoleSignInError(CodedError, RuntimeError):
    """An isolated Console profile could not supply a current API session."""

    default_code = "environment.credentials.sign_in_failed"

_login_started: float | None = None
_login_process: subprocess.Popen[str] | None = None
_login_pending = False


def config_dir() -> Path:
    """The app-owned CLI profile, isolated from a user's other ant sessions."""
    root = user_config_dir()
    try:
        record = json.loads((root / "anthropic-console-profile.json").read_text(encoding="utf-8"))
        generation = record["generation"]
        if (not isinstance(generation, str) or len(generation) != 32
                or any(character not in "0123456789abcdef" for character in generation)):
            raise ConsoleSignInError("The Anthropic Console profile identifier is invalid.")
    except FileNotFoundError:
        return root / "anthropic-console"
    except (OSError, ValueError, TypeError, KeyError):
        raise ConsoleSignInError("The Anthropic Console profile could not be read.") from None
    return root / ("anthropic-console-" + generation)


def _retire_profile() -> None:
    """A late terminal/refresh may write only to the retired profile.

    Terminal launchers do not consistently own the login subprocess (notably
    macOS Terminal). Logout alone cannot cancel a later OAuth completion, so
    persist a new, empty app profile before attempting external cancellation.
    This file contains only an opaque identifier, never credentials.
    """
    from fastmdxplora.agent.models import _write_settings

    try:
        _write_settings(user_config_dir() / "anthropic-console-profile.json",
                        {"generation": uuid.uuid4().hex})
    except OSError:
        raise ConsoleSignInError("Anthropic Console disconnect could not be saved. Try again.") from None


def executable() -> str | None:
    return shutil.which("ant")


def can_launch_login() -> bool:
    if not executable():
        return False
    if os.name == "nt":
        return True
    if sys.platform == "darwin":
        return bool(shutil.which("osascript"))
    return bool(shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal"))


def _environment(profile: Path | None = None) -> dict[str, str]:
    # Start from process variables required to locate a terminal and the home
    # directory. Credential and profile variables are never inherited.
    allowed = (
        "PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT",
        "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP", "LANG", "LC_ALL",
        "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    )
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    env["ANTHROPIC_CONFIG_DIR"] = str(profile if profile is not None else config_dir())
    return env


def _run(args: list[str], *, timeout: float = 15.0,
         profile: Path | None = None) -> subprocess.CompletedProcess[str]:
    ant = executable()
    if not ant:
        raise BackendUnavailable("The official Anthropic ant CLI is not installed.")
    return subprocess.run(
        [ant, *args], env=_environment(profile), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False,
    )


def status() -> dict[str, Any]:
    """Verify API access through the isolated profile without returning secrets."""
    global _login_pending
    if not executable():
        return {"available": False, "connected": False,
                "message": "Install Anthropic's official ant CLI to use Anthropic Console sign-in."}
    try:
        profile = config_dir()
        result = _run(["models", "list", "--format", "json"])
        if config_dir() != profile:
            return {"available": True, "connected": False,
                    "message": "Anthropic Console sign-in changed. Refresh its status."}
        if result.returncode == 0:
            _login_pending = False
            return {
                "available": True,
                "connected": True,
                "message": "Connected to the Anthropic API through the app's Anthropic Console profile.",
            }
        message = "Anthropic Console access is not ready. Sign in again from Agent Settings."
    except subprocess.TimeoutExpired:
        message = "Anthropic Console did not respond. Check the connection and try again."
    except OSError:
        message = "The official ant CLI could not be started."
    if _login_pending:
        finished = _login_process is not None and _login_process.poll() is not None
        expired = _login_started is not None and time.monotonic() - _login_started >= 300
        if not finished and not expired:
            return {"available": True, "connected": None, "pending": True,
                    "message": "Waiting for Anthropic Console sign-in in the opened terminal/browser."}
        _login_pending = False
        return {
            "available": True,
            "connected": False,
            "message": (
                "Anthropic Console sign-in was canceled, failed, or timed out. Start it "
                "again from Agent Settings."
            ),
        }
    return {"available": True, "connected": False, "message": message}


def access_token() -> str:
    """Ask ant to refresh and print its OAuth token for this API request only.

    Anthropic documents this command for scripts that call the API with an
    HTTP client. The captured stdout is never returned to the dashboard or
    included in errors/logs.
    """
    if not executable():
        raise BackendUnavailable("Install Anthropic's official ant CLI to use Anthropic Console sign-in.")
    try:
        profile = config_dir()
        result = _run(["auth", "print-credentials", "--access-token"])
    except subprocess.TimeoutExpired:
        raise ConsoleSignInError("Anthropic Console token refresh timed out. Try reconnecting.") from None
    except OSError:
        raise ConsoleSignInError("The official ant CLI could not be started.") from None
    if config_dir() != profile:
        raise ConsoleSignInError("Anthropic Console sign-in changed during token refresh. Try again.")
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise ConsoleSignInError("Anthropic Console sign-in expired or is unavailable. Sign in again.")
    return token


def connect() -> dict[str, Any]:
    """Open the official CLI's interactive login in a visible terminal."""
    global _login_pending, _login_process, _login_started
    ant = executable()
    if not ant:
        return {"ok": False, "message":
                "Install Anthropic's official ant CLI, then try Anthropic Console sign-in again."}
    if _login_pending:
        return {"ok": False, "message":
                "An Anthropic Console sign-in is already waiting in the opened terminal/browser."}
    env = _environment()
    try:
        if os.name == "nt":
            _login_process = subprocess.Popen(
                [ant, "auth", "login"], env=env,
                creationflags=subprocess.CREATE_NEW_CONSOLE, close_fds=True)
        elif sys.platform == "darwin":
            import shlex

            # Terminal may already be running with another provider session.
            # Sanitize its shell too, rather than only osascript's process.
            command = " ".join(shlex.quote(part) for part in (
                "/usr/bin/env", "-i", *(f"{name}={value}" for name, value in env.items()),
                ant, "auth", "login"))
            quoted = '"' + command.replace("\\", "\\\\").replace('"', '\\"') + '"'
            script = f'tell application "Terminal" to do script {quoted}'
            subprocess.Popen(["osascript", "-e", script], env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
            _login_process = None
        else:
            terminal = shutil.which("x-terminal-emulator")
            argv = ([terminal, "-e", ant, "auth", "login"] if terminal
                    else [shutil.which("gnome-terminal") or "gnome-terminal", "--wait", "--",
                          ant, "auth", "login"])
            _login_process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                                               stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL, close_fds=True)
    except OSError:
        return {
            "ok": False,
            "message": (
                "The Anthropic sign-in terminal could not be opened. Check the system "
                "terminal and retry from Agent Settings."
            ),
        }
    _login_started = time.monotonic()
    _login_pending = True
    return {"ok": True, "message":
            "Finish or cancel sign-in in the opened terminal/browser, then refresh status here."}


def disconnect() -> dict[str, Any]:
    """Log out of every profile in this app-owned CLI config directory."""
    global _login_pending, _login_process
    profile = config_dir()
    _retire_profile()
    if _login_pending and _login_process is not None and _login_process.poll() is None:
        _login_process.terminate()
        try:
            _login_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _login_process.kill()
    _login_process = None
    _login_pending = False
    if not executable():
        return {"ok": True, "connected": False,
                "message": "This app has no Anthropic Console profile to disconnect."}
    try:
        result = _run(["auth", "logout", "--all"], profile=profile)
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "Anthropic Console sign-out timed out. Try again."}
    except OSError:
        return {"ok": False, "message": "The official ant CLI could not be started."}
    if result.returncode != 0:
        return {"ok": False, "message": "Anthropic Console sign-out did not complete."}
    return {"ok": True, "connected": False, "message": "Disconnected from Anthropic Console."}
