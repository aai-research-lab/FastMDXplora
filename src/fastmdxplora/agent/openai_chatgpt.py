"""OpenAI's documented Sign in with ChatGPT flow for this local open app.

This is the public-client OAuth flow for eligible open-source apps, not a
ChatGPT website session or a copied Codex credential. OAuth tokens stay in
the OS credential vault; the dashboard only receives account labels and
connection status. Inference uses the documented public Responses API route.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import http.server
import json
import os
import secrets
import socketserver
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from contextlib import contextmanager
from typing import Any

from fastmdxplora.refusals import CodedError
from fastmdxplora.user_dir import user_config_dir

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
RESOURCE = "https://api.openai.com/v1"
MODEL_LIST_URL = f"{RESOURCE}/models"
RESPONSES_URL = f"{RESOURCE}/responses"
CLIENT_NAME = "FastMDXplora"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
_SERVICE = "FastMDXplora ChatGPT sign-in"
_lock = threading.RLock()
_refresh_lock = threading.RLock()
_store_lock = threading.RLock()
_auth_lock = threading.RLock()
_pending: dict[str, dict[str, Any]] = {}
_pending_note = ""
_UNUSABLE_GRANTS = {
    "invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
    "refresh_token_invalidated", "refresh_token_reused",
}


class SignInError(CodedError, RuntimeError):
    """A safe-to-show authentication failure without provider credentials."""

    default_code = "environment.credentials.sign_in_failed"


class CredentialStoreError(SignInError):
    """A credential-vault failure, distinct from a rejected provider response."""

    default_code = "environment.credentials.secure_store_unavailable"


class InvalidGrant(SignInError):
    """An OAuth grant was revoked or consumed and must be removed locally."""

    default_code = "environment.credentials.grant_invalid"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _vault():
    from fastmdxplora.agent.models import _secure_keyring

    return _secure_keyring()


def _vault_user() -> str:
    return hashlib.sha256(os.path.normcase(str(user_config_dir().resolve())).encode()).hexdigest()


def _chunk_keys(raw: str | None) -> list[str]:
    record = json.loads(raw) if raw else {}
    chunks = record.get("vault_chunks") if isinstance(record, dict) else None
    if chunks is None:
        return []
    generation, count = chunks.get("generation"), chunks.get("count")
    if (not isinstance(generation, str) or len(generation) != 32
            or any(c not in "0123456789abcdef" for c in generation)
            or not isinstance(count, int) or not 1 <= count <= 4096):
        raise CredentialStoreError("The ChatGPT credential-vault manifest is invalid.")
    return [f"{_SERVICE}:{_vault_user()}:{generation}:{index}" for index in range(count)]


def _read_store() -> dict[str, Any]:
    vault = _vault()
    if vault is None:
        return {"host_id": "", "active_client_id": "", "accounts": []}
    try:
        with _process_lock(purpose="store"):
            raw = vault.get_password(_SERVICE, _vault_user())
            keys = _chunk_keys(raw)
            if keys:
                parts = [vault.get_password(key, _vault_user()) for key in keys]
                if any(part is None for part in parts):
                    raise CredentialStoreError("The ChatGPT credential-vault record is incomplete.")
                raw = "".join(parts)
            record = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001 - do not expose vault data or backend errors
        raise CredentialStoreError(
            "The OS credential vault could not read the ChatGPT sign-in. "
            "Unlock the vault and try again."
        ) from None
    if not isinstance(record, dict):
        record = {}
    accounts = record.get("accounts")
    return {
        "host_id": str(record.get("host_id") or ""),
        "active_client_id": str(record.get("active_client_id") or ""),
        "pending_client_id": str(record.get("pending_client_id") or ""),
        "auth_epoch": int(record.get("auth_epoch") or 0),
        "pending_operation": str(record.get("pending_operation") or ""),
        "accounts": [item for item in accounts if isinstance(item, dict)]
        if isinstance(accounts, list) else [],
    }


def _write_store(record: dict[str, Any]) -> None:
    vault = _vault()
    if vault is None:
        raise CredentialStoreError(
            "No supported OS credential vault is available. Use an OpenAI API key instead.")
    written = []
    committed = False
    try:
        with _process_lock(purpose="store"):
            user = _vault_user()
            previous = _chunk_keys(vault.get_password(_SERVICE, user))
            # ASCII JSON gives a predictable UTF-16 size on Windows: each
            # 1000-character entry stays below its 2560-byte credential limit.
            raw = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
            parts = [raw[i:i + 1000] for i in range(0, len(raw), 1000)]
            manifest = json.dumps({"vault_chunks": {
                "generation": uuid.uuid4().hex, "count": len(parts)}})
            for key, part in zip(_chunk_keys(manifest), parts):
                vault.set_password(key, user, part)
                written.append(key)
            # Publish only after every part exists. Readers share this lock,
            # so an old manifest cannot race deletion of its previous parts.
            vault.set_password(_SERVICE, user, manifest)
            committed = True
            for key in previous:
                vault.delete_password(key, user)
    except Exception:  # noqa: BLE001 - do not expose credential data or vault errors
        if not committed:
            for key in written:
                try:
                    vault.delete_password(key, _vault_user())
                except Exception:  # noqa: BLE001 - best effort after a failed write
                    pass
        raise CredentialStoreError(
            "The OS credential vault could not save or finish updating the ChatGPT sign-in. "
            "Unlock the vault and try again."
        ) from None


def _host_id(record: dict[str, Any]) -> str:
    if record.get("host_id"):
        return str(record["host_id"])
    record["host_id"] = f"urn:uuid:{uuid.uuid4()}"
    return str(record["host_id"])


def _account(record: dict[str, Any], client_id: str) -> dict[str, Any] | None:
    return next((item for item in record["accounts"]
                 if str(item.get("client_id") or "") == client_id), None)


def account_choices(record: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    store = record if record is not None else _read_store()
    return [{
        "client_id": str(item.get("client_id") or ""),
        "email": str(item.get("email") or ""),
        "subject_hint": str(item.get("subject") or "")[-8:],
        "connected": bool(item.get("access_token") and PLAN_SCOPE in item.get("scopes", [])),
        "active": str(item.get("client_id") or "") == store.get("active_client_id"),
    } for item in store["accounts"]]


def _https_context() -> ssl.SSLContext:
    """Use Windows chain verification without changing global TLS behavior."""
    if sys.platform == "win32":
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return ssl.create_default_context()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Credentials and metadata stay at the exact documented endpoint.
        return None


def _open_https(request: urllib.request.Request, *, timeout: float):
    parsed = urllib.parse.urlsplit(request.full_url)
    if (parsed.scheme != "https" or parsed.hostname not in {"auth.openai.com", "api.openai.com"}
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443)):
        raise SignInError("OpenAI returned an unexpected HTTPS endpoint.")
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_https_context()), _NoRedirect())
    return opener.open(request, timeout=timeout)


def _transport_message(error: BaseException) -> str:
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return (
            "OpenAI's HTTPS certificate could not be verified. Check the system date "
            "and Windows certificate updates or your network's certificate settings, "
            "then start sign-in again."
        )
    if isinstance(reason, TimeoutError):
        return "OpenAI sign-in timed out. Check the connection and start sign-in again."
    return "OpenAI sign-in could not be reached. Check the connection and try again."


def _exchange(form: dict[str, str], *, timeout: float = 20.0) -> dict[str, Any]:
    data = urllib.parse.urlencode(form).encode("ascii")
    request = urllib.request.Request(
        TOKEN_URL, data=data, headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST")
    try:
        with _open_https(request, timeout=timeout) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Do not let an OAuth response (which may echo parameters) reach UI or logs.
        try:
            error = json.loads(exc.read()).get("error", "")
            code = error.get("code", "") if isinstance(error, dict) else error
        except (ValueError, UnicodeError, AttributeError):
            code = ""
        if isinstance(code, str) and code in _UNUSABLE_GRANTS:
            raise InvalidGrant(
                "This sign-in code or refresh token expired. Start sign-in again."
            ) from None
        raise SignInError(f"OpenAI sign-in returned HTTP {exc.code}. Try again.") from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise SignInError(_transport_message(exc)) from None
    except (UnicodeError, ValueError):
        raise SignInError("OpenAI sign-in returned an unreadable response. Try again.") from None
    if not isinstance(answer, dict):
        raise SignInError("OpenAI sign-in returned an unreadable response. Try again.")
    return answer


def _fetch_json(url: str, *, timeout: float = 15.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"accept": "application/json"})
    try:
        with _open_https(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise SignInError(_transport_message(exc)) from None
    except (UnicodeError, ValueError):
        raise SignInError("OpenAI authentication metadata was not valid JSON.") from None
    if not isinstance(value, dict):
        raise SignInError("OpenAI authentication metadata was not valid JSON.")
    return value


def _verify_id_token(token: str, *, client_id: str, nonce: str,
                     timeout: float = 15.0) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise SignInError("OpenAI returned an invalid identity token.")
    try:
        header = json.loads(_unb64url(parts[0]))
        claims = json.loads(_unb64url(parts[1]))
        signature = _unb64url(parts[2])
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise SignInError("OpenAI returned an invalid identity token.") from None
    if not isinstance(header, dict) or not isinstance(claims, dict) or header.get("alg") != "RS256":
        raise SignInError("OpenAI returned an unsupported identity-token signature.")
    discovery = _fetch_json(DISCOVERY_URL, timeout=timeout)
    if discovery.get("issuer") != ISSUER:
        raise SignInError("OpenAI identity metadata has an unexpected issuer.")
    jwks_url = str(discovery.get("jwks_uri") or "")
    if not jwks_url.startswith(ISSUER + "/"):
        raise SignInError("OpenAI identity metadata has an unexpected signing-key URL.")
    keys = _fetch_json(jwks_url, timeout=timeout).get("keys")
    jwk = next((item for item in keys if isinstance(item, dict)
                and item.get("kid") == header.get("kid") and item.get("kty") == "RSA"
                and item.get("use", "sig") == "sig" and item.get("alg", "RS256") == "RS256"),
               None) if isinstance(keys, list) else None
    if jwk is None:
        raise SignInError("OpenAI's identity signing key was not recognized.")
    try:
        modulus = int.from_bytes(_unb64url(str(jwk["n"])), "big")
        exponent = int.from_bytes(_unb64url(str(jwk["e"])), "big")
        size = (modulus.bit_length() + 7) // 8
        signature_number = int.from_bytes(signature, "big")
        if len(signature) != size or signature_number >= modulus:
            raise SignInError("OpenAI's identity-token signature did not validate.")
        encoded = pow(signature_number, exponent, modulus).to_bytes(size, "big")
    except (KeyError, ValueError, OverflowError):
        raise SignInError("OpenAI's identity signing key was malformed.") from None
    digest = hashlib.sha256(f"{parts[0]}.{parts[1]}".encode("ascii")).digest()
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + digest
    padding_len = size - len(digest_info) - 3
    expected = b"\x00\x01" + b"\xff" * padding_len + b"\x00" + digest_info
    if padding_len < 8 or not hmac.compare_digest(encoded, expected):
        raise SignInError("OpenAI's identity-token signature did not validate.")

    now = int(time.time())
    audience = claims.get("aud")
    audiences = [audience] if isinstance(audience, str) else audience
    try:
        expiry = int(claims.get("exp") or 0)
        not_before = int(claims.get("nbf") or 0)
    except (TypeError, ValueError, OverflowError):
        raise SignInError("OpenAI returned invalid identity-token dates.") from None
    if (claims.get("iss") != ISSUER or not isinstance(audiences, list)
            or client_id not in audiences or expiry <= now or not_before > now + 60
            or not hmac.compare_digest(str(claims.get("nonce") or ""), nonce)
            or not claims.get("sub")):
        raise SignInError("OpenAI's identity, audience, expiry, or nonce did not validate.")
    return claims


def _require_attempt(store: dict[str, Any], attempt: dict[str, Any]) -> None:
    if (store.get("pending_operation") != attempt["operation_id"]
            or store.get("auth_epoch") != attempt["auth_epoch"]):
        raise SignInError("This ChatGPT sign-in was canceled or superseded. Start it again.")


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server_version = "FastMDXploraCallback"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        global _pending_note
        parsed = urllib.parse.urlsplit(self.path)
        expected_host = f"127.0.0.1:{self.server.server_address[1]}"
        if (parsed.scheme or parsed.netloc or parsed.fragment
                or self.headers.get_all("Host") != [expected_host]):
            self._answer(400, "This is not the expected loopback callback address.")
            return
        if parsed.path != "/auth/callback":
            self._answer(404, "This is not the sign-in callback.")
            return
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if any(len(query.get(key, [])) > 1 for key in ("state", "code", "client_id", "error")):
            self._answer(400, "This sign-in callback contains repeated parameters.")
            return
        state = (query.get("state") or [""])[0]
        with _lock:
            attempt = _pending.get(state)
            if attempt is not None and attempt["server"] is not self.server:
                attempt = None
            processing = attempt is not None and attempt.get("processing")
            if attempt is not None and not processing:
                attempt["processing"] = True
        if processing:
            self._answer(409, "This sign-in callback is already being processed.")
            return
        if attempt is None:
            self._answer(
                400,
                (
                    "This sign-in callback is expired or did not match. Return to "
                    "FastMDXplora and try again."
                ),
            )
            return
        try:
            error = (query.get("error") or [""])[0]
            if error:
                if error == "access_denied":
                    raise SignInError("Sign-in was cancelled or plan use was not authorized.")
                raise SignInError("OpenAI sign-in was not completed. Try again.")
            code = (query.get("code") or [""])[0]
            callback_client = (query.get("client_id") or [""])[0]
            expected_client = str(attempt.get("client_id") or "")
            if not code:
                raise SignInError("The sign-in callback did not include an authorization code.")
            if expected_client:
                if callback_client and callback_client != expected_client:
                    raise SignInError(
                        "The sign-in callback returned a different account registration."
                    )
                client_id = expected_client
            else:
                if not callback_client or callback_client == "dynamic_agent_client":
                    raise SignInError(
                        "OpenAI did not return the newly registered client ID. Try again."
                    )
                client_id = callback_client
                with _lock, _process_lock(purpose="auth"):
                    store = _read_store()
                    _require_attempt(store, attempt)
                    store["pending_client_id"] = client_id
                    _write_store(store)
            response = _exchange({
                "grant_type": "authorization_code", "client_id": client_id,
                "code": code, "code_verifier": attempt["verifier"],
                "redirect_uri": attempt["redirect_uri"], "resource": RESOURCE,
            })
            tokens = _validate_tokens(response, client_id=client_id,
                                      nonce=attempt["nonce"])
            with _lock, _process_lock(purpose="auth"):
                store = _read_store()
                _require_attempt(store, attempt)
                if _pending.get(state) is not attempt:
                    raise SignInError("This ChatGPT sign-in was canceled. Start it again.")
                existing = _account(store, client_id)
                if existing is not None and str(existing.get("subject") or "") != tokens["subject"]:
                    raise SignInError(
                        "This client ID belongs to another ChatGPT account. Its saved "
                        "credentials were kept.")
                tokens.update({"client_id": client_id, "issuer": ISSUER,
                               "ext_agent_host_id": attempt["host_id"],
                               "session_id": uuid.uuid4().hex})
                if existing is not None:
                    store["accounts"].remove(existing)
                store["accounts"].append(tokens)
                store.update(active_client_id=client_id, host_id=attempt["host_id"],
                             pending_operation="", auth_epoch=store["auth_epoch"] + 1)
                if store.get("pending_client_id") == client_id:
                    store.pop("pending_client_id", None)
                _write_store(store)
                _pending_note = (
                    "ChatGPT sign-in completed; test model access before saving this route."
                    if PLAN_SCOPE in tokens["scopes"]
                    else "Signed in, but ChatGPT plan use was not authorized. "
                    "Choose Continue with ChatGPT and approve plan use, or use an API key.")
            status = 200
            message = "ChatGPT sign-in is complete. You can return to FastMDXplora."
        except SignInError as exc:
            _pending_note = str(exc)
            status, message = 400, str(exc)
        except Exception:  # noqa: BLE001 - callback failures must not leak protocol data
            _pending_note = "ChatGPT sign-in failed while validating the response. Try again."
            status, message = 400, _pending_note
        finally:
            try:
                _finish_attempt(state)
            except SignInError as exc:
                _pending_note = str(exc)
                status, message = 400, str(exc)
        # Finish the intent before the browser can observe the response. One
        # cleanup path also covers a disconnected browser or a failed exchange.
        self._answer(status, message)

    def _answer(self, status: int, message: str) -> None:
        scrub_callback = (
            "<script>history.replaceState(null, '', '/auth/callback');</script>"
            if urllib.parse.urlsplit(self.path).path == "/auth/callback" else ""
        )
        body = ("<!doctype html><meta charset=utf-8><title>FastMDXplora sign-in</title>"
                f"{scrub_callback}<p>{html.escape(message)}</p>").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _validate_tokens(response: dict[str, Any], *, client_id: str,
                     nonce: str) -> dict[str, Any]:
    access = str(response.get("access_token") or "")
    refresh = str(response.get("refresh_token") or "")
    identity = str(response.get("id_token") or "")
    if not access or not refresh or not identity:
        raise SignInError("OpenAI did not return the tokens needed for a renewable sign-in.")
    claims = _verify_id_token(identity, client_id=client_id, nonce=nonce)
    scope_value = response.get("scope")
    scopes = sorted(set(str(scope_value or "").split()))
    try:
        expires_in = int(response.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    if expires_in <= 0:
        raise SignInError("OpenAI did not return a valid token expiry. Sign in again.")
    token_type = str(response.get("token_type") or "Bearer")
    if token_type.casefold() != "bearer":
        raise SignInError("OpenAI returned an unsupported token type. Sign in again.")
    return {
        "subject": str(claims["sub"]), "email": str(claims.get("email") or ""),
        "id_token": identity, "access_token": access, "refresh_token": refresh,
        "token_type": token_type,
        "expires_at": int(time.time()) + expires_in, "scopes": scopes,
    }


def _stop_callback_server(server: Any) -> None:
    server.shutdown()
    server.server_close()


def _finish_attempt(state: str) -> bool:
    """Invalidate this intent before closing its local timer and listener."""
    attempt = None
    try:
        with _lock:
            attempt = _pending.pop(state, None)
            if attempt is None:
                return False
            with _process_lock(purpose="auth"):
                store = _read_store()
                if store.get("pending_operation") == attempt["operation_id"]:
                    store["pending_operation"] = ""
                    store["auth_epoch"] = store.get("auth_epoch", 0) + 1
                    _write_store(store)
    finally:
        if attempt is not None:
            attempt["timer"].cancel()
            threading.Thread(target=_stop_callback_server, args=(attempt["server"],),
                             daemon=True).start()
    return True


class _LoopbackServer(http.server.ThreadingHTTPServer):
    """Bind the numeric callback address without a blocking reverse-DNS lookup."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def connect(*, client_id: str = "", add_account: bool = False) -> dict[str, Any]:
    """Start a documented loopback public-client OAuth attempt in a browser."""
    global _pending_note
    if _vault() is None:
        return {
            "ok": False,
            "message": (
                "A supported OS credential vault is required for ChatGPT sign-in. Use"
                " an OpenAI API key instead."
            ),
        }
    with _lock:
        if _pending:
            return {"ok": False, "message": "A ChatGPT sign-in is already waiting in the browser."}
    with _process_lock(purpose="auth"):
        store = _read_store()
        starting_epoch = store.get("auth_epoch", 0)
    selected = _account(store, client_id) if client_id else None
    if client_id and selected is None:
        return {"ok": False, "message": "Select a saved ChatGPT account, then try again."}
    if add_account:
        selected = None
    pending_client_id = "" if add_account else str(store.get("pending_client_id") or "")
    state = _b64url(secrets.token_bytes(32))
    nonce = _b64url(secrets.token_bytes(32))
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    try:
        server = _LoopbackServer(("127.0.0.1", 0), _CallbackHandler)
    except OSError:
        return {
            "ok": False,
            "message": (
                "The local sign-in callback could not be started. Close another "
                "dashboard and retry."
            ),
        }
    server.daemon_threads = True
    port = int(server.server_address[1])
    redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
    operation_id = uuid.uuid4().hex
    try:
        with _lock, _process_lock(purpose="auth"):
            store = _read_store()
            if store.get("auth_epoch", 0) != starting_epoch or _pending:
                raise SignInError("ChatGPT sign-in changed while starting. Try again.")
            host_id = _host_id(store)
            store["auth_epoch"] = starting_epoch + 1
            store["pending_operation"] = operation_id
            _write_store(store)
    except SignInError as exc:
        server.server_close()
        return {"ok": False, "message": str(exc)}
    attempt = {
        "state": state, "nonce": nonce, "verifier": verifier,
        "redirect_uri": redirect_uri, "host_id": host_id,
        "client_id": (str(selected.get("client_id") or "") if selected else
                      pending_client_id),
        "server": server, "operation_id": operation_id,
        "auth_epoch": starting_epoch + 1,
    }
    params = {
        "client_id": attempt["client_id"] or "dynamic_agent_client",
        "ext_agent_host_id": host_id, "response_type": "code",
        "redirect_uri": redirect_uri, "scope": SCOPES, "resource": RESOURCE,
        "state": state, "nonce": nonce, "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    if not attempt["client_id"]:
        params["agent_name_hint"] = CLIENT_NAME
    else:
        if selected and selected.get("id_token"):
            params["id_token_hint"] = str(selected["id_token"])
        if selected and selected.get("email"):
            params["login_hint"] = str(selected["email"])
        if not selected or PLAN_SCOPE not in selected.get("scopes", []):
            # Official recovery guidance: ask for consent only after an earlier
            # valid sign-in did not grant direct plan use.
            params["prompt"] = "consent"
    authorize = AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    timer = threading.Timer(300.0, _expire_attempt, args=(state,))
    timer.daemon = True
    attempt["timer"] = timer
    try:
        with _lock, _process_lock(purpose="auth"):
            _require_attempt(_read_store(), attempt)
            _pending[state] = attempt
            _pending_note = ""
    except SignInError as exc:
        server.server_close()
        return {"ok": False, "message": str(exc)}
    threading.Thread(target=server.serve_forever, name="chatgpt-oauth-callback",
                     daemon=True).start()
    timer.start()
    try:
        opened = webbrowser.open(authorize, new=1, autoraise=True)
    except Exception:  # noqa: BLE001 - browser launch is platform-provided
        opened = False
    if not opened:
        _finish_attempt(state)
        return {
            "ok": False,
            "message": (
                "The system browser could not open. Check the default browser and try"
                " again."
            ),
        }
    return {
        "ok": True,
        "pending": True,
        "message": (
            "Continue with ChatGPT in the system browser. Approve the requested "
            "access or cancel there; this dashboard will update when the callback"
            " returns."
        ),
    }


def _expire_attempt(state: str) -> None:
    """Close an abandoned loopback listener and make a canceled attempt visible."""
    global _pending_note
    if _finish_attempt(state):
        _pending_note = "ChatGPT sign-in timed out or was closed. Start it again when ready."


@contextmanager
def _process_lock(*, purpose: str = "refresh"):
    """Lock order: local attempts -> refresh -> auth -> vault store.

    Auth transactions never wait for refresh or perform network I/O. Store
    transactions only protect the bounded credential entries and manifest.
    """
    lock = {"refresh": _refresh_lock, "auth": _auth_lock, "store": _store_lock}[purpose]
    path = user_config_dir() / f"openai-chatgpt-{purpose}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock, path.open("a+b") as handle:
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt

                # Windows permits a byte lock beyond EOF. Reading or initializing
                # this byte before locking races with another refresh process.
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            acquired = True
            yield
        finally:
            if acquired:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)



def _active_session(store: dict[str, Any]) -> dict[str, Any]:
    active = _account(store, str(store.get("active_client_id") or ""))
    if active is None or not active.get("refresh_token"):
        raise SignInError("No active ChatGPT sign-in. Continue with ChatGPT in Agent Settings.")
    if PLAN_SCOPE not in active.get("scopes", []):
        raise SignInError(
            "ChatGPT plan use is not authorized for this account. Continue with ChatGPT "
            "in Agent Settings and approve plan use, or choose API-key authentication.")
    if str(active.get("token_type") or "Bearer").casefold() != "bearer":
        raise SignInError("The saved ChatGPT sign-in does not use bearer tokens. Sign in again.")
    return active


def _clear_tokens(account: dict[str, Any]) -> None:
    for key in ("access_token", "refresh_token", "id_token", "token_type", "expires_at", "scopes"):
        account.pop(key, None)


def _refresh_target(store: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    active = _account(store, before["client_id"])
    if (active is None or not active.get("refresh_token")
            or active.get("refresh_token") != before.get("refresh_token")
            or active.get("session_id") != before.get("session_id")):
        raise SignInError(
            "ChatGPT sign-in changed during refresh. Retry with the selected account.")
    return active


def access_token() -> str:
    """Refresh one session without restoring old credentials or account selection."""
    with _process_lock(purpose="auth"):
        active = _active_session(_read_store())
        if int(active.get("expires_at") or 0) > int(time.time()) + 60:
            return str(active["access_token"])
    with _process_lock():
        with _process_lock(purpose="auth"):
            before = _active_session(_read_store())
            if int(before.get("expires_at") or 0) > int(time.time()) + 60:
                return str(before["access_token"])
        try:
            response = _exchange({
                "grant_type": "refresh_token", "client_id": before["client_id"],
                "refresh_token": before["refresh_token"], "resource": RESOURCE,
            })
        except InvalidGrant:
            with _process_lock(purpose="auth"):
                store = _read_store()
                active = _refresh_target(store, before)
                _clear_tokens(active)
                if store["active_client_id"] == before["client_id"]:
                    store["active_client_id"] = ""
                _write_store(store)
            raise
        with _process_lock(purpose="auth"):
            store = _read_store()
            active = _refresh_target(store, before)
            access = str(response.get("access_token") or "")
            if not access:
                raise SignInError("OpenAI token refresh returned no access token. Sign in again.")
            scopes = sorted(
                set(str(response.get("scope") or " ".join(active.get("scopes", []))).split()))
            try:
                expires_in = int(response.get("expires_in") or 0)
            except (TypeError, ValueError):
                expires_in = 0
            token_type = str(response.get("token_type") or active.get("token_type") or "Bearer")
            selected = store["active_client_id"] == before["client_id"]
            if expires_in <= 0 or token_type.casefold() != "bearer":
                _clear_tokens(active)
                if selected:
                    store["active_client_id"] = ""
                _write_store(store)
                raise SignInError("OpenAI returned invalid refreshed credentials. Sign in again.")
            active.update({
                "access_token": access,
                "refresh_token": str(response.get("refresh_token") or active["refresh_token"]),
                "expires_at": int(time.time()) + expires_in,
                "scopes": scopes, "token_type": token_type,
            })
            if PLAN_SCOPE not in scopes and selected:
                store["active_client_id"] = ""
            # Merge only this unchanged session into the latest store. Keep a
            # rotating token for an inactive account without selecting it again.
            _write_store(store)
            if PLAN_SCOPE not in scopes:
                raise SignInError(
                    "OpenAI refresh no longer includes ChatGPT plan permission. Sign in again.")
            if not selected:
                raise SignInError("The active ChatGPT account changed during refresh. Retry.")
            return access


def status() -> dict[str, Any]:
    if _vault() is None:
        return {
            "available": False,
            "connected": False,
            "message": (
                "A supported OS credential vault is required for ChatGPT sign-in. Use"
                " an OpenAI API key instead."
            ),
            "accounts": [],
            "active_client_id": "",
        }
    store = _read_store()
    active = _account(store, str(store.get("active_client_id") or ""))
    with _lock:
        pending = any(attempt.get("operation_id") == store.get("pending_operation")
                      and attempt.get("auth_epoch") == store.get("auth_epoch")
                      for attempt in _pending.values())
    connected = None if pending else False
    if pending:
        message = "Waiting for ChatGPT sign-in in the system browser."
    elif active and PLAN_SCOPE in active.get("scopes", []) and active.get("refresh_token"):
        try:
            access_token()
            connected = True
            message = (f"Signed in to ChatGPT as {active.get('email') or 'the selected account'}. "
                       "Test model access before using this route.")
        except SignInError as exc:
            message = str(exc)
        store = _read_store()
        current = _account(store, str(store.get("active_client_id") or ""))
        if connected is True and (current is None or not current.get("refresh_token")
                                  or PLAN_SCOPE not in current.get("scopes", [])):
            connected = False
            message = "No active ChatGPT plan sign-in. Continue with ChatGPT to sign in."
        elif connected is True and (current.get("client_id") != active.get("client_id")
                                     or current.get("session_id") != active.get("session_id")):
            connected = None
            message = "The ChatGPT account changed while checking it. Refresh its status."
    elif active:
        message = (f"Signed in as {active.get('email') or 'the selected account'}, "
                   "but ChatGPT plan use was not authorized. Continue with ChatGPT to approve it.")
    else:
        message = _pending_note or (
            (
                "A ChatGPT client registration is waiting for sign-in. Continue with "
                "ChatGPT to finish it."
            )
            if store.get("pending_client_id")
            else "No ChatGPT account is connected. Continue with ChatGPT to sign in."
        )
    return {"available": True, "connected": connected, "pending": pending, "message": message,
            "accounts": account_choices(store),
            "active_client_id": store.get("active_client_id", "")}


def list_models(*, timeout: float = 12.0) -> list[dict[str, str]]:
    """Return account-visible SIWC models using OpenAI's documented catalog."""
    token = access_token()
    request = urllib.request.Request(
        MODEL_LIST_URL, headers={"Authorization": f"Bearer {token}",
                                 "accept": "application/json"})
    try:
        with _open_https(request, timeout=timeout) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise SignInError(
                "OpenAI rejected the active ChatGPT sign-in. Reconnect it in Agent Settings."
            ) from None
        if exc.code == 403:
            raise SignInError(
                "OpenAI policy or account permissions prevent listing models."
            ) from None
        if exc.code == 503:
            raise SignInError(
                "ChatGPT plan routing is temporarily unavailable. Try again shortly."
            ) from None
        raise SignInError(f"OpenAI model listing returned HTTP {exc.code}.") from None
    except (urllib.error.URLError, OSError, TimeoutError, UnicodeError, ValueError):
        raise SignInError(
            "OpenAI models could not be reached. Check the connection and try again."
        ) from None
    rows = answer.get("models") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        raise SignInError("OpenAI returned an unrecognized ChatGPT model catalog.")
    return [{"id": str(row["slug"]), "label": str(row.get("display_name") or row["slug"])}
            for row in rows if isinstance(row, dict) and row.get("slug")
            and row.get("visibility") == "list"]


def disconnect(*, client_id: str = "") -> dict[str, Any]:
    """Clear local access atomically, then attempt to revoke the captured grant."""
    global _pending_note
    with _lock:
        canceled = bool(_pending)
        for state in list(_pending):
            _finish_attempt(state)
        if canceled:
            _pending_note = "ChatGPT sign-in canceled."
    with _process_lock(purpose="auth"):
        store = _read_store()
        selected_id = client_id or str(store.get("active_client_id") or "")
        account = _account(store, selected_id)
        canceled = canceled or bool(store.get("pending_operation"))
        store["pending_operation"] = ""
        store["auth_epoch"] = store.get("auth_epoch", 0) + 1
        refresh = str(account.get("refresh_token") or "") if account else ""
        if account is not None:
            _clear_tokens(account)
        if store.get("active_client_id") == selected_id:
            store["active_client_id"] = ""
        _write_store(store)
    if account is None:
        return {"ok": True, "remote_revoked": None,
                "message": "ChatGPT sign-in canceled." if canceled
                else "No saved ChatGPT sign-in was found."}
    remote_revoked = None
    remote_error = ""
    if refresh:
        try:
            discovery = _fetch_json(DISCOVERY_URL)
            endpoint = str(discovery.get("revocation_endpoint") or "")
            if not endpoint.startswith(ISSUER + "/"):
                raise SignInError("OpenAI revocation metadata was not valid.")
            data = urllib.parse.urlencode({
                "token": refresh, "token_type_hint": "refresh_token",
                "client_id": selected_id,
            }).encode("ascii")
            for attempt in range(3):
                request = urllib.request.Request(endpoint, data=data,
                    headers={"content-type": "application/x-www-form-urlencoded"}, method="POST")
                try:
                    with _open_https(request, timeout=15.0) as response:
                        remote_revoked = response.status == 200
                    if remote_revoked:
                        break
                except urllib.error.HTTPError as exc:
                    if exc.code < 500 or attempt == 2:
                        remote_error = f"OpenAI returned HTTP {exc.code} during remote sign-out."
                        break
                except (urllib.error.URLError, OSError, TimeoutError):
                    if attempt == 2:
                        remote_error = "OpenAI could not be reached for remote sign-out."
                        break
                time.sleep(attempt + 1)
        except SignInError as exc:
            remote_error = str(exc)
    message = "Disconnected from ChatGPT. The account registration is kept for later sign-in."
    if refresh and not remote_revoked:
        message += " The local tokens were cleared, but remote revocation was not confirmed. "
        message += remote_error or "Disconnect the app in ChatGPT Settings if needed."
    elif not refresh:
        message += " No local renewable session was available to revoke remotely."
    return {"ok": True, "remote_revoked": remote_revoked,
            "message": message}


def activate(*, client_id: str) -> dict[str, Any]:
    """Select an account without letting a delayed failure undo a newer choice."""
    with _process_lock(purpose="auth"):
        store = _read_store()
        account = _account(store, client_id)
        if account is None:
            return {"ok": False, "message": "Select a saved ChatGPT account."}
        if PLAN_SCOPE not in account.get("scopes", []) or not account.get("refresh_token"):
            return {"ok": False, "message":
                    "This account has no active ChatGPT plan grant. Continue with ChatGPT first."}
        previous_id = str(store.get("active_client_id") or "")
        epoch = store.get("auth_epoch", 0) + 1
        store.update(active_client_id=client_id, auth_epoch=epoch, pending_operation="")
        _write_store(store)
    with _lock:
        for state in list(_pending):
            _finish_attempt(state)
    try:
        access_token()
    except SignInError as exc:
        with _process_lock(purpose="auth"):
            store = _read_store()
            if store.get("auth_epoch") == epoch and store.get("active_client_id") == client_id:
                previous = _account(store, previous_id)
                store["active_client_id"] = (
                    previous_id if previous and previous.get("refresh_token") else "")
                _write_store(store)
        return {"ok": False, "message": str(exc)}
    with _process_lock(purpose="auth"):
        store = _read_store()
        if store.get("auth_epoch") != epoch or store.get("active_client_id") != client_id:
            return {"ok": False, "message": "ChatGPT account selection changed. Retry."}
    return {"ok": True, "message": "The selected ChatGPT account is active."}
