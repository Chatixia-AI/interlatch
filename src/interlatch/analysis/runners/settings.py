"""The model providers Interlatch can analyze with (Anthropic, Bedrock, OpenAI, Azure, OpenRouter, Ollama, any
OpenAI-compatible server), their [providers.<name>] settings, and the API keys it stores for them."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ...core.config import Config

KEYS_FILE = "provider-keys.json"


@dataclass(frozen=True)
class Provider:
    label: str
    dialect: str  # "anthropic" | "openai" | "ollama"
    base_url: str = ""  # empty: must be configured
    key_env: str = ""  # the environment variable the provider's own tools read
    needs_key: bool = True
    model: str = ""
    small_model: str = ""
    chunk_chars: int = 0  # 0: [analysis] chunk_chars
    json_mode: bool = True  # openai: ask for a JSON object (response_format)
    hint: str = ""  # what base_url looks like, for messages and the dashboard


PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider("Anthropic API", "anthropic", "https://api.anthropic.com", "ANTHROPIC_API_KEY",
                          model="claude-sonnet-5-5", small_model="claude-haiku-4-5"),
    "bedrock": Provider("Amazon Bedrock", "anthropic", "", "AWS_BEARER_TOKEN_BEDROCK", needs_key=False,
                        model="anthropic.claude-sonnet-5-5", small_model="anthropic.claude-haiku-4-5",
                        hint="https://bedrock-mantle.<region>.api.aws/anthropic"),
    "openai": Provider("OpenAI API", "openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "azure": Provider("Azure OpenAI", "openai", "", "AZURE_OPENAI_API_KEY", needs_key=False,
                      hint="https://<resource>.openai.azure.com/openai/v1"),
    "openrouter": Provider("OpenRouter", "openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": Provider("Ollama", "ollama", "http://localhost:11434", needs_key=False, chunk_chars=60_000),
    "openai-compatible": Provider("OpenAI-compatible", "openai", "", "", needs_key=False, json_mode=False,
                                  hint="http://localhost:1234/v1"),
}
# every backend whose API key Interlatch can store: the providers, and IBM Bob, whose headless runs need one
KEY_ENVS = {**{name: p.key_env for name, p in PROVIDERS.items()}, "bob": "BOB_API_KEY"}
# [providers.<name>] keys the dashboard and `interlatch config set` may write; anything else in the section is ignored
SETTINGS = {"base_url": str, "model": str, "small_model": str, "region": str, "profile": str, "resource": str,
            "num_ctx": int, "chunk_chars": int, "max_output_tokens": int, "json_mode": bool, "key_env": str}


def keys_path(cfg: Config) -> Path:
    return cfg.home / KEYS_FILE


def _read_keys(cfg: Config) -> dict:
    try:
        data = json.loads(keys_path(cfg).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def set_key(cfg: Config, provider: str, key: str | None) -> None:
    """Store (or, with None or "", forget) the API key for `provider`; the file is readable by this user only."""
    keys = _read_keys(cfg)
    if key and key.strip():
        keys[provider] = key.strip()
    else:
        keys.pop(provider, None)
    path = keys_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(keys, fh, indent=1)
    os.replace(tmp, path)


def key_source(cfg: Config, provider: str) -> tuple[str | None, str]:
    """(the key, where it came from: "stored", "$ENV_NAME" or ""). A stored key wins over the environment, so the
    dashboard (started by launchd, without the shell's variables) and the CLI use the same one."""
    stored = _read_keys(cfg).get(provider)
    if isinstance(stored, str) and stored.strip():
        return stored.strip(), "stored"
    env = settings(cfg, provider).get("key_env") or KEY_ENVS.get(provider, "")
    if env and os.environ.get(env, "").strip():
        return os.environ[env].strip(), f"${env}"
    return None, ""


def settings(cfg: Config, provider: str) -> dict:
    """[providers.<provider>] from config.toml, keeping only known keys of the right type."""
    raw = cfg.providers.get(provider) or {}
    out = {}
    for k, typ in SETTINGS.items():
        v = raw.get(k)
        if v is None or (typ is int and isinstance(v, bool)):
            continue
        if isinstance(v, typ) or (typ is int and isinstance(v, float)):
            out[k] = typ(v)
    return out


def save_settings(cfg: Config, provider: str, values: dict) -> list[str]:
    """Write the given [providers.<provider>] settings to config.toml; an empty string or 0 removes one. Returns the
    keys that were refused (unknown, or of the wrong type)."""
    from ...core.config import remove_config_value, set_config_value

    refused = []
    for k, v in values.items():
        typ = SETTINGS.get(k)
        if typ is None:
            refused.append(k)
            continue
        if v in ("", None, 0) and typ is not bool:
            remove_config_value(cfg, f"providers.{provider}", k)
            continue
        try:
            v = typ(v) if not (typ is bool and isinstance(v, str)) else v.lower() in ("1", "true", "yes", "on")
        except (TypeError, ValueError):
            refused.append(k)
            continue
        if typ is int and v < 0:
            refused.append(k)
            continue
        if k == "base_url" and v and urlsplit(v).scheme not in ("http", "https"):
            refused.append(k)
            continue
        set_config_value(cfg, f"providers.{provider}", k, json.dumps(v) if typ is not bool else ("true" if v else "false"))
    return refused
