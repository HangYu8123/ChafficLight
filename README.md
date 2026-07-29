# CLI Traffic Light

A desktop traffic-light monitor for **Claude Code CLI** and **Codex CLI** chat sessions.

**One** traffic light for both CLIs together — a Claude session and a Codex session light
the same three lamps, and the number inside a lamp is how many sessions are in that state:

| Lamp | Meaning |
|---|---|
| 🟢 green | running — the agent is working and wants nothing from you |
| 🔴 red | needs input — the agent **asked you something** and cannot continue until you answer |
| 🟡 yellow | idle — the turn is over; the session is alive and waiting for your next prompt |

The lamps read the way a road signal does, from the driver's seat: **green** you may carry
on, **red** you must stop and act before anything moves again, **yellow** the turn is over
and the session is holding. So the colour tracks *what you have to do*, not how bad things
are.

Red is deliberately narrow. It means a permission prompt, a question or another dialog is
open on that session right now — nothing else earns it, so a red light always means *go and
answer it*. A session that merely finished its turn is yellow, not red.

The light carries only the sessions you can still act on. One that has **ended**, or whose
status this build does not recognise, is not on the face at all: there is nothing to do
about either, and giving them a lamp would pad its count with sessions you cannot act on.
Both are still reported in full by `--once` / `--once --json`, as `finished` and `unknown`.

Nothing is listed at all — on the face or in `--once` — once it has been quiet for **24
hours**. A session with no session-file update, no transcript record and no rollout write
in a day is history rather than work in progress, and reading every transcript ever
written is what made a snapshot take seconds.

The window is a small always-on-top widget with no title bar and a transparent backdrop: a
horizontal signal face, the session count inside each lamp, and the total token usage and
tokens/sec underneath. Drag it anywhere by the light itself; the ✕ in its corner (or `Esc`)
closes it. The per-session detail, including **VS Code integrated terminal** detection, is
in `--once` / `--once --json`.

The monitor is **strictly read-only** toward the CLIs' state directories. It never writes to
them, never opens Codex's live SQLite database, and never signals another process.

## Requirements

- Python 3.12+ with `tkinter`. It is part of the standard library but is packaged
  separately on some systems — `sudo apt install python3-tk` on Debian/Ubuntu,
  `brew install python-tk` on macOS. `pip install tk` is an unrelated package and does
  not help.
- [`psutil`](https://pypi.org/project/psutil/) — installed for you by the commands below
- Linux, macOS or Windows
- A desktop session for the GUI (X11/Wayland on Linux); the `--once` modes are headless

## Download the app

The [latest release](https://github.com/HangYu8123/ChafficLight/releases/latest)
carries one compiled archive per platform. Nothing else is needed — no Python, no
virtual environment, no `pip`.

| Platform | Archive | What is inside |
|---|---|---|
| Windows | `ChafficLight-<version>-windows.zip` | `ChafficLight.exe` and `chafficlight-cli.exe` |
| macOS | `ChafficLight-<version>-macos.tar.gz` | `ChafficLight.app` and `chafficlight-cli` |
| Linux | `ChafficLight-<version>-linux.tar.gz` | a single `ChafficLight` binary |

Unpack it and start `ChafficLight`. On macOS, move `ChafficLight.app` to
`/Applications` or `~/Applications` first.

There are **two** programs on Windows and macOS because a windowed build attaches to
no console: `ChafficLight` opens the widget, and `chafficlight-cli` is the one that can
still print, so `--once`, `--json` and the messages below come from it. On Linux one
binary does both.

The builds are unsigned, so the first launch needs one extra click:

- **Windows** — SmartScreen says the publisher is unrecognised: *More info* →
  *Run anyway*.
- **macOS** — Gatekeeper blocks it. Open *System Settings* → *Privacy & Security*,
  find ChafficLight in the Security section and choose *Open Anyway*, then enter your
  password. The button appears for about an hour after you first try to open the app.

## Start it when you log in

```bash
chafficlight --enable-autostart      # or: ChafficLight --enable-autostart
```

registers the app with your desktop's own login mechanism, always pointing at the
windowed program even when you run the command from the console one:

| Platform | What gets created |
|---|---|
| Windows | `ChafficLight.lnk` in the Startup folder |
| macOS | `~/Library/LaunchAgents/io.github.hangyu8123.chafficlight.plist`, `RunAtLoad` |
| Linux | `~/.config/autostart/chafficlight.desktop` |

`--disable-autostart` removes it again. It records where the program is **now**, so
re-run it after moving the app; the flags combine freely with `--install-desktop`,
since having an icon and starting at login are separate questions.

## Install from source

```bash
pipx install git+https://github.com/HangYu8123/ChafficLight.git
pipx ensurepath     # only if ~/.local/bin is not on your PATH yet
```

Or from a checkout, if you want to hack on it:

```bash
git clone https://github.com/HangYu8123/ChafficLight.git
cd ChafficLight
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
```

On Windows (PowerShell), substitute `py -3.12` for `python3` and
`.venv\Scripts\python.exe` for `.venv/bin/python`.

Either route installs two commands:

| Command | What it is for |
|---|---|
| `chafficlight` | everything you type, including `--once` / `--json` |
| `chafficlight-gui` | what a desktop launcher runs. On Windows it is wrapped in a `pythonw` executable so no console window appears — which also means it has **no usable stdout**, so `--once` prints nothing through it. |

## Make it click-to-run

```bash
chafficlight --install-desktop
```

creates the launcher your desktop understands, pointing at `chafficlight-gui`:

| Platform | What gets created |
|---|---|
| Windows | `ChafficLight.lnk` in the Start Menu and on the Desktop |
| macOS | `~/Applications/ChafficLight.app` |
| Linux | `~/.local/share/applications/chafficlight.desktop` |

`chafficlight --uninstall-desktop` removes it again. Both print exactly which launchers
they touched; nothing else is written except the standard directories above when they do
not exist yet.

On Linux the entry also gets an `Icon=` key, pointing at a copy of the app icon installed
to `$XDG_DATA_HOME/chafficlight/` — an ELF carries no icon of its own, so the file has to
live somewhere the entry can keep naming. `--uninstall-desktop` removes that copy too.

The launcher records the absolute path of `chafficlight-gui` as installed, so the virtual
environment does not have to be activated — or even be on `PATH` — for the icon to work.
The macOS bundle is built on your machine rather than downloaded, so it carries no
quarantine attribute and Gatekeeper does not prompt for it.

On macOS this command refuses when `~/Applications/ChafficLight.app` is a real
application rather than a launcher it wrote earlier — a downloaded release lives at
exactly that path, and installing over it would delete it outright.

## Build the app yourself

```bash
python -m pip install -e ".[build]"
python tools/build_app.py
```

leaves this platform's programs and one archive in `dist/`. PyInstaller is not a
cross-compiler, so it only ever builds for the machine it runs on; the
`.github/workflows/build-apps.yml` matrix is what produces all three, and it runs each
artifact it builds rather than only uploading it.

The app icon is the single `cli_traffic_light/chafficlight.png`. Windows and macOS
executables embed it — Pillow, part of the `build` extra, converts it to `.ico` and
`.icns` during the build — so replacing the icon means replacing that one file and
nothing else. It has no alpha channel today, so it renders as a full square rather than
a shaped mark; an RGBA image drops in without any code change.

## Usage

```bash
# open the floating traffic-light widget (refreshes every 2s; ✕ or Esc closes it)
chafficlight

# one plain-text snapshot, then exit
chafficlight --once

# one JSON snapshot, then exit — strictly headless, never creates a Tk root
chafficlight --once --json
```

Without installing, the same three commands are `python3 -m cli_traffic_light`,
`... -m cli_traffic_light --once` and `... --once --json`; on Windows use
`.venv\Scripts\python.exe`.

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
  (`busy` / `shell` / `waiting` / `idle`), `sessionId`, `cwd`, `name`, `updatedAt`,
  `procStart`. `busy` and `shell` are both **running** — `shell` is a finished turn with a
  background shell command still executing, so work really is in flight. `waiting` is the
  one and only **needs input**: the CLI writes it while a dialog blocks the agent, together
  with a `waitingFor` note reading `permission prompt`, `input needed`, `sandbox request`,
  `worker request` or `dialog open`. `idle` is **idle** — ready for your next prompt, which
  is not the same as asking you for one.
- `procStart` guards against pid reuse. It is *not* epoch seconds, and its units differ by
  platform: on Linux it is `/proc/<pid>/stat` field 22 (boot-relative clock ticks, matched
  exactly); on Windows it is .NET `DateTime.Ticks` — 100 ns units since 0001-01-01 in
  **local** time, because `Process.StartTime` is built with `DateTime.FromFileTime`, which
  returns a `Local`-kind value. The Windows side is re-derived from `psutil`'s
  `create_time()`, a float that loses about a microsecond at epoch magnitudes, so it is
  matched within 1 ms rather than exactly. A missing or unparseable value is skipped
  rather than treated as a dead session.
- `projects/<slug>/<sessionId>.jsonl` — the transcript, for `message.usage` and for the
  session's `cwd`. Subagent transcripts under `.../<sessionId>/subagents/` are counted as a
  **separate** aggregate.
- `ide/<port>.lock` — a VS Code workspace hint, honoured only while its `pid` is alive.

**Codex CLI** — `$CODEX_HOME` (default `~/.codex`):

- `sessions/YYYY/MM/DD/rollout-*.jsonl` — `session_meta`, `task_started`, `task_complete`,
  `turn_aborted`, `token_count`. `turn_aborted` counts as a turn terminator; without it an
  aborted turn stays green forever. `task_complete` and `turn_aborted` are both **idle**,
  never needs input: Codex filters approval requests out of the rollout before writing it,
  so a finished turn and a cancelled one are the only turn ends it records. A rollout whose
  `session_meta` carries `thread_source: subagent` is skipped, so a subagent never appears
  as a session of its own.
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
3. a `code` / `code-server` / `.vscode-server/bin/*/node` process ancestor → `likely`.
   Matched on the basename, case-insensitively and ignoring any `.exe`, so Windows'
   `G:\Microsoft VS Code\Code.exe` counts exactly as Linux' `code` does.
4. a live `ide/*.lock` whose `workspaceFolders` contains the session's cwd → `likely`.
   Compared with `os.path.normcase`/`normpath`, so a Windows lock writing `c:\...` matches
   a session recording `C:\...` while POSIX paths stay case-sensitive. Nothing is read
   from disk, since a workspace folder may no longer exist.

Tier 4 is a workspace match, not proof about the terminal — check `vscode_confidence`, not
just the `is_vscode` boolean, if the distinction matters to you.

## Tests

```bash
env -u PYTEST_ADDOPTS -u PYTEST_PLUGINS -u PYTHONPATH -u PYTHONSTARTUP \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest tests/ -q -p no:cacheprovider --strict-markers
```

On Windows (PowerShell):

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider --strict-markers
```

219 tests, 0 skipped. Every test builds its own fixture tree
under `tmp_path` and points `CLAUDE_CONFIG_DIR` / `CODEX_HOME` at it, so no test ever reads
your real session data. The GUI tests drive a real (withdrawn) `Tk()` root and read the
lamp colours, counts, figures and item geometry back off the canvas; the transparency test
asserts that the backdrop *agrees with* whether the window manager accepted the colour key,
which is a real check on Windows and on X11 alike rather than a skip. Tests that need a `procStart` take it from the same lookup the
reader uses, so the fixture cannot drift from the platform's own format.

## Known limitations

- Codex rollouts carry no pid, so Codex sessions can only reach VS Code detection tier 4.
- Finished Claude sessions have no live process, so they get no VS Code detection.
- A finished session's `cwd` comes from its transcript records. Only when no record carries
  one does it fall back to decoding the project-directory slug, which is irreversibly
  lossy: the slug maps `\`, `/`, `:` and `.` all onto `-`, so `...FindPapers--github` is
  equally `FindPapers\.github` and `FindPapers\-github`.
- On Windows the pid-reuse guard matches within 1 ms rather than exactly, so a pid reused
  by a process started inside that window would not be caught.
- `tokens/sec` reads `0.0` for a session billed fewer than twice within the 60-second window.
- The transparent backdrop uses `-transparentcolor`, which Tk documents as **Windows-only**.
  macOS and Linux fall back to an opaque dark window; everything else is identical.
- The widget is undecorated, so it has no taskbar entry and the window manager cannot close
  it. The ✕ and `Esc` are the only ways out, and the transparent area is click-through on
  Windows — so drag it by the light, not by the space around it.
- `snapshot()` runs on the Tk thread, so the window hitches while it reads. The first one
  parses every transcript inside the 24-hour bound; later ones re-parse only the files that
  changed, but still stat every transcript and read each live session's environment through
  `psutil`. On the development machine, with 9 sessions, that is ~1.2 s once and ~0.26 s per
  refresh after.
- **Codex sessions can never show red.** Codex's rollout writer filters approval and
  elicitation events out before the file is written, so nothing on disk says a Codex session
  is blocked on you. A Codex session sitting on an approval prompt therefore reads green
  until 15 minutes of mtime staleness make it `finished` and drop it off the light. The only
  signal that would fix this is Codex's push-based `notify` hook, which needs config and a
  writer process — it would cost this app its read-only, zero-config property, so it was not
  taken. **Red is a Claude-only light**: treat a green Codex session that has not moved in a
  while as worth a look.
- **`finished` means different things per CLI, and it leaves the light.** For Claude it is a
  transcript whose process is gone. For Codex nothing records that a session ended, so it is
  only "no rollout write for 15 minutes" — a Codex chat you left alone becomes `finished`
  and disappears from the face while it is still sitting at its prompt, where yellow would
  describe it better. Use `--once` to see it.
- A Claude session only reaches red if its pid passes the `procStart` liveness check
  first, so a false negative there hides the light rather than showing a wrong one.
- The downloadable builds are **unsigned**, so Windows SmartScreen and macOS Gatekeeper
  both warn on first launch (see *Download the app*). Signing needs a code-signing
  certificate and, on macOS, notarisation.
- The macOS archive is built on an **Apple-silicon** runner, so it is arm64-only; Intel
  Macs need a build from source. The Linux binary is built against glibc 2.35 and is
  forward-compatible only, so it will not start on a distribution older than that.
- `--enable-autostart` records where the program is at the moment you run it. Move the
  app afterwards and the login entry points at nothing — re-run the flag to fix it.
- Status strings, on-disk layouts and rollout formats are **internal** to both CLIs, not
  documented APIs — a CLI upgrade can change them. Claude Code's `waitingFor` vocabulary is
  documented and is still growing (`input needed` arrived in 2.1.212), which is why this
  build keys on `status == "waiting"` and never on the `waitingFor` text.
