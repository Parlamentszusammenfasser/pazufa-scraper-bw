"""The backend's track validation for BW, without a backend (DD-040).

Both tables are copied verbatim from the trackfile the backend loads:
  tracks.toml @ tag v0.2.9+v0.1.3, version 0.1.3
  https://codeberg.org/PaZuFa/parlamentszusammenfasser/raw/tag/v0.2.9+v0.1.3/tracks.toml
Update them together when the backend moves to a new tracks version.
"""

from collections.abc import Iterable

import pytest

# [stations]
STATION_LETTERS = {
    "preparl-regent": "R",
    "preparl-eckpup": "E",
    "preparl-regbsl": "S",
    "parl-initiativ": "I",
    "parl-vollvlsgn": "L",
    "parl-akzeptanz": "J",
    "parl-zurueckgz": "Z",
    "parl-ablehnung": "N",
    "parl-ausschber": "A",
    "postparl-gsblt": "G",
    "postparl-kraft": "K",
    "postparl-vgstp": "C",
    "preparl-vbegde": "B",
    "parl-ggentwurf": "W",
    "postparl-vesja": "Y",
    "postparl-vesne": "X",
}

_GG_LAND_PARL = "((E*R+)?S)?I((LA*(Z|LJ(C|GA*(C|KA*C?))|LN|LA*(Z|LJ(C|GA*(C|KA*C?))|LN)))|Z)"
# [tracks.BW]
BW_TRACKS = {
    "gg-land-parl": _GG_LAND_PARL,
    "gg-land-parl-haushalt": _GG_LAND_PARL,
}


def letters(stationen: Iterable) -> str:
    """The letters in the backend's order (by ``zp_start``); a typ without letter raises KeyError."""
    return "".join(STATION_LETTERS[s.typ] for s in sorted(stationen, key=lambda s: s.zp_start))


def passes(typ: str, track: str) -> bool:
    """The backend accepts a prefix of a valid word; ``partial=True`` is exactly that."""
    regex = pytest.importorskip("regex")  # provided transitively via litellm→tiktoken
    return regex.fullmatch(BW_TRACKS[typ], track, partial=True) is not None
