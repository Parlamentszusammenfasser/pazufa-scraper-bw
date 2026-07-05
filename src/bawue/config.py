"""BawueConfig: local replacement for the removed collector.config.CollectorConfiguration.

Reads config.toml (path via --config-file), environment variables and CLI
args, with precedence CLI > env > config file > default — same as the
removed collector. Only the fields BaWue's scrapers, config_loader and
notifications actually read are ported; `scrapers_dir` (replaced by the
static registry in bawue.__main__) and `oapiconfig` (replaced by
bawue.api.build_client) are dropped.
"""

import logging
import os
import sys
from argparse import ArgumentParser
from pathlib import Path

import toml

from bawue.cache import BawueCache

logger = logging.getLogger(__name__)


class ConfigProp:
    """Captures the parsing behaviour of a single config option."""

    def __init__(
        self,
        attribute_name,
        config_file,
        environment=None,
        argname=None,
        default=None,
        cli_setup_f=None,
        required=False,
    ) -> None:
        self.attr = attribute_name
        self.cfg = config_file
        self.env = environment
        self.value = default
        self.value_set_by = "dft"
        self.arg = argname
        self.cli_setup = cli_setup_f
        self.required = required


class BawueConfig:
    def __init__(self) -> None:
        configurations = [
            ConfigProp(
                "linearize",
                "main.linearize",
                None,
                "linearize",
                False,
                lambda p: p.add_argument(
                    "--linearize",
                    help="Await all extraction tasks one-by-one instead of gathering",
                    action="store_true",
                ),
            ),
            ConfigProp(
                "dry_run",
                "main.dry-run",
                "DRY_RUN",
                "dry_run",
                False,
                lambda p: p.add_argument(
                    "--dry-run",
                    help="Run scrapers without submitting results to the API",
                    action="store_true",
                ),
            ),
            ConfigProp("collector_id", "main.collector-uuid", "COLLECTOR_ID", required=True),
            ConfigProp("cycle_time_s", "main.cycle-time-s", "CYCLE_TIME_S", None, 10800),
            ConfigProp(
                "once",
                "main.once",
                "ONCE",
                "once",
                False,
                lambda p: p.add_argument(
                    "--once",
                    help="Run a single scraping cycle and exit (for cron/serverless deployments)",
                    action="store_true",
                ),
            ),
            # cache
            ConfigProp("redis_host", "cache.redis-host", "REDIS_HOST", None, "localhost"),
            ConfigProp("redis_port", "cache.redis-port", "REDIS_PORT", None, 6379),
            # backend
            ConfigProp("database_url", "backend.ltzf-api-url", "LTZF_API_URL", None, "http://localhost:80"),
            ConfigProp(
                "api_key",
                "backend.ltzf-api-key",
                "LTZF_API_KEY",
                "ltzf_api_key",
                None,
                lambda p: p.add_argument(
                    "--ltzf-api-key",
                    help="The key with which you auth yourself as collector to the backend",
                ),
                True,
            ),
            # scraper filter (replaces scrapers_dir auto-discovery — see bawue.__main__.SCRAPERS)
            ConfigProp(
                "scrapers",
                "scrapers.scrapers",
                None,
                "run",
                [],
                lambda p: p.add_argument("--run", help="run only scrapers specified", nargs="*"),
            ),
            # logging
            ConfigProp("api_obj_log", "logging.api-obj-log", "API_OBJ_LOG", None),
            ConfigProp("logfile", "logging.logfile"),
            ConfigProp("parsewarn", "logging.parsewarn"),
            ConfigProp("errorfile", "logging.errorfile"),
            # llm — provider_key and provider_base_url are both optional: exactly one of
            # them enables LLM enrichment (see bawue_vorgaenge_scraper / bawue_beteiligung_scraper).
            ConfigProp("llm_provider_key", "llm.provider-key", "LLM_PROVIDER_KEY"),
            ConfigProp("llm_provider_base_url", "llm.provider-base-url", "LLM_PROVIDER_BASE_URL"),
            ConfigProp("llm_model", "llm.model", "LLM_MODEL", None, "gpt-5-nano"),
        ]
        self.config_file = None
        self.dump_config = False
        self.configurations = configurations

    def _apply_env(self) -> None:
        for config in self.configurations:
            if config.env is None:
                continue
            env_prop = os.getenv(config.env)
            if env_prop:
                if isinstance(config.value, bool):
                    config.value = env_prop.lower() in ("true", "1", "yes")
                elif isinstance(config.value, int):
                    config.value = int(env_prop)
                elif isinstance(config.value, list):
                    config.value = env_prop.split(";")
                else:
                    config.value = env_prop
                config.value_set_by = "env"

    def _init_secondary_objects(self) -> None:
        self.cache = BawueCache(self.redis_host, self.redis_port, disabled=self.dry_run)

    def load(self) -> None:
        parser = ArgumentParser(prog="bawue", description="Bundled BaWue Scrapers")
        parser.add_argument("--config-file", help="the config file to use")
        parser.add_argument(
            "--dump-config",
            help="Print the resolved configuration and exit",
            action="store_true",
        )

        for config in self.configurations:
            if config.arg:
                config.cli_setup(parser)
        args = parser.parse_args()

        config_file = None
        if not args.config_file and Path("config.toml").is_file():
            config_file = "config.toml"
        elif args.config_file:
            config_file = args.config_file
        self.config_file = config_file

        if config_file:
            with open(config_file) as f:
                loaded = toml.load(f)
                for config in self.configurations:
                    if not config.cfg:
                        continue
                    cfg_path = config.cfg.split(".")
                    if cfg_path[0] not in loaded or cfg_path[1] not in loaded[cfg_path[0]]:
                        continue
                    cfg_prop = loaded[cfg_path[0]][cfg_path[1]]
                    if cfg_prop:
                        config.value = cfg_prop
                        config.value_set_by = "cfg"

        self._apply_env()

        for config in self.configurations:
            if not config.arg:
                continue
            arg_prop = getattr(args, config.arg, None)
            if arg_prop:
                config.value = int(arg_prop) if isinstance(config.value, int) else arg_prop
                config.value_set_by = "cli"

        if args.dump_config:
            logger.info(str(self))
            sys.exit(0)

        missing_required = []
        for config in self.configurations:
            if config.required and config.value is None:
                missing_required.append(config)
            setattr(self, config.attr, config.value)
        if missing_required:
            names = ", ".join(mr.attr for mr in missing_required)
            logger.critical("Missing required configurations: \n%s", names)
            sys.exit(1)

        self._init_secondary_objects()

    def __str__(self) -> str:
        output = "Configuration of BaWue Scraper\n"
        output += f"Config File: {self.config_file}\n"
        output += "dft=default|env=environment var|cli=command line argument\n"
        for config in self.configurations:
            name = config.cfg or config.attr
            missing = config.required and config.value is None
            output += f"{config.value_set_by}{name:.>25}: {config.value}"
            output += " MISSING\n" if missing else "\n"
        return output
