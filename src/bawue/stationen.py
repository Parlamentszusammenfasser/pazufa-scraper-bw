"""Station rules: merge, order and identify the Stationen of one Vorgang (issue #100).

Plain functions over ``list[Station]`` with no scraper state; the DDs are cited where each rule lives.
"""

from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from bawue.types import UNSET, Doktyp, Dokument, Station, Stationstyp, Unset

# Same-committee `parl-ausschber` fundstellen belonging to one deliberation round
# (e.g. a Beschlussempfehlung plus its Bericht) fall days apart and are merged into
# a single station. Two Beschlussempfehlungen months apart are *distinct*
# deliberations, however, and merging them would silently drop the later date
# (`_merge_into` keeps the earlier `zp_start`). Cap the merge window so far-apart
# committee reports stay separate records (issue #54, DD-039).
_AUSSCHBER_MERGE_MAX_GAP = timedelta(days=60)


def ensure_ausschber_after_vollvlsgn(stationen: list[Station]) -> None:
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


def enforce_total_ordering(stationen: list[Station]) -> None:
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


def try_merge_station(
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
    target = _find_merge_target(stationen, station, station_typ_str, last_station_typ_str)
    if target is None:
        return False
    _merge_into(target, station)
    return True


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
        return _find_matching_ausschuss(stationen, station.gremium.name, station.zp_start)

    if not stationen:
        return None
    last = stationen[-1]
    if last.typ != station.typ or last.gremium.name != station.gremium.name:
        return None

    if station.typ == Stationstyp.PARL_VOLLVLSGN and not _same_round_label(station_typ_str, last_station_typ_str):
        return None

    return last


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


def is_stellungnahme(station: Station, station_typ_str: str = "") -> bool:
    """Check if a station represents a Stellungnahme (position statement).

    Detected by either:
    - All documents having Doktyp.STELLUNGNAHME (standard case with PDF)
    - The Fundstelle type being "Stellungnahme"/"Antwort" with no documents (no PDF URL)
    """
    if station.dokumente and all(d.typ == Doktyp.STELLUNGNAHME for d in station.dokumente):
        return True
    return not station.dokumente and station_typ_str.lower() in _STELLUNGNAHME_STATION_TYPEN


def attach_stellungnahme(stationen: list[Station], dokumente: list[Dokument]) -> None:
    """Attach Stellungnahme documents to the most recent station."""
    if isinstance(stationen[-1].stellungnahmen, Unset):
        stationen[-1].stellungnahmen = []
    stationen[-1].stellungnahmen.extend(dokumente)


def synthetic_initiativ_api_id(vorgang_id: str) -> str | Unset:
    """Deterministic, date-free api_id for the synthetic parl-initiativ (DD-046).

    Mirrors the synthetic ablehnung (DD-010): the station carries no Fundstelle of
    its own, so nothing about it is stable except the Vorgang it belongs to. Keying
    it on ``zp_start`` like a real station (DD-028) breaks on the next scrape,
    because the date is inferred from the stations that happen to follow the regbsl
    at that moment. Left UNSET for "unknown" Vorgänge, matching
    ``assign_stable_station_ids``, which cannot scope an id without a Vorgang id.
    """
    if vorgang_id == "unknown":
        return UNSET
    return str(uuid5(NAMESPACE_URL, f"bawue-synth-initiativ-{vorgang_id}"))


def assign_stable_station_ids(stationen: list[Station], vorgang_id: str) -> None:
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
    ``enforce_total_ordering`` leaves tied) and must not collapse onto one
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
