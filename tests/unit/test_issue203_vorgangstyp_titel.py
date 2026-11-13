"""Issue #203: treaty laws and Nachträge get their spec 0.2.9 Vorgangstyp from the title (DD-085).

https://github.com/Parlamentszusammenfasser/pazufa-scraper-bw/issues/203
"""

import pytest

from bawue.bawue_vorgaenge_scraper import _vorgang_fingerprint
from bawue.enum_mapper import map_vorgangstyp
from bawue.types import Vorgangstyp
from tests.unit.test_bawue_scraper import _make_raw_vorgang

STAATSVERTRAG = "Gesetz zu dem Zweiten Staatsvertrag zur Änderung medienrechtlicher Staatsverträge"
NACHTRAG = "Gesetz über die Feststellung eines Nachtrags zum Staatshaushaltsplan von Baden-Württemberg für 2025/2026"


@pytest.mark.parametrize(
    ("parlis_typ", "titel", "expected"),
    [
        ("Gesetzgebung", STAATSVERTRAG, Vorgangstyp.GG_LAND_PARL_STAATSVERTRAG),
        (
            "Gesetzgebung",
            "Gesetz zu dem Abkommen über die Zentralstelle der Länder",
            Vorgangstyp.GG_LAND_PARL_STAATSVERTRAG,
        ),
        ("Gesetzgebung", "Gesetz zum Schutz von Kindern", Vorgangstyp.GG_LAND_PARL),
        # A law that also ratifies a treaty is no treaty law.
        (
            "Gesetzgebung",
            "Gesetz zur Änderung des Klimaschutzgesetzes sowie Gesetz zu dem Abkommen",
            Vorgangstyp.GG_LAND_PARL,
        ),
        ("Volksantrag", STAATSVERTRAG, Vorgangstyp.GG_LAND_VOLK),
        ("Haushaltsgesetzgebung", NACHTRAG, Vorgangstyp.GG_LAND_PARL_NACHTRAGSHAUSHALT),
        ("Haushaltsgesetzgebung", "Haushaltsbegleitgesetz zum Nachtrag 2025/2026", Vorgangstyp.GG_LAND_PARL_HAUSHALT),
        ("Haushaltsgesetzgebung", "Staatshaushaltsgesetz 2025/2026", Vorgangstyp.GG_LAND_PARL_HAUSHALT),
    ],
    ids=["staatsvertrag", "abkommen", "zum-schutz", "mixed", "volk", "nachtrag", "begleitgesetz", "sthg"],
)
def test_title_refines_the_parlis_type(parlis_typ, titel, expected):
    assert map_vorgangstyp(parlis_typ, titel) == expected


@pytest.mark.parametrize(
    ("vorgangstyp", "titel", "rebuilt"),
    [("Gesetzgebung", STAATSVERTRAG, True), ("Haushaltsgesetzgebung", NACHTRAG, True), ("Gesetzgebung", "X", False)],
    ids=["staatsvertrag", "nachtrag", "other"],
)
def test_stored_ones_are_rebuilt_once_for_their_type(vorgangstyp, titel, rebuilt):
    """Closed treaty laws never change in PARLIS, so a marker switches stored ones to the new type."""
    raw = _make_raw_vorgang("V-1", titel=titel, vorgangstyp=vorgangstyp)
    assert (_vorgang_fingerprint(raw) != _vorgang_fingerprint({**raw, "titel": "Y"})) is rebuilt
