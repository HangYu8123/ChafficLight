"""Frozen acceptance tests for the click-to-run desktop launchers.

Original request:
The user asked for ChafficLight to be installable as an app on each platform, or
click-to-run wherever a real application bundle is not possible.

The launcher bodies are built by pure functions, so every platform's artifact can be
checked from any platform; only the Windows COM call itself needs Windows, and what is
asserted there is the environment it is handed rather than the shortcut it writes.
"""

import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

from cli_traffic_light import cli, desktop

COMMAND = ["/home/u/.local/bin/chafficlight-gui"]


def test_desktop_entry_carries_the_required_keys():
    text = desktop.desktop_entry_text(COMMAND)
    assert "[Desktop Entry]" in text
    assert "Type=Application" in text
    assert f"Name={desktop.APP_NAME}" in text
    assert 'Exec="/home/u/.local/bin/chafficlight-gui"' in text
    assert "Terminal=false" in text
    assert "Categories=Utility;Development;" in text


def test_desktop_entry_names_an_icon_when_given_one():
    # An absolute path, not a theme name: the app ships one square PNG, and installing
    # it into an icon theme would claim a size directory it does not match.
    # Built from the Path rather than spelled out, so "absolute" means absolute on
    # whichever platform runs the suite.
    icon = Path.home() / "icons" / "chafficlight.png"
    assert f"Icon={icon}\n" in desktop.desktop_entry_text(COMMAND, icon)


def test_desktop_entry_omits_the_icon_key_rather_than_naming_nothing():
    assert "Icon=" not in desktop.desktop_entry_text(COMMAND)


def test_the_icon_ships_inside_the_package():
    # Beside the package is the one place an installed distribution and a frozen build
    # both resolve, and pyproject ships it as package data for the first.
    assert desktop._icon_source() is not None
    assert desktop._icon_source().read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_installing_copies_the_icon_out_of_the_build(tmp_path, monkeypatch):
    # A onefile build unpacks into a temp directory deleted on exit, so an entry
    # pointing where the icon currently sits would go blank once the installer ended.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    destination = desktop._installed_icon_path()
    desktop._install_icon(destination)
    assert destination.read_bytes() == desktop._icon_source().read_bytes()


def test_a_build_with_no_icon_installs_no_icon(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "_icon_source", lambda: None)
    desktop._install_icon(tmp_path / "nested" / "icon.png")
    assert not (tmp_path / "nested").exists()


def test_the_linux_launcher_owns_the_icon_and_autostart_does_not(tmp_path, monkeypatch):
    # Removing the launcher must take its icon with it; removing an autostart entry
    # must not blank the launcher's.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert desktop._installed_icon_path() in desktop._targets()
    assert desktop._installed_icon_path() not in desktop._targets(autostart=True)


def test_install_then_uninstall_round_trips_the_icon(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(desktop.shutil, "which", lambda name, path=None: None)

    created = desktop.install()
    icon = desktop._installed_icon_path()
    assert icon in created and icon.is_file()
    entry = tmp_path / "applications" / "chafficlight.desktop"
    assert f"Icon={icon}" in entry.read_text(encoding="utf-8")

    assert set(desktop.uninstall()) == set(created)
    assert not icon.exists()


def test_desktop_entry_omits_a_version_key():
    # Version= means the spec version the file conforms to, never the app's own.
    assert "Version=" not in desktop.desktop_entry_text(COMMAND)


def test_desktop_exec_quotes_a_path_containing_spaces():
    text = desktop.desktop_entry_text(["/home/Hang Yu/.local/bin/chafficlight-gui"])
    assert 'Exec="/home/Hang Yu/.local/bin/chafficlight-gui"' in text


@pytest.mark.parametrize(
    ("raw", "quoted"),
    [
        ("/a$b", '"/a\\$b"'),
        ("/a`b", '"/a\\`b"'),
        ('/a"b', '"/a\\"b"'),
        # Four, not two: the quoting rule escapes the backslash and the spec's string
        # escaping then halves it again.
        ("/a\\b", '"/a\\\\\\\\b"'),
        ("/a%b", '"/a%%b"'),
    ],
)
def test_desktop_exec_escapes_each_reserved_character(raw, quoted):
    assert desktop._desktop_quote(raw) == quoted


def test_desktop_exec_spells_every_argument_separately():
    text = desktop.desktop_entry_text(["/usr/bin/python3", "-m", "cli_traffic_light"])
    assert 'Exec="/usr/bin/python3" "-m" "cli_traffic_light"' in text


def test_info_plist_carries_the_required_keys():
    text = desktop.info_plist_text()
    for key in (
        "CFBundleInfoDictionaryVersion",
        "CFBundlePackageType",
        "CFBundleName",
        "CFBundleExecutable",
        "CFBundleIdentifier",
        "CFBundleVersion",
        "CFBundleShortVersionString",
    ):
        assert f"<key>{key}</key>" in text
    assert "<string>APPL</string>" in text
    assert f"<key>CFBundleExecutable</key><string>{desktop.APP_NAME}</string>" in text


def test_info_plist_high_resolution_flag_is_a_boolean_not_a_string():
    # The string form parses without error and silently does nothing, leaving Tk
    # rendering at 1x on a Retina display.
    assert "<key>NSHighResolutionCapable</key><true/>" in desktop.info_plist_text()


def test_app_launcher_script_execs_the_command_quoted():
    text = desktop.app_launcher_text(["/Users/Hang Yu/bin/chafficlight-gui"])
    assert text.startswith("#!/bin/sh\n")
    assert "exec '/Users/Hang Yu/bin/chafficlight-gui'\n" in text


def test_app_bundle_has_the_layout_launchservices_requires(tmp_path):
    bundle = tmp_path / f"{desktop.APP_NAME}.app"
    desktop._write_app_bundle(bundle, COMMAND)
    executable = bundle / "Contents" / "MacOS" / desktop.APP_NAME
    assert (bundle / "Contents" / "Info.plist").is_file()
    assert executable.is_file()
    if os.name == "posix":
        assert executable.stat().st_mode & 0o111


def test_desktop_entry_is_written_where_the_spec_puts_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert desktop._desktop_file_path() == tmp_path / "applications" / "chafficlight.desktop"


def test_launcher_command_prefers_the_gui_entry_point(monkeypatch):
    monkeypatch.setattr(
        desktop.shutil,
        "which",
        lambda name, path=None: f"/opt/bin/{name}" if name == "chafficlight-gui" else None,
    )
    assert desktop.launcher_command() == ["/opt/bin/chafficlight-gui"]


def test_launcher_command_never_lets_which_search_the_working_directory(monkeypatch):
    # Left to itself, shutil.which also searches the cwd on Windows and answers with a
    # bare relative name — useless as a launcher target, and attacker-controlled if the
    # install is run from a directory someone else can write to.
    seen = []

    def record(name, path=None):
        seen.append(path)
        return None

    monkeypatch.setattr(desktop.shutil, "which", record)
    desktop.launcher_command()
    assert seen and all(path for path in seen)


def test_launcher_command_falls_back_to_running_the_module(monkeypatch):
    monkeypatch.setattr(desktop.shutil, "which", lambda name, path=None: None)
    assert desktop.launcher_command()[-2:] == ["-m", "cli_traffic_light"]


def test_module_fallback_launcher_starts_in_the_directory_that_imports_it():
    # `-m cli_traffic_light` resolves the package from the working directory, so the
    # launcher only works if it is started at the checkout root.
    workdir = desktop._launcher_workdir(["/usr/bin/python3", "-m", "cli_traffic_light"])
    assert (workdir / "cli_traffic_light" / "__init__.py").is_file()


def test_entry_point_launcher_starts_beside_its_executable():
    assert desktop._launcher_workdir(["/opt/bin/chafficlight-gui"]) == Path("/opt/bin")


def test_desktop_entry_pins_the_working_directory():
    text = desktop.desktop_entry_text(["/usr/bin/python3", "-m", "cli_traffic_light"])
    assert "\nPath=" in text


def test_app_launcher_script_pins_the_working_directory():
    text = desktop.app_launcher_text(["/usr/bin/python3", "-m", "cli_traffic_light"])
    assert text.splitlines()[1].startswith("cd ")


@pytest.mark.parametrize(
    "stdout",
    ["", "\n\n", "not-an-absolute-path\n", "Programs\nDesktop\n"],
)
def test_shortcut_paths_drop_anything_that_is_not_an_absolute_path(stdout):
    assert desktop._parse_folders(stdout) == []


def test_shortcut_paths_are_named_from_the_folders_the_shell_reported(tmp_path):
    # tmp_path rather than a literal, so "absolute" means absolute on whichever
    # platform is running the suite.
    programs, desk = tmp_path / "Start Menu" / "Programs", tmp_path / "Desktop"
    parsed = desktop._parse_folders(f"{programs}\n{desk}\n")
    assert parsed == [programs / "ChafficLight.lnk", desk / "ChafficLight.lnk"]


def test_shortcut_powershell_reads_every_value_from_the_environment():
    # A path containing a quote must not be able to inject PowerShell into the
    # installer, so the script is a constant and each value it reads is supplied as
    # environment data. Drift either way — a variable the script reads but nothing
    # sets, or one set but never read — fails here.
    referenced = set(re.findall(r"\$env:(\w+)", desktop._SHORTCUT_PS))
    assert referenced == set(desktop._shortcut_env([], ["exe"]))


def test_shortcut_environment_carries_paths_verbatim(tmp_path):
    link = tmp_path / "ChafficLight.lnk"
    env = desktop._shortcut_env([link], [r"C:\Program Files\x'y\chafficlight-gui.exe"])
    assert env["CHAFFICLIGHT_LINKS"] == str(link)
    assert env["CHAFFICLIGHT_TARGET"] == r"C:\Program Files\x'y\chafficlight-gui.exe"
    assert env["CHAFFICLIGHT_WORKDIR"] == r"C:\Program Files\x'y"


def test_installer_flags_never_scan_for_sessions(monkeypatch, capsys):
    # Installing a launcher must not walk the CLIs' state directories, the way
    # --once must not construct a Tk root.
    monkeypatch.setattr(desktop, "install", lambda: [Path("/somewhere/ChafficLight.app")])
    monkeypatch.setattr(
        cli, "Monitor", lambda *a, **k: pytest.fail("Monitor built on the installer path")
    )
    assert cli.main(["--install-desktop"]) == 0
    assert "created /somewhere/ChafficLight.app" in capsys.readouterr().out.replace("\\", "/")


def test_uninstall_reports_when_there_was_no_launcher(monkeypatch, capsys):
    monkeypatch.setattr(desktop, "uninstall", lambda: [])
    assert cli.main(["--uninstall-desktop"]) == 0
    assert "no launcher was installed" in capsys.readouterr().out


def test_the_two_installer_flags_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.main(["--install-desktop", "--uninstall-desktop"])


def test_a_frozen_build_launches_itself(tmp_path, monkeypatch):
    # There is no entry point to find inside a frozen app, and `-m cli_traffic_light`
    # would reach the bootloader as an argument rather than as an interpreter option.
    running = tmp_path / "ChafficLight"
    running.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(running), raising=False)
    assert desktop.launcher_command() == [str(running)]


def test_a_frozen_build_prefers_the_windowed_binary_beside_it(tmp_path, monkeypatch):
    # A release ships a console binary too, and it is the one a user types this
    # command into. Registering it would open a console window at every login.
    console = tmp_path / "chafficlight-cli.exe"
    windowed = tmp_path / "ChafficLight.exe"
    for path in (console, windowed):
        path.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(console), raising=False)
    assert desktop.launcher_command() == [str(windowed)]


def test_a_frozen_build_prefers_the_bundle_executable_launchd_can_exec(
    tmp_path, monkeypatch
):
    console = tmp_path / "chafficlight-cli"
    inner = tmp_path / "ChafficLight.app" / "Contents" / "MacOS" / "ChafficLight"
    inner.parent.mkdir(parents=True)
    inner.write_text("binary", encoding="utf-8")
    console.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(console), raising=False)
    # launchd execs the path it is given, and a .app is a directory.
    assert desktop.launcher_command() == [str(inner)]


def test_a_frozen_macos_build_refuses_to_install_over_itself(monkeypatch):
    # `_app_bundle_path()` is where the user drags the download, and `_write_app_bundle`
    # opens with rmtree — installing would delete the running app and leave a stub
    # pointing into the tree it just deleted.
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert "move ChafficLight.app" in desktop.unavailable_reason()


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_an_unfrozen_build_installs_normally(platform, monkeypatch):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert desktop.unavailable_reason() is None


def test_installing_refuses_to_delete_a_real_downloaded_app(tmp_path, monkeypatch):
    # `_write_app_bundle` opens with rmtree, and ~/Applications is exactly where a
    # release tells its owner to keep the compiled app. rmtree is not the Trash.
    bundle = tmp_path / "ChafficLight.app"
    executable = bundle / "Contents" / "MacOS" / "ChafficLight"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"\xcf\xfa\xed\xfe compiled Mach-O, not our shim")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(desktop, "_app_bundle_path", lambda: bundle)
    assert "refusing to replace it" in desktop.unavailable_reason()


def test_installing_still_replaces_a_launcher_this_module_wrote(tmp_path, monkeypatch):
    bundle = tmp_path / "ChafficLight.app"
    desktop._write_app_bundle(bundle, COMMAND)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(desktop, "_app_bundle_path", lambda: bundle)
    assert desktop.unavailable_reason() is None


def test_a_refused_launcher_still_lets_the_login_entry_through(monkeypatch, capsys):
    # The refusal is about one destructive write, not about the whole command: a
    # frozen macOS user must still be able to ask for start-at-login.
    monkeypatch.setattr(desktop, "unavailable_reason", lambda: "already an app")
    monkeypatch.setattr(
        desktop, "install", lambda autostart=False: [Path("/x/login.plist")]
    )
    assert cli.main(["--install-desktop", "--enable-autostart"]) == 0
    out = capsys.readouterr().out.replace("\\", "/")
    assert "already an app" in out and "created /x/login.plist" in out


def test_the_frozen_command_finds_the_bundle_where_users_are_told_to_keep_it(
    tmp_path, monkeypatch
):
    console = tmp_path / "chafficlight-cli"
    console.write_text("binary", encoding="utf-8")
    moved = tmp_path / "Applications" / "ChafficLight.app" / "Contents" / "MacOS"
    moved.mkdir(parents=True)
    (moved / "ChafficLight").write_text("binary", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(console), raising=False)
    monkeypatch.setattr(
        desktop, "_app_bundle_path", lambda: tmp_path / "Applications" / "ChafficLight.app"
    )
    assert desktop.launcher_command() == [str(moved / "ChafficLight")]


def test_no_shell_folder_means_no_powershell_call(monkeypatch):
    # An empty link list would reach the script as a single empty string and fail
    # CreateShortcut inside the installer.
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(desktop, "_targets", lambda autostart=False: [])
    monkeypatch.setattr(desktop.shutil, "which", lambda name, path=None: None)
    monkeypatch.setattr(
        desktop, "_powershell", lambda *a, **k: pytest.fail("ran with no shortcut paths")
    )
    assert desktop.install() == []


def test_powershell_is_named_absolutely(monkeypatch):
    # Given a bare name, CreateProcess searches the working directory before PATH —
    # and a downloaded app runs from wherever it was unpacked.
    seen = {}

    def record(command, **kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(desktop.subprocess, "run", record)
    desktop._powershell("$x = 1")
    assert Path(seen["command"][0]).is_absolute()
    assert seen["command"][0].lower().endswith("powershell.exe")


def test_the_refusal_reaches_the_user_instead_of_a_launcher(monkeypatch, capsys):
    monkeypatch.setattr(desktop, "unavailable_reason", lambda: "already an app")
    monkeypatch.setattr(
        desktop, "install", lambda *a, **k: pytest.fail("installed over the app bundle")
    )
    assert cli.main(["--install-desktop"]) == 0
    assert "already an app" in capsys.readouterr().out


def test_launch_agent_starts_at_login_and_execs_the_binary():
    text = desktop.launch_agent_plist_text(["/Users/u/Applications/X.app/Contents/MacOS/X"])
    parsed = plistlib.loads(text.encode("utf-8"))
    # RunAtLoad defaults to false, and for an agent it is what "at login" means.
    assert parsed["RunAtLoad"] is True
    assert parsed["Label"] == desktop._BUNDLE_ID
    # launchd execs this path, and a .app is a directory, so it must be the executable.
    assert parsed["ProgramArguments"] == ["/Users/u/Applications/X.app/Contents/MacOS/X"]


def test_launch_agent_survives_a_path_that_would_break_hand_written_xml():
    text = desktop.launch_agent_plist_text(["/Users/a&b/<X>/ChafficLight"])
    assert plistlib.loads(text.encode("utf-8"))["ProgramArguments"] == [
        "/Users/a&b/<X>/ChafficLight"
    ]


def test_the_autostart_entry_is_written_where_the_spec_puts_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert desktop._autostart_entry_path() == tmp_path / "autostart" / "chafficlight.desktop"


def test_autostart_and_launcher_are_different_files_on_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert desktop._targets(autostart=True) != desktop._targets()


def test_windows_autostart_targets_the_startup_folder(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    asked = []
    monkeypatch.setattr(
        desktop, "_powershell", lambda script, env=None: asked.append(env) or ""
    )
    desktop._targets(autostart=True)
    desktop._targets()
    assert [env["CHAFFICLIGHT_FOLDERS"] for env in asked] == ["Startup", "Programs\nDesktop"]


def test_the_folder_script_reads_its_folder_names_from_the_environment():
    # Same rule as the shortcut script: the PowerShell body is a constant and every
    # value it reads is supplied as data.
    assert "$env:CHAFFICLIGHT_FOLDERS" in desktop._FOLDERS_PS


def test_powershell_runs_headless_with_no_inherited_handles(monkeypatch):
    # A frozen windowed build inherits no valid standard handles, and a console would
    # flash at someone who double-clicked an app.
    seen = {}

    def record(command, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(desktop.subprocess, "run", record)
    desktop._powershell("$x = 1")
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["stdout"] is subprocess.PIPE
    assert seen["stderr"] is subprocess.PIPE
    assert seen["creationflags"] == desktop._CREATE_NO_WINDOW


def test_a_launcher_and_a_login_entry_can_be_asked_for_together(monkeypatch, capsys):
    # Starting at login is not an alternative to having an icon; the ordinary first run
    # wants both.
    monkeypatch.setattr(desktop, "unavailable_reason", lambda: None)
    monkeypatch.setattr(
        desktop, "install", lambda autostart=False: [Path("/x/autostart" if autostart else "/x/menu")]
    )
    assert cli.main(["--install-desktop", "--enable-autostart"]) == 0
    out = capsys.readouterr().out.replace("\\", "/")
    assert "created /x/menu" in out and "created /x/autostart" in out


def test_the_two_autostart_flags_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.main(["--enable-autostart", "--disable-autostart"])


def test_disabling_autostart_reports_when_there_was_none(monkeypatch, capsys):
    monkeypatch.setattr(desktop, "uninstall", lambda autostart=False: [])
    assert cli.main(["--disable-autostart"]) == 0
    assert "no autostart entry was installed" in capsys.readouterr().out


def test_autostart_flags_never_scan_for_sessions(monkeypatch):
    monkeypatch.setattr(desktop, "install", lambda autostart=False: [])
    monkeypatch.setattr(
        cli, "Monitor", lambda *a, **k: pytest.fail("Monitor built on the installer path")
    )
    assert cli.main(["--enable-autostart"]) == 0


def test_install_then_uninstall_round_trips_an_autostart_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(desktop.shutil, "which", lambda name, path=None: None)

    created = desktop.install(autostart=True)
    assert created == [tmp_path / "autostart" / "chafficlight.desktop"]
    # The autostart spec reuses the Desktop Entry format, so the body is the same one.
    assert created[0].read_text(encoding="utf-8").startswith("[Desktop Entry]")

    assert desktop.uninstall(autostart=True) == created
    assert desktop.uninstall(autostart=True) == []


def test_install_then_uninstall_round_trips_a_desktop_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(desktop.shutil, "which", lambda name, path=None: None)

    created = desktop.install()
    # The entry first: the launcher also owns the icon file it points at.
    assert created[0] == tmp_path / "applications" / "chafficlight.desktop"
    assert created[0].read_text(encoding="utf-8").startswith("[Desktop Entry]")

    assert desktop.uninstall() == created
    assert not created[0].exists()
    assert desktop.uninstall() == []
