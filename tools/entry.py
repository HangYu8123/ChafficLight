"""The single module a frozen build starts from.

Original request:
The user asked for helper scripts that compile this repo into apps people can
download and run for Windows, Linux and macOS without installing Python or making a
virtual environment.

``cli_traffic_light/__main__.py`` cannot serve as that entry point: it reaches the CLI
through a relative import, which fails the moment a freezer runs the file as a
top-level script rather than as part of the package.
"""

import sys

from cli_traffic_light.cli import main


def user_arguments(argv: list[str]) -> list[str]:
    """``argv`` without the process-serial number macOS may prepend.

    LaunchServices passes ``-psn_0_<n>`` to a bundled app it starts from the Finder or
    from a login agent. ``argparse`` treats it as an unrecognised option and exits with
    a usage message — invisibly, because a windowed build has no stderr to print it on,
    so the app would simply fail to appear.
    """
    return [argument for argument in argv if not argument.startswith("-psn_")]


if __name__ == "__main__":
    sys.exit(main(user_arguments(sys.argv[1:])))
