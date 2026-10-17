"""Turning a stored provider choice into the function the agent needs.

:func:`fastmdxplora.agent.propose_config` takes a callable: prompt in, text
out. That is the whole interface, and it stays that way -- nothing in this
package constructs a client, and no provider is baked in.

What this module adds is one particular callable, built from a choice
somebody made once. It exists because "write fifteen lines of HTTPS" is a
reasonable thing to ask of a developer and an unreasonable thing to ask of
somebody who wants to run a simulation.

Three rules, and the first is the one that matters.

**The key never enters a study.** Not the config, not the manifest, not a
log line, not an error message. Those files get shared, pasted into
issues, and committed; a key in one is a key on the internet. It lives in
the OS credential vault or the environment, and is read only in the local
request process.

**The environment wins.** ``ANTHROPIC_API_KEY`` and ``OPENAI_API_KEY`` are
checked before the vault, because that is how a cluster job and a CI
run get a key without anybody storing one, and because it is what people
already expect.

**Which AI model wrote a study belongs in the record.** The provider and
the AI model's name, never the key. Six months on, "why did this study pick
300 K" has a different answer depending on whether a frontier AI model or a 7B
running on a laptop proposed it, and that is provenance like any other.

Two providers are offered by name. A third option takes any
OpenAI-compatible base URL, which covers DeepSeek, vLLM, Ollama,
OpenRouter and most local servers -- they speak the same shape, so there
is nothing to add per provider.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any

from fastmdxplora.refusals import StudyError
from fastmdxplora.user_dir import user_config_dir

__all__ = [
    "list_models",
    "probe_connection",
    "default_model",
    "cache_for",
    "save_cache_for",
    "CACHE_FOR",
    "ModelChoice",
    "PROVIDERS",
    "model_path",
    "load_choice",
    "save_choice",
    "forget_api_key",
    "completion_for",
    "describe_choice",
]

#: What each provider needs. The differences between them are an endpoint,
#: an auth header and where the reply sits in the response -- a table, not
#: an integration.
PROVIDERS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "label": "Anthropic",
        "url": "https://api.anthropic.com/v1/messages",
        "default_model": "claude-sonnet-4-6",
        #: What to offer before a key is set, and if the provider cannot
        #: be reached. The real list comes from `list_models`, which asks
        #: the provider -- a list written here is stale the week after it
        #: is written, and an AI model released next month would be missing
        #: with nothing to explain why.
        #: One string, not a guessed list. `claude-opus-4-1` sat here and
        #: 404s -- a name invented from a pattern rather than read from the
        #: provider. Anything beyond the default comes from `list_models`,
        #: which asks; a wrong name offered in a dropdown is worse than no
        #: dropdown, because it looks chosen rather than typed.
        "models": ("claude-sonnet-4-6",),
        "models_url": "https://api.anthropic.com/v1/models",
        "env": "ANTHROPIC_API_KEY",
        "auth": "x-api-key",
    },
    "openai": {
        "label": "OpenAI",
        "url": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-5",
        "models": ("gpt-5",),
        "models_url": "https://api.openai.com/v1/models",
        "env": "OPENAI_API_KEY",
        "auth": "bearer",
    },
    "compatible": {
        # DeepSeek, vLLM, Ollama, OpenRouter, and anything else speaking
        # the OpenAI chat shape. One entry rather than one per vendor,
        # because a list of vendors goes stale and a protocol does not.
        "label": "Other (any OpenAI-compatible server)",
        #: Shown when this option is picked, because somebody choosing it
        #: knows they want DeepSeek or a local AI model and does not know the
        #: URL. Examples rather than a list of supported vendors: the
        #: protocol is what is supported, and a vendor list goes stale.
        "examples": (
            ("DeepSeek", "https://api.deepseek.com", "deepseek-chat"),
            ("OpenRouter", "https://openrouter.ai/api/v1",
             "anthropic/claude-sonnet-4-6"),
            ("Ollama (local)", "http://localhost:11434/v1", "llama3.1"),
            ("vLLM (local)", "http://localhost:8000/v1", "<your AI model>"),
        ),
        "url": "",
        "default_model": "",
        "env": "FASTMDX_MODEL_API_KEY",
        "auth": "bearer",
    },
}

AUTH_METHODS = {
    "anthropic": ("api_key", "anthropic_console"),
    "openai": ("api_key", "chatgpt_plan"),
    "compatible": ("api_key",),
}


@dataclass(frozen=True)
class ModelChoice:
    """Which AI model to ask, and where. Never the key."""

    provider: str
    model: str
    base_url: str = ""
    auth_method: str = "api_key"

    @property
    def url(self) -> str:
        if self.auth_method == "chatgpt_plan":
            from fastmdxplora.agent.openai_chatgpt import RESPONSES_URL

            return RESPONSES_URL
        if self.base_url:
            return self.base_url.rstrip("/") + "/chat/completions"
        return str(PROVIDERS[self.provider]["url"])

    @property
    def auth_style(self) -> str:
        if self.auth_method in {"anthropic_console", "chatgpt_plan"}:
            return "bearer"
        return str(PROVIDERS[self.provider]["auth"])

    def as_record(self) -> dict[str, str]:
        """What goes in a manifest. Provider and AI model, and that is all."""
        record = {"provider": self.provider, "model": self.model,
                  "auth_method": self.auth_method}
        if self.base_url:
            record["base_url"] = self.base_url
        return record

    def __str__(self) -> str:
        where = f" at {self.base_url}" if self.base_url else ""
        return f"{PROVIDERS[self.provider]['label']}, {self.model}{where}"


#: The family each provider's default belongs to, and its version read from
#: a name: the newest of it a provider offers is the default.
_FAMILIES: dict[str, re.Pattern[str]] = {
    # claude-sonnet-4-6, claude-sonnet-4-5-20250929, claude-sonnet-5
    "anthropic": re.compile(r"^claude-sonnet-(\d+)(?:-(\d{1,2})(?!\d))?(?:-\d{8})?$"),
    # gpt-5, gpt-5.1; not gpt-5-mini or gpt-4o, which are other families
    "openai": re.compile(r"^gpt-(\d+)(?:\.(\d+))?$"),
}


def default_model(provider: str, offered: Any = ()) -> str:
    """The AI model to offer first: the newest of the family FastMDXplora's
    written default belongs to among those the provider offers, or the
    written default where it offers none of them (no key yet, or offline).

    The default was written in (`claude-sonnet-4-6`, `gpt-5`) and stayed
    there as newer ones came out; a new person was offered last year's AI
    model with nothing to say there was another. The family is kept, not
    the newest of everything: a provider's newest may be its largest and
    dearest, or its smallest."""
    spec = PROVIDERS.get(provider) or {}
    written = str(spec.get("default_model") or "")
    family = _FAMILIES.get(provider)
    if family is None:
        return written
    best: tuple[tuple[int, int], int, str] | None = None
    for name in offered or ():
        found = family.match(str(name))
        if found is None:
            continue
        version = (int(found.group(1)), int(found.group(2) or 0))
        # Of one version, the shortest name: the alias, not a dated snapshot.
        ranked = (version, -len(str(name)), str(name))
        if best is None or ranked > best:
            best = ranked
    return best[2] if best is not None else written


#: How long the Agent's instructions are kept in the provider's cache
#: (Anthropic): "auto" is an hour on the GUI's Agent page, where a person
#: reads a plan before answering, and five minutes from the command line,
#: where a request is usually asked once.
CACHE_FOR = ("auto", "1h", "5m")


def cache_for(where: str = "command line", path: Path | None = None) -> str:
    """``"1h"`` or ``"5m"``: the setting stored with the choice, or for
    ``"auto"`` (and where none is stored), an hour on the page and five
    minutes anywhere else."""
    record = _settings_record(path)
    chosen = str(record.get("cache_for") or "auto")
    if chosen in ("1h", "5m"):
        return chosen
    return "1h" if where == "page" else "5m"


def save_cache_for(value: str, path: Path | None = None) -> None:
    """Store how long the instructions are kept beside the model choice."""
    _validate_cache_for(value)
    target = path or model_path()
    record = _settings_record(target)
    record["cache_for"] = value
    _write_settings(target, record)


def _validate_cache_for(value: str) -> None:
    if value not in CACHE_FOR:
        raise StudyError(f"The cache is kept for {', '.join(CACHE_FOR)}; not {value!r}.",
                         code="config.option.not_permitted", option="cache_for",
                         permitted=list(CACHE_FOR))


def _write_settings(target: Path, record: dict[str, Any]) -> None:
    """Replace settings atomically so readers never see an incomplete save."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=f".{target.name}-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, indent=2)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def model_path() -> Path:
    """Where the choice is stored. Outside any study, on purpose."""
    return user_config_dir() / "model.json"


def _settings_record(path: Path | None = None) -> dict[str, Any]:
    target = path or model_path()
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict):
        return {}
    # Remove legacy plaintext keys without migrating or returning their value.
    if "api_key" in record:
        record.pop("api_key", None)
        try:
            _write_settings(target, record)
        except OSError:  # pragma: no cover - read-only settings folder
            pass
    return record


def _credential_identity(choice: ModelChoice, path: Path | None = None) -> tuple[str, str]:
    target = os.path.normcase(str((path or model_path()).resolve()))
    identity = f"{target}\0{choice.provider}\0{choice.base_url}"
    return "FastMDXplora Agent", hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _secure_keyring():
    """Return an OS-backed keyring; reject plaintext and fail backends."""
    try:
        import keyring

        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001 - unavailable or unconfigured OS vault
        return None
    module = type(backend).__module__
    secure = (module.startswith("keyring.backends.Windows")
              or module.startswith("keyring.backends.macOS")
              or module.startswith("keyring.backends.SecretService")
              or module.startswith("keyring.backends.libsecret"))
    return keyring if secure else None


def _stored_api_key(choice: ModelChoice, path: Path | None = None) -> str:
    keyring = _secure_keyring()
    if keyring is None:
        return ""
    service, username = _credential_identity(choice, path)
    try:
        return str(keyring.get_password(service, username) or "")
    except Exception:  # noqa: BLE001 - vault locked or unavailable
        return ""


def _store_api_key(choice: ModelChoice, value: str, path: Path | None = None) -> None:
    keyring = _secure_keyring()
    if keyring is None:
        raise StudyError(
            "No supported OS credential vault is available. Set the provider's "
            "environment variable instead of saving an API key here.",
            code="environment.credentials.secure_store_unavailable",
            provider=choice.provider,
        )
    service, username = _credential_identity(choice, path)
    try:
        keyring.set_password(service, username, value)
    except Exception:  # noqa: BLE001 - vault denied writes or is locked
        raise StudyError(
            "The OS credential vault could not store this key. Set the provider's "
            "environment variable instead.",
            code="environment.credentials.secure_store_unavailable",
            provider=choice.provider,
        ) from None


def forget_api_key(choice: ModelChoice, path: Path | None = None) -> bool:
    """Remove a saved provider key without changing environment variables."""
    keyring = _secure_keyring()
    if keyring is None:
        raise StudyError(
            "The OS credential vault is unavailable. Unlock it and try disconnecting again.",
            code="environment.credentials.secure_store_unavailable",
        )
    service, username = _credential_identity(choice, path)
    try:
        if keyring.get_password(service, username) is None:
            return False
        keyring.delete_password(service, username)
    except Exception:  # noqa: BLE001 - vault errors must not expose credentials
        raise StudyError(
            "The saved API key could not be removed. Unlock the OS credential vault and retry.",
            code="environment.credentials.secure_store_unavailable",
        ) from None
    return True


def load_choice(path: Path | None = None) -> ModelChoice | None:
    """The stored choice, or ``None`` where nothing has been set."""
    record = _settings_record(path)
    provider = str(record.get("provider", ""))
    if provider not in PROVIDERS:
        return None
    auth_method = str(record.get("auth_method") or "api_key")
    if auth_method not in AUTH_METHODS[provider]:
        auth_method = "api_key"
    return ModelChoice(provider=provider,
                       model=str(record.get("model", "")),
                       base_url=str(record.get("base_url", "")),
                       auth_method=auth_method)


def save_choice(choice: ModelChoice, *, key: str = "",
                path: Path | None = None, cache: str | None = None) -> Path:
    """Store the choice and auth method; keep any API key in the OS vault."""
    if choice.auth_method not in AUTH_METHODS.get(choice.provider, ()):
        raise StudyError("That authentication method is not supported for this provider.",
                         code="config.option.not_permitted", option="auth_method")
    if cache is not None:
        _validate_cache_for(cache)
    if key:
        if choice.auth_method != "api_key":
            raise StudyError("An API key cannot be saved for this authentication method.",
                             code="config.option.not_permitted", option="api_key")
        _store_api_key(choice, key, path)
    target = path or model_path()
    record: dict[str, str] = dict(choice.as_record())
    if cache is not None:
        record["cache_for"] = cache
    _write_settings(target, record)
    return target


def _key_for(choice: ModelChoice, path: Path | None = None) -> str:
    """The key, environment first.

    Environment before vault so a cluster job, a CI run or a shared machine
    can supply one per session without anybody storing it, and so a stored
    key can be overridden for one run without editing anything.
    """
    if choice.auth_method == "anthropic_console":
        from fastmdxplora.agent.anthropic_cli import ConsoleSignInError, access_token
        from fastmdxplora.refusals import BackendUnavailable

        try:
            return access_token()
        except (ConsoleSignInError, BackendUnavailable) as exc:
            raise StudyError(str(exc), code=exc.code,
                             provider=choice.provider) from None
    if choice.auth_method == "chatgpt_plan":
        from fastmdxplora.agent.openai_chatgpt import SignInError, access_token

        try:
            return access_token()
        except SignInError as exc:
            raise StudyError(str(exc), code=exc.code,
                             provider=choice.provider) from None
    env_name = str(PROVIDERS[choice.provider]["env"])
    from_env = os.environ.get(env_name)
    if from_env:
        return from_env
    stored = _stored_api_key(choice, path)
    if stored:
        return stored
    raise StudyError(
        f"No API key for {PROVIDERS[choice.provider]['label']}. Set "
        f"{env_name} in the environment, or enter it in Agent Settings to "
        "save it in the OS credential vault. It is never written into settings files.",
        code="environment.credentials.absent",
        provider=choice.provider, environment_variable=env_name,
    )


def _auth_headers(choice: ModelChoice, key: str) -> dict[str, str]:
    headers = {}
    if key:
        if choice.auth_style == "x-api-key":
            headers["x-api-key"] = key
        else:
            headers["Authorization"] = f"Bearer {key}"
    if choice.provider == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
    return headers



def list_models(choice: ModelChoice | None = None, *,
                path: Path | None = None, timeout: float = 10.0) -> tuple:
    """The AI models this provider currently has, asked of the provider.

    A list written into this file is stale the week after it is written,
    and an AI model released next month would simply be missing with nothing to
    explain why. Both hosted providers publish one at ``/v1/models``, and
    an OpenAI-compatible server serves the same path relative to its base
    URL, so a local Ollama answers this too.

    Returns the fallback in `PROVIDERS` when there is no key yet, when the
    provider cannot be reached, or when the answer is not the shape this
    expects. Being offline should leave somebody with a usable dropdown
    rather than an empty one, and the panel's free-text option covers
    anything neither source knows.
    """
    import json
    import urllib.error
    import urllib.request

    chosen = choice or load_choice(path=path)
    if chosen is None:
        return ()
    spec = PROVIDERS.get(chosen.provider) or {}
    fallback = tuple(spec.get("models") or ())

    if chosen.auth_method == "chatgpt_plan":
        from fastmdxplora.agent.openai_chatgpt import SignInError
        from fastmdxplora.agent.openai_chatgpt import list_models as siwc_models

        try:
            return tuple(item["id"] for item in siwc_models(timeout=timeout))
        except SignInError:
            return fallback

    url = spec.get("models_url")
    if not url and chosen.base_url:
        url = chosen.base_url.rstrip("/") + "/models"
    if not url:
        return fallback

    try:
        key = _key_for(chosen, path)
    except StudyError:
        return fallback
    try:
        request = urllib.request.Request(url, headers=_auth_headers(chosen, key))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return fallback

    # Anthropic and OpenAI both answer {"data": [{"id": ...}, ...]}.
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return fallback
    found = tuple(str(r["id"]) for r in rows
                  if isinstance(r, dict) and r.get("id"))
    return found or fallback


def list_model_options(choice: ModelChoice, *, timeout: float = 10.0) -> list[dict[str, str]]:
    """Return supported model ids and display labels for the selected route."""
    if choice.auth_method == "chatgpt_plan":
        from fastmdxplora.agent.openai_chatgpt import list_models as siwc_models

        return siwc_models(timeout=timeout)
    return [{"id": model, "label": model} for model in
            list_models(choice, timeout=timeout)]


def probe_connection(choice: ModelChoice | None = None, *,
                     timeout: float = 12.0) -> dict[str, Any]:
    """Check the saved route with the provider without exposing credentials.

    Unlike :func:`list_models`, this method does not turn a failed request
    into a fallback list: Settings needs the actual response state to tell an
    expired key from a working connection.
    """
    import urllib.error
    import urllib.request

    settled = choice or load_choice()
    if settled is None:
        return {"connected": False, "message": "Save a provider and model first."}
    spec = PROVIDERS[settled.provider]
    if settled.auth_method == "chatgpt_plan":
        from fastmdxplora.agent.openai_chatgpt import SignInError
        from fastmdxplora.agent.openai_chatgpt import list_models as siwc_models

        try:
            options = siwc_models(timeout=timeout)
        except SignInError as exc:
            return {"connected": False, "message": str(exc), "code": exc.code}
        model_names = [item["id"] for item in options]
        if settled.model not in model_names:
            return {"connected": False,
                    "message": ("ChatGPT sign-in is valid, but the selected model is not "
                                "available to this account. Choose a model from the list."),
                    "models": model_names, "model_options": options}
        return {"connected": True,
                "message": f"ChatGPT plan route verified. {len(model_names)} available model(s).",
                "models": model_names, "model_options": options}
    url = spec.get("models_url")
    if not url and settled.base_url:
        url = settled.base_url.rstrip("/") + "/models"
    if not url:
        return {"connected": False, "message": "This provider has no model-list endpoint to test."}
    try:
        key = _key_for(settled)
    except StudyError as exc:
        return {"connected": False, "message": str(exc), "code": exc.code}

    try:
        request = urllib.request.Request(str(url), headers=_auth_headers(settled, key))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            message = ("The Anthropic Console login expired or is unavailable. Sign in again."
                       if settled.auth_method == "anthropic_console"
                       else "The API key was rejected. Check it and save the corrected key.")
        elif exc.code == 403:
            message = "The credential is valid but does not have access to this provider resource."
        elif exc.code == 429:
            message = "The provider rate limit or account quota was reached. Try again later."
        else:
            message = (
                f"The provider returned HTTP {exc.code}. Check the model access and try again."
            )
        return {"connected": False, "message": message}
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return {"connected": False,
                "message": "The provider could not be reached or returned an unreadable response."}

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {"connected": False, "message": "The provider response was not a model list."}
    model_names = [str(row["id"]) for row in rows
                   if isinstance(row, dict) and row.get("id")]
    if settled.model not in model_names:
        return {"connected": True,
                "message": ("Connected, but the selected model is not visible to this "
                            "credential. Choose one of the provider's listed models."),
                "models": model_names}
    return {
        "connected": True,
        "message": f"Connected. {len(model_names)} available model(s) reported by the provider.",
        "models": model_names,
    }


def completion_for(choice: ModelChoice | None = None, *,
                   path: Path | None = None, timeout: float = 120.0,
                   where: str = "command line"):
    """Build the prompt-in, text-out function the agent takes. ``where`` it
    is asked from (``"page"`` for the GUI's Agent page) decides how long
    the instructions are cached where the setting is ``auto``.

    One HTTPS request per call, through the standard library. No client
    library, because a package that should not gain a dependency should
    not gain one for this either, and because the difference between the
    providers is an endpoint, a header and a field name.
    """
    import urllib.error
    import urllib.request

    settled = choice or load_choice(path)
    if settled is None:
        raise StudyError(
            "No AI model has been chosen. Run `fastmdx agent model` to pick one. "
            "Nothing in FastMDXplora needs an AI model unless you ask for the "
            "agent, so this only comes up when you do.",
            code="environment.model.unset",
        )

    def complete(prompt: str, on_text: Callable[[str], None] | None = None) -> str:
        """The AI model's reply to ``prompt``. With ``on_text``, asked for as a
        stream and handed to ``on_text`` piece by piece as it is written;
        the whole reply is returned either way. An exception from
        ``on_text`` (the page that asked has gone) ends the request."""
        key = _key_for(settled, path)
        headers = _auth_headers(settled, key)
        if settled.provider == "anthropic":
            body: dict[str, Any] = {
                "model": settled.model, "max_tokens": 4000,
                "messages": [{"role": "user", "content": prompt}]}
        elif settled.auth_method == "chatgpt_plan":
            body = {}
        else:
            body = {"model": settled.model,
                    "messages": [{"role": "user", "content": prompt}]}
        headers["content-type"] = "application/json"
        if on_text is not None and settled.auth_method != "chatgpt_plan":
            body["stream"] = True

        from fastmdxplora.agent import turns

        if settled.auth_method == "chatgpt_plan":
            result = turns.take_turn(
                url=settled.url, headers=headers, shape="responses", model=settled.model,
                system="", messages=[{"role": "user", "text": prompt}], tools=[],
                label=str(PROVIDERS[settled.provider]["label"]), timeout=timeout,
                on_text=on_text)
            return result.text

        for attempt in range(turns.MOST_TRIES):
            request = urllib.request.Request(
                settled.url, method="POST",
                data=json.dumps(body).encode(), headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    if on_text is not None:
                        return _streamed(response, on_text, settled.url)
                    answer = json.loads(response.read())
                break
            except urllib.error.HTTPError as exc:
                # Asked again where the provider says to (a rate limit, an
                # overloaded service), after the wait it gives.
                if exc.code in turns.RETRIED and attempt < turns.MOST_TRIES - 1:
                    exc.read()
                    turns._sleep(turns._wait_for(exc, attempt))
                    continue
                # The provider's own reason, and never the request that carried
                # the key. An error message is the easiest place for a secret
                # to escape into a log.
                detail = turns.redacted(exc.read().decode("utf-8", "replace")[:400],
                                        [key])
                raise StudyError(
                    f"{PROVIDERS[settled.provider]['label']} refused the "
                    f"request ({exc.code}): {detail}",
                    code="environment.service.unusable_response",
                    url=settled.url,
                ) from None
            except urllib.error.URLError as exc:
                raise StudyError(
                    f"Could not reach {settled.url}: {exc.reason}",
                    code="environment.service.unreachable", url=settled.url,
                ) from None

        if "content" in answer:  # Anthropic
            return "".join(part.get("text", "")
                           for part in answer.get("content", []))
        choices = answer.get("choices") or []  # OpenAI shape
        if choices:
            return str(choices[0].get("message", {}).get("content", ""))
        raise StudyError(
            f"{settled.url} answered in a shape this does not recognise. "
            "An OpenAI-compatible server returns `choices[0].message."
            "content`; Anthropic returns `content[].text`.",
            code="environment.service.unusable_response", url=settled.url,
        )

    # Said on the function, so a caller can tell a completion that streams
    # from one that answers whole (a test's, or another's).
    complete.streams = True  # type: ignore[attr-defined]

    def turn(system: str, messages: list[dict[str, Any]], tools: list[Any], *,
             on_text: Callable[[str], None] | None = None,
             on_call: Callable[[str], None] | None = None) -> Any:
        """One turn with the tools declared to the provider
        (:mod:`fastmdxplora.agent.turns`). A server that turns tools away
        raises :class:`~fastmdxplora.agent.turns.NoToolCalling`, for the
        conversation to be asked in text; it is noted only by
        ``turned_away``, which the loop calls when that was its first turn."""
        from fastmdxplora.agent.turns import take_turn

        key = _key_for(settled, path)
        shape = ("blocks" if settled.provider == "anthropic" else
                 "responses" if settled.auth_method == "chatgpt_plan" else "chat")
        return take_turn(url=settled.url, headers=_auth_headers(settled, key), shape=shape,
                         model=settled.model, system=system, messages=messages,
                         tools=tools, label=str(PROVIDERS[settled.provider]["label"]),
                         timeout=timeout, on_text=on_text, on_call=on_call,
                         usage_in_stream=settled.provider == "openai"
                         and settled.auth_method == "api_key",
                         cache_for=cache_for(where, path))

    def turned_away() -> None:
        """The server turned tools away on a conversation's first turn:
        noted, so later conversations with it use the text protocol from
        the start rather than being refused once each."""
        _note_no_tool_calling(path, settled)

    # Only where this server has not turned tools away before.
    if _takes_tools(path):
        complete.turn = turn  # type: ignore[attr-defined]
        complete.turned_away = turned_away  # type: ignore[attr-defined]
    return complete


def _takes_tools(path: Path | None = None) -> bool:
    """False once the chosen server has turned tools away (`tool_use: false`
    for the saved provider, model, URL and authentication method."""
    record = _settings_record(path)
    if record.get("tool_use") is not False:
        return True
    said_of = record.get("tool_use_of")
    now = {"provider": str(record.get("provider", "")), "model": str(record.get("model", "")),
           "base_url": str(record.get("base_url", "")),
           "auth_method": str(record.get("auth_method") or "api_key")}
    return not isinstance(said_of, dict) or said_of != now


def _note_no_tool_calling(path: Path | None = None,
                          choice: ModelChoice | None = None) -> None:
    """Record that the chosen server takes no tools, and which server that
    was, keeping all settings. Not written where the file now holds another
    choice than the one that was asked."""
    target = path or model_path()
    record = _settings_record(target)
    if not record:
        return
    of = {"provider": str(record.get("provider", "")), "model": str(record.get("model", "")),
          "base_url": str(record.get("base_url", "")),
          "auth_method": str(record.get("auth_method") or "api_key")}
    if choice is not None and of != {"provider": choice.provider, "model": choice.model,
                                     "base_url": choice.base_url,
                                     "auth_method": choice.auth_method}:
        return
    record["tool_use"] = False
    record["tool_use_of"] = of
    try:
        _write_settings(target, record)
    except OSError:  # pragma: no cover - a read-only settings folder
        pass


def _streamed(response: Any, on_text: Callable[[str], None], url: str) -> str:
    """A reply read as server-sent events, in content blocks or the OpenAI chat shape,
    each piece of text handed on as it arrives, the whole returned."""
    parts: list[str] = []
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
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            raise StudyError(
                f"I could not finish: the provider said: {str(error.get('message') or error)[:400]}",
                code="environment.service.unusable_response", url=url)
        piece = ""
        delta = event.get("delta")
        if event.get("type") == "content_block_delta" and isinstance(delta, dict):
            piece = str(delta.get("text") or "")  # content blocks
        else:
            choices = event.get("choices") or []  # OpenAI shape
            if choices and isinstance(choices[0], dict):
                piece = str((choices[0].get("delta") or {}).get("content") or "")
        if piece:
            parts.append(piece)
            on_text(piece)
    return "".join(parts)


def describe_choice(path: Path | None = None) -> str:
    """What is set, for `fastmdx agent model` and the agent panel."""
    settled = load_choice(path)
    if settled is None:
        return "No AI model chosen. Run `fastmdx agent model` to pick one."
    env_name = str(PROVIDERS[settled.provider]["env"])
    if settled.auth_method == "anthropic_console":
        said = f"{settled}\nAuthentication: Anthropic Console OAuth through the official ant CLI"
    elif settled.auth_method == "chatgpt_plan":
        said = f"{settled}\nAuthentication: Sign in with ChatGPT plan usage"
    else:
        where = ("the environment" if os.environ.get(env_name)
                 else "the OS credential vault" if _has_stored_key(settled, path) else "nowhere")
        said = f"{settled}\nAPI key read from: {where} ({env_name})"
    if not _takes_tools(path):
        said += ("\nThis server does not take tool calls, so the Agent writes to it "
                 "in plain text.")
    return said


def _has_stored_key(choice: ModelChoice, path: Path | None = None) -> bool:
    return bool(_stored_api_key(choice, path))
