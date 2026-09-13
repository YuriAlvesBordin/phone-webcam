"""Logging setup shared by all modules."""

from __future__ import annotations

import logging

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%H:%M:%S"


def setup(verbose: bool = False) -> None:
    """Configure the root logger. Call once at startup."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    logging.basicConfig(level=level, handlers=[handler], force=True)

    # Mute noisy third-party loggers unless debugging.
    if not verbose:
        for name in ("uvicorn.access", "aiortc", "aioice", "PIL", "matplotlib"):
            logging.getLogger(name).setLevel(logging.WARNING)
