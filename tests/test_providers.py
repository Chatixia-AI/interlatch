"""Analysis through a model provider's API (providers.py): a fake server speaks each request shape (Anthropic Messages,
Chat Completions, Ollama's /api/chat) and answers with the fake claude's reply for the same system prompt."""

import datetime as dt
import json
import os
import stat
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from chronicle import providers
from chronicle.config import load_config, set_config_value
from chronicle.llm import LLMError, UsageLimitError, make_runner
from chronicle.providers import ApiRunner, key_source, set_key, sigv4
from chronicle.worker import run_worker

from conftest import CWD, SID


class FakeProvider:
    """Records each request; `mode` bends the next replies (limit, auth, error, length, refusal, full-context)."""

    def __init__(self, fake_claude):
        self.fake_claude, self.requests, self.mode, self.models = fake_claude, [], "ok", ["m-big", "m-small"]
        me = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, data):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                me.requests.append({"method": "GET", "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
                if self.path == "/api/tags":
                    return self._send(200, {"models": [{"name": m} for m in me.models]})
                return self._send(200, {"data": [{"id": m} for m in me.models]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                me.requests.append({"method": "POST", "path": self.path, "body": body,
                                    "headers": {k.lower(): v for k, v in self.headers.items()}})
                status, data = me.answer(self.path, body)
                self._send(status, data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def reply_text(self, system: str, prompt: str) -> str:
        out = subprocess.run([str(self.fake_claude), "-p", "--system-prompt", system], input=prompt, capture_output=True,
                             text=True, env={**os.environ, "FAKE_CLAUDE_LOG": ""})
        return json.loads(out.stdout)["result"]

    def answer(self, path: str, body: dict) -> tuple[int, dict]:
        if self.mode == "limit":
            return 429, {"error": {"type": "rate_limit_error", "message": "Rate limit reached"}}
        if self.mode == "auth":
            return 401, {"error": {"message": "Incorrect API key provided"}}
        if self.mode == "error":
            return 500, {"error": {"message": "internal"}}
        if path == "/v1/messages":
            text = self.reply_text(body["system"], body["messages"][0]["content"])
            stop = {"length": "max_tokens", "refusal": "refusal"}.get(self.mode, "end_turn")
            return 200, {"model": body["model"], "stop_reason": stop, "content": [{"type": "thinking", "thinking": ""},
                                                                                   {"type": "text", "text": text}],
                         "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 0}}
        system, prompt = body["messages"][0]["content"], body["messages"][1]["content"]
        text = self.reply_text(system, prompt)
        if path == "/chat/completions":
            usage = {"prompt_tokens": 1200, "completion_tokens": 300, "prompt_tokens_details": {"cached_tokens": 200}}
            if body.get("usage"):
                usage["cost"] = 0.0042
            return 200, {"model": body["model"], "usage": usage,
                         "choices": [{"finish_reason": "length" if self.mode == "length" else "stop",
                                      "message": {"role": "assistant", "content": text}}]}
        if path == "/api/chat":
            ctx = body["options"]["num_ctx"]
            return 200, {"model": body["model"], "message": {"role": "assistant", "content": text}, "done": True,
                         "done_reason": "stop", "prompt_eval_count": ctx if self.mode == "full" else 900, "eval_count": 150,
                         "total_duration": 2_500_000_000}
        return 404, {"error": "not found"}

    def posts(self) -> list[dict]:
        return [r for r in self.requests if r["method"] == "POST"]


@pytest.fixture()
def fake(env):
    server = FakeProvider(env["fake"])
    yield server
    server.httpd.shutdown()


def use(env, provider: str, **settings) -> None:
    cfg = env["cfg"]
    set_config_value(cfg, "analysis", "backend", json.dumps(provider))
    for k, v in settings.items():
        set_config_value(cfg, f"providers.{provider}", k, json.dumps(v))
    env["cfg"] = load_config(cfg.home)


@pytest.fixture(autouse=True)
def no_provider_env(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "AZURE_OPENAI_API_KEY", "AWS_BEARER_TOKEN_BEDROCK",
                "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_REGION",
                "AWS_DEFAULT_REGION", "OLLAMA_HOST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(providers, "_tool", lambda name, extra=(): None)  # no aws / az CLI unless a test adds one
    providers._cached.clear()


# ---- the request shapes ---------------------------------------------------------------------------------------------

def test_anthropic_messages(env, fake):
    use(env, "anthropic", base_url=fake.url)
    set_key(env["cfg"], "anthropic", "sk-ant-test")
    runner = make_runner(env["cfg"])
    assert isinstance(runner, ApiRunner) and runner.available()
    res = runner.run("<transcript>hi</transcript>", {"type": "object"}, system="Summarize the session.", model="sonnet")
    assert res.data["title"] == "Fixed login token expiry bug"
    req = fake.posts()[-1]
    assert req["path"] == "/v1/messages" and req["headers"]["x-api-key"] == "sk-ant-test"
    assert req["headers"]["anthropic-version"] == "2023-06-01"
    assert req["body"]["model"] == "claude-sonnet-5-5" and req["body"]["output_config"] == {"effort": "medium"}
    assert "tools" not in req["body"] and "conform to this JSON Schema" in req["body"]["system"]
    assert res.model == "claude-sonnet-5-5" and (res.input_tokens, res.output_tokens) == (1000, 200)
    assert res.cost_usd == pytest.approx((1000 * 2 + 200 * 10) / 1e6)
    runner.run("x", {"type": "object"}, system="Summarize the session.", model="haiku", effort="low")
    small = fake.posts()[-1]["body"]
    assert small["model"] == "claude-haiku-4-5" and "output_config" not in small  # Haiku takes no effort


@pytest.mark.parametrize("provider,auth", [
    ("openai", ("authorization", "Bearer sk-openai")),
    ("openrouter", ("authorization", "Bearer sk-openai")),
    ("azure", ("api-key", "sk-openai")),
    ("openai-compatible", ("authorization", "Bearer sk-openai")),
])
def test_chat_completions(env, fake, provider, auth):
    use(env, provider, base_url=fake.url, model="m-big", small_model="m-small")
    set_key(env["cfg"], provider, "sk-openai")
    runner = make_runner(env["cfg"])
    res = runner.run("<transcript>hi</transcript>", {"type": "object"}, system="Summarize the session.", model="sonnet")
    assert res.data["title"] == "Fixed login token expiry bug"
    req = fake.posts()[-1]
    assert req["path"] == "/chat/completions" and req["headers"][auth[0]] == auth[1]
    assert req["body"]["model"] == "m-big" and req["body"]["messages"][0]["role"] == "system"
    assert ("response_format" in req["body"]) is (provider != "openai-compatible")
    assert "max_tokens" not in req["body"] and "max_completion_tokens" not in req["body"] and "tools" not in req["body"]
    assert (res.input_tokens, res.output_tokens) == (1200, 300)
    if provider == "openrouter":
        assert req["headers"]["x-title"] == "Interlatch" and res.cost_usd == pytest.approx(0.0042)
    runner.run("x", {"type": "object"}, system="Summarize the session.", model="haiku")
    assert fake.posts()[-1]["body"]["model"] == "m-small"
    runner.run("x", {"type": "object"}, system="Summarize the session.", model="gpt-5.5")  # a model of its own: as is
    assert fake.posts()[-1]["body"]["model"] == "gpt-5.5"


def test_output_limit_names_the_right_field(env, fake):
    use(env, "openai", base_url=fake.url, model="m-big", max_output_tokens=9000)
    set_key(env["cfg"], "openai", "k")
    make_runner(env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")
    assert fake.posts()[-1]["body"]["max_completion_tokens"] == 9000
    use(env, "openrouter", base_url=fake.url, model="m-big", max_output_tokens=9000)
    set_key(env["cfg"], "openrouter", "k")
    make_runner(env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")
    assert fake.posts()[-1]["body"]["max_tokens"] == 9000


def test_ollama_native_chat(env, fake):
    use(env, "ollama", base_url=fake.url, model="qwen3:30b")
    runner = make_runner(env["cfg"])
    assert runner.available() and runner.local() and runner.chunk_chars == 60_000  # no key needed, nothing leaves
    res = runner.run("x", {"type": "object"}, system="Summarize the session.", model="sonnet")
    req = fake.posts()[-1]
    assert req["path"] == "/api/chat" and req["body"]["format"] == "json" and req["body"]["stream"] is False
    assert req["body"]["options"] == {"num_ctx": 32768} and req["body"]["model"] == "qwen3:30b"
    assert res.model == "ollama:qwen3:30b" and res.cost_usd == 0 and res.duration_ms == 2500


def test_ollama_prompt_that_fills_the_context_is_an_error(env, fake):
    use(env, "ollama", base_url=fake.url, model="llama3", num_ctx=8192)
    fake.mode = "full"
    with pytest.raises(LLMError, match="filled the 8,192-token context window"):
        make_runner(env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")


def test_ollama_host_from_the_environment(env, monkeypatch):
    use(env, "ollama", model="llama3")
    assert make_runner(env["cfg"]).base_url == "http://localhost:11434"
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.5:11434")
    runner = make_runner(env["cfg"])
    assert runner.base_url == "http://10.0.0.5:11434" and not runner.local()


# ---- errors ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode,exc,match", [
    ("limit", UsageLimitError, "HTTP 429: Rate limit reached"),
    ("auth", UsageLimitError, "HTTP 401: Incorrect API key"),
    ("error", LLMError, "HTTP 500"),
    ("length", LLMError, "cut off at the output limit"),
])
def test_errors(env, fake, mode, exc, match):
    use(env, "openai", base_url=fake.url, model="m")
    set_key(env["cfg"], "openai", "k")
    fake.mode = mode
    with pytest.raises(exc, match=match) as info:
        make_runner(env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")
    if mode in ("error", "length"):
        assert not isinstance(info.value, UsageLimitError)


def test_anthropic_refusal_and_cut_off(env, fake):
    use(env, "anthropic", base_url=fake.url)
    set_key(env["cfg"], "anthropic", "k")
    fake.mode = "refusal"
    with pytest.raises(LLMError, match="declined"):
        make_runner(env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")
    fake.mode = "length"
    with pytest.raises(LLMError, match="providers.anthropic.max_output_tokens"):
        make_runner(env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.")


def test_unreachable_endpoint(env):
    use(env, "ollama", base_url="http://127.0.0.1:9", model="llama3")
    with pytest.raises(LLMError, match="could not reach 127.0.0.1:9"):
        make_runner(env["cfg"]).run("x", {"type": "object"}, system="s")


def test_unavailable_reasons(env):
    use(env, "openai")
    assert make_runner(env["cfg"]).unavailable_reason() == "OpenAI API: choose a model (providers.openai.model)"
    use(env, "openai", model="gpt-5.5")
    assert make_runner(env["cfg"]).unavailable_reason() == "OpenAI API: add an API key"
    use(env, "azure", model="dep")
    assert "set its endpoint (providers.azure.base_url)" in make_runner(env["cfg"]).unavailable_reason()
    use(env, "azure", resource="contoso")
    runner = make_runner(env["cfg"])
    assert runner.base_url == "https://contoso.openai.azure.com/openai/v1" and runner.unavailable_reason() == "Azure OpenAI: add an API key"
    with pytest.raises(LLMError, match="add an API key"):
        runner.run("x", {"type": "object"}, system="s")


# ---- keys -----------------------------------------------------------------------------------------------------------

def test_keys_are_private_and_a_stored_key_wins(env, monkeypatch):
    cfg = env["cfg"]
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert key_source(cfg, "openai") == ("from-env", "$OPENAI_API_KEY")
    set_key(cfg, "openai", "  stored-key ")
    assert key_source(cfg, "openai") == ("stored-key", "stored")
    assert stat.S_IMODE(os.stat(cfg.home / "provider-keys.json").st_mode) == 0o600
    assert "stored-key" not in (cfg.home / "config.toml").read_text()
    set_key(cfg, "openai", None)
    assert key_source(cfg, "openai") == ("from-env", "$OPENAI_API_KEY")


def test_the_dashboard_never_sees_a_key(env):
    from chronicle.server import App

    set_key(env["cfg"], "openrouter", "sk-or-secret")
    app = App(env["cfg"])
    out = app.analysis_backends()
    assert "sk-or-secret" not in json.dumps(out)
    choice = next(c for c in out["choices"] if c["name"] == "openrouter")
    assert choice["key"]["source"] == "stored" and choice["kind"] == "api"
    assert [c["name"] for c in out["choices"]][:2] == ["claude", "codex"]


# ---- Bedrock and Azure sign-in --------------------------------------------------------------------------------------

@pytest.mark.parametrize("url,signature", [
    ("https://bedrock-mantle.us-east-1.api.aws/anthropic/v1/messages",
     "f159837366a4bacf1def29985cac25e7201aa3f5c3572c8a90590dda52a529eb"),
    ("https://x.example.com/v1/models?limit=100&b=a%20b", "c0f09d701605f3db0e53fff800b4f2c530abb767cf8e7ec51159bfd4ab24c213"),
])
def test_sigv4_matches_botocore(url, signature):
    # signatures botocore's SigV4Auth made for the same request at the same time
    headers = sigv4("POST", url, b'{"a": 1}', {"content-type": "application/json", "anthropic-version": "2023-06-01"},
                    region="us-east-1", service="bedrock-mantle",
                    creds={"AccessKeyId": "AKIDEXAMPLE", "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
                           "SessionToken": "tok"},
                    now=dt.datetime(2015, 8, 30, 12, 36, tzinfo=dt.timezone.utc))
    assert headers["X-Amz-Date"] == "20150830T123600Z" and headers["X-Amz-Security-Token"] == "tok"
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/bedrock-mantle/aws4_request, "
        f"SignedHeaders=anthropic-version;content-type;host;x-amz-date;x-amz-security-token, Signature={signature}")


def test_bedrock_signs_with_aws_credentials(env, fake, monkeypatch):
    use(env, "bedrock", base_url=fake.url + "/anthropic", region="eu-west-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "session-token")
    runner = make_runner(env["cfg"])
    assert runner.available()
    fake_paths = []
    orig = fake.answer
    fake.answer = lambda path, body: (fake_paths.append(path), orig(path.removeprefix("/anthropic"), body))[1]
    res = runner.run("x", {"type": "object"}, system="Summarize the session.", model="haiku")
    req = fake.posts()[-1]
    assert fake_paths == ["/anthropic/v1/messages"] and req["body"]["model"] == "anthropic.claude-haiku-4-5"
    auth = req["headers"]["authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/") and "/eu-west-1/bedrock-mantle/aws4_request" in auth
    assert "x-amz-security-token" in auth and req["headers"]["x-amz-security-token"] == "session-token"
    assert "x-api-key" not in req["headers"] and res.data


def test_bedrock_endpoint_and_api_key(env, fake):
    use(env, "bedrock", region="ap-northeast-1")
    assert make_runner(env["cfg"]).base_url == "https://bedrock-mantle.ap-northeast-1.api.aws/anthropic"
    assert make_runner(env["cfg"]).unavailable_reason() == "Amazon Bedrock: add an API key"  # no key, no AWS sign-in
    use(env, "bedrock", base_url=fake.url)
    set_key(env["cfg"], "bedrock", "bedrock-api-key")
    make_runner(env["cfg"]).run("x", {"type": "object"}, system="Summarize the session.", model="claude-opus-5-5")
    req = fake.posts()[-1]
    assert req["headers"]["x-api-key"] == "bedrock-api-key" and req["body"]["model"] == "anthropic.claude-opus-5-5"


def test_bedrock_credentials_from_the_aws_cli(env, tmp_path, monkeypatch):
    aws = tmp_path / "aws"
    aws.write_text("#!/bin/sh\necho \"$@\" >> " + str(tmp_path / "aws.log") + "\n"
                   "echo '{\"Version\": 1, \"AccessKeyId\": \"ASIA1\", \"SecretAccessKey\": \"s\", \"SessionToken\": \"t\", "
                   "\"Expiration\": \"2999-01-01T00:00:00Z\"}'\n")
    aws.chmod(0o755)
    monkeypatch.setattr(providers, "_tool", lambda name, extra=(): str(aws) if name == "aws" else None)
    creds = providers.aws_credentials("work")
    assert creds["AccessKeyId"] == "ASIA1" and providers.aws_credentials("work") is creds  # cached until it expires
    assert (tmp_path / "aws.log").read_text().split() == ["configure", "export-credentials", "--format", "process",
                                                          "--profile", "work"]


def test_azure_signs_in_with_entra_when_it_has_no_key(env, fake, tmp_path, monkeypatch):
    az = tmp_path / "az"
    az.write_text("#!/bin/sh\necho entra-token\n")
    az.chmod(0o755)
    monkeypatch.setattr(providers, "_tool", lambda name, extra=(): str(az) if name == "az" else None)
    use(env, "azure", base_url=fake.url, model="my-deployment")
    runner = make_runner(env["cfg"])
    assert runner.available()
    runner.run("x", {"type": "object"}, system="Summarize the session.")
    req = fake.posts()[-1]
    assert req["headers"]["authorization"] == "Bearer entra-token" and "api-key" not in req["headers"]


# ---- end to end, CLI, dashboard -------------------------------------------------------------------------------------

def test_worker_end_to_end_through_an_api(synced, fake):
    use(synced, "openrouter", base_url=fake.url, model="anthropic/claude-sonnet-5.5")
    set_key(synced["cfg"], "openrouter", "k")
    cfg, conn = synced["cfg"], synced["conn"]
    report = run_worker(cfg)
    assert report.analyzed == [SID] and not report.failed and CWD in report.synthesized
    row = conn.execute("SELECT model, cost_usd FROM analyses WHERE kind = 'session' AND target = ?", (SID,)).fetchone()
    assert row["model"] == "anthropic/claude-sonnet-5.5" and row["cost_usd"] == pytest.approx(0.0042)
    assert conn.execute("SELECT COUNT(*) FROM knowledge WHERE session_id = ?", (SID,)).fetchone()[0] >= 1
    assert not (synced["tmp"] / "fake_claude.log").exists()  # Claude Code was never run


def test_worker_says_what_is_missing(synced):
    use(synced, "openai")
    report = run_worker(synced["cfg"])
    assert report.note == "OpenAI API: choose a model (providers.openai.model); analysis skipped" and not report.analyzed


def test_cli_config(env, capsys, monkeypatch):
    from chronicle.cli import main

    assert main(["config", "set", "analysis.backend", "ollama"]) == 0
    assert main(["config", "set", "providers.ollama.model", "qwen3:30b"]) == 0
    assert main(["config", "set", "providers.ollama.num_ctx", "65536"]) == 0
    cfg = load_config(env["cfg"].home)
    assert cfg.analysis.backend == "ollama" and cfg.providers["ollama"] == {"model": "qwen3:30b", "num_ctx": 65536}
    assert main(["config", "set", "providers.ollama.api_key", "x"]) == 2  # keys go through set-key
    assert main(["config", "set", "providers.gemini.model", "x"]) == 2
    assert main(["config", "set-key", "openai", "sk-cli"]) == 0
    assert key_source(cfg, "openai") == ("sk-cli", "stored")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "sk-typed")
    assert main(["config", "set-key", "openai"]) == 0 and key_source(cfg, "openai")[0] == "sk-typed"
    assert main(["config", "forget-key", "openai"]) == 0 and key_source(cfg, "openai") == (None, "")
    assert main(["config", "set-key", "nope", "x"]) == 2


def test_dashboard_saves_tests_and_lists(env, fake):
    from chronicle.server import App

    app = App(env["cfg"])
    out = app.action_provider({"provider": "openai-compatible", "key": "sk-local",
                               "settings": {"base_url": fake.url, "model": "m-big", "num_ctx": 4096, "bogus": 1}})
    assert out["error"] == "not saved: bogus"
    cfg = load_config(env["cfg"].home)
    assert cfg.providers["openai-compatible"] == {"base_url": fake.url, "model": "m-big", "num_ctx": 4096}
    assert key_source(cfg, "openai-compatible") == ("sk-local", "stored")
    assert "error" in app.action_provider({"provider": "openai-compatible", "settings": {"base_url": "file:///etc/passwd"}})
    assert app.action_provider({"provider": "openai-compatible", "settings": {"num_ctx": 0}})  # 0 removes it
    assert "num_ctx" not in load_config(env["cfg"].home).providers["openai-compatible"]
    test = app.action_provider_test("openai-compatible")
    assert test["model"] == "m-big" and "ok" in test
    assert app.provider_models("openai-compatible") == {"models": ["m-big", "m-small"]}
    assert app.action_backend("openai-compatible")["backend"] == "openai-compatible"
    assert app.status_small()["analysis"]["label"] == "OpenAI-compatible"
    assert app.status_small()["analysis"]["billed"] is False  # its endpoint is on this computer
    for backend, billed in (("openai", True), ("ollama", False), ("claude", False), ("codex", False)):
        app.cfg.analysis.backend = backend
        assert app.status_small()["analysis"]["billed"] is billed, backend  # what "Analyze N sessions" warns of
    assert "error" in app.action_provider_test("gemini")


def test_provider_settings_are_refused_from_another_device(env):
    from chronicle.server import App, make_handler

    app = App(env["cfg"])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    httpd.RequestHandlerClass = make_handler(app, httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/api/analysis/provider", method="POST",
                                     data=json.dumps({"provider": "openai", "key": "sk-x"}).encode(),
                                     headers={"X-Interlatch": "1", "Content-Type": "application/json",
                                              "X-Forwarded-For": "100.64.0.9"})
        with pytest.raises(urllib.error.HTTPError) as info:
            urllib.request.urlopen(req, timeout=10)
        assert info.value.code == 403
        assert key_source(env["cfg"], "openai") == (None, "")
    finally:
        httpd.shutdown()
