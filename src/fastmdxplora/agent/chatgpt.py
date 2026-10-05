"""OpenAI ChatGPT subscription authentication through the published Codex client.

The login contract is copied from OpenAI's open-source Codex client, not from
an inferred web flow. It uses the published application client ID, loopback
redirect ports 1455 and 1457, PKCE, and the auth.openai.com OAuth endpoints.
The resulting token is used only with ChatGPT's official Codex backend.

Reference implementation:
https://github.com/openai/codex/tree/main/codex-rs/login
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

from fastmdxplora.refusals import StudyError
from fastmdxplora.user_dir import user_config_dir

CHATGPT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CHATGPT_ISSUER = "https://auth.openai.com"
CHATGPT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CHATGPT_REDIRECT_PORTS = (1455, 1457)
CHATGPT_SCOPE = "openid profile email offline_access api.connectors.read api.connectors.invoke"
CHATGPT_ORIGINATOR = "fastmdxplora"


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
    verifier: str
    port: int


@dataclass(frozen=True)
class ChatGPTTokens:
    access_token: str
    refresh_token: str
    expires_at: float


@dataclass(frozen=True)
class ChatGPTModel:
    id: str
    label: str
    default_reasoning_level: str | None
    reasoning_levels: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "reasoning_levels": list(self.reasoning_levels),
        }
        if self.default_reasoning_level:
            record["default_reasoning_level"] = self.default_reasoning_level
        return record


def token_path(path: Path | None = None) -> Path:
    return path or (user_config_dir() / "chatgpt_tokens.json")


def save_tokens(tokens: ChatGPTTokens, path: Path | None = None) -> Path:
    target = token_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_at": tokens.expires_at,
    }, indent=2), encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def load_tokens(path: Path | None = None) -> ChatGPTTokens | None:
    try:
        record = json.loads(token_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    access = record.get("access_token")
    refresh = record.get("refresh_token")
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        return None
    try:
        expires = float(record.get("expires_at", 0))
    except (TypeError, ValueError):
        return None
    return ChatGPTTokens(access, refresh, expires)


def _pkce_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_request(*, state: str | None = None, verifier: str | None = None,
                           port: int = CHATGPT_REDIRECT_PORTS[0]) -> AuthorizationRequest:
    actual_state = state or secrets.token_urlsafe(32)
    actual_verifier = verifier or _pkce_verifier()
    redirect = f"http://127.0.0.1:{port}/auth/callback"
    query = urllib.parse.urlencode({
        "client_id": CHATGPT_CLIENT_ID,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": CHATGPT_SCOPE,
        "state": actual_state,
        "code_challenge": _challenge(actual_verifier),
        "code_challenge_method": "S256",
    })
    return AuthorizationRequest(
        f"{CHATGPT_ISSUER}/oauth/authorize?{query}",
        actual_state,
        actual_verifier,
        port,
    )


def validate_callback(params: dict[str, Any], *, expected_state: str) -> str:
    if params.get("state") != expected_state:
        raise StudyError(
            "OAuth callback state did not match.",
            code="environment.credentials.invalid",
        )
    if params.get("error"):
        raise StudyError(
            f"OAuth sign-in was refused: {params['error']}",
            code="environment.credentials.invalid",
        )
    code = params.get("code")
    if not isinstance(code, str) or not code:
        raise StudyError(
            "OAuth callback did not contain an authorization code.",
            code="environment.credentials.invalid",
        )
    return code


def request_json(url: str, *, method: str = "GET", body: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None, timeout: float = 30.0,
                 form_encoded: bool = False) -> dict[str, Any]:
    data = None
    if body is not None:
        data = (urllib.parse.urlencode(body).encode("utf-8")
                if form_encoded else json.dumps(body).encode("utf-8"))
    content_type = (
        "application/x-www-form-urlencoded" if form_encoded
        else "application/json"
    )
    request = urllib.request.Request(url, data=data, method=method, headers={
        "accept": "application/json",
        **({"content-type": content_type} if body is not None else {}),
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise StudyError(
            f"OpenAI sign-in service refused the request ({exc.code}): {detail}",
            code="environment.service.unusable_response", url=url,
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", str(exc))
        raise StudyError(
            f"Could not reach the OpenAI sign-in service: {reason}",
            code="environment.service.unreachable", url=url,
        ) from None
    if not isinstance(payload, dict):
        raise StudyError(
            "OpenAI sign-in service returned an invalid response.",
            code="environment.service.unusable_response", url=url,
        )
    return payload


def _exchange_code(code: str, verifier: str, port: int) -> ChatGPTTokens:
    payload = request_json(
        f"{CHATGPT_ISSUER}/oauth/token", method="POST", form_encoded=True,
        body={
            "grant_type": "authorization_code",
            "client_id": CHATGPT_CLIENT_ID,
            "code": code,
            "redirect_uri": f"http://127.0.0.1:{port}/auth/callback",
            "code_verifier": verifier,
        },
    )
    return _tokens_from_payload(payload)


def _tokens_from_payload(payload: dict[str, Any]) -> ChatGPTTokens:
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        raise StudyError(
            "OpenAI sign-in returned no usable session tokens.",
            code="environment.credentials.invalid",
        )
    try:
        expires_in = float(payload.get("expires_in", 3600))
    except (TypeError, ValueError):
        expires_in = 3600.0
    return ChatGPTTokens(access, refresh, time.time() + max(60.0, expires_in))


def _access_token(path: Path | None = None) -> str:
    tokens = load_tokens(path)
    if tokens is None:
        raise StudyError(
            "Sign in with ChatGPT before choosing a subscription model.",
            code="environment.credentials.absent",
        )
    if tokens.expires_at > time.time() + 30:
        return tokens.access_token
    payload = request_json(f"{CHATGPT_ISSUER}/oauth/token", method="POST", body={
        "grant_type": "refresh_token",
        "client_id": CHATGPT_CLIENT_ID,
        "refresh_token": tokens.refresh_token,
    })
    refreshed = _tokens_from_payload(payload)
    save_tokens(refreshed, path)
    return refreshed.access_token


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "originator": CHATGPT_ORIGINATOR,
    }


def model_metadata_from_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("models")
    if not isinstance(rows, list):
        return []
    found: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("slug"):
            continue
        levels = row.get("supported_reasoning_levels") or []
        efforts = [
            str(level.get("effort")) for level in levels
            if isinstance(level, dict) and level.get("effort")
        ]
        found.append(ChatGPTModel(
            id=str(row["slug"]),
            label=str(row.get("display_name") or row["slug"]),
            default_reasoning_level=(
                str(row["default_reasoning_level"])
                if row.get("default_reasoning_level") else None
            ),
            reasoning_levels=tuple(dict.fromkeys(efforts)),
        ).as_record())
    return found


def list_model_metadata(*, path: Path | None = None, timeout: float = 30.0) -> list[dict[str, Any]]:
    token = _access_token(path)
    payload = request_json(
        f"{CHATGPT_CODEX_BASE_URL}/models?client_version=fastmdxplora",
        headers=_auth_headers(token), timeout=timeout,
    )
    return model_metadata_from_response(payload)


def list_models(*, path: Path | None = None, timeout: float = 30.0) -> tuple[str, ...]:
    return tuple(row["id"] for row in list_model_metadata(path=path, timeout=timeout))


def _response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str):
        return direct
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                parts.append(str(content.get("text") or ""))
    return "".join(parts)


def complete(choice: Any, prompt: str, *, path: Path | None = None,
             on_text: Callable[[str], None] | None = None,
             timeout: float = 120.0) -> str:
    token = _access_token(path)
    body = {"model": choice.model, "input": prompt, "stream": on_text is not None}
    if on_text is None:
        payload = request_json(
            f"{CHATGPT_CODEX_BASE_URL}/responses", method="POST", body=body,
            headers=_auth_headers(token), timeout=timeout,
        )
    else:
        return _stream_completion(body, token, on_text, timeout)
    answer = _response_text(payload)
    return answer


def _stream_completion(body: dict[str, Any], token: str,
                       on_text: Callable[[str], None], timeout: float) -> str:
    request = urllib.request.Request(
        f"{CHATGPT_CODEX_BASE_URL}/responses", method="POST",
        data=json.dumps(body).encode(), headers={
            "accept": "text/event-stream",
            "content-type": "application/json",
            **_auth_headers(token),
        })
    parts: list[str] = []
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "error":
                    raise StudyError(
                        "The ChatGPT response stream returned an error.",
                        code="environment.service.unusable_response",
                    )
                if event.get("type") != "response.output_text.delta":
                    continue
                piece = str(event.get("delta") or "")
                if piece:
                    parts.append(piece)
                    on_text(piece)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise StudyError(
            f"ChatGPT refused the request ({exc.code}): {detail}",
            code="environment.service.unusable_response",
        ) from None
    except urllib.error.URLError as exc:
        raise StudyError(
            f"Could not reach the ChatGPT service: {exc.reason}",
            code="environment.service.unreachable",
        ) from None
    return "".join(parts)


def begin_sign_in(*, on_complete: Callable[[ChatGPTTokens], None] | None = None,
                  timeout: float = 300.0) -> AuthorizationRequest:
    """Start the fixed-port local OAuth callback and return its browser URL."""
    selected: AuthorizationRequest | None = None
    server: HTTPServer | None = None
    for port in CHATGPT_REDIRECT_PORTS:
        try:
            selected = authorization_request(port=port)
            server = HTTPServer(("127.0.0.1", port), _callback_handler(
                selected, on_complete, timeout))
            break
        except OSError:
            continue
    if selected is None or server is None:
        raise StudyError(
            "ChatGPT sign-in needs local port 1455 or 1457; both are in use.",
            code="environment.service.unavailable",
        )
    threading.Thread(target=server.handle_request, daemon=True).start()
    return selected


def _callback_handler(request: AuthorizationRequest,
                      on_complete: Callable[[ChatGPTTokens], None] | None,
                      timeout: float) -> type[BaseHTTPRequestHandler]:
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/auth/callback":
                self.send_error(404)
                return
            params = {key: values[-1] for key, values in
                      urllib.parse.parse_qs(parsed.query).items() if values}
            try:
                code = validate_callback(params, expected_state=request.state)
                tokens = _exchange_code(code, request.verifier, request.port)
                save_tokens(tokens)
                if on_complete is not None:
                    on_complete(tokens)
                message = "ChatGPT sign-in completed. You may close this window."
                status = 200
            except (ValueError, StudyError) as exc:
                message = f"ChatGPT sign-in failed: {exc}"
                status = 400
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<p>{message}</p>".encode())

        def log_message(self, format: str, *args: Any) -> None:
            return

    return CallbackHandler
