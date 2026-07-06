"""Redis-backed cache, replacing the removed collector.scrapercache.ScraperCache.

Only the raw string get/set operations are ported: they're the only ones any
BaWue scraper actually calls (Vorgänge/Beteiligung cache under a `vg2:` key
they build themselves, Sitzungen and LLM-semantics caching use raw keys too).
"""

import logging
import sys

import redis

logger = logging.getLogger(__name__)


class BawueCache:
    def __init__(
        self,
        redis_host: str | None,
        redis_port: int | None,
        disabled: bool = False,
    ) -> None:
        self.disabled = disabled
        self.redis_client: redis.Redis | None = None
        if disabled or redis_host is None or redis_port is None:
            self.disabled = True
            logger.warning("Caching disabled")
            return

        try:
            self.redis_client = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
            self.redis_client.ping()
            logger.info("Connected to Redis at %s:%s", redis_host, redis_port)
        except redis.ConnectionError as e:
            logger.error("Failed to connect to Redis: %s", e)
            sys.exit(1)
        except Exception as e:
            logger.error("Unexpected error connecting to Redis: %s", e)
            sys.exit(1)

    def store_raw(self, key: str, value: str, typehint: str = "Raw Value") -> bool:
        if self.disabled:
            return True
        try:
            success = self.redis_client.set(key, value)
            if not success:
                logger.warning("Storing %s (key=`%s`) failed!", typehint, key)
                return False
            return True
        except Exception:
            logger.error("Error storing raw value with key `%s`", key)
            return False

    def get_raw(self, key: str, typehint: str = "Raw Value") -> str | None:
        if self.disabled:
            return None
        try:
            value = self.redis_client.get(key)
            if not value:
                logger.debug("%s (key=`%s`) not found in cache", typehint, key)
                return None
            return value
        except Exception:
            logger.error("Error retrieving raw value with key `%s`", key)
            return None
