"""PDF text: download, extraction with OCR fallback, cleanup and page windows (DD-015)."""

import asyncio
import hashlib
import logging
import re
import ssl
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import certifi
from kreuzberg import ConcurrencyConfig, ExtractionConfig, OcrConfig, PageConfig, extract_file
from pazufa_corelib.normalization import normalize_volltext as _core_normalize_volltext

logger = logging.getLogger(__name__)

MIN_TEXT_LENGTH = 64


def _loop_semaphore(slot: dict, limit: int) -> asyncio.Semaphore:
    """The current event loop's semaphore, shared by all scrapers of a cycle.

    A semaphore binds to the first loop that waits on it, and one process can run
    several loops (the tests do; __main__ once did, one per cycle, issue #101). Only the current loop's is
    kept: a waited-on semaphore references its loop, so keeping more would leak.
    """
    loop = asyncio.get_running_loop()
    if slot.get("loop") is not loop:
        slot.update(loop=loop, sem=asyncio.Semaphore(limit))
    return slot["sem"]


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

    Delegates the shared cleaning pipeline (NFKC, invisible/control-char,
    HTML-markup and HTML-entity stripping, garbled-paragraph removal, ``-\n`` line-break
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
# Garbled text detection
# ---------------------------------------------------------------------------

_GARBLED_LATIN_EXT_THRESHOLD = 0.05  # 5% of alpha chars in Latin Extended → garbled
# C1 controls never occur in properly extracted text, so any recurring share is a
# defect. 0.5% sits an order of magnitude above the "one stray char" noise floor
# and well below the ~1.0-2.0% measured on real Muster-2 documents (DS 17/1201).
_GARBLED_C1_THRESHOLD = 0.005


def _is_garbled(text: str) -> bool:
    """Detect garbled PDF text from broken font encodings (DD-015).

    Flags both patterns DD-015 documents, because both are recoverable by the
    OCR retry that this function gates:

    * *Muster 1* — Latin Extended characters (U+0100-U+024F) above 5% of all
      alphabetic characters. They appear when a PDF font lacks a proper
      ToUnicode CMap and kreuzberg maps glyph IDs to wrong Unicode code points.
    * *Muster 2* — C1 control characters (0x80-0x9F) above 0.5%, the by-product
      of the constant ASCII shift (``bQGHUXQJVDQWUDJ`` → ``Änderungsantrag``).
      Mirrors ``_paragraph_quality_score``'s signal 1, but one layer earlier:
      that scorer only *strips* such paragraphs, leaving the document empty
      (GitHub issue #20).

    Both ratios are measured over the whole document, so a long, mostly clean
    PDF with only a few garbled pages stays below the thresholds; those are
    still caught (and dropped) by ``normalize_volltext``.
    """
    if not text or len(text) < MIN_TEXT_LENGTH:
        return False

    alpha_count = 0
    latin_ext_count = 0
    c1_count = 0
    for c in text:
        code = ord(c)
        if c.isalpha():
            alpha_count += 1
            if 0x0100 <= code <= 0x024F:
                latin_ext_count += 1
        elif 0x80 <= code <= 0x9F:
            c1_count += 1

    if alpha_count == 0:
        return False

    return (latin_ext_count / alpha_count) > _GARBLED_LATIN_EXT_THRESHOLD or (
        c1_count / alpha_count
    ) > _GARBLED_C1_THRESHOLD


def _usable_length(text: str) -> int:
    """How many characters of *text* survive :func:`normalize_volltext`.

    ``_is_garbled`` measures the whole document, so it also fires on PDFs where
    only part of the text is broken (measured: ~15 % of the affected corpus keeps
    40-97 % of its content). OCR replaces *everything*, including the pages that
    extracted cleanly, and is itself lossy on tables — so "not garbled" alone is
    too weak a bar for accepting the OCR result. Comparing what actually survives
    normalization on both sides makes the swap a measured decision.
    """
    return len(normalize_volltext(text))


# ---------------------------------------------------------------------------
# Page-hint extraction for plenary protocols
# ---------------------------------------------------------------------------


# kreuzberg's marker template is "\n\n<!-- PAGE N -->\n\n", but the surrounding
# "\n\n" only exists when a marker has page content on both sides. The marker
# for the document's first page has nothing before it, and the marker for a
# trailing empty page has nothing after it — in both cases the fixed \n\n is
# missing, so anchor to the string boundary as an alternative (issue #27: this
# silently dropped page 1's content for every #page=1-anchored document,
# substituting whatever page followed it instead of falling back).
_PAGE_MARKER_RE = re.compile(r"(?:\A|\n\n)<!-- PAGE (\d+) -->(?:\n\n|\Z)")


# Pages of a #page=N window: the volltext of an anchored Fundstelle.
_WINDOW_PAGES = 30
# Real agenda pages hold ≥ 3584 chars (WP17/18); a page with only its header ~95.
_MIN_AGENDA_PAGE_CHARS = 500


def _parse_page_hint(url: str) -> int | None:
    """Extract page number from a URL's ``#page=N`` fragment."""
    fragment = urlparse(url).fragment
    match = re.match(r"^page=(\d+)$", fragment)
    return int(match.group(1)) if match else None


def _page_map(text: str) -> dict[int, str]:
    """PDF page number → page text, from kreuzberg's page markers (empty without markers)."""
    parts = _PAGE_MARKER_RE.split(text)
    # parts alternates: [text_before_first_marker, page_num, text, page_num, text, ...]
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _agenda_text(text: str, page_hint: int | None, druckseiten: list[int] | None) -> tuple[str, list[int]] | None:
    """The agenda item's pages of a marked protocol text, normalized, with their PDF numbers.

    The anchor is the item's first printed page (verified on 489 WP17/18 Fundstellen), so
    the PDF page is the printed page plus a fixed offset (issue #74). Capped at the volltext
    window: the LLM never reads more than before. None without anchor or pages, or if a
    page is missing, does not start with its printed number (2635 of 2635 real pages do),
    or holds little more than its header: then the caller keeps the whole window.
    """
    if not page_hint or not druckseiten:
        return None
    offset = page_hint - druckseiten[0]
    printed = {p + offset: p for p in druckseiten if p + offset < page_hint + _WINDOW_PAGES}  # PDF → printed
    page_map = _page_map(text)
    if not all(re.match(rf"\s*{p}(?!\d)", page_map.get(pdf, "")) for pdf, p in printed.items()):
        return None
    pages = sorted(printed)
    agenda = normalize_volltext("\n\n".join(page_map[p] for p in pages))
    return (agenda, pages) if len(agenda) >= _MIN_AGENDA_PAGE_CHARS * len(pages) else None


def _extract_relevant_pages(text: str, start_page: int, max_pages: int = _WINDOW_PAGES) -> str:
    """Extract text from *start_page* through *start_page + max_pages* using page markers.

    Falls back to the full text when no markers are found or when the
    requested page range lies beyond the document.
    """
    page_map = _page_map(text)
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


class PdfNotAvailableError(RuntimeError):
    """Non-200 response for a PDF, e.g. a Plenarprotokoll not published yet.

    Expected, so callers log it without a traceback (issue #69)."""

    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"PDF download returned HTTP {status}: {url}")
        self.status = status


async def download_pdf(session, url: str) -> Path:
    """Download a PDF to a temporary file via aiohttp session."""
    clean_url = url.split("#")[0] if "#" in url else url
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    async with session.get(clean_url, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=60)) as response:
        if response.status != 200:
            raise PdfNotAvailableError(response.status, clean_url)
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
#   * _ocr_semaphore() caps how many documents OCR *at the same time*.
#
# Peak OCR memory ≈ 0.65 GB base + (_OCR_LIMIT x max_threads) x ~0.66 GB.
# The default (2 x 1) targets ~2 GB, comfortably inside a 4 GB container while
# leaving the fast native-text path (no OCR) free to run at main.max-concurrency.
#
# NB: lowering OCR DPI via ImagePreprocessingConfig would shrink each bitmap, but
# that code path SIGABRTs in kreuzberg 4.9.9, so max_threads is the usable lever.
_OCR_MAX_THREADS = 1
_OCR_LIMIT = 2
_OCR_SLOT: dict = {}


def _ocr_semaphore() -> asyncio.Semaphore:
    return _loop_semaphore(_OCR_SLOT, _OCR_LIMIT)


_OCR_CONFIG = ExtractionConfig(
    force_ocr=True,
    ocr=OcrConfig(backend="tesseract", language="deu"),
    concurrency=ConcurrencyConfig(max_threads=_OCR_MAX_THREADS),
)
# OCR replaces the whole extracted text, so it has to carry the page markers too
# when a ``#page=N`` hint is in play — otherwise _extract_relevant_pages finds
# none and silently returns the entire document, giving every Antrag of a
# Sammeldrucksache the same volltext (GitHub issue #20).
_OCR_CONFIG_PAGE_MARKERS = ExtractionConfig(
    force_ocr=True,
    ocr=OcrConfig(backend="tesseract", language="deu"),
    concurrency=ConcurrencyConfig(max_threads=_OCR_MAX_THREADS),
    pages=PageConfig(insert_page_markers=True),
)


async def extract_pdf_text(pdf_path: Path, page_markers: bool = False) -> tuple[str, str]:
    """Extract text and compute SHA256 hash from a PDF file.

    Args:
        pdf_path: Path to the PDF file.
        page_markers: Insert kreuzberg's page markers, also on the OCR retry, so the
            caller can cut a ``#page=N`` window (``_extract_relevant_pages``) or a
            protocol's agenda pages (``_agenda_text``, issue #74).

    Returns (full_text, hash).  Falls back to OCR when:
    1. Normal extraction yields fewer than 64 characters, or
    2. The extracted text is garbled (broken font encoding detected).

    OCR uses Tesseract with German language for proper character recognition.
    """
    with open(pdf_path, "rb") as f:
        doc_hash = hashlib.file_digest(f, "sha256").hexdigest()

    if page_markers:
        config = ExtractionConfig(force_ocr=False, pages=PageConfig(insert_page_markers=True))
        ocr_config = _OCR_CONFIG_PAGE_MARKERS
    else:
        config = ExtractionConfig(force_ocr=False)
        ocr_config = _OCR_CONFIG

    result = await extract_file(pdf_path, config=config)
    text = result.content or ""

    if len(text) < MIN_TEXT_LENGTH:
        logger.warning("Normal text extraction yielded <%d chars, retrying with OCR", MIN_TEXT_LENGTH)
        async with _ocr_semaphore():
            ocr_result = await extract_file(pdf_path, config=ocr_config)
        text = ocr_result.content or ""
    elif _is_garbled(text):
        logger.warning("Garbled text detected (broken font encoding), retrying with OCR")
        try:
            async with _ocr_semaphore():
                ocr_result = await extract_file(pdf_path, config=ocr_config)
            ocr_text = ocr_result.content or ""
            if ocr_text and not _is_garbled(ocr_text) and _usable_length(ocr_text) > _usable_length(text):
                text = ocr_text
            else:
                logger.warning("OCR did not improve garbled text, keeping original")
        except Exception as exc:
            logger.warning("OCR retry failed (%s), keeping original garbled text", type(exc).__name__)

    return text, doc_hash


_DRUCKSNR_RE = re.compile(r"(\d+)/(\d+)")
# Landtag BW prints the issue date once, in the page-1 footer: "Ausgegeben: 10.12.2021",
# on Entwürfe/Anträge "Eingegangen: 19.10.2021/Ausgegeben: 20.10.2021". Where the footer
# lands in the extracted text varies (native vs. OCR), so the first occurrence is taken.
_AUSGEGEBEN_RE = re.compile(r"Ausgegeben:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")
# 150 staging Dokumente: issued 0 to 42 days after the PARLIS date (DD-061).
_MAX_AUSGABE_VERZUG = timedelta(days=90)


def ausgegeben_datum(text: str, drucksnr: str | None, parlis_datum: datetime) -> datetime | None:
    """The issue date printed in the page-1 footer of Drucksache *drucksnr*, or None.

    PARLIS only gives the session/Eingang date, which precedes the issue date by up to
    weeks (issue #23, DD-061). The text must carry the document's own header, and a date
    outside ``[parlis_datum, parlis_datum + 90 days]`` is dropped as a misread.
    """
    nummer = _DRUCKSNR_RE.fullmatch(drucksnr or "")
    if nummer is None:
        return None
    wp, nr = nummer.groups()
    if not re.search(rf"Drucksache\s+{wp}\s*/\s*{nr}(?!\d)", text):
        return None
    match = _AUSGEGEBEN_RE.search(text)
    if match is None:
        return None
    day, month, year = map(int, match.groups())
    try:
        datum = datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        datum = None
    if datum is None or not parlis_datum <= datum <= parlis_datum + _MAX_AUSGABE_VERZUG:
        logger.warning("Drucksache %s: implausible Ausgegeben date %s.%s.%s, keeping PARLIS", drucksnr, *match.groups())
        return None
    return datum
