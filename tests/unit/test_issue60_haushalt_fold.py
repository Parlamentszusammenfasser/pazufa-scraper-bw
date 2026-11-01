"""Regression tests for issue #60: one Vorgang per budget.

https://github.com/Parlamentszusammenfasser/pazufa-scraper-bw/issues/60

PARLIS keeps every Einzelplan of a Staatshaushaltsgesetz as a Vorgang of its own,
and all of them cite its Gesetzentwurf as Initiativdrucksache (DD-041). The fixture
is the WP17 budget 2025/26 as ``ParlisClient.search`` returned it (2026-11-01): the
17 Einzelpläne, the Staatshaushaltsgesetz V-236470 (Drucksache 17/8000) and the
Haushaltsbegleitgesetz V-236465, which has an Initiativdrucksache of its own.
"""

import copy
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper, _fold_einzelplaene
from bawue.types import Stationstyp

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "parlis" / "issue60" / "haushalt_2025_26.json"
_STHG = "V-236470"
_BEGLEITGESETZ = "V-236465"


def _records() -> list[dict]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _scraper() -> BawueVorgaengeScraper:
    """Listing and station pipeline without network or LLM."""
    scraper = object.__new__(BawueVorgaengeScraper)
    scraper._wahlperiode = 17
    scraper._wahlperiode_start_date = None
    scraper._enabled_vorgangstypen = frozenset({"Haushaltsgesetzgebung"})
    scraper._parlis = MagicMock()
    scraper._parlis.pop_errors.return_value = []
    scraper._parlis_errors = []
    scraper._raw_cache = {}
    scraper._fingerprints = {}
    scraper._by_type = {}
    scraper._llm_enabled = False
    scraper._llm = None
    scraper._filter_sonstig = True
    scraper.session = MagicMock()
    scraper._client = MagicMock()
    return scraper


def _sthg(folded: list[dict]) -> dict:
    return next(r for r in folded if r["vorgangs_id"] == _STHG)


def test_folds_einzelplaene_into_the_staatshaushaltsgesetz(caplog):
    folded = _fold_einzelplaene(_records())

    assert "no single Staatshaushaltsgesetz" not in caplog.text
    assert sorted(r["vorgangs_id"] for r in folded) == [_BEGLEITGESETZ, _STHG]
    assert _sthg(folded)["titel"].startswith("Gesetz über die Feststellung des Staatshaushaltsplans")
    drucksachen = {f.get("drucksache") for f in _sthg(folded)["fundstellen_parsed"]}
    # The committee reports of Einzelplan 11 and 04, and Einzelplan 04's Änderungsanträge.
    assert {"17/8311", "17/8304", "17/8404"} <= drucksachen


def test_drops_what_the_staatshaushaltsgesetz_already_cites():
    """Einzelpläne re-cite its Gesetzentwurf, readings and Beschlüsse with own page ranges."""
    fundstellen = _sthg(_fold_einzelplaene(_records()))["fundstellen_parsed"]

    def raws(**cited: str) -> list[str]:
        key, value = next(iter(cited.items()))
        return [f["raw"] for f in fundstellen if f.get(key) == value]

    assert raws(drucksache="17/8000") == ["Gesetzentwurf    Landesregierung  22.10.2024 Drucksache 17/8000   (48 S.)"]
    assert len(raws(drucksache="17/8420")) == 1
    assert len(raws(plenarprotokoll="17/112")) == 1
    # Its own citation of the second reading, not the per-Einzelplan page ranges.
    assert len(raws(plenarprotokoll="17/109")) == 1
    assert "S. 6462-6554" in raws(plenarprotokoll="17/109")[0]


@pytest.mark.parametrize("titles", [("Einzelplan 11", "Einzelplan 04"), ("Gesetz A", "Gesetz B")])
def test_warns_unless_the_budget_has_one_staatshaushaltsgesetz(titles, caplog):
    """Issue #60 found exactly one per budget; otherwise a wrong record names the Vorgang."""
    records = [r for r in _records() if r["vorgangs_id"] in ("V-237492", "V-237478")]
    for record, titel in zip(records, titles, strict=True):
        record["titel"] = titel

    _fold_einzelplaene(records)

    assert "no single Staatshaushaltsgesetz" in caplog.text


def test_leaves_other_records_untouched():
    records = _records()
    begleitgesetz = copy.deepcopy(next(r for r in records if r["vorgangs_id"] == _BEGLEITGESETZ))
    gesetz = {**copy.deepcopy(records[0]), "vorgangs_id": "V-1", "Vorgangstyp": "Gesetzgebung"}

    folded = _fold_einzelplaene([gesetz, *records])

    assert gesetz in folded
    assert begleitgesetz in folded


@pytest.mark.asyncio
async def test_builds_one_track_valid_vorgang():
    """Committee reports merge into one station; Anträge land on the second reading."""
    vorgang = await _scraper()._build_vorgang(_sthg(_fold_einzelplaene(_records())))

    stationen = sorted(vorgang.stationen, key=lambda s: s.zp_start)
    assert [s.typ for s in stationen] == [
        Stationstyp.PREPARL_REGBSL,
        Stationstyp.PARL_INITIATIV,
        Stationstyp.PARL_VOLLVLSGN,
        Stationstyp.PARL_AUSSCHBER,
        Stationstyp.PARL_VOLLVLSGN,
        Stationstyp.PARL_VOLLVLSGN,
        Stationstyp.PARL_AKZEPTANZ,
        Stationstyp.POSTPARL_GSBLT,
    ]
    ausschber, zweite = stationen[3], stationen[4]
    assert len(ausschber.dokumente) == 18  # 17 Einzelpläne + Staatshaushaltsgesetz
    assert "17/8404" in {d.drucksnr for d in zweite.dokumente}
    for station in stationen:
        hashes = [d.hash_ for d in station.dokumente]
        assert len(hashes) == len(set(hashes)), f"duplicate document hash on {station.typ}"
    assert ("initdrucks", "17/8000") in {(i.typ, i.id) for i in vorgang.ids}


@pytest.mark.asyncio
async def test_listing_lists_the_budget_once_and_fingerprints_every_einzelplan():
    """A new Fundstelle on any Einzelplan must trigger a re-upload (DD-052)."""
    records = _records()
    scraper = _scraper()
    with patch("bawue.bawue_vorgaenge_scraper.asyncio.to_thread", return_value=records):
        ids = await scraper.listing_page_extractor("Haushaltsgesetzgebung")
    before = scraper._fingerprints[_STHG]

    einzelplan = next(r for r in records if r["vorgangs_id"] == "V-237492")
    einzelplan["fundstellen_parsed"].append(
        {"raw": "Mitteilung    Landesregierung  06.06.2025 Drucksache 17/9999", "drucksache": "17/9999"}
    )
    with patch("bawue.bawue_vorgaenge_scraper.asyncio.to_thread", return_value=records):
        await scraper.listing_page_extractor("Haushaltsgesetzgebung")

    assert sorted(ids) == [_BEGLEITGESETZ, _STHG]
    assert scraper._fingerprints[_STHG] != before
