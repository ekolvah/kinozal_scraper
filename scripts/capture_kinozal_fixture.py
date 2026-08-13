#!/usr/bin/env python3
"""Capture one Kinozal page through the production origin-to-mirror fetcher.

Usage: python scripts/capture_kinozal_fixture.py <url> <path>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kinozal_scraper.kinozal_pipeline import Kinozal  # noqa: E402


class DetailsFetcher(Protocol):
    def fetch_details(self, url: str) -> str: ...


def capture(url: str, path: Path, *, fetcher: DetailsFetcher | None = None) -> None:
    """Fetch *url* with Kinozal's tested failover and persist an immutable fixture."""
    active_fetcher = fetcher if fetcher is not None else Kinozal.from_env()
    response = active_fetcher.fetch_details(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.encode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    capture(args.url, args.path)
    print(f"captured {args.url} -> {args.path}")


if __name__ == "__main__":
    main()
