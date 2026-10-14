"""The language Interlatch writes analyses and knowledge in ([analysis] language), as a prompt says it."""

from __future__ import annotations

from ..core.config import Config

# What a prompt says instead of (or besides) its English-writing rule when [analysis] language is "ja"
JAPANESE = (
    "Write all prose in Japanese: natural and concise. Short fields may be fragments and need not use です/ます. Put a "
    "half-width space between Japanese and Latin letters or digits. Keep identifiers, commands, file paths, config keys, "
    "URLs, error messages, quotes and code verbatim, in their original language. JSON keys and every enum or code value "
    "the schema lists (kind, scope, confidence, outcome, reason, verdict and the like) stay exactly as listed, in English."
)


def written_in(cfg: Config, system: str, english: str = "", note: str = "") -> str:
    """`system` in the language Interlatch writes in ([analysis] language). English leaves it exactly as it is; Japanese
    swaps its English-writing sentence (`english`) for JAPANESE and `note`, or appends them when it has none."""
    if cfg.analysis.language != "ja":
        return system
    rule = " ".join(x for x in (JAPANESE, note) if x)
    return system.replace(english, rule) if english and english in system else f"{system}\n\n{rule}"
