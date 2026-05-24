"""Command-line entry point for arxiv-literature-report."""

from __future__ import annotations

from .core import main as run_report


def main(argv: list[str] | None = None) -> int:
    return run_report(argv)

