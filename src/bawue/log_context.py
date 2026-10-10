"""Contextvar-based logging context for per-Vorgang log enrichment."""

import contextvars
import logging

_vorgangs_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("vorgangs_id", default=None)


class VorgangsnummerFilter(logging.Filter):
    """Adds ``vorgangs_id`` from the current async context to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.vorgangs_id = _vorgangs_id.get()
        return True


def set_vorgangs_id(vorgangs_id: str | None) -> contextvars.Token:
    """Set the vorgangs_id for the current context. Returns a token to restore previous value."""
    return _vorgangs_id.set(vorgangs_id)


def reset_vorgangs_id(token: contextvars.Token) -> None:
    """Restore the vorgangs_id to the value before the matching set_vorgangs_id call."""
    _vorgangs_id.reset(token)
