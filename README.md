# CLI Traffic Light

A desktop traffic-light monitor for **Claude Code CLI** and **Codex CLI** chat sessions.

One coloured light per session:

| Light | Meaning |
|---|---|
| 🟢 green | running — the agent is working |
| 🟡 yellow | needs input — the agent is waiting for you |
| 🔴 red | finished — the session has ended |
| ⚪ grey | unknown — the CLI reported a status this build doesn't recognise |

Each row also shows the session's total token usage, its tokens/sec, and whether its
terminal is a **VS Code integrated terminal**.

The monitor is **strictly read-only** toward the CLIs' state directories. It never writes to
them, never opens Codex's live SQLite database, and never signals another process.

## Requirements

- Python 3.12+ with `tkinter`
- [`psutil`](https://pypi.org/project/psutil/)
- A display (X11/Wayland) for the GUI; the `--once` modes are headless

## Install

```bash
git clone https://github.com/HangYu8123/ChafficLight.git
cd ChafficLight
python3 -m venv .venv
.venv/bin/python -m pip install psutil pytest
```

## Usage

```bash
# open the traffic-light window (refreshes every 2s)
.venv/bin/python -m cli_traffic_light

# one plain-text snapshot, then exit
.venv/bin/python -m cli_traffic_light.cli --once

# one JSON snapshot, then exit — strictly headless, never creates a Tk root
.venv/bin/python -m cli_traffic_light.cli --once --json
```

`CLAUDE_CONFIG_DIR` and `CODEX_HOME` override the default `~/.claude` / `~/.codex`.

JSON output shape:

```json
{
  "sessions": [
    {
      "session_id": "...", "agent": "claude", "title": "...", "cwd": "...",
      "state": "running", "tokens_per_sec": 12.5,
      "is_vscode": true, "vscode_confidence": "confident", "pid": 12345,
      "input_tokens": 0, "output_tokens": 0,
      "cache_creation_tokens": 0, "cache_read_tokens": 0, "total_tokens": 0
    }
  ],
  "totals": { "total_tokens": 0 },
  "subagent_totals": { "total_tokens": 0 }
}
```

## How it reads status

**Claude Code** — `$CLAUDE_CONFIG_DIR` (default `~/.claude`):

- `sessions/<pid>.json` — one file per *live* session, carrying `status`
  (`busy` / `shell` / `idle`), `sessionId`, `cwd`, `name`, `updatedAt`, `procStart`.
  `busy` and `shell` are both **running**; `idle` is **needs input**.
- `procStart` is a string holding `/proc/<pid>/stat` field 22 — boot-relative clock ticks,
  *not* epoch seconds. It guards against pid reuse; a missing or unparseable value is
  skipped rather than treated as a dead session.
- `projects/<slug>/<sessionId>.jsonl` — the transcript, for `message.usage`. Subagent
  transcripts under `.../<sessionId>/subagents/` are counted as a **separate** aggregate.
- `ide/<port>.lock` — a VS Code workspace hint, honoured only while its `pid` is alive.

**Codex CLI** — `$CODEX_HOME` (default `~/.codex`):

- `sessions/YYYY/MM/DD/rollout-*.jsonl` — `session_meta`, `task_started`, `task_complete`,
  `turn_aborted`, `token_count`. `turn_aborted` counts as a turn terminator; without it an
  aborted turn stays green forever.
- `session_index.jsonl` — thread titles.
- `state_*.sqlite` is deliberately **never opened**: versioned filename, WAL mode with a
  live writer, and known lock-contention issues.

## Token accounting

The two CLIs report cache tokens differently, so they are normalised differently:

- **Claude** — `cache_read_input_tokens` is a *sibling* of `input_tokens`, so the headline
  `total = input + output + cache_creation` **excludes cache reads**. In practice cache
  reads run ~40× the headline number, so including them would be meaningless.
- **Codex** — `cached_input_tokens` is a *subset of* `input_tokens`, so it is subtracted
  back out: `total = input − cached + cache_write + output`.
- **tokens/sec** is a sliding 60-second window over clamped non-negative deltas — never
  `last − first`, because the counters reset on session resume and context compaction.

Cache-read counts are still reported separately, per session and in the totals.

## VS Code terminal detection

Tried in order, most authoritative first:

1. `TERM_PROGRAM == "vscode"` in the session process's environment → `confident`
2. any of `VSCODE_INJECTION`, `VSCODE_IPC_HOOK_CLI`, `VSCODE_GIT_ASKPASS_NODE`,
   `VSCODE_SHELL_INTEGRATION` → `likely` (catches tmux shadowing `TERM_PROGRAM`)
3. a `code` / `code-server` / `.vscode-server/bin/*/node` process ancestor → `likely`
4. a live `ide/*.lock` whose `workspaceFolders` contains the session's cwd → `likely`

Tier 4 is a workspace match, not proof about the terminal — check `vscode_confidence`, not
just the `is_vscode` boolean, if the distinction matters to you.

## Tests

```bash
env -u PYTEST_ADDOPTS -u PYTEST_PLUGINS -u PYTHONPATH -u PYTHONSTARTUP \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest tests/ -q -p no:cacheprovider --strict-markers
```

84 tests, 0 skipped. Every test builds its own fixture tree under `tmp_path` and points
`CLAUDE_CONFIG_DIR` / `CODEX_HOME` at it, so no test ever reads your real session data. The
GUI tests drive a real (withdrawn) `Tk()` root and read colours back out of the widgets.

## Known limitations

- Codex rollouts carry no pid, so Codex sessions can only reach VS Code detection tier 4.
- Finished Claude sessions have no live process, so they get no VS Code detection.
- A session's `cwd` recovered from a project-directory slug is lossy for directory names
  containing hyphens.
- `tokens/sec` reads `0.0` for a session billed fewer than twice within the 60-second window.
- `snapshot()` runs on the Tk thread, so the window hitches ~0.7 s per refresh on a machine
  with thousands of historical transcripts.
- Status strings, on-disk layouts and rollout formats are **internal** to both CLIs, not
  documented APIs — a CLI upgrade can change them.
