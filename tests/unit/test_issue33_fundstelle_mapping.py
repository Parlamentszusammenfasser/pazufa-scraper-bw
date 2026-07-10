"""Regression tests for issue #33 — wrong link/titel on Redeprotokoll (parl-vollvlsgn) stations.

https://codeberg.org/PaZuFa/pazufa-scraper-bw/issues/33

The reviewer reported two symptoms observed in *stale* staging data. Re-scraping
both Vorgänge from live PARLIS (2026-07-10) confirms current ``main`` already
produces the correct, PDF-verified mapping — these tests pin that behaviour so it
cannot silently regress. ``link`` and ``titel`` are deterministic (parsed from
PARLIS' own Fundstellen, upstream of any PDF download or LLM call), so the
fixtures below are the exact raw Fundstellen PARLIS serves for each Vorgang
(``station_typ`` + ``pdf_url`` verified identical to the live response).

* **Fall 1 (V-213867, Landeshochschulgesetz 17/847).** Staging showed the
  "1. Lesung" card carrying the *Zweite Beratung* of a foreign bill
  (Klimaschutzgesetz 17/521), linking ``17_0013_06102021.pdf#page=23``. PARLIS in
  fact labels this Vorgang's first reading ``"Erste Beratung"`` and supplies the
  anchor ``#page=41`` directly in the Fundstelle href — the ``#page=23`` value was
  never in PARLIS' data for V-213867 and is not reproducible. The two plenary
  readings (Erste, Zweite) are also separated by the intervening committee report
  (parl-ausschber), so they never merge. Verified against the original PDF: page
  41 is where the Landeshochschulgesetz debate begins; page 23 is the Klima debate.

* **Fall 2 (V-215974, Staatshaushaltsplan Einzelplan 03).** Staging labelled both
  plenary readings "Zweite Beratung", shifting the cards by one round. Budget
  Einzelpläne have no separate *Erste Beratung* plenary station (the Einbringung
  runs on the Feststellungsgesetz Vorgang), so the true rounds are *Zweite* and
  *Dritte Beratung*. The round-aware merge (DD-024 / DD-026) is what keeps these
  correct: the drucksache-level ``"Beschluss des Landtags in Zweiter Beratung"``
  folds into the Zweite-Beratung station, while the ``"Dritte Beratung"`` stays a
  distinct station with its own label — see ``test_two_distinct_readings_not_merged``,
  which fails if ``_same_round_label`` regresses to round-blind merging.

The page anchors are the ones PARLIS serves in the Fundstelle href; the values
asserted here were cross-checked against the live protocol PDFs linked in the issue
(PP 17/13, 17/15, 17/22, 17/25).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper, _reading_round
from bawue.types import Doktyp, Stationstyp, Vorgang

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "parlis" / "issue33"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


async def _build(raw: dict) -> Vorgang:
    """Run the real station pipeline (no network, no LLM) over a fixture Vorgang."""
    scraper = object.__new__(BawueVorgaengeScraper)
    scraper._wahlperiode = 17
    scraper._llm_enabled = False
    scraper._llm = None
    scraper._filter_sonstig = True
    scraper.session = MagicMock()
    scraper._client = MagicMock()
    return await scraper._build_vorgang(raw)


def _vollvlsgn(vorgang: Vorgang):
    return [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_VOLLVLSGN]


def _redeprotokoll_doc(station):
    """The plenary-protocol document of a reading station (the one carrying the /Plp/ link)."""
    for d in station.dokumente:
        if "/Plp/" in d.link:
            return d
    raise AssertionError(f"no Plenarprotokoll document on station {station.typ}")


class TestFall1Landeshochschulgesetz:
    """V-213867: first reading must be the Erste Beratung of 17/847, not a foreign bill."""

    @pytest.mark.asyncio
    async def test_first_reading_is_erste_beratung_page_41(self):
        vorgang = await _build(_load("v213867"))
        readings = _vollvlsgn(vorgang)
        assert len(readings) == 2

        first = _redeprotokoll_doc(readings[0])
        # The exact defect the reviewer reported: titel "Zweite Beratung" + #page=23.
        assert first.titel == "Erste Beratung"
        assert first.typ == Doktyp.REDEPROTOKOLL
        assert first.link.endswith("17%5F0013%5F06102021.pdf#page=41")
        assert "#page=23" not in first.link

    @pytest.mark.asyncio
    async def test_second_reading_is_zweite_beratung_page_27(self):
        vorgang = await _build(_load("v213867"))
        second = _redeprotokoll_doc(_vollvlsgn(vorgang)[1])
        assert second.titel == "Zweite Beratung"
        assert second.link.endswith("17%5F0015%5F20102021.pdf#page=27")

    @pytest.mark.asyncio
    async def test_reading_rounds_are_one_then_two(self):
        vorgang = await _build(_load("v213867"))
        rounds = [_reading_round(_redeprotokoll_doc(s).titel) for s in _vollvlsgn(vorgang)]
        assert rounds == [1, 2]


class TestFall2StaatshaushaltEinzelplan:
    """V-215974: the two budget readings stay distinct and keep their true rounds (2, 3)."""

    @pytest.mark.asyncio
    async def test_two_distinct_readings_not_merged(self):
        vorgang = await _build(_load("v215974"))
        # Must not collapse to one (over-merge) nor split the merged Zweite Beratung.
        assert len(_vollvlsgn(vorgang)) == 2

    @pytest.mark.asyncio
    async def test_reading_titels_and_links(self):
        vorgang = await _build(_load("v215974"))
        readings = _vollvlsgn(vorgang)

        zweite = _redeprotokoll_doc(readings[0])
        assert zweite.titel == "Zweite Beratung"
        assert zweite.link.endswith("17%5F0022%5F15122021.pdf#page=49")

        dritte = _redeprotokoll_doc(readings[1])
        # Staging showed this mislabelled "Zweite Beratung" (the reported shift).
        assert dritte.titel == "Dritte Beratung"
        assert dritte.link.endswith("17%5F0025%5F22122021.pdf#page=26")

    @pytest.mark.asyncio
    async def test_rounds_are_two_then_three_monotonic(self):
        vorgang = await _build(_load("v215974"))
        rounds = [_reading_round(_redeprotokoll_doc(s).titel) for s in _vollvlsgn(vorgang)]
        assert rounds == [2, 3]
