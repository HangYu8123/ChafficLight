"""Frozen acceptance tests for VS Code integrated-terminal detection.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions, and asked it to flag which of those sessions are running in a
VS Code integrated terminal.
"""

import os

import pytest

from cli_traffic_light.vscode import detect_vscode

CWD = "/work/project-a"


def test_term_program_vscode_is_confident():
    result = detect_vscode({"PWD": CWD, "TERM_PROGRAM": "vscode"}, [], [])
    assert result.detected is True
    assert result.confidence == "confident"


def test_tmux_shadowing_term_program_still_detects_via_ipc_hook():
    result = detect_vscode(
        {"PWD": CWD, "TERM_PROGRAM": "tmux", "VSCODE_IPC_HOOK_CLI": "/run/user/1000/vscode-ipc-1.sock"},
        ["bash", "tmux: server", "systemd"],
        [],
    )
    assert result.detected is True
    assert result.confidence == "likely"


@pytest.mark.parametrize(
    "marker",
    ["VSCODE_INJECTION", "VSCODE_GIT_ASKPASS_NODE", "VSCODE_SHELL_INTEGRATION"],
)
def test_each_vscode_environment_marker_is_likely(marker):
    result = detect_vscode({"PWD": CWD, marker: "1"}, ["bash"], [])
    assert result.detected is True
    assert result.confidence == "likely"


@pytest.mark.parametrize(
    "ancestor",
    [
        "code",
        "code-server",
        "/home/someone/.vscode-server/bin/a1b2c3d4e5f6a7b8/node",
    ],
)
def test_vscode_process_ancestry_is_likely(ancestor):
    result = detect_vscode({"PWD": CWD}, ["bash", ancestor, "systemd"], [])
    assert result.detected is True
    assert result.confidence == "likely"


@pytest.mark.parametrize(
    "ancestor",
    [
        "Code.exe",
        r"G:\Microsoft VS Code\Code.exe",
        r"C:\Users\someone\AppData\Local\Programs\Microsoft VS Code\Code.exe",
    ],
)
def test_windows_vscode_executable_ancestry_is_likely(ancestor):
    """Windows reports ``Code.exe``, usually as a full path, where Linux says ``code``."""
    result = detect_vscode({"PWD": CWD}, ["powershell.exe", ancestor, "explorer.exe"], [])
    assert result.detected is True
    assert result.confidence == "likely"


def test_plain_terminal_with_a_plain_node_ancestor_is_not_detected():
    result = detect_vscode(
        {"PWD": CWD, "TERM_PROGRAM": "gnome-terminal"},
        ["bash", "node", "systemd"],
        [],
    )
    assert result.detected is False
    assert result.confidence == "none"


def test_dead_ide_lock_matching_the_cwd_must_not_promote():
    result = detect_vscode(
        {"PWD": CWD},
        ["bash"],
        [{"pid": 999_002, "alive": False, "workspaceFolders": [CWD]}],
    )
    assert result.detected is False
    assert result.confidence == "none"


def test_live_ide_lock_matching_the_cwd_is_likely():
    result = detect_vscode(
        {"PWD": CWD},
        ["bash"],
        [{"pid": 999_002, "alive": True, "workspaceFolders": [CWD]}],
    )
    assert result.detected is True
    assert result.confidence == "likely"


def test_live_ide_lock_with_a_trailing_separator_still_matches():
    result = detect_vscode(
        {"PWD": CWD},
        ["bash"],
        [{"pid": 999_002, "alive": True, "workspaceFolders": [CWD + "/"]}],
    )
    assert result.detected is True
    assert result.confidence == "likely"


def test_ide_lock_differing_only_in_drive_letter_case_matches_on_windows():
    """VS Code writes ``c:\\...`` while the session records ``C:\\...``.

    Windows paths are case-insensitive so this must match there; POSIX paths
    are case-sensitive so it must not match here.
    """
    result = detect_vscode(
        {"PWD": r"C:\work\project-a"},
        ["bash"],
        [{"pid": 999_002, "alive": True, "workspaceFolders": [r"c:\work\project-a"]}],
    )
    assert result.detected is (os.name == "nt")


@pytest.mark.parametrize("folders", [[None], [123], ["", CWD + "x"], "not-a-list"])
def test_malformed_workspace_folders_are_ignored_not_raised(folders):
    """The lock file is written by VS Code, not by us, so it is untrusted."""
    result = detect_vscode(
        {"PWD": CWD},
        ["bash"],
        [{"pid": 999_002, "alive": True, "workspaceFolders": folders}],
    )
    assert result.detected is False


def test_live_ide_lock_for_another_workspace_must_not_promote():
    result = detect_vscode(
        {"PWD": CWD},
        ["bash"],
        [{"pid": 999_002, "alive": True, "workspaceFolders": ["/work/project-b"]}],
    )
    assert result.detected is False
    assert result.confidence == "none"


def test_term_program_wins_over_weaker_signals():
    result = detect_vscode(
        {"PWD": CWD, "TERM_PROGRAM": "vscode", "VSCODE_INJECTION": "1"},
        ["code"],
        [{"pid": 999_002, "alive": True, "workspaceFolders": [CWD]}],
    )
    assert result.detected is True
    assert result.confidence == "confident"
