"""Wahlperiode update check — probes Beteiligungsportal for a newer Wahlperiode."""

import logging

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://beteiligungsportal.baden-wuerttemberg.de"


def check_for_newer_wahlperiode(current_wahlperiode: int) -> None:
    """Probe Beteiligungsportal for the next Wahlperiode and warn if it exists."""
    next_wp = current_wahlperiode + 1
    url = f"{BASE_URL}/de/mitmachen/lp-{next_wp}"
    try:
        resp = requests.get(url, timeout=10, allow_redirects=True)
        if resp.status_code == 200:
            logger.warning("!" * 60)
            logger.warning("  NEUE WAHLPERIODE VERFÜGBAR: WP %d", next_wp)
            logger.warning("  Bitte 'wahlperiode' in config.toml auf %d aktualisieren!", next_wp)
            logger.warning("  URL: %s", url)
            logger.warning("!" * 60)
        else:
            logger.debug(
                "Wahlperiode check: lp-%d returned HTTP %s — no new period",
                next_wp,
                resp.status_code,
            )
    except Exception:
        logger.warning(
            "Wahlperiode check failed (network error) — could not verify lp-%d",
            next_wp,
            exc_info=True,
        )
