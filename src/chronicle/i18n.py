"""The dashboard's language for the text the server writes: every /api call says which one (X-Interlatch-Lang: en or ja),
so it is chosen per browser, independently of [analysis] language (what Interlatch writes its knowledge in).

tr() and trn() translate while a response is built, never at import time. What is not for the dashboard stays English:
the CLI and the MCP server (they send no header), files Interlatch writes, logs, codes, and anything sent to a model.
Text stored in the database is translated when it is read, where it is one of a known set of templates.
"""

from __future__ import annotations

from contextvars import ContextVar

from .i18n_ja import JA

LANGS = ("en", "ja")
lang: ContextVar[str] = ContextVar("lang", default="en")  # set per request by the server; a new thread starts at "en"


def pick(header: str | None) -> str:
    """The language an X-Interlatch-Lang header asks for: "ja", or "en" for anything else."""
    value = (header or "").strip().lower()
    return value if value in LANGS else "en"


def tr(text: str, /, **kw) -> str:
    """`text` in the current language (English when it has no translation), then formatted with `kw`."""
    if lang.get() == "ja":
        text = JA.get(text, text)
    return text.format(**kw) if kw else text


def trn(n: int, one: str, other: str, /, **kw) -> str:
    """The singular or plural template for `n`, formatted with n and `kw`. Japanese has one form, `other`'s."""
    text = one if n == 1 else other
    if lang.get() == "ja":
        text = JA.get(other, text)
    return text.format(n=n, **kw)
