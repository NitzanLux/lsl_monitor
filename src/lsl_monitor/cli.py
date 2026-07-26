"""Command-line interface for validation and GUI startup."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lsl_monitor.config import ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor configured LSL streams")
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        help="monitor JSON configuration (default: json/example.monitor.json)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="validate the configuration without starting the GUI",
    )
    parser.add_argument(
        "--designer",
        action="store_true",
        help="open the visual layout designer with mock streams",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.designer:
        from lsl_monitor.designer import run_designer

        return run_designer(Path(args.config) if args.config else None)
    config_path = Path(args.config or "json/example.monitor.json")
    try:
        config = load_config(config_path)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    if args.validate:
        print(f"Valid configuration: {config.source_path}")
        print(f"Configured streams: {len(config.streams)}")
        return 0

    from lsl_monitor.app import run

    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())
