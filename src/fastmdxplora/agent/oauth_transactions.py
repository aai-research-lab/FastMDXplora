"""Single-use ChatGPT authorization transactions; no credentials or network I/O.

The dashboard connection service owns callback listening, token verification and
protected storage. These helpers enforce PKCE and callback binding before any
code reaches that service. They never change the active model/account.
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlsplit

AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
_CLIENT = re.compile(r"oaiapp_[A-Za-z0-9_-]{1,200}\Z")


class AuthorizationError(ValueError):
    """Safe user-facing refusal, without callback contents or token material."""


@dataclass(repr=False)
class AuthorizationGrant:
    client_id: str
    code: str
    verifier: str
    nonce: str
    redirect_uri: str

    def token_form(self) -> dict[str, str]:
        return {"grant_type": "authorization_code", "client_id": self.client_id,
                "code": self.code, "code_verifier": self.verifier,
                "redirect_uri": self.redirect_uri, "resource": RESOURCE}


@dataclass(repr=False)
class AuthorizationAttempt:
    redirect_uri: str
    host_id: str
    client_id: str = "dynamic_agent_client"
    lifetime_seconds: float = 600
    _state: str = field(default_factory=lambda: secrets.token_urlsafe(32), init=False)
    _nonce: str = field(default_factory=lambda: secrets.token_urlsafe(32), init=False)
    _verifier: str = field(default_factory=lambda: secrets.token_urlsafe(48), init=False)
    _created: float = field(default_factory=time.monotonic, init=False)
    _used: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        parsed = urlsplit(self.redirect_uri)
        try:
            port = parsed.port
            host_uuid = uuid.UUID(self.host_id.removeprefix("urn:uuid:"))
        except (ValueError, AttributeError):
            raise AuthorizationError("The local sign-in configuration is invalid.") from None
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or not port or parsed.path != "/auth/callback" or parsed.query
                or parsed.fragment or parsed.username or parsed.password):
            raise AuthorizationError("Sign-in requires a local loopback callback.")
        if self.host_id != "urn:uuid:" + str(host_uuid):
            raise AuthorizationError("Sign-in requires a stable UUID host identifier.")
        if self.client_id != "dynamic_agent_client" and not _CLIENT.fullmatch(self.client_id):
            raise AuthorizationError("The saved sign-in registration is invalid.")
        if not 0 < self.lifetime_seconds <= 600:
            raise AuthorizationError("The sign-in timeout is invalid.")

    def authorization_url(self) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self._verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        params = {"client_id": self.client_id, "ext_agent_host_id": self.host_id,
                  "redirect_uri": self.redirect_uri, "response_type": "code",
                  "scope": SCOPES, "resource": RESOURCE, "state": self._state,
                  "nonce": self._nonce, "code_challenge_method": "S256",
                  "code_challenge": challenge}
        if self.client_id == "dynamic_agent_client":
            params["agent_name_hint"] = "FastMDXplora"
        return AUTHORIZE_URL + "?" + urlencode(params)

    def cancel(self) -> None:
        with self._lock:
            self._used = True
            self._verifier = self._nonce = self._state = ""

    @property
    def consumed(self) -> bool:
        """Expose lifecycle status without exposing authorization material."""
        with self._lock:
            return self._used

    def consume_callback(self, query: str) -> AuthorizationGrant:
        """Bind and consume a query before code exchange, exactly once.

        Unknown provider parameters are ignored; duplicate security fields are
        rejected. OAuth errors consume the attempt but never become raw UI text.
        An unrelated/wrong-state request does not cancel the legitimate attempt.
        """
        with self._lock:
            if self._used or time.monotonic() - self._created >= self.lifetime_seconds:
                self.cancel_unlocked()
                raise AuthorizationError("Sign-in expired or was already completed. Start a new sign-in.")
            if not isinstance(query, str) or len(query) > 16_384:
                raise AuthorizationError("The sign-in callback is invalid.")
            try:
                values = parse_qs(query, keep_blank_values=True, max_num_fields=32)
            except ValueError:
                raise AuthorizationError("The sign-in callback is invalid.") from None
            if any(len(values.get(name, [])) > 1 for name in ("state", "code", "client_id", "error")):
                raise AuthorizationError("The sign-in callback is ambiguous.")
            state = values.get("state", [""])[0]
            if not state or not state.isascii() or not secrets.compare_digest(state, self._state):
                raise AuthorizationError("The sign-in callback does not match this attempt.")
            if "error" in values:
                self.cancel_unlocked()
                raise AuthorizationError("Sign-in was declined or could not be completed. Try again when ready.")
            issued = values.get("client_id", [self.client_id])[0]
            if not _CLIENT.fullmatch(issued):
                raise AuthorizationError("Sign-in did not supply a valid client registration.")
            if self.client_id != "dynamic_agent_client" and issued != self.client_id:
                raise AuthorizationError("Sign-in returned a different client registration.")
            code = values.get("code", [""])[0]
            if not code or len(code) > 8192 or any(ord(char) < 32 for char in code):
                raise AuthorizationError("Sign-in did not supply a valid authorization code.")
            result = AuthorizationGrant(issued, code, self._verifier, self._nonce, self.redirect_uri)
            self.cancel_unlocked()
            return result

    def cancel_unlocked(self) -> None:
        self._used = True
        self._verifier = self._nonce = self._state = ""
