"""The Wahlperioden the scraper knows. Which one to scrape is set once, in [bawue]
wahlperiode (env WAHLPERIODE); everything else derives from it (issues #6, #63, DD-062)."""

from datetime import date

# The Landtag's constitution date; the PARLIS search runs from here to today.
# A new Wahlperiode needs one entry here.
WAHLPERIODE_START = {17: date(2021, 4, 26), 18: date(2026, 5, 1)}
CURRENT_WAHLPERIODE = max(WAHLPERIODE_START)


def wahlperiode_start(wahlperiode: int, override: str | date | None = None) -> date:
    """Where the search starts: *override* ([bawue] wahlperiode-start-date, to narrow the
    lookback), else the Wahlperiode's constitution date."""
    known = WAHLPERIODE_START.get(wahlperiode)
    if override is None:
        if known is None:
            raise ValueError(
                f"Start of Wahlperiode {wahlperiode} unknown: add it to WAHLPERIODE_START "
                "or set [bawue] wahlperiode-start-date"
            )
        return known
    start = date.fromisoformat(override) if isinstance(override, str) else override
    # Only narrows: a stale date from an earlier Wahlperiode or a future one is a misconfiguration.
    if not isinstance(start, date) or start > date.today() or (known is not None and start < known):
        raise ValueError(
            f"wahlperiode-start-date {override!r} must be a date between the start of "
            f"Wahlperiode {wahlperiode} and today"
        )
    return start
