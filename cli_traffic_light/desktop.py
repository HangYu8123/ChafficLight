"""Desktop integration: create or remove a click-to-run launcher for the app.

Original request:
The user asked for ChafficLight to be installable as an app on each platform, or
click-to-run wherever a real application bundle is not possible.

Each platform gets the launcher its desktop actually understands: a Start Menu and
Desktop shortcut on Windows, an application bundle in ``~/Applications`` on macOS, and
a freedesktop desktop entry on Linux. The text each one contains is built by a pure
function so it can be checked without a desktop to install into.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__

__all__ = ["install", "uninstall", "launcher_command", "unavailable_reason"]

APP_NAME = "ChafficLight"
_BUNDLE_ID = "io.github.hangyu8123.chafficlight"
_COMMENT = "Traffic light for Claude Code and Codex CLI sessions"
_ICON_NAME = "chafficlight.png"

# The shell folders each command targets. The launcher goes in the Start Menu and on
# the Desktop; the login entry goes in the Startup folder, which is FOLDERID_Startup
# and per-user, so it needs no elevation.
_LAUNCHER_FOLDERS = ("Programs", "Desktop")
_STARTUP_FOLDERS = ("Startup",)


def _windowless_python() -> str:
    """The interpreter to fall back on, preferring one that opens no console."""
    if sys.platform == "win32":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return sys.executable


def _frozen_command() -> list[str]:
    """What a launcher should run when this build is already frozen.

    A frozen build has no entry point to find and cannot re-run itself as a module:
    `sys.executable` is the bootloader, and ``-m cli_traffic_light`` would reach it as
    an argument rather than as an interpreter option.

    It is not simply `sys.executable` either. A release ships two binaries — the
    windowed app and a console one, so that ``--once`` and these very messages have
    somewhere to print — and it is from the console one that a user runs this command.
    Registering *that* would open a console window at every login, so the windowed
    sibling wins whenever it is there: the bundle's inner executable on macOS, which
    is what launchd can exec, otherwise a plain neighbour.
    """
    running = Path(sys.executable)
    windowed = (
        running.with_name(f"{APP_NAME}.app") / "Contents" / "MacOS" / APP_NAME,
        # Where `unavailable_reason` tells its owner to keep the bundle. Without this
        # the console binary is the only candidate left the moment they comply, and a
        # user who followed the instruction would get a console app at login.
        _app_bundle_path() / "Contents" / "MacOS" / APP_NAME,
        running.with_name(APP_NAME + running.suffix),
    )
    for candidate in windowed:
        if candidate != running and candidate.exists():
            return [str(candidate)]
    return [str(running)]


def launcher_command() -> list[str]:
    """The command a launcher should run.

    Prefers the installed ``chafficlight-gui`` entry point, whose Windows wrapper
    opens no console window, and searches beside the running interpreter before PATH
    so an unactivated virtual environment still resolves. Falls back to running the
    module when the project was not installed as a distribution.

    The search path is always passed explicitly: left to itself, `shutil.which` also
    searches the working directory on Windows and answers with a bare relative name,
    which is useless to a launcher and picks up anything sitting in the directory the
    install happened to be run from.
    """
    if getattr(sys, "frozen", False):
        return _frozen_command()
    search = os.pathsep.join(
        [str(Path(sys.executable).parent), os.environ.get("PATH", "")]
    )
    for name in ("chafficlight-gui", "chafficlight"):
        found = shutil.which(name, path=search)
        if found:
            return [found]
    return [_windowless_python(), "-m", "cli_traffic_light"]


def _launcher_workdir(command: list[str]) -> Path:
    """The directory the launcher should start in.

    The module fallback resolves ``cli_traffic_light`` from the working directory, so
    it only imports when that is the checkout root; an entry point does not care where
    it is started from.
    """
    if "-m" in command:
        return Path(__file__).resolve().parent.parent
    return Path(command[0]).parent


def _desktop_quote(argument: str) -> str:
    """One ``Exec=`` argument, quoted per the Desktop Entry Specification.

    Quoted unconditionally rather than only when a reserved character appears: it is
    always valid, and a home directory containing a space is the common case.
    """
    # A literal backslash needs four: the quoting rule escapes it, and the value is
    # then read through the spec's string escaping, which halves it again.
    escaped = argument.replace("\\", "\\\\\\\\")
    for char in ('"', "`", "$"):
        escaped = escaped.replace(char, "\\" + char)
    return '"' + escaped.replace("%", "%%") + '"'


def desktop_entry_text(command: list[str], icon: Path | None = None) -> str:
    """The freedesktop desktop-entry body launching ``command``.

    ``Icon`` takes an absolute path rather than a theme name: the icon this app ships
    is one square PNG, and installing it into an icon theme would mean claiming a
    size directory the file does not match. The spec accepts an absolute path
    explicitly, and an entry whose icon is missing simply shows none.
    """
    exec_line = " ".join(_desktop_quote(part) for part in command)
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Comment={_COMMENT}\n"
        + (f"Icon={icon}\n" if icon else "")
        + f"Exec={exec_line}\n"
        f"Path={_launcher_workdir(command)}\n"
        "Terminal=false\n"
        "Categories=Utility;Development;\n"
    )


def info_plist_text() -> str:
    """The ``Info.plist`` body for the macOS application bundle."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleName</key><string>{APP_NAME}</string>
    <key>CFBundleExecutable</key><string>{APP_NAME}</string>
    <key>CFBundleIdentifier</key><string>{_BUNDLE_ID}</string>
    <key>CFBundleVersion</key><string>{__version__}</string>
    <key>CFBundleShortVersionString</key><string>{__version__}</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""


def app_launcher_text(command: list[str]) -> str:
    """The bundle's ``Contents/MacOS`` script, which execs ``command``.

    ``CFBundleExecutable`` need not be a compiled binary, and the command is spelled
    with absolute paths because a bundle launched from Finder inherits a minimal PATH.
    """
    spelled = " ".join(shlex.quote(part) for part in command)
    return f"#!/bin/sh\ncd {shlex.quote(str(_launcher_workdir(command)))}\nexec {spelled}\n"


def launch_agent_plist_text(command: list[str]) -> str:
    """The launchd agent that starts ``command`` at login.

    ``RunAtLoad`` defaults to false and means "at login" for an agent, so it has to be
    written explicitly. ``ProgramArguments`` is exec'd directly, which is why it must
    name the executable inside the bundle rather than the ``.app`` directory — the
    frozen `launcher_command` already answers with exactly that path.

    Built through ``plistlib`` rather than by hand, unlike `info_plist_text`: this one
    carries a path the user chose by deciding where to keep the download, and a ``&``
    or ``<`` in it would otherwise produce a file launchd silently refuses.
    """
    return plistlib.dumps(
        {"Label": _BUNDLE_ID, "ProgramArguments": list(command), "RunAtLoad": True}
    ).decode("utf-8")


def _write_launch_agent(path: Path, command: list[str]) -> None:
    """Write the login agent for ``command`` to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(launch_agent_plist_text(command), encoding="utf-8")


def _icon_source() -> Path | None:
    """The icon shipped inside this build, if it is there.

    One lookup for both shapes: an installed distribution keeps it beside the package
    in ``site-packages``, and a frozen build has it unpacked beside the package under
    ``sys._MEIPASS`` — which is why it is copied out before a desktop entry names it.
    """
    icon = Path(__file__).resolve().parent / _ICON_NAME
    return icon if icon.is_file() else None


def _installed_icon_path() -> Path:
    """Where the icon is kept for a desktop entry to point at."""
    data_home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(data_home) / "chafficlight" / _ICON_NAME


def _install_icon(destination: Path) -> None:
    """Put the icon somewhere that outlives this process.

    A onefile build unpacks itself into a temporary directory deleted on exit, so an
    entry pointing at `_icon_source` would go blank the moment the installer finished.
    """
    source = _icon_source()
    if source is None:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _write_desktop_entry(path: Path, command: list[str], icon: Path | None) -> None:
    """Write the desktop entry for ``command`` to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(desktop_entry_text(command, icon), encoding="utf-8")


def _write_app_bundle(path: Path, command: list[str]) -> None:
    """Write a minimal macOS application bundle for ``command`` at ``path``."""
    shutil.rmtree(path, ignore_errors=True)  # never merge onto a previous bundle
    contents = path / "Contents"
    executable = contents / "MacOS" / APP_NAME
    executable.parent.mkdir(parents=True, exist_ok=True)
    (contents / "Info.plist").write_text(info_plist_text(), encoding="utf-8")
    executable.write_text(app_launcher_text(command), encoding="utf-8")
    executable.chmod(0o755)


def _desktop_file_path() -> Path:
    """Where the desktop entry belongs, honouring ``XDG_DATA_HOME``."""
    data_home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(data_home) / "applications" / "chafficlight.desktop"


def _autostart_entry_path() -> Path:
    """Where the freedesktop autostart entry belongs, honouring ``XDG_CONFIG_HOME``.

    A separate directory from the application entry, and a separate file: the
    Autostart specification reuses the Desktop Entry format but reads it from
    ``$XDG_CONFIG_HOME/autostart`` only.
    """
    config_home = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config_home) / "autostart" / "chafficlight.desktop"


def _app_bundle_path() -> Path:
    """Where the application bundle belongs."""
    return Path.home() / "Applications" / f"{APP_NAME}.app"


def _launch_agent_path() -> Path:
    """Where the per-user login agent belongs."""
    return Path.home() / "Library" / "LaunchAgents" / f"{_BUNDLE_ID}.plist"


# Reads its inputs from the environment so that no path is ever interpolated into a
# command line: a path is data, and one containing a quote would otherwise be able to
# inject PowerShell into this installer.
_SHORTCUT_PS = """
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
foreach ($link in ($env:CHAFFICLIGHT_LINKS -split "`n")) {
    $shortcut = $shell.CreateShortcut($link)
    $shortcut.TargetPath = $env:CHAFFICLIGHT_TARGET
    $shortcut.Arguments = $env:CHAFFICLIGHT_ARGS
    $shortcut.WorkingDirectory = $env:CHAFFICLIGHT_WORKDIR
    $shortcut.Description = $env:CHAFFICLIGHT_DESCRIPTION
    $shortcut.Save()
}
"""

# The shell's own folder table, because those directory names are localised. Written
# straight to the console rather than returned, so the output encoding is ours and the
# formatter cannot wrap a long path onto a second line. Which folders to report is
# passed as environment data for the same reason the shortcut paths are.
_FOLDERS_PS = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$shell = New-Object -ComObject WScript.Shell
foreach ($name in ($env:CHAFFICLIGHT_FOLDERS -split "`n")) {
    [Console]::Out.WriteLine($shell.SpecialFolders($name))
}
"""

# CREATE_NO_WINDOW, spelled out because the attribute only exists on Windows.
_CREATE_NO_WINDOW = 0x08000000


def _powershell(script: str, extra_env: dict[str, str] | None = None) -> str:
    """Run ``script`` through Windows PowerShell and return its stdout.

    Every standard stream is redirected and no console is created, because a frozen
    windowed build inherits no valid handles: leaving one attached raises ``[WinError
    6] The handle is invalid`` before the script runs, and creating a console flashes
    a window at a user who double-clicked an app. stderr is captured rather than
    discarded so `CalledProcessError` still carries what COM said.
    """
    # Spelled absolutely: given a bare name, CreateProcess searches the calling
    # program's own directory and the working directory before PATH, and a compiled
    # release runs from whichever folder its owner unpacked the download into.
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    result = subprocess.run(
        [
            str(system32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**os.environ, **(extra_env or {})},
        creationflags=_CREATE_NO_WINDOW,
        check=True,
    )
    return result.stdout


def _shortcut_env(links: list[Path], command: list[str]) -> dict[str, str]:
    """The environment `_SHORTCUT_PS` reads its paths from."""
    return {
        "CHAFFICLIGHT_LINKS": "\n".join(str(link) for link in links),
        "CHAFFICLIGHT_TARGET": command[0],
        "CHAFFICLIGHT_ARGS": subprocess.list2cmdline(command[1:]),
        "CHAFFICLIGHT_WORKDIR": str(_launcher_workdir(command)),
        "CHAFFICLIGHT_DESCRIPTION": _COMMENT,
    }


def _parse_folders(stdout: str) -> list[Path]:
    """The shortcut paths named by `_FOLDERS_PS` output.

    Anything that is not an absolute path is dropped rather than turned into a
    directory: this is the one value in the module that is parsed rather than built.
    """
    folders = [Path(line.strip()) for line in stdout.splitlines() if line.strip()]
    return [folder / f"{APP_NAME}.lnk" for folder in folders if folder.is_absolute()]


def _shortcut_paths(folders: tuple[str, ...]) -> list[Path]:
    """The shortcut path inside each named shell folder."""
    return _parse_folders(
        _powershell(_FOLDERS_PS, {"CHAFFICLIGHT_FOLDERS": "\n".join(folders)})
    )


def _is_our_bundle(path: Path) -> bool:
    """Whether the bundle at ``path`` is a launcher this module wrote.

    `_write_app_bundle` opens with `rmtree`, and `_app_bundle_path` is now also where
    someone who downloaded a release is told to keep the real app. The launcher this
    module writes has the ``/bin/sh`` script `app_launcher_text` builds as its
    executable, where a compiled bundle has a Mach-O binary — so the first line tells
    a shim it may replace from an application it must not.
    """
    try:
        return (path / "Contents" / "MacOS" / APP_NAME).read_bytes()[:9] == b"#!/bin/sh"
    except OSError:
        return False


def unavailable_reason() -> str | None:
    """Why the launcher commands do not apply here, if they do not.

    Both cases are the same hazard from opposite ends: `_write_app_bundle` starts by
    deleting whatever stands at `_app_bundle_path`, and with compiled releases that is
    no longer always a shim this module made. A frozen build *is* the app, so
    installing would delete the running program and leave a stub pointing into the
    tree it just erased; an ordinary checkout would do the same to the copy its owner
    downloaded. `rmtree` is not the Trash, so neither is recoverable.

    Autostart is unaffected and deliberately not gated: it writes a login agent
    beside the bundle, never over it.
    """
    if sys.platform != "darwin":
        return None
    if getattr(sys, "frozen", False):
        return (
            f"this build already is the app — move {APP_NAME}.app to ~/Applications "
            "instead of installing a launcher for it"
        )
    bundle = _app_bundle_path()
    if bundle.exists() and not _is_our_bundle(bundle):
        return f"{bundle} is a real application, not a launcher — refusing to replace it"
    return None


def _targets(autostart: bool = False) -> list[Path]:
    """What this platform's launcher — or login entry — consists of, installed or not."""
    if sys.platform == "win32":
        return _shortcut_paths(_STARTUP_FOLDERS if autostart else _LAUNCHER_FOLDERS)
    if sys.platform == "darwin":
        return [_launch_agent_path() if autostart else _app_bundle_path()]
    if autostart:
        return [_autostart_entry_path()]
    # The icon belongs to the launcher: an ELF carries none, so the entry names a file
    # that has to be installed and removed with it. The autostart entry points at the
    # same copy without owning it, and simply shows no icon when it is not there.
    return [_desktop_file_path(), _installed_icon_path()]


def install(autostart: bool = False) -> list[Path]:
    """Create this platform's click-to-run launcher, returning what was created."""
    command = launcher_command()
    targets = _targets(autostart)
    if sys.platform == "win32":
        # Only when the shell actually named a folder: an empty link list would reach
        # PowerShell as one empty string and fail CreateShortcut inside the installer.
        for link in targets:
            link.parent.mkdir(parents=True, exist_ok=True)
        if targets:
            _powershell(_SHORTCUT_PS, _shortcut_env(targets, command))
    elif sys.platform == "darwin":
        if autostart:
            _write_launch_agent(targets[0], command)
        else:
            _write_app_bundle(targets[0], command)
    else:
        icon = _installed_icon_path()
        if not autostart:
            _install_icon(icon)
        # The autostart specification reuses the Desktop Entry format verbatim, so the
        # two Linux entries differ only in which directory they land in.
        _write_desktop_entry(targets[0], command, icon if icon.is_file() else None)
    # Report what is actually on disk, so a launcher that failed to appear is not
    # announced as created.
    return [target for target in targets if target.exists()]


def uninstall(autostart: bool = False) -> list[Path]:
    """Remove whatever `install` created, returning what was actually removed."""
    removed = []
    for target in _targets(autostart):
        # A symlinked launcher must be unlinked, not walked: is_dir() follows it, and
        # rmtree refuses a symlink, while a broken one is not exists() at all.
        if target.is_symlink():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        else:
            continue
        removed.append(target)
    return removed
