"""Document enrichment module: PDF text extraction + LLM semantic extraction.

Downloads PDFs, extracts text via kreuzberg, then uses pazufa-scraper-core's LLMConnector
to extract structured metadata (summary, keywords, scores) from the text.

BaWue-specific: PARLIS already provides title, authors, dates, and drucksache number.
The LLM is only used for body semantics (single pass, no header extraction needed).
"""

import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import NamedTuple

import litellm
from json_repair import repair_json
from pazufa_corelib.llm import LLMConnector

from bawue.cache import BawueCache, RedisUnavailableError
from bawue.pdf_text import (
    PdfNotAvailableError,
    _agenda_text,
    _extract_relevant_pages,
    _loop_semaphore,
    _parse_page_hint,
    ausgegeben_datum,
    download_pdf,
    extract_pdf_text,
    normalize_volltext,
)
from bawue.types import Doktyp, Dokument, Zusammenfassungstupel

logger = logging.getLogger(__name__)


class EnrichmentResult(NamedTuple):
    """Result of document enrichment: enriched Dokument + download status."""

    dokument: Dokument
    # True when the PDF download itself failed (e.g. a Plenarprotokoll that is
    # not published yet, issue #66). Retryable — the scraper must not cache the
    # Vorgang so the next cycle attempts the download again. Content-level
    # failures (empty/garbled extraction) are permanent and stay False.
    download_failed: bool = False
    # HTTP status of a failed download; 404 = not published yet (issue #128).
    http_status: int | None = None


class LLMMetrics:
    """Tracks LLM statistics for a scraper run: document enrichment and, per Vorgang,
    Kurztitel and Ressort (issue #56 — the numbers DD-055's live check needs)."""

    def __init__(self) -> None:
        self.success: int = 0
        self.failed: int = 0
        self.cache_hits: int = 0
        self.kurztitel_generated: int = 0
        self.kurztitel_fallback: int = 0
        self.ressort_classified: int = 0
        self.ressort_null: int = 0
        self.ressort_rejected: int = 0
        self.ressort_failed: int = 0

    @property
    def total(self) -> int:
        return self.success + self.failed + self.cache_hits

    def format_lines(self) -> list[str]:
        lines = []
        if total := self.total:
            lines += [
                "",
                "LLM enrichment:",
                f"  Success:     {self.success}",
                f"  Failed:      {self.failed}",
                f"  Cache hits:  {self.cache_hits} ({self.cache_hits / total:.0%})",
                f"  Total:       {total}",
            ]
        kurztitel = (self.kurztitel_generated, self.kurztitel_fallback)
        ressort = (self.ressort_classified, self.ressort_null, self.ressort_rejected, self.ressort_failed)
        if any(kurztitel + ressort):
            lines += [
                "",
                "LLM per Vorgang:",
                f"  Kurztitel:   {self.kurztitel_generated} generated, {self.kurztitel_fallback} fallback to titel",
                f"  Ressort:     {self.ressort_classified} classified, {self.ressort_null} null, "
                f"{self.ressort_rejected} rejected, {self.ressort_failed} failed",
            ]
        return lines


MAX_JSON_RETRIES = 3
_LLM_LIMIT = 3


_LLM_SLOT: dict = {}


def _llm_semaphore() -> asyncio.Semaphore:
    return _loop_semaphore(_LLM_SLOT, _LLM_LIMIT)


# Two-tier LLM semantics cache, keyed by (doc_hash, prompt_hash):
# 1. _hash_cache (in-memory dict): fast intra-cycle deduplication, cleared each cycle
#    by __main__.main() via clear_hash_cache() to prevent unbounded memory growth.
# 2. Redis (via BawueCache): persistent cross-cycle deduplication, survives restarts.
#    Key format: "llm-semantics:{doc_hash}:{prompt_hash}".
# The prompt hash covers the system prompt plus the doktyp-specific body prompt, so
# different prompts (e.g. ENTWURF vs STELLUNGNAHME) never share a cache entry.
_hash_cache: dict[str, dict] = {}

_REDIS_CACHE_PREFIX = "llm-semantics:"


def clear_hash_cache() -> None:
    """Clear the in-memory hash cache; called once per cycle by ``__main__.main()``."""
    _hash_cache.clear()


# ---------------------------------------------------------------------------
# LLM Prompts — one per Doktyp group, German, body-only (no header extraction)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "Du bist ein präziser Assistent für politische und juristische Texte. "
    "Antworte klar, faktenorientiert. "
    "Füge keine Formatierungen oder Hervorhebungen hinzu. "
    "Antworte nur mit dem reinen Text, ohne Einleitungen oder Erklärungen. "
    "Die Antwort darf nur die direkt angeforderten Informationen enthalten. "
    "Spekulationen oder Annahmen sind zu vermeiden."
)

# Closes the JSON template of the single-topic prompts below: Brandenburg's three
# partial summaries and their rules (DD-056). Shared so the two cannot drift apart.
_TEILE_PROMPT = """\
 "intention": "Welches Problem soll gelöst werden und mit welchem Ziel? (1-3 Sätze)",
 "regelungsinhalt": "Was wird konkret geregelt oder geändert? (1-3 Sätze)",
 "kosten": "Welche Kosten, Einnahmen oder finanziellen Auswirkungen nennt das Dokument? (1-3 Sätze)"}
intention, regelungsinhalt und kosten: sachlich, ohne Wertung, nur Informationen aus dem Text.
Gibt das Dokument zu intention, regelungsinhalt oder kosten nichts her: leeren String zurückgeben.
Die Zusammenfassung ist immer zu füllen.
Keine Aufzählung der Artikelstruktur ("Artikel 1 ändert ..."), sondern Inhalt in eigenen Worten.
Antworte ausschließlich mit validem JSON. Halluziniere keine Informationen."""

BODY_PROMPT_ENTWURF = (
    """\
Extrahiere aus dem folgenden Gesetzestext die folgenden Informationen als JSON:
{"schlagworte": ["Liste inhaltlich bedeutsamer Schlagworte"],
 "zusammenfassung": "Zusammenfassung in 150-250 Worten",
 "kurztitel": "Kurzer verständlicher Titel in einfacher Sprache",
 "vorwort": "Präambel oder Intentionsbeschreibung des Entwurfs, falls vorhanden",
"""
    + _TEILE_PROMPT
)

BODY_PROMPT_STELLUNGNAHME = """\
Extrahiere aus der folgenden Stellungnahme die folgenden Informationen als JSON:
{"schlagworte": ["Liste inhaltlich bedeutsamer Schlagworte"],
 "zusammenfassung": "Zusammenfassung in 150-250 Worten",
 "kurztitel": "Kurzer verständlicher Titel in einfacher Sprache",
 "meinung": <1-5, Meinungsbild: 1=ablehnend, 5=zustimmend>}
Antworte ausschließlich mit validem JSON. Halluziniere keine Informationen."""

BODY_PROMPT_BESCHLUSSEMPF = (
    """\
Extrahiere aus der folgenden Beschlussempfehlung die folgenden Informationen als JSON:
{"schlagworte": ["Liste inhaltlich bedeutsamer Schlagworte"],
 "zusammenfassung": "Zusammenfassung in 150-250 Worten",
 "kurztitel": "Kurzer verständlicher Titel in einfacher Sprache",
 "meinung": <1-5, Meinungsbild: 1=Ablehnung empfohlen, 5=Zustimmung empfohlen>,
"""
    + _TEILE_PROMPT
)

BODY_PROMPT_GENERIC = """\
Extrahiere aus dem folgenden parlamentarischen Dokument die folgenden Informationen als JSON:
{"schlagworte": ["Liste inhaltlich bedeutsamer Schlagworte"],
 "zusammenfassung": "Zusammenfassung in 150-250 Worten",
 "kurztitel": "Kurzer verständlicher Titel in einfacher Sprache"}
Antworte ausschließlich mit validem JSON. Halluziniere keine Informationen."""

BODY_PROMPT_REDEPROTOKOLL = """\
Extrahiere aus dem folgenden Ausschnitt eines Plenarprotokolls die folgenden Informationen als JSON:
{"schlagworte": ["Liste inhaltlich bedeutsamer Schlagworte"],
 "zusammenfassung": "Neutrale Zusammenfassung des Sitzungsabschnitts in 150-250 Worten",
 "kurztitel": "Kurzer verständlicher Titel in einfacher Sprache"}
Dieses Protokoll wird von mehreren Gesetzgebungsverfahren gemeinsam genutzt, die in
derselben Plenarsitzung behandelt wurden. Fasse den Sitzungsabschnitt daher neutral
zusammen und nenne alle behandelten Tagesordnungspunkte bzw. Gesetzentwürfe
gleichrangig, ohne dich auf einen einzelnen davon zu konzentrieren.
Antworte ausschließlich mit validem JSON. Halluziniere keine Informationen."""

# A protocol narrowed to one agenda item's pages (issue #74). Items debated together
# share the pages, hence the row (DD-049), so the summary names them all.
BODY_PROMPT_REDEPROTOKOLL_TOP = """\
Extrahiere aus dem folgenden Ausschnitt eines Plenarprotokolls die folgenden Informationen als JSON:
{"schlagworte": ["Liste inhaltlich bedeutsamer Schlagworte"],
 "zusammenfassung": "Neutrale Zusammenfassung der Beratung in 150-250 Worten",
 "kurztitel": "Kurzer verständlicher Titel in einfacher Sprache"}
Der Ausschnitt umfasst die Seiten eines Tagesordnungspunkts. Am Anfang und am Ende können
Reste anderer Tagesordnungspunkte stehen; lass diese weg. Werden mehrere Gesetzentwürfe
gemeinsam beraten, nenne sie gleichrangig.
Antworte ausschließlich mit validem JSON. Halluziniere keine Informationen."""

_DOKTYP_PROMPT_MAP: dict[Doktyp, str] = {
    Doktyp.ENTWURF: BODY_PROMPT_ENTWURF,
    Doktyp.PREPARL_ENTWURF: BODY_PROMPT_ENTWURF,
    Doktyp.STELLUNGNAHME: BODY_PROMPT_STELLUNGNAHME,
    Doktyp.BESCHLUSSEMPF: BODY_PROMPT_BESCHLUSSEMPF,
    Doktyp.REDEPROTOKOLL: BODY_PROMPT_REDEPROTOKOLL,
}


def _prompt_for_doktyp(doktyp: Doktyp) -> str:
    """Return the appropriate LLM prompt for a given document type."""
    return _DOKTYP_PROMPT_MAP.get(doktyp, BODY_PROMPT_GENERIC)


def _context_prefix(titel: str | None, drucksnr: str | None) -> str:
    """Build the document-identity header that tells the LLM which matter to summarize.

    Plenary protocols (and other multi-topic PDFs) debate several bills in one
    document. Without naming the target bill/Drucksache the model cannot tell
    which passage is relevant and may summarize the wrong topic (issue #32).
    Returns an empty string when no identity is available.
    """
    parts: list[str] = []
    if titel and titel.strip():
        parts.append(f"dem Gesetzgebungsverfahren „{titel.strip()}“")
    if drucksnr and drucksnr.strip():
        parts.append(f"Drucksache {drucksnr.strip()}")
    if not parts:
        return ""
    bezug = " bzw. ".join(parts)
    return (
        f"KONTEXT: Dieses Dokument gehört zu {bezug}. "
        "Falls der Text mehrere Themen behandelt (z. B. ein Plenarprotokoll mit "
        "mehreren Tagesordnungspunkten), berücksichtige ausschließlich die "
        "Abschnitte, die sich auf dieses Verfahren bzw. diese Drucksache beziehen.\n\n"
    )


def _prompt_fingerprint(
    doktyp: Doktyp,
    drucksnr: str | None = None,
    titel: str | None = None,
    vorgang_vnr: str | None = None,
    body_prompt: str | None = None,
) -> str:
    """SHA256 over (system prompt + context header + body prompt) for the given doktyp.

    Used as the prompt component of the LLM semantics cache key. The document
    identity (titel/Drucksache) is part of the fingerprint because the same
    protocol PDF — hence the same file hash — is reused across several bills
    debated in one session; without it the second bill would reuse the first
    bill's cached (wrong-topic) summary (issue #32).

    *vorgang_vnr* (the surrounding Vorgang's initiating Drucksache) is folded in
    as a defense-in-depth per-Vorgang component: it uniquely identifies the bill,
    so two bills sharing one protocol PDF stay on separate cache entries even if
    their titles happen to coincide (issue #35).
    """
    body = body_prompt or _prompt_for_doktyp(doktyp)
    prefix = _context_prefix(titel, drucksnr)
    vnr_part = f"\n\nVORGANG-VNR:{vorgang_vnr}" if vorgang_vnr else ""
    return hashlib.sha256(f"{_SYSTEM_PROMPT}\n\n{prefix}{body}{vnr_part}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Score validation
# ---------------------------------------------------------------------------

_SCORE_RANGES: dict[str, tuple[int, int]] = {
    "meinung": (1, 5),
}


def _validate_scores(data: dict) -> dict:
    """Validate and clamp the meinung (1-5) range.

    Non-numeric values are removed. Numeric values are clamped to valid ranges.
    """
    for field, (lo, hi) in _SCORE_RANGES.items():
        if field not in data:
            continue
        val = data[field]
        if not isinstance(val, (int, float)):
            logger.warning("LLM returned non-numeric %s=%r, removing", field, val)
            data[field] = None
        else:
            clamped = max(lo, min(hi, int(val)))
            if clamped != val:
                logger.warning("LLM %s=%r out of range [%d,%d], clamped to %d", field, val, lo, hi, clamped)
            data[field] = clamped
    # Remove None entries set by non-numeric removal
    return {k: v for k, v in data.items() if v is not None}


# ---------------------------------------------------------------------------
# LLM-output sanitization
# ---------------------------------------------------------------------------

# Matches HTML-like tag artefacts (e.g. ``</narrow>``, ``<br/>`` from
# gpt-5-nano output). Requires a letter immediately after ``<`` (or ``</``)
# so stray inequalities like ``x < 5 und y > 3`` are NOT treated as a tag —
# those fall through to the guillemet substitution below. 80-char inner cap
# prevents pathological backtracking on malformed input.
_HTML_LIKE_TAG_RE = re.compile(r"</?[a-zA-Z][^<>]{0,80}>")


def _sanitize_llm_text(text: object) -> str | None:
    """Strip HTML-like tag artefacts and neutralise stray angle brackets in LLM output (DD-027).

    The backend's XSS validator rejects payloads containing ``<``/``>``. The
    system prompt forbids formatting (``Füge keine Formatierungen ... hinzu``),
    so any tag in a string-typed LLM field is by definition an artefact (e.g.
    gpt-5-nano sporadically appending ``</narrow>``). Matches
    :func:`normalize_volltext`'s defensive guillemet substitution for any
    brackets that survive the tag pass. Returns ``None`` for empty / None
    inputs so the API client omits the field rather than sending an empty
    string (the backend rejects those) — and for non-strings: the LLM's JSON is
    untyped, and one malformed field must not drop all the others.
    """
    if not isinstance(text, str) or not text:
        return None
    text = _HTML_LIKE_TAG_RE.sub("", text)
    text = text.replace("<", "\u2039").replace(">", "\u203a")
    text = text.strip()
    return text or None


# Reserved spec-0.2.5 type for an LLM-written summary of the whole document
# (GitHub issue #42, DD-054); a plain string would be stored as `full`.
ZUSAMMENFASSUNG_TYP = "full-llm"


# Partial summaries (issue #42 step 2, DD-056): semantics key → typ, in send order.
# Labels mirror pazufa-scraper-bb (#67); the "llm" marker is what the website keys
# its "AI generated" label on. Only BODY_PROMPT_ENTWURF/_BESCHLUSSEMPF ask for them.
ZUSAMMENFASSUNG_TEILE: dict[str, str] = {
    "intention": "intention-llm",
    "regelungsinhalt": "regelungsinhalt-llm",
    "kosten": "kosten-llm",
}


def _llm_zusammenfassung(semantics: dict) -> list[Zusammenfassungstupel] | None:
    """Sanitised LLM summary as typed tuples — ``full-llm`` first, then any non-empty
    partial summary from :data:`ZUSAMMENFASSUNG_TEILE`. None without a ``full-llm``
    text: as in BB, the sections never go out on their own (DD-056)."""
    if not (full := _sanitize_llm_text(semantics.get("zusammenfassung"))):
        return None
    tupel = [Zusammenfassungstupel(typ=ZUSAMMENFASSUNG_TYP, inhalt=full)]
    for key, typ in ZUSAMMENFASSUNG_TEILE.items():
        if text := _sanitize_llm_text(semantics.get(key)):
            tupel.append(Zusammenfassungstupel(typ=typ, inhalt=text))
    return tupel


def zusammenfassung_text(dok: Dokument) -> str | None:
    """Plain text of *dok*'s ``full-llm`` summary, None when it has none."""
    if isinstance(dok.zusammenfassung, list):
        return next((t.inhalt for t in dok.zusammenfassung if t.typ == ZUSAMMENFASSUNG_TYP), None)
    return None


def _sanitize_llm_strings(values: list[str] | None) -> list[str] | None:
    """Apply :func:`_sanitize_llm_text` to each list item, dropping empty results."""
    if not values:
        return values
    cleaned = [_sanitize_llm_text(v) for v in values]
    return [v for v in cleaned if v]


# ---------------------------------------------------------------------------
# LLM semantic extraction
# ---------------------------------------------------------------------------


def _parse_llm_response(content: str) -> dict:
    """Parse the LLM's JSON content, falling back to json-repair on malformed output.

    Raises JSONDecodeError if repair produces something unusable (non-dict or empty),
    so `enrich_dokument`'s text-only fallback kicks in and `metrics.failed` is counted.
    """
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(
            "LLM returned malformed JSON (%s at col %d); attempting json-repair",
            e.msg,
            e.colno,
        )
        repaired = repair_json(content, return_objects=True)
        # repair may return "" / [] / None / {} for inputs it can't rescue
        if not isinstance(repaired, dict) or not repaired:
            raise
        return repaired


async def _llm_json(llm: LLMConnector, model: str, messages: list[dict]) -> dict:
    """Run one JSON-mode chat completion and return the parsed object."""
    async with _llm_semaphore():
        response = await litellm.acompletion(
            model=model,
            api_key=llm.api_key,
            messages=messages,
            temperature=llm.temperature,
            timeout=llm.timeout_seconds,
            response_format={"type": "json_object"},
            num_retries=MAX_JSON_RETRIES,
        )
    return _parse_llm_response(response.choices[0].message.content)


async def extract_semantics(
    llm: LLMConnector,
    full_text: str,
    doktyp: Doktyp,
    model: str = "gpt-5-nano",
    dok_titel: str | None = None,
    drucksnr: str | None = None,
    body_prompt: str | None = None,
) -> dict:
    """Call LLM to extract structured metadata from document text.

    Returns a dict with keys like schlagworte, zusammenfassung, kurztitel,
    and optionally meinung/vorwort depending on doktyp.

    The full document text is sent unmodified — there is no token truncation.
    When *dok_titel* and/or *drucksnr* are given, a context header is prepended
    so the model summarizes only the passages that concern this specific bill,
    even in multi-topic plenary protocols (issue #32).

    Uses response_format=json_object with a json-repair fallback for models
    that occasionally emit malformed JSON, and validates score ranges
    post-extraction.
    """
    prompt = body_prompt or _prompt_for_doktyp(doktyp)
    context = _context_prefix(dok_titel, drucksnr)
    user_message = f"{context}{prompt}\n\n{full_text}"

    input_tokens = litellm.token_counter(model=model, text=user_message)
    logger.info("LLM call: %d input tokens, model=%s", input_tokens, model)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    return _validate_scores(await _llm_json(llm, model, messages))


async def narrow_to_relevant_section(
    llm: LLMConnector,
    text: str,
    doktyp: Doktyp,
    titel: str | None,
    drucksnr: str | None,
) -> str:
    """Narrow a plenary protocol to the section that concerns this Vorgang.

    Delegates to pazufa-corelib's ``LLMConnector.extract_relevant_section``, which
    splits the text into chunks, has the LLM mark the relevant line ranges and then
    extracts them verbatim (issue #32). Only the *summary input* is narrowed; the
    stored ``volltext`` remains the page-hint window.

    Only ``REDEPROTOKOLL`` is narrowed — other doktypen are single-topic, so
    narrowing would be wasteful and could drop content.

    Returns *text* unchanged when narrowing is not applicable, the extractor is
    unavailable, it errors, or it finds no relevant content.
    """
    if doktyp != Doktyp.REDEPROTOKOLL:
        return text
    if not (titel and titel.strip()):
        return text  # extract_relevant_section requires a non-empty title
    if not hasattr(llm, "extract_relevant_section"):
        return text  # older corelib without the feature

    try:
        section = await llm.extract_relevant_section(text, titel.strip(), vorgang_vnr=drucksnr or None)
    except Exception:
        logger.warning("Section extraction failed, using full window", exc_info=True)
        return text

    if section:
        logger.info("Narrowed protocol from %d to %d chars via section extraction", len(text), len(section))
        return section
    logger.info("Section extraction found no relevant content, using full window")
    return text


# ---------------------------------------------------------------------------
# Redis cache helpers
# ---------------------------------------------------------------------------


def _cache_key(doc_hash: str, prompt_hash: str) -> str:
    """Composite cache key binding a document to the exact prompt used."""
    return f"{doc_hash}:{prompt_hash}"


def _redis_get(
    cache: BawueCache | None, key: str, prefix: str = _REDIS_CACHE_PREFIX, typehint: str = "LLM Semantics"
) -> str | None:
    """Look up an LLM result in Redis under *prefix*. Returns the stored string or None."""
    if cache is None:
        return None
    return cache.get_raw(f"{prefix}{key}", typehint=typehint)


def _redis_set(
    cache: BawueCache | None, key: str, value: str, prefix: str = _REDIS_CACHE_PREFIX, typehint: str = "LLM Semantics"
) -> None:
    """Store an LLM result in Redis under *prefix*."""
    if cache is None:
        return
    cache.store_raw(f"{prefix}{key}", value, typehint=typehint)


# ---------------------------------------------------------------------------
# Main enrichment entry point
# ---------------------------------------------------------------------------


async def enrich_dokument(
    session,
    llm: LLMConnector,
    dok: Dokument,
    model: str = "gpt-5-nano",
    vorgang_titel: str | None = None,
    vorgang_vnr: str | None = None,
    metrics: LLMMetrics | None = None,
    cache: BawueCache | None = None,
    druckseiten: list[int] | None = None,
) -> EnrichmentResult:
    """Enrich a plain Dokument with PDF text extraction and LLM semantics.

    Takes an existing Dokument (as built by the scraper with empty volltext/hash)
    and returns an EnrichmentResult containing the enriched Dokument. PARLIS
    metadata (titel, autoren, drucksnr, timestamps) is preserved.

    *vorgang_titel* is the bill title of the surrounding Vorgang. It is passed
    to the LLM as document-identity context so that multi-topic plenary protocols
    are summarized for the correct bill (issue #32); it falls back to the
    document's own title when not provided.

    *vorgang_vnr* is the surrounding Vorgang's initiating Drucksache. It is handed
    to the section extractor as a matching anchor (protocol documents carry no
    Drucksache of their own) and folded into the cache key so shared protocols stay
    Vorgang-specific (issue #35).

    Exception: for ``REDEPROTOKOLL``, *vorgang_titel*/*vorgang_vnr* are deliberately
    withheld: bills debated together share the row (issue #49). With the Fundstelle's
    printed pages (*druckseiten*) and its anchor, the LLM reads only that agenda item
    and caches per file and pages (issue #74, DD-037). Without them it reads the window
    and caches on the plain file digest, one summary per sitting (issue #25).

    ``hash_`` is the document's row identity in the backend and always carries the
    ``#page=N`` anchor when the Fundstelle has one — one row per Fundstelle, so
    ``titel``, ``link`` and ``autoren`` stay with the Vorgang that produced them
    (issue #25, DD-043).

    Uses an in-memory hash cache to skip LLM calls for duplicate PDFs within
    the same scraper run.

    Graceful degradation:
    - Tier 1: Full enrichment (PDF + LLM succeed)
    - Tier 2: Text-only (PDF succeeds, LLM fails) → volltext + hash, no LLM fields
    - Tier 3: Metadata-only (PDF download fails) → original Dokument unchanged
    """
    pdf_path: Path | None = None
    try:
        # Download PDF
        page_hint = _parse_page_hint(dok.link)
        try:
            pdf_path = await download_pdf(session, dok.link)
        except PdfNotAvailableError as exc:
            logger.warning("%s, returning original document", exc)
            return EnrichmentResult(dokument=dok, download_failed=True, http_status=exc.status)
        except Exception:
            logger.warning("PDF download failed for %s, returning original document", dok.link, exc_info=True)
            return EnrichmentResult(dokument=dok, download_failed=True)

        # Extract text + hash, then normalize. The page markers stay until the
        # window is cut, so a protocol can still pick its agenda pages (issue #74).
        marked_text, doc_hash = await extract_pdf_text(pdf_path, page_markers=page_hint is not None)
        full_text = normalize_volltext(
            marked_text if page_hint is None else _extract_relevant_pages(marked_text, page_hint)
        )

        # A #page=N anchor marks one Fundstelle's window into a PDF that several
        # references share: separate logical documents packed into one
        # Sammeldrucksache (several Änderungsanträge under Drucksache 17/4495,
        # issue #72), or the section of a plenary protocol that belongs to one
        # bill (issue #25). doc_hash is the whole-file digest, so all of them
        # would collide — the backend keys rel_station_dokument on the hash, so a
        # shared hash collapses them into a single row that can only hold one
        # set of titel/link/autoren. Folding the anchor into the persisted hash
        # keeps one row per Fundstelle; file_hash stays available as the identity
        # of the *file*, which is what the semantics cache keys on for
        # REDEPROTOKOLL (DD-043).
        file_hash = doc_hash
        if page_hint is not None:
            doc_hash = hashlib.sha256(f"{doc_hash}#page={page_hint}".encode()).hexdigest()

        if not full_text:
            # Empty volltext is rejected by the backend (required StrictStr).
            # Fall back to the original Dokument (volltext=TODO_MARKER) rather
            # than asking the LLM to invent metadata for an empty document.
            logger.warning(
                "PDF text extraction yielded empty content for %s, returning original document",
                dok.link,
            )
            return EnrichmentResult(dokument=dok)

        # Document-identity context for the prompt: the Vorgang title (bill name)
        # or, as a fallback, the document's own title, plus the Drucksache.
        # REDEPROTOKOLL is withheld from this context (issue #49) — see the
        # docstring's "Exception" paragraph. It caches on the file digest, plus the
        # agenda pages it read (DD-037). Every other doktyp caches against the
        # anchored hash, where a differing anchor means a genuinely different document.
        summary_text, body_prompt = full_text, None
        if dok.typ == Doktyp.REDEPROTOKOLL:
            context_titel = None
            context_vorgang_vnr = None
            context_drucksnr = None
            cache_hash = file_hash
            if (agenda := _agenda_text(marked_text, page_hint, druckseiten)) is not None:
                summary_text, pages = agenda
                body_prompt = BODY_PROMPT_REDEPROTOKOLL_TOP
                cache_hash = hashlib.sha256(f"{file_hash}#pages={pages}".encode()).hexdigest()
        else:
            context_titel = vorgang_titel or dok.titel
            context_vorgang_vnr = vorgang_vnr
            context_drucksnr = dok.drucksnr
            cache_hash = doc_hash

        # Try LLM extraction (with two-tier cache deduplication, keyed by
        # cache_hash + prompt_hash). The prompt hash includes the document identity
        # so two bills sharing one protocol PDF don't collide (issue #32) — except
        # REDEPROTOKOLL, where it is deliberately omitted (issue #49).
        prompt_hash = _prompt_fingerprint(
            dok.typ,
            drucksnr=context_drucksnr,
            titel=context_titel,
            vorgang_vnr=context_vorgang_vnr,
            body_prompt=body_prompt,
        )
        cache_key = _cache_key(cache_hash, prompt_hash)
        # zp_referenz stays the PARLIS date; zp_modifiziert = the printed issue date (issue #23).
        ausgegeben = ausgegeben_datum(full_text, dok.drucksnr, dok.zp_referenz)
        text_fields = {
            "titel": dok.titel,
            "volltext": full_text,
            "hash_": doc_hash,
            "typ": dok.typ,
            "zp_modifiziert": ausgegeben or dok.zp_modifiziert,
            "zp_referenz": dok.zp_referenz,
            "zp_erstellt": dok.zp_erstellt,
            "link": dok.link,
            "autoren": dok.autoren,
            "drucksnr": dok.drucksnr,
        }
        try:
            if cache_key in _hash_cache:
                logger.info(
                    "In-memory cache hit for %s/%s, skipping LLM call",
                    cache_hash[:12],
                    prompt_hash[:8],
                )
                semantics = _hash_cache[cache_key]
                if metrics is not None:
                    metrics.cache_hits += 1
            elif (cached_json := _redis_get(cache, cache_key)) is not None:
                logger.info(
                    "Redis cache hit for %s/%s, skipping LLM call",
                    cache_hash[:12],
                    prompt_hash[:8],
                )
                semantics = json.loads(cached_json)
                _hash_cache[cache_key] = semantics
                if metrics is not None:
                    metrics.cache_hits += 1
            else:
                # For multi-topic plenary protocols, narrow the summary input to
                # the section about this Vorgang (issue #32). volltext stays the
                # full window; only the LLM input is narrowed, and only on a cache
                # miss so the semantics cache stays effective. context_titel is
                # None for REDEPROTOKOLL, so narrow_to_relevant_section() no-ops
                # (issue #49) — see the docstring's "Exception" paragraph.
                summary_input = await narrow_to_relevant_section(
                    llm, summary_text, dok.typ, context_titel, context_vorgang_vnr or context_drucksnr
                )
                semantics = await extract_semantics(
                    llm,
                    summary_input,
                    dok.typ,
                    model=model,
                    dok_titel=context_titel,
                    drucksnr=context_drucksnr,
                    body_prompt=body_prompt,
                )
                _hash_cache[cache_key] = semantics
                _redis_set(cache, cache_key, json.dumps(semantics))
                if metrics is not None:
                    metrics.success += 1

            return EnrichmentResult(
                dokument=Dokument(
                    **text_fields,
                    zusammenfassung=_llm_zusammenfassung(semantics),
                    schlagworte=_sanitize_llm_strings(semantics.get("schlagworte")),
                    kurztitel=_sanitize_llm_text(semantics.get("kurztitel")),
                    meinung=semantics.get("meinung"),
                    vorwort=_sanitize_llm_text(semantics.get("vorwort")),
                ),
            )
        except RedisUnavailableError:
            raise  # fail the Vorgang so it is retried, not cached text-only (issue #105)
        except Exception:
            logger.warning("LLM extraction failed for %s, using text-only fallback", dok.link, exc_info=True)
            if metrics is not None:
                metrics.failed += 1
            return EnrichmentResult(dokument=Dokument(**text_fields))

    except RedisUnavailableError:
        raise
    except Exception:
        logger.warning("PDF download/extraction failed for %s, returning original document", dok.link, exc_info=True)
        return EnrichmentResult(dokument=dok)

    finally:
        if pdf_path is not None:
            pdf_path.unlink(missing_ok=True)
