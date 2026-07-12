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
import ssl
import tempfile
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

import aiohttp
import certifi
import litellm
from json_repair import repair_json
from kreuzberg import ConcurrencyConfig, ExtractionConfig, OcrConfig, PageConfig, extract_file
from pazufa_corelib.llm import LLMConnector
from pazufa_corelib.normalization import normalize_volltext as _core_normalize_volltext

from bawue.cache import BawueCache
from bawue.types import Doktyp, Dokument

logger = logging.getLogger(__name__)


class EnrichmentResult(NamedTuple):
    """Result of document enrichment: enriched Dokument + optional Station-level fields."""

    dokument: Dokument
    trojanergefahr: int | None = None


class LLMMetrics:
    """Tracks LLM enrichment statistics for a scraper run."""

    def __init__(self) -> None:
        self.success: int = 0
        self.failed: int = 0
        self.cache_hits: int = 0

    @property
    def total(self) -> int:
        return self.success + self.failed + self.cache_hits

    def format_lines(self) -> list[str]:
        total = self.total
        ratio_suffix = f" ({self.cache_hits / total:.0%})" if total > 0 else ""
        return [
            "",
            "LLM enrichment:",
            f"  Success:     {self.success}",
            f"  Failed:      {self.failed}",
            f"  Cache hits:  {self.cache_hits}{ratio_suffix}",
            f"  Total:       {total}",
        ]


MAX_JSON_RETRIES = 3
MIN_TEXT_LENGTH = 64
_LLM_SEMAPHORE = asyncio.Semaphore(3)

# Two-tier LLM semantics cache, keyed by (doc_hash, prompt_hash):
# 1. _hash_cache (in-memory dict): fast intra-cycle deduplication, cleared each cycle
#    via clear_hash_cache() to prevent unbounded memory growth.
# 2. Redis (via BawueCache): persistent cross-cycle deduplication, survives restarts.
#    Key format: "llm-semantics:{doc_hash}:{prompt_hash}".
# The prompt hash covers the system prompt plus the doktyp-specific body prompt, so
# different prompts (e.g. ENTWURF vs STELLUNGNAHME) never share a cache entry.
_hash_cache: dict[str, dict] = {}

_REDIS_CACHE_PREFIX = "llm-semantics:"


def clear_hash_cache() -> None:
    """Clear the in-memory hash cache between scraper cycles."""
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

BODY_PROMPT_ENTWURF = """\
Extrahiere aus dem folgenden Gesetzestext die folgenden Informationen als JSON:
{"schlagworte": ["Liste inhaltlich bedeutsamer Schlagworte"],
 "zusammenfassung": "Zusammenfassung in 150-250 Worten",
 "kurztitel": "Kurzer verständlicher Titel in einfacher Sprache",
 "vorwort": "Präambel oder Intentionsbeschreibung des Entwurfs, falls vorhanden",
 "trojanergefahr": <1-10, Wahrscheinlichkeit versteckter Zwecke>}
Antworte ausschließlich mit validem JSON. Halluziniere keine Informationen."""

BODY_PROMPT_STELLUNGNAHME = """\
Extrahiere aus der folgenden Stellungnahme die folgenden Informationen als JSON:
{"schlagworte": ["Liste inhaltlich bedeutsamer Schlagworte"],
 "zusammenfassung": "Zusammenfassung in 150-250 Worten",
 "kurztitel": "Kurzer verständlicher Titel in einfacher Sprache",
 "meinung": <1-5, Meinungsbild: 1=ablehnend, 5=zustimmend>}
Antworte ausschließlich mit validem JSON. Halluziniere keine Informationen."""

BODY_PROMPT_BESCHLUSSEMPF = """\
Extrahiere aus der folgenden Beschlussempfehlung die folgenden Informationen als JSON:
{"schlagworte": ["Liste inhaltlich bedeutsamer Schlagworte"],
 "zusammenfassung": "Zusammenfassung in 150-250 Worten",
 "kurztitel": "Kurzer verständlicher Titel in einfacher Sprache",
 "meinung": <1-5, Meinungsbild: 1=Ablehnung empfohlen, 5=Zustimmung empfohlen>,
 "trojanergefahr": <1-10, Wahrscheinlichkeit versteckter Zwecke>}
Antworte ausschließlich mit validem JSON. Halluziniere keine Informationen."""

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
    body = _prompt_for_doktyp(doktyp)
    prefix = _context_prefix(titel, drucksnr)
    vnr_part = f"\n\nVORGANG-VNR:{vorgang_vnr}" if vorgang_vnr else ""
    return hashlib.sha256(f"{_SYSTEM_PROMPT}\n\n{prefix}{body}{vnr_part}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Score validation
# ---------------------------------------------------------------------------

_SCORE_RANGES: dict[str, tuple[int, int]] = {
    "trojanergefahr": (1, 10),
    "meinung": (1, 5),
}


def _validate_scores(data: dict) -> dict:
    """Validate and clamp trojanergefahr (1-10) and meinung (1-5) ranges.

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
# Text normalization
# ---------------------------------------------------------------------------

# Line-break hyphenation (issue #20). German words split across a line break
# carry a hyphen ("abzu-senken", "Lebens-jahr"). Kreuzberg strips the newline
# but keeps the hyphen, so the artefact survives as an *inline* hyphen that
# corelib's "-\n" rejoin never sees. We remove the hyphen only when it sits
# between two lowercase letters (an optional lone newline covers backends that
# preserve the break). Genuine compound hyphens continue with an uppercase
# letter ("Baden-Württemberg", "E-Mail") and are left untouched.
_LINEBREAK_HYPHEN_RE = re.compile(r"(?<=[a-zäöüß])-\n?(?=[a-zäöüß])")


def _paragraph_quality_score(text: str) -> float:
    """Score a paragraph's text quality from 0.0 (garbled) to 1.0 (clean).

    Detects broken PDF font encoding via three signals:
    - C1 control characters (0x80-0x9F)
    - Latin Extended-A+B characters (0x0100-0x024F)
    - Long words without German vowels
    """
    if not text or not text.strip():
        return 1.0

    alpha_chars = [c for c in text if c.isalpha()]
    n = len(alpha_chars)
    if n == 0:
        return 1.0

    # Signal 1: C1 control characters — never in properly extracted German text
    c1_count = sum(1 for c in text if 0x80 <= ord(c) <= 0x9F)

    # Signal 2: Latin Extended-A+B (0x0100-0x024F, same range as _is_garbled)
    ext_count = sum(1 for c in text if 0x0100 <= ord(c) <= 0x024F)

    # Signal 3: long words without German vowels (German is vowel-rich)
    words = re.findall(r"[a-zA-ZäöüÄÖÜß]+", text)
    long_words = [w for w in words if len(w) >= 5]
    if long_words:
        vowelless = sum(1 for w in long_words if not re.search(r"[aeiouäöüAEIOUÄÖÜ]", w, re.IGNORECASE))
        vowelless_ratio = vowelless / len(long_words)
    else:
        vowelless_ratio = 0.0

    # Signal 4: excessive uppercase ratio — garbled font encoding produces mostly uppercase.
    # Normal German prose is ~5-15% uppercase (sentence starts, nouns).
    upper_count = sum(1 for c in alpha_chars if c.isupper())
    upper_ratio = upper_count / n
    # Only penalize when ratio is abnormally high (>60%)
    upper_penalty = max(0.0, (upper_ratio - 0.6) * 3) if upper_ratio > 0.6 else 0.0

    c1_ratio = c1_count / n
    ext_ratio = ext_count / n

    quality = 1.0 - min(1.0, c1_ratio * 20 + ext_ratio * 5 + vowelless_ratio * 1.5 + upper_penalty)
    return max(0.0, quality)


def normalize_volltext(text: str) -> str:
    r"""Normalize extracted PDF text: fix encoding, strip garbled sections, escape XSS.

    Delegates the shared cleaning pipeline (NFKC, invisible/control-char and
    HTML-entity stripping, garbled-paragraph removal, ``-\n`` line-break
    rejoining, guillemet XSS neutralization) to ``pazufa_corelib`` so the bulk
    of the logic lives in one place. Two BaWue-specific passes are layered on
    top of it:

    1. A second garbled-paragraph filter using :func:`_paragraph_quality_score`.
       corelib gates its uppercase penalty behind a ≥4-word minimum, so a
       broken-font paragraph that collapses to a single token after control-char
       stripping (the ASCII-shift pattern in BW PDFs) slips through corelib's
       scorer; the local scorer has no word-count gate and catches it.
    2. Issue #20: kreuzberg joins a hyphenated line break but keeps the hyphen,
       leaving an *inline* artefact (``abzu-senken``) that corelib's ``-\n``
       rule never sees.

    Applied after PDF text extraction, before LLM and API submission.
    """
    if not text:
        return text

    text = _core_normalize_volltext(text)

    # Pass 1: drop garbled paragraphs corelib's word-gated scorer misses.
    paragraphs = text.split("\n\n")
    text = "\n\n".join(p for p in paragraphs if _paragraph_quality_score(p) >= 0.5)

    # Pass 2 (issue #20): reassemble words the extractor left hyphenated.
    return _LINEBREAK_HYPHEN_RE.sub("", text)


# ---------------------------------------------------------------------------
# LLM-output sanitization
# ---------------------------------------------------------------------------

# Matches HTML-like tag artefacts (e.g. ``</narrow>``, ``<br/>`` from
# gpt-5-nano output). Requires a letter immediately after ``<`` (or ``</``)
# so stray inequalities like ``x < 5 und y > 3`` are NOT treated as a tag —
# those fall through to the guillemet substitution below. 80-char inner cap
# prevents pathological backtracking on malformed input.
_HTML_LIKE_TAG_RE = re.compile(r"</?[a-zA-Z][^<>]{0,80}>")


def _sanitize_llm_text(text: str | None) -> str | None:
    """Strip HTML-like tag artefacts and neutralise stray angle brackets in LLM output (DD-027).

    The backend's XSS validator rejects payloads containing ``<``/``>``. The
    system prompt forbids formatting (``Füge keine Formatierungen ... hinzu``),
    so any tag in a string-typed LLM field is by definition an artefact (e.g.
    gpt-5-nano sporadically appending ``</narrow>``). Matches
    :func:`normalize_volltext`'s defensive guillemet substitution for any
    brackets that survive the tag pass. Returns ``None`` for empty / None
    inputs so the API client omits the field rather than sending an empty
    string (the backend rejects those).
    """
    if not text:
        return None
    text = _HTML_LIKE_TAG_RE.sub("", text)
    text = text.replace("<", "\u2039").replace(">", "\u203a")
    text = text.strip()
    return text or None


def _sanitize_llm_strings(values: list[str] | None) -> list[str] | None:
    """Apply :func:`_sanitize_llm_text` to each list item, dropping empty results."""
    if not values:
        return values
    cleaned = [_sanitize_llm_text(v) for v in values]
    return [v for v in cleaned if v]


# ---------------------------------------------------------------------------
# Garbled text detection
# ---------------------------------------------------------------------------

_GARBLED_LATIN_EXT_THRESHOLD = 0.05  # 5% of alpha chars in Latin Extended → garbled


def _is_garbled(text: str) -> bool:
    """Detect garbled PDF text from broken font encodings.

    Returns True when the ratio of Latin Extended characters (U+0100-U+024F)
    to total alphabetic characters exceeds 5%.  These characters appear when
    a PDF font lacks a proper ToUnicode CMap and kreuzberg maps glyph IDs to
    wrong Unicode code points.
    """
    if not text or len(text) < MIN_TEXT_LENGTH:
        return False

    alpha_count = 0
    latin_ext_count = 0
    for c in text:
        if c.isalpha():
            alpha_count += 1
            if 0x0100 <= ord(c) <= 0x024F:
                latin_ext_count += 1

    if alpha_count == 0:
        return False

    return (latin_ext_count / alpha_count) > _GARBLED_LATIN_EXT_THRESHOLD


# ---------------------------------------------------------------------------
# Page-hint extraction for plenary protocols
# ---------------------------------------------------------------------------

_PAGE_MARKER_RE = re.compile(r"\n\n<!-- PAGE (\d+) -->\n\n")


def _parse_page_hint(url: str) -> int | None:
    """Extract page number from a URL's ``#page=N`` fragment."""
    fragment = urlparse(url).fragment
    match = re.match(r"^page=(\d+)$", fragment)
    return int(match.group(1)) if match else None


def _extract_relevant_pages(text: str, start_page: int, max_pages: int = 30) -> str:
    """Extract text from *start_page* through *start_page + max_pages* using page markers.

    Falls back to the full text when no markers are found or when the
    requested page range lies beyond the document.
    """
    parts = _PAGE_MARKER_RE.split(text)
    # parts alternates: [text_before_first_marker, page_num, text, page_num, text, ...]
    page_map: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        page_map[int(parts[i])] = parts[i + 1]

    if not page_map:
        return text

    end_page = start_page + max_pages
    relevant = [page_map[p] for p in sorted(page_map) if start_page <= p < end_page]

    if not relevant:
        return text

    windowed = "\n\n".join(relevant)
    # A #page=N fragment can point at a blank or near-empty page (e.g. one holding
    # only a running page number or a "TODO" placeholder). Persisting that window
    # would drop the document's real content, so fall back to the full text when
    # the window yields fewer than MIN_TEXT_LENGTH usable chars (issue #50).
    if len(windowed.strip()) < MIN_TEXT_LENGTH:
        logger.info(
            "Page-hint window (page %d) yielded <%d usable chars, falling back to full text",
            start_page,
            MIN_TEXT_LENGTH,
        )
        return text

    return windowed


# ---------------------------------------------------------------------------
# PDF download + text extraction
# ---------------------------------------------------------------------------


async def download_pdf(session, url: str) -> Path:
    """Download a PDF to a temporary file via aiohttp session."""
    clean_url = url.split("#")[0] if "#" in url else url
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    async with session.get(clean_url, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=60)) as response:
        if response.status != 200:
            logger.warning("PDF download returned HTTP %d: %s", response.status, clean_url)
            raise RuntimeError(f"PDF download failed with status {response.status}: {clean_url}")
        content = await response.read()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(content)
    return Path(tmp.name)


# OCR is the memory-heavy fallback: kreuzberg rasterizes every PDF page to a
# ~300 DPI bitmap before running Tesseract. By default its internal Rayon pool
# renders one page per CPU core in parallel, so a single large document can spike
# to ~7.5 GB RSS on a 12-core host (measured) — enough to OOM-kill the container
# on its own. Two guards keep the peak bounded and roughly independent of the
# host's core count and of how many documents are in flight:
#
#   * max_threads=1 forces sequential page rendering *within* one document,
#     capping it at ~1.3 GB (measured) instead of cores x ~0.7 GB.
#   * _OCR_SEMAPHORE caps how many documents OCR *at the same time*.
#
# Peak OCR memory ≈ 0.65 GB base + (_OCR_SEMAPHORE limit x max_threads) x ~0.66 GB.
# The default (2 x 1) targets ~2 GB, comfortably inside a 4 GB container while
# leaving the fast native-text path (no OCR) free to run at main.max-concurrency.
#
# NB: lowering OCR DPI via ImagePreprocessingConfig would shrink each bitmap, but
# that code path SIGABRTs in kreuzberg 4.9.9, so max_threads is the usable lever.
_OCR_MAX_THREADS = 1
_OCR_SEMAPHORE = asyncio.Semaphore(2)
_OCR_CONFIG = ExtractionConfig(
    force_ocr=True,
    ocr=OcrConfig(backend="tesseract", language="deu"),
    concurrency=ConcurrencyConfig(max_threads=_OCR_MAX_THREADS),
)


async def extract_pdf_text(pdf_path: Path, page_hint: int | None = None) -> tuple[str, str]:
    """Extract text and compute SHA256 hash from a PDF file.

    Args:
        pdf_path: Path to the PDF file.
        page_hint: Optional page number (from ``#page=N`` URL fragment).
            When set, page markers are inserted during extraction and only
            pages *page_hint* through *page_hint + 30* are returned.

    Returns (full_text, hash).  Falls back to OCR when:
    1. Normal extraction yields fewer than 64 characters, or
    2. The extracted text is garbled (broken font encoding detected).

    OCR uses Tesseract with German language for proper character recognition.
    """
    with open(pdf_path, "rb") as f:
        doc_hash = hashlib.file_digest(f, "sha256").hexdigest()

    if page_hint is not None:
        config = ExtractionConfig(force_ocr=False, pages=PageConfig(insert_page_markers=True))
    else:
        config = ExtractionConfig(force_ocr=False)

    result = await extract_file(pdf_path, config=config)
    text = result.content or ""

    if len(text) < MIN_TEXT_LENGTH:
        logger.warning("Normal text extraction yielded <%d chars, retrying with OCR", MIN_TEXT_LENGTH)
        async with _OCR_SEMAPHORE:
            ocr_result = await extract_file(pdf_path, config=_OCR_CONFIG)
        text = ocr_result.content or ""
    elif _is_garbled(text):
        logger.warning("Garbled text detected (broken font encoding), retrying with OCR")
        try:
            async with _OCR_SEMAPHORE:
                ocr_result = await extract_file(pdf_path, config=_OCR_CONFIG)
            ocr_text = ocr_result.content or ""
            if ocr_text and not _is_garbled(ocr_text):
                text = ocr_text
            else:
                logger.warning("OCR did not improve garbled text, keeping original")
        except Exception as exc:
            logger.warning("OCR retry failed (%s), keeping original garbled text", type(exc).__name__)

    if page_hint is not None:
        text = _extract_relevant_pages(text, page_hint)

    return text, doc_hash


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


async def extract_semantics(
    llm: LLMConnector,
    full_text: str,
    doktyp: Doktyp,
    model: str = "gpt-5-nano",
    dok_titel: str | None = None,
    drucksnr: str | None = None,
) -> dict:
    """Call LLM to extract structured metadata from document text.

    Returns a dict with keys like schlagworte, zusammenfassung, kurztitel,
    and optionally trojanergefahr/meinung/vorwort depending on doktyp.

    The full document text is sent unmodified — there is no token truncation.
    When *dok_titel* and/or *drucksnr* are given, a context header is prepended
    so the model summarizes only the passages that concern this specific bill,
    even in multi-topic plenary protocols (issue #32).

    Uses response_format=json_object with a json-repair fallback for models
    that occasionally emit malformed JSON, and validates score ranges
    post-extraction.
    """
    prompt = _prompt_for_doktyp(doktyp)
    context = _context_prefix(dok_titel, drucksnr)
    user_message = f"{context}{prompt}\n\n{full_text}"

    input_tokens = litellm.token_counter(model=model, text=user_message)
    logger.info("LLM call: %d input tokens, model=%s", input_tokens, model)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    async with _LLM_SEMAPHORE:
        response = await litellm.acompletion(
            model=model,
            api_key=llm.api_key,
            messages=messages,
            temperature=llm.temperature,
            timeout=llm.timeout_seconds,
            response_format={"type": "json_object"},
            num_retries=MAX_JSON_RETRIES,
        )

    content = response.choices[0].message.content
    data = _parse_llm_response(content)
    return _validate_scores(data)


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


def _redis_get(cache: BawueCache | None, key: str) -> str | None:
    """Look up LLM semantics in Redis. Returns JSON string or None."""
    if cache is None:
        return None
    return cache.get_raw(f"{_REDIS_CACHE_PREFIX}{key}", typehint="LLM Semantics")


def _redis_set(cache: BawueCache | None, key: str, value: str) -> None:
    """Store LLM semantics in Redis."""
    if cache is None:
        return
    cache.store_raw(f"{_REDIS_CACHE_PREFIX}{key}", value, typehint="LLM Semantics")


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
) -> EnrichmentResult:
    """Enrich a plain Dokument with PDF text extraction and LLM semantics.

    Takes an existing Dokument (as built by the scraper with empty volltext/hash)
    and returns an EnrichmentResult containing the enriched Dokument and an optional
    trojanergefahr score (Station-level field extracted by LLM). PARLIS metadata
    (titel, autoren, drucksnr, timestamps) is preserved.

    *vorgang_titel* is the bill title of the surrounding Vorgang. It is passed
    to the LLM as document-identity context so that multi-topic plenary protocols
    are summarized for the correct bill (issue #32); it falls back to the
    document's own title when not provided.

    *vorgang_vnr* is the surrounding Vorgang's initiating Drucksache. It is handed
    to the section extractor as a matching anchor (protocol documents carry no
    Drucksache of their own) and folded into the cache key so shared protocols stay
    Vorgang-specific (issue #35).

    Exception: for ``REDEPROTOKOLL``, *vorgang_titel*/*vorgang_vnr* are deliberately
    withheld. The backend stores one Dokument row per PDF hash, shared across every
    bill debated in that plenary sitting, so a per-bill narrowed summary can never
    survive more than one bill's upload — the last one silently overwrites the rest
    (issue #49). Withholding bill identity collapses the cache key to
    ``(doc_hash, doktyp)``, so every bill sharing the PDF reuses the same neutral,
    session-level summary instead of computing (and overwriting with) its own.

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
        pdf_path = await download_pdf(session, dok.link)

        # Extract text + hash, then normalize
        full_text, doc_hash = await extract_pdf_text(pdf_path, page_hint=page_hint)
        full_text = normalize_volltext(full_text)

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
        # docstring's "Exception" paragraph.
        if dok.typ == Doktyp.REDEPROTOKOLL:
            context_titel = None
            context_vorgang_vnr = None
            context_drucksnr = None
        else:
            context_titel = vorgang_titel or dok.titel
            context_vorgang_vnr = vorgang_vnr
            context_drucksnr = dok.drucksnr

        # Try LLM extraction (with two-tier cache deduplication, keyed by
        # doc_hash + prompt_hash). The prompt hash includes the document identity
        # so two bills sharing one protocol PDF don't collide (issue #32) — except
        # REDEPROTOKOLL, where it is deliberately omitted (issue #49).
        prompt_hash = _prompt_fingerprint(
            dok.typ, drucksnr=context_drucksnr, titel=context_titel, vorgang_vnr=context_vorgang_vnr
        )
        cache_key = _cache_key(doc_hash, prompt_hash)
        try:
            if cache_key in _hash_cache:
                logger.info(
                    "In-memory cache hit for %s/%s, skipping LLM call",
                    doc_hash[:12],
                    prompt_hash[:8],
                )
                semantics = _hash_cache[cache_key]
                if metrics is not None:
                    metrics.cache_hits += 1
            elif (cached_json := _redis_get(cache, cache_key)) is not None:
                logger.info(
                    "Redis cache hit for %s/%s, skipping LLM call",
                    doc_hash[:12],
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
                    llm, full_text, dok.typ, context_titel, context_vorgang_vnr or context_drucksnr
                )
                semantics = await extract_semantics(
                    llm, summary_input, dok.typ, model=model, dok_titel=context_titel, drucksnr=context_drucksnr
                )
                _hash_cache[cache_key] = semantics
                _redis_set(cache, cache_key, json.dumps(semantics))
                if metrics is not None:
                    metrics.success += 1

            return EnrichmentResult(
                dokument=Dokument(
                    titel=dok.titel,
                    volltext=full_text,
                    hash_=doc_hash,
                    typ=dok.typ,
                    zp_modifiziert=dok.zp_modifiziert,
                    zp_referenz=dok.zp_referenz,
                    zp_erstellt=dok.zp_erstellt,
                    link=dok.link,
                    autoren=dok.autoren,
                    drucksnr=dok.drucksnr,
                    zusammenfassung=_sanitize_llm_text(semantics.get("zusammenfassung")),
                    schlagworte=_sanitize_llm_strings(semantics.get("schlagworte")),
                    kurztitel=_sanitize_llm_text(semantics.get("kurztitel")),
                    meinung=semantics.get("meinung"),
                    vorwort=_sanitize_llm_text(semantics.get("vorwort")),
                ),
                trojanergefahr=semantics.get("trojanergefahr"),
            )
        except Exception:
            logger.warning("LLM extraction failed for %s, using text-only fallback", dok.link, exc_info=True)
            if metrics is not None:
                metrics.failed += 1
            return EnrichmentResult(
                dokument=Dokument(
                    titel=dok.titel,
                    volltext=full_text,
                    hash_=doc_hash,
                    typ=dok.typ,
                    zp_modifiziert=dok.zp_modifiziert,
                    zp_referenz=dok.zp_referenz,
                    zp_erstellt=dok.zp_erstellt,
                    link=dok.link,
                    autoren=dok.autoren,
                    drucksnr=dok.drucksnr,
                ),
            )

    except Exception:
        logger.warning("PDF download/extraction failed for %s, returning original document", dok.link, exc_info=True)
        return EnrichmentResult(dokument=dok)

    finally:
        if pdf_path is not None:
            pdf_path.unlink(missing_ok=True)
