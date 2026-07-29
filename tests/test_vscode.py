"""Frozen acceptance tests for VS Code integrated-terminal detection.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions, and asked it to flag which of those sessions are running in a
VS Code integrated terminal.
"""

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
