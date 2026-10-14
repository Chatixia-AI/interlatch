"""API-equivalent cost estimation from transcript usage blocks.

Prices are USD per million tokens (first-party list prices). Cache writes cost 1.25x input for
the 5-minute TTL and 2x for the 1-hour TTL; cache reads are 0.1x input except where noted.
Subscription users do not pay these amounts; they are a consistent yardstick for usage.
"""

from __future__ import annotations

import re

# model id -> (input, output, cache_read)
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-mythos-5-1": (10.0, 50.0, 0.25),
    "claude-fable-5": (10.0, 50.0, 1.0),
    "claude-mythos-5": (10.0, 50.0, 1.0),
    "claude-mythos-preview": (10.0, 50.0, 1.0),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-opus-4-7": (5.0, 25.0, 0.50),
    "claude-opus-4-6": (5.0, 25.0, 0.50),
    "claude-opus-4-5": (5.0, 25.0, 0.50),
    "claude-opus-4-1": (15.0, 75.0, 1.50),
    "claude-opus-4-0": (15.0, 75.0, 1.50),
    "claude-opus-4": (15.0, 75.0, 1.50),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
    "claude-sonnet-4-5": (3.0, 15.0, 0.30),
    "claude-sonnet-4-0": (3.0, 15.0, 0.30),
    "claude-sonnet-4": (3.0, 15.0, 0.30),
    "claude-3-7-sonnet": (3.0, 15.0, 0.30),
    "claude-3-5-sonnet": (3.0, 15.0, 0.30),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
    "claude-3-5-haiku": (0.80, 4.0, 0.08),
    "claude-3-haiku": (0.25, 1.25, 0.03),
    "claude-3-opus": (15.0, 75.0, 1.50),
}

# Fallback by family when an unknown (newer) model shows up.
FAMILY_FALLBACK = {
    "fable": "claude-fable-5-1",
    "mythos": "claude-mythos-5-1",
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}

WEB_SEARCH_PER_REQUEST = 10.0 / 1000  # $10 per 1,000 searches
FAST_MODE_MULTIPLIER = 2.0

_DATE_SUFFIX = re.compile(r"-\d{8}$")


def normalize_model(model: str | None) -> str | None:
    """'claude-opus-5-5[1m]' -> 'claude-opus-5-5'; 'claude-haiku-4-5-20251001' -> 'claude-haiku-4-5'."""
    if not model or model.startswith("<"):
        return None
    m = model.strip().lower()
    m = re.sub(r"\[.*?\]$", "", m)
    m = m.split("@")[0]
    m = re.sub(r"^(us|eu|apac|global)\.anthropic\.", "", m)
    m = re.sub(r"^anthropic\.", "", m)
    m = re.sub(r"-v\d+(:\d+)?$", "", m)
    m = _DATE_SUFFIX.sub("", m)
    return m


def price_for(model: str | None) -> tuple[tuple[float, float, float] | None, bool]:
    """Return ((input, output, cache_read), exact_match)."""
    m = normalize_model(model)
    if not m:
        return None, True
    if m in PRICES:
        return PRICES[m], True
    # longest known prefix (e.g. a dated or suffixed variant)
    for known in sorted(PRICES, key=len, reverse=True):
        if m.startswith(known):
            return PRICES[known], True
    for family, fallback in FAMILY_FALLBACK.items():
        if family in m:
            return PRICES[fallback], False
    return None, False


def usage_cost(model: str | None, usage: dict | None, speed: str | None = None) -> float:
    """Estimate the USD cost of one API response from its usage block."""
    if not usage:
        return 0.0
    prices, _ = price_for(model)
    if not prices:
        return 0.0
    p_in, p_out, p_cache_read = prices
    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0
    cache_read = usage.get("cache_read_input_tokens") or 0
    cache_creation = usage.get("cache_creation_input_tokens") or 0
    breakdown = usage.get("cache_creation") or {}
    write_1h = breakdown.get("ephemeral_1h_input_tokens") or 0
    write_5m = breakdown.get("ephemeral_5m_input_tokens")
    if write_5m is None:
        write_5m = max(cache_creation - write_1h, 0)
    cost = (
        input_tokens * p_in
        + write_5m * p_in * 1.25
        + write_1h * p_in * 2.0
        + cache_read * p_cache_read
        + output_tokens * p_out
    ) / 1_000_000
    if (speed or usage.get("speed")) == "fast":
        cost *= FAST_MODE_MULTIPLIER
    server = usage.get("server_tool_use") or {}
    cost += (server.get("web_search_requests") or 0) * WEB_SEARCH_PER_REQUEST
    return cost


# ------------------------------------------------------------------ OpenAI (Codex sessions)
# model -> (input, output, cached input) USD per million tokens; reasoning tokens bill as output.
OPENAI_PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-5": (1.25, 10.0, 0.125),
    "gpt-5-codex": (1.25, 10.0, 0.125),
    "gpt-5-mini": (0.25, 2.0, 0.025),
    "gpt-5-nano": (0.05, 0.40, 0.005),
    "gpt-4.1": (2.0, 8.0, 0.5),
    "gpt-4.1-mini": (0.4, 1.6, 0.1),
    "gpt-4.1-nano": (0.1, 0.4, 0.025),
    "gpt-4o": (2.5, 10.0, 1.25),
    "gpt-4o-mini": (0.15, 0.6, 0.075),
    "o3": (2.0, 8.0, 0.5),
    "o3-pro": (20.0, 80.0, 20.0),
    "o4-mini": (1.1, 4.4, 0.275),
    "codex-mini-latest": (1.5, 6.0, 0.375),
}


def is_openai_model(model: str | None) -> bool:
    m = normalize_model(model) or ""
    return m.startswith(("gpt-", "o1", "o3", "o4", "codex-"))


def openai_price_for(model: str | None) -> tuple[tuple[float, float, float] | None, bool]:
    """((input, output, cached), exact). Newer GPT-5.x/6 variants fall back to GPT-5 rates as an estimate."""
    m = normalize_model(model)
    if not m:
        return None, True
    if m in OPENAI_PRICES:
        return OPENAI_PRICES[m], True
    for known in sorted(OPENAI_PRICES, key=len, reverse=True):
        if m.startswith(known + "-") or m.startswith(known + "."):
            return OPENAI_PRICES[known], True
    if "nano" in m:
        return OPENAI_PRICES["gpt-5-nano"], False
    if "mini" in m:
        return OPENAI_PRICES["gpt-5-mini"], False
    if m.startswith(("gpt-5", "gpt-6", "gpt-", "codex")):
        return OPENAI_PRICES["gpt-5"], False
    if m.startswith(("o1", "o3", "o4")):
        return OPENAI_PRICES["o3"], False
    return None, False


def openai_cost(model: str | None, uncached_input: int, cached_input: int, output: int) -> float:
    prices, _ = openai_price_for(model)
    if not prices:
        return 0.0
    p_in, p_out, p_cached = prices
    return (uncached_input * p_in + cached_input * p_cached + output * p_out) / 1_000_000
