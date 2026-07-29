"""Package entry point so ``python -m cli_traffic_light`` runs the CLI.

Original request:
The user asked for a traffic-light GUI that watches their Claude Code and Codex
CLI chat sessions and shows, per session, whether it is running (green), waiting
for their input (yellow) or finished (red), along with token usage.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
