"""Issue #180 (DD-079): which portal draft a Landesregierung bill grew from, decided by its text.

The texts are the real WP 18 drafts and Drucksachen, extracted as the scraper does
(``extract_pdf_text`` + ``normalize_volltext``).
"""

import gzip
from datetime import date
from functools import cache
from pathlib import Path

import pytest

from bawue.entwurf_match import containment, in_window, pick, shingles

TEXTS = Path(__file__).parent.parent / "fixtures" / "entwuerfe"


@cache
def _shingles(name: str) -> frozenset[int]:
    return shingles(gzip.decompress((TEXTS / f"{name}.txt.gz").read_bytes()).decode())


DRUCKSACHE_OF = {
    "entwurf_bvanpg_2026": "drucksache_18_623",
    "entwurf_gewhag": "drucksache_18_692",
    "entwurf_hbeglg_2027": "drucksache_18_701",
}
# Last salary law: reads much like the 2026 one, the hardest wrong candidate.
FAMILY = ["entwurf_bvanpg_2024"]


def _scores(drucksache: str, entwuerfe: list[str]) -> dict[str, float]:
    return {e: containment(_shingles(e), _shingles(drucksache)) for e in entwuerfe}


@pytest.mark.parametrize(("entwurf", "drucksache"), DRUCKSACHE_OF.items())
def test_each_bill_picks_its_draft_among_all(entwurf, drucksache):
    assert pick(_scores(drucksache, [*DRUCKSACHE_OF, *FAMILY])) == entwurf


@pytest.mark.parametrize("drucksache", DRUCKSACHE_OF.values())
def test_no_bill_contains_a_foreign_draft(drucksache):
    foreign = [e for e, d in DRUCKSACHE_OF.items() if d != drucksache] + FAMILY
    assert pick(_scores(drucksache, foreign)) is None


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        ({}, None),
        ({"a": 0.39}, None),
        ({"a": 0.4}, "a"),
        ({"a": 0.9, "b": 0.19}, "a"),
        ({"a": 0.9, "b": 0.2}, None),  # a rival this close: two drafts of one law, so neither
    ],
    ids=["no-candidate", "below", "at-threshold", "clear-winner", "rival"],
)
def test_pick(scores, expected):
    assert pick(scores) == expected


def test_an_empty_draft_is_contained_nowhere():
    assert containment(frozenset(), _shingles("drucksache_18_623")) == 0.0


@pytest.mark.parametrize(
    ("released", "expected"),
    [
        (date(2025, 9, 29), True),  # a year before
        (date(2025, 9, 28), False),
        (date(2026, 9, 28), True),
        (date(2026, 9, 29), False),  # same day: the track needs R strictly before S
        (date(2026, 10, 1), False),
    ],
)
def test_window_before_the_drucksache(released, expected):
    assert in_window(released, date(2026, 9, 29)) is expected
