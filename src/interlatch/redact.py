"""Best-effort secret redaction for text that leaves the raw archive (LLM digests, Markdown export)."""

from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[REDACTED PRIVATE KEY]"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), "[REDACTED:anthropic-key]"),
    (re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}"), "[REDACTED:api-key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "[REDACTED:github-token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"), "[REDACTED:github-token]"),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"), "[REDACTED:gitlab-token]"),
    (re.compile(r"\bxox[abposrdc]-[A-Za-z0-9%\-]{10,}"), "[REDACTED:slack-token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:aws-key-id]"),
    (re.compile(r"\bASIA[0-9A-Z]{16}\b"), "[REDACTED:aws-key-id]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "[REDACTED:google-key]"),
    (re.compile(r"\bdapi[0-9a-f]{32}(?:-\d+)?\b"), "[REDACTED:databricks-token]"),
    (re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[0-9A-Za-z]{20,}"), "[REDACTED:stripe-key]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"), "[REDACTED:hf-token]"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b"), "[REDACTED:npm-token]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{8,}"), "[REDACTED:jwt]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{20,}=*"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://[^:/\s@]+:)([^@/\s]{3,})(@)"), r"\1[REDACTED]\3"),
    (
        re.compile(
            # the name may carry a prefix, as an environment variable's does (DB_PASSWORD, PGPASSWORD, AWS_SECRET_ACCESS_KEY)
            r"(?i)(\b[a-z0-9_-]*?(?:password|passwd|pwd|secret|client[_-]?secret|api[_-]?key|apikey|access[_-]?key|"
            r"secret[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token|private[_-]?key|token)\b"
            r"[\"']?\s*[:=]\s*[\"']?)([^\s\"'`,;]{6,})"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)\b(AccountKey|SharedAccessKey|sig)=([A-Za-z0-9%+/=]{16,})"), r"\1=[REDACTED]"),
    # HTTP credentials: an Authorization header's (a Basic one is user:password in base64, a short Bearer one escapes
    # the pattern above), curl's -u user:password, and cookies, which are as good as a password while they last
    (re.compile(r"(?i)(\b(?:proxy-)?authorization[\"']?\s*[:=]\s*[\"']?(?:basic|bearer|token)\s+)([^\s\"',;]{4,})"),
     r"\1[REDACTED]"),
    (re.compile(r"(?i)(\bcurl\b[^\n]*?\s(?:-u|--user)[=\s]\s*[\"']?[^\s:\"']+:)([^\s\"']+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(\b(?:set-)?cookie[\"']?\s*:\s*[\"']?)([^\s=;:\"']+=[^\r\n\"']*)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(--cookie[=\s]\s*[\"']?)([^\s=;\"']+=[^\r\n\"']*)"), r"\1[REDACTED]"),
]


def redact(text: str | None) -> str:
    if not text:
        return text or ""
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
