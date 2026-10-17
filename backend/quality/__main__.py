"""``python -m quality`` — the evaluation runner."""

from __future__ import annotations

from .bench.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
