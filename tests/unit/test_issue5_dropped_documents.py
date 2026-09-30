"""Regression tests for issue #5 — documents were dropped silently.

https://github.com/Parlamentszusammenfasser/pazufa-scraper-bw/issues/5

V-244180 (Juristenausbildungsgesetz) lists 8 Fundstellen in PARLIS, but only 6 were
persisted: the Entschließungsantrag of the SPD (Drs. 17/10262) was discarded without a
log line, the Mitteilung der Präsidentin (Drs. 17/10130, 22 S.) was filtered as
``sonstig`` at DEBUG level. Now the Entschließungsantrag is attached to the plenary
reading like an Änderungsantrag (DD-001), and every dropped Fundstelle is logged with
its reason and counted for the run summary.

The fixture is the raw PARLIS search response shared with the issue #26 tests.
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper, _print_vorgaenge_summary
from bawue.types import Doktyp, Stationstyp

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "parlis" / "issue26" / "v244180.json"


def _scraper() -> BawueVorgaengeScraper:
    scraper = object.__new__(BawueVorgaengeScraper)
    scraper._wahlperiode = 17
    scraper._llm_enabled = False
    scraper._llm = None
    scraper._filter_sonstig = True
    scraper.session = MagicMock()
    scraper._client = MagicMock()
    return scraper


def _raw() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


class TestV244180Juristenausbildungsgesetz:
    @pytest.mark.asyncio
    async def test_entschliessungsantrag_attached_to_zweite_beratung(self):
        vorgang = await _scraper()._build_vorgang(_raw())

        hits = [(s, d) for s in vorgang.stationen for d in s.dokumente if d.drucksnr == "17/10262"]
        assert len(hits) == 1
        station, dok = hits[0]
        assert station.typ == Stationstyp.PARL_VOLLVLSGN
        assert station.zp_start.date().isoformat() == "2026-02-04"
        assert dok.typ == Doktyp.ANTRAG
        assert [str(a.organisation) for a in dok.autoren] == ["Fraktion der SPD"]

    @pytest.mark.asyncio
    async def test_entschliessungsantrag_adds_no_station(self):
        vorgang = await _scraper()._build_vorgang(_raw())

        assert [s.typ for s in vorgang.stationen].count(Stationstyp.PARL_VOLLVLSGN) == 2

    @pytest.mark.asyncio
    async def test_mitteilung_dropped_with_info_log(self, caplog):
        with caplog.at_level(logging.INFO, logger="bawue.bawue_vorgaenge_scraper"):
            vorgang = await _scraper()._build_vorgang(_raw())

        assert all(d.drucksnr != "17/10130" for s in vorgang.stationen for d in s.dokumente)
        drops = [r for r in caplog.records if r.levelno == logging.INFO and "Dropping" in r.message]
        assert len(drops) == 1
        assert "17/10130" in drops[0].message
        assert "V-244180" in drops[0].message
        assert "sonstig" in drops[0].message

    @pytest.mark.asyncio
    async def test_dropped_fundstellen_counted_per_vorgang(self):
        scraper = _scraper()
        scraper._dropped_fundstellen = {}

        await scraper._build_vorgang(_raw())

        assert scraper._dropped_fundstellen == {"V-244180": ["Mitteilung → sonstig"]}


class TestDroppedFundstellenSummary:
    def test_summary_lists_dropped_fundstellen_per_vorgang(self):
        lines = _print_vorgaenge_summary(
            17,
            {},
            0,
            0,
            0,
            0,
            0,
            0,
            1.0,
            dropped_fundstellen={
                "V-244180": ["Mitteilung → sonstig"],
                "V-222724": ["Mitteilung → sonstig"] * 7,
            },
        )

        text = "\n".join(lines)
        assert "Vorgänge with dropped Fundstellen (2):" in text
        assert "V-244180 | 1x Mitteilung → sonstig" in text
        assert "V-222724 | 7x Mitteilung → sonstig" in text

    def test_summary_omits_section_without_drops(self):
        lines = _print_vorgaenge_summary(17, {}, 0, 0, 0, 0, 0, 0, 1.0, dropped_fundstellen={})

        assert not any("dropped Fundstellen" in line for line in lines)


def _fund(raw: str, station_typ: str, datum: str | None = "10.02.2026", drucksache: str = "17/10300") -> dict:
    return {
        "raw": raw,
        "datum": datum,
        "drucksache": drucksache,
        "station_typ": station_typ,
        "pdf_url": f"https://example.com/{drucksache.replace('/', '_')}.pdf",
    }


class TestEveryDropIsCounted:
    """The loud drops (WARNING/ERROR) are counted for the run summary, too."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("fund", "reason"),
        [
            (
                _fund("Stellungnahme  Fraktion GRÜNE  10.02.2026", "Stellungnahme"),
                "Stellungnahme without preceding station",
            ),
            (
                _fund("Entschließungsantrag  Fraktion der SPD  10.02.2026", "Entschließungsantrag"),
                "Änderungsantrag/Entschließungsantrag without parl-vollvlsgn",
            ),
            (_fund("Gesetzentwurf  Fraktion GRÜNE", "Gesetzentwurf", datum=None), "no parseable date"),
        ],
    )
    async def test_drop_is_counted(self, fund, reason):
        scraper = _scraper()
        scraper._dropped_fundstellen = {}
        raw = {"vorgangs_id": "V-9", "titel": "T", "Vorgangstyp": "Gesetzgebung", "fundstellen_parsed": [fund]}

        await scraper._build_vorgang(raw)

        assert scraper._dropped_fundstellen == {"V-9": [reason]}

    @pytest.mark.asyncio
    async def test_missing_date_drop_logged_once_at_error(self, caplog):
        """The drop replaces _build_station's own 'Skipping station' ERROR, not duplicates it."""
        fund = _fund("Gesetzentwurf  Fraktion GRÜNE", "Gesetzentwurf", datum=None)
        raw = {"vorgangs_id": "V-9", "titel": "T", "Vorgangstyp": "Gesetzgebung", "fundstellen_parsed": [fund]}

        with caplog.at_level(logging.INFO, logger="bawue.bawue_vorgaenge_scraper"):
            await _scraper()._build_vorgang(raw)

        drops = [r for r in caplog.records if "Dropping" in r.message or "Skipping station" in r.message]
        assert [(r.levelno, "V-9" in r.message) for r in drops] == [(logging.ERROR, True)]
