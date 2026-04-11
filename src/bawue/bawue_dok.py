"""Document enrichment module: PDF text extraction + LLM semantic extraction.

Downloads PDFs, extracts text via kreuzberg, then uses collector-core's LLMConnector
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
import unicodedata
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

import aiohttp
import certifi
import litellm
from collector.scrapercache import ScraperCache
from collector_core import LLMConnector
from kreuzberg import ExtractionConfig, OcrConfig, PageConfig, extract_file
from openapi_client.models.doktyp import Doktyp
from openapi_client.models.dokument import Dokument

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
        return [
            "",
            "LLM enrichment:",
            f"  Success:     {self.success}",
            f"  Failed:      {self.failed}",
            f"  Cache hits:  {self.cache_hits}",
            f"  Total:       {self.total}",
        ]


MAX_JSON_RETRIES = 3
MIN_TEXT_LENGTH = 64
DEFAULT_TRUNCATE_TOKENS = 12000
_LLM_SEMAPHORE = asyncio.Semaphore(3)

# Two-tier LLM semantics cache:
# 1. _hash_cache (in-memory dict): fast intra-cycle deduplication, cleared each cycle
#    via clear_hash_cache() to prevent unbounded memory growth.
# 2. Redis (via ScraperCache): persistent cross-cycle deduplication, survives restarts.
#    Key format: "llm-semantics:{sha256_hash}", value: JSON-serialized semantics dict.
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

_DOKTYP_PROMPT_MAP: dict[Doktyp, str] = {
    Doktyp.ENTWURF: BODY_PROMPT_ENTWURF,
    Doktyp.PREPARL_MINUS_ENTWURF: BODY_PROMPT_ENTWURF,
    Doktyp.STELLUNGNAHME: BODY_PROMPT_STELLUNGNAHME,
    Doktyp.BESCHLUSSEMPF: BODY_PROMPT_BESCHLUSSEMPF,
}


def _prompt_for_doktyp(doktyp: Doktyp) -> str:
    """Return the appropriate LLM prompt for a given document type."""
    return _DOKTYP_PROMPT_MAP.get(doktyp, BODY_PROMPT_GENERIC)


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

_C1_CONTROL_RE = re.compile(r"[\x80-\x9f]")
_TRAILING_WHITESPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)


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
    """Normalize extracted PDF text: fix encoding, strip garbled sections, escape XSS.

    Applied after PDF text extraction, before LLM and API submission.
    """
    if not text:
        return text

    # 1. Unicode NFKC normalization (e.g. ﬁ ligature → fi)
    text = unicodedata.normalize("NFKC", text)

    # 2. Join soft-hyphenated words: PARLIS PDFs use U+0002 (STX) as soft hyphen
    # e.g. "ausgezeich\u0002net" -> "ausgezeichnet"
    text = text.replace("\x02", "")

    # 2.5. Remove U+FFFD replacement characters (encoding failures)
    text = text.replace("\ufffd", "")

    # 3. Strip C1 control characters (0x80-0x9F)
    text = _C1_CONTROL_RE.sub("", text)

    # 4. Normalize line endings: \r\n → \n, lone \r → \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 5. Remove garbled paragraphs (split on double-newline, score each).
    #    Also collapses excessive blank lines: split on \n\n+ and rejoin with \n\n.
    paragraphs = re.split(r"\n\n+", text)
    clean_paragraphs = [p for p in paragraphs if _paragraph_quality_score(p) >= 0.5]
    text = "\n\n".join(clean_paragraphs)

    # 6. Strip trailing whitespace per line
    text = _TRAILING_WHITESPACE_RE.sub("", text)

    # 7. Neutralize angle brackets (XSS prevention): < and > to guillemets
    text = text.replace("<", "\u2039").replace(">", "\u203a")

    return text.strip()


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
# Text truncation
# ---------------------------------------------------------------------------


def truncate_text(text: str, max_tokens: int, model: str = "gpt-5-nano") -> str:
    """Truncate text to fit within a token budget.

    Args:
        text: The input text to (possibly) truncate.
        max_tokens: Maximum number of tokens. 0 means no truncation.
        model: Model name for tokenizer selection (e.g. "gpt-5-nano").

    Returns:
        The original text if within budget, otherwise truncated text.
    """
    if max_tokens <= 0:
        return text

    token_count = litellm.token_counter(model=model, text=text)
    if token_count <= max_tokens:
        return text

    tokens = litellm.encode(model=model, text=text)
    truncated_tokens = tokens[:max_tokens]
    truncated = litellm.decode(model=model, tokens=truncated_tokens)
    logger.info("Truncated text from %d to %d tokens", token_count, max_tokens)
    return truncated


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

    return "\n\n".join(relevant)


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


_OCR_CONFIG = ExtractionConfig(
    force_ocr=True,
    ocr=OcrConfig(backend="tesseract", language="deu"),
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
        ocr_result = await extract_file(pdf_path, config=_OCR_CONFIG)
        text = ocr_result.content or ""
    elif _is_garbled(text):
        logger.warning("Garbled text detected (broken font encoding), retrying with OCR")
        try:
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


async def extract_semantics(
    llm: LLMConnector,
    full_text: str,
    doktyp: Doktyp,
    model: str = "gpt-5-nano",
    max_tokens: int = DEFAULT_TRUNCATE_TOKENS,
) -> dict:
    """Call LLM to extract structured metadata from document text.

    Returns a dict with keys like schlagworte, zusammenfassung, kurztitel,
    and optionally trojanergefahr/meinung/vorwort depending on doktyp.

    Uses response_format=json_object for guaranteed valid JSON output,
    and validates score ranges post-extraction.
    """
    text = truncate_text(full_text, max_tokens=max_tokens, model=model)

    prompt = _prompt_for_doktyp(doktyp)
    user_message = f"{prompt}\n\n{text}"

    input_tokens = litellm.token_counter(model=model, text=user_message)
    logger.info("LLM call: %d input tokens, model=%s", input_tokens, model)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    api_base = getattr(llm, "api_base", None)
    async with _LLM_SEMAPHORE:
        response = await litellm.acompletion(
            model=model,
            api_key=llm.api_key,
            api_base=api_base,
            messages=messages,
            temperature=llm.temperature,
            timeout=llm.timeout_seconds,
            response_format={"type": "json_object"},
            num_retries=MAX_JSON_RETRIES,
        )

    content = response.choices[0].message.content
    data = json.loads(content)
    return _validate_scores(data)


# ---------------------------------------------------------------------------
# Redis cache helpers
# ---------------------------------------------------------------------------


def _redis_get(cache: ScraperCache | None, doc_hash: str) -> str | None:
    """Look up LLM semantics in Redis. Returns JSON string or None."""
    if cache is None:
        return None
    return cache.get_raw(f"{_REDIS_CACHE_PREFIX}{doc_hash}", typehint="LLM Semantics")


def _redis_set(cache: ScraperCache | None, doc_hash: str, value: str) -> None:
    """Store LLM semantics in Redis."""
    if cache is None:
        return
    cache.store_raw(f"{_REDIS_CACHE_PREFIX}{doc_hash}", value, typehint="LLM Semantics")


# ---------------------------------------------------------------------------
# Main enrichment entry point
# ---------------------------------------------------------------------------


async def enrich_dokument(
    session,
    llm: LLMConnector,
    dok: Dokument,
    model: str = "gpt-5-nano",
    max_tokens: int = DEFAULT_TRUNCATE_TOKENS,
    metrics: LLMMetrics | None = None,
    cache: ScraperCache | None = None,
) -> EnrichmentResult:
    """Enrich a plain Dokument with PDF text extraction and LLM semantics.

    Takes an existing Dokument (as built by the scraper with empty volltext/hash)
    and returns an EnrichmentResult containing the enriched Dokument and an optional
    trojanergefahr score (Station-level field extracted by LLM). PARLIS metadata
    (titel, autoren, drucksnr, timestamps) is preserved.

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

        # Try LLM extraction (with two-tier cache deduplication)
        try:
            if doc_hash in _hash_cache:
                logger.info("In-memory cache hit for %s, skipping LLM call", doc_hash[:12])
                semantics = _hash_cache[doc_hash]
                if metrics is not None:
                    metrics.cache_hits += 1
            elif (cached_json := _redis_get(cache, doc_hash)) is not None:
                logger.info("Redis cache hit for %s, skipping LLM call", doc_hash[:12])
                semantics = json.loads(cached_json)
                _hash_cache[doc_hash] = semantics
                if metrics is not None:
                    metrics.cache_hits += 1
            else:
                semantics = await extract_semantics(llm, full_text, dok.typ, model=model, max_tokens=max_tokens)
                _hash_cache[doc_hash] = semantics
                _redis_set(cache, doc_hash, json.dumps(semantics))
                if metrics is not None:
                    metrics.success += 1

            return EnrichmentResult(
                dokument=Dokument(
                    titel=dok.titel,
                    volltext=full_text,
                    hash=doc_hash,
                    typ=dok.typ,
                    zp_modifiziert=dok.zp_modifiziert,
                    zp_referenz=dok.zp_referenz,
                    zp_erstellt=dok.zp_erstellt,
                    link=dok.link,
                    autoren=dok.autoren,
                    drucksnr=dok.drucksnr,
                    zusammenfassung=semantics.get("zusammenfassung"),
                    schlagworte=semantics.get("schlagworte"),
                    kurztitel=semantics.get("kurztitel"),
                    meinung=semantics.get("meinung"),
                    vorwort=semantics.get("vorwort"),
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
                    hash=doc_hash,
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
