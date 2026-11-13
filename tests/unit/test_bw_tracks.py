"""BW track validation, checked locally instead of against the backend (DD-040).

A case is a station-letter string (letters in ``tests/bw_tracks.py``). The backend accepts
a prefix of a valid word, so a running process passes; a wrong one fails as soon as no
valid continuation is left.
"""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from bawue.enum_mapper import STATIONSTYP_MAP, VORGANGSTYP_MAP
from bawue.types import Stationstyp, Vorgangstyp
from tests.bw_tracks import BW_TRACKS, STATION_LETTERS, letters, passes

_GOLDEN = Path(__file__).parent.parent / "fixtures" / "parlis" / "issue100" / "built_vorgaenge.json"

_VALID = [
    pytest.param("S", id="Kabinettsbeschluss, noch nicht eingebracht"),
    pytest.param("RSI", id="Regierungsentwurf mit Anhoerung"),
    pytest.param("ERRSI", id="Eckpunkte und mehrere Anhoerungsentwuerfe"),
    pytest.param("ILAL", id="Fraktionsentwurf ohne Kabinett"),
    pytest.param("SIL", id="nach der ersten Lesung"),
    pytest.param("SILAL", id="Haushalt-Einzelplan, Issue #48"),
    pytest.param("SILLJ", id="zwei Lesungen ohne Ausschussbericht"),
    pytest.param("SILAAALJ", id="mehrere Ausschussberichte"),
    pytest.param("SILALJGK", id="abgeschlossen"),
    pytest.param("SILALALJGK", id="drei Lesungen, Haushalt GO 42"),
    pytest.param("SILALN", id="abgelehnt in zweiter Lesung"),
    pytest.param("SILALALN", id="abgelehnt in dritter Lesung"),
    pytest.param("IZ", id="zurueckgezogen vor der ersten Lesung"),
    pytest.param("SILZ", id="zurueckgezogen nach der ersten Lesung"),
    pytest.param("SILALZ", id="zurueckgezogen nach der zweiten Lesung"),
    pytest.param("SILALJC", id="Verfassungsgerichtsstopp vor dem Gesetzblatt"),
    pytest.param("SILALJGKC", id="Verfassungsgerichtsstopp nach Inkrafttreten"),
    pytest.param("SILALJGAKAC", id="Folgeberichte nach Gesetzblatt und Inkrafttreten"),
]

_INVALID = [
    pytest.param("L", id="Lesung ohne Einbringung"),
    pytest.param("SLI", id="Lesung vor der Einbringung"),
    pytest.param("ES", id="Eckpunkte ohne Anhoerungsentwurf"),
    pytest.param("SR", id="Anhoerung nach dem Kabinettsbeschluss"),
    pytest.param("SSIL", id="doppelter Kabinettsbeschluss, DD-046"),
    pytest.param("SIIL", id="doppelte Einbringung, DD-034"),
    pytest.param("SIA", id="Ausschussbericht vor der ersten Lesung, DD-025"),
    pytest.param("SILJ", id="Annahme in erster Lesung, GO 43 Abs. 4"),
    pytest.param("SILN", id="Ablehnung in erster Lesung, GO 43 Abs. 4"),
    pytest.param("SILALALAL", id="vier Lesungen, DD-024"),
    pytest.param("SILALALZ", id="zurueckgezogen nach der dritten Lesung"),
    pytest.param("SILALG", id="Gesetzblatt ohne Annahme"),
    pytest.param("SILALJK", id="Inkrafttreten ohne Gesetzblatt"),
    pytest.param("SILALJGG", id="zweites Gesetzblatt"),
    pytest.param("SILALNJ", id="Annahme nach Ablehnung"),
    pytest.param("SILZL", id="Lesung nach Ruecknahme"),
]


# gg-land-parl only: the other BW tracks equal it or deviate on purpose (e.g. §47a).
@pytest.mark.parametrize("track", _VALID)
def test_valid_track(track):
    assert passes("gg-land-parl", track)


@pytest.mark.parametrize("track", _INVALID)
def test_invalid_track(track):
    assert not passes("gg-land-parl", track)


def test_every_vorgangstyp_the_scraper_maps_has_a_bw_track():
    """Else „not specified for parliament BW“. ``sonstig`` is not enabled by default; ``gg-land-volk`` is a gap (DD-016)."""
    mapped = set(VORGANGSTYP_MAP.values()) - {Vorgangstyp.SONSTIG, Vorgangstyp.GG_LAND_VOLK}
    assert mapped <= BW_TRACKS.keys()


def test_every_stationstyp_of_a_gesetz_has_a_letter():
    """A Station without a letter fails the whole Vorgang."""
    emitted = {*STATIONSTYP_MAP.values(), Stationstyp.PREPARL_REGBSL, Stationstyp.PREPARL_REGENT}
    assert emitted - {Stationstyp.SONSTIG} <= STATION_LETTERS.keys()


# Invented fixture (gesetzgebung_results.html): one reading, then Annahme. PARLIS has no such
# Gesetz, and the scraper does not invent missing readings (DD-016).
_INVENTED = {"V-98001"}


def _golden() -> list:
    return [
        pytest.param(v, id=vorgnr, marks=pytest.mark.xfail(strict=True) if vorgnr in _INVENTED else ())
        for v in json.loads(_GOLDEN.read_text(encoding="utf-8"))
        if v["typ"] in BW_TRACKS
        for vorgnr in [v["ids"][0]["id"]]
    ]


@pytest.mark.parametrize("vorgang", _golden())
def test_built_vorgang_passes_its_track(vorgang):
    """Every Vorgang built from the PARLIS fixtures (issue #100) would be accepted."""
    stationen = [
        SimpleNamespace(typ=s["typ"], zp_start=datetime.fromisoformat(s["zp_start"])) for s in vorgang["stationen"]
    ]
    # Same-time Stationen of different typ have no defined order in the backend (DD-016).
    by_zp: dict = {}
    for s in stationen:
        by_zp.setdefault(s.zp_start, set()).add(s.typ)
    assert all(len(typen) == 1 for typen in by_zp.values()), by_zp

    assert passes(vorgang["typ"], letters(stationen)), letters(stationen)
