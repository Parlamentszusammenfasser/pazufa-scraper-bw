"""Shared helpers for scraper run summary reporting."""

from dataclasses import dataclass


@dataclass
class FailedItem:
    """A single item whose processing or upload failed.

    Captured at the point of failure so the run summary can give an
    operator a direct lead to investigate — id, title, and a short
    reason (HTTP status, exception type, message).
    """

    item_id: str
    titel: str | None
    reason: str


def api_exception_reason(exc: Exception) -> str:
    """Build a short ``HTTP <status> <reason>`` tag for a run summary.

    Falls back to ``<ExceptionType>: <message>`` when the exception has
    no ``status`` attribute (i.e. is not an API exception).
    """
    status = getattr(exc, "status", None)
    reason = getattr(exc, "reason", None) or ""
    if status is None:
        return f"{type(exc).__name__}: {exc}"
    return f"HTTP {status} {reason}".rstrip()


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as ``Hh MMm SSs`` or ``MMm SSs``.

    Examples:
        0.4    -> "0m 00s"
        61.0   -> "1m 01s"
        3605.0 -> "1h 00m 05s"
        23045.7 -> "6h 24m 06s"
    """
    if seconds < 0:
        seconds = 0
    total = round(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def format_failed_section(
    failed_items: list[FailedItem],
    *,
    header: str = "Failed items",
    max_items: int = 20,
    title_max_len: int = 80,
) -> list[str]:
    """Format a section listing failed items.

    Returns an empty list when *failed_items* is empty so callers can
    unconditionally extend a lines list.
    """
    if not failed_items:
        return []

    lines = ["", f"{header} ({len(failed_items)}):"]
    for item in failed_items[:max_items]:
        title = (item.titel or "").strip()
        if len(title) > title_max_len:
            title = title[: title_max_len - 1] + "…"
        title_part = f" | {title}" if title else ""
        lines.append(f"  - {item.item_id}{title_part} | {item.reason}")
    remaining = len(failed_items) - max_items
    if remaining > 0:
        lines.append(f"  … {remaining} more")
    return lines
