"""Acceptance tests for the standalone-app build helper.

Original request:
The user asked for helper scripts that compile this repo into apps people can download
and run on Windows, Linux and macOS without installing Python, and can set to start on
login.

Every build decision is a pure function of the target platform, so each platform's
command line and artifact layout is checked from whichever platform runs the suite —
the same property `test_desktop.py` relies on. What no test here can establish is that
the frozen binary runs; that evidence comes from the build workflow, which executes
each artifact it produces.
"""

import importlib.util
import sys
import tarfile
import types
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    """Import a `tools/` script by path.

    By path rather than by name because `tools/` is deliberately not a package: a
    top-level one would sit on `sys.path` beside the real `packaging` distribution
    setuptools imports.
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because `@dataclass` resolves the postponed
    # annotations of `Build` by looking its own module up in `sys.modules`.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build_app = _load("build_app")
entry = _load("entry")

DIST = Path("/tmp/dist")
WORK = Path("/tmp/work")


def _args(build, platform):
    return build_app.pyinstaller_args(build, platform, DIST, WORK)


def _named(platform, name):
    return next(build for build in build_app.builds(platform) if build.name == name)


def test_windows_ships_a_windowed_app_and_a_console_binary():
    # A windowed build attaches to no console, so --once/--json prints nothing for
    # someone running it at a prompt; the frozen app is the only copy a downloader
    # has, so both must exist.
    names = {build.name: build.windowed for build in build_app.builds("win32")}
    assert names == {"ChafficLight": True, "chafficlight-cli": False}


def test_macos_ships_the_same_pair():
    names = {build.name: build.windowed for build in build_app.builds("darwin")}
    assert names == {"ChafficLight": True, "chafficlight-cli": False}


def test_linux_ships_one_binary_that_keeps_its_streams():
    # No console/GUI split on Linux: --windowed is a no-op there, so a single build
    # opens the window and still prints.
    (build,) = build_app.builds("linux")
    assert (build.name, build.windowed, build.onefile) == ("ChafficLight", False, True)


def test_the_macos_bundle_is_not_built_onefile():
    # PyInstaller documents onefile app bundles as unrecommended: they unpack
    # themselves on every launch.
    assert _named("darwin", "ChafficLight").onefile is False


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_no_build_lets_upx_touch_the_bootloader(platform):
    # UPX measurably raises antivirus detection of the one component every
    # PyInstaller-built program shares.
    for build in build_app.builds(platform):
        assert "--noupx" in _args(build, platform)


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_no_build_enables_argv_emulation_or_code_signing(platform):
    # --argv-emulation is documented to interfere with Tcl/Tk up to segfaults, and the
    # window is the whole app; --codesign-identity turns on the hardened runtime and
    # would then require a real Apple certificate.
    for build in build_app.builds(platform):
        rendered = " ".join(_args(build, platform))
        assert "--argv-emulation" not in rendered
        assert "--codesign-identity" not in rendered


def test_only_the_macos_bundle_carries_the_bundle_identifier():
    assert "--osx-bundle-identifier" in _args(_named("darwin", "ChafficLight"), "darwin")
    assert "--osx-bundle-identifier" not in _args(
        _named("darwin", "chafficlight-cli"), "darwin"
    )


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_the_icon_is_embedded_where_an_executable_can_carry_one(platform):
    # PyInstaller converts the one PNG to .ico / .icns through Pillow, so the repo
    # keeps a single editable asset rather than three that can drift apart.
    for build in build_app.builds(platform):
        args = _args(build, platform)
        assert args[args.index("--icon") + 1] == str(build_app.ICON)


def test_linux_ships_the_icon_as_data_because_an_elf_carries_none():
    # On Linux the icon is a file the desktop entry points at, so the binary has to
    # bring one with it.
    args = _args(_named("linux", "ChafficLight"), "linux")
    assert "--icon" not in args
    payload = args[args.index("--add-data") + 1]
    assert payload.startswith(str(build_app.ICON))
    assert payload.endswith("cli_traffic_light")


def test_the_icon_asset_is_a_real_png_in_the_package():
    assert build_app.ICON.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_entry_script_is_the_last_argument_and_exists():
    build = _named("win32", "ChafficLight")
    assert _args(build, "win32")[-1] == str(build_app.ENTRY_SCRIPT)
    assert build_app.ENTRY_SCRIPT.is_file()


def test_the_package_root_is_on_the_import_path():
    # The entry script sits outside the package, so PyInstaller cannot infer the root.
    args = _args(_named("linux", "ChafficLight"), "linux")
    assert args[args.index("--paths") + 1] == str(build_app.ROOT)


@pytest.mark.parametrize(
    ("platform", "name", "expected"),
    [
        ("win32", "ChafficLight", "ChafficLight.exe"),
        ("win32", "chafficlight-cli", "chafficlight-cli.exe"),
        ("darwin", "ChafficLight", "ChafficLight.app"),
        ("darwin", "chafficlight-cli", "chafficlight-cli"),
        ("linux", "ChafficLight", "ChafficLight"),
    ],
)
def test_artifact_lands_where_pyinstaller_leaves_it(platform, name, expected):
    build = _named(platform, name)
    assert build_app.artifact_path(build, platform, DIST) == DIST / expected


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("win32", "ChafficLight-9.9.9-windows.zip"),
        ("darwin", "ChafficLight-9.9.9-macos.tar.gz"),
        ("linux", "ChafficLight-9.9.9-linux.tar.gz"),
    ],
)
def test_the_archive_is_named_for_its_platform_and_version(platform, expected):
    assert build_app.archive_path(platform, DIST, "9.9.9") == DIST / expected


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_platforms_with_an_executable_bit_get_a_tarball_not_a_zip(platform):
    # Zip carries no Unix permissions, and GitHub documents everything inside an
    # uploaded artifact as arriving 644 — which will not start.
    assert build_app.archive_path(platform, DIST, "0.1.0").suffixes[-2:] == [
        ".tar",
        ".gz",
    ]


def test_the_archive_holds_each_artifact_under_its_own_name(tmp_path):
    members = [tmp_path / "ChafficLight", tmp_path / "chafficlight-cli"]
    for member in members:
        member.write_text("binary", encoding="utf-8")

    tarball = build_app._write_archive(tmp_path / "out.tar.gz", members)
    with tarfile.open(tarball) as archive:
        assert sorted(archive.getnames()) == ["ChafficLight", "chafficlight-cli"]

    zipped = build_app._write_archive(tmp_path / "out.zip", members)
    with zipfile.ZipFile(zipped) as archive:
        assert sorted(archive.namelist()) == ["ChafficLight", "chafficlight-cli"]


def test_a_bundle_directory_is_archived_whole(tmp_path):
    bundle = tmp_path / "ChafficLight.app" / "Contents" / "MacOS"
    bundle.mkdir(parents=True)
    (bundle / "ChafficLight").write_text("#!/bin/sh\n", encoding="utf-8")

    tarball = build_app._write_archive(
        tmp_path / "out.tar.gz", [tmp_path / "ChafficLight.app"]
    )
    with tarfile.open(tarball) as archive:
        assert "ChafficLight.app/Contents/MacOS/ChafficLight" in archive.getnames()


@pytest.mark.parametrize("serial", ["-psn_0_12345", "-psn_1_9"])
def test_the_entry_script_drops_the_macos_process_serial_argument(serial):
    # LaunchServices adds it when it starts a bundle; argparse would exit with a usage
    # message on it, and a windowed build has no stderr to show it on, so the app would
    # just never appear.
    assert entry.user_arguments([serial, "--once"]) == ["--once"]


def test_the_entry_script_leaves_real_arguments_alone():
    assert entry.user_arguments(["--once", "--json"]) == ["--once", "--json"]


def _stub_build(monkeypatch, tmp_path, artifact_exists=True):
    """Drive `main` without a PyInstaller install or a real compile."""
    monkeypatch.setitem(sys.modules, "PyInstaller", types.ModuleType("PyInstaller"))
    monkeypatch.setattr(build_app, "builds", lambda platform: [build_app.Build("X", False, True)])
    artifact = tmp_path / "X"
    if artifact_exists:
        artifact.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(build_app, "artifact_path", lambda b, p, d: artifact)
    calls = []
    monkeypatch.setattr(
        build_app.subprocess, "run", lambda command, **kwargs: calls.append(command)
    )
    return calls


def test_the_builder_freezes_the_interpreter_that_runs_it(tmp_path, monkeypatch):
    # PyInstaller freezes whichever interpreter invokes it, and its console script need
    # not be on PATH, so the call must go through sys.executable.
    calls = _stub_build(monkeypatch, tmp_path)
    assert build_app.main(["--dist", str(tmp_path), "--work", str(tmp_path)]) == 0
    assert calls and all(
        call[:3] == [sys.executable, "-m", "PyInstaller"] for call in calls
    )


def test_a_missing_artifact_fails_the_build(tmp_path, monkeypatch):
    # PyInstaller exiting 0 is not evidence that anything was produced.
    _stub_build(monkeypatch, tmp_path, artifact_exists=False)
    assert build_app.main(["--dist", str(tmp_path), "--work", str(tmp_path)]) == 1


def test_a_missing_pyinstaller_says_how_to_install_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "PyInstaller", None)
    assert build_app.main(["--dist", str(tmp_path), "--work", str(tmp_path)]) == 2
    assert "pip install pyinstaller" in capsys.readouterr().err
