"""Scrapers for tests, built without __init__ (no TOML, network or Redis)."""

from unittest.mock import MagicMock

import toml

from bawue.bawue_beteiligung_scraper import BawueBeteiligungScraper
from bawue.bawue_sitzungen_scraper import BawueSitzungenScraper
from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper
from bawue.pipeline import Scraper


def mock_config(toml_text: str = "") -> MagicMock:
    """A config whose `section()` reads *toml_text*, for scraper __init__ tests."""
    config = MagicMock()
    config.collector_id = "00000000-0000-0000-0000-000000000001"
    config.llm_provider_key = None
    sections = toml.loads(toml_text)
    config.section.side_effect = lambda name: dict(sections.get(name, {}))
    return config


def make_scraper[S: Scraper](cls: type[S]) -> S:
    """*cls* with the attributes the shared pipeline reads (the former class defaults); tests set the rest."""
    scraper = object.__new__(cls)
    state = {
        "listing_urls": [],
        "scraper_id": None,
        "config": None,
        "session": None,
        "item_count": 0,
        "cached_count": 0,
        "cache_errors": 0,
        "summary": None,
    }
    if issubclass(cls, BawueVorgaengeScraper):
        state |= {
            "_emit_initdrucks_ident": True,
            # No Gesetzblatt lookup: Stationen keep the PARLIS date, nothing hits the network (DD-047).
            "_gsblt_dates": None,
            "_dropped_fundstellen": {},
            "_retry_next_cycle": set(),
            "_changed": 0,
            # No Beteiligung drafts registered: no bill looks for one (DD-079).
            "_entwuerfe": {},
            "_entwurf_shingles": {},
            "_stale_entwuerfe": [],
        }
    if issubclass(cls, (BawueBeteiligungScraper, BawueSitzungenScraper)):
        state["_beteiligungsfristen"] = True
    for name, value in state.items():
        setattr(scraper, name, value)
    return scraper
