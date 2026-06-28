"""BaWue Vorgänge scraper: VorgangsScraper subclass for Baden-Württemberg PARLIS."""

import asyncio
import logging
import re
import ssl
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import aiohttp
import certifi
from collector.config import CollectorConfiguration
from collector.interface import VorgangsScraper

from bawue.bawue_dok import LLMMetrics, clear_hash_cache
from bawue.config_loader import load_toml_section
from bawue.enum_mapper import map_dokumententyp, map_stationstyp, map_vorgangstyp
from bawue.log_context import reset_vorgangs_id, set_vorgangs_id
from bawue.notifications import send_mattermost_summary
from bawue.parlis_client import ParlisClient
from bawue.rate_limiter import create_upload_limiter
from bawue.run_report import FailedItem, format_duration, format_failed_section
from bawue.types import (
    TODO_MARKER,
    Autor,
    Doktyp,
    Dokument,
    Gremium,
    Parlament,
    RawFundstelle,
    RawVorgang,
    ReservedGremium,
    Station,
    StationDokumenteInner,
    Stationstyp,
    VgIdent,
    Vorgang,
    canonicalize_organisation,
    is_verfassungsaendernd,
    none_if_blank,
    todo_if_blank,
)
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
    Known party/government variants are mapped to their canonical form via
    ``canonicalize_organisation`` (DD-022); unknown organizations pass through
    unchanged.
    """
    if not text or not text.strip():
        return []
    return [Autor(organisation=canonicalize_organisation(part)) for part in _AUTOR_SPLIT_RE.split(text) if part.strip()]


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
        self._failed_items: list[FailedItem] = []
        self._parlis_errors: list[str] = []

        # LLM document enrichment (optional, requires LLM_PROVIDER_KEY or LLM_PROVIDER_BASE_URL)
        llm_key = getattr(config, "llm_provider_key", None)
        llm_base_url = getattr(config, "llm_provider_base_url", None)
        self._llm_enabled = bool(llm_key) or bool(llm_base_url)
        self._llm = None
        self._llm_metrics = LLMMetrics()
        llm_config = load_toml_section(config, "llm")
        self._llm_model = config.llm_model
        self._llm_truncate_tokens = int(llm_config.get("truncate-tokens", 12000))
        if self._llm_enabled:
            from pazufa_corelib.llm import LLMConnector

            self._llm = LLMConnector(
                model=config.llm_model,
                api_key=llm_key,
                api_base=llm_base_url,
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
            lines = _print_vorgaenge_summary(
                self._wahlperiode,
                self._by_type,
                self._published,
                self._skipped,
                self._failed,
                duration,
                self._llm_metrics if self._llm_enabled else None,
                self._failed_items,
                self._parlis_errors,
            )
            send_mattermost_summary(self.config, "BaWue Vorgänge Run Summary", lines)

    async def send_result(self, item: Vorgang) -> Vorgang | None:
        outcome = upload_vorgang(
            self.config.oapiconfig,
            self.scraper_id,
            self._upload_limiter,
            item,
            dry_run=self.config.dry_run,
            log_item=self.log_item,
        )
        if outcome.vorgang is not None:
            self._published += 1
        else:
            self._failed += 1
            self._failed_items.append(
                FailedItem(
                    item_id=str(item.kurztitel or item.api_id),
                    titel=item.titel,
                    reason=outcome.error or "unknown error",
                )
            )
        return outcome.vorgang

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
        self._parlis_errors.extend(self._parlis.pop_errors())
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

        self._ensure_ausschber_after_vollvlsgn(stationen)
        self._enforce_total_ordering(stationen)

        # Stable identity for document-less stations so the backend links and
        # updates them across re-runs instead of inserting duplicates (DD-028).
        _assign_stable_station_ids(stationen, vorgang_id)

        # parse vorgangs-id
        ids = [VgIdent(id=vorgang_id, typ="vorgnr")] if vorgang_id != "unknown" else None

        # Cross-referencing aid (Issue #26): also expose the Initiativdrucksache
        # so consumers can join on the originating Gesetzentwurf/Antrag number.
        initiativ_drucks = _initiativ_drucksnr(stationen)
        if initiativ_drucks:
            initdrucks_ident = VgIdent(id=initiativ_drucks, typ="initdrucks")
            ids = [initdrucks_ident] if ids is None else [*ids, initdrucks_ident]

        return Vorgang(
            api_id=str(api_id),
            titel=todo_if_blank(titel),
            kurztitel=_vorgang_kurztitel(stationen),
            typ=typ,
            wahlperiode=self._wahlperiode,
            verfassungsaendernd=is_verfassungsaendernd(titel),
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
        last_station_typ_str = ""
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

            if self._try_merge_station(stationen, station, station_typ_str, last_station_typ_str):
                continue

            stationen.append(station)
            last_station_typ_str = station_typ_str

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
        # Deterministic api_id so the backend station_merge_candidates query matches
        # this synthetic station across re-runs (it has no documents, so the only other
        # merge key — shared document hash — never matches and would insert a duplicate
        # row on every upload).
        api_id = uuid5(NAMESPACE_URL, f"bawue-synth-ablehnung-{vorgang_id}")
        stationen.append(
            Station(
                api_id=str(api_id),
                typ=Stationstyp.PARL_MINUS_ABLEHNUNG,
                dokumente=[],
                zp_start=zp_start,
                gremium=Gremium(
                    parlament=Parlament.BW,
                    wahlperiode=self._wahlperiode,
                    name=ReservedGremium.PLENUM,
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
                name=ReservedGremium.PLENUM,
            ),
        )
        stationen.insert(next_idx, synthetic)

    @staticmethod
    def _ensure_ausschber_after_vollvlsgn(stationen: list[Station]) -> None:
        """Re-time any ``parl-ausschber`` that precedes the first ``parl-vollvlsgn``.

        The BW ``gg-land-parl`` track requires the canonical ordering
        ``parl-initiativ → parl-vollvlsgn → parl-ausschber``. PARLIS dates the
        Bericht/Beschlussempfehlung Drucksache by its publication date, which
        for Haushalt Einzelpläne falls in mid-November — before the first
        plenary reading in December. The track validator then rejects the
        ausschber as out-of-order. Pin such ausschber stations to one hour
        past the first vollvlsgn so the canonical position is preserved
        without losing the station entirely.

        The list-position of the ausschber is left untouched; the backend
        sorts by ``zp_start`` before validating, so adjusting the timestamp
        is sufficient.
        """
        if not stationen:
            return

        first_vollvlsgn_idx: int | None = None
        for i, s in enumerate(stationen):
            if s.typ == Stationstyp.PARL_MINUS_VOLLVLSGN:
                first_vollvlsgn_idx = i
                break

        if first_vollvlsgn_idx is None:
            return

        anchor_zp_start = stationen[first_vollvlsgn_idx].zp_start
        bumped_zp_start = anchor_zp_start + timedelta(hours=1)

        for station in stationen[:first_vollvlsgn_idx]:
            if station.typ != Stationstyp.PARL_MINUS_AUSSCHBER:
                continue
            station.zp_start = bumped_zp_start
            if station.zp_modifiziert is not None and station.zp_modifiziert < bumped_zp_start:
                station.zp_modifiziert = bumped_zp_start

    @staticmethod
    def _enforce_total_ordering(stationen: list[Station]) -> None:
        """Ensure no two different-typed stations share the same ``zp_start``.

        The backend's track validation sorts stations by ``zp_start``; if two
        stations of different ``Stationstyp`` collide on the same value, the
        order is ambiguous and the upload is rejected. PARLIS Fundstellen carry
        only date precision (midnight UTC), so different-typed stations from
        the same day clash by default — and the synthetic ``parl-initiativ`` /
        ``parl-ablehnung`` insertions deliberately reuse a neighbor's
        ``zp_start``. Bumps each colliding station forward in 1-hour steps
        until its slot is unique to its type. Same-typed stations stay tied,
        which the backend explicitly permits (e.g. multiple Ausschussberatungen
        announced on the same date).
        """
        seen: dict[datetime, set[Stationstyp]] = {}
        for station in stationen:
            while station.zp_start in seen and seen[station.zp_start] - {station.typ}:
                bumped = station.zp_start + timedelta(hours=1)
                station.zp_start = bumped
                if station.zp_modifiziert is not None and station.zp_modifiziert < bumped:
                    station.zp_modifiziert = bumped
            seen.setdefault(station.zp_start, set()).add(station.typ)

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
    def _try_merge_station(
        stationen: list[Station],
        station: Station,
        station_typ_str: str,
        last_station_typ_str: str,
    ) -> bool:
        """Merge ``station`` into an existing entry in ``stationen`` if possible.

        Returns True iff a merge happened (caller should skip appending the new
        station). The merge rules are type-specific — see ``_find_merge_target``
        for the dispatch.
        """
        target = BawueVorgaengeScraper._find_merge_target(stationen, station, station_typ_str, last_station_typ_str)
        if target is None:
            return False
        BawueVorgaengeScraper._merge_into(target, station)
        return True

    @staticmethod
    def _find_merge_target(
        stationen: list[Station],
        station: Station,
        station_typ_str: str,
        last_station_typ_str: str,
    ) -> Station | None:
        """Locate the existing Station that ``station`` should merge into, or None.

        Dispatches on ``station.typ``:
          - PARL_MINUS_AUSSCHBER: scan backwards for same committee, stop at plenary.
          - PARL_MINUS_VOLLVLSGN: last appended station, but only if its raw PARLIS
            ``station_typ`` text describes the same reading round (DD-024).
          - Everything else: last appended station with same ``typ`` + gremium.
        """
        if station.typ == Stationstyp.PARL_MINUS_AUSSCHBER:
            return BawueVorgaengeScraper._find_matching_ausschuss(stationen, station.gremium.name)

        if not stationen:
            return None
        last = stationen[-1]
        if last.typ != station.typ or last.gremium.name != station.gremium.name:
            return None

        if station.typ == Stationstyp.PARL_MINUS_VOLLVLSGN and not _same_round_label(
            station_typ_str, last_station_typ_str
        ):
            return None

        return last

    @staticmethod
    def _merge_into(target: Station, station: Station) -> None:
        """Fold ``station`` into ``target`` in place.

        Always extends ``dokumente``. For plenary-reading consolidation, also
        widens the temporal span ``[zp_start, zp_modifiziert]`` to cover the new
        fundstelle's date (DD-024 — supports multi-day reading rounds like the
        Staatshaushaltsgesetz Einzelplan debates).
        """
        target.dokumente.extend(station.dokumente)
        if target.typ == Stationstyp.PARL_MINUS_VOLLVLSGN:
            _widen_span(target, station.zp_start)

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

        gremium = self._determine_gremium(fund, station_typ)
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

    def _determine_gremium(self, fund: RawFundstelle, station_typ: Stationstyp) -> Gremium:
        """Determine which parliamentary body handled this Fundstelle (DD-021).

        Priority:
          1. Named committee (Ausschuss) → use the specific committee name.
          2. postparl-gsblt stations → reserved name `gesetzesblatt`
             (wiki + BY-scraper convention, see DD-021).
          3. Everything else → reserved name `plenum`, which the DoD defines
             as the default "wenn etwas 'irgendwie passiert'".
        """
        ausschuss = fund.get("ausschuss", "")
        if ausschuss:
            name: str = ausschuss
        elif station_typ == Stationstyp.POSTPARL_MINUS_GSBLT:
            name = ReservedGremium.GESETZESBLATT
        else:
            name = ReservedGremium.PLENUM
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
            # PARLIS occasionally omits the document link for very recent
            # Drucksachen (empty pdf_url in the WMV35 field). Reconstruct it from
            # the Drucksache number and verify it resolves before using it.
            pdf_url = await self._fallback_pdf_url(fund.get("drucksache"))
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

        # volltext + hash carry the TODO marker until LLM enrichment fills
        # them with extracted PDF text and a content hash. The new backend
        # rejects empty strings; without LLM, the placeholder remains.
        dok = Dokument(
            titel=station_typ_str or "Dokument",
            volltext=TODO_MARKER,
            hash=TODO_MARKER,
            typ=doc_typ,
            zp_modifiziert=zp_start,
            zp_referenz=zp_start,
            link=pdf_url,
            autoren=autoren,
            drucksnr=none_if_blank(fund.get("drucksache")),
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

    async def _fallback_pdf_url(self, drucksache: str | None) -> str | None:
        """Reconstruct and verify a Landtag-BW PDF URL when PARLIS omits it.

        Returns the deterministic URL only if it actually resolves (HTTP 200
        after the website's 303 redirect to the blob store); otherwise None, so
        we never attach a broken link.
        """
        candidate = _construct_drucksache_pdf_url(drucksache)
        if candidate is None:
            return None
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        try:
            async with self.session.head(
                candidate,
                ssl=ssl_ctx,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    logger.info("Reconstructed missing PDF URL for Drucksache %s: %s", drucksache, candidate)
                    return candidate
                logger.warning(
                    "Reconstructed PDF URL for Drucksache %s did not resolve (HTTP %d): %s",
                    drucksache,
                    resp.status,
                    candidate,
                )
        except Exception:
            logger.warning("Verification of reconstructed PDF URL failed for Drucksache %s: %s", drucksache, candidate)
        return None


_LANDTAG_PDF_BASE = "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente"
_DRUCKSACHE_RE = re.compile(r"(\d+)/(\d+)")


def _construct_drucksache_pdf_url(drucksache: str | None) -> str | None:
    """Build the deterministic Landtag-BW PDF URL for a ``WP/Nummer`` Drucksache.

    The public website hosts every Drucksache at a predictable path that
    303-redirects to the actual blob store, e.g. Drucksache ``18/75`` →
    ``…/dokumente/WP18/Drucksachen/0000/18_0075.pdf``. The directory is the
    thousand-block of the Drucksache number, and the number is zero-padded to
    four digits. Returns None if the Drucksache is missing or malformed.
    """
    if not drucksache:
        return None
    match = _DRUCKSACHE_RE.fullmatch(drucksache.strip())
    if match is None:
        return None
    wp, num = int(match.group(1)), int(match.group(2))
    block = (num // 1000) * 1000
    return f"{_LANDTAG_PDF_BASE}/WP{wp}/Drucksachen/{block:04d}/{wp}_{num:04d}.pdf"


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


# Station types whose document carries the Initiativdrucksache: the parliamentary
# initiative (Gesetzentwurf/Antrag/Anfrage einer Fraktion) and the government bill
# (Gesetzentwurf der Landesregierung, mapped to preparl-regbsl).
_INITIATIV_TYPEN: frozenset[Stationstyp] = frozenset(
    {
        Stationstyp.PARL_MINUS_INITIATIV,
        Stationstyp.PREPARL_MINUS_REGBSL,
    }
)


def _initiativ_drucksnr(stationen: list[Station]) -> str | None:
    """Return the Drucksache number of the initiating document, if available.

    The Initiativdrucksache is the Drucksache under which the originating
    Gesetzentwurf/Antrag was published. It lives on the first initiative-type
    station that carries a document with a Drucksache number. The synthetic
    parl-initiativ inserted after a government Gesetzentwurf has no document, so
    the preparl-regbsl Gesetzentwurf is matched in that case.
    """
    for station in stationen:
        if station.typ not in _INITIATIV_TYPEN:
            continue
        for dok in station.dokumente:
            drucksnr = dok.actual_instance.drucksnr
            if drucksnr:
                return drucksnr
    return None


def _vorgang_kurztitel(stationen: list[Station]) -> str | None:
    """Return a human-readable Kurztitel for the Vorgang (Issue #25).

    Reuses the LLM-generated, plain-language ``kurztitel`` of the initiating
    Gesetzentwurf/Antrag, which best summarises the whole process. Falls back to
    the first document that carries a kurztitel, and finally to None when LLM
    enrichment is disabled (so the official title remains the only title).
    """
    for typen in (_INITIATIV_TYPEN, None):
        for station in stationen:
            if typen is not None and station.typ not in typen:
                continue
            for dok in station.dokumente:
                kurztitel = dok.actual_instance.kurztitel
                if kurztitel:
                    return kurztitel
    return None


def _assign_stable_station_ids(stationen: list[Station], vorgang_id: str) -> None:
    """Give document-less stations a deterministic api_id (DD-028).

    The backend matches a station against an existing one on re-upload by its
    api_id or by a shared document hash (the ``station_merge_candidates`` query,
    see DD-010). A station with no documents has neither key, so on every re-run
    the backend cannot recognise it and inserts a duplicate — which then breaks
    the Vorgang track (e.g. two ``parl-initiativ`` stations form an invalid
    ``II`` sequence). Deriving a stable api_id from (vorgang_id, typ, zp_start)
    lets the backend link and update the existing row instead.

    Document-bearing stations keep relying on their document hash, and stations
    that already carry an api_id (the synthetic ablehnung, DD-010) are left
    untouched.
    """
    if vorgang_id == "unknown":
        return
    for station in stationen:
        if station.api_id is not None or station.dokumente:
            continue
        key = f"bawue-station-{vorgang_id}-{station.typ.value}-{station.zp_start.isoformat()}"
        station.api_id = str(uuid5(NAMESPACE_URL, key))


_READING_ROUND_STEMS: dict[str, int] = {
    "erst": 1,
    "zweit": 2,
    "dritt": 3,
    "viert": 4,
    "fünft": 5,
}


def _reading_round(label: str) -> int | None:
    """Return the parliamentary reading-round ordinal for round-bearing labels.

    Recognises ``"<Ordinal>e Beratung"`` (e.g. ``"Zweite Beratung"``) and the
    drucksache-level vote variant ``"Beschluss des Landtags in <Ordinal>er
    Beratung"``. Returns ``None`` for labels without ``"Beratung"`` (e.g.
    ``"Überweisung"``, ``"Schlussabstimmung"``) so they fall back to exact-text
    equality in :func:`_same_round_label` (DD-026).
    """
    norm = label.strip().casefold()
    if "beratung" not in norm:
        return None
    for stem, n in _READING_ROUND_STEMS.items():
        if stem in norm:
            return n
    return None


def _same_round_label(a: str, b: str) -> bool:
    """Two PARLIS ``station_typ`` labels refer to the same reading round (DD-024, DD-026).

    Comparison is case-insensitive and whitespace-trimmed. An empty label on
    either side is *not* a reliable round signal and returns False — this is
    the defensive default that keeps stations separate when the parser could
    not extract a label.

    DD-026 extension: labels of the form ``"Beschluss des Landtags in <Ordinal>er
    Beratung"`` are treated as the same reading round as ``"<Ordinal>e
    Beratung"`` (debate transcript and formal vote of the same plenary
    reading), so they merge into one ``parl-vollvlsgn`` station.
    """
    norm_a = a.strip().casefold()
    norm_b = b.strip().casefold()
    if not norm_a or not norm_b:
        return False
    if norm_a == norm_b:
        return True
    round_a = _reading_round(norm_a)
    round_b = _reading_round(norm_b)
    return round_a is not None and round_a == round_b


def _widen_span(station: Station, new_date: datetime) -> None:
    """Extend ``station``'s ``[zp_start, zp_modifiziert]`` range to include ``new_date``.

    Used when consolidating multi-day reading rounds into a single Station
    (DD-024). ``zp_modifiziert`` is set only when ``new_date`` extends the
    range strictly past the current end; it stays ``None`` for same-day merges.
    """
    if new_date < station.zp_start:
        station.zp_start = new_date
    current_end = station.zp_modifiziert or station.zp_start
    if new_date > current_end:
        station.zp_modifiziert = new_date


# -- Run summary ------------------------------------------------------------------


def _print_vorgaenge_summary(
    wahlperiode: int,
    by_type: dict[str, int],
    published: int,
    skipped: int,
    failed: int,
    duration: float,
    llm_metrics: LLMMetrics | None = None,
    failed_items: list[FailedItem] | None = None,
    parlis_errors: list[str] | None = None,
) -> list[str]:
    discovered = sum(by_type.values())
    lines = [
        f"Wahlperiode: {wahlperiode} | Duration: {format_duration(duration)}",
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
    if failed_items:
        lines.extend(format_failed_section(failed_items, header="Failed Vorgänge"))
    if parlis_errors:
        lines.append("")
        lines.append(f":warning: PARLIS errors ({len(parlis_errors)}):")
        for err in parlis_errors:
            lines.append(f"  - {err}")
    print("=== BaWue Vorgänge Run Summary ===\n" + "\n".join(lines))
    return lines


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
