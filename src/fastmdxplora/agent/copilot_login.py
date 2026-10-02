"""Bound the official Copilot SDK's interactive login protocol.

The SDK belongs in an isolated child process, not the scientific environment.
Its typed RPC adapter supplies dictionaries here; this module never accepts a
token, reads a credential file, or grants plaintext persistence consent. The
owner must stop the native client in its own finally block, including when a
begin request fails before returning a flow ID.
"""
from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urlsplit

from fastmdxplora.agent.openai_plan import ConnectionError


def authorization_url(value):
    """Only the official GitHub.com browser flow may be presented to the user."""
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 33 for c in value):
        raise ConnectionError("Copilot returned an invalid sign-in address.")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname == "github.com"
                 and parsed.port in (None, 443) and not parsed.username
                 and not parsed.password and not parsed.fragment
                 and parsed.path in ("/login/device", "/login/oauth/authorize"))
    except ValueError:
        valid = False
    if not valid:
        raise ConnectionError("Copilot returned an unsupported sign-in address.")
    return value


def completed_identity(step):
    """A terminal login result is not model entitlement or inference proof."""
    result = step.get("result")
    if not isinstance(result, dict):
        raise ConnectionError("Copilot did not confirm the login result.")
    if result.get("status") == "needs-plaintext-consent":
        raise ConnectionError("Copilot requested plaintext credential storage; this connection refuses it.")
    login = result.get("login")
    if (result.get("status") != "completed" or result.get("host") != "github.com"
            or not isinstance(login, str)
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", login)
            or "--" in login):
        raise ConnectionError("Copilot did not confirm a GitHub.com account.")
    return {"host": "github.com", "login": login, "subject": "github.com/" + login.lower()}


async def browser_login(*, begin, advance, cancel, publish_url, cancelled,
                        user_requested=False, timeout=600, poll_interval=0.5):
    """Run a user-requested native flow, forwarding only its verified URL.

Callbacks wrap official SDK accounts.login begin/advance/cancel RPCs. They must
not provide alternate credentials or auto-login. Native provider messages and
opaque flow IDs never leave this controller. The owner verifies account status
and model availability separately before marking an account connected.
"""
    if user_requested is not True:
        raise ConnectionError("Start Copilot sign-in with the Connect button.")
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
        raise ValueError("Login timeout must be within 600 seconds.")
    if not isinstance(poll_interval, (int, float)) or not 0 <= poll_interval <= 5:
        raise ValueError("Login polling interval must be within five seconds.")
    deadline = time.monotonic() + timeout
    flow = None
    succeeded = False
    emitted = set()

    async def bounded(awaitable):
        task = asyncio.ensure_future(awaitable)
        try:
            while True:
                if cancelled.is_set():
                    raise ConnectionError("Copilot sign-in was cancelled.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ConnectionError("Copilot sign-in timed out.")
                done, _ = await asyncio.wait({task}, timeout=min(0.1, remaining))
                if done:
                    return task.result()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    try:
        begun = await bounded(begin("githubDotCom"))
        if not isinstance(begun, dict):
            raise ConnectionError("Copilot did not start a supported sign-in flow.")
        candidate = begun.get("flowId")
        if not isinstance(candidate, str) or not 0 < len(candidate) <= 256:
            raise ConnectionError("Copilot did not start a supported sign-in flow.")
        flow = candidate
        step = begun.get("step")
        for _ in range(256):
            if cancelled.is_set():
                raise ConnectionError("Copilot sign-in was cancelled.")
            if not isinstance(step, dict):
                raise ConnectionError("Copilot returned an unsupported login step.")
            kind = step.get("kind")
            if kind == "completed":
                identity = completed_identity(step)
                succeeded = True
                return identity
            if kind == "open-url":
                url = authorization_url(step.get("url"))
                if url not in emitted:
                    publish_url(url)
                    emitted.add(url)
            elif kind != "awaiting":
                # Refuse provider-driven browser/broker interaction, arbitrary
                # input and unknown future protocol steps. Never echo messages.
                raise ConnectionError("Copilot returned an unsupported login step.")
            if poll_interval:
                await bounded(asyncio.sleep(poll_interval))
            step = await bounded(advance(flow))
        raise ConnectionError("Copilot sign-in exceeded the supported step limit.")
    except ConnectionError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:
        raise ConnectionError("Copilot sign-in could not complete.") from None
    finally:
        if flow is not None and not succeeded:
            try:
                await asyncio.wait_for(cancel(flow), timeout=5)
            except (Exception, asyncio.CancelledError):
                # The owner must always stop its native client as a second
                # cleanup boundary, even if the cancel RPC cannot complete.
                pass
