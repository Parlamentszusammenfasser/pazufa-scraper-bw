"""PDF text: download, extraction with OCR fallback, cleanup and page windows (DD-015)."""

import asyncio
import hashlib
import logging
import re
import ssl
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import certifi
from kreuzberg import extract_file
from pazufa_corelib.normalization import normalize_volltext as _core_normalize_volltext
from pazufa_pdf_parser import Ocr, ParseOptions, PdfDocument, Severity, Source, parse

logger = logging.getLogger(__name__)

MIN_TEXT_LENGTH = 64


def loop_semaphore(slot: dict, limit: int) -> asyncio.Semaphore:
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


def paragraph_quality_score(text: str) -> float:
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

    # Signal 2: Latin Extended-A+B (0x0100-0x024F)
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

    1. A second garbled-paragraph filter using :func:`paragraph_quality_score`.
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
    text = "\n\n".join(p for p in paragraphs if paragraph_quality_score(p) >= 0.5)

    # Pass 2 (issue #20): reassemble words the extractor left hyphenated.
    return _LINEBREAK_HYPHEN_RE.sub("", text)


# ---------------------------------------------------------------------------
# Page-hint extraction for plenary protocols
# ---------------------------------------------------------------------------


# Pages of a #page=N window: the volltext of an anchored Fundstelle.
_WINDOW_PAGES = 30
# Real agenda pages hold ≥ 3584 chars (WP17/18); a page with only its header ~95.
_MIN_AGENDA_PAGE_CHARS = 500


def parse_page_hint(url: str) -> int | None:
    """Extract page number from a URL's ``#page=N`` fragment."""
    fragment = urlparse(url).fragment
    match = re.match(r"^page=(\d+)$", fragment)
    return int(match.group(1)) if match else None


def agenda_text(doc: PdfDocument, page_hint: int | None, druckseiten: list[int] | None) -> tuple[str, list[int]] | None:
    """The agenda item's pages of a parsed protocol, normalized, with their PDF numbers.

    The anchor is the item's first printed page (verified on 489 WP17/18 Fundstellen), so
    the PDF page is the printed page plus a fixed offset (issue #74). Capped at the volltext
    window: the LLM never reads more than before. None without anchor or pages, or if a
    page is missing, carries another printed number, or holds little more than its header:
    then the caller keeps the whole window.
    """
    if not page_hint or not druckseiten:
        return None
    offset = page_hint - druckseiten[0]
    printed = {p + offset: str(p) for p in druckseiten if p + offset < page_hint + _WINDOW_PAGES}  # PDF → printed
    by_number = {page.number: page.printed_page for page in doc.pages}
    if not all(by_number.get(pdf) == label for pdf, label in printed.items()):
        return None
    pages = sorted(printed)
    agenda = normalize_volltext(doc.text(pages=pages))
    return (agenda, pages) if len(agenda) >= _MIN_AGENDA_PAGE_CHARS * len(pages) else None


# ---------------------------------------------------------------------------
# PDF download + text extraction
# ---------------------------------------------------------------------------


class PdfNotAvailableError(RuntimeError):
    """Non-200 response for a PDF, e.g. a Plenarprotokoll not published yet.

    Expected, so callers log it without a traceback (issue #69)."""

    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"PDF download returned HTTP {status}: {url}")
        self.status = status


# Bounds memory (Cloud Run's /tmp is RAM too); the largest WP17 PDF has 3.4 MB (issue #182).
MAX_PDF_BYTES = 50 * 1024 * 1024


async def download_pdf(session, url: str) -> Path:
    """Download a PDF of at most MAX_PDF_BYTES to a temporary file via aiohttp session."""
    clean_url = url.split("#")[0] if "#" in url else url
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    async with session.get(clean_url, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=60)) as response:
        if response.status != 200:
            raise PdfNotAvailableError(response.status, clean_url)
        content = bytearray()
        async for chunk in response.content.iter_any():
            content += chunk
            if len(content) > MAX_PDF_BYTES:
                raise ValueError(f"PDF larger than {MAX_PDF_BYTES} bytes: {clean_url}")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(content)
    return Path(tmp.name)


async def extract_pdf_text(pdf_path: Path) -> str:
    """The PDF's text via kreuzberg, without OCR: only for the Tagesordnung, whose speaking-time
    column pazufa-parser still mixes into the item titles (issue #218, pdf-parser#8)."""
    return (await extract_file(pdf_path)).content or ""


# OCR is the memory-heavy step. pazufa-parser OCRs page by page and only the pages that need it (DD-015);
# one Ocr object caps the tesseract processes of all parse() calls in the process (issue #217).
_OCR_LIMIT = 2
# OCR time grows with pages, not bytes: 0.76 s/page (17/1104); the largest WP17 PDF has 225 pages (issue #182).
MAX_OCR_PAGES = 500
_PARSE_OPTIONS = ParseOptions(ocr_engine=Ocr(max_parallel=_OCR_LIMIT), max_ocr_pages=MAX_OCR_PAGES)
_PARSE_SLOT: dict = {}


async def _parse(pdf: Source, options: ParseOptions) -> PdfDocument:
    # A parse holds a default-executor thread while it waits for OCR; unlimited, they starve DNS and downloads.
    async with loop_semaphore(_PARSE_SLOT, _OCR_LIMIT):
        doc = await asyncio.to_thread(parse, pdf, options)
    for issue in doc.report.issues:  # the parser does not log: OCR failures, the OCR page limit, ...
        if issue.severity >= Severity.WARNING:
            # A failed OCR is final: no retry, the page stays without text (issue #218).
            level = logging.ERROR if issue.severity >= Severity.ERROR else logging.WARNING
            logger.log(level, "pazufa-parser %s (page %s): %s", issue.code, issue.page, issue.message)
    return doc


async def parse_pdf(pdf: Source, page_hint: int | None = None) -> PdfDocument:
    """The parsed PDF (a path or its bytes); with a ``#page=N`` hint only its window of _WINDOW_PAGES pages.

    A window with fewer than MIN_TEXT_LENGTH chars (a blank anchor page, or one past the
    end) falls back to the whole document (DD-038, issue #50).
    """
    if page_hint:  # #page=0 is no page: ParseOptions(first_page=0) raises
        window = await _parse(
            pdf, replace(_PARSE_OPTIONS, first_page=page_hint, last_page=page_hint + _WINDOW_PAGES - 1)
        )
        if len(window.text().strip()) >= MIN_TEXT_LENGTH:
            return window
        logger.info(
            "Page-hint window (page %d) yielded <%d usable chars, falling back to full text",
            page_hint,
            MIN_TEXT_LENGTH,
        )
    return await _parse(pdf, _PARSE_OPTIONS)


async def read_pdf(session, url: str) -> str:
    """A PDF's normalized volltext, for Entwurf matching (DD-079)."""
    pdf_path = await download_pdf(session, url)
    try:
        return normalize_volltext((await parse_pdf(pdf_path)).text())
    finally:
        pdf_path.unlink(missing_ok=True)


def pdf_sha1(pdf_path: Path) -> str:
    """SHA-1 of the PDF file: the backend cross-references documents by it (issue #41)."""
    with open(pdf_path, "rb") as f:
        return hashlib.file_digest(f, "sha1").hexdigest()


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
