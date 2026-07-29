"""Compile this checkout into a standalone app for the platform it is run on.

Original request:
The user asked for helper scripts that compile this repo into installable apps for
Windows, Linux and macOS, so people can download one and run it without installing
Python or creating a virtual environment, and can set it to start on login.

PyInstaller is not a cross-compiler, so this builds for the host platform only and the
GitHub Actions workflow runs it once per platform. Following `desktop.py`, every
decision here is a pure function and only `main` touches the disk.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli_traffic_light import __version__  # noqa: E402  (needs ROOT on the path first)

APP_NAME = "ChafficLight"
CONSOLE_NAME = "chafficlight-cli"
BUNDLE_ID = "io.github.hangyu8123.chafficlight"
ENTRY_SCRIPT = ROOT / "tools" / "entry.py"
ICON = ROOT / "cli_traffic_light" / "chafficlight.png"

_PLATFORM_LABELS = {"win32": "windows", "darwin": "macos"}


@dataclass(frozen=True)
class Build:
    """One executable a platform ships."""

    name: str
    windowed: bool
    onefile: bool


def builds(platform: str) -> list[Build]:
    """The executables ``platform`` ships.

    A windowed build attaches to no console, and PyInstaller then leaves `sys.stdout`
    and `sys.stderr` at ``None`` rather than substituting a writer. Measured on
    Windows: typing ``ChafficLight.exe --once --json`` at a prompt printed nothing at
    all, while the same call through an explicitly redirected pipe still produced its
    3847 bytes. So the headless modes and every launcher message are lost for exactly
    the person most likely to want them — someone at a terminal. The frozen app is the
    only copy a downloader has, so each platform whose GUI build is windowed ships a
    console binary beside it, the same split the ``chafficlight`` /
    ``chafficlight-gui`` entry points already make. Linux has no console/GUI
    distinction, so one binary serves both.
    """
    console = Build(CONSOLE_NAME, windowed=False, onefile=True)
    if platform == "win32":
        return [Build(APP_NAME, windowed=True, onefile=True), console]
    if platform == "darwin":
        # Not onefile: PyInstaller documents onefile app bundles as unrecommended,
        # since they unpack themselves on every launch.
        return [Build(APP_NAME, windowed=True, onefile=False), console]
    return [Build(APP_NAME, windowed=False, onefile=True)]


def pyinstaller_args(build: Build, platform: str, dist: Path, work: Path) -> list[str]:
    """The PyInstaller command line for one `Build`.

    ``--noupx`` because UPX compression measurably raises antivirus detection of the
    bootloader, which is already the component scanners flag. ``--argv-emulation`` is
    deliberately never passed: its Apple-event processing is documented to interfere
    with Tcl/Tk, up to segmentation faults, and the Tk window is the whole app. Nor is
    ``--codesign-identity``, which turns on the hardened runtime and would then demand
    a real Apple certificate.
    """
    args = [
        "--noconfirm",
        "--clean",
        "--noupx",
        "--name",
        build.name,
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        # The entry script lives outside the package, so the import root is explicit.
        "--paths",
        str(ROOT),
    ]
    if build.onefile:
        args.append("--onefile")
    if build.windowed:
        args.append("--windowed")
    if platform in ("win32", "darwin"):
        # The source is one PNG rather than a committed .ico and .icns: PyInstaller
        # converts it to each platform's format through Pillow, so the repo keeps a
        # single editable asset instead of three that can drift apart.
        args += ["--icon", str(ICON)]
    else:
        # An ELF carries no icon at all — on Linux the icon is a file the desktop
        # entry points at, so the binary has to bring one with it.
        args += ["--add-data", f"{ICON}{os.pathsep}cli_traffic_light"]
    if platform == "darwin" and build.windowed:
        args += ["--osx-bundle-identifier", BUNDLE_ID]
    return [*args, str(ENTRY_SCRIPT)]


def artifact_path(build: Build, platform: str, dist: Path) -> Path:
    """Where PyInstaller leaves the artifact for one `Build`."""
    if platform == "darwin" and build.windowed:
        # --windowed is what makes macOS emit a bundle rather than a bare executable.
        return dist / f"{build.name}.app"
    if platform == "win32":
        return dist / f"{build.name}.exe"
    return dist / build.name


def archive_path(platform: str, dist: Path, version: str) -> Path:
    """The single file a release attaches for ``platform``.

    A tarball everywhere the executable bit matters: zip carries no Unix permissions,
    and GitHub's own artifact upload documents that everything inside one arrives as
    644 — which is to say not executable, so the download would not start at all.
    """
    label = _PLATFORM_LABELS.get(platform, "linux")
    suffix = ".zip" if platform == "win32" else ".tar.gz"
    return dist / f"{APP_NAME}-{version}-{label}{suffix}"


def _write_archive(path: Path, members: list[Path]) -> Path:
    """Pack ``members`` into ``path``, by its suffix."""
    if path.suffix == ".zip":
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for member in members:
                archive.write(member, member.name)
        return path
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            archive.add(member, member.name)  # recurses into the .app bundle
    return path


def main(argv: list[str] | None = None) -> int:
    """Build every artifact this platform ships and pack them into one archive."""
    parser = argparse.ArgumentParser(
        prog="build_app",
        description="Compile ChafficLight into a standalone app for this platform.",
    )
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--work", type=Path, default=ROOT / "build")
    args = parser.parse_args(argv)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            "PyInstaller is not installed. Run:\n"
            f"  {sys.executable} -m pip install pyinstaller",
            file=sys.stderr,
        )
        return 2

    built = []
    for build in builds(sys.platform):
        # Through the interpreter rather than the console script, which need not be on
        # PATH — and PyInstaller freezes whichever interpreter runs it.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                *pyinstaller_args(build, sys.platform, args.dist, args.work),
            ],
            check=True,
        )
        path = artifact_path(build, sys.platform, args.dist)
        if not path.exists():
            print(f"PyInstaller succeeded but {path} is missing", file=sys.stderr)
            return 1
        built.append(path)

    archive = archive_path(sys.platform, args.dist, __version__)
    _write_archive(archive, built)
    for path in built:
        print(f"built {path}")
    print(f"packed {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
