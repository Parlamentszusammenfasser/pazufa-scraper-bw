"""BaWue Vorgänge scraper: VorgangsScraper subclass for Baden-Württemberg PARLIS."""

import asyncio
import logging
import re
import time
import uuid
from datetime import date, datetime
from uuid import NAMESPACE_URL, uuid5

import aiohttp
import toml
from collector.config import CollectorConfiguration
from collector.interface import VorgangsScraper
from openapi_client.models import (
    Autor,
    Dokument,
    Gremium,
    Parlament,
    Station,
    StationDokumenteInner,
    Stationstyp,
    VgIdent,
    VgIdentTyp,
    Vorgang,
)
from openapi_client.models.doktyp import Doktyp

from bawue.enum_mapper import VORGANGSTYP_MAP, map_dokumententyp, map_stationstyp, map_vorgangstyp
from bawue.parlis_client import ParlisClient
from bawue.types import RawFundstelle, RawVorgang
from bawue.wahlperiode_check import check_for_newer_wahlperiode

logger = logging.getLogger(__name__)

def _parse_autoren(text: str) -> list[Autor]:
    """Parse a comma-separated author string into a list of Autor objects."""
    if not text or not text.strip():
        return []
    return [Autor(organisation=part.strip()) for part in text.split(",") if part.strip()]


DEFAULT_VORGANGSTYPEN: list[str] = list(VORGANGSTYP_MAP.keys())
DEFAULT_WAHLPERIODE = 17
DEFAULT_WAHLPERIODE_START = date(2021, 4, 26)  # WP 17 BW: Landtag constituted
DEFAULT_PARLIS_DELAY = 1.0


class BawueVorgaengeScraper(VorgangsScraper):
    """Scrapes legislative data from the Baden-Württemberg PARLIS system.

    Auto-discovered by the framework when placed in the scrapers directory.
    Uses synchronous requests for PARLIS (cookie-based session management)
    wrapped in the async framework contract.
    """

    def __init__(self, config: CollectorConfiguration, session: aiohttp.ClientSession) -> None:
        # Load BaWue-specific config from TOML
        bawue_config = self._load_bawue_config(config)
        self._wahlperiode = bawue_config.get("wahlperiode", DEFAULT_WAHLPERIODE)
        wp_start = bawue_config.get("wahlperiode-start-date", DEFAULT_WAHLPERIODE_START)
        self._wahlperiode_start_date = date.fromisoformat(wp_start) if isinstance(wp_start, str) else wp_start
        parlis_delay = bawue_config.get("parlis-request-delay-s", DEFAULT_PARLIS_DELAY)

        # The listing_urls are Vorgangstyp strings — the framework passes them to listing_page_extractor
        listing_urls = DEFAULT_VORGANGSTYPEN

        super().__init__(config, uuid.UUID(config.collector_id), listing_urls, session)

        self._parlis = ParlisClient(
            wahlperiode=self._wahlperiode,
            request_delay_s=parlis_delay,
            wahlperiode_start_date=self._wahlperiode_start_date,
        )

        # Local cache: vorgang_id → RawVorgang dict. Populated by listing_page_extractor,
        # consumed by item_extractor. Needed because the framework deduplicates items via a set
        # of hashable keys, but we need the full raw data for conversion.
        self._raw_cache: dict[str, RawVorgang] = {}

    @staticmethod
    def _load_bawue_config(config: CollectorConfiguration) -> dict:
        """Load [bawue] section from the collector config file."""
        config_file = getattr(config, "config_file", None)
        if config_file:
            try:
                loaded = toml.load(config_file)
                return loaded.get("bawue", {})
            except Exception:
                logger.warning("Could not load [bawue] section from config file: %s", config_file, exc_info=True)
        return {}

    async def run(self) -> None:
        check_for_newer_wahlperiode(self._wahlperiode)
        start = time.monotonic()
        try:
            await super().run()
        finally:
            duration = time.monotonic() - start
            logger.info("Completed in %.1fs", duration)

    async def listing_page_extractor(self, vorgangstyp: str) -> list[str]:
        """Search PARLIS for a given Vorgangstyp and return vorgang IDs.

        The framework calls this for each entry in self.listing_urls.
        We use the Vorgangstyp string as the "listing URL".
        """
        date_from = self._wahlperiode_start_date
        date_to = date.today()

        # PARLIS uses synchronous requests — offload to a thread to avoid blocking the event loop
        raw_vorgaenge = await asyncio.to_thread(self._parlis.search, vorgangstyp, date_from, date_to)
        logger.info("Found %d Vorgänge for type '%s'", len(raw_vorgaenge), vorgangstyp)

        vorgang_ids = []
        for raw in raw_vorgaenge:
            vid = raw.get("vorgangs_id", "")
            if vid:
                self._raw_cache[vid] = raw
                vorgang_ids.append(vid)

        return vorgang_ids

    async def item_extractor(self, vorgang_id: str) -> Vorgang | None:
        """Convert a raw PARLIS Vorgang into a framework Vorgang model.

        The framework calls this for each item returned by listing_page_extractor.
        """
        raw = self._raw_cache.pop(vorgang_id, None)
        if raw is None:
            logger.error("No raw data found for vorgang_id %s", vorgang_id)
            return None

        return self._build_vorgang(raw)

    def _build_vorgang(self, raw: RawVorgang) -> Vorgang:
        """Convert a raw PARLIS dict into a framework Vorgang model.

        Each PARLIS Vorgang contains a list of Fundstellen (references to printed documents
        or plenary sessions). These are converted to Stationen (legislative process steps).
        Stellungnahmen (position statements) are not independent steps — they attach as
        children of the preceding station, matching the BY scraper convention.
        """
        vorgang_id = raw.get("vorgangs_id", "unknown")
        titel = raw.get("titel", "")
        initiative = raw.get("Initiative", "")
        vorgangstyp_str = raw.get("Vorgangstyp", "")

        api_id = uuid5(NAMESPACE_URL, vorgang_id)
        typ = map_vorgangstyp(vorgangstyp_str)
        initiatoren = _parse_autoren(initiative)

        stationen = self._collect_stationen(raw.get("fundstellen_parsed", []), initiative, vorgang_id)
        ids = [VgIdent(id=vorgang_id, typ=VgIdentTyp.VORGNR)] if vorgang_id != "unknown" else None

        return Vorgang(
            api_id=str(api_id),
            titel=titel,
            typ=typ,
            wahlperiode=self._wahlperiode,
            verfassungsaendernd=False,
            initiatoren=initiatoren,
            stationen=stationen,
            ids=ids,
        )

    def _collect_stationen(
        self, fundstellen: list[RawFundstelle], initiative: str, vorgang_id: str
    ) -> list[Station]:
        """Build stations from parsed Fundstellen, nesting Stellungnahmen as children.

        PARLIS lists Stellungnahmen as separate Fundstellen, but they belong to the
        preceding legislative step (e.g. a committee report). If a Stellungnahme appears
        before any station, it is discarded with a warning.
        """
        stationen: list[Station] = []
        for fund in fundstellen:
            station = self._build_station(fund, initiative)

            if self._is_stellungnahme(station):
                self._attach_stellungnahme(stationen, station.dokumente, vorgang_id)
                continue

            if station.dokumente and self._try_merge_station(stationen, station):
                continue

            stationen.append(station)
        return stationen

    @staticmethod
    def _try_merge_station(stationen: list[Station], station: Station) -> bool:
        """Try to merge a station into an existing one. Returns True if merged."""
        if station.typ == Stationstyp.PARL_MINUS_AUSSCHBER:
            match = BawueVorgaengeScraper._find_matching_ausschuss(stationen, station.gremium.name)
        elif stationen and stationen[-1].typ == station.typ and stationen[-1].gremium.name == station.gremium.name:
            match = stationen[-1]
        else:
            match = None

        if match is not None:
            match.dokumente.extend(station.dokumente)
            return True
        return False

    @staticmethod
    def _find_matching_ausschuss(stationen: list[Station], gremium_name: str) -> Station | None:
        """Search backwards for a committee station with the same gremium, stopping at plenary."""
        for s in reversed(stationen):
            if s.typ == Stationstyp.PARL_MINUS_VOLLVLSGN:
                return None
            if s.typ == Stationstyp.PARL_MINUS_AUSSCHBER and s.gremium.name == gremium_name:
                return s
        return None

    @staticmethod
    def _is_stellungnahme(station: Station) -> bool:
        """Check if all documents in a station are Stellungnahmen (position statements)."""
        return bool(station.dokumente) and all(
            d.actual_instance.typ == Doktyp.STELLUNGNAHME for d in station.dokumente
        )

    @staticmethod
    def _attach_stellungnahme(
        stationen: list[Station],
        dokumente: list[StationDokumenteInner],
        vorgang_id: str,
    ) -> None:
        """Attach Stellungnahme documents to the most recent station."""
        if stationen:
            if stationen[-1].stellungnahmen is None:
                stationen[-1].stellungnahmen = []
            stationen[-1].stellungnahmen.extend(dokumente)
        else:
            logger.warning(
                "Discarding Stellungnahme without preceding station for Vorgang %s",
                vorgang_id,
            )

    def _build_station(self, fund: RawFundstelle, initiative: str) -> Station:
        """Convert a parsed Fundstelle dict into a framework Station.

        A Fundstelle is a reference line from the PARLIS search results, e.g.:
        "Gesetzentwurf  Fraktion GRÜNE  04.02.2026 Drucksache 17/10266  (13 S.)"
        It is pre-parsed into a dict by parlis_parser with fields like datum, drucksache,
        station_typ, pdf_url, ausschuss, plenarprotokoll, etc.
        """
        station_typ_str = fund.get("station_typ", "")
        station_typ = map_stationstyp(station_typ_str, initiator=initiative)
        zp_start = _parse_fundstelle_date(fund)
        gremium = self._determine_gremium(fund)
        dokumente = self._build_dokumente(fund, station_typ_str, station_typ, initiative, zp_start)

        return Station(
            typ=station_typ,
            dokumente=dokumente,
            zp_start=zp_start,
            gremium=gremium,
        )

    def _determine_gremium(self, fund: RawFundstelle) -> Gremium:
        """Determine which parliamentary body handled this Fundstelle.

        Priority: named committee (Ausschuss) > plenary session > generic "Landtag" fallback.
        """
        ausschuss = fund.get("ausschuss", "")
        if ausschuss:
            name = ausschuss
        elif fund.get("plenarprotokoll"):
            name = "Plenum"
        else:
            name = "Landtag"
        return Gremium(parlament=Parlament.BW, name=name, wahlperiode=self._wahlperiode)

    @staticmethod
    def _build_dokumente(
        fund: RawFundstelle,
        station_typ_str: str,
        station_typ: Stationstyp,
        initiative: str,
        zp_start: datetime,
    ) -> list[StationDokumenteInner]:
        """Build the document list for a station (0 or 1 documents).

        A document is only created when the Fundstelle includes a PDF link.
        Authors are taken from the Fundstelle's autor_text if available,
        otherwise fall back to the Vorgang-level initiative (who initiated the process).
        """
        pdf_url = fund.get("pdf_url", "")
        if not pdf_url:
            return []

        doc_typ = map_dokumententyp(
            station_typ_str,
            is_vorparlamentarisch=(station_typ == Stationstyp.PREPARL_MINUS_REGENT),
        )

        autor_text = fund.get("autor_text", "")
        autoren = _parse_autoren(autor_text) if autor_text else _parse_autoren(initiative)

        dok = Dokument(
            titel=station_typ_str or "Dokument",
            volltext="",
            hash="",
            typ=doc_typ,
            zp_modifiziert=zp_start,
            zp_referenz=zp_start,
            link=pdf_url,
            autoren=autoren,
            drucksnr=fund.get("drucksache"),
        )
        return [StationDokumenteInner(dok)]


# -- Date parsing helpers ----------------------------------------------------------

# Matches a 4-digit year starting with "20" (e.g. 2024, 2028).
# Used as fallback when PARLIS dates are malformed, like "00.00.2028"
# (placeholder format when only the year is known).
_YEAR_PATTERN = re.compile(r"(20\d{2})")


def _parse_fundstelle_date(fund: RawFundstelle) -> datetime:
    """Parse the date from a Fundstelle, with graceful fallbacks.

    PARLIS dates are typically DD.MM.YYYY, but can be malformed:
    - "00.00.2028" → placeholder when only the year is known → falls back to Jan 1
    - completely missing → falls back to current time
    """
    datum_str = fund.get("datum", "")
    if not datum_str:
        logger.warning(
            "No date found for Fundstelle '%s' (Drucksache: %s), using current time",
            fund.get("raw", ""),
            fund.get("drucksache", "unknown"),
        )
        return datetime.now()

    try:
        return datetime.strptime(datum_str, "%d.%m.%Y")
    except ValueError:
        return _fallback_date_from_year(datum_str, fund)


def _fallback_date_from_year(datum_str: str, fund: RawFundstelle) -> datetime:
    """Extract a year from a malformed date string, or fall back to now."""
    year_match = _YEAR_PATTERN.search(datum_str)
    if year_match:
        logger.warning(
            "Invalid date '%s' for Fundstelle '%s' (Drucksache: %s), using %s-01-01",
            datum_str,
            fund.get("raw", ""),
            fund.get("drucksache", "unknown"),
            year_match.group(),
        )
        return datetime(int(year_match.group()), 1, 1)

    logger.warning(
        "Unparseable date '%s' for Fundstelle '%s' (Drucksache: %s), using current time",
        datum_str,
        fund.get("raw", ""),
        fund.get("drucksache", "unknown"),
    )
    return datetime.now()
