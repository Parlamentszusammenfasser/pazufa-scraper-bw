"""Integration tests for issue #25, driven from the real PARLIS and PDF sources.

Issue #25 (https://github.com/Parlamentszusammenfasser/pazufa-scraper-bw/issues/25)
reported that a Plenarprotokoll PDF is persisted as **one shared `Dokument` row** for
every Vorgang debated in that sitting. Sharing `hash_`/`volltext`/summary is deliberate
(DD-036/DD-037, DD-043, issue #49), but three fields on that row are per-Vorgang
Fundstelle metadata, not per-PDF content: `titel` (the reading differs per bill),
`link` (the `#page=N` anchor points at *that* bill's section) and `autoren`. Because
the row was shared, all three held whichever Vorgang was uploaded last — every sibling
showed a wrong reading label and deep-linked into an unrelated debate.

**Ground truth, verified against the sources and asserted below.** Plenarprotokoll
17/137 (10.12.2025) is cited by 10 Vorgänge, among them:

| Vorgang | Bill | Fundstelle | Anchor |
|---|---|---|---|
| V-244180 | Gesetz zur Änderung des Juristenausbildungsgesetzes | Erste Beratung | `#page=70` |
| V-243384 | Gesetz zur Änderung schulgesetzlicher Regelungen | Zweite Beratung | `#page=31` |

PDF page 70 names the Juristenausbildungsgesetz; page 31 is the Schulgesetz debate.
The staging row observed for V-244180 held V-243384's values — `titel: "Zweite
Beratung"` and `#page=31` — i.e. the deep link dropped the reader into the school-law
debate. Nothing here depends on staging: PARLIS and the PDF are both permanent.

The three tests form the chain the issue describes:

1. ``test_parlis_gives_each_vorgang_its_own_page_anchor`` — the source really does
   distinguish the two Fundstellen (same PDF file, different anchor, different reading).
2. ``test_pdf_windows_confirm_the_anchors`` — the anchors are meaningful: each page
   window holds its own bill's debate and not the other's.
3. ``test_two_vorgaenge_get_their_own_row_from_the_real_pdf`` — the regression test:
   enriching both Fundstellen off the real PDF yields two documents with distinct
   ``hash_`` (so the backend keeps two rows), each holding its own ``titel``/``link``,
   while the LLM is still called once for the sitting (no issue #49 regression).

Run with: pytest -m integration tests/integration/test_issue25_shared_protocol_rows.py
"""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from bawue.bawue_dok import _hash_cache, download_pdf, enrich_dokument, extract_pdf_text, normalize_volltext
from bawue.parlis_client import ParlisClient
from bawue.types import Autor, Doktyp, Dokument

pytestmark = pytest.mark.integration

PLP_17_137 = "17/137"
PLP_17_137_URL = "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP17/Plp/17_0137_10122025.pdf"
SITTING_DATE = datetime(2025, 12, 10, tzinfo=UTC)

# The two Vorgänge of that sitting used throughout, keyed by PARLIS Vorgangs-ID:
# (expected reading, expected page anchor, a word that only its own debate uses).
CASES = {
    "V-244180": ("Erste Beratung", 70, "Juristenausbildungsgesetz"),
    "V-243384": ("Zweite Beratung", 31, "Schulgesetz"),
}

# PARLIS search window that returns both Vorgänge in a single query (~11 results).
SEARCH_FROM = date(2025, 11, 1)
SEARCH_TO = date(2025, 11, 30)

_LLM_JSON = (
    '{"schlagworte": ["landtag", "plenardebatte"], '
    '"zusammenfassung": "Sitzungsbezogene Zusammenfassung des Plenarprotokolls.", '
    '"kurztitel": "Plenarprotokoll 17/137"}'
)


def _mock_llm_response():
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = _LLM_JSON
    return response


def _make_dok(titel: str, page: int) -> Dokument:
    """A Plenarprotokoll Dokument exactly as ``_build_dokumente`` builds it.

    ``titel`` is the Fundstelle's reading label and ``link`` carries that
    Fundstelle's page anchor — the two fields the shared row used to lose.
    """
    return Dokument(
        titel=titel,
        volltext="",
        hash_="",
        typ=Doktyp.REDEPROTOKOLL,
        zp_modifiziert=SITTING_DATE,
        zp_referenz=SITTING_DATE,
        link=f"{PLP_17_137_URL}#page={page}",
        autoren=[Autor(organisation="Landesregierung")],
        drucksnr=None,
    )


def test_parlis_gives_each_vorgang_its_own_page_anchor():
    """PARLIS ground truth: both Vorgänge cite one PDF at different anchors."""
    client = ParlisClient(wahlperiode=17, request_delay_s=0.5)
    results = client.search("Gesetzgebung", SEARCH_FROM, SEARCH_TO)

    fundstellen = {
        vid: f
        for r in results
        if (vid := r.get("vorgangs_id")) in CASES
        for f in r.get("fundstellen_parsed", [])
        if f.get("plenarprotokoll") == PLP_17_137
    }
    assert set(fundstellen) == set(CASES), f"PARLIS no longer returns both Vorgänge: {sorted(fundstellen)}"

    for vid, (reading, page, _keyword) in CASES.items():
        fund = fundstellen[vid]
        assert fund["station_typ"] == reading, f"{vid}: unexpected reading {fund['station_typ']!r}"
        assert fund["pdf_url"].endswith(f"#page={page}"), f"{vid}: unexpected anchor in {fund['pdf_url']}"

    # One file, two anchors, two readings — so a single row cannot represent both.
    files = {fundstellen[vid]["pdf_url"].split("#")[0] for vid in CASES}
    assert len(files) == 1, f"expected one shared protocol PDF, got {files}"
    assert len({fundstellen[vid]["pdf_url"] for vid in CASES}) == 2
    assert len({fundstellen[vid]["station_typ"] for vid in CASES}) == 2


@pytest.mark.asyncio
async def test_pdf_windows_confirm_the_anchors():
    """PDF ground truth: each anchor's window holds its own bill, not the other's."""
    windows = {}
    async with aiohttp.ClientSession() as session:
        path = await download_pdf(session, PLP_17_137_URL)
        try:
            for vid, (_reading, page, _keyword) in CASES.items():
                text, _hash = await extract_pdf_text(path, page_hint=page)
                windows[vid] = normalize_volltext(text)
        finally:
            path.unlink(missing_ok=True)

    for vid, (_reading, _page, keyword) in CASES.items():
        others = [kw for other, (_r, _p, kw) in CASES.items() if other != vid]
        assert keyword in windows[vid], f"{vid}: own topic {keyword!r} missing from its window"
        for foreign in others:
            assert foreign not in windows[vid], f"{vid}: window bleeds into the {foreign!r} debate"

    # The windows really are different text — the stored volltext is per Fundstelle.
    assert windows["V-244180"] != windows["V-243384"]


@pytest.mark.asyncio
async def test_two_vorgaenge_get_their_own_row_from_the_real_pdf():
    """Regression test for issue #25, against the real protocol PDF.

    The LLM is stubbed (this needs no ``LLM_PROVIDER_KEY``) because the defect is in
    the document identity, not the semantics — but the stub is counted, which is how
    the issue #49 guarantee is checked at the same time.
    """
    _hash_cache.clear()
    llm = MagicMock()
    llm.extract_relevant_section = AsyncMock(side_effect=AssertionError("REDEPROTOKOLL must not be narrowed"))

    try:
        async with aiohttp.ClientSession() as session:
            with patch(
                "bawue.bawue_dok.litellm.acompletion",
                new_callable=AsyncMock,
                return_value=_mock_llm_response(),
            ) as mock_llm:
                results = {
                    vid: await enrich_dokument(session, llm, _make_dok(reading, page))
                    for vid, (reading, page, _keyword) in CASES.items()
                }
    finally:
        _hash_cache.clear()

    juristen = results["V-244180"].dokument
    schulgesetz = results["V-243384"].dokument

    # Acceptance criterion 1+2: one row per Vorgang, each keeping its own Fundstelle
    # metadata. A shared hash_ is what collapsed them in the backend.
    assert juristen.hash_ != schulgesetz.hash_, "both Fundstellen still collapse into one Dokument row"
    assert juristen.titel == "Erste Beratung"
    assert juristen.link.endswith("#page=70")
    assert schulgesetz.titel == "Zweite Beratung"
    assert schulgesetz.link.endswith("#page=31")

    # Each row's volltext is its own debate window, not the other's.
    assert "Juristenausbildungsgesetz" in juristen.volltext
    assert "Juristenausbildungsgesetz" not in schulgesetz.volltext
    assert "Schulgesetz" in schulgesetz.volltext
    assert "Schulgesetz" not in juristen.volltext

    # Acceptance criterion 3: the summary is still computed once per protocol PDF —
    # the semantics cache stays keyed on the plain file digest (issue #49).
    assert mock_llm.await_count == 1, f"expected one LLM call for the sitting, got {mock_llm.await_count}"
    assert juristen.zusammenfassung == schulgesetz.zusammenfassung
    assert juristen.schlagworte == schulgesetz.schlagworte
    assert juristen.kurztitel == schulgesetz.kurztitel
