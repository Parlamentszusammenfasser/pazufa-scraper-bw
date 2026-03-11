"""Parser for Tagesordnung (agenda) PDF text content."""

import re
import subprocess
from dataclasses import dataclass


@dataclass
class Top:
    nummer: str
    titel: str


_STRIP_PATTERNS = re.compile(
    r"^Drucksache\s+\d+/\d+|^(angenommen|abgelehnt|überwiesen)\b",
    re.IGNORECASE,
)


def parse_tops(text: str) -> list[Top]:
    """Parse numbered agenda items from Tagesordnung text."""
    if not text.strip():
        return []

    parts = re.split(r"^(\d+)\.\s+", text, flags=re.MULTILINE)
    # parts = [preamble, nummer, content, nummer, content, ...]
    if len(parts) < 3:
        return []

    tops = []
    for i in range(1, len(parts) - 1, 2):
        nummer = parts[i]
        content = parts[i + 1]
        lines = [
            line
            for line in content.splitlines()
            if line.strip() and not _STRIP_PATTERNS.match(line.strip())
        ]
        titel = " ".join(line.strip() for line in lines)
        tops.append(Top(nummer=nummer, titel=titel))

    return tops


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using pdftotext."""
    result = subprocess.run(["pdftotext", "-", "-"], input=pdf_bytes, capture_output=True)
    return result.stdout.decode("utf-8")
