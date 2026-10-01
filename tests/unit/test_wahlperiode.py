"""Issues #6/#63: edge cases of the derived search start; the regular cases run
through the scrapers' config (TestIssue63WahlperiodeConfig, dry_run)."""

from datetime import date, timedelta

import pytest

from bawue.wahlperiode import wahlperiode_start


def test_override_also_covers_an_unknown_wahlperiode():
    assert wahlperiode_start(19, "2026-09-01") == date(2026, 9, 1)


def test_unknown_wahlperiode_without_override_says_what_to_do():
    with pytest.raises(ValueError, match=r"19.*WAHLPERIODE_START.*wahlperiode-start-date"):
        wahlperiode_start(19)


def test_malformed_override_is_rejected():
    with pytest.raises(ValueError):
        wahlperiode_start(18, "01.05.2026")


@pytest.mark.parametrize(
    "override",
    [
        "2026-04-30",  # before WP 18: a stale date left over from an earlier Wahlperiode
        (date.today() + timedelta(days=1)).isoformat(),  # future: an empty search every run
        2026,  # TOML integer instead of a date
    ],
)
def test_override_outside_the_wahlperiode_is_rejected(override):
    with pytest.raises(ValueError, match="wahlperiode-start-date"):
        wahlperiode_start(18, override)


@pytest.mark.parametrize("override", ["2026-05-01", date.today().isoformat()])
def test_override_bounds_are_inclusive(override):
    assert wahlperiode_start(18, override) == date.fromisoformat(override)
