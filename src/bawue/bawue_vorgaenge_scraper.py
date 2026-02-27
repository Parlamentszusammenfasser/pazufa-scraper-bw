"""BaWue Vorgänge scraper: VorgangsScraper subclass for Baden-Württemberg PARLIS."""

import asyncio
import logging
import re
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

from bawue.enum_mapper import VORGANGSTYP_MAP, map_dokumententyp, map_stationstyp, map_vorgangstyp
from bawue.parlis_client import ParlisClient
from bawue.types import RawFundstelle, RawVorgang

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
        """Convert a raw PARLIS dict into a framework Vorgang model."""
        vorgang_id = raw.get("vorgangs_id", "unknown")
        titel = raw.get("titel", "")
        initiative = raw.get("Initiative", "")
        vorgangstyp_str = raw.get("Vorgangstyp", "")

        api_id = uuid5(NAMESPACE_URL, vorgang_id)
        typ = map_vorgangstyp(vorgangstyp_str)
        initiatoren = _parse_autoren(initiative)

        stationen = []
        for fund in raw.get("fundstellen_parsed", []):
            station = self._build_station(fund, initiative)
            stationen.append(station)

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

    def _build_station(self, fund: RawFundstelle, initiative: str) -> Station:
        """Convert a parsed Fundstelle dict into a framework Station."""
        station_typ_str = fund.get("station_typ", "")
        station_typ = map_stationstyp(station_typ_str, initiator=initiative)

        # Parse date
        datum_str = fund.get("datum", "")
        if datum_str:
            try:
                zp_start = datetime.strptime(datum_str, "%d.%m.%Y")
            except ValueError:
                year_match = re.search(r"(20\d{2})", datum_str)
                if year_match:
                    zp_start = datetime(int(year_match.group()), 1, 1)
                    logger.warning(
                        "Invalid date '%s' for Fundstelle '%s' (Drucksache: %s), using %s-01-01",
                        datum_str,
                        fund.get("raw", ""),
                        fund.get("drucksache", "unknown"),
                        year_match.group(),
                    )
                else:
                    zp_start = datetime.now()
                    logger.warning(
                        "Unparseable date '%s' for Fundstelle '%s' (Drucksache: %s), using current time",
                        datum_str,
                        fund.get("raw", ""),
                        fund.get("drucksache", "unknown"),
                    )
        else:
            zp_start = datetime.now()
            logger.warning(
                "No date found for Fundstelle '%s' (Drucksache: %s), using current time",
                fund.get("raw", ""),
                fund.get("drucksache", "unknown"),
            )

        # Determine gremium
        ausschuss = fund.get("ausschuss", "")
        if ausschuss:
            gremium = Gremium(parlament=Parlament.BW, name=ausschuss, wahlperiode=self._wahlperiode)
        elif fund.get("plenarprotokoll"):
            gremium = Gremium(parlament=Parlament.BW, name="Plenum", wahlperiode=self._wahlperiode)
        else:
            gremium = Gremium(parlament=Parlament.BW, name="Landtag", wahlperiode=self._wahlperiode)

        # Build documents
        dokumente: list[StationDokumenteInner] = []
        pdf_url = fund.get("pdf_url", "")
        if pdf_url:
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
            dokumente.append(StationDokumenteInner(dok))

        return Station(
            typ=station_typ,
            dokumente=dokumente,
            zp_start=zp_start,
            gremium=gremium,
        )
