"""Backend "none": Interlatch records sessions and never analyzes them. Every model call goes through make_runner, so
a runner that is never available stops them all: session analysis, knowledge bases, weekly re-analyses, screening,
glossaries and the dashboard's buttons."""

from __future__ import annotations

from .base import LLMError, LLMResult, Runner


class OffRunner(Runner):
    name, label, cli = "none", "Record only", ""

    def available(self) -> bool:
        return False

    def unavailable_reason(self) -> str:
        from ...core.i18n import tr

        return tr("Analysis is off (record only): sessions are recorded but not analyzed")

    def where(self) -> str:
        return ""

    def local(self) -> bool:
        return True  # nothing is sent anywhere

    def model_label(self, model: str | None = None) -> str:
        return "no model (record only)"

    def describe(self) -> dict:
        return {**super().describe(), "kind": "off", "local": True}

    def _call(self, prompt: str, *, system: str, model: str | None, effort: str | None, timeout: int | None,
              max_budget_usd: float | None) -> tuple[LLMResult, str]:
        raise LLMError(self.unavailable_reason())
