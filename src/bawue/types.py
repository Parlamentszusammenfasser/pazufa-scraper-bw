"""Internal data structures for raw PARLIS data before conversion to framework models."""

from enum import StrEnum
from typing import TypedDict


class ReservedGremium(StrEnum):
    """Canonical Gremium names reserved by the OpenAPI spec.

    Source: `vendor/pazufa-collector-core/openapi.yaml` field
    `Gremium.name` — "'plenum', 'regierung', 'volk' sind reservierte namen".

    `StrEnum` members ARE `str` instances, so they pass through pydantic's
    `StrictStr` validation on `Gremium.name` without conversion.
    """

    PLENUM = "plenum"
    REGIERUNG = "regierung"
    VOLK = "volk"


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
