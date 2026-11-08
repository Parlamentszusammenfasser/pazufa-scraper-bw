"""Issue #180 (DD-079): a Referentenentwurf from the Beteiligungsportal joins its PARLIS Vorgang.

The backend merges two PUTs only on a shared api_id or vg_ident, and neither source
names the other. So the PARLIS scraper takes over the draft's api_id, or, for a bill
the backend already has, adds the draft's station to it.
"""

import gzip
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import NAMESPACE_URL, uuid5

import pytest

from bawue.api import BawueApiError
from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper, _print_vorgaenge_summary
from bawue.beteiligung_client import BASE_URL
from bawue.beteiligung_parser import parse_process_detail
from bawue.cache import ENTWUERFE_KEY, UEBERNAHMEN_KEY, RedisUnavailableError
from bawue.pdf_text import PdfNotAvailableError
from bawue.types import UNSET, Doktyp, Stationstyp, Vorgang
from bawue.upload_throttle import UploadOutcome
from tests.factories import make_scraper
from tests.unit.test_bawue_scraper import _InMemoryCache, _make_raw_vorgang
from tests.unit.test_beteiligung_scraper import FIXTURES
from tests.unit.test_beteiligung_scraper import _make_scraper as _beteiligung_scraper

TEXTS = Path(__file__).parent.parent / "fixtures" / "entwuerfe"


@dataclass(frozen=True)
class Pair:
    """A WP 18 law a reader found split in two (#180): portal draft and PARLIS bill."""

    fixture: str
    slug: str
    released: date
    entwurf: str
    vid: str
    titel: str
    eingang: str
    drucksache: str
    drucksache_text: str

    @property
    def drucksache_pdf(self) -> str:
        return f"https://www.landtag-bw.de/files/{self.drucksache.replace('/', '_')}.pdf"


BVANP = Pair(
    "dienst_und_versorgungsbezuege_full.html",
    "dienst-und-versorgungsbezuege",
    date(2026, 6, 17),
    "entwurf_bvanpg_2026",
    "V-248237",
    "Gesetz über die Anpassung von Dienst- und Versorgungsbezügen in Baden-Württemberg 2026/2027/2028 "
    "(BVAnpGBW 2026/2027/2028)",
    "29.09.2026",
    "18/623",
    "drucksache_18_623",
)
HBEGLG = Pair(
    "haushaltsbegleitgesetz_2027_detail.html",
    "haushaltsbegleitgesetz-2027",
    date(2026, 10, 12),
    "entwurf_hbeglg_2027",
    "V-248580",
    "Haushaltsbegleitgesetz 2027",
    "20.10.2026",
    "18/701",
    "drucksache_18_701",
)


def _text(name: str) -> str:
    return gzip.decompress((TEXTS / f"{name}.txt.gz").read_bytes()).decode()


def _backend_merges(a: Vorgang, b: Vorgang) -> bool:
    """Mirror of pazufa-backend `vorgang_merge_candidates` (both BW, so the Bundesland check holds)."""

    def ids(v: Vorgang) -> set[tuple[str, str]]:
        return set() if v.ids is UNSET else {(i.typ, i.id) for i in v.ids}

    same_frame = a.wahlperiode == b.wahlperiode and a.typ == b.typ
    return same_frame and (a.api_id == b.api_id or bool(ids(a) & ids(b)))


async def _upload_draft(cache, pair: Pair, *, slug: str | None = None, released=None, wahlperiode=18) -> Vorgang:
    """Build the draft and store it as the pipeline does after its upload succeeded."""
    slug = slug or pair.slug
    scraper = _beteiligung_scraper()
    scraper._wahlperiode = wahlperiode
    scraper.config.cache = cache
    scraper._client.fetch_last_modified.return_value = released or pair.released
    scraper._fingerprints[slug] = "fingerprint"
    vorgang = await scraper._build_vorgang(slug, parse_process_detail((FIXTURES / pair.fixture).read_text(), BASE_URL))
    await scraper.store_extracted_result(slug, vorgang)
    return vorgang


def _parlis(cache) -> BawueVorgaengeScraper:
    scraper = make_scraper(BawueVorgaengeScraper)
    scraper._wahlperiode = 18
    scraper._llm_enabled = False
    scraper._llm = None
    scraper._filter_sonstig = True
    scraper.session = MagicMock()
    scraper._client = MagicMock()
    scraper.config = MagicMock()
    scraper.config.cache = cache
    scraper._entwuerfe = json.loads(cache.get_raw(ENTWUERFE_KEY) or "{}")  # as run() reads it per cycle
    return scraper


def _raw(pair: Pair, initiative: str = "Landesregierung") -> dict:
    fund = {
        "raw": f"Gesetzentwurf    {initiative}  {pair.eingang} Drucksache {pair.drucksache}   (40 S.)",
        "datum": pair.eingang,
        "drucksache": pair.drucksache,
        "station_typ": "Gesetzentwurf",
        "seiten": 40,
        "pdf_url": pair.drucksache_pdf,
    }
    return _make_raw_vorgang(pair.vid, titel=pair.titel, initiative=initiative, fundstellen=[fund])


def _pdfs(*pairs: Pair, entwurf_text: str | None = None) -> dict[str, str]:
    """PDF texts by URL: each pair's draft and Drucksache."""
    texts = {}
    for pair in pairs:
        detail = parse_process_detail((FIXTURES / pair.fixture).read_text(), BASE_URL)
        texts[detail.pdf_links[0]["url"]] = _text(entwurf_text or pair.entwurf)
        texts[pair.drucksache_pdf] = _text(pair.drucksache_text)
    return texts


async def _build(scraper, raw, pdfs: dict[str, str | Exception], *, uploaded: bool | Exception = False):
    """Build the bill as item_extractor does; `uploaded`: the backend already has it, or asking it raises."""

    async def _read(_session, url):
        if isinstance(text := pdfs[url], Exception):
            raise text
        return text

    read = AsyncMock(side_effect=_read)
    error = uploaded if isinstance(uploaded, Exception) else None
    with (
        patch("bawue.bawue_vorgaenge_scraper.read_pdf", read),
        patch("bawue.bawue_vorgaenge_scraper.vorgang_exists", return_value=uploaded, side_effect=error) as exists,
    ):
        vorgang = await scraper._build_vorgang(raw)
    return vorgang, read, exists


def _own_api_id(pair: Pair) -> str:
    return str(uuid5(NAMESPACE_URL, pair.vid))


def _uebernahmen(cache) -> dict:
    return json.loads(cache.get_raw(UEBERNAHMEN_KEY) or "{}")


@pytest.mark.asyncio
@pytest.mark.parametrize(("pair", "score"), [(BVANP, "0.86"), (HBEGLG, "0.89")], ids=["18-623", "18-701"])
async def test_issue180_new_bill_takes_over_the_draft(pair, score):
    """The backend merges the bill into the draft's Vorgang, which keeps its regent station."""
    cache = _InMemoryCache()
    regent = await _upload_draft(cache, pair)
    scraper = _parlis(cache)

    parlis, _, exists = await _build(scraper, _raw(pair), _pdfs(pair))

    assert _backend_merges(regent, parlis)
    assert parlis.api_id == regent.api_id
    assert Stationstyp.PREPARL_REGENT not in [s.typ for s in parlis.stationen]
    assert regent.links[0] in parlis.links
    exists.assert_called_once_with(ANY, _own_api_id(pair))
    assert _uebernahmen(cache) == {pair.slug: {"vorgnr": pair.vid, "api_id": regent.api_id}}
    # A wrong match would be silent otherwise.
    assert scraper._entwurf_joins == [f"{pair.vid} ← {pair.slug} ({score})"]


@pytest.mark.asyncio
async def test_bill_uploaded_before_keeps_its_api_id_and_gains_the_draft_station():
    """A second api_id would make the backend find two Vorgänge (AmbiguousMatch), so the bill keeps its own."""
    cache = _InMemoryCache()
    regent = await _upload_draft(cache, BVANP)
    scraper = _parlis(cache)

    parlis, _, _ = await _build(scraper, _raw(BVANP), _pdfs(BVANP), uploaded=True)

    assert parlis.api_id == _own_api_id(BVANP)
    assert regent.links[0] in parlis.links
    entwurf, regbsl = sorted(parlis.stationen, key=lambda s: s.zp_start)[:2]
    assert (entwurf.typ, regbsl.typ) == (Stationstyp.PREPARL_REGENT, Stationstyp.PREPARL_REGBSL)
    assert entwurf.zp_start == datetime(2026, 6, 17, tzinfo=UTC)
    assert entwurf.api_id is not UNSET
    # The same document rows as the draft's, so the backend links them instead of adding new ones.
    [dok] = entwurf.dokumente
    [portal_dok] = regent.stationen[0].dokumente
    assert (dok.titel, dok.link, dok.hash_, dok.typ, dok.autoren) == (
        portal_dok.titel,
        portal_dok.link,
        portal_dok.hash_,
        Doktyp.PREPARL_ENTWURF,
        regent.initiatoren,
    )
    assert _uebernahmen(cache) == {BVANP.slug: {"vorgnr": BVANP.vid, "api_id": _own_api_id(BVANP)}}
    assert scraper._entwurf_joins == [f"{BVANP.vid} ← {BVANP.slug} (0.86)"]


def test_run_report_lists_every_join():
    lines = _print_vorgaenge_summary(
        18,
        {},
        new_or_retried=1,
        changed=0,
        cached=0,
        published=1,
        skipped=0,
        failed=0,
        duration=1.0,
        entwurf_joins=["V-248580 ← haushaltsbegleitgesetz-2027 (0.89)", "V-248237 ← x (0.86)"],
    )
    i = lines.index("Bills joined to a Beteiligung draft (2, DD-079):")
    assert lines[i + 1 : i + 3] == ["  - V-248580 ← haushaltsbegleitgesetz-2027 (0.89)", "  - V-248237 ← x (0.86)"]


async def _report(cache, exists=lambda *_: True) -> tuple[list[str], MagicMock]:
    """A later run's summary, which goes to Mattermost; the bills are cached, so it only reports."""
    scraper = _parlis(cache)
    scraper._by_type, scraper._failed_items, scraper._parlis_errors = {}, [], []
    scraper._published = scraper._skipped = scraper._failed = 0
    with (
        patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.run", AsyncMock()),
        patch("bawue.bawue_vorgaenge_scraper.check_for_newer_wahlperiode"),
        patch("bawue.bawue_vorgaenge_scraper.vorgang_exists", side_effect=exists) as get,
    ):
        await scraper.run()
    return scraper.summary[1], get


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exists", "listed"),
    [(lambda *_: True, True), (lambda *_: False, False), (BawueApiError(503, b"", "vorgang_get_by_id"), True)],
    ids=["not-deleted", "deleted", "backend-down"],
)
async def test_stale_portal_vorgang_is_reported_every_run_until_deleted(exists, listed):
    """Not only in the deciding run: an admin back from vacation still finds it."""
    cache = _InMemoryCache()
    regent = await _upload_draft(cache, BVANP)
    await _build(_parlis(cache), _raw(BVANP), _pdfs(BVANP), uploaded=True)

    lines, get = await _report(cache, exists)

    get.assert_called_once_with(ANY, regent.api_id)
    assert (":warning: Portal Vorgänge to delete (1, DD-079):" in lines) is listed
    assert (f"  - {regent.api_id} ({BVANP.slug}), joined to {BVANP.vid}" in lines) is listed


@pytest.mark.asyncio
async def test_report_says_when_redis_failed_before_the_check():
    """The check runs in run()'s finally: raising there would lose the whole summary."""
    cache = _InMemoryCache()
    await _upload_draft(cache, BVANP)
    await _build(_parlis(cache), _raw(BVANP), _pdfs(BVANP), uploaded=True)
    get_raw = cache.get_raw

    def _down_after_start(key, typehint=""):
        if key == UEBERNAHMEN_KEY:
            raise RedisUnavailableError("Redis read failed")
        return get_raw(key)

    cache.get_raw = _down_after_start
    lines, _ = await _report(cache)

    assert "  - not checked: Redis unavailable" in lines


@pytest.mark.asyncio
async def test_taken_over_draft_leaves_nothing_to_delete():
    cache = _InMemoryCache()
    await _upload_draft(cache, BVANP)
    await _build(_parlis(cache), _raw(BVANP), _pdfs(BVANP))

    lines, get = await _report(cache)

    get.assert_not_called()
    assert not [line for line in lines if "to delete" in line]


@pytest.mark.asyncio
@pytest.mark.parametrize("uploaded", [False, True], ids=["taken-over", "added-station"])
async def test_the_decision_is_made_once(uploaded):
    cache = _InMemoryCache()
    await _upload_draft(cache, BVANP)
    first, _, _ = await _build(_parlis(cache), _raw(BVANP), _pdfs(BVANP), uploaded=uploaded)

    again, read, exists = await _build(_parlis(cache), _raw(BVANP), {}, uploaded=not uploaded)

    assert again.api_id == first.api_id
    assert [s.typ for s in again.stationen] == [s.typ for s in first.stationen]
    read.assert_not_called()
    exists.assert_not_called()


@pytest.mark.asyncio
async def test_with_llm_the_enriched_texts_decide_and_the_station_is_enriched(monkeypatch):
    """Production: enrichment already read the Drucksache, and the draft's station carries its text."""
    from bawue.bawue_dok import EnrichmentResult

    cache = _InMemoryCache()
    regent = await _upload_draft(cache, BVANP)
    texts = _pdfs(BVANP)

    async def _enrich(_session, _llm, dok, **_kw):
        dok.volltext = texts[dok.link]
        return EnrichmentResult(dokument=dok)

    monkeypatch.setattr("bawue.bawue_dok.enrich_dokument", _enrich)
    monkeypatch.setattr("bawue.bawue_vorgaenge_scraper.vorgang_kurztitel", AsyncMock(return_value="Kurz"))
    monkeypatch.setattr("bawue.bawue_vorgaenge_scraper.vorgang_ressort", AsyncMock(return_value=None))
    scraper = _parlis(cache)
    scraper._llm_enabled, scraper._llm, scraper._llm_model, scraper._llm_metrics = True, MagicMock(), "m", MagicMock()
    entwurf_pdf = regent.stationen[0].dokumente[0].link

    parlis, read, _ = await _build(scraper, _raw(BVANP), {entwurf_pdf: texts[entwurf_pdf]}, uploaded=True)

    read.assert_called_once_with(ANY, entwurf_pdf)
    assert parlis.stationen[0].dokumente[0].volltext == texts[entwurf_pdf]


@pytest.mark.asyncio
async def test_unsaved_decision_retries_next_cycle():
    cache = _InMemoryCache()
    regent = await _upload_draft(cache, BVANP)
    store_raw = cache.store_raw
    cache.store_raw = lambda key, value, typehint="": key != UEBERNAHMEN_KEY and store_raw(key, value)
    scraper = _parlis(cache)

    parlis, _, _ = await _build(scraper, _raw(BVANP), _pdfs(BVANP))

    assert parlis.api_id == regent.api_id
    assert BVANP.vid in scraper._retry_next_cycle


@pytest.mark.asyncio
@pytest.mark.parametrize("down", ["backend", "redis"])
async def test_failure_while_deciding_records_nothing(down):
    """A decision on a guess would be permanent: the build fails, uncached (#83), and decides next cycle."""
    cache = _InMemoryCache()
    regent = await _upload_draft(cache, BVANP)
    scraper = _parlis(cache)
    get_raw = cache.get_raw
    uploaded: bool | Exception = False
    if down == "backend":
        uploaded = BawueApiError(503, b"", "vorgang_get_by_id")
    else:

        def _down(key, typehint=""):
            if key == UEBERNAHMEN_KEY:
                raise RedisUnavailableError("Redis read failed")
            return get_raw(key)

        cache.get_raw = _down

    with pytest.raises((BawueApiError, RedisUnavailableError)):
        await _build(scraper, _raw(BVANP), _pdfs(BVANP), uploaded=uploaded)

    cache.get_raw = get_raw
    assert _uebernahmen(cache) == {}
    assert scraper._entwurf_joins == []
    parlis, _, _ = await _build(_parlis(cache), _raw(BVANP), _pdfs(BVANP))
    assert parlis.api_id == regent.api_id


@pytest.mark.asyncio
async def test_second_bill_matching_a_draft_taken_meanwhile_stays_apart():
    """Two bills of one cycle may match concurrently: the first decision stands."""
    cache = _InMemoryCache()
    await _upload_draft(cache, BVANP)
    scraper = _parlis(cache)

    async def _taken_meanwhile(*_args):
        cache.store_raw(UEBERNAHMEN_KEY, json.dumps({BVANP.slug: {"vorgnr": "V-1", "api_id": "x"}}))
        return BVANP.slug, 0.86

    with patch.object(scraper, "_match_entwurf", _taken_meanwhile):
        parlis, _, exists = await _build(scraper, _raw(BVANP), {})

    assert parlis.api_id == _own_api_id(BVANP)
    exists.assert_not_called()
    assert _uebernahmen(cache)[BVANP.slug]["vorgnr"] == "V-1"


# -- No join: the bill stays as it is ------------------------------------------------


async def _assert_separate(cache, pair: Pair, pdfs: dict[str, str], raw: dict | None = None) -> AsyncMock:
    scraper = _parlis(cache)
    parlis, read, exists = await _build(scraper, raw or _raw(pair), pdfs)
    assert parlis.api_id == _own_api_id(pair)
    assert Stationstyp.PREPARL_REGENT not in [s.typ for s in parlis.stationen]
    assert parlis.links is UNSET
    exists.assert_not_called()
    assert pair.vid not in json.dumps(_uebernahmen(cache))
    assert pair.vid not in scraper._retry_next_cycle  # settled: cached until its fingerprint changes
    return read


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("draft", "released", "wahlperiode"),
    [(HBEGLG, None, 18), (BVANP, date(2025, 9, 28), 18), (BVANP, None, 17)],
    ids=["after-the-drucksache", "over-a-year-before", "other-wahlperiode"],
)
async def test_draft_outside_the_window_is_never_read(draft, released, wahlperiode):
    """The BVAnp bill (29.09.2026) against a draft released 12.10.2026, 28.09.2025, or in WP 17."""
    cache = _InMemoryCache()
    await _upload_draft(cache, draft, released=released, wahlperiode=wahlperiode)
    read = await _assert_separate(cache, BVANP, _pdfs(BVANP, HBEGLG))
    read.assert_not_called()


@pytest.mark.asyncio
async def test_bill_by_a_fraktion_has_no_draft():
    cache = _InMemoryCache()
    await _upload_draft(cache, BVANP)
    read = await _assert_separate(cache, BVANP, _pdfs(BVANP), _raw(BVANP, initiative="Fraktion GRÜNE"))
    read.assert_not_called()


@pytest.mark.asyncio
async def test_draft_of_another_law_is_not_taken_over():
    """The Haushaltsbegleitgesetz bill (20.10.) has the BVAnp draft (17.06.) in its window."""
    cache = _InMemoryCache()
    await _upload_draft(cache, BVANP)
    await _assert_separate(cache, HBEGLG, _pdfs(BVANP, HBEGLG))


@pytest.mark.asyncio
async def test_last_years_law_is_not_taken_over():
    """The 2024 salary law reads much like the 2026 one, yet shares only 0.09 of its 5-grams."""
    cache = _InMemoryCache()
    await _upload_draft(cache, BVANP)
    await _assert_separate(cache, BVANP, _pdfs(BVANP, entwurf_text="entwurf_bvanpg_2024"))


@pytest.mark.asyncio
async def test_drucksache_without_text_has_no_draft():
    """OCR found no text: every run would read the same, so a retry would only repeat OCR and upload."""
    cache = _InMemoryCache()
    await _upload_draft(cache, BVANP)
    await _assert_separate(cache, BVANP, {**_pdfs(BVANP), BVANP.drucksache_pdf: ""})


@pytest.mark.asyncio
async def test_two_drafts_of_one_law_are_both_left_alone():
    cache = _InMemoryCache()
    await _upload_draft(cache, BVANP)
    await _upload_draft(cache, BVANP, slug="dienst-und-versorgungsbezuege-2")
    await _assert_separate(cache, BVANP, _pdfs(BVANP))


@pytest.mark.asyncio
async def test_draft_taken_over_by_another_bill_is_not_a_candidate():
    cache = _InMemoryCache()
    await _upload_draft(cache, BVANP)
    cache.store_raw(UEBERNAHMEN_KEY, json.dumps({BVANP.slug: {"vorgnr": "V-1", "api_id": "x"}}))
    read = await _assert_separate(cache, BVANP, _pdfs(BVANP))
    read.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["drucksache-not-yet", "drucksache-down", "draft-down", "drucksache-unlisted"])
async def test_unread_pdf_retries_next_cycle(missing):
    """Uncached: a bill with a candidate must not settle on 'no draft' because a PDF was missing."""
    cache = _InMemoryCache()
    regent = await _upload_draft(cache, BVANP)
    entwurf_pdf = regent.stationen[0].dokumente[0].link
    pdfs: dict[str, str | Exception] = {**_pdfs(BVANP)}
    raw = _raw(BVANP)
    if missing == "drucksache-not-yet":
        pdfs[BVANP.drucksache_pdf] = PdfNotAvailableError(404, BVANP.drucksache_pdf)
    elif missing == "drucksache-down":
        pdfs[BVANP.drucksache_pdf] = TimeoutError()
    elif missing == "draft-down":
        pdfs[entwurf_pdf] = PdfNotAvailableError(503, entwurf_pdf)
    else:
        raw["fundstellen_parsed"][0]["pdf_url"] = ""
    scraper = _parlis(cache)

    parlis, read, _ = await _build(scraper, raw, pdfs)

    assert parlis.api_id == _own_api_id(BVANP)
    assert BVANP.vid in scraper._retry_next_cycle
    assert _uebernahmen(cache) == {}
    if missing != "draft-down":  # without the bill's text no draft can be decided, so none is read
        assert entwurf_pdf not in [c.args[1] for c in read.call_args_list]


@pytest.mark.asyncio
async def test_draft_the_portal_removed_is_no_candidate():
    """A 404 draft would block every bill in its window for a year; the bill's own draft still wins."""
    cache = _InMemoryCache()
    gone = await _upload_draft(cache, BVANP)
    regent = await _upload_draft(cache, HBEGLG)
    pdfs: dict[str, str | Exception] = {**_pdfs(BVANP, HBEGLG)}
    gone_pdf = gone.stationen[0].dokumente[0].link
    pdfs[gone_pdf] = PdfNotAvailableError(404, gone_pdf)
    scraper = _parlis(cache)

    parlis, _, _ = await _build(scraper, _raw(HBEGLG), pdfs)

    assert parlis.api_id == regent.api_id
    assert HBEGLG.vid not in scraper._retry_next_cycle


@pytest.mark.asyncio
async def test_draft_pdf_the_portal_removed_leaves_the_others_to_decide():
    cache = _InMemoryCache()
    regent = await _upload_draft(cache, BVANP)
    gone = "https://beteiligungsportal.baden-wuerttemberg.de/fileadmin/anlage.pdf"
    entwuerfe = json.loads(cache.get_raw(ENTWUERFE_KEY))
    entwuerfe[BVANP.slug]["pdfs"].insert(0, {"url": gone, "titel": "Anlage", "datum": "2026-06-17"})
    cache.store_raw(ENTWUERFE_KEY, json.dumps(entwuerfe))
    scraper = _parlis(cache)

    parlis, read, _ = await _build(scraper, _raw(BVANP), {**_pdfs(BVANP), gone: PdfNotAvailableError(404, gone)})

    assert parlis.api_id == regent.api_id
    assert BVANP.vid not in scraper._retry_next_cycle
    assert gone in [c.args[1] for c in read.call_args_list]


# -- Beteiligung side -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_registers_the_draft():
    cache = _InMemoryCache()
    regent = await _upload_draft(cache, BVANP)

    assert json.loads(cache.get_raw(ENTWUERFE_KEY)) == {
        BVANP.slug: {
            "api_id": regent.api_id,
            "wahlperiode": 18,
            "portal": f"{BASE_URL}/de/mitmachen/lp-18/{BVANP.slug}",
            "autoren": [{"organisation": "Ministerium für Finanzen"}],
            "pdfs": [
                {
                    "url": regent.stationen[0].dokumente[0].link,
                    "titel": regent.stationen[0].dokumente[0].titel,
                    "datum": "2026-06-17",
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_taken_over_draft_is_never_uploaded_again():
    """From then on PARLIS writes the Vorgang; a draft PUT would overwrite the bill's titel."""
    cache = _InMemoryCache()
    scraper = _beteiligung_scraper()
    scraper.config.cache = cache
    scraper._fingerprints[BVANP.slug] = "the portal page changed"
    assert await scraper.get_cached_result(BVANP.slug) is None

    cache.store_raw(UEBERNAHMEN_KEY, json.dumps({BVANP.slug: {"vorgnr": BVANP.vid, "api_id": "x"}}))

    assert await scraper.get_cached_result(BVANP.slug) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(("taken", "uploaded"), [(BVANP.slug, False), ("another-law", True)], ids=["own", "other"])
async def test_draft_taken_over_while_it_was_built_is_not_uploaded(taken, uploaded):
    """PARLIS may take the draft over during the build (LLM enrichment): the cache check was before it."""
    cache = _InMemoryCache()
    scraper = _beteiligung_scraper()
    scraper._wahlperiode = 18
    scraper.config.cache = cache
    regent = await scraper._build_vorgang(
        BVANP.slug, parse_process_detail((FIXTURES / BVANP.fixture).read_text(), BASE_URL)
    )
    cache.store_raw(UEBERNAHMEN_KEY, json.dumps({taken: {"vorgnr": BVANP.vid, "api_id": "x"}}))

    with patch("bawue.bawue_beteiligung_scraper.upload_vorgang", return_value=UploadOutcome(regent)) as upload:
        sent = await scraper.send_result(regent)

    assert upload.called is uploaded
    assert (sent is not None) is uploaded
    assert scraper._skipped == (0 if uploaded else 1)


# -- Fingerprint: a draft registered later rebuilds the bill once ---------------------


async def _fingerprint(cache, raw: dict) -> str:
    scraper = _parlis(cache)
    scraper._wahlperiode_start_date = None
    scraper._enabled_vorgangstypen = frozenset({"Gesetzgebung"})
    scraper._raw_cache, scraper._fingerprints, scraper._by_type, scraper._parlis_errors = {}, {}, {}, []
    scraper._parlis = MagicMock()
    scraper._parlis.pop_errors.return_value = []
    with patch("bawue.bawue_vorgaenge_scraper.asyncio.to_thread", AsyncMock(return_value=[raw])):
        await scraper.listing_page_extractor("Gesetzgebung")
    return scraper._fingerprints[raw["vorgangs_id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["Gesetzentwurf", ""], ids=["parsed", "unparsed"])
async def test_a_draft_in_the_window_changes_the_bills_fingerprint_once(label, caplog):
    """Unparsed: the raw text decides (DD-011), quietly, as listing runs for every Vorgang every cycle."""
    raw = _raw(BVANP)
    raw["fundstellen_parsed"][0]["station_typ"] = label
    before = await _fingerprint(_InMemoryCache(), raw)
    cache = _InMemoryCache()
    await _upload_draft(cache, BVANP)
    caplog.clear()
    registered = await _fingerprint(cache, raw)
    cache.store_raw(UEBERNAHMEN_KEY, json.dumps({BVANP.slug: {"vorgnr": BVANP.vid, "api_id": "x"}}))

    assert registered != before
    assert await _fingerprint(cache, raw) == registered
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "draft"),
    [(_raw(BVANP, initiative="Fraktion GRÜNE"), BVANP), (_raw(BVANP), HBEGLG)],
    ids=["fraktion", "draft-after-the-drucksache"],
)
async def test_other_vorgaenge_keep_their_fingerprint(raw, draft):
    cache = _InMemoryCache()
    before = await _fingerprint(cache, raw)
    await _upload_draft(cache, draft)
    assert await _fingerprint(cache, raw) == before
