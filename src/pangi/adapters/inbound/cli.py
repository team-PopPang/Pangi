"""Dependency-light bootstrap CLI."""

import argparse
from collections.abc import Sequence

from pangi._version import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the command parser without importing optional adapters."""

    parser = argparse.ArgumentParser(prog="pangi", description="Pangi agent runtime")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the minimal CLI entry point."""

    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0

