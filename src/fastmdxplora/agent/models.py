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
a file of its own with owner-only permissions, or in the environment, and
this module is the only thing that reads it.

**The environment wins.** ``ANTHROPIC_API_KEY`` and ``OPENAI_API_KEY`` are
checked before the stored file, because that is how a cluster job and a CI
run get a key without anybody storing one, and because it is what people
already expect.

**Which model wrote a study belongs in the record.** The provider and the
model name, never the key. Six months on, "why did this study pick 300 K"
has a different answer depending on whether a frontier model or a 7B
running on a laptop proposed it, and that is provenance like any other.

Two providers are offered by name. A third option takes any
OpenAI-compatible base URL, which covers DeepSeek, vLLM, Ollama,
OpenRouter and most local servers -- they speak the same shape, so there
is nothing to add per provider.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import StudyError
from fastmdxplora.user_dir import user_config_dir

__all__ = [
    "list_models",
    "ModelChoice",
    "PROVIDERS",
    "model_path",
    "load_choice",
    "save_choice",
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
        #: is written, and a model released next month would be missing
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
        #: knows they want DeepSeek or a local model and does not know the
        #: URL. Examples rather than a list of supported vendors: the
        #: protocol is what is supported, and a vendor list goes stale.
        "examples": (
            ("DeepSeek", "https://api.deepseek.com", "deepseek-chat"),
            ("OpenRouter", "https://openrouter.ai/api/v1",
             "anthropic/claude-sonnet-4-6"),
            ("Ollama (local)", "http://localhost:11434/v1", "llama3.1"),
            ("vLLM (local)", "http://localhost:8000/v1", "<your model>"),
        ),
        "url": "",
        "default_model": "",
        "env": "FASTMDX_MODEL_API_KEY",
        "auth": "bearer",
    },
}


@dataclass(frozen=True)
class ModelChoice:
    """Which model to ask, and where. Never the key."""

    provider: str
    model: str
    base_url: str = ""

    @property
    def url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/") + "/chat/completions"
        return str(PROVIDERS[self.provider]["url"])

    @property
    def auth_style(self) -> str:
        return str(PROVIDERS[self.provider]["auth"])

    def as_record(self) -> dict[str, str]:
        """What goes in a manifest. Provider and model, and that is all."""
        record = {"provider": self.provider, "model": self.model}
        if self.base_url:
            record["base_url"] = self.base_url
        return record

    def __str__(self) -> str:
        where = f" at {self.base_url}" if self.base_url else ""
        return f"{PROVIDERS[self.provider]['label']}, {self.model}{where}"


def model_path() -> Path:
    """Where the choice is stored. Outside any study, on purpose."""
    return user_config_dir() / "model.json"


def load_choice(path: Path | None = None) -> ModelChoice | None:
    """The stored choice, or ``None`` where nothing has been set."""
    target = path or model_path()
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    provider = str(record.get("provider", ""))
    if provider not in PROVIDERS:
        return None
    return ModelChoice(provider=provider,
                       model=str(record.get("model", "")),
                       base_url=str(record.get("base_url", "")))


def save_choice(choice: ModelChoice, *, key: str = "",
                path: Path | None = None) -> Path:
    """Store the choice, and the key if one was given.

    The key goes in the same file because there is nowhere better that
    works on every platform without a dependency, and the file is owner-
    only. It is never copied anywhere else: :func:`as_record` does not
    carry it, and nothing writes this file into a study.

    Giving no key is the ordinary case on a shared machine -- the choice is
    stored, and the key comes from the environment at call time.
    """
    target = path or model_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, str] = dict(choice.as_record())
    if key:
        record["api_key"] = key
    target.write_text(json.dumps(record, indent=2), encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:  # pragma: no cover - some filesystems refuse
        pass
    return target


def _key_for(choice: ModelChoice, path: Path | None = None) -> str:
    """The key, environment first.

    Environment before file so a cluster job, a CI run or a shared machine
    can supply one per session without anybody storing it, and so a stored
    key can be overridden for one run without editing anything.
    """
    env_name = str(PROVIDERS[choice.provider]["env"])
    from_env = os.environ.get(env_name)
    if from_env:
        return from_env
    try:
        record = json.loads((path or model_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    stored = str(record.get("api_key", ""))
    if stored:
        return stored
    raise StudyError(
        f"No API key for {PROVIDERS[choice.provider]['label']}. Set "
        f"{env_name} in the environment, or run `fastmdx agent set` and "
        "give one when asked. The key is never written into a study, so it "
        "has to come from one of those two places.",
        code="environment.credentials.absent",
        provider=choice.provider, environment_variable=env_name,
    )



def list_models(choice: ModelChoice | None = None, *,
                path: Path | None = None, timeout: float = 10.0) -> tuple:
    """The models this provider currently has, asked of the provider.

    A list written into this file is stale the week after it is written,
    and a model released next month would simply be missing with nothing to
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

    url = spec.get("models_url")
    if not url and chosen.base_url:
        url = chosen.base_url.rstrip("/") + "/models"
    if not url:
        return fallback

    try:
        key = _key_for(chosen, path)
    except StudyError:
        return fallback
    headers = {}
    if chosen.provider == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif key:
        headers = {"Authorization": f"Bearer {key}"}

    try:
        request = urllib.request.Request(url, headers=headers)
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


def completion_for(choice: ModelChoice | None = None, *,
                   path: Path | None = None, timeout: float = 120.0):
    """Build the prompt-in, text-out function the agent takes.

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
            "No model has been chosen. Run `fastmdx agent set` to pick one. "
            "Nothing in FastMDXplora needs a model unless you ask for the "
            "agent, so this only comes up when you do.",
            code="environment.model.unset",
        )

    def complete(prompt: str) -> str:
        key = _key_for(settled, path)
        if settled.auth_style == "x-api-key":
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
            body: dict[str, Any] = {
                "model": settled.model, "max_tokens": 4000,
                "messages": [{"role": "user", "content": prompt}]}
        else:
            headers = {"Authorization": f"Bearer {key}"}
            body = {"model": settled.model,
                    "messages": [{"role": "user", "content": prompt}]}
        headers["content-type"] = "application/json"

        request = urllib.request.Request(
            settled.url, method="POST",
            data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                answer = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # The provider's own reason, and never the request that carried
            # the key. An error message is the easiest place for a secret
            # to escape into a log.
            detail = exc.read().decode("utf-8", "replace")[:400]
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

    return complete


def describe_choice(path: Path | None = None) -> str:
    """One line about what is set, for `fastmdx agent set` with no answer."""
    settled = load_choice(path)
    if settled is None:
        return "No model chosen. Run `fastmdx agent set` to pick one."
    env_name = str(PROVIDERS[settled.provider]["env"])
    where = ("the environment" if os.environ.get(env_name)
             else "the stored file" if _has_stored_key(path) else "nowhere")
    return f"{settled}\nKey read from: {where} ({env_name})"


def _has_stored_key(path: Path | None = None) -> bool:
    try:
        record = json.loads((path or model_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(record.get("api_key"))
