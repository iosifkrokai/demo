"""``python -m quality`` — the evaluation runner (see quality/runner.py).

Sub-entry points:
    python -m quality.runner   # live / replay / compare / compliance
    python -m quality.evals    # the four offline + live flow stages
    python -m quality.report   # the one-page quality readout
"""

from __future__ import annotations

from .runner import main

if __name__ == "__main__":
    raise SystemExit(main())
