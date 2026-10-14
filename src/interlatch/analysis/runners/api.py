"""Analysis through a model provider's HTTP API instead of a coding agent's CLI (`analysis.backend`).

Three request shapes cover the providers:
- "anthropic": the Messages API. Anthropic itself, and Claude in Amazon Bedrock (bedrock-mantle), which serves the
  same API, signed with SigV4 or called with a Bedrock API key.
- "openai": Chat Completions. OpenAI, Azure OpenAI (its v1 endpoint), OpenRouter, and any server that speaks it
  (LM Studio, vLLM, Groq, Gemini's OpenAI endpoint, ...).
- "ollama": Ollama's own /api/chat, which, unlike its OpenAI endpoint, takes the context window size (num_ctx):
  Ollama's small default would otherwise cut the start of a long transcript off without a word.

Settings live in config.toml under [providers.<name>]; API keys live apart from it, in provider-keys.json (mode 600)
in Interlatch's folder, or come from the provider's usual environment variable. Only the standard library is used.
No tools are offered to the model: a plain API call can only answer.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

from ...core.config import Config
from . import process  # its POLL_S and SLEEP_GRACE_S, read when a request runs
from .base import CLAUDE_MODEL, LIMIT_MARKERS, LLMError, LLMResult, Runner, SleepInterruptedError, UsageLimitError
from .settings import PROVIDERS, key_source, settings

ANTHROPIC_VERSION = "2023-06-01"
AZURE_SCOPE = "https://cognitiveservices.azure.com"
DEFAULT_MAX_OUTPUT = 32_000  # the Messages API needs one; thinking counts toward it


OLLAMA_NUM_CTX = 32_768
_CLAUDE_ALIAS = re.compile(r"^(sonnet|haiku|opus|fable|mythos|opusplan|default)(\[.*\])?$")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0")


def _tool(name: str, extra: tuple[str, ...] = ()) -> str | None:
    """A CLI on PATH, or in the usual install folders (launchd's PATH is short)."""
    found = shutil.which(name)
    if found:
        return found
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", *extra):
        p = Path(d).expanduser() / name
        if p.exists():
            return str(p)
    return None


# ---- HTTP -----------------------------------------------------------------------------------------------------------

def http(method: str, url: str, *, body: bytes | None, headers: dict, timeout: int, name: str) -> tuple[int, bytes]:
    """One request, with a wall-clock deadline that also notices system sleep (as `run_sleep_aware` does for CLIs):
    the request runs in a thread while this one watches the clocks."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    box: dict = {}

    def go():
        try:
            with urllib.request.urlopen(req, timeout=timeout + 30) as r:
                box["res"] = (r.status, r.read())
        except urllib.error.HTTPError as exc:
            box["res"] = (exc.code, exc.read() or b"")
        except BaseException as exc:  # noqa: BLE001 - handed to the caller
            box["err"] = exc

    t = threading.Thread(target=go, name=f"interlatch-{name}", daemon=True)
    t.start()
    wall0, mono0 = time.time(), time.monotonic()
    while True:
        t.join(process.POLL_S)
        if not t.is_alive():
            break
        wall = time.time() - wall0
        slept = wall - (time.monotonic() - mono0)
        if slept > process.SLEEP_GRACE_S:
            raise SleepInterruptedError(f"machine slept for {int(slept)}s during the call")
        if wall > timeout:
            raise LLMError(f"{name} timed out after {int(wall)}s")
    if "err" in box:
        exc = box["err"]
        reason = getattr(exc, "reason", None) or exc
        raise LLMError(f"could not reach {urlsplit(url).netloc}: {reason}")
    return box["res"]


def _error(name: str, status: int, raw: bytes) -> LLMError:
    """The provider's error, as UsageLimitError when analysis should pause (limits, credit, sign-in)."""
    text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text)
        err = data.get("error") if isinstance(data, dict) else None
        msg = (err.get("message") if isinstance(err, dict) else err) or data.get("message") or text
    except (ValueError, AttributeError):
        msg = text
    msg = f"{name}: HTTP {status}: {str(msg).strip()[:600]}"
    if status in (401, 402, 403, 429, 529) or any(m in msg.lower() for m in LIMIT_MARKERS):
        return UsageLimitError(msg)
    return LLMError(msg)


# ---- AWS (Bedrock) --------------------------------------------------------------------------------------------------

def sigv4(method: str, url: str, body: bytes, headers: dict, *, region: str, service: str, creds: dict,
          now: dt.datetime | None = None) -> dict:
    """`headers` plus the AWS Signature Version 4 ones (Authorization, X-Amz-Date, X-Amz-Security-Token)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    u = urlsplit(url)
    signed = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    signed["host"] = u.netloc
    signed["x-amz-date"] = amz_date
    if creds.get("SessionToken"):
        signed["x-amz-security-token"] = creds["SessionToken"]
    names = sorted(signed)
    query = "&".join(sorted(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
                            for k, v in parse_qsl(u.query, keep_blank_values=True)))
    path = "/".join(quote(quote(seg, safe="-_.~"), safe="-_.~%") for seg in (u.path or "/").split("/"))
    canonical = "\n".join([method, path or "/", query, "".join(f"{k}:{signed[k]}\n" for k in names), ";".join(names),
                           hashlib.sha256(body).hexdigest()])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ("AWS4" + creds["SecretAccessKey"]).encode()
    for part in (day, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {**headers, "X-Amz-Date": amz_date,
           "Authorization": f"AWS4-HMAC-SHA256 Credential={creds['AccessKeyId']}/{scope}, "
                            f"SignedHeaders={';'.join(names)}, Signature={signature}"}
    if creds.get("SessionToken"):
        out["X-Amz-Security-Token"] = creds["SessionToken"]
    return out


_cache_lock = threading.Lock()
_cached: dict[str, tuple[float, object]] = {}  # (expires at, value): AWS credentials and Entra tokens


def _cached_value(key: str, make, ttl_of) -> object:
    with _cache_lock:
        hit = _cached.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    value = make()
    with _cache_lock:
        _cached[key] = (time.time() + ttl_of(value), value)
    return value


def aws_credentials(profile: str = "") -> dict:
    """Static keys from the environment, else whatever the AWS CLI resolves (SSO, assumed roles, ~/.aws)."""
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY") and not profile:
        return {"AccessKeyId": os.environ["AWS_ACCESS_KEY_ID"], "SecretAccessKey": os.environ["AWS_SECRET_ACCESS_KEY"],
                "SessionToken": os.environ.get("AWS_SESSION_TOKEN", "")}
    aws = _tool("aws")
    if not aws:
        raise UsageLimitError("no AWS credentials: set a Bedrock API key, AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, "
                              "or install the AWS CLI and sign in (aws sso login)")

    def make() -> dict:
        cmd = [aws, "configure", "export-credentials", "--format", "process"] + (["--profile", profile] if profile else [])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UsageLimitError(f"aws configure export-credentials failed: {exc}") from exc
        if proc.returncode != 0:
            raise UsageLimitError(f"AWS sign-in needed ({(proc.stderr or proc.stdout).strip()[:300]})")
        return json.loads(proc.stdout)

    def ttl(c: dict) -> float:
        exp = c.get("Expiration")
        if not exp:
            return 3600
        try:
            left = dt.datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp() - time.time()
        except ValueError:
            return 300
        return max(0.0, left - 300)

    return _cached_value(f"aws:{profile}", make, ttl)  # type: ignore[return-value]


def entra_token() -> str:
    """A Microsoft Entra ID token for Azure OpenAI from the Azure CLI's sign-in (az login), for tenants that turn
    API keys off."""
    az = _tool("az")
    if not az:
        raise UsageLimitError("no Azure OpenAI API key, and the Azure CLI (az) is not installed to sign in with")

    def make() -> str:
        cmd = [az, "account", "get-access-token", "--resource", AZURE_SCOPE, "--query", "accessToken", "-o", "tsv"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UsageLimitError(f"az account get-access-token failed: {exc}") from exc
        if proc.returncode != 0 or not proc.stdout.strip():
            raise UsageLimitError(f"Azure sign-in needed: run az login ({(proc.stderr or '').strip()[:300]})")
        return proc.stdout.strip()

    return _cached_value("azure", make, lambda _t: 45 * 60)  # type: ignore[return-value]


# ---- the runner -----------------------------------------------------------------------------------------------------

class ApiRunner(Runner):
    """Asks a provider's HTTP API; one class for every provider, the request shape picked by its dialect."""

    def __init__(self, cfg: Config, provider: str):
        super().__init__(cfg)
        self.name = provider
        self.spec = PROVIDERS[provider]
        self.label = self.spec.label
        self.s = settings(cfg, provider)
        self.cli = self.label

    # -- where, what, and whether it can run

    @property
    def base_url(self) -> str:
        s, p = self.s, self.name
        if s.get("base_url"):
            return s["base_url"].rstrip("/")
        if p == "bedrock":
            return f"https://bedrock-mantle.{self.region}.api.aws/anthropic"
        if p == "azure" and s.get("resource"):
            return f"https://{s['resource']}.openai.azure.com/openai/v1"
        if p == "ollama" and os.environ.get("OLLAMA_HOST"):
            host = os.environ["OLLAMA_HOST"].strip().rstrip("/")
            return host if "://" in host else f"http://{host}"
        return self.spec.base_url.rstrip("/")

    @property
    def region(self) -> str:
        return self.s.get("region") or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"

    def where(self) -> str:
        return self.base_url or self.spec.hint

    def local(self) -> bool:
        """Transcripts stay on this computer: the endpoint is localhost."""
        host = (urlsplit(self.base_url).hostname or "") if self.base_url else ""
        return host in _LOCAL_HOSTS

    @property
    def chunk_chars(self) -> int:
        return self.s.get("chunk_chars") or self.spec.chunk_chars or self.cfg.analysis.chunk_chars

    def _models(self) -> tuple[str, str]:
        model = self.s.get("model") or self.spec.model
        return model, self.s.get("small_model") or self.spec.small_model or model

    def _model(self, model: str | None) -> str:
        """The model for a call. Callers pass Interlatch's Claude defaults ("sonnet", "haiku"): those pick this
        provider's model, or its small model for "haiku". A full Claude id goes as is to the Claude providers and
        picks the same way elsewhere; any other name is taken as this provider's own model id."""
        main, small = self._models()
        if not model:
            return main
        m = model.lower()
        if _CLAUDE_ALIAS.match(m) or (self.spec.dialect != "anthropic" and CLAUDE_MODEL.match(m)):
            return small if "haiku" in m else main
        if self.name == "bedrock" and m.startswith("claude-"):
            return f"anthropic.{model}"
        return model

    def model_label(self, model: str | None = None) -> str:
        return self._model(model) or "no model set"

    def _credentials(self) -> str:
        """How this provider would sign in: "key", "aws", "entra", "none", or "" when it has nothing to use."""
        key, _ = key_source(self.cfg, self.name)
        if key:
            return "key"
        if self.name == "bedrock":
            env = os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")
            return "aws" if env or _tool("aws") else ""
        if self.name == "azure":
            return "entra" if _tool("az") else ""
        return "" if self.spec.needs_key else "none"

    def unavailable_reason(self) -> str:
        from ...core.i18n import tr

        if not self.base_url:
            return tr("{label}: set its endpoint (providers.{name}.base_url)", label=self.label, name=self.name)
        if not self._models()[0]:
            return tr("{label}: choose a model (providers.{name}.model)", label=self.label, name=self.name)
        if not self._credentials():
            return tr("{label}: add an API key", label=self.label)
        return ""

    def available(self) -> bool:
        return not self.unavailable_reason()

    # -- calls

    def _call(self, prompt: str, *, system: str, model: str | None, effort: str | None, timeout: int | None,
              max_budget_usd: float | None) -> tuple[LLMResult, str]:
        reason = self.unavailable_reason()
        if reason:
            raise LLMError(reason)
        model = self._model(model)
        timeout = timeout or self.cfg.analysis.timeout_seconds
        t0 = time.monotonic()
        call = {"anthropic": self._anthropic, "openai": self._openai, "ollama": self._ollama}[self.spec.dialect]
        text, res = call(prompt, system, model, effort or self.cfg.analysis.effort, timeout)
        res.duration_ms = res.duration_ms or int((time.monotonic() - t0) * 1000)
        return res, text

    def _post(self, path: str, payload: dict, headers: dict, timeout: int) -> dict:
        url = self.base_url + path
        body = json.dumps(payload).encode()
        headers = {"content-type": "application/json", "user-agent": "interlatch", **headers}
        status, raw = http("POST", url, body=body, headers=headers, timeout=timeout, name=self.label)
        if status >= 400:
            raise _error(self.label, status, raw)
        try:
            data = json.loads(raw)
        except ValueError:
            raise LLMError(f"{self.label}: the reply was not JSON: {raw[:200]!r}") from None
        if not isinstance(data, dict):
            raise LLMError(f"{self.label}: unexpected reply: {str(data)[:200]}")
        return data

    def _cut_off(self) -> LLMError:
        limit = self.s.get("max_output_tokens") or DEFAULT_MAX_OUTPUT
        return LLMError(f"{self.label}: the reply was cut off at the output limit; raise "
                        f"providers.{self.name}.max_output_tokens (now {limit})")

    def _anthropic(self, prompt: str, system: str, model: str, effort: str, timeout: int) -> tuple[str, LLMResult]:
        payload = {"model": model, "max_tokens": self.s.get("max_output_tokens") or DEFAULT_MAX_OUTPUT,
                   "system": system, "messages": [{"role": "user", "content": prompt}]}
        if effort and "haiku" not in model:  # Haiku takes no effort setting
            payload["output_config"] = {"effort": effort}
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        key, _ = key_source(self.cfg, self.name)
        if key:
            headers["x-api-key"] = key
        elif self.name == "bedrock":
            body = json.dumps(payload).encode()
            signed = sigv4("POST", self.base_url + "/v1/messages", body,
                           {"content-type": "application/json", **headers}, region=self.region,
                           service="bedrock-mantle", creds=aws_credentials(self.s.get("profile", "")))
            headers = {k: v for k, v in signed.items() if k.lower() != "content-type"}
        data = self._post("/v1/messages", payload, headers, timeout)
        stop = data.get("stop_reason")
        if stop == "refusal":
            raise LLMError(f"{self.label}: the model declined ({(data.get('stop_details') or {}).get('category')})")
        if stop == "max_tokens":
            raise self._cut_off()
        text = "".join(b.get("text") or "" for b in data.get("content") or [] if isinstance(b, dict) and b.get("type") == "text")
        u = data.get("usage") or {}
        from ...core.pricing import usage_cost

        served = data.get("model") or model
        return text, LLMResult(
            data={}, cost_usd=usage_cost(served, u), duration_ms=0, model=served,
            input_tokens=int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
            + int(u.get("cache_creation_input_tokens") or 0),
            output_tokens=int(u.get("output_tokens") or 0))

    def _openai(self, prompt: str, system: str, model: str, effort: str, timeout: int) -> tuple[str, LLMResult]:
        payload: dict = {"model": model, "messages": [{"role": "system", "content": system},
                                                      {"role": "user", "content": prompt}]}
        if self.s.get("json_mode", self.spec.json_mode):
            payload["response_format"] = {"type": "json_object"}
        if self.s.get("max_output_tokens"):  # otherwise the model's own limit: newer OpenAI models refuse max_tokens
            field = "max_completion_tokens" if self.name in ("openai", "azure") else "max_tokens"
            payload[field] = self.s["max_output_tokens"]
        headers: dict = {}
        key, _ = key_source(self.cfg, self.name)
        if self.name == "azure":
            headers = {"api-key": key} if key else {"authorization": f"Bearer {entra_token()}"}
        elif key:
            headers = {"authorization": f"Bearer {key}"}
        if self.name == "openrouter":
            headers |= {"http-referer": "https://interlatch.com", "x-title": "Interlatch"}
            payload["usage"] = {"include": True}
        data = self._post("/chat/completions", payload, headers, timeout)
        choice = (data.get("choices") or [{}])[0] or {}
        msg = choice.get("message") or {}
        if msg.get("refusal"):
            raise LLMError(f"{self.label}: the model declined: {str(msg['refusal'])[:300]}")
        if choice.get("finish_reason") == "length":
            raise self._cut_off()
        text = msg.get("content") or ""
        if isinstance(text, list):  # some servers send content parts
            text = "".join(p.get("text") or "" for p in text if isinstance(p, dict))
        u = data.get("usage") or {}
        inp, out = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        served = data.get("model") or model
        cost = u.get("cost")
        if not isinstance(cost, (int, float)):
            from ...core.pricing import is_openai_model, openai_cost

            cost = openai_cost(served, inp - cached, cached, out) if is_openai_model(served) else 0.0
        return str(text), LLMResult(data={}, cost_usd=float(cost), duration_ms=0, model=served,
                                    input_tokens=inp, output_tokens=out)

    def _ollama(self, prompt: str, system: str, model: str, effort: str, timeout: int) -> tuple[str, LLMResult]:
        num_ctx = self.s.get("num_ctx") or OLLAMA_NUM_CTX
        options: dict = {"num_ctx": num_ctx}
        if self.s.get("max_output_tokens"):
            options["num_predict"] = self.s["max_output_tokens"]
        payload = {"model": model, "stream": False, "format": "json", "options": options,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        data = self._post("/api/chat", payload, {}, timeout)
        prompt_tokens, out = int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0)
        if prompt_tokens >= num_ctx - 16:  # Ollama drops the start of a prompt that does not fit, silently
            raise LLMError(f"Ollama: the prompt filled the {num_ctx:,}-token context window (num_ctx), so its start was "
                           f"cut off; raise providers.ollama.num_ctx or lower providers.ollama.chunk_chars")
        if data.get("done_reason") == "length":
            raise self._cut_off()
        return str((data.get("message") or {}).get("content") or ""), LLMResult(
            data={}, cost_usd=0.0, duration_ms=int((data.get("total_duration") or 0) / 1e6), model=f"ollama:{model}",
            input_tokens=prompt_tokens, output_tokens=out)

    # -- the dashboard's helpers

    def list_models(self, timeout: int = 15) -> list[str]:
        """Model ids the endpoint offers, for the dashboard's suggestions; [] when it cannot say."""
        key, _ = key_source(self.cfg, self.name)
        headers: dict = {}
        if self.spec.dialect == "ollama":
            path = "/api/tags"
        elif self.spec.dialect == "openai":
            path = "/models"
            if self.name == "azure" and key:
                headers = {"api-key": key}
            elif key:
                headers = {"authorization": f"Bearer {key}"}
        elif self.name == "anthropic" and key:
            path, headers = "/v1/models?limit=100", {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
        else:
            return []
        if not self.base_url:
            return []
        try:
            status, raw = http("GET", self.base_url + path, body=None, headers={"user-agent": "interlatch", **headers},
                               timeout=timeout, name=self.label)
            data = json.loads(raw) if status < 400 else {}
        except (LLMError, ValueError):
            return []
        rows = (data.get("models") if self.spec.dialect == "ollama" else data.get("data")) or []
        ids = [str(r.get("name") or r.get("id") or "") for r in rows if isinstance(r, dict)]
        return sorted({i for i in ids if i})

    def describe(self) -> dict:
        """What the dashboard shows for this provider; never the key itself."""
        _, source = key_source(self.cfg, self.name)
        main, small = self._models()
        return {"name": self.name, "label": self.label, "kind": "api", "dialect": self.spec.dialect,
                "available": self.available(), "reason": self.unavailable_reason(), "path": self.where(),
                "model": main or "", "small_model": small or "", "local": self.local(),
                "key": {"source": source, "env": self.s.get("key_env") or self.spec.key_env,
                        "needed": self.spec.needs_key, "alt": self._credentials() if self._credentials() not in ("key", "none") else ""},
                "settings": self.s, "defaults": {"base_url": self.spec.base_url, "hint": self.spec.hint, "model": self.spec.model,
                                                 "small_model": self.spec.small_model,
                                                 "chunk_chars": self.spec.chunk_chars or self.cfg.analysis.chunk_chars,
                                                 "num_ctx": OLLAMA_NUM_CTX if self.spec.dialect == "ollama" else 0}}
