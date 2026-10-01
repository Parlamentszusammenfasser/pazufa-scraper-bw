"""verify_fulltext's PARLIS search range (issues #6/#63)."""

from datetime import date, timedelta
from unittest.mock import patch

from bawue.verify_fulltext import collect_pdf_urls


def test_lookback_replaces_the_derived_start_even_for_an_unknown_wahlperiode():
    with patch("bawue.verify_fulltext.ParlisClient") as parlis:
        parlis.return_value.search.return_value = []
        collect_pdf_urls(wahlperiode=19, lookback_days=7)

    assert parlis.call_args.kwargs["wahlperiode_start_date"] == date.today() - timedelta(days=7)
