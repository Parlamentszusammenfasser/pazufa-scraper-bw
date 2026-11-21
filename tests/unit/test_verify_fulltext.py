"""verify_fulltext's PARLIS search range (issues #6/#63) and its parser comparison (issue #216)."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from bawue.verify_fulltext import _kreuzberg_side, _parser_side, collect_pdf_urls, compare_findings, sample_fundstellen


def test_lookback_replaces_the_derived_start_even_for_an_unknown_wahlperiode():
    with patch("bawue.verify_fulltext.ParlisClient") as parlis:
        parlis.return_value.search.return_value = []
        collect_pdf_urls(wahlperiode=19, lookback_days=7)

    assert parlis.call_args.kwargs["wahlperiode_start_date"] == date.today() - timedelta(days=7)


# --compare-parser (issue #216)

PDF_17_273 = Path(__file__).parent.parent / "fixtures" / "pdf" / "17_0273_D.pdf"


def test_issue216_parser_matches_kreuzberg_on_17_273():
    fund = {"pdf_url": "https://www.landtag-bw.de/17_0273_D.pdf", "drucksache": "17/273", "datum": "22.06.2021"}
    parlis_datum = datetime(2021, 6, 22, tzinfo=UTC)

    kreuzberg, native_pages = asyncio.run(_kreuzberg_side(PDF_17_273, fund, parlis_datum))
    parser = _parser_side(PDF_17_273, fund, parlis_datum, native_pages)

    assert compare_findings({"kreuzberg": kreuzberg, "parser": parser}) == []
    assert kreuzberg["ausgegeben"] == parser["ausgegeben"] == "2021-06-25"
    # The footer is furniture to the parser: its body text alone loses the date.
    assert parser["ausgegeben_body_only"] is None


def _side(**overrides):
    return {"ausgegeben": "2021-06-25", "length": 1000, "agenda": None, "native_replaced": [], **overrides}


@pytest.mark.parametrize(
    ("parser", "finding"),
    [
        (_side(), None),
        (_side(length=900), None),
        (_side(ausgegeben=None), "Ausgegeben lost"),
        (_side(length=899), "text 899 of 1,000"),
        (_side(native_replaced=[3]), "pages [3]"),
        (_side(agenda={"pages": [34, 35], "length": 9000}), "agenda pages None → [34, 35]"),
    ],
)
def test_issue216_findings(parser, finding):
    findings = compare_findings({"kreuzberg": _side(), "parser": parser})

    if finding is None:
        assert findings == []
    else:
        assert len(findings) == 1 and finding in findings[0]


def test_issue216_sample_spreads_over_the_search_and_adds_included_drucksachen():
    funds = [{"pdf_url": f"u{i}", "drucksache": f"17/{i}"} for i in range(10)]

    sample = sample_fundstellen(funds + funds[:1], limit=3, include={"17/0", "17/7"})

    assert [f["pdf_url"] for f in sample] == ["u0", "u3", "u6", "u7"]
