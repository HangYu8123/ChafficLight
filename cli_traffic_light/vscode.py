"""Detection of sessions whose terminal is a VS Code integrated terminal.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions, and asked it to flag which of those sessions are running in a
VS Code integrated terminal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = ["VSCodeDetection", "detect_vscode"]

#: Environment variables VS Code exports into its integrated terminal.
_ENV_MARKERS = (
    "VSCODE_INJECTION",
    "VSCODE_IPC_HOOK_CLI",
    "VSCODE_GIT_ASKPASS_NODE",
    "VSCODE_SHELL_INTEGRATION",
)


def _basename(name: str) -> str:
    """The trailing component of ``name``, lowercased and without ``.exe``.

    Both separators are honoured whatever platform this runs on, because the
    strings come from another process's ancestry rather than from this one.
    """
    stem = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return stem[:-4] if stem.endswith(".exe") else stem


def _is_vscode_ancestor(name: str) -> bool:
    """Whether an ancestor process name/command belongs to VS Code.

    Compared on the basename because the same process is ``code`` on Linux and
    ``Code.exe`` — usually reported as a full path — on Windows. A bare ``node``
    is not enough: only a ``node`` under a ``.vscode-server`` install path
    counts, so unrelated Node processes never promote a session.
    """
    path = name.replace("\\", "/")
    stem = _basename(path)
    if stem in ("code", "code-server"):
        return True
    return ".vscode-server/bin/" in path and stem == "node"


def _paths_equal(left: str, right: str) -> bool:
    """Whether two paths name the same directory, without touching the disk.

    ``normcase`` lowercases and unifies separators on Windows and does nothing
    at all on POSIX, so a Windows drive letter matches in either case while a
    case-sensitive filesystem stays case-sensitive; ``normpath`` additionally
    settles a trailing separator. Neither reads the filesystem, which matters
    because a ``workspaceFolders`` entry may name a directory that is not there.
    Either side may also be missing or not a string at all, since one of them
    comes from a lock file this app does not write.
    """
    if not (isinstance(left, str) and isinstance(right, str) and left and right):
        return False
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


@dataclass
class VSCodeDetection:
    """Whether a session looks like a VS Code terminal, and how sure we are."""

    detected: bool
    confidence: str


def detect_vscode(
    env: dict,
    ancestor_names: list[str],
    ide_locks: list[dict],
) -> VSCodeDetection:
    """Classify a session from its environment, process ancestry and IDE locks.

    ``ide_locks`` entries look like ``{"pid": int, "alive": bool,
    "workspaceFolders": [str]}``; the session's working directory is supplied as
    ``env["PWD"]``. A lock whose process is dead never promotes a session.
    """
    if env.get("TERM_PROGRAM") == "vscode":
        return VSCodeDetection(True, "confident")

    if any(marker in env for marker in _ENV_MARKERS):
        return VSCodeDetection(True, "likely")

    if any(_is_vscode_ancestor(name) for name in ancestor_names):
        return VSCodeDetection(True, "likely")

    cwd = env.get("PWD")
    for lock in ide_locks:
        folders = lock.get("workspaceFolders") or []
        if lock.get("alive") and any(_paths_equal(cwd, folder) for folder in folders):
            return VSCodeDetection(True, "likely")

    return VSCodeDetection(False, "none")
