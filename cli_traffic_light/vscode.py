"""Detection of sessions whose terminal is a VS Code integrated terminal.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions, and asked it to flag which of those sessions are running in a
VS Code integrated terminal.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["VSCodeDetection", "detect_vscode"]

#: Environment variables VS Code exports into its integrated terminal.
_ENV_MARKERS = (
    "VSCODE_INJECTION",
    "VSCODE_IPC_HOOK_CLI",
    "VSCODE_GIT_ASKPASS_NODE",
    "VSCODE_SHELL_INTEGRATION",
)


def _is_vscode_ancestor(name: str) -> bool:
    """Whether an ancestor process name/command belongs to VS Code.

    A bare ``node`` is not enough — only a ``node`` under a ``.vscode-server``
    install path counts, so unrelated Node processes never promote a session.
    """
    if name in ("code", "code-server"):
        return True
    return ".vscode-server/bin/" in name and name.endswith("/node")


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
        if lock.get("alive") and cwd in lock.get("workspaceFolders", []):
            return VSCodeDetection(True, "likely")

    return VSCodeDetection(False, "none")
