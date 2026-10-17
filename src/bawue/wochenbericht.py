"""Weekly summary: Gesetzgebungsvorgänge that are new or reached another stage (issue #82, DD-072).

Compares today's stage of each Vorgang with the stage stored a week ago, so late uploads
and rebuilt Vorgänge are counted correctly.
"""

import json
import logging
from datetime import UTC, datetime

from bawue.api import build_client, get_vorgaenge
from bawue.config import BawueConfig
from bawue.config_loader import load_toml_section
from bawue.wahlperiode import CURRENT_WAHLPERIODE

logger = logging.getLogger(__name__)

_KEY = "wochenbericht"
_MAX_LINES = 20
_LABELS = {
    "preparl-regent": "Referentenentwurf",
    "preparl-regbsl": "Kabinettsbeschluss",
    "parl-initiativ": "Eingebracht",
    "parl-vollvlsgn": "Plenum",
    "parl-ausschber": "Ausschussberatung",
    "parl-akzeptanz": "Angenommen",
    "parl-ablehnung": "Abgelehnt",
    "parl-zurueckgz": "Zurückgezogen",
    "postparl-gsblt": "Verkündet",
    "postparl-kraft": "In Kraft",
}


def stages(vorgaenge: list[dict], now: datetime) -> dict[str, list[str]]:
    """api_id → [title, typ of the latest Station that has started]. Planned Stationen (DD-069) don't count."""
    result = {}
    for v in vorgaenge:
        started = [s for s in v["stationen"] if datetime.fromisoformat(s["zp_start"]) <= now]
        if v["typ"].startswith("gg-") and started:
            latest = max(started, key=lambda s: datetime.fromisoformat(s["zp_start"]))
            result[v["api_id"]] = [v.get("kurztitel") or v["titel"], latest["typ"]]
    return result


def _label(typ: str) -> str:
    return _LABELS.get(typ, typ)


def report_lines(old: dict[str, list[str]], new: dict[str, list[str]]) -> list[str]:
    """New Vorgänge first, then those whose stage changed, each sorted by title."""
    added = sorted(v for k, v in new.items() if k not in old)
    moved = sorted([*v, old[k][1]] for k, v in new.items() if k in old and old[k][1] != v[1])
    lines = [f"🆕 {titel}: {_label(typ)}" for titel, typ in added]
    lines += [f"➡️ {titel}: {_label(before)} → {_label(typ)}" for titel, typ, before in moved]
    if len(lines) > _MAX_LINES:
        lines = [*lines[:_MAX_LINES], f"… {len(lines) - _MAX_LINES} weitere"]
    return lines


def weekly_section(config: BawueConfig, now: datetime | None = None) -> tuple[str, list[str]] | None:
    """Run-report section, once per ISO week (the first run, normally Monday 03:00). Fail-soft."""
    now = now or datetime.now(UTC)
    year, week, _ = now.isocalendar()
    woche = f"{year}-W{week:02d}"
    if config.cache.disabled:
        return None  # no stand to compare with (cache disabled)
    try:
        # Not get_raw: it hides a Redis error as None, which would overwrite the stand.
        stored = json.loads(config.cache.redis_client.get(_KEY) or "{}")
        if stored.get("woche") == woche:
            return None
        wahlperiode = load_toml_section(config, "bawue").get("wahlperiode", CURRENT_WAHLPERIODE)
        current = stages(get_vorgaenge(build_client(config.database_url, config.api_key), wahlperiode), now)
    except Exception as e:
        logger.warning("Wochenbericht skipped, retried next run: %s", e)
        return None
    if not config.cache.store_raw(_KEY, json.dumps({"woche": woche, "stand": current}), "Wochenbericht"):
        return None  # else the next run posts this week's report again
    if "stand" not in stored:
        return None  # first run: nothing to compare with
    return f"Wochenbericht Gesetzgebung {woche}", report_lines(stored["stand"], current) or ["Keine Änderungen."]
