#!/usr/bin/env python3
"""Retired Hub synchronization entry point; retained for compatibility only."""
from __future__ import annotations

import sys


def main() -> int:
    """Reject every invocation without reading configuration or mutating files."""
    print(
        "[hub] retired: Hub copy/commit/push and dry-run are no longer supported. "
        "Edit the source kit explicitly; consumer updates require separate approval.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
