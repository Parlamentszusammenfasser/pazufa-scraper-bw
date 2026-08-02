"""BaWue Vorgänge scraper: VorgangsScraper subclass for Baden-Württemberg PARLIS."""

import asyncio
import logging
import re
import ssl
import time
import uuid
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

import aiohttp
import certifi

from bawue.api import build_client
from bawue.bawue_dok import LLMMetrics, clear_hash_cache
from bawue.config import BawueConfig
from bawue.config_loader import load_toml_section
from bawue.enum_mapper import map_dokumententyp, map_stationstyp, map_vorgangstyp
from bawue.gesetzblatt_client import GesetzblattClient
from bawue.gesetzblatt_lookup import GesetzblattDateLookup
from bawue.log_context import get_vorgangs_id, reset_vorgangs_id, set_vorgangs_id
from bawue.notifications import send_mattermost_summary
from bawue.parlis_client import ParlisClient
from bawue.pipeline import VorgangsScraper
from bawue.rate_limiter import create_upload_limiter
from bawue.run_report import FailedItem, format_duration, format_failed_section
from bawue.types import (
    TODO_MARKER,
    UNSET,
    Autor,
    Doktyp,
    Dokument,
    Gremium,
    Parlament,
    RawFundstelle,
    RawVorgang,
    ReservedGremium,
    Station,
    Stationstyp,
    Unset,
    VgIdent,
    Vorgang,
    canonicalize_organisation,
    is_verfassungsaendernd,
    none_if_blank,
    placeholder_hash,
    todo_if_blank,
)
from bawue.upload_throttle import upload_vorgang
from bawue.wahlperiode_check import check_for_newer_wahlperiode

logger = logging.getLogger(__name__)


def _deepcopy_preserving_unset[T](obj: T) -> T:
    """``deepcopy`` a corelib model tree without cloning the ``UNSET`` sentinel.

    Corelib's generated ``to_dict`` omits optional fields via an identity check
    (``if value is not UNSET``). A plain ``copy.deepcopy`` mints a *fresh* ``Unset``
    instance for every such field, which then fails that check and leaks into the
    payload as a raw, non-JSON-serializable ``Unset`` (72% of WP17 uploads failed
    with "Object of type Unset is not JSON serializable"). Seeding the memo maps
    the singleton to itself, so every reference to it copies back to the singleton.
    """
    return deepcopy(obj, {id(UNSET): UNSET})


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
DEFAULT_GSBLT_DELAY = 1.0

# Same-committee `parl-ausschber` fundstellen belonging to one deliberation round
# (e.g. a Beschlussempfehlung plus its Bericht) fall days apart and are merged into
# a single station. Two Beschlussempfehlungen months apart are *distinct*
# deliberations, however, and merging them would silently drop the later date
# (`_merge_into` keeps the earlier `zp_start`). Cap the merge window so far-apart
# committee reports stay separate records (issue #54, DD-039).
_AUSSCHBER_MERGE_MAX_GAP = timedelta(days=60)


class BawueVorgaengeScraper(VorgangsScraper):
    """Scrapes legislative data from the Baden-Württemberg PARLIS system.

    Auto-discovered by the framework when placed in the scrapers directory.
    Uses synchronous requests for PARLIS (cookie-based session management)
    wrapped in the async framework contract.
    """

    # Emit the Initiativdrucksache as a cross-reference `vg_ident` (Issue #26).
    # Default off as a backend workaround (DD-041): the backend merges unrelated
    # Vorgänge that share this many-to-one ident. Class-level default so tests and
    # other manual-construction paths inherit the safe value without setting it.
    # see backend issue: https://codeberg.org/PaZuFa/pazufa-backend/issues/150
    _emit_initdrucks_ident: bool = False

    # Resolves a Gesetzblatt citation to its real Ausgabedatum (DD-047). Class-level
    # default of None so manual-construction paths (dry_run, unit tests) simply keep
    # the PARLIS date instead of reaching for the network.
    _gsblt_dates: "GesetzblattDateLookup | None" = None

    def __init__(self, config: BawueConfig, session: aiohttp.ClientSession) -> None:
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
        # DD-041 backend workaround toggle: keep the Initiativdrucksache off the
        # `ids` until the backend stops merging on many-to-one `vg_ident`s.
        self._emit_initdrucks_ident = bawue_config.get("emit-initdrucks-ident", False)

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
        self._client = build_client(config.database_url, config.api_key)

        # DD-047: PARLIS dates a Gesetzblatt Fundstelle by the Ausfertigung, so the
        # postparl-gsblt station needs the Ausgabedatum from the Gesetzblatt itself.
        gsblt_config = load_toml_section(config, "gesetzblatt")
        self._gsblt_dates = GesetzblattDateLookup(
            GesetzblattClient(request_delay_s=gsblt_config.get("request-delay-s", DEFAULT_GSBLT_DELAY))
        )

        # vorgnrs whose enrichment hit a not-yet-published PDF this cycle
        # (issue #66) — excluded from caching so the next cycle retries.
        self._pending_pdf_downloads: set[str] = set()

        self._published: int = 0
        self._failed: int = 0
        self._skipped: int = 0
        self._by_type: dict[str, int] = {}
        self._failed_items: list[FailedItem] = []
        self._parlis_errors: list[str] = []

        # LLM document enrichment (optional, requires LLM_PROVIDER_KEY)
        llm_key = getattr(config, "llm_provider_key", None)
        self._llm_enabled = bool(llm_key)
        self._llm = None
        self._llm_metrics = LLMMetrics()
        self._llm_model = config.llm_model
        if self._llm_enabled:
            from pazufa_corelib.llm import LLMConnector

            self._llm = LLMConnector(
                model=config.llm_model,
                api_key=llm_key,
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
            self._client,
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

    async def store_extracted_result(self, item_key: str, result: Vorgang) -> None:
        """Cache the uploaded Vorgang — unless a PDF download failed (issue #66).

        Plenarprotokolle are published weeks after the session; a Vorgang cached
        with a still-missing PDF would keep its TODO volltext until the PARLIS
        record changes — for a closed (e.g. abgelehnt) Vorgang that is never.
        Skipping the cache makes the next cycle retry the download.
        """
        vorgnr = next((i.id for i in result.ids or [] if i.typ == "vorgnr"), None)
        if vorgnr is not None and vorgnr in self._pending_pdf_downloads:
            self._pending_pdf_downloads.discard(vorgnr)
            logger.info(
                "Not caching Vorgang %s: a document PDF is not published yet; retrying next cycle",
                vorgnr,
            )
            return
        await super().store_extracted_result(item_key, result)

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
        # The initiating Drucksache (Gesetzentwurf) anchors section extraction for
        # shared plenary protocols; derive it up front so it is available during
        # station building, when enrichment runs (issue #35).
        initiativ_vnr = _initiativ_drucksnr_from_fundstellen(fundstellen_parsed, initiative)
        stationen = await self._collect_stationen(fundstellen_parsed, initiative, vorgang_id, titel, initiativ_vnr)

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

        self._ensure_initiativ_after_regbsl(stationen, vorgang_id)

        # Retime any out-of-order ausschber before anchoring the synthetic
        # ablehnung station on the chronological last station, so the ablehnung
        # doesn't get anchored on a stale (pre-retiming) ausschber timestamp.
        self._ensure_ausschber_after_vollvlsgn(stationen)

        # parse rejections
        aktueller_stand = raw.get("Aktueller Stand", "")
        if aktueller_stand == "Abgelehnt":
            self._ensure_ablehnung_station(stationen, vorgang_id)

        self._enforce_total_ordering(stationen)

        # Stable identity for document-less stations so the backend links and
        # updates them across re-runs instead of inserting duplicates (DD-028).
        _assign_stable_station_ids(stationen, vorgang_id)

        # parse vorgangs-id
        ids = [VgIdent(id=vorgang_id, typ="vorgnr")] if vorgang_id != "unknown" else None

        # Cross-referencing aid (Issue #26): expose the Initiativdrucksache so
        # consumers can join on the originating Gesetzentwurf/Antrag number.
        # OFF by default (DD-041): the backend's `vorgang_merge_candidates` treats
        # any single shared `vg_ident` as proof that two Vorgänge are the same
        # process, and the Initiativdrucksache is many-to-one (every
        # Haushalt-Einzelplan cites the same Staatshaushaltsgesetz), so emitting
        # it merges unrelated Vorgänge → HTTP 500 `rel_station_dokument_pkey` /
        # silent title corruption. Re-enable via `emit-initdrucks-ident = true`
        # once the backend matches only on 1:1 identifiers.
        if self._emit_initdrucks_ident:
            initiativ_drucks = _initiativ_drucksnr(stationen)
            if initiativ_drucks:
                initdrucks_ident = VgIdent(id=initiativ_drucks, typ="initdrucks")
                ids = [initdrucks_ident] if ids is None else [*ids, initdrucks_ident]

        # Issue #31: forward the parsed PARLIS Vorgang URL as a backlink so
        # consumers can trace the entry back to its source in PARLIS.
        detail_url = raw.get("detail_url")

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
            links=[detail_url] if detail_url else UNSET,
        )

    _POSTPARL_TYPEN: frozenset[Stationstyp] = frozenset(
        {
            Stationstyp.POSTPARL_GSBLT,
            Stationstyp.POSTPARL_VESJA,
            Stationstyp.POSTPARL_VESNE,
            Stationstyp.POSTPARL_KRAFT,
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
        self,
        fundstellen: list[RawFundstelle],
        initiative: str,
        vorgang_id: str,
        vorgang_titel: str = "",
        vorgang_vnr: str | None = None,
    ) -> list[Station]:
        """Build stations from parsed Fundstellen, nesting Stellungnahmen as children.

        PARLIS lists Stellungnahmen as separate Fundstellen, but they belong to the
        preceding legislative step (e.g. a committee report). If a Stellungnahme appears
        before any station, it is discarded with a warning.

        Änderungsanträge are attached as documents to the nearest parl-vollvlsgn station.
        Entschließungsanträge are discarded entirely.
        """
        stationen: list[Station] = []
        pending_aenderungsantraege: list[list[Dokument]] = []
        seen_ausschber = False
        seen_vollvlsgn = False
        last_station_typ_str = ""
        for fund in fundstellen:
            station = await self._build_station(fund, initiative, vorgang_titel, vorgang_vnr)
            if station is None:
                continue
            station_typ_str = fund.get("station_typ", "")
            typ_lower = station_typ_str.lower()

            # Positional heuristic: PARLIS sometimes omits the leading station-type
            # label on a plenary reading, leaving a bare "Plenarprotokoll WP/Nr
            # DD.MM.YYYY S. X-Y" Fundstelle that the regex-based station_typ
            # extraction can't label, so map_stationstyp falls through to SONSTIG
            # and it gets silently dropped by the filter below. If it's the first
            # such unlabeled Plenarprotokoll Fundstelle (no reading recorded yet),
            # it is that reading (typically Erste Beratung) — reclassify instead
            # of discarding it.
            if (
                station.typ == Stationstyp.SONSTIG
                and not station_typ_str
                and fund.get("plenarprotokoll")
                and not seen_vollvlsgn
            ):
                logger.info(
                    "Reclassifying unlabeled Plenarprotokoll Fundstelle as parl-vollvlsgn (first reading) in %s: '%s'",
                    vorgang_id,
                    fund.get("raw", "?"),
                )
                station.typ = Stationstyp.PARL_VOLLVLSGN

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
                station.typ == Stationstyp.PARL_INITIATIV
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

            if station.typ == Stationstyp.PARL_AUSSCHBER:
                seen_ausschber = True

            if station.typ == Stationstyp.PARL_VOLLVLSGN:
                seen_vollvlsgn = True

            # Attach any buffered Änderungsanträge to this station if it's a vollvlsgn
            if station.typ == Stationstyp.PARL_VOLLVLSGN and pending_aenderungsantraege:
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
        if any(s.typ == Stationstyp.PARL_ABLEHNUNG for s in stationen):
            return

        if not stationen:
            logger.warning(
                "Cannot synthesize ablehnung station for Vorgang %s: no existing stations",
                vorgang_id,
            )
            return

        # Anchor on the chronologically last station's zp_start, not the last one
        # by list position: Fundstellen arrive in PARLIS listing order, which can
        # place e.g. an ausschber ahead of a vollvlsgn in the list even after
        # _ensure_ausschber_after_vollvlsgn has re-timed it to a later timestamp.
        zp_start = max(s.zp_start for s in stationen)
        # Deterministic api_id so the backend station_merge_candidates query matches
        # this synthetic station across re-runs (it has no documents, so the only other
        # merge key — shared document hash — never matches and would insert a duplicate
        # row on every upload).
        api_id = uuid5(NAMESPACE_URL, f"bawue-synth-ablehnung-{vorgang_id}")
        stationen.append(
            Station(
                api_id=str(api_id),
                typ=Stationstyp.PARL_ABLEHNUNG,
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

    def _ensure_initiativ_after_regbsl(self, stationen: list[Station], vorgang_id: str = "unknown") -> None:
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
            if s.typ == Stationstyp.PREPARL_REGBSL:
                regbsl_idx = i

        if regbsl_idx is None:
            return

        # Check if a parl-initiativ already follows
        next_idx = regbsl_idx + 1
        if next_idx < len(stationen) and stationen[next_idx].typ == Stationstyp.PARL_INITIATIV:
            return

        # Date the introduction from the earliest station following the regbsl by
        # zp_start, not the list-next one: PARLIS list order is not chronological
        # (issue #48), so the neighbouring Fundstelle can be a later-dated reading
        # that would push the synthetic parl-initiativ *after* an earlier first
        # reading. Anchoring on the earliest post-regbsl zp_start keeps it at or
        # before the first plenary reading. (Dating it exactly on the first
        # reading instead would collide, and _enforce_total_ordering — which only
        # bumps forward — would leapfrog the reading past the retimed ausschber.)
        # Fall back to the list-next / regbsl date when nothing follows.
        regbsl_zp = stationen[regbsl_idx].zp_start
        later_starts = [s.zp_start for i, s in enumerate(stationen) if i != regbsl_idx and s.zp_start > regbsl_zp]
        if later_starts:
            zp_start = min(later_starts)
        elif next_idx < len(stationen):
            zp_start = stationen[next_idx].zp_start
        else:
            zp_start = regbsl_zp

        synthetic = Station(
            typ=Stationstyp.PARL_INITIATIV,
            # Date-independent identity (DD-046). `zp_start` above is derived from
            # whichever stations currently follow the regbsl, so it moves as soon as
            # a later scrape picks up the first reading (V-247045: 30.06. → 23.07.).
            # The DD-028 key includes zp_start, so the api_id would move with it, the
            # backend could not match the persisted row and would keep both — an
            # invalid duplicate that fails track validation on every later upload.
            # There is at most one synthetic parl-initiativ per Vorgang, so the
            # Vorgang id alone scopes it (same approach as the ablehnung, DD-010).
            api_id=_synthetic_initiativ_api_id(vorgang_id),
            # Deep-copy so the synthetic parl-initiativ owns its documents rather
            # than aliasing the regbsl's Dokument objects (issue #47). A shared
            # object would let per-station mutation (e.g. the stable api_id keyed
            # off document links) leak between the two stations. Preserve the UNSET
            # singleton across the copy so optional fields stay JSON-serializable.
            dokumente=_deepcopy_preserving_unset(stationen[regbsl_idx].dokumente),
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

        Both the anchor (the first reading) and the "out of order" test key off
        ``zp_start``, not list position: PARLIS list order is not chronological,
        so the offending ausschber can appear *after* the reading in the list
        (issue #48). The list-position of the ausschber is left untouched; the
        backend sorts by ``zp_start`` before validating, so adjusting the
        timestamp is sufficient. Must run before ``_ensure_ablehnung_station``,
        which anchors on the chronologically last ``zp_start`` and would
        otherwise anchor on this ausschber's stale, pre-retiming timestamp.
        """
        vollvlsgn_starts = [s.zp_start for s in stationen if s.typ == Stationstyp.PARL_VOLLVLSGN]
        if not vollvlsgn_starts:
            return

        anchor_zp_start = min(vollvlsgn_starts)
        bumped_zp_start = anchor_zp_start + timedelta(hours=1)

        for station in stationen:
            if station.typ != Stationstyp.PARL_AUSSCHBER:
                continue
            if station.zp_start > anchor_zp_start:
                continue
            station.zp_start = bumped_zp_start
            if isinstance(station.zp_modifiziert, datetime) and station.zp_modifiziert < bumped_zp_start:
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
                if isinstance(station.zp_modifiziert, datetime) and station.zp_modifiziert < bumped:
                    station.zp_modifiziert = bumped
            seen.setdefault(station.zp_start, set()).add(station.typ)

    @staticmethod
    def _attach_pending_aenderungsantraege(
        stationen: list[Station],
        pending: list[list[Dokument]],
        vorgang_id: str,
    ) -> None:
        """Attach remaining Änderungsantrag docs to the last vollvlsgn, or warn."""
        target = None
        for s in reversed(stationen):
            if s.typ == Stationstyp.PARL_VOLLVLSGN:
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
          - PARL_AUSSCHBER: scan backwards for same committee, stop at plenary.
          - PARL_VOLLVLSGN: last appended station, but only if its raw PARLIS
            ``station_typ`` text describes the same reading round (DD-024).
          - Everything else: last appended station with same ``typ`` + gremium.
        """
        if station.typ == Stationstyp.PARL_AUSSCHBER:
            return BawueVorgaengeScraper._find_matching_ausschuss(stationen, station.gremium.name, station.zp_start)

        if not stationen:
            return None
        last = stationen[-1]
        if last.typ != station.typ or last.gremium.name != station.gremium.name:
            return None

        if station.typ == Stationstyp.PARL_VOLLVLSGN and not _same_round_label(station_typ_str, last_station_typ_str):
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
        if target.typ == Stationstyp.PARL_VOLLVLSGN:
            _widen_span(target, station.zp_start)

    @staticmethod
    def _find_matching_ausschuss(stationen: list[Station], gremium_name: str, zp_start: datetime) -> Station | None:
        """Search backwards for a committee station with the same gremium, stopping at plenary.

        A candidate more than ``_AUSSCHBER_MERGE_MAX_GAP`` away from ``zp_start`` is a
        distinct deliberation, not a continuation of the same one, so it is not merged
        (issue #54 — merging would drop the later Beschlussempfehlung's date).
        """
        for s in reversed(stationen):
            if s.typ == Stationstyp.PARL_VOLLVLSGN:
                return None
            if s.typ == Stationstyp.PARL_AUSSCHBER and s.gremium.name == gremium_name:
                if abs(zp_start - s.zp_start) > _AUSSCHBER_MERGE_MAX_GAP:
                    return None
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
        if station.dokumente and all(d.typ == Doktyp.STELLUNGNAHME for d in station.dokumente):
            return True
        return not station.dokumente and station_typ_str.lower() in BawueVorgaengeScraper._STELLUNGNAHME_STATION_TYPEN

    @staticmethod
    def _attach_stellungnahme(
        stationen: list[Station],
        dokumente: list[Dokument],
        vorgang_id: str,
    ) -> None:
        """Attach Stellungnahme documents to the most recent station."""
        if stationen:
            if isinstance(stationen[-1].stellungnahmen, Unset):
                stationen[-1].stellungnahmen = []
            stationen[-1].stellungnahmen.extend(dokumente)
        else:
            logger.warning(
                "Discarding Stellungnahme without preceding station for Vorgang %s",
                vorgang_id,
            )

    async def _gesetzblatt_ausgabedatum(self, fund: RawFundstelle) -> datetime | None:
        """Resolve a Gesetzblatt Fundstelle to the day the Gesetzblatt was issued (DD-047).

        PARLIS carries only the "Gesetz vom <Datum>" Ausfertigungsdatum, which is
        typically two to three weeks before the actual Ausgabedatum (issue #9).
        Returns None when the citation is missing or unresolvable, leaving the
        caller's PARLIS date untouched.
        """
        if self._gsblt_dates is None:
            return None
        jahr = fund.get("gesetzblatt_jahr")
        nummer = fund.get("gesetzblatt_nr")
        if jahr is None or nummer is None:
            return None
        zp = await self._gsblt_dates.publikationsdatum(jahr, nummer)
        if zp is not None:
            logger.info("Gesetzblatt %d Nr. %d issued %s (PARLIS: Ausfertigung)", jahr, nummer, zp.date())
        return zp

    async def _build_station(
        self, fund: RawFundstelle, initiative: str, vorgang_titel: str = "", vorgang_vnr: str | None = None
    ) -> Station | None:
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

        # The document keeps the PARLIS date as its `zp_referenz` (the Ausfertigung),
        # so `_build_dokumente` is deliberately still given the unmodified zp_start.
        gremium = self._determine_gremium(fund, station_typ)
        dokumente, trojanergefahr = await self._build_dokumente(
            fund, station_typ_str, mapping_text, station_typ, initiative, zp_start, vorgang_titel, vorgang_vnr
        )

        if station_typ == Stationstyp.POSTPARL_GSBLT:
            zp_start = await self._gesetzblatt_ausgabedatum(fund) or zp_start

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
          3. preparl-* stations (Regierungsbeschluss, Regierungsentwurf, ...) →
             reserved name `regierung` — these are cabinet-stage actions, not
             Landtag ones (issue #10, DD-021 update).
          4. Everything else → reserved name `plenum`, which the DoD defines
             as the default "wenn etwas 'irgendwie passiert'".
        """
        ausschuss = fund.get("ausschuss", "")
        if ausschuss:
            name: str = ausschuss
        elif station_typ == Stationstyp.POSTPARL_GSBLT:
            name = ReservedGremium.GESETZESBLATT
        elif station_typ.value.startswith("preparl-"):
            name = ReservedGremium.REGIERUNG
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
        vorgang_titel: str = "",
        vorgang_vnr: str | None = None,
    ) -> tuple[list[Dokument], int | None]:
        """Build the document list for a station (0 or 1 documents).

        A document is only created when the Fundstelle includes a PDF link.
        Authors are taken from the Fundstelle's autor_text if available, else the
        committee named in the Fundstelle, else the Vorgang-level initiative
        (who initiated the process). See DD-042.

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
            is_vorparlamentarisch=(station_typ == Stationstyp.PREPARL_REGBSL),
        )
        if doc_typ == Doktyp.SONSTIG and fund.get("plenarprotokoll"):
            doc_typ = Doktyp.REDEPROTOKOLL

        # Author priority (DD-042): the Fundstelle's own author, else the committee
        # that produced the document, else the Vorgang initiator. The committee step
        # matters for Beschlussempfehlungen, whose acting body PARLIS names in the
        # Fundstelle but which previously inherited the initiator (issue #71).
        autor_text = fund.get("autor_text", "") or fund.get("ausschuss", "")
        autoren = _parse_autoren(autor_text) if autor_text else _parse_autoren(initiative)

        # volltext carries the TODO marker until LLM enrichment fills it with
        # extracted PDF text. The new backend rejects empty strings; without
        # LLM, the placeholder remains. hash_ gets a link-derived placeholder
        # instead — a shared literal would collide across Vorgänge (DD-048).
        dok = Dokument(
            titel=station_typ_str or "Dokument",
            volltext=TODO_MARKER,
            hash_=placeholder_hash(pdf_url),
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
                    vorgang_titel=vorgang_titel,
                    vorgang_vnr=vorgang_vnr,
                    metrics=self._llm_metrics,
                    cache=self.config.cache,
                )
                dok = result.dokument
                trojanergefahr = result.trojanergefahr
                if result.download_failed and (vorgnr := get_vorgangs_id()):
                    self._pending_pdf_downloads.add(vorgnr)
            except Exception:
                logger.warning("Document enrichment failed for %s", pdf_url)

        return [dok], trojanergefahr

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


def _dedup_drucks(doks: list[Dokument]) -> list[Dokument]:
    """Remove duplicate documents with the same Drucksache number.

    Documents without a drucksnr are always kept (no dedup key).
    Ported from the BY scraper's dedup_drucks pattern.

    The dedup key includes the link's ``#page=N`` anchor: several distinct
    documents can share one Sammeldrucksache PDF, each anchored to its own page
    (e.g. two Änderungsanträge under Drucksache 17/4495, one at ``#page=1`` and
    the adopted one at ``#page=5``, issue #72). They differ only in the anchor,
    so keying on the Drucksache alone would drop all but the first. True
    duplicates (same Drucksache, same anchor) are still collapsed.
    """
    unique: list[Dokument] = []
    seen_keys: set[tuple[str, str]] = set()
    for d in doks:
        drucksnr = d.drucksnr
        if drucksnr:
            key = (drucksnr, urlparse(d.link).fragment if d.link else "")
            if key in seen_keys:
                continue
            seen_keys.add(key)
        unique.append(d)
    return unique


# Station types whose document carries the Initiativdrucksache: the parliamentary
# initiative (Gesetzentwurf/Antrag/Anfrage einer Fraktion) and the government bill
# (Gesetzentwurf der Landesregierung, mapped to preparl-regbsl).
_INITIATIV_TYPEN: frozenset[Stationstyp] = frozenset(
    {
        Stationstyp.PARL_INITIATIV,
        Stationstyp.PREPARL_REGBSL,
    }
)


def _initiativ_drucksnr(stationen: list[Station]) -> str | None:
    """Return the Drucksache number of the initiating document, if available.

    The Initiativdrucksache is the Drucksache under which the originating
    Gesetzentwurf/Antrag was published. It lives on the first initiative-type
    station that carries a document with a Drucksache number. The synthetic
    parl-initiativ inserted after a government Gesetzentwurf has no document, so
    the preparl-regbsl Gesetzentwurf is matched in that case.

    Only consulted when ``emit-initdrucks-ident`` is enabled (DD-041).
    """
    for station in stationen:
        if station.typ not in _INITIATIV_TYPEN:
            continue
        for dok in station.dokumente:
            drucksnr = dok.drucksnr
            if drucksnr:
                return drucksnr
    return None


def _initiativ_drucksnr_from_fundstellen(fundstellen: list[RawFundstelle], initiative: str) -> str | None:
    """Initiating Drucksache derived from the raw Fundstellen, before stations exist.

    Mirrors :func:`_initiativ_drucksnr` but works on the parsed Fundstellen so the
    number is available *during* station construction, when enrichment runs and
    needs it as a section-matching anchor for shared plenary protocols (issue #35).
    """
    for fund in fundstellen:
        mapping_text = fund.get("station_typ") or fund.get("raw", "")
        if map_stationstyp(mapping_text, initiator=initiative) in _INITIATIV_TYPEN:
            drucksnr = none_if_blank(fund.get("drucksache"))
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
                kurztitel = dok.kurztitel
                if kurztitel:
                    return kurztitel
    return None


def _synthetic_initiativ_api_id(vorgang_id: str) -> str | Unset:
    """Deterministic, date-free api_id for the synthetic parl-initiativ (DD-046).

    Mirrors the synthetic ablehnung (DD-010): the station carries no Fundstelle of
    its own, so nothing about it is stable except the Vorgang it belongs to. Keying
    it on ``zp_start`` like a real station (DD-028) breaks on the next scrape,
    because the date is inferred from the stations that happen to follow the regbsl
    at that moment. Left UNSET for "unknown" Vorgänge, matching
    ``_assign_stable_station_ids``, which cannot scope an id without a Vorgang id.
    """
    if vorgang_id == "unknown":
        return UNSET
    return str(uuid5(NAMESPACE_URL, f"bawue-synth-initiativ-{vorgang_id}"))


def _assign_stable_station_ids(stationen: list[Station], vorgang_id: str) -> None:
    """Give every station a deterministic, Vorgang-scoped api_id (DD-028, DD-034).

    The backend matches a station against an existing one on re-upload by its
    api_id or by a shared document hash (the ``station_merge_candidates`` query,
    see DD-010). Relying on the document hash is unsafe: identical PDFs recur
    across Vorgänge (every Haushalt-Einzelplan Vorgang cites the shared
    Staatshaushaltsgesetz Drucksache 17/1000), so the hash matches *across*
    Vorgänge and the backend merges unrelated stations — a duplicate-key
    violation on ``rel_station_dokument_pkey`` (issue #47). A missing api_id is
    just as bad the other way: the backend cannot recognise the station on
    re-runs and inserts a duplicate, breaking the track (e.g. two
    ``parl-initiativ`` stations form an invalid ``II`` sequence, DD-028).

    Deriving the api_id from (vorgang_id, typ, zp_start) scopes identity to the
    Vorgang and lets the backend link and update the existing row. Stations that
    already carry an api_id (the synthetic ablehnung, DD-010) are left untouched.

    The key must not depend on the station's documents (issue #66): young PARLIS
    records list Fundstellen before their PDFs exist, so document sets grow on
    later scrapes. A document-derived key changes when the PDF arrives, the
    backend cannot match the re-uploaded station against the persisted row and
    keeps both — an invalid ``II`` sequence that fails track validation on every
    subsequent upload. Two same-typ stations may still legitimately share a
    ``zp_start`` (e.g. two plenary readings dated the same day, which
    ``_enforce_total_ordering`` leaves tied) and must not collapse onto one
    api_id, so ties are disambiguated by their position among equal-keyed
    stations instead. The first station of a (typ, zp_start) key keeps the exact
    DD-028 format so already-persisted rows still match.
    """
    if vorgang_id == "unknown":
        return
    tie_count: dict[tuple[str, str], int] = {}
    for station in stationen:
        if not isinstance(station.api_id, Unset):
            continue
        zp = station.zp_start.isoformat()
        key = f"bawue-station-{vorgang_id}-{station.typ.value}-{zp}"
        ordinal = tie_count.get((station.typ.value, zp), 0)
        tie_count[(station.typ.value, zp)] = ordinal + 1
        if ordinal:
            key = f"{key}-tie{ordinal + 1}"
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
