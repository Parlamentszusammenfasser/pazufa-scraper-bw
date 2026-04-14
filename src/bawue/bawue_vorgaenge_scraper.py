"""BaWue Vorgänge scraper: VorgangsScraper subclass for Baden-Württemberg PARLIS."""

import asyncio
import logging
import re
import time
import uuid
from datetime import UTC, date, datetime
from uuid import NAMESPACE_URL, uuid5

import aiohttp
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
    Vorgang,
)
from openapi_client.models.doktyp import Doktyp

from bawue.bawue_dok import LLMMetrics, clear_hash_cache
from bawue.config_loader import load_toml_section
from bawue.enum_mapper import map_dokumententyp, map_stationstyp, map_vorgangstyp
from bawue.log_context import reset_vorgangs_id, set_vorgangs_id
from bawue.parlis_client import ParlisClient
from bawue.rate_limiter import create_upload_limiter
from bawue.types import RawFundstelle, RawVorgang
from bawue.upload_throttle import upload_vorgang
from bawue.wahlperiode_check import check_for_newer_wahlperiode

logger = logging.getLogger(__name__)


_AUTOR_SPLIT_RE = re.compile(
    r",\s+(?=Fraktion|Ministerium|Landesregierung|Staatsministerium|Präsident|Ständiger|Abg\.)",
)


def _parse_autoren(text: str) -> list[Autor]:
    """Parse a comma-separated author string into a list of Autor objects.

    Uses lookahead splitting to avoid breaking ministry names that contain
    commas (e.g. "Ministerium für Umwelt, Klima und Energiewirtschaft").
    """
    if not text or not text.strip():
        return []
    return [Autor(organisation=part.strip()) for part in _AUTOR_SPLIT_RE.split(text) if part.strip()]


DEFAULT_ENABLED_VORGANGSTYPEN: list[str] = [
    "Gesetzgebung",
    "Haushaltsgesetzgebung",
    "Volksantrag",
]
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
        bawue_config = load_toml_section(config, "bawue")
        self._wahlperiode = bawue_config.get("wahlperiode", DEFAULT_WAHLPERIODE)
        wp_start = bawue_config.get("wahlperiode-start-date", DEFAULT_WAHLPERIODE_START)
        self._wahlperiode_start_date = date.fromisoformat(wp_start) if isinstance(wp_start, str) else wp_start
        parlis_delay = bawue_config.get("parlis-request-delay-s", DEFAULT_PARLIS_DELAY)

        # The listing_urls are Vorgangstyp strings — the framework passes them to listing_page_extractor
        listing_urls = bawue_config.get("enabled-vorgangstypen", DEFAULT_ENABLED_VORGANGSTYPEN)
        self._enabled_vorgangstypen: frozenset[str] = frozenset(listing_urls)
        self._filter_sonstig = bawue_config.get("filter-sonstig-stations", True)

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

        self._upload_limiter = create_upload_limiter()

        self._published: int = 0
        self._failed: int = 0
        self._skipped: int = 0
        self._by_type: dict[str, int] = {}

        # LLM document enrichment (optional, requires LLM_PROVIDER_KEY)
        self._llm_enabled = bool(getattr(config, "llm_provider_key", None))
        self._llm = None
        self._llm_metrics = LLMMetrics()
        llm_config = load_toml_section(config, "llm")
        self._llm_model = config.llm_model
        self._llm_truncate_tokens = int(llm_config.get("truncate-tokens", 12000))
        if self._llm_enabled:
            from collector_core import LLMConnector

            self._llm = LLMConnector(
                model=config.llm_model,
                api_key=config.llm_provider_key,
                rate_limit_max_calls=5,
                rate_limit_window_seconds=60,
            )

    async def run(self) -> None:
        check_for_newer_wahlperiode(self._wahlperiode)
        start = time.monotonic()
        try:
            await super().run()
        finally:
            duration = time.monotonic() - start
            logger.info("Completed in %.1fs", duration)
            _print_vorgaenge_summary(
                self._wahlperiode,
                self._by_type,
                self._published,
                self._skipped,
                self._failed,
                duration,
                self._llm_metrics if self._llm_enabled else None,
            )

    async def send_result(self, item: Vorgang) -> Vorgang | None:
        result = upload_vorgang(
            self.config.oapiconfig,
            self.scraper_id,
            self._upload_limiter,
            item,
            dry_run=self.config.dry_run,
            log_item=self.log_item,
        )
        if result is not None:
            self._published += 1
        else:
            self._failed += 1
        return result

    async def listing_page_extractor(self, vorgangstyp: str) -> list[str]:
        """Search PARLIS for a given Vorgangstyp and return vorgang IDs.

        The framework calls this for each entry in self.listing_urls.
        We use the Vorgangstyp string as the "listing URL".
        """
        clear_hash_cache()
        date_from = self._wahlperiode_start_date
        date_to = date.today()

        # PARLIS uses synchronous requests — offload to a thread to avoid blocking the event loop
        raw_vorgaenge = await asyncio.to_thread(self._parlis.search, vorgangstyp, date_from, date_to)
        logger.info("Found %d Vorgänge for type '%s'", len(raw_vorgaenge), vorgangstyp)

        vorgang_ids = []
        for raw in raw_vorgaenge:
            vid = raw.get("vorgangs_id", "")
            typ = raw.get("Vorgangstyp", "")
            if typ not in self._enabled_vorgangstypen:
                logger.debug("Skipping Vorgang %s with unsupported type '%s'", vid, typ)
                self._skipped += 1
                continue
            if vid:
                self._raw_cache[vid] = raw
                vorgang_ids.append(vid)

        self._by_type[vorgangstyp] = self._by_type.get(vorgangstyp, 0) + len(vorgang_ids)
        return vorgang_ids

    async def item_extractor(self, vorgang_id: str) -> Vorgang | None:
        """Convert a raw PARLIS Vorgang into a framework Vorgang model.

        The framework calls this for each item returned by listing_page_extractor.
        """
        token = set_vorgangs_id(vorgang_id)
        try:
            raw = self._raw_cache.pop(vorgang_id, None)
            if raw is None:
                logger.error("No raw data found for vorgang_id %s", vorgang_id)
                self._skipped += 1
                return None

            vorgang = await self._build_vorgang(raw)

            # Skip Vorgänge where all Fundstellen had unparseable dates → no stations.
            if not vorgang.stationen:
                logger.info(
                    "Skipping Vorgang %s ('%s'): no parseable stations",
                    vorgang_id,
                    vorgang.titel[:60],
                )
                self._skipped += 1
                return None

            # Skip non-legislative meta-entries (Bekanntmachungen, Berichtigungen, etc.)
            # that only have post-parliamentary stations and no parliamentary process.
            if all(s.typ in self._POSTPARL_TYPEN for s in vorgang.stationen):
                logger.info(
                    "Skipping Vorgang %s ('%s'): only post-parliamentary stations, not a full legislative process",
                    vorgang_id,
                    vorgang.titel[:60],
                )
                self._skipped += 1
                return None

            return vorgang
        finally:
            reset_vorgangs_id(token)

    async def _build_vorgang(self, raw: RawVorgang) -> Vorgang:
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

        # Fallback: PARLIS omits Initiative for some Vorgangstypen (e.g. Haushaltsgesetzgebung).
        # In that case, infer from the first Fundstelle's autor_text.
        if not initiative:
            fundstellen = raw.get("fundstellen_parsed", [])
            for fund in fundstellen:
                autor_text = fund.get("autor_text", "")
                if autor_text:
                    initiative = autor_text
                    break

        api_id = uuid5(NAMESPACE_URL, vorgang_id)
        typ = map_vorgangstyp(vorgangstyp_str)
        initiatoren = _parse_autoren(initiative)

        fundstellen_parsed = raw.get("fundstellen_parsed", [])
        stationen = await self._collect_stationen(fundstellen_parsed, initiative, vorgang_id)
        stationen = self._filter_post_legislative_stations(stationen, vorgang_id)  # WORKAROUND: DD-018

        if fundstellen_parsed and not stationen:
            logger.warning(
                "Vorgang %s ('%s') has %d Fundstellen but ALL stations were skipped "
                "(no parseable dates). "
                "Fundstellen: %s",
                vorgang_id,
                titel[:80],
                len(fundstellen_parsed),
                [f.get("raw", "")[:100] for f in fundstellen_parsed],
            )

        self._ensure_initiativ_after_regbsl(stationen)

        # parse rejections
        aktueller_stand = raw.get("Aktueller Stand", "")
        if aktueller_stand == "Abgelehnt":
            self._ensure_ablehnung_station(stationen, vorgang_id)

        # parse vorgangs-id
        ids = [VgIdent(id=vorgang_id, typ="vorgnr")] if vorgang_id != "unknown" else None

        return Vorgang(
            api_id=str(api_id),
            titel=titel,
            kurztitel=vorgang_id if vorgang_id != "unknown" else None,
            typ=typ,
            wahlperiode=self._wahlperiode,
            verfassungsaendernd=False,
            initiatoren=initiatoren,
            stationen=stationen,
            ids=ids,
        )

    _POSTPARL_TYPEN: frozenset[Stationstyp] = frozenset(
        {
            Stationstyp.POSTPARL_MINUS_GSBLT,
            Stationstyp.POSTPARL_MINUS_VESJA,
            Stationstyp.POSTPARL_MINUS_VESNE,
            Stationstyp.POSTPARL_MINUS_KRAFT,
        }
    )

    _AENDERUNGSANTRAG_TYPEN: frozenset[str] = frozenset(
        {
            "änderungsantrag",
            "änderungsanträge",
        }
    )
    _ENTSCHLIESSUNGSANTRAG_TYPEN: frozenset[str] = frozenset(
        {
            "entschließungsantrag",
            "entschließungsanträge",
        }
    )
    _AMBIGUOUS_ANTRAG_TYPEN: frozenset[str] = frozenset(
        {
            "antrag",
            "anträge",
        }
    )

    # WORKAROUND (Issue 1A / DD-018)
    # TODO: Remove once backend track regex supports post-enactment stations.
    def _filter_post_legislative_stations(self, stationen: list[Station], vorgang_id: str) -> list[Station]:
        """Filter parl-* stations that appear chronologically after any postparl-* station.

        PARLIS appends late Ausschussberichte (Evaluierungsklausel / Berichtspflicht)
        to already-concluded Vorgänge. The backend track regex rejects these.
        This workaround drops them until the backend track is extended.
        """
        postparl_dates = [s.zp_start for s in stationen if s.typ in self._POSTPARL_TYPEN]
        if not postparl_dates:
            return stationen

        earliest_postparl = min(postparl_dates)
        filtered: list[Station] = []
        for s in stationen:
            if s.typ and s.typ.value.startswith("parl-") and s.zp_start > earliest_postparl:
                logger.warning(
                    "WORKAROUND (DD-018): Filtering post-legislative %s station "
                    "(date: %s) after postparl station (date: %s) in %s",
                    s.typ.value,
                    s.zp_start.date(),
                    earliest_postparl.date(),
                    vorgang_id,
                )
                continue
            filtered.append(s)
        return filtered

    async def _collect_stationen(
        self, fundstellen: list[RawFundstelle], initiative: str, vorgang_id: str
    ) -> list[Station]:
        """Build stations from parsed Fundstellen, nesting Stellungnahmen as children.

        PARLIS lists Stellungnahmen as separate Fundstellen, but they belong to the
        preceding legislative step (e.g. a committee report). If a Stellungnahme appears
        before any station, it is discarded with a warning.

        Änderungsanträge are attached as documents to the nearest parl-vollvlsgn station.
        Entschließungsanträge are discarded entirely.
        """
        stationen: list[Station] = []
        pending_aenderungsantraege: list[list[StationDokumenteInner]] = []
        seen_ausschber = False
        for fund in fundstellen:
            station = await self._build_station(fund, initiative)
            if station is None:
                continue
            station_typ_str = fund.get("station_typ", "")
            typ_lower = station_typ_str.lower()

            if typ_lower in self._ENTSCHLIESSUNGSANTRAG_TYPEN:
                continue

            if typ_lower in self._AENDERUNGSANTRAG_TYPEN:
                if station.dokumente:
                    pending_aenderungsantraege.append(station.dokumente)
                continue

            # Positional heuristic (Issue 1B / DD-019): PARLIS labels
            # Änderungsanträge as plain "Antrag". After a committee report,
            # "Antrag" is always an amendment, not a new initiative.
            if (
                station.typ == Stationstyp.PARL_MINUS_INITIATIV
                and typ_lower in self._AMBIGUOUS_ANTRAG_TYPEN
                and seen_ausschber
            ):
                logger.info(
                    "Reclassifying '%s' as Änderungsantrag (after Ausschussbericht) in %s",
                    station_typ_str,
                    vorgang_id,
                )
                if station.dokumente:
                    pending_aenderungsantraege.append(station.dokumente)
                continue

            if self._is_stellungnahme(station, station_typ_str):
                self._attach_stellungnahme(stationen, station.dokumente, vorgang_id)
                continue

            if self._filter_sonstig and station.typ == Stationstyp.SONSTIG:
                logger.debug(
                    "Filtering sonstig station (Fundstelle: %s) in %s",
                    fund.get("raw", "?"),
                    vorgang_id,
                )
                continue

            if self._try_merge_station(stationen, station):
                continue

            stationen.append(station)

            if station.typ == Stationstyp.PARL_MINUS_AUSSCHBER:
                seen_ausschber = True

            # Attach any buffered Änderungsanträge to this station if it's a vollvlsgn
            if station.typ == Stationstyp.PARL_MINUS_VOLLVLSGN and pending_aenderungsantraege:
                for docs in pending_aenderungsantraege:
                    station.dokumente.extend(docs)
                pending_aenderungsantraege.clear()

        # Remaining Änderungsanträge: attach to the last vollvlsgn or warn
        if pending_aenderungsantraege:
            self._attach_pending_aenderungsantraege(stationen, pending_aenderungsantraege, vorgang_id)

        for station in stationen:
            station.dokumente = _dedup_drucks(station.dokumente)
        return stationen

    def _ensure_ablehnung_station(self, stationen: list[Station], vorgang_id: str) -> None:
        """Append a synthetic parl-ablehnung station if none exists.

        PARLIS lists acceptance outcomes (Zustimmung, Annahme, etc.) as separate
        Fundstellen, but does not list rejection (Ablehnung). The rejection is only
        recorded in the 'Aktueller Stand' metadata field. When that field says
        'Abgelehnt', this method adds the missing station.
        """
        if any(s.typ == Stationstyp.PARL_MINUS_ABLEHNUNG for s in stationen):
            return

        if not stationen:
            logger.warning(
                "Cannot synthesize ablehnung station for Vorgang %s: no existing stations",
                vorgang_id,
            )
            return

        zp_start = stationen[-1].zp_start
        stationen.append(
            Station(
                typ=Stationstyp.PARL_MINUS_ABLEHNUNG,
                dokumente=[],
                zp_start=zp_start,
                gremium=Gremium(
                    parlament=Parlament.BW,
                    wahlperiode=self._wahlperiode,
                    name="Landtag",
                ),
            )
        )
        logger.info(
            "Synthesized parl-ablehnung station for Vorgang %s (Aktueller Stand: Abgelehnt)",
            vorgang_id,
        )

    def _ensure_initiativ_after_regbsl(self, stationen: list[Station]) -> None:
        """Insert a synthetic parl-initiativ station after preparl-regbsl if missing.

        PARLIS uses a single Fundstelle "Gesetzentwurf" for government bills, which
        the scraper maps to preparl-regbsl (Kabinettsbeschluss).  However, the backend
        track definition requires a parl-initiativ station between the pre-parliamentary
        phase and the first plenary reading (parl-vollvlsgn).  The parliamentary
        introduction is implicit in PARLIS data — this method makes it explicit.
        """
        if not stationen:
            return

        # Find the last preparl-regbsl (there may be several pre-parliamentary stations)
        regbsl_idx = None
        for i, s in enumerate(stationen):
            if s.typ == Stationstyp.PREPARL_MINUS_REGBSL:
                regbsl_idx = i

        if regbsl_idx is None:
            return

        # Check if a parl-initiativ already follows
        next_idx = regbsl_idx + 1
        if next_idx < len(stationen) and stationen[next_idx].typ == Stationstyp.PARL_MINUS_INITIATIV:
            return

        # Determine the date: use the next station's date if available, else the regbsl's
        zp_start = stationen[next_idx].zp_start if next_idx < len(stationen) else stationen[regbsl_idx].zp_start

        synthetic = Station(
            typ=Stationstyp.PARL_MINUS_INITIATIV,
            dokumente=stationen[regbsl_idx].dokumente.copy(),
            zp_start=zp_start,
            gremium=Gremium(
                parlament=Parlament.BW,
                wahlperiode=self._wahlperiode,
                name="Landtag",
            ),
        )
        stationen.insert(next_idx, synthetic)

    @staticmethod
    def _attach_pending_aenderungsantraege(
        stationen: list[Station],
        pending: list[list[StationDokumenteInner]],
        vorgang_id: str,
    ) -> None:
        """Attach remaining Änderungsantrag docs to the last vollvlsgn, or warn."""
        target = None
        for s in reversed(stationen):
            if s.typ == Stationstyp.PARL_MINUS_VOLLVLSGN:
                target = s
                break
        if target is not None:
            for docs in pending:
                target.dokumente.extend(docs)
        else:
            logger.warning(
                "Discarding Änderungsanträge without vollvlsgn station for Vorgang %s",
                vorgang_id,
            )

    @staticmethod
    def _try_merge_station(stationen: list[Station], station: Station) -> bool:
        """Try to merge a station into an existing one. Returns True if merged."""
        if station.typ == Stationstyp.PARL_MINUS_AUSSCHBER:
            match = BawueVorgaengeScraper._find_matching_ausschuss(stationen, station.gremium.name)
        elif (
            stationen
            and stationen[-1].typ == station.typ
            and stationen[-1].gremium.name == station.gremium.name
            and station.typ != Stationstyp.PARL_MINUS_VOLLVLSGN
        ):
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

    _STELLUNGNAHME_STATION_TYPEN: frozenset[str] = frozenset({"stellungnahme", "antwort"})

    @staticmethod
    def _is_stellungnahme(station: Station, station_typ_str: str = "") -> bool:
        """Check if a station represents a Stellungnahme (position statement).

        Detected by either:
        - All documents having Doktyp.STELLUNGNAHME (standard case with PDF)
        - The Fundstelle type being "Stellungnahme"/"Antwort" with no documents (no PDF URL)
        """
        if station.dokumente and all(d.actual_instance.typ == Doktyp.STELLUNGNAHME for d in station.dokumente):
            return True
        return not station.dokumente and station_typ_str.lower() in BawueVorgaengeScraper._STELLUNGNAHME_STATION_TYPEN

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

    async def _build_station(self, fund: RawFundstelle, initiative: str) -> Station | None:
        """Convert a parsed Fundstelle dict into a framework Station.

        A Fundstelle is a reference line from the PARLIS search results, e.g.:
        "Gesetzentwurf  Fraktion GRÜNE  04.02.2026 Drucksache 17/10266  (13 S.)"
        It is pre-parsed into a dict by parlis_parser with fields like datum, drucksache,
        station_typ, pdf_url, ausschuss, plenarprotokoll, etc.
        """
        station_typ_str = fund.get("station_typ", "")
        raw_text = fund.get("raw", "")

        # Fallback: when the regex-based station_typ extraction fails (e.g. single-space
        # separator in the Fundstelle text), use the full raw text for enum mapping.
        # map_stationstyp does substring matching, so it can find the type in the raw text.
        mapping_text = station_typ_str or raw_text
        if not station_typ_str and raw_text:
            logger.warning(
                "Fundstelle station_typ not extracted by regex, using raw text fallback: '%s'",
                raw_text[:80],
            )

        station_typ = map_stationstyp(mapping_text, initiator=initiative)

        # Cross-check: the parser can truncate multi-word types at internal
        # double-spaces (e.g. "Beschluss des Landtags  in Zweiter Beratung"
        # → station_typ="Beschluss des Landtags", losing the "in" qualifier).
        # If the full raw text maps to a different non-SONSTIG type, prefer it.
        if station_typ_str and raw_text:
            raw_typ = map_stationstyp(raw_text, initiator=initiative)
            if raw_typ != station_typ and raw_typ != Stationstyp.SONSTIG:
                station_typ = raw_typ

        zp_start = _parse_fundstelle_date(fund)
        if zp_start is None:
            logger.error(
                "Skipping station for Fundstelle '%s' (Drucksache: %s) — no parseable date",
                fund.get("raw", ""),
                fund.get("drucksache", "unknown"),
            )
            return None

        gremium = self._determine_gremium(fund)
        dokumente, trojanergefahr = await self._build_dokumente(
            fund, station_typ_str, mapping_text, station_typ, initiative, zp_start
        )

        return Station(
            typ=station_typ,
            dokumente=dokumente,
            zp_start=zp_start,
            gremium=gremium,
            trojanergefahr=trojanergefahr,
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

    async def _build_dokumente(
        self,
        fund: RawFundstelle,
        station_typ_str: str,
        mapping_text: str,
        station_typ: Stationstyp,
        initiative: str,
        zp_start: datetime,
    ) -> tuple[list[StationDokumenteInner], int | None]:
        """Build the document list for a station (0 or 1 documents).

        A document is only created when the Fundstelle includes a PDF link.
        Authors are taken from the Fundstelle's autor_text if available,
        otherwise fall back to the Vorgang-level initiative (who initiated the process).

        When LLM is enabled, enriches the document with PDF text extraction
        and LLM-based semantic extraction (summary, keywords, scores).

        Returns (dokumente, trojanergefahr) where trojanergefahr is a Station-level
        score extracted by the LLM (or None).
        """
        pdf_url = fund.get("pdf_url", "")
        if not pdf_url:
            return [], None

        doc_typ = map_dokumententyp(
            mapping_text,
            is_vorparlamentarisch=(station_typ == Stationstyp.PREPARL_MINUS_REGBSL),
        )
        if doc_typ == Doktyp.SONSTIG and fund.get("plenarprotokoll"):
            doc_typ = Doktyp.REDEPROTOKOLL

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

        trojanergefahr = None
        if self._llm_enabled and self._llm is not None:
            try:
                from bawue.bawue_dok import enrich_dokument

                result = await enrich_dokument(
                    self.session,
                    self._llm,
                    dok,
                    model=self._llm_model,
                    max_tokens=self._llm_truncate_tokens,
                    metrics=self._llm_metrics,
                    cache=self.config.cache,
                )
                dok = result.dokument
                trojanergefahr = result.trojanergefahr
            except Exception:
                logger.warning("Document enrichment failed for %s", pdf_url)

        return [StationDokumenteInner(dok)], trojanergefahr


def _dedup_drucks(doks: list[StationDokumenteInner]) -> list[StationDokumenteInner]:
    """Remove duplicate documents with the same Drucksache number.

    Documents without a drucksnr are always kept (no dedup key).
    Ported from the BY scraper's dedup_drucks pattern.
    """
    unique: list[StationDokumenteInner] = []
    seen_drucksnr: set[str] = set()
    for d in doks:
        drucksnr = d.actual_instance.drucksnr
        if drucksnr:
            if drucksnr in seen_drucksnr:
                continue
            seen_drucksnr.add(drucksnr)
        unique.append(d)
    return unique


# -- Run summary ------------------------------------------------------------------


def _print_vorgaenge_summary(
    wahlperiode: int,
    by_type: dict[str, int],
    published: int,
    skipped: int,
    failed: int,
    duration: float,
    llm_metrics: LLMMetrics | None = None,
) -> None:
    discovered = sum(by_type.values())
    lines = [
        "=== BaWue Vorgänge Run Summary ===",
        f"Wahlperiode: {wahlperiode} | Duration: {duration:.1f}s",
        f"Discovered:  {discovered}",
        f"Published:   {published}",
        f"Skipped:     {skipped}",
        f"Failed:      {failed}",
    ]
    if by_type:
        lines.append("")
        lines.append("By type:")
        for typ, count in by_type.items():
            lines.append(f"  {typ}:  {count}")
    if llm_metrics is not None and llm_metrics.total > 0:
        lines.extend(llm_metrics.format_lines())
    print("\n".join(lines))


# -- Date parsing helpers ----------------------------------------------------------

# Matches a 4-digit year starting with "20" (e.g. 2024, 2028).
# Used as fallback when PARLIS dates are malformed, like "00.00.2028"
# (placeholder format when only the year is known).
_YEAR_PATTERN = re.compile(r"(20\d{2})")


def _parse_fundstelle_date(fund: RawFundstelle) -> datetime | None:
    """Parse the date from a Fundstelle, returning None if unfillable.

    PARLIS dates are typically DD.MM.YYYY, but can be malformed:
    - "00.00.2028" → placeholder when only the year is known → falls back to Jan 1
    - completely missing or unparseable → returns None (station must be skipped)
    """
    datum_str = fund.get("datum", "")
    if not datum_str:
        logger.error(
            "No date found for Fundstelle '%s' (Drucksache: %s)",
            fund.get("raw", ""),
            fund.get("drucksache", "unknown"),
        )
        return None

    try:
        return datetime.strptime(datum_str, "%d.%m.%Y").replace(tzinfo=UTC)
    except ValueError:
        return _fallback_date_from_year(datum_str, fund)


def _fallback_date_from_year(datum_str: str, fund: RawFundstelle) -> datetime | None:
    """Extract a year from a malformed date string, or return None if unfillable."""
    year_match = _YEAR_PATTERN.search(datum_str)
    if year_match:
        logger.warning(
            "Invalid date '%s' for Fundstelle '%s' (Drucksache: %s), using %s-01-01",
            datum_str,
            fund.get("raw", ""),
            fund.get("drucksache", "unknown"),
            year_match.group(),
        )
        return datetime(int(year_match.group()), 1, 1, tzinfo=UTC)

    logger.error(
        "Unparseable date '%s' for Fundstelle '%s' (Drucksache: %s)",
        datum_str,
        fund.get("raw", ""),
        fund.get("drucksache", "unknown"),
    )
    return None
