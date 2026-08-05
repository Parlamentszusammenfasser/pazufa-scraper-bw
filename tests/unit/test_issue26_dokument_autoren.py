"""Regression tests for issue #26 — Dokument.autoren fell back to the Vorgang initiator.

https://github.com/Parlamentszusammenfasser/pazufa-scraper-bw/issues/26

Before the fix, every document whose PARLIS Fundstelle names no author inherited the
*initiator of the Vorgang* (DD-042 step 3). PARLIS names an author for the Gesetzentwurf
and a committee for the Beschlussempfehlung, but for nothing else — so a Gesetzblatt
signed „Die Regierung des Landes Baden-Württemberg" was attributed to *Fraktion der SPD*
merely because the SPD had introduced the bill, and the Landtag's own Gesetzesbeschluss
carried the initiator too.

The fixtures are the raw PARLIS search responses for the three Vorgänge cited as evidence
in the issue (fetched 05.08.2026 via ``ParlisClient.search``, stored verbatim). The tests
run the real station pipeline over them — no network, no LLM — so they cover the whole
chain from Fundstelle to Dokument, not just the author lookup.

Expected attribution, verified against each document's own text:

* ``Gesetzentwurf`` → the initiator (PARLIS supplies ``autor_text``) — unchanged.
* ``Beschlussempfehlung und Bericht`` → the committee (``ausschuss``) — unchanged, DD-042 / #71.
* ``Gesetzesbeschluss des Landtags`` → Landtag ("Der Landtag hat am … beschlossen",
  Drs. 17/10246, 17/2290).
* ``Beschluss des Landtags in Zweiter/Dritter Beratung`` → Landtag (Drs. 17/8420, 17/8443).
* Plenarprotokolle (Erste/Zweite/Dritte Beratung) → Landtag.
* ``Gesetz`` im Gesetzblatt → Landesregierung (GBl 2026 Nr. 12, p. 2: „Stuttgart, den
  10. Februar 2026 — Die Regierung des Landes Baden-Württemberg: Kretschmann, …").
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper
from bawue.types import Doktyp, Dokument, Stationstyp, Vorgang

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "parlis" / "issue26"


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


def _dokumente(vorgang: Vorgang) -> list[tuple[Stationstyp, Dokument]]:
    return [(s.typ, d) for s in vorgang.stationen for d in s.dokumente]


def _autoren(dok: Dokument) -> list[str]:
    return [str(a.organisation) for a in dok.autoren]


def _by_titel(vorgang: Vorgang, titel: str) -> Dokument:
    matches = [d for _, d in _dokumente(vorgang) if d.titel == titel]
    assert matches, f"no document titled {titel!r} in {[d.titel for _, d in _dokumente(vorgang)]}"
    assert len(matches) == 1, f"{len(matches)} documents titled {titel!r}"
    return matches[0]


class TestV244180Juristenausbildungsgesetz:
    """SPD bill: the Gesetzblatt and the Gesetzesbeschluss must not carry the SPD."""

    @pytest.mark.asyncio
    async def test_gesetzblatt_is_landesregierung_not_fraktion(self):
        gesetz = _by_titel(await _build(_load("v244180")), "Gesetz")
        assert "gesetzblaetter/2026/GBl2026012.pdf" in gesetz.link
        assert _autoren(gesetz) == ["Landesregierung"]

    @pytest.mark.asyncio
    async def test_gesetzesbeschluss_is_landtag(self):
        beschluss = _by_titel(await _build(_load("v244180")), "Gesetzesbeschluss des Landtags")
        assert beschluss.drucksnr == "17/10246"
        assert _autoren(beschluss) == ["Landtag"]

    @pytest.mark.asyncio
    async def test_plenarprotokolle_are_landtag(self):
        vorgang = await _build(_load("v244180"))
        readings = [d for _, d in _dokumente(vorgang) if d.typ == Doktyp.REDEPROTOKOLL]
        assert [d.titel for d in readings] == ["Erste Beratung", "Zweite Beratung"]
        assert [_autoren(d) for d in readings] == [["Landtag"], ["Landtag"]]

    @pytest.mark.asyncio
    async def test_gesetzentwurf_keeps_its_parlis_author(self):
        entwurf = _by_titel(await _build(_load("v244180")), "Gesetzentwurf")
        assert entwurf.drucksnr == "17/9871"
        assert _autoren(entwurf) == ["Fraktion der SPD"]

    @pytest.mark.asyncio
    async def test_beschlussempfehlung_keeps_the_committee_issue71(self):
        """The DD-042 committee step must survive — no #71 regression."""
        empfehlung = _by_titel(await _build(_load("v244180")), "Beschlussempfehlung und Bericht")
        assert _autoren(empfehlung) == ["Ständiger Ausschuss"]

    @pytest.mark.asyncio
    async def test_only_the_gesetzentwurf_carries_the_fraktion(self):
        """Acceptance criterion: the initiator appears on the document it actually wrote."""
        vorgang = await _build(_load("v244180"))
        carrying_spd = [d.titel for _, d in _dokumente(vorgang) if "Fraktion der SPD" in _autoren(d)]
        assert carrying_spd == ["Gesetzentwurf"]


class TestV217223Medienstaatsvertrag:
    """Landesregierung bill: the initiator fallback was accidentally plausible here."""

    @pytest.mark.asyncio
    async def test_gesetzesbeschluss_is_landtag_not_the_initiator(self):
        beschluss = _by_titel(await _build(_load("v217223")), "Gesetzesbeschluss des Landtags")
        assert beschluss.drucksnr == "17/2290"
        assert _autoren(beschluss) == ["Landtag"]

    @pytest.mark.asyncio
    async def test_gesetzblatt_stays_landesregierung(self):
        assert _autoren(_by_titel(await _build(_load("v217223")), "Gesetz")) == ["Landesregierung"]

    @pytest.mark.asyncio
    async def test_gesetzentwurf_keeps_the_landesregierung(self):
        # The Regierungsentwurf is listed twice: once at the cabinet station, once
        # in parliament. Both keep the author PARLIS supplies.
        vorgang = await _build(_load("v217223"))
        entwuerfe = [d for _, d in _dokumente(vorgang) if d.titel == "Gesetzentwurf"]
        assert [_autoren(d) for d in entwuerfe] == [["Landesregierung"], ["Landesregierung"]]


class TestV237492StaatshaushaltsplanEinzelplan11:
    """Budget Einzelplan: both „Beschluss des Landtags in … Beratung" belong to the Landtag."""

    @pytest.mark.asyncio
    async def test_beschluesse_in_zweiter_und_dritter_beratung_are_landtag(self):
        vorgang = await _build(_load("v237492"))
        beschluesse = [d for _, d in _dokumente(vorgang) if d.titel.startswith("Beschluss des Landtags in")]
        assert [d.drucksnr for d in beschluesse] == ["17/8420", "17/8443"]
        assert [_autoren(d) for d in beschluesse] == [["Landtag"], ["Landtag"]]

    @pytest.mark.asyncio
    async def test_beschlussempfehlung_keeps_the_finance_committee(self):
        empfehlung = _by_titel(await _build(_load("v237492")), "Beschlussempfehlung und Bericht")
        assert _autoren(empfehlung) == ["Ausschuss für Finanzen"]

    @pytest.mark.asyncio
    async def test_no_document_carries_the_landesregierung_except_its_entwurf(self):
        vorgang = await _build(_load("v237492"))
        carrying = [d.titel for _, d in _dokumente(vorgang) if "Landesregierung" in _autoren(d)]
        # Twice: the Regierungsentwurf is listed at the cabinet station and in parliament.
        assert carrying == ["Gesetzentwurf", "Gesetzentwurf"]
