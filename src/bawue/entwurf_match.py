"""Which Referentenentwurf a Landesregierung bill grew from (issue #180, DD-079).

Neither the Beteiligungsportal nor PARLIS names the other, and titles change on the
way. The text decides: the draft's word 5-grams largely recur in the Drucksache,
which adds the Begründung and the hearing results, and no other draft comes close.
"""

import re
from datetime import date, timedelta

# WP 17/18: true pairs 0.51 to 0.96, wrong candidates ≤ 0.16 (even the yearly salary laws).
ACCEPT = 0.4
RIVAL = 0.2
# A draft precedes its Drucksache by 6 to 323 days (WP 17).
WINDOW = timedelta(days=365)


def shingles(text: str) -> frozenset[int]:
    words = re.findall(r"\w+", text.lower())
    return frozenset(hash(tuple(words[i : i + 5])) for i in range(len(words) - 4))


def containment(entwurf: frozenset[int], drucksache: frozenset[int]) -> float:
    """Share of the draft's 5-grams found in the Drucksache."""
    return len(entwurf & drucksache) / len(entwurf) if entwurf else 0.0


def pick(scores: dict[str, float]) -> str | None:
    """The one draft the Drucksache contains; none if a second comes close (two drafts of one law)."""
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked or ranked[0][1] < ACCEPT or (len(ranked) > 1 and ranked[1][1] >= RIVAL):
        return None
    return ranked[0][0]


def in_window(released: date, regbsl: date) -> bool:
    """Released before the Drucksache: the track needs `R` before `S`."""
    return regbsl - WINDOW <= released < regbsl
