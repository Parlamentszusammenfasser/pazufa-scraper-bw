"""Internal data structures for raw PARLIS data before conversion to framework models."""

from enum import StrEnum
from typing import TypedDict


class ReservedGremium(StrEnum):
    """Canonical Gremium names reserved by the community DoD + OpenAPI spec.

    Sources:
    - `vendor/pazufa-collector-core/openapi.yaml` field `Gremium.name`:
      "'plenum', 'regierung', 'volk' sind reservierte namen".
    - Community DoD wiki: adds `gesetzesblatt` "für die Veröffentlichung im
      Gesetzesblatt" and describes `plenum` as the default catch-all.
    - BY reference scraper (`vendor/pazufa-collector/collector/scrapers/bylt_scraper.py`):
      emits `gesetzesblatt` literally for `postparl-gsblt` stations and `plenum`
      for all other non-committee stations.

    `GESETZESBLATT` is absent from the spec description but present in the wiki
    and in the BY scraper's production output — the spec description appears
    incomplete rather than authoritative (the spec schema itself accepts any
    string). See DD-021.

    `StrEnum` members ARE `str` instances, so they pass through pydantic's
    `StrictStr` validation on `Gremium.name` without conversion.
    """

    PLENUM = "plenum"
    REGIERUNG = "regierung"
    VOLK = "volk"
    GESETZESBLATT = "gesetzesblatt"


class RawFundstelle(TypedDict, total=False):
    """Structured data parsed from a PARLIS Fundstelle text entry."""

    raw: str
    datum: str
    drucksache: str | None
    plenarprotokoll: str | None
    station_typ: str
    autor_text: str | None
    ausschuss: str | None
    seiten: int | None
    pdf_url: str | None


class RawVorgang(TypedDict, total=False):
    """Raw Vorgang data as returned by the PARLIS HTML parser.

    Contains both fixed keys (titel, vorgangs_id, etc.) and dynamic keys
    parsed from PARLIS ``<dt>/<dd>`` elements (Vorgangstyp, Initiative, ...).
    """

    titel: str
    vorgangs_id: str
    detail_url: str
    fundstellen_parsed: list[RawFundstelle]
    # Dynamic PARLIS keys (from <dt>/<dd> parsing):
    Vorgangstyp: str
    Initiative: str
