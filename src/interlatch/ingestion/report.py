"""What a sync did: sessions new, updated and unchanged, files archived, errors, and the sessions it touched."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SyncReport:
    sessions_new: int = 0
    sessions_updated: int = 0
    sessions_unchanged: int = 0
    sessions_missing_source: int = 0
    files_archived: int = 0
    bytes_archived: int = 0
    history_sessions: int = 0
    memory_items: int = 0
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    touched: list[str] = field(default_factory=list)
    analyses_imported: int = 0

    def summary(self) -> str:
        return (
            f"{self.sessions_new} new, {self.sessions_updated} updated, {self.sessions_unchanged} unchanged sessions; "
            f"{self.files_archived} files archived ({self.bytes_archived / 1e6:.1f} MB); "
            f"{self.history_sessions} history-only sessions; {self.memory_items} memory notes; "
            + (f"{self.analyses_imported} analyses from other computers; " if self.analyses_imported else "")
            + f"{len(self.errors)} errors in {self.seconds:.1f}s"
        )
