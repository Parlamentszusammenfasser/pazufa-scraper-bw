"""Characterization test for issue #100: pins the Vorgänge built from the PARLIS fixtures.

https://github.com/Parlamentszusammenfasser/pazufa-scraper-bw/issues/100

Moving the station rules out of the scraper must not change a single field, including
every Station ``api_id`` and ``zp_start``. After an intended rule change, regenerate with
``UPDATE_GOLDEN=1 make test`` and review the diff of the golden file.
"""

import json
import os
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper, _fold_einzelplaene
from bawue.parlis_parser import parse_results
from tests.factories import make_scraper

_PARLIS = Path(__file__).parent.parent / "fixtures" / "parlis"
_GOLDEN = _PARLIS / "issue100" / "built_vorgaenge.json"
# Listed, not globbed: a fixture added for another issue must not change the golden file.
_RESULT_PAGES = [
    "antrag_results.html",
    "antrag_results_with_json.html",
    "gesetzgebung_backlink_real_results.html",
    "gesetzgebung_landesregierung_results.html",
    "gesetzgebung_results.html",
    "gesetzgebung_results_with_json.html",
    "gesetzgebung_shared_amendment_results.html",
    "gesetzgebung_unlabeled_erste_beratung_results.html",
    "gesetzgebung_wp18_complete_results.html",
    "gesetzgebung_wp18_young_results.html",
    "kleine_anfrage_results.html",
    "kleine_anfrage_results_with_json.html",
]
_RAW_VORGAENGE = [
    "issue26/v217223.json",
    "issue26/v237492.json",
    "issue26/v244180.json",
    "issue33/v213867.json",
    "issue33/v215974.json",
]


def _raws() -> list[dict]:
    raws = [r for name in _RESULT_PAGES for r in parse_results((_PARLIS / name).read_text())]
    raws += [json.loads((_PARLIS / name).read_text(encoding="utf-8")) for name in _RAW_VORGAENGE]
    return raws + _fold_einzelplaene(json.loads((_PARLIS / "issue60" / "haushalt_2025_26.json").read_text()))


@pytest.mark.asyncio
async def test_built_vorgaenge_match_golden(monkeypatch):
    # Fixed "today", so no Fundstelle is ever held back (DD-068).
    monkeypatch.setattr(BawueVorgaengeScraper, "_today", staticmethod(lambda: date(2026, 11, 1)))
    scraper = make_scraper(BawueVorgaengeScraper)
    scraper._wahlperiode = 17
    scraper._llm_enabled = False
    scraper._llm = None
    scraper._filter_sonstig = True
    scraper.session = MagicMock()
    scraper._client = MagicMock()

    built = [(await scraper._build_vorgang(raw)).to_dict() for raw in _raws()]
    actual = json.loads(json.dumps(built, default=str))

    if os.environ.get("UPDATE_GOLDEN"):
        _GOLDEN.parent.mkdir(exist_ok=True)
        _GOLDEN.write_text(json.dumps(actual, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    assert actual == json.loads(_GOLDEN.read_text(encoding="utf-8"))
