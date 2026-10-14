"""Paths as agents record them, on any operating system: POSIX or Windows (drive, UNC, a file URI's /c:/), the
spellings one path can take, and a project's name from its folder."""

from __future__ import annotations

import re
from pathlib import Path

# A Windows path as agents record it: a drive (C:\ or C:/, or /c:/ as a VS Code file URI's path spells it) or a UNC
# share (\\server\share). Any other path that starts with / is a POSIX one, where a backslash is an ordinary character.
_DRIVE_RE = re.compile(r"[A-Za-z]:[/\\]")
_URI_DRIVE_RE = re.compile(r"/[A-Za-z]:(?:/|$)")
_UNC_RE = re.compile(r"(?:\\\\|//)[^/\\]")


def path_kind(path) -> str | None:
    """"drive" (C:\\x, C:/x, /c:/x), "unc" (\\\\server\\share, //server/share) or "posix" (/x) for an absolute path;
    None for anything else (a relative path, C: alone, not a string)."""
    if not isinstance(path, str) or not path:
        return None
    if _DRIVE_RE.match(path) or _URI_DRIVE_RE.match(path):
        return "drive"
    if _UNC_RE.match(path):
        return "unc"
    return "posix" if path.startswith("/") else None


def is_abs_path(path) -> bool:
    r"""Whether `path` is an absolute path on POSIX or Windows (/x, C:\x, C:/x, \\server\share)."""
    return path_kind(path) is not None


def normalize_project_path(path):
    """One spelling of an absolute path, to compare by: no trailing separator (/ and C:/ kept), and for a Windows path
    forward slashes and an upper-case drive letter (Windows' paths are case-insensitive; the rest of it is kept as
    written). A POSIX path keeps its backslashes. Anything else comes back as it is."""
    kind = path_kind(path)
    if kind is None:
        return path
    if kind == "posix":
        return path.rstrip("/") or "/"
    p = (path[1:] if _URI_DRIVE_RE.match(path) else path).replace("\\", "/")
    if kind == "unc":
        return p.rstrip("/")
    return p[0].upper() + ":/" + p[3:].rstrip("/")


def path_variants(path) -> list[str]:
    """The ways agents may have recorded an absolute path, for an exact match in SQL: on Windows either separator,
    either drive-letter case, and a VS Code URI's /c:/ form; a POSIX path only as it is. Normalized first."""
    p = normalize_project_path(path)
    kind = path_kind(p)
    if kind == "drive":
        out = []
        for q in (p, p[0].lower() + p[1:]):
            out += [q, q.replace("/", "\\"), "/" + q]
        return out
    if kind == "unc":
        return [p, p.replace("/", "\\")]
    return [p]


def abs_path_sql(col: str) -> str:
    """A WHERE clause that keeps the rows whose `col` looks like an absolute path, POSIX or Windows (read them with
    is_abs_path: `_` matches any character)."""
    return f"({col} LIKE '/%' OR {col} LIKE '_:/%' OR {col} LIKE '_:\\%' OR {col} LIKE '\\\\%')"


def clean_abs_path(path) -> str:
    """An absolute path normalized (normalize_project_path) with its . and .. worked out, the same on any system;
    "" for anything that isn't one."""
    import posixpath

    if not is_abs_path(path):
        return ""
    p = normalize_project_path(path)
    head = p[:2] if path_kind(p) == "drive" else ""  # C:
    return normalize_project_path(head + posixpath.normpath(p[len(head):]))


def project_name_for(path: str | None, project_dir: str | None = None) -> str:
    if path:
        return Path(path.rstrip("/")).name or path
    if project_dir:
        return project_dir.strip("-").split("-")[-1]
    return "unknown"
