"""Issues #6/#63: edge cases of the derived search start; the regular cases run
through the scrapers' config (TestIssue63WahlperiodeConfig, dry_run)."""

from datetime import date

import pytest

from bawue.wahlperiode import wahlperiode_start


def test_override_also_covers_an_unknown_wahlperiode():
    assert wahlperiode_start(19, "2031-05-01") == date(2031, 5, 1)


def test_unknown_wahlperiode_without_override_says_what_to_do():
    with pytest.raises(ValueError, match=r"19.*WAHLPERIODE_START.*wahlperiode-start-date"):
        wahlperiode_start(19)


def test_malformed_override_is_rejected():
    with pytest.raises(ValueError):
        wahlperiode_start(18, "01.05.2026")
