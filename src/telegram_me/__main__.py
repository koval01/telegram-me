"""Command line interface: `telegram-me serve | worker | version`."""

import argparse
import asyncio

from telegram_me import __version__
from telegram_me.settings import Settings


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and run the selected command."""
    parser = argparse.ArgumentParser(
        prog="telegram-me", description="REST API for public Telegram channels."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the HTTP API (granian)")
    serve.add_argument("--reload", action="store_true", help="restart on code changes (dev)")
    commands.add_parser("worker", help="run the feed refresher without the API (needs Redis)")
    commands.add_parser("version", help="print the version")
    args = parser.parse_args(argv)

    match args.command:
        case "serve":
            from telegram_me.server import serve as run_server  # noqa: PLC0415

            run_server(Settings(), reload=args.reload)
        case "worker":
            from telegram_me.server import run_worker  # noqa: PLC0415

            asyncio.run(run_worker(Settings()))
        case "version":
            print(__version__)  # noqa: T201


if __name__ == "__main__":
    main()
