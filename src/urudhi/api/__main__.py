"""Serve the Urudhi API.

    python -m urudhi.api [--db urudhi.sqlite3] [--port 8000]

Point ``--db`` at a batch run's database (``python -m urudhi.sim`` with a file
path) to browse it in the dashboard, or at a fresh file for a live-mode
webhook receiver.
"""

from __future__ import annotations

import argparse

import uvicorn

from urudhi.api.app import create_app
from urudhi.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m urudhi.api")
    parser.add_argument("--db", default="urudhi.sqlite3")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    app = create_app(Store(args.db))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
