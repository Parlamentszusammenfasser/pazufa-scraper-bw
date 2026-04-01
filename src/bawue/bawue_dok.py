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
import tempfile
import unicodedata
from pathlib import Path
from typing import NamedTuple

import litellm
from collector_core import LLMConnector
from kreuzberg import ExtractionConfig, extract_file
from openapi_client.models.doktyp import Doktyp
from openapi_client.models.dokument import Dokument

logger = logging.getLogger(__name__)


class EnrichmentResult(NamedTuple):
    """Result of document enrichment: enriched Dokument + optional Station-level fields."""

    dokument: Dokument
    trojanergefahr: int | None = None


MAX_JSON_RETRIES = 3
MIN_TEXT_LENGTH = 64
DEFAULT_TRUNCATE_TOKENS = 12000
_LLM_SEMAPHORE = asyncio.Semaphore(3)

# In-memory cache: SHA256 hash → LLM semantics dict (per-run deduplication)
_hash_cache: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# LLM Prompts — one per Doktyp group, German, body-only (no header extraction)
# ---------------------------------------------------------------------------

BODY_PROMPT_ENTWURF = """\
Extrahiere aus dem folgenden Gesetzestext die folgenden Informationen als JSON:
{"schlagworte": ["Liste inhaltlich bedeutsamer Schlagworte"],
 "zusammenfassung": "Zusammenfassung in 150-250 Worten",
 "kurztitel": "Kurzer verständlicher Titel in einfacher Sprache",
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
# Text normalization
# ---------------------------------------------------------------------------

_C1_CONTROL_RE = re.compile(r"[\x80-\x9f]")
_EXCESSIVE_BLANK_LINES_RE = re.compile(r"\n{3,}")
_TRAILING_WHITESPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)


def _paragraph_quality_score(text: str) -> float:
    """Score a paragraph's text quality from 0.0 (garbled) to 1.0 (clean).

    Detects broken PDF font encoding via three signals:
    - C1 control characters (0x80-0x9F)
    - Latin Extended-B characters (0x0180-0x024F)
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

    # Signal 2: Latin Extended-B (ůĂƐƐĞŶ-ŵŝƚ patterns from broken ToUnicode)
    ext_count = sum(1 for c in text if 0x0180 <= ord(c) <= 0x024F)

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

    # 2. Strip C1 control characters (0x80-0x9F)
    text = _C1_CONTROL_RE.sub("", text)

    # 3. Normalize line endings: \r\n → \n, lone \r → \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 4. Remove garbled paragraphs (split on double-newline, score each)
    paragraphs = re.split(r"\n\n+", text)
    clean_paragraphs = [p for p in paragraphs if _paragraph_quality_score(p) >= 0.5]
    text = "\n\n".join(clean_paragraphs)

    # 5. Collapse excessive blank lines (3+ → 2)
    text = _EXCESSIVE_BLANK_LINES_RE.sub("\n\n", text)

    # 6. Strip trailing whitespace per line
    text = _TRAILING_WHITESPACE_RE.sub("", text)

    # 7. Neutralize angle brackets (XSS prevention): < and > to guillemets
    text = text.replace("<", "\u2039").replace(">", "\u203a")

    return text.strip()


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
# PDF download + text extraction
# ---------------------------------------------------------------------------


async def download_pdf(session, url: str) -> Path:
    """Download a PDF to a temporary file via aiohttp session."""
    async with session.get(url) as response:
        if response.status != 200:
            raise RuntimeError(f"PDF download failed with status {response.status}: {url}")
        content = await response.read()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(content)
    return Path(tmp.name)


async def extract_pdf_text(pdf_path: Path) -> tuple[str, str]:
    """Extract text and compute SHA256 hash from a PDF file.

    Returns (full_text, hash). Falls back to OCR if normal extraction yields <64 chars.
    """
    with open(pdf_path, "rb") as f:
        doc_hash = hashlib.file_digest(f, "sha256").hexdigest()

    result = await extract_file(pdf_path, config=ExtractionConfig(force_ocr=False))
    text = result.content or ""

    if len(text) < MIN_TEXT_LENGTH:
        logger.warning("Normal text extraction yielded <%d chars, retrying with OCR", MIN_TEXT_LENGTH)
        ocr_result = await extract_file(pdf_path, config=ExtractionConfig(force_ocr=True))
        text = ocr_result.content or ""

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
    and optionally trojanergefahr/meinung depending on doktyp.

    Retries up to MAX_JSON_RETRIES times on JSON parse failures.
    """
    text = truncate_text(full_text, max_tokens=max_tokens, model=model)

    prompt = _prompt_for_doktyp(doktyp)
    user_message = f"{prompt}\n\n{text}"

    input_tokens = litellm.token_counter(model=model, text=user_message)
    logger.info("LLM call: %d input tokens, model=%s", input_tokens, model)

    for attempt in range(MAX_JSON_RETRIES):
        async with _LLM_SEMAPHORE:
            response = await llm.generate_text(user_message)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            logger.warning(
                "LLM response not valid JSON (attempt %d/%d)",
                attempt + 1,
                MAX_JSON_RETRIES,
            )
            if attempt == MAX_JSON_RETRIES - 1:
                raise

    raise RuntimeError("Should not reach here")  # pragma: no cover


# ---------------------------------------------------------------------------
# Main enrichment entry point
# ---------------------------------------------------------------------------


async def enrich_dokument(
    session,
    llm: LLMConnector,
    dok: Dokument,
    model: str = "gpt-5-nano",
    max_tokens: int = DEFAULT_TRUNCATE_TOKENS,
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
        pdf_path = await download_pdf(session, dok.link)

        # Extract text + hash, then normalize
        full_text, doc_hash = await extract_pdf_text(pdf_path)
        full_text = normalize_volltext(full_text)

        # Try LLM extraction (with hash cache deduplication)
        try:
            if doc_hash in _hash_cache:
                logger.info("Hash cache hit for %s, skipping LLM call", doc_hash[:12])
                semantics = _hash_cache[doc_hash]
            else:
                semantics = await extract_semantics(llm, full_text, dok.typ, model=model, max_tokens=max_tokens)
                _hash_cache[doc_hash] = semantics

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
                ),
                trojanergefahr=semantics.get("trojanergefahr"),
            )
        except Exception:
            logger.warning("LLM extraction failed for %s, using text-only fallback", dok.link)
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
        logger.warning("PDF download/extraction failed for %s, returning original document", dok.link)
        return EnrichmentResult(dokument=dok)

    finally:
        if pdf_path is not None:
            pdf_path.unlink(missing_ok=True)
